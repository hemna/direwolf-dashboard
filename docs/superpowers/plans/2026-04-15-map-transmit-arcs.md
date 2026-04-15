# Map Transmit/Receive Arc Animations Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add real-time TX/RX arc animations, packet route polylines, and a map legend to the direwolf dashboard, ported from the haminfo dashboard.

**Architecture:** Modify `_strip_agw_header()` to extract the Via path from AGW monitor frames and feed it through `aprslib.parse()` so the `path` field is populated. On the frontend, add ~300 lines to `app.js` implementing SVG arc animations, path parsing, route polylines, a station position cache, and a collapsible legend control.

**Tech Stack:** Python (FastAPI, aprslib), vanilla JavaScript (Leaflet.js, SVG), SQLite, pytest

---

## Chunk 1: Backend — Extract Path from AGW Monitor Header

### Task 1: Modify `_strip_agw_header()` to extract Via path

**Files:**
- Modify: `src/direwolf_dashboard/processor.py:143-163`
- Test: `tests/test_processor.py`

- [ ] **Step 1: Write failing tests for path extraction**

Add to `tests/test_processor.py` after the existing imports (line 7), add `_strip_agw_header` to the import list:

```python
from direwolf_dashboard.processor import (
    calculate_initial_compass_bearing,
    degrees_to_cardinal,
    format_compact_log,
    packet_to_dict,
    PacketProcessor,
    _strip_agw_header,
)
```

Add a new test class after `TestFormatCompactLog` (after line ~120):

```python
class TestStripAgwHeader:
    """Tests for AGW header stripping with path extraction."""

    def test_extracts_via_path(self):
        raw = '1:Fm N3ABC To APRS Via N3XYZ*,WIDE1-1,qAR,N3LLO-10 <UI pid=F0 Len=128>[12:34:56]\r!4003.50N/07507.23W>'
        payload, via_path = _strip_agw_header(raw)
        assert payload == '!4003.50N/07507.23W>'
        assert via_path == 'N3XYZ*,WIDE1-1,qAR,N3LLO-10'

    def test_no_via_clause(self):
        raw = '1:Fm N3ABC To APRS <UI pid=F0 Len=128>[12:34:56]\r!4003.50N/07507.23W>'
        payload, via_path = _strip_agw_header(raw)
        assert payload == '!4003.50N/07507.23W>'
        assert via_path is None

    def test_simple_format_with_via(self):
        raw = '1:Fm WB4BOR To APRS Via WIDE1-1 [12:34:56] !4003.50N/07507.23W>'
        payload, via_path = _strip_agw_header(raw)
        assert payload == '!4003.50N/07507.23W>'
        assert via_path == 'WIDE1-1'

    def test_multiple_via_hops(self):
        raw = '1:Fm N3ABC To APRS Via N3XYZ*,N3DEF*,WIDE2-1,qAR,N3LLO-10 <UI pid=F0 Len=64>[12:34:56]\r!4003.50N/07507.23W>'
        payload, via_path = _strip_agw_header(raw)
        assert via_path == 'N3XYZ*,N3DEF*,WIDE2-1,qAR,N3LLO-10'

    def test_no_agw_header_returns_none_path(self):
        raw = 'N3ABC>APRS,WIDE1-1:!4003.50N/07507.23W>'
        payload, via_path = _strip_agw_header(raw)
        assert payload == raw  # returned unchanged
        assert via_path is None

    def test_via_with_ssid(self):
        raw = '1:Fm N3ABC-5 To APRS Via N3XYZ-10*,qAR,N3LLO-10 [12:34:56]\r!4003.50N/07507.23W>'
        payload, via_path = _strip_agw_header(raw)
        assert via_path == 'N3XYZ-10*,qAR,N3LLO-10'
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_processor.py::TestStripAgwHeader -v`
Expected: FAIL — `_strip_agw_header` returns a string, not a tuple.

- [ ] **Step 3: Implement the path extraction in `_strip_agw_header`**

Replace the function at `src/direwolf_dashboard/processor.py:143-163` with:

```python
def _strip_agw_header(raw: str) -> tuple[str, str | None]:
    """Strip Direwolf AGW monitor header from raw frame data.

    AGW monitored frames look like:
        1:Fm CALL1 To CALL2 Via PATH <UI pid=F0 Len=128 PF=0 >[HH:MM:SS]\\r<payload>
    or the simpler form:
        1:Fm CALL1 To CALL2 Via PATH [HH:MM:SS] <payload>

    Returns:
        Tuple of (payload, via_path). via_path is the comma-separated path string
        from the Via clause (e.g. "N3XYZ*,WIDE1-1,qAR,N3LLO-10"), or None if no
        Via clause found. Payload is the APRS info portion.
    """
    import re

    # Match: <channel>:Fm <from> To <to> [Via <path>] [<UI info>] [<time>] then
    # optional \r and whitespace before the payload
    m = re.match(
        r"^\d+:Fm\s+\S+\s+To\s+\S+(?:\s+Via\s+(\S+(?:,\S+)*))?(?:\s+<[^>]*>)*\s*\[\d{2}:\d{2}:\d{2}\]\s*",
        raw,
    )
    if m:
        via_path = m.group(1)  # None if no Via clause in the match
        payload = raw[m.end():].lstrip("\r\n")
        return payload, via_path
    return raw, None
```

