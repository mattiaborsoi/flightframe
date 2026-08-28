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

from .. import airlines, palette
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
        "arr": "arr. {hh}",
        "terminal": "Terminal {t}",
        "gate": "Gate {g}",
        "delayed": "Delayed {m} min",
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
        "arr": "arrivo {hh}",
        "terminal": "Terminal {t}",
        "gate": "Uscita {g}",
        "delayed": "In ritardo di {m} min",
        "age": "{y} anni",
        "more": "e altri {n} voli",
        "footer": "si aggiorna da solo · segue il volo in diretta quando è in aria",
    },
}

MONTHS_IT = ["", "Gen", "Feb", "Mar", "Apr", "Mag", "Giu",
             "Lug", "Ago", "Set", "Ott", "Nov", "Dic"]
WEEKDAYS_IT = ["lun", "mar", "mer", "gio", "ven", "sab", "dom"]

# The schedule APIs hand back whatever language the airport's country
# prefers; the board sticks to English names. Small and curated: only
# cities that have actually appeared wrong, plus their obvious siblings.
CITY_EN = {
    "Bâle": "Basel", "Genève": "Geneva", "Zürich": "Zurich",
    "München": "Munich", "Köln": "Cologne", "Nürnberg": "Nuremberg",
    "Wien": "Vienna", "Bruxelles": "Brussels", "København": "Copenhagen",
    "Göteborg": "Gothenburg", "Praha": "Prague", "Warszawa": "Warsaw",
    "Lisboa": "Lisbon", "Sevilla": "Seville", "Athina": "Athens",
    "Roma": "Rome", "Milano": "Milan", "Venezia": "Venice",
    "Napoli": "Naples", "Firenze": "Florence", "Torino": "Turin",
}


def _date_str(d: date, lang: str) -> str:
    """dd/MMM/yyyy — compact enough that queue rows keep a single line."""
    mon = MONTHS_IT[d.month] if lang == "it" else f"{d:%b}"
    return f"{d.day:02d}/{mon}/{d.year}"


def _hero_date_str(d: date, lang: str) -> str:
    """The hero date carries the weekday — people plan around "giovedì",
    not around the 19th."""
    wd = WEEKDAYS_IT[d.weekday()] if lang == "it" else f"{d:%a}"
    return f"{wd} {_date_str(d, lang)}"


def _place(row: dict, side: str) -> str:
    """"Venice–VCE" when the city is known, the bare code otherwise."""
    code = (row.get(side) or "···").upper()
    city = row.get(f"{side}_city")
    return f"{_short_city(city)}–{code}" if city else code


def _short_city(city: str, limit: int = 14) -> str:
    """Municipality fields carry things like "Paisley, Renfrewshire"."""
    city = city.split(",")[0].split("/")[0].split(" (")[0].strip()
    city = CITY_EN.get(city, city)
    if len(city) <= limit:
        return city
    # Cut at a word boundary when one leaves enough behind: "Frankfurt"
    # reads better than "Frankfurt-a…". (Bounded loop — a lone connective
    # in the trace renderer's version of this once pinned a CPU for an hour.)
    head = city[: limit - 1]
    best = ""
    for sep in (" ", "-"):
        if sep in head:
            best = max(best, head.rsplit(sep, 1)[0].rstrip(" -"), key=len)
    return best if len(best) >= 4 else head.rstrip() + "…"


def _times(row: dict) -> str:
    """"23:20–19:00 +1": both ends in their airport's own local time, with
    the day marker when the flight lands after midnight. Empty unless the
    departure time is known."""
    out = row.get("dep_time") or ""
    if out and row.get("arr_time"):
        out += f"–{row['arr_time']}"
        if row.get("arr_day_offset"):
            out += f" +{row['arr_day_offset']}"
    return out


def _row_route(row: dict, limit: int = 12) -> str:
    """"Copenhagen → Seoul" when cities are known, codes otherwise."""
    def side(key):
        city = row.get(f"{key}_city")
        return _short_city(city, limit) if city \
            else (row.get(key) or "?").upper()
    return f"{side('origin')} → {side('destination')}"


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
           heading_right: bool = True, outline: bool = False) -> None:
    """Draw the airliner at (x, y), nose to the right (or up)."""
    pts = []
    for px, py in _PLANE:
        if heading_right:
            px, py = -py, px          # rotate nose-up -> nose-right
        pts.append(f"{x + px * size:.1f},{y + py * size:.1f}")
    stroke = (f' stroke="{palette.INK}" stroke-width="{max(size * 0.022, 1.4):.1f}"'
              f' stroke-linejoin="round"') if outline else ""
    c.add(f'<polygon points="{" ".join(pts)}" fill="{fill}"{stroke}/>')


