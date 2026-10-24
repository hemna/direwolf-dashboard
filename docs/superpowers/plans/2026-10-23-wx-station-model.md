# WX Station Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render weather-reporting stations on the MapLibre map as full NWS surface-analysis station plots (wind barb, temp/dewpoint/pressure, calm ring, omitted missing fields) with a state-driven legend, closing issue #152.

**Architecture:** Pure geometry math (barb decomposition, rotation, m/s→kt, presence mask) lives in a new Python module `station_model.py` because the repo is pytest-only (no JS runner); the backend attaches the computed model as `wx_model` beside `last_weather` on both station endpoints. The frontend draws that geometry to a canvas, registers it as a MapLibre runtime image per WX station, and switches the `stations-symbol-base` icon to it. A "Station plots" toggle (default on) controls both model rendering and a new "Weather stations" legend section in the existing map legend.

**Tech Stack:** Python 3.11+/Starlette (backend), pytest + pytest-asyncio, ruff, MapLibre GL JS runtime images + GeoJSON symbol layers (vanilla JS, no build step).

**Spec:** `docs/superpowers/specs/2026-10-23-wx-station-model-design.md` (committed `1e254b5`). The plan argues from the spec; executors read both.

## Global Constraints

- **Fully offline** target (DigiPi): no new dependencies, no CDN URLs; all assets local/vendored.
- No JS test runner: frontend behavior is verified by pytest static-analysis of `app.js`/`style.css` (pattern: `tests/test_maplibre_*.py`).
- Wind on the model is always **knots**; stored/app wind stays m/s (popup `fmtWind` untouched).
- Model predicate: any station with a truthy `last_weather`; `wx_model` is a sibling of `last_weather`, absent when no weather.
- One issue → one PR; PR body opens with a plain-English paragraph; CHANGELOG updated under `[Unreleased]`.
- Lint: `uv run ruff check .` must pass; tests: `uv run pytest tests/ -q` green.

## Review Focus

1. **Station with weather but no wind** (`last_weather` with temp only): must render a circle + temp/dewpoint, no shaft, no calm ring → pinned in Task 2 (`build_station_model` presence logic) and Task 4 (canvas draws nothing for a missing barb).
2. **`_` WX packets** (canonical APRS weather format): aprslib returns `course`/`speed`, never `wind_direction`/`wind_speed`; without Task 1 no barb can ever render → regression-tested from a real packet in Task 1.
3. **0 kt vs missing wind**: 0 kt = calm (outer ring, no barb); missing wind = neither ring nor barb → pinned in Task 2 matrix.
4. **0-vs-360 direction**: 0° and 360° both plot straight up (wind from north) → pinned in Task 2 rotation tests.
5. **Runtime images die on style.load** (theme switch): models must re-register or stations silently lose their icon → pinned in Task 4's `style.load` re-registration.

---
---

### Task 1: Backend wind extraction fix (D1a)

**Files:**
- Modify: `src/direwolf_dashboard/decoder.py` (add `extract_wind`; use it in `_categorize_sections`)
- Modify: `src/direwolf_dashboard/lifecycle.py:283-289` (use `extract_wind` for `wind_direction`/`wind_speed`)
- Test: `tests/test_wind_extraction.py` (new), `tests/test_lifecycle.py:547` (extend `TestStoreWeatherReport`)

**Interfaces:**
- Consumes: none.
- Produces: `decoder.extract_wind(parsed: dict) -> tuple[float | None, float | None]` returning `(wind_direction_deg, wind_speed_mps)`. `_categorize_sections(parsed)` now includes `wind_direction`/`wind_speed` in its `weather` section for `_` packets. `lifecycle._store_weather_report` stores them.

- [ ] **Step 1: Write the failing unit test for `extract_wind`**

