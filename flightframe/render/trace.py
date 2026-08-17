"""Long-exposure sky trace: every track over the window, as one drawing.

The subject is elapsed time, not the current instant, which is what makes it
suit a panel that can only refresh every fifteen minutes — it visibly grows all
day without ever needing to be current.

Three things earn their ink beyond the tracks themselves:

  * A ground stencil (Thames, M25). Without it the plot is unplaceable — you
    cannot tell a bundle over the City from one over Kent.
  * A scale bar in kilometres, because concentric rings alone do not tell you
    how big the picture is.
  * Destination labels around the rim. Outbound tracks all exit the frame
    eventually; naming where they were going turns the busiest, least
    informative part of the drawing into the most informative.

Density is the design variable that cannot be settled without real data. Knobs,
in the order worth reaching for: shorten `hours`, raise `min_points`, drop
distant tracks with `max_km`, raise the altitude floor at the collector.
"""
from __future__ import annotations

import math
from datetime import datetime

from .. import palette
from ..canvas import Canvas
from ..units import Units

CX, CY = 600.0, 700.0
RADIUS_PX = 452.0
MAX_LABELS = 11
MIN_LABEL_SEPARATION_DEG = 17.0


def render(
    tracks: dict[str, list[tuple[float, float, float, float]]],
    *,
    label: str,
    lat: float,
    lon: float,
    radius_nm: float,
    units: Units,
    since: datetime | None = None,
    until: datetime | None = None,
    basemap: dict[str, list[list[tuple[float, float]]]] | None = None,
    airports: list[dict] | None = None,
    destinations: dict[str, dict] | None = None,
    max_km: float | None = None,
    max_tracks: int = 70,
    min_points: int = 3,
    now: datetime | None = None,
) -> Canvas:
    """`tracks` maps hex -> [(lat, lon, altitude_ft, ts), ...].
    `destinations` maps hex -> {"city": str} for outbound aircraft."""
    now = now or datetime.now()
    c = Canvas(background=palette.PAPER)

    span_nm = radius_nm
    scale = RADIUS_PX / span_nm                     # pixels per nautical mile
    coslat = math.cos(math.radians(lat))
    blue, ink = palette.HEX["blue"], palette.INK

    def project(plat: float, plon: float) -> tuple[float, float]:
        dy = (plat - lat) * 60.0
        dx = (plon - lon) * 60.0 * coslat
        return CX + dx * scale, CY - dy * scale

    c.add('<clipPath id="plot">'
          f'<circle cx="{CX}" cy="{CY}" r="{RADIUS_PX}"/></clipPath>')

    # -- ground stencil ---------------------------------------------------
    # Drawn ON TOP of the tracks, not under them. Underneath it is completely
    # swamped once there is more than about half an hour of history, which
    # defeats the entire point of having a ground reference.
    def stencil() -> None:
        if basemap:
            c.add('<g clip-path="url(#plot)">')
            for line in basemap.get("motorway", []):
                c.polyline([project(a, o) for a, o in line], blue, width=2.0)
            for line in basemap.get("thames", []):
                c.polyline([project(a, o) for a, o in line], blue, width=5.0)
            # Runways last and heaviest of the three. At this scale a runway is
            # only ~40px long, but its angle is the reason the approach
            # corridors lie where they do, so it is worth the ink.
            #
            # Only the long ones. OSM returns 158 runways here, most of them
            # farm strips and gliding clubs, and drawn indiscriminately they
            # scatter blue debris across the whole plot.
            for line in basemap.get("runway", []):
                pts = [project(a, o) for a, o in line]
                if _extent(pts) < 22:
                    continue
                c.polyline(pts, blue, width=5.5)
            c.add("</g>")

        for field in airports or []:
            x, y = project(field["lat"], field["lon"])
            if math.hypot(x - CX, y - CY) > RADIUS_PX - 26:
                continue
            # Marker and label side by side, not stacked: centring the text on
            # the marker put the ring straight through the letters.
            c.circle(x, y, 7, fill=ink, stroke="none", width=0)
            c.halo_text(x + 14, y + 8, field["iata"], size=23, weight="500",
                        halo_width=7)

    # -- range rings ------------------------------------------------------
    for value in units.ring_steps(span_nm):
        r = units.to_nm(value) * scale
        if r < RADIUS_PX - 8:
            c.add(f'<circle cx="{CX}" cy="{CY}" r="{r:.1f}" fill="none" '
                  f'stroke="{blue}" stroke-width="1.6" stroke-dasharray="2 12"/>')
    c.add(f'<circle cx="{CX}" cy="{CY}" r="{RADIUS_PX}" fill="none" '
          f'stroke="{ink}" stroke-width="2"/>')

    # -- tracks -----------------------------------------------------------
    # Density is the whole battle. Half an hour of London traffic is already
    # ~200 aircraft and twelve hours is a solid mat of ink, so the drawing has
    # to choose. It keeps the tracks that cross the most sky, which are also
    # the ones that read as clean lines rather than as scribble: stacks,
    # holds and circuit traffic are exactly the short tangled ones.
    eligible = []
    for hexcode, points in tracks.items():
        if len(points) < min_points:
            continue
        if (max_km is not None
                and min(_km(lat, lon, p[0], p[1], coslat) for p in points) > max_km):
            continue
        span_px = _extent([project(p[0], p[1]) for p in points])
        if span_px < 40:                    # parked on the spot; nothing to draw
            continue
        eligible.append((span_px, hexcode, points))

    eligible.sort(key=lambda e: -e[0])
    selected = eligible[:max_tracks]

    c.add('<g clip-path="url(#plot)">')
    for _span, _hexcode, points in selected:
        run: list[tuple[float, float]] = []
        current: str | None = None
        for plat, plon, alt, _ts in points:
            band = palette.band(alt)
            xy = project(plat, plon)
            if current is None:
                current, run = band, [xy]
            elif band != current:
                run.append(xy)
                c.polyline(run, palette.HEX[current], width=2.6)
                current, run = band, [xy]
            else:
                run.append(xy)
        if len(run) > 1:
            c.polyline(run, palette.HEX[current or "black"], width=2.6)
    c.add("</g>")
    drawn = len(selected)

    stencil()

    # -- home -------------------------------------------------------------
    c.path(f"M{CX - 15} {CY}h30M{CX} {CY - 15}v30", stroke=ink, width=3.5)
    c.circle(CX, CY, 8, stroke=ink, width=3.5)

    # -- where they were going --------------------------------------------
    if destinations:
        _rim_labels(c, {h: p for _s, h, p in selected},
                    destinations, project, ink, exclude=label)

    # -- scale bar --------------------------------------------------------
    _scale_bar(c, units, scale, span_nm, ink, blue)

    # -- caption ----------------------------------------------------------
    c.rect(0, 1400, 1200, 200, palette.PAPER)
    c.line(90, 1425, 1110, 1425, width=3)
    window = f" · {since:%H:%M}–{until:%H:%M}" if since and until else ""
    c.text(90, 1492, f"{drawn} aircraft{window}", size=46, weight="500", spacing=1)
    c.text(90, 1548, f"{now:%d %B %Y} · {label} · "
                     f"{units.distance_str(span_nm, 0)} radius", size=34, fill=blue)
    return c


