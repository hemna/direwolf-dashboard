# Port Client Monitor Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Monitor Direwolf's TCP KISS/AGW ports for client connections and show a live status panel in the dashboard UI.

**Architecture:** A new `PortMonitor` service parses direwolf.conf for port definitions, periodically scans `/proc/net/tcp` (or `lsof` on macOS) for established connections, and broadcasts connect/disconnect events over the existing WebSocket. The `LogTailer`/`PacketProcessor` pipeline provides instant detection between scans.

**Tech Stack:** Python 3.11+ asyncio, FastAPI WebSocket, vanilla JS frontend

---

## Chunk 1: Config & Direwolf Conf Parser

### Task 1: Add `conf_file` to DirewolfConfig

**Files:**
- Modify: `src/direwolf_dashboard/config.py:26-29` (DirewolfConfig dataclass)
- Modify: `src/direwolf_dashboard/config.py:99-109` (RESTART_REQUIRED_FIELDS)
- Modify: `src/direwolf_dashboard/config.py:145` (remove pop line)
- Test: `tests/test_config.py`

- [ ] **Step 1: Write test for conf_file field**

In `tests/test_config.py`, add a test that verifies `conf_file` is preserved:

```python
class TestConfFileField:
    """Test conf_file field on DirewolfConfig."""

    def test_conf_file_default_empty(self):
        """conf_file defaults to empty string."""
        from direwolf_dashboard.config import DirewolfConfig
        dc = DirewolfConfig()
        assert dc.conf_file == ""

    def test_conf_file_preserved_in_config(self, tmp_path):
        """conf_file from YAML is preserved (not stripped)."""
        from direwolf_dashboard.config import load_config
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "direwolf:\n"
            "  agw_host: localhost\n"
            "  agw_port: 8000\n"
            "  conf_file: /etc/direwolf/direwolf.conf\n"
        )
        config = load_config(str(cfg_file))
        assert config.direwolf.conf_file == "/etc/direwolf/direwolf.conf"

    def test_conf_file_in_restart_required(self):
        """conf_file is a restart-required field."""
        from direwolf_dashboard.config import RESTART_REQUIRED_FIELDS
        assert "direwolf.conf_file" in RESTART_REQUIRED_FIELDS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py::TestConfFileField -v`
Expected: FAIL — `DirewolfConfig` has no `conf_file` attribute

- [ ] **Step 3: Implement conf_file field**

In `src/direwolf_dashboard/config.py`:

1. Add `conf_file` to `DirewolfConfig`:
```python
@dataclass
class DirewolfConfig:
    agw_host: str = "localhost"
    agw_port: int = 8000
    log_file: str = "/var/log/direwolf/direwolf.log"
    conf_file: str = ""  # Path to Direwolf's config file for port monitoring
```

2. Remove the pop line at line 145:
```python
    # DELETE this line:
    # direwolf_dict.pop("conf_file", None)  # Removed field
```

3. Add to `RESTART_REQUIRED_FIELDS`:
```python
RESTART_REQUIRED_FIELDS = {
    "data_dir",
    "server.host", "server.port",
    "direwolf.agw_host", "direwolf.agw_port", "direwolf.log_file",
    "direwolf.conf_file",
    "storage.db_path", "tiles.cache_dir",
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py::TestConfFileField -v`
Expected: PASS

- [ ] **Step 5: Run full config test suite**

Run: `uv run pytest tests/test_config.py -v`
Expected: All pass (the old migration test that expected conf_file to be stripped needs updating)

- [ ] **Step 6: Fix migration test if needed**

The existing test at `tests/test_config.py:168` asserts `not hasattr(config.direwolf, "conf_file")`. Update it:
```python
# Old:
assert not hasattr(config.direwolf, "conf_file")
# New:
assert config.direwolf.conf_file == "/home/pi/direwolf.conf"
```

- [ ] **Step 7: Commit**

```bash
git add src/direwolf_dashboard/config.py tests/test_config.py
git commit -m "feat: re-add conf_file field to DirewolfConfig for port monitoring"
```

---

### Task 2: Create Direwolf Conf Parser

**Files:**
- Create: `src/direwolf_dashboard/dw_conf_parser.py`
- Create: `tests/test_dw_conf_parser.py`

- [ ] **Step 1: Write tests for the parser**

Create `tests/test_dw_conf_parser.py`:

```python
"""Tests for Direwolf config file parser."""
import pytest
from direwolf_dashboard.dw_conf_parser import parse_direwolf_conf, DirewolfPorts


class TestParseDirewolfConf:
    """Test parsing of direwolf.conf for port directives."""

    def test_default_ports(self, tmp_path):
        """Config with no port directives returns defaults."""
        conf = tmp_path / "direwolf.conf"
        conf.write_text("MYCALL N0CALL\nMODEM 1200\n")
        result = parse_direwolf_conf(str(conf))
        # Direwolf defaults: AGW 8000, KISS 8001
        assert result.agw_ports == [8000]
        assert result.kiss_ports == [8001]
        assert result.agw_enabled is True
        assert result.kiss_enabled is True

    def test_explicit_ports(self, tmp_path):
        """Explicit port directives are parsed."""
        conf = tmp_path / "direwolf.conf"
        conf.write_text("AGWPORT 9000\nKISSPORT 9001\n")
        result = parse_direwolf_conf(str(conf))
        assert result.agw_ports == [9000]
        assert result.kiss_ports == [9001]

    def test_multiple_kiss_ports(self, tmp_path):
        """Multiple KISSPORT lines produce multiple ports."""
        conf = tmp_path / "direwolf.conf"
        conf.write_text("KISSPORT 8001\nKISSPORT 8002\n")
        result = parse_direwolf_conf(str(conf))
        assert result.kiss_ports == [8001, 8002]

    def test_disabled_kiss(self, tmp_path):
        """KISSPORT 0 disables KISS."""
        conf = tmp_path / "direwolf.conf"
        conf.write_text("KISSPORT 0\n")
        result = parse_direwolf_conf(str(conf))
        assert result.kiss_enabled is False
        assert result.kiss_ports == []

    def test_disabled_agw(self, tmp_path):
        """AGWPORT 0 disables AGW."""
        conf = tmp_path / "direwolf.conf"
        conf.write_text("AGWPORT 0\n")
        result = parse_direwolf_conf(str(conf))
        assert result.agw_enabled is False
        assert result.agw_ports == []

    def test_comments_ignored(self, tmp_path):
        """Lines starting with # are comments."""
        conf = tmp_path / "direwolf.conf"
        conf.write_text("# KISSPORT 9999\nKISSPORT 8001\n")
        result = parse_direwolf_conf(str(conf))
        assert result.kiss_ports == [8001]

    def test_case_insensitive(self, tmp_path):
        """Directives are case-insensitive."""
        conf = tmp_path / "direwolf.conf"
        conf.write_text("kissport 8001\nagwport 8000\n")
        result = parse_direwolf_conf(str(conf))
        assert result.kiss_ports == [8001]
        assert result.agw_ports == [8000]

    def test_file_not_found(self):
        """Missing file returns None."""
        result = parse_direwolf_conf("/nonexistent/direwolf.conf")
        assert result is None

    def test_malformed_port_value(self, tmp_path):
        """Non-integer port value is ignored, falls back to defaults."""
        conf = tmp_path / "direwolf.conf"
        conf.write_text("KISSPORT abc\n")
        result = parse_direwolf_conf(str(conf))
        # Malformed line ignored, default KISS port applies
        assert result.kiss_ports == [8001]

    def test_inline_comments(self, tmp_path):
        """Inline comments after port value are handled."""
        conf = tmp_path / "direwolf.conf"
        conf.write_text("KISSPORT 8001  # my kiss port\n")
        result = parse_direwolf_conf(str(conf))
        assert result.kiss_ports == [8001]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_dw_conf_parser.py -v`