```python
# tests/test_wind_extraction.py
import aprslib
from direwolf_dashboard.decoder import extract_wind, _categorize_sections

RAW = "TEST1>APRS,TCPXX*:!4713.58N/08724.97W_230/040g055t068r000p000P000h50b10051.DsVP"

def test_wind_extraction_from_underscore_packet():
    parsed = aprslib.parse(RAW)
    assert parsed["wind_direction"] is None
    assert parsed["wind_speed"] is None            # the gap being fixed
    direction, speed = extract_wind(parsed)
    assert direction == 230
    assert speed == pytest.approx(74.08 * 1000 / 3600, abs=0.01)  # 20.58 m/s

def test_wind_extraction_prefers_weather_dict():
    parsed = {"weather": {"wind_direction": 90, "wind_speed": 5.0}}
    assert extract_wind(parsed) == (90, 5.0)

def test_wind_extraction_none_when_absent():
    assert extract_wind({"weather": {}}) == (None, None)

def test_categorize_sections_includes_wind_for_underscore():
    parsed = aprslib.parse(RAW)
    w = _categorize_sections(parsed)["weather"]
    assert w["wind_direction"] == 230
    assert w["wind_speed"] == pytest.approx(74.08 * 1000 / 3600, abs=0.01)
```

- [ ] **Step 2: Run to confirm failure**

Run: `uv run pytest tests/test_wind_extraction.py -v`
Expected: FAIL with `ImportError: cannot import name 'extract_wind'`.

- [ ] **Step 3: Implement `extract_wind` in `src/direwolf_dashboard/decoder.py`**

Signature and behavior: prefer `weather.wind_direction`/`weather.wind_speed` when present in the `weather` dict; else map top-level `course` → direction and top-level `speed` (km/h) → m/s via `* 1000 / 3600`. Returns `(None, None)` when neither source has wind. One short docstring quoting why (aprslib `_` quirk).

- [ ] **Step 4: Wire into `_categorize_sections` and `lifecycle._store_weather_report`**

In `decoder._categorize_sections`, after the weather-field loop, merge `extract_wind(parsed)` into `weather_data` for any missing wind keys. In `lifecycle._store_weather_report`, replace the `wind_direction = _first(weather, parsed, ...)` / `wind_speed = _first(...)` lines (lifecycle.py ~287-288) with `wind_direction, wind_speed = extract_wind(parsed)` (import it) — keep the m/s unit.

- [ ] **Step 5: Extend `TestStoreWeatherReport` with a real-packet regression test**

Add to `tests/test_lifecycle.py`:

```python
async def test_underscore_packet_stores_wind(self):
    """_ WX packets carry wind as top-level course/speed(km/h); they must
    reach storage as wind_direction/wind_speed (regression for the station model)."""
    services = self._make_services()
    await _store_weather_report(
        {"from_call": "TEST1", "timestamp": 123.0,
         "raw_packet": "TEST1>APRS,TCPXX*:!4713.58N/08724.97W_230/040g055t068"
                       "r000p000P000h50b10051.DsVP"},
        services,
    )  # does NOT patch aprslib.parse — drives the real parser
    report = services.storage.insert_weather_report.await_args.args[0]
    assert report["wind_direction"] == 230
    assert report["wind_speed"] == pytest.approx(74.08 * 1000 / 3600, abs=0.01)
    # temp 20C / rh 50%  ->  dewpoint ~9.25C (also pins AC3)
    assert report["dewpoint"] == pytest.approx(9.25, abs=0.05)
```

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest tests/ -q`
Expected: PASS (existing 374 + new tests).

- [ ] **Step 7: Commit**

```bash
git add src/direwolf_dashboard/decoder.py src/direwolf_dashboard/lifecycle.py tests/test_wind_extraction.py tests/test_lifecycle.py
git commit -m "fix(decoder): extract wind_direction/wind_speed from _ WX packets (issue #152)"
```

---

### Task 2: Pure station-model math module

**Files:**
- Create: `src/direwolf_dashboard/station_model.py`
- Test: `tests/test_station_model.py` (new)

**Interfaces:**
- Consumes: nothing (pure module).
- Produces, for Task 3 / Task 4:
  - `station_model.KT_PER_MPS = 1.94384`
  - `station_model.wind_barb(speed_kt: float) -> list[str]` — list of feather kinds **inside→outside**, pennant innermost: `"pennant" | "full" | "half"`; `[]` when speed rounds to 0.
  - `station_model.barb_rotation(direction_deg: float) -> float` — compass degrees toward wind-from (identity), normalized to `[0, 360)`.
  - `station_model.build_station_model(last_weather: dict | None) -> dict | None` — `None` for falsy/empty `last_weather`; else
    `{rotation_deg, calm, feathers, gust_kt, has_temp, has_dewpoint, has_pressure}` where `calm` is `True` when wind speed ≥ 0 but rounds to 0 (0 kt), `feathers` `[]` when calm or wind missing, `gust_kt` only when a gust is present (`None` silently omitted from the dict), and the `has_*` flags are `True` when the corresponding value is not `None`. Rotated values: `wind_speed`/`wind_gust` come in **m/s** and are converted with `KT_PER_MPS`.

- [ ] **Step 1: Write the failing barb-matrix + rotation tests**

```python
# tests/test_station_model.py
from direwolf_dashboard.station_model import wind_barb, barb_rotation, build_station_model, KT_PER_MPS