def _rim_labels(c: Canvas, tracks, destinations, project, ink: str,
                exclude: str = "") -> None:
    """Name the places outbound aircraft were heading, around the edge."""
    candidates: list[tuple[float, str]] = []
    for hexcode, points in tracks.items():
        dest = destinations.get(hexcode)
        if not dest or len(points) < 3:
            continue
        # A flight *to* here is arriving, not departing; naming it on the
        # rim would point outward at your own rooftop.
        if _same_place(dest["city"], exclude):
            continue
        x0, y0 = project(points[0][0], points[0][1])
        x1, y1 = project(points[-1][0], points[-1][1])
        r0 = math.hypot(x0 - CX, y0 - CY)
        r1 = math.hypot(x1 - CX, y1 - CY)
        if r1 <= r0:                       # inbound; its origin is elsewhere
            continue
        angle = math.degrees(math.atan2(x1 - CX, CY - y1)) % 360   # 0 = north
        candidates.append((angle, dest["city"]))

    candidates.sort()
    placed: list[tuple[float, str]] = []
    for angle, city in candidates:
        if any(city == c2 for _a, c2 in placed):
            continue
        if any(min(abs(angle - a), 360 - abs(angle - a)) < MIN_LABEL_SEPARATION_DEG
               for a, _c in placed):
            continue
        placed.append((angle, city))
        if len(placed) >= MAX_LABELS:
            break

    for angle, city in placed:
        rad = math.radians(angle)
        sin, cos = math.sin(rad), math.cos(rad)
        x0, y0 = CX + sin * RADIUS_PX, CY - cos * RADIUS_PX
        x1, y1 = CX + sin * (RADIUS_PX + 22), CY - cos * (RADIUS_PX + 22)
        c.path(f"M{x0:.1f} {y0:.1f}L{x1:.1f} {y1:.1f}", stroke=ink, width=3)
        # A small arrowhead outboard, so the label reads as a direction of
        # travel rather than as a place that happens to be over there.
        tip_x, tip_y = CX + sin * (RADIUS_PX + 34), CY - cos * (RADIUS_PX + 34)
        c.path(f"M{x1 - sin * 2:.1f} {y1 + cos * 2:.1f}L{tip_x:.1f} {tip_y:.1f}",
               stroke=ink, width=3)
        # Clamp to the canvas. Unclamped, anything due east or west ran off
        # the edge — "Heraklion" lost its tail and Paisley lost its head.
        name = _short(city)
        anchor = "start" if sin > 0.08 else "end" if sin < -0.08 else "middle"
        width = len(name) * 14.2
        lx = CX + sin * (RADIUS_PX + 44)
        ly = CY - cos * (RADIUS_PX + 44) + (14 if abs(sin) < 0.08 and cos < 0 else 6)
        left = {"start": lx, "end": lx - width, "middle": lx - width / 2}[anchor]
        # 34px margin: the physical mount overlaps the image edge by ~10px
        # and the mat cutter gets a ±1mm tolerance on top of that.
        left = max(34.0, min(left, 1166.0 - width))
        lx = {"start": left, "end": left + width, "middle": left + width / 2}[anchor]
        c.text(lx, ly, name, size=25, anchor=anchor)