Expected: FAIL — module does not exist

- [ ] **Step 3: Implement the parser**

Create `src/direwolf_dashboard/dw_conf_parser.py`:

```python
"""Parser for Direwolf's configuration file (direwolf.conf).

Extracts KISSPORT and AGWPORT directives to determine which TCP ports
Direwolf is listening on for client connections.
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

LOG = logging.getLogger(__name__)

# Direwolf defaults (used when no explicit directive found)
DEFAULT_AGW_PORT = 8000
DEFAULT_KISS_PORT = 8001

# Pattern: directive name followed by value, optional inline comment
_PORT_RE = re.compile(r"^\s*(KISSPORT|AGWPORT)\s+(\S+)", re.IGNORECASE)


@dataclass
class DirewolfPorts:
    """Discovered port configuration from direwolf.conf."""

    kiss_ports: list[int] = field(default_factory=list)
    agw_ports: list[int] = field(default_factory=list)
    kiss_enabled: bool = True
    agw_enabled: bool = True


def parse_direwolf_conf(path: str) -> Optional[DirewolfPorts]:
    """Parse a Direwolf config file and extract port directives.

    Args:
        path: Path to direwolf.conf

    Returns:
        DirewolfPorts with discovered ports, or None if file not found.
    """
    conf_path = Path(path)
    if not conf_path.is_file():
        LOG.warning(f"Direwolf config not found: {path}")
        return None

    kiss_ports: list[int] = []
    agw_ports: list[int] = []
    kiss_seen = False
    agw_seen = False

    try:
        text = conf_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        LOG.warning(f"Cannot read direwolf config: {e}")
        return None

    for line in text.splitlines():
        # Skip comments
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue

        m = _PORT_RE.match(stripped)
        if not m:
            continue

        directive = m.group(1).upper()
        value_str = m.group(2)

        # Strip inline comment
        if "#" in value_str:
            value_str = value_str.split("#")[0].strip()

        try:
            port_num = int(value_str)
        except ValueError:
            LOG.warning(f"Ignoring malformed port value: {stripped}")
            continue

        if directive == "KISSPORT":
            kiss_seen = True
            if port_num == 0:
                # Explicitly disabled
                pass
            else:
                kiss_ports.append(port_num)
        elif directive == "AGWPORT":
            agw_seen = True
            if port_num == 0:
                # Explicitly disabled
                pass
            else:
                agw_ports.append(port_num)

    # Apply defaults if no directive was seen
    if not kiss_seen:
        kiss_ports = [DEFAULT_KISS_PORT]
    if not agw_seen:
        agw_ports = [DEFAULT_AGW_PORT]

    # Determine enabled state
    kiss_enabled = len(kiss_ports) > 0
    agw_enabled = len(agw_ports) > 0

    result = DirewolfPorts(
        kiss_ports=kiss_ports,
        agw_ports=agw_ports,
        kiss_enabled=kiss_enabled,
        agw_enabled=agw_enabled,
    )
    LOG.info(f"Parsed direwolf config: {result}")
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_dw_conf_parser.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/direwolf_dashboard/dw_conf_parser.py tests/test_dw_conf_parser.py
git commit -m "feat: add direwolf.conf parser for KISS/AGW port discovery"
```

---

## Chunk 2: Port Monitor Service

### Task 3: Create TCP Scanner (platform-aware)

**Files:**
- Create: `src/direwolf_dashboard/port_monitor.py` (scanner portion)
- Create: `tests/test_port_monitor.py`

- [ ] **Step 1: Write tests for /proc/net/tcp parsing**

Create `tests/test_port_monitor.py`:

```python
"""Tests for port monitor service."""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from datetime import datetime, timezone

from direwolf_dashboard.port_monitor import (
    parse_proc_net_tcp,
    TcpConnection,
    PortMonitor,
)
from direwolf_dashboard.dw_conf_parser import DirewolfPorts


class TestParseProcNetTcp:
    """Test /proc/net/tcp hex line parsing."""

    def test_parse_established_connection(self):
        """Parse a single ESTABLISHED connection line."""
        # local 0.0.0.0:8001 remote 192.168.1.50:54321 state 01 (ESTABLISHED)
        line = "   0: 00000000:1F41 3201A8C0:D431 01 00000000:00000000 00:00000000 00000000     0        0 12345 1 0000000000000000 100 0 0 10 0"
        results = parse_proc_net_tcp([line])
        assert len(results) == 1
        conn = results[0]
        assert conn.local_port == 8001
        assert conn.remote_ip == "192.168.1.50"
        assert conn.remote_port == 54321
        assert conn.state == "ESTABLISHED"

    def test_parse_listen_state(self):
        """Parse a LISTEN state line."""
        # local 0.0.0.0:8001 remote 0.0.0.0:0 state 0A (LISTEN)
        line = "   1: 00000000:1F41 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 12345 1 0000000000000000 100 0 0 10 0"
        results = parse_proc_net_tcp([line])
        assert len(results) == 1
        assert results[0].state == "LISTEN"
        assert results[0].local_port == 8001

    def test_skip_header_line(self):
        """Header line (sl local_address...) is skipped."""
        header = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode"
        results = parse_proc_net_tcp([header])
        assert results == []

    def test_multiple_connections(self):
        """Parse multiple lines."""
        lines = [
            "   0: 00000000:1F41 3201A8C0:D431 01 00000000:00000000 00:00000000 00000000     0        0 12345 1 0000000000000000 100 0 0 10 0",
            "   1: 00000000:1F41 6901A8C0:E803 01 00000000:00000000 00:00000000 00000000     0        0 12346 1 0000000000000000 100 0 0 10 0",
        ]
        results = parse_proc_net_tcp(lines)
        assert len(results) == 2
        assert results[0].remote_ip == "192.168.1.50"
        assert results[1].remote_ip == "192.168.1.105"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_port_monitor.py::TestParseProcNetTcp -v`
Expected: FAIL — module does not exist

- [ ] **Step 3: Implement TCP scanner**

Create `src/direwolf_dashboard/port_monitor.py`:

