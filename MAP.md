# dw219 Map — static UI inventory (branch fix/218-top-toolbar)

Files: `src/direwolf_dashboard/static/{app.js, style.css, index.html, dw-toolbar.js}`.
Line numbers are from the worktree at `/private/tmp/dw219-work`.

---

## 1. THE FIVE OVERLAY CONTROLS

All five are MapLibre controls created with `mlCreate(className)` and added with
`map.addControl(mlControl(builder, position))` (helpers: `mlControl` app.js L2827-2841,
`mlCreate` L2842-2846). All are constructed inside the `DOMContentLoaded` init block
(app.js L1603-1609), in this order: `initCenterFab, initLegend, initStatsOverlay,
initFilterOverlay, initMyLocationOverlay, initCornerMode, initGpxOverlay`.

### 1a. Legend — `initLegend` (app.js L435-502)
- **Class:** `map-legend`. **Position:** `'bottom-left'`. **addControl:** L498
  `map.addControl(mlControl(function () { return container; }, 'bottom-left'));`
- **Inner HTML** (template literal, L443-483) — interpolates `txColor, tcpipColor,
  rxColor, trackColor, routeColor` (L438-442 via `themeColor(...)`):
  ```
  <div class="map-legend-header">
      Legend <span class="legend-collapse-icon">&#9650;</span>
  </div>
  <div class="legend-body">
      <div class="legend-section-title">Animations</div>
      <div class="legend-item"> <svg...path stroke="${txColor}".../> <span>RF Transmit</span></div>
      <div class="legend-item"> <svg...path stroke="${tcpipColor}".../> <span>Internet (TCPIP)</span></div>
      <div class="legend-item"> <svg...path stroke="${rxColor}".../> <span>Receiving</span></div>
      <div class="legend-section-title">Trails</div>
      <div class="legend-item"> <div style="...background:${trackColor};..."></div> <span>Movement trail</span></div>
      <div class="legend-item"> <div style="...border-top:3px dashed ${routeColor};..."></div> <span>Packet route</span></div>
      <div id="legend-wx">
          <div class="legend-section-title">Weather stations</div>
          <div id="legend-wx-body"></div>
      </div>
  </div>
  ```
- **End of init:** header click toggles `.collapsed` + persists `dw-legend-collapsed` +
  calls `placeTrailPanel()` (L486-493); restore collapsed L494-497; after addControl it calls
  `setWxLegendVisible(wxStationEnabled())` (L499), `initWxLegend()` (L500), `setLegendCap()` (L501).
- **Element ids/classes referenced elsewhere:** `.map-legend` (L732,744,782, style.css + collision code),
  `.map-legend .legend-body` (updateLegendGpx L1546), `#legend-wx` (setWxLegendVisible L506),
  `#legend-wx-body` (initWxLegend L527), `.legend-gpx-section` (updateLegendGpx L1548,1553,1570),
  classes `legend-hidden`/`legend-tight`/`collapsed` set by collision code.
- **Runtime updaters:**
  - `setWxLegendVisible(show)` L505-508 — shows/hides `#legend-wx`. Called L499, L541, L1131.
  - `initWxLegend()` L525-543 — fetches `/weather/stations/legend`, fills `#legend-wx-body` (uses `wxScaleBar` L512-519).
  - `updateLegendGpx(show)` L1545-1572 — appends/removes `.legend-gpx-section`. Called L1511 (loadGpxData), L1530 (clearGpxOverlay).
  - `setLegendCap()` L729-736 — collision placement (see §2).

### 1b. Stats — `initStatsOverlay` (app.js L548-590)
- **Class:** `stats-overlay`. **Position:** `'top-left'`. **addControl:** L586
  `map.addControl(mlControl(function () { return container; }, 'top-left'));`
- **Module var:** `statsOverlayEl` (L546, assigned L585). Config flag `showStatsOverlay` (L49).
- **Inner HTML** (string concat, L551-558):
  ```
  <div class="stats-overlay-header">Stats <span class="stats-collapse-icon">▲</span></div>
  <div class="stats-overlay-body">
  <div class="stats-overlay-row"><span class="stats-label">Stations</span><span class="stats-value" id="stats-stations">0</span></div>
  <div class="stats-overlay-row"><span class="stats-label">Packets</span><span class="stats-value" id="stats-packets">0</span></div>
  <div class="stats-overlay-row"><span class="stats-label">Pkts/hr</span><span class="stats-value" id="stats-rate">0</span></div>
  <div class="stats-overlay-row"><span class="stats-label">Uptime</span><span class="stats-value" id="stats-uptime">0s</span></div>
  </div>
  ```
- **Header click (L561-580):** branches on `cornerMapContainer().classList.contains('stats-tight')`.
  Tight → toggles `.stats-open`, calls `updateLeftColumnFit()` + `placeTrailPanel()` (L567-573).
  Else → toggles `.collapsed`, persists `dw-stats-collapsed`, `placeTrailPanel()` (L575-579).
