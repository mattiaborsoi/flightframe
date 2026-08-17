"""Position history.

The long-exposure trace is an accumulation, so it needs samples taken
continuously — including while the frame is asleep. The collector writes here
every minute regardless of when the panel next refreshes.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from .sources import Aircraft

SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
    hex         TEXT NOT NULL,
    ts          REAL NOT NULL,
    lat         REAL NOT NULL,
    lon         REAL NOT NULL,
    altitude_ft REAL,
    track_deg   REAL,
    speed_kt    REAL,
    callsign    TEXT,
    type        TEXT,
    PRIMARY KEY (hex, ts)
);
CREATE INDEX IF NOT EXISTS positions_ts ON positions (ts);
"""


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def record(self, aircraft: list[Aircraft]) -> int:
        rows = [
            (a.hex, a.seen_at, a.lat, a.lon, a.altitude_ft, a.track_deg,
             a.ground_speed_kt, a.callsign, a.type)
            for a in aircraft if a.hex
        ]
        with self.db:
            self.db.executemany(
                "INSERT OR REPLACE INTO positions "
                "(hex, ts, lat, lon, altitude_ft, track_deg, speed_kt, callsign, type) "
                "VALUES (?,?,?,?,?,?,?,?,?)", rows)
        return len(rows)

    def tracks(self, hours: float) -> dict[str, list[tuple[float, float, float, float]]]:
        """hex -> [(lat, lon, altitude_ft, ts), ...] ordered in time."""
        cutoff = time.time() - hours * 3600
        cur = self.db.execute(
            "SELECT hex, lat, lon, altitude_ft, ts FROM positions "
            "WHERE ts >= ? ORDER BY hex, ts", (cutoff,))
        out: dict[str, list[tuple[float, float, float, float]]] = {}
        for hexcode, lat, lon, alt, ts in cur:
            out.setdefault(hexcode, []).append((lat, lon, alt, ts))
        return out

    def track_meta(self, hours: float) -> dict[str, tuple[str | None, str | None]]:
        """hex -> (callsign, type), taking the most recent non-null of each.

        Needed to label the trace: the drawing knows where things went, but the
        destination only exists against a callsign.
        """
        cutoff = time.time() - hours * 3600
        cur = self.db.execute(
            "SELECT hex, callsign, type FROM positions "
            "WHERE ts >= ? ORDER BY ts", (cutoff,))
        out: dict[str, tuple[str | None, str | None]] = {}
        for hexcode, callsign, kind in cur:
            prev = out.get(hexcode, (None, None))
            out[hexcode] = (callsign or prev[0], kind or prev[1])
        return out

    def span(self, hours: float) -> tuple[float | None, float | None]:
        cutoff = time.time() - hours * 3600
        row = self.db.execute(
            "SELECT MIN(ts), MAX(ts) FROM positions WHERE ts >= ?", (cutoff,)).fetchone()
        return (row[0], row[1]) if row else (None, None)

    def count(self, hours: float) -> int:
        cutoff = time.time() - hours * 3600
        return self.db.execute(
            "SELECT COUNT(DISTINCT hex) FROM positions WHERE ts >= ?", (cutoff,)).fetchone()[0]

    def prune(self, keep_hours: float) -> int:
        cutoff = time.time() - keep_hours * 3600
        with self.db:
            cur = self.db.execute("DELETE FROM positions WHERE ts < ?", (cutoff,))
        return cur.rowcount