```python
"""Port monitor service — tracks client connections to Direwolf's TCP ports.

Scans /proc/net/tcp (Linux) or uses lsof (macOS) to detect clients
connected to Direwolf's KISS and AGW ports.
"""

import asyncio
import logging
import platform
import socket
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Optional

from direwolf_dashboard.dw_conf_parser import DirewolfPorts

LOG = logging.getLogger(__name__)

# TCP states from /proc/net/tcp
_TCP_STATES = {
    "01": "ESTABLISHED",
    "0A": "LISTEN",
}


@dataclass
class TcpConnection:
    """A single TCP connection parsed from /proc/net/tcp or lsof."""

    local_port: int
    remote_ip: str
    remote_port: int
    state: str  # "ESTABLISHED" or "LISTEN"


@dataclass
class ConnectedClient:
    """A client connected to a Direwolf port."""

    remote_ip: str
    remote_port: int
    local_port: int
    port_type: str  # "KISS" or "AGW"
    hostname: Optional[str] = None
    connected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    is_self: bool = False


def _hex_to_ip(hex_str: str) -> str:
    """Convert /proc/net/tcp hex IP (little-endian) to dotted quad."""
    # /proc/net/tcp stores IPs as little-endian hex
    addr_int = int(hex_str, 16)
    return f"{addr_int & 0xFF}.{(addr_int >> 8) & 0xFF}.{(addr_int >> 16) & 0xFF}.{(addr_int >> 24) & 0xFF}"


def _hex_to_port(hex_str: str) -> int:
    """Convert hex port string to integer."""
    return int(hex_str, 16)


def parse_proc_net_tcp(lines: list[str]) -> list[TcpConnection]:
    """Parse lines from /proc/net/tcp into TcpConnection objects.

    Args:
        lines: Raw lines from /proc/net/tcp (without the header).

    Returns:
        List of parsed TCP connections.
    """
    connections = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("sl") or line.startswith("S"):
            continue

        parts = line.split()
        if len(parts) < 4:
            continue

        try:
            # Format: "sl: local_address remote_address st ..."
            local_addr = parts[1]  # hex_ip:hex_port
            remote_addr = parts[2]
            state_hex = parts[3]

            local_ip_hex, local_port_hex = local_addr.split(":")
            remote_ip_hex, remote_port_hex = remote_addr.split(":")

            state = _TCP_STATES.get(state_hex)
            if state is None:
                continue  # Only care about ESTABLISHED and LISTEN

            connections.append(
                TcpConnection(
                    local_port=_hex_to_port(local_port_hex),
                    remote_ip=_hex_to_ip(remote_ip_hex),
                    remote_port=_hex_to_port(remote_port_hex),
                    state=state,
                )
            )
        except (ValueError, IndexError):
            continue

    return connections


async def _scan_proc_net_tcp() -> list[TcpConnection]:
    """Read /proc/net/tcp and parse connections. Linux only."""
    proc_path = Path("/proc/net/tcp")
    if not proc_path.exists():
        return []

    loop = asyncio.get_event_loop()
    text = await loop.run_in_executor(None, proc_path.read_text)
    lines = text.splitlines()[1:]  # Skip header
    return parse_proc_net_tcp(lines)


async def _scan_lsof(ports: list[int]) -> list[TcpConnection]:
    """Use lsof to find TCP connections on given ports. macOS fallback.

    Makes two calls: one for ESTABLISHED, one for LISTEN state.
    """
    if not ports:
        return []

    connections = []
    for state_filter, state_name in [("ESTABLISHED", "ESTABLISHED"), ("LISTEN", "LISTEN")]:
        try:
            proc = await asyncio.create_subprocess_exec(
                "lsof", "-i", "TCP", "-n", "-P", "-s", f"TCP:{state_filter}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
        except (FileNotFoundError, PermissionError) as e:
            LOG.warning(f"lsof not available: {e}")
            return []

        port_set = set(ports)
        for line in stdout.decode(errors="replace").splitlines()[1:]:
            parts = line.split()
            if len(parts) < 9:
                continue
            # lsof format: COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME
            name = parts[8]  # e.g. "localhost:8001->192.168.1.50:54321" or "*:8001"
            if state_filter == "LISTEN":
                # LISTEN lines look like "*:8001" or "localhost:8001"
                try:
                    local_port = int(name.rsplit(":", 1)[1])
                    if local_port in port_set:
                        connections.append(
                            TcpConnection(
                                local_port=local_port,
                                remote_ip="0.0.0.0",
                                remote_port=0,
                                state="LISTEN",
                            )
                        )
                except (ValueError, IndexError):
                    continue
            else:
                # ESTABLISHED lines have "->"
                if "->" not in name:
                    continue
                try:
                    local_part, remote_part = name.split("->")
                    local_port = int(local_part.rsplit(":", 1)[1])
                    if local_port not in port_set:
                        continue
                    remote_host, remote_port_str = remote_part.rsplit(":", 1)
                    remote_port = int(remote_port_str)
                    remote_ip = remote_host.strip("[]")
                    connections.append(
                        TcpConnection(
                            local_port=local_port,
                            remote_ip=remote_ip,
                            remote_port=remote_port,
                            state="ESTABLISHED",
                        )
                    )
                except (ValueError, IndexError):
                    continue

    return connections
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_port_monitor.py::TestParseProcNetTcp -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/direwolf_dashboard/port_monitor.py tests/test_port_monitor.py
git commit -m "feat: add TCP connection scanner (proc/net/tcp + lsof fallback)"
```

---

### Task 4: Implement PortMonitor Service (delta detection, DNS, scan loop)

**Files:**
- Modify: `src/direwolf_dashboard/port_monitor.py` (add PortMonitor class)
- Modify: `tests/test_port_monitor.py` (add integration tests)

- [ ] **Step 1: Write tests for delta detection and DNS**

Add to `tests/test_port_monitor.py`:

```python
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock


class TestPortMonitorDelta:
    """Test connection delta detection."""

    @pytest.fixture
    def ports(self):
        return DirewolfPorts(
            kiss_ports=[8001],
            agw_ports=[8000],
            kiss_enabled=True,
            agw_enabled=True,
        )

    @pytest.fixture
    def monitor(self, ports):
        broadcast_fn = AsyncMock()
        own_port_fn = MagicMock(return_value=None)
        return PortMonitor(
            ports=ports,
            broadcast_fn=broadcast_fn,
            own_agw_port_fn=own_port_fn,
            scan_interval=1.0,
        )

    def test_detect_new_connection(self, monitor):
        """New connection in scan produces client_connected event."""
        prev = set()
        current_connections = [
            TcpConnection(local_port=8001, remote_ip="192.168.1.50", remote_port=54321, state="ESTABLISHED"),
        ]
        added, removed = monitor._compute_delta(prev, current_connections)
        assert len(added) == 1
        assert added[0].remote_ip == "192.168.1.50"
        assert removed == []

    def test_detect_disconnection(self, monitor):
        """Missing connection produces client_disconnected event."""
        prev = {("192.168.1.50", 54321, 8001)}
        current_connections = []
        added, removed = monitor._compute_delta(prev, current_connections)
        assert added == []
        assert len(removed) == 1
        assert removed[0] == ("192.168.1.50", 54321, 8001)

    def test_no_change(self, monitor):
        """Same connections produce no events."""
        prev = {("192.168.1.50", 54321, 8001)}
        current_connections = [
            TcpConnection(local_port=8001, remote_ip="192.168.1.50", remote_port=54321, state="ESTABLISHED"),
        ]
        added, removed = monitor._compute_delta(prev, current_connections)
        assert added == []
        assert removed == []

    def test_self_connection_labeled(self, monitor):
        """Dashboard's own AGW connection is marked is_self=True."""
        monitor._own_agw_port_fn = MagicMock(return_value=54000)
        conn = TcpConnection(local_port=8000, remote_ip="127.0.0.1", remote_port=54000, state="ESTABLISHED")
        client = monitor._connection_to_client(conn)
        assert client.is_self is True
        assert client.port_type == "AGW"


class TestDnsCache:
    """Test reverse DNS caching."""

    @pytest.mark.asyncio
    async def test_cache_stores_result(self):
        from direwolf_dashboard.port_monitor import DnsCache

        cache = DnsCache(max_size=10, ttl_seconds=3600)
        with patch("socket.gethostbyaddr", return_value=("myhost.local", [], ["192.168.1.1"])):
            result = await cache.resolve("192.168.1.1")
        assert result == "myhost.local"
        # Second call should use cache (no socket call)
        with patch("socket.gethostbyaddr", side_effect=Exception("should not be called")):
            result2 = await cache.resolve("192.168.1.1")
        assert result2 == "myhost.local"

    @pytest.mark.asyncio
    async def test_cache_fallback_on_failure(self):
        from direwolf_dashboard.port_monitor import DnsCache

        cache = DnsCache(max_size=10, ttl_seconds=3600)
        with patch("socket.gethostbyaddr", side_effect=socket.herror("not found")):
            result = await cache.resolve("10.0.0.1")
        assert result is None

    @pytest.mark.asyncio
    async def test_cache_lru_eviction(self):
        from direwolf_dashboard.port_monitor import DnsCache

        cache = DnsCache(max_size=2, ttl_seconds=3600)
        with patch("socket.gethostbyaddr", return_value=("h1", [], ["1.1.1.1"])):
            await cache.resolve("1.1.1.1")
        with patch("socket.gethostbyaddr", return_value=("h2", [], ["2.2.2.2"])):
            await cache.resolve("2.2.2.2")
        with patch("socket.gethostbyaddr", return_value=("h3", [], ["3.3.3.3"])):
            await cache.resolve("3.3.3.3")
        # 1.1.1.1 should have been evicted (LRU)
        assert "1.1.1.1" not in cache._cache
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_port_monitor.py::TestPortMonitorDelta -v`
Expected: FAIL — PortMonitor class doesn't have these methods yet

- [ ] **Step 3: Implement PortMonitor class**

Add to `src/direwolf_dashboard/port_monitor.py` (after the scanner functions):