- **Visibility from config:** L587-589 hides `statsOverlayEl` when `!showStatsOverlay`.
- **Element ids referenced:** `#stats-stations`, `#stats-packets`, `#stats-rate`, `#stats-uptime`
  (built L554-557; read in updateStatsOverlay L595-601). `statsOverlayEl` toggled on config save
  L5544-5546. `showStatsOverlay` set L1642 (config load), L5348 (settings populate), L5544 (save).
- **Runtime updaters:**
  - `updateStatsOverlay(stats)` L592-603 — writes the four values (uses `formatUptime` L605-614).
    Called L1616 (initial fetch `/stats`) and from WebSocket stats handling.

### 1c. Filters — `initFilterOverlay` (app.js L1035-1144)
- **Class:** `filter-overlay`. **Position:** `'top-right'`. **addControl:** L1143
  `map.addControl(mlControl(function () { return container; }, 'top-right'));`
- **Inner HTML** (string concat, L1038-1105):
  ```
  <button type="button" class="filter-overlay-header" aria-expanded="true" aria-controls="filter-overlay-body">Filters <span class="filter-collapse-icon">▲</span></button>
  <div class="filter-overlay-body" id="filter-overlay-body">
  <div class="filter-overlay-row"><label class="filter-overlay-label" for="filter-callsign">Callsign</label><input type="text" id="filter-callsign" placeholder="Filter..." /></div>
  <div class="filter-overlay-row"><label class="filter-overlay-label" for="filter-search">Search</label><input type="search" id="filter-search" placeholder="Search log..." /></div>
  <div class="filter-overlay-row"><label class="filter-overlay-label" for="trail-duration">Trails</label><select id="trail-duration" title="Trail duration"><option value="1" selected>1h</option><option value="2">2h</option><option value="6">6h</option><option value="24">24h</option></select></div>
  <div class="filter-overlay-row"><label class="filter-overlay-label" for="filter-type">Type</label><select id="filter-type"><option value="">All</option><option value="GPSPacket">GPS</option><option value="MessagePacket">Message</option><option value="WeatherPacket">Weather</option><option value="StatusPacket">Status</option><option value="ObjectPacket">Object</option><option value="TelemetryPacket">Telemetry</option></select></div>
  <div class="filter-overlay-row"><label class="filter-overlay-label" for="filter-txrx">TX/RX</label><select id="filter-txrx"><option value="">All</option><option value="rx">RX</option><option value="tx">TX</option></select></div>
  <div class="filter-overlay-row"><label class="filter-overlay-label" for="filter-station-model">Station plots</label><input type="checkbox" id="filter-station-model" [checked] /></div>
  <div class="filter-overlay-row"><label class="filter-overlay-label" for="filter-packet-paths">Packet paths</label><input type="checkbox" id="filter-packet-paths" [checked] /></div>
  <div class="filter-overlay-row"><label class="filter-overlay-label" for="filter-follow-callsign">Follow</label><input type="text" id="filter-follow-callsign" placeholder="Follow callsign..." /></div>
  <div class="filter-overlay-row"><label class="filter-overlay-label" for="filter-follow-mode">Follow mode</label><select id="filter-follow-mode" title="..."><option value="dim" selected>Dim</option><option value="hide">Hide</option></select></div>
  </div>
  <div class="corner-scroll-track" aria-hidden="true"></div>
  <div class="corner-scroll-thumb" aria-hidden="true"></div>
  ```
  (checkbox `checked` state computed inline: station-model L1083 from `dw-station-model`, packet-paths L1089 from `packetPathsEnabled`.)
- **Body scroll handler → `paintCornerScroll(container)`** L1107-1111.
- **Header click (L1112-1120):** `if (toggleCornerCard('filter')) return;` else toggle `.collapsed` +
  persist `dw-filter-collapsed` + `syncCornerIcon` + `placeTrailPanel`.
- **Toggles wired:** `#filter-station-model` change L1126-1133 (persist + refreshStations + setWxLegendVisible);
  `#filter-packet-paths` change L1136-1142 (persist + setPacketPathsEnabled).
- **Element ids referenced elsewhere:** `#filter-callsign` L2739; `#filter-type` L2740; `#filter-txrx` L2741;
  `#filter-search` L5210, L5248, L5262, L5280; `#trail-duration` L5203; `#filter-follow-mode` L3737, L3970;
  `#filter-follow-callsign` L3952, L3961; `.filter-collapse-icon` syncCornerIcon L633; `.filter-overlay-header` L635,1112.
- **Runtime updaters:** no dedicated "update" fn; state changes flow through the `initFilters`/`initFollowStation`
  logic and the corner-mode collision code (`syncCornerIcon` L627-637, `toggleCornerCard` L979-995).

### 1d. My Location — `initMyLocationOverlay` (app.js L1147-1204)
- **Class:** `my-location-overlay`. **Position:** `'top-right'`. **addControl:** L1199
  `map.addControl(mlControl(function () { return container; }, 'top-right'));`
