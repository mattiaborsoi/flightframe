#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 YODE PTE LTD
# SPDX-License-Identifier: Apache-2.0
"""Build one unit's encrypted NVS + NVS-key partitions.

The setup credential is accepted only from a protected file or a hidden
interactive prompt. It is never accepted as a command-line value and is never
printed. Run this inside an activated ESP-IDF 5.3 environment (or its Docker
image); the temporary plaintext CSV is mode 0600 and removed on exit.

Example:
  python tools/build_factory_nvs.py \
      --mac 02:00:00:00:00:01 \
      --credential-file /secure/unit-0001.credential \
      --out /secure/unit-0001
"""
import argparse
import csv
import getpass
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile


NVS_SIZE = 0x6000
NVS_OFFSET = 0x9000
NVS_KEYS_OFFSET = 0x12000
FACTORY_NAMESPACE = "fp_factory"
FACTORY_KEY = "setup_cred"


def normalize_mac(raw):
    compact = re.sub(r"[:-]", "", raw.strip())
    if not re.fullmatch(r"[0-9A-Fa-f]{12}", compact):
        raise ValueError("MAC must contain exactly 12 hexadecimal digits")
    return ":".join(compact[i:i + 2] for i in range(0, 12, 2)).lower()


def read_credential(path):
    if path:
        info = os.stat(path)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("credential path must be a regular file")
        # Reject group/world-readable secret inputs instead of silently
        # copying a credential that was already exposed.
        if info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise ValueError("credential file must be mode 0600 or stricter")
        with open(path, "r", encoding="ascii") as handle:
            value = handle.read().rstrip("\r\n")
    elif not sys.stdin.isatty():
        value = sys.stdin.read().rstrip("\r\n")
    else:
        value = getpass.getpass("Factory setup credential: ")
    encoded = value.encode("ascii")
    if len(encoded) < 32 or len(encoded) > 159:
        raise ValueError("credential must be 32..159 ASCII bytes")
    if any(byte < 0x21 or byte > 0x7e for byte in encoded):
        raise ValueError("credential must contain printable non-space ASCII")
    return value


def find_generator(explicit):
    candidates = []
    if explicit:
        candidates.append(explicit)
    idf_path = os.environ.get("IDF_PATH")
    if idf_path:
        candidates.append(os.path.join(
            idf_path, "components", "nvs_flash",
            "nvs_partition_generator", "nvs_partition_gen.py"))
    for candidate in candidates:
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)
    raise ValueError(
        "nvs_partition_gen.py not found; activate ESP-IDF 5.3 or pass "
        "--generator")


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(
        description="build encrypted per-unit factory NVS artifacts")
    parser.add_argument("--mac", required=True, help="unit Wi-Fi STA MAC")
    parser.add_argument(
        "--credential-file",
        help="mode-0600 file; omit to read hidden prompt or stdin")
    parser.add_argument(
        "--out", required=True,
        help="new empty output directory on protected factory storage")
    parser.add_argument(
        "--generator", help="explicit nvs_partition_gen.py path")
    args = parser.parse_args()

    try:
        mac = normalize_mac(args.mac)
        credential = read_credential(args.credential_file)
        generator = find_generator(args.generator)
    except (OSError, UnicodeError, ValueError) as exc:
        parser.error(str(exc))

    out_dir = os.path.abspath(args.out)
    if os.path.exists(out_dir):
        parser.error("output directory already exists; refusing to overwrite")
    os.makedirs(out_dir, mode=0o700)
    nvs_path = os.path.join(out_dir, "nvs.bin")
    keys_path = os.path.join(out_dir, "nvs_keys.bin")

    try:
        with tempfile.TemporaryDirectory(prefix="flightportrait-nvs-") as tmp:
            os.chmod(tmp, 0o700)
            csv_path = os.path.join(tmp, "factory.csv")
            with open(csv_path, "w", newline="", encoding="ascii") as handle:
                writer = csv.writer(handle, lineterminator="\n")
                writer.writerow(("key", "type", "encoding", "value"))
                writer.writerow((FACTORY_NAMESPACE, "namespace", "", ""))
                writer.writerow((FACTORY_KEY, "data", "string", credential))
            os.chmod(csv_path, 0o600)
            subprocess.run(
                [
                    sys.executable, generator, "encrypt",
                    csv_path, nvs_path, hex(NVS_SIZE),
                    "--keygen", "--keyfile", keys_path,
                ],
                check=True,
            )
        credential_hash = hashlib.sha256(
            credential.encode("ascii")).hexdigest()
    finally:
        # Drop the reference; the mode-0600 temporary plaintext file has
        # already been removed by TemporaryDirectory.
        credential = "\0" * len(credential)

    if os.path.getsize(nvs_path) != NVS_SIZE:
        parser.error("generator returned an unexpected NVS partition size")
    if os.path.getsize(keys_path) != 0x1000:
        parser.error("generator returned an unexpected NVS-key size")
    os.chmod(nvs_path, 0o600)
    os.chmod(keys_path, 0o600)
    manifest = {
        "mac": mac,
        "credential_sha256": credential_hash,
        "namespace": FACTORY_NAMESPACE,
        "key": FACTORY_KEY,
        "partitions": [
            {
                "file": "nvs.bin",
                "offset": hex(NVS_OFFSET),
                "size": NVS_SIZE,
                "sha256": sha256_file(nvs_path),
            },
            {
                "file": "nvs_keys.bin",
                "offset": hex(NVS_KEYS_OFFSET),
                "size": 0x1000,
                "sha256": sha256_file(keys_path),
            },
        ],
    }
    manifest_path = os.path.join(out_dir, "manifest.json")
    with open(manifest_path, "w", encoding="ascii") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.chmod(manifest_path, 0o600)
    print("built encrypted factory NVS for %s" % mac)
    print("artifacts: %s" % out_dir)
    print("flash offsets: nvs.bin@0x9000 nvs_keys.bin@0x12000")
    print("credential was not printed; verify its hash in manifest.json")


if __name__ == "__main__":
    main()
