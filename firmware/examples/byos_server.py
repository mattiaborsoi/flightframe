#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 YODE PTE LTD
# SPDX-License-Identifier: Apache-2.0
"""Minimal bring-your-own-server for FlightPortrait frames. Stdlib only.

Implements the three device endpoints from docs/PROTOCOL.md well enough
to run a stock frame: point the frame at this host during BLE
provisioning (PROTOCOL.md §5) and it will set up, poll, download, and
display whatever panel image you serve.

    python3 byos_server.py --image path/to/panel.bin [--port 8642]
        [--secret VALUE] [--sleep 3600]

The image must be exactly 960,000 bytes in the PROTOCOL.md §1 format
(the calibration patterns in first-flash/bins/ work). Swap the file on
disk and the next poll serves the new content — the frame notices via
the hash and refreshes. --secret, when set, must match the structured
BYOS setup secret entered during provisioning; without it any
provision_secret is accepted. Issued device tokens live in
byos_state.json next to this file, so restarts don't strand frames.

This is a reference, not a product: no TLS (the frame allows plain http
for hand-set targets), no rate limiting, one image for every frame.
"""
import argparse
import hashlib
import json
import os
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "byos_state.json")
IMAGE_BYTES = 960000


def load_state():
    try:
        with open(STATE_PATH) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"tokens": {}}


def save_state(state):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(state, fh, indent=1)
    os.replace(tmp, STATE_PATH)


class Handler(BaseHTTPRequestHandler):
    server_version = "flightportrait-byos-example"
    args = None
    state = None

    def send_json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_body_json(self):
        try:
            n = int(self.headers.get("Content-Length", "0"))
            return json.loads(self.rfile.read(n).decode())
        except (ValueError, UnicodeDecodeError):
            return None

    def bearer_ok(self):
        auth = self.headers.get("Authorization", "")
        return (auth.startswith("Bearer ") and
                auth[7:] in self.state["tokens"].values())

    def log_telemetry(self):
        parts = []
        for h in ("X-Fw-Version", "X-Boot-Reason", "X-Rssi",
                  "X-Battery-Mv"):
            v = self.headers.get(h)
            if v:
                parts.append("%s=%s" % (h, v))
        if parts:
            print("  telemetry:", " ".join(parts))

    def do_POST(self):
        if self.path == "/device/v1/setup":
            body = self.read_body_json()
            if not isinstance(body, dict) or "mac" not in body:
                return self.send_json(422, {"detail": "bad body"})
            if (self.args.secret and
                    body.get("provision_secret") != self.args.secret):
                return self.send_json(401, {"detail": "bad secret"})
            token = secrets.token_hex(32)
            self.state["tokens"][body["mac"]] = token
            save_state(self.state)
            print("setup: %s enrolled (hw_rev=%s)"
                  % (body["mac"], body.get("hw_rev", "?")))
            # No pairing block: account pairing is a first-party
            # extension this example does not implement (PROTOCOL.md §2).
            return self.send_json(200, {"device_token": token})
        if self.path == "/device/v1/log":
            if not self.bearer_ok():
                return self.send_json(401, {"detail": "unknown token"})
            body = self.read_body_json()
            if not isinstance(body, dict) or \
                    not isinstance(body.get("logs"), list):
                return self.send_json(422, {"detail": "bad body"})
            self.log_telemetry()
            for entry in body["logs"]:
                print("  frame log [%s] %s (ts=%s)"
                      % (entry.get("level", "error"),
                         entry.get("message", ""), entry.get("ts")))
            return self.send_json(200, {"ok": True})
        return self.send_json(404, {"detail": "unknown endpoint"})

    def do_GET(self):
        if self.path == "/device/v1/display":
            if not self.bearer_ok():
                return self.send_json(401, {"detail": "unknown token"})
            self.log_telemetry()
            try:
                with open(self.args.image, "rb") as fh:
                    image = fh.read()
            except OSError:
                return self.send_json(503, {"detail": "image unreadable"})
            if len(image) != IMAGE_BYTES:
                return self.send_json(503, {"detail": "image wrong size"})
            digest = hashlib.sha256(image).hexdigest()
            host = self.headers.get("Host", "localhost")
            return self.send_json(200, {
                "image_url": "http://%s/img/%s.bin" % (host, digest),
                "image_hash": "sha256:" + digest,
                "sleep_s": self.args.sleep,
                "firmware": None,
                "reset": False,
            })
        if self.path.startswith("/img/"):
            try:
                with open(self.args.image, "rb") as fh:
                    image = fh.read()
            except OSError:
                return self.send_json(503, {"detail": "image unreadable"})
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(image)))
            self.end_headers()
            self.wfile.write(image)
            return None
        return self.send_json(404, {"detail": "unknown endpoint"})

    def log_message(self, fmt, *fmt_args):
        print("%s %s" % (self.command, self.path))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--image", required=True,
                    help="960,000-byte panel image to serve")
    ap.add_argument("--port", type=int, default=8642)
    ap.add_argument("--secret", default="",
                    help="require this BYOS setup secret (default: any)")
    ap.add_argument("--sleep", type=int, default=3600,
                    help="sleep_s handed to the frame (default 3600)")
    args = ap.parse_args()
    if not os.path.exists(args.image):
        sys.exit("no such image: %s" % args.image)
    sys.stdout.reconfigure(line_buffering=True)

    Handler.args = args
    Handler.state = load_state()
    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print("serving %s on port %d — point the frame at http://<this-host>:%d"
          % (args.image, args.port, args.port))
    server.serve_forever()


if __name__ == "__main__":
    main()
