"""Specimen plate: a few aircraft, staggered, on a flooded background.

Deliberately close to the reference so it can be judged side by side with the
alternatives. Two honest differences from the original:

  * These are top-view outlines filled with the operator's brand colour, not
    side-view renders wearing the actual livery. Liveries are trademarked
    artwork; see airlines.py.

  * A flooded background is the most expensive thing this panel can display —
    every pixel flips on every refresh. At a 15-minute cadence that works
    directly against the battery budget. `background="white"` is the cheap
    variant and still reads well.
"""
from __future__ import annotations

from datetime import datetime

from .. import airlines, palette
from ..canvas import SERIF, Canvas
from ..shapes import FALLBACK
from ..sources import Aircraft
from ..units import Units

SLOTS = 4


def render(
    aircraft: list[Aircraft],
    *,
    label: str,
    lat: float,
    lon: float,
    shapes,
    units: Units,
    background: str = "blue",
    edition: int = 1,
    show_coords: bool = False,
    title: str = "FLIGHT FRAME",
    now: datetime | None = None,
) -> Canvas:
    now = now or datetime.now()
    bg = palette.HEX[background]
    # On a flooded background the paper tone is the only usable "ink" for type;
    # on white, black is.
    fg = palette.HEX["white"] if background != "white" else palette.HEX["black"]

    c = Canvas(background=bg)

    # -- header -----------------------------------------------------------
    c.text(80, 104, title, size=38, fill=fg, family=SERIF, spacing=9)
    c.line(80, 146, 1120, 146, stroke=fg, width=2.5)

    # -- aircraft ---------------------------------------------------------
    # Each label sits centred directly beneath its own aircraft. An earlier
    # version put the text block on the opposite side of the spread, which
    # looked balanced and was genuinely ambiguous — with four aircraft
    # staggered down the page there was no way to tell which label went with
    # which. Proximity beats symmetry.
    chosen = _pick(aircraft, SLOTS, shapes)
    for i, ac in enumerate(chosen):
        cy = 268 + i * 310
        left = i % 2 == 0
        cx = 340 if left else 860
        brand = airlines.lookup(ac.airline_icao, ac.airline)

        shape = shapes.get(ac.type)
        if shape:
            body = _body_ink(brand, background)
            # A white-bodied aircraft needs its outline to carry the shape on a
            # flooded background; on colour the fill does the work.
            outline = (fg if body == "white" and background != "white"
                       else palette.HEX["black"])
            c.add(shape.group(
                cx, cy, 215,
                fill=palette.HEX[body],
                stroke=outline,
                stroke_width=2.2,
                rotate=90 if left else 270,
            ))

        operator = brand.name or ac.airline or ac.callsign or "Unknown operator"
        c.text(cx, cy + 148, operator, size=33, fill=fg, anchor="middle", weight="500")
        c.text(cx, cy + 184, ac.type_name or ac.type or "", size=25,
               fill=fg, anchor="middle")

        bits = []
        if ac.route:
            bits.append(ac.route)
        if ac.altitude_ft is not None:
            bits.append(units.altitude_str(ac.altitude_ft))
        if ac.registration:
            bits.append(ac.registration)
        c.text(cx, cy + 218, " · ".join(bits), size=23, fill=fg,
               anchor="middle", spacing=0.4)

    # -- footer -----------------------------------------------------------
    c.line(80, 1470, 1120, 1470, stroke=fg, width=2.5)
    parts = [f"№ {edition}", now.strftime("%d %B %Y").upper()]
    if show_coords:
        parts.append(f"{abs(lat):.4f}° {'N' if lat >= 0 else 'S'} "
                     f"{abs(lon):.4f}° {'E' if lon >= 0 else 'W'}")
    parts.append(f"RECEIVED OVER {label.upper()}")
    parts.append(f"{len(chosen)} AIRCRAFT")
    c.text(600, 1522, "  ·  ".join(parts), size=19, fill=fg, anchor="middle", spacing=1.6)

    return c


def _body_ink(brand: airlines.Brand, background: str) -> str:
    """The brand colour, unless it would vanish into the background.

    Six inks and no shading means an airline whose colour matches the
    background disappears entirely apart from its outline — Ryanair blue on a
    blue poster. Fall back to the accent, then to whichever of white/black the
    background is not.
    """
    if brand.body != background:
        return brand.body
    if brand.accent != background:
        return brand.accent
    return "black" if background == "white" else "white"


def _pick(aircraft: list[Aircraft], n: int, shapes) -> list[Aircraft]:
    """Choose a spread worth looking at rather than simply the nearest few.

    Two failure modes to avoid, both seen on real data. Nearest-n returns four
    identical easyJet A320s, because that is what short-haul London is. But
    preferring rare types is worse — it surfaces exactly the helicopters,
    trainers and light twins that orbit closest to the ground, none of which
    have a shape in the library, so the poster fills with fallback silhouettes
    and no airline at all.

    So: score commercial traffic that we can actually draw, then break ties on
    type variety, then on proximity.
    """
    scored: list[tuple[float, Aircraft]] = []
    for ac in aircraft:
        score = 0.0
        if shapes.resolve(ac.type) != FALLBACK and shapes.get(ac.type):
            score += 100                       # we can draw it properly
        if ac.airline_icao or ac.airline:
            score += 60                        # a known operator
        if ac.route:
            score += 40                        # somewhere to and from
        if (ac.altitude_ft or 0) > 3_000:
            score += 20                        # airborne, not circling the field
        score -= ac.distance_nm                # nearer is better, gently
        scored.append((score, ac))

    scored.sort(key=lambda pair: -pair[0])

    chosen: list[Aircraft] = []
    seen_types: set[str] = set()
    for _score, ac in scored:                  # first pass: one per type
        key = ac.type or ac.hex
        if key not in seen_types:
            seen_types.add(key)
            chosen.append(ac)
        if len(chosen) == n:
            return chosen
    for _score, ac in scored:                  # top up if traffic is thin
        if ac not in chosen:
            chosen.append(ac)
        if len(chosen) == n:
            break
    return chosen
