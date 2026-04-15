"""Tests for async log file tailer."""

import asyncio
import os
import pytest

from direwolf_dashboard.log_tailer import (
    extract_audio_level,
    extract_callsign,
    is_decoded_packet_line,
    is_tx_line,
    LogTailer,
)


class TestExtractAudioLevel:
    """Test audio level extraction from log lines."""

    def test_basic_audio_level(self):
        assert extract_audio_level("audio level = 42(16/11)") == 42

    def test_audio_level_with_spaces(self):
        assert extract_audio_level("audio level  =  87(30/20)") == 87

    def test_audio_level_in_context(self):
        line = "[0.3] WB4BOR audio level = 42(16/11)  [NONE]   __|"
        assert extract_audio_level(line) == 42

    def test_no_audio_level(self):
        assert extract_audio_level("some random log line") is None

    def test_audio_level_zero(self):
        assert extract_audio_level("audio level = 0(0/0)") == 0


class TestExtractCallsign:
    """Test callsign extraction from decoded packet lines."""

    def test_basic_callsign(self):
        line = "[0 L>R] WB4BOR>APRS,WIDE1-1:!3745.00N/07730.00W>"
        assert extract_callsign(line) == "WB4BOR"

    def test_callsign_with_ssid(self):
        line = "[0 L>R] WB4BOR-9>APRS:@position"
        assert extract_callsign(line) == "WB4BOR-9"

    def test_callsign_with_decimal_channel(self):
        line = "[0.3 L>R] N3LLO>APRS:=3800.00N/07800.00W-PHG2360"
        assert extract_callsign(line) == "N3LLO"

    def test_no_callsign(self):
        assert extract_callsign("audio level = 42") is None

    def test_tx_callsign(self):
        line = "[0 R>L] PRIOR>WB4BOR:Hello"
        assert extract_callsign(line) == "PRIOR"


class TestIsDecodedPacketLine:
    """Test detection of decoded packet lines."""

    def test_rx_packet_line(self):
        assert is_decoded_packet_line("[0 L>R] WB4BOR>APRS:data") is True

    def test_tx_packet_line(self):
        assert is_decoded_packet_line("[0 R>L] PRIOR>WB4BOR:Hello") is True

    def test_decimal_channel(self):
        assert is_decoded_packet_line("[0.3 L>R] N3LLO>APRS:data") is True

    def test_not_packet_line(self):
        assert is_decoded_packet_line("audio level = 42(16/11)") is False

    def test_empty_line(self):
        assert is_decoded_packet_line("") is False


class TestIsTxLine:
    """Test TX vs RX detection."""

    def test_rx_line(self):
        assert is_tx_line("[0 L>R] WB4BOR>APRS:data") is False

    def test_tx_line(self):
        assert is_tx_line("[0 R>L] PRIOR>WB4BOR:Hello") is True


