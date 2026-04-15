"""Tests for AGW socket reader."""

import asyncio
import struct
import pytest

from direwolf_dashboard.agw import (
    AGW_HEADER_SIZE,
    AGWHeader,
    parse_header,
    build_frame,
    AGWReader,
)


class TestParseHeader:
    """Test AGW header parsing."""

    def test_parse_valid_header(self):
        # Build a known header: port=0, kind='U', from='WB4BOR', to='APRS', data_len=50
        kind = b"U\x00\x00\x00"
        call_from = b"WB4BOR\x00\x00\x00\x00"
        call_to = b"APRS\x00\x00\x00\x00\x00\x00"
        header_bytes = struct.pack("<I", 0) + kind + call_from + call_to + struct.pack("<II", 50, 0)

        header = parse_header(header_bytes)

        assert header.port == 0
        assert header.data_kind == "U"
        assert header.call_from == "WB4BOR"
        assert header.call_to == "APRS"
        assert header.data_len == 50
        assert header.unused == 0

    def test_parse_tx_frame(self):
        kind = b"T\x00\x00\x00"
        call_from = b"PRIOR\x00\x00\x00\x00\x00"
        call_to = b"WB4BOR\x00\x00\x00\x00"
        header_bytes = struct.pack("<I", 0) + kind + call_from + call_to + struct.pack("<II", 30, 0)

        header = parse_header(header_bytes)

        assert header.data_kind == "T"
        assert header.call_from == "PRIOR"
        assert header.call_to == "WB4BOR"
        assert header.data_len == 30

    def test_parse_version_response(self):
        kind = b"R\x00\x00\x00"
        call_from = b"\x00" * 10
        call_to = b"\x00" * 10
        header_bytes = struct.pack("<I", 0) + kind + call_from + call_to + struct.pack("<II", 8, 0)

        header = parse_header(header_bytes)

        assert header.data_kind == "R"
        assert header.data_len == 8

    def test_parse_wrong_size_raises(self):
        with pytest.raises(ValueError, match="Expected 36 bytes"):
            parse_header(b"\x00" * 20)

    def test_parse_empty_callsigns(self):
        kind = b"U\x00\x00\x00"
        call_from = b"\x00" * 10
        call_to = b"\x00" * 10
        header_bytes = struct.pack("<I", 0) + kind + call_from + call_to + struct.pack("<II", 0, 0)

        header = parse_header(header_bytes)

        assert header.call_from == ""
        assert header.call_to == ""


class TestBuildFrame:
    """Test AGW frame construction."""

    def test_build_register_frame(self):
        frame = build_frame("R")
        assert len(frame) == AGW_HEADER_SIZE  # No data payload

        header = parse_header(frame[:AGW_HEADER_SIZE])
        assert header.data_kind == "R"
        assert header.data_len == 0

    def test_build_monitor_frame(self):
        frame = build_frame("m")
        assert len(frame) == AGW_HEADER_SIZE

        header = parse_header(frame[:AGW_HEADER_SIZE])
        assert header.data_kind == "m"
        assert header.data_len == 0

    def test_build_frame_with_data(self):
        payload = b"Hello APRS"
        frame = build_frame("D", call_from="WB4BOR", call_to="APRS", data=payload)

        assert len(frame) == AGW_HEADER_SIZE + len(payload)

        header = parse_header(frame[:AGW_HEADER_SIZE])
        assert header.data_kind == "D"
        assert header.call_from == "WB4BOR"
        assert header.call_to == "APRS"
        assert header.data_len == len(payload)
        assert frame[AGW_HEADER_SIZE:] == payload

    def test_build_frame_truncates_long_callsign(self):
        frame = build_frame("U", call_from="ABCDEFGHIJKLMNOP")
        header = parse_header(frame[:AGW_HEADER_SIZE])
        # Should be truncated to 10 chars
        assert len(header.call_from) <= 10

    def test_roundtrip_header(self):
        """Build a frame and parse the header back — should match."""
        frame = build_frame("U", call_from="WB4BOR", call_to="APRS", port=1)
        header = parse_header(frame[:AGW_HEADER_SIZE])

        assert header.port == 1
        assert header.data_kind == "U"
        assert header.call_from == "WB4BOR"
        assert header.call_to == "APRS"


