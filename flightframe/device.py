"""The three endpoints the frame's firmware speaks — multi-tenant.

From the device protocol. The frame wakes, asks whether there is a new
picture, downloads a flat 960,000-byte bitmap, blits it, sleeps. It never
accepts an inbound connection, so everything here is pull-only.

    POST /device/v1/setup     first connection; issues a bearer token
    GET  /device/v1/display   every wake; the whole conversation
    POST /device/v1/log       batched crash and error reports

Multi-tenancy rules, all enforced in this file:

  * setup() binds a frame to exactly one tenant via the provisioning secret
    the frame carries in NVS (entered once at BLE provisioning). No secret,
    no token. A known MAC re-registering with a different tenant's secret
    MOVES — that is the legitimate re-point flow.
  * Every other endpoint resolves tenant identity from the bearer token and
    nothing else. Image downloads are scoped to the requesting frame's
    tenant, so no token can fetch another household's sky.
  * A disabled tenant's frames get 503, never 401: 401 is terminal in the
    firmware (it backs off forever), and "disabled" should be reversible.

Two details from the spec that still shape the code:

  * `image_hash` is the frame's own change detection; hashes must be stable.
  * `sleep_s` is server-owned and authoritative. Every battery decision
    lives here — which is also why cadence and awake windows can be tenant
    dashboard settings with zero firmware involvement.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .config import AppConfig, for_tenant
from .display import Selection
from .registry import Registry
from .tracking import Tracker

PREFIX = "/device/v1"

# The firmware refuses anything outside 1..2^32-1, and the panel vendor's own
# reliability testing ran at 150s intervals. Never ask for faster than that.
MIN_SLEEP_S = 150
MAX_SLEEP_S = 6 * 3600

# A press opens a short window of quicker polls so a second look is responsive.
BUTTON_WINDOW_S = 15 * 60
BUTTON_SLEEP_S = 150

# ~10% on a 1S li-ion. Below this the frame shows the charge poster and takes
# the longest sleep we have.
LOW_BATTERY_MV = 3500


class DeviceAPI:
    """Serves the protocol. Owned by the web handler, which routes to it."""

    def __init__(self, app: AppConfig, registry: Registry):
        self.app = app
        self.registry = registry
        # design-image hash memo: (tenant_id, design) -> (mtime, sha)
        self._hashes: dict[tuple[str, str], tuple[float, str]] = {}
        # button windows are per-frame, keyed by MAC. The single global float
        # this replaces meant any frame's button press sped up every frame.
        self._button_until: dict[str, float] = {}

    # -- image identity ---------------------------------------------------

    def _hash(self, tid: str, out_dir: Path,
              design: str) -> tuple[str, Path] | None:
        path = out_dir / f"{design}.bin"
        if not path.exists():
            return None
        mtime = path.stat().st_mtime
        cached = self._hashes.get((tid, design))
        if cached and cached[0] == mtime:
            return cached[1], path
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        self._hashes[(tid, design)] = (mtime, digest)
        return digest, path

    def image_by_hash(self, token: str, digest: str) -> bytes | None:
        """Serve a packed image by content hash.

        The firmware fetches image_url exactly as given — WITHOUT the bearer
        header (verified on real hardware; the simulator's auth'd download
        hid this for two days). So the digest itself is the credential: 256
        bits, learnable only from the owning tenant's authenticated /display
        call, and not enumerable. A token, when present, just narrows the
        search to its own tenant.
        """
        device = self.registry.device_by_token(token) if token else None
        if device is not None:
            tenant_dirs = [(device["tenant_id"],
                            self.app.tenant_out(device["tenant_id"]))]
        else:
            tenant_dirs = [(t["id"], self.app.tenant_out(t["id"]))
                           for t in self.registry.tenants()]
        names = [d.name for d in _designs()] + ["charge"]
        for tid, out_dir in tenant_dirs:
            for design in names:
                found = self._hash(tid, out_dir, design)
                if found and found[0] == digest:
                    return found[1].read_bytes()
            # Grace window: the renderer may have rotated the poster between
            # the display response and this download; the previous
            # generation stays valid.
            for prev in out_dir.glob("*.bin.prev"):
                data = prev.read_bytes()
                if hashlib.sha256(data).hexdigest() == digest:
                    return data
        return None

    # -- scheduling -------------------------------------------------------

    def _sleep_s(self, settings, tz_name: str, mac: str,
                 tracked) -> tuple[int, str]:
        """How long the frame should sleep, and why (the why goes in the log).

        `now` is computed in the tenant's timezone so a frame in Milan
        sleeps by Milan's night, not the server's.
        """
        if time.time() < self._button_until.get(mac, 0.0):
            return BUTTON_SLEEP_S, "button window"

        if tracked is not None:
            return max(MIN_SLEEP_S, tracked.panel_interval_s), "tracking"

        try:
            now = datetime.now(ZoneInfo(tz_name))
        except (KeyError, ValueError):
            now = datetime.now()
        start, end = settings.awake_from, settings.awake_until
        if not (start <= now.time() <= end):
            target = now.replace(hour=start.hour, minute=start.minute,
                                 second=0, microsecond=0)
            if now.time() > end:
                target += timedelta(days=1)
            return (max(MIN_SLEEP_S, min(int((target - now).total_seconds()),
                                         MAX_SLEEP_S)), "asleep until morning")

        return max(MIN_SLEEP_S, settings.refresh_minutes * 60), "normal"

    # -- endpoints --------------------------------------------------------

    def setup(self, body: dict[str, Any]) -> tuple[int, dict]:
        mac = str(body.get("mac") or body.get("mac_address") or "").strip().lower()
        hw = str(body.get("hw_rev") or body.get("hardware") or "unknown")
        secret = str(body.get("provision_secret") or "")
        if not mac:
            return 400, {"error": "mac required"}
        tenant = self.registry.tenant_by_secret(secret)
        if tenant is None:
            # Recoverable for the firmware: it retries setup with backoff, so
            # a mistyped secret can be fixed by re-provisioning, not a reflash.
            return 403, {"error": "unknown provisioning secret"}
        token = self.registry.device_register(tenant["id"], mac, hw)
        return 200, {"device_token": token, "server": "flightframe"}

    def display(self, token: str, headers: dict[str, str],
                base_url: str) -> tuple[int, dict]:
        device = self.registry.device_by_token(token)
        if device is None:
            # Genuinely unknown token: terminal 401, needs re-provisioning.
            return 401, {"error": "unknown token"}
        tenant = self.registry.tenant(device["tenant_id"])
        if tenant is None or tenant["status"] != "active":
            return 503, {"error": "tenant disabled"}

        telemetry = {
            "battery_mv": _int(headers.get("x-battery-mv")),
            "rssi": _int(headers.get("x-rssi")),
            "fw_version": headers.get("x-fw-version"),
            "boot_reason": headers.get("x-boot-reason"),
            "power_source": headers.get("x-power-source"),
        }
        self.registry.device_touch(device["id"], telemetry)

        if (telemetry.get("boot_reason") or "") == "button":
            self._button_until[device["mac"]] = time.time() + BUTTON_WINDOW_S

        settings = for_tenant(self.app, tenant)
        tracker = Tracker(settings.data_dir, settings.cache_dir,
                          settings.user_agent)
        tracked = tracker.load()
        if tracked is not None and tracked.status == "expired":
            tracked = None

        battery = telemetry.get("battery_mv")
        low_battery = bool(battery and battery < LOW_BATTERY_MV)
        if low_battery:
            # The poster becomes the message: full-screen "please charge".
            design = "charge"
        else:
            design = Selection(settings.data_dir).effective(tracked is not None)

        found = self._hash(tenant["id"], settings.out_dir, design)
        if found is None and low_battery:
            # Charge poster not rendered yet; fall back to the real design so
            # the frame is never left without an image over a race.
            design = Selection(settings.data_dir).effective(tracked is not None)
            found = self._hash(tenant["id"], settings.out_dir, design)
        if found is None:
            # Nothing rendered yet. Do not invent a payload — a short sleep
            # and a retry is honest and costs one cheap wake.
            return 503, {"error": f"{design} not rendered yet"}
        digest, _path = found

        if low_battery:
            sleep_s, why = MAX_SLEEP_S, "low battery"
        else:
            sleep_s, why = self._sleep_s(settings, tenant["tz"],
                                         device["mac"], tracked)

        firmware_url = None
        if device.get("pending_firmware"):
            fw_path = self.app.data_dir / "firmware" / device["pending_firmware"]
            if fw_path.exists():
                firmware_url = f"{base_url}{PREFIX}/fw/{device['pending_firmware']}"
            if telemetry.get("fw_version") and device["pending_firmware"].startswith(
                    str(telemetry["fw_version"])):
                self.registry.device_clear_pending_firmware(device["id"])
                firmware_url = None

        return 200, {
            "image_url": f"{base_url}{PREFIX}/img/{digest}.bin",
            "image_hash": f"sha256:{digest}",
            "sleep_s": int(sleep_s),
            "firmware": firmware_url,
            "reset": False,
            "_design": design,      # ignored by firmware, useful in the log
            "_why": why,
        }

    def log(self, token: str, body: Any) -> tuple[int, dict]:
        device = self.registry.device_by_token(token)
        if device is None:
            return 401, {"error": "unknown token"}
        self.registry.device_touch(device["id"], {})
        path = self.app.tenant_data(device["tenant_id"]) / "device.log"
        _append_rotating(path, f"{datetime.now():%Y-%m-%d %H:%M:%S} "
                               f"{json.dumps(body)}\n")
        return 200, {"ok": True}


def _append_rotating(path: Path, line: str, max_bytes: int = 512 * 1024) -> None:
    """Bounded device log: one rotation generation, never more than ~1MB."""
    if path.exists() and path.stat().st_size > max_bytes:
        path.replace(path.with_suffix(".log.1"))
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)


def _designs():
    from .render import DESIGNS
    return DESIGNS


def _int(value: str | None) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None
