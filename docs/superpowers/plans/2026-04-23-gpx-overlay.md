# GPX File Overlay Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow users to upload a GPX file and overlay its tracks, routes, and waypoints on the Leaflet map alongside live APRS data.

**Architecture:** Entirely client-side. Vendor the leaflet-gpx UMD build as a local script. Add a Leaflet map control for file upload/management. Parse GPX in the browser, render with auto-styled layers, persist in localStorage. Zero server changes.

**Tech Stack:** Leaflet.js (existing), leaflet-gpx v2.1.2 (vendored UMD build), vanilla JavaScript, CSS

**Spec:** `docs/superpowers/specs/2026-04-23-gpx-overlay-design.md`

---

## File Structure

| File | Responsibility |
|------|---------------|
| `src/direwolf_dashboard/static/leaflet/gpx.js` | **New** — Vendored leaflet-gpx v2.1.2 UMD build. Extends `L.FeatureGroup` with GPX parsing. |
| `src/direwolf_dashboard/static/index.html` | Add `<script>` tag for `gpx.js` between `leaflet.js` and `app.js` |
| `src/direwolf_dashboard/static/app.js` | **Modify** — Add GPX overlay module (~120 lines) within the existing IIFE |
| `src/direwolf_dashboard/static/style.css` | **Modify** — Add `.gpx-control` styles (~60 lines) |
| `tests/test_gpx_overlay.html` | **New** — Manual test page with sample GPX data for verifying the overlay |

---

## Chunk 1: Vendor Plugin and Wire Up Script Tag

### Task 1: Vendor the leaflet-gpx UMD build

**Files:**
- Create: `src/direwolf_dashboard/static/leaflet/gpx.js`

- [ ] **Step 1: Download the leaflet-gpx UMD build**

```bash
curl -o src/direwolf_dashboard/static/leaflet/gpx.js \
  'https://cdn.jsdelivr.net/npm/leaflet-gpx@2.1.2/gpx.js'
```

- [ ] **Step 2: Verify the file starts with the expected license header and uses L.GPX**

```bash
head -5 src/direwolf_dashboard/static/leaflet/gpx.js
# Expected: starts with /** Copyright ... */
grep -c 'L.GPX = L.FeatureGroup.extend' src/direwolf_dashboard/static/leaflet/gpx.js
# Expected: 1
```

- [ ] **Step 3: Verify no external URLs in the vendored file**

```bash
grep -n 'http://' src/direwolf_dashboard/static/leaflet/gpx.js | grep -v '// ' | grep -v 'topografix.com/GPX'
# Expected: no matches (the topografix.com URL is just an XML namespace string, not a network request)
```

- [ ] **Step 4: Commit**

```bash
git add src/direwolf_dashboard/static/leaflet/gpx.js
git commit -m "vendor: add leaflet-gpx v2.1.2 UMD build for GPX overlay"
```

### Task 2: Add script tag to index.html

**Files:**
- Modify: `src/direwolf_dashboard/static/index.html:233`

- [ ] **Step 1: Add gpx.js script tag between leaflet.js and app.js**

In `src/direwolf_dashboard/static/index.html`, change:

```html
    <script src="/static/leaflet/leaflet.js"></script>
    <script src="/static/app.js"></script>
```

To:

```html
    <script src="/static/leaflet/leaflet.js"></script>
    <script src="/static/leaflet/gpx.js"></script>
    <script src="/static/app.js"></script>
```

The order matters: `gpx.js` depends on `L` being defined by `leaflet.js`, and `app.js` depends on `L.GPX` being defined by `gpx.js`.

- [ ] **Step 2: Commit**

```bash
git add src/direwolf_dashboard/static/index.html
git commit -m "feat: load leaflet-gpx plugin in index.html"
```

---

## Chunk 2: CSS Styles for GPX Control

### Task 3: Add GPX control CSS

**Files:**
- Modify: `src/direwolf_dashboard/static/style.css` (append after `.map-legend` block, around line 643)

- [ ] **Step 1: Add GPX control styles**

