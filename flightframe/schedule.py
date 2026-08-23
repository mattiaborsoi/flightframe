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


def _hhmm(stamp: str | None) -> str | None:
    """"2026-08-22 08:20+01:00" or ISO-T variants -> "08:20"."""
    if not stamp or len(stamp) < 16:
        return None
    tail = stamp[11:16]
    return tail if tail[:2].isdigit() and tail[2] == ":" else None


def _minutes_between(sched: str | None, revised: str | None) -> int | None:
    """Delay in minutes, from same-day local timestamps; None if unknowable."""
    s, r = _hhmm(sched), _hhmm(revised)
    if not (s and r) or s == r:
        return None
    return ((int(r[:2]) * 60 + int(r[3:])) - (int(s[:2]) * 60 + int(s[3:])))


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
        # A flight number can map to several legs, some sparse. Prefer the
        # one that actually knows its departure time.
        leg = max(legs, key=lambda l: (
            bool(((l.get("departure") or {}).get("scheduledTime") or {})
                 .get("local")),
            bool(((l.get("arrival") or {}).get("scheduledTime") or {})
                 .get("local"))))
        dep = leg.get("departure") or {}
        arr = leg.get("arrival") or {}
        sched = (dep.get("scheduledTime") or {}).get("local")
        revised = (dep.get("revisedTime") or {}).get("local")
        out: dict = {}
        if _hhmm(revised or sched):
            out["dep_time"] = _hhmm(revised or sched)
        arr_t = ((arr.get("revisedTime") or {}).get("local")
                 or (arr.get("scheduledTime") or {}).get("local"))
        if _hhmm(arr_t):
            out["arr_time"] = _hhmm(arr_t)
        for side, key in ((dep, "origin"), (arr, "destination")):
            iata = ((side.get("airport") or {}).get("iata") or "").strip()
            if iata:
                out[key] = iata
        if dep.get("terminal"):
            out["dep_terminal"] = str(dep["terminal"])
        if dep.get("gate"):
            out["dep_gate"] = str(dep["gate"])
        delay = _minutes_between(sched, revised)
        if delay is not None:
            out["delay_min"] = delay
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
        if not (0 <= days_out <= REFRESH_DAYS):
            continue
        # Gates and delays only publish in the final hours before departure;
        # a flat 12h cadence would miss them. Departure day refreshes every
        # 3h (~8 extra calls per flight, still far inside the free tiers).
        cadence_h = 3 if days_out == 0 else REFRESH_HOURS
        if now - (row.get("last_refreshed") or 0) < cadence_h * 3600:
            continue
        fields = {}
        if not (row.get("origin") and row.get("destination")):
            fields.update(route_autofill(row["flight_no"], cache_dir,
                                         user_agent))
        fields.update(scheduled_details(row["flight_no"], row["date"],
                                        api_key, user_agent,
                                        provider=provider))
        registry.flight_refresh(row["id"], fields, now)
        done += 1
    return done
