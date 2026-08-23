"""Where is <name> flying next: the travel board for a faraway frame.

Made for the frame that hangs in someone else's house — parents, mostly.
It answers their actual question ("when do we next worry about you?") with a
countdown in days, and switches language per tenant so the answer arrives
in the reader's own words.

Layout: the next flight is the hero — big countdown, route, date and time,
aircraft — and later flights queue beneath it as quieter rows. Within 24
hours of departure the hero grows live detail (tail number, exact type) as
the transponder world starts to know about the flight. While a flight is
actually in the air this design never shows at all: the tracker takes the
glass over with the flight design, which is the drama this board is the
calm before.
"""
from __future__ import annotations

from datetime import date, datetime

from .. import palette
from ..canvas import Canvas

STRINGS = {
    "en": {
        "title": "Where is {name} flying next",
        "today": "TODAY",
        "tomorrow": "TOMORROW",
        "days": "IN {n} DAYS",
        "no_flights": "No flights planned",
        "no_flights_sub": "the sky can wait",
        "later": "And after that",
        "aircraft": "Aircraft",
        "tail": "Tail",
        "age": "{y} years old",
        "more": "and {n} more flights",
        "footer": "updates by itself · tracks the flight live while airborne",
    },
    "it": {
        "title": "Dove sta volando {name}",
        "today": "OGGI",
        "tomorrow": "DOMANI",
        "days": "FRA {n} GIORNI",
        "no_flights": "Nessun volo in programma",
        "no_flights_sub": "il cielo può aspettare",
        "later": "E poi",
        "aircraft": "Aereo",
        "tail": "Marche",
        "age": "{y} anni",
        "more": "e altri {n} voli",
        "footer": "si aggiorna da solo · segue il volo in diretta quando è in aria",
    },
}

MONTHS_IT = ["", "Gen", "Feb", "Mar", "Apr", "Mag", "Giu",
             "Lug", "Ago", "Set", "Ott", "Nov", "Dic"]


def _date_str(d: date, lang: str) -> str:
    """dd/MMM/yyyy — compact enough that queue rows keep a single line."""
    mon = MONTHS_IT[d.month] if lang == "it" else f"{d:%b}"
    return f"{d.day:02d}/{mon}/{d.year}"


def _place(row: dict, side: str) -> str:
    """"Venice–VCE" when the city is known, the bare code otherwise."""
    code = (row.get(side) or "···").upper()
    city = row.get(f"{side}_city")
    return f"{_short_city(city)}–{code}" if city else code


def _short_city(city: str, limit: int = 14) -> str:
    """Municipality fields carry things like "Paisley, Renfrewshire"."""
    city = city.split(",")[0].split("/")[0].split(" (")[0].strip()
    return city if len(city) <= limit else city[: limit - 1].rstrip() + "…"


def _countdown(days: int, t: dict) -> tuple[str, str]:
    """(big text, ink) for the countdown block."""
    if days <= 0:
        return t["today"], "red"
    if days == 1:
        return t["tomorrow"], "red"
    if days <= 7:
        return t["days"].format(n=days), "yellow"
    return t["days"].format(n=days), "green"


# Top-view airliner, nose-up, unit coords — the same silhouette as the app
# icon, so the mark and the posters agree on what a plane looks like.
_PLANE_HALF = [
    (0.000, -0.460), (0.052, -0.380), (0.060, -0.130), (0.420, 0.100),
    (0.420, 0.170), (0.066, 0.080), (0.058, 0.300), (0.190, 0.410),
    (0.190, 0.462), (0.040, 0.410), (0.000, 0.470),
]
_PLANE = _PLANE_HALF + [(-x, y) for x, y in reversed(_PLANE_HALF)]


def _plane(c: Canvas, x: float, y: float, size: float, fill: str,
           heading_right: bool = True) -> None:
    """Draw the airliner at (x, y), nose to the right (or up)."""
    pts = []
    for px, py in _PLANE:
        if heading_right:
            px, py = -py, px          # rotate nose-up -> nose-right
        pts.append(f"{x + px * size:.1f},{y + py * size:.1f}")
    c.add(f'<polygon points="{" ".join(pts)}" fill="{fill}"/>')