Append the following CSS after the `.map-legend.collapsed .map-legend-header` rule block (after line 642 in `style.css`):

```css
/* --- GPX Overlay Control --- */
.gpx-control {
    background: rgba(30, 30, 30, 0.92);
    border-radius: 6px;
    padding: 0;
    font-size: 11px;
    color: #ccc;
    border: 1px solid #444;
    pointer-events: auto;
    max-width: 170px;
    overflow: hidden;
}
.gpx-control-header {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 7px 10px;
    cursor: pointer;
    font-size: 10px;
    color: #ff8c00;
    border-bottom: 1px solid #444;
    user-select: none;
}
.gpx-control-header:hover {
    color: #ffaa33;
}
.gpx-control-header .gpx-collapse-icon {
    margin-left: auto;
    font-size: 8px;
    color: #666;
}
.gpx-control.collapsed .gpx-control-body {
    display: none;
}
.gpx-control.collapsed .gpx-control-header {
    border-bottom: none;
}
.gpx-control-body {
    padding: 8px 10px;
}
.gpx-control .gpx-stats {
    font-size: 9px;
    color: #aaa;
    margin-bottom: 6px;
}
.gpx-control .gpx-stats .gpx-filename {
    color: #ccc;
    font-weight: bold;
    margin-bottom: 2px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 140px;
}
.gpx-control .gpx-actions {
    display: flex;
    gap: 6px;
}
.gpx-control .gpx-actions button {
    flex: 1;
    padding: 3px 0;
    text-align: center;
    border: none;
    border-radius: 3px;
    font-size: 9px;
    font-family: inherit;
    cursor: pointer;
}
.gpx-control .gpx-btn-fit {
    background: #ff8c00;
    color: #000;
    font-weight: bold;
}
.gpx-control .gpx-btn-fit:hover {
    background: #ffaa33;
}
.gpx-control .gpx-btn-clear {
    background: #444;
    color: #ccc;
}
.gpx-control .gpx-btn-clear:hover {
    background: #555;
}
.gpx-control .gpx-btn-load {
    width: 100%;
    padding: 4px 0;
    background: #ff8c00;
    color: #000;
    font-weight: bold;
    border: none;
    border-radius: 3px;
    font-size: 10px;
    font-family: inherit;
    cursor: pointer;
}
.gpx-control .gpx-btn-load:hover {
    background: #ffaa33;
}
.gpx-control .gpx-error {
    font-size: 9px;
    color: #e74c3c;
    margin-top: 4px;
}
.gpx-marker {
    background: none !important;
    border: none !important;
}
```

- [ ] **Step 2: Verify CSS parses without errors**

Open the dashboard in a browser and check the browser console for CSS parse errors. Alternatively:

```bash
# Quick sanity check — count braces match
python3 -c "
css = open('src/direwolf_dashboard/static/style.css').read()
print('Open:', css.count('{'), 'Close:', css.count('}'))
assert css.count('{') == css.count('}'), 'Brace mismatch!'
print('OK: braces balanced')
"
```

- [ ] **Step 3: Commit**

```bash
git add src/direwolf_dashboard/static/style.css
git commit -m "feat: add CSS styles for GPX overlay map control"
```

---

## Chunk 3: GPX Overlay Module in app.js

### Task 4: Add GPX overlay state and helper function

**Files:**
- Modify: `src/direwolf_dashboard/static/app.js`

- [ ] **Step 1: Add GPX state variable**

In `app.js`, after line 24 (`let showRouteDistances = true;`), add:

```javascript
    let gpxLayer = null;    // Current GPX overlay layer (L.GPX instance)
```

- [ ] **Step 2: Add gpxCircleIcon helper function**

In `app.js`, after the `initLegend()` function definition (after line 202), add the GPX helper:

