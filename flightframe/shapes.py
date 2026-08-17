"""Aircraft outlines, keyed by ICAO type code.

Source: https://github.com/RexKramer1/AircraftShapesSVG (GPL-3.0) — 182 top-view
vector shapes whose filenames are ICAO type codes, which is exactly what
adsb.lol reports in its `t` field. Fetched at runtime and cached rather than
vendored, so the GPL artwork stays out of this repository.

Two jobs beyond fetching:

  * Bounding boxes. Each shape carries its own viewBox with a different origin
    (A20N is "-22 -21 80 80", A388 is "-0.25 -4 80 80"), so shapes sit
    differently in frame and cannot simply be dropped into a grid. The real
    content bounds are recovered by rasterising once and reading the alpha
    channel, then cached.

  * Stroke weight. The source is stroke-width 0.264583 in an 80-unit viewBox —
    a hairline. On e-ink, with no anti-aliasing to soften it, a hairline either
    disappears or breaks into dots. Every path is stroked with no fills, so the
    weight is ours to set.
"""
from __future__ import annotations

import io
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

BASE = "https://raw.githubusercontent.com/RexKramer1/AircraftShapesSVG/main/Shapes%20SVG"

# Types the library has no shape for, mapped to their nearest relative.
# Measured against live traffic: raw coverage was 87%, these take it past 96%.
ALIASES: dict[str, str] = {
    "A319": "A320",   # same aircraft, shorter fuselage
    "A19N": "A20N",
    "E190": "E195",   # same family
    "E290": "E195",
    "E195": "E195",
    "E75L": "E170",
    "E75S": "E170",
    "E170": "E170",
    "AT76": "AT75",   # ATR 72-600 vs -500
    "AT72": "AT75",
    "GLEX": "GL5T",   # Global Express is that family
    "GL7T": "GL5T",
    "DA40": "DA42",
    "C25A": "C25B",
    "C56X": "C25B",
    "B733": "B737",
    "B735": "B737",
    "B39M": "B39M",
}

# Not aircraft. adsb.lol surfaces ground installations and test targets too.
NOT_AIRCRAFT = {"TWR", "GRND", "TEST"}

FALLBACK = "Unidentified"

_VIEWBOX = re.compile(r'viewBox="([^"]+)"')
_PATH_D = re.compile(r'<path\b[^>]*?\sd="([^"]+)"', re.S)


@dataclass(frozen=True)
class Shape:
    code: str
    viewbox: tuple[float, float, float, float]
    paths: tuple[str, ...]
    bbox: tuple[float, float, float, float]   # x, y, w, h in user units

    def group(
        self,
        cx: float,
        cy: float,
        size: float,
        *,
        stroke: str = "#000000",
        fill: str = "none",
        stroke_width: float = 1.6,
        rotate: float = 0.0,
        detail: bool = True,
    ) -> str:
        """An SVG <g> with the shape centred on (cx, cy), scaled to `size`.

        `size` is the longest dimension in output pixels. `rotate` is degrees
        clockwise from north, matching how ADS-B reports track.

        The first path is the outline; the rest are detail (control surfaces,
        engine cowls, door lines). Only the outline takes the fill — filling the
        detail layer would flood the shape.
        """
        bx, by, bw, bh = self.bbox
        scale = size / max(bw, bh) if max(bw, bh) else 1.0
        sw = stroke_width / scale          # keep the stroke constant on screen

        paths = self.paths if detail else self.paths[:1]
        body = []
        for i, d in enumerate(paths):
            body.append(
                f'<path d="{d}" fill="{fill if i == 0 else "none"}" stroke="{stroke}" '
                f'stroke-width="{sw:.4f}" stroke-linejoin="round" stroke-linecap="round"/>'
            )
        inner = "".join(body)
        return (
            f'<g transform="translate({cx:.2f},{cy:.2f}) rotate({rotate:.2f}) '
            f'scale({scale:.5f}) translate({-(bx + bw / 2):.4f},{-(by + bh / 2):.4f})">'
            f"{inner}</g>"
        )


class Library:
    def __init__(self, cache_dir: Path, user_agent: str = "flightframe/0.1"):
        self.dir = cache_dir / "shapes"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.user_agent = user_agent
        self.bbox_path = cache_dir / "shape_bboxes.json"
        self._bboxes: dict[str, list[float]] = {}
        if self.bbox_path.exists():
            try:
                self._bboxes = json.loads(self.bbox_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                self._bboxes = {}
        self._loaded: dict[str, Shape | None] = {}

    # -- fetching ---------------------------------------------------------

    def _source(self, code: str) -> str | None:
        path = self.dir / f"{code}.svg"
        if path.exists():
            return path.read_text(encoding="utf-8")
        url = f"{BASE}/{urllib.parse.quote(code)}.svg"
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                if resp.status != 200:
                    return None
                text = resp.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError, OSError, UnicodeDecodeError):
            return None
        if "<svg" not in text:
            return None
        path.write_text(text, encoding="utf-8")
        return text

    # -- bounding boxes ---------------------------------------------------

    def _measure(self, code: str, viewbox, paths) -> tuple[float, float, float, float]:
        """Content bounds in user units, found by rasterising and reading alpha."""
        if code in self._bboxes:
            return tuple(self._bboxes[code])          # type: ignore[return-value]

        import cairosvg
        from PIL import Image

        vx, vy, vw, vh = viewbox
        px = 400
        doc = (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vx} {vy} {vw} {vh}" '
            f'width="{px}" height="{px}">'
            + "".join(f'<path d="{d}" fill="none" stroke="#000" stroke-width="0.5"/>'
                      for d in paths)
            + "</svg>"
        )
        png = cairosvg.svg2png(bytestring=doc.encode("utf-8"))
        img = Image.open(io.BytesIO(png)).convert("RGBA")
        bounds = img.split()[3].getbbox()
        if not bounds:
            box = (vx, vy, vw, vh)
        else:
            x0, y0, x1, y1 = bounds
            box = (vx + x0 / px * vw, vy + y0 / px * vh,
                   (x1 - x0) / px * vw, (y1 - y0) / px * vh)

        self._bboxes[code] = list(box)
        self.bbox_path.write_text(json.dumps(self._bboxes), encoding="utf-8")
        return box

    # -- public -----------------------------------------------------------

    def resolve(self, icao_type: str | None) -> str:
        """ICAO type -> the shape code that should be drawn for it."""
        if not icao_type:
            return FALLBACK
        code = icao_type.strip().upper()
        if code in NOT_AIRCRAFT:
            return FALLBACK
        return ALIASES.get(code, code)

    def get(self, icao_type: str | None) -> Shape | None:
        code = self.resolve(icao_type)
        if code in self._loaded:
            return self._loaded[code]

        text = self._source(code)
        if text is None and code != FALLBACK:
            code, text = FALLBACK, self._source(FALLBACK)
        if text is None:
            self._loaded[code] = None
            return None

        vb = _VIEWBOX.search(text)
        viewbox = tuple(float(v) for v in vb.group(1).replace(",", " ").split()) if vb \
            else (0.0, 0.0, 80.0, 80.0)
        paths = tuple(_PATH_D.findall(text))
        if not paths:
            self._loaded[code] = None
            return None

        shape = Shape(code, viewbox, paths, self._measure(code, viewbox, paths))  # type: ignore[arg-type]
        self._loaded[code] = shape
        return shape

    def prefetch(self, codes) -> dict[str, bool]:
        return {c: self.get(c) is not None for c in dict.fromkeys(codes)}
