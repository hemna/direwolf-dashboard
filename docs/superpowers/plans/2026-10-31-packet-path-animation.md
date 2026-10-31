# Plan: packet-path animation (#205) — direwolf-dashboard

Four-HELD-enhancement companion. Mirrors haminfo issue #345 / PR #394
(merge `8dabb959`), shared spec basemap-kit #63.

- Issue: http://git.hemna.com/hemna/direwolf-dashboard/issues/205
- Shared spec: http://git.hemna.com/hemna/basemap-kit/issues/63
- Reference: `~/devel/mine/hamradio/haminfo` @ `8dabb959`
  - `haminfo-dashboard/src/haminfo_dashboard/packet_paths.py` (server hop algorithm)
  - `haminfo-dashboard/src/haminfo_dashboard/websocket.py` (packet dict gains the fields)
  - `haminfo-dashboard/src/haminfo_dashboard/static/js/map-shared/packet-path-animator.js`
    + `packet-path-overlay.js` (+ `state-manager.js` touched)
  - `haminfo-dashboard/tests/fixtures/packet_paths.json` (26 fixtures: direct_igate,
    used_unused, alias_only, all_aliases, ssid_mismatch, qao/qac/qax/qau/qai,
    tcpip_origin, third_party_tx, third_party_used_outer, nested_third_party,
    message_with_id, rx_correlated, tx_correlated …)
  - `haminfo-dashboard/tests/test_packet_paths.py`,
    `haminfo-dashboard/tests/js/packet-path-{animator,overlay,map}.test.js`

## Measured current state (this repo, master `3e98b2f`)

- `processor.py:216 packet_to_dict(...)` already unwraps third-party `}`
  (`:232 is_third_party`, `:244`), sets `packet["path"]` from `parsed.get("path")`
  (`:265`) and `_extract_path_from_raw(aprs_string)` (`:287`).
- Client `app.js`: `parsePath` is the haminfo #345-flagged verbatim copy
  (`app.js:2093-2113`) — keeps only `*` hops, treats any `qA*` next field as the
  igate (incl. `qAC`/`qAX`, `qAX` not flagged tcpip); alias list (`app.js:148-151`)
  lacks `TRACEn-N`, `WIDE4`+, `ECHO`, `GATE`, `ARISS`; `updatePositionCache`
  (`app.js:2251-2265`) is `Object.keys` + O(n) oldest-key scan; the only animation
  gate today is the 3 s per-callsign `animationThrottle` (`app.js:145-146`,
  `:2340-2375`); `tx-arcs` dashed style is the existing internet-link visual;
  no `visibilitychange`, no `prefers-reduced-motion`.
- No `packet_paths.py`, no animator, no packet-path layers.
- Client test convention here: pytest source-asserts
  (`tests/test_maplibre_*.py`) plus the node harness pattern
  (`tests/test_app_settings_save.py`: `_extract(app_js, name)` + `node -e` with a
  stub prelude, `pytest.skip` when node missing). CI runs `node --check` on
  every non-vendor `.js`, so new JS must stay syntactically valid.

## Port decisions

1. **Server: vendor `packet_paths.py`** (port of the haminfo module: `MAX_RAW_LENGTH`
   8192, `MAX_PATH_TOKENS` 64, `MAX_UNWRAP_DEPTH` 4, `_CALL`/`_TOKEN`/`_Q`/`_ALIAS`
   regexes, `_ALIASES` set, `_is_alias`, `_parse_frame`, `_route`, `_collapse`,
   `parse_packet_path`) and call it from `packet_to_dict`, adding the dict keys
   `packet_id`, `source`, `hops`, `rx_igate`, `tx_igate`, `tx_hops`, `no_rf_leg`
   alongside the existing `path`. Keep `path` — the log panel and GPX read it.
2. **Fixtures: copy `tests/fixtures/packet_paths.json` verbatim** — same ids, same
   expectations. The Python port must pass them unchanged.
