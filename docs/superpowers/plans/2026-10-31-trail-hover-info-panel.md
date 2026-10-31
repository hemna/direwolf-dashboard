# Plan: trail hover highlight + station info panel (#207) — direwolf-dashboard

Companion to the HELD enhancements. Mirrors haminfo issue #359 / PR #407
(merge `565ee8e5`) and its follow-ups `0e076fd6` (panel re-place when an
overlay opens), `b67ef60b` (a trail click opens the panel every time).

- Issue: http://git.hemna.com/hemna/direwolf-dashboard/issues/207
- Shared spec + statistics maths: http://git.hemna.com/hemna/basemap-kit/issues/65
- Reference: `~/devel/mine/hamradio/haminfo` @ `565ee8e5`
  - `…/static/js/map-shared/trail-stats.js` (293 lines, engine-agnostic maths)
  - `…/static/js/map-shared/trail-panel.js` (285 lines, DOM only:
    `escapeHtml`, `symbolHtml`, `windowLabel`, `speedRow`, `buildRows`,
    `overlaps`, `computePlacement`, `create`/`show`/`place`/`close`/`isOpen`)
  - `…/templates/dashboard/map.html` (hover + click wiring)
  - tests: `…/tests/test_map_trail_panel.py`, `…/tests/test_trail_panel_browser.py`,
    `…/tests/js/trail-stats.test.js`, `…/tests/js/trail-panel.test.js`

## Measured current state (this repo, master `3e98b2f`)

- Trails today: source `station-tracks`, layer `station-tracks-line`
  (`static/app.js`), rows from `GET /api/station_tracks?hours=` →
  `{callsign: [[lat, lon, timestamp], …]}` oldest-first
  (`routers.py:249`, `storage.py:361` — no speed or course columns;
  `packets` table stores `latitude`, `longitude`, `timestamp` only).
- No hover on trails, no info panel: grep `infoPanel`/`info.?panel` → 0 hits.
- Overlay chrome that a panel must dodge (measured `app.js` + localStorage keys):
  legend `dw-legend-collapsed`, stats `dw-stats-collapsed`, filters
  `dw-filter-collapsed`, my-location `dw-myloc-collapsed`, plus the live log
  panel and header controls.
- Client test convention: source-assert (`tests/test_maplibre_*.py`) + node
  harness (`tests/test_app_settings_save.py` extracts named functions from
  `app.js` and runs them under `node -e` with a stub DOM; skips if no node).

## Port decisions