# Schedule APIs name the metal ("Airbus A350-900"); the shape library is
# keyed by ICAO type code (A359). Ordered longest-match-first over a
# condensed string, so "A320 NEO" never falls through to plain A320.
_MODEL_CODES = [
    ("A3501000", "A35K"), ("A350900", "A359"), ("A350", "A359"),
    ("A330900", "A339"), ("A330300", "A333"), ("A330200", "A332"),
    ("A380", "A388"), ("A321NEO", "A21N"), ("A320NEO", "A20N"),
    ("A321", "A321"), ("A320", "A320"), ("A319", "A319"), ("A318", "A318"),
    ("A220300", "BCS3"), ("A220100", "BCS1"), ("A220", "BCS3"),
    ("737MAX8", "B38M"), ("737MAX9", "B39M"), ("737MAX", "B38M"),
    ("737900", "B739"), ("737800", "B738"), ("737700", "B737"),
    ("7478", "B748"), ("747400", "B744"), ("747", "B744"),
    ("777300", "B77W"), ("777200", "B772"), ("777", "B772"),
    ("78710", "B78X"), ("7879", "B789"), ("7878", "B788"), ("787", "B789"),
    ("767", "B763"), ("757", "B752"),
    ("E195", "E195"), ("E190", "E190"), ("E175", "E75L"),
    ("ATR72", "AT76"), ("ATR42", "AT45"), ("Q400", "DH8D"),
]
_CODE_RE = __import__("re").compile(r"^[A-Z]{1,2}\d{2,3}[A-Z]{0,2}$")


def _type_code(model: str | None) -> str | None:
    if not model:
        return None
    raw = model.strip().upper()
    if _CODE_RE.match(raw):
        return raw                     # already an ICAO type code
    condensed = "".join(ch for ch in raw if ch.isalnum())
    for needle, code in _MODEL_CODES:
        if needle in condensed:
            return code
    return None


def _draw_aircraft(c: Canvas, row: dict, x: float, y: float, size: float,
                   shapes=None) -> None:
    """The actual airframe in its airline's livery, nose to the right —
    the liveried grid's treatment, in miniature. Falls back to the shared
    icon when the shape library is absent (tests) or the type unknown."""
    fill = _livery(row)
    shape = shapes.get(_type_code(row.get("aircraft"))) if shapes else None
    if shape:
        c.add(shape.group(x, y, size * 1.35, fill=fill, stroke=palette.INK,
                          stroke_width=1.5, rotate=90))
    else:
        _plane(c, x, y, size, fill, outline=True)


def _livery(row: dict) -> str:
    """The airline's brand ink, as the liveried grid paints it. White-bodied
    carriers (BA, and any airline the table does not know) read fine here:
    the outline carries the silhouette on paper, exactly like the grid."""
    brand = airlines.lookup(row.get("airline_icao"), row.get("airline_name"))
    return palette.HEX[brand.body]


