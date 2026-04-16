# Config Simplification & My Position — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove direwolf.conf parsing, remove callsign/symbol from config, add a "my position" feature driven from the UI.

**Architecture:** Strip the "home station" concept from config and processor. Add a `MyPositionConfig` dataclass and UI-driven position selection (station tracking or dropped pin). Move bearing/distance from processor to broadcast consumer. Add cold-start modal/toast for first-time users.

**Tech Stack:** Python/FastAPI (backend), vanilla JS/Leaflet (frontend), SQLite (storage), pytest (tests)

**Spec:** `docs/superpowers/specs/2026-04-16-config-simplification-design.md`

---

## Chunk 1: Backend Config Cleanup

### Task 1: Add MyPositionConfig dataclass and update StationConfig

**Files:**
- Modify: `src/direwolf_dashboard/config.py:21-37`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write tests for new config dataclasses**

In `tests/test_config.py`, add a new test class:

```python
from direwolf_dashboard.config import MyPositionConfig

class TestMyPositionConfig:
    """Test MyPositionConfig dataclass."""

    def test_default_my_position_all_none(self):
        mp = MyPositionConfig()
        assert mp.type is None
        assert mp.callsign is None
        assert mp.latitude is None
        assert mp.longitude is None

    def test_station_type(self):
        mp = MyPositionConfig(type="station", callsign="WB4BOR")
        assert mp.type == "station"
        assert mp.callsign == "WB4BOR"
        assert mp.latitude is None
        assert mp.longitude is None

    def test_pin_type(self):
        mp = MyPositionConfig(type="pin", latitude=37.75, longitude=-77.45)
        assert mp.type == "pin"
        assert mp.callsign is None
        assert mp.latitude == 37.75
        assert mp.longitude == -77.45


class TestStationConfigSimplified:
    """Test StationConfig after removing callsign/symbol fields."""

    def test_station_config_has_no_callsign(self):
        config = Config()
        assert not hasattr(config.station, 'callsign')

    def test_station_config_has_no_symbol(self):
        config = Config()
        assert not hasattr(config.station, 'symbol')
        assert not hasattr(config.station, 'symbol_table')

    def test_station_config_has_my_position(self):
        config = Config()
        assert isinstance(config.station.my_position, MyPositionConfig)
        assert config.station.my_position.type is None

    def test_config_to_dict_includes_my_position(self):
        config = Config()
        d = config.to_dict()
        assert "my_position" in d["station"]
        assert d["station"]["my_position"]["type"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_config.py::TestMyPositionConfig -v`
Expected: FAIL (MyPositionConfig not found)

- [ ] **Step 3: Implement MyPositionConfig and update StationConfig**

In `src/direwolf_dashboard/config.py`, add `MyPositionConfig` before `StationConfig` and update `StationConfig`:

```python
@dataclass
class MyPositionConfig:
    type: Optional[str] = None          # "station", "pin", or None
    callsign: Optional[str] = None      # set when type="station"
    latitude: Optional[float] = None    # set when type="pin"
    longitude: Optional[float] = None   # set when type="pin"


@dataclass
class StationConfig:
    latitude: float = 0.0
    longitude: float = 0.0
    zoom: int = 12
    my_position: MyPositionConfig = field(default_factory=MyPositionConfig)
```

Remove `callsign`, `symbol`, `symbol_table` from `StationConfig`.

- [ ] **Step 4: Update `_dict_to_config` to handle nested MyPositionConfig**

In `config.py`, update `_dict_to_config`:

```python
def _dict_to_config(d: dict) -> Config:
    station_dict = d.get("station", {})
    my_pos_dict = station_dict.pop("my_position", {})
    # Filter out removed fields that may exist in old config files
    for removed in ("callsign", "symbol", "symbol_table"):
        station_dict.pop(removed, None)
    station = StationConfig(**station_dict, my_position=MyPositionConfig(**my_pos_dict))
    
    direwolf_dict = d.get("direwolf", {})
    direwolf_dict.pop("conf_file", None)  # Removed field
    
    return Config(
        station=station,
        direwolf=DirewolfConfig(**direwolf_dict),
        server=ServerConfig(**d.get("server", {})),
        storage=StorageConfig(**d.get("storage", {})),
        tiles=TilesConfig(**d.get("tiles", {})),
        display=DisplayConfig(**d.get("display", {})),
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_config.py::TestMyPositionConfig tests/test_config.py::TestStationConfigSimplified -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/direwolf_dashboard/config.py tests/test_config.py
git commit -m "feat: add MyPositionConfig, remove callsign/symbol from StationConfig"
```

### Task 2: Remove direwolf.conf parsing code

**Files:**
- Modify: `src/direwolf_dashboard/config.py` (remove ~130 lines)
- Modify: `tests/test_config.py` (remove direwolf conf tests)

- [ ] **Step 1: Remove direwolf.conf code from config.py**

Remove these items from `src/direwolf_dashboard/config.py`:
- `DIREWOLF_SYMBOL_NAMES` dict (lines ~228-248)
- `parse_direwolf_conf()` function (lines ~251-305)
- `_parse_pbeacon()` function (lines ~307-359)
- `DIGIPI_DIREWOLF_CONF` constant
- Auto-import logic in `load_config()` (the `else` branch that calls `parse_direwolf_conf(DIGIPI_DIREWOLF_CONF)`)
- `conf_file` from `DirewolfConfig` dataclass
- `import shlex` (only used by pbeacon parsing)