```javascript
    // --- GPX Overlay ---
    function gpxCircleIcon(fillColor, radius) {
        var size = radius * 2 + 4;
        return L.divIcon({
            className: 'gpx-marker',
            iconSize: [size, size],
            iconAnchor: [size / 2, size / 2],
            html: '<svg width="' + size + '" height="' + size + '">' +
                  '<circle cx="' + (size / 2) + '" cy="' + (size / 2) + '" r="' + radius + '" ' +
                  'fill="' + fillColor + '" stroke="white" stroke-width="1.5"/></svg>'
        });
    }
```

- [ ] **Step 3: Commit**

```bash
git add src/direwolf_dashboard/static/app.js
git commit -m "feat: add GPX state variable and circle icon helper"
```

### Task 5: Add the GPX control and core logic

**Files:**
- Modify: `src/direwolf_dashboard/static/app.js`

- [ ] **Step 1: Add initGpxOverlay function**

In `app.js`, after the `gpxCircleIcon` helper, add:

```javascript
    function initGpxOverlay() {
        var gpxFileInput = document.createElement('input');
        gpxFileInput.type = 'file';
        gpxFileInput.accept = '.gpx,.xml';
        gpxFileInput.style.display = 'none';
        document.body.appendChild(gpxFileInput);

        var GpxControl = L.Control.extend({
            options: { position: 'topleft' },
            onAdd: function () {
                var container = L.DomUtil.create('div', 'gpx-control');
                container.innerHTML =
                    '<div class="gpx-control-header">' +
                        '<span>\u{1F4CD}</span>' +
                        '<span>GPX Overlay</span>' +
                        '<span class="gpx-collapse-icon">\u25B2</span>' +
                    '</div>' +
                    '<div class="gpx-control-body">' +
                        '<div class="gpx-empty">' +
                            '<button class="gpx-btn-load">Load GPX</button>' +
                        '</div>' +
                        '<div class="gpx-loaded" style="display:none;">' +
                            '<div class="gpx-stats">' +
                                '<div class="gpx-filename"></div>' +
                                '<div class="gpx-info"></div>' +
                            '</div>' +
                            '<div class="gpx-actions">' +
                                '<button class="gpx-btn-fit">Fit Map</button>' +
                                '<button class="gpx-btn-clear">Clear</button>' +
                            '</div>' +
                        '</div>' +
                        '<div class="gpx-error" style="display:none;"></div>' +
                    '</div>';

                var header = container.querySelector('.gpx-control-header');
                var collapseIcon = container.querySelector('.gpx-collapse-icon');
                header.addEventListener('click', function () {
                    container.classList.toggle('collapsed');
                    collapseIcon.textContent = container.classList.contains('collapsed') ? '\u25BC' : '\u25B2';
                });

                container.querySelector('.gpx-btn-load').addEventListener('click', function () {
                    gpxFileInput.click();
                });

                container.querySelector('.gpx-btn-fit').addEventListener('click', function () {
                    if (gpxLayer) {
                        map.fitBounds(gpxLayer.getBounds());
                    }
                });

                container.querySelector('.gpx-btn-clear').addEventListener('click', function () {
                    clearGpxOverlay(container);
                });

                L.DomEvent.disableClickPropagation(container);
                L.DomEvent.disableScrollPropagation(container);

                this._container = container;
                return container;
            }
        });

        var gpxControl = new GpxControl();
        gpxControl.addTo(map);
        var controlEl = gpxControl._container;

        gpxFileInput.addEventListener('change', function () {
            if (!gpxFileInput.files || !gpxFileInput.files[0]) return;
            var file = gpxFileInput.files[0];

            // Size check
            if (file.size > 5 * 1024 * 1024) {
                showGpxError(controlEl, 'File too large (max 5MB)');
                gpxFileInput.value = '';
                return;
            }

            var reader = new FileReader();
            reader.onload = function (e) {
                loadGpxData(controlEl, e.target.result, file.name);
            };
            reader.readAsText(file);
            gpxFileInput.value = '';
        });

        // Restore from localStorage on init
        try {
            var saved = localStorage.getItem('gpx_overlay');
            if (saved) {
                var parsed = JSON.parse(saved);
                if (parsed && parsed.data) {
                    loadGpxData(controlEl, parsed.data, parsed.filename || 'saved.gpx');
                }
            }
        } catch (e) {
            console.warn('Failed to restore GPX overlay:', e);
        }
    }

    function loadGpxData(controlEl, gpxText, filename) {
        // Remove existing layer
        if (gpxLayer) {
            map.removeLayer(gpxLayer);
            gpxLayer = null;
        }

        var errorEl = controlEl.querySelector('.gpx-error');
        errorEl.style.display = 'none';

        try {
            gpxLayer = new L.GPX(gpxText, {
                async: true,
                parseElements: ['track', 'route', 'waypoint'],
                polyline_options: [
                    {
                        color: '#ff8c00',
                        opacity: 0.8,
                        weight: 3,
                        lineCap: 'round',
                        lineJoin: 'round'
                    },
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
            }).on('loaded', function (e) {
                var gpx = e.target;
                var layers = gpx.getLayers();
                if (!layers || layers.length === 0) {
                    showGpxError(controlEl, 'No tracks or waypoints found');
                    map.removeLayer(gpxLayer);
                    gpxLayer = null;
                    return;
                }

                // Show loaded state
                var truncName = filename.length > 20 ? filename.substring(0, 17) + '...' : filename;
                controlEl.querySelector('.gpx-filename').textContent = truncName;
                controlEl.querySelector('.gpx-filename').title = filename;

                var distKm = (gpx.get_distance() / 1000).toFixed(1);
                var parts = [distKm + ' km'];
                // Count tracks and waypoints from the GPX layers
                var nTrk = 0, nWpt = 0;
                gpx.getLayers().forEach(function (layer) {
                    if (layer instanceof L.Polyline) nTrk++;
                    else if (layer instanceof L.Marker) nWpt++;
                });
                // Subtract start/end markers from waypoint count
                if (nWpt >= 2) { nWpt -= 2; nTrk = Math.max(nTrk, 1); }
                if (nTrk > 0) parts.push(nTrk + ' trk');
                if (nWpt > 0) parts.push(nWpt + ' wpt');
                controlEl.querySelector('.gpx-info').textContent = parts.join(' \u00B7 ');

                controlEl.querySelector('.gpx-empty').style.display = 'none';
                controlEl.querySelector('.gpx-loaded').style.display = '';

                // Update legend
                updateLegendGpx(true);

                // Save to localStorage
                try {
                    localStorage.setItem('gpx_overlay', JSON.stringify({
                        filename: filename,
                        data: gpxText
                    }));
                } catch (e) {
                    if (e.name === 'QuotaExceededError') {
                        showGpxError(controlEl, 'Too large to save \u2014 won\u2019t persist on reload');
                    }
                }

                map.fitBounds(gpx.getBounds());
            }).on('error', function (e) {
                showGpxError(controlEl, 'Invalid GPX file');
                gpxLayer = null;
            }).addTo(map);
        } catch (e) {
            showGpxError(controlEl, 'Failed to parse GPX file');
            gpxLayer = null;
        }
    }

    function clearGpxOverlay(controlEl) {
        if (gpxLayer) {
            map.removeLayer(gpxLayer);
            gpxLayer = null;
        }
        controlEl.querySelector('.gpx-empty').style.display = '';
        controlEl.querySelector('.gpx-loaded').style.display = 'none';
        controlEl.querySelector('.gpx-error').style.display = 'none';
        updateLegendGpx(false);
        try {
            localStorage.removeItem('gpx_overlay');
        } catch (e) { /* ignore */ }
    }

    function showGpxError(controlEl, message) {
        var errorEl = controlEl.querySelector('.gpx-error');
        errorEl.textContent = message;
        errorEl.style.display = '';
        setTimeout(function () {
            errorEl.style.display = 'none';
        }, 5000);
    }
```

