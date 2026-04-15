# Map Transmit/Receive Arc Animations

**Date**: 2026-04-15
**Status**: Approved

## Summary

Add real-time transmit and receive arc animations to the direwolf dashboard map, ported from the haminfo dashboard. When a station transmits, expanding red (or gray for internet-origin) arcs appear at the transmitter. When a station receives, collapsing green arcs appear at the receiver. Blue dashed polylines show the packet route through digipeaters and igates. A collapsible legend explains the visual elements.

## Context

The haminfo dashboard at `aprsdashboard.hemna.com` has a map feature that visualizes APRS packet flow with animated arcs and route lines. The direwolf dashboard already has a Leaflet map with station markers, track polylines, APRS symbol sprites, and real-time WebSocket packet delivery. This design adds the missing animation layer.

## Decisions

- **Full fidelity animations**: 3-tier SVG arc animations matching haminfo, not a lighter-weight alternative. The Pi Zero 2W can handle a few temporary DOM elements per packet.
- **Full route polylines**: Draw the complete packet path (TX -> digipeaters -> igate), not just endpoint arcs.
- **Red/gray TX distinction**: Red arcs for RF packets, dark gray for TCPIP/internet-origin, detected via APRS path analysis.
- **Both TX and RX at home station**: Show TX arcs when the home station transmits and RX arcs when it receives.
- **Direct port into app.js**: No separate module file or canvas-based rendering. The ~300 lines of animation code fit naturally in the existing single-file architecture.

## Design

### 1. Data Flow

```
AGW Packet (with raw APRS string)
  -> processor.py (extract raw_path preserving * markers)
  -> WebSocket broadcast (packet event with raw_path field)
  -> app.js handlePacketEvent()
  -> parsePath(raw_path) extracts digipeaters, igate, isTcpip
  -> Animation orchestration based on tx flag
```

Packets arrive via the existing WebSocket `packet` event. Each packet already includes `from_call`, `to_call`, `latitude`, `longitude`, `tx`, `raw_packet`, and `path`. The new `raw_path` field provides the unmodified APRS path string with `*` markers preserved on used digipeaters.

### 2. APRS Path Parsing

A new `parsePath(rawPathString)` function parses the raw APRS digipeater path and returns:

```javascript
{
  digipeaters: ['N3ABC'],    // Used digipeaters (had * marker), excluding generic aliases
  igate: 'N3LLO-10',        // Callsign after qA* construct, or null
  isTcpip: false             // true if TCPIP, qAC, or qAU in path
}
```

**Generic aliases excluded from route drawing** (not real stations with positions):
```
WIDE, WIDE1, WIDE2, WIDE3, WIDE1-1, WIDE2-1, WIDE2-2, WIDE3-3,
RELAY, TRACE, TCPIP, CQ, QST, APRS, RFONLY, NOGATE
```

The path string comes from the raw APRS packet. Example raw packet:
```
N3ABC>APRS,N3XYZ*,WIDE1-1,qAR,N3LLO-10:!4003.50N/07507.23W>
```
Path portion: `N3XYZ*,WIDE1-1,qAR,N3LLO-10`
- `N3XYZ*` -> used digipeater (has `*`)
- `WIDE1-1` -> generic alias, skip
- `qAR` -> q-construct, next callsign is igate
- `N3LLO-10` -> igate

### 3. Station Position Cache

A JavaScript object `stationPositionCache` maps callsigns to `{lat, lng}` coordinates. Limited to 1000 entries with oldest-eviction.

**Bootstrap**: On page load, `GET /api/stations/positions` returns all known station positions:
```json
{
  "N3ABC": {"lat": 40.058, "lng": -75.121},
  "N3LLO-10": {"lat": 39.952, "lng": -75.164},
  ...
}
```

**Ongoing updates**: Every incoming WebSocket packet with position data updates the cache. The existing `stations` object (visible markers) is also consulted.

**Position resolution order** for route drawing:
1. `stations[callsign]` (visible marker on map)
2. `stationPositionCache[callsign]` (any previously seen station)
3. Skip segment if position unknown

### 4. SVG Arc Icons

#### TX Arcs (Outward Expanding)

`createTransmitArcIcon(index, color)` creates an SVG arc icon as a data URI:

- **3 sizes**: index 1 (48px, radius 17), index 2 (56px, radius 24), index 3 (64px, radius 31)
- **Arc geometry**: Two curved SVG `<path>` elements (left + right arcs) using SVG arc commands, ~50 degrees of coverage per side
- **Colors**: `#ff0000` (red) for RF, `#333333` (dark gray) for TCPIP
- **Stroke**: 3px with round linecap
- **Returns**: `L.icon({ iconUrl: 'data:image/svg+xml,...', iconSize: [size, size], iconAnchor: [size/2, size/2] })`

Each arc is placed as a non-interactive `L.marker` at the station's position with `zIndexOffset: 1000` to appear above regular markers.

#### RX Arcs (Inward Collapsing)

`createReceiveArcIcon(index)` - same technique but:

- **Color**: `#00cc00` (green)
- **Rotation**: Arcs oriented top/bottom (rotated 90 degrees from TX arcs)
- **Direction**: Outermost appears first, innermost last (signal "arriving")

