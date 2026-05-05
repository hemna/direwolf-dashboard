# Port Client Monitor Design

## Summary

Monitor Direwolf's TCP KISS and AGW ports for client connections/disconnections and display them in a persistent status panel in the dashboard UI. Uses a hybrid approach: parsing Direwolf's log for immediate event detection combined with periodic TCP socket scanning for ground-truth accuracy.

## Motivation

When running Direwolf as a TNC, multiple client applications (Xastir, Pat, APRSIS32, etc.) connect via TCP KISS or AGW ports. Operators need visibility into which clients are connected at any time -- for debugging, verifying setup, and monitoring station health.

## Configuration

### Dashboard Config (YAML)

Add `conf_file` field to the `direwolf` section:

```yaml
direwolf:
  agw_host: localhost
  agw_port: 8000
  log_file: /var/log/direwolf/direwolf.log
  conf_file: /etc/direwolf/direwolf.conf   # Path to Direwolf's config file
```

- If `conf_file` is not set or the file doesn't exist, port monitoring is disabled entirely.
- `conf_file` is added to `RESTART_REQUIRED_FIELDS`.
- **Migration note:** `config.py` currently has `direwolf_dict.pop("conf_file", None)` which strips this field during migration from old configs. This line must be removed to re-enable the field.

### Direwolf Conf Parsing

New module `dw_conf_parser.py` reads direwolf.conf and extracts:

- `KISSPORT` directives -- port numbers Direwolf listens on for KISS clients
- `AGWPORT` directives -- port numbers Direwolf listens on for AGW clients
- Multiple ports supported (Direwolf allows multiple lines)
- A port value of `0` means explicitly disabled

Returns:

```python
@dataclass
class DirewolfPorts:
    kiss_ports: list[int]   # e.g. [8001, 8002] or [] if disabled
    agw_ports: list[int]    # e.g. [8000] or [] if disabled
    kiss_enabled: bool      # False if "KISSPORT 0" or no KISSPORT directive
    agw_enabled: bool       # False if "AGWPORT 0" or no AGWPORT directive
```

## Port Monitor Service

### Module: `port_monitor.py`

Async service class `PortMonitor` runs as a background task.

### Constructor Signature

```python
class PortMonitor:
    def __init__(
        self,
        ports: DirewolfPorts,
        broadcast_fn: Callable[[str, dict], Awaitable[None]],
        own_agw_port_fn: Callable[[], int | None],
        scan_interval: float = 5.0,
    ):
```

- `broadcast_fn`: A closure created in `lifecycle.py` that captures `ws_clients` and calls `broadcast_event(event, data, ws_clients)`. Signature: `async (event_name: str, data: dict) -> None`.
- `own_agw_port_fn`: Returns the local ephemeral port of the dashboard's own AGW connection (from `AGWReader`), or `None` if not connected. Used to label the dashboard's own connection.
- `scan_interval`: Configurable scan interval in seconds (default 5.0).

### Responsibilities

1. On startup, receive parsed `DirewolfPorts` (discovered ports to monitor)
2. Run periodic TCP scan loop (default every 5 seconds)
3. Maintain set of currently connected clients
4. On delta (new/dropped connections), perform async reverse DNS, broadcast events

### TCP Scanning Strategy

- **Linux (production/DigiPi):** Read `/proc/net/tcp` directly. Parse hex-encoded local/remote addresses and connection state. State `01` = ESTABLISHED, state `0A` = LISTEN. No subprocess needed.
- **macOS (dev only):** Fall back to `lsof -i TCP -n -P -s TCP:ESTABLISHED` subprocess. Parse output lines matching the monitored ports. Note: `lsof` on macOS can see the dashboard's own connections without elevated privileges.

**Port listening detection:** In addition to tracking ESTABLISHED connections, the scan checks whether each configured port is in LISTEN state. This detects whether Direwolf is actually running and listening.

### Client State Model

```python
@dataclass
class ConnectedClient:
    remote_ip: str
    remote_port: int
    local_port: int
    port_type: str          # "KISS" or "AGW"
    hostname: str | None    # Reverse DNS result, None until resolved
    connected_at: datetime  # Time first detected (by log event or TCP scan, whichever is earlier)
    is_self: bool           # True if this is the dashboard's own AGW connection

@dataclass
class PortStatus:
    port: int
    port_type: str          # "KISS" or "AGW"
    listening: bool         # True if Direwolf is listening on this port
    clients: list[ConnectedClient]
```

