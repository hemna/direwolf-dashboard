"""Tests for SQLite storage layer."""

import time
import pytest

from direwolf_dashboard.storage import Storage


@pytest.fixture
async def storage(tmp_path):
    """Create a temporary storage instance."""
    db_path = str(tmp_path / "test.db")
    s = Storage(db_path)
    await s.init()
    yield s
    await s.close()


def _make_packet(**overrides) -> dict:
    """Create a test packet dict with sensible defaults."""
    packet = {
        "timestamp": time.time(),
        "type": "GPSPacket",
        "tx": False,
        "from_call": "WB4BOR",
        "to_call": "APRS",
        "path": ["WIDE1-1", "WIDE2-1"],
        "msg_no": "123",
        "latitude": 37.75,
        "longitude": -77.45,
        "symbol": ">",
        "symbol_table": "/",
        "human_info": "38.50mph 287°",
        "comment": "Kenwood TM-D710",
        "audio_level": 42,
        "raw_log": ["[0 L>R] WB4BOR>APRS", "audio level = 42(16/11)"],
        "compact_log": "<span>RX↓ GPSPacket</span>",
        "raw_packet": "WB4BOR>APRS,WIDE1-1,WIDE2-1:!3745.00N/07730.00W>",
    }
    packet.update(overrides)
    return packet


class TestStorageInit:
    """Test database initialization."""

    async def test_init_creates_tables(self, storage):
        cursor = await storage._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        rows = await cursor.fetchall()
        table_names = [row["name"] for row in rows]
        assert "packets" in table_names
        assert "stations" in table_names
        assert "config" in table_names

    async def test_wal_mode_enabled(self, storage):
        cursor = await storage._db.execute("PRAGMA journal_mode")
        row = await cursor.fetchone()
        assert row[0] == "wal"

    async def test_indexes_created(self, storage):
        cursor = await storage._db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
        )
        rows = await cursor.fetchall()
        index_names = [row["name"] for row in rows]
        assert "idx_packets_timestamp" in index_names
        assert "idx_packets_from_call" in index_names
        assert "idx_packets_type" in index_names


class TestPacketCRUD:
    """Test packet insert and query operations."""

    async def test_insert_packet(self, storage):
        packet = _make_packet()
        row_id = await storage.insert_packet(packet)
        assert row_id is not None
        assert row_id > 0

    async def test_insert_and_retrieve(self, storage):
        packet = _make_packet(from_call="N3LLO")
        await storage.insert_packet(packet)

        results = await storage.query_packets(limit=10)
        assert len(results) == 1
        assert results[0]["from_call"] == "N3LLO"
        assert results[0]["type"] == "GPSPacket"
        assert results[0]["latitude"] == 37.75

    async def test_path_serialization(self, storage):
        packet = _make_packet(path=["WIDE1-1", "WIDE2-1"])
        await storage.insert_packet(packet)

        results = await storage.query_packets(limit=10)
        assert results[0]["path"] == ["WIDE1-1", "WIDE2-1"]

    async def test_query_packets_by_time(self, storage):
        old_time = time.time() - 3600
        new_time = time.time()

        await storage.insert_packet(_make_packet(timestamp=old_time, from_call="OLD"))
        await storage.insert_packet(_make_packet(timestamp=new_time, from_call="NEW"))

        cutoff = time.time() - 1800  # 30 minutes ago
        results = await storage.query_packets(since=cutoff)
        assert len(results) == 1
        assert results[0]["from_call"] == "NEW"

    async def test_query_packets_by_callsign(self, storage):
        await storage.insert_packet(_make_packet(from_call="WB4BOR"))
        await storage.insert_packet(_make_packet(from_call="N3LLO"))
        await storage.insert_packet(_make_packet(from_call="WB4BOR"))

        results = await storage.query_packets(callsign="WB4BOR")
        assert len(results) == 2
        assert all(r["from_call"] == "WB4BOR" for r in results)

    async def test_query_packets_by_type(self, storage):
        await storage.insert_packet(_make_packet(type="GPSPacket"))
        await storage.insert_packet(_make_packet(type="MessagePacket"))
        await storage.insert_packet(_make_packet(type="GPSPacket"))

        results = await storage.query_packets(packet_type="GPSPacket")
        assert len(results) == 2
        assert all(r["type"] == "GPSPacket" for r in results)

    async def test_query_packets_limit(self, storage):
        for i in range(10):
            await storage.insert_packet(_make_packet(from_call=f"CALL{i}"))

        results = await storage.query_packets(limit=5)
        assert len(results) == 5

    async def test_query_packets_ordered_newest_first(self, storage):
        for i in range(3):
            await storage.insert_packet(
                _make_packet(timestamp=1000.0 + i, from_call=f"CALL{i}")
            )

        results = await storage.query_packets(limit=10)
        assert results[0]["timestamp"] > results[-1]["timestamp"]

    async def test_query_empty_db(self, storage):
        results = await storage.query_packets()
        assert results == []


