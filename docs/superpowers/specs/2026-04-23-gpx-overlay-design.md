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
| Visual styling | Auto-styled by GPX element type | Distinct from existing APRS overlays (cyan `#00b4d8` trails, dashed blue `#0000ff` packet routes) |
| Parsing location | Client-side (browser) | Keeps Pi CPU free, works offline |

## Architecture

### Files Changed

| File | Change |
|------|--------|
| `src/direwolf_dashboard/static/leaflet/gpx.js` | **New** — vendored leaflet-gpx plugin (~15KB) |
| `src/direwolf_dashboard/static/index.html` | Add `<script>` tag for `gpx.js` |
| `src/direwolf_dashboard/static/app.js` | New GPX overlay module within existing IIFE |
| `src/direwolf_dashboard/static/style.css` | Styles for GPX map control |

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
  "data": "<gpx>...raw XML string...</gpx>"
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

- Filename displayed (truncated to 20 characters if long)
- Stats from leaflet-gpx: distance, track count, waypoint count
- "Fit Map" — calls `map.fitBounds(gpxLayer.getBounds())`
- "Clear" — removes layer from map, deletes from localStorage, reverts to empty state
- Control starts expanded when a GPX file is loaded (including on page reload from localStorage)

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

All colors chosen to be distinct from existing APRS overlays. The existing map uses cyan (`#00b4d8`) for station movement trails, dashed blue (`#0000ff`) for packet routes, red (`#ff0000`) for TX arcs, and green (`#00cc00`) for RX arcs. The GPX start/end markers use different shades of green (`#22c55e`) and red (`#ef4444`) than the APRS animations, and are circle markers rather than arc SVGs, so visual confusion is unlikely.

| Element | Style | Color |
|---------|-------|-------|
| Tracks | Solid polyline, weight 3, opacity 0.8, round caps | `#ff8c00` (dark orange) |
| Routes | Dashed polyline (`dashArray: '8,6'`), weight 2, opacity 0.8 | `#ff8c00` (dark orange) |
| Start marker | `L.divIcon` with inline SVG circle, radius 6 | `#22c55e` (green) fill, white stroke |
| End marker | `L.divIcon` with inline SVG circle, radius 6 | `#ef4444` (red) fill, white stroke |
| Waypoints | `L.divIcon` with inline SVG circle, radius 5, with name tooltip | `#a855f7` (purple) fill, white stroke |

No external icon images needed — inline SVG circles avoid file dependencies and render crisp at any zoom.

### leaflet-gpx Configuration

leaflet-gpx markers expect `L.Icon` or `L.DivIcon` instances (not `L.circleMarker`). We use `L.divIcon` with inline SVG to render colored circles without external image dependencies:

```javascript
function gpxCircleIcon(fillColor, radius) {
    var size = radius * 2 + 4;
    return L.divIcon({
        className: 'gpx-marker',
        iconSize: [size, size],
        iconAnchor: [size/2, size/2],
        html: '<svg width="' + size + '" height="' + size + '">' +
              '<circle cx="' + size/2 + '" cy="' + size/2 + '" r="' + radius + '" ' +
              'fill="' + fillColor + '" stroke="white" stroke-width="1.5"/></svg>'
    });
}

new L.GPX(gpxText, {
    async: true,
    parseElements: ['track', 'route', 'waypoint'],
    polyline_options: [
        // Index 0: track style
        {
            color: '#ff8c00',
            opacity: 0.8,
            weight: 3,
            lineCap: 'round',
            lineJoin: 'round'
        },
        // Index 1: route style (dashed)
        {
            color: '#ff8c00',
            opacity: 0.8,
            weight: 2,
            dashArray: '8,6',
            lineCap: 'round',
            lineJoin: 'round'
        }
    ],
    markers: {
        startIcon: gpxCircleIcon('#22c55e', 6),
        endIcon: gpxCircleIcon('#ef4444', 6),
        wptIcons: { '': gpxCircleIcon('#a855f7', 5) },
        wptTypeIcons: {}
    }
});
```

## Error Handling

| Condition | Detection | User Feedback |
|-----------|-----------|---------------|
| Malformed XML | leaflet-gpx `'error'` event | Red inline message: "Invalid GPX file" (auto-clears after 5s) |
| Valid XML, no GPX content | `'loaded'` event + `gpxLayer.getLayers().length === 0` | "No tracks or waypoints found" |
| File too large | `file.size > 5 * 1024 * 1024` before FileReader | "File too large (max 5MB)" |
| localStorage quota exceeded | `QuotaExceededError` on `setItem` | "GPX too large to save — overlay shown but won't persist on reload" (overlay still renders, just not persisted) |
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
