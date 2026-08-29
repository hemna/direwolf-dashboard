# Changelog

All notable changes to this project are documented here.

## [Unreleased]

### Added
- **`GET /api/health` endpoint** — returns `{"status": "ok", "uptime_seconds": N}` with
  HTTP 200 when all services are running.  Returns HTTP 503 with `{"status": "degraded",
  "issues": [...]}` when the AGW reader is disconnected or the log tailer is inactive.
  Suitable for systemd `ExecStartPost` health-checks, load-balancer probes, and monitoring
  tools. Closes #30.

### Fixed
- **Housekeeping sleep jitter** — `_housekeeping_loop` now sleeps for 3600 s ± 5 %
  instead of a fixed hour, preventing predictable SD card write bursts on DigiPi
  systems that always restart at the same wall-clock time. Closes #29.
- **`asyncio.get_event_loop()` replaced with `asyncio.get_running_loop()`** in `tile_proxy.py` — eliminates `DeprecationWarning` on Python 3.10+ and `RuntimeError` on Python 3.12 when called from within a running event loop. Both call sites in `get_cache_stats()` and `_check_cache_budget()` are affected. Closes #20.
- **APRS symbol sprites are now vendored locally** — previously loaded from
  `raw.githubusercontent.com`, causing broken station icons on offline DigiPi
  deployments. Both sprite sheets (`aprs-symbols-24-0.png`,
  `aprs-symbols-24-1.png`) are now served from `/static/symbols/` and no
  longer require an internet connection.
- **XSS vulnerability in station popup eliminated** — popup action buttons
  (`Set as My Position`, `Remove as My Position`, `View Weather`, `Download GPX`,
  `Remove Pin`) previously embedded callsign values directly in `onclick`
  attribute strings. A crafted APRS callsign containing a single-quote could
  break out of the string argument and execute arbitrary JavaScript when a user
  opened the popup. All buttons now use `addEventListener` via DOM construction.
- **DB transaction rollback on storage errors** — a failed `insert_packet` or
  `upsert_station` call (e.g. disk full on DigiPi SD card) now triggers an
  explicit `rollback()` so the aiosqlite connection is always in a clean state
  for the next packet. The packet is still broadcast to WebSocket clients even
  when storage fails.

### Performance
- **Cache `my_position` to eliminate per-packet DB reads** — `resolve_my_position()`
  now caches the resolved coordinates on `DirewolfServices` and skips the DB round-trip
  on every packet. The cache is invalidated (a) when `my_position` is updated via the
  config API, and (b) when a position packet arrives from the callsign being tracked as
  "my position". On a busy APRS channel this removes hundreds of `get_my_position` /
  `get_station` DB calls per minute.

### Refactored
- **Compact log HTML moved to frontend** — `format_compact_log()` in `processor.py`
  and the bearing HTML mutation in `lifecycle._broadcast_consumer` are removed.
  `renderCompactLog(packet)` in `app.js` now builds the log row HTML using CSS
  variables (`--log-rx`, `--log-type`, `--log-comment`, etc.), so colors
  automatically respect the active theme. The `compact_log` DB column is retained
  for backward compatibility but no longer written with HTML. Closes #28.

## [1.0.8] - 2026-08-29

### Fixed
- **Tile proxy `os.walk` no longer blocks the event loop** — `get_cache_stats()`
  and cache eviction now run in a thread-pool executor, preventing SD card I/O
  from stalling packet processing on Raspberry Pi. Stats are cached for 60 s.
- **Oversized AGW frames no longer crash the reader** — frames with
  `data_len > 65536` bytes now raise `ConnectionError`, triggering the existing
  reconnect loop instead of attempting a multi-megabyte heap allocation.
- **`DELETE /api/storage` requires explicit confirmation** — the wipe endpoint
  now requires `{"confirm": true}` in the JSON body, preventing accidental data
  loss from errant DELETE requests (e.g. misconfigured reverse proxies).
- **`_qint` query-parameter helper uses correct `Optional[int]` types** — fixes
  a type annotation inconsistency where `min_val`/`max_val` were typed as `int`
  but defaulted to `None`.
- **`Config()` no longer touches the filesystem** — `_resolve_data_dir()` was
  called inside `Config.__post_init__`, meaning every bare `Config()` construction
  (tests, CLI check, default-config factory) performed `os.makedirs` and a write
  test. It is now only called once inside `load_config()`.

