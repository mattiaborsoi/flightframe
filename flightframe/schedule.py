"""Scheduled-flight lookups: turning a flight number into times and metal.

Two tiers, honestly separated by what free data can actually answer:

  * Route (origin/destination airports): adsbdb, keyless, cached forever.
    A flight number's route almost never changes; this fills the board the
    moment a flight is typed in.
  * Departure time and aircraft for a SPECIFIC DATE: only schedule APIs
    know this, and none are keyless. If SCHEDULE_API_KEY is set (a free
    aviationstack key: 100 calls/month), flights within REFRESH_DAYS get
    looked up at most every REFRESH_HOURS — ten flights refreshed daily
    through their final week stays comfortably inside the free tier.
    Without a key, those fields stay as entered by hand.

Refreshed fields overwrite the stored row: the whole point is that the
airline's changes win over what was typed at booking time.
"""
from __future__ import annotations

import json
import time
import urllib.parse

from . import sources

REFRESH_DAYS = 7          # only flights this close get API calls
REFRESH_HOURS = 12        # per-flight refresh cadence within that window

AVIATIONSTACK = "https://api.aviationstack.com/v1/flights"
AERODATABOX = "https://aerodatabox.p.rapidapi.com/flights/number"


def route_autofill(flight_no: str, cache_dir, user_agent: str) -> dict:
    """Origin/destination IATA from the keyless route database. Best-effort:
    an unknown callsign or a network blip returns {}."""
    try:
        enricher = sources.Enricher(cache_dir, user_agent)
        route = enricher.route(flight_no) or {}
        enricher.save()
        out = {}
        origin = route.get("origin") or {}
        dest = route.get("destination") or {}
        if origin.get("iata_code"):
            out["origin"] = origin["iata_code"]
        if dest.get("iata_code"):
            out["destination"] = dest["iata_code"]
        return out
    except Exception:
        return {}


def scheduled_details(flight_no: str, date: str, api_key: str,
                      user_agent: str, provider: str = "aviationstack") -> dict:
    """Schedule detail for one dated flight, via whichever provider the
    deployment has a key for.

    Returns any of {dep_time, arr_time, origin, destination, aircraft,
    dep_terminal, dep_gate, delay_min, registration}; {} when the API has
    nothing (or errors — a schedule lookup must never break a render pass)."""
    if not api_key:
        return {}
    if provider == "aerodatabox":
        return _aerodatabox(flight_no, date, api_key, user_agent)
    return _aviationstack(flight_no, date, api_key, user_agent)


def _utc_offset_min(stamp: str | None) -> int | None:
    """"2026-08-28 12:31-04:00" -> -240. The offset is per-date, so summer
    and winter time arrive correct without any timezone database."""
    if not stamp or len(stamp) < 22:
        return None
    tail = stamp[-6:]
    if tail[0] not in "+-" or tail[3] != ":":
        return None
    try:
        minutes = int(tail[1:3]) * 60 + int(tail[4:6])
    except ValueError:
        return None
    return -minutes if tail[0] == "-" else minutes


def _day_gap(base_date: str, stamp: str | None) -> int:
    """Whole days from `base_date` to the date inside `stamp`; 0 if unknown."""
    from datetime import date as _d
    if not stamp or len(stamp) < 10:
        return 0
    try:
        return (_d.fromisoformat(stamp[:10]) - _d.fromisoformat(base_date)).days
    except ValueError:
        return 0


def _hhmm(stamp: str | None) -> str | None:
    """"2026-08-22 08:20+01:00" or ISO-T variants -> "08:20"."""
    if not stamp or len(stamp) < 16:
        return None
    tail = stamp[11:16]
    return tail if tail[:2].isdigit() and tail[2] == ":" else None


def _local_dt(stamp: str | None):
    """"2026-09-14 23:50+02:00" -> an aware datetime, or None."""
    from datetime import datetime
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.strip())
    except ValueError:
        return None


def _minutes_between(sched: str | None, revised: str | None) -> int | None:
    """Delay in minutes; None if unknowable.

    Whole timestamps, never the clock face alone: a 23:50 departure put
    back to 00:20 is thirty minutes late, and subtracting HH:MM made it
    1,410 minutes EARLY — a number the board was saved from printing only
    because it filters delays to positive ones.
    """
    a, b = _local_dt(sched), _local_dt(revised)
    if not (a and b) or a == b:
        return None
    return round((b - a).total_seconds() / 60.0)