def render(
    flights: list[dict],
    *,
    name: str,
    lang: str = "en",
    live: dict | None = None,
    now: datetime | None = None,
    shapes=None,
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
    shown = rest[:6]
    hidden = len(rest) - len(shown)
    hero_date = date.fromisoformat(hero["date"])
    days = (hero_date - now.date()).days
    big, band = _countdown(days, t)

    # -- hero countdown ----------------------------------------------------
    # Ink, never a colour: yellow digits on paper are barely legible (the
    # same rule as the portrait stats). The urgency band becomes the bar
    # underneath instead.
    # Sized to clear the plane's contrail at x~775: "FRA 83 GIORNI" at a
    # fixed 96px ran straight through it. The bar underneath matches the
    # text's actual width instead of guessing with a different coefficient.
    big_size = min(96.0, 660.0 / (0.62 * max(len(big), 6)))
    c.text(90, 330, big, size=big_size, weight="500", spacing=2)
    c.line(90, 358, 90 + min(0.62 * big_size * len(big), 690), 358,
           stroke=palette.HEX[band], width=10)
    times = _times(hero)
    if not times and hero.get("arr_time"):
        # Airlines sometimes publish the arrival first; show what exists.
        times = t["arr"].format(hh=hero["arr_time"])
    c.text(90, 418, _hero_date_str(hero_date, lang) + (f" · {times}" if times
                                                  else ""),
           size=38, fill=blue)

    # The hero aircraft in its airline's actual livery, contrail in the
    # urgency colour — brand carries identity, colour carries time. Sized
    # so the wingtips clear the header rule above and the date line below.
    hx, hy = 990, 318
    _draw_aircraft(c, hero, hx, hy, 150, shapes)
    for gap, ln in ((0.34, 0.10), (0.52, 0.07)):
        c.line(hx - 330 * (gap + ln), hy, hx - 330 * gap, hy,
               stroke=palette.HEX[band], width=9)

    # -- hero route --------------------------------------------------------
    # "Venice–VCE" when the route cache knows the city, sized to fit the
    # column whatever the pair of names turns out to be (§15: hierarchy
    # from weight and size as a set — both sides share one size, always).
    o = _place(hero, "origin")
    d_ = _place(hero, "destination")
    # 0.60 em average advance: measured ~0.55 for Inter over these strings,
    # padded so the arrow clears the glyphs even under a wider fallback
    # face (the deployed DejaVu once put the arrow through "London–LHR").
    est = 0.60
    size = min(110.0, 1020.0 / (est * max(len(o) + len(d_), 6) + 1.7))
    y_r = 560
    c.text(90, y_r, o, size=size, weight="500", fill=blue, spacing=-1)
    # The arrow scales with the type: span, head, and stroke are all in em,
    # so it reads as punctuation of the route rather than an afterthought.
    ax = 90 + len(o) * size * est + size * 0.30
    ay = y_r - size * 0.34
    seg, head = size * 0.95, size * 0.20
    c.path(f"M{ax:.1f} {ay:.1f}h{seg:.1f}"
           f"m-{head:.1f} -{head * 0.72:.1f}l{head:.1f} {head * 0.72:.1f}"
           f"l-{head:.1f} {head * 0.72:.1f}",
           stroke=palette.HEX["red"], width=max(6.0, size * 0.085),
           fill="none", stroke_linecap="round", stroke_linejoin="round")
    c.text(ax + seg + size * 0.45, y_r, d_, size=size, weight="500",
           fill=blue, spacing=-1)

    # Flight number and aircraft together on one line: the two facts a
    # departure board would pair (§16: grouping — proximity implies
    # relationship), auto-filled from the flight number upstream.
    craft = (live or {}).get("type_name") or hero.get("aircraft")
    c.text(90, 630, "  ·  ".join(x for x in (hero["flight_no"], craft) if x),
           size=36)

    # -- hero details ------------------------------------------------------
    y = 700
    details: list[tuple[str, str, str]] = []
    if (hero.get("delay_min") or 0) > 0:
        details.append(("", t["delayed"].format(m=hero["delay_min"]),
                        palette.HEX["red"]))
    where = [t["terminal"].format(t=hero["dep_terminal"])
             if hero.get("dep_terminal") else "",
             t["gate"].format(g=hero["dep_gate"])
             if hero.get("dep_gate") else ""]
    if any(where):
        details.append(("", " · ".join(w for w in where if w), ink))
    reg = (live or {}).get("registration") or hero.get("registration")
    if reg:
        details.append((t["tail"], reg, ink))
    if live and live.get("age_years"):
        details.append(("", t["age"].format(y=live["age_years"]), ink))
    if hero.get("note"):
        details.append(("", hero["note"], ink))
    for caption, value, fill in details[:4]:
        if caption:
            c.text(90, y, caption, size=26, fill=blue)
            c.text(300, y, value, size=30, fill=fill)
        else:
            c.text(90, y, value, size=30, fill=fill)
        y += 50

    # -- the queue ---------------------------------------------------------
    if shown:
        # Directly under the last detail row — no fixed band of dead air
        # when the airline has published nothing yet.
        top = min(max(y + 44, 744), 960)
        c.line(90, top, 1110, top, width=2)
        c.text(90, top + 52, t["later"], size=30, fill=blue)
        y = top + 110
        # Rows stretch to spend the space that exists: two flights should
        # not huddle at the top of an empty half-poster, and eight should
        # still fit above the footer.
        floor = 1440 - (36 if hidden > 0 else 0)
        row_h = max(62.0, min(118.0, (floor - y) / len(shown)))
        # Two-line rows when the column affords them: cities and flight
        # number carry the row, times and aircraft sit under it in blue —
        # the same hierarchy as the hero, in miniature. Cramped boards
        # fall back to one dense line of codes.
        roomy = row_h >= 78
        for row in shown:
            d2 = date.fromisoformat(row["date"])
            dd = (d2 - now.date()).days
            small, band2 = _countdown(dd, t)
            _draw_aircraft(c, row, 118, y - 11, 46 if roomy else 40,
                           shapes)
            if roomy:
                c.text(160, y, f"{_row_route(row)}  ·  {row['flight_no']}",
                       size=30)
                sub = "  ·  ".join(x for x in (
                    _times(row) or row.get("dep_time"),
                    row.get("aircraft")) if x)
                if sub:
                    c.text(160, y + 33, sub, size=23, fill=blue)
            else:
                route = f"{(row.get('origin') or '?').upper()}–" \
                        f"{(row.get('destination') or '?').upper()}"
                left = "  ·  ".join(x for x in (
                    route, row["flight_no"], row.get("dep_time")) if x)
                c.text(160, y, left, size=28)
            c.text(1110, y, f"{_date_str(d2, lang)} · {small.lower()}",
                   size=28 if roomy else 25, fill=blue, anchor="end")
            y += row_h
        if hidden > 0:
            c.text(160, y + 2, t["more"].format(n=hidden), size=27,
                   fill=blue)

    c.line(90, 1470, 1110, 1470, width=3)
    c.text(90, 1528, t["footer"], size=26, fill=blue)
    return c