```python
class DnsCache:
    """LRU cache for reverse DNS lookups with TTL expiry."""

    def __init__(self, max_size: int = 128, ttl_seconds: float = 3600.0):
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self._cache: OrderedDict[str, tuple[Optional[str], float]] = OrderedDict()

    async def resolve(self, ip: str) -> Optional[str]:
        """Resolve IP to hostname. Returns cached value or performs lookup."""
        now = time.time()

        # Check cache
        if ip in self._cache:
            hostname, ts = self._cache[ip]
            if now - ts < self.ttl_seconds:
                self._cache.move_to_end(ip)
                return hostname
            else:
                del self._cache[ip]

        # Perform lookup in executor
        hostname = await self._do_lookup(ip)

        # Store in cache
        self._cache[ip] = (hostname, now)
        self._cache.move_to_end(ip)

        # Evict LRU if over capacity
        while len(self._cache) > self.max_size:
            self._cache.popitem(last=False)

        return hostname

    async def _do_lookup(self, ip: str) -> Optional[str]:
        """Perform reverse DNS lookup with timeout."""
        loop = asyncio.get_event_loop()
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(None, socket.gethostbyaddr, ip),
                timeout=2.0,
            )
            return result[0]
        except (socket.herror, socket.gaierror, asyncio.TimeoutError, OSError):
            return None


class PortMonitor:
    """Monitors Direwolf's TCP ports for client connections.

    Periodically scans for established TCP connections and broadcasts
    connect/disconnect events via WebSocket.
    """

    def __init__(
        self,
        ports: DirewolfPorts,
        broadcast_fn: Optional[Callable[[str, dict], Awaitable[None]]] = None,
        own_agw_port_fn: Optional[Callable[[], Optional[int]]] = None,
        scan_interval: float = 5.0,
    ):
        self._ports = ports
        self._broadcast_fn = broadcast_fn
        self._own_agw_port_fn = own_agw_port_fn or (lambda: None)
        self._scan_interval = scan_interval
        self._running = False
        self._dns_cache = DnsCache(max_size=128, ttl_seconds=3600.0)

        # All monitored ports
        self._monitored_ports: set[int] = set(ports.kiss_ports + ports.agw_ports)

        # Port type lookup
        self._port_type: dict[int, str] = {}
        for p in ports.kiss_ports:
            self._port_type[p] = "KISS"
        for p in ports.agw_ports:
            self._port_type[p] = "AGW"

        # Current state
        self._connected_keys: set[tuple[str, int, int]] = set()  # (ip, remote_port, local_port)
        self._clients: dict[tuple[str, int, int], ConnectedClient] = {}
        self._listening_ports: set[int] = set()

        # Platform detection
        self._use_proc = platform.system() == "Linux"

    async def run(self) -> None:
        """Main scan loop. Call as a background task."""
        self._running = True
        LOG.info(f"Port monitor started. Watching ports: {sorted(self._monitored_ports)}")

        while self._running:
            if not self._broadcast_fn:
                await asyncio.sleep(self._scan_interval)
                continue
            try:
                await self._scan()
            except Exception as e:
                LOG.warning(f"Port scan error: {e}")
            await asyncio.sleep(self._scan_interval)

    async def stop(self) -> None:
        """Stop the monitor."""
        self._running = False

    def get_status(self) -> dict:
        """Get current port status for API/WS responses."""
        return {
            "kiss_enabled": self._ports.kiss_enabled,
            "kiss_ports": [
                {"port": p, "listening": p in self._listening_ports}
                for p in self._ports.kiss_ports
            ],
            "agw_enabled": self._ports.agw_enabled,
            "agw_ports": [
                {"port": p, "listening": p in self._listening_ports}
                for p in self._ports.agw_ports
            ],
            "clients": [self._client_to_dict(c) for c in self._clients.values()],
        }

    async def handle_log_event(self, event_type: str, ip: Optional[str], port: int) -> None:
        """Handle a connect/disconnect detected from Direwolf's log.

        Provides immediate notification between scan intervals.
        Note: Direwolf logs may not include the client IP (only client number
        and port). When IP is None or "unknown", we skip DNS and report what
        we have — the next TCP scan will provide the full picture.
        """
        if not self._broadcast_fn:
            return

        resolved_ip = ip if (ip and ip != "unknown") else None
        hostname = None
        if resolved_ip:
            hostname = await self._dns_cache.resolve(resolved_ip)

        if event_type == "connected":
            await self._broadcast_fn("client_connected", {
                "remote_ip": resolved_ip,
                "remote_port": 0,  # Unknown from log
                "local_port": port,
                "port_type": self._port_type.get(port, "UNKNOWN"),
                "hostname": hostname,
                "connected_at": datetime.now(timezone.utc).isoformat(),
                "is_self": False,
            })
        elif event_type == "disconnected":
            await self._broadcast_fn("client_disconnected", {
                "remote_ip": resolved_ip,
                "remote_port": 0,
                "local_port": port,
                "port_type": self._port_type.get(port, "UNKNOWN"),
                "hostname": hostname,
                "disconnected_at": datetime.now(timezone.utc).isoformat(),
                "is_self": False,
            })

    async def _scan_connections(self) -> list[TcpConnection]:
        """Get all TCP connections on monitored ports. Overridable for testing."""
        if self._use_proc:
            return await _scan_proc_net_tcp()
        else:
            return await _scan_lsof(sorted(self._monitored_ports))

    async def _scan(self) -> None:
        """Perform one scan cycle."""
        all_connections = await self._scan_connections()

        # Filter to our monitored ports
        relevant = [c for c in all_connections if c.local_port in self._monitored_ports]

        # Update listening state
        new_listening = {c.local_port for c in relevant if c.state == "LISTEN"}
        for port in self._monitored_ports:
            was_listening = port in self._listening_ports
            now_listening = port in new_listening
            if was_listening != now_listening:
                await self._broadcast_fn("port_status", {
                    "port": port,
                    "port_type": self._port_type.get(port, "UNKNOWN"),
                    "listening": now_listening,
                })
        self._listening_ports = new_listening

        # Get ESTABLISHED connections
        established = [c for c in relevant if c.state == "ESTABLISHED"]

        # Compute delta
        added, removed = self._compute_delta(self._connected_keys, established)

        # Process removals
        for key in removed:
            client = self._clients.pop(key, None)
            self._connected_keys.discard(key)
            if client:
                await self._broadcast_fn("client_disconnected", {
                    "remote_ip": client.remote_ip,
                    "remote_port": client.remote_port,
                    "local_port": client.local_port,
                    "port_type": client.port_type,
                    "hostname": client.hostname,
                    "disconnected_at": datetime.now(timezone.utc).isoformat(),
                    "is_self": client.is_self,
                })

        # Process additions
        for conn in added:
            client = self._connection_to_client(conn)
            # Resolve DNS asynchronously
            client.hostname = await self._dns_cache.resolve(client.remote_ip)
            key = (client.remote_ip, client.remote_port, client.local_port)
            self._connected_keys.add(key)
            self._clients[key] = client
            await self._broadcast_fn("client_connected", self._client_to_dict(client))

    def _compute_delta(
        self,
        prev_keys: set[tuple[str, int, int]],
        current_connections: list[TcpConnection],
    ) -> tuple[list[TcpConnection], list[tuple[str, int, int]]]:
        """Compare previous state to current scan and return (added, removed)."""
        current_keys = {
            (c.remote_ip, c.remote_port, c.local_port)
            for c in current_connections
        }
        new_keys = current_keys - prev_keys
        gone_keys = prev_keys - current_keys

        added = [c for c in current_connections if (c.remote_ip, c.remote_port, c.local_port) in new_keys]
        removed = list(gone_keys)

        return added, removed

    def _connection_to_client(self, conn: TcpConnection) -> ConnectedClient:
        """Convert a TcpConnection to a ConnectedClient."""
        port_type = self._port_type.get(conn.local_port, "UNKNOWN")

        # Check if this is the dashboard's own connection
        own_port = self._own_agw_port_fn()
        is_self = (
            own_port is not None
            and conn.remote_ip == "127.0.0.1"
            and conn.remote_port == own_port
            and conn.local_port in self._ports.agw_ports
        )

        return ConnectedClient(
            remote_ip=conn.remote_ip,
            remote_port=conn.remote_port,
            local_port=conn.local_port,
            port_type=port_type,
            is_self=is_self,
        )

    @staticmethod
    def _client_to_dict(client: ConnectedClient) -> dict:
        """Serialize a ConnectedClient to a dict for JSON."""
        return {
            "remote_ip": client.remote_ip,
            "remote_port": client.remote_port,
            "local_port": client.local_port,
            "port_type": client.port_type,
            "hostname": client.hostname,
            "connected_at": client.connected_at.isoformat(),
            "is_self": client.is_self,
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_port_monitor.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/direwolf_dashboard/port_monitor.py tests/test_port_monitor.py
git commit -m "feat: implement PortMonitor with delta detection, DNS cache, scan loop"
```

---

## Chunk 3: Backend Integration

### Task 5: Expose `local_port` on AGWReader

**Files:**
- Modify: `src/direwolf_dashboard/agw.py:128` (add property)
- Modify: `tests/test_agw.py`

- [ ] **Step 1: Write test for local_port property**

Add to `tests/test_agw.py`:

```python
class TestAGWReaderLocalPort:
    """Test local_port property."""

    def test_local_port_none_when_disconnected(self):
        """local_port is None when not connected."""
        from direwolf_dashboard.agw import AGWReader
        reader = AGWReader(host="localhost", port=8000, packet_callback=AsyncMock())
        assert reader.local_port is None

    @pytest.mark.asyncio
    async def test_local_port_returns_ephemeral(self):
        """local_port returns the local socket port when connected."""
        from direwolf_dashboard.agw import AGWReader
        reader = AGWReader(host="localhost", port=8000, packet_callback=AsyncMock())
        # Simulate connected state with a mock writer
        mock_writer = MagicMock()
        mock_writer.get_extra_info.return_value = ("127.0.0.1", 54321)
        reader._writer = mock_writer
        reader._connected = True
        assert reader.local_port == 54321
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_agw.py::TestAGWReaderLocalPort -v`
Expected: FAIL — no `local_port` property

- [ ] **Step 3: Implement local_port property**

In `src/direwolf_dashboard/agw.py`, after the `connected` property (line 132), add:

```python
    @property
    def local_port(self) -> Optional[int]:
        """Return the local ephemeral port of the AGW connection, or None."""
        if self._writer and self._connected:
            sockname = self._writer.get_extra_info("sockname")
            if sockname:
                return sockname[1]
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_agw.py::TestAGWReaderLocalPort -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/direwolf_dashboard/agw.py tests/test_agw.py
git commit -m "feat: expose local_port property on AGWReader"
```

---

### Task 6: Wire PortMonitor into Lifecycle

**Files:**
- Modify: `src/direwolf_dashboard/lifecycle.py`
- Modify: `tests/test_lifecycle.py`

- [ ] **Step 1: Write test for port monitor integration**

Add to `tests/test_lifecycle.py`:

