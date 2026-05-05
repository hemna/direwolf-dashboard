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

### Responsibilities

1. On startup, receive parsed `DirewolfPorts` (discovered ports to monitor)
2. Run periodic TCP scan loop (every 5 seconds)
3. Maintain set of currently connected clients
4. On delta (new/dropped connections), perform async reverse DNS, broadcast events

### TCP Scanning Strategy

- **Linux (production/DigiPi):** Read `/proc/net/tcp` directly. Parse hex-encoded local/remote addresses and connection state. `01` = ESTABLISHED. No subprocess needed.
- **macOS (dev):** Fall back to `lsof -i -n -P` subprocess or `ss` equivalent.

### Client State Model

```python
@dataclass
class ConnectedClient:
    remote_ip: str
    remote_port: int
    local_port: int
    port_type: str          # "KISS" or "AGW"
    hostname: str | None    # Reverse DNS result, None until resolved
    connected_at: datetime
```

### Event Flow

1. Each scan produces a set of `(remote_ip, remote_port, local_port)` tuples
2. Compare against previous scan's set
3. New entries → `client_connected` event
4. Missing entries → `client_disconnected` event
5. Events broadcast via existing `broadcast_event()` to all WebSocket clients

### Reverse DNS

- Use `asyncio.get_event_loop().run_in_executor()` with `socket.getfqdn()`
- Cache results (IP → hostname) to avoid repeated lookups
- Timeout after 2 seconds, fall back to raw IP

### Log Tailer Integration

The existing `LogTailer` → `PacketProcessor` pipeline gains a new parse rule:

- Match Direwolf's connect/disconnect log patterns (e.g. `"Connected to client application ..."`)
- On match, immediately emit event without waiting for next TCP scan cycle
- Next TCP scan reconciles (confirms or corrects) the state
- If `PortMonitor` doesn't exist (conf_file not configured), log lines are ignored

### Dashboard's Own Connection

The dashboard itself connects to an AGW port. The `PortMonitor` will detect this.

- Filter: exclude connections from `127.0.0.1` that match the dashboard's own AGW socket
- Or label as "(this dashboard)" in the UI

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
    "connected_at": "2026-05-05T14:32:01Z"
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
    "disconnected_at": "2026-05-05T14:35:12Z"
  }
}
```

### Extended `status` Event

On WebSocket connect, the existing `status` event includes a new `ports` field:

```json
{
  "event": "status",
  "data": {
    "agw_connected": true,
    "log_tailer_active": true,
    "ports": {
      "kiss_enabled": true,
      "kiss_ports": [8001],
      "agw_enabled": true,
      "agw_ports": [8000],
      "clients": [
        {
          "remote_ip": "192.168.1.50",
          "remote_port": 54321,
          "local_port": 8001,
          "port_type": "KISS",
          "hostname": "xastir-pi.local",
          "connected_at": "2026-05-05T14:32:01Z"
        }
      ]
    }
  }
}
```

When `conf_file` is not configured, `ports` is `null`.

## Frontend UI

### Persistent Status Panel

Located below the existing connection status indicator in the toolbar/sidebar.

```
┌─ Direwolf Ports ──────────────────────┐
│                                        │
│  AGW  :8000  ● Enabled                │
│    └─ xastir-pi.local    (connected)  │
│    └─ 192.168.1.105      (connected)  │
│                                        │
│  KISS :8001  ● Enabled                │
│    └─ pat-mobile.local   (connected)  │
│                                        │
│  KISS :8002  ● Enabled                │
│    (no clients)                        │
│                                        │
└────────────────────────────────────────┘
```

### Disabled Ports

```
│  KISS         ○ Disabled               │
```

Grey/dim dot, no client list.

### When `conf_file` Not Configured

Panel is hidden entirely.

### Styling

- Dark theme, matching existing `style.css` variables
- Green dot (`●`) for enabled, grey dot (`○`) for disabled
- Monospace font for IPs/ports
- Brief CSS highlight animation: green glow on new connection, red glow on disconnect before removal
- Compact: one line per client
- Collapsible on mobile-width screens

## Service Integration & Lifecycle

### Startup (`lifecycle.py`)

1. If `config.direwolf.conf_file` is set and file exists:
   - Parse direwolf.conf → `DirewolfPorts`
   - Create `PortMonitor(ports, broadcast_fn)`
   - Start as background task alongside AGW/LogTailer
2. If not set or file missing:
   - No `PortMonitor` created
   - `ports` field in status events is `null`

### Shutdown

- `port_monitor.stop()` -- cancels scan loop, clears state

### Error Handling

- Malformed direwolf.conf → log warning, treat as "no ports configured"
- `/proc/net/tcp` unavailable → fall back to `lsof` subprocess
- TCP scan failures → log, retry next cycle (don't crash)
- Reverse DNS timeout → use raw IP

## New Files

- `src/direwolf_dashboard/dw_conf_parser.py` -- Direwolf config file parser
- `src/direwolf_dashboard/port_monitor.py` -- Port monitoring service

## Modified Files

- `src/direwolf_dashboard/config.py` -- Add `conf_file` field to `DirewolfConfig`
- `src/direwolf_dashboard/lifecycle.py` -- Start/stop `PortMonitor`, include ports in status
- `src/direwolf_dashboard/routers.py` -- Include port data in `status` WS event
- `src/direwolf_dashboard/processor.py` -- Parse client connect/disconnect log lines
- `src/direwolf_dashboard/static/app.js` -- Port status panel UI, handle new WS events
- `src/direwolf_dashboard/static/style.css` -- Panel styling
- `src/direwolf_dashboard/static/index.html` -- Panel container element

## Testing

- Unit tests for `dw_conf_parser.py` with various direwolf.conf formats
- Unit tests for `/proc/net/tcp` parsing logic
- Unit tests for delta detection (connect/disconnect)
- Integration test: mock TCP connections, verify WS events emitted
- Manual browser test: verify panel renders and updates in real-time