- [ ] **Step 2: Commit**

```bash
git add src/direwolf_dashboard/static/app.js
git commit -m "feat: add GPX overlay control, load/clear/error logic"
```

### Task 6: Add legend update function and init call

**Files:**
- Modify: `src/direwolf_dashboard/static/app.js`

- [ ] **Step 1: Add updateLegendGpx function**

Add after the `showGpxError` function:

```javascript
    function updateLegendGpx(show) {
        var legend = document.querySelector('.map-legend .legend-body');
        if (!legend) return;
        var existing = legend.querySelector('.legend-gpx-section');
        if (show && !existing) {
            var section = document.createElement('div');
            section.className = 'legend-gpx-section';
            section.innerHTML =
                '<div class="legend-section-title">GPX Overlay</div>' +
                '<div class="legend-item">' +
                    '<div style="width:20px;height:3px;background:#ff8c00;border-radius:2px;"></div>' +
                    '<span>GPX track</span>' +
                '</div>' +
                '<div class="legend-item">' +
                    '<div style="width:20px;height:0;border-top:3px dashed #ff8c00;opacity:0.8;"></div>' +
                    '<span>GPX route</span>' +
                '</div>' +
                '<div class="legend-item">' +
                    '<svg width="10" height="10"><circle cx="5" cy="5" r="4" fill="#a855f7" stroke="white" stroke-width="1"/></svg>' +
                    '<span>GPX waypoint</span>' +
                '</div>';
            legend.appendChild(section);
        } else if (!show && existing) {
            existing.remove();
        }
    }
```

