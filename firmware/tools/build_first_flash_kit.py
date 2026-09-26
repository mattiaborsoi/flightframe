#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 YODE PTE LTD
# SPDX-License-Identifier: Apache-2.0
"""Build the E1004 first-flash SD-card test set.

The three calibration buffers are generated directly in the canonical
PROTOCOL.md 4-bpp format, stdlib only. They are the part that matters for
bringing up glass, and they build anywhere:

    python3 tools/build_first_flash_kit.py

Optionally the kit also carries real posters. Those are rendered through the
FlightPortrait art -> e-ink pipeline, which lives in the full tree this
firmware is developed in and is not part of the firmware repo; this tool
never invents a showcase subject. Where that renderer is present:

    python3 tools/build_first_flash_kit.py --logs server/data/2026-07-18.json
"""
import argparse
import hashlib
import importlib
import json
import os
import sys


W, H = 1200, 1600
ROW_BYTES = W // 2
BIN_SIZE = W * H // 2
VALID = (0x0, 0x1, 0x2, 0x3, 0x5, 0x6)
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
SERVER = os.path.join(ROOT, "server")
DEFAULT_OUT = os.path.join(ROOT, "firmware", "first-flash", "bins")


def write_pattern(path, pixel):
    """Write row-major pixels, left pixel in the high nibble."""
    digest = hashlib.sha256()
    with open(path, "wb") as fh:
        for y in range(H):
            row = bytearray(ROW_BYTES)
            for x in range(0, W, 2):
                left, right = pixel(x, y), pixel(x + 1, y)
                assert left in VALID and right in VALID
                row[x // 2] = (left << 4) | right
            fh.write(row)
            digest.update(row)
    assert os.path.getsize(path) == BIN_SIZE
    return digest.hexdigest()


def palette_pixel(x, y):
    # Every controller sees every ink; boundaries are exactly horizontal.
    bands = VALID
    return bands[min(len(bands) - 1, y * len(bands) // H)]


def geometry_pixel(x, y):
    # Border/corners catch crop and rotation. The 600 px seam is deliberately
    # loud so a missing/reversed panel controller cannot hide in an artwork.
    if x < 8 or x >= W - 8 or y < 8 or y >= H - 8:
        return 0x0
    if x < 120 and y < 120:
        return 0x3
    if x >= W - 120 and y < 120:
        return 0x2
    if x < 120 and y >= H - 120:
        return 0x5
    if x >= W - 120 and y >= H - 120:
        return 0x6
    if 596 <= x < 604:
        return 0x3 if (y // 40) % 2 == 0 else 0x5
    if 796 <= y < 804:
        return 0x0
    if x % 200 < 2 or y % 200 < 2:
        return 0x0
    return 0x1


def nibble_pixel(x, y):
    # Top: bytes 0x35. Middle: bytes 0x53. Bottom: bytes 0x01. If pair
    # order is reversed, the left/right color ordering reverses visibly.
    if y < H // 3:
        return 0x3 if x % 2 == 0 else 0x5
    if y < 2 * H // 3:
        return 0x5 if x % 2 == 0 else 0x3
    return 0x0 if x % 2 == 0 else 0x1


def latest_logs(limit=3):
    data = os.path.join(SERVER, "data")
    paths = [os.path.join(data, name) for name in os.listdir(data)] \
        if os.path.isdir(data) else []
    paths = [p for p in paths if p.endswith(".json")]
    return sorted(paths)[-limit:]


def render_portrait(log_path, out_dir):
    if not os.path.isdir(SERVER):
        sys.exit("--logs needs the poster renderer, which is not part of the "
                 "firmware repo. Drop --logs to build the three calibration "
                 "buffers, which are what glass bring-up actually needs.")
    # Renderer paths are cwd-relative by design; import it exactly as its CLI
    # does and restore the caller's cwd afterwards.
    before = os.getcwd()
    prior_now = os.environ.get("FP_FAKE_NOW")
    sys.path.insert(0, SERVER)
    try:
        os.chdir(SERVER)
        art = importlib.import_module("art")
        eink = importlib.import_module("eink")
        with open(os.path.abspath(os.path.join(ROOT, log_path))
                  if not os.path.isabs(log_path) else log_path) as fh:
            log = json.load(fh)
        day = os.path.splitext(os.path.basename(log_path))[0]
        os.environ["FP_FAKE_NOW"] = day
        stem = "portrait-%s" % day
        svg = os.path.join(out_dir, stem + ".svg")
        with open(svg, "w", encoding="utf-8") as fh:
            fh.write(art.MODES["a"](log))
        outs = eink.process(svg, out_dir=out_dir, stem=stem)
        os.remove(svg)
        os.remove(outs["panel"])
        return outs["bin"], outs["preview"]
    finally:
        if prior_now is None:
            os.environ.pop("FP_FAKE_NOW", None)
        else:
            os.environ["FP_FAKE_NOW"] = prior_now
        os.chdir(before)
        sys.path.pop(0)


def validate(path):
    with open(path, "rb") as fh:
        data = fh.read()
    if len(data) != BIN_SIZE:
        raise ValueError("%s: %d bytes, expected %d" %
                         (path, len(data), BIN_SIZE))
    invalid = set()
    for byte in data:
        if byte >> 4 not in VALID:
            invalid.add(byte >> 4)
        if byte & 0x0f not in VALID:
            invalid.add(byte & 0x0f)
    if invalid:
        raise ValueError("%s: invalid palette codes %s" %
                         (path, sorted(invalid)))
    return hashlib.sha256(data).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", nargs="*",
                    help="real daily log JSON files to render as posters "
                         "(needs the poster renderer; default: the latest "
                         "three if it is present, otherwise none)")
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args()
    args.out = os.path.abspath(args.out)
    os.makedirs(args.out, exist_ok=True)

    patterns = (
        ("calibration-01-palette.bin", palette_pixel),
        ("calibration-02-geometry.bin", geometry_pixel),
        ("calibration-03-nibbles.bin", nibble_pixel),
    )
    for name, pixel in patterns:
        path = os.path.join(args.out, name)
        write_pattern(path, pixel)
        print(name)

    logs = args.logs if args.logs is not None else latest_logs()
    if len(logs) > 3:
        ap.error("pass at most three real daily logs")
    for path in logs:
        rendered, preview = render_portrait(path, args.out)
        print(os.path.basename(rendered))
        print(os.path.basename(preview))

    bins = sorted(os.path.join(args.out, name)
                  for name in os.listdir(args.out) if name.endswith(".bin"))
    manifest = os.path.join(args.out, "MANIFEST.sha256")
    with open(manifest, "w", encoding="ascii") as fh:
        for path in bins:
            fh.write("%s  %s\n" % (validate(path), os.path.basename(path)))
    print("validated %d files: 960000 bytes, final six-color palette only" %
          len(bins))
    print(manifest)


if __name__ == "__main__":
    main()