The `load_config()` `else` branch (first launch) should simplify to just creating defaults and saving:

```python
    else:
        merged = default_dict
        first_config = _dict_to_config(merged)
        save_config(first_config, path)
```

- [ ] **Step 2: Remove direwolf conf tests from test_config.py**

Remove:
- `TestFirstLaunchDirewolfImport` class entirely
- `TestParseDirewolfConf` class entirely (if it exists)
- Any imports of `parse_direwolf_conf`
- Update `TestDefaultConfig` tests that reference `callsign`, `symbol`, `symbol_table`

- [ ] **Step 3: Fix remaining test references to removed fields**

Update any tests in `test_config.py` that set `callsign`, `symbol`, etc. in config updates. For example, `test_update_persists_to_yaml` which sets `callsign: "WB4BOR"` should be changed to test a valid field like `latitude`.

- [ ] **Step 4: Run all config tests**

Run: `python -m pytest tests/test_config.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/direwolf_dashboard/config.py tests/test_config.py
git commit -m "feat: remove direwolf.conf parsing, DirewolfConfig.conf_file, and related tests"
```

### Task 3: Update processor and server atomically — remove station coords, move bearing/distance

> **IMPORTANT:** The processor constructor change and the server `create_app` update MUST happen
> in the same task to avoid test failures. Tasks 3 and 4 from the original plan are merged.

**Files:**
- Modify: `src/direwolf_dashboard/processor.py:258-277` (remove bearing from packet_to_dict)
- Modify: `src/direwolf_dashboard/processor.py:131-138` (remove bearing from format_compact_log)
- Modify: `src/direwolf_dashboard/processor.py:370-395` (remove constructor params)
- Modify: `src/direwolf_dashboard/server.py:208-224` (remove import endpoint)
- Modify: `src/direwolf_dashboard/server.py:77-80` (update processor init)
- Modify: `src/direwolf_dashboard/server.py:134-148` (update get_packets to await async enrich)
- Modify: `src/direwolf_dashboard/server.py:306-317` (update websocket_endpoint to await async enrich)
- Modify: `src/direwolf_dashboard/server.py:226-228` (remove hot-reload of processor coords)
- Modify: `src/direwolf_dashboard/server.py:370-440` (_enrich_with_bearing + _broadcast_consumer)
- Modify: `tests/test_server.py`
- Modify: `tests/test_processor.py`

- [ ] **Step 1: Write test for my_position validation in PUT /api/config**

Add to `tests/test_server.py`:

```python
class TestMyPositionValidation:
    """Test my_position validation in PUT /api/config."""

    async def test_set_station_type(self, test_app):
        app, storage = test_app
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.put(
                "/api/config",
                json={"station": {"my_position": {"type": "station", "callsign": "WB4BOR"}}},
            )
            assert response.status_code == 200

    async def test_set_pin_type(self, test_app):
        app, storage = test_app
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.put(
                "/api/config",
                json={"station": {"my_position": {"type": "pin", "latitude": 37.75, "longitude": -77.45}}},
            )
            assert response.status_code == 200

    async def test_clear_my_position(self, test_app):
        app, storage = test_app
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.put(
                "/api/config",
                json={"station": {"my_position": {"type": None}}},
            )
            assert response.status_code == 200

    async def test_station_type_requires_callsign(self, test_app):
        app, storage = test_app
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.put(
                "/api/config",
                json={"station": {"my_position": {"type": "station", "callsign": ""}}},
            )
            assert response.status_code == 400

    async def test_pin_type_requires_valid_coords(self, test_app):
        app, storage = test_app
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.put(
                "/api/config",
                json={"station": {"my_position": {"type": "pin", "latitude": 999}}},
            )
            assert response.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_server.py::TestMyPositionValidation -v`
Expected: FAIL

- [ ] **Step 3: Update processor.py (constructor, packet_to_dict, format_compact_log)**

1. Remove `station_lat` and `station_lon` from `PacketProcessor.__init__`:

```python
class PacketProcessor:
    def __init__(self, broadcast_queue: asyncio.Queue):
        self.broadcast_queue = broadcast_queue
        self._pending_log_data: dict[str, dict] = {}
        self._correlation_window = 2.0
```

2. Remove `station_lat`/`station_lon` params from `packet_to_dict()` function signature.

3. Remove the bearing/distance computation block (lines 258-274) from `packet_to_dict()`.

4. Remove the bearing/distance block from `format_compact_log()` (lines 131-138):

```python
    # REMOVE these lines from format_compact_log():
    # bearing = packet.get("bearing")
    # distance = packet.get("distance_miles")
    # if bearing and distance is not None:
    #     parts.append(...)
```

This block becomes dead code since bearing/distance are no longer set at parse time.

5. Update the call in `on_agw_packet()` to not pass `station_lat`/`station_lon`.

- [ ] **Step 4: Update processor tests**

In `tests/test_processor.py`:
- Remove or update `test_with_station_position_computes_bearing` to verify bearing is NOT computed in packet_to_dict.
- Update **all** `PacketProcessor` instantiations (there are 3: `test_agw_packet_queued`, `test_queue_full_drops_oldest`, `test_log_data_correlation`) to use the new constructor:

```python
class TestPacketProcessor:
    async def test_agw_packet_queued(self):
        queue = asyncio.Queue()
        proc = PacketProcessor(broadcast_queue=queue)
        # ... rest unchanged
```

- [ ] **Step 5: Implement server changes**

