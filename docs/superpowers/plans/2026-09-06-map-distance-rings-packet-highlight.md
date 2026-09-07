# Map Distance Rings and Packet Highlighting Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add zoom-adaptive distance rings and live packet-row station highlighting to the dashboard map.

**Architecture:** Keep both features in the existing frontend IIFE. Add a dedicated Leaflet layer group for four rings and labels, recalculated from the map center and viewport edge. Extend the existing station-overlay selection state with transient row hover state and marker-icon class updates; no backend changes.

**Tech Stack:** Vanilla JavaScript, Leaflet, CSS, pytest, uv.

---

## Chunk 1: Adaptive Distance Rings

### Task 1: Add ring state and geometry helpers

**Files:**
- Modify: `src/direwolf_dashboard/static/app.js` near the map state and map initialization

- [ ] Add a dedicated `L.layerGroup` reference and ring metadata constants near the existing map state, and add the layer group to `map` during map initialization before the first ring update.
- [ ] Add a helper that converts the distance from `map.getCenter()` to the midpoint of the right edge of the map container into meters, using Leaflet's map projection APIs.
- [ ] Add a helper that formats a meter value in the existing km/mi style used by station distance overlays.
- [ ] Add an update function that reads `map.getCenter()` on every call, removes/rebuilds four non-interactive dashed circles centered there at 25%, 50%, 75%, and 100% of the measured edge distance, with non-interactive labels at each circle's top point.
- [ ] Call the update function once after the map and tile layer are initialized.
- [ ] Register `zoomend`, `moveend`, and map `resize` handlers to update the rings. Use a `ResizeObserver` on `#map` to call `map.invalidateSize()` and trigger the ring update when responsive layout changes the container dimensions.

### Task 2: Add ring presentation styles

**Files:**
- Modify: `src/direwolf_dashboard/static/style.css` near map overlay styles

- [ ] Add a compact, theme-compatible class for ring labels with a translucent background, readable text, and no pointer interaction.
- [ ] Use the existing theme variables where available and avoid external assets or fonts.

### Task 3: Verify ring behavior

- [ ] Run `uv run pytest tests/ -v`.
- [ ] Run a JavaScript syntax check with the available runtime, for example `node --check src/direwolf_dashboard/static/app.js` if Node is installed.
- [ ] Manually verify rings appear on initial load, remain centered on the visible map center after panning, resize after zooming, and remain readable on the responsive map/log layout.

## Chunk 2: Packet-Row Station Highlighting

### Task 4: Centralize marker highlight state

**Files:**
- Modify: `src/direwolf_dashboard/static/app.js` around the existing station overlay state and `addOrUpdateStation`
- Modify: `src/direwolf_dashboard/static/style.css` near `.my-station` and log selection styles

- [ ] Add transient hovered callsign state alongside `_selectedCallsign`.
- [ ] Add a helper that applies or removes a dedicated CSS class on a station marker icon based on whether its callsign is hovered or selected.
- [ ] Call the helper whenever hover or selected callsign state changes, including immediately after `showStationOverlay()` sets `_selectedCallsign`.
- [ ] Reapply the helper after station icon replacement in `addOrUpdateStation`, so symbol updates preserve the active highlight.
- [ ] Reapply the helper after a new marker is created in `addOrUpdateStation`, so a station that is already hovered or selected receives its highlight immediately.
- [ ] Keep the existing `popupclose` handler connected to `clearStationOverlay()`, and have that function remove marker classes and clear both transient and persistent callsigns.
- [ ] Update `clearStationsAndPackets()` to remove marker classes and clear both transient and persistent callsigns before it resets the station collection, without changing existing popup, route, or log-row behavior.
- [ ] Add a visually distinct marker-icon highlight class that works in both dark and light themes and does not replace `.my-station` styling.

### Task 5: Connect packet rows to station markers

**Files:**
- Modify: `src/direwolf_dashboard/static/app.js` in `addLogRow`

- [ ] Add `mouseenter` handling that sets the row's callsign as the transient hover and applies marker highlighting when the station exists.
- [ ] Add `mouseleave` handling that restores the persistent selected callsign highlight, if any.
- [ ] Preserve the current row click expand/collapse behavior and extend it to set persistent callsign selection and open the station popup when a marker exists.
- [ ] Keep hover and click handling from calling `setView`, `flyTo`, or other map-position-changing APIs; opening the popup must leave the current map center and zoom unchanged.
- [ ] Keep rows for unknown callsigns functional without creating markers or throwing errors.
- [ ] Ensure row action buttons continue to stop propagation and do not trigger station selection.

### Task 6: Verify packet interaction behavior

- [ ] Run `uv run pytest tests/ -v`.
- [ ] Run the JavaScript syntax check again.
- [ ] Manually verify hover highlights the source marker, moving away restores the selected marker, clicking opens the source popup while preserving expansion, unknown callsigns do not error, and popup/clear actions remove stale highlights.

## Chunk 3: Final Review

### Task 7: Review the implementation

- [ ] Inspect `git diff --check` and the full diff for unrelated changes.
- [ ] Confirm the implementation diff contains only `src/direwolf_dashboard/static/app.js` and `src/direwolf_dashboard/static/style.css`; the already committed design/plan documents are expected branch artifacts, and the pre-existing `uv.lock` and `direwolf-dashboard-v1.1.0-features.md` changes remain untouched.
- [ ] Commit the implementation with a focused message such as `feat: add map distance rings and packet station highlighting`.