Key change: the `Via` group `(?:\s+Via\s+\S+(?:,\S+)*)` becomes a capturing group `(?:\s+Via\s+(\S+(?:,\S+)*))` so `m.group(1)` extracts the path string.

- [ ] **Step 4: Fix callers of `_strip_agw_header`**

In `src/direwolf_dashboard/processor.py:199`, update the call site in `packet_to_dict`:

Replace line 199:
```python
    payload = _strip_agw_header(raw_aprs_string)
```

With:
```python
    payload, via_path = _strip_agw_header(raw_aprs_string)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_processor.py::TestStripAgwHeader -v`
Expected: All 6 tests PASS.

- [ ] **Step 6: Run all existing tests to verify no regressions**

Run: `pytest tests/test_processor.py -v`
Expected: All tests PASS (existing tests still work because `packet_to_dict` now unpacks the tuple correctly).

- [ ] **Step 7: Commit**

```bash
git add src/direwolf_dashboard/processor.py tests/test_processor.py
git commit -m "feat: extract Via path from AGW monitor header in _strip_agw_header"
```

### Task 2: Thread Via path through to `aprslib.parse()`

**Files:**
- Modify: `src/direwolf_dashboard/processor.py:166-178` (`_extract_aprs_for_parsing`)
- Modify: `src/direwolf_dashboard/processor.py:201` (call site in `packet_to_dict`)
- Test: `tests/test_processor.py`

- [ ] **Step 1: Write failing tests for path in parsed output**

Add `_extract_aprs_for_parsing` to the import in `tests/test_processor.py`:

```python
from direwolf_dashboard.processor import (
    calculate_initial_compass_bearing,
    degrees_to_cardinal,
    format_compact_log,
    packet_to_dict,
    PacketProcessor,
    _strip_agw_header,
    _extract_aprs_for_parsing,
)
```

Add a new test class:

```python
class TestExtractAprsForParsing:
    """Tests for building parseable APRS strings with path."""

    def test_includes_via_path(self):
        result = _extract_aprs_for_parsing("!4003.50N/07507.23W>", "N3ABC", "APRS", "N3XYZ*,WIDE1-1,qAR,N3LLO-10")
        assert result == "N3ABC>APRS,N3XYZ*,WIDE1-1,qAR,N3LLO-10:!4003.50N/07507.23W>"

    def test_no_via_path(self):
        result = _extract_aprs_for_parsing("!4003.50N/07507.23W>", "N3ABC", "APRS", None)
        assert result == "N3ABC>APRS:!4003.50N/07507.23W>"

    def test_third_party_packet_ignores_via(self):
        result = _extract_aprs_for_parsing("}W3ADO>APRS:!4003.50N/07507.23W>", "N3ABC", "APRS", "WIDE1-1")
        assert result == "W3ADO>APRS:!4003.50N/07507.23W>"
```

Add a test to the existing `TestPacketToDict` class:

```python
    def test_agw_packet_with_via_path_populates_path(self):
        raw = '1:Fm N3ABC To APRS Via N3XYZ*,WIDE1-1,qAR,N3LLO-10 <UI pid=F0 Len=64>[12:34:56]\r!4003.50N/07507.23W>'
        result = packet_to_dict(raw, tx=False, call_from="N3ABC", call_to="APRS")
        assert result is not None
        assert len(result["path"]) > 0
        # aprslib preserves * markers on used digipeaters
        assert 'N3XYZ*' in result["path"]
        assert 'qAR' in result["path"]
        assert 'N3LLO-10' in result["path"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_processor.py::TestExtractAprsForParsing -v`
Expected: FAIL — `_extract_aprs_for_parsing` takes 3 args, not 4.

- [ ] **Step 3: Implement the via_path parameter**

Replace `_extract_aprs_for_parsing` at `src/direwolf_dashboard/processor.py:166-178`:

```python
def _extract_aprs_for_parsing(
    payload: str, call_from: str, call_to: str, via_path: str | None = None
) -> str:
    """Build a parseable APRS string from the payload.

    Third-party packets start with '}' and contain a full embedded APRS packet.
    Normal packets are just the info field and need from>to: prepended.
    If via_path is provided, it's included in the header so aprslib can parse
    the digipeater path.

    Returns a string suitable for aprslib.parse().
    """
    if payload.startswith("}"):
        # Third-party packet — the part after '}' is a full APRS packet
        return payload[1:]
    # Build standard APRS format: FROM>TO[,PATH]:payload
    if via_path:
        return f"{call_from}>{call_to},{via_path}:{payload}"
    return f"{call_from}>{call_to}:{payload}"
```