- [ ] **Step 2: Add initGpxOverlay call to the DOMContentLoaded init sequence**

In `app.js`, in the `DOMContentLoaded` event handler (around line 220), add `initGpxOverlay();` after `initLegend();`:

Change:

```javascript
        initMap();
        initLegend();
        await loadStations();
```

To:

```javascript
        initMap();
        initLegend();
        initGpxOverlay();
        await loadStations();
```

- [ ] **Step 3: Commit**

```bash
git add src/direwolf_dashboard/static/app.js
git commit -m "feat: add GPX legend section and wire up init call"
```

---

## Chunk 4: Manual Test and Verification

### Task 7: Create a manual test page

**Files:**
- Create: `tests/test_gpx_overlay.html`

- [ ] **Step 1: Create a test HTML file with embedded sample GPX data**

```html
<!DOCTYPE html>
<html>
<head>
    <title>GPX Overlay Test</title>
    <style>body { font-family: monospace; padding: 20px; }</style>
</head>
<body>
    <h1>GPX Overlay Manual Test</h1>
    <h2>Sample GPX Files for Testing</h2>

    <h3>1. Valid GPX with track + waypoints</h3>
    <p>Save as <code>test_track.gpx</code>:</p>
    <textarea rows="20" cols="80" readonly>&lt;?xml version="1.0" encoding="UTF-8"?&gt;
&lt;gpx version="1.1" creator="test"
     xmlns="http://www.topografix.com/GPX/1/1"&gt;
  &lt;wpt lat="38.8977" lon="-77.0365"&gt;
    &lt;name&gt;Start Point&lt;/name&gt;
  &lt;/wpt&gt;
  &lt;wpt lat="38.8895" lon="-77.0353"&gt;
    &lt;name&gt;Waypoint 2&lt;/name&gt;
  &lt;/wpt&gt;
  &lt;trk&gt;
    &lt;name&gt;Test Track&lt;/name&gt;
    &lt;trkseg&gt;
      &lt;trkpt lat="38.8977" lon="-77.0365"&gt;&lt;/trkpt&gt;
      &lt;trkpt lat="38.8960" lon="-77.0360"&gt;&lt;/trkpt&gt;
      &lt;trkpt lat="38.8940" lon="-77.0355"&gt;&lt;/trkpt&gt;
      &lt;trkpt lat="38.8920" lon="-77.0350"&gt;&lt;/trkpt&gt;
      &lt;trkpt lat="38.8895" lon="-77.0353"&gt;&lt;/trkpt&gt;
    &lt;/trkseg&gt;
  &lt;/trk&gt;
&lt;/gpx&gt;</textarea>

    <h3>2. Valid GPX with route</h3>
    <p>Save as <code>test_route.gpx</code>:</p>
    <textarea rows="15" cols="80" readonly>&lt;?xml version="1.0" encoding="UTF-8"?&gt;
&lt;gpx version="1.1" creator="test"
     xmlns="http://www.topografix.com/GPX/1/1"&gt;
  &lt;rte&gt;
    &lt;name&gt;Test Route&lt;/name&gt;
    &lt;rtept lat="40.7128" lon="-74.0060"&gt;&lt;name&gt;NYC&lt;/name&gt;&lt;/rtept&gt;
    &lt;rtept lat="40.7580" lon="-73.9855"&gt;&lt;name&gt;Times Square&lt;/name&gt;&lt;/rtept&gt;
    &lt;rtept lat="40.7484" lon="-73.9857"&gt;&lt;name&gt;Empire State&lt;/name&gt;&lt;/rtept&gt;
  &lt;/rte&gt;
&lt;/gpx&gt;</textarea>

    <h3>3. Empty GPX (should show error)</h3>
    <p>Save as <code>test_empty.gpx</code>:</p>
    <textarea rows="5" cols="80" readonly>&lt;?xml version="1.0"?&gt;
&lt;gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1"&gt;
&lt;/gpx&gt;</textarea>

    <h3>4. Invalid XML (should show error)</h3>
    <p>Save as <code>test_invalid.gpx</code>:</p>
    <textarea rows="3" cols="80" readonly>this is not xml at all &lt;broken</textarea>

    <h2>Test Checklist</h2>
    <ul>
        <li>[ ] Load test_track.gpx — orange track line with green start / red end markers, purple waypoints</li>
        <li>[ ] Load test_route.gpx — dashed orange route line</li>
        <li>[ ] Load test_empty.gpx — "No tracks or waypoints found" error message</li>
        <li>[ ] Load test_invalid.gpx — "Invalid GPX file" error message</li>
        <li>[ ] "Fit Map" button zooms to GPX bounds</li>
        <li>[ ] "Clear" button removes overlay and reverts to "Load GPX" button</li>
        <li>[ ] Legend shows "GPX Overlay" section when loaded, hides when cleared</li>
        <li>[ ] Reload page — GPX overlay persists from localStorage</li>
        <li>[ ] Clear, reload page — no overlay shown</li>
        <li>[ ] Load a new file while one is already loaded — old one replaced</li>
        <li>[ ] Collapse/expand the GPX control header</li>
        <li>[ ] APRS station markers still render correctly on top of GPX overlay</li>
    </ul>
</body>
</html>
```

