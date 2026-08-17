"""Single-plane portrait: one aircraft gets the whole poster.

Editorial rather than dashboard. Selection rotates through superlatives so the
poster changes character between editions instead of just changing numbers,
with a recently-shown set to stop the same airframe recurring.
"""
from __future__ import annotations

import math
from datetime import datetime

from .. import airlines, palette
from ..canvas import Canvas
from ..sources import Aircraft
from ..units import Units

EARTH_KM = 6371.0

SUPERLATIVES: dict[str, tuple[str, callable]] = {
    # "Furthest travelled", not "furthest away" — this ranks by the length of
    # the flight, and the aircraft is very often directly overhead.
    "furthest": ("Furthest travelled", lambda a: _route_km(a) or 0),
    "highest": ("Highest overhead", lambda a: a.altitude_ft or 0),
    "lowest": ("Lowest overhead", lambda a: -(a.altitude_ft or 1e9)),
    "fastest": ("Fastest overhead", lambda a: a.ground_speed_kt or 0),
    "closest": ("Closest overhead", lambda a: -a.distance_nm),
}


def choose(aircraft: list[Aircraft], mode: str = "furthest",
           exclude: set[str] | None = None) -> tuple[Aircraft, str] | None:
    exclude = exclude or set()
    pool = [a for a in aircraft if a.hex not in exclude] or aircraft
    if not pool:
        return None
    heading, key = SUPERLATIVES.get(mode, SUPERLATIVES["furthest"])
    return max(pool, key=key), heading


def render(
    ac: Aircraft,
    heading: str,
    *,
    label: str,
    shapes,
    units: Units,
    now: datetime | None = None,
) -> Canvas:
    now = now or datetime.now()
    c = Canvas(background=palette.PAPER)
    brand = airlines.lookup(ac.airline_icao, ac.airline)
    accent = palette.HEX["red"]
    blue = palette.HEX["blue"]

    c.text(90, 150, heading.lower(), size=34, fill=accent, spacing=3)
    c.line(90, 185, 1110, 185, stroke=accent, width=4)

    hero = ac.registration or ac.callsign or ac.hex.upper()
    c.text(90, 330, hero, size=150, weight="500", spacing=-2)
    subtitle = " · ".join(x for x in [brand.name or ac.airline, ac.type_name or ac.type] if x)
    c.text(90, 395, subtitle, size=40)

    # -- route ------------------------------------------------------------
    y = 600
    if ac.origin and ac.destination:
        c.text(90, y, str(ac.origin.get("iata") or "???"), size=130, weight="500", fill=blue)
        # Drawn, not typed. Cairo falls back through whatever fonts the host
        # happens to have and U+2192 is missing often enough to render as tofu.
        c.path(f"M470 {y - 40}h110m-26-18l26 18l-26 18", stroke=accent, width=9,
               fill="none", stroke_linecap="round", stroke_linejoin="round")
        c.text(640, y, str(ac.destination.get("iata") or "???"), size=130,
               weight="500", fill=blue)
        c.text(90, y + 52, str(ac.origin.get("city") or ""), size=34)
        c.text(640, y + 52, str(ac.destination.get("city") or ""), size=34)

        km = _route_km(ac)
        if km:
            c.text(90, 935, f"{units.from_km(km):,.0f} {units.distance_suffix} "
                            "great circle", size=34, fill=blue)
        _arc(c, 860, km)
    else:
        c.text(90, y, "Route unknown", size=90, weight="500", fill=blue)
        c.text(90, y + 55, "no flight plan filed against this callsign", size=32)

    # -- the aircraft itself ----------------------------------------------
    shape = shapes.get(ac.type)
    if shape:
        c.add(shape.group(
            945, 360, 272,
            fill=palette.HEX[brand.body],
            stroke=palette.INK,
            stroke_width=2.0,
            rotate=(ac.track_deg or 0),
        ))

    # -- stats ------------------------------------------------------------
    c.line(90, 1010, 1110, 1010, width=3)
    _stat(c, 90, 1100, "Altitude",
          f"{units.altitude(ac.altitude_ft):,.0f}" if ac.altitude_ft else "—",
          _vs_note(ac, units), palette.band_hex(ac.altitude_ft))
    _stat(c, 640, 1100, "Ground speed",
          f"{units.speed(ac.ground_speed_kt):,.0f}" if ac.ground_speed_kt else "—",
          units.speed_suffix)
    dist = units.distance(ac.distance_nm)
    _stat(c, 90, 1350, "Distance from you",
          f"{dist:,.1f}" if dist < 10 else f"{dist:,.0f}",
          f"{units.distance_suffix}, bearing {ac.bearing_deg:03.0f}°")

    c.line(90, 1520, 1110, 1520, stroke=accent, width=4)
    c.text(90, 1570, f"{now:%d %B %Y} · {now:%H:%M} · {label}", size=30, fill=blue)
    return c


def _stat(c: Canvas, x: float, y: float, caption: str, value: str, note: str,
          accent: str | None = None) -> None:
    """Big numbers are always INK. Yellow was the altitude-band ink for a
    mid-height plane, and yellow digits on paper are barely legible — so the
    band colour became an underline, and the number stays readable."""
    c.text(x, y, caption, size=30, fill=palette.HEX["blue"])
    c.text(x, y + 80, value, size=86, weight="500", fill=palette.INK)
    if accent and accent != palette.INK:
        c.line(x, y + 98, x + max(len(value) * 48, 120), y + 98,
               stroke=accent, width=8)
    c.text(x, y + 125, note, size=28)


def _vs_note(ac: Aircraft, units: Units) -> str:
    unit = "metres" if units.name == "metric" else "feet"
    vs = ac.vertical_fpm or 0
    if vs > 300:
        return f"{unit}, climbing {units.climb_str(vs)}"
    if vs < -300:
        return f"{unit}, descending {units.climb_str(vs)}"
    return f"{unit}, level"


def _arc(c: Canvas, y: float, km: float | None) -> None:
    """Route length, drawn to scale.

    Previously a fixed decorative curve, which made a Dublin hop and a Sydney
    haul look identical while a number underneath claimed otherwise. Width now
    tracks distance on a log scale — the same one the rose uses — so short
    routes are visibly short.
    """
    span = 960.0
    if km:
        t = math.log(max(km, 300.0) / 300.0) / math.log(16_000.0 / 300.0)
        span = 180.0 + min(max(t, 0.0), 1.0) * 780.0
    x0 = 120.0
    x1 = x0 + span
    c.path(f"M{x0} {y} Q{(x0 + x1) / 2} {y - span * 0.17} {x1} {y}",
           stroke=palette.HEX["red"], width=6)
    c.circle(x0, y, 16, fill=palette.HEX["blue"], stroke="none", width=0)
    c.circle(x1, y, 16, stroke=palette.HEX["blue"], width=6)


def _route_km(ac: Aircraft) -> float | None:
    if not (ac.origin and ac.destination):
        return None
    try:
        p1, l1 = math.radians(ac.origin["lat"]), math.radians(ac.origin["lon"])
        p2, l2 = math.radians(ac.destination["lat"]), math.radians(ac.destination["lon"])
    except (TypeError, KeyError):
        return None
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin((l2 - l1) / 2) ** 2
    return 2 * EARTH_KM * math.asin(math.sqrt(a))