### Event Flow

1. Each scan produces a set of `(remote_ip, remote_port, local_port)` tuples for ESTABLISHED connections
2. Compare against previous scan's set
3. New entries → `client_connected` event
4. Missing entries → `client_disconnected` event
5. Events broadcast via `broadcast_fn` to all WebSocket clients

**Ephemeral port note:** TCP ephemeral ports change on reconnect. If a client at the same IP disconnects and reconnects within one scan cycle, this produces a disconnect+connect event pair. This is correct behavior -- the UI shows the events as they happen.

### Reverse DNS

- Use `asyncio.get_event_loop().run_in_executor()` with `socket.gethostbyaddr()`
- LRU cache: max 128 entries, evict least-recently-used (bounded for Pi memory)
- Timeout after 2 seconds, fall back to raw IP
- Cache entries expire after 1 hour (re-resolve on next access)

### Log Tailer Integration

**Mechanism:** The `PacketProcessor.on_log_lines()` method is extended to detect Direwolf's client connect/disconnect log patterns. When a match is found, it calls `port_monitor.handle_log_event(event_type, details)` directly.

The `PacketProcessor` receives a reference to `PortMonitor` (or `None` if port monitoring is disabled) during construction.

**Flow:**
1. `LogTailer` calls `processor.on_log_lines(lines, ...)`
2. `on_log_lines` scans for patterns like `"Connected to client application ..."` or `"Client ... disconnected"`
3. On match, calls `port_monitor.handle_log_event("connected", {"ip": ..., "port": ...})`
4. `PortMonitor` immediately broadcasts the event and updates its internal state
5. Next TCP scan reconciles (confirms or corrects) -- if the log event was spurious, the scan corrects it

If `PortMonitor` is `None` (conf_file not configured), the patterns are simply not checked.

### Dashboard's Own Connection

The dashboard connects to one AGW port. The `PortMonitor` detects this connection.

**Decision:** Label it as "(this dashboard)" in the UI rather than filtering it out. This is more useful for debugging -- the operator can see that the dashboard itself is connected.

**Implementation:** `AGWReader` exposes a property `local_port -> int | None` returning the ephemeral port of its TCP socket. `PortMonitor` calls `own_agw_port_fn()` on each scan and marks matching connections with `is_self = True`.

### Direwolf Restart Handling

If Direwolf restarts:
1. All ESTABLISHED connections disappear from TCP scan → disconnect events emitted for all clients
2. The LISTEN state disappears briefly → `listening: false` reported for affected ports
3. When Direwolf comes back, LISTEN state returns → `listening: true`
4. Clients reconnect → new connect events

The UI shows `listening: false` as a warning state (e.g. amber dot, "Not Listening") distinct from "Disabled".

## WebSocket Events

### `client_connected`

```json
{
  "event": "client_connected",
  "data": {
    "remote_ip": "192.168.1.50",
    "remote_port": 54321,
    "local_port": 8001,
    "port_type": "KISS",
    "hostname": "xastir-pi.local",
    "connected_at": "2026-05-05T14:32:01Z",
    "is_self": false
  }
}
```

### `client_disconnected`

```json
{
  "event": "client_disconnected",
  "data": {
    "remote_ip": "192.168.1.50",
    "remote_port": 54321,
    "local_port": 8001,
    "port_type": "KISS",
    "hostname": "xastir-pi.local",
    "disconnected_at": "2026-05-05T14:35:12Z",
    "is_self": false
  }
}
```

### `port_status`

Emitted when a port's listening state changes:

```json
{
  "event": "port_status",
  "data": {
    "port": 8001,
    "port_type": "KISS",
    "listening": false
  }
}
```

### Extended `status` Event

On WebSocket connect (in `routers.py` at the `ws.send_json({"event": "status", ...})` block), add a `ports` field to the existing payload:

```json
{
  "event": "status",
  "data": {
    "agw_connected": true,
    "log_tailer_active": true,
    "ports": {
      "kiss_enabled": true,
      "kiss_ports": [{"port": 8001, "listening": true}],
      "agw_enabled": true,
      "agw_ports": [{"port": 8000, "listening": true}],
      "clients": [
        {
          "remote_ip": "192.168.1.50",
          "remote_port": 54321,
          "local_port": 8001,
          "port_type": "KISS",
          "hostname": "xastir-pi.local",
          "connected_at": "2026-05-05T14:32:01Z",
          "is_self": false
        }
      ]
    }
  }
}
```

