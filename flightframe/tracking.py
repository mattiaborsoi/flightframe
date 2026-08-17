"""Follow one specific flight from pushback to thirty minutes after landing.

You give it a flight number as it appears on a boarding pass ("BA117");
adsbdb turns that into an ICAO callsign and a route with airport coordinates,
and adsb.lol's global callsign lookup finds the aircraft wherever it is.

The hard part is not the tracking, it is the gaps. adsb.lol is a volunteer
receiver network with nothing in the middle of an ocean, so a London-New York
flight simply vanishes for two hours somewhere past Ireland. Treating that as
"landed" or "lost" would break the feature exactly when it is most interesting,
so a flight that disappears goes to OUT_OF_RANGE and keeps its last known
position on the poster until it either comes back or is overdue.

States:

    SCHEDULED      route resolved, no position yet — not off the ground
    AIRBORNE       being received
    OUT_OF_RANGE   was airborne, nothing heard for a while, not near arrival
    LANDED         on the ground, or vanished low and close to the destination
    EXPIRED        landed more than HOLD_MINUTES ago; the frame moves on
"""
from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any

from . import sources

SCHEDULED, AIRBORNE, OUT_OF_RANGE, LANDED, EXPIRED = (
    "scheduled", "airborne", "out_of_range", "landed", "expired")

# How long a landed flight stays on the wall before the normal rotation resumes.
HOLD_MINUTES = 30
# Silence longer than this means we have lost contact, not that it landed.
SILENCE_MINUTES = 12
# Within this of the destination, low and silent, counts as arrived.
ARRIVAL_KM = 45.0
ARRIVAL_ALT_FT = 10_000
# A flight nobody ever hears from is abandoned after this.
STALE_HOURS = 26


@dataclass
class Flight:
    query: str                       # what the user typed
    callsign: str                    # ICAO, e.g. BAW117
    callsign_iata: str | None
    airline: str | None
    origin: dict[str, Any] | None
    destination: dict[str, Any] | None
    started_at: float
    status: str = SCHEDULED
    last_seen: float | None = None
    landed_at: float | None = None
    position: dict[str, Any] | None = None
    registration: str | None = None
    type: str | None = None
    history: list[list[float]] = field(default_factory=list)   # [lat, lon] samples

    # -- derived ----------------------------------------------------------

    @property
    def route_km(self) -> float | None:
        if not (self.origin and self.destination):
            return None
        return _km(self.origin["lat"], self.origin["lon"],
                   self.destination["lat"], self.destination["lon"])

    @property
    def progress(self) -> float | None:
        """0..1 along the route, from how far it is between the two airports.

        Using distance-from-origin over total would run past 1.0 on any route
        that is not a straight line; splitting the two legs keeps it honest
        even when the aircraft is well off the great circle.
        """
        if not (self.position and self.origin and self.destination):
            return None
        lat, lon = self.position["lat"], self.position["lon"]
        flown = _km(self.origin["lat"], self.origin["lon"], lat, lon)
        remaining = _km(lat, lon, self.destination["lat"], self.destination["lon"])
        total = flown + remaining
        if total <= 0:
            return None
        if self.status == LANDED:
            return 1.0
        return max(0.0, min(1.0, flown / total))

    @property
    def remaining_km(self) -> float | None:
        if not (self.position and self.destination):
            return None
        return _km(self.position["lat"], self.position["lon"],
                   self.destination["lat"], self.destination["lon"])

    @property
    def eta_minutes(self) -> float | None:
        km = self.remaining_km
        speed = (self.position or {}).get("gs")
        if km is None or not speed or speed < 40:
            return None
        return km / (speed * 1.852) * 60

    @property
    def panel_interval_s(self) -> int:
        """How long the frame should sleep before showing this flight again.

        A flat five minutes across a long-haul is roughly 85 full panel
        refreshes, a serious bite out of a charge, and almost all of them show
        an aeroplane that has moved a centimetre along a bar. The interesting
        moments are the ends: climb-out, descent, and the last half hour. So
        cruise is cheap and the approach is expensive, which is the right way
        round.
        """
        if self.status == SCHEDULED:
            return 15 * 60
        if self.status == OUT_OF_RANGE:
            return 30 * 60          # no receivers out there; polling won't help
        if self.status == LANDED:
            return 10 * 60

        vs = (self.position or {}).get("vs") or 0
        if abs(vs) > 700:
            return 5 * 60           # climbing out or coming down

        eta = self.eta_minutes
        if eta is None:
            return 20 * 60
        if eta < 45:
            return 5 * 60
        if eta < 120:
            return 15 * 60
        return 30 * 60

    @property
    def silent_minutes(self) -> float | None:
        if self.last_seen is None:
            return None
        return (time.time() - self.last_seen) / 60

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.update(progress=self.progress, remaining_km=self.remaining_km,
                 eta_minutes=self.eta_minutes, route_km=self.route_km,
                 silent_minutes=self.silent_minutes,
                 panel_interval_s=self.panel_interval_s)
        return d


