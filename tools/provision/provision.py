#!/usr/bin/env python3
"""Provision the frame over BLE: server target, then Wi-Fi, then confirmation.

Run from Terminal.app (macOS asks for Bluetooth permission on first run):

    ~/flightframe/.venv/bin/python ~/flightframe/tools/provision/provision.py

Why this exists: the firmware's BLE transaction is ESP-IDF Unified
Provisioning (Security 2) *plus* two custom endpoints from PROTOCOL.md §6 —
`fp-api-base` (the BYOS server URL, which must be written before the Wi-Fi
credentials) and `fp-pair` (the completion handshake). Espressif's phone app
knows neither, which is why it connects fine and then silently never
finishes.

Order is load-bearing:
    session → fp-api-base → Wi-Fi creds → apply → join → fp-pair until done

The SRP6a/AES-GCM crypto and protobuf framing are Espressif's own esp_prov
modules, vendored unmodified under vendor/ (Apache-2.0). Only the BLE
transport is ours, because upstream's was written for an older bleak.

The Wi-Fi password is read interactively (hidden) and travels AES-GCM
encrypted inside the SRP6a session; it is never logged or written to disk.
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import secrets
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "vendor"), os.path.join(HERE, "vendor", "esp_prov")]

import proto  # noqa: E402,F401  (vendored; must import before the helpers)
from prov import wifi_prov  # noqa: E402
from security import Security2  # noqa: E402
from utils import str_to_bytes  # noqa: E402

from bleak import BleakClient, BleakScanner  # noqa: E402

# All device- and deployment-specific values come from the command line: the
# BLE name/username/pop are printed in the QR on the glass of the frame being
# provisioned, and the URL is your server. Nothing personal ships in this file.

SERVICE_UUID = "5c33aff1-92c8-47a8-b464-9d2072544650"
# ESP-IDF hands the UUID byte array to the BLE stack LSB-first, so scanners
# (bleak included) see the 16 bytes in reverse order. PROTOCOL.md §6 calls
# this out; accept both forms.
_rev = bytes.fromhex(SERVICE_UUID.replace("-", ""))[::-1].hex()
SERVICE_UUID_LSB = f"{_rev[:8]}-{_rev[8:12]}-{_rev[12:16]}-{_rev[16:20]}-{_rev[20:]}"

JOIN_TRIES = 25          # x2s poll for the Wi-Fi join verdict
PAIR_TRIES = 20          # fp-pair 'get' may block ~30s server-side per call


class BLE:
    """Minimal protocomm-over-GATT transport for bleak >= 3.

    Endpoint names come from the 0x2901 user descriptors the firmware
    attaches to each characteristic, exactly as upstream's client does it.
    esp_prov's helpers speak latin-1 strings, so send() does too.
    """

    def __init__(self) -> None:
        self.client: BleakClient | None = None
        self.endpoints: dict[str, object] = {}

    async def connect(self, devname: str, timeout: float = 20.0) -> None:
        print(f"      scanning for {devname} …")
        device = await BleakScanner.find_device_by_name(devname, timeout=timeout)
        if device is None:
            raise RuntimeError(
                f"{devname} not advertising. Is the frame awake and showing "
                "the QR? A sleeping frame stops advertising; press a side "
                "button or power-cycle it.")
        self.client = BleakClient(device)
        await self.client.connect()
        service = (self.client.services.get_service(SERVICE_UUID)
                   or self.client.services.get_service(SERVICE_UUID_LSB))
        if service is None:
            raise RuntimeError(
                f"service {SERVICE_UUID} (or LSB form {SERVICE_UUID_LSB}) "
                f"not found; got {[s.uuid for s in self.client.services]}")
        for ch in service.characteristics:
            for desc in ch.descriptors:
                if desc.uuid[4:8].lower() != "2901":
                    continue
                raw = await self.client.read_gatt_descriptor(desc)
                self.endpoints[bytes(raw).decode("latin-1").lower()] = ch
        missing = {"prov-session", "prov-config", "fp-api-base", "fp-pair"} \
            - set(self.endpoints)
        if missing:
            raise RuntimeError(f"endpoints missing from GATT: {sorted(missing)} "
                               f"(found {sorted(self.endpoints)})")

    async def send(self, ep: str, data: str | bytes) -> str:
        assert self.client is not None
        if isinstance(data, str):
            data = data.encode("latin-1")
        ch = self.endpoints[ep]
        await self.client.write_gatt_char(ch, bytes(data), response=True)
        reply = await self.client.read_gatt_char(ch)
        return bytes(reply).decode("latin-1")

    async def close(self) -> None:
        if self.client is not None:
            try:
                await self.client.disconnect()
            except Exception:
                pass


async def establish_session(ble: BLE, sec: Security2) -> None:
    # Drive the Security2 FSM exactly like upstream esp_prov.establish_session.
    # The Security2 FSM speaks latin-1 strings and converts internally.
    response: str | None = None
    while True:
        request = sec.security_session(response)
        if request is None:
            return
        response = await ble.send("prov-session", request)


async def send_json(ble: BLE, sec: Security2, ep: str, payload: dict) -> dict:
    body = json.dumps(payload, separators=(",", ":")).encode()
    enc = sec.encrypt_data(body).decode("latin-1")
    reply = sec.decrypt_data(str_to_bytes(await ble.send(ep, enc)))
    return json.loads(reply.decode("utf-8", "replace"))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True, help="BLE name from the QR, e.g. PROV_A1B2C3")
    ap.add_argument("--username", required=True, help="sec2 username from the QR")
    ap.add_argument("--pop", required=True, help="sec2 password (pop) from the QR")
    ap.add_argument("--url", required=True, help="server base URL, e.g. https://frame.example.com")
    ap.add_argument("--secret", default=None,
                    help="tenant provisioning secret from the dashboard; this "
                         "is what binds the frame to an account on a "
                         "multi-tenant server")
    ap.add_argument("--ssid", default=None, help="Wi-Fi SSID (prompted if omitted)")
    args = ap.parse_args()

    ssid = args.ssid or input("Wi-Fi SSID (2.4 GHz): ").strip()
    password = getpass.getpass(f"Wi-Fi password for {ssid!r} (hidden): ")
    if not ssid or not password:
        print("SSID and password are both required.")
        return 1

    # The firmware requires the structured form — a plain URL fails closed
    # with byos-secret-required. Against a multi-tenant server the secret IS
    # the tenant binding, so pass --secret; a random value (single-tenant
    # servers ignore it) remains the default.
    setup_secret = args.secret or secrets.token_hex(16)

    ble = BLE()
    print("[1/6] connecting …")
    await ble.connect(args.name)
    print(f"      connected; endpoints: {sorted(ble.endpoints)}")

    try:
        print("[2/6] Security-2 (SRP6a) session …")
        sec = Security2(args.username, args.pop, verbose=False)
        await establish_session(ble, sec)
        print("      established")

        print(f"[3/6] setting server target {args.url} …")
        reply = await send_json(ble, sec, "fp-api-base",
                                {"url": args.url, "setup_secret": setup_secret})
        if reply.get("status") != "ok":
            print(f"      REJECTED: {reply} — stopping before Wi-Fi.")
            return 1
        print("      accepted")

        print(f"[4/6] sending Wi-Fi credentials for {ssid!r} …")
        status = wifi_prov.config_set_config_response(
            sec, await ble.send(
                "prov-config",
                wifi_prov.config_set_config_request(sec, ssid, password)))
        if status != 0:
            print(f"      SetConfig failed with status {status}")
            return 1
        status = wifi_prov.config_apply_config_response(
            sec, await ble.send(
                "prov-config", wifi_prov.config_apply_config_request(sec)))
        if status != 0:
            print(f"      ApplyConfig failed with status {status}")
            return 1

        print("[5/6] waiting for the frame to join the network …")
        verdict = "unknown"
        for _ in range(JOIN_TRIES):
            await asyncio.sleep(2)
            verdict = wifi_prov.config_get_status_response(
                sec, await ble.send(
                    "prov-config", wifi_prov.config_get_status_request(sec)))
            if verdict in ("connected", "failed"):
                break
        if verdict != "connected":
            print(f"      Wi-Fi join did not complete (state: {verdict}). "
                  "The BLE session stays open on the frame — fix the "
                  "password/SSID and re-run this script.")
            return 1

        print("[6/6] confirming setup against the server (fp-pair) …")
        for attempt in range(PAIR_TRIES):
            reply = await send_json(ble, sec, "fp-pair", {"op": "get"})
            status = reply.get("status", "bundle")
            if status == "retry":
                print(f"      not ready yet (attempt {attempt + 1}), retrying …")
                await asyncio.sleep(3)
                continue
            if status == "byos":
                print("\nDone. The frame has joined the Wi-Fi, registered with "
                      "the flightframe server, and will now fetch its first "
                      "poster. Give the panel ~30 seconds to refresh.")
                return 0
            print(f"\nUnexpected fp-pair result: {reply}")
            print("Anything but 'byos' means the transaction ended another "
                  "way — check the server log before retrying.")
            return 1
        print("\nfp-pair never left 'retry' — the frame could not reach "
              f"{args.url}. Check the server is up and on the same LAN.")
        return 1
    finally:
        await ble.close()


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        raise SystemExit(130)
