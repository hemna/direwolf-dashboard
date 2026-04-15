# Direwolf Dashboard

A lightweight, web-based live display of [Direwolf](https://github.com/wb2osz/direwolf) TNC activity. Designed to run on a **Raspberry Pi Zero 2W** — shows a live map of APRS stations and a scrolling packet log in your browser.

## Features

- **Live Leaflet map** with station markers, callsign labels, and track lines for moving stations
- **Scrolling packet log** formatted in [APRSD](https://github.com/craigerl/aprsd) compact style with color-coded TX/RX, callsigns, paths, and bearing/distance
- **Dual data sources** — connects to Direwolf's AGW socket (TX/RX distinction) and tails the log file (audio levels, decode stats)
- **Inline raw log toggle** — click any packet to expand the raw Direwolf log lines
- **Filters** — by callsign, packet type, TX/RX
- **SQLite storage** with configurable retention (default 7 days)
- **Tile caching proxy** — lazy on-demand caching or pre-download for offline use
- **All settings configurable via the web UI** — station info, Direwolf connection, retention, tile cache
- **Single async Python process** — ~30-50MB RAM, one systemd service

## Requirements

- Python 3.11+
- [Direwolf](https://github.com/wb2osz/direwolf) running with AGW enabled (default port 8000)

## Installation

### From source

```bash
git clone https://github.com/hemna/direwolf-dashboard.git
cd direwolf-dashboard
pip install .
```

### Development install

```bash
pip install -e ".[dev]"
```

### Using pipx

```bash
pipx install direwolf-dashboard
```

## Quick Start

1. **Check connectivity** to your Direwolf instance:

   ```bash
   direwolf-dashboard check
   ```

2. **Start the dashboard:**

   ```bash
   direwolf-dashboard serve
   ```

3. **Open your browser** at `http://<pi-address>:8080`

## Configuration

On first run, a default config is created at `~/.config/direwolf-dashboard/config.yaml`:

```yaml
station:
  callsign: "N0CALL"
  latitude: 0.0
  longitude: 0.0

direwolf:
  agw_host: "localhost"
  agw_port: 8000
  log_file: "/var/log/direwolf/direwolf.log"

server:
  host: "0.0.0.0"
  port: 8080

storage:
  db_path: "~/.local/share/direwolf-dashboard/packets.db"
  retention_days: 7

tiles:
  cache_dir: "~/.local/share/direwolf-dashboard/tiles"
  cache_mode: "lazy"
  tile_url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
  max_cache_mb: 500
```

Edit the station callsign and coordinates to see bearing/distance info. All settings can also be changed from the Settings panel in the web UI.

You can also specify a custom config path:

```bash
direwolf-dashboard -c /path/to/config.yaml serve
```

## Running as a systemd Service

```bash
sudo cp contrib/direwolf-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable direwolf-dashboard
sudo systemctl start direwolf-dashboard
```

Or use the included install script:

```bash
sudo bash contrib/install.sh
```

> [!NOTE]
> Edit the service file if your `direwolf-dashboard` binary is installed somewhere other than `/usr/local/bin/`, or if you need to run as a different user.

## CLI Reference

| Command | Description |
|---------|-------------|
| `direwolf-dashboard serve` | Start the web server |
| `direwolf-dashboard check` | Validate config and test Direwolf connectivity |
| `direwolf-dashboard version` | Show version |
| `direwolf-dashboard -c PATH serve` | Use a custom config file |

## Architecture

Single async Python process using FastAPI + uvicorn:

```
  Direwolf AGW Socket ──► AGW Reader ──┐
          (TCP:8000)                    ├──► Packet Processor ──► async queue
  Direwolf Log File ────► Log Tailer ──┘         │                    │
                                                  │               ┌───┴───┐
                                                  │               │       │
                                              SQLite DB     WebSocket Broadcast
                                              (7-day)        (live clients)
                                                  │               │
                                              REST API ◄──── FastAPI Server
                                              Tile Proxy      Static Files
```

- **AGW Reader** — connects to Direwolf's AGWPE interface, distinguishes TX (`T` frames) from RX (`U` frames)
- **Log Tailer** — async tail -f with log rotation detection, extracts audio levels and raw console output
- **Packet Processor** — merges both data sources, parses APRS via `aprslib`, computes bearing/distance, formats APRSD-style compact log
- **Storage** — SQLite in WAL mode with automatic housekeeping
- **Tile Proxy** — caches OpenStreetMap tiles to disk with lazy or pre-download modes

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Run the server locally
direwolf-dashboard serve
```

## Project Structure

```
direwolf-dashboard/
├── pyproject.toml
├── contrib/
│   ├── direwolf-dashboard.service   # systemd unit file
│   └── install.sh                   # service install helper
├── src/direwolf_dashboard/
│   ├── __init__.py
│   ├── __main__.py                  # python -m entry point
│   ├── agw.py                       # AGW/AGWPE socket reader
│   ├── cli.py                       # Click CLI commands
│   ├── config.py                    # YAML config management
│   ├── log_tailer.py                # Async log file tailer
│   ├── processor.py                 # Packet processing + formatting
│   ├── server.py                    # FastAPI app, REST API, WebSocket
│   ├── storage.py                   # SQLite storage layer
│   ├── tile_proxy.py                # Map tile caching proxy
│   └── static/                      # Web UI (HTML, CSS, JS, Leaflet)
└── tests/                           # 108 tests
```