class Tracker:
    def __init__(self, data_dir: Path, cache_dir: Path, user_agent: str):
        self.path = data_dir / "tracking.json"
        self.cache_dir = cache_dir
        self.user_agent = user_agent

    # -- persistence ------------------------------------------------------

    def load(self) -> Flight | None:
        if not self.path.exists():
            return None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return Flight(**raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            return None

    def save(self, flight: Flight) -> None:
        # Atomic: the renderer and the web server both write this file, and a
        # torn write loses the tracked flight entirely.
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(flight)), encoding="utf-8")
        os.replace(tmp, self.path)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)

    # -- lifecycle --------------------------------------------------------

    def start(self, query: str) -> tuple[Flight | None, str]:
        """Resolve a flight number and begin tracking. Returns (flight, message)."""
        query = query.strip().upper().replace(" ", "")
        if not query:
            return None, "Enter a flight number, for example BA117."

        enricher = sources.Enricher(self.cache_dir, self.user_agent)
        route = enricher.route(query)
        enricher.save()
        if not route:
            return None, (f"No route on file for {query}. Try the airline's own "
                          "code (BA117), or check the flight is operating today.")

        origin = _airport(route.get("origin"))
        destination = _airport(route.get("destination"))
        if not (origin and destination):
            return None, f"{query} resolved, but without usable airport coordinates."

        flight = Flight(
            query=query,
            callsign=route.get("callsign_icao") or route.get("callsign") or query,
            callsign_iata=route.get("callsign_iata"),
            airline=(route.get("airline") or {}).get("name"),
            origin=origin,
            destination=destination,
            started_at=time.time(),
        )
        self.save(flight)
        return flight, (f"Tracking {flight.callsign_iata or flight.callsign}: "
                        f"{origin['iata']} to {destination['iata']}.")

    def poll(self) -> Flight | None:
        """Refresh the tracked flight. Returns None when nothing is tracked."""
        flight = self.load()
        if flight is None:
            return None

        if flight.status == EXPIRED:
            self.clear()
            return None

        # Give up on a flight that never appeared.
        if (flight.last_seen is None
                and time.time() - flight.started_at > STALE_HOURS * 3600):
            self.clear()
            return None

        raw = sources._get(
            f"https://api.adsb.lol/v2/callsign/{flight.callsign}",
            self.user_agent, attempts=2)

        seen = None
        if raw and raw.get("ac"):
            seen = raw["ac"][0]

        now = time.time()
        if seen is not None:
            alt = seen.get("alt_baro")
            on_ground = not isinstance(alt, (int, float))
            flight.last_seen = now
            flight.registration = (seen.get("r") or "").strip() or flight.registration
            flight.type = (seen.get("t") or "").strip() or flight.type
            if seen.get("lat") is not None:
                flight.position = {
                    "lat": float(seen["lat"]),
                    "lon": float(seen["lon"]),
                    "alt_ft": None if on_ground else float(alt),
                    "gs": _f(seen.get("gs")),
                    "track": _f(seen.get("track")),
                    "vs": _f(seen.get("baro_rate")),
                }
                point = [round(float(seen["lat"]), 3), round(float(seen["lon"]), 3)]
                if not flight.history or flight.history[-1] != point:
                    flight.history.append(point)
                    flight.history = flight.history[-400:]

            if on_ground and flight.status in (AIRBORNE, OUT_OF_RANGE):
                flight.status, flight.landed_at = LANDED, now
            elif not on_ground:
                flight.status = AIRBORNE
        else:
            silent = (now - flight.last_seen) / 60 if flight.last_seen else 0
            if flight.status in (AIRBORNE, OUT_OF_RANGE) and silent > SILENCE_MINUTES:
                # Vanishing low and near the destination is an arrival. Vanishing
                # at cruise over water is a coverage gap, and must not be
                # confused with one.
                near = (flight.remaining_km is not None
                        and flight.remaining_km < ARRIVAL_KM)
                low = ((flight.position or {}).get("alt_ft") or 1e9) < ARRIVAL_ALT_FT
                if near and low:
                    flight.status, flight.landed_at = LANDED, flight.last_seen
                else:
                    flight.status = OUT_OF_RANGE

        if (flight.status == LANDED and flight.landed_at
                and (now - flight.landed_at) / 60 > HOLD_MINUTES):
            flight.status = EXPIRED

        self.save(flight)
        return None if flight.status == EXPIRED else flight


def _airport(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        return {
            "iata": raw.get("iata_code"),
            "icao": raw.get("icao_code"),
            "name": raw.get("name"),
            "city": raw.get("municipality"),
            "country": raw.get("country_name"),
            "lat": float(raw["latitude"]),
            "lon": float(raw["longitude"]),
        }
    except (KeyError, TypeError, ValueError):
        return None


def _f(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))
