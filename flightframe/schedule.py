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
                      user_agent: str) -> dict:
    """Departure time and aircraft for one dated flight, via aviationstack.

    Returns any of {dep_time, aircraft, origin, destination}; {} when the
    API has nothing (or errors — a schedule lookup must never break a
    render pass)."""
    if not api_key:
        return {}
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
                user_agent: str, now: float | None = None) -> int:
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
        if now - (row.get("last_refreshed") or 0) < REFRESH_HOURS * 3600:
            continue
        fields = {}
        if not (row.get("origin") and row.get("destination")):
            fields.update(route_autofill(row["flight_no"], cache_dir,
                                         user_agent))
        fields.update(scheduled_details(row["flight_no"], row["date"],
                                        api_key, user_agent))
        registry.flight_refresh(row["id"], fields, now)
        done += 1
    return done
