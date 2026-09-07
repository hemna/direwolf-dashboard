# Map Distance Rings and Packet Station Highlighting

## Goal

Add four zoom-adaptive dashed distance rings around the visible map center and make live packet-log rows highlight their source station, matching the existing station-popup log highlighting behavior.

## Design

- Create a dedicated Leaflet layer group containing four non-interactive dashed `L.circle` layers and distance labels.
- Center the ring group on `map.getCenter()` and recalculate ring radii and labels on `zoomend` and `moveend`.
- Use the dashboard's existing km/mi distance display convention for labels.
- Store the currently selected callsign separately from the transient hovered callsign.
- Add hover and click behavior to live packet rows using their existing `data-callsign` value.
- Highlight the corresponding station marker without moving the map. A row click also opens the station popup when that station is available.
- On hover exit, restore the selected station highlight instead of clearing it. Clear transient and persistent marker highlighting when the selection or station data is cleared.

## Scope

Frontend-only changes to `static/app.js` and `static/style.css`. No backend, database, WebSocket, or packet-schema changes.

## Validation

- Run the existing pytest suite.
- Check JavaScript syntax with the repository's available tooling.
- Review the final diff for unrelated changes.