When `conf_file` is not configured, `ports` is `null`.

### REST API

**`GET /api/ports`** -- Returns the current port status (same structure as the `ports` field in the status event). Returns `null` if port monitoring is disabled. Useful for debugging via curl.

## Frontend UI

### Persistent Status Panel

Located below the existing connection status indicator in the toolbar/sidebar.

**Enabled + Listening + Clients:**
```
┌─ Direwolf Ports ──────────────────────┐
│                                        │
│  AGW  :8000  ● Listening               │
│    └─ (this dashboard)               │
│    └─ xastir-pi.local                │
│                                        │
│  KISS :8001  ● Listening               │
│    └─ pat-mobile.local               │
│                                        │
│  KISS :8002  ● Listening               │
│    (no clients)                        │
│                                        │
└────────────────────────────────────────┘
```

**Enabled + Not Listening (Direwolf down):**
```
│  KISS :8001  ◉ Not Listening           │
```

Amber/warning dot, no client list.

**Disabled:**
```
│  KISS         ○ Disabled               │
```

Grey/dim dot, no client list.

**When `conf_file` Not Configured:**

Panel is hidden entirely.

### Styling

- Dark theme, matching existing `style.css` variables
- Green dot (`●`) for listening, amber dot (`◉`) for not listening, grey dot (`○`) for disabled
- Monospace font for IPs/ports
- Brief CSS highlight animation: green glow on new connection, red glow on disconnect before removal
- Compact: one line per client
- "(this dashboard)" shown in dimmed/italic text
- Collapsible on mobile-width screens

## Service Integration & Lifecycle

### Startup (`lifecycle.py`)

1. If `config.direwolf.conf_file` is set and file exists:
   - Parse direwolf.conf → `DirewolfPorts`
   - Create `broadcast_fn` closure capturing `ws_clients`
   - Create `PortMonitor(ports, broadcast_fn, own_agw_port_fn)`
   - Pass `port_monitor` reference to `PacketProcessor`
   - Start `port_monitor.run()` as background task alongside AGW/LogTailer
2. If not set or file missing:
   - No `PortMonitor` created
   - `PacketProcessor` receives `None` for port_monitor
   - `ports` field in status events is `null`

### Shutdown

- `port_monitor.stop()` -- sets `_running = False`, cancels scan loop, clears state

### Error Handling

- Malformed direwolf.conf → log warning, treat as "no ports configured"
- `/proc/net/tcp` unavailable → fall back to `lsof -i TCP -n -P -s TCP:ESTABLISHED` subprocess
- TCP scan failures → log warning, retry next cycle (don't crash)
- Reverse DNS timeout → use raw IP
- `lsof` permission denied → log once, disable macOS fallback, monitor degrades gracefully

## New Files

- `src/direwolf_dashboard/dw_conf_parser.py` -- Direwolf config file parser
- `src/direwolf_dashboard/port_monitor.py` -- Port monitoring service

## Modified Files

- `src/direwolf_dashboard/config.py` -- Add `conf_file` field to `DirewolfConfig`, remove the `direwolf_dict.pop("conf_file", None)` migration line
- `src/direwolf_dashboard/lifecycle.py` -- Start/stop `PortMonitor`, create broadcast_fn closure, wire up references
- `src/direwolf_dashboard/routers.py` -- Include port data in `status` WS event, add `GET /api/ports` endpoint
- `src/direwolf_dashboard/processor.py` -- Parse client connect/disconnect log lines, call `port_monitor.handle_log_event()`
- `src/direwolf_dashboard/agw.py` -- Expose `local_port` property on `AGWReader`
- `src/direwolf_dashboard/static/app.js` -- Port status panel UI, handle new WS events
- `src/direwolf_dashboard/static/style.css` -- Panel styling
- `src/direwolf_dashboard/static/index.html` -- Panel container element

## Testing

- Unit tests for `dw_conf_parser.py` with various direwolf.conf formats (multiple ports, disabled ports, missing directives, malformed files)
- Unit tests for `/proc/net/tcp` hex parsing logic
- Unit tests for `lsof` output parsing (macOS fallback)
- Unit tests for delta detection (connect/disconnect, same-IP reconnect)
- Unit tests for reverse DNS cache (LRU eviction, expiry)
- Unit tests for dashboard self-connection labeling
- Integration test: mock TCP state, verify WS events emitted
- Integration test: verify `GET /api/ports` returns correct state
- Manual browser test: verify panel renders and updates in real-time
