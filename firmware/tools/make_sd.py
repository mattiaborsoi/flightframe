#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 YODE PTE LTD
# SPDX-License-Identifier: Apache-2.0
"""Drop a rendered panel buffer onto a mounted SD card as
/flightportrait.bin for the arduino-sd-demo. Stdlib only.

Any 960,000-byte buffer in the PROTOCOL.md §1 format works — the
calibration images in first-flash/bins are the usual starting point:

  python3 tools/make_sd.py --src first-flash/bins/calibration-01-palette.bin
  python3 tools/make_sd.py --src path/to/x.bin --volume /Volumes/FLIGHTPORTRAIT

With no --src it picks the newest render from the poster renderer's
output directory, which exists only in the full FlightPortrait tree that
this firmware is developed in; elsewhere, pass --src.
"""
import argparse
import glob
import hashlib
import os
import shutil
import sys

BIN_SIZE = 960000
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))


def newest_render():
    out = os.path.join(ROOT, "server", "out")
    if not os.path.isdir(out):
        sys.exit("pass --src: no renderer output directory here (the poster "
                 "renderer is not part of the firmware repo). Any "
                 "%d-byte panel buffer works, e.g. "
                 "first-flash/bins/calibration-01-palette.bin" % BIN_SIZE)
    cands = [p for p in glob.glob(os.path.join(out, "eink*.bin"))
             if os.path.getsize(p) == BIN_SIZE]
    if not cands:
        sys.exit("no %d-byte render in %s — run `python3 main.py render` "
                 "(or demo) in server/ first" % (BIN_SIZE, out))
    return max(cands, key=os.path.getmtime)


def find_volume():
    vols = [v for v in glob.glob("/Volumes/*")
            if os.access(v, os.W_OK) and not os.path.islink(v)
            and v != "/Volumes/Macintosh HD"]
    if len(vols) == 1:
        return vols[0]
    sys.exit("pass --volume explicitly; candidates: %s" % (vols or "none"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", help="960,000-byte panel .bin (default: the "
                                  "newest poster render, full tree only)")
    ap.add_argument("--volume", help="mounted SD card (default: autodetect)")
    args = ap.parse_args()

    src = args.src or newest_render()
    size = os.path.getsize(src)
    if size != BIN_SIZE:
        sys.exit("%s is %d bytes, panel buffer must be exactly %d"
                 % (src, size, BIN_SIZE))

    vol = args.volume or find_volume()
    dst = os.path.join(vol, "flightportrait.bin")
    shutil.copyfile(src, dst)

    with open(dst, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    print("%s -> %s" % (src, dst))
    print("sha256:%s" % digest)
    print("eject the card, insert into the reTerminal, press reset.")


if __name__ == "__main__":
    main()
