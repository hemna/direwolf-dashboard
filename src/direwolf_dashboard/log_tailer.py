"""Async log file tailer for Direwolf log output."""

import asyncio
import logging
import os
import re
from typing import Callable, Optional

LOG = logging.getLogger(__name__)

# Regex patterns for Direwolf log line parsing
AUDIO_LEVEL_RE = re.compile(r"audio level\s*=\s*(\d+)")
# Decoded packet lines look like: [0 L>R] or [0.3 L>R] or [0 R>L]
DECODED_PACKET_RE = re.compile(r"\[[\d.]+\s+[LR]>[LR]\]")
# Callsign extraction from decoded lines like: [0 L>R] WB4BOR>APRS,WIDE1-1:...
CALLSIGN_RE = re.compile(r"\[[\d.]+\s+[LR]>[LR]\]\s+([A-Z0-9]{1,7}(?:-\d{1,2})?)>")
# TX indicator in direwolf log
TX_INDICATOR_RE = re.compile(r"\[[\d.]+\s+R>L\]")


def extract_audio_level(line: str) -> Optional[int]:
    """Extract audio level from a Direwolf log line.

    Example: 'audio level = 42(16/11)' -> 42
    """
    match = AUDIO_LEVEL_RE.search(line)
    if match:
        return int(match.group(1))
    return None


def extract_callsign(line: str) -> Optional[str]:
    """Extract the source callsign from a decoded packet log line.

    Example: '[0 L>R] WB4BOR>APRS,WIDE1-1:...' -> 'WB4BOR'
    """
    match = CALLSIGN_RE.search(line)
    if match:
        return match.group(1)
    return None


def is_decoded_packet_line(line: str) -> bool:
    """Check if a line is a decoded packet line."""
    return DECODED_PACKET_RE.search(line) is not None


def is_tx_line(line: str) -> bool:
    """Check if a decoded packet line is a TX (R>L = radio to line = transmit)."""
    return TX_INDICATOR_RE.search(line) is not None


class LogTailer:
    """Async tail-follow reader for the Direwolf log file.

    Reads new lines as they're written, extracts metadata (audio levels,
    callsigns, raw lines), and dispatches them via callback.
    """

    def __init__(
        self,
        log_path: str,
        line_callback: Callable,
        sleep_interval: float = 0.1,
        max_backoff: float = 60.0,
    ):
        """Initialize the log tailer.

        Args:
            log_path: Path to the Direwolf log file.
            line_callback: Async callable(raw_lines: list[str], audio_level: int|None, callsign: str|None)
            sleep_interval: Seconds to sleep between empty reads.
            max_backoff: Maximum backoff when file doesn't exist.
        """
        self.log_path = log_path
        self.line_callback = line_callback
        self.sleep_interval = sleep_interval
        self.max_backoff = max_backoff
        self._running = False
        self._active = False

    @property
    def active(self) -> bool:
        """Whether the tailer is actively reading a file."""
        return self._active

    async def run(self) -> None:
        """Main run loop. Waits for file, then tails it.

        Handles file-not-found with backoff and log rotation via inode monitoring.
        """
        self._running = True
        backoff = min(1.0, self.max_backoff)

        while self._running:
            try:
                # Wait for file to exist
                while self._running and not os.path.exists(self.log_path):
                    LOG.warning(
                        f"Log file not found: {self.log_path}. "
                        f"Retrying in {backoff:.0f}s..."
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, self.max_backoff)

                if not self._running:
                    break

                backoff = 1.0  # Reset on success
                await self._tail_file()

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._active = False
                if self._running:
                    LOG.error(f"Log tailer error: {e}. Retrying in {backoff:.0f}s...")
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, self.max_backoff)

    async def stop(self) -> None:
        """Stop the tailer."""
        self._running = False
        self._active = False

    async def _tail_file(self) -> None:
        """Open the file, seek to end, and tail new lines."""
        initial_inode = os.stat(self.log_path).st_ino
        LOG.info(f"Tailing log file: {self.log_path} (inode: {initial_inode})")

        with open(self.log_path, "r") as f:
            # Seek to end — only read new lines
            f.seek(0, 2)
            self._active = True

            line_buffer = ""
            accumulated_lines = []
            current_audio_level = None
            current_callsign = None

            while self._running:
                # Check for log rotation (inode change)
                try:
                    current_inode = os.stat(self.log_path).st_ino
                    if current_inode != initial_inode:
                        LOG.info("Log file rotated, reopening...")
                        self._active = False
                        return  # Will re-enter _tail_file from run()
                except FileNotFoundError:
                    LOG.warning("Log file disappeared, waiting for it to return...")
                    self._active = False
                    return

                chunk = f.readline()
                if chunk:
                    line_buffer += chunk
                    if line_buffer.endswith("\n"):
                        line = line_buffer.rstrip("\n")
                        line_buffer = ""

                        # Check if this is a decoded packet line
                        if is_decoded_packet_line(line):
                            # If we had accumulated lines from a previous packet, dispatch them
                            if accumulated_lines and current_callsign:
                                try:
                                    await self.line_callback(
                                        raw_lines=accumulated_lines,
                                        audio_level=current_audio_level,
                                        callsign=current_callsign,
                                    )
                                except Exception as e:
                                    LOG.error(f"Error in line callback: {e}")

                            # Start new accumulation
                            accumulated_lines = [line]
                            current_callsign = extract_callsign(line)
                            current_audio_level = None
                        else:
                            # Accumulate metadata lines
                            accumulated_lines.append(line)
                            level = extract_audio_level(line)
                            if level is not None:
                                current_audio_level = level
                else:
                    # No new data — flush any pending packet if we've waited long enough
                    if accumulated_lines and current_callsign:
                        try:
                            await self.line_callback(
                                raw_lines=accumulated_lines,
                                audio_level=current_audio_level,
                                callsign=current_callsign,
                            )
                        except Exception as e:
                            LOG.error(f"Error in line callback: {e}")
                        accumulated_lines = []
                        current_audio_level = None
                        current_callsign = None

                    await asyncio.sleep(self.sleep_interval)
