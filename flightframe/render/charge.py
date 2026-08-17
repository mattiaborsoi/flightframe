"""Please-charge poster: what a frame shows when its battery is nearly flat.

Not a design anyone can pick — the server swaps to it when telemetry reports
low voltage, and swaps back when the frame reports recovery. E-ink keeps it
visible even after the battery finally dies, which is the whole point: a dead
frame explains itself instead of freezing on a stale sky.
"""
from __future__ import annotations

from .. import palette
from ..canvas import Canvas


def render(*, label: str) -> Canvas:
    c = Canvas(background=palette.PAPER)
    ink = palette.INK
    red = palette.HEX["red"]
    blue = palette.HEX["blue"]

    # Battery glyph, centred: body + terminal + one red sliver of charge.
    bx, by, bw, bh = 400, 560, 400, 190
    c.rect(bx, by, bw, bh, "none")
    c.add(f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="24" '
          f'fill="none" stroke="{ink}" stroke-width="10"/>')
    c.add(f'<rect x="{bx + bw + 14}" y="{by + 55}" width="36" height="80" '
          f'rx="10" fill="{ink}"/>')
    c.add(f'<rect x="{bx + 26}" y="{by + 26}" width="52" '
          f'height="{bh - 52}" rx="10" fill="{red}"/>')

    c.text(600, 900, "Please charge me", size=72, weight="500",
           anchor="middle")
    c.text(600, 980, "plug USB-C into the back of the frame",
           size=34, anchor="middle", fill=blue)
    c.text(600, 1040, "the sky returns on its own once charging",
           size=30, anchor="middle")

    c.line(90, 1470, 1110, 1470, width=3)
    c.text(90, 1530, label, size=30, fill=blue)
    return c