- **Module var:** `myLocationOverlayEl` (L54, assigned L1198). Config flag `showMyLocationOverlay` (L50).
- **Inner HTML** (string concat, L1150-1165):
  ```
  <button type="button" class="my-location-header" aria-expanded="true" aria-controls="my-location-body">
      <span class="my-location-title">My Location</span>
      <span class="my-location-actions">
          <span class="my-location-btn my-location-center" title="Center map on my location">⌖</span>
          <span class="my-location-btn my-location-clear hidden" title="Clear my location">×</span>
          <span class="my-location-collapse-icon">▲</span>
      </span>
  </button>
  <div class="my-location-body" id="my-location-body">
      <div class="my-location-type">Not set</div>
      <div class="my-location-coords"></div>
  </div>
  <div class="corner-scroll-track" aria-hidden="true"></div>
  <div class="corner-scroll-thumb" aria-hidden="true"></div>
  ```
- **Body scroll handler → `paintCornerScroll`** L1167-1171.
- **`.my-location-center` click** L1173-1179 (mapFlyTo to my position). **`.my-location-clear` click** L1181-1183
  (`window._removeMyPosition`). **Header click** L1185-1194: `if (toggleCornerCard('myloc')) return;` else toggle
  `.collapsed` + persist `dw-myloc-collapsed` + `syncCornerIcon` + `placeTrailPanel`.
- **Visibility from config:** L1200-1202 hides when `!showMyLocationOverlay`. Final `updateMyLocationOverlay()` L1203.
- **Element classes referenced:** `.my-location-type`, `.my-location-coords`, `.my-location-clear`,
  `.my-location-center` (read in updateMyLocationOverlay L1209-1212); `.my-location-collapse-icon`/`.my-location-header`
  in syncCornerIcon L633-635. `showMyLocationOverlay` L1643?/L5349/L5541-area; `myLocationOverlayEl` toggled on save.
- **Runtime updaters:**
  - `updateMyLocationOverlay()` L1206-1246 — fills type/coords, shows/hides clear+center buttons from
    `config.station.my_position` / `getMyPosition()`. Called L1203, L1614, and after pin/position changes.

### 1e. GPX — `initGpxOverlay` (app.js L1371-1477), builder `buildGpxControl` (L1379-1433)
- **Class:** `gpx-control`. **Position:** `'top-left'`. **addControl:** L1435
  `map.addControl(mlControl(buildGpxControl, 'top-left'));`
- **Module var:** `gpxControlEl` (L53, assigned L1437). Config flag `showGpxOverlay` (L48).
- **Inner HTML** (string concat inside buildGpxControl, L1382-1403):
  ```
  <div class="gpx-control-header">
      <span>📍</span>
      <span>GPX Overlay</span>
      <span class="gpx-collapse-icon">▲</span>
  </div>
  <div class="gpx-control-body">
      <div class="gpx-empty">
          <button class="gpx-btn-load">Load GPX</button>
      </div>
      <div class="gpx-loaded" style="display:none;">
          <div class="gpx-stats">
              <div class="gpx-filename"></div>
              <div class="gpx-info"></div>
          </div>
          <div class="gpx-actions">
              <button class="gpx-btn-fit">Fit Map</button>
              <button class="gpx-btn-clear">Clear</button>
          </div>
      </div>
      <div class="gpx-error" style="display:none;"></div>
  </div>
  ```
- **Header click** L1407-1412 toggle `.collapsed` + persist `dw-gpx-collapsed`. Restore L1413-1416.
- **Buttons:** `.gpx-btn-load` → file input click L1418-1420; `.gpx-btn-fit` → `fitGpxBounds` L1422-1426;
  `.gpx-btn-clear` → `clearGpxOverlay` L1428-1430. After addControl: `gpxControlEl = controlEl` L1437,
  `map.on('style.load', addGpxLayers)` L1438; visibility `if (!showGpxOverlay) controlEl.style.display='none'` L1442-1444;
  file-change handler L1446-1463; localStorage restore L1466-1476.
- **Element classes referenced:** `.gpx-filename`/`.gpx-info`/`.gpx-empty`/`.gpx-loaded`/`.gpx-error`
  (loadGpxData L1502-1509, clearGpxOverlay L1527-1529, showGpxError L1537-1541). `gpxControlEl` toggled on config save ~L5540-5542.
- **Runtime updaters:**
  - `loadGpxData(controlEl,text,filename)` L1479-1522 — parses, sets layers, fills stats, calls `updateLegendGpx(true)` L1511.
  - `clearGpxOverlay(controlEl)` L1524-1534 — resets, `updateLegendGpx(false)` L1530.
  - `showGpxError` L1536-1543. (Supporting: `parseGpxToGeoJSON` L1272-1327, `addGpxLayers` L1335-1365, `setGpxData` L1367-1369.)

---

## 2. COLLISION / POSITIONING CODE (delete candidates)

Shared geometry helpers: `rectsOverlap(a,b,gap)` L656-660, `collides(box,except,gap)` L682-688,
`cornerMapContainer()` L623-625, `CORNER_COMPACT_MAX_MAP_HEIGHT=300` L621,
consts `LEGEND_MIN_CLEAR=120` L762, `LEGEND_SHORT_MAX=160` L763, `COLUMN_STRIP=8` L861,
`LEGEND_HEADER_H=46` L863, `LEGEND_OBSTACLES` string L706-711.

