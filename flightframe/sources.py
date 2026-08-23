"""Live aircraft data.

Two public, keyless APIs, both volunteer-funded:

  adsb.lol   positions, altitude, speed, track, type, registration
  adsbdb     callsign -> route with airport coordinates; hex -> airframe

ADS-B does not broadcast where a flight is going, so origin and destination
always need the second lookup. Routes and airframes are near-static, so they
are cached on disk indefinitely and only positions are re-fetched.
"""
from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

ADSB_LOL = "https://api.adsb.lol/v2/lat/{lat}/lon/{lon}/dist/{dist}"
ADSBDB = "https://api.adsbdb.com/v0"

EARTH_NM = 3440.065


@dataclass
class Aircraft:
    hex: str
    callsign: str | None
    type: str | None            # ICAO type code, e.g. A20N
    registration: str | None
    lat: float
    lon: float
    altitude_ft: float | None
    ground_speed_kt: float | None
    track_deg: float | None
    vertical_fpm: float | None
    distance_nm: float
    bearing_deg: float
    seen_at: float

    # filled in by enrich()
    airline: str | None = None
    airline_icao: str | None = None
    origin: dict[str, Any] | None = None
    destination: dict[str, Any] | None = None
    type_name: str | None = None

    @property
    def route(self) -> str | None:
        if self.origin and self.destination:
            return f"{self.origin.get('iata')}–{self.destination.get('iata')}"
        return None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _get(url: str, user_agent: str, timeout: float = 20.0,
         attempts: int = 1,
         headers: dict[str, str] | None = None) -> dict[str, Any] | None:
    for attempt in range(attempts):
        req = urllib.request.Request(
            url, headers={"User-Agent": user_agent, **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
            if attempt < attempts - 1:
                time.sleep(2 * (attempt + 1))
    return None


def bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing in degrees, 0 = north."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_NM * math.asin(math.sqrt(a))


def fetch(lat: float, lon: float, radius_nm: float, user_agent: str,
          min_altitude_ft: float = 0.0) -> list[Aircraft] | None:
    """Everything airborne within radius_nm right now.

    Ground traffic is excluded by adsb.lol reporting alt_baro as the string
    "ground". `min_altitude_ft` additionally drops aircraft that are airborne
    but too low to be interesting — circuit traffic and short final, which
    near an airport is most of what you see.

    Returns None when the request itself failed, as distinct from an empty list
    meaning the sky really is clear. Collapsing the two overwrote a good poster
    with one reading "0 aircraft" the first time adsb.lol rate-limited us.
    """
    url = ADSB_LOL.format(lat=round(lat, 5), lon=round(lon, 5), dist=int(radius_nm))
    payload = _get(url, user_agent, attempts=3)
    if payload is None or "ac" not in payload:
        return None

    now = time.time()
    out: list[Aircraft] = []
    for raw in payload.get("ac", []):
        alt = raw.get("alt_baro")
        if not isinstance(alt, (int, float)):
            continue                      # "ground", or no altitude at all
        if alt < min_altitude_ft:
            continue
        if raw.get("lat") is None or raw.get("lon") is None:
            continue
        plat, plon = float(raw["lat"]), float(raw["lon"])
        callsign = (raw.get("flight") or "").strip() or None
        out.append(Aircraft(
            hex=(raw.get("hex") or "").strip().lower(),
            callsign=callsign,
            type=(raw.get("t") or "").strip() or None,
            registration=(raw.get("r") or "").strip() or None,
            lat=plat,
            lon=plon,
            altitude_ft=float(alt),
            ground_speed_kt=_maybe_float(raw.get("gs")),
            track_deg=_maybe_float(raw.get("track")),
            vertical_fpm=_maybe_float(raw.get("baro_rate")),
            distance_nm=_maybe_float(raw.get("dst")) or haversine_nm(lat, lon, plat, plon),
            bearing_deg=bearing(lat, lon, plat, plon),
            seen_at=now,
        ))
    out.sort(key=lambda a: a.distance_nm)
    return out


SNAPSHOT_STALE_S = 300


def lockey(lat: float, lon: float, radius_nm: float) -> str:
    """Location-group key: tenants within ~100m and the same radius share one
    adsb.lol poll. Also the snapshot filename, so keep it filesystem-safe."""
    return f"{lat:.3f}_{lon:.3f}_{int(radius_nm)}".replace("-", "m")


def write_snapshot(cache_dir: Path, key: str,
                   aircraft: list[Aircraft]) -> None:
    live = cache_dir / "live"
    live.mkdir(parents=True, exist_ok=True)
    tmp = live / f"{key}.tmp"
    tmp.write_text(json.dumps({"fetched_at": time.time(),
                               "aircraft": [a.as_dict() for a in aircraft]}))
    os.replace(tmp, live / f"{key}.json")


def read_snapshot(cache_dir: Path, key: str,
                  min_altitude_ft: float = 0) -> list[Aircraft] | None:
    """The renderer's view of the sky. None for missing or stale — exactly
    the contract fetch() has for a failed request, so a dead collector reads
    as an outage and the previous posters survive."""
    path = cache_dir / "live" / f"{key}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if time.time() - float(payload.get("fetched_at", 0)) > SNAPSHOT_STALE_S:
        return None
    fields = {f.name for f in Aircraft.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    out = []
    for raw in payload.get("aircraft", []):
        ac = Aircraft(**{k: v for k, v in raw.items() if k in fields})
        if ac.altitude_ft is None or ac.altitude_ft >= min_altitude_ft:
            out.append(ac)
    return out


def _maybe_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class Enricher:
    """adsbdb lookups, cached on disk forever.

    Negative results are cached too. Plenty of callsigns — business jets,
    positioning flights, most military — simply have no route on file, and
    re-asking on every render would hammer a free service for nothing.
    """

    def __init__(self, cache_dir: Path, user_agent: str):
        self.path = cache_dir / "adsbdb.json"
        self.user_agent = user_agent
        self.cache: dict[str, Any] = {}
        if self.path.exists():
            try:
                self.cache = json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                self.cache = {}
        self._dirty = False

    def save(self) -> None:
        """Merge-then-replace. This cache is shared by the collector, the
        renderer, and web threads across every tenant; a plain rewrite from
        one process silently threw away entries another had just learned.
        Merging on save keeps the union (negative results included), and the
        tmp+replace makes the write atomic."""
        if not self._dirty:
            return
        merged: dict = {}
        try:
            merged = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
        merged.update(self.cache)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(merged), encoding="utf-8")
        os.replace(tmp, self.path)
        self.cache = merged
        self._dirty = False

    def _lookup(self, key: str, url: str) -> Any:
        if key in self.cache:
            return self.cache[key]
        payload = _get(url, self.user_agent)
        # Only persist a real answer. _get returns None for a network failure
        # exactly as for "nothing on file", and this cache is forever and
        # shared across tenants — caching a transient failure would blank a
        # callsign's route from every poster permanently. A genuine no-route
        # is re-queried next time (cheap; most callsigns do have a route).
        if payload is not None:
            self.cache[key] = payload
            self._dirty = True
        return payload

    def route(self, callsign: str) -> dict[str, Any] | None:
        payload = self._lookup(f"cs:{callsign}", f"{ADSBDB}/callsign/{callsign}")
        try:
            return payload["response"]["flightroute"]
        except (TypeError, KeyError):
            return None

    def airframe(self, hexcode: str) -> dict[str, Any] | None:
        payload = self._lookup(f"hex:{hexcode}", f"{ADSBDB}/aircraft/{hexcode}")
        try:
            return payload["response"]["aircraft"]
        except (TypeError, KeyError):
            return None

    def apply(self, aircraft: list[Aircraft], limit: int | None = None) -> list[Aircraft]:
        """Attach route and airframe detail, most interesting first."""
        targets = aircraft if limit is None else aircraft[:limit]
        for ac in targets:
            if ac.callsign:
                fr = self.route(ac.callsign)
                if fr:
                    ac.airline = (fr.get("airline") or {}).get("name")
                    ac.airline_icao = (fr.get("airline") or {}).get("icao")
                    ac.origin = _airport(fr.get("origin"))
                    ac.destination = _airport(fr.get("destination"))
            if ac.hex:
                af = self.airframe(ac.hex)
                if af:
                    ac.type_name = af.get("type")
                    ac.airline = ac.airline or af.get("registered_owner")
                    ac.airline_icao = ac.airline_icao or af.get(
                        "registered_owner_operator_flag_code")
        self.save()
        return aircraft


def _airport(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    if not raw:
        return None
    return {
        "iata": raw.get("iata_code"),
        "icao": raw.get("icao_code"),
        "name": raw.get("name"),
        "city": raw.get("municipality"),
        "country": raw.get("country_name"),
        "lat": raw.get("latitude"),
        "lon": raw.get("longitude"),
    }
