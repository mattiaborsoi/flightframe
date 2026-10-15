"""Over-the-air firmware for the frames.

The server, not the frame, decides who updates. A frame is offered the newest
image in the store only when it has been opted in (``devices.fw_auto``) and
reports an older version. Every other frame, including any frame running a
build that predates this module, keeps receiving ``"firmware": null`` exactly
as before.

That matters more than it looks. The firmware treats a ``firmware`` field that
is present but not a well-formed ``{url, sha256, version}`` object as a broken
response and fails the WHOLE poll, not just the update. An earlier version of
this server emitted a bare URL string there; had a frame ever been flagged for
it, that frame would have stopped updating its poster entirely. So ``offer()``
returns either a complete object or ``None``, never anything in between.

Images live in ``DATA/firmware/<sha256>.bin`` and are served by digest, the
same capability scheme as posters: the firmware downloads without its bearer
token, and a 256-bit name cannot be guessed. The version is read from the
image's own app descriptor, so a file can never be mislabelled by hand.
"""
from __future__ import annotations

import hashlib
import re
import struct
from pathlib import Path

ESP_IMAGE_MAGIC = 0xE9
APP_DESC_OFFSET = 32          # image header (24 B) + first segment header (8 B)
APP_DESC_MAGIC = 0xABCD5432
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def image_version(data: bytes) -> str | None:
    """The version string compiled into an ESP-IDF app image, or None if
    this is not one. Reads esp_app_desc_t: magic, secure_version, two
    reserved words, then version[32]."""
    if len(data) < APP_DESC_OFFSET + 48 or data[0] != ESP_IMAGE_MAGIC:
        return None
    if struct.unpack_from("<I", data, APP_DESC_OFFSET)[0] != APP_DESC_MAGIC:
        return None
    raw = data[APP_DESC_OFFSET + 16:APP_DESC_OFFSET + 48]
    version = raw.split(b"\0", 1)[0].decode("ascii", "replace").strip()
    return version or None


def parse_version(text: str | None) -> tuple[int, int, int] | None:
    """'1.0.2' -> (1, 0, 2). Anything else is not comparable: no offer."""
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", (text or "").strip())
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


class Store:
    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir) / "firmware"

    def add(self, source: Path) -> dict:
        data = Path(source).read_bytes()
        version = image_version(data)
        if version is None:
            raise ValueError(f"{source} is not an ESP32 app image")
        if parse_version(version) is None:
            raise ValueError(f"version {version!r} is not MAJOR.MINOR.PATCH; "
                             "the server could not tell whether it is newer")
        for existing in self.images():
            if existing["version"] == version:
                raise ValueError(f"version {version} is already in the store; "
                                 "a version must never name two builds")
        digest = hashlib.sha256(data).hexdigest()
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / f".{digest}.tmp"
        tmp.write_bytes(data)
        tmp.replace(self.dir / f"{digest}.bin")
        return {"version": version, "sha256": digest, "size": len(data)}

    def images(self) -> list[dict]:
        out = []
        for path in sorted(self.dir.glob("*.bin")) if self.dir.exists() else []:
            if not _HEX64.match(path.stem):
                continue
            with path.open("rb") as fh:
                head = fh.read(APP_DESC_OFFSET + 48)
            version = image_version(head)
            if version and parse_version(version):
                out.append({"version": version, "sha256": path.stem,
                            "size": path.stat().st_size})
        return sorted(out, key=lambda i: parse_version(i["version"]))

    def latest(self) -> dict | None:
        images = self.images()
        return images[-1] if images else None

    def path_for(self, digest: str) -> Path | None:
        if not _HEX64.match(digest or ""):
            return None
        path = self.dir / f"{digest}.bin"
        return path if path.is_file() else None

    def remove(self, version: str) -> bool:
        for image in self.images():
            if image["version"] == version:
                (self.dir / f"{image['sha256']}.bin").unlink()
                return True
        return False


def offer(store: Store, device: dict, reported_version: str | None,
          base_url: str, prefix: str) -> dict | None:
    """The firmware field for one poll: a complete offer, or None."""
    if not device.get("fw_auto"):
        return None
    latest = store.latest()
    if latest is None:
        return None
    current = parse_version(reported_version)
    newest = parse_version(latest["version"])
    if current is None or newest is None or newest <= current:
        return None
    url = f"{base_url}{prefix}/fw/{latest['sha256']}.bin"
    if len(url) >= 768 or len(latest["version"]) >= 32:
        return None                       # would not fit the firmware's buffers
    return {"url": url, "sha256": latest["sha256"],
            "version": latest["version"]}