KT = KT_PER_MPS

def test_barb_matrix():
    assert wind_barb(0) == []            # calm handled by caller
    assert wind_barb(3) == ["half"]      # nonzero < 5 -> one half barb
    assert wind_barb(5) == ["half"]      # rounds to 5 -> 1 half
    assert wind_barb(7) == ["half"]      # rounds to 5
    assert wind_barb(25) == ["full", "full", "half"]
    assert wind_barb(50) == ["pennant"]
    assert wind_barb(75) == ["pennant", "full", "full", "half"]
    assert wind_barb(40) == ["full", "full", "full", "full"]

def test_rotation_directions():
    assert barb_rotation(0) == 0
    assert barb_rotation(360) == 0       # 0-vs-360 equivalence
    assert barb_rotation(90) == 90
    assert barb_rotation(230) == 230

def test_build_station_model_aprs_only():
    m = build_station_model({"temperature": 20.0, "dewpoint": 9.3,
                             "wind_direction": 230, "wind_speed": 20.58, "humidity": 50})
    assert m == {"rotation_deg": 230, "calm": False, "feathers": ["full", "full", "full", "full"],
                 "has_temp": True, "has_dewpoint": True, "has_pressure": False}
    assert "gust_kt" not in m

def test_build_station_model_metar_style_full():
    m = build_station_model({"temperature": 25.0, "dewpoint": 15.0, "pressure": 1012.0,
                             "wind_direction": 90, "wind_speed": 38.57, "wind_gust": 30.88,
                             "visibility": 10.0, "sky_cover": 6, "wx_code": "RA"})
    assert m["calm"] is False
    assert m["feathers"] == ["pennant", "full", "full", "half"]  # ~75 kt
    assert m["gust_kt"] == pytest.approx(30.88 * KT, abs=0.01)
    assert m["has_pressure"] is True

def test_build_station_model_calm_vs_missing_wind():
    calm = build_station_model({"wind_direction": 0, "wind_speed": 0.0})
    assert calm["calm"] is True and calm["feathers"] == []
    none = build_station_model({})
    assert none is None
    nw = build_station_model({"temperature": 10.0})   # no wind at all
    assert nw["calm"] is False and nw["feathers"] == [] and nw["rotation_deg"] is None
    assert nw["has_temp"] is True and nw["has_dewpoint"] is False