In `src/direwolf_dashboard/server.py`:

Add `from dataclasses import asdict` to the imports at the top of the file.

1. **Remove** the `POST /api/import-direwolf-conf` endpoint (lines 208-224).

2. **Add validation** to `PUT /api/config` endpoint. Before `update_config()` call:

```python
    @app.put("/api/config")
    async def put_config(body: dict):
        # Validate my_position if present
        my_pos = body.get("station", {}).get("my_position")
        if my_pos is not None:
            mp_type = my_pos.get("type")
            if mp_type == "station":
                if not my_pos.get("callsign"):
                    raise HTTPException(
                        status_code=400,
                        detail="my_position type 'station' requires a non-empty callsign",
                    )
            elif mp_type == "pin":
                lat = my_pos.get("latitude")
                lon = my_pos.get("longitude")
                if lat is None or lon is None:
                    raise HTTPException(
                        status_code=400,
                        detail="my_position type 'pin' requires latitude and longitude",
                    )
                if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
                    raise HTTPException(
                        status_code=400,
                        detail="latitude must be [-90, 90], longitude must be [-180, 180]",
                    )
            elif mp_type is not None:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid my_position type: {mp_type}",
                )
        # ... existing update_config logic ...
```

3. **Remove** `station_lat`/`station_lon` from `PacketProcessor` init in `create_app` (line ~77-80):

```python
        state.processor = PacketProcessor(
            broadcast_queue=state.broadcast_queue,
        )
```

4. **Remove** the hot-reload lines (226-228) that set `processor.station_lat`/`station_lon`.

5. **Add WebSocket broadcast** for config_updated after successful PUT:

```python
        # After update_config succeeds, broadcast my_position changes
        if my_pos is not None:
            await _broadcast_event("config_updated", {
                "my_position": asdict(state.config.station.my_position)
            })
```

- [ ] **Step 6: Update _enrich_with_bearing to async and fix all call sites**

Replace `_enrich_with_bearing` in `server.py`:

```python
async def _resolve_my_position() -> Optional[tuple[float, float]]:
    """Resolve current 'my position' coordinates."""
    if not state.config:
        return None
    mp = state.config.station.my_position
    if mp.type == "pin" and mp.latitude is not None and mp.longitude is not None:
        return (mp.latitude, mp.longitude)
    if mp.type == "station" and mp.callsign and state.storage:
        stn = await state.storage.get_station(mp.callsign)
        if stn and stn.get("latitude") and stn.get("longitude"):
            return (stn["latitude"], stn["longitude"])
    return None


async def _enrich_with_bearing(packet: dict) -> None:
    """Add bearing/distance to packet using my_position as reference."""
    from direwolf_dashboard.processor import (
        calculate_initial_compass_bearing,
        degrees_to_cardinal,
    )
    from haversine import haversine, Unit

    if not packet.get("latitude") or not packet.get("longitude"):
        return

    my_coords = await _resolve_my_position()
    if not my_coords:
        return

    try:
        pkt_coords = (packet["latitude"], packet["longitude"])
        bearing_deg = calculate_initial_compass_bearing(my_coords, pkt_coords)
        packet["bearing"] = degrees_to_cardinal(bearing_deg, full_string=True)
        packet["distance_miles"] = round(
            haversine(my_coords, pkt_coords, unit=Unit.MILES), 2
        )
    except Exception:
        pass
```

Note: `_enrich_with_bearing` is now `async` since it may do a DB lookup.

**CRITICAL: Update ALL call sites to await the async function:**

1. `get_packets()` (line ~146-147): change `_enrich_with_bearing(p)` to `await _enrich_with_bearing(p)`:

```python
    @app.get("/api/packets")
    async def get_packets(...):
        packets = await state.storage.query_packets(...)
        for p in packets:
            await _enrich_with_bearing(p)
        return packets
```

2. `websocket_endpoint()` initial burst (line ~315-316): change to `await`:

```python
            recent = await state.storage.query_packets(limit=50)
            for p in reversed(recent):
                await _enrich_with_bearing(p)
                await ws.send_json({"event": "packet", "data": p})
```

3. `_broadcast_consumer()` — already async, just ensure the call uses `await`.

- [ ] **Step 7: Update _broadcast_consumer to enrich and append bearing to compact_log**

Use the existing color scheme from `processor.py` (DEGREES_COLOR = `#FFA900`, DISTANCE_COLOR = `#FF5733`):

```python
async def _broadcast_consumer() -> None:
    while True:
        try:
            packet = await state.broadcast_queue.get()

            if state.storage:
                row_id = await state.storage.insert_packet(packet)
                packet["id"] = row_id

                if packet.get("latitude") and packet.get("longitude"):
                    await state.storage.upsert_station(
                        callsign=packet["from_call"],
                        last_seen=packet["timestamp"],
                        latitude=packet["latitude"],
                        longitude=packet["longitude"],
                        symbol=packet.get("symbol"),
                        symbol_table=packet.get("symbol_table"),
                        comment=packet.get("comment"),
                    )
                else:
                    stn = await state.storage.get_station(packet["from_call"])
                    if stn and stn.get("latitude") and stn.get("longitude"):
                        packet["latitude"] = stn["latitude"]
                        packet["longitude"] = stn["longitude"]
                        packet["symbol"] = packet.get("symbol") or stn.get("symbol")
                        packet["symbol_table"] = packet.get("symbol_table") or stn.get("symbol_table")
                        packet["position_from_db"] = True
                    await state.storage.upsert_station(
                        callsign=packet["from_call"],
                        last_seen=packet["timestamp"],
                    )

                # Enrich with bearing/distance from my_position
                await _enrich_with_bearing(packet)

                # Append bearing/distance to compact_log if present
                # Use same color scheme as the original format_compact_log
                if packet.get("bearing") and packet.get("compact_log"):
                    dist = packet.get("distance_miles", 0)
                    bearing_html = (
                        f' : <span style="color:#FFA900">{packet["bearing"]}</span>'
                        f'<span style="color:#FF5733">@{dist:.2f}miles</span>'
                    )
                    packet["compact_log"] += bearing_html

            await _broadcast_event("packet", packet)

        except asyncio.CancelledError:
            break
        except Exception as e:
            LOG.error(f"Broadcast consumer error: {e}")
```

