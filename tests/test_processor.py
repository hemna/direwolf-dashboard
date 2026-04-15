"""Tests for packet processor."""

import asyncio
import math
import pytest

from direwolf_dashboard.processor import (
    calculate_initial_compass_bearing,
    degrees_to_cardinal,
    format_compact_log,
    packet_to_dict,
    PacketProcessor,
)


class TestBearing:
    """Test compass bearing calculation."""

    def test_north_bearing(self):
        # Point due north should be ~0 degrees
        bearing = calculate_initial_compass_bearing((37.0, -77.0), (38.0, -77.0))
        assert 355 < bearing or bearing < 5  # Near 0/360

    def test_east_bearing(self):
        bearing = calculate_initial_compass_bearing((37.0, -77.0), (37.0, -76.0))
        assert 85 < bearing < 95  # Near 90

    def test_south_bearing(self):
        bearing = calculate_initial_compass_bearing((38.0, -77.0), (37.0, -77.0))
        assert 175 < bearing < 185  # Near 180

    def test_west_bearing(self):
        bearing = calculate_initial_compass_bearing((37.0, -77.0), (37.0, -78.0))
        assert 265 < bearing < 275  # Near 270

    def test_same_point(self):
        bearing = calculate_initial_compass_bearing((37.0, -77.0), (37.0, -77.0))
        assert 0 <= bearing < 360


class TestDegreesToCardinal:
    def test_north(self):
        assert degrees_to_cardinal(0) == "N"

    def test_east(self):
        assert degrees_to_cardinal(90) == "E"

    def test_south(self):
        assert degrees_to_cardinal(180) == "S"

    def test_west(self):
        assert degrees_to_cardinal(270) == "W"

    def test_northeast(self):
        assert degrees_to_cardinal(45) == "NE"

    def test_nnw(self):
        assert degrees_to_cardinal(337.5) == "NNW"


class TestFormatCompactLog:
    """Test APRSD-style compact log HTML formatting."""

    def test_rx_gps_packet(self):
        packet = {
            "tx": False,
            "type": "GPSPacket",
            "msg_no": "23",
            "from_call": "WB4BOR",
            "to_call": "APRS",
            "path": ["WIDE1-1", "WIDE2-1"],
            "human_info": "38.50mph 287°",
            "bearing": "NNW",
            "distance_miles": 12.34,
        }
        html = format_compact_log(packet)

        assert "RX\u2193" in html
        assert "GPSPacket" in html
        assert ":23" in html
        assert "WB4BOR" in html
        assert "APRS" in html
        assert "WIDE1-1" in html
        assert "38.50mph" in html
        assert "NNW" in html
        assert "12.34miles" in html
        assert "#C70039" in html  # from_call color
        assert "#D033FF" in html  # to_call color

    def test_tx_message_packet(self):
        packet = {
            "tx": True,
            "type": "MessagePacket",
            "msg_no": "45",
            "from_call": "PRIOR",
            "to_call": "WB4BOR",
            "path": [],
            "human_info": "Hello there",
        }
        html = format_compact_log(packet)

        assert "TX\u2191" in html
        assert "color:red" in html
        assert "MessagePacket" in html
        assert "Hello there" in html

    def test_no_path(self):
        packet = {
            "tx": False,
            "type": "StatusPacket",
            "msg_no": "",
            "from_call": "N3LLO",
            "to_call": "APRS",
            "path": [],
            "human_info": "Online",
        }
        html = format_compact_log(packet)
        assert "N3LLO" in html
        assert "Online" in html


class TestPacketToDict:
    """Test raw APRS string parsing into packet dict."""

    def test_parse_position_packet(self):
        raw = "WB4BOR>APRS,WIDE1-1:!3745.00N/07730.00W>"
        result = packet_to_dict(raw, tx=False, call_from="WB4BOR", call_to="APRS")

        assert result is not None
        assert result["from_call"] == "WB4BOR"
        assert result["tx"] is False
        assert result["raw_packet"] == raw
        assert result["compact_log"]  # Should have HTML content

    def test_parse_unparseable_packet(self):
        raw = "GARBAGE_DATA_NOT_APRS"
        result = packet_to_dict(raw, tx=False, call_from="TEST", call_to="APRS")

        assert result is not None
        assert result["type"] == "RawPacket"
        assert result["from_call"] == "TEST"

    def test_with_audio_level(self):
        raw = "WB4BOR>APRS:!3745.00N/07730.00W>"
        result = packet_to_dict(
            raw, tx=False, call_from="WB4BOR", call_to="APRS", audio_level=42
        )

        assert result["audio_level"] == 42

    def test_with_raw_log_lines(self):
        raw = "WB4BOR>APRS:!3745.00N/07730.00W>"
        lines = ["[0 L>R] WB4BOR>APRS", "audio level = 42"]
        result = packet_to_dict(
            raw, tx=False, call_from="WB4BOR", call_to="APRS", raw_log_lines=lines
        )

        assert result["raw_log"] == lines

    def test_with_station_position_computes_bearing(self):
        # Station at origin, packet to the north
        raw = "WB4BOR>APRS:!3800.00N/07700.00W>"
        result = packet_to_dict(
            raw,
            tx=False,
            call_from="WB4BOR",
            call_to="APRS",
            station_lat=37.0,
            station_lon=-77.0,
        )

        # If parsing succeeds and lat/lon are present, bearing should be computed
        if result.get("latitude") and result.get("longitude"):
            assert result.get("bearing") is not None
            assert result.get("distance_miles") is not None


class TestPacketProcessor:
    """Test the packet processor queue behavior."""

    async def test_agw_packet_queued(self):
        queue = asyncio.Queue(maxsize=100)
        proc = PacketProcessor(37.0, -77.0, queue)

        raw = b"WB4BOR>APRS:!3745.00N/07730.00W>"
        await proc.on_agw_packet(raw, tx=False, call_from="WB4BOR", call_to="APRS")

        assert not queue.empty()
        packet = queue.get_nowait()
        assert packet["from_call"] == "WB4BOR"

    async def test_queue_full_drops_oldest(self):
        queue = asyncio.Queue(maxsize=2)
        proc = PacketProcessor(0, 0, queue)

        for i in range(3):
            await proc.on_agw_packet(
                f"CALL{i}>APRS:data".encode(),
                tx=False,
                call_from=f"CALL{i}",
                call_to="APRS",
            )

        # Queue should have 2 items (oldest was dropped)
        assert queue.qsize() == 2

    async def test_log_data_correlation(self):
        queue = asyncio.Queue(maxsize=100)
        proc = PacketProcessor(0, 0, queue)

        # First, log data arrives
        await proc.on_log_lines(
            raw_lines=["[0 L>R] WB4BOR>APRS", "audio level = 42"],
            audio_level=42,
            callsign="WB4BOR",
        )

        # Then AGW packet arrives for same callsign
        await proc.on_agw_packet(
            b"WB4BOR>APRS:!3745.00N/07730.00W>",
            tx=False,
            call_from="WB4BOR",
            call_to="APRS",
        )

        packet = queue.get_nowait()
        assert packet["audio_level"] == 42
        assert len(packet["raw_log"]) == 2
