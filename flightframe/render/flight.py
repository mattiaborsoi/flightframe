"""One tracked flight, from pushback to half an hour after it lands.

Built to be read from across a room while you are half paying attention: the
status line and the progress bar carry the whole story, and the numbers are
there if you walk closer.

The status line is the part that matters most. A flight over the Atlantic is
invisible to a volunteer receiver network for hours at a time, and a poster
that goes blank or silently freezes would read as broken. So a gap is stated
plainly — "over the Atlantic · last seen 47 min ago" — and the last known
position stays on the bar.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .. import airlines, names, palette
from ..canvas import Canvas
from ..tracking import AIRBORNE, LANDED, OUT_OF_RANGE, SCHEDULED, Flight
from ..units import Units

BAR_X0, BAR_X1, BAR_Y = 120.0, 1080.0, 760.0


T = {
    "en": {
        "scheduled": "not yet airborne", "airborne": "in the air",
        "out_of_range": "out of receiver range", "landed": "landed",
        "est_head": "in flight",
        "est_line": "Departed · waiting for a live signal",
        "landed_at": "Landed at {hh}",
        "waiting": "Waiting for it to take off",
        "in_air": "In the air",
        "min_to_go": "About {m:,.0f} minutes to go",
        "hm_to_go": "About {h}h {m:02d}m to go",
        "last_heard": "{where} · last heard {m:,.0f} min ago · "
                      "no receivers out there, this is normal",
        "hold": "showing for another {m:,.0f} minutes",
        "arriving": "arriving about {hh}",
        "out": "Out of range", "atlantic": "Over the Atlantic",
        "indian": "Over the Indian Ocean", "pacific": "Over the Pacific",
        "desert": "Over the desert", "arctic": "Over the Arctic",
        "altitude": "Altitude", "speed": "Ground speed",
        "remaining": "Remaining",
        "climbing": "climbing {v}", "descending": "descending {v}",
        "level": "level",
        "of_total": "of {d} ({p:,.0f}% flown)",
        "est_note": "progress estimated from the schedule",
        "months": ["", "January", "February", "March", "April", "May",
                   "June", "July", "August", "September", "October",
                   "November", "December"],
    },
    "it": {
        "scheduled": "non ancora in volo", "airborne": "in volo",
        "out_of_range": "fuori portata dei ricevitori",
        "landed": "atterrato",
        "est_head": "in volo",
        "est_line": "Decollato · in attesa del segnale",
        "landed_at": "Atterrato alle {hh}",
        "waiting": "In attesa del decollo",
        "in_air": "In volo",
        "min_to_go": "Circa {m:,.0f} minuti all'arrivo",
        "hm_to_go": "Circa {h}h {m:02d}m all'arrivo",
        "last_heard": "{where} · ultimo segnale {m:,.0f} min fa · "
                      "nessun ricevitore laggiù, è normale",
        "hold": "ancora {m:,.0f} minuti su questo schermo",
        "arriving": "arrivo previsto verso le {hh}",
        "out": "Fuori portata", "atlantic": "Sull'Atlantico",
        "indian": "Sull'Oceano Indiano", "pacific": "Sul Pacifico",
        "desert": "Sul deserto", "arctic": "Sull'Artico",
        "altitude": "Altitudine", "speed": "Velocità",
        "remaining": "Rimanenti",
        "climbing": "in salita {v}", "descending": "in discesa {v}",
        "level": "in quota",
        "of_total": "di {d} ({p:,.0f}% percorso)",
        "est_note": "avanzamento stimato dall'orario",
        "months": ["", "gennaio", "febbraio", "marzo", "aprile", "maggio",
                   "giugno", "luglio", "agosto", "settembre", "ottobre",
                   "novembre", "dicembre"],
    },
}


def render(flight: Flight, *, label: str, shapes, units: Units,
           now: datetime | None = None,
           footnote: str | None = None,
           schedule_line: str | None = None,
           estimated: dict | None = None,
           lang: str = "en") -> Canvas:
    t = T.get(lang, T["en"])
    now = now or datetime.now()
    c = Canvas(background=palette.PAPER)
    blue, red, ink = palette.HEX["blue"], palette.HEX["red"], palette.INK
    brand = airlines.lookup(None, flight.airline)

    # The airline says it is flying but no receiver has heard it (many
    # airlines fly a number under an unpredictable operational callsign).
    # Show an honest clock-estimated flight instead of a stuck "waiting".
    est = estimated if (estimated and flight.status == SCHEDULED) else None

    accent = {
        SCHEDULED: blue,
        AIRBORNE: palette.HEX["green"],
        OUT_OF_RANGE: palette.HEX["yellow"],
        LANDED: red,
    }.get(flight.status, blue)
    if est:
        accent = palette.HEX["green"]

    # -- header -----------------------------------------------------------
    c.text(90, 148, t["est_head"] if est else _status_word(flight, t),
           size=34, fill=accent, spacing=3)
    c.line(90, 184, 1110, 184, stroke=accent, width=5)

    c.text(90, 316, flight.callsign_iata or flight.callsign, size=140,
           weight="500", spacing=-2)
    subtitle = " · ".join(x for x in [flight.airline, flight.type,
                                      flight.registration] if x)
    c.text(90, 378, subtitle, size=34)

    # -- route ------------------------------------------------------------
    o, d = flight.origin or {}, flight.destination or {}
    c.text(BAR_X0, 560, str(o.get("iata") or "???"), size=112, weight="500", fill=blue)
    c.text(BAR_X1, 560, str(d.get("iata") or "???"), size=112, weight="500",
           fill=blue, anchor="end")
    # Through the shared normaliser: the tracker's airport record and the
    # board's schedule rows must agree on what to call a city.
    c.text(BAR_X0, 610, names.city(o.get("city"), o.get("iata")), size=30)
    c.text(BAR_X1, 610, names.city(d.get("city"), d.get("iata")), size=30,
           anchor="end")

    # -- progress ---------------------------------------------------------
    progress = flight.progress
    if progress is None and est:
        progress = est["frac"]
    c.line(BAR_X0, BAR_Y, BAR_X1, BAR_Y, stroke=blue, width=5,
           stroke_dasharray="2 12")
    if progress is not None:
        x = BAR_X0 + progress * (BAR_X1 - BAR_X0)
        c.line(BAR_X0, BAR_Y, x, BAR_Y, stroke=accent, width=9)
        shape = shapes.get(flight.type)
        if shape and flight.status != LANDED:
            c.add(shape.group(x, BAR_Y - 62, 108,
                              fill=palette.HEX[brand.body], stroke=ink,
                              stroke_width=2.4, rotate=90))
        else:
            c.circle(x, BAR_Y, 15, fill=accent, stroke="none", width=0)
    c.circle(BAR_X0, BAR_Y, 13, fill=blue, stroke="none", width=0)
    c.circle(BAR_X1, BAR_Y, 13, fill=palette.PAPER, stroke=blue, width=5)

    # -- the sentence you read from the sofa ------------------------------
    c.text(90, 900,
           t["est_line"] if est
           else _headline(flight, units, now, t), size=52, weight="500")
    detail = None if est else _detail(flight, units, now, t)
    if detail:
        c.text(90, 952, detail, size=30, fill=blue)

    # -- numbers ----------------------------------------------------------
    if schedule_line:
        # Scheduled times in both clocks: the airports' own, the viewer's.
        # Below the live detail line when one is present — they overprinted
        # each other on TK1986's final approach.
        c.text(90, 1000 if detail else 970, schedule_line,
               size=28 if detail else 32, fill=blue)
    c.line(90, 1030, 1110, 1030, width=3)
    pos = flight.position or {}
    _stat(c, 90, 1120, t["altitude"],
          units.altitude_str(pos["alt_ft"]) if pos.get("alt_ft") else "—",
          _vs_note(pos, units, t))
    _stat(c, 640, 1120, t["speed"],
          units.speed_str(pos["gs"]) if pos.get("gs") else "—", "")
    remaining = "—"
    if flight.remaining_km is not None:
        remaining = (f"{units.from_km(flight.remaining_km):,.0f} "
                     f"{units.distance_suffix}")
    _stat(c, 90, 1330, t["remaining"], remaining,
          t["est_note"] if est else _route_note(flight, units, t))

    if footnote:
        # The traveller's next hop, in the stats grid's empty right cell —
        # most useful mid-connection, when the person watching the frame
        # wants to know what comes after this leg.
        word, route, detail = footnote
        c.text(640, 1319, word, size=30, fill=blue)
        c.text(640, 1390, route, size=42, weight="500")
        c.text(640, 1438, detail, size=27, fill=blue)
    c.line(90, 1500, 1110, 1500, stroke=accent, width=5)
    # The clock appears only while airborne, as a "position as of" stamp.
    # In the waiting and landed states every render must be byte-identical:
    # the frame blits on hash change, and a footer minute-hand was forcing
    # a full 30-second panel refresh of an otherwise unchanged screen.
    day = f"{now.day} {t['months'][now.month]} {now.year}"
    if flight.status == AIRBORNE:
        c.text(90, 1552, f"{day} · {now:%H:%M} · {label}",
               size=30, fill=blue)
    else:
        c.text(90, 1552, f"{day} · {label}", size=30, fill=blue)
    return c


def _status_word(flight: Flight, t: dict) -> str:
    return {
        SCHEDULED: t["scheduled"],
        AIRBORNE: t["airborne"],
        OUT_OF_RANGE: t["out_of_range"],
        LANDED: t["landed"],
    }.get(flight.status, "tracking")


def _headline(flight: Flight, units: Units, now: datetime,
              t: dict) -> str:
    if flight.status == LANDED:
        when = datetime.fromtimestamp(flight.landed_at) if flight.landed_at else now
        return t["landed_at"].format(hh=f"{when:%H:%M}")
    if flight.status == SCHEDULED:
        return t["waiting"]
    eta = flight.eta_minutes
    if eta is None:
        return t["in_air"]
    if eta < 60:
        return t["min_to_go"].format(m=eta)
    return t["hm_to_go"].format(h=int(eta // 60), m=int(eta % 60))


def _detail(flight: Flight, units: Units, now: datetime,
            t: dict) -> str:
    if flight.status == OUT_OF_RANGE:
        silent = flight.silent_minutes or 0
        where = _ocean_hint(flight, t)
        return t["last_heard"].format(where=where, m=silent)
    if flight.status == LANDED and flight.landed_at:
        gone = (now - datetime.fromtimestamp(flight.landed_at)).total_seconds() / 60
        return t["hold"].format(m=max(0, 30 - gone))
    if flight.status == AIRBORNE:
        eta = flight.eta_minutes
        if eta is not None:
            return t["arriving"].format(
                hh=f"{(now + timedelta(minutes=eta)):%H:%M}")
    return ""


def _ocean_hint(flight: Flight, t: dict) -> str:
    """A rough guess at where the gap is, so it reads as an explanation."""
    pos = flight.position
    if not pos:
        return t["out"]
    lat, lon = pos["lat"], pos["lon"]
    if -60 < lon < -10 and 20 < lat < 65:
        return t["atlantic"]
    if 40 < lon < 100 and -10 < lat < 35:
        return t["indian"]
    if (lon > 140 or lon < -120) and -50 < lat < 55:
        return t["pacific"]
    if 10 < lon < 40 and 12 < lat < 32:
        return t["desert"]
    if lat > 62:
        return t["arctic"]
    return t["out"]


def _route_note(flight: Flight, units: Units, t: dict) -> str:
    total = flight.route_km
    if total is None:
        return ""
    return t["of_total"].format(
        d=f"{units.from_km(total):,.0f} {units.distance_suffix}",
        p=(flight.progress or 0) * 100)


def _vs_note(pos: dict, units: Units, t: dict) -> str:
    vs = pos.get("vs") or 0
    if vs > 300:
        return t["climbing"].format(v=units.climb_str(vs))
    if vs < -300:
        return t["descending"].format(v=units.climb_str(vs))
    return t["level"] if pos.get("alt_ft") else ""


def _stat(c: Canvas, x: float, y: float, caption: str, value: str,
          note: str) -> None:
    c.text(x, y, caption, size=30, fill=palette.HEX["blue"])
    c.text(x, y + 78, value, size=76, weight="500")
    if note:
        c.text(x, y + 122, note, size=27)
