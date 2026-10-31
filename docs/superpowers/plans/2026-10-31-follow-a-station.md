# Plan: follow a station (#206) — direwolf-dashboard

Companion to the HELD enhancements. Mirrors haminfo issue #350 / PR #401
(merge `26908832`, plus follow-up `2bf716bd` for dimming across a theme flip).

- Issue: http://git.hemna.com/hemna/direwolf-dashboard/issues/206
- Contract: `docs/FOLLOW-STATION.md` in `hemna/basemap-kit` @ master
  (kit PR #67, spec issue #64) — sections N1–N4 and N6–N14 apply to a web
  client; N5 (mobile camera lock) is out of scope.
- Reference: `~/devel/mine/hamradio/haminfo` @ `26908832`
  - `…/static/js/map-shared/follow-station.js` (436 lines, engine-agnostic):
    `normalizeCallsign`, `distanceKm`, `parsePath`, `viewportWidthKm`,
    `formatClock`, `fallbackIdentity`, `createFollower` with
    `mode: 'dim'|'hide'` (`silentSeconds: 60`), `follow`/`unfollow`/
    `suspend`/`resume`/`cameraMove`/`tooSoon`/`farThreshold`/`setPosition`/
    `flushPending`/`mergePath`/`addRow`/`onPacket`/`farDue`/`onSilence`/
    `chipText`/`setMode`/`setReduceMotion`
  - `…/templates/dashboard/map.html` (UI wiring), `popup-builder.js` touched
  - tests: `…/tests/test_map_follow_station.py`,
    `…/tests/test_map_follow_browser.py`, `…/tests/js/*.test.js`
  - `2bf716bd` `…/tests/test_map_follow_station.py` — dim state survives a
    light/dark theme flip

## Measured current state (this repo, master `3e98b2f`)

- Map sources/layers in `static/app.js`: sources `stations`, `station-overlay`,
  `station-tracks`, `tx-arcs`, `gpx`, `decode-path`; layers
  `stations-symbol-base`, `stations-symbol-overlay`, `stations-label`,
  `stations-my-glow`, `station-tracks-line`, `station-overlay-hops`,
  `station-overlay-distance`, `tx-arcs-line`, `gpx-route`, `gpx-track`,
  `gpx-points`, `decode-path-line`.
- No `follow-station` code: grep `followStation`/`follow-station` in `app.js` →
  0 hits.
- Data available: `GET /api/stations`, `/api/station_positions`,
  `/api/station_tracks?hours=`, `/api/station/<cs>`; live WS `/ws`;
  log panel + `format_compact_log` (`processor.py:94`).
- Client tests: source-assert (`tests/test_maplibre_*.py`) + node harness
  (`tests/test_app_settings_save.py` pattern).

## Port decisions

1. **One follow section inside `app.js`** (this app is a single IIFE; that is
   the surrounding convention), porting the reference state machine
   one-for-one: same names, same `dim`/`hide` modes, same `silentSeconds: 60`,
   same `farThreshold(view)` / `tooSoon(now)` gating, same `chipText`.
2. **Camera**: `maplibregl.Map` `flyTo`/`easeTo` on the followed station's
   position; far travel per N4 (web client) uses the reference's viewport-width
   rule, not a fixed zoom jump.
3. **Dimming applies to layer paint, not the theme object**, so a light/dark
   flip (`toggleTheme` → `applyThemeStyle` → `repaintAppLayers` in `app.js:162-243`)
   keeps the follow state — the exact bug `2bf716bd` fixed in haminfo.
4. **Per-station packets**: chip (callsign + age + last heard clock) plus a
   live-log filter that shows only that station's packets; `escape`/click-away
   unfollows.
5. **Reduced motion**: same rule as #205 — camera moves are suppressed at
   init when the OS setting is on; explicit user control still works.

## Tasks (TDD, one commit each)

### T1 — Follow state machine (node harness)
- **RED:** `tests/test_map_follow_station.py`, node-harness style: extract the
  follow functions from `app.js`, stub `mapFlyTo`/`map`/`fetch`, then assert the
  reference behaviour: `follow(cs)` locks and dims, a far packet triggers
  `farDue` + camera move, `tooSoon` throttles repeat moves, `onSilence(60 s)`
  shows the silence chip, `unfollow` restores full opacity, `suspend`/`resume`
  stop and restart camera moves, `normalizeCallsign` handles the `>…` suffix and
  case, `fallbackIdentity` keys packets without a `packet_id`.
- **GREEN:** the follow section in `app.js`.
- Commit: `feat(map): follow one station with camera lock and dim mode (#206)`

### T2 — UI: chip, controls, live-log filter
- **RED:** source-asserts in the same file: a follow control exists in the
  existing controls rail, the chip element renders, the log filter hooks the
  existing log-row renderer, Escape unfollows.
- **GREEN:** UI wiring + `dw-follow-callsign` persistence like the other
  `dw-*` keys.
- Commit: `feat(map): follow chip, controls and per-station live log (#206)`

### T3 — Theme-flip dimming guard
- **RED:** node harness: `applyTheme('light')` (or `toggleTheme`) while
  following leaves the non-followed layers dimmed, not reset; the theme flip
  re-applies its own paint on top.
- **GREEN:** repaint path reads the follow state when composing paint.
- Commit: `fix(map): keep follow dimming across a theme flip (#206)`

## Out of scope

N5 mobile camera lock; no new endpoints (existing `/api/station*` are enough);
no change to popup builder beyond what the follow chip reuses.

## Verification gate

`uvx ruff check . --select=E,F --ignore=E501` · `uv run pytest tests/ -q` ·
`node --check` every touched `.js`.

## Appendix A — haminfo reference kit (verbatim, inlined)

Merge `26908832` (PR #401) plus theme-flip fix `2bf716bd` (PR #418).

### follow-station.js (complete — state machine to port one-for-one)

```js
/* map-shared: follow one station — camera lock, packet log and chip rules.
 * Engine-agnostic: no map and no DOM. Exposed as window.MapShared.followStation.
 *
 * The page (map.html) owns the camera and the markup; this module holds every
 * decision so it can be driven from a test without a browser:
 *   - when the camera may move (dead zone, minimum interval, manual stop)
 *   - when a packet is far enough to deserve the wide view, and how a second
 *     far packet replaces the first instead of fighting it
 *   - which packets enter the log and how two receptions of one packet share
 *     a row (identity comes from the server, basemap-kit #63 N4.1)
 *   - what the chip says while the station has no position or has gone silent
 *
 * The shared spec is basemap-kit issue #64; the normative document it points
 * at (docs/FOLLOW-STATION.md) is not merged yet, so the numbers below are the
 * decisions this page makes and the PR states them.
 */
(function () {
    'use strict';

    var DEFAULTS = {
        deadZoneRatio: 0.2,       // share of the viewport kept still around centre
        minMoveIntervalMs: 1200,  // no camera move closer together than this
        easeMs: 600,
        farKm: 50,                // a packet this far from the station is far
        farViewportRatio: 0.35,   // ...or this share of the viewport width
        farHoldMs: 2500,          // how long the wide view is held
        farReturnMs: 700,
        silentSeconds: 60,        // matches the feed's CALLSIGN_SILENCE_SECONDS
        logLimit: 50
    };

    // Same callsign form the feed accepts (haminfo #348), so the page does not
    // subscribe to something the server will reject.
    var CALLSIGN_RE = /^[A-Z0-9]{1,6}(?:-[0-9]{1,2})?$/;

    function normalizeCallsign(value) {
        if (typeof value !== 'string') return null;
        var callsign = value.trim().toUpperCase();
        if (!CALLSIGN_RE.test(callsign)) return null;
        if (callsign.indexOf('-') !== -1 && parseInt(callsign.split('-')[1], 10) > 15) {
            return null;
        }
        return callsign;
    }

    function distanceKm(a, b) {
        if (!a || !b) return 0;
        var R = 6371;
        var dLat = (b.lat - a.lat) * Math.PI / 180;
        var dLon = (b.lng - a.lng) * Math.PI / 180;
        var s = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
            Math.cos(a.lat * Math.PI / 180) * Math.cos(b.lat * Math.PI / 180) *
            Math.sin(dLon / 2) * Math.sin(dLon / 2);
        return R * 2 * Math.atan2(Math.sqrt(s), Math.sqrt(1 - s));
    }

    // Split an APRS path into the hops that were actually used. #345 removed
    // state-manager's parser, so this module keeps its own copy of the rule
    // the log's "via" column and hop count depend on.
    var GENERIC_PATH_ALIASES = {
        WIDE: 1, WIDE1: 1, WIDE2: 1, WIDE3: 1, 'WIDE1-1': 1, 'WIDE2-1': 1,
        'WIDE2-2': 1, 'WIDE3-3': 1, RELAY: 1, TRACE: 1, TCPIP: 1, CQ: 1,
        QST: 1, APRS: 1, RFONLY: 1, NOGATE: 1
    };
    function parsePath(pathString) {
        var result = { digipeaters: [], igate: null, isTcpip: false };
        if (!pathString) return result;
        var parts = pathString.split(',').map(function (p) { return p.trim(); });
        parts.forEach(function (part) {
            if (part === 'TCPIP' || part === 'TCPIP*' || part === 'qAC' || part === 'qAU') {
                result.isTcpip = true;
            }
        });
        for (var i = 0; i < parts.length; i += 1) {
            var part = parts[i];
            if (part.indexOf('qA') === 0 && i + 1 < parts.length) {
                result.igate = parts[i + 1];
                break;   // the igate is always the last meaningful element
            }
            if (part.slice(-1) === '*') {
                var callsign = part.slice(0, -1);
                var base = callsign.replace(/-[0-9]+$/, '');
                if (!GENERIC_PATH_ALIASES[callsign] && !GENERIC_PATH_ALIASES[base]) {
                    result.digipeaters.push(callsign);
                }
            }
        }
        return result;
    }
    // How many kilometres the viewport spans. MapLibre v5 dropped
    // Map#distance(), and a width of 0 made the dead zone and the far
    // threshold unreachable, so the page passes its own projection in.
    function viewportWidthKm(unproject, width, height) {
        if (typeof unproject !== 'function' || !(width > 0)) return 0;
        var midRow = (height || 0) / 2;
        var west;
        var east;
        try {
            west = unproject([0, midRow]);
            east = unproject([width, midRow]);
        } catch (e) {
            return 0;
        }
        if (!west || !east) return 0;
        return distanceKm(
            { lat: west.lat, lng: west.lng },
            { lat: east.lat, lng: east.lng }
        );
    }
    function formatClock(iso, now) {
        var d = iso ? new Date(iso) : new Date(now || Date.now());
        if (isNaN(d.getTime())) return '--:--:--';
        var pad = function (n) { return (n < 10 ? '0' : '') + n; };
        return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
    }

    // Fallback identity when a payload arrives without the server's packet_id:
    // the same rule as callsign_feed.packet_identity (frame with the path out).
    function fallbackIdentity(packet) {
        var raw = (packet.raw || '').trim();
        var header = raw.split(',')[0] || '';
        var source = (packet.from_call || '').toUpperCase();
        var destination = (packet.addressee || packet.to_call || '').toUpperCase();
        if (raw.indexOf(':') !== -1) {
            var info = raw.slice(raw.indexOf(':') + 1);
            return source + '>' + destination + ':' + info;
        }
        return source + '>' + destination + ':' + (packet.comment || '');
    }

    function createFollower(options) {
        options = options || {};
        var cfg = {};
        Object.keys(DEFAULTS).forEach(function (key) {
            cfg[key] = options.config && options.config[key] !== undefined
                ? options.config[key] : DEFAULTS[key];
        });
        var readPath = options.parsePath || parsePath;
        var reduceMotion = !!options.reduceMotion;

        var state = {
            callsign: null,
            following: false,
            suspended: false,
            mode: options.mode === 'hide' ? 'hide' : 'dim',
            position: null,
            lastMoveAt: 0,
            pending: false,
            lastHeardAt: null,
            silentSeconds: null,
            far: null,          // { points, until }
            seen: {},           // packet identity -> log row
            log: []
        };

        function motion() {
            return reduceMotion ? 'jump' : 'ease';
        }

        function resetLog() {
            state.seen = {};
            state.log = [];
        }

        function follow(callsign) {
            var normalized = normalizeCallsign(callsign);
            if (!normalized) return { type: 'error', reason: 'invalid_callsign' };
            if (state.callsign !== normalized) resetLog();
            state.callsign = normalized;
            state.following = true;
            state.suspended = false;
            state.pending = false;
            state.silentSeconds = null;
            state.far = null;
            state.lastHeardAt = null;
            // Every per-station field resets: following B after A must not
            // keep A's coordinates, silence clock or move clock.
            state.position = null;
            state.lastMoveAt = 0;
            return { type: 'following', callsign: normalized };
        }

        function unfollow() {
            var was = state.callsign;
            state.following = false;
            state.suspended = false;
            state.pending = false;
            state.far = null;
            state.callsign = null;
            state.position = null;
            state.lastHeardAt = null;
            state.silentSeconds = null;
            return { type: 'stopped', callsign: was };
        }

        // A hand pan or zoom: following pauses and the page shows resume.
        function suspend() {
            if (!state.following || state.suspended) return { type: 'unchanged' };
            state.suspended = true;
            state.pending = false;
            state.far = null;
            return { type: 'suspended', callsign: state.callsign };
        }

        function resume(now) {
            if (!state.following) return { type: 'unchanged' };
            state.suspended = false;
            state.lastMoveAt = 0;
            return {
                type: 'resumed',
                callsign: state.callsign,
                // Resume returns to the station at once, as #350 asks.
                move: state.position ? { to: state.position, zoom: null, motion: motion() } : null
            };
        }

        function cameraMove(pos, view, now, reason) {
            state.lastMoveAt = now;
            state.pending = false;
            return {
                type: 'move',
                to: pos,
                zoom: view && view.zoom !== undefined ? view.zoom : null,
                motion: motion(),
                reason: reason
            };
        }

        function tooSoon(now) {
            return now - state.lastMoveAt < cfg.minMoveIntervalMs;
        }

        function farThreshold(view) {
            var width = (view && view.viewportKmWidth) || 0;
            return Math.max(cfg.farKm, width * cfg.farViewportRatio);
        }

        // A position report for the followed station.
        function setPosition(pos, now, view) {
            if (!pos || typeof pos.lat !== 'number' || typeof pos.lng !== 'number') {
                return { type: 'none' };
            }
            var previous = state.position;
            state.position = { lat: pos.lat, lng: pos.lng };
            state.lastHeardAt = now;
            state.silentSeconds = null;
            if (!state.following || state.suspended) return { type: 'none' };
            if (previous) {
                var jump = distanceKm(previous, state.position);
                if (jump > farThreshold(view)) {
                    state.lastHeardAt = now;
                    return {
                        type: 'far',
                        points: [previous, state.position],
                        holdMs: cfg.farHoldMs,
                        motion: motion()
                    };
                }
            }
            if (!view || !view.center) return cameraMove(state.position, view, now, 'first-position');
            if (distanceKm(view.center, state.position) * 2 <=
                (view.viewportKmWidth || 0) * cfg.deadZoneRatio) {
                return { type: 'none', reason: 'dead-zone' };
            }
            if (tooSoon(now)) {
                state.pending = true;
                return { type: 'pending', retryInMs: cfg.minMoveIntervalMs - (now - state.lastMoveAt) };
            }
            return cameraMove(state.position, view, now, 'left-dead-zone');
        }

        // A deferred move that the page asks for once the floor has passed.
        function flushPending(now, view) {
            if (!state.pending || !state.following || state.suspended) return { type: 'none' };
            if (tooSoon(now)) return { type: 'none' };
            if (!view || !view.center) return { type: 'none' };
            return cameraMove(state.position, view, now, 'deferred');
        }

        function mergePath(row, packet) {
            if (!packet.path) return;
            if (!row.paths) row.paths = [row.path];
            if (row.paths.indexOf(packet.path) === -1) {
                row.paths.push(packet.path);
                row.path = row.paths.join(' / ');
                row.path_count = row.paths.length;
            }
        }

        function addRow(packet) {
            var path = packet.path || '';
            var parsed = readPath(path);
            var row = {
                id: packet.packet_id || fallbackIdentity(packet),
                time: formatClock(packet.received_at),
                received_at: packet.received_at || null,
                direction: packet.direction === 'to' ? 'to' : 'from',
                callsign: state.callsign,
                other: packet.direction === 'to'
                    ? (packet.from_call || '')
                    : (packet.addressee || packet.to_call || ''),
                packet_type: packet.packet_type || 'unknown',
                path: path,
                hop_count: (parsed.digipeaters || []).length,
                via: (parsed.digipeaters || []).join('>') || (parsed.isTcpip ? 'APRS-IS' : 'direct'),
                comment: packet.comment || packet.human_info || ''
            };
            state.log.unshift(row);
            if (state.log.length > cfg.logLimit) {
                var dropped = state.log.splice(cfg.logLimit);
                dropped.forEach(function (old) { delete state.seen[old.id]; });
            }
            state.seen[row.id] = row;
            return row;
        }

        // A packet the feed says concerns the followed station, in one or both
        // directions. Duplicate receptions share a row (identity, not count).
        function onPacket(packet, now, view) {
            if (!state.following || !packet) return { type: 'none' };
            var identity = packet.packet_id || fallbackIdentity(packet);
            var existing = state.seen[identity];
            if (existing) {
                mergePath(existing, packet);
                return { type: 'duplicate', row: existing };
            }
            var row = addRow(packet);
            state.lastHeardAt = now;
            state.silentSeconds = null;
            var result = { type: 'packet', row: row, duplicate: false };
            var lat = packet.latitude;
            var lng = packet.longitude;
            if (state.position && typeof lat === 'number' && typeof lng === 'number') {
                var target = { lat: lat, lng: lng };
                if (distanceKm(state.position, target) > farThreshold(view)) {
                    // A far packet replaces any wide view still on screen, so
                    // two in a row widen once instead of fighting (acceptance 3).
                    state.far = { points: [state.position, target], until: now + cfg.farHoldMs };
                    result.far = {
                        points: state.far.points,
                        holdMs: cfg.farHoldMs,
                        returnMs: cfg.farReturnMs,
                        motion: motion()
                    };
                }
            }
            return result;
        }

        function farDue(now) {
            if (!state.far) return null;
            if (now < state.far.until) return null;
            var points = state.far.points;
            state.far = null;
            return { type: 'far-return', points: points, motion: motion() };
        }

        function onSilence(seconds) {
            state.silentSeconds = typeof seconds === 'number' ? seconds : cfg.silentSeconds;
            return { type: 'silent', text: chipText().label, silent: true };
        }

        function chipText(now) {
            if (!state.following) return { text: '', label: '', silent: false };
            var name = state.callsign;
            if (!state.position) {
                return { text: 'Following ' + name, label: 'Following ' + name + ', no position yet', silent: false };
            }
            if (state.silentSeconds !== null) {
                var mins = Math.max(1, Math.round(state.silentSeconds / 60));
                // The dot alone would be colour-only feedback, so the chip text
                // says it too (WCAG 1.4.1) and the label spells out the minutes.
                return {
                    text: 'Following ' + name + ' · silent ' + mins + 'm',
                    label: 'Following ' + name + ', nothing heard for ' + mins + ' minute' +
                        (mins === 1 ? '' : 's'),
                    silent: true
                };
            }
            if (state.suspended) {
                return { text: 'Following ' + name + ' — paused', label: 'Following ' + name + ', paused', silent: false };
            }
            return { text: 'Following ' + name, label: 'Following ' + name, silent: false };
        }

        function setMode(mode) {
            state.mode = mode === 'hide' ? 'hide' : 'dim';
            return state.mode;
        }

        function setReduceMotion(value) {
            reduceMotion = !!value;
            return reduceMotion;
        }

        return {
            config: cfg,
            follow: follow,
            unfollow: unfollow,
            suspend: suspend,
            resume: resume,
            setPosition: setPosition,
            flushPending: flushPending,
            onPacket: onPacket,
            farDue: farDue,
            onSilence: onSilence,
            chipText: chipText,
            log: function () { return state.log.slice(); },
            setMode: setMode,
            setReduceMotion: setReduceMotion,
            state: function () {
                return {
                    callsign: state.callsign,
                    following: state.following,
                    suspended: state.suspended,
                    mode: state.mode,
                    hasPosition: !!state.position,
                    position: state.position,
                    silent: state.silentSeconds !== null
                };
            }
        };
    }

    window.MapShared = window.MapShared || {};
    window.MapShared.followStation = {
        DEFAULTS: DEFAULTS,
        create: createFollower,
        normalizeCallsign: normalizeCallsign,
        distanceKm: distanceKm,
        viewportWidthKm: viewportWidthKm,
        parsePath: parsePath,
        formatClock: formatClock,
        fallbackIdentity: fallbackIdentity
    };
})();

```

### theme-flip fix diff (T3 analogue)

```diff
diff --git a/CHANGELOG.md b/CHANGELOG.md
index 706e837e..67274200 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -8,6 +8,11 @@
 
 All notable changes to the haminfo project are documented here.
 
+## 2026-10-30
+
+### Fixed
+- **Following a station survives a theme flip (issue #417)**: `map.setStyle` rebuilds the station layers and resets their opacity, and the theme-reload handler re-applied every other overlay state but not the follow dimming, so flipping between light and dark undimmed every other station while the follow chip still said they were dimmed. The handler now re-applies the dimming with the rest, and a test pins the whole re-apply list so a state added later cannot be forgotten the same way.
+
 ## 2026-10-29
 
 ### Fixed
diff --git a/haminfo-dashboard/src/haminfo_dashboard/templates/dashboard/map.html b/haminfo-dashboard/src/haminfo_dashboard/templates/dashboard/map.html
index 61fa5d93..ae8ed037 100644
--- a/haminfo-dashboard/src/haminfo_dashboard/templates/dashboard/map.html
+++ b/haminfo-dashboard/src/haminfo_dashboard/templates/dashboard/map.html
@@ -4036,6 +4036,10 @@ if (window.haminfoMapNeedsLegacy()) {
         packetPathOverlay.restoreLayers();
         syncStationLabelTheme(currentTheme);
         applyStationLabels();   // #406: the rebuilt label layer starts visible
+        // #417: setStyle rebuilds the station layers, so they come back at full
+        // opacity with no filter; re-apply the follow dimming or a theme flip
+        // undims every station while the chip still says they are dimmed.
+        applyFollowDimming();
         ensureTrailLayers();
         ensureIssLayers();
         // If a TLE was already fetched before the kit style finished loading
diff --git a/haminfo-dashboard/tests/test_map_follow_station.py b/haminfo-dashboard/tests/test_map_follow_station.py
index 1d601dda..4f034099 100644
--- a/haminfo-dashboard/tests/test_map_follow_station.py
+++ b/haminfo-dashboard/tests/test_map_follow_station.py
@@ -523,3 +523,31 @@ def test_stop_clears_the_follow(behaviour):
     assert behaviour['stateAfterStop']['following'] is False
     assert behaviour['stateAfterStop']['callsign'] is None
     assert behaviour['silentAfterStop'] is False
+
+
+STYLE_LOAD_ANCHOR = "map.on('style.load', function () {"
+
+
+def style_load_handler_source():
+    """Return the theme-reload handler body, up to its own closing line."""
+    source = template_source()
+    start = source.index(STYLE_LOAD_ANCHOR)
+    end = source.index('\n    });', start)
+    return source[start:end]
+
+
+@pytest.mark.parametrize('call', [
+    'applyStationLabels();',                    # #406: rebuilt labels start visible
+    'ensureTrailLayers();',
+    'applyRadarOverlayState();',
+    'applyHeatmapViewMode(heatmapMode, false);',
+    'applyFollowDimming();',                    # #417
+])
+def test_theme_reload_reapplies_every_paint_state(call):
+    """#417: a theme flip calls map.setStyle, which rebuilds the station layers
+    and drops every paint property set on them. Follow dimming was the one
+    re-apply this handler was missing: the chip kept saying the other stations
+    were dimmed while all of them came back at full opacity. Pin the whole list
+    so the next state added to the page cannot be forgotten here in the same way.
+    """
+    assert call in style_load_handler_source()

```