- [ ] **Step 8: Write tests for _resolve_my_position edge cases**

Add to `tests/test_server.py`:

```python
class TestResolveMyPosition:
    """Test bearing/distance resolution from my_position."""

    async def test_pin_type_enriches_packets(self, test_app):
        app, storage = test_app
        with TestClient(app, raise_server_exceptions=False) as client:
            # Set pin position
            client.put("/api/config", json={
                "station": {"my_position": {"type": "pin", "latitude": 37.75, "longitude": -77.45}}
            })
            # Insert a packet with position
            await storage.insert_packet({
                "timestamp": time.time(), "from_call": "TEST", "to_call": "APRS",
                "type": "GPSPacket", "latitude": 38.0, "longitude": -78.0,
                "compact_log": "test", "raw_packet": "test",
            })
            resp = client.get("/api/packets")
            data = resp.json()
            assert any(p.get("bearing") for p in data)

    async def test_station_no_db_position_skips(self, test_app):
        app, storage = test_app
        with TestClient(app, raise_server_exceptions=False) as client:
            client.put("/api/config", json={
                "station": {"my_position": {"type": "station", "callsign": "UNKNOWN"}}
            })
            await storage.insert_packet({
                "timestamp": time.time(), "from_call": "TEST", "to_call": "APRS",
                "type": "GPSPacket", "latitude": 38.0, "longitude": -78.0,
                "compact_log": "test", "raw_packet": "test",
            })
            resp = client.get("/api/packets")
            data = resp.json()
            assert not any(p.get("bearing") for p in data)

    async def test_null_type_skips(self, test_app):
        app, storage = test_app
        with TestClient(app, raise_server_exceptions=False) as client:
            await storage.insert_packet({
                "timestamp": time.time(), "from_call": "TEST", "to_call": "APRS",
                "type": "GPSPacket", "latitude": 38.0, "longitude": -78.0,
                "compact_log": "test", "raw_packet": "test",
            })
            resp = client.get("/api/packets")
            data = resp.json()
            assert not any(p.get("bearing") for p in data)
```

- [ ] **Step 9: Update existing server tests**

Update `test_put_config_hot_reload` and `test_put_config_restart_required` to not reference callsign. Update `test_get_config` assertion to not check for callsign.

- [ ] **Step 10: Run all processor and server tests**

Run: `python -m pytest tests/test_processor.py tests/test_server.py -v`
Expected: All PASS

- [ ] **Step 11: Commit**

```bash
git add src/direwolf_dashboard/processor.py src/direwolf_dashboard/server.py tests/test_processor.py tests/test_server.py
git commit -m "feat: remove import endpoint, move bearing/distance to broadcast consumer with my_position"
```

### Task 4: Update CLI check command

**Files:**
- Modify: `src/direwolf_dashboard/cli.py:67-73`

- [ ] **Step 1: Update CLI check output**

In `cli.py`, the `check` command displays `config.station.callsign`. Replace that with lat/lon display:

```python
    click.echo(f"  Station lat/lon: {config.station.latitude}, {config.station.longitude}")
    click.echo(f"  Zoom: {config.station.zoom}")
    mp = config.station.my_position
    if mp.type == "station":
        click.echo(f"  My Position: tracking station {mp.callsign}")
    elif mp.type == "pin":
        click.echo(f"  My Position: pin at {mp.latitude}, {mp.longitude}")
    else:
        click.echo(f"  My Position: not set")
```

- [ ] **Step 2: Run full test suite to make sure nothing is broken**

Run: `python -m pytest -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add src/direwolf_dashboard/cli.py
git commit -m "feat: update CLI check command to display my_position instead of callsign"
```

---

## Chunk 2: Frontend — Remove Home Station, Add Cold Start

### Task 5: Remove home station marker and symbol picker from frontend

**Files:**
- Modify: `src/direwolf_dashboard/static/app.js:143-261` (remove symbol picker)
- Modify: `src/direwolf_dashboard/static/app.js:352-389` (remove home marker from initMap)
- Modify: `src/direwolf_dashboard/static/app.js:516-522` (remove home station check from getStationPosition)
- Modify: `src/direwolf_dashboard/static/app.js:604-624` (remove homeCall from createRoutingPolylines)
- Modify: `src/direwolf_dashboard/static/app.js:841-857` (remove home-based animation logic from onPacket)
- Modify: `src/direwolf_dashboard/static/app.js:576-695` (initSettings/populateSettings/saveSettings)
- Modify: `src/direwolf_dashboard/static/index.html` (remove settings fields, symbol picker modal)
- Modify: `src/direwolf_dashboard/static/style.css` (remove symbol picker CSS)