class TestStations:
    """Test station upsert and query operations."""

    async def test_upsert_station_insert(self, storage):
        await storage.upsert_station(
            "WB4BOR", time.time(), 37.75, -77.45, ">", "/", "Test"
        )
        stations = await storage.get_stations()
        assert len(stations) == 1
        assert stations[0]["callsign"] == "WB4BOR"
        assert stations[0]["packet_count"] == 1

    async def test_upsert_station_increments_count(self, storage):
        t = time.time()
        await storage.upsert_station("WB4BOR", t, 37.75, -77.45)
        await storage.upsert_station("WB4BOR", t + 1, 37.76, -77.46)
        await storage.upsert_station("WB4BOR", t + 2, 37.77, -77.47)

        stations = await storage.get_stations()
        assert len(stations) == 1
        assert stations[0]["packet_count"] == 3
        # Should have latest position
        assert stations[0]["latitude"] == 37.77

    async def test_get_all_stations(self, storage):
        t = time.time()
        await storage.upsert_station("WB4BOR", t, 37.75, -77.45)
        await storage.upsert_station("N3LLO", t, 38.00, -78.00)
        await storage.upsert_station("KI4ABC", t, 36.50, -76.50)

        stations = await storage.get_stations()
        assert len(stations) == 3
        callsigns = {s["callsign"] for s in stations}
        assert callsigns == {"WB4BOR", "N3LLO", "KI4ABC"}

    async def test_get_station_track(self, storage):
        t = time.time()
        for i in range(5):
            await storage.insert_packet(
                _make_packet(
                    from_call="WB4BOR",
                    timestamp=t + i,
                    latitude=37.75 + i * 0.01,
                    longitude=-77.45 + i * 0.01,
                )
            )
        # Insert some packets for a different station
        await storage.insert_packet(
            _make_packet(from_call="N3LLO", timestamp=t, latitude=38.0, longitude=-78.0)
        )

        track = await storage.get_station_track("WB4BOR")
        assert len(track) == 5
        assert all(t.get("latitude") is not None for t in track)
        # Should be newest first
        assert track[0]["latitude"] > track[-1]["latitude"]

    async def test_get_station_track_with_since(self, storage):
        t = time.time()
        for i in range(5):
            await storage.insert_packet(
                _make_packet(
                    from_call="WB4BOR",
                    timestamp=t - 3600 + i * 100,  # spread over last hour
                    latitude=37.75 + i * 0.01,
                    longitude=-77.45 + i * 0.01,
                )
            )
        # Only get points from last 300 seconds worth
        track = await storage.get_station_track("WB4BOR", since=t - 3600 + 200)
        assert len(track) == 3  # points at i=2,3,4

    async def test_get_all_station_tracks(self, storage):
        t = time.time()
        for i in range(3):
            await storage.insert_packet(
                _make_packet(
                    from_call="WB4BOR",
                    timestamp=t - 1800 + i * 100,
                    latitude=37.75 + i * 0.01,
                    longitude=-77.45,
                )
            )
            await storage.insert_packet(
                _make_packet(
                    from_call="N3LLO",
                    timestamp=t - 1800 + i * 100,
                    latitude=38.0 + i * 0.01,
                    longitude=-78.0,
                )
            )
        tracks = await storage.get_all_station_tracks(since=t - 3600)
        assert "WB4BOR" in tracks
        assert "N3LLO" in tracks
        assert len(tracks["WB4BOR"]) == 3
        assert len(tracks["N3LLO"]) == 3
        # Should be oldest first
        assert tracks["WB4BOR"][0]["timestamp"] < tracks["WB4BOR"][-1]["timestamp"]

    async def test_get_all_station_positions(self, storage):
        await storage.upsert_station("N3ABC", 100.0, latitude=40.0, longitude=-75.0)
        await storage.upsert_station("N3DEF", 100.0, latitude=39.0, longitude=-76.0)
        await storage.upsert_station("N3GHI", 100.0)  # no position
        positions = await storage.get_all_station_positions()
        assert len(positions) == 2
        callsigns = {p["callsign"] for p in positions}
        assert callsigns == {"N3ABC", "N3DEF"}
        for p in positions:
            assert "latitude" in p
            assert "longitude" in p


class TestHousekeeping:
    """Test data retention and cleanup."""

    async def test_housekeeping_deletes_old(self, storage):
        old_time = time.time() - (8 * 86400)  # 8 days ago
        new_time = time.time()

        await storage.insert_packet(_make_packet(timestamp=old_time, from_call="OLD"))
        await storage.insert_packet(_make_packet(timestamp=new_time, from_call="NEW"))

        deleted = await storage.housekeep(retention_days=7)
        assert deleted == 1

        results = await storage.query_packets()
        assert len(results) == 1
        assert results[0]["from_call"] == "NEW"

    async def test_housekeeping_keeps_recent(self, storage):
        t = time.time()
        for i in range(5):
            await storage.insert_packet(_make_packet(timestamp=t - i * 3600))

        deleted = await storage.housekeep(retention_days=7)
        assert deleted == 0

        results = await storage.query_packets()
        assert len(results) == 5


class TestStats:
    """Test statistics retrieval."""

    async def test_stats_empty_db(self, storage):
        stats = await storage.get_stats()
        assert stats["packets_total"] == 0
        assert stats["stations_active"] == 0
        assert stats["packets_last_hour"] == 0

    async def test_stats_with_data(self, storage):
        t = time.time()
        for i in range(10):
            await storage.insert_packet(_make_packet(timestamp=t - i * 60))
        await storage.upsert_station("WB4BOR", t, 37.75, -77.45)
        await storage.upsert_station("N3LLO", t, 38.0, -78.0)

        stats = await storage.get_stats()
        assert stats["packets_total"] == 10
        assert stats["stations_active"] == 2
        assert stats["packets_last_hour"] == 10
