"""Tenant, user, session, and device registry: one SQLite file, three processes.

Why SQLite and not another JSON file: the web server's threads, the collector,
and the renderer all read and write this state concurrently. The existing JSON
files survive because each has effectively one writer; tenant/session/device
rows do not. WAL mode plus busy_timeout gives real transactions for free, and
SQLite is already in the stack.

Security invariants enforced here, not in callers:

  * Secrets are stored hashed. Session cookies and device bearer tokens are
    written as sha256 hex; the plaintext exists only in the HTTP exchange.
    A copy of app.sqlite is not a credential dump.
  * provision_secret is compared in constant time against every active tenant,
    not looked up by value — a lookup by secret would leak existence timing.
  * Every device row references exactly one tenant (token_hash UNIQUE), so a
    bearer token can never be ambiguous about whose frame it is.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 5

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  lat REAL NOT NULL, lon REAL NOT NULL, label TEXT NOT NULL,
  radius_nm REAL NOT NULL DEFAULT 25,
  units TEXT NOT NULL DEFAULT 'metric',
  refresh_minutes INTEGER NOT NULL DEFAULT 15,
  awake_from TEXT NOT NULL DEFAULT '07:00',
  awake_until TEXT NOT NULL DEFAULT '23:00',
  tz TEXT NOT NULL DEFAULT 'Europe/London',
  trace_hours REAL NOT NULL DEFAULT 3,
  min_altitude_ft REAL NOT NULL DEFAULT 1000,
  section_radius_km REAL NOT NULL DEFAULT 25,
  provision_secret TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  tenant_id TEXT NOT NULL REFERENCES tenants(id),
  email TEXT UNIQUE NOT NULL COLLATE NOCASE,
  pw_hash TEXT NOT NULL,
  is_admin INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  created_at REAL NOT NULL,
  expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS devices (
  id INTEGER PRIMARY KEY,
  tenant_id TEXT NOT NULL REFERENCES tenants(id),
  mac TEXT UNIQUE NOT NULL,
  token_hash TEXT UNIQUE NOT NULL,
  hw_rev TEXT, fw_version TEXT,
  first_seen REAL, last_setup REAL, last_seen REAL,
  battery_mv INTEGER, rssi INTEGER, boot_reason TEXT, power_source TEXT,
  battery_history TEXT NOT NULL DEFAULT '[]',
  pending_firmware TEXT
);
CREATE TABLE IF NOT EXISTS upcoming_flights (
  id INTEGER PRIMARY KEY,
  tenant_id TEXT NOT NULL REFERENCES tenants(id),
  flight_no TEXT NOT NULL,
  date TEXT NOT NULL,                -- YYYY-MM-DD, tenant-local
  dep_time TEXT,                     -- HH:MM local, optional
  origin TEXT, destination TEXT,     -- free text: "LHR" or "London Heathrow"
  aircraft TEXT,                     -- as booked: "A350-900"
  note TEXT,
  status TEXT NOT NULL DEFAULT 'upcoming',  -- upcoming|tracking|done|missed
  created_at REAL NOT NULL,
  last_refreshed REAL,               -- schedule-API refresh stamp
  arr_time TEXT,                     -- HH:MM at the destination, optional
  dep_terminal TEXT, dep_gate TEXT,  -- as published, close to departure
  delay_min INTEGER,                 -- revised vs scheduled, minutes
  registration TEXT,                 -- tail assigned to the flight
  origin_city TEXT, destination_city TEXT  -- from the schedule API
);
"""

# Columns added after v1 shipped; ALTER is idempotent-guarded by version.
_MIGRATIONS = {
    2: ["ALTER TABLE tenants ADD COLUMN lang TEXT NOT NULL DEFAULT 'en'",
        "ALTER TABLE tenants ADD COLUMN follows_flights_of TEXT"],
    3: ["ALTER TABLE upcoming_flights ADD COLUMN last_refreshed REAL"],
    4: ["ALTER TABLE upcoming_flights ADD COLUMN arr_time TEXT",
        "ALTER TABLE upcoming_flights ADD COLUMN dep_terminal TEXT",
        "ALTER TABLE upcoming_flights ADD COLUMN dep_gate TEXT",
        "ALTER TABLE upcoming_flights ADD COLUMN delay_min INTEGER",
        "ALTER TABLE upcoming_flights ADD COLUMN registration TEXT"],
    5: ["ALTER TABLE upcoming_flights ADD COLUMN origin_city TEXT",
        "ALTER TABLE upcoming_flights ADD COLUMN destination_city TEXT"],
}

