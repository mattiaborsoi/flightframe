"""Settings, loaded from the environment with public-safe defaults.

Every default here points at Oxford Circus. The real location lives only in
.env, which is gitignored. Nothing in the committed tree should reveal where a
frame actually hangs.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Oxford Circus, London.
DEFAULT_LAT = 51.5154
DEFAULT_LON = -0.1410
DEFAULT_LABEL = "Oxford Circus"


def _load_dotenv(path: Path) -> dict[str, str]:
    """Minimal .env reader. Returns the values instead of mutating
    os.environ: the old setdefault approach made a second load() in the same
    process silently reuse the first caller's values, which is exactly the
    bug multi-tenancy cannot afford. Real environment variables still win —
    see load().
    """
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def _hhmm(value: str, fallback: time) -> time:
    try:
        hh, _, mm = value.partition(":")
        return time(int(hh), int(mm))
    except (ValueError, TypeError):
        return fallback


@dataclass(frozen=True)
class Settings:
    lat: float
    lon: float
    label: str
    radius_nm: float
    refresh_minutes: int
    awake_from: time
    awake_until: time
    trace_hours: int
    units_name: str
    min_altitude_ft: float
    section_radius_km: float
    user_agent: str
    data_dir: Path
    out_dir: Path
    cache_dir: Path

    @property
    def units(self):
        from . import units as _u
        return _u.get(self.units_name)

    @property
    def is_default_location(self) -> bool:
        """True when running on the public placeholder rather than a real home."""
        return (round(self.lat, 4), round(self.lon, 4)) == (DEFAULT_LAT, DEFAULT_LON)


def load(env_file: Path | None = None) -> Settings:
    dotenv = _load_dotenv(env_file or ROOT / ".env")

    def g(key: str, default=None):
        # Real environment beats .env beats default — same precedence as
        # before, without writing into os.environ.
        return os.environ.get(key, dotenv.get(key, default))

    data_dir = Path(g("DATA_DIR", str(ROOT / "data")))
    out_dir = Path(g("OUT_DIR", str(ROOT / "out")))
    cache_dir = Path(g("CACHE_DIR", str(ROOT / "cache")))
    for d in (data_dir, out_dir, cache_dir):
        d.mkdir(parents=True, exist_ok=True)

    return Settings(
        lat=float(g("HOME_LAT", DEFAULT_LAT)),
        lon=float(g("HOME_LON", DEFAULT_LON)),
        label=g("HOME_LABEL", DEFAULT_LABEL),
        radius_nm=float(g("RADIUS_NM", 25)),
        refresh_minutes=int(g("REFRESH_MINUTES", 15)),
        awake_from=_hhmm(g("AWAKE_FROM", ""), time(7, 0)),
        awake_until=_hhmm(g("AWAKE_UNTIL", ""), time(23, 0)),
        trace_hours=int(g("TRACE_HOURS", 12)),
        units_name=g("UNITS", "metric"),
        min_altitude_ft=float(g("MIN_ALTITUDE_FT", 1000)),
        section_radius_km=float(g("SECTION_RADIUS_KM", 25)),
        user_agent=g("USER_AGENT", "flightframe/0.1"),
        data_dir=data_dir,
        out_dir=out_dir,
        cache_dir=cache_dir,
    )


@dataclass(frozen=True)
class AppConfig:
    """Process-wide multi-tenant configuration: shared paths and knobs.

    Everything location-shaped lives on the per-tenant Settings built by
    for_tenant(); AppConfig deliberately has no lat/lon anywhere.
    """
    data_dir: Path
    out_dir: Path
    cache_dir: Path
    user_agent: str
    app_hosts: frozenset[str]     # hostnames we serve; used by the CSRF check
    schedule_api_key: str = ""    # optional schedule-API key; see schedule.py
    schedule_provider: str = "aviationstack"   # or "aerodatabox" (RapidAPI)

    @property
    def registry_path(self) -> Path:
        return self.data_dir / "app.sqlite"

    def tenant_data(self, tid: str) -> Path:
        d = self.data_dir / "tenants" / tid
        d.mkdir(parents=True, exist_ok=True)
        return d

    def tenant_out(self, tid: str) -> Path:
        d = self.out_dir / "tenants" / tid
        d.mkdir(parents=True, exist_ok=True)
        return d


def load_app(env_file: Path | None = None) -> AppConfig:
    dotenv = _load_dotenv(env_file or ROOT / ".env")

    def g(key: str, default=None):
        return os.environ.get(key, dotenv.get(key, default))

    data_dir = Path(g("DATA_DIR", str(ROOT / "data")))
    out_dir = Path(g("OUT_DIR", str(ROOT / "out")))
    cache_dir = Path(g("CACHE_DIR", str(ROOT / "cache")))
    for d in (data_dir, out_dir, cache_dir):
        d.mkdir(parents=True, exist_ok=True)
    hosts = {h.strip() for h in
             str(g("APP_HOSTS", "localhost,127.0.0.1")).split(",") if h.strip()}
    return AppConfig(data_dir=data_dir, out_dir=out_dir, cache_dir=cache_dir,
                     user_agent=g("USER_AGENT", "flightframe/0.1"),
                     app_hosts=frozenset(hosts),
                     schedule_api_key=str(g("SCHEDULE_API_KEY", "") or ""),
                     schedule_provider=str(g("SCHEDULE_PROVIDER",
                                             "aviationstack") or
                                           "aviationstack").strip().lower())


def for_tenant(app: AppConfig, tenant: dict) -> Settings:
    """A tenant row from the registry, shaped as the frozen Settings the
    entire render stack already consumes — so the renderers never learn
    that tenants exist."""
    return Settings(
        lat=float(tenant["lat"]),
        lon=float(tenant["lon"]),
        label=tenant["label"],
        radius_nm=float(tenant["radius_nm"]),
        refresh_minutes=int(tenant["refresh_minutes"]),
        awake_from=_hhmm(tenant["awake_from"], time(7, 0)),
        awake_until=_hhmm(tenant["awake_until"], time(23, 0)),
        trace_hours=int(tenant["trace_hours"]),
        units_name=tenant["units"],
        min_altitude_ft=float(tenant["min_altitude_ft"]),
        section_radius_km=float(tenant["section_radius_km"]),
        user_agent=app.user_agent,
        data_dir=app.tenant_data(tenant["id"]),
        out_dir=app.tenant_out(tenant["id"]),
        cache_dir=app.cache_dir,
    )
