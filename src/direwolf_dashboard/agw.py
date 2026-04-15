"""AGW/AGWPE socket reader for Direwolf."""

import asyncio
import logging
import struct
from dataclasses import dataclass
from typing import Callable, Optional

LOG = logging.getLogger(__name__)

# AGW frame header format: 36 bytes
# port(4) + DataKind(4) + CallFrom(10) + CallTo(10) + DataLen(4) + unused(4)
AGW_HEADER_SIZE = 36
AGW_HEADER_FORMAT = "<I4s10s10sII"


@dataclass
class AGWHeader:
    """Parsed AGW protocol frame header."""

    port: int
    data_kind: str  # Single character like 'U', 'T', 'R', etc.
    call_from: str
    call_to: str
    data_len: int
    unused: int = 0


def parse_header(data: bytes) -> AGWHeader:
    """Parse a 36-byte AGW frame header.

    Args:
        data: Exactly 36 bytes of header data.

    Returns:
        Parsed AGWHeader.

    Raises:
        ValueError: If data is not exactly 36 bytes.
    """
    if len(data) != AGW_HEADER_SIZE:
        raise ValueError(f"Expected {AGW_HEADER_SIZE} bytes, got {len(data)}")

    port, kind_bytes, from_bytes, to_bytes, data_len, unused = struct.unpack(
        AGW_HEADER_FORMAT, data
    )

    # Decode strings, strip null bytes
    data_kind = kind_bytes.decode("ascii", errors="replace").rstrip("\x00")
    call_from = from_bytes.decode("ascii", errors="replace").rstrip("\x00")
    call_to = to_bytes.decode("ascii", errors="replace").rstrip("\x00")

    return AGWHeader(
        port=port,
        data_kind=data_kind,
        call_from=call_from,
        call_to=call_to,
        data_len=data_len,
        unused=unused,
    )


def build_frame(
    data_kind: str,
    call_from: str = "",
    call_to: str = "",
    data: bytes = b"",
    port: int = 0,
) -> bytes:
    """Build an AGW protocol frame.

    Args:
        data_kind: Single character frame type (e.g., 'R', 'm', 'k').
        call_from: Source callsign (max 10 chars).
        call_to: Destination callsign (max 10 chars).
        data: Frame payload data.
        port: Radio port number (default 0).

    Returns:
        Complete AGW frame bytes (header + data).
    """
    kind_bytes = data_kind.encode("ascii").ljust(4, b"\x00")[:4]
    from_bytes = call_from.encode("ascii").ljust(10, b"\x00")[:10]
    to_bytes = call_to.encode("ascii").ljust(10, b"\x00")[:10]

    header = struct.pack(
        AGW_HEADER_FORMAT,
        port,
        kind_bytes,
        from_bytes,
        to_bytes,
        len(data),
        0,  # unused
    )
    return header + data


class AGWReader:
    """Async reader for Direwolf's AGW/AGWPE network interface.

    Connects to Direwolf, enables monitoring, and dispatches
    decoded packets via a callback.
    """

    def __init__(
        self,
        host: str,
        port: int,
        packet_callback: Callable,
        max_backoff: float = 60.0,
    ):
        """Initialize the AGW reader.

        Args:
            host: Direwolf AGW host address.
            port: Direwolf AGW port (default 8000).
            packet_callback: Async callable(raw_data: bytes, tx: bool, call_from: str, call_to: str)
            max_backoff: Maximum reconnect backoff in seconds.
        """
        self.host = host
        self.port = port
        self.packet_callback = packet_callback
        self.max_backoff = max_backoff
        self._running = False
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    async def run(self) -> None:
        """Main run loop. Connects, registers, and reads frames.

        Auto-reconnects with exponential backoff on disconnection.
        """
        self._running = True
        backoff = 1.0

        while self._running:
            try:
                await self._connect()
                backoff = 1.0  # Reset backoff on successful connect
                await self._read_loop()
            except (ConnectionError, OSError, asyncio.IncompleteReadError) as e:
                self._connected = False
                if self._running:
                    LOG.warning(
                        f"AGW connection lost: {e}. Reconnecting in {backoff:.0f}s..."
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, self.max_backoff)
            except asyncio.CancelledError:
                break
            finally:
                await self._close_connection()

    async def stop(self) -> None:
        """Stop the reader and close the connection."""
        self._running = False
        await self._close_connection()

    async def _connect(self) -> None:
        """Establish connection and send registration frames."""
        LOG.info(f"Connecting to AGW at {self.host}:{self.port}...")
        self._reader, self._writer = await asyncio.open_connection(
            self.host, self.port
        )
        self._connected = True
        LOG.info("AGW connected. Sending registration frames...")

        # Send 'R' frame — request version info
        self._writer.write(build_frame("R"))
        await self._writer.drain()

        # Send 'm' frame — enable monitoring of raw frames
        self._writer.write(build_frame("m"))
        await self._writer.drain()

        LOG.info("AGW monitoring enabled.")

    async def _read_loop(self) -> None:
        """Read and dispatch AGW frames until disconnection."""
        while self._running:
            # Read 36-byte header
            header_data = await self._reader.readexactly(AGW_HEADER_SIZE)
            header = parse_header(header_data)

            # Read data payload if present
            data = b""
            if header.data_len > 0:
                data = await self._reader.readexactly(header.data_len)

            await self._dispatch_frame(header, data)

    async def _dispatch_frame(self, header: AGWHeader, data: bytes) -> None:
        """Process a received AGW frame based on its type."""
        kind = header.data_kind

        if kind == "R":
            # Version info response
            if len(data) >= 8:
                major = struct.unpack("<H", data[0:2])[0]
                minor = struct.unpack("<H", data[2:4])[0]
                LOG.info(f"AGW version: {major}.{minor}")

        elif kind == "U":
            # Monitored (RX) packet — contains raw AX.25 frame
            LOG.debug(f"RX frame from {header.call_from} to {header.call_to}")
            try:
                await self.packet_callback(
                    raw_data=data,
                    tx=False,
                    call_from=header.call_from,
                    call_to=header.call_to,
                )
            except Exception as e:
                LOG.error(f"Error processing RX packet: {e}")

        elif kind == "T":
            # Transmitted (TX) packet
            LOG.debug(f"TX frame from {header.call_from} to {header.call_to}")
            try:
                await self.packet_callback(
                    raw_data=data,
                    tx=True,
                    call_from=header.call_from,
                    call_to=header.call_to,
                )
            except Exception as e:
                LOG.error(f"Error processing TX packet: {e}")

        elif kind == "K":
            # Raw AX.25 frame
            LOG.debug(f"Raw AX.25 frame ({len(data)} bytes)")

        else:
            LOG.debug(f"Unhandled AGW frame type '{kind}' ({len(data)} bytes)")

    async def _close_connection(self) -> None:
        """Close the TCP connection."""
        self._connected = False
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None