- [ ] **Step 4: Update the call site in `packet_to_dict`**

At `src/direwolf_dashboard/processor.py:201`, replace:
```python
    aprs_string = _extract_aprs_for_parsing(payload, call_from, call_to)
```
With:
```python
    aprs_string = _extract_aprs_for_parsing(payload, call_from, call_to, via_path)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_processor.py -v`
Expected: All tests PASS, including new ones and existing ones.

- [ ] **Step 6: Commit**

```bash
git add src/direwolf_dashboard/processor.py tests/test_processor.py
git commit -m "feat: include Via path in APRS string for aprslib parsing"
```

### Task 3: Add `/api/stations/positions` endpoint

**Files:**
- Modify: `src/direwolf_dashboard/storage.py`
- Modify: `src/direwolf_dashboard/server.py`
- Test: `tests/test_storage.py`
- Test: `tests/test_server.py`

- [ ] **Step 1: Write failing test for storage method**

Add to `tests/test_storage.py` inside `TestStations` class (after `test_get_station_track`, between lines 213 and 216):

```python
    async def test_get_all_station_positions(self, storage):
        await storage.upsert_station("N3ABC", 100.0, latitude=40.0, longitude=-75.0)
        await storage.upsert_station("N3DEF", 100.0, latitude=39.0, longitude=-76.0)
        await storage.upsert_station("N3GHI", 100.0)  # no position

        positions = await storage.get_all_station_positions()
        assert len(positions) == 2
        callsigns = {p["callsign"] for p in positions}
        assert callsigns == {"N3ABC", "N3DEF"}
        for p in positions:
            assert "latitude" in p
            assert "longitude" in p
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_storage.py::TestStations::test_get_all_station_positions -v`
Expected: FAIL — `get_all_station_positions` does not exist.

- [ ] **Step 3: Implement `get_all_station_positions` in storage**

Add after `get_station` method in `src/direwolf_dashboard/storage.py` (between lines 202 and 204, before `get_station_track`):

```python
    async def get_all_station_positions(self) -> list[dict]:
        """Return callsign + lat/lon for all stations with known positions."""
        cursor = await self._db.execute(
            "SELECT callsign, latitude, longitude FROM stations "
            "WHERE latitude IS NOT NULL AND longitude IS NOT NULL"
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
```

Note: Uses `self._db` (the persistent connection with `row_factory = aiosqlite.Row` already set in `init()`), consistent with all other storage methods.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_storage.py::TestStations::test_get_all_station_positions -v`
Expected: PASS.

- [ ] **Step 5: Write failing test for API endpoint**

Add to `tests/test_server.py`, new test class after `TestStatsEndpoint`:

```python
class TestStationPositionsEndpoint:
    async def test_get_positions_empty(self, test_app):
        app, storage = test_app
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/api/stations/positions")
            assert resp.status_code == 200
            assert resp.json() == {}

    async def test_get_positions_with_data(self, test_app):
        app, storage = test_app
        await storage.upsert_station("N3ABC", 100.0, latitude=40.0, longitude=-75.0)
        await storage.upsert_station("N3DEF", 100.0, latitude=39.0, longitude=-76.0)
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/api/stations/positions")
            assert resp.status_code == 200
            data = resp.json()
            assert "N3ABC" in data
            assert data["N3ABC"]["lat"] == 40.0
            assert data["N3ABC"]["lng"] == -75.0
            assert "N3DEF" in data
```

- [ ] **Step 6: Run test to verify it fails**

Run: `pytest tests/test_server.py::TestStationPositionsEndpoint -v`
Expected: FAIL — 404 (endpoint doesn't exist).

- [ ] **Step 7: Implement the API endpoint**

Add in `src/direwolf_dashboard/server.py` inside `create_app()`, after the `/api/station/{callsign}` endpoint (after line ~164):

```python
    @app.get("/api/stations/positions")
    async def get_station_positions():
        """Return lightweight position map for all known stations."""
        rows = await state.storage.get_all_station_positions()
        return {
            row["callsign"]: {"lat": row["latitude"], "lng": row["longitude"]}
            for row in rows
        }
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `pytest tests/test_server.py::TestStationPositionsEndpoint -v`
Expected: PASS.

- [ ] **Step 9: Run all tests to verify no regressions**

Run: `pytest tests/ -v`
Expected: All tests PASS.

- [ ] **Step 10: Commit**

```bash
git add src/direwolf_dashboard/storage.py src/direwolf_dashboard/server.py tests/test_storage.py tests/test_server.py
git commit -m "feat: add /api/stations/positions endpoint for station position cache bootstrap"
```

---

## Chunk 2: Frontend — Arc Animations, Path Parsing, Route Polylines, Legend

### Task 4: Add station position cache and path parsing to `app.js`

