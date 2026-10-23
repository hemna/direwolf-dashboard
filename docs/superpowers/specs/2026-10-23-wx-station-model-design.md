# WX Station Model — Design (issue #152)

Date: 2026-10-23 · Branch: `feature/152-wx-station-model` (base `refactor/leaflet-removal` @ cf2ee67)
Author: engineer (Walt approved the go + MapLibre base)

## Intent and why

Today every station, including weather stations, renders as the same generic APRS
sprite icon. Hemna wants the weather-station symbol to be a **full NWS
surface-analysis station plot**: the standard layout with the wind barb growing
out of a station circle and temperature, dewpoint, pressure and (when present)
visibility/sky-cover/weather fields placed at their fixed positions around it.
Fields the station does not report are omitted — no placeholder, no zero. A
legend explaining the symbols and units appears only while the weather-station
model layer is enabled.

This is one of three cross-linked issues with the same requirement: android #460,
haminfo #264, and this one. Each repo implements it against its own map stack.

## Goals

- Weather-reporting stations render the station model with every available
  field, placed per the Pilot Institute surface-analysis article layout.
- Barbs read per NWS rules: half barb 5 kt, full barb 10 kt, pennant 50 kt,
  pennants innermost, calm (0 kt) = outer ring with no barb.
- Missing fields are omitted (a radio-fed APRS WX station renders as
  circle + barb + temp + dewpoint; fields without a data source are structurally
  omittable).
- Pure math (barb decomposition, rotation, dewpoint) is unit-tested in pytest —
  the repo has no JS test runner, so the math lives in Python.
- Legend shows a labeled example model, barb reading rules and units; it appears
  when the model layer is enabled and disappears when the layer is off.

## Non-goals

- **No METAR ingestion.** This pipeline is fed by Direwolf/AGW (radio) data only;
  `visibility`, `sky-cover` and `present-weather` therefore have no source here
  and are always omitted. The design keeps those fields structurally omittable
  so a future METAR-fed source can light them up without rework.
- **No rain on the model.** Rain 1h/24h stays only in the station detail
  readout, per the issue.
- No pressure-trend element (the weather report has no 3-hour change field; it
  is always absent and never rendered).
- No new frontend build system or test runner.

## What already exists (verified in this session)

Backend (all on the base branch):

- `decoder.py` parses APRS WX packets into `temperature`, `humidity`, `pressure`,
  `wind_direction`, `wind_speed`, `wind_gust`, `rain_1h`, `rain_24h`.
- **Known gap (measured this session):** for the canonical `_` WX format,
  aprslib returns the wind as top-level `course` (deg) and `speed` (km/h) — not
  `wind_direction`/`wind_speed` — so today `_` stations store no wind at all.
  The barb requirement cannot be met without fixing this (see D1a).
- `lifecycle.py` already derives **dewpoint** via the Magnus formula
  (`a=17.27, b=237.7`, alpha → dewpoint) and stores it in the weather report.
- Storage layer persists these fields per report; `routers.py` exposes
  `last_weather` on `/api/stations` (line 224) and the slim
  `/api/weather/stations` endpoint (`{callsign, last_weather}`), plus
  `/api/weather/{callsign}` history. Weather packets arrive via the same
  `/api/stations` refresh the map already uses, so `last_weather` is already in
  the browser.
- Unit convention today: wind is **m/s** in the stored data (`fmtWind(ms)`
  renders m/s or mph; aprslib gives gust and Peet-Bros `wind_speed` in m/s).

Frontend (MapLibre):

- Single `stations` GeoJSON source (`stationFeatureCollection`) backing symbol
  layers: `stations-my-glow`, `stations-symbol-base` (APRS sprite via
  `icon-image` case on `symbol_table`/`symbol_hex`), `stations-symbol-overlay`,
  and `stations-label` (callsign text). Click/hover are bound to the symbol and
  label layers.
- Station popup already prints an inline weather summary from `d.last_weather`
  (temp, wind, humidity).
- An existing `map-legend` panel (sections "Animations", "Trails") is rendered
  bottom-left (`initLegend`, `mlCreate('map-legend')`), collapse state persisted.
- The station "Type" filter dropdown has a `WeatherPacket` option; there is no
  weather-station map **layer toggle** today.

## Design

### D1. Pure math lives in Python; JS only draws

New module `src/direwolf_dashboard/station_model.py` (pure, no I/O):

- `wind_barb(speed_kt) -> {feathers: [...]  , calm: bool}`
  speed → nearest-5-kt feather decomposition (pennants innermost, then full,
  then half; nonzero < 5 kt = one half barb; 0 kt = calm).
- `barb_rotation(deg) -> float` (direction point, 0/360 handled).
- `build_station_model(last_weather) -> dict | None` — presence-mask plus the
  geometry: `calm`, `feathers`, `rotation_deg`, `gust_kt` (when present),
  `has_temp`, `has_dewpoint`, `has_pressure`, incl. `dewpoint` (reuse/re-check
  against the existing Magnus derivation).
