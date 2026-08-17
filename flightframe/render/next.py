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

MONTHS_IT = ["", "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
             "luglio", "agosto", "settembre", "ottobre", "novembre",
             "dicembre"]


def _date_str(d: date, lang: str) -> str:
    if lang == "it":
        return f"{d.day} {MONTHS_IT[d.month]} {d.year}"
    return f"{d:%-d %B %Y}"


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
    dense = len(shown) > 3
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
    o = (hero.get("origin") or "···").upper()
    d_ = (hero.get("destination") or "···").upper()
    size = 110 if max(len(o), len(d_)) <= 4 else 60
    c.text(90, 570, o, size=size, weight="500", fill=blue)
    c.path("M455 530h100m-24-16l24 16l-24 16", stroke=palette.HEX["red"],
           width=8, fill="none", stroke_linecap="round",
           stroke_linejoin="round")
    c.text(610, 570, d_, size=size, weight="500", fill=blue)
    c.text(90, 632, hero["flight_no"], size=38)

    # -- hero details ------------------------------------------------------
    y = 726
    details: list[tuple[str, str]] = []
    if hero.get("aircraft"):
        details.append((t["aircraft"], hero["aircraft"]))
    if live:
        if live.get("type_name"):
            details[-1:] = [(t["aircraft"], live["type_name"])]
        if live.get("registration"):
            details.append((t["tail"], live["registration"]))
        if live.get("age_years"):
            details.append(("", t["age"].format(y=live["age_years"])))
    if hero.get("note"):
        details.append(("", hero["note"]))
    for caption, value in details[: 2 if dense else 4]:
        if caption:
            c.text(90, y, caption, size=26, fill=blue)
            c.text(300, y, value, size=30)
        else:
            c.text(90, y, value, size=30)
        y += 52

    # -- the queue ---------------------------------------------------------
    if shown:
        top = 840 if dense else 1080
        c.line(90, top, 1110, top, width=2)
        c.text(90, top + 52, t["later"], size=30, fill=blue)
        y = top + 108
        row_h = 64 if dense else 84
        for row in shown:
            d2 = date.fromisoformat(row["date"])
            dd = (d2 - now.date()).days
            small, band2 = _countdown(dd, t)
            _plane(c, 116, y - 10, 40 if dense else 44,
                   palette.HEX[band2])
            route = f"{(row.get('origin') or '?').upper()}–" \
                    f"{(row.get('destination') or '?').upper()}"
            c.text(160, y, f"{route}  ·  {row['flight_no']}"
                   + (f" · {row['dep_time']}" if row.get("dep_time")
                      and dense else ""), size=28 if dense else 32)
            c.text(1110, y, f"{_date_str(d2, lang)} · {small.lower()}",
                   size=25 if dense else 28, fill=blue, anchor="end")
            y += row_h
        if hidden > 0:
            c.text(160, y + 6, t["more"].format(n=hidden), size=27,
                   fill=blue)

    c.line(90, 1470, 1110, 1470, width=3)
    c.text(90, 1528, t["footer"], size=26, fill=blue)
    return c