```

- [ ] **Step 2: Run to confirm failure**

Run: `uv run pytest tests/test_station_model.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'direwolf_dashboard.station_model'`.

- [ ] **Step 3: Implement the three functions in `src/direwolf_dashboard/station_model.py`**

Pure functions, no I/O. `wind_barb`: round half-up to nearest 5 kt, then decompose 50→`"pennant"`, 10→`"full"`, 5→`"half"`, emit inside→outside. `barb_rotation`: `direction_deg % 360`. `build_station_model`: presence flags from `is not None` checks (explicit — 0 is a valid temperature); `calm = wind speed is not None and round(...) == 0`; `gust_kt` and value conversions use `KT_PER_MPS`. Omit `gust_kt` when no gust (absent key, not `None`).

- [ ] **Step 4: Run the model tests**

Run: `uv run pytest tests/test_station_model.py -v`
Expected: PASS. If `wind_barb(40)` or rounding disagrees with the matrix, the test is the spec — fix the implementation, not the matrix.

- [ ] **Step 5: Commit**

```bash
git add src/direwolf_dashboard/station_model.py tests/test_station_model.py
git commit -m "feat(152): pure station-model math — barbs, rotation, presence mask (issue #152)"
```

---

### Task 3: `wx_model` in the station payloads

**Files:**
- Modify: `src/direwolf_dashboard/routers.py:215-225` (`get_stations`) and `:428-440` (`get_weather_stations`)
- Test: `tests/test_routers.py` (extend `TestStationsWeather` ~line 136 and `TestWeatherStationsEndpoint` ~line 370)

**Interfaces:**
- Consumes: `station_model.build_station_model` (Task 2).
- Produces: both `/api/stations` and `/api/weather/stations` include `wx_model` (the Task 2 dict) on entries whose `last_weather` is present; no `wx_model` key otherwise. `last_weather` itself is unchanged.

- [ ] **Step 1: Write/extend the failing router tests**

```python
# in TestStationsWeather:
def test_stations_weather_station_gets_wx_model(self):
    wx = {"callsign": "W1WX", "temperature": 72.5, "dewpoint": 60.0,
          "wind_direction": 230, "wind_speed": 10.3, "timestamp": 1234567890.0}
    ...same _make_services as neighbour tests...
    resp = client.get("/api/stations")
    assert resp.status_code == 200
    entry = next(s for s in resp.json() if s["callsign"] == "W1WX")
    assert "wx_model" in entry
    assert entry["wx_model"]["rotation_deg"] == 230
    assert entry["wx_model"]["has_temp"] is True

# in TestWeatherStationsEndpoint:
def test_weather_station_gets_wx_model(self):
    ...W1WX with wind...
    resp = client.get("/api/weather/stations")
    entry = next(s for s in resp.json() if s["callsign"] == "W1WX")
    assert "wx_model" in entry

def test_station_without_weather_has_no_wx_model(self):
    ...existing no-weather cases unchanged... assert "wx_model" not in entry
```

- [ ] **Step 2: Run to confirm failure**

Run: `uv run pytest tests/test_routers.py -k "wx_model" -v`
Expected: FAIL (assertion error — key absent).

- [ ] **Step 3: Wire `wx_model` in `routers.py`**

In both endpoint paths, where `station["last_weather"] = latest_weather[cs]` (or the slim-entry construction) happens, add `station["wx_model"] = build_station_model(latest_weather[cs])`. Import `from direwolf_dashboard.station_model import build_station_model`.

- [ ] **Step 4: Run the router tests**

Run: `uv run pytest tests/test_routers.py -v`
Expected: PASS (existing + new).

- [ ] **Step 5: Commit**

```bash
git add src/direwolf_dashboard/routers.py tests/test_routers.py
git commit -m "feat(152): expose wx_model station-model geometry in the station APIs (issue #152)"
```

---

### Task 4: MapLibre runtime-image rendering

**Files:**
- Modify: `src/direwolf_dashboard/static/app.js`
- Test: `tests/test_maplibre_wx_station_model.py` (new, static-analysis pattern from `test_maplibre_station_label.py`)

**Interfaces:**
- Consumes: `wx_model` property on the station payload (Task 3) and `last_weather` (for temp/pressure text).
- Produces: station features get `properties.wx_model` = registered image id (e.g. `"wx-W1WX"`) when the toggle is on and the station has `last_weather`; key absent otherwise. Helpers: `upsertWxStationImage(cs, last_weather)`, `reRegisterWxStationImages()`, `wxStationEnabled()`.

- [ ] **Step 1: Write the failing static tests**

```python
# tests/test_maplibre_wx_station_model.py  (reuse _layer() helper pattern from test_maplibre_station_label.py)

def test_symbol_base_icons_pick_runtime_image_when_present():
    base = _layer("stations-symbol-base")
    assert "wx_model" in base          # icon-image case references the runtime image key
    assert "'icon-allow-overlap': true" in base

def test_model_image_registered_on_style_load():
    assert "reRegisterWxStationImages" in JS          # called from addStationLayers (style.load hook)
    assert "map.addImage" in JS
    assert "upsertWxStationImage" in JS

def test_model_uses_last_weather_and_toggle():
    assert "last_weather" in JS        # upsert reads the weather payload
    assert "wxStationEnabled" in JS    # gated by the toggle