- **Units:** the model and its legend/math are always **knots** (NWS
  convention). All wind values are converted m/s → kt (×1.94384) inside
  `station_model.py`; the existing app popup/panel wind display (`fmtWind`,
  m/s or mph by preference) is untouched.
- Models are composed per station at response time; raw values are sent, and the
  frontend formats temperature/pressure text with its existing
  `fmtTemp`/`fmtPressure` helpers (the °F/°C and inHg/hPa preference stays where
  it already lives).

### D1a. Fix `_` packet wind extraction (root issue before any barb)

aprslib surfaces `_` WX wind as top-level `course` (deg) / `speed` (km/h) —
it scales the raw position-suffix digits by x1.852 as if they were knots, but
the `_` comment's speed digits are **mph** per the APRS weather-field spec.
Add one shared helper in `decoder.py`:

- `extract_wind(parsed) -> (direction_deg | None, speed_mps | None)` —
  prefer `weather.wind_direction`/`weather.wind_speed` when present (already
  m/s); for symbol-`_` packets map `course` → direction and recover the raw
  digits (`speed / 1.852`), converting mph → m/s (x0.44704). Course/speed on
  any other packet is course-over-ground and is not wind.
- Used by `_categorize_sections` (the `/api/decode` display) **and**
  `lifecycle._store_weather_report` (storage + `last_weather`), so both paths
  agree. Stored unit stays **m/s** (consistent with existing gust/Peet data).

Wiring: `routers.py` composes the model as a sibling of `last_weather`:
`station["wx_model"] = build_station_model(latest_weather[cs])` where a WX
report exists, in both `/api/stations` and `/api/weather/stations`; the key is
absent when the station has no weather, which is the frontend's signal to keep
the plain sprite. Models are recomputed at response time, so unit/date changes
need no re-derivation.

Why Python: the issue's acceptance criteria require unit tests against a value
matrix (0, 3, 5, 7, 25, 50, 75 kt; N/E/S/W; 0-vs-360; calm; missing-wind; dew
point pairs). pytest is the only test infrastructure in the repo; duplicating the
math in JS would create two sources of truth. The frontend consumes geometry and
draws.

### D2. MapLibre rendering: per-station runtime images

A station model is a structured drawing (circle, colored fill by sky-cover when
present, barb, and text at fixed offsets) that cannot come from the APRS sprite.
Draw each WX station's model onto a `<canvas>` in JS, register it with
`map.addImage('wx-<callsign>', imgData, { sdf: false, pixelRatio })`, and let
`stations-symbol-base`'s `icon-image` choose it for stations that have a model
(`['case', ['has','wx_model'], ['get','wx_model'], <existing APRS sprite expr>]`).

- Image lifecycle: re-register after every `style.load` (theme switches reload
  the style, which drops runtime images — the code already re-adds overlay
  sources on style.load; same hook). Regenerate + re-register when the station's
  `last_weather` changes during refresh.
- Weather stations are sparse; the runtime-image cost is small on the Pi target.
- `stationFeatures` gains a `wx_model` property (populated from the payload);
  non-WX stations keep the sprite.

### D3. Layer toggle + state-driven legend

- Add a checkbox **"Station plots"** to the existing filter overlay
  (`filter-station-model`), default **on**; state persisted (localStorage, same
  idiom as `dw-legend-collapsed`).
- When **off**: WX stations fall back to the plain APRS sprite (model images not
  registered / not referenced) and the legend section is removed from the DOM.
  When **on**: models render and the legend section shows.