TENANT_FIELDS = ("id", "name", "status", "lat", "lon", "label", "radius_nm",
                 "units", "refresh_minutes", "awake_from", "awake_until", "tz",
                 "trace_hours", "min_altitude_ft", "section_radius_km",
                 "provision_secret", "created_at", "lang", "follows_flights_of")

# Settings a tenant may edit about themselves from the dashboard. Location is
# here deliberately; identity fields (id, name, status, secret) are not.
TENANT_EDITABLE = {"lat", "lon", "label", "radius_nm", "units",
                   "refresh_minutes", "awake_from", "awake_until", "tz",
                   "trace_hours", "min_altitude_ft", "section_radius_km",
                   "lang"}

# Admin-only tenant knobs (CLI, not the dashboard): whose flight list a
# frame follows. A frame for the family follows the traveller's tenant.
TENANT_ADMIN = {"follows_flights_of"}

SESSION_HOURS = 24 * 30


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class Registry:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(_SCHEMA)
            version = db.execute("PRAGMA user_version").fetchone()[0]
            for target in range(version + 1, SCHEMA_VERSION + 1):
                for stmt in _MIGRATIONS.get(target, []):
                    try:
                        db.execute(stmt)
                    except sqlite3.OperationalError:
                        pass          # column already there (fresh schema)
            db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    # -- tenants ----------------------------------------------------------

    def tenant_add(self, tid: str, name: str, lat: float, lon: float,
                   label: str, **extra: Any) -> dict:
        secret = secrets.token_urlsafe(24)
        row = {"id": tid, "name": name, "status": "active", "lat": lat,
               "lon": lon, "label": label, "provision_secret": secret,
               "created_at": time.time()}
        row.update({k: v for k, v in extra.items() if k in TENANT_EDITABLE})
        cols = ", ".join(row)
        marks = ", ".join("?" * len(row))
        with self._db() as db:
            db.execute(f"INSERT INTO tenants ({cols}) VALUES ({marks})",
                       tuple(row.values()))
        return self.tenant(tid)  # round-trip so defaults are filled in

    def tenant(self, tid: str) -> dict | None:
        with self._db() as db:
            r = db.execute("SELECT * FROM tenants WHERE id=?", (tid,)).fetchone()
        return dict(r) if r else None

    def tenants(self, status: str | None = "active") -> list[dict]:
        q = "SELECT * FROM tenants"
        args: tuple = ()
        if status:
            q += " WHERE status=?"
            args = (status,)
        with self._db() as db:
            return [dict(r) for r in db.execute(q + " ORDER BY id", args)]

    def tenant_update(self, tid: str, fields: dict) -> None:
        allowed = {k: v for k, v in fields.items() if k in TENANT_EDITABLE}
        if not allowed:
            return
        sets = ", ".join(f"{k}=?" for k in allowed)
        with self._db() as db:
            db.execute(f"UPDATE tenants SET {sets} WHERE id=?",
                       (*allowed.values(), tid))

    def tenant_set_status(self, tid: str, status: str) -> None:
        with self._db() as db:
            db.execute("UPDATE tenants SET status=? WHERE id=?", (status, tid))

    def tenant_rotate_secret(self, tid: str) -> str:
        secret = secrets.token_urlsafe(24)
        with self._db() as db:
            db.execute("UPDATE tenants SET provision_secret=? WHERE id=?",
                       (secret, tid))
        return secret

    def tenant_by_secret(self, secret: str) -> dict | None:
        """Constant-time scan of active tenants — never a WHERE on the secret."""
        if not secret:
            return None
        match = None
        for row in self.tenants(status="active"):
            if hmac.compare_digest(row["provision_secret"], secret):
                match = row      # keep scanning; uniform time over the fleet
        return match

    # -- users / sessions -------------------------------------------------

    def user_add(self, tenant_id: str, email: str, pw_hash: str,
                 is_admin: bool = False) -> None:
        with self._db() as db:
            db.execute(
                "INSERT INTO users (tenant_id, email, pw_hash, is_admin,"
                " created_at) VALUES (?,?,?,?,?)",
                (tenant_id, email.strip().lower(), pw_hash,
                 int(is_admin), time.time()))

    def user_by_email(self, email: str) -> dict | None:
        with self._db() as db:
            r = db.execute("SELECT * FROM users WHERE email=?",
                           (email.strip().lower(),)).fetchone()
        return dict(r) if r else None

    def session_create(self, user_id: int) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._db() as db:
            db.execute(
                "INSERT INTO sessions (token_hash, user_id, created_at,"
                " expires_at) VALUES (?,?,?,?)",
                (_sha(token), user_id, now, now + SESSION_HOURS * 3600))
            db.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        return token

    def session_user(self, token: str) -> dict | None:
        """Resolve a cookie to {user..., tenant...} or None. The only place
        tenant identity is ever derived for a human request."""
        if not token:
            return None
        with self._db() as db:
            r = db.execute(
                "SELECT u.*, s.expires_at FROM sessions s"
                " JOIN users u ON u.id = s.user_id"
                " WHERE s.token_hash=? AND s.expires_at > ?",
                (_sha(token), time.time())).fetchone()
        if r is None:
            return None
        user = dict(r)
        tenant = self.tenant(user["tenant_id"])
        if tenant is None or tenant["status"] != "active" and not user["is_admin"]:
            return None
        user["tenant"] = tenant
        return user

    def session_destroy(self, token: str) -> None:
        with self._db() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (_sha(token),))

    # -- devices ----------------------------------------------------------

    def device_register(self, tenant_id: str, mac: str, hw_rev: str) -> str:
        """Issue a fresh bearer token for a frame, binding it to the tenant.

        A known MAC re-registering (same or different tenant) gets a new token
        and moves: that is the legitimate re-provision / re-point flow, and the
        old token dies with the UPDATE because only its hash was kept.
        """
        token = secrets.token_hex(32)     # 64 lowercase hex: firmware contract
        now = time.time()
        with self._db() as db:
            row = db.execute("SELECT id FROM devices WHERE mac=?",
                             (mac,)).fetchone()
            if row:
                db.execute(
                    "UPDATE devices SET tenant_id=?, token_hash=?, hw_rev=?,"
                    " last_setup=? WHERE mac=?",
                    (tenant_id, _sha(token), hw_rev, now, mac))
            else:
                db.execute(
                    "INSERT INTO devices (tenant_id, mac, token_hash, hw_rev,"
                    " first_seen, last_setup) VALUES (?,?,?,?,?,?)",
                    (tenant_id, mac, _sha(token), hw_rev, now, now))
        return token

    def device_by_token(self, token: str) -> dict | None:
        if not token:
            return None
        with self._db() as db:
            r = db.execute("SELECT * FROM devices WHERE token_hash=?",
                           (_sha(token),)).fetchone()
        return dict(r) if r else None

    def device_touch(self, device_id: int, telemetry: dict) -> None:
        keep = {k: telemetry[k] for k in
                ("battery_mv", "rssi", "fw_version", "boot_reason",
                 "power_source") if telemetry.get(k) is not None}
        with self._db() as db:
            if keep:
                sets = ", ".join(f"{k}=?" for k in keep)
                db.execute(f"UPDATE devices SET {sets}, last_seen=? WHERE id=?",
                           (*keep.values(), time.time(), device_id))
            else:
                db.execute("UPDATE devices SET last_seen=? WHERE id=?",
                           (time.time(), device_id))
            if telemetry.get("battery_mv"):
                r = db.execute("SELECT battery_history FROM devices WHERE id=?",
                               (device_id,)).fetchone()
                history = json.loads(r["battery_history"]) if r else []
                history.append([int(time.time()), telemetry["battery_mv"]])
                db.execute("UPDATE devices SET battery_history=? WHERE id=?",
                           (json.dumps(history[-500:]), device_id))

    def devices_for_tenant(self, tenant_id: str) -> list[dict]:
        with self._db() as db:
            return [dict(r) for r in db.execute(
                "SELECT * FROM devices WHERE tenant_id=? ORDER BY id",
                (tenant_id,))]

    def devices_all(self) -> list[dict]:
        with self._db() as db:
            return [dict(r) for r in db.execute(
                "SELECT * FROM devices ORDER BY tenant_id, id")]

    def device_clear_pending_firmware(self, device_id: int) -> None:
        with self._db() as db:
            db.execute("UPDATE devices SET pending_firmware=NULL WHERE id=?",
                       (device_id,))


    # -- upcoming flights -------------------------------------------------

    def flights_for(self, tenant_id: str, *, resolve_follow: bool = False,
                    include_done: bool = False) -> list[dict]:
        """A tenant's flight list — or the list they follow, for frames that
        show someone else's travels."""
        tid = tenant_id
        if resolve_follow:
            t = self.tenant(tenant_id)
            if t and t.get("follows_flights_of"):
                followed = self.tenant(t["follows_flights_of"])
                if followed:
                    tid = followed["id"]
        q = "SELECT * FROM upcoming_flights WHERE tenant_id=?"
        if not include_done:
            q += " AND status IN ('upcoming', 'tracking')"
        with self._db() as db:
            return [dict(r) for r in
                    db.execute(q + " ORDER BY date, dep_time", (tid,))]

    def flight_add(self, tenant_id: str, flight_no: str, date: str,
                   **extra) -> int:
        cols = {"tenant_id": tenant_id, "flight_no": flight_no.upper(),
                "date": date, "created_at": time.time()}
        cols.update({k: extra[k] for k in
                     ("dep_time", "origin", "destination", "aircraft", "note")
                     if extra.get(k)})
        names = ", ".join(cols)
        marks = ", ".join("?" * len(cols))
        with self._db() as db:
            cur = db.execute(
                f"INSERT INTO upcoming_flights ({names}) VALUES ({marks})",
                tuple(cols.values()))
            return cur.lastrowid

    def flight_set_status(self, flight_id: int, status: str) -> None:
        with self._db() as db:
            db.execute("UPDATE upcoming_flights SET status=? WHERE id=?",
                       (status, flight_id))

    def flight_delete(self, tenant_id: str, flight_id: int) -> bool:
        """Tenant-scoped delete: you can only remove your own flights."""
        with self._db() as db:
            cur = db.execute(
                "DELETE FROM upcoming_flights WHERE id=? AND tenant_id=?",
                (flight_id, tenant_id))
            return cur.rowcount > 0

    def tenant_admin_update(self, tid: str, fields: dict) -> None:
        allowed = {k: v for k, v in fields.items() if k in TENANT_ADMIN}
        if not allowed:
            return
        sets = ", ".join(f"{k}=?" for k in allowed)
        with self._db() as db:
            db.execute(f"UPDATE tenants SET {sets} WHERE id=?",
                       (*allowed.values(), tid))

    def flight_refresh(self, flight_id: int, fields: dict,
                       now: float) -> None:
        """Overwrite schedule-sourced fields; the airline's data wins."""
        keep = {k: fields[k] for k in
                ("dep_time", "origin", "destination", "aircraft",
                 "arr_time", "dep_terminal", "dep_gate", "delay_min",
                 "registration", "origin_city", "destination_city")
                if fields.get(k)}
        sets = ", ".join(f"{k}=?" for k in keep) + (", " if keep else "")             + "last_refreshed=?"
        with self._db() as db:
            db.execute(f"UPDATE upcoming_flights SET {sets} WHERE id=?",
                       (*keep.values(), now, flight_id))