def test_feature_property_set_when_weather_present():
    assert "properties.wx_model" in JS  # set where stationFeatures props are built
```

- [ ] **Step 2: Run to confirm failure**

Run: `uv run pytest tests/test_maplibre_wx_station_model.py -v`
Expected: FAIL (`AssertionError` on first missing string, or ModuleNotFoundError — create the file first).

- [ ] **Step 3: Implement the model image pipeline in `app.js`**

At the `stationFeatures` property build site (app.js ~1515-1525, next to `props.overlay_hex`): if `wxStationEnabled()` and `data.last_weather`, set `props.wx_model = upsertWxStationImage(cs, data.last_weather)`; else leave the key absent.

`upsertWxStationImage(cs, last_weather) -> string`:
- Build the geometry from the station entry's `wx_model` (the payload sibling of
  `last_weather`; it was computed on the backend in Task 3) — draw it via the
  attached `wx_model` geometry, don't recompute it. `last_weather` supplies the
  raw values the drawing formats as text.
- Draw onto a canvas: station circle (open when no sky cover; filled per `sky_cover` oktas when present), wind shaft from the circle pointing in `rotation_deg` (0 = up = wind-from-north), feathers from `feathers` (inside→outside: pennant closest), calm outer ring when `calm`, temp top-left, dewpoint bottom-left, pressure top-right (text via existing `fmtTemp`/`fmtPressure`), gust annotation `G<n> kt` beside the barb tip (offset near the shaft tip; exact offset is the drawing's call, note it in the PR). Halo/text-shadow for legibility on the dark basemap.
- Register `map.addImage('wx-' + cs, imgData, { pixelRatio: devicePixelRatio })` (re-adding replaces in place) and return `'wx-' + cs`.

`reRegisterWxStationImages()`: re-run `addImage` for every callsign that has a registered canvas (keep a small `wxImageCache = {}` keyed by callsign holding the latest `ImageData`), then `refreshStations()` so features reference valid images. Call it from `addStationLayers` (already re-run on `style.load` — this is what survives theme switches).

- [ ] **Step 4: Update `stations-symbol-base` icon-image to branch on `wx_model`**

```
'icon-image': ['case',
    ['has', 'wx_model'], ['get', 'wx_model'],
    ['case', ['==', ['get', 'symbol_table'], '/'], <existing APRS expr...>, <existing APRS expr...>]]
```
Keep `icon-allow-overlap`/`icon-ignore-placement` as-is.

- [ ] **Step 5: Run static tests + full suite**

Run: `uv run pytest tests/test_maplibre_wx_station_model.py tests/test_maplibre_main_map.py tests/test_maplibre_station_label.py -v` then `uv run pytest tests/ -q`
Expected: PASS. Browser-verify: run `uv run direwolf-dashboard serve` with a live/AGW feed (or the manual fixture step below) — a WX station shows a barbed plot, non-WX stations keep their sprite.

- [ ] **Step 6: Commit**

```bash
git add src/direwolf_dashboard/static/app.js tests/test_maplibre_wx_station_model.py
git commit -m "feat(152): render WX stations as NWS station plots via MapLibre runtime images (issue #152)"
```

---

### Task 5: "Station plots" toggle + legend

**Files:**
- Modify: `src/direwolf_dashboard/static/app.js`, `src/direwolf_dashboard/static/style.css`
- Test: `tests/test_maplibre_wx_station_model.py` (extend; static pattern)

**Interfaces:**
- Consumes: `wxStationEnabled()` (Task 4) and the `map-legend` markup from `initLegend` (app.js ~349).
- Produces: `setWxLegendVisible(show: bool)`; a `#legend-wx` block inside the map legend body (legend HTML already templated in `initLegend`); `#filter-station-model` checkbox row in the filter overlay (app.js ~468-510).

- [ ] **Step 1: Write/extend the failing static tests**