## [1.0.7] - 2026-05-29

### Added
- **Collapsible overlays** — all map overlay panels (Stats, Filters, My Location,
  GPX Overlay, Legend) can now be collapsed and expanded by clicking their header.
  Collapse state is persisted in localStorage and restored on page refresh.
- **Map view persistence** — zoom level and map center are saved to localStorage
  on every pan/zoom and restored on page refresh. First visit still uses the
  normal centering priority chain (my position → config → most-recent station).
- **Legend arrow fix** — the legend header arrow now correctly shows ▲ when
  expanded and ▼ when collapsed (was previously a static ▼).
- **Station path visualization** — clicking a station marker draws the packet
  path on the map: orange dashed lines from station through each digipeater to
  the iGate, using live station positions. Path hops are also shown as text in
  the popup (e.g. `→ KD4ATF-3 › APPOMX`).
- **Distance to My Position** — when My Position is set, clicking any station
  draws a blue dashed line to your position with a floating distance label in
  both km and miles. Distance is also shown in the popup.
- **Log row highlight** — clicking a station highlights all matching rows in the
  Live Packet Log with an orange left-border and scrolls to the first match.
  Closing the popup clears the highlight.

### Changed
- **RAM optimizations** for Raspberry Pi Zero 2W (RSS reduced from ~49 MB to
  ~35 MB, packages from 63 → 12):
  - Replaced FastAPI with Starlette directly — removes Pydantic, pydantic-core,
    annotated-types (the only Pydantic model in the codebase was a 2-line
    `DecodeRequest` with a single field)
  - Replaced `httpx` tile fetching with `asyncio.to_thread` + stdlib `urllib`
  - Added `PYTHONMALLOC=malloc` and `MALLOC_ARENA_MAX=2` to the systemd service
    so Python returns freed memory to the OS instead of hoarding it
  - Removed unused `aprsd` dependency and switched to `uvicorn` (no extras),
    cutting installed packages from 63 → 12
  - Disabled FastAPI OpenAPI/docs schema generation (not needed in production)
  - Capped uvicorn thread pool to 2 workers; use stdlib asyncio loop and h11
  - Reduced broadcast queue depth from 500 → 50 entries
  - Set SQLite `PRAGMA cache_size = -512` (512 KB vs 2 MB default)
  - Tile stats `os.walk` now cached for 60 s instead of running every 10 s
  - Single-station API endpoint now queries by callsign directly instead of
    fetching all stations and doing a linear scan

## [1.0.6] - 2026-05-09

### Added
- **Clear data FAB** — trash icon button on the map that wipes all stored
  packets, stations, and resets My Position. Includes a confirmation modal
  with a "Don't ask me again" checkbox (persisted in localStorage).
- **Retention days setting** — configurable in Settings > Storage to control
  how long packets are kept before housekeeping deletes them.

### Fixed
- **Housekeeping now purges stale stations** — the periodic housekeeping loop
  deleted old packets and weather reports but never touched the stations table,
  causing the map to display all stations ever heard regardless of the
  retention_days setting. Stations with `last_seen` older than the retention
  window are now deleted alongside packets.

### Changed
- **Packet log toggle simplified** — the 3-state cycle (expanded/peek/hidden)
  is now a simple show/hide toggle. Panel defaults to 1/3 viewport height on
  first show, remembers the last dragged position via localStorage.
- **Timing logs demoted to DEBUG** — all `[TIMING]` instrumentation in
  lifecycle, AGW, and processor modules now logs at DEBUG level instead of
  INFO, significantly reducing log noise on the Pi.

### Fixed
- Replace `assert` with proper HTTP 503 responses in all route handlers —
  assertions are stripped in optimized Python builds.
- Data directory fallback for readonly filesystems — if `~/.local/share` is
  not writable (e.g. DigiPi), automatically falls back to
  `/tmp/direwolf-dashboard`.
- Guard `os.makedirs` against empty dirname in storage init.
- Fix dict mutation in `PUT /api/config` when extracting `my_position`.
- Track tile preload background tasks for proper shutdown cancellation.
- Add tile coordinate bounds validation (z: 0-19, x/y within range).
- Remove dead `_disconnect_event` code from WebSocket handler.
- Add debug logging to silent bearing/distance calculation exceptions.
- Move inline imports (`aprslib`, `re`, `math`, `haversine`) to module top
  level for clarity and slight performance improvement.

