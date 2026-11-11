# dw219 Map Consolidation Implementation Plan

**Goal:** Replace the many floating map panels with one closed-by-default "Map" drawer, a slim toolbar, a one-line Stats readout, and a header status chip — leaving only zoom, one locate button, and attribution on the map, and deleting all overlay collision-positioning code.

**Architecture:** Keep MapLibre GL. Stop adding Filters/MyLocation/Stats/GPX/Legend as `map.addControl` floating cards. Instead build a single `#map-drawer` DOM element (sibling of `#map`, inside `#map-container`) with collapsible `<details>` sections that hold the *same* control markup and ids moved verbatim so existing update functions (`updateStatsOverlay`, `updateMyLocationOverlay`, `updateLegendGpx`, `setWxLegendVisible`, `initWxLegend`, filter/gpx handlers) keep working against the same ids. Merge the TOOLS drawer into the toolbar as a ⋮ overflow menu. Delete the collision cluster (§2 of MAP.md) and its CSS custom props, its ResizeObserver, and the geometry test guards + screenshots that only exist for it.

**Tech Stack:** Vanilla JS (single IIFE in app.js + dw-toolbar.js glue), MapLibre GL (vendored), CSS, pytest + CDP served-page tests.

**Spec:** PR #219 comment 33563 (Hemna PM note). Reference: master `screenshot.png` for visual/polish bar and full tile render.

## Global Constraints

- Fully offline; no external URLs. No JS build step. Low resource (Pi Zero 2W).
- Drawer **closed by default**. ≥1024px: drawer pushes/resizes the map (no cover). <1024px: overlay with scrim. <600px: bottom sheet, max 60vh, dismiss by ✕/swipe-down.
- Toolbar: ≤5 icons + ⋮ overflow. Keep Find, station count, Map-drawer toggle, Packet log, Messages, Theme. Overflow: Decode, Clear log, Drop pin, Weather stations, Station list, Keyboard help, Settings. **Remove the gear/Settings icon** (Settings nav tab exists). <600px: only Find, drawer toggle, ⋮.
- Stats = one-line toolbar readout ("N stn · N pkt · N/hr · Ns"); <600px moves into drawer.
- Waiting message = status chip next to "Disconnected" in header, not a full-width row.
- Map keeps only: zoom +/− (top-left), ONE locate button (bottom-right), attribution pill (collapses to ⓘ <600px). Remove "z 2.0" readout. Move trash/clear-data into ⋮.
- Only one surface open at a time (drawer open closes ⋮ and vice-versa).
- Delete ALL overlay collision JS; **no JS positions one overlay from another's size**.
- Final diff MUST be smaller than current (101 files, +2408/−51). Shrink by deleting collision code, its tests, and the 59 `dw219-*` screenshots.

## Review Focus

- **Drawer open/close state persistence & default** — must be closed on fresh load (cleared storage); a remembered-open drawer would fail AC2. Test: fresh page → drawer closed, 3 overlay groups only.
- **Every function still reachable in ≤2 taps** (AC4) — moving TOOLS items into ⋮ and cards into drawer must not orphan any handler (station list, weather, messages, decode, clear log, drop pin, GPX load, all filters, all 4 stats, settings, clear data, keyboard help, theme). Test: `elementFromPoint`/click reaches each.
- **No duplicate entry points** (AC5) — each action one place. Removing the gear icon while keeping Settings in ⋮ AND nav tab is a duplicate; spec says remove gear, keep nav tab → Settings only in ⋮? Resolve: Settings reachable via nav tab (primary) and ⋮ overflow (secondary) — spec explicitly lists Settings in ⋮ overflow AND says remove the gear because the nav tab exists. Keep ⋮→Settings, remove toolbar gear icon. That is one toolbar entry (⋮) + the nav tab, matching spec text.
- **Map tiles render fully** in every screenshot (no blank/partial tiles) — drawer resize of the map must call `map.resize()` so MapLibre re-renders tiles for the new width.
- **Light + dark theme** parity for drawer, chip, overflow menu, stats readout.

---

## Tasks (executed natively in this session)