- [ ] **Step 1: Remove symbol picker code from app.js**

Remove functions: `updateSymbolPreview()` (lines 144-160), `renderSymbolGrid()` (lines 162-173), `initSymbolPicker()` (lines 175-261).

Remove the `initSymbolPicker()` call from the DOMContentLoaded init sequence (line 327).

- [ ] **Step 2: Remove home marker from initMap()**

Replace the initMap function (lines 352-389). Remove the home station marker block (lines 364-386). The new initMap will just create the map — centering logic is handled in Task 7.

```javascript
    function initMap() {
        const lat = config.station?.latitude || 0;
        const lon = config.station?.longitude || 0;
        const zoom = config.station?.zoom || 12;

        map = L.map('map').setView([lat, lon], zoom);

        L.tileLayer('/api/tiles/{z}/{x}/{y}.png', {
            attribution: '&copy; OpenStreetMap contributors',
            maxZoom: 18,
        }).addTo(map);

        setInterval(cleanupAnimations, 10000);
    }
```

- [ ] **Step 3: Remove home station from getStationPosition()**

Replace lines 516-522 (the home callsign check) — just use stations dict and cache:

```javascript
    function getStationPosition(callsign) {
        const s = stations[callsign];
        if (s && s.data && s.data.latitude != null && s.data.longitude != null) {
            return { lat: s.data.latitude, lng: s.data.longitude };
        }
        const cached = stationPositionCache[callsign];
        if (cached) return { lat: cached.lat, lng: cached.lng };
        return null;
    }
```

- [ ] **Step 4: Remove homeCall from createRoutingPolylines()**

Remove lines 616-624 that append the home station as the final waypoint. Routes are now purely packet-data-driven.

- [ ] **Step 5: Update onPacket() to remove home-based animations**

Replace onPacket (lines 821-858). Remove home-based TX/RX animation triggers:

```javascript
    function onPacket(packet) {
        addLogRow(packet);
        if (packet.latitude != null && packet.longitude != null) {
            updatePositionCache(packet.from_call, packet.latitude, packet.longitude);
        }
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

        // Animations based on my_position
        const myPos = getMyPosition();
        if (packet.tx) {
            if (myPos) {
                playTransmitAnimation('My Station', myPos.lat, myPos.lng, []);
            }
        } else {
            if (packet.latitude && packet.longitude) {
                playTransmitAnimation(packet.from_call, packet.latitude, packet.longitude, packet.path || []);
            }
            if (myPos) {
                const myCall = config.station?.my_position?.callsign;
                if (myCall) {
                    setTimeout(() => {
                        playReceiveAnimation(myCall, myPos.lat, myPos.lng);
                    }, 300);
                }
            }
        }
    }
```

Add helper `getMyPosition()`:

```javascript
    function getMyPosition() {
        const mp = config.station?.my_position;
        if (!mp || !mp.type) return null;
        if (mp.type === 'pin') {
            return mp.latitude != null && mp.longitude != null
                ? { lat: mp.latitude, lng: mp.longitude }
                : null;
        }
        if (mp.type === 'station' && mp.callsign) {
            return getStationPosition(mp.callsign);
        }
        return null;
    }
```

- [ ] **Step 6: Simplify settings modal in initSettings/populateSettings/saveSettings**

Remove callsign, symbol, symbol_table, conf_file, and import button references from:
- `initSettings()` — remove import button click handler and symbol-related listeners
- `populateSettings()` — remove lines setting `cfg-callsign`, `cfg-symbol`, `cfg-symbol-table`, `cfg-conf-file`
- `saveSettings()` — remove callsign/symbol/conf_file from the body sent to PUT

- [ ] **Step 7: Update index.html — remove settings fields and symbol picker modal**

In `src/direwolf_dashboard/static/index.html`:

Remove from the Station fieldset:
- `<label>Callsign <input ... id="cfg-callsign" /></label>`
- The entire `.symbol-row` div (symbol, table, preview, pick button)

Remove from the Direwolf fieldset:
- `<label>Config File <input ... id="cfg-conf-file" ... /></label>`
- The import row (import button, import feedback)

Remove the entire Symbol Picker Modal (`#symbol-picker-modal` div).

- [ ] **Step 8: Remove symbol picker CSS from style.css**

Remove `.symbol-picker-content`, `.symbol-tabs`, `.symbol-tab`, `.symbol-grid`, `.symbol-cell`, `.symbol-preview`, `.symbol-preview-name`, `.symbol-row` CSS rules.

- [ ] **Step 9: Commit**

```bash
git add src/direwolf_dashboard/static/app.js src/direwolf_dashboard/static/index.html src/direwolf_dashboard/static/style.css
git commit -m "feat: remove home station marker, symbol picker, and direwolf.conf import from frontend"
```

### Task 6: Add cold-start modal and toast

**Files:**
- Modify: `src/direwolf_dashboard/static/app.js` (initMap, startup logic)
- Modify: `src/direwolf_dashboard/static/index.html` (add modal and toast HTML)
- Modify: `src/direwolf_dashboard/static/style.css` (add toast CSS)

- [ ] **Step 1: Add waiting modal and toast HTML to index.html**

After the settings modal, add:

```html
    <!-- Waiting for Position Modal -->
    <div id="waiting-modal" class="modal hidden">
        <div class="modal-content waiting-modal-content">
            <div class="modal-header">
                <h2>Waiting for Stations</h2>
                <button id="btn-close-waiting">&times;</button>
            </div>
            <div class="modal-body">
                <p>Listening for APRS packets... The map will center on the first station heard with a position report.</p>
            </div>
        </div>
    </div>

    <!-- Position toast -->
    <div id="waiting-toast" class="toast hidden">
        Waiting for first position packet...
    </div>
```

- [ ] **Step 2: Add toast CSS to style.css**

```css
.toast {
    position: fixed;
    top: 50px;
    left: 50%;
    transform: translateX(-50%);
    background: var(--modal-bg, #16213e);
    color: var(--text, #e0e0e0);
    border: 1px solid var(--border, #2a2a4a);
    padding: 8px 20px;
    border-radius: 6px;
    font-size: 13px;
    z-index: 2000;
    box-shadow: 0 2px 8px rgba(0,0,0,0.5);
}

.waiting-modal-content {
    max-width: 400px;
    text-align: center;
}
```

- [ ] **Step 3: Implement startup center logic in app.js**

Add a new function `initMapCenter()` called after `initMap()` and `loadStations()`:

```javascript
    let waitingForPosition = false;

    async function initMapCenter() {
        const mp = config.station?.my_position;
        const zoom = config.station?.zoom || 12;

        // Priority 1: my_position
        if (mp && mp.type === 'pin' && mp.latitude != null && mp.longitude != null) {
            map.setView([mp.latitude, mp.longitude], zoom);
            return;
        }
        if (mp && mp.type === 'station' && mp.callsign) {
            const pos = getStationPosition(mp.callsign);
            if (pos) {
                map.setView([pos.lat, pos.lng], zoom);
                return;
            }
        }

        // Priority 2: config lat/lon
        const cfgLat = config.station?.latitude;
        const cfgLon = config.station?.longitude;
        if (cfgLat != null && cfgLon != null && (cfgLat !== 0 || cfgLon !== 0)) {
            map.setView([cfgLat, cfgLon], zoom);
            return;
        }

        // Priority 3: most recent station from DB (already loaded in stations dict)
        const stationKeys = Object.keys(stations);
        if (stationKeys.length > 0) {
            const first = stations[stationKeys[0]];
            if (first && first.data.latitude && first.data.longitude) {
                map.setView([first.data.latitude, first.data.longitude], zoom);
                return;
            }
        }

        // Priority 4: cold start — center (0,0), zoom 3, show modal
        map.setView([0, 0], 3);
        waitingForPosition = true;
        showWaitingModal();
    }

    function showWaitingModal() {
        const modal = document.getElementById('waiting-modal');
        modal.classList.remove('hidden');

        const closeBtn = document.getElementById('btn-close-waiting');
        closeBtn.addEventListener('click', () => {
            modal.classList.add('hidden');
            showWaitingToast();
        });
        modal.addEventListener('click', (e) => {
            if (e.target === modal) {
                modal.classList.add('hidden');
                showWaitingToast();
            }
        });
    }

    function showWaitingToast() {
        document.getElementById('waiting-toast').classList.remove('hidden');
    }

    function dismissWaiting() {
        waitingForPosition = false;
        document.getElementById('waiting-modal').classList.add('hidden');
        document.getElementById('waiting-toast').classList.add('hidden');
    }
```

- [ ] **Step 4: Update onPacket to handle first position packet**

Add to the top of onPacket, inside the position check:

```javascript
    function onPacket(packet) {
        addLogRow(packet);
        if (packet.latitude != null && packet.longitude != null) {
            updatePositionCache(packet.from_call, packet.latitude, packet.longitude);
        }
        if (packet.latitude && packet.longitude) {
            // First position packet — center map and dismiss waiting
            if (waitingForPosition) {
                dismissWaiting();
                const zoom = config.station?.zoom || 12;
                map.flyTo([packet.latitude, packet.longitude], zoom);
            }
            // ... rest of onPacket
```

- [ ] **Step 5: Update DOMContentLoaded init sequence**

```javascript
    document.addEventListener('DOMContentLoaded', async () => {
        await loadConfig();
        initMap();
        initLegend();
        await loadStations();
        loadStationPositions();
        loadTracks();
        await initMapCenter();
        connectWebSocket();
        initFilters();
        initSettings();
        initMapResize();
        initLogToggle();
        initMobileMenu();
    });
```

Note: `initSymbolPicker()` removed, `initMapCenter()` added after `loadStations()`.

- [ ] **Step 6: Commit**

```bash
git add src/direwolf_dashboard/static/app.js src/direwolf_dashboard/static/index.html src/direwolf_dashboard/static/style.css
git commit -m "feat: add cold-start modal/toast and smart map centering logic"
```

---

## Chunk 3: Frontend — My Position Feature

### Task 7: Add "Set as My Position" to station popups

**Files:**
- Modify: `src/direwolf_dashboard/static/app.js` (updateStationPopup, add save logic)

- [ ] **Step 1: Update updateStationPopup to include my_position button**

Replace `updateStationPopup()`:

```javascript
    function updateStationPopup(callsign) {
        const s = stations[callsign];
        if (!s) return;

        const d = s.data;
        let html = `<b>${callsign}</b><br>`;
        if (d.last_comment) html += `${d.last_comment}<br>`;
        if (d.packet_count) html += `Packets: ${d.packet_count}<br>`;
        if (d.last_seen) {
            const ago = Math.round((Date.now() / 1000 - d.last_seen) / 60);
            html += `Last seen: ${ago}m ago<br>`;
        }

        const mp = config.station?.my_position;
        const isMe = mp && mp.type === 'station' && mp.callsign === callsign;
        if (isMe) {
            html += `<button class="popup-btn popup-btn-remove" onclick="window._removeMyPosition()">Remove as My Position</button>`;
        } else {
            html += `<button class="popup-btn popup-btn-set" onclick="window._setMyPositionStation('${callsign}')">Set as My Position</button>`;
        }

        s.marker.bindPopup(html);
    }
```