```python
class TestPortMonitorIntegration:
    """Test PortMonitor startup/shutdown in lifecycle."""

    @pytest.mark.asyncio
    async def test_port_monitor_started_when_conf_file_set(self, tmp_path):
        """PortMonitor is started when conf_file points to valid direwolf.conf."""
        from direwolf_dashboard.config import Config, DirewolfConfig
        from direwolf_dashboard.lifecycle import startup_services, shutdown_services

        # Create a fake direwolf.conf
        conf = tmp_path / "direwolf.conf"
        conf.write_text("KISSPORT 8001\nAGWPORT 8000\n")

        config = Config(
            data_dir=str(tmp_path),
            direwolf=DirewolfConfig(
                agw_host="localhost",
                agw_port=8000,
                conf_file=str(conf),
            ),
        )
        # We'll need to mock actual services to avoid real connections
        with patch("direwolf_dashboard.lifecycle.Storage") as MockStorage, \
             patch("direwolf_dashboard.lifecycle.TileProxy") as MockTile, \
             patch("direwolf_dashboard.lifecycle.AGWReader") as MockAGW, \
             patch("direwolf_dashboard.lifecycle.LogTailer") as MockLog, \
             patch("direwolf_dashboard.lifecycle.PortMonitor") as MockPM:
            MockStorage.return_value.init = AsyncMock()
            MockTile.return_value.init = AsyncMock()
            MockAGW.return_value.run = AsyncMock()
            MockLog.return_value.run = AsyncMock()
            MockPM.return_value.run = AsyncMock()

            services = await startup_services(config)
            assert services.port_monitor is not None
            await shutdown_services(services)

    @pytest.mark.asyncio
    async def test_port_monitor_none_when_no_conf_file(self, tmp_path):
        """PortMonitor is None when conf_file not set."""
        from direwolf_dashboard.config import Config, DirewolfConfig
        from direwolf_dashboard.lifecycle import startup_services, shutdown_services

        config = Config(
            data_dir=str(tmp_path),
            direwolf=DirewolfConfig(agw_host="localhost", agw_port=8000),
        )
        with patch("direwolf_dashboard.lifecycle.Storage") as MockStorage, \
             patch("direwolf_dashboard.lifecycle.TileProxy") as MockTile, \
             patch("direwolf_dashboard.lifecycle.AGWReader") as MockAGW, \
             patch("direwolf_dashboard.lifecycle.LogTailer") as MockLog:
            MockStorage.return_value.init = AsyncMock()
            MockTile.return_value.init = AsyncMock()
            MockAGW.return_value.run = AsyncMock()
            MockLog.return_value.run = AsyncMock()

            services = await startup_services(config)
            assert services.port_monitor is None
            await shutdown_services(services)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_lifecycle.py::TestPortMonitorIntegration -v`
Expected: FAIL — no `port_monitor` attribute on DirewolfServices

- [ ] **Step 3: Implement lifecycle integration**

In `src/direwolf_dashboard/lifecycle.py`:

1. Add import at top:
```python
from direwolf_dashboard.dw_conf_parser import parse_direwolf_conf
from direwolf_dashboard.port_monitor import PortMonitor
```

2. Add `port_monitor` field to `DirewolfServices` (after `log_tailer`):
```python
    log_tailer: LogTailer
    port_monitor: Optional["PortMonitor"] = None
```

