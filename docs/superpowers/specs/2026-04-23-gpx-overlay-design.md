# GPX File Overlay Design Spec

**Date:** 2026-04-23  
**Status:** Approved  
**Scope:** Client-side only — zero server changes

## Problem

Users want to overlay GPX files (tracks, routes, waypoints) on the Direwolf Dashboard map alongside live APRS station data. Use cases include pre-planned route display during operating sessions, post-activity review against captured APRS positions, and marking areas of interest (event perimeters, SAR zones).

## Decisions

| Aspect | Decision | Rationale |
|--------|----------|-----------|
| GPX parsing | leaflet-gpx plugin (vendored) | Battle-tested, handles edge cases, built-in track/route/waypoint support |
| Storage | Browser localStorage | No server changes, no Pi storage impact, persists per-browser across reloads |
| File count | Single file at a time | Keeps UI simple; uploading a new file replaces the old one |
| UI location | Leaflet map control, top-left (below zoom) | Standard Leaflet convention for tool controls |
| Visual styling | Auto-styled by GPX element type | Distinct from existing APRS overlays (cyan trails, blue routes) |
| Parsing location | Client-side (browser) | Keeps Pi CPU free, works offline |

## Architecture

### Files Changed

| File | Change |
|------|--------|
| `static/leaflet/gpx.js` | **New** — vendored leaflet-gpx plugin (~15KB) |
| `static/index.html` | Add `<script>` tag for `gpx.js` |
| `static/app.js` | New GPX overlay module within existing IIFE |
| `static/style.css` | Styles for GPX map control |

No Python/backend changes. No new API endpoints. No database changes.

### Data Flow

```
User clicks "Load GPX"
  → hidden <input type="file" accept=".gpx,.xml"> opens browser file picker
  → FileReader reads file as text
  → Size check (max 5MB)
  → Pass text to new L.GPX(gpxText, options)
  → On 'loaded' event: check layer has content
  → If valid: store in localStorage, show stats, add legend entries
  → If empty/error: show inline error, don't persist
  → On page reload: check localStorage, re-render if present
```

### localStorage Schema

Key: `gpx_overlay`

```json
{
  "filename": "hike_route.gpx",
  "data": "<gpx>...raw XML string...</gpx>",
  "addedAt": "2026-04-23T12:00:00Z"
}
```

## UI Design

### GPX Map Control (L.Control, position: topleft)

Two states:

**Empty state:**
```
┌─────────────────┐
│ 📍 GPX Overlay  │
│ [  Load GPX   ] │
└─────────────────┘
```

- Collapsible header (click to collapse/expand, like the existing legend)
- "Load GPX" button triggers hidden file input
- Accepts `.gpx` and `.xml` files

**Loaded state:**
```
┌──────────────────┐
│ 📍 GPX Overlay ▲ │
│ hike_route.gpx   │
│ 12.4 km · 3 trk  │
│ · 2 wpts         │
│ [Fit Map] [Clear] │
└──────────────────┘
```

- Filename displayed (truncated if long)
- Stats from leaflet-gpx: distance, track count, waypoint count
- "Fit Map" — calls `map.fitBounds(gpxLayer.getBounds())`
- "Clear" — removes layer from map, deletes from localStorage, reverts to empty state

### Legend Updates

When a GPX file is loaded, a "GPX Overlay" section is appended to the existing legend:

```
GPX Overlay
━━ GPX track       (solid orange line)
┅┅ GPX route       (dashed orange line)
●  GPX waypoint    (purple circle)
```

When cleared, this section is removed.

## Visual Styling

All colors chosen to be distinct from existing APRS overlays (cyan trails, blue packet routes, red TX, green RX).

| Element | Style | Color |
|---------|-------|-------|
| Tracks | Solid polyline, weight 3, round caps | `#ff8c00` (dark orange) |
| Routes | Dashed polyline, weight 2 | `#ff8c00` (dark orange) |
| Start marker | `L.circleMarker`, radius 6 | `#22c55e` (green) fill, white stroke |
| End marker | `L.circleMarker`, radius 6 | `#ef4444` (red) fill, white stroke |
| Waypoints | `L.circleMarker`, radius 5, with name tooltip | `#a855f7` (purple) fill, white stroke |

No external icon images needed — circle markers avoid file dependencies and render crisp at any zoom.

### leaflet-gpx Configuration

```javascript
new L.GPX(gpxText, {
    async: true,
    parseElements: ['track', 'route', 'waypoint'],
    polyline_options: [{
        color: '#ff8c00',
        opacity: 0.8,
        weight: 3,
        lineCap: 'round',
        lineJoin: 'round'
    }],
    markers: {
        startIcon: L.circleMarker([0,0], { radius: 6, color: 'white', weight: 1.5, fillColor: '#22c55e', fillOpacity: 1 }),
        endIcon: L.circleMarker([0,0], { radius: 6, color: 'white', weight: 1.5, fillColor: '#ef4444', fillOpacity: 1 }),
        wptIcons: {},       // default waypoint icon overridden below
        wptTypeIcons: {}
    }
});
```

Note: The exact marker configuration may need adjustment based on how leaflet-gpx handles circleMarkers vs. Icons. If `L.circleMarker` isn't supported as a marker icon, we'll create small `L.divIcon` elements with inline SVG circles instead.

## Error Handling

| Condition | Detection | User Feedback |
|-----------|-----------|---------------|
| Malformed XML | leaflet-gpx `'error'` event | Red inline message: "Invalid GPX file" (auto-clears after 5s) |
| Valid XML, no GPX content | `'loaded'` event + `gpxLayer.getLayers().length === 0` | "No tracks or waypoints found" |
| File too large | `file.size > 5 * 1024 * 1024` before FileReader | "File too large (max 5MB)" |
| Partial data (some tracks corrupt) | leaflet-gpx renders what it can | Show stats for successfully parsed content |
| Empty `<trkseg>` elements | Same as "no content" case | "No tracks or waypoints found" |

In all error cases, nothing is saved to localStorage. The control remains in empty state.

## CSS Approach

The GPX control styling matches the existing map legend:
- `background: rgba(30, 30, 30, 0.92)` — same as `.map-legend`
- `border: 1px solid #444` — same border color
- `border-radius: 6px` — same radius
- `font-family` — inherits the monospace stack from the dashboard
- `font-size: 10-11px` — same range as legend

New CSS classes:
- `.gpx-control` — main container
- `.gpx-control-header` — collapsible header
- `.gpx-control-body` — content area
- `.gpx-control .gpx-error` — error message styling
- `.gpx-control .gpx-stats` — filename and statistics
- `.gpx-control .gpx-actions` — button row

## Testing Considerations

- Manual testing with various GPX files (Garmin exports, Strava, hand-crafted)
- Test with empty GPX files, malformed XML, oversized files
- Test localStorage persistence across page reloads
- Test "Clear" properly removes all layers and localStorage data
- Test that GPX overlay doesn't interfere with existing APRS markers/trails
- Test on Pi Zero 2W to verify performance with typical GPX file sizes
- Test collapsible behavior doesn't conflict with existing legend control

## Out of Scope

- Multiple simultaneous GPX files
- Server-side GPX storage
- GPX file editing or creation
- Elevation profile display
- GPX track animation/playback
- Drag-and-drop file upload (browser file picker only)
- KML/KMZ or other geo-format support