- [ ] **Step 2: Add global my_position helper functions**

```javascript
    // Expose on window for popup button onclick
    window._setMyPositionStation = async function(callsign) {
        await saveMyPosition({ type: 'station', callsign: callsign });
        map.closePopup();
    };

    window._removeMyPosition = async function() {
        await saveMyPosition({ type: null });
        map.closePopup();
    };

    async function saveMyPosition(myPos) {
        try {
            const resp = await fetch('/api/config', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ station: { my_position: myPos } }),
            });
            if (resp.ok) {
                config.station.my_position = myPos;
                updateMyPositionMarker();
                // Center map on new position
                const pos = getMyPosition();
                if (pos) {
                    const zoom = config.station?.zoom || 12;
                    map.flyTo([pos.lat, pos.lng], zoom);
                }
                // Re-render all popups to update buttons
                for (const cs of Object.keys(stations)) {
                    updateStationPopup(cs);
                }
            }
        } catch (e) {
            console.error('Failed to save my_position:', e);
        }
    }
```

- [ ] **Step 3: Add visual indicator for "me" station**

```javascript
    let myPositionPinMarker = null;

    function updateMyPositionMarker() {
        const mp = config.station?.my_position;

        // Remove existing pin marker if any
        if (myPositionPinMarker) {
            map.removeLayer(myPositionPinMarker);
            myPositionPinMarker = null;
        }

        // Remove 'my-station' class from all markers
        for (const cs of Object.keys(stations)) {
            const el = stations[cs].marker.getElement();
            if (el) el.classList.remove('my-station');
        }

        if (!mp || !mp.type) return;

        if (mp.type === 'station' && mp.callsign && stations[mp.callsign]) {
            const el = stations[mp.callsign].marker.getElement();
            if (el) el.classList.add('my-station');
        }

        if (mp.type === 'pin' && mp.latitude != null && mp.longitude != null) {
            const pinIcon = L.divIcon({
                className: 'my-position-pin',
                html: '<div class="pin-marker">📍</div>',
                iconSize: [24, 24],
                iconAnchor: [12, 24],
            });
            myPositionPinMarker = L.marker([mp.latitude, mp.longitude], { icon: pinIcon })
                .addTo(map)
                .bindPopup('<b>My Position</b><br><button class="popup-btn popup-btn-remove" onclick="window._removeMyPosition()">Remove My Position</button>');
        }
    }
```

- [ ] **Step 4: Add CSS for visual indicator and popup buttons**

In `style.css`:

```css
.my-station {
    filter: drop-shadow(0 0 6px #00aaff) drop-shadow(0 0 3px #00aaff);
}

.my-position-pin .pin-marker {
    font-size: 24px;
    line-height: 1;
}

.popup-btn {
    display: block;
    width: 100%;
    margin-top: 6px;
    padding: 4px 8px;
    border: none;
    border-radius: 4px;
    cursor: pointer;
    font-size: 12px;
}

.popup-btn-set {
    background: #1a6b3c;
    color: #fff;
}

.popup-btn-set:hover {
    background: #228b4a;
}

.popup-btn-remove {
    background: #6b1a1a;
    color: #fff;
}

.popup-btn-remove:hover {
    background: #8b2222;
}
```

- [ ] **Step 5: Call updateMyPositionMarker() on startup**

In the DOMContentLoaded init, after `initMapCenter()`:

```javascript
        await initMapCenter();
        updateMyPositionMarker();
```

- [ ] **Step 6: Commit**

```bash
git add src/direwolf_dashboard/static/app.js src/direwolf_dashboard/static/style.css
git commit -m "feat: add 'Set as My Position' button in station popups with visual indicator"
```

### Task 8: Add "Drop Pin" feature

**Files:**
- Modify: `src/direwolf_dashboard/static/app.js` (pin placement mode)
- Modify: `src/direwolf_dashboard/static/index.html` (toolbar button)
- Modify: `src/direwolf_dashboard/static/style.css` (pin mode styling)

- [ ] **Step 1: Add Drop Pin toolbar button to index.html**

In the toolbar row, add a pin button:

```html
<button id="btn-drop-pin" title="Drop Pin — set my position">📌</button>
```

Also add it to the mobile menu.

- [ ] **Step 2: Implement pin placement mode in app.js**