3. In `startup_services()`, after creating `log_tailer` and before building `services`, create port_monitor without broadcast_fn (it needs `services.ws_clients` which doesn't exist yet):
```python
    # Create port monitor (if conf_file configured)
    port_monitor: Optional[PortMonitor] = None
    if config.direwolf.conf_file:
        dw_ports = parse_direwolf_conf(config.direwolf.conf_file)
        if dw_ports:
            port_monitor = PortMonitor(
                ports=dw_ports,
                broadcast_fn=None,  # Set after services created
                own_agw_port_fn=lambda: agw_reader.local_port,
            )
```

4. Add `port_monitor=port_monitor` to the `DirewolfServices(...)` constructor call.

5. **After** `services` is created, wire up the broadcast function:
```python
    # Wire up port_monitor broadcast now that services (and ws_clients) exist
    if services.port_monitor:
        async def _pm_broadcast(event: str, data: dict) -> None:
            await broadcast_event(event, data, services.ws_clients)
        services.port_monitor._broadcast_fn = _pm_broadcast
```

6. Add port_monitor background task after the others:
```python
    if services.port_monitor:
        background_tasks.append(asyncio.create_task(services.port_monitor.run()))
```

7. In `shutdown_services()`, add before canceling tasks:
```python
    if services.port_monitor:
        await services.port_monitor.stop()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_lifecycle.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/direwolf_dashboard/lifecycle.py tests/test_lifecycle.py
git commit -m "feat: wire PortMonitor into service lifecycle"
```

---

### Task 7: Add WebSocket status and REST endpoint

**Files:**
- Modify: `src/direwolf_dashboard/routers.py`
- Modify: `tests/test_routers.py`

- [ ] **Step 1: Write test for GET /api/ports**

Add to `tests/test_routers.py`:

```python
class TestPortsEndpoint:
    """Test GET /api/ports endpoint."""

    @pytest.mark.asyncio
    async def test_ports_returns_status_when_monitor_active(self, client):
        """Returns port status when port monitor is running."""
        # Mock port_monitor.get_status()
        services = client.app.state.container.services
        services.port_monitor = MagicMock()
        services.port_monitor.get_status.return_value = {
            "kiss_enabled": True,
            "kiss_ports": [{"port": 8001, "listening": True}],
            "agw_enabled": True,
            "agw_ports": [{"port": 8000, "listening": True}],
            "clients": [],
        }
        response = client.get("/api/ports")
        assert response.status_code == 200
        data = response.json()
        assert data["kiss_enabled"] is True

    @pytest.mark.asyncio
    async def test_ports_returns_null_when_no_monitor(self, client):
        """Returns null when port monitor is disabled."""
        services = client.app.state.container.services
        services.port_monitor = None
        response = client.get("/api/ports")
        assert response.status_code == 200
        assert response.json() is None
```

- [ ] **Step 2: Implement the endpoint and status extension**

In `src/direwolf_dashboard/routers.py`:

1. Add `GET /api/ports` in the main API router:
```python
@router.get("/ports")
async def get_ports():
    """Get current Direwolf port status and connected clients."""
    services = container.services
    if services and services.port_monitor:
        return services.port_monitor.get_status()
    return None
```

2. In the WebSocket handler, extend the initial status event (after line 372):
```python
            # Send current status
            port_status = None
            if services.port_monitor:
                port_status = services.port_monitor.get_status()

            await ws.send_json(
                {
                    "event": "status",
                    "data": {
                        "agw_connected": services.agw_reader.connected if services.agw_reader else False,
                        "log_tailer_active": services.log_tailer.active if services.log_tailer else False,
                        "ports": port_status,
                    },
                }
            )
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/test_routers.py -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add src/direwolf_dashboard/routers.py tests/test_routers.py
git commit -m "feat: add GET /api/ports endpoint and ports in WS status"
```

---

### Task 8: Log tailer integration for instant detection

**Files:**
- Modify: `src/direwolf_dashboard/processor.py`
- Modify: `tests/test_processor.py`

- [ ] **Step 1: Write test for log event detection**

Add to `tests/test_processor.py`:

```python
class TestClientLogDetection:
    """Test detection of client connect/disconnect from Direwolf log lines."""

    @pytest.fixture
    def processor_with_monitor(self):
        from direwolf_dashboard.processor import PacketProcessor
        queue = asyncio.Queue()
        proc = PacketProcessor(broadcast_queue=queue)
        proc.port_monitor = AsyncMock()
        return proc

    @pytest.mark.asyncio
    async def test_detect_client_connected(self, processor_with_monitor):
        """Detects 'Connected to client application' log line."""
        proc = processor_with_monitor
        lines = ["Connected to client application 0 on port 8001..."]
        await proc.on_log_lines(lines, audio_level=None, callsign=None)
        proc.port_monitor.handle_log_event.assert_called_once()
        args = proc.port_monitor.handle_log_event.call_args
        assert args[0][0] == "connected"  # event_type

    @pytest.mark.asyncio
    async def test_no_detection_when_no_monitor(self):
        """No crash when port_monitor is None."""
        from direwolf_dashboard.processor import PacketProcessor
        queue = asyncio.Queue()
        proc = PacketProcessor(broadcast_queue=queue)
        proc.port_monitor = None
        lines = ["Connected to client application 0 on port 8001..."]
        await proc.on_log_lines(lines, audio_level=None, callsign=None)
        # Should not crash
```

- [ ] **Step 2: Implement log detection in processor**

In `src/direwolf_dashboard/processor.py`:

1. Add `port_monitor` attribute to `__init__`:
```python
    def __init__(self, broadcast_queue: asyncio.Queue):
        self.broadcast_queue = broadcast_queue
        self.port_monitor = None  # Set by lifecycle if port monitoring enabled
        self._pending_log_data: dict[str, dict] = {}
        self._correlation_window = 2.0
```

2. Add a regex at module level:
```python
import re
_CLIENT_CONNECT_RE = re.compile(
    r"Connected to client application (\d+) on port (\d+)", re.IGNORECASE
)
_CLIENT_DISCONNECT_RE = re.compile(
    r"Client (\d+) on port (\d+) disconnected", re.IGNORECASE
)
```

3. In `on_log_lines()`, before the callsign correlation logic, add:
```python
        # Check for client connect/disconnect events
        if self.port_monitor:
            for line in raw_lines:
                m = _CLIENT_CONNECT_RE.search(line)
                if m:
                    port = int(m.group(2))
                    await self.port_monitor.handle_log_event("connected", "unknown", port)
                    continue
                m = _CLIENT_DISCONNECT_RE.search(line)
                if m:
                    port = int(m.group(2))
                    await self.port_monitor.handle_log_event("disconnected", "unknown", port)
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/test_processor.py::TestClientLogDetection -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add src/direwolf_dashboard/processor.py tests/test_processor.py
git commit -m "feat: detect client connect/disconnect from Direwolf log lines"
```

---

## Chunk 4: Frontend UI

### Task 9: Add HTML container for port panel

**Files:**
- Modify: `src/direwolf_dashboard/static/index.html`

- [ ] **Step 1: Add port panel HTML**

After the `#status-indicator` div (line 31), but still inside `.toolbar-left`, add:

```html
            <div id="port-panel" class="port-panel hidden">
                <div class="port-panel-header" id="port-panel-toggle">
                    <span>Ports</span>
                    <span class="port-panel-chevron">&#9660;</span>
                </div>
                <div class="port-panel-body" id="port-panel-body">
                    <!-- Populated by JS -->
                </div>
            </div>
```

- [ ] **Step 2: Verify no regressions**

Open `index.html` in browser, confirm no visual breakage (manual check).

- [ ] **Step 3: Commit**

```bash
git add src/direwolf_dashboard/static/index.html
git commit -m "feat: add port panel HTML container"
```

---

### Task 10: Add port panel CSS

**Files:**
- Modify: `src/direwolf_dashboard/static/style.css`

- [ ] **Step 1: Add styles for the port panel**

Append to `style.css`:

```css
/* Port Client Monitor Panel */
.port-panel {
    position: relative;
    margin-left: 12px;
    font-size: 11px;
}
.port-panel.hidden {
    display: none;
}
.port-panel-header {
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 4px;
    color: var(--text-secondary);
    user-select: none;
}
.port-panel-header:hover {
    color: var(--text-primary);
}
.port-panel-chevron {
    font-size: 8px;
    transition: transform 0.2s;
}
.port-panel.collapsed .port-panel-chevron {
    transform: rotate(-90deg);
}
.port-panel.collapsed .port-panel-body {
    display: none;
}
.port-panel-body {
    position: absolute;
    top: 100%;
    left: 0;
    z-index: 1000;
    background: var(--bg-surface);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 8px 12px;
    min-width: 240px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.3);
    margin-top: 4px;
}
.port-entry {
    margin-bottom: 6px;
}
.port-entry:last-child {
    margin-bottom: 0;
}
.port-header {
    display: flex;
    align-items: center;
    gap: 6px;
    font-weight: 600;
    font-family: var(--font-mono);
}
.port-dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    display: inline-block;
}
.port-dot.listening {
    background: var(--success);
}
.port-dot.not-listening {
    background: var(--warning, #f0ad4e);
}
.port-dot.disabled {
    background: var(--text-muted, #666);
}
.port-clients {
    padding-left: 16px;
    font-family: var(--font-mono);
    color: var(--text-secondary);
}
.port-client {
    padding: 1px 0;
    transition: background 0.3s;
}
.port-client.is-self {
    font-style: italic;
    opacity: 0.7;
}
.port-client.flash-connect {
    background: rgba(40, 167, 69, 0.2);
}
.port-client.flash-disconnect {
    background: rgba(220, 53, 69, 0.2);
}
.port-no-clients {
    color: var(--text-muted, #666);
    font-style: italic;
    padding-left: 16px;
}

/* Mobile: auto-collapse port panel */
@media (max-width: 600px) {
    .port-panel:not(.collapsed) .port-panel-body {
        max-width: 200px;
    }
    .port-panel {
        /* Start collapsed on mobile */
    }
}
```

- [ ] **Step 2: Commit**

```bash
git add src/direwolf_dashboard/static/style.css
git commit -m "feat: add port panel CSS styling"
```

---

### Task 11: Implement port panel JavaScript

**Files:**
- Modify: `src/direwolf_dashboard/static/app.js`

- [ ] **Step 1: Add port panel state and rendering**

In `app.js`, inside the IIFE, add a new section for port panel management. Add after the `setStatus` function:

```javascript
    // --- Port Panel ---
    let portState = null; // null = disabled, object = current state

    function initPortPanel(portsData) {
        const panel = document.getElementById('port-panel');
        if (!portsData) {
            panel.classList.add('hidden');
            portState = null;
            return;
        }
        panel.classList.remove('hidden');
        portState = portsData;
        renderPortPanel();
    }

    function renderPortPanel() {
        const body = document.getElementById('port-panel-body');
        if (!portState) { body.innerHTML = ''; return; }

        let html = '';

        // AGW ports
        if (portState.agw_enabled) {
            for (const p of portState.agw_ports) {
                html += renderPortEntry('AGW', p.port, p.listening, 'agw');
            }
        } else {
            html += renderDisabledPort('AGW');
        }

        // KISS ports
        if (portState.kiss_enabled) {
            for (const p of portState.kiss_ports) {
                html += renderPortEntry('KISS', p.port, p.listening, 'kiss');
            }
        } else {
            html += renderDisabledPort('KISS');
        }

        body.innerHTML = html;
    }

    function renderPortEntry(type, port, listening, cssClass) {
        const dotClass = listening ? 'listening' : 'not-listening';
        const statusText = listening ? 'Listening' : 'Not Listening';
        const clients = getClientsForPort(port);
        let clientsHtml = '';
        if (clients.length === 0) {
            clientsHtml = '<div class="port-no-clients">(no clients)</div>';
        } else {
            clientsHtml = clients.map(c => {
                const name = c.is_self ? '(this dashboard)' : (c.hostname || c.remote_ip);
                const selfClass = c.is_self ? ' is-self' : '';
                return `<div class="port-client${selfClass}" data-key="${c.remote_ip}:${c.remote_port}:${c.local_port}">${name}</div>`;
            }).join('');
        }
        return `<div class="port-entry" data-port="${port}">
            <div class="port-header"><span class="port-dot ${dotClass}"></span> ${type} :${port} <span style="font-weight:normal;color:var(--text-secondary)">${statusText}</span></div>
            <div class="port-clients">${clientsHtml}</div>
        </div>`;
    }

    function renderDisabledPort(type) {
        return `<div class="port-entry">
            <div class="port-header"><span class="port-dot disabled"></span> ${type} <span style="font-weight:normal;color:var(--text-muted)">Disabled</span></div>
        </div>`;
    }

    function getClientsForPort(port) {
        if (!portState || !portState.clients) return [];
        return portState.clients.filter(c => c.local_port === port);
    }

    function onClientConnected(data) {
        if (!portState) return;
        portState.clients.push(data);
        renderPortPanel();
        // Flash animation
        setTimeout(() => {
            const el = document.querySelector(`[data-key="${data.remote_ip}:${data.remote_port}:${data.local_port}"]`);
            if (el) {
                el.classList.add('flash-connect');
                setTimeout(() => el.classList.remove('flash-connect'), 1500);
            }
        }, 50);
    }

    function onClientDisconnected(data) {
        if (!portState) return;
        const key = `${data.remote_ip}:${data.remote_port}:${data.local_port}`;
        // Flash before removing
        const el = document.querySelector(`[data-key="${key}"]`);
        if (el) {
            el.classList.add('flash-disconnect');
            setTimeout(() => {
                portState.clients = portState.clients.filter(c =>
                    !(c.remote_ip === data.remote_ip && c.remote_port === data.remote_port && c.local_port === data.local_port)
                );
                renderPortPanel();
            }, 800);
        } else {
            portState.clients = portState.clients.filter(c =>
                !(c.remote_ip === data.remote_ip && c.remote_port === data.remote_port && c.local_port === data.local_port)
            );
            renderPortPanel();
        }
    }

    function onPortStatus(data) {
        if (!portState) return;
        // Update listening state for the given port
        const allPorts = [...portState.kiss_ports, ...portState.agw_ports];
        for (const p of allPorts) {
            if (p.port === data.port) {
                p.listening = data.listening;
            }
        }
        renderPortPanel();
    }
```

- [ ] **Step 2: Wire into WebSocket message handler**

In the `handleWSMessage` function (around line 1224), add cases:

```javascript
        case 'status':
            setStatus(msg.data.agw_connected);
            initPortPanel(msg.data.ports || null);
            break;
        case 'client_connected':
            onClientConnected(msg.data);
            break;
        case 'client_disconnected':
            onClientDisconnected(msg.data);
            break;
        case 'port_status':
            onPortStatus(msg.data);
            break;
```

- [ ] **Step 3: Add panel toggle behavior**

```javascript
    // Panel collapse toggle
    document.getElementById('port-panel-toggle').addEventListener('click', function() {
        document.getElementById('port-panel').classList.toggle('collapsed');
    });
```

- [ ] **Step 4: Manual browser test**

Open dashboard in browser. Verify:
- Panel hidden when no `ports` data in status
- Panel shows when ports data present
- Correct port listings with listening/disabled states
- Client entries render

- [ ] **Step 5: Commit**

```bash
git add src/direwolf_dashboard/static/app.js
git commit -m "feat: implement port panel UI with live connect/disconnect updates"
```

---

## Chunk 5: Integration Testing & Polish

### Task 12: Full integration test

**Files:**
- Create: `tests/test_port_monitor_integration.py`

- [ ] **Step 1: Write end-to-end test**

```python
"""Integration test for port monitor end-to-end flow."""
import pytest
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock

from direwolf_dashboard.dw_conf_parser import DirewolfPorts
from direwolf_dashboard.port_monitor import PortMonitor, TcpConnection


class TestPortMonitorE2E:
    """End-to-end test of scan -> delta -> broadcast."""

    @pytest.mark.asyncio
    async def test_full_cycle_detect_connect_disconnect(self):
        """Scan detects connection, then disconnection."""
        ports = DirewolfPorts(kiss_ports=[8001], agw_ports=[], kiss_enabled=True, agw_enabled=False)
        events = []

        async def mock_broadcast(event, data):
            events.append((event, data))

        monitor = PortMonitor(
            ports=ports,
            broadcast_fn=mock_broadcast,
            own_agw_port_fn=lambda: None,
            scan_interval=0.1,
        )

        # Mock scan to return one connection
        fake_connections = [
            TcpConnection(local_port=8001, remote_ip="192.168.1.50", remote_port=12345, state="ESTABLISHED"),
            TcpConnection(local_port=8001, remote_ip="0.0.0.0", remote_port=0, state="LISTEN"),
        ]
        with patch.object(monitor, "_scan_connections", return_value=fake_connections):
            with patch("direwolf_dashboard.port_monitor.DnsCache.resolve", return_value="test-host.local"):
                await monitor._scan()

        # Should have port_status (listening) + client_connected
        assert any(e[0] == "client_connected" for e in events)
        connected_event = next(e for e in events if e[0] == "client_connected")
        assert connected_event[1]["remote_ip"] == "192.168.1.50"
        assert connected_event[1]["hostname"] == "test-host.local"

        events.clear()

        # Now scan with no connections (client disconnected)
        with patch.object(monitor, "_scan_connections", return_value=[
            TcpConnection(local_port=8001, remote_ip="0.0.0.0", remote_port=0, state="LISTEN"),
        ]):
            await monitor._scan()

        assert any(e[0] == "client_disconnected" for e in events)
```

- [ ] **Step 2: Run test**

Run: `uv run pytest tests/test_port_monitor_integration.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_port_monitor_integration.py
git commit -m "test: add port monitor integration test"
```

---

### Task 13: Run full test suite

- [ ] **Step 1: Run all tests**

Run: `uv run pytest tests/ -v`
Expected: All PASS

- [ ] **Step 2: Fix any failures**

Address any regressions from modified files.

- [ ] **Step 3: Final commit if fixes needed**

```bash
git add -A
git commit -m "fix: address test regressions from port monitor integration"
```