- Legend: add a **"Weather stations"** section to the existing `map-legend`
  body with a small labeled SVG/canvas example model (temp top-left, dewpoint
  bottom-left, pressure top-right), the barb rules in text
  ("half barb = 5 kt, full barb = 10 kt, pennant = 50 kt, calm = circle around
  the station"), gust annotation note, sky-cover fill note, and the units in use
  (**wind in kt** — the model's fixed unit; temperature °F/°C and pressure as
  the app displays). This is the main map's legend; it cannot linger on the
  decode mini-map (separate MapLibre instance) by construction.

### D4. Gust annotation placement

Draw the gust as text just past the barb tip, on the upwind side of the shaft
(e.g. `G<n> kt`). Exact pixel offset is the canvas geometry's call; the final
placement (offset + formatting) is stated in the PR.

## Engineer's calls (flagged; not user decisions)

1. Math in Python at response time (D1) — chosen for pytest-only testability.
2. **Wind extraction fix is in scope (D1a)** — without it, `_` WX packets
   (the most common APRS weather format) store no wind and no barb can render.
   This is a correctness prerequisite for the issue, not scope creep.
3. Runtime images for rendering (D2) — keeps the station model inside the
   existing GeoJSON/symbol-layer model (labels, glow, click handling all keep
   working).
4. Toggle default **on**; checkbox in the filter overlay, key
   `filter-station-model`, persisted in localStorage (D3).
5. Gust annotation beside the barb tip (D4).
6. **Model predicate is data-driven** (Q2): any station with a truthy
   `last_weather` gets a model (the same predicate the popup's inline weather
   summary already uses, app.js line ~1573); `wx_model` is a sibling of
   `last_weather` and absent when there is no weather, so the frontend keeps the
   plain sprite. This includes stations whose APRS symbol is not `_` but that
   report weather.
7. **Model wind is always knots** (Q3): barbs, gust annotation and legend state
   kt; the app's popup/panel wind display keeps its existing m/s-or-mph
   preference. Deliberate NWS split, stated in the PR.
8. APRS-only vs "METAR" fixture tests: the repo has no METAR source, so the
   "METAR fixture" in tests is a synthetic `last_weather` carrying
   `visibility`/`sky_cover`/`wx_code` — it exercises the omittability path, which
   is the whole point of AC4.

## Field → placement map (final)

| Element | Placement | Source (backend field on `last_weather`) |
|---|---|---|
| Temperature | top-left | `temperature` (else omit) |
| Dew point | bottom-left | `dewpoint` (else omit) |
| Wind barb | shaft from circle toward where wind comes FROM | `wind_direction` + `wind_speed` (m/s → kt in station_model); calm = ring, no barb; **requires D1a extraction fix** |
| Wind gust | text beside barb tip (D4) | `wind_gust` (m/s → kt; else omit) |
| Pressure | top-right | `pressure` (else omit) |
| Pressure trend | right below pressure | no source here — always omit |
| Visibility | per article figure | no source here — omit (synthetic fixture only) |
| Sky cover | circle fill | no source here — plain open circle (synthetic fixture only) |
| Present weather | left of circle | no source here — omit (synthetic fixture only) |
| Rain 1h/24h | n/a | stays in station detail readout only |

## Tests

- `tests/test_station_model.py`
  - barb matrix (round to nearest 5 kt, then decompose; pennant-innermost):
    0 → calm (no feathers); 3 → `["half"]` (nonzero <5); 5 → `["half"]`;
    7 → `["half"]` (7 rounds to 5); 25 → `["full","full","half"]`;
    50 → `["pennant"]`; 75 → `["pennant","full","full","half"]`.
    List order is inside→outside (pennant closest to the circle).
  - rotation: N/E/S/W and 0-vs-360 equivalence; missing direction → no barb.
  - dewpoint: known temp/humidity pairs (e.g. (20 °C, 50 %) → ≈9.3 °C; cross-check against the existing Magnus code path used in lifecycle).
  - m/s → kt conversion (e.g. 20.58 m/s → 40 kt) and gust conversion.
  - missing-field behavior: APRS-only fixture (temp+wind+humidity only) vs synthetic METAR fixture (all fields) — assert the presence mask and geometry.
- `tests/test_decoder.py` / `tests/test_lifecycle.py` (extend): **regression test
  for D1a** — run aprslib.parse on a canonical `_` WX packet and assert
  `extract_wind` returns the direction and speed (km/h → m/s) the current
  measurement yields, and that `last_weather` picks up `wind_speed`/
  `wind_direction` from it.
- `tests/test_maplibre_wx_station_model.py` (repo's static-analysis pattern)
  - `stations-symbol-base` references `wx_model` in its icon-image case.
  - runtime-image registration + re-register on style.load present in app.js.
  - toggle: `filter-station-model` checkbox exists, default on, persisted; when off → sprite fallback + legend section hidden; legend strings "5", "10", "50", "kt", "calm" and unit strings present.
- `tests/test_routers.py` (extend): `/api/weather/stations` and `/api/stations`
  include `wx_model` for stations with weather, absent for non-WX stations.

## Acceptance criteria mapping

1. Model renders all available fields in correct placement — D1/D2/D4.
2. Barb geometry + feather math unit-tested with the exact value matrix — test_station_model.
3. Dewpoint derivation unit-tested — test_station_model (Magnus).
4. Missing-field behavior unit-tested (APRS-only vs METAR fixtures) — test_station_model.
5. Legible at default zoom; min-zoom decision documented in the PR — builder/PR (canvas geometry work).
6. Real screenshots (METAR-fed full model + APRS-only reduced model) in the PR body — manual; needs a running app and a populated feed.
7. Delivery: one issue → one PR; pytest green on devbuild; CI green; PR body opens with plain-English paragraph — process.
Legend 1–4: legend content + units, state-driven show/hide, tests for show/hide + 5/10/50/calm strings, screenshot in PR.

## Deployment note

Per repo AGENTS.md the deployed target is DigiPi (`/home/pi/direwolf-dashboard`,
systemd `direwolf-dashboard`). Nothing in this design touches storage layout,
config, or the readonly-root constraint; all new art is pure Python + in-browser
canvas. The issue's "builds/tests on devbuild" note is honored via pytest on the
branch; the actual deploy path is DigiPi per repo docs.

## What's left to the builder

- Canvas pixel geometry for the model image (circle radius, barb/f feather
  drawing, text offsets, halo for legibility on the dark basemap).
- Exact `wx_<callsign>` image key/registry + style.load re-registration wiring.
- The "Station plots" checkbox DOM/CSS + persistence key + legend section HTML.
- Min-zoom legibility check + statement in the PR (AC5).
- Screenshots on the running app (AC6, legend AC4).