**Files:**
- Modify: `src/direwolf_dashboard/static/app.js`

- [ ] **Step 1: Add state variables**

In `app.js`, after the existing state variables (after line 17, after `const MAX_LOG_ROWS = 500;`), add:

```javascript
    // --- Animation state ---
    const stationPositionCache = {};  // callsign -> { lat, lng, updatedAt }
    const POSITION_CACHE_MAX = 1000;
    const animationThrottle = {};     // callsign -> timestamp of last animation
    const ANIMATION_COOLDOWN_MS = 3000;
    const GENERIC_PATH_ALIASES = new Set([
        'WIDE', 'WIDE1', 'WIDE2', 'WIDE3', 'WIDE1-1', 'WIDE2-1', 'WIDE2-2', 'WIDE3-3',
        'RELAY', 'TRACE', 'TCPIP', 'CQ', 'QST', 'APRS', 'RFONLY', 'NOGATE',
    ]);
```

- [ ] **Step 2: Add `loadStationPositions()` function**

Add after the existing `loadStations()` function (after line ~301):

```javascript
    async function loadStationPositions() {
        try {
            const resp = await fetch('/api/stations/positions');
            const data = await resp.json();
            const now = Date.now();
            for (const [callsign, pos] of Object.entries(data)) {
                stationPositionCache[callsign] = { lat: pos.lat, lng: pos.lng, updatedAt: now };
            }
        } catch (e) {
            console.error('Failed to load station positions:', e);
        }
    }
```

- [ ] **Step 3: Call `loadStationPositions()` on init**

In the `DOMContentLoaded` handler (around line 245-255), after `await loadStations();` and before `connectWebSocket();`, add:

```javascript
        loadStationPositions();
```

- [ ] **Step 4: Add `parsePath()` function**

Add after the new `loadStationPositions` function:

```javascript
    function parsePath(pathArray) {
        const result = { digipeaters: [], igate: null, isTcpip: false };
        if (!pathArray || !Array.isArray(pathArray) || pathArray.length === 0) return result;

        // Detect TCPIP-origin packets
        if (pathArray.some(p => p === 'TCPIP' || p === 'TCPIP*')) {
            result.isTcpip = true;
        }
        if (pathArray.some(p => p === 'qAC' || p === 'qAU')) {
            result.isTcpip = true;
        }

        for (let i = 0; i < pathArray.length; i++) {
            const part = pathArray[i];

            // Found q-construct -- next element is the igate
            if (part.startsWith('qA') && i + 1 < pathArray.length) {
                result.igate = pathArray[i + 1];
                break;
            }

            // Check for used digipeater (ends with *)
            if (part.endsWith('*')) {
                const callsign = part.slice(0, -1);
                if (!GENERIC_PATH_ALIASES.has(callsign) && !GENERIC_PATH_ALIASES.has(callsign.replace(/-\d+$/, ''))) {
                    result.digipeaters.push(callsign);
                }
            }
        }

        return result;
    }
```

- [ ] **Step 5: Add `getStationPosition()` function**

```javascript
    function getStationPosition(callsign) {
        // 0. Home station — use configured coordinates
        const homeCall = config.station?.callsign;
        if (homeCall && callsign.toUpperCase() === homeCall.toUpperCase()) {
            const lat = config.station?.latitude;
            const lng = config.station?.longitude;
            if (lat != null && lng != null) return { lat, lng };
        }
        // 1. Visible marker on map
        const s = stations[callsign];
        if (s && s.data && s.data.latitude != null && s.data.longitude != null) {
            return { lat: s.data.latitude, lng: s.data.longitude };
        }
        // 2. Position cache
        const cached = stationPositionCache[callsign];
        if (cached) return { lat: cached.lat, lng: cached.lng };
        return null;
    }
```

- [ ] **Step 6: Add `updatePositionCache()` helper**

```javascript
    function updatePositionCache(callsign, lat, lng) {
        stationPositionCache[callsign] = { lat, lng, updatedAt: Date.now() };
        // Evict oldest if over limit
        const keys = Object.keys(stationPositionCache);
        if (keys.length > POSITION_CACHE_MAX) {
            let oldestKey = keys[0];
            let oldestTime = stationPositionCache[oldestKey].updatedAt;
            for (const key of keys) {
                if (stationPositionCache[key].updatedAt < oldestTime) {
                    oldestKey = key;
                    oldestTime = stationPositionCache[key].updatedAt;
                }
            }
            delete stationPositionCache[oldestKey];
        }
    }
```

- [ ] **Step 7: Commit**

```bash
git add src/direwolf_dashboard/static/app.js
git commit -m "feat: add station position cache and APRS path parsing to frontend"
```

### Task 5: Add SVG arc icon creators and animation functions

**Files:**
- Modify: `src/direwolf_dashboard/static/app.js`

- [ ] **Step 1: Add `createTransmitArcIcon()` function**