| Function | Definition | Call sites |
|---|---|---|
| `setCornerBodyCap` | L642-651 | L982 (`toggleCornerCard`), L1000 (`updateCornerMode`). Sets `--corner-open-max` L645. |
| `overlayObstacles(except)` | L666-680 | L683 (inside `collides`). |
| `legendCoversControl(box)` | L712-723 | L735 (inside `setLegendCap`). |
| `setLegendCap` | L729-736 | L501 (`initLegend`), L1025 (ResizeObserver). Calls `placeLegend`+`stepLegendAside`; toggles `.legend-hidden`. |
| `stepLegendAside` | L742-761 | L731 (inside `setLegendCap`). Sets `--legend-shift` L760. |
| `legendLift(mapBox,naturalLeft,legendRight)` | L769-778 | L805 (`placeLegend`), L882 (`updateLeftColumnFit`). |
| `placeLegend` | L780-853 | L730 (inside `setLegendCap`). Sets/removes `--legend-shift`,`--legend-lift`,`--legend-max` (L799-801,821,828-829,837-839,843-845,850,852). |
| `capUnderCards` | L813-822 (nested in placeLegend) | L830, L840 (within placeLegend). Sets `--legend-max` L821. |
| `updateLeftColumnFit` | L865-891 | L571 (stats header click), L1020 (ResizeObserver). Toggles `.stats-tight`. |
| `updateFabStack` | L897-934 | L1024 (ResizeObserver). Toggles `.fab-row`; sets/removes `--fab-lift` (L906,927). |

**Related collision helpers (same cluster):** `paintCornerScroll(card)` L954-976 (called L649,991,1109,1169),
`toggleCornerCard(which)` L979-995 (called L1114,1188), `closeCornerCards` L936-946 (called L1004,L986),
`syncCornerIcon(el)` L627-637, `updateCornerMode` L997-1005, `initCornerMode` L1007-1033.

**Observers / resize handlers that drive the above:**
- **ResizeObserver** `initCornerMode` L1013-1032. The single observer callback L1018-1026 runs:
  `updateCornerMode()` L1019, `updateLeftColumnFit()` L1020, `updateFabStack()` L1024, `setLegendCap()` L1025.
  Observes `cornerMapContainer()` L1027, `.maplibregl-ctrl-top-left` rail L1028-1029, `.maplibregl-ctrl-top-right` L1030-1031.
- **MutationObserver:** none in app.js for these (the only MutationObserver is in dw-toolbar.js L31-33 on
  `#status-indicator` for the LIVE badge — unrelated to collision code).
- **`window.addEventListener('resize', …)`:** none in app.js (searched L1-7629). Resize is handled only via the
  ResizeObserver above and `map.on('resize', …)` for `measurePacketPathChrome` L1720 and `placeTrailPanel` L1949.

**CSS custom properties set by this code:** `--corner-open-max` (L645), `--legend-shift` (L759-760,799,837,844,850,852),
`--legend-lift` (L800,829,839,845), `--legend-max` (L801,821,828,838,843), `--fab-lift` (L906,927).
Consumed in style.css: `--legend-max`/`--legend-shift`/`--legend-lift` in `.map-legend` L1112-1116;
`--corner-open-max` in `.corner-compact …corner-open` L2901; `--fab-lift` in `#map-container.fab-row .fab-center/.fab-clear` L471,473-474.

---

## 3. "z 2.0" ZOOM READOUT
- **`buildZoomReadout()`** app.js L2798-2805. Creates `mlCreate('map-zoom-readout')` L2799;
  `el.title='Current zoom level (same number as in the URL)'` L2800; defines `update` using
  `formatZoomReadout(map.getZoom())` L2801; `map.on('zoom', update)` L2802; initial `update()` L2803.
- **addControl:** L1694 `map.addControl(mlControl(buildZoomReadout, 'top-left'));`
  (preceded L1693 by `NavigationControl` also `'top-left'`).
- **Updater:** the inner `update` listener on map `'zoom'` (L2801-2802); text via `formatZoomReadout`.
- **CSS:** `.map-zoom-readout` style.css L3019-3027 (`background: rgba(0,0,0,0.6); font: 12px/1.4 monospace; pointer-events: none;`).

---

## 4. THE WAITING MESSAGE
- **HTML:** index.html L74 `<div id="waiting-toast" class="toast hidden">Waiting for first position packet...</div>`
  — sits between `#toolrow` (L47-69) and `#mobile-menu`/`#map-container`. Comment L70-73 says it is now a
  layout row under the tool row, not a floating box.
- **`showWaitingToast()`** app.js L5973-5976 — only removes `.hidden` from `#waiting-toast`. Called L5952, L5960.
- **`dismissWaiting()`** app.js L5978-5984 — hides `#waiting-modal` and `#waiting-toast`. Called L4930, L6049.
- **`positionWaitingToast` / `toastSpot`:** NOT PRESENT. `grep` for both in app.js returns no definitions
  (only `showWaitingToast`/`dismissWaiting` exist). No remaining runtime toast-positioning code.
- **What remains:** the toast is a static DOM row toggled only by `.hidden`; placement is pure CSS
  (`.toast` style.css L1634-1641: `background/color/border-bottom/padding/font-size/text-align`, no position).