def render(
    flights: list[dict],
    *,
    name: str,
    lang: str = "en",
    live: dict | None = None,
    now: datetime | None = None,
) -> Canvas:
    """`flights` are upcoming_flights rows (soonest first). `live` is the
    optional ≤24h enrichment: {registration, type_name, age_years}."""
    now = now or datetime.now()
    t = STRINGS.get(lang, STRINGS["en"])
    c = Canvas(background=palette.PAPER)
    blue = palette.HEX["blue"]
    ink = palette.INK

    c.text(90, 150, t["title"].format(name=name), size=56, weight="500",
           spacing=0.5)
    c.line(90, 196, 1110, 196, width=3)

    if not flights:
        c.text(600, 700, t["no_flights"], size=64, weight="500",
               anchor="middle")
        c.text(600, 780, t["no_flights_sub"], size=36, anchor="middle",
               fill=blue)
        c.line(90, 1470, 1110, 1470, width=3)
        c.text(90, 1528, t["footer"], size=26, fill=blue)
        return c

    hero, rest = flights[0], flights[1:]
    shown = rest[:7]
    hidden = len(rest) - len(shown)
    hero_date = date.fromisoformat(hero["date"])
    days = (hero_date - now.date()).days
    big, band = _countdown(days, t)

    # -- hero countdown ----------------------------------------------------
    # Ink, never a colour: yellow digits on paper are barely legible (the
    # same rule as the portrait stats). The urgency band becomes the bar
    # underneath instead.
    c.text(90, 330, big, size=96, weight="500", spacing=2)
    c.line(90, 358, 90 + min(len(big) * 60, 700), 358,
           stroke=palette.HEX[band], width=10)
    c.text(90, 418, _date_str(hero_date, lang)
           + (f" · {hero['dep_time']}" if hero.get("dep_time") else ""),
           size=38, fill=blue)

    # The hero plane: climbing across the top-right, contrail in the
    # urgency colour. The graphics the board was missing.
    _plane(c, 985, 300, 210, palette.HEX["blue"])
    for gap, ln in ((0.62, 0.16), (0.88, 0.10)):
        c.line(985 - 210 * (gap + ln), 300, 985 - 210 * gap, 300,
               stroke=palette.HEX[band], width=9)

    # -- hero route --------------------------------------------------------
    # "Venice–VCE" when the route cache knows the city, sized to fit the
    # column whatever the pair of names turns out to be (§15: hierarchy
    # from weight and size as a set — both sides share one size, always).
    o = _place(hero, "origin")
    d_ = _place(hero, "destination")
    arrow = 140.0
    size = min(110.0, (1020.0 - arrow) / (0.56 * max(len(o) + len(d_), 6)))
    y_r = 560
    c.text(90, y_r, o, size=size, weight="500", fill=blue, spacing=-1)
    ax = 90 + len(o) * size * 0.56 + 34
    ay = y_r - size * 0.34
    c.path(f"M{ax:.0f} {ay:.0f}h72m-20-14l20 14l-20 14",
           stroke=palette.HEX["red"], width=8, fill="none",
           stroke_linecap="round", stroke_linejoin="round")
    c.text(ax + 106, y_r, d_, size=size, weight="500", fill=blue, spacing=-1)

    # Flight number and aircraft together on one line: the two facts a
    # departure board would pair (§16: grouping — proximity implies
    # relationship), auto-filled from the flight number upstream.
    craft = (live or {}).get("type_name") or hero.get("aircraft")
    c.text(90, 630, "  ·  ".join(x for x in (hero["flight_no"], craft) if x),
           size=36)

    # -- hero details ------------------------------------------------------
    y = 712
    details: list[tuple[str, str]] = []
    if live:
        if live.get("registration"):
            details.append((t["tail"], live["registration"]))
        if live.get("age_years"):
            details.append(("", t["age"].format(y=live["age_years"])))
    if hero.get("note"):
        details.append(("", hero["note"]))
    for caption, value in details[:3]:
        if caption:
            c.text(90, y, caption, size=26, fill=blue)
            c.text(300, y, value, size=30)
        else:
            c.text(90, y, value, size=30)
        y += 50

    # -- the queue ---------------------------------------------------------
    if shown:
        top = max(y + 44, 800)
        c.line(90, top, 1110, top, width=2)
        c.text(90, top + 52, t["later"], size=30, fill=blue)
        y = top + 110
        # Rows stretch to spend the space that exists: two flights should
        # not huddle at the top of an empty half-poster, and eight should
        # still fit above the footer.
        floor = 1440 - (36 if hidden > 0 else 0)
        row_h = max(62.0, min(112.0, (floor - y) / len(shown)))
        roomy = row_h >= 78
        for row in shown:
            d2 = date.fromisoformat(row["date"])
            dd = (d2 - now.date()).days
            small, band2 = _countdown(dd, t)
            _plane(c, 116, y - 10, 44 if roomy else 40, palette.HEX[band2])
            route = f"{(row.get('origin') or '?').upper()}–" \
                    f"{(row.get('destination') or '?').upper()}"
            left = "  ·  ".join(x for x in (
                route, row["flight_no"], row.get("dep_time"),
                row.get("aircraft")) if x)
            c.text(160, y, left, size=31 if roomy else 28)
            c.text(1110, y, f"{_date_str(d2, lang)} · {small.lower()}",
                   size=28 if roomy else 25, fill=blue, anchor="end")
            y += row_h
        if hidden > 0:
            c.text(160, y + 2, t["more"].format(n=hidden), size=27,
                   fill=blue)

    c.line(90, 1470, 1110, 1470, width=3)
    c.text(90, 1528, t["footer"], size=26, fill=blue)
    return c
