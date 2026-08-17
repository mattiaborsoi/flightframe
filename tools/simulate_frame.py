#!/usr/bin/env python3
"""Pretend to be the frame, and check the server the way the firmware would.

    python3 tools/simulate_frame.py http://127.0.0.1:8080

Worth running before flashing, and again whenever the frame misbehaves: it
separates "the server is wrong" from "the firmware is wrong", which is
otherwise very hard to tell apart from the frame's end — a bad response makes
the firmware back off silently rather than complain.
"""
from __future__ import annotations

import hashlib
import json
import sys
import urllib.error
import urllib.request

PFX = "/device/v1"
MAC = "aa:bb:cc:dd:ee:ff"          # a stand-in; the real frame uses its own


def call(base, method, path, body=None, headers=None, raw=False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers=headers or {})
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            payload = r.read()
            return r.status, payload if raw else json.loads(payload)
    except urllib.error.HTTPError as e:
        return e.code, e.read() if raw else json.loads(e.read() or b"{}")


def main(base: str, secret: str | None = None) -> int:
    failures = []

    def check(label, cond, detail=""):
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}{'  ' + detail if detail else ''}")
        if not cond:
            failures.append(label)

    body = {"mac": MAC, "hw_rev": "proto-ee02"}
    if secret:
        body["provision_secret"] = secret
    st, r = call(base, "POST", f"{PFX}/setup", body)
    check("setup issues a token", st == 200 and bool(r.get("device_token")), f"status {st}")
    token = r.get("device_token", "")
    if not token:
        # Multi-tenant servers require a valid --secret; without one setup
        # returns 403 and there is nothing further to probe. Report cleanly
        # rather than crashing on the missing image_url a few lines down.
        print("\nsetup did not issue a token — pass --secret <tenant secret> "
              "for a multi-tenant server. Stopping.")
        return 1
    auth = {"Authorization": f"Bearer {token}"}
    hdr = dict(auth, **{"X-Battery-Mv": "3941", "X-Rssi": "-61",
                        "X-Fw-Version": "0.1.0", "X-Boot-Reason": "rtc"})

    st, disp = call(base, "GET", f"{PFX}/display", headers=hdr)
    check("display returns 200", st == 200, f"status {st}")
    h = disp.get("image_hash", "")
    check("image_hash is sha256: plus 64 lowercase hex",
          h.startswith("sha256:") and len(h) == 71
          and all(c in "0123456789abcdef" for c in h[7:]))
    check("sleep_s is a positive int",
          isinstance(disp.get("sleep_s"), int) and 0 < disp["sleep_s"] < 2**32,
          f"{disp.get('sleep_s')}s ({disp.get('_why')})  showing {disp.get('_design')}")

    st, blob = call(base, "GET", disp["image_url"].replace(base, ""), headers=auth, raw=True)
    check("image is exactly 960,000 bytes", st == 200 and len(blob) == 960_000,
          f"{len(blob):,}")
    check("sha256 matches what was advertised",
          f"sha256:{hashlib.sha256(blob).hexdigest()}" == h)
    nibbles = set()
    for b in blob[::887]:
        nibbles.add(b >> 4)
        nibbles.add(b & 15)
    check("only legal palette indices", nibbles <= {0, 1, 2, 3, 5, 6}, str(sorted(nibbles)))

    st, pressed = call(base, "GET", f"{PFX}/display",
                       headers=dict(hdr, **{"X-Boot-Reason": "button"}))
    check("button shortens the sleep", pressed.get("sleep_s", 9e9) < disp["sleep_s"],
          f"{pressed.get('sleep_s')}s vs {disp['sleep_s']}s")
    check("never below the panel's tested 150s", pressed.get("sleep_s", 0) >= 150)

    st, _ = call(base, "GET", f"{PFX}/display", headers={"Authorization": "Bearer nope"})
    check("unknown token gives 401", st == 401, f"status {st}")
    st, _ = call(base, "GET", f"{PFX}/img/{'0' * 64}.bin", headers=auth, raw=True)
    check("unknown image hash gives 404", st == 404, f"status {st}")

    st, low = call(base, "GET", f"{PFX}/display",
                   headers=dict(hdr, **{"X-Battery-Mv": "3300"}))
    check("low battery collapses the schedule", low.get("sleep_s", 0) >= 3600,
          f"{low.get('sleep_s')}s")

    print("\n" + ("all checks passed" if not failures
                  else f"{len(failures)} FAILED: " + ", ".join(failures)))
    return 0 if not failures else 1


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("base", nargs="?", default="http://127.0.0.1:8080")
    ap.add_argument("--secret", default=None,
                    help="tenant provisioning secret (multi-tenant servers)")
    a = ap.parse_args()
    raise SystemExit(main(a.base, a.secret))
