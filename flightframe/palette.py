"""The Spectra 6 palette, quantisation, and the packed panel format.

Panel format, from the frame protocol spec:
  exactly 960,000 bytes, 1200x1600, two pixels per byte, high nibble = left
  pixel, 600 bytes per row. Palette indices are fixed by the hardware and are
  NOT contiguous — 4 is unused.

Colours below are approximations of what the panel actually puts on glass, not
saturated RGB primaries. Quantising against these matters: match a photo's red
against pure #FF0000 and every warm tone collapses into it, because nothing in
the source is anywhere near as bright as a primary. Matching against the ink's
real brick red keeps the separation you actually get.
"""
from __future__ import annotations

from PIL import Image

WIDTH, HEIGHT = 1200, 1600
PACKED_BYTES = 960_000

# name -> (hardware index, approximate on-glass RGB)
INKS: dict[str, tuple[int, tuple[int, int, int]]] = {
    "black":  (0x0, (30, 30, 28)),
    "white":  (0x1, (218, 216, 206)),
    "yellow": (0x2, (196, 167, 46)),
    "red":    (0x3, (158, 59, 50)),
    "blue":   (0x5, (47, 75, 124)),
    "green":  (0x6, (63, 107, 74)),
}

ORDER = ["white", "black", "red", "yellow", "green", "blue"]
RGB = {name: INKS[name][1] for name in INKS}
HEX = {name: "#{:02X}{:02X}{:02X}".format(*INKS[name][1]) for name in INKS}

# Position in ORDER -> hardware nibble. PIL quantises into 0..n-1 in palette
# order, so this is the translation table from PIL index to panel index.
_PIL_TO_PANEL = [INKS[name][0] for name in ORDER]

PAPER = HEX["white"]
INK = HEX["black"]


def _palette_image() -> Image.Image:
    """A P-mode image carrying our six colours, for Image.quantize()."""
    flat: list[int] = []
    for name in ORDER:
        flat.extend(INKS[name][1])
    flat.extend([0, 0, 0] * (256 - len(ORDER)))
    pal = Image.new("P", (1, 1))
    pal.putpalette(flat)
    return pal


PALETTE_IMAGE = _palette_image()


def quantise(img: Image.Image, dither: bool = False) -> Image.Image:
    """Map an RGB image onto the six inks. Returns a P-mode image.

    dither=False (nearest colour) is right for line art, type and flat fills —
    it keeps edges hard, which is what you want when there are no greys to
    soften them with. dither=True (Floyd-Steinberg) is for photographs and
    gradients, and is what produces the characteristic e-ink grain.
    """
    if img.mode != "RGB":
        img = img.convert("RGB")
    mode = Image.Dither.FLOYDSTEINBERG if dither else Image.Dither.NONE
    return img.quantize(palette=PALETTE_IMAGE, dither=mode)


def to_preview(indexed: Image.Image) -> Image.Image:
    """Indexed image back to RGB, as the panel would look."""
    return indexed.convert("RGB")


def pack(indexed: Image.Image) -> bytes:
    """Pack a quantised image into the 960,000-byte panel format."""
    if indexed.size != (WIDTH, HEIGHT):
        raise ValueError(f"expected {WIDTH}x{HEIGHT}, got {indexed.size[0]}x{indexed.size[1]}")
    if indexed.mode != "P":
        raise ValueError(f"expected a quantised P-mode image, got {indexed.mode}")

    src = indexed.tobytes()
    lut = _PIL_TO_PANEL
    out = bytearray(PACKED_BYTES)
    o = 0
    for row_start in range(0, WIDTH * HEIGHT, WIDTH):
        row = src[row_start:row_start + WIDTH]
        for x in range(0, WIDTH, 2):
            out[o] = (lut[row[x]] << 4) | lut[row[x + 1]]
            o += 1

    if len(out) != PACKED_BYTES:
        raise AssertionError(f"packed {len(out)} bytes, expected {PACKED_BYTES}")
    return bytes(out)


def verify(packed: bytes) -> None:
    """Fail loudly on anything the firmware would silently reject.

    A bad image doesn't error on the frame — it just drops the download and the
    wake quietly fails, which is near-impossible to debug from the other end.
    Catch it here instead.
    """
    if len(packed) != PACKED_BYTES:
        raise ValueError(f"packed image is {len(packed)} bytes, must be {PACKED_BYTES}")
    allowed = {ink[0] for ink in INKS.values()}
    seen = set()
    for byte in packed:
        seen.add(byte >> 4)
        seen.add(byte & 0x0F)
    if not seen <= allowed:
        raise ValueError(f"illegal palette indices in packed image: {sorted(seen - allowed)}")


# Altitude bands, shared by every renderer so the views teach each other.
#
# Four bands, not five, and deliberately no blue: blue is reserved for the
# ground — water, roads, rings, axes, scale bars. With six inks and a map
# underneath, a colour cannot mean both "the Thames" and "25,000 feet"; the
# M25 vanished into the traffic the first time it was tried. So warm-to-dark
# climbs into the sky, and blue stays on the earth.
#
# Low is red on purpose: those are the aircraft you can actually see and hear.
BANDS: list[tuple[float, str, str]] = [
    (5_000, "red", "below 1.5 km"),
    (15_000, "yellow", "1.5–4.5 km"),
    (30_000, "green", "4.5–9 km"),
    (float("inf"), "black", "above 9 km"),
]

GROUND = "blue"


def band(altitude_ft: float | None) -> str:
    """Ink name for an altitude."""
    if altitude_ft is None:
        return "black"
    for ceiling, name, _ in BANDS:
        if altitude_ft < ceiling:
            return name
    return "black"


def band_hex(altitude_ft: float | None) -> str:
    return HEX[band(altitude_ft)]