- **index.html L37-45 (#topnav-right / status chip area):**
  ```
  <div class="topnav-right">
      <div id="status-indicator" class="status disconnected">
          <span class="status-dot"></span>
          <span id="status-text">Disconnected</span>
      </div>
      <span class="utc-clock" id="utc-clock" title="UTC time">--:--:--</span>
      <span class="dw-local" id="dw-local" title="Local time"></span>
      <span class="live-badge" id="live-badge">LIVE</span>
  </div>
  ```

---

## 5. THE TOOLBAR

### #toolrow (index.html L47-69) — buttons in `.toolrow-right` (L52-68)
| id | title |
|---|---|
| (input) `dw-find` | Find callsign search box (L50, in `.toolrow-left`) |
| `dw-station-count` (span) | "0 stations" count (L53) |
| `btn-station-list` | "Station List (S)" 📋 (L54) |
| `btn-weather-panel` | "Weather Stations (W)" ⛅ (L55) |
| `btn-messages-panel` | "APRS Messages (M)" 💬 (L56) |
| `btn-drop-pin` | "Drop Pin - set my position" 📌 (L57) |
| `btn-decode` | "Decode APRS Packet (Ctrl+K)" 📡 (L58) |
| `btn-keyboard-help` | "Keyboard shortcuts (?)" ⌨ (L59) |
| `btn-theme` | "Toggle theme" (sun/moon spans) (L60-63) |
| `btn-toggle-log` | "Toggle packet log" ☰ (L64) |
| `btn-settings` | "Settings" ⚙ (L65) |
| `btn-tools` | "Toggle tool panel" 🔧 (L66) |
| `btn-mobile-menu` | "Menu" ⋮ (class `mobile-only`) (L67) |

### dw-toolbar.js (full file, L1-107) — purpose
IIFE glue for the #218 toolbar. Does:
- **UTC clock + local + LIVE badge:** `tickClock` L12-20 (fills `#utc-clock` from `toUTCString().slice(17,25)` and
  `#dw-local` from local HH:MM:SS), `setInterval(tickClock,1000)` L21-22. `syncLive` L26-35 toggles `.on` on
  `#live-badge` when `#status-indicator` has `.connected`; a **MutationObserver** on `#status-indicator` class L31-33
  plus `setInterval(syncLive,2000)` L34.
- **Nav pills:** `navButtons` from `.nav-item` L38; `PANEL_ACTIONS` map L39-44
  (`stations→btn-station-list, weather→btn-weather-panel, messages→btn-messages-panel, settings→btn-settings`);
  click handler L45-52 sets `.active` and `.click()`s the mapped button (map panel has no action).
- **Tool panel:** `setPanel(open)` L56-61 toggles `#toolpanel.hidden` and `#btn-tools.on`;
  `#btn-tools` click toggles open L62-67; `#btn-toolpanel-close` closes L68-69;
  every `#toolpanel [data-action]` row forwards its click to `$(data-action).click()` L70-76.
- **Find:** `doFind` L80-91 uppercases `#dw-find`, `window.__dwToolbar.getStation(cs)` → `flyTo` or opens station list; Enter key L92-96.
- **Station count:** `updateCount` L99-104 writes `#dw-station-count` from `window.__dwToolbar.stationCount()`;
  `setInterval(updateCount,3000)` L105.

### #toolpanel (index.html L86-114) — sections + data-action
- Header L87-90: title "TOOLS" + `#btn-toolpanel-close`.
- **PACKETS** (L92-97): `data-action="btn-toggle-log"` / `"btn-decode"` / `"btn-clear-log"`.
- **OVERLAYS** (L98-102): `data-action="btn-weather-panel"` / `"btn-messages-panel"`.
- **STATIONS** (L103-107): `data-action="btn-station-list"` / `"btn-drop-pin"`.
- **DISPLAY** (L108-112): `data-action="btn-theme"` / `"btn-settings"`.
- All `data-action` clicks are forwarded in dw-toolbar.js L70-76 to the matching toolbar button id.

### #mobile-menu (index.html L76-82)
`.mobile-menu-buttons` with `mobile-btn-drop-pin` (L78), `mobile-btn-toggle-log` (L79), `mobile-btn-settings` (L80).

### JS click handlers for each toolbar button id (app.js)
- `btn-station-list` → `initStationListModal` L7211; open wired L7232 (`btnOpen.addEventListener('click', openModal)`); `S` key handler L7268-7276.
- `btn-weather-panel` → `initWeatherPanelModal` L7452; `btnOpen` L7454, click L7472-area; `W` key handler L7504-7512.
- `btn-messages-panel` → `initMessagesPanelModal` L7303; `btnOpen` L7305, click L7333-area; `M` key handler L7343-7351.
- `btn-drop-pin` → `initDropPin` L6454; click L6458 `btn.addEventListener('click', () => togglePinMode())` (`togglePinMode` L6485-…, pin placement click L6500+).
- `btn-decode` → `initDecode` L6534; `btn` L6535; Ctrl+K handler L6561.
- `btn-keyboard-help` → `initKeyboardHelpModal` L7589; `btnOpen` L7591, click L7599-area.
- `btn-theme` → DOMContentLoaded L1580-1581 `themeBtn.addEventListener('click', toggleTheme)` (also referenced L271).
- `btn-toggle-log` → `initLogToggle` L5681; `btn` L5682; click L5707 `btn.addEventListener('click', toggleLogViewState)`; `L` key L5710-5717.
- `btn-settings` → `initSettings` L5303; `btnOpen` L5305, click handler L5307+ (opens settings modal); also `.click()`ed from mobile L5869.
- `btn-tools` → handled in dw-toolbar.js L62-67 (no app.js handler).
- `btn-mobile-menu` → `initMobileMenu` L5853; `btn` L5854, toggle L5859-5861.
- `btn-about-logo` → `initAboutModal` L6103; `logoBtn` L6107, opens About.
- `btn-clear-log` → L5239 inline click (clears `#log-list`).
- **Mobile menu buttons:** `mobile-btn-drop-pin` L6456 (click → `togglePinMode()` L6458-area);
  `mobile-btn-toggle-log` L5864 (→ `toggleLogViewState()`); `mobile-btn-settings` L5868 (→ `#btn-settings.click()`).

### toolpanel data-action forwarding
All `data-action` values (`btn-toggle-log, btn-decode, btn-clear-log, btn-weather-panel, btn-messages-panel,
btn-station-list, btn-drop-pin, btn-theme, btn-settings`) are dispatched by the generic forwarder in
**dw-toolbar.js L70-76** to the matching real button's click handler listed above.

---

## 6. FAB BUTTONS
- **HTML:** `#btn-center-my-pos` (`.fab-center`, title "Center on my position", ⌖) index.html L116;
  `#btn-clear-data` (`.fab-clear`, title "Clear all stations and packets", 🗑) index.html L117.
  Both inside `#map-container` after `#map`.
- **`initCenterFab()`** app.js L2848-2858 — `#btn-center-my-pos` click L2851-2857: `getMyPosition()` → `mapFlyTo(pos.lat,pos.lng,toMlZoom(zoom))` (zoom `config.station?.zoom || 12`). Called L1603.
- **`initClearDataFab()`** app.js L5606-5610 — `#btn-clear-data` click L5609 `btn.addEventListener('click', wipeDatabase)`. Called L1622. (`wipeDatabase` triggers the confirm-wipe modal `initConfirmWipeModal` L5612-5652.)
- **Other references:** both button ids read in `placeLegend`/`stepLegendAside` L745-746 and `updateFabStack` L902-903 (collision code).
- **CSS:** `.fab-center` L396-425, `.fab-clear` L428-457, row-mode overrides `#map-container.fab-row .fab-center` L465-472 and `.fab-clear` L473-477 (both use `bottom: calc(24px + var(--fab-lift,0px))`).

---

## 7. LEFT COLUMN concept
There is **no literal `left-column`/`leftColumn` identifier** in app.js or style.css (grep returns none).
The "left column" is the MapLibre `.maplibregl-ctrl-top-left` rail (NavigationControl + zoom readout + GPX +
Stats) plus the bottom-left legend, managed by the collision code.

**JS references (app.js):**
- `LEGEND_HEADER_H = 46` L863; used L883 (`updateLeftColumnFit` band calc).
- `COLUMN_STRIP = 8` L861; used L870, L883.
- `stats-tight` class: read L566; removed L869; added L887 (`updateLeftColumnFit`); set/removed drives §2.
- `stats-open` class: toggled L568, read L569 (stats header click, tight branch).
- `fab-row` class: checked L748 (`stepLegendAside`); removed L905, added L913 (`updateFabStack`).
- `updateLeftColumnFit()` L865-891 caps the top-left rail to map height minus strip and the legend band; adds `.stats-tight` when `rail.scrollHeight` exceeds.

**CSS references (style.css):**
- `#map-container.stats-tight .maplibregl-ctrl-top-left .maplibregl-ctrl { margin-top: 1px }` L2804-2808.
- `#map-container.stats-tight .stats-overlay-body { display:none }` L2809-2811.
- `#map-container.stats-tight .stats-overlay-header { border-bottom:none }` L2812-2814.
- `#map-container.stats-tight .stats-collapse-icon { display:none }` L2815-2817.
- `#map-container.stats-tight .stats-overlay.stats-open .stats-overlay-body { display:block }` L2824-2826.
- `…stats-open .stats-overlay-header { border-bottom:… }` L2827-2829; `…stats-open .stats-collapse-icon { display:inline }` L2830-2832.
- `#map-container.fab-row .fab-center` L465-472; `.fab-clear` L473-477.
- (No CSS rule literally named `fab-row`-column or `left-column`; `LEGEND_HEADER_H` is JS-only.)

---

## 8. CSS LINE RANGES (style.css)

- **`.map-legend` / `legend-*`:** `.map-legend` L1099-1117; `.map-legend-header` L1118-1126; `:hover` L1127-1129;
  `.legend-hidden` L1136; `.legend-tight .legend-body` L1137-1139; `.legend-tight .map-legend-header` L1140-1145;
  `.legend-tight .legend-collapse-icon` L1146-1148; `.legend-section-title` L1149-1156; `.legend-item` L1157-1162;
  `.legend-item:last-child` L1163-1165; `#legend-wx .legend-wx-example` L1168-1182; `.map-legend.collapsed .legend-body` L1183-1185;
  `.map-legend.collapsed .map-legend-header` L1186-1190; packet-segment legend swatches `.legend-source/-destination/-path/-datatype` L1974-1977.
- **`.stats-overlay` / `stats-*`:** `.stats-overlay` L1195-1204; `-header` L1205-1215; `:hover` L1216-1218; `-body` L1219-1221;
  `.collapsed -body` L1222-1224; `.collapsed -header` L1225-1227; `-row` L1228-1233; `.stats-label` L1234-1236; `.stats-value` L1237-…;
  (tight/open variants under §7 at L2804-2832).
- **`.filter-overlay`:** `.filter-overlay-header,.my-location-header` L1245-1255; `:focus-visible` L1256-1262;
  `.filter-overlay` L1263-1272; `-header` L1273-1283; `:hover` L1284-1286; `-body` L1287-1290; `.collapsed -body` L1291-1293;
  `.collapsed -header` L1294-1296; `-row` L1297-1302; `-label` L1303-1307; `select/input` L1308-1322; `input` L1320-…; `:focus` L1323-1329.
- **`.my-location-overlay`:** `.my-location-overlay` L1330-1339; `-header` L1340-1349; `:hover .my-location-title` L1350-1352;
  `-body` L1353-1355; `.collapsed -body` L1356-1358; `.collapsed -header` L1359-1362; `-collapse-icon` L1363-1367; `-title` L1368-1373;
  `-actions` L1374-1378; `-btn` L1379-1386; `:hover` L1387-1390; `.hidden` L1391-1393; `-type` L1394-1398; `-coords` L1399-1404.
- **`.gpx-control` / `gpx-*`:** `.gpx-control` L1405-1415; `-header` L1416-1426; `:hover` L1427-1429; `-header .gpx-collapse-icon` L1430-1434;
  `.collapsed -body` L1435-1437; `.collapsed -header` L1438-1440; `-body` L1441-1443; `.gpx-stats` L1444-1448; `.gpx-filename` L1449-1457;
  `.gpx-actions` L1458-1461; `-actions button` L1462-1471; `.gpx-btn-fit` L1472-1476 / `:hover` L1477-1479; `.gpx-btn-clear` L1480-1483 / `:hover` L1484-1486;
  `.gpx-btn-load` L1487-1498 / `:hover` L1499-1501; `.gpx-error` L1502-1506; `.gpx-marker` L1507-….
- **`.map-zoom-readout`:** L3019-3027.
- **`.toast`:** L1634-1641 (comment L1629-1633). (`.waiting-modal-content` L1644-1647 is the separate modal.)
- **`.fab-*` / fab-row:** `.fab-center` L396-425, `.fab-clear` L428-457, `#map-container.fab-row .fab-center` L465-472, `.fab-clear` L473-477.
  (No `.fab-row`-named standalone rule; no `fab-row`/`fab-stack` class besides the `#map-container.fab-row` combinators.)
- **`#toolpanel` / `toolpanel-*`:** `#toolpanel` L3132-3150; `.hidden` L3151; `.toolpanel-header` L3152-3162; `#btn-toolpanel-close` L3163-3171;
  `.toolpanel-body` L3172-3179; `::-webkit-scrollbar*` L3182-3188; `.toolpanel-section` L3189; `-section h3` L3190-3196; `.toolpanel-btn` L3197-3209; `:hover` L3210.
- **`#toolrow` / `toolrow-*`:** `#toolrow` L3083-3091; `[light] #toolrow` L3092-3095; `.toolrow-left` L3096; `#toolrow label` L3097;
  `#dw-find` L3098-3106 (`[light]` L3107); `.toolrow-right` L3108; `.station-count` L3109; `.tool-toggle` L3110-3124; `.on` L3125-3129; `[light]` L3130-3131.
  (`#topnav`/`.topnav-*` L3030-3082, `.nav-item` L3050-3064.)
- **@media blocks mentioning toolrow/600/440/360/320:**
  - `@media (max-width: 768px)` L3211-3215 — hides `.tagline,.topnav-nav`; `#dw-find width:120px`; `#toolpanel width:220px`.
  - `@media (max-width: 600px)` L3224-3229 — `#toolrow { flex-wrap: wrap }`, `.toolrow-right flex-wrap`, `#dw-find flex:1 1 120px`, `.station-count display:none`.
  - `@media (max-width: 440px)` L3230-3232 — `.toolrow-left { flex: 1 1 100% }`.
  - `@media (max-width: 360px)` L3234-3236 — `#btn-keyboard-help { display:none }` (comment "keeps 320px on one line" L3233).
  - Earlier `@media (max-width: 600px)` L1550-1581 — mobile toolbar: `.mobile-only` show, hide `#btn-toggle-log/#btn-settings/#status-text`, `.logo`, `#btn-mobile-menu` styles.
  - Other `@media` blocks (L2177, 2244, 2422, 2540, 2670 @640, 2775 @640) are modal-only (decode/weather/changelog/station-list/weather-panel/messages) — do NOT touch toolrow/overlays.
  - No `@media (max-width: 320px)` block exists; "320px" appears only in comments (L3222, L3233) and as a `width: 360px` value (L942, L2252).

---

## 9. TESTS & SCREENSHOTS

### tests/test_corner_served.py (served-page / CDP browser tests)
Helpers: `_free_port` L100, `served` fixture L109, `_CDP` L143/147/158, `_page` L167, `_open_filters` L229,
`browser` L489, `_cell` L535, `_cell_once` L549, `_corner_failures` L610, `_open_stats` L680.
- `test_served_page_sets_the_corner_cap_itself` L234 — asserts `--corner-open-max` comes from the running page. **Collision/geometry guard (delete candidate).**
- `test_served_attribution_strip_clears_the_open_filter_card` L245 — strip vs open filter card, 8px min. **Geometry guard.**
- `test_served_attribution_strip_clears_the_card_in_light_theme` L263 — same, light theme. **Geometry guard.**
- `test_served_filter_panel_fits_short_windows_without_scrolling` L270 — 1100x432 panel fits without scroll. **Geometry guard.**
- `test_served_short_window_paints_its_own_scroll_indicator` L291 — page-painted track/thumb, no UA gutter. **Geometry/scroll guard (delete candidate).**
- `test_served_open_card_stays_inside_the_map_and_hides_the_other` L330 — open card stays in map, other hidden. **Geometry guard** (but also behavior: one-open-at-a-time).
- `test_served_nothing_is_covered_at_any_reviewed_size` L633 — full size×theme matrix, nothing covered. **Collision geometry guard (delete candidate).**
- `test_served_stats_shows_its_values_in_a_short_window` L713 — tight-mode Stats header opens body, values reachable. **Real behavior** (stats-open toggle), with geometry assertions.
- `test_served_waiting_message_is_a_row_of_the_layout` L735 — waiting row never covers a control across 8 sizes. **Collision geometry guard.**
- `test_served_legend_yields_instead_of_covering_a_control` L751 — legend hidden vs covering My Location at 320x568. **Collision geometry guard (delete candidate).**
- `test_served_left_column_clears_the_resize_strip` L771 — left column ends above 8px strip. **Collision geometry guard.**
- `test_served_map_buttons_clear_the_credit_pill` L789 — fab-row buttons lift clear of credit pill. **Collision geometry guard.**

### tests/test_corner_geometry.py (static JSDOM-style measurement)
Helpers: `_compact_threshold` L39, `_compact` L47, `_filter_card` L68, `_myloc_card` L85, `_measure` L105, `_card` L176.
- `test_compact_corner_fits_a_255px_map` L180 — 255px map collapses both cards to headers inside map. **Collision geometry guard.**
- `test_round7_layout_is_untouched_on_a_308px_map` L199 — 308px keeps both cards open at 219/66px. **Geometry guard.**
- `test_compact_engages_at_a_255px_map_and_not_at_308` L214 — asserts the real `CORNER_COMPACT` threshold (255 collapse, 308 not). **Real behavior guard** (reads actual threshold, resists mutation).
- `test_attribution_strip_insets_both_resting_and_open` L235 — asserts `right:210px` + `right:250px` CSS strings. **Collision geometry guard (delete candidate).**

### tests/test_toolpanel_layout.py (static source assertions)
Helpers: `_css` L20, `_html` L24, `_block` L28.
- `test_toolpanel_lives_inside_map_container` L33 — DOM order topnav<toolrow<map-container<toolpanel<log. **Real structural behavior.**
- `test_toolpanel_absolute_over_the_map_only` L49 — `position:absolute; top/left/bottom:8px`, no `vh`. **Real behavior/layout guard.**
- `test_toolpanel_never_over_the_top_bands` L64 — z-index: map-container 0, bands 110, panel 0<z<110. **Real behavior/layout guard.**
- `test_all_sections_reachable_with_visible_scrollbar` L91 — 4 sections, body `overflow-y:auto`, 12px scrollbar. **Real behavior.**
- `test_toggle_buttons_are_distinguishable` L109 — `btn-toggle-log` and `btn-tools` have different icons. **Real behavior.**
- `test_waiting_toast_is_a_row_under_the_top_bands` L121 — toolrow<waiting-toast<map-container, `.toast` not fixed/absolute, no `positionWaitingToast` in JS. **Real behavior/layout guard** (encodes the "toast is a layout row" decision).

### docs/screenshots/
**59** files matching `dw219-*` (naming pattern `dw219-rN-<WxH>-<theme>-<state>.png`, e.g.
`dw219-r10-1024x400-dark-filter-open.png`).

---

### Notes / limits
- Line numbers verified against the worktree at `/private/tmp/dw219-work` this session.
- "Delete candidate" classifications in §9 reflect whether a test asserts pure collision/overlap geometry
  vs. a structural/behavioral contract; I did not run the suite, so pass/fail state is NOT DETERMINED.
