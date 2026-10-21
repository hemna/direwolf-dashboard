# Direwolf Dashboard

A lightweight, web-based live display of [Direwolf](https://github.com/wb2osz/direwolf) TNC activity. Designed to run on a **Raspberry Pi Zero 2W** — shows a live map of APRS stations and a scrolling packet log in your browser.

![Direwolf Dashboard](screenshot.png)

## Features

### Map & Stations
- **🔗 Shareable map links** — the URL follows the map as `#zoom/lat/lon` (e.g. `/#12/37.3400/-78.8500`, same format as haminfo's map); open or send it to restore that view. The current zoom is shown under the +/- buttons.
- **Live MapLibre map** with APRS symbol icons, callsign labels, and movement trails
- **Live stats overlay** — station count, packet count, packets/hour, and server uptime (togglable in Settings)
- **Animated RF activity** — transmit/receive arc animations with packet route visualization via digipeaters and IGates
- **Collapsible map legend** showing animation and trail symbols
- **Trail duration selector** — choose 1h, 2h, 6h, or 24h of station movement history
- **⛅ Weather Stations panel** (`W`) — sortable table of all weather stations heard with current temp, wind, humidity, and pressure; map popups for weather stations show current conditions inline
- **📋 Station List panel** (`S`) — sortable, filterable table of every station heard with live age, packet count, position, and one-click CSV export

### Packet Log
- **Scrolling packet log** formatted in [APRSD](https://github.com/craigerl/aprsd) compact style with color-coded TX/RX, callsigns, paths, and bearing/distance
- **Three-state packet log** — toggle between expanded (50/50 split), peek (3–4 rows), or hidden (full-screen map) with toolbar button or `L` key; state persists across sessions
- **Inline raw log toggle** — click any packet to expand the raw Direwolf log lines
- **📶 Audio level / signal quality bars** — RX packets with an audio level show a 5-bar strength indicator; top bars yellow/red for strong or overloaded signals
- **🎨 Configurable log colors** — all 8 color slots (RX, TX, type, comment, human info, bearing, distance, dim) editable via color pickers in Settings; saved to `localStorage`
- **🔍 Full-text search** — `Ctrl+F` focuses a live search box that filters the log by callsign, raw packet, comment, or human info

### APRS Messaging
- **💬 APRS Messages panel** (`M`) — all `MessagePacket` traffic grouped into per-conversation threads; threads addressed to your callsign highlighted blue and sorted first

### Filters
- **Callsign filter** — live filter log rows and map markers by callsign fragment
- **Type filter** — show only GPS, Message, Weather, Status, Object, or Telemetry packets
- **TX/RX filter** — show only transmitted or received packets
- **Full-text search** — search across raw packet data and all decoded fields simultaneously

### Tools
- **APRS packet decoder** (`Ctrl+K`) — paste any raw APRS string and see a structured decode
- **⌨ Keyboard shortcuts help** (`?`) — modal table of all keyboard shortcuts
- **GPX track download** — download a station's position history as a GPX file
- **📥 Packet export to CSV** — download all stored packets via `GET /api/packets/export` (Settings → Storage)
- **🏥 Health endpoint** — `GET /api/health` returns service status; suitable for systemd health checks and monitoring

### Data & Storage
- **Dual data sources** — connects to Direwolf's AGW socket (TX/RX distinction) and tails the log file (audio levels, decode stats)
- **SQLite storage** with configurable retention (default 7 days) and one-click database wipe from the UI

### Operations
- **All settings configurable via the web UI** — station info, default map zoom, Direwolf connection, retention, stats overlay
- **Configurable data directory** — single `data_dir` setting controls where all writable data lives; essential for readonly root filesystems (e.g. DigiPi)
- **Fully offline** — all assets (JS, CSS, MapLibre, basemap tiles, APRS symbol sprites) served locally; no CDN or external URLs
- **Single async Python process** — ~30–50 MB RAM, one systemd service

---

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- [Direwolf](https://github.com/wb2osz/direwolf) running with AGW enabled (default port 8000)

---

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

### Offline install (no internet on the Pi)

`pyproject.toml` pins `aprslib` to a fork commit (`hemna/aprs-python`),
so a fully offline `uv pip install -e .` needs that dependency cached
first. On any networked machine, build the fork's wheel once, then
install from it on the Pi (issue #92):

```bash
# 1) On a networked machine, build the aprslib fork wheel:
git clone https://github.com/hemna/aprs-python.git
cd aprs-python
git checkout ab9f9113831a2bd2948d6178c7ffc1c9c075a6af
uv build --wheel          # produces dist/aprslib-*.whl

# 2) Copy the wheel to the Pi (e.g. scp / USB), then install everything:
uv venv
uv pip install ./aprslib-*.whl
uv pip install -e .       # now resolves aprslib from the installed wheel
```

The same applies to the other git-hosted dependency if one is added
later: cache or wheel it before going offline.

### Development install

```bash
uv pip install -e ".[dev]"
```

---

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

3. **Configure your station** — click the gear icon (⚙ Settings) in the toolbar and enter your callsign, coordinates, and APRS symbol. You can also click **Import from Direwolf conf** to auto-populate these from your existing `direwolf.conf` file.

4. **Check connectivity** to your Direwolf instance (optional):

   ```bash
   .venv/bin/direwolf-dashboard check
   ```

### First Launch

On first launch, the dashboard creates a default config at `~/.config/direwolf-dashboard/config.yaml`. Out of the box:

- The map centers at 0,0 with zoom level 12 — open **Settings** to set your station coordinates and the map will center on your location
- If running on a **DigiPi**, the dashboard automatically imports your callsign, coordinates, and symbol from `/etc/direwolf/direwolf.conf` on first launch
- A **live stats overlay** appears in the top-left of the map showing station count, packets, packets/hour, and server uptime — toggle it off in Settings if you prefer a clean map
- The packet log starts in **expanded** mode on desktop and **hidden** mode on mobile — use the toggle button in the toolbar (or press `L`) to switch between expanded, peek, and hidden views

---

## Configuration

The config file lives at `~/.config/direwolf-dashboard/config.yaml`:

```yaml
# Root directory for all writable data (database).
# Defaults to ~/.local/share/direwolf-dashboard
# On DigiPi (readonly root FS), set to /tmp/direwolf-dashboard
data_dir: "~/.local/share/direwolf-dashboard"

station:
  latitude: 0.0
  longitude: 0.0
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

display:
  show_stats_overlay: true
```

All settings can be changed from the Settings panel in the web UI. You can also specify a custom config path:

```bash
direwolf-dashboard -c /path/to/config.yaml serve
```

### Configurable data directory

The `data_dir` setting controls where all writable data is stored. When `storage.db_path`
is left empty (the default), it resolves to `<data_dir>/packets.db`. You can still
override the path individually if needed.

> [!TIP]
> On **DigiPi** or any system with a readonly root filesystem, set `data_dir` to a ramdisk path like `/tmp/direwolf-dashboard`. The directory is created automatically on startup.

---

## Map region (offline basemap)

**Worldwide, automatic.** On first start the dashboard downloads the map
region around your station itself, wherever you are: Europe, Japan,
Australia, South America, anywhere. Nothing to build or copy by hand.

1. Set your position: `station.latitude` / `station.longitude` in the config,
   or drop a pin / pick your station in the UI ("My Location").
2. Start the dashboard with internet access once. In the background it extracts
   a square of `basemap.radius_km` (default **40 km**) around you, up to
   `basemap.maxzoom` (default **13**), from the first working entry of
   `basemap.sources`:
   1. the basemap-kit planet on cloud.hemna.com
      (`https://cloud.hemna.com/basemap-kit/archives/planet.pmtiles`, read with
      HTTP range requests);
   2. otherwise the daily [Protomaps](https://protomaps.com) planet build.

   A source that returns 404, can't be reached, or isn't a valid vector PMTiles
   archive is skipped. A source that stops below `basemap.maxzoom` (the hosted
   world planet is capped at a low zoom to keep hosting small) is only used when
   no full-detail source works; then you get a coarser map instead of none. The result is `<data_dir>/basemap/region.pmtiles` plus a
   `region.json` sidecar that records which source was used.
3. Reload the page. From then on the map is fully offline.

A dense city at the defaults is about 30-45 MB; rural areas are much smaller.
The download uses the `pmtiles` CLI if it's installed, otherwise a built-in
Python extractor (no extra install on the Pi). If the station later moves more
than a quarter of the radius, or you raise the radius or zoom, the region is
downloaded again on the next start.

```yaml
basemap:
  auto_fetch: true      # false = never download; use what's on disk
  radius_km: 40         # half-width of the square, km
  maxzoom: 13           # 14 roughly doubles the size; 15 again
  sources:              # tried in order; first valid archive wins
    - https://cloud.hemna.com/basemap-kit/archives/planet.pmtiles
    - protomaps          # = yesterday's build.protomaps.com planet
  # entries may also be other URLs or local .pmtiles paths
```

**No position or no internet?** The map still works. A bundled world map
(zoom 0-4, #144) covers every country, so you see coastlines, borders and
cities instead of a blank map. The dashboard retries the download every 5
minutes until it has a position, then every 6 hours after a network failure.
`GET /api/config` → `basemap.auto` shows the current state (`no_position`,
`fetching`, `done`, `failed`, ...) and `basemap.auto.source`, the archive the region came from.

If you set `basemap.region_archive` yourself, or copy a `region.pmtiles` without
`region.json` into the data dir, the dashboard keeps it and never overwrites it.

### Manual download (optional)

**Option A: fetch now, or on a networked machine:**

```bash
# same download as the automatic one; uses the pmtiles CLI if present,
# otherwise the built-in Python extractor
direwolf-dashboard basemap fetch
```

This extracts a square region around your configured `station.latitude` /
`station.longitude` (default `basemap.radius_km`, 40 km) from yesterday's Protomaps planet
build and writes `<data_dir>/basemap/region.pmtiles`. Customize with:

```bash
direwolf-dashboard basemap fetch \
  --bbox=-90,40,-88,42 \        # explicit box: MIN_LON,MIN_LAT,MAX_LON,MAX_LAT
  --maxzoom=15 \                # default basemap.maxzoom (13)
  --planet=https://build.protomaps.com/20261019.pmtiles \   # overrides basemap.sources
  --output=/tmp/basemap/region.pmtiles
```

To copy it to the Pi instead of fetching there, deliver the `.pmtiles` to
`<data_dir>/basemap/region.pmtiles` (e.g. `scp region.pmtiles pi@digipi.local:` →
`sudo mv region.pmtiles /tmp/direwolf-dashboard/basemap/`).

**Option B — build with basemap-kit's `scripts/build_regions.py`:**

```bash
# regions.json with one entry, e.g. central Virginia (~165 x 120 km, z0-15, ~48 MB):
#   {"schemaVersion":1,"regions":[{"id":"va-appomattox","name":"Central Virginia",
#     "maxzoom":15,"bounds":[-79.6,36.8,-78.1,37.9]}]}
python3 basemap-kit/scripts/build_regions.py --regions regions.json --out build \
  --planet https://build.protomaps.com/$(date -u +%Y%m%d).pmtiles
scp build/archives/va-appomattox.pmtiles pi@digipi.local:
```

Then on the Pi keep it somewhere persistent (the default `data_dir` under
`/tmp` is wiped on reboot) and point the config at it:

```yaml
basemap:
  region_archive: /home/pi/direwolf-dashboard/va-appomattox.pmtiles
```

Restart the dashboard; `/api/config` should report `basemap.region.name` as
`va-appomattox.pmtiles`. Keep archives well under the Pi's free space
(z15 roughly triples the size of z14).

**Serving stays LAN-offline.** The dashboard serves the archive at
`/basemap/region.pmtiles` (with HTTP Range support) from the data dir when
present, falling back to the vendored sample. A fetched
`<data_dir>/basemap/region.pmtiles` is picked up automatically while
`basemap.region_archive` is left at its default. You can also point
`basemap.region_archive` at an absolute path or a data-dir-relative file in the
config. `/api/config` reports the active archive under `basemap.region`
(`name` + `size`).

---

## Running as a systemd Service

The included service file at [`contrib/direwolf-dashboard.service`](contrib/direwolf-dashboard.service)
runs the dashboard from the project's virtual environment.

1. **Edit the service file** if your install path or user differs from the defaults
   (`/home/pi/direwolf-dashboard`, user `pi`):

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

   Or use the included install script (handles all of the above):

   ```bash
   sudo bash contrib/install.sh
   ```

3. **Verify it's running:**

   ```bash
   sudo systemctl status direwolf-dashboard
   # Service logs
   journalctl -u direwolf-dashboard -f
   ```

### Updating a running service

```bash
cd ~/direwolf-dashboard
git pull
uv pip install -e .
sudo systemctl restart direwolf-dashboard
```

### DigiPi — readonly root filesystem

DigiPi uses a readonly root filesystem to protect the SD card. The dashboard needs a
writable directory for its SQLite database. A ramdisk is mounted at
`/tmp` on DigiPi — point `data_dir` there:

1. **Create the config directory and file** (on a writable partition, e.g. `/home/pi`):

   ```bash
   mkdir -p /home/pi/.config/direwolf-dashboard
   cat > /home/pi/.config/direwolf-dashboard/config.yaml << 'EOF'
   # Writable ramdisk — survives reboots just fine because packets are re-heard
   data_dir: "/tmp/direwolf-dashboard"

   station:
     latitude: 0.0
     longitude: 0.0
     zoom: 12

   direwolf:
     agw_host: "localhost"
     agw_port: 8000
     log_file: "/var/log/direwolf/direwolf.log"

   server:
     host: "0.0.0.0"
     port: 8080

   storage:
     db_path: ""          # resolves to /tmp/direwolf-dashboard/packets.db
     retention_days: 7

   EOF
   ```

2. **Pass the config path via the service file** — add `--config` to `ExecStart`:

   ```ini
   ExecStart=/home/pi/direwolf-dashboard/.venv/bin/direwolf-dashboard \
       --config /home/pi/.config/direwolf-dashboard/config.yaml serve
   ```

   The service file already includes `MALLOC_ARENA_MAX=2` and `PYTHONMALLOC=malloc`
   to keep RSS low on the Pi Zero 2W's 512 MB RAM.

3. **Optional — add a health-check** in the service file to confirm the server is
   accepting requests before systemd reports the service as started:

   ```ini
   ExecStartPost=/bin/sh -c 'sleep 3 && curl -sf http://localhost:8080/api/health || exit 1'
   ```

> [!NOTE]
> Data stored in `/tmp` is **not persistent across reboots**. This is intentional for
> DigiPi — the ramdisk prevents repeated writes to the SD card. Station positions and
> packet history are rebuilt from live RF traffic after each reboot.

---

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `S` | Open / close Station List panel |
| `W` | Open / close Weather Stations panel |
| `M` | Open / close APRS Messages panel |
| `L` | Cycle packet log: expanded → peek → hidden |
| `Ctrl+F` / `Cmd+F` | Focus packet log search box |
| `Ctrl+K` | Open Decode APRS Packet |
| `?` | Show keyboard shortcuts help |
| `Esc` | Close any open modal |

Press `?` in the app to see the full list in a modal overlay.

---

## REST API Reference

All endpoints are under `/api/`.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/packets` | GET | Query stored packets (`since`, `callsign`, `type`, `limit`) |
| `/api/packets/export` | GET | Download all packets as CSV (`since`, `callsign`, `type`, `limit`) |
| `/api/messages` | GET | Query APRS MessagePackets (`since`, `callsign`, `limit`) |
| `/api/stations` | GET | All heard stations (includes `last_weather` for weather stations) |
| `/api/stations/positions` | GET | Callsign → lat/lon map |
| `/api/stations/tracks` | GET | Station position history |
| `/api/station/{callsign}` | GET | Single station details |
| `/api/station/{callsign}/gpx` | GET | Download station track as GPX |
| `/api/weather/{callsign}` | GET | Weather history for a station |
| `/api/health` | GET | Service health (`{"status":"ok","uptime_seconds":N}`) |
| `/api/stats` | GET | Packet/station counts |
| `/api/config` | GET/PUT | Read or update configuration |
| `/api/storage` | DELETE | Wipe the packet database (requires `{"confirm":true}`) |
| `/api/decode` | POST | Decode a raw APRS packet string |

### Packet export examples

```bash
# All packets (up to 10 000 rows)
curl http://localhost:8080/api/packets/export -o packets.csv

# One station, last 24 h
curl "http://localhost:8080/api/packets/export?callsign=WB4BOR" -o wb4bor.csv

# GPS packets only
curl "http://localhost:8080/api/packets/export?type=GPSPacket" -o gps.csv
```

---

## Architecture

Single async Python process using Starlette + uvicorn:

```
  Direwolf AGW Socket ──► AGW Reader ──┐
          (TCP:8000)                    ├──► Packet Processor ──► async queue
  Direwolf Log File ────► Log Tailer ──┘         │                    │
                                                  │               ┌───┴───┐
                                                  │               │       │
                                              SQLite DB     WebSocket Broadcast
                                              (7-day)        (live clients)
                                                  │               │
                                              REST API ◄──── Starlette Server
                                              Static Files
```

- **AGW Reader** — connects to Direwolf's AGWPE interface, distinguishes TX (`T` frames) from RX (`U` frames), extracts Via path from AGW headers
- **Log Tailer** — async tail -f with log rotation detection, extracts audio levels and raw console output
- **Packet Processor** — merges both data sources, parses APRS via `aprslib`, computes bearing/distance, formats APRSD-style compact log
- **Storage** — SQLite in WAL mode with automatic housekeeping and runtime wipe support
- **Stats Broadcaster** — pushes live statistics (station count, packets, uptime) to all connected clients every 10 seconds

---

## Development

```bash
# Clone and set up
git clone https://github.com/hemna/direwolf-dashboard.git
cd direwolf-dashboard
uv venv
uv pip install -e ".[dev]"

# Run tests
uv run pytest tests/ -v

# Lint
uvx ruff check .

# Run the server locally
.venv/bin/direwolf-dashboard serve
```

---

## Project Structure

```
direwolf-dashboard/
├── pyproject.toml
├── CHANGELOG.md
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
│   ├── routers.py                   # REST API + WebSocket routes
│   ├── server.py                    # Starlette app factory
│   ├── storage.py                   # SQLite storage layer
│   └── static/
│       ├── index.html               # Single-page app shell
│       ├── app.js                   # All client-side JS (vanilla, IIFE)
│       ├── style.css                # Dark/light theme CSS
│       └── symbols/                 # Vendored APRS symbol sprites (offline)
└── tests/                           # 244 tests (pytest)
```

---

## License

by [WB4BOR](https://github.com/hemna)
