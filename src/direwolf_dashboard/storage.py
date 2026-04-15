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
    raw_log     TEXT,
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
"""


class Storage:
    """Async SQLite storage for packets and stations."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def init(self) -> None:
        """Initialize database: create tables, enable WAL mode."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
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

    async def insert_packet(self, packet: dict) -> int:
        """Insert a packet record. Returns the inserted row id."""
        # Serialize list fields to JSON strings
        path = json.dumps(packet.get("path")) if packet.get("path") else None
        raw_log = json.dumps(packet.get("raw_log")) if packet.get("raw_log") else None

        cursor = await self._db.execute(
            """INSERT INTO packets
            (timestamp, type, tx, from_call, to_call, path, msg_no,
             latitude, longitude, symbol, symbol_table, human_info,
             comment, audio_level, raw_log, compact_log, raw_packet)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                raw_log,
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

    async def get_station_track(self, callsign: str, limit: int = 100) -> list[dict]:
        """Return position history for a station."""
        cursor = await self._db.execute(
            """SELECT timestamp, latitude, longitude
            FROM packets
            WHERE from_call = ? AND latitude IS NOT NULL AND longitude IS NOT NULL
            ORDER BY timestamp DESC
            LIMIT ?""",
            (callsign, limit),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

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

        if d.get("raw_log"):
            try:
                d["raw_log"] = json.loads(d["raw_log"])
            except (json.JSONDecodeError, TypeError):
                d["raw_log"] = []

        return d
