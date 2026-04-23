"""Packet processor — merges AGW + log data, computes derived fields, formats output."""

import asyncio
import logging
import math
import time
from typing import Optional

LOG = logging.getLogger(__name__)


def calculate_initial_compass_bearing(point1: tuple, point2: tuple) -> float:
    """Calculate initial compass bearing between two lat/lon points.

    Same formula as APRSD's utils.calculate_initial_compass_bearing.

    Args:
        point1: (latitude, longitude) of origin.
        point2: (latitude, longitude) of destination.

    Returns:
        Bearing in degrees (0-360).
    """
    lat1 = math.radians(point1[0])
    lat2 = math.radians(point2[0])
    diff_lon = math.radians(point2[1] - point1[1])

    x = math.sin(diff_lon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - (
        math.sin(lat1) * math.cos(lat2) * math.cos(diff_lon)
    )

    initial_bearing = math.atan2(x, y)
    initial_bearing = math.degrees(initial_bearing)
    compass_bearing = (initial_bearing + 360) % 360

    return compass_bearing


def degrees_to_cardinal(degrees: float, full_string: bool = True) -> str:
    """Convert compass degrees to cardinal direction string.

    Same logic as APRSD's utils.degrees_to_cardinal.
    """
    dirs = [
        "N",
        "NNE",
        "NE",
        "ENE",
        "E",
        "ESE",
        "SE",
        "SSE",
        "S",
        "SSW",
        "SW",
        "WSW",
        "W",
        "WNW",
        "NW",
        "NNW",
    ]
    ix = round(degrees / (360.0 / len(dirs))) % len(dirs)
    return dirs[ix]


def format_compact_log(packet: dict) -> str:
    """Format a packet dict into APRSD-style compact log HTML.

    Matches the color scheme and format from aprsd/packets/log.py:
    - from_call: #C70039
    - to_call: #D033FF
    - TX: red, RX: #1AA730
    - Packet type: cyan
    - Distance: #FF5733
    - Bearing: #FFA900
    """
    FROM_COLOR = "#C70039"
    TO_COLOR = "#D033FF"
    PACKET_COLOR = "cyan"
    DISTANCE_COLOR = "#FF5733"
    DEGREES_COLOR = "#FFA900"

    tx = packet.get("tx", False)
    pkt_type = packet.get("type", "Unknown")
    msg_no = packet.get("msg_no", "")
    from_call = packet.get("from_call", "")
    to_call = packet.get("to_call", "")
    path = packet.get("path", [])

    # TX/RX indicator
    if tx:
        indicator = '<span style="color:red">TX\u2191</span>'
        arrow_color = "red"
    else:
        indicator = '<span style="color:#1AA730">RX\u2193</span>'
        arrow_color = "#1AA730"

    arrow = f'<span style="color:{arrow_color}">\u2192</span>'

    # Packet type + msg number
    type_str = f'<span style="color:{PACKET_COLOR}">{pkt_type}</span>'
    if msg_no:
        type_str += f":{msg_no}"

    # Path
    if path:
        path_str = arrow.join(
            f'<span style="color:{arrow_color}">{p}</span>' for p in path
        )
        path_str += arrow
    else:
        path_str = ""

    # From → path → To
    from_str = f'<span style="color:{FROM_COLOR}">{from_call}</span>'
    to_str = f'<span style="color:{TO_COLOR}">{to_call}</span>'

    parts = [indicator, " ", type_str, " ", from_str, arrow, path_str, to_str]

    # Show via station for third-party packets
    via = packet.get("via")
    if via:
        parts.append(f' <span style="color:#888">via {via}</span>')

    # Human info (message content, speed, etc.)
    human_info = packet.get("human_info")
    if human_info:
        parts.append(f' : <span style="color:#DAA520">{human_info}</span>')

    return "".join(parts)


def _strip_agw_header(raw: str) -> tuple[str, str | None]:
    """Strip Direwolf AGW monitor header from raw frame data.

    AGW monitored frames look like:
        1:Fm CALL1 To CALL2 Via PATH <UI pid=F0 Len=128 PF=0 >[HH:MM:SS]\\r<payload>
    or the simpler form:
        1:Fm CALL1 To CALL2 Via PATH [HH:MM:SS] <payload>

    Returns:
        Tuple of (payload, via_path). via_path is the comma-separated path string
        from the Via clause (e.g. "N3XYZ*,WIDE1-1,qAR,N3LLO-10"), or None if no
        Via clause found. Payload is the APRS info portion.
    """
    import re

    m = re.match(
        r"^\d+:Fm\s+\S+\s+To\s+\S+(?:\s+Via\s+(\S+(?:,\S+)*))?(?:\s+<[^>]*>)*\s*\[\d{2}:\d{2}:\d{2}\]\s*",
        raw,
    )
    if m:
        via_path = m.group(1)
        payload = raw[m.end() :].lstrip("\r\n")
        return payload, via_path
    return raw, None


def _extract_aprs_for_parsing(
    payload: str, call_from: str, call_to: str, via_path: str | None = None
) -> str:
    """Build a parseable APRS string from the payload.

    Third-party packets start with '}' and contain a full embedded APRS packet.
    Normal packets are just the info field and need from>to: prepended.
    If via_path is provided, it's included in the header so aprslib can parse
    the digipeater path.

    Returns a string suitable for aprslib.parse().
    """
    if payload.startswith("}"):
        # Third-party packet — the part after '}' is a full APRS packet
        return payload[1:]
    if via_path:
        return f"{call_from}>{call_to},{via_path}:{payload}"
    # Build standard APRS format: FROM>TO:payload
    return f"{call_from}>{call_to}:{payload}"


def packet_to_dict(
    raw_aprs_string: str,
    tx: bool,
    call_from: str,
    call_to: str,
    audio_level: Optional[int] = None,
) -> Optional[dict]:
    """Parse a raw APRS string and build a packet dict.

    Uses aprslib for parsing and enriches with computed fields.

    Returns:
        Packet dict ready for storage and broadcasting, or None if parsing fails.
    """
    # Strip Direwolf AGW monitor header if present
    payload, via_path = _strip_agw_header(raw_aprs_string)
    is_third_party = payload.startswith("}")
    aprs_string = _extract_aprs_for_parsing(payload, call_from, call_to, via_path)
    # Strip trailing nulls and carriage returns left over from AGW frames
    aprs_string = aprs_string.rstrip("\r\n\x00")

    try:
        import aprslib

        parsed = aprslib.parse(aprs_string)
    except Exception:
        # If aprslib can't parse it, build a minimal dict from what we have
        parsed = None

    # For third-party packets, use the inner packet's from/to if parsed
    if is_third_party and parsed:
        effective_from = parsed.get("from", call_from)
        effective_to = parsed.get("to", call_to)
    else:
        effective_from = call_from
        effective_to = call_to

    packet = {
        "timestamp": time.time(),
        "tx": tx,
        "from_call": effective_from or (parsed.get("from") if parsed else ""),
        "to_call": effective_to or (parsed.get("to") if parsed else ""),
        "audio_level": audio_level,
        "raw_packet": aprs_string,
    }

    if is_third_party:
        packet["via"] = call_from  # The station that relayed the third-party packet

    if parsed:
        packet["type"] = _classify_packet_type(parsed)
        packet["path"] = parsed.get("path", [])
        packet["msg_no"] = parsed.get("msgNo", "")
        packet["latitude"] = parsed.get("latitude")
        packet["longitude"] = parsed.get("longitude")
        packet["symbol"] = parsed.get("symbol", "")
        packet["symbol_table"] = parsed.get("symbol_table", "")
        packet["human_info"] = _build_human_info(parsed)
        packet["comment"] = parsed.get("comment", "")
    else:
        packet["type"] = "RawPacket"
        packet["path"] = []
        packet["msg_no"] = ""
        packet["human_info"] = payload
        packet["comment"] = ""

    # Generate compact log HTML
    packet["compact_log"] = format_compact_log(packet)

    return packet


def _classify_packet_type(parsed: dict) -> str:
    """Classify an aprslib parsed dict into a packet type name."""
    fmt = parsed.get("format", "")
    if "message" in fmt.lower() or parsed.get("message_text"):
        return "MessagePacket"
    elif parsed.get("weather"):
        return "WeatherPacket"
    elif parsed.get("latitude") is not None:
        return "GPSPacket"
    elif "status" in fmt.lower():
        return "StatusPacket"
    elif "object" in fmt.lower():
        return "ObjectPacket"
    elif "item" in fmt.lower():
        return "ItemPacket"
    elif parsed.get("telemetry"):
        return "TelemetryPacket"
    else:
        return "UnknownPacket"


def _build_human_info(parsed: dict) -> str:
    """Build a human-readable info string from parsed APRS data."""
    parts = []

    # Position-related info
    speed = parsed.get("speed")
    if speed and speed > 0:
        parts.append(f"{speed:.1f}mph")

    course = parsed.get("course")
    if course and course > 0:
        parts.append(f"{course:.0f}\u00b0")

    altitude = parsed.get("altitude")
    if altitude:
        parts.append(f"alt:{altitude:.0f}m")

    # Message
    message_text = parsed.get("message_text")
    if message_text:
        parts.append(message_text)

    # Status
    status = parsed.get("status")
    if status:
        parts.append(status)

    # Comment
    comment = parsed.get("comment")
    if comment and not message_text:
        parts.append(comment)

    return " ".join(parts) if parts else ""


class PacketProcessor:
    """Processes packets from AGW and log tailer, merges data, broadcasts.

    Sits between the data sources (AGW reader, log tailer) and consumers
    (SQLite storage, WebSocket broadcaster).
    """

    def __init__(
        self,
        broadcast_queue: asyncio.Queue,
    ):
        self.broadcast_queue = broadcast_queue
        # Buffer for correlating AGW packets with log data
        self._pending_log_data: dict[
            str, dict
        ] = {}  # callsign -> {audio_level, raw_lines, timestamp}
        self._correlation_window = 2.0  # seconds

    async def on_agw_packet(
        self, raw_data: bytes, tx: bool, call_from: str, call_to: str
    ) -> None:
        """Handle a packet from the AGW socket reader.

        Attempts to correlate with recent log data, then processes and broadcasts.
        """
        # Try to extract raw APRS string from AGW data
        try:
            raw_aprs = raw_data.decode("ascii", errors="replace").strip()
        except Exception:
            raw_aprs = ""

        # Check for correlated log data (audio level only)
        audio_level = None
        log_data = self._pending_log_data.pop(call_from, None)
        if (
            log_data
            and (time.time() - log_data["timestamp"]) < self._correlation_window
        ):
            audio_level = log_data.get("audio_level")

        packet = packet_to_dict(
            raw_aprs_string=raw_aprs,
            tx=tx,
            call_from=call_from,
            call_to=call_to,
            audio_level=audio_level,
        )

        if packet:
            try:
                self.broadcast_queue.put_nowait(packet)
            except asyncio.QueueFull:
                # Drop oldest by getting and discarding
                try:
                    self.broadcast_queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                self.broadcast_queue.put_nowait(packet)
                LOG.warning("Broadcast queue full, dropped oldest packet")

    async def on_log_lines(
        self, raw_lines: list[str], audio_level: Optional[int], callsign: Optional[str]
    ) -> None:
        """Handle log lines from the log file tailer.

        Stores them for correlation with AGW packets.
        """
        if callsign:
            self._pending_log_data[callsign] = {
                "audio_level": audio_level,
                "raw_lines": raw_lines,
                "timestamp": time.time(),
            }

        # Clean old pending data
        now = time.time()
        stale = [
            k
            for k, v in self._pending_log_data.items()
            if now - v["timestamp"] > self._correlation_window * 2
        ]
        for k in stale:
            del self._pending_log_data[k]