def _short(city: str, limit: int = 14) -> str:
    """Municipality fields carry things like "Paisley, Renfrewshire".

    Over-long names break at a word, not mid-word: "Frankfurt am Main"
    becomes "Frankfurt", never "Frankfurt am…".
    """
    city = city.split(",")[0].split(" (")[0].strip()
    if len(city) <= limit:
        return city
    cut = city[:limit].rsplit(" ", 1)[0].rstrip(" -–")
    # A dangling connective reads worse than a shorter name:
    # "Las Palmas de" -> "Las Palmas".
    while cut.split(" ")[-1].lower() in ("de", "del", "di", "da", "am", "an",
                                         "of", "the", "la", "le", "el", "on"):
        cut = cut.rsplit(" ", 1)[0]
    return cut if len(cut) >= 4 else city[: limit - 1].rstrip() + "…"


def _same_place(a: str, b: str) -> bool:
    if not a or not b:
        return False
    a, b = a.lower(), b.lower()
    return a in b or b in a or (a == "london")


def _extent(points: list[tuple[float, float]]) -> float:
    """Diagonal of the track's bounding box, in pixels."""
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return math.hypot(max(xs) - min(xs), max(ys) - min(ys))


def _scale_bar(c: Canvas, units: Units, scale: float, span_nm: float,
               ink: str, blue: str) -> None:
    steps = units.ring_steps(span_nm)
    length_units = steps[0] if steps else units.distance(span_nm) / 3
    px = units.to_nm(length_units) * scale
    x0, y = 90.0, 1300.0
    c.path(f"M{x0} {y - 9}v18M{x0} {y}h{px:.1f}M{x0 + px:.1f} {y - 9}v18",
           stroke=ink, width=3)
    c.text(x0 + px + 14, y + 9, f"{length_units:,.0f} {units.distance_suffix}", size=28)
    c.text(x0, y + 44, "rings every "
                       f"{length_units:,.0f} {units.distance_suffix}", size=23, fill=blue)


def _km(lat1: float, lon1: float, lat2: float, lon2: float, coslat: float) -> float:
    dy = (lat2 - lat1) * 60.0
    dx = (lon2 - lon1) * 60.0 * coslat
    return math.hypot(dx, dy) * 1.852