### 5. Animation Orchestration

#### Received Packet (tx=false): "Remote station transmitted, we received"

```
t=0ms:     TX innermost arc at from_call position (red or gray)
t=150ms:   TX middle arc at from_call position
t=300ms:   TX outermost arc at from_call position
           + Route polylines appear (from_call -> digis -> home)
           + RX outermost arc at home station
t=450ms:   RX middle arc at home station
t=600ms:   RX innermost arc at home station
t=800ms:   Remove TX innermost
t=900ms:   Remove TX middle
t=1000ms:  Remove TX outermost
t=1100ms:  Remove RX outermost
t=1200ms:  Remove RX middle
t=1300ms:  Remove RX innermost
t=5000ms:  Remove route polylines
```

#### Transmitted Packet (tx=true): "We transmitted"

```
t=0ms:     TX innermost arc at home station (red)
t=150ms:   TX middle arc at home station
t=300ms:   TX outermost arc at home station
t=800ms:   Remove TX innermost
t=900ms:   Remove TX middle
t=1000ms:  Remove TX outermost
```

No route polylines for TX packets (we don't yet know the path until we hear the packet digipeated back). No RX arcs (we don't know who received it).

#### Guard Rails

- If the station has no known position, skip the animation entirely
- If an animation is already playing for the same station, let it complete (no overlapping)
- All temporary markers tracked in an array; `cleanupAnimations()` called every 10 seconds to remove any leaked elements

### 6. Route Polylines

`createRoutingPolylines(fromLat, fromLng, pathInfo)` draws the packet route:

- **Style**: `L.polyline` with `color: '#0000ff', weight: 3, opacity: 0.6, dashArray: '4,8'`
- **Segments**: TX station -> digi1 -> digi2 -> ... -> igate -> home station
- **Position resolution**: Each callsign looked up in station cache. Unknown positions cause that segment to be skipped (line drawn between known stations only).
- **Lifetime**: Appears at t=300ms, removed at t=5000ms
- **Visual distinction**: Blue dashed (route) vs cyan solid (existing movement trails)

### 7. Map Legend

A custom `L.Control` positioned at bottom-right:

- Collapsible (expanded by default, click header to collapse)
- Dark theme matching existing CSS (`background: #1e1e1e`, white text)
- Contents:
  - Small red arc SVG + "RF Transmit"
  - Small gray arc SVG + "Internet (TCPIP)"
  - Small green arc SVG + "Receiving"
  - Cyan solid line + "Movement trail"
  - Blue dashed line + "Packet route"

### 8. Backend Changes

#### New Endpoint: `GET /api/stations/positions`

Returns a lightweight position map for all known stations:

```python
@app.get("/api/stations/positions")
async def get_station_positions():
    stations = await storage.get_all_station_positions()
    return {s["callsign"]: {"lat": s["latitude"], "lng": s["longitude"]}
            for s in stations}
```

New storage method `get_all_station_positions()` queries:
```sql
SELECT callsign, latitude, longitude FROM stations
WHERE latitude IS NOT NULL AND longitude IS NOT NULL
```

#### Modified Packet Processing: `raw_path` Field

In `processor.py`, when processing an AGW packet, extract the raw path string from the APRS packet (the portion between `>` destination and `:` data start) and include it as `raw_path` in the broadcast data. The existing `path` field (cleaned array) remains unchanged.

Example: For raw packet `N3ABC>APRS,N3XYZ*,WIDE1-1,qAR,N3LLO-10:!4003.50N/...`
- Existing `path`: `["N3XYZ", "WIDE1-1", "qAR", "N3LLO-10"]` (cleaned)
- New `raw_path`: `"N3XYZ*,WIDE1-1,qAR,N3LLO-10"` (preserves `*` markers)

The `raw_path` is also stored in the `packets` table (new TEXT column) and included when serving historical packets via `GET /api/packets`.

### 9. Files Changed

| File | Change |
|------|--------|
| `src/direwolf_dashboard/static/app.js` | Add ~300 lines: path parsing, arc icons, animations, route polylines, station position cache, legend control |
| `src/direwolf_dashboard/static/index.html` | Minor: legend CSS styles in `<style>` block |
| `src/direwolf_dashboard/static/style.css` | Legend styling (collapsible panel, dark theme) |
| `src/direwolf_dashboard/server.py` | New `/api/stations/positions` endpoint |
| `src/direwolf_dashboard/storage.py` | New `get_all_station_positions()` method; add `raw_path` column to packets table |
| `src/direwolf_dashboard/processor.py` | Extract `raw_path` from raw APRS packet, include in broadcast |
| `tests/test_processor.py` | Tests for raw_path extraction |
| `tests/test_storage.py` | Tests for positions endpoint and raw_path storage |
| `tests/test_server.py` | Tests for /api/stations/positions endpoint |

### 10. Testing Strategy

- **Unit tests**: Path parsing logic (various APRS path formats), raw_path extraction
- **Integration tests**: `/api/stations/positions` endpoint returns correct data
- **Manual testing**: Visual verification of arc animations, route lines, legend display
- **Edge cases**: Packets without position, unknown digipeater positions, rapid packet bursts, TCPIP detection