class TestAGWReader:
    """Test AGW reader connection and dispatch logic."""

    async def test_reader_sends_registration_frames(self):
        """Verify the reader sends R and m frames on connect."""
        received_data = bytearray()

        async def mock_server(reader, writer):
            # Collect what the client sends
            while True:
                try:
                    data = await asyncio.wait_for(reader.read(1024), timeout=1.0)
                    if not data:
                        break
                    received_data.extend(data)
                except asyncio.TimeoutError:
                    break
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(mock_server, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]

        async def noop_callback(**kwargs):
            pass

        agw = AGWReader("127.0.0.1", port, noop_callback)

        # Run reader briefly, then stop
        task = asyncio.create_task(agw.run())
        await asyncio.sleep(0.5)
        await agw.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        server.close()
        await server.wait_closed()

        # Should have received at least 2 frames (R + m)
        assert len(received_data) >= AGW_HEADER_SIZE * 2

        # Parse first frame — should be 'R'
        first_header = parse_header(bytes(received_data[:AGW_HEADER_SIZE]))
        assert first_header.data_kind == "R"

        # Parse second frame — should be 'm'
        second_header = parse_header(
            bytes(received_data[AGW_HEADER_SIZE : AGW_HEADER_SIZE * 2])
        )
        assert second_header.data_kind == "m"

    async def test_reader_dispatches_rx_packet(self):
        """Verify the reader calls callback for U (RX) frames."""
        received_packets = []

        async def mock_server(reader, writer):
            # Read registration frames from client
            await asyncio.sleep(0.2)
            # Send a monitored (U) frame
            payload = b"WB4BOR>APRS:!3745.00N/07730.00W>"
            frame = build_frame("U", call_from="WB4BOR", call_to="APRS", data=payload)
            writer.write(frame)
            await writer.drain()
            # Keep connection open briefly
            await asyncio.sleep(0.5)
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(mock_server, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]

        async def callback(**kwargs):
            received_packets.append(kwargs)

        agw = AGWReader("127.0.0.1", port, callback)
        task = asyncio.create_task(agw.run())
        await asyncio.sleep(1.0)
        await agw.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        server.close()
        await server.wait_closed()

        assert len(received_packets) == 1
        assert received_packets[0]["tx"] is False
        assert received_packets[0]["call_from"] == "WB4BOR"
        assert received_packets[0]["call_to"] == "APRS"

    async def test_reader_dispatches_tx_packet(self):
        """Verify the reader calls callback for T (TX) frames."""
        received_packets = []

        async def mock_server(reader, writer):
            await asyncio.sleep(0.2)
            payload = b"PRIOR>WB4BOR:Hello"
            frame = build_frame("T", call_from="PRIOR", call_to="WB4BOR", data=payload)
            writer.write(frame)
            await writer.drain()
            await asyncio.sleep(0.5)
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(mock_server, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]

        async def callback(**kwargs):
            received_packets.append(kwargs)

        agw = AGWReader("127.0.0.1", port, callback)
        task = asyncio.create_task(agw.run())
        await asyncio.sleep(1.0)
        await agw.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        server.close()
        await server.wait_closed()

        assert len(received_packets) == 1
        assert received_packets[0]["tx"] is True

    async def test_reader_reconnects_on_disconnect(self):
        """Verify the reader attempts reconnection after disconnect."""
        connect_count = 0

        async def mock_server(reader, writer):
            nonlocal connect_count
            connect_count += 1
            # Close immediately to trigger reconnect
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(mock_server, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]

        async def noop_callback(**kwargs):
            pass

        agw = AGWReader("127.0.0.1", port, noop_callback, max_backoff=1.0)
        task = asyncio.create_task(agw.run())

        # Wait enough time for at least 2 connection attempts
        await asyncio.sleep(3.0)
        await agw.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        server.close()
        await server.wait_closed()

        assert connect_count >= 2
