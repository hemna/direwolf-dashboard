# Map Distance Rings and Packet Station Highlighting

## Goal

Add four zoom-adaptive dashed distance rings around the visible map center and make live packet-log rows highlight their source station, matching the existing station-popup log highlighting behavior.

## Design

- Create a dedicated Leaflet layer group containing four non-interactive dashed `L.circle` layers and four non-interactive labels.
- Center the ring group on `map.getCenter()`. On initial map setup and every `zoomend`/`moveend`, measure the map-center distance to the horizontal viewport edge and set ring radii to 25%, 50%, 75%, and 100% of that distance.
- Place each label at the top of its ring and format it with the existing km/mi convention used by station overlays.
- Store the currently selected callsign separately from the transient hovered callsign.
- Add `mouseenter`, `mouseleave`, and click behavior to live packet rows using their existing `data-callsign` value.
- Highlight the corresponding station marker by toggling a dedicated marker-icon CSS class without moving the map. A row click keeps the existing expand/collapse behavior and also opens the station popup when that station is available.
- On hover exit, restore the selected station highlight instead of clearing it. Clear transient and persistent marker highlighting when the selection is cleared, the popup closes, or `clearStationsAndPackets()` runs. Rows for callsigns without a marker still support row expansion and receive no map highlight.

## Scope

Frontend-only changes to `src/direwolf_dashboard/static/app.js` and `src/direwolf_dashboard/static/style.css`. No backend, database, WebSocket, or packet-schema changes.

## Validation

- Run the existing pytest suite.
- Check JavaScript syntax with the repository's available tooling.
- Manually verify initial ring rendering, ring updates after pan/zoom, packet-row hover, click-to-popup, hover restoration after selection, missing-marker rows, and responsive map/log layout.
- Review the final diff for unrelated changes.