def _aerodatabox(flight_no: str, date: str, api_key: str,
                 user_agent: str) -> dict:
    """AeroDataBox (RapidAPI): the Flighty-grade detail — terminals, gates,
    revised times, assigned tail — on a free tier the refresh budget fits."""
    try:
        # No dateLocalRole filter: it turned real flights into empty 204s
        # (verified against BA588 — filtered 204, unfiltered a full leg).
        legs = sources._get(
            f"{AERODATABOX}/{urllib.parse.quote(flight_no)}/{date}",
            user_agent, timeout=15, attempts=1,
            headers={"X-RapidAPI-Key": api_key,
                     "X-RapidAPI-Host": "aerodatabox.p.rapidapi.com"})
        if not isinstance(legs, list) or not legs:
            return {}
        # A flight number can map to several legs — sparse codeshare stubs,
        # and for red-eyes BOTH the leg departing this date and the one
        # arriving on it. Prefer the leg that departs on the asked-for date,
        # then any leg that knows its departure time.
        def _leg_key(l):
            dep_local = ((l.get("departure") or {})
                         .get("scheduledTime") or {}).get("local") or ""
            arr_local = ((l.get("arrival") or {})
                         .get("scheduledTime") or {}).get("local") or ""
            return (dep_local[:10] == date, bool(dep_local), bool(arr_local))
        leg = max(legs, key=_leg_key)
        dep = leg.get("departure") or {}
        arr = leg.get("arrival") or {}
        sched = (dep.get("scheduledTime") or {}).get("local")
        revised = (dep.get("revisedTime") or {}).get("local")
        out: dict = {}
        if _hhmm(revised or sched):
            out["dep_time"] = _hhmm(revised or sched)
            off = _utc_offset_min(revised or sched)
            if off is not None:
                out["dep_offset_min"] = off
            # A delay can push the departure past midnight, onto a date
            # that is no longer the one the flight was filed under. Only
            # the clock face was stored, so the departure instant came out
            # a day early and took the glass a day early with it.
            out["dep_day_offset"] = _day_gap(date, (revised or sched)) or None
        arr_t = ((arr.get("revisedTime") or {}).get("local")
                 or (arr.get("scheduledTime") or {}).get("local"))
        if _hhmm(arr_t):
            out["arr_time"] = _hhmm(arr_t)
            off = _utc_offset_min(arr_t)
            if off is not None:
                out["arr_offset_min"] = off
            # Red-eyes land the day after they leave; the board marks the
            # arrival with "+1". Dates compare in each airport's own local
            # calendar, which is exactly what a passenger's watch does.
            # Measured from the DEPARTURE's date, not the filing date, so
            # the board's "+1" keeps meaning "the day after you leave".
            dep_date = (revised or sched or "")[:10] or date
            out["arr_day_offset"] = _day_gap(dep_date, arr_t) or None
        for side, key in ((dep, "origin"), (arr, "destination")):
            airport = side.get("airport") or {}
            iata = (airport.get("iata") or "").strip()
            if iata:
                out[key] = iata
                # The schedule may correct the airport the route database
                # guessed (BA588: LIN -> MXP); carrying the city with the
                # correction keeps "Milan–MXP" from collapsing to a bare code.
                if airport.get("municipalityName"):
                    out[f"{key}_city"] = airport["municipalityName"]
                # Coordinates too: the keyless route database is a static
                # table and can name last season's airport (BA607: Pisa
                # when today's schedule says Venice), which had the
                # tracker measuring progress from 250 km away.
                loc = airport.get("location") or {}
                if loc.get("lat") is not None and loc.get("lon") is not None:
                    out[f"{key}_lat"] = float(loc["lat"])
                    out[f"{key}_lon"] = float(loc["lon"])
        # Volatile facts are reported as None when the airline withdraws
        # them, so the board can stop showing a gate that was released or
        # a delay that was cancelled. Only ever reached on a leg that
        # parsed: an API error returns {} and changes nothing.
        out["dep_terminal"] = str(dep["terminal"]) if dep.get("terminal") else None
        out["dep_gate"] = str(dep["gate"]) if dep.get("gate") else None
        out["delay_min"] = _minutes_between(sched, revised)
        if leg.get("status"):
            out["airline_status"] = str(leg["status"])
        aircraft = leg.get("aircraft") or {}
        if aircraft.get("model"):
            out["aircraft"] = aircraft["model"]
        if aircraft.get("reg"):
            out["registration"] = aircraft["reg"]
        return out
    except Exception:
        return {}