Add after the position cache helpers:

```javascript
    function createTransmitArcIcon(index, color) {
        const strokeColor = color || '#ff0000';
        const size = 40 + (index * 8);  // 48, 56, 64
        const center = size / 2;
        const radius = 10 + (index * 7);  // 17, 24, 31

        const arcAngle = 50;
        const startAngle = 180 + (90 - arcAngle / 2);
        const endAngle = startAngle + arcAngle;

        function polarToCartesian(cx, cy, r, angleDeg) {
            const rad = (angleDeg * Math.PI) / 180;
            return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
        }

        const lStart = polarToCartesian(center, center, radius, startAngle);
        const lEnd = polarToCartesian(center, center, radius, endAngle);
        const leftArc = `M ${lStart.x} ${lStart.y} A ${radius} ${radius} 0 0 1 ${lEnd.x} ${lEnd.y}`;

        const rStartAngle = 360 - endAngle + 180;
        const rEndAngle = rStartAngle + arcAngle;
        const rStart = polarToCartesian(center, center, radius, rStartAngle);
        const rEnd = polarToCartesian(center, center, radius, rEndAngle);
        const rightArc = `M ${rStart.x} ${rStart.y} A ${radius} ${radius} 0 0 1 ${rEnd.x} ${rEnd.y}`;

        const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
            <path d="${leftArc}" fill="none" stroke="${strokeColor}" stroke-width="3" stroke-linecap="round" stroke-opacity="0.7"/>
            <path d="${rightArc}" fill="none" stroke="${strokeColor}" stroke-width="3" stroke-linecap="round" stroke-opacity="0.7"/>
        </svg>`;

        const svgUrl = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
        return L.icon({ iconUrl: svgUrl, iconSize: [size, size], iconAnchor: [center, center] });
    }