## [1.0.5] - 2026-04-30

### Added
- **Weather modal** — tap a weather station icon and click "View Weather" to
  see current conditions (temperature, dewpoint, humidity, pressure, wind, rain)
  and historical Chart.js line graphs of temperature/dewpoint and barometric
  pressure over time. Vendored Chart.js 4.4.1 for offline use.
- **Weather data storage** — new `weather_reports` table stores parsed weather
  fields on ingest, including computed dewpoint (Magnus formula). Housekeeping
  and DB wipe include the new table.
- `GET /api/weather/{callsign}` endpoint for historical weather data.
- **Center FAB** — floating GPS crosshair button on the map that flies to the
  user's configured My Position.
- **About modal** — shows app name, version, author (WB4BOR), DigiPi credit
  (KM6LYW), GitHub link, and APRS Chat promo. Triggered from toolbar logo or
  footer link.
- **Changelog modal** — view the formatted changelog from within the app.

## [1.0.4] - 2026-04-28

### Added
- **My Location map overlay** — new panel (top-right, below filters) showing
  the user's configured position: station callsign or dropped pin with lat/lon
  coordinates. Includes center-on-me and clear buttons. Toggleable via
  Settings > Map Display.
- `show_my_location_overlay` display setting (defaults to on).

### Changed
- **my_position moved from YAML config to SQLite DB** — my_position is runtime
  state set from the web UI, not a deploy-time setting. It now lives in the
  `config` table in SQLite instead of being written to the YAML file. This is
  especially important on DigiPi's readonly root filesystem.
- `resolve_my_position()` reads from DB first, falls back to the static
  `station.latitude`/`station.longitude` from YAML if nothing is set.
- `MyPositionConfig` dataclass removed; legacy YAML files with `my_position`
  are silently stripped on load.

### Fixed
- Clearing my_position via the UI (`null` payload) now works correctly — fixed
  a bug where `None` value was indistinguishable from a missing key.
- Pipeline timing instrumentation and wsproto WebSocket backend.
- Reset my_position on DB wipe.
- TX animation fallback when myPos is null.

## [1.0.3] - 2026-04-24

### Added
- **Live stats overlay on map** — stations, packets, pkts/hr, tiles, uptime.
- Tile cache size displayed in settings.
- Configurable `data_dir` for all writable data (DB, tile cache).
- Wipe database button in settings with in-app confirmation modal.
- Light/dark theme with embed support for digipi-web integration.

### Fixed
- Skip zero-byte cached tiles and re-fetch from upstream.
- Limit concurrent upstream tile connections and improve retry logic.
- Retry failed tile fetches on transient errors.

## [1.0.2] - 2026-04-22

### Added
- **GPX overlay** — load GPX files onto the map with route display, waypoint
  markers, and stats (distance, elevation, track/waypoint counts). Vendored
  leaflet-gpx v2.1.2 for fully offline use.
- Setting to show/hide GPX overlay control.
- Packet Log settings section with show timestamps toggle.
- Map legend moved to bottom-left.
- Copy and decode buttons in packet log rows.

### Fixed
- Align checkbox labels left in Map Display settings.
- Close settings modal immediately on save.

## [1.0.1] - 2026-04-18

### Added
- **Packet decode panel** — decode raw APRS packets with color-coded field
  annotations and path station lookups.
- Clean APRS string display in log raw view with copy button.

### Fixed
- Strip trailing `\r` and null bytes from APRS strings.
- Clipboard copy fallback for non-HTTPS contexts.
- Remove raw_log (AGW-formatted lines) from the pipeline — use clean APRS
  string in `raw_packet` instead.

## [1.0.0] - 2026-04-15

### Added
- Initial release.
- Real-time APRS packet display via AGW/AGWPE protocol and Direwolf log tailing.
- Leaflet map with APRS symbol icons, station tracks, and popups.
- SQLite storage with configurable retention and housekeeping.
- Offline tile proxy with lazy caching for Raspberry Pi / DigiPi use.
- Settings modal for station, Direwolf, server, storage, and tile configuration.
- YAML config file with sensible defaults.
- WebSocket live updates for packets and stats.
- TX/RX arc animations on the map.
- APRS symbol preview and picker.
- Bearing and distance enrichment relative to user position.