def _aviationstack(flight_no: str, date: str, api_key: str,
                   user_agent: str) -> dict:
    try:
        query = urllib.parse.urlencode({
            "access_key": api_key, "flight_iata": flight_no,
            "flight_date": date, "limit": 1})
        payload = sources._get(f"{AVIATIONSTACK}?{query}", user_agent,
                               timeout=15, attempts=1)
        rows = (payload or {}).get("data") or []
        if not rows:
            return {}
        row = rows[0]
        out: dict = {}
        dep = row.get("departure") or {}
        if dep.get("scheduled"):
            # "2026-08-20T09:40:00+00:00" -> local-to-airport HH:MM as
            # published; the string carries the airport's own offset.
            out["dep_time"] = dep["scheduled"][11:16]
        if dep.get("iata"):
            out["origin"] = dep["iata"]
        arr = row.get("arrival") or {}
        if arr.get("iata"):
            out["destination"] = arr["iata"]
        aircraft = (row.get("aircraft") or {}).get("iata") \
            or (row.get("aircraft") or {}).get("icao")
        if aircraft:
            out["aircraft"] = aircraft
        return out
    except Exception:
        return {}


def refresh_due(registry, tenant_id: str, api_key: str, cache_dir,
                user_agent: str, now: float | None = None,
                provider: str = "aviationstack") -> int:
    """Refresh every near-term flight whose data may have gone stale.
    Called from the renderer's per-tenant pass; returns refreshes done."""
    from datetime import date as _date
    now = now or time.time()
    done = 0
    for row in registry.flights_for(tenant_id):
        try:
            days_out = (_date.fromisoformat(row["date"]) - _date.today()).days
        except ValueError:
            continue
        if days_out < 0:
            continue
        if days_out > REFRESH_DAYS:
            # Beyond the refresh window the schedule barely moves, but it
            # EXISTS months out — one fill on add (retried monthly, so a
            # not-yet-published schedule still lands eventually) gives the
            # queue its cities and times for pennies of quota.
            cadence_s = 30 * 86400
            retry_s = 24 * 3600
            if now - (row.get("last_refreshed") or 0) < cadence_s:
                continue
        else:
            # Gates and delays only publish in the final hours before
            # departure; a flat 12h cadence would miss them. Departure day
            # refreshes every 3h, tightening to 20 minutes around the
            # departure itself so a late-posted delay reaches the glass
            # while it still matters. A handful of calls, only on flight
            # days, still far inside the free tiers.
            cadence_s = (3 if days_out == 0 else REFRESH_HOURS) * 3600
            if days_out == 0 and row.get("dep_time"):
                try:
                    from datetime import datetime, timedelta, timezone
                    off = row.get("dep_offset_min")
                    dep = datetime.fromisoformat(
                        f"{row['date']} {row['dep_time']}").replace(
                        tzinfo=timezone(timedelta(
                            minutes=off if off is not None else 0)))
                    dep += timedelta(days=row.get("dep_day_offset") or 0)
                    to_dep = dep.timestamp() - now
                    if -6 * 3600 <= to_dep <= 3 * 3600:
                        cadence_s = 20 * 60
                except ValueError:
                    pass
            # Retrying an empty answer must never be FASTER than the
            # normal cadence, only sooner than a long one.
            retry_s = min(cadence_s, 3600)
            if now - (row.get("last_refreshed") or 0) < cadence_s:
                continue
        fields = {}
        if not (row.get("origin") and row.get("destination")):
            fields.update(route_autofill(row["flight_no"], cache_dir,
                                         user_agent))
        if done:
            # The free tiers meter per second as well as per month; a burst
            # of back-to-back lookups had every flight after the first come
            # back 429-empty and stamped as done.
            time.sleep(1.2)
        looked_up = scheduled_details(row["flight_no"], row["date"],
                                      api_key, user_agent,
                                      provider=provider)
        fields.update(looked_up)
        # An empty answer is a rate limit or an unpublished schedule, not
        # a fact worth remembering for a whole cadence period: backdate
        # the stamp so the next attempt lands sooner. The backdate is a
        # FRACTION OF THIS ROW'S CADENCE. A flat eleven hours assumed the
        # twelve-hour cadence and, on a departure day metered at twenty
        # minutes, left every row permanently overdue: an empty answer
        # then retried on every renderer pass, ten times the normal call
        # volume, and a rate limit fed itself.
        stamp = now if looked_up else now - cadence_s + retry_s
        registry.flight_refresh(row["id"], fields, stamp)
        done += 1
    return done
