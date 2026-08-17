"""A minimal ground reference, so the trace has somewhere to sit.

Without it a long-exposure plot is pretty but unplaceable — you cannot tell
whether a bundle of tracks is over the City or over Kent. The Thames and the
M25 are enough: two lines that anyone who lives here reads instantly, and
between them they carry scale without turning the poster into a map.

Geometry comes from OpenStreetMap via Overpass, simplified with
Douglas-Peucker and cached on disk. OSM data is ODbL — attribute it if you
publish renders.

Deliberately not drawn: borough boundaries, roads, place labels. The tracks are
the subject; this is a watermark under them.
"""
from __future__ import annotations

import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

OVERPASS = "https://overpass-api.de/api/interpreter"

# name -> Overpass selector. Keep this short; each entry is ink on the poster.
LAYERS: dict[str, str] = {
    "thames": 'way["waterway"="river"]["name"="River Thames"]',
    "motorway": 'way["ref"="M25"]["highway"="motorway"]',
    # Runways do double duty: they mark where the airports are, and their
    # orientation explains why the approach corridors lie where they do.
    "runway": 'way["aeroway"="runway"]',
}

# Aerodromes are fetched as points rather than lines, and only when they carry
# an IATA code — that filters farm strips and gliding clubs out automatically
# without hardcoding a list of "important" airports.
AIRPORTS = 'nwr["aeroway"="aerodrome"]["iata"]'

KM_PER_DEG_LAT = 111.32


class Basemap:
    def __init__(self, cache_dir: Path, user_agent: str = "flightframe/0.1"):
        self.dir = cache_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.user_agent = user_agent

    def _cache_path(self, lat: float, lon: float, radius_km: float) -> Path:
        return self.dir / f"basemap_{lat:.2f}_{lon:.2f}_{radius_km:.0f}.json"

    def features(self, lat: float, lon: float, radius_km: float,
                 tolerance_deg: float = 0.0008) -> dict[str, list[list[tuple[float, float]]]]:
        """layer name -> list of polylines, each a list of (lat, lon)."""
        path = self._cache_path(lat, lon, radius_km)
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                return {k: [[tuple(p) for p in line] for line in v] for k, v in raw.items()}
            except (json.JSONDecodeError, TypeError, ValueError):
                pass

        dlat = radius_km / KM_PER_DEG_LAT
        dlon = dlat / max(math.cos(math.radians(lat)), 0.01)
        bbox = (lat - dlat, lon - dlon, lat + dlat, lon + dlon)

        out: dict[str, list[list[tuple[float, float]]]] = {}
        complete = True
        for i, (name, selector) in enumerate(LAYERS.items()):
            if i:
                time.sleep(2)          # Overpass is free and shared; don't hammer it
            elements = self._query(selector, bbox)
            if elements is None:
                complete = False
                out[name] = []
                continue
            lines = []
            for el in elements:
                pts = [(p["lat"], p["lon"]) for p in el.get("geometry", [])]
                if len(pts) >= 2:
                    simplified = _douglas_peucker(pts, tolerance_deg)
                    if len(simplified) >= 2:
                        lines.append(simplified)
            out[name] = lines

        # Only cache a complete answer. Caching a partial one bakes a missing
        # layer in permanently, and the failure is invisible from then on —
        # which is exactly how the Thames went missing the first time.
        if complete and all(out.values()):
            path.write_text(json.dumps(out), encoding="utf-8")
        else:
            missing = [k for k, v in out.items() if not v]
            print(f"basemap: incomplete ({', '.join(missing)} missing), not cached — "
                  f"Overpass may be rate-limiting; it will retry next render",
                  file=sys.stderr, flush=True)
        return out

    def airports(self, lat: float, lon: float, radius_km: float) -> list[dict]:
        """[{iata, name, lat, lon}, ...] for aerodromes with an IATA code."""
        path = self.dir / f"airports_{lat:.2f}_{lon:.2f}_{radius_km:.0f}.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass

        dlat = radius_km / KM_PER_DEG_LAT
        dlon = dlat / max(math.cos(math.radians(lat)), 0.01)
        bbox = (lat - dlat, lon - dlon, lat + dlat, lon + dlon)
        elements = self._query(AIRPORTS, bbox, out="out center tags;")
        if elements is None:
            return []

        out = []
        for el in elements:
            tags = el.get("tags", {})
            centre = el.get("center") or {"lat": el.get("lat"), "lon": el.get("lon")}
            if centre.get("lat") is None or not tags.get("iata"):
                continue
            out.append({
                "iata": tags["iata"],
                "name": tags.get("name", ""),
                "lat": centre["lat"],
                "lon": centre["lon"],
            })
        if out:
            path.write_text(json.dumps(out), encoding="utf-8")
        return out

    def _query(self, selector: str, bbox: tuple[float, float, float, float],
               attempts: int = 3, out: str = "out geom;") -> list[dict] | None:
        """Elements, or None if the query genuinely failed (as opposed to
        succeeding and matching nothing). The distinction matters: one is worth
        retrying and caching around, the other is not."""
        body = (f"[out:json][timeout:90];({selector}"
                f"({bbox[0]:.4f},{bbox[1]:.4f},{bbox[2]:.4f},{bbox[3]:.4f}););{out}")
        for attempt in range(attempts):
            req = urllib.request.Request(
                OVERPASS,
                data=body.encode("utf-8"),
                headers={"User-Agent": self.user_agent,
                         "Content-Type": "application/x-www-form-urlencoded"},
            )
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    return json.loads(resp.read().decode("utf-8")).get("elements", [])
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError,
                    OSError) as exc:
                if attempt == attempts - 1:
                    print(f"basemap: {selector[:28]}… failed after {attempts} tries "
                          f"({type(exc).__name__})", file=sys.stderr, flush=True)
                    return None
                time.sleep(3 * (attempt + 1))
        return None


def _douglas_peucker(points: list[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
    """Iterative Douglas-Peucker. The Thames alone arrives as ~4,700 points."""
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]

    while stack:
        first, last = stack.pop()
        worst, index = 0.0, -1
        ax, ay = points[first]
        bx, by = points[last]
        for i in range(first + 1, last):
            d = _perp(points[i], (ax, ay), (bx, by))
            if d > worst:
                worst, index = d, i
        if worst > tol and index != -1:
            keep[index] = True
            stack.append((first, index))
            stack.append((index, last))

    return [p for p, k in zip(points, keep, strict=True) if k]


def _perp(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    px, py = p
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))