3. **Client: one `packet-paths` section in `app.js`**, not new script files
   (this app is one IIFE; that is the surrounding convention). Behavioural
   constants come from the reference animator:
   `SOURCE_MAX_AGE = 2 h`, `HOP_MAX_AGE = 24 h`, `DUPE_WINDOW = 30 s`,
   `maxConcurrent = 3`, `trailMs = 2000`, `SOURCE_PULSE = 900`, `HOP_MS = 630`,
   `INTERNET_MS = 1080`, `NODE_PAUSE = 600`, `TX_PAUSE = 180`,
   `COLORS = {rf '#32c8d7', aprsis '#cf8bea', tx '#ff6566'}`,
   one `requestAnimationFrame` loop, bounded queue, dedupe by `packet_id`
   (fall back to the reference's identity when absent).
4. **Settings:** navbar toggle `Packet paths` (localStorage `dw-packet-paths`,
   default **on**, but initialised **off** when
   `window.matchMedia('(prefers-reduced-motion: reduce)')` matches — Hemna
   decision 2026-10-28 11:41 EDT). `visibilitychange` hides and pauses the loop.
5. **Fix the flagged defects** in the shared code once: rewrite `parsePath`
   against the shared algorithm (all used hops, `qA[EOS]` only for
   rx_igate/`no_rf_leg`, full alias set), and replace `updatePositionCache`
   with a `Map` (insertion order = cheap LRU). Grep callers of both first.

## Tasks (TDD, one commit each)

### T1 — Server hop algorithm
- **RED:** `tests/test_packet_paths.py`, mirroring `8dabb959:…/tests/test_packet_paths.py`:
  every fixture id in `tests/fixtures/packet_paths.json` asserts
  `hops`/`rx_igate`/`tx_igate`/`tx_hops`/`no_rf_leg`/`packet_id`; malformed
  frames (CRLF, NUL, oversize, depth > 4, bad addressee) → `None`.
- **GREEN:** new `src/direwolf_dashboard/packet_paths.py` (port).
- Commit: `feat(processor): shared packet-path hop algorithm from basemap-kit#63 (#205)`

### T2 — Wire the packet dict + websocket
- **RED:** extend `tests/test_processor.py` (or new `tests/test_packet_path_fields.py`)
  — `packet_to_dict` output carries the new keys for a third-party and a
  plain-rf fixture; `tests/test_routers.py` asserts `/api/packets` and the
  `/ws` packet event pass them through.
- **GREEN:** call `parse_packet_path` in `packet_to_dict`; keep `path`.
- Commit: `feat(processor): serve packet-path fields to the map client (#205)`

### T3 — Client path parsing + position cache (node harness)
- **RED:** `tests/test_packet_path_parsing.py` using the
  `test_app_settings_save.py` node pattern: extract `parsePath` and
  `updatePositionCache` from `app.js`, run the fixture expectations
  (the `*`-hop bug and `qAX` bug are the failing case), plus
  LRU eviction order for the cache.
- **GREEN:** rewrite `parsePath` + `Map`-based cache; fix every caller found by
  grep, keeping their error handling.
- Commit: `fix(map): conform packet path parsing and position cache (#205)`

### T4 — Animator + layers + cap (node harness)
- **RED:** `tests/test_packet_path_animation.py` — extract the animator section
  under node with a stub `maplibregl`/DOM prelude: cap is enforced in
  `offer()` (a 4th concurrent animation is dropped, `dropped` counted), queue is
  bounded, dedupe within 30 s animates once, hops with no recent position are
  skipped without inventing one, `aprsis` leg uses the dashed internet style,
  one `setData` per frame.
- **GREEN:** animator section + `packet-paths` source/line/circle layers below
  `stations-symbol-base`, re-added on style reload the way `tx-arcs` is.
- Commit: `feat(map): animate APRS packet paths with a concurrency cap (#205)`

### T5 — Setting, reduced motion, tab-idle guard
- **RED:** source-assert tests: navbar toggle exists, `dw-packet-paths` persisted,
  `prefers-reduced-motion` gate, `visibilitychange` pause; node harness:
  reduce-motion ⇒ initial off, explicit toggle still turns it on.
- **GREEN:** wire into the existing settings panel; ensure the loop stops when
  the tab is hidden and the queue stays bounded (the day-open-tab criterion).
- Commit: `feat(settings): packet-path toggle honouring reduced motion (#205)`

## Out of scope

No haminfo #346 far-position lookup (the dashboard must work offline); no
new dependencies; no change to `tx-arcs` semantics beyond reusing its line
style for the `aprsis` segment.

## Verification gate

`uvx ruff check . --select=E,F --ignore=E501` · `uv run pytest tests/ -q` ·
`node --check` every touched `.js`.

## Appendix A — haminfo reference kit (verbatim, inlined)

Merge `8dabb959` (PR #394). Server algorithm + fixtures + animator/overlay behaviour to port into the app.js IIFE.

### packet_paths.py (complete)

```python
"""Bounded raw TNC2 path metadata for packet animations (no position guesses)."""

from __future__ import annotations

import hashlib
import re


MAX_RAW_LENGTH = 8192
MAX_PATH_TOKENS = 64
MAX_UNWRAP_DEPTH = 4

_CALL = re.compile(r'[A-Z0-9]{1,6}(?:-(?:[0-9]|1[0-5]))?\Z')
_TOKEN = re.compile(r'[A-Za-z0-9][A-Za-z0-9-]{0,31}\*?\Z')
_Q = re.compile(r'qA[A-Za-z]\Z')
_ALIAS = re.compile(r'(?:WIDE|TRACE)(?:[0-9]+(?:-[0-9]+)?)?\Z')
_ALIASES = {'RELAY', 'ECHO', 'GATE', 'RFONLY', 'NOGATE', 'TCPIP', 'TCPXX', 'ARISS'}


def _is_alias(call: str) -> bool:
    return call in _ALIASES or bool(_ALIAS.fullmatch(call))


def _parse_frame(raw: str) -> tuple | None:
    header, sep, info = raw.partition(':')
    source, arrow, rest = header.partition('>')
    destination, comma, path = rest.partition(',')
    if not sep or not info or not arrow or not _CALL.fullmatch(source) or not _CALL.fullmatch(destination):
        return None
    tokens = path.split(',') if comma else []
    if len(tokens) > MAX_PATH_TOKENS or any(not _TOKEN.fullmatch(token) for token in tokens):
        return None
    trace = False
    for token in tokens:
        call = token.removesuffix('*')
        if _Q.fullmatch(call) and token.endswith('*'):
            return None
        trace = trace or bool(_Q.fullmatch(call))
        if not trace and not (_CALL.fullmatch(call) or _is_alias(call)):
            return None
    return source, destination, path, info, tokens


def _route(tokens: list[str]) -> tuple[list[str], str | None, str | None, bool]:
    q_index = next((i for i, token in enumerate(tokens) if _Q.fullmatch(token)), len(tokens))
    q_construct = tokens[q_index] if q_index < len(tokens) else None
    rf = tokens[:q_index]
    no_rf_leg = q_construct in {'qAC', 'qAX', 'qAU'} or any(
        token.removesuffix('*') == 'TCPIP' for token in rf
    )
    rx_igate = None
    if not no_rf_leg and q_construct in {'qAR', 'qAO', 'qAS'} and q_index + 1 < len(tokens):
        call = tokens[q_index + 1].removesuffix('*')
        if _CALL.fullmatch(call) and not _is_alias(call):
            rx_igate = call
    last_star = max((i for i, token in enumerate(rf) if token.endswith('*')), default=-1)
    used = [] if no_rf_leg else [
        token.removesuffix('*') for token in rf[:last_star + 1]
        if not _is_alias(token.removesuffix('*'))
    ]
    return used, rx_igate, q_construct, no_rf_leg


def _collapse(calls: list[str]) -> list[str]:
    return [call for i, call in enumerate(calls) if i == 0 or call != calls[i - 1]]


def parse_packet_path(raw: str) -> dict | None:
    """Parse raw (never the truncated DB path); return None for malformed frames.

    Identity is SHA1 of the innermost ``src>dest:info`` without any path.
    Messages substitute the addressee for dest; info retains the message ID.
    A third-party observation describes the outer TX RF leg separately from
    the original source. Unknown positions and recipient endpoints stay client-side.
    """
    if not isinstance(raw, str) or len(raw) > MAX_RAW_LENGTH:
        return None
    raw = raw.rstrip('\r\n')
    if any(char in raw for char in ('\r', '\n', '\x00')):
        return None
    outer = _parse_frame(raw)
    if outer is None:
        return None
    frame = outer
    depth = 0
    while frame[3].startswith('}'):
        if depth == MAX_UNWRAP_DEPTH:
            return None
        frame = _parse_frame(frame[3][1:])
        if frame is None:
            return None
        depth += 1

    source, destination, _, info, _ = frame
    used, rx_igate, q_construct, no_rf_leg = _route(outer[4])
    if q_construct in {'qAR', 'qAO', 'qAS'} and not no_rf_leg and rx_igate is None:
        return None
    addressee = None
    if info.startswith(':'):
        if len(info) < 11 or info[10] != ':':
            return None
        addressee = info[1:10].strip()
        if not re.fullmatch(r'[A-Z0-9][A-Z0-9-]{0,8}', addressee):
            return None
    canonical = f'{source}>{addressee or destination}:{info}'
    try:
        packet_id = hashlib.sha1(canonical.encode('utf-8')).hexdigest()
    except UnicodeEncodeError:
        return None
    third_party = depth > 0
    return {
        'packet_id': packet_id,
        'source': source,
        'hops': [source] if third_party else _collapse([source] + used + ([rx_igate] if rx_igate else [])),
        'rx_igate': rx_igate,
        'tx_igate': outer[0] if third_party else None,
        'no_rf_leg': no_rf_leg,
        'tx_hops': _collapse([outer[0]] + used) if third_party and not no_rf_leg else [],
    }

```

### tests/fixtures/packet_paths.json (complete)

```json
[
  {"id":"direct_igate","raw":"N0CALL>APRS,qAR,K4ABC:!3740.00N/07730.00W-","expected":{"hops":["N0CALL","K4ABC"],"rx_igate":"K4ABC"}},
  {"id":"used_unused","raw":"N0CALL>APRS,K4DIG*,WIDE2-1,qAR,K4ABC:...","expected":{"hops":["N0CALL","K4DIG","K4ABC"]}},
  {"id":"alias_only","raw":"N0CALL>APRS,WIDE1*,WIDE2-1,qAR,K4ABC:...","expected":{"hops":["N0CALL","K4ABC"]}},
  {"id":"no_star_unused","raw":"N0CALL>APRS,WIDE1-1,WIDE2-1,qAR,K4ABC:...","expected":{"hops":["N0CALL","K4ABC"]}},
  {"id":"two_digis","raw":"N0CALL>APRS,K4DIG,K4DG2*,WIDE2,qAR,K4ABC:...","expected":{"hops":["N0CALL","K4DIG","K4DG2","K4ABC"]}},
  {"id":"multi_stars","raw":"N0CALL>APRS,K4DIG*,K4DG2,K4DG3*,K4DG4,qAR,K4ABC:...","expected":{"hops":["N0CALL","K4DIG","K4DG2","K4DG3","K4ABC"]}},
  {"id":"all_aliases","raw":"N0CALL>APRS,WIDE4-4,WIDE9,TRACE7-2,TRACE,RELAY,ECHO,GATE,RFONLY,NOGATE,ARISS*,qAR,K4ABC:...","expected":{"hops":["N0CALL","K4ABC"]}},
  {"id":"unknown_digi","raw":"N0CALL>APRS,NOPOS*,qAR,K4ABC:...","expected":{"hops":["N0CALL","NOPOS","K4ABC"]}},
  {"id":"ssid_mismatch","raw":"N0CALL-9>APRS,K4DIG-1*,qAR,K4ABC-10:...","expected":{"source":"N0CALL-9","hops":["N0CALL-9","K4DIG-1","K4ABC-10"]}},
  {"id":"repeated_calls","raw":"N0CALL>APRS,N0CALL,K4DIG,K4DIG*,qAR,K4DIG:...","expected":{"hops":["N0CALL","K4DIG"]}},
  {"id":"no_star_calls","raw":"N0CALL>APRS,K4DIG,K4DG2:...","expected":{"hops":["N0CALL"],"rx_igate":null}},
  {"id":"local_rf","raw":"N0CALL>APRS,K4DIG*:...","expected":{"hops":["N0CALL","K4DIG"]}},
  {"id":"qao","raw":"N0CALL>APRS,K4DIG*,qAO,K4ABC:...","expected":{"hops":["N0CALL","K4DIG","K4ABC"],"rx_igate":"K4ABC"}},
  {"id":"qac_internet","raw":"N0CALL>APRS,TCPIP*,qAC,T2TEST:...","expected":{"hops":["N0CALL"],"no_rf_leg":true,"rx_igate":null}},
  {"id":"qax_internet","raw":"N0CALL>APRS,K4DIG*,qAX,T2TEST:...","expected":{"hops":["N0CALL"],"no_rf_leg":true,"rx_igate":null}},
  {"id":"qau_internet","raw":"N0CALL>APRS,qAU,T2TEST:...","expected":{"hops":["N0CALL"],"no_rf_leg":true,"rx_igate":null}},
  {"id":"tcpip_origin","raw":"N0CALL>APRS,TCPIP*,K4DIG*,qAR,K4ABC:...","expected":{"hops":["N0CALL"],"no_rf_leg":true,"rx_igate":null}},
  {"id":"tcpxx_alias","raw":"N0CALL>APRS,TCPXX*,qAR,K4ABC:...","expected":{"hops":["N0CALL","K4ABC"]}},
  {"id":"qas_iss","raw":"N0CALL>APRS,RS0ISS*,qAS,K4ABC:...","expected":{"hops":["N0CALL","RS0ISS","K4ABC"],"rx_igate":"K4ABC"}},
  {"id":"qai_servers","raw":"N0CALL>APRS,K4DIG*,qAI,LONGSERVER,T2TEST,K4ABC*:...","expected":{"hops":["N0CALL","K4DIG"],"rx_igate":null}},
  {"id":"first_q_only","raw":"N0CALL>APRS,K4DIG*,qAR,K4ABC,qAO,K4XYZ*:...","expected":{"hops":["N0CALL","K4DIG","K4ABC"],"rx_igate":"K4ABC"}},
  {"id":"third_party_tx","raw":"K4TX>APRS,WIDE1-1:}N0CALL>APRS,TCPIP,K4TX*:!3740.00N/...","expected":{"source":"N0CALL","hops":["N0CALL"],"tx_igate":"K4TX","tx_hops":["K4TX"],"no_rf_leg":false}},
  {"id":"third_party_used_outer","raw":"K4TX>APRS,K4DG1,K4DG2*,WIDE2-1,qAR,K4RX:}N0CALL>APRS,TCPIP,K4TX*:...","expected":{"hops":["N0CALL"],"rx_igate":"K4RX","tx_igate":"K4TX","tx_hops":["K4TX","K4DG1","K4DG2"],"no_rf_leg":false}},
  {"id":"nested_third_party","raw":"K4TX>APRS,K4DIG*:}K4MID>APRS,TCPIP*:}N0CALL>APRS,TCPIP,K4MID*:...","expected":{"source":"N0CALL","hops":["N0CALL"],"tx_igate":"K4TX","tx_hops":["K4TX","K4DIG"],"no_rf_leg":false}},
  {"id":"message_with_id","raw":"N0CALL>APRS,WIDE1-1,qAR,K4ABC::K5DEST   :hello{42","canonical":"N0CALL>K5DEST::K5DEST   :hello{42","expected":{"hops":["N0CALL","K4ABC"]}},
  {"id":"message_without_id","raw":"N0CALL>APRS,qAR,K4ABC::K5DEST-9 :hello","canonical":"N0CALL>K5DEST-9::K5DEST-9 :hello","expected":{"hops":["N0CALL","K4ABC"]}},
  {"id":"rx_correlated","raw":"WB4BOR>APRS,WIDE1-1,qAR,APPOMX::KM6LYW   :test{01","canonical":"WB4BOR>KM6LYW::KM6LYW   :test{01","expected":{"hops":["WB4BOR","APPOMX"],"tx_igate":null}},
  {"id":"tx_correlated","raw":"KM6LYW>APRS:}WB4BOR>APRS,TCPIP,KM6LYW*::KM6LYW   :test{01","canonical":"WB4BOR>KM6LYW::KM6LYW   :test{01","expected":{"hops":["WB4BOR"],"tx_igate":"KM6LYW","tx_hops":["KM6LYW"]}}
]

```

### packet-path-animator.js (complete)

```js
/* One bounded packet journey tree, one clock, no map objects or timers. */
(function () {
    'use strict';
    const SOURCE_MAX_AGE = 2 * 3600000;
    const HOP_MAX_AGE = 24 * 3600000;
    const DUPE_WINDOW = 30000;
    // Measured gold-standard GIF motion: rings overlap travel, rather than delaying it.
    const SOURCE_PULSE = 900;
    const HOP_MS = 630;
    const HOP_PAUSE = 0;
    const INTERNET_MS = 1080;
    const NODE_PAUSE = 600;
    const TX_PAUSE = 180;
    const COLORS = {rf: '#32c8d7', aprsis: '#cf8bea', tx: '#ff6566'};

    function validPosition(p, now, maxAge) {
        return p && Number.isFinite(p.lat) && Number.isFinite(p.lng) &&
            Math.abs(p.lat) <= 90 && Math.abs(p.lng) <= 180 &&
            Number.isFinite(p.at) && p.at <= now && now - p.at <= maxAge;
    }

    function point(call, position) {
        return {call: call, coordinates: [position.lng, position.lat]};
    }

    // Spherical interpolation takes the short path across the antimeridian.
    function sampleRf(a, b) {
        const rad = Math.PI / 180;
        const hav = window.MapShared.stateManager.haversineKm;
        const angle = hav(a[1], a[0], b[1], b[0]) / 6371;
        if (angle < 1e-9) return [a, b];
        const xyz = p => [Math.cos(p[1] * rad) * Math.cos(p[0] * rad),
            Math.cos(p[1] * rad) * Math.sin(p[0] * rad), Math.sin(p[1] * rad)];
        const x = xyz(a), y = xyz(b), samples = [];
        for (let i = 0; i <= 32; i++) {
            const t = i / 32;
            // Antipodal points have no unique great circle; use a bounded linear fallback.
            const s = Math.sin(angle);
            if (Math.abs(s) < 1e-9) {
                const dlon = ((b[0] - a[0] + 540) % 360) - 180;
                samples.push([a[0] + dlon * t, a[1] + (b[1] - a[1]) * t]);
                continue;
            }
            const u = Math.sin((1 - t) * angle) / s, v = Math.sin(t * angle) / s;
            const z = x.map((n, j) => u * n + v * y[j]);
            let lon = Math.atan2(z[1], z[0]) / rad;
            const previous = samples.length ? samples[samples.length - 1][0] : a[0];
            while (lon - previous > 180) lon -= 360;
            while (lon - previous < -180) lon += 360;
            samples.push([lon, Math.atan2(z[2], Math.hypot(z[0], z[1])) / rad]);
        }
        return samples;
    }

    function partial(samples, progress) {
        const index = progress * (samples.length - 1);
        const whole = Math.min(Math.floor(index), samples.length - 2);
        const t = index - whole;
        const a = samples[whole], b = samples[whole + 1];
        const dot = [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
        return {line: samples.slice(0, whole + 1).concat([dot]), dot: dot};
    }

    function create(options) {
        const cache = options.positionCache;
        const active = new Map(), seen = new Map(), queue = [];
        let maxConcurrent = 3, trailMs = 2000, dropped = 0, sequence = 0;

        function position(call, packet, now, source) {
            if (source && packet.from_call === call && packet.latitude != null && packet.longitude != null) {
                const p = {lat: Number(packet.latitude), lng: Number(packet.longitude),
                    at: window.MapShared.stateManager.utcTimestamp(packet.received_at)};
                if (validPosition(p, now, SOURCE_MAX_AGE)) return p;
            }
            const p = cache.get(call);
            return validPosition(p, now, source ? SOURCE_MAX_AGE : HOP_MAX_AGE) ? p : null;
        }

        function resolve(packet, now) {
            if (!packet || typeof packet.packet_id !== 'string' || !packet.source ||
                !Array.isArray(packet.hops)) return null;
            const src = position(packet.source, packet, now, true);
            if (!src) return null;
            const source = point(packet.source, src);
            const calls = packet.no_rf_leg || packet.tx_igate ? [packet.source] : packet.hops.slice(0, 16);
            const stops = [source];
            for (const call of calls) {
                if (call === stops[stops.length - 1].call) continue;
                const p = position(call, packet, now, false);
                if (p) stops.push(point(call, p));
            }
            const tx = [];
            for (const call of (packet.no_rf_leg ? [] : packet.tx_hops || []).slice(0, 16)) {
                const p = position(call, packet, now, false);
                if (p && (!tx.length || tx[tx.length - 1].call !== call)) tx.push(point(call, p));
            }
            const dest = packet.addressee && position(packet.addressee, packet, now, false);
            return {source: source, stops: stops, tx: tx,
                rx: packet.rx_igate, dest: dest ? point(packet.addressee, dest) : null};
        }

        function pruneSeen(now) {
            for (const [id, record] of seen) {
                if (now - record.first < DUPE_WINDOW) break;
                seen.delete(id);
            }
        }

        function enqueue(item) {
            if (active.size < maxConcurrent) {
                start(item, item.now);
            } else {
                if (queue.length === maxConcurrent) { queue.shift(); dropped++; }
                queue.push(item);
            }
        }

        function start(item, now) {
            const journey = {id: item.id, source: item.resolved.source, start: now,
                edges: new Map(), stops: new Map(), endpointOnly: item.endpointOnly,
                pendingTx: [], routes: [], internetNodes: new Map(),
                color: sequence++ % 2 ? '#ffb36b' : COLORS.rf, until: now + SOURCE_PULSE};
            active.set(item.id, journey);
            for (const route of item.routes) addRoute(journey, route, now);
        }

        function addStop(journey, stop, at, kind, color) {
            const previous = journey.stops.get(stop.call);
            if (!previous || at < previous.at) journey.stops.set(stop.call,
                {stop: stop, at: at, kind: kind, color: color || COLORS[kind]});
        }

        function addChain(journey, stops, now, prefix, kind, initialAt, color) {
            let at = initialAt;
            let key = prefix;
            const count = stops.length - 1;
            const duration = kind === 'rf' ? Math.min(HOP_MS, 3000 / Math.max(1, count)) : INTERNET_MS;
            color = color || COLORS[kind];
            addStop(journey, stops[0], at, kind, color);
            for (let i = 1; i < stops.length && journey.edges.size < 32; i++) {
                const from = stops[i - 1], to = stops[i];
                key += '>' + to.call;
                const shared = journey.edges.get(key);
                if (shared) { at = shared.end + HOP_PAUSE; continue; }
                at = Math.max(at, now);   // A late suffix must not start on its prefix's old clock.
                const same = kind === 'rf' && Math.abs(from.coordinates[0] - to.coordinates[0]) < 1e-7 &&
                    Math.abs(from.coordinates[1] - to.coordinates[1]) < 1e-7;
                const end = at + (same ? 0 : duration);
                if (!same) journey.edges.set(key, {from: from, to: to, kind: kind, color: color, start: at,
                    end: end, samples: kind === 'rf' ? sampleRf(from.coordinates, to.coordinates) : null});
                addStop(journey, to, end, kind, color);
                at = end + HOP_PAUSE;
                journey.until = Math.max(journey.until, end + trailMs);
            }
            return at - HOP_PAUSE;
        }

        function addInternet(journey, rx, txStops, dest, now, at) {
            const app = dest ? {...dest, call: dest.call + ' app', app: true} : null;
            const target = txStops.length ? txStops[0] : app;
            if (!target || target.call === rx.call) return;
            let node = journey.internetNodes.get(rx.call);
            if (!node) {
                node = {call: 'APRS-IS (internet)', coordinates: rx.coordinates,
                    internet: {from: rx.coordinates, to: target.coordinates}};
                journey.internetNodes.set(rx.call, node);
            } else if (txStops.length) node.internet.to = target.coordinates;
            if (app) app.node = node;
            const nodeAt = addChain(journey, [rx, node], now,
                rx.call + ':internet', 'aprsis', Math.max(at, now));
            const branchAt = nodeAt + NODE_PAUSE;
            const end = addChain(journey, [node, target], now,
                rx.call + ':internet-out', 'aprsis', branchAt);
            // Hold the shared inbound dashes until the internet branches have faded.
            for (const edge of journey.edges.values()) {
                if (edge.to === node) edge.holdUntil = Math.max(edge.holdUntil || 0, end + trailMs);
            }
            if (txStops.length) {
                const rf = txStops.slice();
                if (dest && rf[rf.length - 1].call !== dest.call) rf.push(dest);
                addChain(journey, rf, now, target.call + ':tx', 'rf', end + TX_PAUSE, COLORS.tx);
                if (dest) {
                    addChain(journey, [node, app], now, rx.call + ':internet-out', 'aprsis',
                        branchAt);
                }
            }
        }

        function addRoute(journey, route, now) {
            if (journey.routes.length >= 8) { dropped++; return; }
            journey.routes.push(route);
            if (journey.endpointOnly) {
                const end = route.tx.length ? route.dest || route.tx[route.tx.length - 1] :
                    route.stops[route.stops.length - 1];
                addStop(journey, end, now, route.tx.length ? 'aprsis' : 'rf');
                journey.until = Math.max(journey.until, now + SOURCE_PULSE);
                return;
            }
            const rfAt = addChain(journey, route.stops, now, journey.source.call, 'rf',
                Math.max(journey.start, now), journey.routes.length === 1 ? journey.color : '#ef76c4');
            const rx = route.rx && route.stops.find(s => s.call === route.rx);
            if (rx) {
                if (route.dest) addInternet(journey, rx, [], route.dest, now, rfAt + HOP_PAUSE);
                for (const waiting of journey.pendingTx) {
                    addInternet(journey, rx, waiting.tx, waiting.dest, now, rfAt + HOP_PAUSE);
                }
                journey.pendingTx = [];
            }
            if (route.tx.length) {
                const knownRx = journey.routes.find(r => r.rx && r.stops.some(s => s.call === r.rx));
                if (knownRx) {
                    const stop = knownRx.stops.find(s => s.call === knownRx.rx);
                    const rxStop = journey.stops.get(stop.call);
                    addInternet(journey, stop, route.tx, route.dest, now, rxStop.at + HOP_PAUSE);
                } else {
                    // TX-only observations cannot invent the unknown RX IGate.
                    addChain(journey, route.tx.concat(route.dest ? [route.dest] : []), now,
                         route.tx[0].call + ':tx', 'rf', now, COLORS.tx);
                    journey.pendingTx.push(route);
                }
            }
        }

        function offer(packet, now) {
            pruneSeen(now);
            const resolved = resolve(packet, now);
            if (!resolved) return false;
            const id = packet.packet_id;
            const signature = resolved.stops.map(s => s.call).join('>') + '|' +
                resolved.tx.map(s => s.call).join('>') + '|' + (resolved.dest ? resolved.dest.call : '');
            let record = seen.get(id);
            if (record && record.paths.has(signature)) return false;
            if (!record) {
                if (seen.size >= 1000) { dropped++; return false; }
                record = {first: now, paths: new Set()};
                seen.set(id, record);
            }
            if (record.paths.size >= 8) { dropped++; return false; }
            record.paths.add(signature);
            const playing = active.get(id);
            if (playing) addRoute(playing, resolved, now);
            else {
                const waiting = queue.find(item => item.id === id);
                if (waiting) waiting.routes.push(resolved);
                else enqueue({id: id, resolved: resolved, routes: [resolved], now: now,
                    endpointOnly: record.paths.size > 1});
            }
            return true;
        }

        function coordinates(stop) {
            if (stop.app && options.appPosition) return options.appPosition(coordinates(stop.node), stop.coordinates);
            return stop.internet && options.internetPosition ?
                options.internetPosition(stop.internet.from, stop.internet.to) : stop.coordinates;
        }

        function feature(journey, geometry, props) {
            return {type: 'Feature', geometry: geometry,
                properties: {anim_id: journey.id, ...props}};
        }

        function frame(now) {
            const output = [];
            for (const [id, journey] of active) {
                if (now >= journey.until) { active.delete(id); continue; }
                if (!journey.endpointOnly) addStop(journey, journey.source, journey.start, 'rf', journey.color);
                for (const {stop, at, kind, color} of journey.stops.values()) {
                    const age = now - at;
                    // Labels stay beside every stop while the journey is on screen.
                    const pulse = age >= 0 && age < SOURCE_PULSE;
                    output.push(feature(journey, {type: 'Point', coordinates: coordinates(stop)}, {
                        feature: 'pulse', kind: kind, callsign: stop.call,
                        color: color, radius: pulse && !stop.internet ? 8 + 28 * age / SOURCE_PULSE : 0,
                        opacity: pulse ? 0.8 * (1 - age / SOURCE_PULSE) : 0,
                        label: stop.call, internet: Boolean(stop.internet), app: Boolean(stop.app),
                    }));
                }
                for (const edge of journey.edges.values()) {
                    if (now < edge.start || now >= (edge.holdUntil || edge.end + trailMs)) continue;
                    const progress = Math.min(1, (now - edge.start) / (edge.end - edge.start));
                    const from = coordinates(edge.from), to = coordinates(edge.to);
                    const samples = edge.samples || (options.internetCurve ?
                        options.internetCurve(from, to, Boolean(edge.to.internet)) : [from, to]);
                    const geometry = partial(samples, progress);
                    const age = Math.max(0, now - edge.end);
                    const props = {kind: edge.kind, from_call: edge.from.call, to_call: edge.to.call,
                        color: edge.color, progress: progress, age_ms: age,
                        opacity: edge.kind === 'aprsis' ? 0.85 : 0.85 * (1 - age / trailMs)};
                    output.push(feature(journey, {type: 'LineString', coordinates: geometry.line},
                        {...props, feature: 'segment'}));
                    if (progress < 1) output.push(feature(journey, {type: 'Point', coordinates: geometry.dot},
                        {...props, feature: 'dot', opacity: 1, radius: 4}));
                }
            }
            while (queue.length && active.size < maxConcurrent) {
                const item = queue.shift();
                if (now - item.now < DUPE_WINDOW) start(item, now);
                else dropped++;
            }
            pruneSeen(now);
            return {type: 'FeatureCollection', features: output};
        }

        function clear() { active.clear(); queue.length = 0; seen.clear(); sequence = 0; }
        function configure(settings) {
            if ([1, 3, 5].includes(settings.maxConcurrent)) maxConcurrent = settings.maxConcurrent;
            if ([1000, 2000, 4000].includes(settings.trailMs)) {
                const change = settings.trailMs - trailMs;
                trailMs = settings.trailMs;
                for (const journey of active.values()) {
                    if (journey.edges.size) journey.until += change;
                    for (const edge of journey.edges.values()) {
                        if (edge.holdUntil) edge.holdUntil += change;
                    }
                }
            }
            while (active.size > maxConcurrent) active.delete(active.keys().next().value);
            while (queue.length > maxConcurrent) { queue.shift(); dropped++; }
        }
        return {offer: offer, frame: frame, clear: clear, configure: configure,
            knownPosition: (call, packet, now) => position(call, packet, now, call === packet.source),
            active: active, queue: queue, seen: seen,
            get dropped() { return dropped; },
            get hasWork() { return active.size > 0 || queue.length > 0; }};
    }

    window.MapShared = window.MapShared || {};
    window.MapShared.packetPathAnimator = {create: create};
})();

```

### packet-path-overlay.js (complete)

```js
/* MapLibre lifecycle and preferences for the bounded, map-independent animator. */
(function () {
    'use strict';

    function init(options) {
        const map = options.map, cache = options.positionCache;
        const controls = {};
        for (const name of ['toggle', 'enabled', 'cap', 'trail', 'readout', 'legend']) {
            controls[name] = document.getElementById('packet-path-' + name);
        }
        const motion = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;
        function saved(key) {
            try { return window.localStorage.getItem(key); } catch (_) { return null; }
        }
        function persist(key, value) {
            try { window.localStorage.setItem(key, value); } catch (_) { /* Private browsing can deny storage. */ }
        }
        const choice = saved('haminfo-packet-paths');
        let explicit = choice === 'on' || choice === 'off';
        let enabled = explicit ? choice === 'on' : !(motion && motion.matches);
        let cap = Number(saved('haminfo-packet-path-cap'));
        let trail = Number(saved('haminfo-packet-path-trail'));
        if (![1, 3, 5].includes(cap)) cap = 3;
        if (![1000, 2000, 4000].includes(trail)) trail = 2000;
        let raf = null;
        let data = {type: 'FeatureCollection', features: []};
        let chrome = 0, width = 0;

        function measureChrome() {
            const top = map.getContainer().getBoundingClientRect().top;
            width = map.getCanvas().clientWidth;
            chrome = 0;
            for (const element of document.querySelectorAll('.navbar, #map-controls, .maplibregl-ctrl-top-left, .maplibregl-ctrl-top-right')) {
                chrome = Math.max(chrome, element.getBoundingClientRect().bottom - top);
            }
        }
        function internetPosition(from, to) {
            const a = map.project(from), b = map.project(to);
            const node = map.unproject({x: (a.x + b.x) / 2,
                y: Math.max(chrome + 40, Math.min(a.y, b.y) - 150)});
            return [node.lng, node.lat];
        }
        function appPosition(node, station) {
            const a = map.project(node), b = map.project(station);
            const app = map.unproject({x: b.x, y: a.y + 50});
            return [app.lng, app.lat];
        }
        function internetCurve(from, to, inbound) {
            const a = map.project(from), b = map.project(to), points = [];
            const c = inbound ? {x: a.x, y: b.y} : {x: a.x + (b.x - a.x) * 0.55, y: a.y - 16};
            const d = inbound ? {x: a.x + (b.x - a.x) * 0.55, y: b.y} : {x: b.x, y: a.y - 16};
            for (let i = 0; i <= 32; i++) {
                const t = i / 32, u = 1 - t;
                const p = map.unproject({x: u ** 3 * a.x + 3 * u ** 2 * t * c.x + 3 * u * t ** 2 * d.x + t ** 3 * b.x,
                    y: u ** 3 * a.y + 3 * u ** 2 * t * c.y + 3 * u * t ** 2 * d.y + t ** 3 * b.y});
                points.push([p.lng, p.lat]);
            }
            return points;
        }
        const animator = window.MapShared.packetPathAnimator.create({positionCache: cache,
            internetPosition: internetPosition, appPosition: appPosition, internetCurve: internetCurve});
        animator.configure({maxConcurrent: cap, trailMs: trail});

        function styleReady() { return !map.isStyleLoaded || map.isStyleLoaded(); }
        function writeData() {
            const source = map.getSource('packet-paths');
            if (source) source.setData(data);
        }
        function legend() {
            if (!controls.legend) return;
            const internet = data.features.some(f => f.properties.kind === 'aprsis');
            controls.legend.hidden = !internet;
            controls.legend.textContent = internet ? 'RF — solid   APRS-IS — dashed (internet)' : '';
        }
        function clear() {
            if (raf !== null) window.cancelAnimationFrame(raf);
            raf = null;
            animator.clear();
            data = {type: 'FeatureCollection', features: []};
            writeData();
            if (controls.readout) controls.readout.textContent = '';
            legend();
        }
        function schedule() {
            if (raf === null && enabled && !document.hidden && animator.hasWork) {
                raf = window.requestAnimationFrame(render);
            }
        }
        function render() {
            raf = null;
            if (!enabled || document.hidden) { clear(); return; }
            // Animator positions and received_at use epoch milliseconds, not rAF's monotonic clock.
            data = animator.frame(Date.now());
            for (const feature of data.features) {
                if (feature.properties.feature === 'pulse' && !feature.properties.internet) {
                    const x = map.project(feature.geometry.coordinates).x;
                    // Reserve one text-size per glyph plus the one-em offset; never move RF coordinates.
                    feature.properties.label_anchor = x + 12 * (feature.properties.label.length + 1) > width ? 'right' : 'left';
                }
            }
            writeData();
            legend();
            schedule();
        }
        function syncControls() {
            if (controls.toggle) controls.toggle.setAttribute('aria-pressed', String(enabled));
            if (controls.enabled) controls.enabled.checked = enabled;
            if (controls.cap) controls.cap.value = String(cap);
            if (controls.trail) controls.trail.value = String(trail);
        }
        function setEnabled(value, userChoice) {
            enabled = value;
            if (userChoice) {
                explicit = true;
                persist('haminfo-packet-paths', enabled ? 'on' : 'off');
            }
            syncControls();
            if (!enabled) clear();
        }
        if (controls.toggle) controls.toggle.addEventListener('click', () => setEnabled(!enabled, true));
        if (controls.enabled) controls.enabled.addEventListener('change', () => setEnabled(controls.enabled.checked, true));
        if (controls.cap) controls.cap.addEventListener('change', function () {
            const value = Number(controls.cap.value);
            if ([1, 3, 5].includes(value)) {
                cap = value;
                animator.configure({maxConcurrent: cap});
                persist('haminfo-packet-path-cap', String(cap));
            }
            syncControls();
        });
        if (controls.trail) controls.trail.addEventListener('change', function () {
            const value = Number(controls.trail.value);
            if ([1000, 2000, 4000].includes(value)) {
                trail = value;
                animator.configure({trailMs: trail});
                persist('haminfo-packet-path-trail', String(trail));
            }
            syncControls();
        });
        if (motion) {
            const changed = () => { if (!explicit) setEnabled(!motion.matches, false); };
            if (motion.addEventListener) motion.addEventListener('change', changed);
            else if (motion.addListener) motion.addListener(changed);
        }
        document.addEventListener('visibilitychange', () => { if (document.hidden) clear(); });

        function offer(packet) {
            if (!enabled || document.hidden || !packet || !packet.source || !Array.isArray(packet.hops)) return false;
            if (/^(object|item)$/.test((packet.packet_type || '').toLowerCase())) return false;
            const now = Date.now(), bounds = map.getBounds();
            const calls = [packet.source].concat(packet.hops.slice(0, 16),
                (packet.tx_hops || []).slice(0, 16));
            const visible = calls.some(call => {
                if (!call) return false;
                const p = animator.knownPosition(call, packet, now);
                return p && bounds.contains([p.lng, p.lat]);
            });
            if (!visible || !animator.offer(packet, now)) return false;
            if (controls.readout) {
                const raw = typeof packet.raw === 'string' ? packet.raw : '';
                const colon = raw.indexOf(':');
                let header = colon < 0 ? raw : raw.slice(0, colon);
                if (colon >= 0 && raw[colon + 1] === '}') header += '\n' + raw.slice(colon + 2).split(':')[0];
                controls.readout.textContent = header;
            }
            schedule();
            return true;
        }

        function restoreLayers(styleLoaded) {
            if (!styleLoaded && !styleReady()) return;
            if (!map.getSource('packet-paths')) map.addSource('packet-paths', {type: 'geojson', data: data});
            const get = key => ['get', key];
            const filter = (feature, kind) => kind ? ['all', ['==', get('feature'), feature], ['==', get('kind'), kind]] :
                ['==', get('feature'), feature];
            const pulse = filter('pulse');
            const node = ['all', pulse, ['==', get('internet'), true]];
            const layers = [
                // A slower-fading dark edge keeps late trails readable over light basemap tiles.
                {id: 'packet-path-casing', type: 'line', filter: filter('segment', 'rf'),
                    paint: {'line-color': '#101820', 'line-width': 5, 'line-opacity': ['sqrt', get('opacity')]}},
                {id: 'packet-path-glow', type: 'line', filter: filter('segment', 'rf'),
                    paint: {'line-color': get('color'), 'line-width': 12, 'line-blur': 6,
                        'line-opacity': ['*', get('opacity'), 0.35]}},
                {id: 'packet-path-line', type: 'line', filter: filter('segment', 'rf'),
                    paint: {'line-color': get('color'), 'line-width': 3, 'line-opacity': get('opacity')}},
                {id: 'packet-path-internet', type: 'line', filter: filter('segment', 'aprsis'),
                    paint: {'line-color': get('color'), 'line-width': 2, 'line-dasharray': [5, 5], 'line-opacity': get('opacity')}},
                {id: 'packet-path-dot-halo', type: 'circle', filter: filter('dot'),
                    paint: {'circle-color': get('color'), 'circle-radius': 8, 'circle-blur': 0.7, 'circle-opacity': 0.5}},
                {id: 'packet-path-dot', type: 'circle', filter: filter('dot'),
                    paint: {'circle-color': '#ffffff', 'circle-radius': get('radius'), 'circle-opacity': get('opacity')}},
                {id: 'packet-path-pulse', type: 'circle', filter: pulse,
                    paint: {'circle-radius': get('radius'), 'circle-opacity': 0, 'circle-stroke-color': get('color'),
                        'circle-stroke-width': 2, 'circle-stroke-opacity': get('opacity')}},
                {id: 'packet-path-label', type: 'symbol', filter: ['all', pulse, ['!=', get('internet'), true]],
                    layout: {'text-field': get('label'), 'text-font': ['NotoSansRegular'], 'text-size': 12,
                        'text-anchor': get('label_anchor'),
                        'text-offset': ['case', ['==', get('label_anchor'), 'right'], ['literal', [-1, 0]], ['literal', [1, 0]]],
                        'text-allow-overlap': true, 'text-ignore-placement': true},
                    paint: {'text-color': '#e8eef8', 'text-halo-color': '#101820', 'text-halo-width': 2}},
                {id: 'packet-path-node', type: 'circle', filter: node,
                    paint: {'circle-radius': 18, 'circle-color': '#403065', 'circle-stroke-width': 1, 'circle-stroke-color': get('color')}},
                {id: 'packet-path-node-is', type: 'symbol', filter: node,
                    layout: {'text-field': 'IS', 'text-font': ['NotoSansRegular'], 'text-size': 12,
                        'text-allow-overlap': true, 'text-ignore-placement': true}, paint: {'text-color': '#e8eef8'}},
                {id: 'packet-path-app', type: 'circle', filter: ['all', pulse, ['==', get('app'), true]],
                    paint: {'circle-radius': 8, 'circle-color': '#101820', 'circle-stroke-color': get('color'), 'circle-stroke-width': 2}},
                {id: 'packet-path-node-label', type: 'symbol', filter: node,
                    layout: {'text-field': get('label'), 'text-font': ['NotoSansRegular'], 'text-size': 12,
                        'text-offset': [0, 2.7], 'text-allow-overlap': true, 'text-ignore-placement': true},
                    paint: {'text-color': get('color'), 'text-halo-color': '#101820', 'text-halo-width': 1.5}},
            ];
            const before = map.getLayer('stations_icon') ? 'stations_icon' : undefined;
            for (const layer of layers) {
                if (map.getLayer(layer.id)) continue;
                layer.source = 'packet-paths';
                // Dynamic properties must not ease between successive animation frames.
                for (const key of Object.keys(layer.paint)) layer.paint[key + '-transition'] = {duration: 0, delay: 0};
                map.addLayer(layer, before);
            }
            writeData();
            schedule();
        }
        syncControls();
        legend();
        measureChrome();
        map.on('resize', measureChrome);
        map.on('style.load', () => { measureChrome(); restoreLayers(true); });
        restoreLayers();
        return {offer: offer, restoreLayers: restoreLayers, clear: clear};
    }

    window.MapShared = window.MapShared || {};
    window.MapShared.packetPathOverlay = {init: init};
})();

```
