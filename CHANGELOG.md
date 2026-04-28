# Changelog

All notable changes to this project are documented here.

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
