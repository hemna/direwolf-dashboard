# Config Simplification: Remove direwolf.conf, Add "My Position"

**Date:** 2026-04-16
**Status:** Draft

## Summary

Remove all direwolf.conf reading/parsing from the dashboard. Remove callsign,
symbol, and overlay from configuration. Make latitude/longitude optional for
initial map centering. Replace the "home station" concept with a user-driven
"my position" feature that can either track a station heard on-air or be a
manually dropped pin.

## Motivation

The dashboard should not need to read direwolf.conf. The callsign and symbol
are properties of stations discovered from packets, not configuration values.
The user's position on the map should be an explicit choice made through the
UI, not derived from a config file.

## Config Changes

### Removed Fields

- `station.callsign`
- `station.symbol`
- `station.symbol_table`
- `direwolf.conf_file`

### Modified Fields

- `station.latitude` -- optional, used only for initial map centering
- `station.longitude` -- optional, used only for initial map centering

### New Fields

```yaml
station:
  latitude: 39.8
  longitude: -98.6
  zoom: 12
  # --- Managed by the UI. Do not edit manually. ---
  my_position:
    type: null          # "station", "pin", or null
    callsign: null      # set when type=station (position resolved from DB)
    latitude: null      # set when type=pin (fixed position)
    longitude: null     # set when type=pin (fixed position)
```

### Config Rules

- `my_position.type: "station"` + `callsign` -- position resolved from DB at
  runtime, auto-tracks as new packets arrive from that callsign
- `my_position.type: "pin"` + `latitude/longitude` -- fixed position stored in
  config
- `my_position.type: null` -- no "me" set, no bearing/distance in logs

### Migration

Existing config.yaml files with removed fields (callsign, symbol, conf_file,
symbol_table) are silently ignored on load. The new `my_position` key is added
with null defaults.

## Map Initialization

### Startup Center Logic (priority order)

1. `my_position` resolved coordinates:
   - `type: station` -- look up callsign's latest position from DB
   - `type: pin` -- use lat/lon from config
2. `station.latitude` / `station.longitude` if present
3. Most recently seen station with a position in the DB
4. Random coordinates at zoom level 3-4 (cold start)

### Cold Start Modal

When no position data exists (option 4 above):

- Display a modal over the map: "Listening for APRS packets... The map will
  center on the first station heard with a position report."
- User can dismiss the modal (click X or backdrop).
- On dismiss, a toast notification appears at the top: "Waiting for first
  position packet..."
- When the first position packet arrives via WebSocket:
  - Dismiss the modal (if still open) or the toast
  - Fly/pan the map to the new position

### Toast Component

A simple fixed-position bar at the top of the map area, styled consistently
with the dark theme. Not a full modal -- just a small notification strip that
passively indicates "waiting for position" state.

## "My Position" Feature

### Setting "Me" via Station Selection

- Click a station marker on the map to open its popup
- Popup includes a "Set as My Position" button
- Saves `my_position: { type: "station", callsign: "<callsign>" }` via
  `PUT /api/config`
- Map centers on that station
- As new position packets arrive from that callsign, the bearing/distance
  reference point auto-updates
- The station marker gets a visual indicator (e.g., ring or highlight)

### Setting "Me" via Dropped Pin

Two triggers:

- **Toolbar button:** "Drop Pin" button enters pin-placement mode. Next tap
  on the map places the pin. Tap button again or press Escape to cancel.
- **Long-press on map:** ~500ms touch-and-hold places pin directly (also
  works as right-click on desktop).

Saves `my_position: { type: "pin", latitude: <lat>, longitude: <lon> }` via
`PUT /api/config`.

Displays a distinct pin marker (not an APRS symbol -- a generic "you are
here" marker).

### Clearing "Me"

- `type: station` -- click the station marker popup -> "Remove as My Position"
- `type: pin` -- click the pin marker popup -> "Remove My Position"
- Sets `my_position.type` back to null in config
- Bearing/distance stops appearing in new log entries
- Visual indicator removed

### Bearing/Distance in Logs

- When `my_position` is set, position packets are enriched with bearing and
  distance from "me":
  - `type: station` -- reference coords from that station's latest DB position
  - `type: pin` -- reference coords from config
- When `my_position` is null, bearing/distance fields are omitted
- Bearing/distance computation moves from `processor.py` to the broadcast
  consumer in `server.py` (since it needs DB access for station lookups)

## Code Removal

### Backend (config.py)

- `StationConfig.callsign`, `.symbol`, `.symbol_table` fields
- `DirewolfConfig.conf_file` field
- `parse_direwolf_conf()` function
- `_parse_pbeacon()` function
- `DIREWOLF_SYMBOL_NAMES` dict
- Auto-import from `DIGIPI_DIREWOLF_CONF` on first launch

### Backend (server.py)

- `POST /api/import-direwolf-conf` endpoint
- Home station concept in broadcast consumer
- Direct config lat/lon usage in `_enrich_with_bearing()`

### Backend (processor.py)

- `station_lat` / `station_lon` constructor parameters
- Bearing/distance computation (moves to server.py broadcast consumer)

### Frontend (index.html / app.js)

- Callsign, symbol, symbol_table settings fields
- Symbol picker modal entirely (`initSymbolPicker()`, `renderSymbolGrid()`)
- "Import from Direwolf conf" button and `importDirewolfConf()` function
- Home marker creation in `initMap()`

### Tests

- All direwolf.conf parsing tests in `test_config.py`
- Tests referencing callsign/symbol in config context

## Code Retained (Modified)

- `createSymbolIcon()` -- still needed for station markers from packets
- Symbol sprite sheets -- still needed for station markers
- Settings modal -- simplified (lat/lon, zoom, direwolf connection, storage,
  tiles)
- Station markers, tracks, and route lines -- all derived from packet data

## New Components

### Backend

- `MyPositionConfig` dataclass with `type`, `callsign`, `latitude`,
  `longitude` fields (all defaulting to null)
- `StationConfig` updated to include `my_position: MyPositionConfig`
- Bearing/distance enrichment in broadcast consumer resolves "me" coords:
  - `type: station` -- query DB for latest position of that callsign
  - `type: pin` -- read lat/lon from config
  - `null` -- skip enrichment
- `PUT /api/config` updated to handle `my_position` updates

### Frontend

- **"Drop Pin" toolbar button** + pin-placement mode (click button, then tap
  map to place; click again or Escape to cancel)
- **Long-press handler** on map for pin placement (~500ms touch-and-hold;
  also works as right-click on desktop)
- **"Set as My Position"** button in station marker popups
- **"Remove as My Position"** / **"Remove My Position"** in popups when
  already set
- **Pin marker** -- distinct from APRS symbols, generic "you are here" style
- **Visual indicator** on "me" station marker (ring, highlight, or border)
- **Cold-start modal** -- "Listening for APRS packets... The map will center
  on the first station heard with a position report."
- **Toast notification** -- replaces modal on dismiss, "Waiting for first
  position packet...", auto-dismissed on first position packet
- **Map fly-to** on first position packet when in cold-start state
- **Startup logic** updated to follow priority: my_position > config lat/lon >
  most recent DB station > random coords
