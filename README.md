# Direwolf Dashboard

A lightweight, web-based live display of [Direwolf](https://github.com/wb2osz/direwolf) TNC activity. Designed to run on a **Raspberry Pi Zero 2W** — shows a live map of APRS stations and a scrolling packet log in your browser.

![Direwolf Dashboard](screenshot.png)

## Features

- **Live Leaflet map** with APRS symbol icons, callsign labels, and movement trails
- **Live stats overlay** — station count, packet count, packets/hour, tile cache size, and server uptime displayed on the map (togglable in Settings)
- **Animated RF activity** — transmit/receive arc animations on the map with packet route visualization via digipeaters and IGates
- **Collapsible map legend** showing animation and trail symbols
- **Scrolling packet log** formatted in [APRSD](https://github.com/craigerl/aprsd) compact style with color-coded TX/RX, callsigns, paths, and bearing/distance
- **Three-state packet log** — toggle between expanded (50/50 split), peek (3-4 rows), or hidden (full-screen map) with a toolbar button or the `L` keyboard shortcut; state persists across sessions
- **Dual data sources** — connects to Direwolf's AGW socket (TX/RX distinction) and tails the log file (audio levels, decode stats)
- **Inline raw log toggle** — click any packet to expand the raw Direwolf log lines
- **Filters** — by callsign, packet type, TX/RX direction
- **Trail duration selector** — choose 1h, 2h, 6h, or 24h of station movement history
- **Mobile-responsive toolbar** — hamburger menu on narrow screens with all filter controls accessible
- **APRS symbol picker** — visual grid of all primary and alternate APRS symbols for station configuration
- **Import from Direwolf config** — auto-imports callsign, coordinates, and symbol from your `direwolf.conf`
- **Configurable data directory** — single `data_dir` setting controls where all writable data lives; essential for readonly root filesystems (e.g. DigiPi)
- **SQLite storage** with configurable retention (default 7 days) and one-click database wipe from the UI
- **Tile caching proxy** — lazy on-demand caching or pre-download for offline use, with automatic retry on transient errors, concurrency limiting, and tile cache size displayed in Settings
- **All settings configurable via the web UI** — station info, default map zoom, Direwolf connection, retention, tile cache, stats overlay
- **Single async Python process** — ~30-50MB RAM, one systemd service

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- [Direwolf](https://github.com/wb2osz/direwolf) running with AGW enabled (default port 8000)

## Installation

### Install uv (if not already installed)

[uv](https://docs.astral.sh/uv/) is a fast Python package manager. Install it with:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

After install, restart your shell or run `source ~/.local/bin/env` so that `uv` is on your PATH.

### Clone and install

```bash
git clone https://github.com/hemna/direwolf-dashboard.git
cd direwolf-dashboard

# Create a virtual environment and install
uv venv
uv pip install -e .
```

This creates a `.venv` directory inside the project with the `direwolf-dashboard` command available at `.venv/bin/direwolf-dashboard`.

### Development install

```bash
uv pip install -e ".[dev]"
```

## Quick Start

1. **Start the dashboard:**

   ```bash
   cd direwolf-dashboard
   .venv/bin/direwolf-dashboard serve
   ```

   Or activate the venv first:

   ```bash
   source .venv/bin/activate
   direwolf-dashboard serve
   ```

2. **Open your browser** at `http://<pi-address>:8080`

3. **Configure your station** — click the gear icon (Settings) in the toolbar and enter your callsign, coordinates, and APRS symbol. You can also click **Import from Direwolf conf** to auto-populate these from your existing `direwolf.conf` file.

4. **Check connectivity** to your Direwolf instance (optional):

   ```bash
   .venv/bin/direwolf-dashboard check
   ```

### First Launch

On first launch, the dashboard creates a default config at `~/.config/direwolf-dashboard/config.yaml`. Out of the box:

- The map centers at 0,0 with zoom level 12 — open **Settings** to set your station coordinates and the map will center on your location
- If running on a **DigiPi**, the dashboard automatically imports your callsign, coordinates, and symbol from `/etc/direwolf/direwolf.conf` on first launch
- A **live stats overlay** appears in the top-left of the map showing station count, packets, packets/hour, tile cache size, and server uptime — toggle it off in Settings if you prefer a clean map
- The packet log starts in **expanded** mode on desktop and **hidden** mode on mobile — use the toggle button in the toolbar (or press `L`) to switch between expanded, peek, and hidden views
- Map tiles are cached lazily on first view — for offline/field use, switch to **Pre-download** mode in Settings to cache tiles for your area ahead of time

## Configuration

The config file lives at `~/.config/direwolf-dashboard/config.yaml`:

```yaml
# Root directory for all writable data (database + tile cache).
# Defaults to ~/.local/share/direwolf-dashboard
# On DigiPi (readonly root FS), set to /tmp/direwolf-dashboard
data_dir: "~/.local/share/direwolf-dashboard"

station:
  callsign: "N0CALL"
  latitude: 0.0
  longitude: 0.0
  symbol: "-"
  symbol_table: "/"
  zoom: 12

direwolf:
  agw_host: "localhost"
  agw_port: 8000
  log_file: "/var/log/direwolf/direwolf.log"

server:
  host: "0.0.0.0"
  port: 8080

storage:
  # Defaults to <data_dir>/packets.db if left empty
  db_path: ""
  retention_days: 7

tiles:
  # Defaults to <data_dir>/tiles if left empty
  cache_dir: ""
  cache_mode: "lazy"
  tile_url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
  max_cache_mb: 500

display:
  show_stats_overlay: true
```

All settings can be changed from the Settings panel in the web UI. You can also specify a custom config path:

```bash
direwolf-dashboard -c /path/to/config.yaml serve
```

### Configurable data directory

The `data_dir` setting controls where all writable data is stored. When `storage.db_path` or `tiles.cache_dir` are left empty (the default), they resolve to `<data_dir>/packets.db` and `<data_dir>/tiles` respectively. You can still override each path individually if needed.

> [!TIP]
> On **DigiPi** or any system with a readonly root filesystem, set `data_dir` to a ramdisk path like `/tmp/direwolf-dashboard`. The directory is created automatically on startup.

## Running as a systemd Service

The included service file runs the dashboard from the project's virtual environment.

1. **Edit the service file** if your install path or user differs from the defaults (`/home/pi/direwolf-dashboard`, user `pi`):

   ```bash
   vi contrib/direwolf-dashboard.service
   ```

2. **Install and start the service:**

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

3. **Verify it's running:**

   ```bash
   sudo systemctl status direwolf-dashboard
   ```

### Updating

To update a running installation:

```bash
cd ~/direwolf-dashboard
git pull
uv pip install -e .
sudo systemctl restart direwolf-dashboard
```

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `L` | Cycle packet log view: expanded → peek → hidden |

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

- **AGW Reader** — connects to Direwolf's AGWPE interface, distinguishes TX (`T` frames) from RX (`U` frames), extracts Via path from AGW headers
- **Log Tailer** — async tail -f with log rotation detection, extracts audio levels and raw console output
- **Packet Processor** — merges both data sources, parses APRS via `aprslib`, computes bearing/distance, formats APRSD-style compact log
- **Storage** — SQLite in WAL mode with automatic housekeeping and runtime wipe support
- **Tile Proxy** — caches OpenStreetMap tiles to disk with lazy or pre-download modes, retry with exponential backoff on transient errors, connection concurrency limiting, and automatic zero-byte tile recovery
- **Stats Broadcaster** — pushes live statistics (station count, packets, tile cache size, uptime) to all connected clients every 10 seconds

## Development

```bash
# Clone and set up
git clone https://github.com/hemna/direwolf-dashboard.git
cd direwolf-dashboard
uv venv
uv pip install -e ".[dev]"

# Run tests
uv run pytest tests/ -v

# Run the server locally
.venv/bin/direwolf-dashboard serve
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
│   ├── decoder.py                   # Manual APRS decode fallback
│   ├── lifecycle.py                 # Service container, startup/shutdown
│   ├── log_tailer.py                # Async log file tailer
│   ├── processor.py                 # Packet processing + formatting
│   ├── routers.py                   # REST API + WebSocket + tile proxy
│   ├── server.py                    # FastAPI app factory
│   ├── storage.py                   # SQLite storage layer
│   ├── tile_proxy.py                # Map tile caching proxy
│   └── static/                      # Web UI (HTML, CSS, JS, Leaflet)
└── tests/                           # 161 tests
```

## License

by [WB4BOR](https://github.com/hemna)
