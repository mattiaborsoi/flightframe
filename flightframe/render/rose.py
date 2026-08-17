"""Destination rose: the places that passed overhead, and how far away they are.

Not a chart. The point of this poster is the moment you look up from the sofa
and register that something above your roof is on its way to Guam — so the
place names are the subject and everything else is scaffolding to be kept out
of their way. An earlier version had dotted range rings, compass points and a
log-scale legend, and read as instrumentation.

What survives from that version, because it is what makes the idea work:

  * True bearing. "That way is Tokyo" is the whole appeal, and it is only
    honest if the ray really points at Tokyo.
  * Logarithmic radius. Linear puts Dublin and Lanzarote on top of each other
    and wastes three quarters of the poster.

What changed: destinations are chosen one per angular sector, furthest first.
That spreads them evenly around the compass instead of bunching every
transatlantic departure into the same north-west smear, and it naturally
surfaces the remote ones — which are the ones worth dreaming about.
"""
from __future__ import annotations

import math
from datetime import datetime

from .. import palette
from ..canvas import Canvas
from ..units import Units

CX, CY = 600.0, 800.0
R_MIN, R_MAX = 64.0, 395.0
NEAR_KM, FAR_KM = 400.0, 16_000.0

# Anything closer than this is a commute, not a destination.
MIN_INTERESTING_KM = 700.0
SECTORS = 12

DISTANCE_BANDS: list[tuple[float, str]] = [
    (1_500, "red"),
    (4_000, "yellow"),
    (9_000, "green"),
    (float("inf"), "black"),
]


def band(km: float) -> str:
    for ceiling, ink in DISTANCE_BANDS:
        if km < ceiling:
            return ink
    return "black"


def render(
    destinations: list[dict],
    *,
    label: str,
    units: Units,
    since: datetime | None = None,
    until: datetime | None = None,
    now: datetime | None = None,
) -> Canvas:
    """`destinations` is [{city, km, bearing, iata}, ...], already deduplicated."""
    now = now or datetime.now()
    c = Canvas(background=palette.PAPER)
    blue = palette.HEX["blue"]

    chosen = _by_sector(destinations)
    furthest = max(chosen, key=lambda d: d["km"]) if chosen else None

    c.text(90, 152, "Somewhere else", size=64, weight="500", spacing=0.5)
    if furthest:
        c.text(90, 210, f"{len(chosen)} places passed overhead · furthest "
                        f"{_short(furthest['city'])}, "
                        f"{units.from_km(furthest['km']):,.0f} "
                        f"{units.distance_suffix}", size=30, fill=blue)
    c.line(90, 246, 1110, 246, width=3)

    placed: list[tuple[float, float, float, float]] = []

    for dest in sorted(chosen, key=lambda d: -d["km"]):
        rad = math.radians(dest["bearing"])
        sin, cos = math.sin(rad), math.cos(rad)
        r = _radius(dest["km"])
        x0, y0 = CX + sin * R_MIN, CY - cos * R_MIN
        x1, y1 = CX + sin * r, CY - cos * r
        colour = palette.HEX[band(dest["km"])]

        c.path(f"M{x0:.1f} {y0:.1f}L{x1:.1f} {y1:.1f}", stroke=colour, width=2.6)
        c.circle(x1, y1, 8, fill=colour, stroke="none", width=0)

        # The furthest gets the biggest type. It is the one worth noticing.
        hero = furthest is not None and dest is furthest
        size = 40 if hero else 30
        city = _short(dest["city"], 16 if hero else 15)
        distance = f"{units.from_km(dest['km']):,.0f} {units.distance_suffix}"

        anchor = "start" if sin > 0.15 else "end" if sin < -0.15 else "middle"
        gap = 20
        lx = x1 + (gap if anchor == "start" else -gap if anchor == "end" else 0)
        ly = y1 + (9 if abs(sin) >= 0.15 else (-30 if cos > 0 else 44))

        # 0.60: the 0.53 glyph estimate under-measured medium-weight names —
        # "Melbourne" at furthest-city size overran the right edge.
        width = max(len(city) * size * 0.60, len(distance) * 13.0)
        left = {"start": lx, "end": lx - width, "middle": lx - width / 2}[anchor]
        # 34px margin: the physical mount overlaps the image edge by ~10px.
        left = max(34.0, min(left, 1166.0 - width))
        box = (left - 8, ly - size - 6, left + width + 8, ly + 34)
        if box[1] < 270 or box[3] > 1300 or any(_overlaps(box, o) for o in placed):
            continue
        placed.append(box)

        lx = {"start": left, "end": left + width, "middle": left + width / 2}[anchor]
        c.halo_text(lx, ly, city, size=size, anchor=anchor, weight="500", halo_width=8)
        c.halo_text(lx, ly + 30, distance, size=23, anchor=anchor, fill=blue,
                    halo_width=7)

    # -- home -------------------------------------------------------------
    c.circle(CX, CY, 9, fill=palette.INK, stroke="none", width=0)
    c.circle(CX, CY, 26, stroke=palette.INK, width=2.5)
    c.halo_text(CX, CY + 60, label.upper(), size=21, anchor="middle", fill=blue,
                halo_width=9)

    # -- footer -----------------------------------------------------------
    c.line(90, 1370, 1110, 1370, width=3)
    window = f"{since:%H:%M}–{until:%H:%M}" if since and until else "today"
    c.text(90, 1432, "Each line points the true way to somewhere a plane "
                     "above you was going.", size=27)
    c.text(90, 1478, f"Length is great-circle distance, {units.distance_suffix}, "
                     "on a logarithmic scale.", size=27)
    # Same meta-line grammar as every design: date first, place last, blue.
    c.text(90, 1546, f"{now:%d %B %Y} · {window} · {label}", size=32, fill=blue)
    return c