1. **Maths is the spec, not an approximation.** Port `trail-stats.js` verbatim
   into one `app.js` section: `EARTH_RADIUS_KM = 6371.0088`,
   `SAME_POSITION_DEG = 0.00001`, `GAP_MIN_SECONDS = 300`,
   `GAP_MEDIAN_FACTOR = 3`, `BAD_FIX_KMH = 1000`, `KM_PER_MILE = 1.609344`,
   `haversineKm`, `bearingDeg`, `normalise`, `median`, `mean`,
   `compute(points, options)`, `formatDistance`. It must reproduce the spec's
   worked example exactly (issue #207 body): 6 points, 2.4 km, 9 min elapsed,
   beacon interval 60 s median and mean over 4 gaps, 16/21/22 km/h, heading 50°.
2. **Derived branches are the common case here**, since this app's track rows
   carry no speed or course: `headingReported = false` →
   `headingDeg = bearingDeg(second-last, last)`; `maxKph`/`averageKph` from
   derived segment speeds. Keep the reported-value branches for when speed
   is present, so the code matches the other clients.
3. **Hover = 8 screen px.** `queryRenderedFeatures` with a box of ±8 px around
   the pointer on `station-tracks-line` only; the whole trail highlights (one
   feature per station in `station-tracks`, so a new
   `station-tracks-highlight` layer drawn from that feature), cursor `pointer`.
4. **Click opens the panel; Escape closes it.** Panel is one DOM element built
   by a `trail-panel` section porting `buildRows`/`speedRow`/`windowLabel`/
   `symbolHtml` (the APRS sprite technique the station popup already uses),
   field order per the spec. `place()` never covers the hovered trail and dodges
   the overlay chrome above; it re-places when an overlay opens or closes —
   haminfo's Live Packets sheet is this app's live-log panel.
5. **A trail click must open the panel every time** (`b67ef60b`): the hover hit
   test must not depend on a previous hover state.

## Tasks (TDD, one commit each)

### Task 1 — Stats maths (node harness)
- **RED:** `tests/test_trail_stats.py`: extract `compute`, `haversineKm`,
  `bearingDeg`, `normalise`, `median`, `mean`, `formatDistance` from `app.js`
  and assert the worked example (2.4 km / 9 min / 60 s median+mean / 16, 21, 22
  km/h / 50° / 6 points), plus: duplicate-point collapse, bad-fix rejection
  (>1000 km/h), gap split at `max(3 × median interval, 300 s)` with the split
  excluded from distance and speed, `<2` points → `hasTrail: false`,
  imperial vs metric formatting.
- **GREEN:** the stats section (port).
- Commit: `feat(map): spec trail statistics from basemap-kit#65 (#207)`

### Task 2 — Hover highlight (MapLibre layer + query)
- **RED:** source-asserts in `tests/test_map_trail_panel.py` (the tolerance
  constant is 8 px, only `station-tracks-line` is queried, a
  `station-tracks-highlight` layer is added/removed, `move` cursor on hit);
  node harness for the hit-test helper.
- **GREEN:** hover code next to the existing trail source; re-added after a
  style reload the way `tx-arcs` is.
- Commit: `feat(map): highlight a whole station trail on hover (#207)`

### Task 3 — Info panel (DOM + placement)
- **RED:** node harness on the panel section: `buildRows` field order
  (distance, elapsed, beacon interval, speeds, heading, last heard, point
  count), `escapeHtml` applied to callsign/comment, missing values render
  `-`; `place()` returns a position that does not overlap the trail bbox nor
  any chrome rect, and re-places when a chrome rect changes.
- **GREEN:** panel section + Escape key close (mirrors the existing key handlers).
- Commit: `feat(map): trail info panel beside the trail (#207)`

### Task 4 — Click opens every time + overlay re-place
- **RED:** source + node asserts that a click calls `show()` without requiring
  hover first, and that opening/closing the live-log or stats overlay re-places
  an open panel (`0e076fd6` analogue).
- **GREEN:** wire click + a `place()` re-run on the existing overlay toggle
  functions.
- Commit: `fix(map): a trail click opens its panel and dodges overlays (#207)`

## Out of scope

No new endpoint or storage field (track rows stay `[lat, lon, timestamp]`;
speeds/heading use the derived branches); no trail statistics in the station
list popup; no mobile-specific behaviour.

## Verification gate

`uvx ruff check . --select=E,F --ignore=E501` · `uv run pytest tests/ -q` ·
`node --check` every touched `.js`.

## Known risks to state in the PR

- Hover tolerance is screen-space, so at very low zoom several trails compete;
  nearest-wins is the reference behaviour — keep it, note it.
- Derived speeds depend on packet spacing; a station whose packets are all
  one fix apart shows `-` for speed rows (the spec's gap rules apply).

## Appendix A — haminfo reference kit (verbatim, inlined)

Merge `565ee8e5` (PR #407), plus follow-ups `0e076fd6` (PR #421) and `b67ef60b` (PR #423).

### trail-stats.js (complete — the spec maths)

```js
/* map-shared: station trail statistics (shared spec, basemap-kit#65).
 * Engine-agnostic (no Leaflet, no MapLibre, no DOM). Exposed as
 * window.MapShared.trailStats.
 *
 * The maths is the canonical spec, not a local approximation: every map client
 * must reproduce the worked example in basemap-kit#65 exactly, so this module
 * is the single place the numbers are computed and it is unit-tested against
 * that example (tests/js/trail-stats.test.js).
 *
 * Input points are the trail endpoint's rows, oldest first:
 *   [longitude, latitude, timestamp, speed, course, altitude, comment]
 * with speed in km/h and course in degrees; either may be null.
 */
(function () {
    'use strict';

    var EARTH_RADIUS_KM = 6371.0088;   // spec rule 4
    var SAME_POSITION_DEG = 0.00001;   // spec rule 2
    var GAP_MIN_SECONDS = 300;         // spec rule 11
    var GAP_MEDIAN_FACTOR = 3;         // spec rule 11
    var BAD_FIX_KMH = 1000;            // spec rule 12 (bad fixes)
    var KM_PER_MILE = 1.609344;

    function toRad(deg) {
        return deg * Math.PI / 180;
    }

    // Great-circle distance in km, haversine, Earth radius 6371.0088 km.
    function haversineKm(lat1, lon1, lat2, lon2) {
        var dLat = toRad(lat2 - lat1);
        var dLon = toRad(lon2 - lon1);
        var a = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
            Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) *
            Math.sin(dLon / 2) * Math.sin(dLon / 2);
        return EARTH_RADIUS_KM * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
    }

    // Initial bearing in whole-ish degrees, 0-360, north = 0.
    function bearingDeg(lat1, lon1, lat2, lon2) {
        var dLon = toRad(lon2 - lon1);
        var y = Math.sin(dLon) * Math.cos(toRad(lat2));
        var x = Math.cos(toRad(lat1)) * Math.sin(toRad(lat2)) -
            Math.sin(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.cos(dLon);
        return (Math.atan2(y, x) * 180 / Math.PI + 360) % 360;
    }

    function numberOf(value) {
        return typeof value === 'number' && isFinite(value) ? value : null;
    }

    function timeMs(timestamp) {
        var t = new Date(timestamp).getTime();
        return isFinite(t) ? t : null;
    }

    /**
     * Rules 1-3: keep only usable positions, merge duplicate hears, correct
     * same-timestamp disagreements, and sort by time.
     */
    function normalise(points) {
        var usable = [];
        (points || []).forEach(function (p) {
            if (!p) return;
            var lon = numberOf(p[0]);
            var lat = numberOf(p[1]);
            var t = timeMs(p[2]);
            if (lon === null || lat === null || t === null) return;
            usable.push({
                lon: lon,
                lat: lat,
                t: t,
                timestamp: p[2],
                speed: numberOf(p[3]),
                course: numberOf(p[4]),
            });
        });
        usable.sort(function (a, b) { return a.t - b.t; });

        var out = [];
        usable.forEach(function (p) {
            var prev = out[out.length - 1];
            if (prev && prev.t === p.t) {
                var samePlace =
                    Math.abs(prev.lat - p.lat) <= SAME_POSITION_DEG &&
                    Math.abs(prev.lon - p.lon) <= SAME_POSITION_DEG;
                if (samePlace) return;      // one hear, keep the earliest
                out[out.length - 1] = p;    // a correction, keep the last heard
                return;
            }
            out.push(p);
        });
        return out;
    }

    function median(values) {
        if (!values.length) return null;
        var sorted = values.slice().sort(function (a, b) { return a - b; });
        var mid = Math.floor(sorted.length / 2);
        return sorted.length % 2
            ? sorted[mid]
            : (sorted[mid - 1] + sorted[mid]) / 2;
    }

    function mean(values) {
        if (!values.length) return null;
        return values.reduce(function (a, b) { return a + b; }, 0) / values.length;
    }

    /**
     * Compute the panel's numbers. Returns raw values (km, km/h, seconds,
     * degrees); formatting is a separate concern so display rounding can never
     * leak into the maths.
     */
    function compute(points, options) {
        var opts = options || {};
        var rows = normalise(points);
        var result = {
            points: rows.length,
            hasTrail: rows.length >= 2,
            firstTime: rows.length ? rows[0].timestamp : null,
            lastTime: rows.length ? rows[rows.length - 1].timestamp : null,
            elapsedSeconds: null,
            distanceKm: null,
            averageKph: null,
            currentKph: null,
            currentReported: false,
            maxKph: null,
            maxReported: false,
            headingDeg: null,
            headingReported: false,
            beacon: { medianSeconds: null, meanSeconds: null, intervals: 0 },
            badFixes: 0,
            truncated: !!opts.truncated,
            returnedPoints: opts.returnedPoints || rows.length,
            windowHours: opts.windowHours || null,
        };
        if (!rows.length) return result;

        // Rule 14: fewer than two points is not a trail, so nothing else is
        // computed and the panel shows only the callsign and last heard.
        if (!result.hasTrail) return result;

        var segments = [];
        for (var i = 1; i < rows.length; i++) {
            var a = rows[i - 1];
            var b = rows[i];
            var distKm = haversineKm(a.lat, a.lon, b.lat, b.lon);
            var seconds = (b.t - a.t) / 1000;
            segments.push({
                distKm: distKm,
                seconds: seconds,
                derivedKph: seconds > 0 ? distKm / (seconds / 3600) : null,
                bad: seconds > 0 && (distKm / (seconds / 3600)) > BAD_FIX_KMH,
                gap: false,     // filled in below, once the median is known
            });
        }

        // Rule 11: a gap is longer than the larger of three times the median
        // interval and 300 seconds. The boundary is included: the spec's worked
        // example calls its 300 s interval a gap and expects 4 beacon intervals.
        var intervals = segments.map(function (s) { return s.seconds; });
        var medInterval = median(intervals);
        var gapThreshold = Math.max(GAP_MEDIAN_FACTOR * (medInterval || 0), GAP_MIN_SECONDS);
        segments.forEach(function (s) {
            s.gap = s.seconds >= gapThreshold;
        });

        // Rule 12: a bad fix is excluded from distance and from maximum speed
        // and is reported, never silently dropped.
        result.badFixes = segments.filter(function (s) { return s.bad; }).length;

        result.elapsedSeconds = (rows[rows.length - 1].t - rows[0].t) / 1000;
        result.distanceKm = segments.reduce(function (sum, s) {
            return sum + (s.bad ? 0 : s.distKm);
        }, 0);
        result.averageKph = result.elapsedSeconds > 0
            ? result.distanceKm / (result.elapsedSeconds / 3600)
            : null;

        // Rule 8: reported speed on the newest point, else the last segment's
        // derived speed, and never a number from a gap or a bad fix.
        var newest = rows[rows.length - 1];
        var lastSegment = segments[segments.length - 1];
        if (newest.speed !== null) {
            result.currentKph = newest.speed;
            result.currentReported = true;
        } else if (!lastSegment.gap && !lastSegment.bad) {
            result.currentKph = lastSegment.derivedKph;
        }

        // Rule 9: highest reported speed when any point reports one, else the
        // highest derived segment speed outside gaps and bad fixes.
        var reported = rows.map(function (r) { return r.speed; })
            .filter(function (s) { return s !== null; });
        if (reported.length) {
            result.maxKph = Math.max.apply(null, reported);
            result.maxReported = true;
        } else {
            var derived = segments.filter(function (s) {
                return !s.gap && !s.bad && s.derivedKph !== null;
            }).map(function (s) { return s.derivedKph; });
            result.maxKph = derived.length ? Math.max.apply(null, derived) : null;
        }

        // Rule 10: beacon frequency over the intervals that are not gaps.
        var beaconIntervals = segments.filter(function (s) { return !s.gap; })
            .map(function (s) { return s.seconds; });
        result.beacon = {
            medianSeconds: median(beaconIntervals),
            meanSeconds: mean(beaconIntervals),
            intervals: beaconIntervals.length,
        };

        // Rule 13: reported course at the newest point, else the bearing from
        // the second-newest point.
        if (newest.course !== null) {
            result.headingDeg = newest.course;
            result.headingReported = true;
        } else {
            var prev = rows[rows.length - 2];
            result.headingDeg = bearingDeg(prev.lat, prev.lon, newest.lat, newest.lon);
        }

        return result;
    }

    // ---- display helpers (spec rounding, never used in the maths) ----

    function isMetric(units) {
        return units !== 'imperial';
    }

    // Distances: one decimal below 10, whole above (spec "Rounding").
    function formatDistance(km, units) {
        if (km === null || km === undefined || !isFinite(km)) return '-';
        var value = isMetric(units) ? km : km / KM_PER_MILE;
        var shown = value < 10 ? value.toFixed(1) : Math.round(value).toString();
        return shown + ' ' + (isMetric(units) ? 'km' : 'mi');
    }

    // Speeds: whole units.
    function formatSpeed(kph, units) {
        if (kph === null || kph === undefined || !isFinite(kph)) return '-';
        var value = isMetric(units) ? kph : kph / KM_PER_MILE;
        return Math.round(value) + ' ' + (isMetric(units) ? 'km/h' : 'mph');
    }

    function formatElapsed(seconds) {
        if (seconds === null || seconds === undefined || !isFinite(seconds)) return '-';
        if (seconds < 60) return Math.round(seconds) + ' s';
        if (seconds < 7200) return Math.round(seconds / 60) + ' min';
        var h = Math.floor(seconds / 3600);
        var m = Math.round((seconds - h * 3600) / 60);
        if (m === 60) { h += 1; m = 0; }
        return h + ' h ' + m + ' min';
    }

    function formatInterval(seconds) {
        if (seconds === null || seconds === undefined || !isFinite(seconds)) return '-';
        return Math.round(seconds) + ' s';
    }

    function formatHeading(deg) {
        if (deg === null || deg === undefined || !isFinite(deg)) return '-';
        return (Math.round(deg) % 360) + '°';
    }

    // Local time (the user's own time zone), 24-hour, no date: "10:09".
    function formatClock(timestamp) {
        var t = timeMs(timestamp);
        if (t === null) return '-';
        var d = new Date(t);
        return String(d.getHours()).padStart(2, '0') + ':' +
            String(d.getMinutes()).padStart(2, '0');
    }

    window.MapShared = window.MapShared || {};
    window.MapShared.trailStats = {
        EARTH_RADIUS_KM: EARTH_RADIUS_KM,
        GAP_MIN_SECONDS: GAP_MIN_SECONDS,
        BAD_FIX_KMH: BAD_FIX_KMH,
        haversineKm: haversineKm,
        bearingDeg: bearingDeg,
        normalise: normalise,
        compute: compute,
        formatDistance: formatDistance,
        formatSpeed: formatSpeed,
        formatElapsed: formatElapsed,
        formatInterval: formatInterval,
        formatHeading: formatHeading,
        formatClock: formatClock,
    };
})();

```

### trail-panel.js (complete — DOM panel + placement)

```js
/* map-shared: the trail info panel (shared spec basemap-kit#65, haminfo#359).
 * DOM only — no map engine. The page decides when it opens and where it sits;
 * this module owns the markup, the spec's field order, close behaviour and
 * focus handling.
 *
 * The numbers come from map-shared/trail-stats.js; this module only formats
 * them for display, so the maths and the display are separable and testable.
 */
(function () {
    'use strict';

    function escapeHtml(text) {
        return String(text === null || text === undefined ? '' : text)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    // APRS symbol sprite, the same technique the station popup uses, so a
    // station reads the same in both places.
    function symbolHtml(props) {
        var S = window.MapShared.aprsSymbols;
        if (!S || !props || !props.symbol) return '';
        var table = props.symbol_table || '/';
        var sprite = S.parseSymbolTable(table);
        var pos = S.getSymbolSpritePosition(props.symbol);
        var size = 20;
        var col = pos.x / S.SYMBOL_SIZE;
        var row = pos.y / S.SYMBOL_SIZE;
        return '<span class="trail-panel-symbol" aria-hidden="true" style="' +
            'display:inline-block;width:' + size + 'px;height:' + size + 'px;' +
            'background-image:url(\'' + sprite.spriteUrl + '\');' +
            'background-position:' + (col * size) + 'px ' + (row * size) + 'px;' +
            'background-size:' + (S.SPRITE_COLS * size) + 'px ' + (6 * size) + 'px;' +
            'background-repeat:no-repeat;"></span>';
    }

    function windowLabel(hours) {
        if (hours === null || hours === undefined) return '-';
        if (hours < 1) return 'last ' + Math.round(hours * 60) + ' minutes';
        if (hours === 1) return 'last 1 hour';
        if (hours < 24) return 'last ' + hours + ' hours';
        var days = hours / 24;
        return 'last ' + (days === Math.round(days) ? days : days.toFixed(1)) + ' days';
    }

    function speedRow(label, kph, reported, api, units) {
        if (kph === null || kph === undefined) return {label: label, value: '-'};
        return {
            label: label,
            value: api.formatSpeed(kph, units) + (reported ? ' (reported)' : ' (computed)'),
        };
    }

    // The spec's field order, every field present, "-" where unknown.
    function buildRows(result, api, units, windowHours) {
        return [
            {label: 'Trail start', value: api.formatClock(result.firstTime)},
            {label: 'Elapsed travel time', value: api.formatElapsed(result.elapsedSeconds)},
            {label: 'Total distance', value: api.formatDistance(result.distanceKm, units)},
            {
                label: 'Beacon frequency',
                value: result.beacon.intervals
                    ? api.formatInterval(result.beacon.medianSeconds) + ' median \u00B7 ' +
                      api.formatInterval(result.beacon.meanSeconds) + ' mean (' +
                      result.beacon.intervals + ' intervals)'
                    : '-',
            },
            speedRow('Current speed', result.currentKph, result.currentReported, api, units),
            {label: 'Average speed', value: api.formatSpeed(result.averageKph, units)},
            speedRow('Maximum speed', result.maxKph, result.maxReported, api, units),
            {label: 'Heading', value: api.formatHeading(result.headingDeg)},
            {label: 'Last heard', value: api.formatClock(result.lastTime)},
            {label: 'Points', value: String(result.points)},
            {label: 'Data window', value: windowLabel(windowHours)},
        ];
    }

    // Placement (issue #359 follow-up). The panel sits BESIDE the trail, one
    // gap away from its nearest edge, and flips to the trail's other side only
    // when that first side has no room — never pinned to the far map edge. It
    // is then clamped inside the map viewport and slid clear of the map's own
    // chrome, so it cannot run off an edge or cover the controls. Pure geometry,
    // no DOM, so the rules are unit-testable.
    var PLACEMENT_GAP = 12;
    var PLACEMENT_MARGIN = 10;

    function overlaps(a, b) {
        return a.left < b.right && a.right > b.left &&
            a.top < b.bottom && a.bottom > b.top;
    }

    function computePlacement(trail, size, viewport, chrome) {
        var width = size.width;
        var height = size.height;
        var vw = viewport.width;
        var vh = viewport.height;
        var gap = PLACEMENT_GAP;
        var margin = PLACEMENT_MARGIN;

        // Horizontally: right of the trail, flipping to its left when the right
        // side cannot hold the panel, then clamped so it never leaves the map.
        var minLeft = margin;
        var maxLeft = Math.max(minLeft, vw - width - margin);
        var left = trail.maxX + gap;
        if (left > maxLeft) left = trail.minX - gap - width;
        left = Math.max(minLeft, Math.min(left, maxLeft));

        // Vertically: centred on the trail, but below any bar along the top.
        var minTop = ((chrome && chrome.top) || 0) + margin;
        var maxTop = Math.max(minTop, vh - height - margin);
        var top = Math.max(
            minTop,
            Math.min((trail.minY + trail.maxY) / 2 - height / 2, maxTop));

        // Slide clear of the map's own controls rather than covering them.
        var obstacles = (chrome && chrome.obstacles) || [];
        var blocked = null;
        for (var i = 0; i < obstacles.length; i++) {
            var box = {left: left, right: left + width, top: top, bottom: top + height};
            if (overlaps(box, obstacles[i])) { blocked = obstacles[i]; break; }
        }
        if (blocked) {
            var candidates = [blocked.top - gap - height, blocked.bottom + gap];
            for (var c = 0; c < candidates.length; c++) {
                var candidate = Math.max(minTop, Math.min(candidates[c], maxTop));
                var fits = true;
                for (var k = 0; k < obstacles.length; k++) {
                    var moved = {left: left, right: left + width,
                                 top: candidate, bottom: candidate + height};
                    if (overlaps(moved, obstacles[k])) { fits = false; break; }
                }
                if (fits) { top = candidate; break; }
            }
        }
        return {left: left, top: top};
    }

    function create(options) {
        var root = options.root;
        var api = options.stats;
        var onClose = options.onClose || function () {};
        var element = null;
        var open = false;
        var lastFocus = null;
        var onKeydown = null;

        function ensureElement() {
            if (element) return element;
            element = document.createElement('section');
            element.id = 'trail-panel';
            element.className = 'trail-panel';
            element.setAttribute('role', 'dialog');
            element.setAttribute('aria-labelledby', 'trail-panel-title');
            element.setAttribute('tabindex', '-1');
            element.hidden = true;
            root.appendChild(element);
            return element;
        }

        function close() {
            if (!element || !open) return;
            open = false;
            element.hidden = true;
            if (onKeydown) {
                document.removeEventListener('keydown', onKeydown, true);
                onKeydown = null;
            }
            // preventScroll: focusing must not scroll the page under the user's
            // pointer, which would move the trail away from where they clicked.
            if (lastFocus && typeof lastFocus.focus === 'function') {
                lastFocus.focus({ preventScroll: true });
            }
            lastFocus = null;
            onClose();
        }

        /**
         * Open on one station's trail.
         * opts: {callsign, props, points, view: {units, windowHours,
         *        truncated, returnedPoints}}
         * Returns the computed statistics.
         */
        function show(opts) {
            var view = opts.view || {};
            var result = api.compute(opts.points || [], {
                truncated: view.truncated,
                returnedPoints: view.returnedPoints,
                windowHours: view.windowHours,
            });
            var el = ensureElement();
            var html = '<div class="trail-panel-header">' +
                symbolHtml(opts.props) +
                '<h2 class="trail-panel-title" id="trail-panel-title">' +
                escapeHtml(opts.callsign) + '</h2>' +
                '<button type="button" class="trail-panel-close" ' +
                'aria-label="Close trail panel">\u00D7</button>' +
                '</div>';

            if (!result.hasTrail) {
                // Spec rule 14: no trail is no trail — callsign, last heard and
                // nothing else.
                html += '<dl class="trail-panel-rows">' +
                    '<div class="trail-panel-row"><dt>Last heard</dt><dd>' +
                    escapeHtml(api.formatClock(result.lastTime)) + '</dd></div>' +
                    '</dl><p class="trail-panel-note">No trail in the window</p>';
            } else {
                html += '<dl class="trail-panel-rows">';
                buildRows(result, api, view.units || 'metric', view.windowHours)
                    .forEach(function (row) {
                        html += '<div class="trail-panel-row"><dt>' +
                            escapeHtml(row.label) + '</dt><dd>' +
                            escapeHtml(row.value) + '</dd></div>';
                    });
                html += '</dl>';
                if (result.badFixes) {
                    html += '<p class="trail-panel-note">' + result.badFixes +
                        (result.badFixes === 1 ? ' position' : ' positions') +
                        ' excluded as a bad fix</p>';
                }
                if (result.truncated) {
                    html += '<p class="trail-panel-note">trail truncated, last ' +
                        result.returnedPoints + ' points</p>';
                }
            }
            el.innerHTML = html;
            el.querySelector('.trail-panel-close').addEventListener('click', close);

            lastFocus = document.activeElement;
            open = true;
            el.hidden = false;
            el.focus({ preventScroll: true });

            // Escape works even if focus moved off the panel, and stops there so
            // it never also closes anything behind it (spec rules 5 and 7).
            onKeydown = function (ev) {
                if (ev.key === 'Escape') {
                    ev.preventDefault();
                    ev.stopPropagation();
                    close();
                }
            };
            document.addEventListener('keydown', onKeydown, true);
            return result;
        }

        /**
         * Put the panel beside the trail, inside the map viewport (spec rule 9).
         * Issue #359 follow-up, Hemna: "the station/trail popup should be near
         * the trail" — so it is anchored to the trail itself, never to a far
         * map edge. opts: {trail: {minX, minY, maxX, maxY} in map pixels,
         * viewport: {width, height}, chrome: {top, obstacles}}. Returns the
         * spot it used so a caller (or a test) can see where it landed.
         */
        function place(opts) {
            if (!element || !open) return null;
            var size = {
                width: element.offsetWidth || 280,
                height: element.offsetHeight || 240,
            };
            var spot = computePlacement(opts.trail, size, opts.viewport, opts.chrome);
            element.style.left = spot.left + 'px';
            element.style.right = '';
            element.style.top = spot.top + 'px';
            return spot;
        }

        function isOpen() { return open; }

        return {
            show: show,
            close: close,
            place: place,
            isOpen: isOpen,
            element: ensureElement,
        };
    }

    window.MapShared = window.MapShared || {};
    window.MapShared.trailPanel = {
        create: create,
        buildRows: buildRows,
        windowLabel: windowLabel,
        computePlacement: computePlacement,
    };
})();

```

### follow-up fix diffs (panel re-place, click-opens)

```diff
diff --git a/docs/screenshots/pr407/trail-panel-imperial-dark-hover.png b/docs/screenshots/pr407/trail-panel-imperial-dark-hover.png
index 50554768..eef92b1b 100644
Binary files a/docs/screenshots/pr407/trail-panel-imperial-dark-hover.png and b/docs/screenshots/pr407/trail-panel-imperial-dark-hover.png differ
diff --git a/docs/screenshots/pr407/trail-panel-imperial-dark-panel.png b/docs/screenshots/pr407/trail-panel-imperial-dark-panel.png
index 24eb3579..e1c6b4db 100644
Binary files a/docs/screenshots/pr407/trail-panel-imperial-dark-panel.png and b/docs/screenshots/pr407/trail-panel-imperial-dark-panel.png differ
diff --git a/docs/screenshots/pr407/trail-panel-imperial-dark-sheet.png b/docs/screenshots/pr407/trail-panel-imperial-dark-sheet.png
new file mode 100644
index 00000000..ea460578
Binary files /dev/null and b/docs/screenshots/pr407/trail-panel-imperial-dark-sheet.png differ
diff --git a/docs/screenshots/pr407/trail-panel-light-flipped.png b/docs/screenshots/pr407/trail-panel-light-flipped.png
index ae7aba6a..186878b4 100644
Binary files a/docs/screenshots/pr407/trail-panel-light-flipped.png and b/docs/screenshots/pr407/trail-panel-light-flipped.png differ
diff --git a/docs/screenshots/pr407/trail-panel-metric-light-hover.png b/docs/screenshots/pr407/trail-panel-metric-light-hover.png
index 9853870d..34477040 100644
Binary files a/docs/screenshots/pr407/trail-panel-metric-light-hover.png and b/docs/screenshots/pr407/trail-panel-metric-light-hover.png differ
diff --git a/docs/screenshots/pr407/trail-panel-metric-light-panel.png b/docs/screenshots/pr407/trail-panel-metric-light-panel.png
index cbbbefde..b9aca97f 100644
Binary files a/docs/screenshots/pr407/trail-panel-metric-light-panel.png and b/docs/screenshots/pr407/trail-panel-metric-light-panel.png differ
diff --git a/docs/screenshots/pr407/trail-panel-metric-light-sheet.png b/docs/screenshots/pr407/trail-panel-metric-light-sheet.png
new file mode 100644
index 00000000..b4775055
Binary files /dev/null and b/docs/screenshots/pr407/trail-panel-metric-light-sheet.png differ
diff --git a/haminfo-dashboard/src/haminfo_dashboard/templates/dashboard/map.html b/haminfo-dashboard/src/haminfo_dashboard/templates/dashboard/map.html
index ea243686..8dec3243 100644
--- a/haminfo-dashboard/src/haminfo_dashboard/templates/dashboard/map.html
+++ b/haminfo-dashboard/src/haminfo_dashboard/templates/dashboard/map.html
@@ -1990,6 +1990,12 @@ if (window.haminfoMapNeedsLegacy()) {
             // else. The station popup stays the marker's own click.
             openTrailPanel(feature.properties.callsign);
         });
+        // The sheet's height transition lags the map resize, so place once at
+        // the new canvas and once when the transition has settled (250 ms).
+        map.on('resize', function () {
+            placeTrailPanel();
+            setTimeout(placeTrailPanel, 260);
+        });
         // A click on bare map closes the panel, as the spec requires; a click
         // that landed on a trail is handled above and must not close it.
         map.on('click', function (e) {
@@ -2074,7 +2080,7 @@ if (window.haminfoMapNeedsLegacy()) {
         trailPanel = MapShared.trailPanel.create({
             root: document.querySelector('.map-wrap'),
             stats: MapShared.trailStats,
-            onClose: function () { setTrailSelected(null); },
+            onClose: function () { trailPanelCall = null; setTrailSelected(null); },
         });
         return trailPanel;
     }
@@ -2082,11 +2088,32 @@ if (window.haminfoMapNeedsLegacy()) {
     // Open one station's trail panel from the raw trail points, never from the
     // simplified geometry drawn on the map (spec: statistics come from every
     // stored position), and place it beside the trail it describes.
+    let trailPanelCall = null;
+
+    // The panel sits beside its trail, so it has to move when the map does: the
+    // Live Packets sheet shrinks #map by its own height and MapLibre re-centres
+    // the view, which slides the trail up under a panel left where it was
+    // (UI/UX review of #407: 100 px off with the sheet open).
+    function placeTrailPanel() {
+        if (!trailPanel || !trailPanel.isOpen() || !trailPanelCall) return;
+        const canvas = map.getCanvas();
+        const width = canvas.clientWidth;
+        const height = canvas.clientHeight;
+        trailPanel.place({
+            trail: trailScreenBounds(trailPanelCall) || {
+                minX: width / 2, minY: height / 2, maxX: width / 2, maxY: height / 2,
+            },
+            viewport: { width: width, height: height },
+            chrome: { top: mapChromeTop(), obstacles: mapChromeObstacles() },
+        });
+    }
+
     function openTrailPanel(callsign) {
         const points = liveTrailBuffers[callsign] || [];
         const feature = stationFeatures[callsign];
         const meta = (trailMeta && trailMeta[callsign]) || {};
         const panel = ensureTrailPanel();
+        trailPanelCall = callsign;
         setTrailSelected(callsign);
         panel.show({
             callsign: callsign,
@@ -2099,20 +2126,12 @@ if (window.haminfoMapNeedsLegacy()) {
                 returnedPoints: meta.returned_points,
             },
         });
-        const canvas = map.getCanvas();
-        const width = canvas.clientWidth;
-        const height = canvas.clientHeight;
-        panel.place({
-            trail: trailScreenBounds(callsign) || {
-                minX: width / 2, minY: height / 2, maxX: width / 2, maxY: height / 2,
-            },
-            viewport: { width: width, height: height },
-            chrome: { top: mapChromeTop(), obstacles: mapChromeObstacles() },
-        });
+        placeTrailPanel();
     }
 
     function closeTrailPanel() {
         if (!trailPanel || !trailPanel.isOpen()) return;
+        trailPanelCall = null;
         trailPanel.close();
     }
 
diff --git a/haminfo-dashboard/tests/test_map_trail_panel.py b/haminfo-dashboard/tests/test_map_trail_panel.py
index efb7fe22..e904cbd4 100644
--- a/haminfo-dashboard/tests/test_map_trail_panel.py
+++ b/haminfo-dashboard/tests/test_map_trail_panel.py
@@ -163,3 +163,18 @@ class TestATrailClickOpensOnlyThePanel:
         assert "layers: ['trails']" in block
         assert block.index("layers: ['trails']") < block.index("stations_icon")
         assert 'openStationPopup(feature.geometry.coordinates' in block
+
+    def test_panel_re_places_when_live_packets_shrinks_the_map(self):
+        source = MAP_TEMPLATE.read_text()
+        assert 'function placeTrailPanel() {' in source
+        assert 'trailPanelCall = callsign;' in source
+        block = self._block(
+            source,
+            "map.on('resize', function () {",
+            '\n        });',
+        )
+        # The sheet shrinks #map and MapLibre re-centres the view, so the panel
+        # has to move with its trail rather than stay where it opened.
+        assert 'placeTrailPanel();' in block
+        # The height transition lags the resize by up to 250 ms.
+        assert 'setTimeout(placeTrailPanel, 260)' in block
diff --git a/haminfo-dashboard/tests/test_trail_panel_browser.py b/haminfo-dashboard/tests/test_trail_panel_browser.py
index 75cff416..fb57178e 100644
--- a/haminfo-dashboard/tests/test_trail_panel_browser.py
+++ b/haminfo-dashboard/tests/test_trail_panel_browser.py
@@ -510,3 +510,123 @@ def test_panel_flips_to_the_other_side_of_the_trail(map_page):
     report['assertions'].append({'flipped_pixels': pixel_gate(
         EVIDENCE / f'trail-panel-{report["theme"]}-flipped.png', report['crop']['top'])})
     assert not report['pageerrors'], report['pageerrors']
+
+# The Live Packets sheet shrinks #map rather than covering it (#402), so the
+# trail moves up when the sheet opens. The panel must still sit one gap from its
+# trail, above the sheet, whichever of the two happened first (issue #359
+# follow-up, UI/UX review of #407).
+SHEET_GEOMETRY = """(coords) => {
+    const map = window.__trailTestMap;
+    const base = document.querySelector('.map-wrap').getBoundingClientRect();
+    const rel = (r) => ({left: r.left - base.left, top: r.top - base.top,
+                         right: r.right - base.left, bottom: r.bottom - base.top,
+                         width: r.width, height: r.height});
+    const sheet = document.getElementById('live-packet-sheet');
+    const panel = document.getElementById('trail-panel');
+    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
+    for (const c of coords) {
+        const p = map.project(c);
+        minX = Math.min(minX, p.x); maxX = Math.max(maxX, p.x);
+        minY = Math.min(minY, p.y); maxY = Math.max(maxY, p.y);
+    }
+    return {
+        sheetOpen: getComputedStyle(sheet).display !== 'none',
+        sheet: rel(sheet.getBoundingClientRect()),
+        panel: panel && !panel.hidden ? rel(panel.getBoundingClientRect()) : null,
+        canvas: rel(map.getCanvas().getBoundingClientRect()),
+        trail: {minX, minY, maxX, maxY},
+        wrap: {width: base.width, height: base.height},
+        zoom: map.getZoom(),
+        tilesLoaded: map.areTilesLoaded(),
+    };
+}"""
+
+
+def assert_panel_beside_with_sheet(info):
+    """One gap from the trail, inside the shrunken map, clear of the sheet."""
+    panel, trail, sheet = info['panel'], info['trail'], info['sheet']
+    assert info['sheetOpen'] is True, info
+    assert panel is not None, info
+    gap_right = abs(panel['left'] - (trail['maxX'] + GAP))
+    gap_left = abs((trail['minX'] - panel['right']) - GAP)
+    assert min(gap_right, gap_left) <= 2, info
+    # ...and it tracks the trail vertically: the sheet moves the trail, the
+    # panel has to move with it (measured 100 px adrift before the fix).
+    panel_mid = (panel['top'] + panel['bottom']) / 2
+    trail_mid = (trail['minY'] + trail['maxY']) / 2
+    assert abs(panel_mid - trail_mid) <= 3, info
+    # The panel belongs to the map, which ends where the sheet starts.
+    assert panel['bottom'] <= sheet['top'] + 0.5, info
+    assert panel['left'] >= 0 and panel['right'] <= info['wrap']['width'] + 0.5, info
+    covers = (panel['left'] < sheet['right'] and panel['right'] > sheet['left'] and
+              panel['top'] < sheet['bottom'] and panel['bottom'] > sheet['top'])
+    assert not covers, info
+
+
+@pytest.mark.parametrize('map_page', [
+    {'units': 'metric', 'theme': 'light', 'tag': 'sheet'},
+    {'units': 'imperial', 'theme': 'dark', 'tag': 'sheet'},
+], indirect=True, ids=['metric-light', 'imperial-dark'])
+def test_panel_stays_beside_the_trail_with_live_packets_open(map_page):
+    page, report, name = map_page
+    shot_name = f'trail-panel-{report["units"]}-{report["theme"]}-sheet'
+    coords = [[point[0], point[1]] for point in TRAIL]
+
+    def sheet_open():
+        page.click('#live-feed-btn')
+        page.wait_for_function(
+            "() => getComputedStyle(document.getElementById('live-packet-sheet'))"
+            ".display !== 'none'", timeout=10000)
+        # #map ends where the sheet begins, so the canvas has to have shrunk.
+        page.wait_for_function(
+            "() => document.querySelector('#map .maplibregl-canvas')"
+            ".getBoundingClientRect().height < 700", timeout=10000)
+        # The resize re-renders the basemap; the shutter must not fire before
+        # the tiles for the new canvas size are drawn.
+        page.wait_for_function(BASEMAP_DRAWN, timeout=90000)
+        settle_map(page)
+
+    def open_panel():
+        point = page.evaluate(CANVAS_POINT, TRAIL[2][:2])
+        page.mouse.click(point['x'], point['y'])
+        page.wait_for_function("() => {const p = document.getElementById('trail-panel');"
+                               " return p && !p.hidden;}", timeout=10000)
+
+    # Order A: the sheet is already open when the trail is clicked.
+    sheet_open()
+    open_panel()
+    settle_map(page)
+    first = page.evaluate(SHEET_GEOMETRY, coords)
+    report['assertions'].append({'sheet_open_then_click': first})
+    assert first['zoom'] == pytest.approx(ZOOM, abs=0.01)
+    assert first['tilesLoaded'] is True
+    assert_panel_beside_with_sheet(first)
+
+    page.screenshot(path=str(EVIDENCE / f'{shot_name}.png'))
+    report['assertions'].append({'sheet_pixels': pixel_gate(
+        EVIDENCE / f'{shot_name}.png', report['crop']['top'])})
+
+    # Order B: the panel is open first, then the sheet shrinks the map under it.
+    page.keyboard.press('Escape')
+    page.wait_for_function("() => document.getElementById('trail-panel').hidden", timeout=5000)
+    page.click('#live-feed-btn')
+    page.wait_for_function(
+        "() => getComputedStyle(document.getElementById('live-packet-sheet'))"
+        ".display === 'none'", timeout=10000)
+    page.wait_for_function(BASEMAP_DRAWN, timeout=60000)
+    open_panel()
+    settle_map(page)
+    before = page.evaluate(SHEET_GEOMETRY, coords)
+    report['assertions'].append({'panel_then_sheet_before': before})
+
+    sheet_open()
+    second = page.evaluate(SHEET_GEOMETRY, coords)
+    report['assertions'].append({'panel_then_sheet_after': second})
+    assert_panel_beside_with_sheet(second)
+    report['assertions'].append({'sheet_gap': {
+        'right_edge_to_panel': round(second['panel']['left'] - second['trail']['maxX'], 2),
+        'panel_bottom_to_sheet_top': round(second['sheet']['top'] - second['panel']['bottom'], 2),
+        'map_height': round(second['canvas']['height'], 2),
+    }})
+    report['assertions'].append({'sheet_settle': settle_map(page)})
+    assert not report['pageerrors'], report['pageerrors']

```

```diff

```




### follow-up fix diff: a trail click opens its info panel every time (PR #423)

```diff
diff --git a/haminfo-dashboard/src/haminfo_dashboard/static/js/map-shared/trail-hit.js b/haminfo-dashboard/src/haminfo_dashboard/static/js/map-shared/trail-hit.js
new file mode 100644
index 00000000..0ec7a492
--- /dev/null
+++ b/haminfo-dashboard/src/haminfo_dashboard/static/js/map-shared/trail-hit.js
@@ -0,0 +1,24 @@
+/* map-shared: the one hit test for a trail (#422).
+ *
+ * Hover, click and the station popup's stand-down must agree on which trail is
+ * under the pointer. MapLibre's point query has no tolerance, which cannot hit a
+ * 3 px line, so every caller asks this module for the spec's 8 px box instead.
+ * The tolerance lives here so the three callers cannot drift apart.
+ */
+(function () {
+    'use strict';
+
+    var TOLERANCE_PX = 8;
+
+    function pick(map, point, tolerance) {
+        if (!map || !point || !map.getLayer || !map.getLayer('trails')) return null;
+        var t = tolerance === undefined ? TOLERANCE_PX : tolerance;
+        var hit = map.queryRenderedFeatures(
+            [[point.x - t, point.y - t], [point.x + t, point.y + t]],
+            {layers: ['trails']});
+        return (hit && hit[0]) || null;
+    }
+
+    window.MapShared = window.MapShared || {};
+    window.MapShared.trailHit = {pick: pick, TOLERANCE_PX: TOLERANCE_PX};
+})();
diff --git a/haminfo-dashboard/src/haminfo_dashboard/static/js/map-shared/trail-panel.js b/haminfo-dashboard/src/haminfo_dashboard/static/js/map-shared/trail-panel.js
index ae7b559e..73d35fd3 100644
--- a/haminfo-dashboard/src/haminfo_dashboard/static/js/map-shared/trail-panel.js
+++ b/haminfo-dashboard/src/haminfo_dashboard/static/js/map-shared/trail-panel.js
@@ -75,6 +75,21 @@
         ];
     }
 
+    // The panel's two non-data states (issue #422): a click paints "loading"
+    // before the numbers are computed, and a point set that cannot be used ends
+    // in a message instead of an empty panel. Plain strings so the states are
+    // unit-testable without a DOM, like buildRows.
+    var LOADING_HTML = '<p class="trail-panel-note trail-panel-loading" role="status">' +
+        'Loading trail details\u2026</p>';
+    var ERROR_HTML = '<p class="trail-panel-note trail-panel-error" role="alert">' +
+        'Trail details could not be loaded. Reload the map and try again.</p>';
+
+    function stateHtml(state) {
+        if (state === 'loading') return LOADING_HTML;
+        if (state === 'error') return ERROR_HTML;
+        return '';
+    }
+
     // Placement (issue #359 follow-up). The panel sits BESIDE the trail, one
     // gap away from its nearest edge, and flips to the trail's other side only
     // when that first side has no room — never pinned to the far map edge. It
@@ -176,17 +191,14 @@
 
         /**
          * Open on one station's trail.
-         * opts: {callsign, props, points, view: {units, windowHours,
+         * opts: {callsign, props, points, state, view: {units, windowHours,
          *        truncated, returnedPoints}}
-         * Returns the computed statistics.
+         * state is 'ready' (default), 'loading' or 'error'; only 'ready' reads
+         * points. Returns the computed statistics, or null for the other states.
          */
         function show(opts) {
+            var state = opts.state || 'ready';
             var view = opts.view || {};
-            var result = api.compute(opts.points || [], {
-                truncated: view.truncated,
-                returnedPoints: view.returnedPoints,
-                windowHours: view.windowHours,
-            });
             var el = ensureElement();
             var html = '<div class="trail-panel-header">' +
                 symbolHtml(opts.props) +
@@ -195,51 +207,68 @@
                 '<button type="button" class="trail-panel-close" ' +
                 'aria-label="Close trail panel">\u00D7</button>' +
                 '</div>';
+            var result = null;
 
-            if (!result.hasTrail) {
-                // Spec rule 14: no trail is no trail — callsign, last heard and
-                // nothing else.
-                html += '<dl class="trail-panel-rows">' +
-                    '<div class="trail-panel-row"><dt>Last heard</dt><dd>' +
-                    escapeHtml(api.formatClock(result.lastTime)) + '</dd></div>' +
-                    '</dl><p class="trail-panel-note">No trail in the window</p>';
+            if (state !== 'ready') {
+                html += stateHtml(state);
             } else {
-                html += '<dl class="trail-panel-rows">';
-                buildRows(result, api, view.units || 'metric', view.windowHours)
-                    .forEach(function (row) {
-                        html += '<div class="trail-panel-row"><dt>' +
-                            escapeHtml(row.label) + '</dt><dd>' +
-                            escapeHtml(row.value) + '</dd></div>';
-                    });
-                html += '</dl>';
-                if (result.badFixes) {
-                    html += '<p class="trail-panel-note">' + result.badFixes +
-                        (result.badFixes === 1 ? ' position' : ' positions') +
-                        ' excluded as a bad fix</p>';
-                }
-                if (result.truncated) {
-                    html += '<p class="trail-panel-note">trail truncated, last ' +
-                        result.returnedPoints + ' points</p>';
+                result = api.compute(opts.points || [], {
+                    truncated: view.truncated,
+                    returnedPoints: view.returnedPoints,
+                    windowHours: view.windowHours,
+                });
+                if (!result.hasTrail) {
+                    // Spec rule 14: no trail is no trail — callsign, last heard
+                    // and nothing else.
+                    html += '<dl class="trail-panel-rows">' +
+                        '<div class="trail-panel-row"><dt>Last heard</dt><dd>' +
+                        escapeHtml(api.formatClock(result.lastTime)) + '</dd></div>' +
+                        '</dl><p class="trail-panel-note">No trail in the window</p>';
+                } else {
+                    html += '<dl class="trail-panel-rows">';
+                    buildRows(result, api, view.units || 'metric', view.windowHours)
+                        .forEach(function (row) {
+                            html += '<div class="trail-panel-row"><dt>' +
+                                escapeHtml(row.label) + '</dt><dd>' +
+                                escapeHtml(row.value) + '</dd></div>';
+                        });
+                    html += '</dl>';
+                    if (result.badFixes) {
+                        html += '<p class="trail-panel-note">' + result.badFixes +
+                            (result.badFixes === 1 ? ' position' : ' positions') +
+                            ' excluded as a bad fix</p>';
+                    }
+                    if (result.truncated) {
+                        html += '<p class="trail-panel-note">trail truncated, last ' +
+                            result.returnedPoints + ' points</p>';
+                    }
                 }
             }
             el.innerHTML = html;
             el.querySelector('.trail-panel-close').addEventListener('click', close);
 
-            lastFocus = document.activeElement;
+            // Only the first show() takes focus: the loading panel is replaced
+            // by the data panel under the user's cursor, and that second call
+            // must not remember the panel itself as where focus came from.
+            if (!open) lastFocus = document.activeElement;
             open = true;
             el.hidden = false;
             el.focus({ preventScroll: true });
 
             // Escape works even if focus moved off the panel, and stops there so
             // it never also closes anything behind it (spec rules 5 and 7).
-            onKeydown = function (ev) {
-                if (ev.key === 'Escape') {
-                    ev.preventDefault();
-                    ev.stopPropagation();
-                    close();
-                }
-            };
-            document.addEventListener('keydown', onKeydown, true);
+            // Registered once per open, or a second show() would leave the old
+            // listener behind and grow one per re-render.
+            if (!onKeydown) {
+                onKeydown = function (ev) {
+                    if (ev.key === 'Escape') {
+                        ev.preventDefault();
+                        ev.stopPropagation();
+                        close();
+                    }
+                };
+                document.addEventListener('keydown', onKeydown, true);
+            }
             return result;
         }
 
@@ -279,6 +308,7 @@
     window.MapShared.trailPanel = {
         create: create,
         buildRows: buildRows,
+        stateHtml: stateHtml,
         windowLabel: windowLabel,
         computePlacement: computePlacement,
     };
diff --git a/haminfo-dashboard/src/haminfo_dashboard/templates/dashboard/map.html b/haminfo-dashboard/src/haminfo_dashboard/templates/dashboard/map.html
index 8dec3243..de04f653 100644
--- a/haminfo-dashboard/src/haminfo_dashboard/templates/dashboard/map.html
+++ b/haminfo-dashboard/src/haminfo_dashboard/templates/dashboard/map.html
@@ -46,6 +46,7 @@ if (window.haminfoMapNeedsLegacy()) {
 <script src="/static/js/map-shared/follow-station.js?v={{ asset_version }}"></script>
 <script src="/static/js/map-shared/trail-builder.js?v={{ asset_version }}"></script>
 <script src="/static/js/map-shared/trail-stats.js?v={{ asset_version }}"></script>
+<script src="/static/js/map-shared/trail-hit.js?v={{ asset_version }}"></script>
 <script src="/static/js/map-shared/trail-panel.js?v={{ asset_version }}"></script>
 <script src="/static/js/map-shared/heatmap.js?v={{ asset_version }}"></script>
 <script src="/static/js/satellite.min.js?v={{ asset_version }}"></script>
@@ -1591,13 +1592,10 @@ if (window.haminfoMapNeedsLegacy()) {
         if (stationsPopupWired) return;
         stationsPopupWired = true;
         map.on('click', function (e) {
-            // Issue #359: a click that landed on a trail is the trail panel's,
-            // even where a station marker sits under the same point. Same hit
-            // test the trail handler uses, so the two can never disagree.
-            if (map.getLayer('trails') &&
-                map.queryRenderedFeatures(e.point, { layers: ['trails'] }).length) {
-                return;
-            }
+            // The marker is the smaller target and owns its own click (issue
+            // #422: "only clicking the station" opens the station popup). A
+            // station usually sits at the end of its own trail, inside the 8 px
+            // trail band, so the trail handler below stands down for a marker.
             const hit = map.queryRenderedFeatures(e.point, {
                 layers: ['stations_icon', 'stations_icon_overlay'],
             });
@@ -1835,7 +1833,6 @@ if (window.haminfoMapNeedsLegacy()) {
     const TRAIL_HIGHLIGHT_COLOR = '#60a5fa';  // the map's existing accent
     const TRAIL_HOVER_WIDTH = TRAIL_WIDTH + 4;
     const TRAIL_SELECT_WIDTH = TRAIL_WIDTH + 5;
-    const TRAIL_HOVER_TOLERANCE_PX = 8;       // spec: hover box around the pointer
     const TRAIL_NO_CALLSIGN = '__none__';
     let trailHours = 1;             // trail window (legacy currentTrailHours)
     const TRAIL_ZOOM_MIN = 7;       // only load trails at/above this zoom
@@ -1953,9 +1950,9 @@ if (window.haminfoMapNeedsLegacy()) {
     }
 
     // Hover highlights the whole trail, click opens its info panel (issue #359).
-    // Hover is a mousemove + 8 px box query rather than mouseenter on the layer:
-    // a 3 px line is hard to hit with MapLibre's zero-tolerance point query, and
-    // the spec fixes the tolerance at 8 px.
+    // Both use one hit test, the spec's 8 px box around the pointer: MapLibre's
+    // zero-tolerance point query cannot hit a 3 px line, so a click that lit the
+    // trail up used to land on bare map and do nothing at all (issue #422).
     function wireTrailPopup() {
         if (trailPopupWired) return;
         trailPopupWired = true;
@@ -1968,13 +1965,7 @@ if (window.haminfoMapNeedsLegacy()) {
             hoverPending = true;
             requestAnimationFrame(function () {
                 hoverPending = false;
-                if (!lastPoint || !map.getLayer('trails')) return;
-                const t = TRAIL_HOVER_TOLERANCE_PX;
-                const box = [
-                    [lastPoint.x - t, lastPoint.y - t],
-                    [lastPoint.x + t, lastPoint.y + t],
-                ];
-                const hit = map.queryRenderedFeatures(box, { layers: ['trails'] })[0];
+                const hit = MapShared.trailHit.pick(map, lastPoint);
                 setTrailHover(hit && hit.properties ? hit.properties.callsign : null);
             });
         });
@@ -1982,13 +1973,17 @@ if (window.haminfoMapNeedsLegacy()) {
             setTrailHover(null);
         });
 
-        map.on('click', 'trails', function (e) {
-            e.preventDefault();
-            const feature = e.features && e.features[0];
-            if (!feature || !feature.properties) return;
+        map.on('click', function (e) {
+            const hit = MapShared.trailHit.pick(map, e.point);
+            if (!hit || !hit.properties) return;
+            // A station marker under the pointer wins: it is the smaller target
+            // and a station sits at the end of its own trail (issue #422).
+            if (map.queryRenderedFeatures(e.point, {
+                layers: ['stations_icon', 'stations_icon_overlay'],
+            }).length) return;
             // Issue #359 item 2: a trail click opens the panel and nothing
             // else. The station popup stays the marker's own click.
-            openTrailPanel(feature.properties.callsign);
+            openTrailPanel(hit.properties.callsign);
         });
         // The sheet's height transition lags the map resize, so place once at
         // the new canvas and once when the transition has settled (250 ms).
@@ -1996,14 +1991,10 @@ if (window.haminfoMapNeedsLegacy()) {
             placeTrailPanel();
             setTimeout(placeTrailPanel, 260);
         });
-        // A click on bare map closes the panel, as the spec requires; a click
-        // that landed on a trail is handled above and must not close it.
+        // A click on bare map closes the panel, as the spec requires; the same
+        // hit test decides, so a trail click cannot be read as a bare one.
         map.on('click', function (e) {
-            if (!map.getLayer('trails')) return;
-            const hit = map.queryRenderedFeatures(
-                [[e.point.x - 2, e.point.y - 2], [e.point.x + 2, e.point.y + 2]],
-                { layers: ['trails'] })[0];
-            if (!hit) closeTrailPanel();
+            if (!MapShared.trailHit.pick(map, e.point)) closeTrailPanel();
         });
     }
 
@@ -2109,24 +2100,53 @@ if (window.haminfoMapNeedsLegacy()) {
     }
 
     function openTrailPanel(callsign) {
-        const points = liveTrailBuffers[callsign] || [];
         const feature = stationFeatures[callsign];
-        const meta = (trailMeta && trailMeta[callsign]) || {};
         const panel = ensureTrailPanel();
         trailPanelCall = callsign;
         setTrailSelected(callsign);
+        // Paint before computing (issue #422): the click must show something on
+        // the spot, and a broken point set must end in a message, never in a
+        // panel that silently fails to open.
         panel.show({
             callsign: callsign,
             props: feature && feature.properties,
-            points: points,
-            view: {
-                units: wxUnits === 'metric' ? 'metric' : 'imperial',
-                windowHours: trailHours,
-                truncated: !!meta.truncated,
-                returnedPoints: meta.returned_points,
-            },
+            state: 'loading',
         });
         placeTrailPanel();
+        requestAnimationFrame(function () {
+            if (trailPanelCall !== callsign) return;   // closed or another trail
+            const points = liveTrailBuffers[callsign];
+            const meta = (trailMeta && trailMeta[callsign]) || {};
+            if (!points) {
+                panel.show({
+                    callsign: callsign,
+                    props: feature && feature.properties,
+                    state: 'error',
+                });
+            } else {
+                try {
+                    panel.show({
+                        callsign: callsign,
+                        props: feature && feature.properties,
+                        points: points,
+                        view: {
+                            units: wxUnits === 'metric' ? 'metric' : 'imperial',
+                            windowHours: trailHours,
+                            truncated: !!meta.truncated,
+                            returnedPoints: meta.returned_points,
+                        },
+                    });
+                } catch (err) {
+                    console.error('Trail panel failed:', err);
+                    panel.show({
+                        callsign: callsign,
+                        props: feature && feature.properties,
+                        state: 'error',
+                    });
+                }
+            }
+            placeTrailPanel();
+        });
     }
 
     function closeTrailPanel() {
diff --git a/haminfo-dashboard/tests/js/trail-hit.test.js b/haminfo-dashboard/tests/js/trail-hit.test.js
new file mode 100644
index 00000000..e1ee73e6
--- /dev/null
+++ b/haminfo-dashboard/tests/js/trail-hit.test.js
@@ -0,0 +1,66 @@
+// haminfo-dashboard/tests/js/trail-hit.test.js
+// Issue #422: hover lights a trail up with an 8 px box, but the click was a
+// MapLibre layer click with no tolerance, so a click near (not on) the 3 px line
+// did nothing. Every caller now asks trail-hit.js, and this file pins the box.
+const test = require('node:test');
+const assert = require('node:assert/strict');
+const fs = require('node:fs');
+const path = require('node:path');
+const vm = require('node:vm');
+
+const MODULE = path.join(
+    __dirname, '../../src/haminfo_dashboard/static/js/map-shared/trail-hit.js');
+
+function load() {
+    const sandbox = {window: {}};
+    vm.createContext(sandbox);
+    vm.runInContext(fs.readFileSync(MODULE, 'utf8'), sandbox, {filename: MODULE});
+    return sandbox.window.MapShared.trailHit;
+}
+
+// A fake map whose only trail is a horizontal 3 px line at y = 100. It answers
+// like MapLibre: a feature comes back only when the query box touches the line.
+function mapWithLine() {
+    const map = {
+        calls: [],
+        getLayer: id => (id === 'trails' ? {id: id} : null),
+        queryRenderedFeatures(box, opts) {
+            map.calls.push({box: box, opts: opts});
+            const touches = box[0][1] <= 101.5 && box[1][1] >= 98.5;
+            return touches ? [{properties: {callsign: 'K5ROX'}}] : [];
+        },
+    };
+    return map;
+}
+
+test('a click 6 px off the line finds the trail the hover box found', function () {
+    const map = mapWithLine();
+    const hit = load().pick(map, {x: 200, y: 94});
+    assert.equal(hit.properties.callsign, 'K5ROX');
+    // Array.from keeps the vm's arrays from crossing the realm boundary.
+    assert.deepEqual(Array.from(map.calls[0].box, p => Array.from(p)),
+        [[192, 86], [208, 102]]);
+    assert.equal(map.calls[0].opts.layers.length, 1);
+    assert.equal(map.calls[0].opts.layers[0], 'trails');
+});
+
+test('the box is the spec 8 px on every side of the pointer', function () {
+    const api = load();
+    assert.equal(api.TOLERANCE_PX, 8);
+    const map = mapWithLine();
+    api.pick(map, {x: 10, y: 20});
+    assert.deepEqual(Array.from(map.calls[0].box, p => Array.from(p)),
+        [[2, 12], [18, 28]]);
+});
+
+test('9 px away is bare map, so the panel can still be closed', function () {
+    const map = mapWithLine();
+    assert.equal(load().pick(map, {x: 200, y: 112}), null);
+});
+
+test('no trail layer is no hit and no throw', function () {
+    const api = load();
+    assert.equal(api.pick({getLayer: () => null}, {x: 1, y: 1}), null);
+    assert.equal(api.pick(null, {x: 1, y: 1}), null);
+    assert.equal(api.pick(mapWithLine(), null), null);
+});
diff --git a/haminfo-dashboard/tests/js/trail-panel.test.js b/haminfo-dashboard/tests/js/trail-panel.test.js
index 7dea0dfa..037111a2 100644
--- a/haminfo-dashboard/tests/js/trail-panel.test.js
+++ b/haminfo-dashboard/tests/js/trail-panel.test.js
@@ -97,6 +97,18 @@ test('window labels cover minutes, hours and days', function () {
     assert.equal(panel.windowLabel(null), '-');
 });
 
+test('the panel has a loading state and a clear failure message', function () {
+    // Issue #422: a click must show something before the numbers arrive, and a
+    // point set that cannot be used must say so rather than stay blank.
+    const {panel} = load();
+    assert.match(panel.stateHtml('loading'), /Loading trail details/);
+    assert.match(panel.stateHtml('loading'), /role="status"/);
+    assert.match(panel.stateHtml('error'), /could not be loaded/);
+    assert.match(panel.stateHtml('error'), /Reload the map and try again/);
+    assert.match(panel.stateHtml('error'), /role="alert"/);
+    assert.equal(panel.stateHtml('ready'), '');
+});
+
 // Placement (issue #359 follow-up): Hemna asked for the popup near the trail,
 // so the panel is anchored to the trail's own edges, never to a far map edge.
 // The rules are pure geometry here; the browser test checks the same rules
diff --git a/haminfo-dashboard/tests/test_map_trail_panel.py b/haminfo-dashboard/tests/test_map_trail_panel.py
index e904cbd4..ecbcfc8b 100644
--- a/haminfo-dashboard/tests/test_map_trail_panel.py
+++ b/haminfo-dashboard/tests/test_map_trail_panel.py
@@ -33,6 +33,7 @@ class TestTemplateWiring:
         [
             # Both modules load before the page script that uses them.
             '<script src="/static/js/map-shared/trail-stats.js',
+            '<script src="/static/js/map-shared/trail-hit.js',
             '<script src="/static/js/map-shared/trail-panel.js',
             # Highlight layers ride on the one trail source, filtered to a
             # callsign, so they can never drift from the trail they describe.
@@ -40,16 +41,17 @@ class TestTemplateWiring:
             "id: 'trails_selected',",
             "source: TRAILS_SOURCE,",
             "filter: ['==', ['get', 'callsign'], TRAIL_NO_CALLSIGN],",
-            # Spec tolerance: an 8 px box around the pointer.
-            'const TRAIL_HOVER_TOLERANCE_PX = 8;',
+            # Spec tolerance: an 8 px box around the pointer, one hit test for
+            # hover and click (issue #422).
+            'MapShared.trailHit.pick(map, lastPoint)',
             'MapShared.trailStats',
             'MapShared.trailPanel.create',
             # Click a trail -> its panel; a click on bare map closes it.
-            'openTrailPanel(feature.properties.callsign);',
+            'openTrailPanel(hit.properties.callsign);',
             'closeTrailPanel();',
             # Statistics come from every stored position, never the drawn
             # (segment-split) geometry.
-            'const points = liveTrailBuffers[callsign] || [];',
+            'const points = liveTrailBuffers[callsign];',
             # Units follow the map's own distance preference.
             "units: wxUnits === 'metric' ? 'metric' : 'imperial',",
             # A theme setStyle rebuilds the layers, so the selection is re-lit.
@@ -112,6 +114,13 @@ class TestModules:
         [
             'window.MapShared.trailPanel = {',
             'function buildRows(result, api, units, windowHours) {',
+            'function stateHtml(state) {',
+            # Issue #422: a click paints before the numbers are computed, and a
+            # point set that cannot be used says so instead of staying blank.
+            'Loading trail details',
+            'Trail details could not be loaded. Reload the map and try again.',
+            'role="status"',
+            'role="alert"',
             "element.setAttribute('role', 'dialog');",
             # Escape closes the panel and stops there, so it never also closes
             # the map's own surface behind it.
@@ -145,25 +154,34 @@ class TestATrailClickOpensOnlyThePanel:
         source = MAP_TEMPLATE.read_text()
         block = self._block(
             source,
-            "map.on('click', 'trails', function (e) {",
-            '\n        });',
+            'function wireTrailPopup() {',
+            'function trailFilter(',
         )
-        assert 'openTrailPanel(feature.properties.callsign);' in block
+        assert 'openTrailPanel(hit.properties.callsign);' in block
         assert 'openStationPopup' not in block
 
-    def test_station_popup_stands_down_for_a_click_on_a_trail(self):
+    def test_a_station_marker_owns_its_own_click(self):
         source = MAP_TEMPLATE.read_text()
         block = self._block(
             source,
             'function wireStationPopup() {',
             '// Coalesce source rewrites',
         )
-        # The trail guard runs before the station hit test, so a marker drawn
-        # over its own trail cannot steal the trail click.
-        assert "layers: ['trails']" in block
-        assert block.index("layers: ['trails']") < block.index("stations_icon")
+        # A station usually sits at the end of its own trail, inside the trail's
+        # 8 px band, so the popup must not defer to the trail (issue #422: only
+        # clicking the station opens the station popup).
+        assert 'MapShared.trailHit.pick' not in block
         assert 'openStationPopup(feature.geometry.coordinates' in block
 
+    def test_the_trail_panel_stands_down_for_a_station_marker(self):
+        source = MAP_TEMPLATE.read_text()
+        block = self._block(
+            source, 'function wireTrailPopup() {', 'function trailFilter(')
+        # Same reason, from the trail's side: a marker under the pointer keeps
+        # the trail panel closed.
+        assert block.index("layers: ['stations_icon', 'stations_icon_overlay']") < (
+            block.index('openTrailPanel(hit.properties.callsign);'))
+
     def test_panel_re_places_when_live_packets_shrinks_the_map(self):
         source = MAP_TEMPLATE.read_text()
         assert 'function placeTrailPanel() {' in source
@@ -178,3 +196,47 @@ class TestATrailClickOpensOnlyThePanel:
         assert 'placeTrailPanel();' in block
         # The height transition lags the resize by up to 250 ms.
         assert 'setTimeout(placeTrailPanel, 260)' in block
+
+
+class TestAClickAlwaysFindsTheTrail:
+    """Issue #422: hover lit the trail up with an 8 px box while the click used
+    MapLibre's zero-tolerance layer click, so a click that far from the 3 px line
+    fell through to bare map and the panel never opened.
+
+    The hit test itself is pinned in tests/js/trail-hit.test.js and exercised on a
+    real map in tests/test_trail_panel_browser.py; these assertions only pin that
+    the page has one hit test and uses it everywhere.
+    """
+
+    @staticmethod
+    def _block(source, start, end):
+        first = source.index(start)
+        return source[first:source.index(end, first)]
+
+    def test_hover_and_click_share_one_hit_test(self):
+        source = MAP_TEMPLATE.read_text()
+        block = self._block(
+            source, 'function wireTrailPopup() {', 'function trailFilter(')
+        # No delegated layer click: it has no tolerance and is the bug.
+        assert "map.on('click', 'trails'" not in source
+        assert 'MapShared.trailHit.pick(map, lastPoint)' in block
+        assert 'MapShared.trailHit.pick(map, e.point)' in block
+        # The trail click is what opens the panel, and it owns that decision.
+        assert block.index('MapShared.trailHit.pick(map, e.point)') < block.index(
+            'openTrailPanel(hit.properties.callsign);')
+
+    def test_bare_map_close_uses_the_same_hit_test(self):
+        source = MAP_TEMPLATE.read_text()
+        block = self._block(
+            source, 'function wireTrailPopup() {', 'function trailFilter(')
+        assert 'if (!MapShared.trailHit.pick(map, e.point)) closeTrailPanel();' in block
+
+    def test_a_click_paints_before_the_numbers_are_computed(self):
+        source = MAP_TEMPLATE.read_text()
+        block = self._block(
+            source, 'function openTrailPanel(callsign) {', 'function closeTrailPanel()')
+        assert "state: 'loading'" in block
+        assert 'requestAnimationFrame' in block
+        # Data that cannot be used ends in a message, never a blank panel.
+        assert "state: 'error'" in block
+        assert '<script src="/static/js/map-shared/trail-hit.js' in source
diff --git a/haminfo-dashboard/tests/test_trail_panel_browser.py b/haminfo-dashboard/tests/test_trail_panel_browser.py
index fb57178e..1090b7a8 100644
--- a/haminfo-dashboard/tests/test_trail_panel_browser.py
+++ b/haminfo-dashboard/tests/test_trail_panel_browser.py
@@ -127,6 +127,23 @@ CANVAS_POINT = """(coord) => {
     const box = document.querySelector('.maplibregl-canvas').getBoundingClientRect();
     return {x: box.left + p.x, y: box.top + p.y};
 }"""
+# Issue #422: a click 6 px off the trail — inside the 8 px hover box, outside a
+# zero-tolerance hit. Midpoint of the longest segment, offset perpendicular.
+CANVAS_OFF_TRAIL = """(coords) => {
+    const map = window.__trailTestMap;
+    let best = null;
+    for (let i = 1; i < coords.length; i++) {
+        const a = map.project(coords[i - 1]);
+        const b = map.project(coords[i]);
+        const len = Math.hypot(b.x - a.x, b.y - a.y);
+        if (!best || len > best.len) best = {a: a, b: b, len: len};
+    }
+    const dx = best.b.x - best.a.x, dy = best.b.y - best.a.y, len = best.len || 1;
+    const mx = (best.a.x + best.b.x) / 2, my = (best.a.y + best.b.y) / 2;
+    const box = document.querySelector('.maplibregl-canvas').getBoundingClientRect();
+    return {x: box.left + mx - dy / len * 6, y: box.top + my + dx / len * 6,
+            segment_px: len};
+}"""
 PANEL_STATE = """() => {
     const panel = document.getElementById('trail-panel');
     return {
@@ -461,6 +478,58 @@ def test_hover_highlights_click_opens_panel(map_page):
     assert not report['pageerrors'], report['pageerrors']
 
 
+@pytest.mark.parametrize('map_page', [
+    {'units': 'metric', 'theme': 'light', 'tag': 'offtrail'},
+], indirect=True, ids=['metric-light'])
+def test_a_click_just_off_the_line_opens_the_panel(map_page):
+    """Issue #422: hover lit the trail up with an 8 px box, but the click had no
+    tolerance, so clicking a highlighted trail did nothing at all."""
+    page, report, name = map_page
+    shot_name = f'trail-panel-{report["theme"]}-offtrail'
+    point = page.evaluate(CANVAS_OFF_TRAIL, [[p[0], p[1]] for p in TRAIL])
+    report['assertions'].append({'click_point': point})
+    assert point['segment_px'] > 5, point
+
+    # The hover box finds the trail from here: this is the state the user was in
+    # when the click was swallowed.
+    page.mouse.move(point['x'], point['y'])
+    page.wait_for_function(
+        "() => JSON.stringify(window.__trailTestMap.getFilter('trails_hover'))"
+        f".includes({json.dumps(CALLSIGN)})", timeout=10000)
+    report['assertions'].append({'offtrail_hover': page.evaluate(PANEL_STATE)['hover']})
+
+    page.mouse.click(point['x'], point['y'])
+    page.wait_for_function(
+        "() => {const p = document.getElementById('trail-panel');"
+        " return p && !p.hidden && p.innerText.includes('Points');}", timeout=10000)
+    state = page.evaluate(PANEL_STATE)
+    report['assertions'].append({'offtrail_panel_text': state['text']})
+    assert CALLSIGN in state['text']
+    assert 'Points' in state['text']
+
+    # A trail click is the panel's; the station popup stays the marker's.
+    popups = page.evaluate(
+        "() => [...document.querySelectorAll('.maplibregl-popup')]"
+        ".filter(el => el.offsetParent !== null).length")
+    assert popups == 0, 'a trail click must not open the station popup'
+
+    settle_map(page)
+    page.screenshot(path=str(EVIDENCE / f'{shot_name}.png'))
+    report['assertions'].append({'offtrail_pixels': pixel_gate(
+        EVIDENCE / f'{shot_name}.png', report['crop']['top'])})
+
+    # The station marker sits at the end of its own trail, inside the same 8 px
+    # band. Its own click must still open the station popup (#422).
+    marker = page.evaluate(CANVAS_POINT, TRAIL[-1][:2])
+    page.mouse.click(marker['x'], marker['y'])
+    page.wait_for_function(
+        "() => [...document.querySelectorAll('.maplibregl-popup')]"
+        ".some(el => el.offsetParent !== null)", timeout=10000)
+    report['assertions'].append({'marker_popup': page.evaluate(
+        "() => document.querySelector('.maplibregl-popup').innerText.slice(0, 80)")})
+    assert not report['pageerrors'], report['pageerrors']
+
+
 @pytest.mark.parametrize('map_page', [
     {'units': 'metric', 'theme': 'light', 'tag': 'flip'},
 ], indirect=True, ids=['metric-light'])

```
