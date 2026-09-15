"""Lookups against two public, keyless APIs, both volunteer-funded:

  adsb.lol   live positions for ONE aircraft at a time, by hex, callsign
             or registration — queried straight from the tracker
  adsbdb     callsign -> route with airport coordinates; hex -> airframe

ADS-B does not broadcast where a flight is going, so origin and destination
always need the second lookup. Routes and airframes are near-static and are
cached on disk indefinitely.

This module used to sweep the whole sky around a home location once a
minute and keep a position history per tenant. The four designs that drew
that sky were removed in Sept 2026, and the sweep, the snapshots and the
history went with them.
"""
from __future__ import annotations

import json
import re
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ADSBDB = "https://api.adsbdb.com/v0"

EARTH_NM = 3440.065


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


_CALLSIGN_RE = re.compile(r"^[A-Z0-9]{2,8}$")


def unpad_callsign(cs: str) -> str | None:
    """"BA0607" -> "BA607"; None when there is no padding to strip.

    Only fires on a real leading zero after a 2-character (IATA) or
    3-character (ICAO) airline code, so "SK987" and "F92538" are left
    exactly as they are.
    """
    cs = (cs or "").strip().upper()
    for n in (2, 3):
        head, tail = cs[:n], cs[n:]
        if len(tail) > 1 and tail[0] == "0" and tail.isdigit():
            return head + tail.lstrip("0")
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
        # A transponder broadcasts whatever the crew typed, and some of it
        # is not a callsign: "BAW671 0" (a real one, seen overhead) has a
        # space in the middle, which urllib refuses to put in a URL. The
        # exception escaped into the renderer and cost that tenant its
        # whole pass — six posters — sixty times in a day.
        if not _CALLSIGN_RE.match((callsign or "").strip().upper()):
            return None
        route = self._route_exact(callsign)
        if route is not None:
            return route
        # Tickets and airline apps zero-pad the number ("BA0607"); the route
        # database stores it bare ("BA607") and answers "unknown callsign"
        # to the padded form. A padded number therefore resolved its
        # airports from the schedule API but could never be TRACKED: the
        # tracker needs this lookup for the ICAO callsign and the airport
        # coordinates, and it failed silently on every pass.
        bare = unpad_callsign(callsign)
        return self._route_exact(bare) if bare else None

    def _route_exact(self, callsign: str) -> dict[str, Any] | None:
        quoted = urllib.parse.quote(callsign.strip().upper(), safe="")
        payload = self._lookup(f"cs:{callsign}", f"{ADSBDB}/callsign/{quoted}")
        try:
            return payload["response"]["flightroute"]
        except (TypeError, KeyError):
            return None

    def airframe(self, hexcode: str) -> dict[str, Any] | None:
        quoted = urllib.parse.quote((hexcode or "").strip(), safe="")
        payload = self._lookup(f"hex:{hexcode}", f"{ADSBDB}/aircraft/{quoted}")
        try:
            return payload["response"]["aircraft"]
        except (TypeError, KeyError):
            return None

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
