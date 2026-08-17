"""Compose a poster as SVG, rasterise it, quantise to six inks, pack for the panel.

Composing in SVG rather than drawing into a bitmap is worth it here: the
aircraft shapes are already SVG, transforms make placement and rotation
trivial, and text layout comes free. Everything rasterises once at 1200x1600.

A note on anti-aliasing. Cairo anti-aliases, and the panel has no greys to
render those soft edges with, so every intermediate pixel gets snapped to
whichever ink is nearest. For line art and type that is exactly what you want —
hard edges. For photographs it is not, which is what `dither=True` is for.
"""
from __future__ import annotations

import hashlib
import html
import io
import os
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from . import palette

# Cairo resolves these against the host's fonts. The Dockerfile installs
# DejaVu so that a Pi renders the same thing your laptop does; if you iterate
# locally on macOS, Helvetica will win and metrics will differ very slightly.
SANS = "Helvetica Neue, Helvetica, Arial, DejaVu Sans, sans-serif"
SERIF = "Georgia, Times New Roman, DejaVu Serif, serif"


def esc(text: str) -> str:
    return html.escape(str(text), quote=True)


@dataclass
class Canvas:
    width: int = palette.WIDTH
    height: int = palette.HEIGHT
    background: str = palette.PAPER
    parts: list[str] = field(default_factory=list)

    def add(self, markup: str) -> None:
        self.parts.append(markup)

    # -- primitives -------------------------------------------------------

    def rect(self, x, y, w, h, fill, **kw) -> None:
        extra = "".join(f' {k.replace("_", "-")}="{v}"' for k, v in kw.items())
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{fill}"{extra}/>')

    def line(self, x1, y1, x2, y2, stroke=palette.INK, width=2.0, **kw) -> None:
        extra = "".join(f' {k.replace("_", "-")}="{v}"' for k, v in kw.items())
        self.add(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
                 f'stroke="{stroke}" stroke-width="{width}"{extra}/>')

    def circle(self, cx, cy, r, fill="none", stroke=palette.INK, width=2.0) -> None:
        self.add(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{fill}" '
                 f'stroke="{stroke}" stroke-width="{width}"/>')

    def path(self, d, fill="none", stroke=palette.INK, width=2.0, **kw) -> None:
        extra = "".join(f' {k.replace("_", "-")}="{v}"' for k, v in kw.items())
        self.add(f'<path d="{d}" fill="{fill}" stroke="{stroke}" '
                 f'stroke-width="{width}"{extra}/>')

    def text(self, x, y, content, size=32, fill=palette.INK, *, anchor="start",
             family=SANS, weight="400", spacing=0.0, style="normal") -> None:
        self.add(
            f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" '
            f'font-weight="{weight}" font-style="{style}" fill="{fill}" '
            f'text-anchor="{anchor}" letter-spacing="{spacing}" '
            f'xml:space="preserve">{esc(content)}</text>'
        )

    def halo_text(self, x, y, content, size=32, fill=palette.INK, *,
                  anchor="start", weight="400", halo=palette.PAPER,
                  halo_width=6.0, family=SANS) -> None:
        """Text with a paper-coloured outline behind it.

        The only way to keep a label legible over a dense plot when there are
        no greys to fade the background with. Drawn as a thick stroke of the
        same glyphs, then the fill on top.
        """
        common = (f'font-family="{family}" font-size="{size}" font-weight="{weight}" '
                  f'text-anchor="{anchor}" xml:space="preserve"')
        body = esc(content)
        self.add(f'<text x="{x}" y="{y}" {common} fill="none" stroke="{halo}" '
                 f'stroke-width="{halo_width}" stroke-linejoin="round">{body}</text>')
        self.add(f'<text x="{x}" y="{y}" {common} fill="{fill}">{body}</text>')

    def polyline(self, points, stroke, width=4.0, opacity=1.0) -> None:
        pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
        self.add(f'<polyline points="{pts}" fill="none" stroke="{stroke}" '
                 f'stroke-width="{width}" stroke-linecap="round" '
                 f'stroke-linejoin="round" opacity="{opacity}"/>')

    # -- output -----------------------------------------------------------

    def svg(self) -> str:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.width}" '
            f'height="{self.height}" viewBox="0 0 {self.width} {self.height}">'
            f'<rect width="{self.width}" height="{self.height}" fill="{self.background}"/>'
            + "".join(self.parts)
            + "</svg>"
        )

    def rasterise(self) -> Image.Image:
        import cairosvg
        png = cairosvg.svg2png(
            bytestring=self.svg().encode("utf-8"),
            output_width=self.width,
            output_height=self.height,
        )
        return Image.open(io.BytesIO(png)).convert("RGB")


def _atomic_write(path: Path, data: bytes) -> None:
    """Write via a temp file and rename.

    The web server reads these while the renderer writes them, so a poll can
    otherwise catch a half-written PNG. Once the device server exists the same
    race hands the frame a truncated .bin, which it drops on the hash check —
    a wake that fails silently and is near-impossible to diagnose from the
    other end. os.replace is atomic within a filesystem.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def render(canvas: Canvas, out_dir: Path, name: str, *, dither: bool = False,
           keep_svg: bool = False) -> dict[str, Path]:
    """Rasterise, quantise, pack, and write everything to out_dir.

    Produces the .bin the frame consumes, plus a PNG showing what the panel
    will actually look like — same six inks, so it is a true preview rather
    than an idealised one.

    Skips writing entirely when the packed bytes are unchanged. That matters
    well beyond saving a little disk I/O: the firmware compares image_hash and
    only blits when it differs, so an untouched .bin means the frame wakes for
    ~5 seconds instead of spending ~15 seconds driving the panel. On a poster
    that only changes every quarter of an hour, most wakes should cost nothing.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}

    indexed = palette.quantise(canvas.rasterise(), dither=dither)
    packed = palette.pack(indexed)
    palette.verify(packed)

    binary = out_dir / f"{name}.bin"
    preview = out_dir / f"{name}.png"
    written["bin"], written["png"] = binary, preview

    # Both files must be present to skip. Checking only the .bin meant a
    # deleted or never-written PNG was never regenerated, because the packed
    # bytes matched and the whole write was short-circuited.
    if (binary.exists() and preview.exists()
            and binary.stat().st_size == len(packed)
            and hashlib.sha256(binary.read_bytes()).digest()
            == hashlib.sha256(packed).digest()):
        written["unchanged"] = True
        return written

    if keep_svg:
        svg = out_dir / f"{name}.svg"
        _atomic_write(svg, canvas.svg().encode("utf-8"))
        written["svg"] = svg

    buf = io.BytesIO()
    palette.to_preview(indexed).save(buf, format="PNG")
    _atomic_write(preview, buf.getvalue())
    # Keep the outgoing image one generation longer. A frame that was told
    # a hash seconds before this write must still be able to download it —
    # without this, roughly one wake in thirty raced the renderer, 404'd,
    # and cost a silent backoff cycle.
    if binary.exists():
        os.replace(binary, binary.with_suffix(".bin.prev"))
    _atomic_write(binary, packed)
    return written