```

- [ ] **Step 2: Add `createReceiveArcIcon()` function**

```javascript
    function createReceiveArcIcon(index) {
        const size = 40 + (index * 8);
        const center = size / 2;
        const radius = 10 + (index * 7);

        const arcAngle = 50;
        const startAngle = 90 + (90 - arcAngle / 2);
        const endAngle = startAngle + arcAngle;

        function polarToCartesian(cx, cy, r, angleDeg) {
            const rad = (angleDeg * Math.PI) / 180;
            return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
        }

        const tStart = polarToCartesian(center, center, radius, startAngle);
        const tEnd = polarToCartesian(center, center, radius, endAngle);
        const topArc = `M ${tStart.x} ${tStart.y} A ${radius} ${radius} 0 0 1 ${tEnd.x} ${tEnd.y}`;

        const bStartAngle = 360 - endAngle + 180;
        const bEndAngle = bStartAngle + arcAngle;
        const bStart = polarToCartesian(center, center, radius, bStartAngle);
        const bEnd = polarToCartesian(center, center, radius, bEndAngle);
        const bottomArc = `M ${bStart.x} ${bStart.y} A ${radius} ${radius} 0 0 1 ${bEnd.x} ${bEnd.y}`;

        const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
            <path d="${topArc}" fill="none" stroke="#00cc00" stroke-width="3" stroke-linecap="round" stroke-opacity="0.7"/>
            <path d="${bottomArc}" fill="none" stroke="#00cc00" stroke-width="3" stroke-linecap="round" stroke-opacity="0.7"/>
        </svg>`;

        const svgUrl = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
        return L.icon({ iconUrl: svgUrl, iconSize: [size, size], iconAnchor: [center, center] });
    }
```

- [ ] **Step 3: Add `createRoutingPolylines()` function**

```javascript
    function createRoutingPolylines(stationLat, stationLng, pathInfo) {
        if (!pathInfo) return [];

        const waypoints = [{ lat: stationLat, lng: stationLng }];

        for (const digiCall of pathInfo.digipeaters) {
            const pos = getStationPosition(digiCall);
            if (pos) waypoints.push(pos);
        }

        if (pathInfo.igate) {
            const pos = getStationPosition(pathInfo.igate);
            if (pos) waypoints.push(pos);
        }

        // Add home station as final waypoint for received packets
        const homeCall = config.station?.callsign;
        if (homeCall) {
            const homePos = getStationPosition(homeCall);
            if (homePos && (waypoints.length === 0 ||
                waypoints[waypoints.length - 1].lat !== homePos.lat ||
                waypoints[waypoints.length - 1].lng !== homePos.lng)) {
                waypoints.push(homePos);
            }
        }

        if (waypoints.length < 2) return [];

        const polylines = [];
        for (let i = 0; i < waypoints.length - 1; i++) {
            const line = L.polyline(
                [[waypoints[i].lat, waypoints[i].lng], [waypoints[i + 1].lat, waypoints[i + 1].lng]],
                { color: '#0000ff', weight: 3, opacity: 0.6, dashArray: '4,8', lineCap: 'round', lineJoin: 'round' }
            );
            polylines.push(line);
        }
        return polylines;
    }
```

- [ ] **Step 4: Add `playReceiveAnimation()` function**

```javascript
    function playReceiveAnimation(callsign, lat, lng) {
        const now = Date.now();
        const throttleKey = 'rx_' + callsign;
        if (animationThrottle[throttleKey] && now - animationThrottle[throttleKey] < ANIMATION_COOLDOWN_MS) return;
        animationThrottle[throttleKey] = now;

        const latLng = [lat, lng];
        const arcMarkers = [];
        for (let i = 1; i <= 3; i++) {
            arcMarkers.push(L.marker(latLng, { icon: createReceiveArcIcon(i), interactive: false, zIndexOffset: 1000 }));
        }

        // Inward-collapsing: outermost first
        arcMarkers[2].addTo(map);
        setTimeout(() => { arcMarkers[1].addTo(map); }, 150);
        setTimeout(() => { arcMarkers[0].addTo(map); }, 300);

        // Remove outermost-first
        setTimeout(() => { map.removeLayer(arcMarkers[2]); }, 800);
        setTimeout(() => { map.removeLayer(arcMarkers[1]); }, 900);
        setTimeout(() => { map.removeLayer(arcMarkers[0]); }, 1000);
    }
```

- [ ] **Step 5: Add `playTransmitAnimation()` function**

```javascript
    function playTransmitAnimation(callsign, lat, lng, pathArray) {
        const now = Date.now();
        if (animationThrottle[callsign] && now - animationThrottle[callsign] < ANIMATION_COOLDOWN_MS) return;
        animationThrottle[callsign] = now;

        // Clean old throttle entries
        if (Object.keys(animationThrottle).length > 500) {
            const cutoff = now - ANIMATION_COOLDOWN_MS * 2;
            for (const key of Object.keys(animationThrottle)) {
                if (animationThrottle[key] < cutoff) delete animationThrottle[key];
            }
        }

        const latLng = [lat, lng];
        const pathInfo = parsePath(pathArray);
        const txColor = pathInfo.isTcpip ? '#333333' : '#ff0000';

        // Create 3 TX arc markers
        const arcMarkers = [];
        for (let i = 1; i <= 3; i++) {
            arcMarkers.push(L.marker(latLng, { icon: createTransmitArcIcon(i, txColor), interactive: false, zIndexOffset: 1000 }));
        }

        // Create routing polylines
        const routingLines = createRoutingPolylines(lat, lng, pathInfo);

        // Collect receivers with known positions
        const receivers = [];
        for (const digiCall of pathInfo.digipeaters) {
            const pos = getStationPosition(digiCall);
            if (pos) receivers.push({ callsign: digiCall, lat: pos.lat, lng: pos.lng });
        }
        if (pathInfo.igate) {
            const pos = getStationPosition(pathInfo.igate);
            if (pos) receivers.push({ callsign: pathInfo.igate, lat: pos.lat, lng: pos.lng });
        }

        // TX animation timeline
        arcMarkers[0].addTo(map);
        setTimeout(() => { arcMarkers[1].addTo(map); }, 150);
        setTimeout(() => {
            arcMarkers[2].addTo(map);
            for (const line of routingLines) line.addTo(map);
        }, 300);

        setTimeout(() => { map.removeLayer(arcMarkers[0]); }, 800);
        setTimeout(() => { map.removeLayer(arcMarkers[1]); }, 900);
        setTimeout(() => { map.removeLayer(arcMarkers[2]); }, 1000);

        // RX animations staggered along path
        for (let i = 0; i < receivers.length; i++) {
            const rx = receivers[i];
            setTimeout(() => { playReceiveAnimation(rx.callsign, rx.lat, rx.lng); }, 300 + (i * 300));
        }

        // Remove routing polylines
        if (routingLines.length > 0) {
            setTimeout(() => {
                for (const line of routingLines) map.removeLayer(line);
            }, 5000);
        }
    }
```

- [ ] **Step 6: Commit**

```bash
git add src/direwolf_dashboard/static/app.js
git commit -m "feat: add SVG arc animations, route polylines, and animation orchestration"
```

### Task 5b: Add animation cleanup safety net

**Files:**
- Modify: `src/direwolf_dashboard/static/app.js`

- [ ] **Step 1: Add active animation tracking array and cleanup function**

Add after the state variables (near the `animationThrottle` declaration):

```javascript
    const activeAnimationElements = [];  // { element, removeAt }
```

Add a new `cleanupAnimations()` function after `playTransmitAnimation()`:

```javascript
    function trackAnimationElement(element, removeAt) {
        activeAnimationElements.push({ element, removeAt });
    }

    function cleanupAnimations() {
        const now = Date.now();
        for (let i = activeAnimationElements.length - 1; i >= 0; i--) {
            const entry = activeAnimationElements[i];
            if (now >= entry.removeAt) {
                try { map.removeLayer(entry.element); } catch (e) { /* already removed */ }
                activeAnimationElements.splice(i, 1);
            }
        }
        // Clean old throttle entries too
        const cutoff = now - ANIMATION_COOLDOWN_MS * 2;
        for (const key of Object.keys(animationThrottle)) {
            if (animationThrottle[key] < cutoff) delete animationThrottle[key];
        }
    }
```

- [ ] **Step 2: Register tracking in `playTransmitAnimation()` and `playReceiveAnimation()`**

In `playTransmitAnimation()`, after each `arcMarker` is pushed to the local array, also register with the cleanup tracker. Add after line `arcMarkers.push(...)` in the TX arc creation loop:

```javascript
            trackAnimationElement(arcMarker, Date.now() + 1100);  // removed at ~1000ms + margin
```

In `playTransmitAnimation()`, after the routing polylines are created, register them:
```javascript
        for (const line of routingLines) {
            trackAnimationElement(line, Date.now() + 5500);  // removed at ~5000ms + margin
        }
```

In `playReceiveAnimation()`, after each `arcMarker` is pushed, register:
```javascript
            trackAnimationElement(arcMarker, Date.now() + 1100);
```

- [ ] **Step 3: Start the cleanup interval in `initMap()`**

At the end of `initMap()` (after the map is created and home marker added), add:

```javascript
        // Safety net: clean up leaked animation elements every 10 seconds
        setInterval(cleanupAnimations, 10000);
```

- [ ] **Step 4: Commit**

```bash
git add src/direwolf_dashboard/static/app.js
git commit -m "feat: add animation cleanup safety net to prevent leaked map elements"
```

### Task 6: Hook animations into `onPacket()` and update position cache

**Files:**
- Modify: `src/direwolf_dashboard/static/app.js`

- [ ] **Step 1: Modify `onPacket()` to trigger animations and update cache**

Replace the existing `onPacket` function at line 418-439 with:

```javascript
    function onPacket(packet) {
        // Add to log
        addLogRow(packet);

        // Update position cache for all packets with coordinates
        if (packet.latitude != null && packet.longitude != null) {
            updatePositionCache(packet.from_call, packet.latitude, packet.longitude);
        }

        // Update map marker
        if (packet.latitude && packet.longitude) {
            addOrUpdateStation({
                callsign: packet.from_call,
                latitude: packet.latitude,
                longitude: packet.longitude,
                symbol: packet.symbol,
                symbol_table: packet.symbol_table,
                last_comment: packet.comment,
                packet_count: (stations[packet.from_call]?.data?.packet_count || 0) + 1,
                last_seen: packet.timestamp,
            });
            if (!packet.position_from_db) {
                updateStationTrack(packet.from_call, packet.latitude, packet.longitude);
            }
        }

        // Trigger animations
        const homeLat = config.station?.latitude;
        const homeLon = config.station?.longitude;
        const homeCall = config.station?.callsign;

        if (packet.tx) {
            // We transmitted — TX arcs at home station
            if (homeLat != null && homeLon != null) {
                playTransmitAnimation(homeCall || 'Home', homeLat, homeLon, []);
            }
        } else {
            // We received — TX arcs at remote station, RX arcs at home
            if (packet.latitude && packet.longitude) {
                playTransmitAnimation(packet.from_call, packet.latitude, packet.longitude, packet.path || []);
            }
            // RX animation at home station
            // playTransmitAnimation triggers RX at receivers found in path (digis, igate).
            // Home station may not be in the parsed path, so trigger explicitly.
            // The 3s throttle in playReceiveAnimation prevents duplicates.
            if (homeLat != null && homeLon != null && homeCall) {
                setTimeout(() => {
                    playReceiveAnimation(homeCall, homeLat, homeLon);
                }, 300);
            }
        }
    }
```

- [ ] **Step 2: Verify manually**

Start the dashboard and verify:
- When a packet is received (tx=false), red arcs appear at the remote station's position and green arcs appear at the home station
- When a packet is transmitted (tx=true), red arcs appear at the home station
- Route polylines appear as blue dashed lines between waypoints
- Animations clean up after their timeouts

- [ ] **Step 3: Commit**

```bash
git add src/direwolf_dashboard/static/app.js
git commit -m "feat: hook arc animations into packet handler with TX/RX orchestration"
```

### Task 7: Add map legend

**Files:**
- Modify: `src/direwolf_dashboard/static/app.js`
- Modify: `src/direwolf_dashboard/static/style.css`

- [ ] **Step 1: Add legend CSS**

Add at the end of `src/direwolf_dashboard/static/style.css` (before the closing comment or at the end):

```css
/* Map legend */
.map-legend {
    background: rgba(30, 30, 30, 0.92);
    border-radius: 6px;
    padding: 10px 12px;
    font-size: 11px;
    color: #ccc;
    border: 1px solid #444;
    pointer-events: auto;
    max-width: 170px;
}
.map-legend-header {
    cursor: pointer;
    font-size: 10px;
    color: #888;
    border-bottom: 1px solid #444;
    padding-bottom: 6px;
    margin-bottom: 6px;
    user-select: none;
}
.map-legend-header:hover {
    color: #bbb;
}
.legend-section-title {
    font-size: 10px;
    color: #666;
    margin-top: 8px;
    margin-bottom: 4px;
    border-top: 1px solid #333;
    padding-top: 6px;
}
.legend-item {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 4px;
}
.legend-item:last-child {
    margin-bottom: 0;
}
.map-legend.collapsed .legend-body {
    display: none;
}
.map-legend.collapsed .map-legend-header {
    border-bottom: none;
    padding-bottom: 0;
    margin-bottom: 0;
}
```

- [ ] **Step 2: Add legend Leaflet control in `app.js`**

Add after the animation functions, a new function `initLegend()`:

```javascript
    function initLegend() {
        const LegendControl = L.Control.extend({
            options: { position: 'bottomright' },
            onAdd: function () {
                const container = L.DomUtil.create('div', 'map-legend');
                container.innerHTML = `
                    <div class="map-legend-header">
                        Legend &#9660;
                    </div>
                    <div class="legend-body">
                        <div class="legend-section-title">Animations</div>
                        <div class="legend-item">
                            <svg width="16" height="16" viewBox="0 0 16 16">
                                <path d="M 3 6 A 6 6 0 0 1 3 10" fill="none" stroke="#ff0000" stroke-width="1.5" stroke-linecap="round" stroke-opacity="0.8"/>
                                <path d="M 13 6 A 6 6 0 0 0 13 10" fill="none" stroke="#ff0000" stroke-width="1.5" stroke-linecap="round" stroke-opacity="0.8"/>
                            </svg>
                            <span>RF Transmit</span>
                        </div>
                        <div class="legend-item">
                            <svg width="16" height="16" viewBox="0 0 16 16">
                                <path d="M 3 6 A 6 6 0 0 1 3 10" fill="none" stroke="#555555" stroke-width="1.5" stroke-linecap="round" stroke-opacity="0.8"/>
                                <path d="M 13 6 A 6 6 0 0 0 13 10" fill="none" stroke="#555555" stroke-width="1.5" stroke-linecap="round" stroke-opacity="0.8"/>
                            </svg>
                            <span>Internet (TCPIP)</span>
                        </div>
                        <div class="legend-item">
                            <svg width="16" height="16" viewBox="0 0 16 16">
                                <path d="M 6 3 A 6 6 0 0 1 10 3" fill="none" stroke="#00cc00" stroke-width="1.5" stroke-linecap="round" stroke-opacity="0.8"/>
                                <path d="M 6 13 A 6 6 0 0 0 10 13" fill="none" stroke="#00cc00" stroke-width="1.5" stroke-linecap="round" stroke-opacity="0.8"/>
                            </svg>
                            <span>Receiving</span>
                        </div>
                        <div class="legend-section-title">Trails</div>
                        <div class="legend-item">
                            <div style="width:20px;height:3px;background:#00b4d8;opacity:0.7;border-radius:2px;"></div>
                            <span>Movement trail</span>
                        </div>
                        <div class="legend-item">
                            <div style="width:20px;height:0;border-top:3px dashed #0000ff;opacity:0.6;"></div>
                            <span>Packet route</span>
                        </div>
                    </div>
                `;
                // Toggle collapse on header click
                const header = container.querySelector('.map-legend-header');
                header.addEventListener('click', () => container.classList.toggle('collapsed'));
                L.DomEvent.disableClickPropagation(container);
                L.DomEvent.disableScrollPropagation(container);
                return container;
            },
        });
        new LegendControl().addTo(map);
    }
```

- [ ] **Step 3: Call `initLegend()` after `initMap()`**

In the `DOMContentLoaded` handler, after `initMap();`, add:

```javascript
        initLegend();
```

- [ ] **Step 4: Verify manually**

Start the dashboard and verify:
- Legend appears at bottom-right of map
- Clicking the header collapses/expands it
- Dark theme matches existing UI
- Legend doesn't interfere with map interactions (drag, zoom)

- [ ] **Step 5: Commit**

```bash
git add src/direwolf_dashboard/static/app.js src/direwolf_dashboard/static/style.css
git commit -m "feat: add collapsible map legend showing animation and trail symbols"
```

### Task 8: Run full test suite and verify

**Files:** None (verification only)

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/ -v`
Expected: All tests PASS.

- [ ] **Step 2: Run linter if available**

Run: `ruff check src/direwolf_dashboard/`
Expected: No errors (or only pre-existing ones).

- [ ] **Step 3: Final commit if any cleanup needed**

If any cleanup was needed, commit with:
```bash
git commit -m "chore: cleanup and formatting for map animation feature"
```