```javascript
    let pinPlacementMode = false;

    function initDropPin() {
        const btn = document.getElementById('btn-drop-pin');
        btn.addEventListener('click', togglePinMode);

        // Long-press on map (mobile)
        let longPressTimer = null;
        let longPressPos = null;

        map.on('mousedown', (e) => {
            longPressPos = e.latlng;
            longPressTimer = setTimeout(() => {
                dropPinAt(longPressPos.lat, longPressPos.lng);
            }, 500);
        });
        map.on('mouseup', () => clearTimeout(longPressTimer));
        map.on('mousemove', () => clearTimeout(longPressTimer));

        // Right-click on map (desktop)
        map.on('contextmenu', (e) => {
            e.originalEvent.preventDefault();
            showPinContextPopup(e.latlng);
        });

        // Escape to cancel pin mode
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && pinPlacementMode) {
                togglePinMode();
            }
        });
    }

    function togglePinMode() {
        pinPlacementMode = !pinPlacementMode;
        const btn = document.getElementById('btn-drop-pin');
        btn.classList.toggle('active', pinPlacementMode);
        map.getContainer().style.cursor = pinPlacementMode ? 'crosshair' : '';

        if (pinPlacementMode) {
            map.once('click', onPinPlacementClick);
        } else {
            map.off('click', onPinPlacementClick);
        }
    }

    function onPinPlacementClick(e) {
        dropPinAt(e.latlng.lat, e.latlng.lng);
        // Exit pin mode
        pinPlacementMode = false;
        const btn = document.getElementById('btn-drop-pin');
        btn.classList.remove('active');
        map.getContainer().style.cursor = '';
    }

    async function dropPinAt(lat, lng) {
        await saveMyPosition({
            type: 'pin',
            latitude: Math.round(lat * 10000) / 10000,
            longitude: Math.round(lng * 10000) / 10000,
        });
    }

    function showPinContextPopup(latlng) {
        const popup = L.popup()
            .setLatLng(latlng)
            .setContent(
                `<b>Set My Position Here?</b><br>` +
                `<small>${latlng.lat.toFixed(4)}, ${latlng.lng.toFixed(4)}</small><br>` +
                `<button class="popup-btn popup-btn-set" onclick="window._dropPinFromPopup(${latlng.lat}, ${latlng.lng})">Set My Position</button>`
            )
            .openOn(map);
    }

    window._dropPinFromPopup = async function(lat, lng) {
        map.closePopup();
        await dropPinAt(lat, lng);
    };
```

- [ ] **Step 3: Add initDropPin() to init sequence**

In the DOMContentLoaded init, after `initMapCenter()`:

```javascript
        initDropPin();
```

- [ ] **Step 4: Add active button CSS**

```css
#btn-drop-pin.active {
    background: var(--accent, #0096c7);
    color: #fff;
}
```

- [ ] **Step 5: Commit**

```bash
git add src/direwolf_dashboard/static/app.js src/direwolf_dashboard/static/index.html src/direwolf_dashboard/static/style.css
git commit -m "feat: add drop pin feature with toolbar button, long-press, and right-click"
```

### Task 9: Add config_updated WebSocket handler and my_position tracking

**Files:**
- Modify: `src/direwolf_dashboard/static/app.js` (WebSocket handler, station tracking)

- [ ] **Step 1: Add config_updated handler in handleWSMessage**

```javascript
    function handleWSMessage(msg) {
        switch (msg.event) {
            case 'packet':
                onPacket(msg.data);
                break;
            case 'stats':
                onStats(msg.data);
                break;
            case 'status':
                setStatus(msg.data.agw_connected);
                break;
            case 'config_updated':
                onConfigUpdated(msg.data);
                break;
            case 'error':
                console.warn('Server error:', msg.data.message);
                break;
            case 'preload_progress':
                onPreloadProgress(msg.data);
                break;
            case 'ping':
                break;
        }
    }

    function onConfigUpdated(data) {
        if (data.my_position !== undefined) {
            config.station.my_position = data.my_position;
            updateMyPositionMarker();
            for (const cs of Object.keys(stations)) {
                updateStationPopup(cs);
            }
        }
    }
```

- [ ] **Step 2: Update onPacket to auto-update my_position reference when tracking a station**

In onPacket, when a position packet arrives for the tracked station, the animation reference point updates automatically because `getMyPosition()` uses `getStationPosition()` which reads from the stations dict (already updated by `addOrUpdateStation`).

No code change needed — the design naturally handles this since station positions are always resolved from the live stations dict.

- [ ] **Step 3: Commit**

```bash
git add src/direwolf_dashboard/static/app.js
git commit -m "feat: add config_updated WebSocket handler for multi-client my_position sync"
```

### Task 10: Remove simulateStations debug function references to home

**Files:**
- Modify: `src/direwolf_dashboard/static/app.js` (simulateStations function)

- [ ] **Step 1: Update simulateStations to use my_position**

The `simulateStations()` debug function (near end of app.js) references `config.station?.latitude` and `config.station?.callsign`. Update it to use `getMyPosition()` instead, or simply use a hardcoded test center if no position is set.

- [ ] **Step 2: Commit**

```bash
git add src/direwolf_dashboard/static/app.js
git commit -m "fix: update simulateStations debug function to use my_position"
```

---

## Chunk 4: Final Verification

### Task 11: Run full test suite and verify

**Files:**
- All test files

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest -v`
Expected: All PASS

- [ ] **Step 2: Check for any remaining references to removed fields**

Run: `grep -rn "callsign\|symbol_table\|conf_file\|parse_direwolf_conf\|import-direwolf-conf\|DIREWOLF_SYMBOL\|DIGIPI_DIREWOLF\|symbol_picker\|initSymbolPicker\|station_lat\|station_lon" src/ tests/ --include="*.py" --include="*.js" --include="*.html"`

Filter results for false positives (e.g., `callsign` is legitimately used for packet callsigns, `symbol_table` in DB columns). Only references to *config* callsign/symbol or direwolf.conf should be gone.

- [ ] **Step 3: Commit any final fixes**

```bash
git add -A
git commit -m "chore: final cleanup of removed field references"
```