### Task 1: Toolbar + overflow menu (index.html, dw-toolbar.js, style.css)
- Rewrite `#toolrow` to: Find + count (left); right = Map-drawer toggle, Packet log, Messages, Theme, ⋮ overflow. Add one-line `#stats-readout` span. Remove: station-list, weather, drop-pin, decode, keyboard-help, settings(gear), tools(🔧) buttons from the visible row.
- Build `#overflow-menu` (replaces #toolpanel TOOLS drawer) containing: Decode packet, Clear log, Drop pin, Weather stations, Station list, Keyboard help, Settings. Each forwards to the existing hidden button's click (keep the real handler buttons as `hidden` elements so app.js wiring is untouched).
- Keep hidden `#btn-station-list`, `#btn-weather-panel`, `#btn-messages-panel`, `#btn-drop-pin`, `#btn-decode`, `#btn-keyboard-help`, `#btn-settings`, `#btn-toggle-log`, `#btn-theme`, `#btn-clear-data` as `display:none` anchors so every app.js `getElementById(...).addEventListener` still binds. Forward menu/toolbar clicks to them.
- Delete `#mobile-menu` and its 3 buttons; ⋮ overflow replaces it.
- One-surface-at-a-time: opening ⋮ closes drawer; opening drawer closes ⋮.
- Verify: `node --check` on dw-toolbar.js; grep confirms every former handler id still present as an element.

### Task 2: The Map drawer shell (index.html, style.css, app.js)
- Add `#map-drawer` inside `#map-container` (sibling of `#map`), closed by default, with a header (title "Map" + ✕) and a scrollable body of `<section>`s: Filters (open by default), My Location, Stats, GPX, Legend.
- CSS responsive: ≥1024px `#map-container` becomes grid/flex so the open drawer takes a left column and the map shrinks beside it; <1024px drawer is absolute overlay + `#drawer-scrim`; <600px bottom-sheet (max-height:60vh, bottom:0, full width).
- Drawer toggle in JS: open/close adds/removes `#map-container.drawer-open`; on every open/close/transitionend call `map.resize()` so tiles repaint.
- Verify: `node --check` app.js.

### Task 3: Move the five cards' content into drawer sections (app.js)
- Convert `initLegend/initStatsOverlay/initFilterOverlay/initMyLocationOverlay/initGpxOverlay` so they populate the drawer section bodies instead of `map.addControl(...)`. Reuse the exact inner-HTML and ids from MAP.md §1 so `updateStatsOverlay`, `updateMyLocationOverlay`, `updateLegendGpx`, `setWxLegendVisible`, `initWxLegend`, the filter change handlers, the gpx load/clear/fit handlers, and `#filter-*`/`#stats-*`/`#legend-wx*`/`.my-location-*`/`.gpx-*` selectors keep resolving.
- Remove the config checkboxes "Show X overlay on map" behavior that only toggled floating cards (keep settings fields harmless or hide — minimal: leave fields, they now toggle the drawer section's visibility).
- Verify: served page shows all five sections inside the drawer, each populated.

### Task 4: Stats one-line readout + waiting status chip (app.js, index.html, dw-toolbar.js)
- `updateStatsOverlay(stats)` ALSO writes `#stats-readout` ("N stn · N pkt · N/hr · Ns"). Keep the detailed Stats section inside the drawer (per spec: readout in toolbar, full Stats in drawer below 600px — keep both; drawer Stats is the ≤600 home).
- Replace `#waiting-toast` row with `#status-chip` next to `#status-text` in `#topnav-right`. `showWaitingToast()`/`dismissWaiting()` toggle the chip instead of the row.
- Verify: chip appears/hides; readout updates.

### Task 5: Strip the map to 3 overlays + delete collision code (app.js, style.css)
- Delete `map-zoom-readout` (buildZoomReadout + addControl L1694). Keep NavigationControl (zoom).
- Keep exactly ONE locate button bottom-right (`#btn-center-my-pos` as a map fab). Move `#btn-clear-data` (trash) into ⋮ overflow (already an element from Task 1); remove its map fab.
- Delete collision cluster: `setCornerBodyCap, overlayObstacles, legendCoversControl, setLegendCap, stepLegendAside, legendLift, placeLegend, capUnderCards, updateLeftColumnFit, updateFabStack, paintCornerScroll, toggleCornerCard, closeCornerCards, syncCornerIcon, updateCornerMode, initCornerMode` and all their call sites, the ResizeObserver, and consts (CORNER_COMPACT_MAX_MAP_HEIGHT, LEGEND_*, COLUMN_STRIP, LEGEND_HEADER_H, LEGEND_OBSTACLES). Keep `rectsOverlap`/`collides` only if used elsewhere (grep; else delete).
- Delete CSS for the floating-card positioning + custom props (`--legend-shift/-lift/-max/--fab-lift/--corner-open-max`), `.stats-tight`, `.fab-row`, `.corner-*`, `#toolpanel`, and the floating `.map-legend/.stats-overlay/.filter-overlay/.my-location-overlay/.gpx-control` absolute/addControl styles (replace with drawer-section styles).
- Verify: `node --check`; grep shows none of the deleted identifiers remain; served page has only zoom + 1 locate + attribution on the map at rest.

### Task 6: Tests — delete collision guards, add consolidation guards (tests/)
- Delete the pure collision geometry guards (MAP.md §9 delete candidates) in test_corner_served.py and test_corner_geometry.py; delete test_toolpanel_layout.py (toolpanel removed). Delete the 59 `dw219-*` screenshots + any screenshot referenced only by deleted tests.
- Add `tests/test_map_consolidation.py` (CDP served-page) asserting the 8 acceptance criteria: (1) overlay coverage ≤5% @1280×800, ≤10% @360×800 with drawer closed; (2) exactly 3 overlay groups on map at rest; (3) 0px² overlaps + elementFromPoint returns own control in rest / drawer-open / ⋮-open at 320,360,560,768,1024,1280,1600 × 400,568,800 dark+light; (4) every function reachable ≤2 taps; (5) no duplicate entry points; (6) no overlay-from-overlay positioning JS.
- Verify: run the new suite; it must pass at the new head and the collision guards must be gone.

### Task 7: Verify, screenshot, measure, push
- Full `uv run pytest tests/ -v`, `uvx ruff check .`, `node --check`.
- Served-page screenshots at 1280×800, 560×800, 360×800 (drawer closed+open) dark+light; eyeball each for truncation/overlap/blank tiles; measure coverage px²/% and overlaps.
- Confirm diff smaller than current (git diff --stat vs master: fewer files, fewer net lines).
- Check no xcodebuild / CI running; push to fix/218-top-toolbar; watch CI to green; summarize measured figures.