```python
def test_toggle_checkbox_exists_default_on_persisted():
    assert "filter-station-model" in JS
    assert "checked" in JS                       # default on
    assert "dw-station-model" in JS              # persistence key

def test_legend_section_present_and_toggle_drives_visibility():
    assert "legend-wx" in JS                     # section id inside initLegend template
    assert "setWxLegendVisible" in JS            # show/hide by state

def test_legend_strings_match_barb_rules():
    legend = JS  # legend template is inline JS string
    for s in ("5", "10", "50", "kt", "calm"):
        assert s in legend
    assert "°" in legend                          # temperature unit
    assert "inHg" in legend or "hPa" in legend    # pressure unit as displayed

def test_off_state_falls_back_to_sprite():
    assert "wxStationEnabled" in JS or "filter-station-model" in JS  # gating in upsert path
```

- [ ] **Step 2: Run to confirm failure**

Run: `uv run pytest tests/test_maplibre_wx_station_model.py -v`
Expected: FAIL (missing strings).

- [ ] **Step 3: Implement `wxStationEnabled()` and the checkbox**

Read `localStorage.getItem('dw-station-model')` (default `'1'`). Add a checkbox row to the filter overlay markup (`#filter-station-model`, label "Station plots", default checked when stored value != `'0'`). On `change`: persist the value, then call `refreshStations()` (repaints models or falls back to sprites) and `setWxLegendVisible(...)`.

- [ ] **Step 4: Implement `setWxLegendVisible` + legend section**

In `initLegend`'s template, append a `<div id="legend-wx">` section: a labeled example model (temp top-left, dewpoint bottom-left, pressure top-right), "half barb = 5 kt, full barb = 10 kt, pennant = 50 kt, calm = circle around the station", gust annotation, sky-cover fill note, and units ("wind kt", temperature °F/°C as displayed, pressure as displayed). `setWxLegendVisible(show)` toggles `#legend-wx` display; call it once at startup (map to `wxStationEnabled()`) and on every checkbox change. Keep it inside the existing legend body so it lives only in the main map's legend (cannot appear in the decode mini-map).

- [ ] **Step 5: Run static tests + full suite + lint**

Run: `uv run pytest tests/ -q && uv run ruff check .`
Expected: PASS, no lint errors.

- [ ] **Step 6: Commit**

```bash
git add src/direwolf_dashboard/static/app.js src/direwolf_dashboard/static/style.css tests/test_maplibre_wx_station_model.py
git commit -m "feat(152): station-plot layer toggle and state-driven NWS legend (issue #152)"
```

---

### Task 6: CHANGELOG, screenshots, delivery

**Files:**
- Modify: `CHANGELOG.md` (`[Unreleased]` — add an `### Added` section)
- Modify: none else

**Interfaces:** nothing code-level.

- [ ] **Step 1: Add the CHANGELOG entry**

Under `[Unreleased]`, `### Added`:
"**Weather-station map symbol is a full NWS surface-analysis station plot (issue #152)** — wind barb (5/10/50 kt feathers, calm ring), temperature, dewpoint, pressure and gust where the station reports them; missing fields are omitted. New 'Station plots' toggle (default on) with a state-driven legend explaining the symbols and units. Also fixes wind extraction for `_` WX packets, which previously never stored wind."

- [ ] **Step 2: Full verification**

Run: `uv run pytest tests/ -q` (all green) and `uv run ruff check .` (clean).

- [ ] **Step 3: Manual screenshot procedure (documented in the PR, not automatable)**

- Reduced (APRS-only) model: point the running app at a live radio/AGW feed until a WX station reports temp+wind; screenshot at readable zoom.
- Full model (METAR-style fields): this pipeline has no METAR source, so inject a synthetic `last_weather` carrying `visibility`/`sky_cover`/`wx_code` for one station during a local run (temporary, not committed) and screenshot it; state this injection in the PR.
- Legend: with "Station plots" on, screenshot the legend over a populated map.
- Zoom-legibility note (AC5): check default zoom; if the full model needs a minimum zoom, state it in the PR.
- Include all shots as clickable markdown links in the PR body.

- [ ] **Step 4: Final commit**

```bash
git add CHANGELOG.md
git commit -m "docs(152): changelog for NWS station-plot map symbols (issue #152)"
```

---
---

**Execution handoff:** base branch `refactor/leaflet-removal`; branch `feature/152-wx-station-model`; worktree `~/devel/mine/hamradio/direwolf-dashboard-152`. Tasks are sequential but backend tasks (1–3) are independent of frontend test scaffolding; each ends with a green suite and a commit.
