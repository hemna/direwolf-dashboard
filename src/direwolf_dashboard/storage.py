"""SQLite storage layer for Direwolf Dashboard."""

import json
import logging
import os
import time
from typing import Optional

import aiosqlite

LOG = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS packets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   REAL NOT NULL,
    type        TEXT NOT NULL,
    tx          BOOLEAN NOT NULL,
    from_call   TEXT NOT NULL,
    to_call     TEXT NOT NULL,
    path        TEXT,
    msg_no      TEXT,
    latitude    REAL,
    longitude   REAL,
    symbol      TEXT,
    symbol_table TEXT,
    human_info  TEXT,
    comment     TEXT,
    audio_level INTEGER,
    compact_log TEXT,
    raw_packet  TEXT
);

CREATE INDEX IF NOT EXISTS idx_packets_timestamp ON packets(timestamp);
CREATE INDEX IF NOT EXISTS idx_packets_from_call ON packets(from_call);
CREATE INDEX IF NOT EXISTS idx_packets_type ON packets(type);

CREATE TABLE IF NOT EXISTS stations (
    callsign    TEXT PRIMARY KEY,
    last_seen   REAL NOT NULL,
    latitude    REAL,
    longitude   REAL,
    symbol      TEXT,
    symbol_table TEXT,
    last_comment TEXT,
    packet_count INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS config (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS weather_reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       REAL NOT NULL,
    callsign        TEXT NOT NULL,
    temperature     REAL,
    dewpoint        REAL,
    humidity        REAL,
    pressure        REAL,
    wind_direction  REAL,
    wind_speed      REAL,
    wind_gust       REAL,
    rain_1h         REAL,
    rain_24h        REAL,
    rain_since_midnight REAL,
    luminosity      REAL
);

CREATE INDEX IF NOT EXISTS idx_weather_callsign_ts ON weather_reports(callsign, timestamp);
"""


class Storage:
    """Async SQLite storage for packets and stations."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def init(self) -> None:
        """Initialize database: create tables, enable WAL mode."""
        parent = os.path.dirname(self.db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row

        # Enable WAL mode for concurrent reads during writes
        await self._db.execute("PRAGMA journal_mode=WAL")

        # Create tables and indexes
        await self._db.executescript(SCHEMA_SQL)
        await self._db.commit()

    async def close(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()
            self._db = None

    async def reset(self) -> None:
        """Delete all data from packets, stations, and config tables.

        Safe to call while the app is running — uses DELETE on the
        existing connection so there are no locking issues with WAL mode.
        The autoincrement counter is also reset so IDs start fresh.
        """
        LOG.info("Resetting database — deleting all data")
        await self._db.execute("DELETE FROM packets")
        await self._db.execute("DELETE FROM stations")
        await self._db.execute("DELETE FROM config")
        await self._db.execute("DELETE FROM weather_reports")
        # Reset autoincrement counters
        await self._db.execute(
            "DELETE FROM sqlite_sequence WHERE name IN ('packets', 'weather_reports')"
        )
        await self._db.commit()
        LOG.info("Database reset complete")

    async def insert_packet(self, packet: dict) -> int:
        """Insert a packet record. Returns the inserted row id."""
        # Serialize list fields to JSON strings
        path = json.dumps(packet.get("path")) if packet.get("path") else None

        cursor = await self._db.execute(
            """INSERT INTO packets
            (timestamp, type, tx, from_call, to_call, path, msg_no,
             latitude, longitude, symbol, symbol_table, human_info,
             comment, audio_level, compact_log, raw_packet)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                packet.get("timestamp", time.time()),
                packet.get("type", "Unknown"),
                packet.get("tx", False),
                packet.get("from_call", ""),
                packet.get("to_call", ""),
                path,
                packet.get("msg_no"),
                packet.get("latitude"),
                packet.get("longitude"),
                packet.get("symbol"),
                packet.get("symbol_table"),
                packet.get("human_info"),
                packet.get("comment"),
                packet.get("audio_level"),
                packet.get("compact_log"),
                packet.get("raw_packet"),
            ),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def query_packets(
        self,
        since: Optional[float] = None,
        limit: int = 100,
        callsign: Optional[str] = None,
        packet_type: Optional[str] = None,
        tx_only: Optional[bool] = None,
    ) -> list[dict]:
        """Query packets with optional filters."""
        conditions = []
        params = []

        if since is not None:
            conditions.append("timestamp >= ?")
            params.append(since)
        if callsign is not None:
            conditions.append("from_call = ?")
            params.append(callsign)
        if packet_type is not None:
            conditions.append("type = ?")
            params.append(packet_type)
        if tx_only is not None:
            conditions.append("tx = ?")
            params.append(tx_only)

        where = " AND ".join(conditions)
        if where:
            where = f"WHERE {where}"

        query = f"""
            SELECT * FROM packets
            {where}
            ORDER BY timestamp DESC
            LIMIT ?
        """
        params.append(limit)

        cursor = await self._db.execute(query, params)
        rows = await cursor.fetchall()
        return [self._row_to_packet_dict(row) for row in rows]

    async def upsert_station(
        self,
        callsign: str,
        last_seen: float,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        symbol: Optional[str] = None,
        symbol_table: Optional[str] = None,
        comment: Optional[str] = None,
    ) -> None:
        """Insert or update a station record, incrementing packet_count."""
        await self._db.execute(
            """INSERT INTO stations
            (callsign, last_seen, latitude, longitude, symbol, symbol_table,
             last_comment, packet_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(callsign) DO UPDATE SET
                last_seen = excluded.last_seen,
                latitude = COALESCE(excluded.latitude, stations.latitude),
                longitude = COALESCE(excluded.longitude, stations.longitude),
                symbol = COALESCE(excluded.symbol, stations.symbol),
                symbol_table = COALESCE(excluded.symbol_table, stations.symbol_table),
                last_comment = COALESCE(excluded.last_comment, stations.last_comment),
                packet_count = stations.packet_count + 1
            """,
            (callsign, last_seen, latitude, longitude, symbol, symbol_table, comment),
        )
        await self._db.commit()

    async def get_stations(self) -> list[dict]:
        """Return all known stations."""
        cursor = await self._db.execute(
            "SELECT * FROM stations ORDER BY last_seen DESC"
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_station(self, callsign: str) -> Optional[dict]:
        """Return a single station record by callsign, or None."""
        cursor = await self._db.execute(
            "SELECT * FROM stations WHERE callsign = ?", (callsign,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_all_station_positions(self) -> list[dict]:
        """Return callsign + lat/lon for all stations with known positions."""
        cursor = await self._db.execute(
            "SELECT callsign, latitude, longitude FROM stations "
            "WHERE latitude IS NOT NULL AND longitude IS NOT NULL"
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_station_track(
        self,
        callsign: str,
        limit: int = 100,
        since: Optional[float] = None,
    ) -> list[dict]:
        """Return position history for a station.

        Args:
            callsign: Station callsign to query.
            limit: Maximum number of points to return.
            since: If provided, only return points with timestamp >= since.
        """
        if since is not None:
            cursor = await self._db.execute(
                """SELECT timestamp, latitude, longitude
                FROM packets
                WHERE from_call = ? AND latitude IS NOT NULL
                    AND longitude IS NOT NULL AND timestamp >= ?
                ORDER BY timestamp DESC
                LIMIT ?""",
                (callsign, since, limit),
            )
        else:
            cursor = await self._db.execute(
                """SELECT timestamp, latitude, longitude
                FROM packets
                WHERE from_call = ? AND latitude IS NOT NULL
                    AND longitude IS NOT NULL
                ORDER BY timestamp DESC
                LIMIT ?""",
                (callsign, limit),
            )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_all_station_tracks(
        self, since: float, limit_per_station: int = 500
    ) -> dict[str, list[dict]]:
        """Return position tracks for all stations since a given time.

        Returns:
            Dict of callsign -> list of {timestamp, latitude, longitude} dicts,
            ordered oldest-first per station.
        """
        cursor = await self._db.execute(
            """SELECT from_call, timestamp, latitude, longitude
            FROM packets
            WHERE latitude IS NOT NULL AND longitude IS NOT NULL
                AND timestamp >= ?
            ORDER BY from_call, timestamp ASC""",
            (since,),
        )
        rows = await cursor.fetchall()
        tracks: dict[str, list[dict]] = {}
        for row in rows:
            r = dict(row)
            cs = r.pop("from_call")
            if cs not in tracks:
                tracks[cs] = []
            if len(tracks[cs]) < limit_per_station:
                tracks[cs].append(r)
        return tracks

    async def get_stations_by_callsigns(
        self, callsigns: list[str]
    ) -> dict[str, dict]:
        """Return station info for a list of callsigns (with known positions).

        Returns dict of callsign -> {latitude, longitude, symbol, symbol_table}.
        """
        if not callsigns:
            return {}

        placeholders = ",".join("?" for _ in callsigns)
        cursor = await self._db.execute(
            f"""SELECT callsign, latitude, longitude, symbol, symbol_table
            FROM stations
            WHERE callsign IN ({placeholders})
              AND latitude IS NOT NULL
              AND longitude IS NOT NULL""",
            callsigns,
        )
        rows = await cursor.fetchall()
        return {
            row["callsign"]: {
                "latitude": row["latitude"],
                "longitude": row["longitude"],
                "symbol": row["symbol"] or ">",
                "symbol_table": row["symbol_table"] or "/",
            }
            for row in rows
        }

    # ---- Weather reports ----

    async def insert_weather_report(self, report: dict) -> int:
        """Insert a parsed weather report. Returns the inserted row id."""
        cursor = await self._db.execute(
            """INSERT INTO weather_reports
            (timestamp, callsign, temperature, dewpoint, humidity, pressure,
             wind_direction, wind_speed, wind_gust, rain_1h, rain_24h,
             rain_since_midnight, luminosity)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                report.get("timestamp", time.time()),
                report.get("callsign", ""),
                report.get("temperature"),
                report.get("dewpoint"),
                report.get("humidity"),
                report.get("pressure"),
                report.get("wind_direction"),
                report.get("wind_speed"),
                report.get("wind_gust"),
                report.get("rain_1h"),
                report.get("rain_24h"),
                report.get("rain_since_midnight"),
                report.get("luminosity"),
            ),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def get_weather_reports(
        self,
        callsign: str,
        since: Optional[float] = None,
        limit: int = 500,
    ) -> list[dict]:
        """Return weather reports for a station, ordered oldest-first for charting."""
        if since is not None:
            cursor = await self._db.execute(
                """SELECT * FROM weather_reports
                WHERE callsign = ? AND timestamp >= ?
                ORDER BY timestamp ASC
                LIMIT ?""",
                (callsign, since, limit),
            )
        else:
            cursor = await self._db.execute(
                """SELECT * FROM weather_reports
                WHERE callsign = ?
                ORDER BY timestamp ASC
                LIMIT ?""",
                (callsign, limit),
            )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    # ---- my_position (stored in the config table) ----

    async def get_my_position(self) -> Optional[dict]:
        """Return the saved my_position dict, or None if not set.

        Stored as a JSON blob under key 'my_position' in the config table.
        Returns e.g. {"type": "station", "callsign": "WB4BOR"} or
        {"type": "pin", "latitude": 37.75, "longitude": -77.45}.
        """
        cursor = await self._db.execute(
            "SELECT value FROM config WHERE key = 'my_position'"
        )
        row = await cursor.fetchone()
        if row:
            try:
                return json.loads(row["value"])
            except (json.JSONDecodeError, TypeError):
                return None
        return None

    async def set_my_position(self, my_position: Optional[dict]) -> None:
        """Save (or clear) the my_position setting.

        Pass None to clear the saved position.
        """
        if my_position is None:
            await self._db.execute(
                "DELETE FROM config WHERE key = 'my_position'"
            )
        else:
            await self._db.execute(
                "INSERT INTO config (key, value) VALUES ('my_position', ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (json.dumps(my_position),),
            )
        await self._db.commit()

    async def get_stats(self) -> dict:
        """Return storage statistics."""
        cursor = await self._db.execute("SELECT COUNT(*) as cnt FROM packets")
        row = await cursor.fetchone()
        total_packets = row["cnt"] if row else 0

        cursor = await self._db.execute("SELECT COUNT(*) as cnt FROM stations")
        row = await cursor.fetchone()
        total_stations = row["cnt"] if row else 0

        # Packets in last hour
        one_hour_ago = time.time() - 3600
        cursor = await self._db.execute(
            "SELECT COUNT(*) as cnt FROM packets WHERE timestamp >= ?",
            (one_hour_ago,),
        )
        row = await cursor.fetchone()
        packets_last_hour = row["cnt"] if row else 0

        # Time range
        cursor = await self._db.execute(
            "SELECT MIN(timestamp) as oldest, MAX(timestamp) as newest FROM packets"
        )
        row = await cursor.fetchone()

        return {
            "packets_total": total_packets,
            "stations_active": total_stations,
            "packets_last_hour": packets_last_hour,
            "oldest_packet": row["oldest"] if row and row["oldest"] else None,
            "newest_packet": row["newest"] if row and row["newest"] else None,
        }

    async def housekeep(self, retention_days: int) -> int:
        """Delete packets older than retention_days. Returns count deleted."""
        cutoff = time.time() - (retention_days * 86400)
        cursor = await self._db.execute(
            "DELETE FROM packets WHERE timestamp < ?", (cutoff,)
        )
        await self._db.execute(
            "DELETE FROM weather_reports WHERE timestamp < ?", (cutoff,)
        )
        await self._db.commit()
        deleted = cursor.rowcount
        if deleted:
            LOG.info(
                f"Housekeeping: deleted {deleted} packets older than {retention_days} days"
            )
        return deleted

    def _row_to_packet_dict(self, row) -> dict:
        """Convert a database row to a packet dict, deserializing JSON fields."""
        d = dict(row)

        # Deserialize JSON-encoded fields
        if d.get("path"):
            try:
                d["path"] = json.loads(d["path"])
            except (json.JSONDecodeError, TypeError):
                d["path"] = []

        return d