class TestLogTailer:
    """Test the async log tailer."""

    async def test_tail_new_lines(self, tmp_path):
        """Verify tailer yields new lines written to the file."""
        log_file = tmp_path / "direwolf.log"
        log_file.write_text("")  # Create empty file

        received = []

        async def callback(raw_lines, audio_level, callsign):
            received.append({
                "raw_lines": raw_lines,
                "audio_level": audio_level,
                "callsign": callsign,
            })

        tailer = LogTailer(str(log_file), callback, sleep_interval=0.05)
        task = asyncio.create_task(tailer.run())

        # Wait for tailer to start
        await asyncio.sleep(0.2)

        # Write a decoded packet line + audio level
        with open(str(log_file), "a") as f:
            f.write("[0 L>R] WB4BOR>APRS,WIDE1-1:!3745.00N/07730.00W>\n")
            f.write("audio level = 42(16/11)\n")

        # Give tailer time to process
        await asyncio.sleep(0.5)

        await tailer.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert len(received) >= 1
        assert received[0]["callsign"] == "WB4BOR"
        assert received[0]["audio_level"] == 42

    async def test_tail_starts_at_end(self, tmp_path):
        """Tailer should only yield new lines, not existing content."""
        log_file = tmp_path / "direwolf.log"
        # Write existing content
        log_file.write_text("[0 L>R] OLD>APRS:old data\naudio level = 10\n")

        received = []

        async def callback(raw_lines, audio_level, callsign):
            received.append({"callsign": callsign})

        tailer = LogTailer(str(log_file), callback, sleep_interval=0.05)
        task = asyncio.create_task(tailer.run())
        await asyncio.sleep(0.3)

        # Write new content
        with open(str(log_file), "a") as f:
            f.write("[0 L>R] NEW>APRS:new data\n")

        await asyncio.sleep(0.5)

        await tailer.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # Should only have the NEW packet
        callsigns = [r["callsign"] for r in received]
        assert "NEW" in callsigns
        assert "OLD" not in callsigns

    async def test_file_not_found_retries(self, tmp_path):
        """Tailer waits when file doesn't exist, then picks up when created."""
        log_file = tmp_path / "direwolf.log"
        # Don't create the file yet

        received = []

        async def callback(raw_lines, audio_level, callsign):
            received.append({"callsign": callsign})

        tailer = LogTailer(str(log_file), callback, sleep_interval=0.05, max_backoff=0.5)
        task = asyncio.create_task(tailer.run())

        # Wait a bit — file doesn't exist yet
        await asyncio.sleep(0.5)

        # Now create the file empty, then append a packet
        log_file.write_text("")
        await asyncio.sleep(1.0)

        with open(str(log_file), "a") as f:
            f.write("[0 L>R] LATE>APRS:data\n")

        await asyncio.sleep(1.5)

        await tailer.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # Should eventually pick up the packet
        callsigns = [r["callsign"] for r in received]
        assert "LATE" in callsigns

    async def test_log_rotation_detection(self, tmp_path):
        """Tailer detects inode change and reopens file."""
        log_file = tmp_path / "direwolf.log"
        log_file.write_text("")

        received = []

        async def callback(raw_lines, audio_level, callsign):
            received.append({"callsign": callsign})

        tailer = LogTailer(str(log_file), callback, sleep_interval=0.05, max_backoff=0.5)
        task = asyncio.create_task(tailer.run())
        await asyncio.sleep(0.2)

        # Simulate log rotation: remove and recreate with new inode
        os.remove(str(log_file))
        await asyncio.sleep(0.3)

        # Create new file empty first, let tailer open it
        log_file.write_text("")
        await asyncio.sleep(1.0)

        # Then append content
        with open(str(log_file), "a") as f:
            f.write("[0 L>R] ROTATED>APRS:after rotation\n")

        await asyncio.sleep(1.5)

        await tailer.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        callsigns = [r["callsign"] for r in received]
        assert "ROTATED" in callsigns

    async def test_extract_audio_level_from_tail(self, tmp_path):
        """Verify audio levels are correctly extracted during tailing."""
        log_file = tmp_path / "direwolf.log"
        log_file.write_text("")

        received = []

        async def callback(raw_lines, audio_level, callsign):
            received.append({"audio_level": audio_level, "callsign": callsign})

        tailer = LogTailer(str(log_file), callback, sleep_interval=0.05)
        task = asyncio.create_task(tailer.run())
        await asyncio.sleep(0.2)

        with open(str(log_file), "a") as f:
            f.write("[0 L>R] WB4BOR>APRS:data\n")
            f.write("audio level = 87(30/20)\n")
            # Write next packet to flush the previous one
            f.write("[0 L>R] N3LLO>APRS:data\n")

        await asyncio.sleep(0.5)

        await tailer.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # First packet should have audio level
        wb4bor = [r for r in received if r["callsign"] == "WB4BOR"]
        assert len(wb4bor) >= 1
        assert wb4bor[0]["audio_level"] == 87