- [ ] **Step 2: Commit**

```bash
git add tests/test_gpx_overlay.html
git commit -m "test: add manual GPX overlay test page with sample data"
```

### Task 8: Run the application and verify

- [ ] **Step 1: Start the dashboard**

```bash
cd /Users/I530566/devel/mine/hamradio/direwolf-dashboard
uv run direwolf-dashboard serve
```

- [ ] **Step 2: Open the dashboard in a browser and verify:**

1. The GPX control appears at top-left below the zoom buttons
2. Click "Load GPX" — file picker opens, accepts `.gpx` and `.xml` files
3. Load a valid GPX file — track renders in orange, markers appear, stats show
4. "Fit Map" zooms to the GPX bounds
5. Legend updates with "GPX Overlay" section
6. Reload the page — GPX overlay reappears from localStorage
7. "Clear" removes the overlay and legend section
8. Load an empty GPX — error message appears and auto-clears
9. Load invalid XML — error message appears and auto-clears
10. Existing APRS features (station markers, trails, packet routes) still work

- [ ] **Step 3: Run existing tests to confirm no regressions**

```bash
uv run pytest tests/ -v
```

Expected: All existing tests pass. (No Python changes were made, so no regressions expected.)

- [ ] **Step 4: Final commit if any adjustments were needed**

```bash
git add -A
git commit -m "feat: GPX file overlay — complete implementation"
```