def _by_sector(destinations: list[dict]) -> list[dict]:
    """The furthest destination in each compass sector.

    Sorting the whole list by distance and truncating drew Guam and San
    Francisco and dropped every European city; taking the nearest drew nothing
    but Amsterdam and Dublin. Sectors give an even spread of directions and
    still favour the interesting end of each one.
    """
    buckets: dict[int, dict] = {}
    for dest in destinations:
        if dest.get("km", 0) < MIN_INTERESTING_KM:
            continue
        sector = int(dest["bearing"] // (360 / SECTORS)) % SECTORS
        if dest["km"] > buckets.get(sector, {"km": -1})["km"]:
            buckets[sector] = dest
    return list(buckets.values())


def _short(city: str, limit: int = 15) -> str:
    """Airport municipality fields sometimes carry a whole airport title."""
    city = city.split(",")[0].split(" (")[0].strip()
    for suffix in (" International Airport", " Airport", " International"):
        if city.endswith(suffix):
            city = city[: -len(suffix)].strip()
    if len(city) <= limit:
        return city
    # Break at a word, not mid-word: "San José" beats "San José (Alaj…".
    cut = city[:limit].rsplit(" ", 1)[0].rstrip(" -–")
    # A dangling connective reads worse than a shorter name:
    # "Las Palmas de" -> "Las Palmas".
    while cut.split(" ")[-1].lower() in ("de", "del", "di", "da", "am", "an",
                                         "of", "the", "la", "le", "el", "on"):
        cut = cut.rsplit(" ", 1)[0]
    return cut if len(cut) >= 4 else city[: limit - 1].rstrip() + "…"


def _radius(km: float) -> float:
    km = max(NEAR_KM, min(km, FAR_KM))
    t = math.log(km / NEAR_KM) / math.log(FAR_KM / NEAR_KM)
    return R_MIN + t * (R_MAX - R_MIN)


def _overlaps(a: tuple[float, float, float, float],
              b: tuple[float, float, float, float]) -> bool:
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])
