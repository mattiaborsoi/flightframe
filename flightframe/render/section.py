"""Altitude cross-section: a side elevation of the sky, not a map.

X is distance from home, Y is altitude, so the arrival stream reads as a
diagonal procession descending toward home and departures climb away from it.

Labels name places, not callsigns. "RYR49SQ" tells you nothing you can feel;
"Dublin" does. Climbing aircraft are labelled with where they are going,
descending ones with where they have come from — so the poster reads as the
world arriving and leaving rather than as a list of flight numbers.

The library shapes are top views and would be wrong on an elevation, so
aircraft are dots with a climb arrow driven by vertical rate.
"""
from __future__ import annotations

import math
from datetime import datetime

from .. import palette
from ..canvas import Canvas
from ..sources import Aircraft
from ..units import Units

X0, X1 = 150, 1120
Y0, Y1 = 1180, 330          # Y1 clears the header rule at 240
CEILING_FT = 40_000


def render(
    aircraft: list[Aircraft],
    *,
    label: str,
    radius_km: float,
    units: Units,
    floor_ft: float = 0.0,
    max_labels: int = 12,
    now: datetime | None = None,
) -> Canvas:
    now = now or datetime.now()
    c = Canvas(background=palette.PAPER)
    blue = palette.HEX["blue"]

    # Zoom in. The collector sweeps the full radius for the rose, but
    # on an elevation everything within a few km of home piles into one column,
    # so the cross-section plots a tighter window and drops the rest.
    radius_nm = radius_km / 1.852
    aircraft = [a for a in aircraft if a.distance_nm <= radius_nm]

    c.text(90, 150, "Overhead now", size=58, weight="500", spacing=1)
    c.text(90, 205, f"{now:%H:%M} · {len(aircraft)} aircraft within "
                    f"{units.distance_str(radius_nm, 0)}", size=32, fill=blue)
    c.line(90, 240, 1110, 240, width=3)

    # -- altitude grid ----------------------------------------------------
    # Dashed and fully opaque, never a faint hairline. There are no greys, so a
    # semi-transparent line quantises to paper at some sub-pixel positions and
    # to blue at others — it survives at one altitude and vanishes at the next.
    for ft, text in units.altitude_steps(CEILING_FT):
        y = _y(ft)
        if y < Y1 - 4:
            continue
        c.line(X0, y, X1, y, stroke=blue, width=2, stroke_dasharray="2 10")
        c.text(96, y + 8, text, size=26, fill=blue)
    c.text(96, Y1 - 26, "altitude, thousands of "
                        f"{'metres' if units.name == 'metric' else 'feet'}",
           size=24, fill=blue)

    # -- ground -----------------------------------------------------------
    c.line(X0, Y0, X1, Y0, width=4)
    for value in [0, *units.ring_steps(radius_nm)]:
        nm = units.to_nm(value)
        if nm > radius_nm:
            continue
        c.text(_x(nm, radius_nm), Y0 + 46, f"{value:,.0f}", size=26,
               fill=blue, anchor="middle")
    c.text(X1, Y0 + 88, f"distance from home, {units.distance_suffix}",
           size=26, fill=blue, anchor="end")
    if floor_ft > 0:
        # The gap between the lowest dot and the ground line is the altitude
        # floor, not absent data. Say so.
        c.text(X0, Y0 + 88, f"below {units.altitude(floor_ft):,.0f} "
                            f"{units.altitude_suffix} not plotted",
               size=24, fill=blue)
    # Home is the same circle-and-crosshair glyph the rose uses, so "this
    # point is you" reads identically across designs.
    c.path(f"M{X0 - 15} {Y0 - 15}h30M{X0} {Y0 - 30}v30", width=3.5)
    c.circle(X0, Y0 - 15, 8, width=3.5)

    # -- aircraft ---------------------------------------------------------
    # Labels go only where they will not overlap something already placed.
    # Highest first, because the top of the plot is sparse and the baseline is
    # a scrum — labelling in distance order puts every label in the scrum.
    # The axis caption is chrome, but it sits exactly where a 12 km overflight
    # wants its label. Register it first so aircraft labels route around it,
    # the same way the rose treats its rings and compass.
    placed: list[tuple[float, float, float, float]] = [(90, Y1 - 52, 560, Y1 - 4)]
    for ac in sorted(aircraft, key=lambda a: -(a.altitude_ft or 0)):
        if ac.altitude_ft is None:
            continue
        x, y = _x(ac.distance_nm, radius_nm), _y(ac.altitude_ft)
        ink = palette.band_hex(ac.altitude_ft)
        c.circle(x, y, 13, fill=ink, stroke="none", width=0)

        vs = ac.vertical_fpm or 0
        climbing = vs > 300
        if abs(vs) > 300:
            d = 1 if vs < 0 else -1        # screen y grows downward
            tip = y + d * 41
            c.path(f"M{x} {y + d * 19}V{tip}M{x - 6} {tip - d * 8}l6 {d * 8}l6 {-d * 8}",
                   stroke=ink, width=3)

        place = _place_name(ac, climbing)
        if len(placed) >= max_labels or not place:
            continue
        box = (x + 14, y - 26, x + 22 + len(place) * 13.4, y + 6)
        if box[2] > X1 or any(_overlaps(box, other) for other in placed):
            continue
        placed.append(box)
        c.text(x + 20, y - 7, place, size=23)

    # -- legend and footer ------------------------------------------------
    c.line(90, 1320, 1110, 1320, width=3)
    x = 103
    for _ceiling, ink, text in palette.BANDS:
        label_text = _band_label(text, units)
        c.circle(x, 1382, 11, fill=palette.HEX[ink], stroke="none", width=0)
        c.text(x + 22, 1391, label_text, size=25)
        # 12.6px/char is measured for this face at 25px; the previous 13.0 with
        # a 46px gap under-counted and ran each dot into the label before it.
        x += 60 + len(label_text) * 12.6
    # Explainer in ink, meta line in blue — the same hierarchy as the rose:
    # blue is reserved for the date-and-place line everywhere.
    c.text(90, 1470, "climbing aircraft show their destination, "
                     "descending ones their origin", size=26)
    c.text(90, 1524, f"{now:%d %B %Y} · {now:%H:%M} · {label}", size=32,
           fill=blue)
    return c


def _place_name(ac: Aircraft, climbing: bool) -> str | None:
    """Where it is going if climbing, where it came from if descending."""
    end = ac.destination if climbing else ac.origin
    if not end:
        end = ac.destination or ac.origin
    if not end:
        return ac.callsign
    return end.get("city") or end.get("iata") or ac.callsign


def _band_label(text: str, units: Units) -> str:
    """Band labels are written in km; translate back for aviation units."""
    if units.name == "metric":
        return text
    return {
        "below 1.5 km": "below 5,000 ft",
        "1.5–4.5 km": "5–15,000 ft",
        "4.5–9 km": "15–30,000 ft",
        "above 9 km": "above 30,000 ft",
    }.get(text, text)


def _overlaps(a: tuple[float, float, float, float],
              b: tuple[float, float, float, float]) -> bool:
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def _x(distance_nm: float, radius_nm: float) -> float:
    return X0 + min(distance_nm / radius_nm, 1.0) * (X1 - X0)


def _y(altitude_ft: float) -> float:
    fraction = min(max(altitude_ft, 0.0), CEILING_FT) / CEILING_FT
    return Y0 - math.sqrt(fraction) * (Y0 - Y1)
