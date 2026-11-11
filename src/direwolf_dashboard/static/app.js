/**
 * Direwolf Dashboard — Client-side application
 *
 * Handles: WebSocket connection, MapLibre main map (decode mini-map stays
 * Leaflet), packet log, filters, settings
 */

(function () {
    'use strict';

    // --- Configurable URLs (set by host app, defaults for standalone) ---
    const API_BASE = window.DIREWOLF_API_BASE || '/api';
    const WS_URL = window.DIREWOLF_WS_URL ||
        ((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws');

    // --- State ---
    let map = null;
    let ws = null;
    let wsReconnectDelay = 1000;
    let config = {};
    let stations = {};      // callsign -> { data, popupDiv }

    // Issue #218: total height of the top toolbar chrome (both bands).
    function toolbarChromeHeight() {
        var h = 0;
        var tn = document.getElementById('topnav'); if (tn) h += tn.offsetHeight;
        var tr = document.getElementById('toolrow'); if (tr) h += tr.offsetHeight;
        var t = document.getElementById('toolbar'); if (t) h += t.offsetHeight;
        return h || 82;
    }

    // Main map is MapLibre (issue #125); Leaflet-only paths are guarded on this.
    let mainMapIsMaplibre = false;
    // GeoJSON features (callsign -> Feature) backing the maplibre station layers.
    let stationFeatures = {};
    let stationFeatureCollection = { type: 'FeatureCollection', features: [] };
    let activePopup = null; // current maplibre popup (for close helpers)
    let activePopupCallsign = null; // station whose popup is open
    let autoScroll = true;
    const MAX_LOG_ROWS = 500;
    let trailHours = 1;  // Current trail duration in hours
    // Station tracks (callsign -> [[lat, lng], ...]) + the GeoJSON sources
    // backing them; data lives in JS state so it survives theme switches.
    const stationTracks = {};
    let _overlayFC = null; // station popup overlay FeatureCollection
    let _arcFC = null;     // active tx-arc animation FeatureCollection
    let showRouteDistances = true; // updated from config on load
    let showGpxOverlay = true;     // updated from config on load
    let showStatsOverlay = true;   // updated from config on load
    let showMyLocationOverlay = true; // updated from config on load
    let showTimestamps = false;    // updated from config on load
    let gpxLayer = null;    // Loaded GPX: { fc, bounds, distanceKm, nTrk, nWpt } (#127)
    let gpxControlEl = null; // Reference to the GPX control DOM element
    let myLocationOverlayEl = null; // Reference to My Location overlay DOM element
    let waitingForPosition = false;
    let urlMapView = null;  // view from a shared #zoom/lat/lon link (#150)
    let myPositionPinMarker = null;
    let pinModeActive = false;

    // --- Log View State ---
    const LOG_STATES = ['visible', 'hidden'];
    const LOG_STATE_KEY = 'logViewState';
    let logViewState = 'visible';
    let resizeEnabled = true;

    // --- API token (issue #73) ---
    // Optional bearer token for state-changing endpoints. Stored client-side
    // in localStorage (never returned by GET /config), sent as X-API-Token.
    const API_TOKEN_KEY = 'dw-api-token';

    /** Return fetch headers with the API token attached when one is stored. */
    function _apiHeaders(extra) {
        const headers = Object.assign({}, extra || {});
        const token = localStorage.getItem(API_TOKEN_KEY);
        if (token) headers['X-API-Token'] = token;
        return headers;
    }

    // --- Modal refresh timer handles (#62) ---
    // Stored so timers can be inspected/cleared; prevents accumulation on re-init.
    let _slAgeTimer = null;       // station-list age refresh
    let _mpRefreshTimer = null;   // messages-panel auto-refresh
    let _wpRefreshTimer = null;   // weather-panel auto-refresh

    // --- Log Colors (#35) ---
    const LOG_COLORS_KEY = 'dw-log-colors';
    // Mapping: input id suffix -> CSS variable name
    const LOG_COLOR_MAP = {
        'rx':         '--log-rx',
        'tx':         '--log-tx',
        'type':       '--log-type',
        'comment':    '--log-comment',
        'human-info': '--log-human-info',
        'bearing':    '--log-bearing',
        'distance':   '--log-distance',
        'dim':        '--log-dim',
    };

    /** Return stored log colors from localStorage, or empty object. */
    function _getStoredLogColors() {
        try { return JSON.parse(localStorage.getItem(LOG_COLORS_KEY) || '{}'); }
        catch { return {}; }
    }

    /** Apply a colors object {varName: hexColor} to :root. */
    function _applyLogColors(colors) {
        const root = document.documentElement;
        Object.entries(colors).forEach(([varName, val]) => {
            if (val) root.style.setProperty(varName, val);
            else     root.style.removeProperty(varName);
        });
    }

    /** Read color inputs from settings and save to localStorage + apply. */
    function _saveLogColors() {
        const colors = {};
        Object.keys(LOG_COLOR_MAP).forEach(suffix => {
            const el = document.getElementById('lc-' + suffix);
            if (el) colors[LOG_COLOR_MAP[suffix]] = el.value;
        });
        localStorage.setItem(LOG_COLORS_KEY, JSON.stringify(colors));
        _applyLogColors(colors);
    }

    /** Reset all log color overrides — removes custom properties so theme defaults apply. */
    function _resetLogColors() {
        localStorage.removeItem(LOG_COLORS_KEY);
        const root = document.documentElement;
        Object.values(LOG_COLOR_MAP).forEach(v => root.style.removeProperty(v));
        _populateLogColorInputs();
    }

    /** Populate color inputs from current computed values (theme + any overrides). */
    function _populateLogColorInputs() {
        const stored = _getStoredLogColors();
        Object.keys(LOG_COLOR_MAP).forEach(suffix => {
            const el = document.getElementById('lc-' + suffix);
            if (!el) return;
            const varName = LOG_COLOR_MAP[suffix];
            if (stored[varName]) {
                el.value = stored[varName];
            } else {
                // Read the current computed value from :root (theme default)
                const computed = getComputedStyle(document.documentElement)
                    .getPropertyValue(varName).trim();
                // color inputs require a valid hex color
                try { el.value = computed || '#888888'; } catch { el.value = '#888888'; }
            }
        });
    }

    // --- Animation state ---
    const stationPositionCache = new Map(); // insertion order = LRU (#205)
    const POSITION_CACHE_MAX = 512;
    const animationThrottle = {};
    const ANIMATION_COOLDOWN_MS = 3000;
    const activeAnimationElements = [];
    // Shared alias set from packet_paths.py (#205); WIDE/TRACE forms match by regex.
    const GENERIC_PATH_ALIASES = new Set([
        'RELAY', 'ECHO', 'GATE', 'RFONLY', 'NOGATE', 'TCPIP', 'TCPXX', 'ARISS',
    ]);

    // Packet-path animator state: declared before the first `await initMap()`
    // so style.load handlers can run while these are already initialized.
    let packetPathsData = {type: 'FeatureCollection', features: []};
    let packetPathsRaf = null;
    let packetPathChrome = 0;
    let packetPathWidth = 0;
    // #205: explicit saved choice (dw-packet-paths '1'/'0') overrides the OS
    // default; default on unless prefers-reduced-motion: reduce (decision
    // 2026-10-28). The settings toggle can flip either way and persists it.
    let packetPathsEnabled = packetPathInitialEnabled(localStorage.getItem('dw-packet-paths'));
    const packetPaths = createPacketPathAnimator(stationPositionCache, packetPathInternetPosition, packetPathInternetCurve);

    // --- Theme Support ---
    const _embedded = window.__dwEmbedded || false;
    let _currentTheme = window.__dwTheme || 'dark';

    /** Read a CSS custom property value from :root / [data-theme]. */
    function themeColor(varName) {
        return getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
    }

    function applyTheme(theme) {
        if (theme !== 'light' && theme !== 'dark') return;
        var redundant = theme === _currentTheme;
        _currentTheme = theme;
        document.documentElement.setAttribute('data-theme', theme);
        if (!_embedded) localStorage.setItem('dw-theme', theme);
        updateThemeIcon();
        // A repeated 'theme-change' message from the embedding window asks for
        // a theme already on screen; nothing to repaint (#201).
        if (redundant) return;
        // Swap the maplibre style's paint in place; a full setStyle drops every
        // layer the page adds on 'style.load' and re-adds it (#201).
        if (mainMapIsMaplibre && map) {
            fetchStyleForTheme(theme).then(applyThemeStyle).catch(function (e) {
                console.error('Failed to switch map style:', e);
            });
        }
    }
    /** True when the kit layer only differs in paint: same type, source and
     *  filter/layout, so setPaintProperty is enough. */
    function themeStructureMatches(layer, live) {
        if (!live || layer.type !== live.type) return false;
        if (layer.source !== live.source || layer['source-layer'] !== live['source-layer']) {
            return false;
        }
        return ['layout', 'filter', 'minzoom', 'maxzoom'].every(function (k) {
            return JSON.stringify(layer[k]) === JSON.stringify(live[k]);
        });
    }
    /** Apply a theme style to the live map. The light and dark kit styles carry
     *  the same sources, layer ids, order, layout and filters; only the paint
     *  values and the base sprite differ (measured in #201). Push those instead
     *  of replacing the style; anything structural still gets a full replace. */
    function applyThemeStyle(style) {
        // shortcut: a kit style that removes layers between page load and a
        // theme click keeps the stale layer until reload; revisit when the kit
        // pin in basemap.py changes.
        var liveLayers = new Map(map.getStyle().layers.map(function (l) { return [l.id, l]; }));
        var structural = style.layers.some(function (l) {
            return !themeStructureMatches(l, liveLayers.get(l.id));
        });
        if (structural || !style.sprite || typeof map.setSprite !== 'function') {
            map.setStyle(style);
            return;
        }
        style.layers.forEach(function (layer) {
            var have = liveLayers.get(layer.id).paint || {};
            var want = layer.paint || {};
            Object.keys(want).forEach(function (k) {
                if (JSON.stringify(want[k]) !== JSON.stringify(have[k])) {
                    map.setPaintProperty(layer.id, k, want[k]);
                }
            });
            Object.keys(have).forEach(function (k) {
                if (!(k in want)) map.setPaintProperty(layer.id, k, null);
            });
        });
        map.setSprite(style.sprite);
        repaintAppLayers();
    }
    /** Paint the layers the page adds itself with the new theme's colours; the
     *  in-place swap leaves them untouched, and they are not in the kit style. */
    function repaintAppLayers() {
        function paint(id, key, value) {
            if (map.getLayer(id)) map.setPaintProperty(id, key, value);
        }
        paint('stations-my-glow', 'circle-color', themeColor('--my-station-glow'));
        paint('stations-my-glow-core', 'circle-color', themeColor('--my-station-glow'));
        paint('stations-label', 'text-color', themeColor('--text-primary'));
        paint('stations-label', 'text-halo-color', themeColor('--label-shadow'));
        paint('station-tracks-line', 'line-color', themeColor('--station-track'));
        paint('station-overlay-path', 'line-color', themeColor('--path-overlay-color') || '#ff9500');
        paint('station-overlay-distance', 'line-color', themeColor('--distance-line-color') || '#00bfff');
        paint('tx-arcs-line', 'line-color', themeColor('--route-tcpip-line'));
        paint('gpx-track', 'line-color', themeColor('--gpx-accent'));
        paint('gpx-route', 'line-color', themeColor('--gpx-accent'));
        paint('gpx-points', 'circle-color', ['match', ['get', 'kind'],
            'start', themeColor('--gpx-start'),
            'end', themeColor('--gpx-end'),
            themeColor('--gpx-waypoint')]);
    }

    function toggleTheme() {
        applyTheme(_currentTheme === 'dark' ? 'light' : 'dark');
    }

    function updateThemeIcon() {
        var btn = document.getElementById('btn-theme');
        if (!btn) return;
        var sun = btn.querySelector('.icon-sun');
        var moon = btn.querySelector('.icon-moon');
        if (sun) sun.style.display = _currentTheme === 'dark' ? 'inline' : 'none';
        if (moon) moon.style.display = _currentTheme === 'light' ? 'inline' : 'none';
    }

    // Listen for theme changes from parent window (iframe embedding)
    window.addEventListener('message', function (e) {
        if (e.data && e.data.type === 'theme-change' && e.data.theme) {
            applyTheme(e.data.theme);
        }
    });

    // Mark body as embedded so CSS can hide the theme toggle
    if (_embedded) {
        document.addEventListener('DOMContentLoaded', function () {
            document.body.classList.add('embedded');
        });
    }

    // --- APRS Symbol Sprites ---
    const SYMBOL_SIZE = 24;
    const SPRITE_COLS = 16;
    const PRIMARY_SPRITE = '/static/symbols/aprs-symbols-24-0.png';
    const SECONDARY_SPRITE = '/static/symbols/aprs-symbols-24-1.png';

    function getSymbolSpritePosition(symbolChar) {
        let charCode = 45; // default '-' = house
        if (symbolChar && symbolChar.length > 0) {
            const code = symbolChar.charCodeAt(0);
            if (code >= 33 && code <= 126) {
                charCode = code;
            }
        }
        const index = Math.max(0, Math.min(95, charCode - 33));
        const col = index % SPRITE_COLS;
        const row = Math.floor(index / SPRITE_COLS);
        return { x: -(col * SYMBOL_SIZE), y: -(row * SYMBOL_SIZE) };
    }

    function parseSymbolTable(symbolTable) {
        if (!symbolTable || symbolTable === '/') {
            return { spriteUrl: PRIMARY_SPRITE, overlay: null };
        } else if (symbolTable === '\\') {
            return { spriteUrl: SECONDARY_SPRITE, overlay: null };
        } else {
            return { spriteUrl: SECONDARY_SPRITE, overlay: symbolTable };
        }
    }

    // --- Weather units (issue #78) ---
    // aprslib stores temperature in Celsius, wind in m/s, pressure in hPa,
    // rain in mm. The user can choose metric (default, matches stored data)
    // or imperial (°F / mph / inHg / in) via the Units setting in Settings.
    const UNITS_KEY = 'dw-units';
    const UNITS_METRIC = 'metric';
    const UNITS_IMPERIAL = 'imperial';

    function getUnits() {
        return localStorage.getItem(UNITS_KEY) === UNITS_IMPERIAL
            ? UNITS_IMPERIAL : UNITS_METRIC;
    }
    function isImperial() { return getUnits() === UNITS_IMPERIAL; }
    function cToF(c) { return (c * 9 / 5) + 32; }
    function msToMph(ms) { return ms * 2.23694; }
    function hpaToInHg(hpa) { return hpa * 0.02953; }
    function mmToIn(mm) { return mm * 0.0393701; }

    function fmtTemp(c) {
        return isImperial() ? `${cToF(c).toFixed(1)}°F` : `${c.toFixed(1)}°C`;
    }
    function fmtWind(ms) {
        return isImperial() ? `${msToMph(ms).toFixed(0)} mph` : `${ms.toFixed(1)} m/s`;
    }
    function fmtPressure(hpa) {
        return isImperial() ? `${hpaToInHg(hpa).toFixed(2)} inHg` : `${hpa.toFixed(1)} hPa`;
    }
    function fmtRain(mm) {
        return isImperial() ? `${mmToIn(mm).toFixed(2)} in` : `${mm.toFixed(1)} mm`;
    }

    /** DOM element for a maplibregl.Marker with the same APRS icon + label
     *  markup as the Leaflet divIcon (decode mini-map, issue #128). */
    function createSymbolIconElement(symbolTable, symbolChar, callsign) {
        const el = document.createElement('div');
        el.className = 'aprs-symbol-icon';
        el.style.width = '24px';
        el.style.height = '24px';
        el.innerHTML = createSymbolIconHtml(symbolTable, symbolChar, callsign);
        return el;
    }

    function createSymbolIconHtml(symbolTable, symbolChar, callsign) {
        const pos = getSymbolSpritePosition(symbolChar);
        const { spriteUrl, overlay } = parseSymbolTable(symbolTable);

        let html = '<div style="position:relative;width:24px;height:24px;">';
        html += `<div style="width:24px;height:24px;background-image:url('${spriteUrl}');background-position:${pos.x}px ${pos.y}px;background-repeat:no-repeat;image-rendering:pixelated;"></div>`;

        if (overlay) {
            // overlay is a radio-derived symbol-table char — escape it (#79)
            html += `<div style="position:absolute;top:0;left:0;width:24px;height:24px;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:bold;color:#000;text-shadow:0 0 2px #fff, 0 0 2px #fff;">${escHtml(overlay)}</div>`;
        }

        if (callsign) {
            // callsign is radio/user-controlled — escape it (#79)
            html += `<span class="station-label" style="position:absolute;left:26px;top:50%;transform:translateY(-50%);white-space:nowrap;">${escHtml(callsign)}</span>`;
        }
        html += '</div>';
        return html;
    }

    // --- APRS Symbol Names ---
    const PRIMARY_SYMBOLS = {
        '!':'Police Station','"':'Reserved','#':'Digi','$':'Phone','%':'DX Cluster',
        '&':'HF Gateway','\'':'Small Aircraft','(':'Mobile Satellite Station',')':'Wheelchair',
        '*':'Snowmobile','+':'Red Cross',',':'Boy Scouts','-':'House','.':'X','/':'Red Dot',
        '0':'Circle (0)','1':'Circle (1)','2':'Circle (2)','3':'Circle (3)','4':'Circle (4)',
        '5':'Circle (5)','6':'Circle (6)','7':'Circle (7)','8':'Circle (8)','9':'Circle (9)',
        ':':'Fire',';':'Campground','<':'Motorcycle','=':'Railroad Engine','>':'Car',
        '?':'File Server','@':'Hurricane','A':'Aid Station','B':'BBS','C':'Canoe','D':'Fire Dept',
        'E':'Horse','F':'Fire Truck','G':'Glider','H':'Hospital','I':'TCP/IP','J':'Jeep',
        'K':'School','L':'PC User','M':'MacAPRS','N':'NTS Station','O':'Balloon',
        'P':'Police','Q':'TBD','R':'Rec Vehicle','S':'Space Shuttle','T':'SSTV',
        'U':'Bus','V':'ATV','W':'NWS Site','X':'Helicopter','Y':'Yacht','Z':'WinAPRS',
        '[':'Jogger','\\':'Triangle',']':'PBBS','^':'Large Aircraft','_':'Weather Station',
        '`':'Dish Antenna','a':'Ambulance','b':'Bicycle','c':'ICP','d':'Fire Station',
        'e':'Horse','f':'Fire Truck','g':'Glider','h':'Hospital','i':'IOTA','j':'Jeep',
        'k':'Truck','l':'Laptop','m':'Mic-E Repeater','n':'Node','o':'EOC','p':'Dog',
        'q':'Grid Square','r':'Repeater','s':'Ship','t':'Truck Stop','u':'Truck (18-wheeler)',
        'v':'Van','w':'Water Station','x':'xAPRS','y':'House w/Yagi','z':'TBD',
        '{':'TBD','|':'TNC Stream Switch','}':'TBD','~':'TNC Stream Switch'
    };

    const ALTERNATE_SYMBOLS = {
        '!':'Emergency','"':'Reserved','#':'Digi (Overlay)','$':'Bank','%':'Power Plant',
        '&':'HF Gateway (Overlay)','\'':'Crash Site','(':'Cloudy','(':'Cloudy',')':'Firenet',
        '*':'Snow','+':'Church',',':'Girl Scouts','-':'House (Overlay)','.':'Unknown','/':'Red Dot',
        '0':'Circle (Overlay)','1':'TBD','2':'TBD','3':'TBD','4':'TBD',
        '5':'TBD','6':'TBD','7':'TBD','8':'802.11','9':'Gas Station',
        ':':'Hail',';':'Park','<':'Advisory (Overlay)','=':'TBD','>':'Car (Overlay)',
        '?':'Info Kiosk','@':'Hurricane','A':'Box','B':'Blowing Snow','C':'Coast Guard',
        'D':'Drizzle','E':'Smoke','F':'Freezing Rain','G':'Snow Shower','H':'Haze',
        'I':'Rain Shower','J':'Lightning','K':'Kenwood','L':'Lighthouse','M':'TBD',
        'N':'Navigation Buoy','O':'Balloon (Overlay)','P':'Parking','Q':'Earthquake',
        'R':'Restaurant','S':'Satellite','T':'Thunderstorm','U':'Sunny','V':'VORTAC',
        'W':'NWS Site (Overlay)','X':'Pharmacy','Y':'TBD','Z':'TBD',
        '[':'Wall Cloud','\\':'TBD',']':'TBD','^':'Aircraft (Overlay)','_':'WX Station (Overlay)',
        '`':'Rain','a':'ARRL','b':'Blowing Dust','c':'Civil Defense','d':'DX Spot',
        'e':'Sleet','f':'Funnel Cloud','g':'Gale Flags','h':'HAM Store','i':'Indoor POI',
        'j':'Work Zone','k':'SUV','l':'Area Symbols','m':'Value Sign','n':'Triangle (Overlay)',
        'o':'Small Circle','p':'Partly Cloudy','q':'TBD','r':'Restrooms','s':'Ship (Overlay)',
        't':'Tornado','u':'Truck (Overlay)','v':'Van (Overlay)','w':'Flooding',
        'x':'Wreck','y':'Skywarn','z':'Shelter (Overlay)','{':'Fog','|':'TNC Stream Switch',
        '}':'TBD','~':'TNC Stream Switch'
    };

    function getSymbolName(table, symbol) {
        if (table === '/') return PRIMARY_SYMBOLS[symbol] || 'Unknown';
        return ALTERNATE_SYMBOLS[symbol] || 'Unknown';
    }

    function initLegend() {
        if (!map) return;
        const container = drawerSection('Legend', false);
        container.classList.add('map-legend');
        var txColor = themeColor('--route-tx-rf');
        var tcpipColor = themeColor('--route-tx-tcpip');
        var rxColor = themeColor('--route-rx');
        var trackColor = themeColor('--station-track');
        var routeColor = themeColor('--route-tcpip-line');
        container.innerHTML = `
                    <div class="legend-body">
                        <div class="legend-section-title">Animations</div>
                        <div class="legend-item">
                            <svg width="16" height="16" viewBox="0 0 16 16">
                                <path d="M 3 6 A 6 6 0 0 1 3 10" fill="none" stroke="${txColor}" stroke-width="1.5" stroke-linecap="round" stroke-opacity="0.8"/>
                                <path d="M 13 6 A 6 6 0 0 0 13 10" fill="none" stroke="${txColor}" stroke-width="1.5" stroke-linecap="round" stroke-opacity="0.8"/>
                            </svg>
                            <span>RF Transmit</span>
                        </div>
                        <div class="legend-item">
                            <svg width="16" height="16" viewBox="0 0 16 16">
                                <path d="M 3 6 A 6 6 0 0 1 3 10" fill="none" stroke="${tcpipColor}" stroke-width="1.5" stroke-linecap="round" stroke-opacity="0.8"/>
                                <path d="M 13 6 A 6 6 0 0 0 13 10" fill="none" stroke="${tcpipColor}" stroke-width="1.5" stroke-linecap="round" stroke-opacity="0.8"/>
                            </svg>
                            <span>Internet (TCPIP)</span>
                        </div>
                        <div class="legend-item">
                            <svg width="16" height="16" viewBox="0 0 16 16">
                                <path d="M 6 3 A 6 6 0 0 1 10 3" fill="none" stroke="${rxColor}" stroke-width="1.5" stroke-linecap="round" stroke-opacity="0.8"/>
                                <path d="M 6 13 A 6 6 0 0 0 10 13" fill="none" stroke="${rxColor}" stroke-width="1.5" stroke-linecap="round" stroke-opacity="0.8"/>
                            </svg>
                            <span>Receiving</span>
                        </div>
                        <div class="legend-section-title">Trails</div>
                        <div class="legend-item">
                            <div style="width:20px;height:3px;background:${trackColor};opacity:0.7;border-radius:2px;"></div>
                            <span>Movement trail</span>
                        </div>
                        <div class="legend-item">
                            <div style="width:20px;height:0;border-top:3px dashed ${routeColor};opacity:0.6;"></div>
                            <span>Packet route</span>
                        </div>
                        <div id="legend-wx">
                            <div class="legend-section-title">Weather stations</div>
                            <div id="legend-wx-body"></div>
                        </div>
                    </div>`;
        setWxLegendVisible(wxStationEnabled());
        initWxLegend();
    }

    /** Show/hide the "Weather stations" legend section with the Station plots toggle. */
    function setWxLegendVisible(show) {
        const el = document.getElementById('legend-wx');
        if (el) el.style.display = show ? '' : 'none';
    }

    /** Build a colour-scale bar (temp warmth or wind speed) from server hex
     *  stops — both scales live in the legend (issue #156 fixes 5 & 6). */
    function wxScaleBar(stops, label) {
        if (!stops || !stops.length) return '';
        const p = '<div style="display:flex;align-items:center;gap:5px;margin:3px 0;">';
        return p +
            '<div style="width:90px;flex-shrink:0;text-align:left;">' + label + '</div>' +
            '<div style="flex:1;height:8px;border-radius:3px;border:1px solid rgba(128,128,128,0.5);background:linear-gradient(90deg,' + stops.join(',') + ');"></div>' +
            '</div>';
    }

    /** Fill the weather-station legend from the server payload
     *  (station_model.legend_data): both colour scales, the sample model and
     *  the per-unit text lines — data-driven so legend and markers always
     *  agree on units and colours. */
    function initWxLegend() {
        fetch(API_BASE + '/weather/stations/legend').then(function (r) { return r.json(); }).then(function (data) {
            const body = document.getElementById('legend-wx-body');
            if (!body || !data || !data.temp_scale) return;
            const tempUnit = isImperial() ? '\u00B0F' : '\u00B0C';
            let html = wxScaleBar(data.temp_scale, 'Temp (' + tempUnit + ')');
            html += wxScaleBar(data.wind_scale, 'Wind (kt)');
            if (data.sample) {
                html += '<div style="margin:4px 0;text-align:center;">' +
                    drawStationModelCanvas(data.sample, 'K4ABC', 1).outerHTML + '</div>';
            }
            const lines = isImperial() ? data.lines_imperial : data.lines_metric;
            if (lines && lines.length) {
                html += '<div class="legend-item"><span>' + lines.join('<br>') + '</span></div>';
            }
            body.innerHTML = html;
            setWxLegendVisible(wxStationEnabled());
        }).catch(function () {});
    }

    // --- Stats Overlay ---
    var statsOverlayEl = null;

    function initStatsOverlay() {
        if (!map) return;
        var container = drawerSection('Stats', false);
        container.classList.add('stats-overlay');
        container.innerHTML =
            '<div class="stats-overlay-body">' +
            '<div class="stats-overlay-row"><span class="stats-label">Stations</span><span class="stats-value" id="stats-stations">0</span></div>' +
            '<div class="stats-overlay-row"><span class="stats-label">Packets</span><span class="stats-value" id="stats-packets">0</span></div>' +
            '<div class="stats-overlay-row"><span class="stats-label">Pkts/hr</span><span class="stats-value" id="stats-rate">0</span></div>' +
            '<div class="stats-overlay-row"><span class="stats-label">Uptime</span><span class="stats-value" id="stats-uptime">0s</span></div>' +
            '</div>';
        statsOverlayEl = container;
    }

    function updateStatsOverlay(stats) {
        var stations = stats.stations_active || 0;
        var packets = stats.packets_total || 0;
        var rate = stats.packets_last_hour || 0;
        var uptime = formatUptime(stats.uptime_seconds || 0);
        // One-line toolbar readout (#219): the always-visible summary.
        var readout = document.getElementById('stats-readout');
        if (readout) {
            readout.textContent = stations + ' stn · ' + packets + ' pkt · ' +
                rate + '/hr · ' + uptime;
        }
        if (!statsOverlayEl) return;
        var el;
        el = document.getElementById('stats-stations');
        if (el) el.textContent = stations;
        el = document.getElementById('stats-packets');
        if (el) el.textContent = packets;
        el = document.getElementById('stats-rate');
        if (el) el.textContent = rate;
        el = document.getElementById('stats-uptime');
        if (el) el.textContent = uptime;
    }

    function formatUptime(seconds) {
        if (seconds < 60) return seconds + 's';
        if (seconds < 3600) return Math.floor(seconds / 60) + 'm';
        var h = Math.floor(seconds / 3600);
        var m = Math.floor((seconds % 3600) / 60);
        if (h < 24) return h + 'h ' + m + 'm';
        var d = Math.floor(h / 24);
        h = h % 24;
        return d + 'd ' + h + 'h';
    }


    function initFilterOverlay() {
        if (!map) return;
        var container = drawerSection('Filters', true);
        container.classList.add('filter-overlay');
        container.innerHTML =
            '<div class="filter-overlay-body" id="filter-overlay-body">' +
            '<div class="filter-overlay-row">' +
                '<label class="filter-overlay-label" for="filter-callsign">Callsign</label>' +
                '<input type="text" id="filter-callsign" placeholder="Filter..." />' +
            '</div>' +
            '<div class="filter-overlay-row">' +
                '<label class="filter-overlay-label" for="filter-search">Search</label>' +
                '<input type="search" id="filter-search" placeholder="Search log..." />' +
            '</div>' +
            '<div class="filter-overlay-row">' +
                '<label class="filter-overlay-label" for="trail-duration">Trails</label>' +
                '<select id="trail-duration" title="Trail duration">' +
                    '<option value="1" selected>1h</option>' +
                    '<option value="2">2h</option>' +
                    '<option value="6">6h</option>' +
                    '<option value="24">24h</option>' +
                '</select>' +
            '</div>' +
            '<div class="filter-overlay-row">' +
                '<label class="filter-overlay-label" for="filter-type">Type</label>' +
                '<select id="filter-type">' +
                    '<option value="">All</option>' +
                    '<option value="GPSPacket">GPS</option>' +
                    '<option value="MessagePacket">Message</option>' +
                    '<option value="WeatherPacket">Weather</option>' +
                    '<option value="StatusPacket">Status</option>' +
                    '<option value="ObjectPacket">Object</option>' +
                    '<option value="TelemetryPacket">Telemetry</option>' +
                '</select>' +
            '</div>' +
            '<div class="filter-overlay-row">' +
                '<label class="filter-overlay-label" for="filter-txrx">TX/RX</label>' +
                '<select id="filter-txrx">' +
                    '<option value="">All</option>' +
                    '<option value="rx">RX</option>' +
                    '<option value="tx">TX</option>' +
                '</select>' +
            '</div>' +
            '<div class="filter-overlay-row">' +
                '<label class="filter-overlay-label" for="filter-station-model">Station plots</label>' +
                '<input type="checkbox" id="filter-station-model" ' +
                    (localStorage.getItem('dw-station-model') !== '0' ? 'checked ' : '') +
                    '/>' +
            '</div>' +
            '<div class="filter-overlay-row">' +
'<label class="filter-overlay-label" for="filter-packet-paths">Packet paths</label>' +
                '<input type="checkbox" id="filter-packet-paths" ' +
                    (packetPathsEnabled ? 'checked ' : '') +
                    '/>' +
            '</div>' +
            '<div class="filter-overlay-row">' +
                '<label class="filter-overlay-label" for="filter-follow-callsign">Follow</label>' +
                '<input type="text" id="filter-follow-callsign" placeholder="Follow callsign..." />' +
            '</div>' +
            '<div class="filter-overlay-row">' +
                '<label class="filter-overlay-label" for="filter-follow-mode">Follow mode</label>' +
                '<select id="filter-follow-mode" title="Dim or hide the other stations while following">' +
                    '<option value="dim" selected>Dim</option>' +
                    '<option value="hide">Hide</option>' +
                '</select>' +
            '</div>' +
            '</div>';
        // Station plots toggle: repaint models or fall back to sprites, and
        // show/hide the weather legend section (issue #152).
        const stationModelToggle = container.querySelector('#filter-station-model');
        if (stationModelToggle) {
            stationModelToggle.addEventListener('change', function () {
                localStorage.setItem('dw-station-model', this.checked ? '1' : '0');
                refreshStations();
                setWxLegendVisible(wxStationEnabled());
            });
        }
        // Packet paths toggle (#205): an explicit choice persists ('1'/'0') and
        // overrides the reduced-motion default on later loads.
        const packetPathsToggle = container.querySelector('#filter-packet-paths');
        if (packetPathsToggle) {
            packetPathsToggle.addEventListener('change', function () {
                localStorage.setItem('dw-packet-paths', this.checked ? '1' : '0');
                setPacketPathsEnabled(this.checked);
            });
        }
    }

    // --- My Location Overlay ---
    function initMyLocationOverlay() {
        if (!map) return;
        var container = drawerSection('My Location', false);
        container.classList.add('my-location-overlay');
        container.innerHTML =
            '<div class="my-location-actions-row">' +
                '<span class="my-location-btn my-location-center" title="Center map on my location">&#8982;</span>' +
                '<span class="my-location-btn my-location-clear hidden" title="Clear my location">&times;</span>' +
            '</div>' +
            '<div class="my-location-body" id="my-location-body">' +
                '<div class="my-location-type">Not set</div>' +
                '<div class="my-location-coords"></div>' +
            '</div>';
        // Wire up center button
        container.querySelector('.my-location-center').addEventListener('click', function () {
            var pos = getMyPosition();
            if (pos) {
                var zoom = config.station?.zoom || 12;
                mapFlyTo(pos.lat, pos.lng, toMlZoom(zoom));
            }
        });
        // Wire up clear button
        container.querySelector('.my-location-clear').addEventListener('click', function () {
            if (window._removeMyPosition) window._removeMyPosition();
        });
        myLocationOverlayEl = container;
        updateMyLocationOverlay();
    }

    function updateMyLocationOverlay() {
        if (!myLocationOverlayEl) return;
        var mp = config.station?.my_position;
        var typeEl = myLocationOverlayEl.querySelector('.my-location-type');
        var coordsEl = myLocationOverlayEl.querySelector('.my-location-coords');
        var clearBtn = myLocationOverlayEl.querySelector('.my-location-clear');
        var centerBtn = myLocationOverlayEl.querySelector('.my-location-center');

        if (!mp || !mp.type) {
            typeEl.textContent = 'Not set';
            coordsEl.textContent = '';
            clearBtn.classList.add('hidden');
            centerBtn.classList.add('hidden');
            return;
        }

        clearBtn.classList.remove('hidden');
        centerBtn.classList.remove('hidden');

        if (mp.type === 'station') {
            typeEl.textContent = mp.callsign || 'Station';
            var pos = getMyPosition();
            if (pos) {
                coordsEl.textContent = pos.lat.toFixed(4) + ', ' + pos.lng.toFixed(4);
            } else {
                coordsEl.textContent = 'Awaiting position...';
            }
        } else if (mp.type === 'pin') {
            typeEl.textContent = 'Dropped Pin';
            if (mp.latitude != null && mp.longitude != null) {
                coordsEl.textContent = mp.latitude.toFixed(4) + ', ' + mp.longitude.toFixed(4);
            } else {
                coordsEl.textContent = '';
            }
        } else {
            typeEl.textContent = 'Not set';
            coordsEl.textContent = '';
            clearBtn.classList.add('hidden');
            centerBtn.classList.add('hidden');
        }
    }

    // --- GPX Overlay (MapLibre GeoJSON source, issue #127) ---
    // Replaces the vendored leaflet-gpx plugin: the GPX text is parsed with
    // DOMParser into GeoJSON (tracks, routes, waypoints, start/end points) and
    // drawn with line + circle layers styled like the Leaflet version.
    const GPX_EMPTY_FC = { type: 'FeatureCollection', features: [] };

    function _gpxPoints(nodes) {
        var pts = [];
        for (var i = 0; i < nodes.length; i++) {
            var lat = parseFloat(nodes[i].getAttribute('lat'));
            var lon = parseFloat(nodes[i].getAttribute('lon'));
            if (isFinite(lat) && isFinite(lon)) pts.push([lon, lat]);
        }
        return pts;
    }

    function _gpxName(el) {
        var n = el.getElementsByTagName('name')[0];
        return n ? n.textContent.trim() : '';
    }

    /** Parse GPX XML text into { fc, bounds, distanceKm, nTrk, nWpt }.
     *  Throws on unparseable XML. bounds is [[minLng,minLat],[maxLng,maxLat]]
     *  or null when there is no geometry. */
    function parseGpxToGeoJSON(gpxText) {
        var doc = new DOMParser().parseFromString(gpxText, 'application/xml');
        if (doc.getElementsByTagName('parsererror').length) throw new Error('Invalid GPX');
        var features = [];
        var lines = [];
        var trks = doc.getElementsByTagName('trk');
        for (var t = 0; t < trks.length; t++) {
            var segs = trks[t].getElementsByTagName('trkseg');
            for (var g = 0; g < segs.length; g++) {
                var pts = _gpxPoints(segs[g].getElementsByTagName('trkpt'));
                if (pts.length >= 2) lines.push({ kind: 'track', name: _gpxName(trks[t]), pts: pts });
            }
        }
        var rtes = doc.getElementsByTagName('rte');
        for (var r = 0; r < rtes.length; r++) {
            var rp = _gpxPoints(rtes[r].getElementsByTagName('rtept'));
            if (rp.length >= 2) lines.push({ kind: 'route', name: _gpxName(rtes[r]), pts: rp });
        }
        var distanceKm = 0;
        lines.forEach(function (l) {
            features.push({ type: 'Feature', properties: { kind: l.kind, name: l.name },
                geometry: { type: 'LineString', coordinates: l.pts } });
            for (var i = 1; i < l.pts.length; i++) {
                distanceKm += haversineKm(l.pts[i - 1][1], l.pts[i - 1][0], l.pts[i][1], l.pts[i][0]);
            }
        });
        var wpts = doc.getElementsByTagName('wpt');
        var nWpt = 0;
        for (var w = 0; w < wpts.length; w++) {
            var wp = _gpxPoints([wpts[w]]);
            if (!wp.length) continue;
            nWpt++;
            features.push({ type: 'Feature', properties: { kind: 'wpt', name: _gpxName(wpts[w]) },
                geometry: { type: 'Point', coordinates: wp[0] } });
        }
        if (lines.length) {
            // leaflet-gpx start/end markers: first point of the first line,
            // last point of the last line.
            var last = lines[lines.length - 1].pts;
            features.push({ type: 'Feature', properties: { kind: 'start' },
                geometry: { type: 'Point', coordinates: lines[0].pts[0] } });
            features.push({ type: 'Feature', properties: { kind: 'end' },
                geometry: { type: 'Point', coordinates: last[last.length - 1] } });
        }
        var bounds = null;
        features.forEach(function (f) {
            var cs = f.geometry.type === 'Point' ? [f.geometry.coordinates] : f.geometry.coordinates;
            cs.forEach(function (c) {
                if (!bounds) bounds = [[c[0], c[1]], [c[0], c[1]]];
                bounds[0][0] = Math.min(bounds[0][0], c[0]); bounds[0][1] = Math.min(bounds[0][1], c[1]);
                bounds[1][0] = Math.max(bounds[1][0], c[0]); bounds[1][1] = Math.max(bounds[1][1], c[1]);
            });
        });
        return { fc: { type: 'FeatureCollection', features: features }, bounds: bounds,
            distanceKm: distanceKm, nTrk: lines.length, nWpt: nWpt };
    }

    function fitGpxBounds(bounds) {
        if (!map || !bounds) return;
        map.fitBounds(bounds, { padding: 40, maxZoom: 16, duration: 0 });
    }

    /** Add the GPX source + layers (idempotent; re-run after style.load). */
    function addGpxLayers() {
        if (!mainMapIsMaplibre || !map || map.getSource('gpx')) return;
        map.addSource('gpx', { type: 'geojson', data: (gpxLayer && gpxLayer.fc) || GPX_EMPTY_FC });
        var before = map.getLayer('stations-symbol-base') ? 'stations-symbol-base' : undefined;
        map.addLayer({
            id: 'gpx-track', type: 'line', source: 'gpx',
            filter: ['==', ['get', 'kind'], 'track'],
            layout: { 'line-cap': 'round', 'line-join': 'round' },
            paint: { 'line-color': themeColor('--gpx-accent'), 'line-opacity': 0.8, 'line-width': 3 },
        }, before);
        map.addLayer({
            id: 'gpx-route', type: 'line', source: 'gpx',
            filter: ['==', ['get', 'kind'], 'route'],
            layout: { 'line-cap': 'round', 'line-join': 'round' },
            paint: { 'line-color': themeColor('--gpx-accent'), 'line-opacity': 0.8, 'line-width': 2,
                'line-dasharray': [4, 3] },
        }, before);
        map.addLayer({
            id: 'gpx-points', type: 'circle', source: 'gpx',
            filter: ['in', ['get', 'kind'], ['literal', ['wpt', 'start', 'end']]],
            paint: {
                'circle-radius': ['match', ['get', 'kind'], 'wpt', 5, 6],
                'circle-color': ['match', ['get', 'kind'],
                    'start', themeColor('--gpx-start'),
                    'end', themeColor('--gpx-end'),
                    themeColor('--gpx-waypoint')],
                'circle-stroke-color': '#ffffff',
                'circle-stroke-width': 1.5,
            },
        }, before);
    }

    function setGpxData(fc) {
        if (map && map.getSource && map.getSource('gpx')) map.getSource('gpx').setData(fc || GPX_EMPTY_FC);
    }

    function initGpxOverlay() {
        if (!mainMapIsMaplibre || !map) return;
        var gpxFileInput = document.createElement('input');
        gpxFileInput.type = 'file';
        gpxFileInput.accept = '.gpx,.xml';
        gpxFileInput.style.display = 'none';
        document.body.appendChild(gpxFileInput);

        var controlEl = drawerSection('GPX Overlay', false);
        controlEl.classList.add('gpx-control');
        controlEl.innerHTML =
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

        controlEl.querySelector('.gpx-btn-load').addEventListener('click', function () {
            gpxFileInput.click();
        });
        controlEl.querySelector('.gpx-btn-fit').addEventListener('click', function () {
            if (gpxLayer && gpxLayer.bounds) {
                fitGpxBounds(gpxLayer.bounds);
            }
        });
        controlEl.querySelector('.gpx-btn-clear').addEventListener('click', function () {
            clearGpxOverlay(controlEl);
        });

        gpxControlEl = controlEl;
        map.on('style.load', addGpxLayers);
        if (map.isStyleLoaded && map.isStyleLoaded()) addGpxLayers();

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
        gpxLayer = null;
        setGpxData(GPX_EMPTY_FC);
        var errorEl = controlEl.querySelector('.gpx-error');
        errorEl.style.display = 'none';

        var gpx;
        try {
            gpx = parseGpxToGeoJSON(gpxText);
        } catch (e) {
            showGpxError(controlEl, 'Invalid GPX file');
            return;
        }
        if (!gpx.fc.features.length) {
            showGpxError(controlEl, 'No tracks or waypoints found');
            return;
        }
        gpxLayer = gpx;
        addGpxLayers();
        setGpxData(gpx.fc);

        // Show loaded state
        var truncName = filename.length > 20 ? filename.substring(0, 17) + '...' : filename;
        controlEl.querySelector('.gpx-filename').textContent = truncName;
        controlEl.querySelector('.gpx-filename').title = filename;
        var parts = [gpx.distanceKm.toFixed(1) + ' km'];
        if (gpx.nTrk > 0) parts.push(gpx.nTrk + ' trk');
        if (gpx.nWpt > 0) parts.push(gpx.nWpt + ' wpt');
        controlEl.querySelector('.gpx-info').textContent = parts.join(' \u00B7 ');
        controlEl.querySelector('.gpx-empty').style.display = 'none';
        controlEl.querySelector('.gpx-loaded').style.display = '';

        updateLegendGpx(true);

        try {
            localStorage.setItem('gpx_overlay', JSON.stringify({ filename: filename, data: gpxText }));
        } catch (e) {
            if (e.name === 'QuotaExceededError') {
                showGpxError(controlEl, 'Too large to save \u2014 won\u2019t persist on reload');
            }
        }

        fitGpxBounds(gpx.bounds);
    }

    function clearGpxOverlay(controlEl) {
        gpxLayer = null;
        setGpxData(GPX_EMPTY_FC);
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

    function updateLegendGpx(show) {
        var legend = document.querySelector('.map-legend .legend-body');
        if (!legend) return;
        var existing = legend.querySelector('.legend-gpx-section');
        if (show && !existing) {
            var gpxColor = themeColor('--gpx-accent');
            var wptColor = themeColor('--gpx-waypoint');
            var section = document.createElement('div');
            section.className = 'legend-gpx-section';
            section.innerHTML =
                '<div class="legend-section-title">GPX Overlay</div>' +
                '<div class="legend-item">' +
                    '<div style="width:20px;height:3px;background:' + gpxColor + ';border-radius:2px;"></div>' +
                    '<span>GPX track</span>' +
                '</div>' +
                '<div class="legend-item">' +
                    '<div style="width:20px;height:0;border-top:3px dashed ' + gpxColor + ';opacity:0.8;"></div>' +
                    '<span>GPX route</span>' +
                '</div>' +
                '<div class="legend-item">' +
                    '<svg width="10" height="10"><circle cx="5" cy="5" r="4" fill="' + wptColor + '" stroke="white" stroke-width="1"/></svg>' +
                    '<span>GPX waypoint</span>' +
                '</div>';
            legend.appendChild(section);
        } else if (!show && existing) {
            existing.remove();
        }
    }

    // --- Init ---
    document.addEventListener('DOMContentLoaded', async () => {
        // Apply saved log color overrides immediately
        _applyLogColors(_getStoredLogColors());

        // Init theme toggle
        var themeBtn = document.getElementById('btn-theme');
        if (themeBtn) themeBtn.addEventListener('click', toggleTheme);
        updateThemeIcon();

        // Init decode modal immediately (doesn't depend on config/network)
        initDecode();
        initWeatherModal();
        initAboutModal();
        initChangelogModal();
        initStationListModal();
        initWeatherPanelModal();
        initMessagesPanelModal();
        initKeyboardHelpModal();
        await loadConfig();
        try {
            await initMap();
        } catch (e) {
            // A basemap/style failure must not stop the rest of the dashboard
            // (websocket, filters, settings) from booting (#125 review).
            console.error('Map init failed:', e);
            const mapEl = document.getElementById('map');
            if (mapEl) mapEl.innerHTML = '<div class="map-webgl-message">The map failed to load.</div>';
        }
        initCenterFab();
        initLegend();
        initStatsOverlay();
        initFilterOverlay();
        initMyLocationOverlay();
        initGpxOverlay();
        await loadStations();
        loadStationPositions();
        loadTracks();
        await initMapCenter();
        updateMyPositionMarker();
        // Load initial stats for overlay before WebSocket connects
        fetch(API_BASE + '/stats').then(function (r) { return r.json(); }).then(updateStatsOverlay).catch(function () {});
        connectWebSocket();
        initFilters();
        initFollowStation();
        initSettings();
        initConfirmWipeModal();
        initClearDataFab();
        initDropPin();
        initMapResize();
        initLogToggle();
    });

    // --- Config ---
    async function loadConfig() {
        try {
            const resp = await fetch(API_BASE + '/config');
            config = await resp.json();
            if (config.version) {
                const el = document.getElementById('app-version');
                if (el) el.textContent = config.version;
            }
            // Apply display settings
            if (config.display != null) {
                showRouteDistances = config.display.show_route_distances !== false;
                showGpxOverlay = config.display.show_gpx_overlay !== false;
                showStatsOverlay = config.display.show_stats_overlay !== false;
                showMyLocationOverlay = config.display.show_my_location_overlay !== false;
            }
            if (config.packet_log != null) {
                showTimestamps = config.packet_log.show_timestamps === true;
            }
        } catch (e) {
            console.error('Failed to load config:', e);
        }
    }

    // --- Map (MapLibre main map, issue #125) ---
    async function initMap() {
        const container = document.getElementById('map');
        if (!container) return;

        // The main map never falls back to Leaflet (#125): Leaflet-only main-map
        // code paths are dead and guarded on this flag, even when WebGL2 fails.
        mainMapIsMaplibre = true;

        // WebGL2 gate: no legacy fallback, just a plain message (#125)
        const probe = document.createElement('canvas');
        if (!probe.getContext('webgl2')) {
            container.innerHTML = '<div class="map-webgl-message">The interactive map needs WebGL2, which this browser does not provide. Enable WebGL2 (or use a browser that supports it) to see the map.</div>';
            return;
        }
        if (typeof maplibregl === 'undefined' || typeof pmtiles === 'undefined') {
            container.innerHTML = '<div class="map-webgl-message">The map libraries failed to load.</div>';
            return;
        }

        // Register the pmtiles protocol (region archive over HTTP Range)
        const protocol = new pmtiles.Protocol();
        maplibregl.addProtocol('pmtiles', protocol.tile);

        const lat = config.station?.latitude || 0;
        const lon = config.station?.longitude || 0;
        const zoom = config.station?.zoom || 12;

        // Shareable view (#150): read #zoom/lat/lon before anything rewrites it.
        urlMapView = parseMapHash(window.location.hash);

        map = new maplibregl.Map({
            container: 'map',
            style: await fetchStyleForTheme(_currentTheme),
            center: urlMapView ? [urlMapView.lon, urlMapView.lat] : [lon, lat],
            zoom: urlMapView ? urlMapView.zoom : toMlZoom(zoom),
            bearing: urlMapView ? urlMapView.bearing : 0,
            pitch: urlMapView ? urlMapView.pitch : 0,
            zoomSnap: 0.1,  // issue #154: fractional handoff zooms (10.9 etc.)
        });
        map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-left');

        // Keep the URL hash in the haminfo /map format (#zoom/lat/lon) so a
        // copied link reopens this exact view (#150).
        map.on('moveend', function () {
            const h = formatMapHash(map.getZoom(), map.getCenter().lat, map.getCenter().lng,
                map.getBearing(), map.getPitch());
            if (window.location.hash !== h) history.replaceState(null, '', h);
        });
        window.addEventListener('hashchange', function () {
            const v = parseMapHash(window.location.hash);
            if (v) map.jumpTo({ center: [v.lon, v.lat], zoom: v.zoom, bearing: v.bearing, pitch: v.pitch });
        });

        // Persist map center across page refreshes (zoom is always from config)
        map.on('moveend', function () {
            const c = map.getCenter();
            localStorage.setItem('dw-map-lat', c.lat);
            localStorage.setItem('dw-map-lng', c.lng);
        });

        // (Re-)add the station source + layers after every style load, including
        // a structural theme swap; the paint-only path (#201) leaves them alone.
        map.on('style.load', addStationLayers);
        map.on('style.load', addOverlaySources);
        map.on('style.load', addPacketPathLayers);
        map.on('resize', measurePacketPathChrome);
        // First paint needs real width/chrome; until now they stayed 0 until
        // some resize fired (#209 review finding 2).
        measurePacketPathChrome();

        // Station click -> popup (same HTML the Leaflet popups used).
        map.on('click', 'stations-symbol-base', onStationLayerClick);
        map.on('click', 'stations-symbol-overlay', onStationLayerClick);
        map.on('click', 'stations-label', onStationLayerClick);
        map.on('mouseenter', 'stations-symbol-base', function () { map.getCanvas().style.cursor = 'pointer'; });
        map.on('mouseleave', 'stations-symbol-base', function () { map.getCanvas().style.cursor = ''; });

        setInterval(cleanupAnimations, 10000);
    }

    /** Rewrite the style's sprite (string or array of {id,url}) and glyphs
     *  URLs to be absolute against `base` (MapLibre rejects relative sprite
     *  URLs, issue #135). new URL percent-encodes { } so the glyphs template
     *  placeholders ({fontstack}/{range}) are restored to keep them usable. */
    function absolutizeStyleUrls(style, base) {
        function abs(u) {
            return new URL(u, base).href
                .replace(/%7Bfontstack%7D/g, '{fontstack}')
                .replace(/%7Brange%7D/g, '{range}');
        }
        if (Array.isArray(style.sprite)) {
            style.sprite.forEach(function (s) { if (s && s.url) s.url = abs(s.url); });
        } else if (typeof style.sprite === 'string') {
            style.sprite = abs(style.sprite);
        }
        if (style.glyphs) style.glyphs = abs(style.glyphs);
        return style;
    }

    /** Fetch the basemap style for a theme, pointing the protomaps source at
     *  the served region archive URL (issue #132: region.pmtiles under the
     *  /basemap mount, with HTTP Range — not the hardcoded sample path). */
    async function fetchStyleForTheme(theme) {
        const name = theme === 'light' ? 'light' : 'dark';
        // issue #154: the served style is split server-side (local z0-11 +
        // same-origin proxied z12+); every vector source URL the server baked
        // in is browser-relative, so absolutize them as pmtiles:// URLs.
        const resp = await fetch('/basemap/style/' + name + '.json');
        const style = await resp.json();
        if (style.sources && style.sources.protomaps) {
            style.sources.protomaps.url = 'pmtiles://' + new URL(style.sources.protomaps.url, window.location.href).href;
        }
        if (style.sources) {
            Object.keys(style.sources).forEach(function (id) {
                const s = style.sources[id];
                if (s && s.type === 'vector' && s.url && s.url.indexOf('pmtiles://') !== 0) {
                    s.url = 'pmtiles://' + new URL(s.url, window.location.href).href;
                }
            });
        }
        addWorldFallback(style);
        return absolutizeStyleUrls(style, window.location.href);
    }

    /** World fallback (#144): a vendored z0-4 planet extract drawn, overzoomed,
     *  under the region's fills/lines so panning past the region archive's
     *  bounds shows coarse land/water/roads instead of empty background.
     *  Labels stay region-only (no doubled text). */
    const WORLD_TILES_URL = '/static/basemap/regions/world.pmtiles';
    function addWorldFallback(style) {
        if (!style.sources || !style.sources.protomaps || !Array.isArray(style.layers)) return;
        style.sources.world = {
            type: 'vector',
            url: 'pmtiles://' + new URL(WORLD_TILES_URL, window.location.href).href,
        };
        const worldLayers = style.layers
            .filter(function (l) { return l.source === 'protomaps' && (l.type === 'fill' || l.type === 'line'); })
            .map(function (l) { return Object.assign({}, l, { id: 'world-' + l.id, source: 'world' }); });
        const bg = style.layers.findIndex(function (l) { return l.type === 'background'; });
        style.layers.splice(bg + 1, 0, ...worldLayers);
    }

    /** One GeoJSON source + symbol layers for all stations:
     *  base APRS icon (aprs:aprs-p-XX / aprs:aprs-a-XX), optional overlay
     *  icon (aprs:aprs-o-YY) stacked on top, and a callsign text label. */
    function addStationLayers() {
        if (!mainMapIsMaplibre || !map) return;
        if (map.getSource('stations')) return;

        map.addSource('stations', { type: 'geojson', data: stationFeatureCollection });

        // My-station glow under the icon (Leaflet .my-station drop-shadow,
        // issue #133): two blurred circles in --my-station-glow.
        map.addLayer({
            id: 'stations-my-glow',
            type: 'circle',
            source: 'stations',
            filter: ['==', ['get', 'is_my'], true],
            paint: {
                'circle-radius': 18,
                'circle-color': themeColor('--my-station-glow'),
                'circle-opacity': 0.55,
                'circle-blur': 0.8,
            },
        });
        map.addLayer({
            id: 'stations-my-glow-core',
            type: 'circle',
            source: 'stations',
            filter: ['==', ['get', 'is_my'], true],
            paint: {
                'circle-radius': 14,
                'circle-color': themeColor('--my-station-glow'),
                'circle-opacity': 0.6,
                'circle-blur': 0.5,
            },
        });

        map.addLayer({
            id: 'stations-symbol-base',
            type: 'symbol',
            source: 'stations',
            layout: {
                'icon-image': [
                    'case',
                    ['has', 'wx_model'], ['get', 'wx_model'],
                    ['==', ['get', 'symbol_table'], '/'],
                    ['concat', 'aprs:aprs-p-', ['get', 'symbol_hex']],
                    ['concat', 'aprs:aprs-a-', ['get', 'symbol_hex']],
                ],
                'icon-size': 1,
                'icon-allow-overlap': true,
                'icon-ignore-placement': true,
            },
        });
        map.addLayer({
            id: 'stations-symbol-overlay',
            type: 'symbol',
            source: 'stations',
            // ['has','overlay_hex'] matches explicit null values too (#135);
            // require a non-null overlay (property is omitted when absent).
            filter: ['!=', ['get', 'overlay_hex'], null],
            layout: {
                'icon-image': ['concat', 'aprs:aprs-o-', ['get', 'overlay_hex']],
                'icon-size': 1,
                'icon-allow-overlap': true,
                'icon-ignore-placement': true,
            },
        });
        map.addLayer({
            id: 'stations-label',
            type: 'symbol',
            source: 'stations',
            layout: {
                'text-field': ['get', 'callsign'],
                // Leaflet .station-label (#133): bold, right of the 24 px
                // icon (left edge 26 px from the icon's left), centered.
                'text-font': ['NotoSansMedium'],
                'text-size': 11,
                'text-anchor': 'left',
                'text-offset': [1.27, 0],
                'text-allow-overlap': true,
                'text-ignore-placement': true,
            },
            paint: {
                'text-color': themeColor('--text-primary'),
                'text-halo-color': themeColor('--label-shadow'),
                'text-halo-width': 1.5,
                'text-halo-blur': 0,
            },
        });

        // Runtime images are dropped when the style reloads; re-register the
        // cached WX plots so station icons survive a theme switch (#152).
        reRegisterWxStationImages();

        scheduleStationRefresh();
    }

    // --- Dynamic GeoJSON line sources (tracks, popup overlay, tx arcs) ---
    // Added below the station symbol layers; re-added after every style.load (a
    // full style replace, including a structural theme swap). Their data is
    // stored in JS state, not the map.
    function addOverlaySources() {
        if (!mainMapIsMaplibre || !map) return;

        if (!map.getSource('station-tracks')) {
            map.addSource('station-tracks', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
            map.addLayer({
                id: 'station-tracks-line',
                type: 'line',
                source: 'station-tracks',
                layout: { 'line-cap': 'round', 'line-join': 'round' },
                paint: {
                    'line-color': themeColor('--station-track'),
                    'line-width': 1.5,
                    'line-opacity': 0.6,
                },
            }, 'stations-symbol-base');
        }

        // style.load drops the hover layer like every other layer here; re-light
        // the still-hovered trail in this re-add pass (same idiom as tx-arcs).
        if (hoveredTrailCallsign) highlightTrail(hoveredTrailCallsign);

        if (!_trailHoverBound) {
            _trailHoverBound = true;
            map.on('mousemove', function (e) {
                setTrailHover(pickTrailHit(map, e.point));
            });
            map.getCanvas().addEventListener('mouseleave', function () {
                setTrailHover(null);
            });
            // Click opens the panel through the SAME 8 px hit test the hover
            // uses, so no prior hover state is needed (port decision 5).
            map.on('click', function (e) {
                // A station marker owns its own click (it opens the station
                // popup); a station usually sits at the end of its own trail,
                // inside the trail's 8 px band.
                if (map.getLayer('stations-symbol-base') &&
                    map.queryRenderedFeatures(e.point, {
                        layers: ['stations-symbol-base', 'stations-symbol-overlay'],
                    }).length) return;
                const hit = pickTrailHit(map, e.point);
                if (hit && hit.properties && hit.properties.callsign) {
                    openTrailPanel(hit.properties.callsign);
                    return;
                }
                // A click on bare map closes the panel (spec rule 5), decided
                // by the same hit test so a trail click is never bare.
                closeTrailPanel();
            });
            // The map itself resizes (log panel, window, splitter), so a panel
            // anchored to a trail has to move with it.
            map.on('resize', placeTrailPanel);
        }

        if (!map.getSource('station-overlay')) {
            map.addSource('station-overlay', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
            map.addLayer({
                id: 'station-overlay-path',
                type: 'line',
                source: 'station-overlay',
                filter: ['==', ['get', 'kind'], 'path'],
                layout: { 'line-cap': 'round', 'line-join': 'round' },
                paint: {
                    'line-color': themeColor('--path-overlay-color') || '#ff9500',
                    'line-width': 2.5,
                    'line-opacity': 0.85,
                    'line-dasharray': [6, 4],
                },
            }, 'stations-symbol-base');
            map.addLayer({
                id: 'station-overlay-distance',
                type: 'line',
                source: 'station-overlay',
                filter: ['==', ['get', 'kind'], 'distance'],
                layout: { 'line-cap': 'round', 'line-join': 'round' },
                paint: {
                    'line-color': themeColor('--distance-line-color') || '#00bfff',
                    'line-width': 1.5,
                    'line-opacity': 0.7,
                    'line-dasharray': [4, 8],
                },
            }, 'stations-symbol-base');
        }

        if (!map.getLayer('station-overlay-hops')) {
            // Hop dots (Leaflet circleMarker r=5, #ff9500) with hover tooltips (#134).
            map.addLayer({
                id: 'station-overlay-hops',
                type: 'circle',
                source: 'station-overlay',
                filter: ['==', ['get', 'kind'], 'hop'],
                paint: {
                    'circle-radius': 5,
                    'circle-color': '#ff9500',
                    'circle-opacity': 0.9,
                    'circle-stroke-color': '#ff9500',
                    'circle-stroke-width': 1.5,
                },
            }, 'stations-symbol-base');
        }
        if (!_hopHoverBound) {
            _hopHoverBound = true;
            map.on('mouseenter', 'station-overlay-hops', onHopEnter);
            map.on('mouseleave', 'station-overlay-hops', onHopLeave);
        }

        if (!map.getSource('tx-arcs')) {
            map.addSource('tx-arcs', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
            map.addLayer({
                id: 'tx-arcs-line',
                type: 'line',
                source: 'tx-arcs',
                layout: { 'line-cap': 'round', 'line-join': 'round' },
                paint: {
                    'line-color': themeColor('--route-tcpip-line'),
                    'line-width': 3,
                    'line-opacity': 0.6,
                    'line-dasharray': [4, 8],
                },
            }, 'stations-symbol-base');
        }

        // Repopulate from persisted state (data survives the style reload).
        refreshStationTrackSource();
        refreshOverlaySource();
        refreshArcSource();
    }

    function refreshStationTrackSource() {
        if (!map || !map.getSource || !map.getSource('station-tracks')) return;
        const features = [];
        for (const [cs, pts] of Object.entries(stationTracks)) {
            if (!pts || pts.length < 2) continue;
            // Hide tracks of stations filtered out (same predicate as stations).
            if (!stationVisibleByFilters(cs, stations[cs])) continue;
            features.push({
                type: 'Feature',
                properties: { callsign: cs },
                // stored [lat, lng] -> GeoJSON [lng, lat]
                geometry: { type: 'LineString', coordinates: pts.map(p => [p[1], p[0]]) },
            });
        }
        map.getSource('station-tracks').setData({ type: 'FeatureCollection', features });
    }

    // --- Trail hover highlight (issue #207, port decision 3) ---
    // Hover is a mousemove + 8 px box query, not mouseenter on the layer: MapLibre's
    // point query has zero tolerance and the base line is 1.5 px wide, so the spec's
    // 8 px screen tolerance lives in the query box. station-tracks keeps ONE LineString
    // feature per station (refreshStationTrackSource), so a hit feature is the whole
    // trail and one highlight layer lights the WHOLE trail up.
    const TRAIL_HOVER_TOLERANCE_PX = 8;  // spec: hover box around the pointer
    var hoveredTrailCallsign = null;
    var _trailHoverBound = false;

    // Hit test shared with the future click wiring (task 4). Several trails
    // inside the box compete at low zoom: nearest wins, like the reference.
    function pickTrailHit(map, point) {
        if (!map || !point || !map.getLayer('station-tracks-line')) return null;
        const t = TRAIL_HOVER_TOLERANCE_PX;
        const hits = map.queryRenderedFeatures(
            [[point.x - t, point.y - t], [point.x + t, point.y + t]],
            {layers: ['station-tracks-line']});
        if (!hits.length) return null;
        if (hits.length === 1) return hits[0];
        let best = null;
        let bestDist = Infinity;
        for (const f of hits) {
            const coords = f.geometry && f.geometry.coordinates;
            if (!coords) continue;
            let d = Infinity;
            for (let i = 1; i < coords.length; i++) {
                const a = map.project(coords[i - 1]);
                const b = map.project(coords[i]);
                d = Math.min(d, distPointToSegment(point, a, b));
            }
            if (d < bestDist) { bestDist = d; best = f; }
        }
        return best || hits[0];
    }

    // Distance from a screen point to a projected segment (nearest-wins tie-up).
    function distPointToSegment(p, a, b) {
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const len2 = dx * dx + dy * dy;
        if (!len2) return Math.hypot(p.x - a.x, p.y - a.y);
        const t = Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / len2));
        return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy));
    }

    // A wide halo line on the SAME source, filtered to the hovered callsign:
    // the geometry can never drift from the trail it describes. Added on hit
    // and removed on miss, drawn under station-tracks-line like the reference
    // halo under its trail.
    function highlightTrail(callsign) {
        if (!map || !map.getSource('station-tracks')) return;
        if (map.getLayer('station-tracks-highlight')) {
            // Bracket call: tests/test_no_leaflet.py bans the literal dotted
            // form as a retired-Leaflet fingerprint; MapLibre needs this call.
            map['removeLayer']('station-tracks-highlight');
        }
        if (!callsign) return;
        map.addLayer({
            id: 'station-tracks-highlight',
            type: 'line',
            source: 'station-tracks',
            filter: ['==', ['get', 'callsign'], callsign],
            layout: { 'line-cap': 'round', 'line-join': 'round' },
            paint: {
                'line-color': themeColor('--accent') || '#60a5fa',
                'line-width': 5.5,    // base 1.5 + 4, mirrors reference hover width
                'line-opacity': 0.55,
            },
        }, 'station-tracks-line');
    }

    // Cursor: the brief wants 'move' while a trail is hovered (the reference
    // uses 'pointer'); cleared on miss so the map's default cursor returns.
    function setTrailHover(feature) {
        const callsign = feature && feature.properties ? feature.properties.callsign : null;
        if (callsign === hoveredTrailCallsign) return;
        hoveredTrailCallsign = callsign;
        highlightTrail(callsign);
        map.getCanvas().style.cursor = callsign ? 'move' : '';
    }

    // --- Trail statistics (shared spec basemap-kit#65, issue #207) ---
    /* Engine-agnostic stats maths (no map, no DOM), ported one-for-one from
     * the reference map-shared/trail-stats.js. The maths is the canonical
     * spec, not a local approximation: it must reproduce the spec's worked
     * example exactly (tests/test_trail_stats.py mirrors the reference
     * trail-stats test). Input rows are the trail-endpoint shape, oldest
     * first: [longitude, latitude, timestamp, speed, course], with speed in
     * km/h and course in degrees; either may be null. This app's track rows
     * carry neither, so the derived branches below are the common case. */
    const EARTH_RADIUS_KM = 6371.0088;   // spec rule 4
    const SAME_POSITION_DEG = 0.00001;   // spec rule 2
    const GAP_MIN_SECONDS = 300;         // spec rule 11
    const GAP_MEDIAN_FACTOR = 3;         // spec rule 11
    const BAD_FIX_KMH = 1000;            // spec rule 12 (bad fixes)
    const KM_PER_MILE = 1.609344;

    function toRad(deg) {
        return deg * Math.PI / 180;
    }

    // Great-circle distance in km, haversine (spec radius 6371.0088, atan2).
    // Self-contained on purpose: the node harnesses extract this function in
    // isolation, so no toRad/EARTH_RADIUS_KM references may leak in.
    function haversineKm(lat1, lon1, lat2, lon2) {
        const dLat = (lat2 - lat1) * Math.PI / 180;
        const dLon = (lon2 - lon1) * Math.PI / 180;
        const a = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
            Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
            Math.sin(dLon / 2) * Math.sin(dLon / 2);
        return 6371.0088 * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
    }

    // Initial bearing in whole-ish degrees, 0-360, north = 0.
    function bearingDeg(lat1, lon1, lat2, lon2) {
        const dLon = toRad(lon2 - lon1);
        const y = Math.sin(dLon) * Math.cos(toRad(lat2));
        const x = Math.cos(toRad(lat1)) * Math.sin(toRad(lat2)) -
            Math.sin(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.cos(dLon);
        return (Math.atan2(y, x) * 180 / Math.PI + 360) % 360;
    }

    function numberOf(value) {
        return typeof value === 'number' && isFinite(value) ? value : null;
    }

    function timeMs(timestamp) {
        // Track rows carry epoch SECONDS (the app reads them * 1000 elsewhere);
        // the spec's own fixtures are ISO strings, which Date parses directly.
        const value = typeof timestamp === 'number' ? timestamp * 1000 : timestamp;
        const t = new Date(value).getTime();
        return isFinite(t) ? t : null;
    }

    /**
     * Rules 1-3: keep only usable positions, merge duplicate hears, correct
     * same-timestamp disagreements, and sort by time.
     */
    function normalise(points) {
        const usable = [];
        (points || []).forEach(function (p) {
            if (!p) return;
            const lon = numberOf(p[0]);
            const lat = numberOf(p[1]);
            const t = timeMs(p[2]);
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

        const out = [];
        usable.forEach(function (p) {
            const prev = out[out.length - 1];
            if (prev && prev.t === p.t) {
                const samePlace =
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
        const sorted = values.slice().sort(function (a, b) { return a - b; });
        const mid = Math.floor(sorted.length / 2);
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
     * degrees); formatting is a separate concern so display rounding can
     * never leak into the maths.
     */
    function compute(points, options) {
        const opts = options || {};
        const rows = normalise(points);
        const result = {
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

        const segments = [];
        for (let i = 1; i < rows.length; i++) {
            const a = rows[i - 1];
            const b = rows[i];
            const distKm = haversineKm(a.lat, a.lon, b.lat, b.lon);
            const seconds = (b.t - a.t) / 1000;
            segments.push({
                distKm: distKm,
                seconds: seconds,
                derivedKph: seconds > 0 ? distKm / (seconds / 3600) : null,
                bad: seconds > 0 && (distKm / (seconds / 3600)) > BAD_FIX_KMH,
                gap: false,     // filled in below, once the median is known
            });
        }

        // Rule 11: a gap is longer than the larger of three times the median
        // interval and 300 seconds. The boundary is included: the spec's
        // worked example calls its 300 s interval a gap and expects 4
        // beacon intervals.
        const intervals = segments.map(function (s) { return s.seconds; });
        const medInterval = median(intervals);
        const gapThreshold = Math.max(GAP_MEDIAN_FACTOR * (medInterval || 0), GAP_MIN_SECONDS);
        segments.forEach(function (s) {
            s.gap = s.seconds >= gapThreshold;
        });

        // Rule 12: a bad fix is excluded from distance and from maximum
        // speed and is reported, never silently dropped.
        result.badFixes = segments.filter(function (s) { return s.bad; }).length;

        result.elapsedSeconds = (rows[rows.length - 1].t - rows[0].t) / 1000;
        result.distanceKm = segments.reduce(function (sum, s) {
            return sum + (s.bad ? 0 : s.distKm);
        }, 0);
        result.averageKph = result.elapsedSeconds > 0
            ? result.distanceKm / (result.elapsedSeconds / 3600)
            : null;

        // Rule 8: reported speed on the newest point, else the last
        // segment's derived speed, never a number from a gap or a bad fix.
        const newest = rows[rows.length - 1];
        const lastSegment = segments[segments.length - 1];
        if (newest.speed !== null) {
            result.currentKph = newest.speed;
            result.currentReported = true;
        } else if (!lastSegment.gap && !lastSegment.bad) {
            result.currentKph = lastSegment.derivedKph;
        }

        // Rule 9: highest reported speed when any point reports one, else
        // the highest derived segment speed outside gaps and bad fixes.
        const reported = rows.map(function (r) { return r.speed; })
            .filter(function (s) { return s !== null; });
        if (reported.length) {
            result.maxKph = Math.max.apply(null, reported);
            result.maxReported = true;
        } else {
            const derived = segments.filter(function (s) {
                return !s.gap && !s.bad && s.derivedKph !== null;
            }).map(function (s) { return s.derivedKph; });
            result.maxKph = derived.length ? Math.max.apply(null, derived) : null;
        }

        // Rule 10: beacon frequency over the intervals that are not gaps.
        const beaconIntervals = segments.filter(function (s) { return !s.gap; })
            .map(function (s) { return s.seconds; });
        result.beacon = {
            medianSeconds: median(beaconIntervals),
            meanSeconds: mean(beaconIntervals),
            intervals: beaconIntervals.length,
        };

        // Rule 13: reported course at the newest point, else the bearing
        // from the second-newest point (the derived branch is this app's
        // common case: track rows carry no course).
        if (newest.course !== null) {
            result.headingDeg = newest.course;
            result.headingReported = true;
        } else {
            const prev = rows[rows.length - 2];
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
        const value = isMetric(units) ? km : km / KM_PER_MILE;
        const shown = value < 10 ? value.toFixed(1) : Math.round(value).toString();
        return shown + ' ' + (isMetric(units) ? 'km' : 'mi');
    }

    // Speeds: whole units.
    function formatSpeed(kph, units) {
        if (kph === null || kph === undefined || !isFinite(kph)) return '-';
        const value = isMetric(units) ? kph : kph / KM_PER_MILE;
        return Math.round(value) + ' ' + (isMetric(units) ? 'km/h' : 'mph');
    }

    function formatElapsed(seconds) {
        if (seconds === null || seconds === undefined || !isFinite(seconds)) return '-';
        if (seconds < 60) return Math.round(seconds) + ' s';
        if (seconds < 7200) return Math.round(seconds / 60) + ' min';
        // let, not const: the minutes below round up into the next hour.
        let h = Math.floor(seconds / 3600);
        let m = Math.round((seconds - h * 3600) / 60);
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
        const t = timeMs(timestamp);
        if (t === null) return '-';
        const d = new Date(t);
        return String(d.getHours()).padStart(2, '0') + ':' +
            String(d.getMinutes()).padStart(2, '0');
    }

    // --- Trail info panel (issue #207, plan tasks 3-4) ---
    /* The DOM half of the shared spec (basemap-kit#65), ported from the
     * reference map-shared/trail-panel.js: the spec's field order, "-" where a
     * value is unknown, and placement beside the trail that never covers the
     * map's own chrome. The numbers come from compute() above; this section
     * only formats and places them. */
    const TRAIL_PLACEMENT_GAP = 12;
    const TRAIL_PLACEMENT_MARGIN = 10;

    var trailPanelEl = null;
    var trailPanelOpen = false;
    var trailPanelCallsign = null;
    var trailPanelLastFocus = null;
    var _trailPanelKeyBound = false;

    function escapeHtml(text) {
        return String(text === null || text === undefined ? '' : text)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    // APRS symbol sprite: the same technique the station popup uses, so a
    // station reads the same in both places.
    function trailSymbolHtml(props) {
        if (!props || !props.symbol) return '';
        const sprite = parseSymbolTable(props.symbol_table);
        const pos = getSymbolSpritePosition(props.symbol);
        return '<span class="trail-panel-symbol" aria-hidden="true" style="' +
            'display:inline-block;width:' + SYMBOL_SIZE + 'px;height:' + SYMBOL_SIZE + 'px;' +
            "background-image:url('" + sprite.spriteUrl + "');" +
            'background-position:' + pos.x + 'px ' + pos.y + 'px;' +
            'background-size:' + (SPRITE_COLS * SYMBOL_SIZE) + 'px ' +
            (6 * SYMBOL_SIZE) + 'px;' +
            'background-repeat:no-repeat;"></span>';
    }

    function trailWindowLabel(hours) {
        if (hours === null || hours === undefined) return '-';
        if (hours < 1) return 'last ' + Math.round(hours * 60) + ' minutes';
        if (hours === 1) return 'last 1 hour';
        if (hours < 24) return 'last ' + hours + ' hours';
        const days = hours / 24;
        return 'last ' + (days === Math.round(days) ? days : days.toFixed(1)) + ' days';
    }

    function trailSpeedRow(label, kph, reported, units) {
        if (kph === null || kph === undefined) return { label: label, value: '-' };
        return {
            label: label,
            value: formatSpeed(kph, units) + (reported ? ' (reported)' : ' (computed)'),
        };
    }

    // The spec's field order, every field present, "-" where unknown.
    function buildTrailRows(result, units, windowHours) {
        return [
            { label: 'Trail start', value: formatClock(result.firstTime) },
            { label: 'Elapsed travel time', value: formatElapsed(result.elapsedSeconds) },
            { label: 'Total distance', value: formatDistance(result.distanceKm, units) },
            {
                label: 'Beacon frequency',
                value: result.beacon.intervals
                    ? formatInterval(result.beacon.medianSeconds) + ' median \u00B7 ' +
                      formatInterval(result.beacon.meanSeconds) + ' mean (' +
                      result.beacon.intervals + ' intervals)'
                    : '-',
            },
            trailSpeedRow('Current speed', result.currentKph, result.currentReported, units),
            { label: 'Average speed', value: formatSpeed(result.averageKph, units) },
            trailSpeedRow('Maximum speed', result.maxKph, result.maxReported, units),
            { label: 'Heading', value: formatHeading(result.headingDeg) },
            { label: 'Last heard', value: formatClock(result.lastTime) },
            { label: 'Points', value: String(result.points) },
            { label: 'Data window', value: trailWindowLabel(windowHours) },
        ];
    }

    function rectsOverlap(a, b) {
        return a.left < b.right && a.right > b.left &&
            a.top < b.bottom && a.bottom > b.top;
    }

    // Pure placement geometry (spec rule 9): the panel sits BESIDE the trail,
    // one gap from its nearest edge, flipping to the other side only when the
    // first has no room, then clamped inside the map viewport and slid clear of
    // the chrome. All boxes are in map pixels.
    function computeTrailPlacement(trail, size, viewport, chrome) {
        const width = size.width;
        const height = size.height;
        const vw = viewport.width;
        const vh = viewport.height;
        const gap = TRAIL_PLACEMENT_GAP;
        const margin = TRAIL_PLACEMENT_MARGIN;

        const minLeft = margin;
        const maxLeft = Math.max(minLeft, vw - width - margin);
        let left = trail.maxX + gap;
        if (left > maxLeft) left = trail.minX - gap - width;
        left = Math.max(minLeft, Math.min(left, maxLeft));

        const minTop = ((chrome && chrome.top) || 0) + margin;
        const maxTop = Math.max(minTop, vh - height - margin);
        let top = Math.max(
            minTop,
            Math.min((trail.minY + trail.maxY) / 2 - height / 2, maxTop));

        // Slide clear of the map's own controls rather than covering them.
        const obstacles = (chrome && chrome.obstacles) || [];
        let blocked = null;
        for (const box of obstacles) {
            if (rectsOverlap({ left: left, right: left + width, top: top, bottom: top + height }, box)) {
                blocked = box;
                break;
            }
        }
        if (blocked) {
            const candidates = [blocked.top - gap - height, blocked.bottom + gap];
            for (const candidate of candidates) {
                const shifted = Math.max(minTop, Math.min(candidate, maxTop));
                const fits = !obstacles.some(function (box) {
                    return rectsOverlap({
                        left: left, right: left + width,
                        top: shifted, bottom: shifted + height,
                    }, box);
                });
                if (fits) { top = shifted; break; }
            }
        }
        return { left: left, top: top };
    }

    function ensureTrailPanel() {
        if (trailPanelEl) return trailPanelEl;
        const root = document.getElementById('map-container');
        if (!root) return null;
        const el = document.createElement('section');
        el.id = 'trail-panel';
        el.className = 'trail-panel';
        el.setAttribute('role', 'dialog');
        el.setAttribute('aria-labelledby', 'trail-panel-title');
        el.setAttribute('tabindex', '-1');
        el.hidden = true;
        root.appendChild(el);
        trailPanelEl = el;
        return el;
    }

    function closeTrailPanel() {
        if (!trailPanelEl || !trailPanelOpen) return;
        trailPanelOpen = false;
        trailPanelCallsign = null;
        trailPanelEl.hidden = true;
        // preventScroll: focusing must not scroll the page under the pointer.
        if (trailPanelLastFocus && typeof trailPanelLastFocus.focus === 'function') {
            trailPanelLastFocus.focus({ preventScroll: true });
        }
        trailPanelLastFocus = null;
    }

    // The trail as drawn, in map pixels, for the placement maths.
    function trailScreenBounds(callsign) {
        const points = stationTracks[callsign];
        if (!map || !points || !points.length) return null;
        let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
        for (const p of points) {
            const s = map.project([p[1], p[0]]);
            minX = Math.min(minX, s.x);
            maxX = Math.max(maxX, s.x);
            minY = Math.min(minY, s.y);
            maxY = Math.max(maxY, s.y);
        }
        return { minX: minX, minY: minY, maxX: maxX, maxY: maxY };
    }

    // The map's own controls (legend, stats, filters, my location, zoom, the
    // follow chip) as boxes relative to the map canvas, so place() can dodge
    // them instead of covering them.
    function mapChromeObstacles(canvasRect) {
        const boxes = [];
        const all = document.querySelectorAll(
            '#map-container .maplibregl-ctrl, #map-container .fab-center');
        for (const el of all) {
            const r = el.getBoundingClientRect();
            if (!r.width || !r.height) continue;
            boxes.push({
                left: r.left - canvasRect.left,
                top: r.top - canvasRect.top,
                right: r.right - canvasRect.left,
                bottom: r.bottom - canvasRect.top,
            });
        }
        return boxes;
    }

    function placeTrailPanel() {
        if (!trailPanelEl || !trailPanelOpen || !map) return null;
        const canvas = map.getCanvas();
        const canvasRect = canvas.getBoundingClientRect();
        const width = canvas.clientWidth;
        const height = canvas.clientHeight;
        const spot = computeTrailPlacement(
            trailScreenBounds(trailPanelCallsign) || {
                minX: width / 2, minY: height / 2, maxX: width / 2, maxY: height / 2,
            },
            {
                width: trailPanelEl.offsetWidth || 260,
                height: trailPanelEl.offsetHeight || 240,
            },
            { width: width, height: height },
            { top: 0, obstacles: mapChromeObstacles(canvasRect) });
        // The panel is absolutely positioned in #map-container; the canvas may
        // sit a few pixels in from it, so shift into the panel's own frame.
        const container = trailPanelEl.parentNode.getBoundingClientRect();
        const dx = canvasRect.left - container.left;
        const dy = canvasRect.top - container.top;
        trailPanelEl.style.left = (spot.left + dx) + 'px';
        trailPanelEl.style.right = '';
        trailPanelEl.style.top = (spot.top + dy) + 'px';
        return spot;
    }

    function openTrailPanel(callsign) {
        const el = ensureTrailPanel();
        if (!el) return;
        // Track rows are [lat, lon, epoch seconds]; the stats take the spec's
        // [lon, lat, timestamp] with speed and course always absent here.
        const points = (stationTracks[callsign] || []).map(function (p) {
            return [p[1], p[0], p[2]];
        });
        const result = compute(points, { windowHours: trailHours });
        const props = stationFeatures[callsign] ? stationFeatures[callsign].properties : null;

        let html = '<div class="trail-panel-header">' +
            trailSymbolHtml(props) +
            '<h2 class="trail-panel-title" id="trail-panel-title">' +
            escapeHtml(callsign) + '</h2>' +
            '<button type="button" class="trail-panel-close" ' +
            'aria-label="Close trail panel">\u00D7</button></div>';

        if (!result.hasTrail) {
            // Spec rule 14: no trail is no trail: callsign, last heard, nothing else.
            html += '<dl class="trail-panel-rows">' +
                '<div class="trail-panel-row"><dt>Last heard</dt><dd>' +
                escapeHtml(formatClock(result.lastTime)) + '</dd></div>' +
                '</dl><p class="trail-panel-note">No trail in the window</p>';
        } else {
            html += '<dl class="trail-panel-rows">';
            buildTrailRows(result, getUnits(), trailHours).forEach(function (row) {
                html += '<div class="trail-panel-row"><dt>' + escapeHtml(row.label) +
                    '</dt><dd>' + escapeHtml(row.value) + '</dd></div>';
            });
            html += '</dl>';
            if (result.badFixes) {
                html += '<p class="trail-panel-note">' + result.badFixes +
                    (result.badFixes === 1 ? ' position' : ' positions') +
                    ' excluded as a bad fix</p>';
            }
        }
        el.innerHTML = html;
        el.querySelector('.trail-panel-close').addEventListener('click', closeTrailPanel);

        trailPanelCallsign = callsign;
        if (!trailPanelOpen) trailPanelLastFocus = document.activeElement;
        trailPanelOpen = true;
        el.hidden = false;
        el.focus({ preventScroll: true });

        // Escape closes the panel even if focus moved off it, and stops there so
        // it never also closes whatever is behind it (spec rules 5 and 7).
        if (!_trailPanelKeyBound) {
            _trailPanelKeyBound = true;
            document.addEventListener('keydown', function (ev) {
                if (trailPanelOpen && ev.key === 'Escape') {
                    ev.preventDefault();
                    ev.stopPropagation();
                    closeTrailPanel();
                }
            }, true);
        }
        placeTrailPanel();
    }

    function refreshOverlaySource() {
        if (!map || !map.getSource || !map.getSource('station-overlay')) return;
        map.getSource('station-overlay').setData(_overlayFC || { type: 'FeatureCollection', features: [] });
    }

    function refreshArcSource() {
        if (!map || !map.getSource || !map.getSource('tx-arcs')) return;
        map.getSource('tx-arcs').setData(_arcFC || { type: 'FeatureCollection', features: [] });
    }

    function addArcFeatures(features) {
        if (!_arcFC) _arcFC = { type: 'FeatureCollection', features: [] };
        _arcFC.features.push(...features);
        refreshArcSource();
    }

    function removeArcFeatures(features) {
        if (!_arcFC || !_arcFC.features.length) return;
        _arcFC.features = _arcFC.features.filter(f => !features.includes(f));
        refreshArcSource();
    }

    function onStationLayerClick(e) {
        if (e.features && e.features.length) {
            openStationPopup(e.features[0].properties.callsign);
        }
    }

    function hex2(ch) {
        return ch.charCodeAt(0).toString(16).padStart(2, '0');
    }

    // --- Station GeoJSON source refresh (throttled) ---
    let _stationRefreshPending = false;
    function scheduleStationRefresh() {
        if (_stationRefreshPending) return;
        _stationRefreshPending = true;
        setTimeout(function () {
            _stationRefreshPending = false;
            refreshStations();
        }, 50);
    }

    function refreshStations() {
        if (!mainMapIsMaplibre || !map || !map.getSource('stations')) return;
        stationFeatureCollection = buildStationFeatureCollection();
        map.getSource('stations').setData(stationFeatureCollection);
    }

    /** Shared filter predicate for the station markers and their track lines:
     *  callsign (substring), type, and tx/rx. Mirrors the old per-marker
     *  show/hide and the log-row filter. */
    function stationVisibleByFilters(cs, s) {
        if (!s || !s.data) return false;
        const callsign = (document.getElementById('filter-callsign') || {}).value?.toUpperCase() || '';
        const type = (document.getElementById('filter-type') || {}).value || '';
        const txrx = (document.getElementById('filter-txrx') || {}).value || '';
        if (callsign && !cs.toUpperCase().includes(callsign)) return false;
        if (type && s.data.type && s.data.type !== type) return false;
        if (txrx && s.data.tx !== undefined) {
            const txVal = s.data.tx ? 'tx' : 'rx';
            if (txVal !== txrx) return false;
        }
        return true;
    }

    function buildStationFeatureCollection() {
        const isMyCall = config.station?.my_position?.callsign;
        const feats = [];
        for (const cs of Object.keys(stationFeatures)) {
            if (!stationVisibleByFilters(cs, stations[cs])) continue;
            stationFeatures[cs].properties.is_my = cs === isMyCall;
            // WX stations render as NWS station plots (runtime image) while the
            // "Station plots" toggle is on; otherwise fall back to the plain
            // APRS sprite. Recomputed each refresh so images re-register when a
            // station's weather changes and the toggle takes effect immediately.
            const entry = stations[cs] && stations[cs].data;
            const modelImg = (wxStationEnabled() && entry && entry.last_weather)
                ? upsertWxStationImage(cs, entry) : null;
            if (modelImg) stationFeatures[cs].properties.wx_model = modelImg;
            else delete stationFeatures[cs].properties.wx_model;
            feats.push(stationFeatures[cs]);
        }
        return { type: 'FeatureCollection', features: feats };
    }

    // --- Shareable map URL (#150) ---
    /** Parse a haminfo-style map hash: #zoom/lat/lon[/bearing[/pitch]]
     *  (MapLibre zoom levels). Returns null for anything else, so other
     *  hashes and out-of-range values leave the normal centering alone. */
    function parseMapHash(hash) {
        if (typeof hash !== 'string') return null;
        const parts = hash.replace(/^#/, '').split('/');
        if (parts.length < 3 || parts.length > 5) return null;
        const n = parts.map(function (p) { return p.trim() === '' ? NaN : Number(p); });
        if (n.some(function (x) { return !isFinite(x); })) return null;
        const zoom = n[0], lat = n[1], lon = n[2];
        if (zoom < 0 || zoom > 24 || lat < -90 || lat > 90 || lon < -180 || lon > 180) return null;
        return { zoom: zoom, lat: lat, lon: lon, bearing: n[3] || 0, pitch: n[4] || 0 };
    }
    /** Format the view like haminfo's /map: #zoom/lat/lon with 4-decimal
     *  coordinates; bearing/pitch are appended only when non-zero. */
    function formatMapHash(zoom, lat, lon, bearing, pitch) {
        let h = '#' + Number(zoom.toFixed(2)) + '/' + lat.toFixed(4) + '/' + lon.toFixed(4);
        const b = Math.round(bearing || 0), p = Math.round(pitch || 0);
        if (b !== 0 || p !== 0) h += '/' + b;
        if (p !== 0) h += '/' + p;
        return h;
    }

    // --- Map view helpers (maplibre) ---
    /** MapLibre uses 512px tiles, Leaflet 256px: Leaflet zoom z == MapLibre
     *  z-1. Convert a configured/Leaflet-semantic zoom (issue #135). */
    function toMlZoom(leafletZoom) {
        return Math.max(0, leafletZoom - 1);
    }
    function mapSetView(lat, lng, zoom) {
        if (!map) return;
        map.jumpTo({ center: [lng, lat], zoom: zoom });
    }
    function mapFlyTo(lat, lng, zoom) {
        if (!map) return;
        map.flyTo({ center: [lng, lat], zoom: zoom });
    }
    function closeActivePopup() {
        if (activePopup) { activePopup.remove(); activePopup = null; }
    }

    /** Wrap a DOM-container builder as a MapLibre control (replaces the old
     *  Leaflet control pattern for the main map). */
    function mlControl(build, position) {
        return {
            getDefaultPosition: function () { return position; },
            onAdd: function () {
                this._container = build();
                this._container.classList.add('maplibregl-ctrl');
                return this._container;
            },
            onRemove: function () {
                if (this._container && this._container.parentNode) {
                    this._container.parentNode.removeChild(this._container);
                }
            },
        };
    }
    function mlCreate(className) {
        const el = document.createElement('div');
        el.className = className;
        return el;
    }

    // --- Map drawer (#219) ---------------------------------------------------
    // The five former floating cards (Legend, Stats, Filters, My Location, GPX)
    // now live as collapsible sections inside #map-drawer. drawerSection builds
    // one section, returns the body element the card content is appended to,
    // and wires its header to open/close that section. dw-toolbar.js owns the
    // drawer's own open/close; here we only repaint the map when it changes.
    function drawerSection(title, openByDefault) {
        var body = document.getElementById('map-drawer-body');
        var section = document.createElement('section');
        section.className = 'drawer-section' + (openByDefault ? '' : ' section-collapsed');
        var header = document.createElement('button');
        header.type = 'button';
        header.className = 'drawer-section-header';
        header.setAttribute('aria-expanded', openByDefault ? 'true' : 'false');
        header.innerHTML = '<span>' + title + '</span><span class="drawer-section-icon">' +
            (openByDefault ? '\u25B2' : '\u25BC') + '</span>';
        var content = document.createElement('div');
        content.className = 'drawer-section-body';
        header.addEventListener('click', function () {
            var collapsed = section.classList.toggle('section-collapsed');
            header.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
            header.querySelector('.drawer-section-icon').textContent = collapsed ? '\u25BC' : '\u25B2';
        });
        section.appendChild(header);
        section.appendChild(content);
        if (body) body.appendChild(section);
        return content;
    }

    // Repaint the map whenever the drawer opens/closes so MapLibre re-renders
    // tiles for the new map width (≥1024px the drawer shrinks the map).
    window.__dwDrawerChanged = function () {
        if (!map) return;
        // Resize once now and again after the CSS transition settles.
        map.resize();
        setTimeout(function () { if (map) map.resize(); }, 220);
    };

    function initCenterFab() {
        const btn = document.getElementById('btn-center-my-pos');
        if (!btn) return;
        btn.addEventListener('click', function () {
            const pos = getMyPosition();
            if (pos) {
                const zoom = config.station?.zoom || 12;
                mapFlyTo(pos.lat, pos.lng, toMlZoom(zoom));
            }
        });
    }

    async function loadStations() {
        try {
            const resp = await fetch(API_BASE + '/stations');
            const stationList = await resp.json();
            for (const s of stationList) {
                addOrUpdateStation(s);
            }
        } catch (e) {
            console.error('Failed to load stations:', e);
        }
    }

    async function loadStationPositions() {
        try {
            const resp = await fetch(API_BASE + '/stations/positions');
            const data = await resp.json();
            for (const [callsign, pos] of Object.entries(data)) {
                // Reuse the single-entry path so bulk loads refresh LRU order.
                updatePositionCache(callsign, pos.lat, pos.lng);
            }
        } catch (e) {
            console.error('Failed to load station positions:', e);
        }
    }

    async function loadTracks() {
        try {
            const resp = await fetch(`${API_BASE}/stations/tracks?hours=${trailHours}`);
            const tracks = await resp.json();
            // Replace all tracks with historical data; live packets extend them
            // via updateStationTrack.
            for (const cs of Object.keys(stationTracks)) delete stationTracks[cs];
            for (const [cs, points] of Object.entries(tracks)) {
                if (!stations[cs]) continue;
                // [lat, lon, epoch seconds]: the trail panel needs the times
                // for its statistics; the drawn line uses the first two.
                stationTracks[cs] = points.map(p => [p[0], p[1], p[2]]);
            }
            refreshStationTrackSource();
        } catch (e) {
            console.error('Failed to load tracks:', e);
        }
    }

    // --- WX station model images (issue #152) ---
    // Each weather station's plot is drawn to a canvas and registered as a
    // MapLibre runtime image. Runtime images drop whenever the style reloads
    // (theme switch), so they are re-registered from wxImageCache on style.load.
    const wxImageCache = {};

    function wxStationEnabled() {
        return localStorage.getItem('dw-station-model') !== '0';
    }

    function drawModelText(ctx, text, x, y, size, align) {
        ctx.font = 'bold ' + (size || 9) + 'px sans-serif';
        ctx.textAlign = align || 'center';
        ctx.textBaseline = 'middle';
        ctx.lineWidth = 3;
        ctx.strokeStyle = themeColor('--label-shadow');
        ctx.strokeText(text, x, y);
        ctx.fillStyle = themeColor('--text-primary');
        ctx.fillText(text, x, y);
    }

    // Box a label would paint, in canvas px: the measured text width plus a
    // 2 px pad for the 3 px halo, and the font em box for the height, so the
    // estimate is never smaller than the real ink. [left, top, right, bottom].
    function labelBox(ctx, text, x, y, size, align) {
        ctx.font = 'bold ' + size + 'px sans-serif';
        const w = ctx.measureText(text).width;
        let left = x;
        if (align === 'right') left = x - w;
        else if (align !== 'left') left = x - w / 2;
        return [left - 2, y - size / 2 - 2, left + w + 2, y + size / 2 + 2];
    }

    function boxesHit(a, b) {
        return a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3];
    }

    function insideCanvas(box) {
        return box[0] > 0 && box[2] < 104 && box[1] > 0 && box[3] < 96;
    }

        // Gray "no data" disc when a station sends no temperature (#156 fix 4) —
    // never an empty circle. Same value the haminfo/#273 reference uses.
    var NO_TEMP_GRAY = '#6b7280';

    /** Draw one station's NWS plot onto a fresh canvas (104x96 logical px,
     *  centre (52,48), circle r=13 — the geometry the haminfo marker uses).
     *  The server computes every colour and converted value (station_model.py);
     *  this renders hexes and layout only, no math. Returns the canvas. */
    function drawStationModelCanvas(model, callsign, px) {
        px = px || 1;
        const canvas = document.createElement('canvas');
        canvas.width = 104 * px;
        canvas.height = 96 * px;
        const ctx = canvas.getContext('2d');
        ctx.scale(px, px);
        const cx = 52, cy = 48, r = 13;
        // Station circle: ALWAYS filled and colored by temperature (#156 fix 5)
        // with the rounded value + unit inside; no temperature -> gray "N/D"
        // disc (#156 fix 4). Never an empty circle.
        ctx.beginPath();
        ctx.arc(cx, cy, r, 0, Math.PI * 2);
        ctx.fillStyle = model.temp_color || NO_TEMP_GRAY;
        ctx.fill();
        ctx.lineWidth = 2;
        ctx.strokeStyle = themeColor('--text-primary');
        ctx.stroke();
        const tempUnit = isImperial() ? '\u00B0F' : '\u00B0C';
        const temp = isImperial() ? model.temp_f : model.temp_c;
        drawModelText(ctx, temp != null ? String(Math.round(temp)) + tempUnit : 'N/D', cx, cy + 4, 9);
        // The fixed fields, so the gust label can be placed around them instead
        // of on top of them (#156 fix 1: nothing overlaps; same geometry and
        // first-free-slot search as the haminfo #273 reference).
        const dewLabel = model.dewpoint_c != null
            ? String(Math.round(isImperial() ? model.dewpoint_f : model.dewpoint_c)) + tempUnit
            : null;
        const pressureLabel = model.pressure_hpa != null
            ? String(model.pressure_hpa) + ' hPa' : null;
        const reserved = [[cx - r - 1, cy - r - 1, cx + r + 1, cy + r + 1]];
        if (pressureLabel) reserved.push(labelBox(ctx, pressureLabel, 100, 18, 10, 'right'));
        if (dewLabel) reserved.push(labelBox(ctx, dewLabel, 4, 82, 8, 'left'));
        if (callsign) reserved.push(labelBox(ctx, callsign, cx, 94, 8));
        // Wind: staff, feathers and calm ring colored by the continuous
        // wind-speed scale (#156 fix 6); color is a server hex.
        const windColor = model.wind_color || themeColor('--text-primary');
        if (model.calm) {
            ctx.beginPath();
            ctx.arc(cx, cy, r + 4, 0, Math.PI * 2);
            ctx.lineWidth = 2;
            ctx.strokeStyle = windColor;
            ctx.stroke();
        }
        if (model.rotation_deg != null) {
            const a = model.rotation_deg * Math.PI / 180;
            const ux = Math.sin(a), uy = -Math.cos(a);   // toward where wind comes FROM
            const tipX = cx + ux * (r + 30), tipY = cy + uy * (r + 30);
            ctx.beginPath();
            ctx.moveTo(cx, cy);
            ctx.lineTo(tipX, tipY);
            ctx.lineWidth = 2;
            ctx.strokeStyle = windColor;
            ctx.stroke();
            const dx = -ux, dy = -uy;                    // feathers stream downwind
            const feats = model.feathers || [];
            for (let i = 0; i < feats.length; i++) {
                const fx = tipX - ux * (i * 6 + 2);
                const fy = tipY - uy * (i * 6 + 2);
                if (feats[i] === 'pennant') {
                    ctx.beginPath();
                    ctx.moveTo(fx, fy);
                    ctx.lineTo(fx + dy * 4 - dx * 4, fy - dx * 4 - dy * 4);
                    ctx.lineTo(fx + dx * 8, fy + dy * 8);
                    ctx.lineTo(fx - dy * 4 - dx * 4, fy + dx * 4 - dy * 4);
                    ctx.closePath();
                    ctx.fillStyle = windColor;
                    ctx.fill();
                } else if (feats[i] === 'full') {
                    ctx.beginPath();
                    ctx.moveTo(fx, fy);
                    ctx.lineTo(fx + dx * 8 + dy * 3, fy + dy * 8 - dx * 3);
                    ctx.lineWidth = 2;
                    ctx.strokeStyle = windColor;
                    ctx.stroke();
                } else {  // half
                    ctx.beginPath();
                    ctx.moveTo(fx, fy);
                    ctx.lineTo(fx + dx * 5 + dy * 1.8, fy + dy * 5 - dx * 1.8);
                    ctx.lineWidth = 2;
                    ctx.strokeStyle = windColor;
                    ctx.stroke();
                }
            }
            // Gust G<kt> just past the barb tip (#156 fix 3 — every value
            // carries its unit; no 3-h pressure trend in the APRS feed). The
            // fixed 40 px tip position hit the pressure text (or the callsign
            // or dewpoint) for winds from certain directions, so walk the tip
            // ring and take the first slot that clears every reserved box.
            if (model.gust_kt != null) {
                const gustText = 'G' + String(Math.round(model.gust_kt)) + 'kt';
                const offsets = [0, 10, -10, 20, -20, 30, -30, 45, -45, 60, -60, 75, -75, 90, -90];
                const radii = [40, 34, 28, 22];
                let spot = null;
                for (let ri = 0; ri < radii.length && !spot; ri++) {
                    for (let oi = 0; oi < offsets.length; oi++) {
                        const ga = (model.rotation_deg + offsets[oi]) * Math.PI / 180;
                        const gx = cx + radii[ri] * Math.sin(ga);
                        const gy = cy - radii[ri] * Math.cos(ga) + 3;
                        const box = labelBox(ctx, gustText, gx, gy, 8);
                        if (!insideCanvas(box)) continue;
                        if (reserved.some(function (b) { return boxesHit(box, b); })) continue;
                        spot = [gx, gy];
                        break;
                    }
                }
                if (!spot) {  // fallback (not reached in practice): clamp inside
                    spot = [Math.max(34, Math.min(70, cx + 40 * Math.sin(a))),
                        Math.max(14, Math.min(88, cy - 40 * Math.cos(a) + 3))];
                }
                drawModelText(ctx, gustText, spot[0], spot[1], 8);
            }
        }
        // Dewpoint bottom-left, pressure top-right in hPa (#156 fixes 1 & 3:
        // each field has its own position and a unit; omitted when the station
        // did not report them).
        if (dewLabel) {
            // Start-anchored at x=4 (haminfo #273 geometry) so the halo stays
            // inside the canvas instead of being cut at the left edge.
            drawModelText(ctx, dewLabel, 4, 82, 8, 'left');
        }
        if (pressureLabel) {
            drawModelText(ctx, pressureLabel, 100, 18, 10, 'right');
        }
        // Callsign below the model (#156 fix 2).
        if (callsign) {
            drawModelText(ctx, callsign, cx, 94, 8);
        }
        return canvas;
    }

    /** Draw one station's NWS plot and register it as a runtime image. Returns
     *  the image id ('wx-<call>') or null when it cannot draw. entry is the
     *  full station payload: geometry + server colors from entry.wx_model. */
    function upsertWxStationImage(cs, entry) {
        if (!map || !entry || !entry.wx_model) return null;
        const px = window.devicePixelRatio || 1;
        const canvas = drawStationModelCanvas(entry.wx_model, cs, px);
        const ctx = canvas.getContext('2d');
        const imgData = ctx.getImageData(0, 0, canvas.width, canvas.height);
        wxImageCache[cs] = imgData;
        // Re-adding an existing runtime image throws in MapLibre; remove first
        // so a changed report updates the plot on the next refresh (spec D2).
        if (map.hasImage('wx-' + cs)) map.removeImage('wx-' + cs);
        map.addImage('wx-' + cs, imgData, { pixelRatio: px });
        return 'wx-' + cs;
    }/** Re-register cached plot images after a style reload (theme switch); drop
     *  entries whose station no longer reports weather. */
    function reRegisterWxStationImages() {
        if (!map) return;
        for (const cs of Object.keys(wxImageCache)) {
            const entry = stations[cs] && stations[cs].data;
            if (entry && entry.wx_model) {
                map.addImage('wx-' + cs, wxImageCache[cs], { pixelRatio: window.devicePixelRatio || 1 });
            } else {
                delete wxImageCache[cs];
            }
        }
    }

    function addOrUpdateStation(data) {
        if (data.latitude == null || data.longitude == null) return;

        const cs = data.callsign;
        const symbolChar = data.symbol || '-';
        const symbolTable = data.symbol_table || '/';

        if (!stations[cs]) {
            stations[cs] = { data: data, popupDiv: null };
        } else {
            // Live position packets carry no weather; keep the last known
            // weather so a WX station's plot doesn't vanish on each packet.
            const prev = stations[cs].data;
            if (data.last_weather === undefined && prev && prev.last_weather !== undefined) {
                data.last_weather = prev.last_weather;
                data.wx_model = prev.wx_model;
            }
        }
        stations[cs].data = data;

        // Update the GeoJSON feature backing the maplibre station layers.
        const props = {
            callsign: cs,
            symbol_hex: hex2(symbolChar),
            symbol_table: symbolTable,
            type: data.type || '',
            tx: data.tx || false,
        };
        // Omit the overlay property entirely when there is none (#135); a
        // GeoJSON null value would otherwise slip past the layer's != null filter.
        if (symbolTable !== '/' && symbolTable !== '\\') {
            props.overlay_hex = hex2(symbolTable);
        }
        stationFeatures[cs] = {
            type: 'Feature',
            geometry: { type: 'Point', coordinates: [data.longitude, data.latitude] },
            properties: props,
        };

        scheduleStationRefresh();
        updateStationPopup(cs);
        // Keep an open popup live: it holds the previous DOM node otherwise.
        if (activePopup && activePopupCallsign === cs && stations[cs].popupDiv) {
            activePopup.setDOMContent(stations[cs].popupDiv);
        }
    }

    function updateStationPopup(callsign) {
        const s = stations[callsign];
        if (!s) return;

        const d = s.data;
        // escHtml all user-supplied strings before inserting into innerHTML (#59)
        let html = `<b>${escHtml(callsign)}</b><br>`;
        if (d.last_comment) html += `${escHtml(d.last_comment)}<br>`;
        if (d.packet_count) html += `Packets: ${d.packet_count}<br>`;
        if (d.last_seen) {
            const ago = Math.round((Date.now() / 1000 - d.last_seen) / 60);
            html += `Last seen: ${ago}m ago<br>`;
        }

        // Path info — each hop must be escaped (#59)
        if (d.last_path && d.last_path.length > 0) {
            const pathInfo = parsePath(d.last_path);
            const hops = [];
            if (pathInfo.digipeaters.length > 0) hops.push(...pathInfo.digipeaters);
            if (pathInfo.igate) hops.push(pathInfo.igate);
            if (hops.length > 0) {
                html += `<div class="popup-path">&#8594; ${hops.map(escHtml).join(' &rsaquo; ')}</div>`;
            } else if (pathInfo.isTcpip) {
                html += `<div class="popup-path">&#8594; TCPIP (internet)</div>`;
            }
        }

        // Inline weather summary for weather stations (#38)
        if (d.last_weather) {
            const w = d.last_weather;
            const parts = [];
            if (w.temperature != null) parts.push(fmtTemp(w.temperature));
            if (w.wind_speed != null) {
                let wind = fmtWind(w.wind_speed);
                if (w.wind_direction != null) wind += ` @ ${w.wind_direction.toFixed(0)}&deg;`;
                parts.push(wind);
            }
            if (w.humidity != null) parts.push(`${w.humidity.toFixed(0)}% RH`);
            if (parts.length > 0) {
                html += `<div class="popup-weather">&#127777; ${parts.join(' &nbsp; ')}</div>`;
            }
        }

        // Distance to My Position
        const myPos = getMyPosition();
        if (myPos && d.latitude != null && d.longitude != null) {
            const distKm = haversineKm(d.latitude, d.longitude, myPos.lat, myPos.lng);
            const distMi = (distKm * 0.621371).toFixed(1);
            html += `<div class="popup-distance">&#128205; ${distKm.toFixed(1)}&thinsp;km / ${distMi}&thinsp;mi</div>`;
        }

        // Build popup as a DOM element so button event handlers never
        // embed callsign data in HTML attribute strings (XSS prevention).
        const popupDiv = document.createElement('div');
        popupDiv.innerHTML = html;

        const mp = config.station?.my_position;
        const isMyStation = mp && mp.type === 'station' && mp.callsign === callsign;
        if (isMyStation) {
            const btn = document.createElement('button');
            btn.className = 'popup-btn popup-btn-remove';
            btn.textContent = 'Remove as My Position';
            btn.addEventListener('click', () => window._removeMyPosition());
            popupDiv.appendChild(btn);
        } else {
            const btn = document.createElement('button');
            btn.className = 'popup-btn popup-btn-set';
            btn.textContent = 'Set as My Position';
            btn.addEventListener('click', () => window._setMyPositionStation(callsign));
            popupDiv.appendChild(btn);
        }
        if (d.symbol === '_') {
            const btn = document.createElement('button');
            btn.className = 'popup-btn popup-btn-weather';
            btn.textContent = 'View Weather';
            btn.addEventListener('click', () => window._viewWeather(callsign));
            popupDiv.appendChild(btn);
        }
        if (d.latitude != null && d.longitude != null) {
            const btn = document.createElement('button');
            btn.className = 'popup-btn popup-btn-gpx';
            btn.textContent = 'Download GPX';
            btn.addEventListener('click', () => window._downloadGpx(callsign));
            popupDiv.appendChild(btn);
        }
        s.popupDiv = popupDiv;
    }

    /** Open a maplibre popup for a station at its coordinates (reuses the
     *  same popup HTML as the Leaflet popups did). */
    function openStationPopup(callsign) {
        const s = stations[callsign];
        if (!s || s.data.latitude == null) return;
        updateStationPopup(callsign);
        if (!s.popupDiv) return;
        closeActivePopup();
        activePopupCallsign = callsign;
        activePopup = new maplibregl.Popup({ offset: 20 })
            .setLngLat([s.data.longitude, s.data.latitude])
            .setDOMContent(s.popupDiv);
        // Register before addTo(): 'open' fires synchronously inside addTo().
        activePopup.on('open', function () { showStationOverlay(callsign); });
        activePopup.on('close', function () { clearStationOverlay(); });
        activePopup.addTo(map);
    }

    /** Pan to a station and open its popup (station-list Focus buttons). */
    function focusStation(callsign) {
        const s = stations[callsign];
        if (!map || !s || s.data.latitude == null) return;
        mapFlyTo(s.data.latitude, s.data.longitude, Math.max(map.getZoom(), toMlZoom(12)));
        openStationPopup(callsign);
    }

    function updateStationTrack(callsign, lat, lon, timestamp) {
        const s = stations[callsign];
        if (!s) return;
        if (!stationTracks[callsign]) stationTracks[callsign] = [];
        stationTracks[callsign].push([lat, lon, timestamp]);
        refreshStationTrackSource();
    }

    // --- APRS Path Parsing --- (mirrors packet_paths.py _route(), issue #205:
    // used hops are every call up to the last '*' before the first qA construct;
    // igate only from qAR/qAO/qAS; isTcpip mirrors no_rf_leg. Shape unchanged.)
    function isPathAlias(call) {
        return GENERIC_PATH_ALIASES.has(call) ||
            /^(?:WIDE|TRACE)(?:[0-9]+(?:-[0-9]+)?)?$/.test(call);
    }
    function parsePath(pathArray) {
        const result = { digipeaters: [], igate: null, isTcpip: false };
        if (!Array.isArray(pathArray) || pathArray.length === 0) return result;
        const qIndex = pathArray.findIndex(p => /^qA[A-Za-z]$/.test(p));
        const rf = qIndex < 0 ? pathArray : pathArray.slice(0, qIndex);
        const qConstruct = qIndex < 0 ? null : pathArray[qIndex];
        const stripped = rf.map(p => p.endsWith('*') ? p.slice(0, -1) : p);
        result.isTcpip = ['qAC', 'qAX', 'qAU'].includes(qConstruct) || stripped.includes('TCPIP');
        if (!result.isTcpip) {
            let lastStar = -1;
            for (let i = 0; i < rf.length; i++) if (rf[i].endsWith('*')) lastStar = i;
            const used = [];
            for (let i = 0; i <= lastStar; i++) {
                if (!isPathAlias(stripped[i])) used.push(stripped[i]);
            }
            // Collapse like the shared _collapse() so repeats don't double-draw.
            result.digipeaters = used.filter((c, i) => i === 0 || c !== used[i - 1]);
            if (['qAR', 'qAO', 'qAS'].includes(qConstruct) && qIndex + 1 < pathArray.length) {
                const call = pathArray[qIndex + 1].replace(/\*$/, '');
                if (/^[A-Z0-9]{1,6}(?:-(?:[0-9]|1[0-5]))?$/.test(call) && !isPathAlias(call)) {
                    result.igate = call;
                }
            }
        }
        return result;
    }

    // --- Follow a station (issue #206) ---
    // Ported one-for-one from haminfo map-shared/follow-station.js (issue
    // basemap-kit #64). Two deliberate differences from the reference:
    //   * no parsePath/GENERIC_PATH_ALIASES copy: app.js has its own parser
    //     (issue #188/#197, path arrays) and this page is the consumer, so
    //     readPath falls back to it instead of the module's private copy;
    //   * no window.MapShared: app.js is a single IIFE, so the module value
    //     lands in the IIFE-scope `followShared` var the map code reads.
    var followShared;
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
            // app.js's parsePath plays the role of the reference's private copy
            // (it takes feed path arrays, issue #188/#197).
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
                        text: 'Following ' + name + ' \u00b7 silent ' + mins + 'm',
                        label: 'Following ' + name + ', nothing heard for ' + mins + ' minute' +
                            (mins === 1 ? '' : 's'),
                        silent: true
                    };
                }
                if (state.suspended) {
                    return { text: 'Following ' + name + ' \u2014 paused', label: 'Following ' + name + ', paused', silent: false };
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

        // The reference ends with window.MapShared.followStation = {...}; the
        // same keys land in the IIFE-scope var instead (no global namespace in
        // app.js). Keys kept one-for-one.
        followShared = {
            DEFAULTS: DEFAULTS,
            create: createFollower,
            normalizeCallsign: normalizeCallsign,
            distanceKm: distanceKm,
            viewportWidthKm: viewportWidthKm,
            formatClock: formatClock,
            fallbackIdentity: fallbackIdentity
        };
    })();


    // --- Follow a station: page wiring (issue #206) ---
    // The state machine above owns every decision (camera when, what the chip
    // says, which packets are log rows); this section mounts the chip + rail
    // controls, feeds the machine from the WS packet flow, applies its
    // results to the map and persists dw-follow-callsign like the other dw-*
    // keys. The harness test slices only the section above, so this code may
    // use the DOM, map and localStorage freely.
    var followPage = null;
    var followChip = null, followChipText = null, followChipDot = null, followChipResume = null;
    var followLastHeardAt = null;
    var followFarTimer = null, followPendingTimer = null, followInputTimer = null;
    var followSelfMove = false; // set around our own camera calls

    function followLogCallsign() {
        if (!followPage) return null;
        var st = followPage.state();
        return st.following ? st.callsign : null;
    }

    function followModeFromRail() {
        var sel = document.getElementById('filter-follow-mode');
        return sel && sel.value === 'hide' ? 'hide' : 'dim';
    }

    function followView() {
        if (!map) return null;
        var c = map.getCenter();
        var el = map.getContainer();
        return {
            center: { lat: c.lat, lng: c.lng },
            zoom: map.getZoom(),
            viewportKmWidth: followShared.viewportWidthKm(
                function (p) { return map.unproject(p); },
                el.clientWidth, el.clientHeight)
        };
    }

    function clearFollowTimers() {
        [followFarTimer, followPendingTimer, followInputTimer].forEach(function (t) {
            if (t) clearTimeout(t);
        });
        followFarTimer = null;
        followPendingTimer = null;
        followInputTimer = null;
    }

    function followCamera(move) {
        if (!map || !move || !move.to) return;
        followSelfMove = true;
        var opts = { center: [move.to.lng, move.to.lat] };
        if (move.zoom != null) opts.zoom = move.zoom;
        if (move.motion === 'jump') {
            map.jumpTo(opts);
        } else {
            opts.duration = move.duration || followShared.DEFAULTS.easeMs;
            map.easeTo(opts);
        }
        followSelfMove = false;
    }

    function followFar(far) {
        if (!map || !far || !far.points) return;
        clearFollowTimers();
        followSelfMove = true;
        var lngs = [far.points[0].lng, far.points[1].lng];
        var lats = [far.points[0].lat, far.points[1].lat];
        // The wide view fits both the station and the far packet (N4 web rule).
        map.fitBounds([[Math.min.apply(null, lngs), Math.min.apply(null, lats)],
            [Math.max.apply(null, lngs), Math.max.apply(null, lats)]],
            { padding: 80, maxZoom: 14, duration: far.motion === 'jump' ? 0 : 600 });
        followSelfMove = false;
        // The machine owns the hold; the timer only asks it when it is due.
        followFarTimer = setTimeout(function () {
            followFarTimer = null;
            handleFollowDecision(followPage.farDue(Date.now()));
        }, far.holdMs);
    }

    function schedulePendingCamera(retryInMs) {
        if (followPendingTimer) clearTimeout(followPendingTimer);
        followPendingTimer = setTimeout(function () {
            followPendingTimer = null;
            handleFollowDecision(followPage.flushPending(Date.now(), followView()));
        }, retryInMs);
    }

    function handleFollowDecision(res) {
        if (!res) return;
        if (res.type === 'move') {
            followCamera(res);
        } else if (res.type === 'far-return') {
            followCamera({ to: followPage.state().position, zoom: null, motion: res.motion,
                duration: followShared.DEFAULTS.farReturnMs });
        } else if (res.type === 'far') {
            followFar(res);
        } else if (res.type === 'packet' && res.far) {
            followFar(res.far);
        } else if (res.type === 'pending') {
            schedulePendingCamera(res.retryInMs);
        }
    }

    function applyFollowDimming() {
        if (!map) return;
        var st = followPage ? followPage.state() : null;
        var cs = st && st.following ? st.callsign : null;
        var opacity = st && st.mode === 'hide' ? 0 : 0.3;
        var pair = [['stations-symbol-base', 'icon-opacity'],
                    ['stations-symbol-overlay', 'icon-opacity'],
                    ['stations-label', 'text-opacity']];
        for (var i = 0; i < pair.length; i += 1) {
            if (!map.getLayer(pair[i][0])) continue;
            map.setPaintProperty(pair[i][0], pair[i][1],
                cs ? ['case', ['==', ['get', 'callsign'], cs], 1, opacity] : 1);
        }
    }

    function renderFollowChip() {
        if (!followChip || !followChipText) return;
        var st = followPage ? followPage.state() : null;
        if (!st || !st.following) {
            followChip.style.display = 'none';
            return;
        }
        var c = followPage.chipText(Date.now());
        followChipText.textContent = c.text;
        followChip.setAttribute('aria-label', c.label);
        // The dot colour alone is not accessible feedback; the chip text
        // says "silent" too (WCAG 1.4.1), same as the reference.
        followChipDot.style.background = c.silent ? '#e8695b' : '#57cd8f';
        followChipResume.style.display = st.suspended ? '' : 'none';
        followChip.classList.toggle('is-silent', c.silent);
        followChip.classList.toggle('is-paused', st.suspended);
        followChip.style.display = '';
    }

    function startFollowing(raw) {
        var norm = followShared.normalizeCallsign(raw);
        if (!norm) return false;
        if (!followPage) {
            followPage = followShared.create({
                mode: followModeFromRail(),
                // #205 rule: OS reduced-motion suppresses camera moves at init.
                reduceMotion: window.matchMedia('(prefers-reduced-motion: reduce)').matches
            });
        }
        var r = followPage.follow(norm);
        if (r.type === 'error') return false;
        localStorage.setItem('dw-follow-callsign', norm);
        followLastHeardAt = Date.now();
        var pos = getStationPosition(norm);
        if (pos) {
            handleFollowDecision(followPage.setPosition({ lat: pos.lat, lng: pos.lng },
                Date.now(), followView()));
        }
        applyFollowDimming();
        renderFollowChip();
        // Rows already on screen must re-run through the filter hook.
        document.querySelectorAll('.log-row').forEach(function (row) { applyFilterToRow(row); });
        return true;
    }

    function stopFollowing() {
        if (!followPage || !followPage.state().following) return;
        localStorage.removeItem('dw-follow-callsign');
        clearFollowTimers();
        followPage.unfollow();
        followLastHeardAt = null;
        applyFollowDimming();
        renderFollowChip();
        // Rows already on screen must re-run through the filter hook.
        document.querySelectorAll('.log-row').forEach(function (row) { applyFilterToRow(row); });
    }

    function suspendFollowing() {
        if (!followPage) return;
        followPage.suspend();
        renderFollowChip();
    }

    function resumeFollowing() {
        if (!followPage) return;
        var r = followPage.resume(Date.now());
        if (r.move) followCamera(r.move);
        renderFollowChip();
    }

    function followFeedPacket(packet) {
        if (!followPage || !packet) return;
        var cs = followLogCallsign();
        if (!cs) return;
        if (packet.from_call !== cs && packet.to_call !== cs) return;
        var now = Date.now();
        followLastHeardAt = now;
        // Object/item packets locate a named object, not the reporting station
        // (issue #170 rule, same isPlacePacket test as the WS hook below).
        var isPlacePacket = packet.type === 'ObjectPacket' || packet.type === 'ItemPacket';
        if (!isPlacePacket && packet.from_call === cs && packet.latitude != null && packet.longitude != null) {
            var pos = followPage.state().position;
            if (!pos || pos.lat !== packet.latitude || pos.lng !== packet.longitude) {
                handleFollowDecision(followPage.setPosition(
                    { lat: packet.latitude, lng: packet.longitude }, now, followView()));
            }
        }
        handleFollowDecision(followPage.onPacket({
            packet_id: packet.id,
            received_at: packet.timestamp ? new Date(packet.timestamp * 1000).toISOString()
                : new Date(now).toISOString(),
            direction: packet.from_call === cs ? 'from' : 'to',
            from_call: packet.from_call,
            addressee: packet.to_call,
            packet_type: packet.type,
            comment: packet.comment || '',
            raw: packet.raw_packet || '',
            path: packet.path,
            latitude: packet.latitude,
            longitude: packet.longitude
        }, now, followView()));
        renderFollowChip();
    }

    function followSilenceTick() {
        if (!followPage) return;
        var st = followPage.state();
        if (!st.following || st.suspended) return;
        if (!followLastHeardAt) return;
        var elapsed = (Date.now() - followLastHeardAt) / 1000;
        // The direwolf WS has no callsign-silence event, so the page times it
        // from the last packet involving the followed station.
        if (elapsed >= followPage.config.silentSeconds) followPage.onSilence(elapsed);
        renderFollowChip();
    }

    function applyRailFollowInput() {
        followInputTimer = null;
        var railInput = document.getElementById('filter-follow-callsign');
        if (!railInput) return;
        var raw = railInput.value.trim();
        if (!raw) { stopFollowing(); return; }
        var norm = followShared.normalizeCallsign(raw);
        if (norm && (!followPage || followPage.state().callsign !== norm)) startFollowing(norm);
    }

    function wireFollowStation() {
        var railInput = document.getElementById('filter-follow-callsign');
        if (railInput) {
            railInput.addEventListener('input', function () {
                if (followInputTimer) clearTimeout(followInputTimer);
                // Debounce so a half-typed "W1" (valid form) does not churn
                // follow state mid-keystroke.
                followInputTimer = setTimeout(applyRailFollowInput, 400);
            });
        }
        var modeSelect = document.getElementById('filter-follow-mode');
        if (modeSelect) {
            modeSelect.addEventListener('change', function () {
                if (followPage) followPage.setMode(modeSelect.value);
                applyFollowDimming();
            });
        }
        if (followChip) {
            followChip.querySelector('#follow-stop-btn').addEventListener('click', function () {
                stopFollowing();
            });
            followChipResume.addEventListener('click', function () { resumeFollowing(); });
        }
        if (!map) return;
        // Pans/zooms from a real gesture (originalEvent present) pause
        // following and completing the move resumes it; a feature's
        // programmatic move stays ignored, and marker drags only fire
        // drag events, so they never suspend.
        map.on('movestart', function (ev) {
            if (followSelfMove || !ev || !ev.originalEvent) return;
            suspendFollowing();
        });
        map.on('moveend', function () {
            if (followPage && followPage.state().suspended) resumeFollowing();
        });
        map.on('zoomstart', function () { if (!followSelfMove) suspendFollowing(); });
        // #201 seam: applyThemeStyle's structural path calls map.setStyle, and
        // the vendored maplibre rebuilds page-added layers at default paint,
        // then re-fires 'style.load'. Follow dimming is page paint, so re-apply
        // it here. The vendored Evented keeps _listeners[type] as an array and
        // fire() iterates registration order; initFollowStation boots after
        // initMap, so this lands above the layer re-adds (as haminfo fix
        // 2bf716bd pinned its re-apply list).
        map.on('style.load', applyFollowDimming);
    }

    function initFollowStation() {
        if (!map) return;
        followChip = mlCreate('follow-chip');
        followChip.innerHTML =
            '<span class="follow-chip-dot" aria-hidden="true"></span>' +
            '<span id="follow-chip-text" aria-live="polite"></span>' +
            '<button id="follow-stop-btn" title="Stop following">\u2715</button>' +
            '<button id="follow-resume-btn" title="Resume following">\u25B6</button>';
        followChipText = followChip.querySelector('#follow-chip-text');
        followChipDot = followChip.querySelector('.follow-chip-dot');
        followChipResume = followChip.querySelector('#follow-resume-btn');
        followChip.style.background = 'var(--overlay-bg)';
        followChip.style.border = '1px solid var(--overlay-border)';
        followChip.style.color = 'var(--overlay-text)';
        followChip.style.padding = '4px 8px';
        followChip.style.fontSize = '11px';
        followChipDot.style.width = '10px';
        followChipDot.style.height = '10px';
        followChipDot.style.display = 'inline-block';
        followChip.style.display = 'none';
        map.addControl(mlControl(function () { return followChip; }, 'top-left'));
        // dw-* persistence: restore follow-on-load like other overlays.
        var saved = localStorage.getItem('dw-follow-callsign');
        if (saved) startFollowing(saved);
        setInterval(followSilenceTick, 5000);
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && followPage && followPage.state().following) stopFollowing();
        });
        wireFollowStation();
    }


    // --- Station overlay: path lines, distance, log highlight ---
    // haversineKm lives in the trail-statistics section above (spec radius).

    var _selectedCallsign = null;
    var _hopHoverBound = false;
    var _hopTooltip = null;
    var _distanceLabelMarker = null;

    function onHopEnter(e) {
        const f = e.features && e.features[0];
        if (!f || !f.properties.label) return;
        map.getCanvas().style.cursor = 'pointer';
        if (_hopTooltip) _hopTooltip.remove();
        _hopTooltip = new maplibregl.Popup({
            closeButton: false, closeOnClick: false, anchor: 'bottom',
            offset: 8, className: 'path-tooltip-popup',
        })
            .setLngLat(f.geometry.coordinates)
            .setHTML('<div class="path-tooltip">' + escHtml(String(f.properties.label)) + '</div>')
            .addTo(map);
    }

    function onHopLeave() {
        map.getCanvas().style.cursor = '';
        if (_hopTooltip) { _hopTooltip.remove(); _hopTooltip = null; }
    }

    /** Leaflet distance-label text: "12.3 km / 7.6 mi" (thin/en spaces). */
    function distanceLabelHtml(distKm) {
        const distMi = (distKm * 0.621371).toFixed(1);
        return `<div class="distance-label">${distKm.toFixed(1)}&thinsp;km&ensp;/&ensp;${distMi}&thinsp;mi</div>`;
    }

    function clearStationOverlay() {
        _overlayFC = { type: 'FeatureCollection', features: [] };
        if (_distanceLabelMarker) { _distanceLabelMarker.remove(); _distanceLabelMarker = null; }
        if (_hopTooltip) { _hopTooltip.remove(); _hopTooltip = null; }
        refreshOverlaySource();
        document.querySelectorAll('.log-row.log-row-selected')
            .forEach(el => el.classList.remove('log-row-selected'));
        _selectedCallsign = null;
    }

    function highlightStationLogRow(callsign) {
        // --- Highlight log rows ---
        const escaped = CSS.escape(callsign);
        document.querySelectorAll(`.log-row[data-callsign="${escaped}"]`)
            .forEach(el => el.classList.add('log-row-selected'));
        const first = document.querySelector(`.log-row[data-callsign="${escaped}"]`);
        if (first) first.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }

    function showStationOverlay(callsign) {
        clearStationOverlay();
        _selectedCallsign = callsign;
        const s = stations[callsign];
        if (!s) return;

        const stLat = s.data.latitude;
        const stLng = s.data.longitude;
        const features = [];

        // --- Path lines: station → digipeaters → igate ---
        const pathInfo = parsePath(s.data.last_path || []);
        const waypoints = [{ lat: stLat, lng: stLng }];
        for (const digi of pathInfo.digipeaters) {
            const pos = getStationPosition(digi);
            if (pos) waypoints.push({ lat: pos.lat, lng: pos.lng, label: digi });
        }
        if (pathInfo.igate) {
            const pos = getStationPosition(pathInfo.igate);
            if (pos) waypoints.push({ lat: pos.lat, lng: pos.lng, label: pathInfo.igate });
        }
        for (let i = 0; i < waypoints.length - 1; i++) {
            features.push({
                type: 'Feature',
                // label (if any) is the digipeater/igate at this hop's far end;
                // issue #134 consumes it for hop tooltips.
                properties: { kind: 'path', label: waypoints[i + 1].label },
                geometry: {
                    type: 'LineString',
                    coordinates: [[waypoints[i].lng, waypoints[i].lat], [waypoints[i + 1].lng, waypoints[i + 1].lat]],
                },
            });
            if (waypoints[i + 1].label) {
                features.push({
                    type: 'Feature',
                    properties: { kind: 'hop', label: waypoints[i + 1].label },
                    geometry: { type: 'Point', coordinates: [waypoints[i + 1].lng, waypoints[i + 1].lat] },
                });
            }
        }

        // --- Distance line to My Position ---
        const myPos = getMyPosition();
        if (myPos) {
            features.push({
                type: 'Feature',
                properties: { kind: 'distance' },
                geometry: {
                    type: 'LineString',
                    coordinates: [[stLng, stLat], [myPos.lng, myPos.lat]],
                },
            });
            if (map && typeof maplibregl !== 'undefined') {
                const el = document.createElement('div');
                el.innerHTML = distanceLabelHtml(haversineKm(stLat, stLng, myPos.lat, myPos.lng));
                el.style.pointerEvents = 'none';
                _distanceLabelMarker = new maplibregl.Marker({ element: el, anchor: 'center' })
                    .setLngLat([(stLng + myPos.lng) / 2, (stLat + myPos.lat) / 2])
                    .addTo(map);
            }
        }

        _overlayFC = { type: 'FeatureCollection', features };
        refreshOverlaySource();
        highlightStationLogRow(callsign);
    }

    // --- Station Position Helpers ---
    function getStationPosition(callsign) {
        const s = stations[callsign];
        if (s && s.data && s.data.latitude != null && s.data.longitude != null) {
            return { lat: s.data.latitude, lng: s.data.longitude };
        }
        const cached = stationPositionCache.get(callsign);
        if (cached) return { lat: cached.lat, lng: cached.lng };
        return null;
    }

    function updatePositionCache(callsign, lat, lng) {
        if (lat == null || lng == null) return;
        // Map insertion order is the LRU order: rewrite refreshes the slot.
        if (stationPositionCache.has(callsign)) stationPositionCache.delete(callsign);
        stationPositionCache.set(callsign, { lat, lng, at: Date.now() });
        if (stationPositionCache.size > POSITION_CACHE_MAX) {
            stationPositionCache.delete(stationPositionCache.keys().next().value);
        }
    }

    // --- Arc Animation (MapLibre) ---
    /** Pulsing ring marker standing in for the old Leaflet SVG arc icons. */
    function createArcMarker(lat, lng, index, color) {
        const size = 40 + (index * 8);
        const el = document.createElement('div');
        el.className = 'arc-burst';
        el.style.width = size + 'px';
        el.style.height = size + 'px';
        el.style.setProperty('--arc-color', color);
        return new maplibregl.Marker({ element: el, anchor: 'center' })
            .setLngLat([lng, lat]);
    }

    // --- Route Polylines (GeoJSON features for the 'tx-arcs' line source) ---
    // Returns { features, labels }: line features plus, when showRouteDistances
    // is on, per-hop km/mi label markers (`.route-distance-label`, same text
    // and units as the Leaflet implementation).
    function createRoutingPolylines(stationLat, stationLng, pathInfo) {
        if (!pathInfo) return { features: [], labels: [] };
        const waypoints = [{ lat: stationLat, lng: stationLng }];
        for (const digiCall of pathInfo.digipeaters) {
            const pos = getStationPosition(digiCall);
            if (pos) waypoints.push(pos);
        }
        if (pathInfo.igate) {
            const pos = getStationPosition(pathInfo.igate);
            if (pos) waypoints.push(pos);
        }
        const features = [];
        const labels = [];
        for (let i = 0; i < waypoints.length - 1; i++) {
            const from = waypoints[i];
            const to = waypoints[i + 1];
            features.push({
                type: 'Feature',
                geometry: {
                    type: 'LineString',
                    coordinates: [[from.lng, from.lat], [to.lng, to.lat]],
                },
            });
            if (showRouteDistances) {
                const distKm = haversineKm(from.lat, from.lng, to.lat, to.lng);
                const distMiles = distKm / 1.609344;
                const el = document.createElement('div');
                el.className = 'route-distance-label';
                el.style.pointerEvents = 'none';
                const span = document.createElement('span');
                span.textContent = `${distKm.toFixed(2)} km / ${distMiles.toFixed(2)} mi`;
                el.appendChild(span);
                // Geographic midpoint (Leaflet offset the label perpendicular
                // and rotated it; a centered mid-hop label keeps it readable).
                labels.push(new maplibregl.Marker({ element: el, anchor: 'center' })
                    .setLngLat([(from.lng + to.lng) / 2, (from.lat + to.lat) / 2]));
            }
        }
        return { features, labels };
    }

    // --- Animation Lifecycle ---
    function trackAnimationElement(element, removeAt) {
        activeAnimationElements.push({ element, removeAt });
    }

    function cleanupAnimations() {
        const now = Date.now();
        for (let i = activeAnimationElements.length - 1; i >= 0; i--) {
            const entry = activeAnimationElements[i];
            if (now >= entry.removeAt) {
                try { entry.element(); } catch (e) { /* already removed */ }
                activeAnimationElements.splice(i, 1);
            }
        }
        const cutoff = now - ANIMATION_COOLDOWN_MS * 2;
        for (const key of Object.keys(animationThrottle)) {
            if (animationThrottle[key] < cutoff) delete animationThrottle[key];
        }
    }

    // --- Animation Playback ---
    function playReceiveAnimation(callsign, lat, lng) {
        const now = Date.now();
        const throttleKey = 'rx_' + callsign;
        if (animationThrottle[throttleKey] && now - animationThrottle[throttleKey] < ANIMATION_COOLDOWN_MS) return;
        animationThrottle[throttleKey] = now;
        const rxColor = themeColor('--route-rx');
        // Three pulsing rings; same absolute timings as Leaflet (62bf407):
        // largest added at t=0, smallest at t=300, removed at 800/900/1000.
        const arcMarkers = [];
        for (let i = 0; i < 3; i++) {
            const m = createArcMarker(lat, lng, i + 1, rxColor);
            arcMarkers.push(m);
            trackAnimationElement(() => m.remove(), now + 1100);
        }
        arcMarkers[2].addTo(map);
        setTimeout(() => arcMarkers[1].addTo(map), 150);
        setTimeout(() => arcMarkers[0].addTo(map), 300);
        setTimeout(() => arcMarkers[2].remove(), 800);
        setTimeout(() => arcMarkers[1].remove(), 900);
        setTimeout(() => arcMarkers[0].remove(), 1000);
    }

    function playTransmitAnimation(callsign, lat, lng, pathArray) {
        const now = Date.now();
        if (animationThrottle[callsign] && now - animationThrottle[callsign] < ANIMATION_COOLDOWN_MS) return;
        animationThrottle[callsign] = now;
        if (Object.keys(animationThrottle).length > 500) {
            const cutoff = now - ANIMATION_COOLDOWN_MS * 2;
            for (const key of Object.keys(animationThrottle)) {
                if (animationThrottle[key] < cutoff) delete animationThrottle[key];
            }
        }
        const pathInfo = parsePath(pathArray);
        const txColor = pathInfo.isTcpip ? themeColor('--route-tx-tcpip') : themeColor('--route-tx-rf');
        // Pulsing rings at the station; absolute timings as Leaflet (62bf407):
        // added at 0/150/300, removed at 800/900/1000.
        const arcMarkers = [];
        for (let i = 0; i < 3; i++) {
            const m = createArcMarker(lat, lng, i + 1, txColor);
            arcMarkers.push(m);
            trackAnimationElement(() => m.remove(), now + 1100);
        }
        // Routing arcs as line features + per-hop distance labels, both removed
        // with the lines (Leaflet timed the lines out at 5000; cleanup guards 5500).
        const routing = createRoutingPolylines(lat, lng, pathInfo);
        const routingFeatures = routing.features;
        const routingLabels = routing.labels;
        const removeRouting = () => {
            for (const label of routingLabels) label.remove();
            removeArcFeatures(routingFeatures);
        };
        arcMarkers[0].addTo(map);
        setTimeout(() => arcMarkers[1].addTo(map), 150);
        setTimeout(() => {
            arcMarkers[2].addTo(map);
            if (routingFeatures.length > 0) {
                addArcFeatures(routingFeatures);
                for (const label of routingLabels) label.addTo(map);
                trackAnimationElement(removeRouting, now + 5500);
            }
        }, 300);
        setTimeout(() => arcMarkers[0].remove(), 800);
        setTimeout(() => arcMarkers[1].remove(), 900);
        setTimeout(() => arcMarkers[2].remove(), 1000);
        if (routingFeatures.length > 0) {
            setTimeout(removeRouting, 5000);
        }
        const receivers = [];
        for (const digiCall of pathInfo.digipeaters) {
            const pos = getStationPosition(digiCall);
            if (pos) receivers.push({ callsign: digiCall, lat: pos.lat, lng: pos.lng });
        }
        if (pathInfo.igate) {
            const pos = getStationPosition(pathInfo.igate);
            if (pos) receivers.push({ callsign: pathInfo.igate, lat: pos.lat, lng: pos.lng });
        }
        for (let i = 0; i < receivers.length; i++) {
            const rx = receivers[i];
            setTimeout(() => { playReceiveAnimation(rx.callsign, rx.lat, rx.lng); }, 300 + (i * 300));
        }
    }

    // --- Packet-path animation (issue #205) ---
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

    // One bounded journey tree, one clock, no map objects or timers (port of
    // the plan's packet-path-animator.js; options become factory parameters).
    function createPacketPathAnimator(positionCache, internetPosition, internetCurve) {
        const cache = positionCache;
        const active = new Map(), seen = new Map(), queue = [];
        let maxConcurrent = 3, trailMs = 2000, dropped = 0, sequence = 0;

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
            const angle = haversineKm(a[1], a[0], b[1], b[0]) / 6371;
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

        function position(call, packet, now, source) {
            if (source && packet.from_call === call && packet.latitude != null && packet.longitude != null) {
                const p = {lat: Number(packet.latitude), lng: Number(packet.longitude),
                    at: packet.timestamp * 1000};
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
            if (stop.app) return stop.coordinates;
            return stop.internet && internetPosition ?
                internetPosition(stop.internet.from, stop.internet.to) : stop.coordinates;
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
                    const samples = edge.samples || (internetCurve ?
                        internetCurve(from, to, Boolean(edge.to.internet)) : [from, to]);
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


    // --- Packet-path map glue: one bounded source, style.reload re-adds,
    // one setData per rendered frame (port of the plan's packet-path-overlay.js).
    function measurePacketPathChrome() {
        const top = map.getContainer().getBoundingClientRect().top;
        packetPathWidth = map.getCanvas().clientWidth;
        packetPathChrome = 0;
        for (const element of document.querySelectorAll('.navbar, #map-controls, .maplibregl-ctrl-top-left, .maplibregl-ctrl-top-right')) {
            packetPathChrome = Math.max(packetPathChrome, element.getBoundingClientRect().bottom - top);
        }
    }

    function packetPathInternetPosition(from, to) {
        const a = map.project(from), b = map.project(to);
        const node = map.unproject({x: (a.x + b.x) / 2,
            y: Math.max(packetPathChrome + 40, Math.min(a.y, b.y) - 150)});
        return [node.lng, node.lat];
    }

    function packetPathInternetCurve(from, to, inbound) {
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

    function addPacketPathLayers() {
        if (!mainMapIsMaplibre || !map) return;
        if (!map.getSource('packet-paths')) {
            map.addSource('packet-paths', {type: 'geojson', data: packetPathsData});
        }
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
                layout: {'text-field': get('label'), 'text-font': ['NotoSansMedium'], 'text-size': 12,
                    'text-anchor': get('label_anchor'),
                    'text-offset': ['case', ['==', get('label_anchor'), 'right'], ['literal', [-1, 0]], ['literal', [1, 0]]],
                    'text-allow-overlap': true, 'text-ignore-placement': true},
                paint: {'text-color': '#e8eef8', 'text-halo-color': '#101820', 'text-halo-width': 2}},
            {id: 'packet-path-node', type: 'circle', filter: node,
                paint: {'circle-radius': 18, 'circle-color': '#403065', 'circle-stroke-width': 1, 'circle-stroke-color': get('color')}},
            {id: 'packet-path-node-is', type: 'symbol', filter: node,
                layout: {'text-field': 'IS', 'text-font': ['NotoSansMedium'], 'text-size': 12,
                    'text-allow-overlap': true, 'text-ignore-placement': true}, paint: {'text-color': '#e8eef8'}},
            {id: 'packet-path-app', type: 'circle', filter: ['all', pulse, ['==', get('app'), true]],
                paint: {'circle-radius': 8, 'circle-color': '#101820', 'circle-stroke-color': get('color'), 'circle-stroke-width': 2}},
            {id: 'packet-path-node-label', type: 'symbol', filter: node,
                layout: {'text-field': get('label'), 'text-font': ['NotoSansMedium'], 'text-size': 12,
                    'text-offset': [0, 2.7], 'text-allow-overlap': true, 'text-ignore-placement': true},
                paint: {'text-color': get('color'), 'text-halo-color': '#101820', 'text-halo-width': 1.5}},
        ];
        const before = map.getLayer('stations-symbol-base') ? 'stations-symbol-base' : undefined;
        for (const layer of layers) {
            if (map.getLayer(layer.id)) continue;
            layer.source = 'packet-paths';
            // Dynamic properties must not ease between successive animation frames.
            for (const key of Object.keys(layer.paint)) layer.paint[key + '-transition'] = {duration: 0, delay: 0};
            map.addLayer(layer, before);
        }
        refreshPacketPathSource();
        schedulePacketPaths();
    }

    function refreshPacketPathSource() {
        if (!map || !map.getSource || !map.getSource('packet-paths')) return;
        map.getSource('packet-paths').setData(packetPathsData);
    }

    // Initial setting (#205): a saved '1'/'0' wins; otherwise on unless the OS
    // asks for reduced motion, matching the reference overlay's semantics.
    function packetPathInitialEnabled(saved) {
        if (saved === '1' || saved === '0') return saved === '1';
        const motion = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;
        return !(motion && motion.matches);
    }

    // Shared hidden/disable clear; reuses the render path minus the rAF bookkeeping
    // (a pending callback early-returns while hidden, so no cancel plumbing).
    function clearPacketPaths() {
        packetPaths.clear();
        packetPathsData = {type: 'FeatureCollection', features: []};
        refreshPacketPathSource();
    }

    function setPacketPathsEnabled(value) {
        packetPathsEnabled = value;
        if (!value) clearPacketPaths();
    }

    // Pause when the tab goes hidden: a day-open tab must not queue journeys
    // or frames (#205 day-open-tab criterion).
    document.addEventListener('visibilitychange', function () {
        if (document.hidden) clearPacketPaths();
    });

    function schedulePacketPaths() {
        if (packetPathsRaf === null && packetPathsEnabled && !document.hidden && packetPaths.hasWork) {
            packetPathsRaf = requestAnimationFrame(renderPacketPaths);
        }
    }

    function renderPacketPaths() {
        packetPathsRaf = null;
        if (!packetPathsEnabled || document.hidden) {
            clearPacketPaths();
            return;
        }
        packetPathsData = packetPaths.frame(Date.now());
        for (const feature of packetPathsData.features) {
            if (feature.properties.feature === 'pulse' && !feature.properties.internet) {
                const x = map.project(feature.geometry.coordinates).x;
                // Reserve one text-size per glyph plus the one-em offset; never move RF coordinates.
                feature.properties.label_anchor = x + 12 * (feature.properties.label.length + 1) > packetPathWidth ? 'right' : 'left';
            }
        }
        refreshPacketPathSource();
        schedulePacketPaths();
    }

    function offerPacketPath(packet) {
        if (!packetPathsEnabled || document.hidden) return false;
        if (!mainMapIsMaplibre || !map) return false;
        if (!packet || !packet.source || !Array.isArray(packet.hops)) return false;
        const now = Date.now(), bounds = map.getBounds();
        const calls = [packet.source].concat(packet.hops.slice(0, 16),
            (packet.tx_hops || []).slice(0, 16));
        const visible = calls.some(call => {
            if (!call) return false;
            const p = packetPaths.knownPosition(call, packet, now);
            return p && bounds.contains([p.lng, p.lat]);
        });
        if (!visible || !packetPaths.offer(packet, now)) return false;
        schedulePacketPaths();
        return true;
    }

    // --- WebSocket ---
    function connectWebSocket() {
        ws = new WebSocket(WS_URL);

        ws.onopen = () => {
            wsReconnectDelay = 1000;
            setStatus(true);
        };

        ws.onmessage = (evt) => {
            try {
                var wsRecvTime = performance.now();
                const msg = JSON.parse(evt.data);
                if (msg.event === 'packet' && msg.data) {
                    msg.data._wsRecvTime = wsRecvTime;
                }
                handleWSMessage(msg);
            } catch (e) {
                console.error('WS message parse error:', e);
            }
        };

        ws.onclose = () => {
            setStatus(false);
            setTimeout(() => {
                wsReconnectDelay = Math.min(wsReconnectDelay * 1.5, 30000);
                connectWebSocket();
            }, wsReconnectDelay);
        };

        ws.onerror = () => {
            ws.close();
        };
    }

    function handleWSMessage(msg) {
        switch (msg.event) {
            case 'packet':
                onPacket(msg.data);
                break;
            case 'stats':
                onStats(msg.data);
                break;
            case 'status':
                setStatus(msg.data.agw_connected);
                break;
            case 'error':
                console.warn('Server error:', msg.data.message);
                break;
            case 'config_updated':
                onConfigUpdated(msg.data);
                break;
            case 'storage_reset':
                clearStationsAndPackets();
                break;
            case 'ping':
                // Respond with pong (keep-alive)
                break;
        }
    }

    function onConfigUpdated(data) {
        if (data.my_position !== undefined) {
            if (!config.station) config.station = {};
            config.station.my_position = data.my_position;
            updateMyPositionMarker();
            updateMyLocationOverlay();
            // Re-render all open popups
            for (const cs of Object.keys(stations)) {
                updateStationPopup(cs);
            }
        }
    }

    function getMyPosition() {
        const mp = config.station?.my_position;
        if (!mp || !mp.type) return null;
        if (mp.type === 'pin') {
            return mp.latitude != null && mp.longitude != null
                ? { lat: mp.latitude, lng: mp.longitude } : null;
        }
        if (mp.type === 'station' && mp.callsign) {
            return getStationPosition(mp.callsign);
        }
        return null;
    }

    function onPacket(packet) {
        var t0 = performance.now();
        var serverAge = packet.timestamp ? ((Date.now() / 1000) - packet.timestamp).toFixed(2) : '?';
        addLogRow(packet);
        // Object/item packets report a named location that is NOT the
        // reporting station's own position — keep them off the reporter's
        // marker, position cache and movement track (issue #170).
        var isPlacePacket = packet.type === 'ObjectPacket' || packet.type === 'ItemPacket';
        if (!isPlacePacket && packet.latitude != null && packet.longitude != null) {
            updatePositionCache(packet.from_call, packet.latitude, packet.longitude);
        }
        if (!isPlacePacket) offerPacketPath(packet);
        // Follow-a-station (#206): feed the machine; it decides log, silence
        // and camera (see the page-wiring section below).
        followFeedPacket(packet);
        if (!isPlacePacket && packet.latitude != null && packet.longitude != null) {
            if (waitingForPosition) {
                dismissWaiting();
                const zoom = config.station?.zoom || 12;
                mapFlyTo(packet.latitude, packet.longitude, toMlZoom(zoom));
            }
            addOrUpdateStation({
                callsign: packet.from_call,
                latitude: packet.latitude,
                longitude: packet.longitude,
                symbol: packet.symbol,
                symbol_table: packet.symbol_table,
                last_comment: packet.comment,
                packet_count: (stations[packet.from_call]?.data?.packet_count || 0) + 1,
                last_seen: packet.timestamp,
                last_path: packet.path || [],
                type: packet.type || '',
                tx: packet.tx || false,
            });
            if (!packet.position_from_db) {
                updateStationTrack(packet.from_call, packet.latitude, packet.longitude,
                    packet.timestamp);
            }
        }
        const myPos = getMyPosition();
        if (packet.tx) {
            // For TX packets, prefer myPos but fall back to the packet's own coordinates
            var txPos = myPos || (packet.latitude != null && packet.longitude != null ? { lat: packet.latitude, lng: packet.longitude } : null);
            if (txPos) {
                playTransmitAnimation('My Station', txPos.lat, txPos.lng, packet.path || []);
            }
        } else {
            if (packet.latitude != null && packet.longitude != null) {
                playTransmitAnimation(packet.from_call, packet.latitude, packet.longitude, packet.path || []);
            }
            // For receive animations, prefer myPos but fall back to packet coords if this is "our" packet heard back
            var rxPos = myPos || (packet.latitude != null && packet.longitude != null ? { lat: packet.latitude, lng: packet.longitude } : null);
            if (rxPos) {
                const myCall = config.station?.my_position?.callsign || packet.from_call;
                setTimeout(() => {
                    playReceiveAnimation(myCall, rxPos.lat, rxPos.lng);
                }, 300);
            }
        }
        var t1 = performance.now();
        var wsDelay = packet._wsRecvTime ? (t0 - packet._wsRecvTime).toFixed(1) : '?';
        var broadcastAge = packet._broadcast_ts ? ((Date.now() / 1000) - packet._broadcast_ts).toFixed(2) : '?';
        var pktId = packet.id || '?';
        console.log('[TIMING] pkt#' + pktId + ' ' + (packet.tx ? 'TX' : 'RX') + ' ' + packet.from_call +
            ' | server_age=' + serverAge + 's' +
            ' | ws_transit=' + broadcastAge + 's' +
            ' | ws_to_render=' + wsDelay + 'ms' +
            ' | render=' + (t1 - t0).toFixed(1) + 'ms');
    }

    function onStats(stats) {
        if (stats.agw_connected !== undefined) {
            setStatus(stats.agw_connected);
        }
        updateStatsOverlay(stats);
    }

    function setStatus(connected) {
        const el = document.getElementById('status-indicator');
        const text = document.getElementById('status-text');
        if (connected) {
            el.classList.remove('disconnected');
            el.classList.add('connected');
            text.textContent = 'Connected';
        } else {
            el.classList.remove('connected');
            el.classList.add('disconnected');
            text.textContent = 'Disconnected';
        }
    }

    // --- Compact Log Renderer ---
    // Generates the compact log HTML entirely in the frontend using CSS variables
    // so colors respect the active theme.  Replaces the Python format_compact_log()
    // function and the bearing-HTML mutation in lifecycle._broadcast_consumer.
    function renderCompactLog(packet) {
        const tx = packet.tx;
        const pktType = packet.type || 'Unknown';
        const fromCall = packet.from_call || '';
        const toCall = packet.to_call || '';
        const path = Array.isArray(packet.path) ? packet.path : [];
        const msgNo = packet.msg_no || '';
        const humanInfo = packet.human_info || '';
        const via = packet.via || '';
        const bearing = packet.bearing || '';
        const distMiles = packet.distance_miles != null ? packet.distance_miles : null;

        const rxColor = 'var(--log-rx)';
        const txColor = 'var(--log-tx)';
        const arrowColor = tx ? txColor : rxColor;
        const typeColor = 'var(--log-type)';
        const fromColor = 'var(--log-tx)';
        const toColor = 'var(--log-comment)';
        const dimColor = 'var(--log-dim)';
        const bearingColor = 'var(--log-bearing)';
        const distColor = 'var(--log-distance)';

        const indicator = tx
            ? `<span style="color:${txColor}">TX&#x2191;</span>`
            : `<span style="color:${rxColor}">RX&#x2193;</span>`;
        const arrow = `<span style="color:${arrowColor}">&#x2192;</span>`;

        let typeStr = `<span style="color:${typeColor}">${escHtml(pktType)}</span>`;
        if (msgNo) typeStr += `:${escHtml(msgNo)}`;

        let pathStr = '';
        if (path.length) {
            pathStr = path.map(p => `<span style="color:${arrowColor}">${escHtml(p)}</span>`).join(arrow) + arrow;
        }

        const fromStr = `<span style="color:${fromColor}">${escHtml(fromCall)}</span>`;
        const toStr = `<span style="color:${toColor}">${escHtml(toCall)}</span>`;

        let html = `${indicator} ${typeStr} ${fromStr}${arrow}${pathStr}${toStr}`;

        if (via) {
            html += ` <span style="color:${dimColor}">via ${escHtml(via)}</span>`;
        }
        if (humanInfo) {
            html += ` : <span style="color:var(--log-human-info)">${escHtml(humanInfo)}</span>`;
        }
        if (bearing && distMiles != null) {
            html += ` : <span style="color:${bearingColor}">${escHtml(bearing)}</span>`
                  + `<span style="color:${distColor}">@${distMiles.toFixed(2)}miles</span>`;
        }
        return html;
    }

    // --- Packet Log ---
    function addLogRow(packet) {
        const logList = document.getElementById('log-list');

        // Create row
        const row = document.createElement('div');
        row.className = 'log-row';
        row.dataset.callsign = packet.from_call || '';
        row.dataset.type = packet.type || '';
        row.dataset.tx = packet.tx ? 'tx' : 'rx';
        // Store searchable text for full-text search (#32)
        row.dataset.search = [
            packet.from_call || '',
            packet.to_call || '',
            packet.human_info || '',
            packet.comment || '',
            packet.raw_packet || '',
        ].join(' ').toLowerCase();

        // Expand toggle
        const expand = document.createElement('span');
        expand.className = 'log-expand';
        expand.textContent = '\u25B6';  // ▶

        // Compact log content — rendered entirely in the frontend using CSS variables
        const content = document.createElement('span');
        content.className = 'log-content';
        content.innerHTML = renderCompactLog(packet);

        // Timestamp (hidden by default, toggled via settings)
        const ts = document.createElement('span');
        ts.className = 'log-timestamp';
        if (packet.timestamp) {
            const d = new Date(packet.timestamp * 1000);
            ts.textContent = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
        }
        if (!showTimestamps) ts.style.display = 'none';

        row.appendChild(expand);
        row.appendChild(ts);
        row.appendChild(content);

        // Audio level indicator (RX packets only, when level is present)
        const audioLevel = packet.audio_level;
        if (!packet.tx && audioLevel != null && audioLevel > 0) {
            const sigEl = document.createElement('span');
            sigEl.className = 'log-signal';
            sigEl.title = `Audio level: ${audioLevel}`;

            // Normalize 0–100 to 1–5 bars (Direwolf reports roughly 0–100)
            const bars = Math.max(1, Math.min(5, Math.round(audioLevel / 20)));
            for (let i = 1; i <= 5; i++) {
                const bar = document.createElement('span');
                bar.className = 'signal-bar' + (i <= bars ? ' active' : '');
                sigEl.appendChild(bar);
            }
            row.appendChild(sigEl);
        }

        // Row action buttons (copy + decode)
        const aprsStr = packet.raw_packet || '';
        const rowActions = document.createElement('span');
        rowActions.className = 'log-row-actions';

        if (aprsStr) {
            const copyBtn = document.createElement('button');
            copyBtn.className = 'log-row-btn';
            copyBtn.title = 'Copy APRS packet';
            copyBtn.innerHTML = '&#x1F4CB;';
            copyBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                copyToClipboard(aprsStr).then(() => {
                    copyBtn.innerHTML = '&#x2713;';
                    copyBtn.classList.add('copied');
                    setTimeout(() => { copyBtn.innerHTML = '&#x1F4CB;'; copyBtn.classList.remove('copied'); }, 1500);
                });
            });
            rowActions.appendChild(copyBtn);

            const decodeBtn = document.createElement('button');
            decodeBtn.className = 'log-row-btn';
            decodeBtn.title = 'Decode this packet';
            decodeBtn.innerHTML = '&#x1F4E1;';
            decodeBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                openDecodeModal();
                const input = document.getElementById('decode-input');
                input.value = aprsStr;
                input.dispatchEvent(new Event('input'));
                submitDecode();
            });
            rowActions.appendChild(decodeBtn);
        }

        row.appendChild(rowActions);

        // Raw log lines (hidden by default)
        const rawDiv = document.createElement('div');
        rawDiv.className = 'log-raw';

        // Show the clean APRS string prominently
        if (aprsStr) {
            const aprsLine = document.createElement('div');
            aprsLine.className = 'log-raw-aprs';

            const aprsText = document.createElement('code');
            aprsText.textContent = aprsStr;
            aprsLine.appendChild(aprsText);

            rawDiv.appendChild(aprsLine);
        }

        // Toggle expand/collapse
        row.addEventListener('click', () => {
            expand.classList.toggle('expanded');
            rawDiv.classList.toggle('visible');
        });

        // Apply current filters
        applyFilterToRow(row);

        // Prepend (newest first)
        logList.insertBefore(rawDiv, logList.firstChild);
        logList.insertBefore(row, logList.firstChild);

        // Trim old rows
        while (logList.children.length > MAX_LOG_ROWS * 2) {
            logList.removeChild(logList.lastChild);
        }

        // Auto-scroll
        if (autoScroll) {
            logList.scrollTop = 0;
        }
    }

    // --- Filters ---
    function initFilters() {
        const callsignInput = document.getElementById('filter-callsign');
        if (!callsignInput) return; // filter overlay not rendered (e.g. no WebGL2)
        const typeSelect = document.getElementById('filter-type');
        const txrxSelect = document.getElementById('filter-txrx');
        const trailSelect = document.getElementById('trail-duration');

        callsignInput.addEventListener('input', applyFilters);
        typeSelect.addEventListener('change', applyFilters);
        txrxSelect.addEventListener('change', applyFilters);

        // Search input — filter as user types
        const searchInput = document.getElementById('filter-search');
        if (searchInput) {
            searchInput.addEventListener('input', applyFilters);
        }

        // Trail duration dropdown
        trailSelect.addEventListener('change', (e) => {
            trailHours = parseInt(e.target.value, 10);
            loadTracks();
        });

        // Auto-scroll detection
        const logList = document.getElementById('log-list');
        const resumeBtn = document.getElementById('btn-resume');

        logList.addEventListener('scroll', () => {
            if (logList.scrollTop > 50) {
                autoScroll = false;
                resumeBtn.classList.remove('hidden');
            }
        });

        resumeBtn.addEventListener('click', () => {
            autoScroll = true;
            logList.scrollTop = 0;
            resumeBtn.classList.add('hidden');
        });

        // Clear log
        document.getElementById('btn-clear-log').addEventListener('click', () => {
            logList.innerHTML = '';
            autoScroll = true;
            resumeBtn.classList.add('hidden');
        });

        // Ctrl+F / Cmd+F: focus the search input
        document.addEventListener('keydown', (e) => {
            if ((e.ctrlKey || e.metaKey) && e.key === 'f') {
                const searchEl = document.getElementById('filter-search');
                if (searchEl) {
                    e.preventDefault();
                    searchEl.focus();
                    searchEl.select();
                }
            }
        });
    }

    function applyFilters() {
        const callsign = document.getElementById('filter-callsign').value.toUpperCase();
        const type = document.getElementById('filter-type').value;
        const txrx = document.getElementById('filter-txrx').value;
        const searchEl = document.getElementById('filter-search');
        const search = searchEl ? searchEl.value.toLowerCase().trim() : '';

        const rows = document.querySelectorAll('.log-row');
        rows.forEach(row => {
            applyFilterToRow(row, callsign, type, txrx, search);
        });

        // Filter map markers + track lines (rebuild the GeoJSON sources)
        scheduleStationRefresh();
        refreshStationTrackSource();
    }

    function applyFilterToRow(row, callsign, type, txrx, search) {
        callsign = callsign || document.getElementById('filter-callsign').value.toUpperCase();
        type = type || document.getElementById('filter-type').value;
        txrx = txrx || document.getElementById('filter-txrx').value;
        if (search === undefined) {
            const searchEl = document.getElementById('filter-search');
            search = searchEl ? searchEl.value.toLowerCase().trim() : '';
        }

        let visible = true;
        // Follow-a-station (#206): while following, the live log shows only
        // that station's packets (sender or addressee — search carries raw).
        const followCs = followLogCallsign();
        if (followCs && row.dataset.callsign !== followCs &&
            (row.dataset.search || '').indexOf(followCs.toLowerCase()) === -1) visible = false;
        if (callsign && !row.dataset.callsign.includes(callsign)) visible = false;
        if (type && row.dataset.type !== type) visible = false;
        if (txrx && row.dataset.tx !== txrx) visible = false;
        if (search && !(row.dataset.search || '').includes(search)) visible = false;

        if (visible) {
            row.classList.remove('hidden-by-filter');
        } else {
            row.classList.add('hidden-by-filter');
        }
    }

    // --- Settings ---
    function initSettings() {
        const modal = document.getElementById('settings-modal');
        const btnOpen = document.getElementById('btn-settings');
        const btnClose = document.getElementById('btn-close-settings');
        const btnSave = document.getElementById('btn-save-settings');

        btnOpen.addEventListener('click', () => {
            populateSettings();
            modal.classList.remove('hidden');
        });

        btnClose.addEventListener('click', () => {
            modal.classList.add('hidden');
        });

        modal.addEventListener('click', (e) => {
            if (e.target === modal) modal.classList.add('hidden');
        });

        btnSave.addEventListener('click', saveSettings);

        // Reset log colors button
        const btnResetColors = document.getElementById('btn-reset-log-colors');
        if (btnResetColors) btnResetColors.addEventListener('click', _resetLogColors);

        // Wipe database
        document.getElementById('btn-wipe-db').addEventListener('click', wipeDatabase);
    }

    function populateSettings() {
        document.getElementById('cfg-latitude').value = config.station?.latitude || 0;
        document.getElementById('cfg-longitude').value = config.station?.longitude || 0;
        document.getElementById('cfg-zoom').value = config.station?.zoom || 12;
        document.getElementById('cfg-agw-host').value = config.direwolf?.agw_host || 'localhost';
        document.getElementById('cfg-agw-port').value = config.direwolf?.agw_port || 8000;
        document.getElementById('cfg-log-file').value = config.direwolf?.log_file || '';
        document.getElementById('cfg-server-port').value = config.server?.port || 8080;
        document.getElementById('cfg-api-token').value = localStorage.getItem(API_TOKEN_KEY) || '';

        document.getElementById('cfg-units').value = getUnits();
        document.getElementById('cfg-retention').value = config.storage?.retention_days || 7;

        // Map display settings
        document.getElementById('cfg-show-route-distances').checked = showRouteDistances;
        document.getElementById('cfg-show-gpx-overlay').checked = showGpxOverlay;
        document.getElementById('cfg-show-stats-overlay').checked = showStatsOverlay;
        document.getElementById('cfg-show-my-location-overlay').checked = showMyLocationOverlay;

        // Packet log settings
        document.getElementById('cfg-show-timestamps').checked = showTimestamps;

        // Log color inputs
        _populateLogColorInputs();

        // Offline map region list (issue #212) — fetched fresh each open so a
        // download on another browser/tab shows up.
        loadBasemapRegions();

    }

    // --- Offline map regions (issue #212) ---------------------------------
    // Hemna's call on PR #213: the kit region list belongs in Settings, where
    // the user picks which region archives this host downloads and caches for
    // offline map use. GET /api/basemap/regions serves the live kit manifest
    // with the server-side fallbacks; the map-side panel stays gone.
    const BASEMAP_REGIONS_URL = API_BASE + '/basemap/regions';

    /** Bytes -> B/KiB/MiB display text. */
    function formatBytes(bytes) {
        if (typeof bytes !== 'number' || bytes < 0) return '?';
        if (bytes >= 1048576) return (bytes / 1048576).toFixed(1) + ' MiB';
        if (bytes >= 1024) return (bytes / 1024).toFixed(1) + ' KiB';
        return bytes + ' B';
    }

    /** One region row: name, size/state, and a Download or Remove button. */
    function regionRow(r, feedback) {
        const row = document.createElement('div');
        row.className = 'basemap-region-row';

        const name = document.createElement('span');
        name.className = 'basemap-region-name';
        name.textContent = r.name && r.name !== r.id ? r.name + ' (' + r.id + ')' : r.id;

        const state = document.createElement('span');
        state.className = 'basemap-region-state';
        state.textContent = r.downloaded
            ? 'Downloaded \u2014 ' + formatBytes(r.downloaded_bytes)
            : 'Not downloaded' + (typeof r.bytes === 'number' ? ' \u2014 ' + formatBytes(r.bytes) : '');

        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = r.downloaded ? 'btn-danger' : 'secondary';
        btn.textContent = r.downloaded ? 'Remove' : 'Download';
        btn.addEventListener('click', function () {
            if (r.downloaded) removeBasemapRegion(r, feedback);
            else downloadBasemapRegion(r, feedback);
        });

        row.appendChild(name);
        row.appendChild(state);
        row.appendChild(btn);
        return row;
    }

    /** Render the region list from GET /api/basemap/regions. */
    async function loadBasemapRegions() {
        const list = document.getElementById('basemap-region-list');
        if (!list) return;
        list.textContent = 'Loading\u2026';
        let regions;
        try {
            const resp = await fetch(BASEMAP_REGIONS_URL);
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            regions = (await resp.json()).regions || [];
        } catch (e) {
            list.textContent = 'Region list unavailable: ' + e.message;
            return;
        }
        list.textContent = '';
        if (!regions.length) {
            list.textContent = 'No regions published.';
            return;
        }
        const feedback = document.getElementById('basemap-region-feedback');
        regions.forEach(function (r) { list.appendChild(regionRow(r, feedback)); });
    }

    /** POST the download; the request returns once the archive is on disk. */
    async function downloadBasemapRegion(r, feedback) {
        feedback.className = 'warning';
        feedback.textContent = 'Downloading ' + r.id + '\u2026 this can take a while.';
        feedback.classList.remove('hidden');
        try {
            const resp = await fetch(
                BASEMAP_REGIONS_URL + '/' + encodeURIComponent(r.id) + '/download',
                { method: 'POST', headers: _apiHeaders({}) },
            );
            const result = await resp.json().catch(function () { return {}; });
            if (!resp.ok) throw new Error(result.detail || ('HTTP ' + resp.status));
            feedback.className = 'success';
            feedback.textContent = 'Downloaded ' + r.id + ' (' + formatBytes(result.size) + ')';
        } catch (e) {
            feedback.className = 'error';
            feedback.textContent = 'Download failed: ' + e.message;
        }
        await loadBasemapRegions();
    }

    /** DELETE the downloaded archive; leaves any vendored copy untouched. */
    async function removeBasemapRegion(r, feedback) {
        try {
            const resp = await fetch(
                BASEMAP_REGIONS_URL + '/' + encodeURIComponent(r.id),
                { method: 'DELETE', headers: _apiHeaders({}) },
            );
            const result = await resp.json().catch(function () { return {}; });
            if (!resp.ok) throw new Error(result.detail || ('HTTP ' + resp.status));
            feedback.className = 'success';
            feedback.textContent = result.removed
                ? 'Removed ' + r.id
                : r.id + ' has no downloaded copy';
        } catch (e) {
            feedback.className = 'error';
            feedback.textContent = 'Remove failed: ' + e.message;
        }
        await loadBasemapRegions();
    }

    async function saveSettings() {
        const updates = {
            station: {
                latitude: parseFloat(document.getElementById('cfg-latitude').value),
                longitude: parseFloat(document.getElementById('cfg-longitude').value),
                zoom: parseInt(document.getElementById('cfg-zoom').value) || 12,
            },
            direwolf: {
                agw_host: document.getElementById('cfg-agw-host').value,
                agw_port: parseInt(document.getElementById('cfg-agw-port').value),
                log_file: document.getElementById('cfg-log-file').value,
            },
            server: {
                port: parseInt(document.getElementById('cfg-server-port').value),
            },
            storage: {
                retention_days: parseInt(document.getElementById('cfg-retention').value),
            },
            display: {
                show_route_distances: document.getElementById('cfg-show-route-distances').checked,
                show_gpx_overlay: document.getElementById('cfg-show-gpx-overlay').checked,
                show_stats_overlay: document.getElementById('cfg-show-stats-overlay').checked,
                show_my_location_overlay: document.getElementById('cfg-show-my-location-overlay').checked,
            },
            packet_log: {
                show_timestamps: document.getElementById('cfg-show-timestamps').checked,
            },
        };

        // Save log colors (localStorage-only, not in the backend config)
        _saveLogColors();

        // Save API token locally (never sent to the backend config)
        const tokenInput = document.getElementById('cfg-api-token');
        if (tokenInput) {
            const token = tokenInput.value.trim();
            if (token) localStorage.setItem(API_TOKEN_KEY, token);
            else localStorage.removeItem(API_TOKEN_KEY);
        }

        // Save units preference locally
        const unitsInput = document.getElementById('cfg-units');
        if (unitsInput) localStorage.setItem(UNITS_KEY, unitsInput.value);

        const feedback = document.getElementById('settings-feedback');
        try {
            const resp = await fetch(API_BASE + '/config', {
                method: 'PUT',
                headers: _apiHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify(updates),
            });
            const result = await resp.json();

            if (resp.ok) {
                feedback.className = result.restart_required ? 'warning' : 'success';
                feedback.textContent = result.restart_required
                    ? `Saved. Restart required for: ${result.updated.join(', ')}`
                    : `Saved: ${result.updated.join(', ') || 'no changes'}`;
                feedback.classList.remove('hidden');
                // Merge section-wise: an ordinary save must not drop fields it
                // did not submit, such as station.my_position (issue #172).
                const merged = { ...config };
                for (const [section, values] of Object.entries(updates)) {
                    merged[section] = { ...(config[section] || {}), ...values };
                }
                config = merged;
                // Apply display settings immediately
                showRouteDistances = updates.display.show_route_distances;
                showGpxOverlay = updates.display.show_gpx_overlay;
                if (gpxControlEl) {
                    gpxControlEl.style.display = showGpxOverlay ? '' : 'none';
                }
                showStatsOverlay = updates.display.show_stats_overlay;
                if (statsOverlayEl) {
                    statsOverlayEl.style.display = showStatsOverlay ? '' : 'none';
                }
                showMyLocationOverlay = updates.display.show_my_location_overlay;
                if (myLocationOverlayEl) {
                    myLocationOverlayEl.style.display = showMyLocationOverlay ? '' : 'none';
                }
                // Apply packet log settings immediately
                showTimestamps = updates.packet_log.show_timestamps;
                document.querySelectorAll('.log-timestamp').forEach(function (el) {
                    el.style.display = showTimestamps ? '' : 'none';
                });
                // Close settings modal
                document.getElementById('settings-modal').classList.add('hidden');
                feedback.classList.add('hidden');
            } else {
                feedback.className = 'error';
                feedback.textContent = `Error: ${JSON.stringify(result.detail)}`;
                feedback.classList.remove('hidden');
            }
        } catch (e) {
            feedback.className = 'error';
            feedback.textContent = `Error: ${e.message}`;
            feedback.classList.remove('hidden');
        }
    }

    var SKIP_CLEAR_CONFIRM_KEY = 'direwolf_dashboard_skip_clear_confirm';

    async function wipeDatabase() {
        // Check if user has opted out of confirmation
        if (localStorage.getItem(SKIP_CLEAR_CONFIRM_KEY) === 'true') {
            await performClearData();
            return;
        }
        var modal = document.getElementById('confirm-wipe-modal');
        // Reset the checkbox each time
        document.getElementById('chk-dont-ask-again').checked = false;
        modal.classList.remove('hidden');
    }

    async function performClearData() {
        try {
            var resp = await fetch(API_BASE + '/storage', {
                method: 'DELETE',
                headers: _apiHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ confirm: true }),
            });
            var result = await resp.json();
            if (resp.ok) {
                clearStationsAndPackets();
                // Reset my position (stations are gone, stale position is misleading)
                saveMyPosition(null);
            } else {
                console.error('Clear data failed:', result.detail || 'Unknown error');
            }
        } catch (e) {
            console.error('Clear data error:', e.message);
        }
    }

    function initClearDataFab() {
        var btn = document.getElementById('btn-clear-data');
        if (!btn) return;
        btn.addEventListener('click', wipeDatabase);
    }

    function initConfirmWipeModal() {
        var modal = document.getElementById('confirm-wipe-modal');
        var btnClose = document.getElementById('btn-close-confirm-wipe');
        var btnCancel = document.getElementById('btn-cancel-wipe');
        var btnConfirm = document.getElementById('btn-confirm-wipe');
        var chkDontAsk = document.getElementById('chk-dont-ask-again');

        function closeModal() { modal.classList.add('hidden'); }

        btnClose.addEventListener('click', closeModal);
        btnCancel.addEventListener('click', closeModal);
        modal.addEventListener('click', function (e) {
            if (e.target === modal) closeModal();
        });

        btnConfirm.addEventListener('click', async function () {
            // Save "don't ask again" preference if checked
            if (chkDontAsk.checked) {
                localStorage.setItem(SKIP_CLEAR_CONFIRM_KEY, 'true');
            }
            closeModal();
            var btn = document.getElementById('btn-wipe-db');
            var feedback = document.getElementById('wipe-feedback');
            btn.disabled = true;
            btn.textContent = 'Clearing...';
            feedback.classList.add('hidden');
            try {
                await performClearData();
                feedback.className = 'success';
                feedback.textContent = 'Data cleared';
                feedback.classList.remove('hidden');
            } catch (e) {
                feedback.className = 'error';
                feedback.textContent = 'Error: ' + e.message;
                feedback.classList.remove('hidden');
            } finally {
                btn.disabled = false;
                btn.textContent = 'Wipe Packet Database';
            }
        });
    }

    function clearStationsAndPackets() {
        // Clear the packet log
        var logList = document.getElementById('log-list');
        if (logList) logList.innerHTML = '';
        autoScroll = true;
        var resumeBtn = document.getElementById('btn-resume');
        if (resumeBtn) resumeBtn.classList.add('hidden');
        // Remove station features / markers from map
        Object.keys(stations).forEach(function (call) {
            delete stationFeatures[call];
        });
        stations = {};
        scheduleStationRefresh();
        // Clear track / overlay / arc line data (maplibre GeoJSON sources)
        for (const cs of Object.keys(stationTracks)) delete stationTracks[cs];
        _overlayFC = { type: 'FeatureCollection', features: [] };
        _arcFC = { type: 'FeatureCollection', features: [] };
        refreshStationTrackSource();
        refreshOverlaySource();
        refreshArcSource();
        // Clear position cache
        stationPositionCache.clear();
        // Re-apply my position marker (uses saved coords from config as fallback)
        updateMyPositionMarker();
    }

    // --- Log View Toggle ---
    function initLogToggle() {
        const btn = document.getElementById('btn-toggle-log');
        const mapContainer = document.getElementById('map-container');

        // Determine initial state
        const saved = localStorage.getItem(LOG_STATE_KEY);
        if (saved && LOG_STATES.includes(saved)) {
            logViewState = saved;
        } else if (window.innerWidth < 768) {
            logViewState = 'hidden';
        } else {
            logViewState = 'visible';
        }

        // Set default map height to 2/3 viewport if no saved height exists
        if (!localStorage.getItem(MAP_HEIGHT_KEY)) {
            var footer = document.getElementById('footer');
            var chrome = toolbarChromeHeight() + (footer ? footer.offsetHeight : 24);
            var available = window.innerHeight - chrome;
            var defaultMapHeight = Math.round(available * 2 / 3);
            localStorage.setItem(MAP_HEIGHT_KEY, defaultMapHeight + 'px');
        }

        applyLogViewState();

        // Click to toggle
        btn.addEventListener('click', toggleLogViewState);

        // Keyboard shortcut: L to toggle (when not focused on an input)
        document.addEventListener('keydown', (e) => {
            if (e.key === 'l' || e.key === 'L') {
                const tag = document.activeElement?.tagName;
                if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;
                e.preventDefault();
                toggleLogViewState();
            }
        });

        // After CSS transition ends on map container, tell Leaflet to resize
        mapContainer.addEventListener('transitionend', (e) => {
            if (e.propertyName === 'height' || e.propertyName === 'flex') {
                if (map) map.resize();
            }
        });
    }

    function toggleLogViewState() {
        logViewState = (logViewState === 'visible') ? 'hidden' : 'visible';
        localStorage.setItem(LOG_STATE_KEY, logViewState);
        applyLogViewState();
    }

    function applyLogViewState() {
        const body = document.body;
        const btn = document.getElementById('btn-toggle-log');
        const mapContainer = document.getElementById('map-container');
        var logContainer = document.getElementById('log-container');

        // Remove all state classes, apply current
        for (const s of LOG_STATES) {
            body.classList.remove('log-' + s);
        }
        body.classList.add('log-' + logViewState);

        if (logViewState === 'hidden') {
            mapContainer.style.height = '';
            mapContainer.style.flex = '';
            logContainer.style.flex = '';
            logContainer.style.height = '';
        } else {
            // Restore saved map height (defaults to 2/3 viewport, set in initLogToggle)
            var savedHeight = localStorage.getItem(MAP_HEIGHT_KEY);
            if (savedHeight) {
                mapContainer.style.height = savedHeight;
                mapContainer.style.flex = 'none';
                logContainer.style.flex = '1';
                logContainer.style.height = '';
            } else {
                mapContainer.style.height = '';
                mapContainer.style.flex = '';
                logContainer.style.flex = '';
                logContainer.style.height = '';
            }
        }

        // Update button icon and title
        if (logViewState === 'hidden') {
            btn.innerHTML = '<span class="toggle-icon">\u25B2</span>';  // ▲
            btn.title = 'Show packet log (L)';
        } else {
            btn.innerHTML = '<span class="toggle-icon">\u25BC</span>';  // ▼
            btn.title = 'Hide packet log (L)';
        }

        // Enable resize when log is visible
        resizeEnabled = (logViewState !== 'hidden');

        // Invalidate map size after a brief delay for transition
        if (map) {
            setTimeout(() => map.resize(), 350);
        }
    }

    // --- Map Resize ---
    var MAP_HEIGHT_KEY = 'dw-map-height';

    function initMapResize() {
        const handle = document.getElementById('map-resize-handle');
        const mapContainer = document.getElementById('map-container');
        let startY, startHeight, dragging = false;

        function beginResize(clientY, e) {
            if (!resizeEnabled) return;
            startY = clientY;
            startHeight = mapContainer.offsetHeight;
            dragging = true;
            var logContainer = document.getElementById('log-container');
            // Disable CSS transitions during drag for instant feedback
            mapContainer.style.transition = 'none';
            logContainer.style.transition = 'none';
            // Override flex so pixel height is respected
            mapContainer.style.flex = 'none';
            logContainer.style.flex = '1';
            logContainer.style.height = '';
            document.addEventListener('mousemove', onMouseMove);
            document.addEventListener('mouseup', stopResize);
            document.addEventListener('touchmove', onTouchMove, { passive: false });
            document.addEventListener('touchend', stopResize);
            e.preventDefault();
        }

        function doResize(clientY) {
            var footer = document.getElementById('footer');
            var minMap = 150;
            var minLog = 80;
            var maxMap = window.innerHeight - toolbarChromeHeight() - (footer ? footer.offsetHeight : 24) - minLog;
            var newHeight = Math.max(minMap, Math.min(maxMap, startHeight + (clientY - startY)));
            mapContainer.style.height = newHeight + 'px';
            map.resize();
        }

        function onMouseMove(e) { doResize(e.clientY); }
        function onTouchMove(e) {
            if (e.touches.length === 1) {
                e.preventDefault();
                doResize(e.touches[0].clientY);
            }
        }

        function stopResize() {
            dragging = false;
            document.removeEventListener('mousemove', onMouseMove);
            document.removeEventListener('mouseup', stopResize);
            document.removeEventListener('touchmove', onTouchMove);
            document.removeEventListener('touchend', stopResize);
            // Re-enable transitions
            mapContainer.style.transition = '';
            document.getElementById('log-container').style.transition = '';
            // Persist the resized height
            if (mapContainer.style.height) {
                localStorage.setItem(MAP_HEIGHT_KEY, mapContainer.style.height);
            }
            map.resize();
        }

        handle.addEventListener('mousedown', function (e) { beginResize(e.clientY, e); });
        handle.addEventListener('touchstart', function (e) {
            if (e.touches.length === 1) beginResize(e.touches[0].clientY, e);
        }, { passive: false });
    }

    // --- Cold Start / Map Centering ---
    async function initMapCenter() {
        // Priority -1 (#150): an explicit #zoom/lat/lon in the URL wins over the
        // saved center and over waiting/auto-follow; the map already opened there.
        if (urlMapView) return;

        // Priority 0: restore last center from localStorage (zoom always from config)
        var savedLat = localStorage.getItem('dw-map-lat');
        var savedLng = localStorage.getItem('dw-map-lng');
        if (savedLat !== null && savedLng !== null) {
            const zoom = config.station?.zoom || 12;
            mapSetView(parseFloat(savedLat), parseFloat(savedLng), toMlZoom(zoom));
            return;
        }

        // Priority 1: my_position resolved coords
        const myPos = getMyPosition();
        if (myPos) {
            const zoom = config.station?.zoom || 12;
            mapSetView(myPos.lat, myPos.lng, toMlZoom(zoom));
            return;
        }

        // Priority 2: config lat/lon if non-zero
        const lat = config.station?.latitude || 0;
        const lon = config.station?.longitude || 0;
        if (lat !== 0 || lon !== 0) {
            const zoom = config.station?.zoom || 12;
            mapSetView(lat, lon, toMlZoom(zoom));
            return;
        }

        // Priority 3: Most recent station from DB (already loaded)
        const stationKeys = Object.keys(stations);
        if (stationKeys.length > 0) {
            // Find most recently seen station
            let best = null;
            let bestTime = 0;
            for (const cs of stationKeys) {
                const s = stations[cs];
                if (s.data && s.data.latitude != null && s.data.longitude != null) {
                    const t = s.data.last_seen || 0;
                    if (!best || t > bestTime) {
                        best = s;
                        bestTime = t;
                    }
                }
            }
            if (best) {
                const zoom = config.station?.zoom || 12;
                mapSetView(best.data.latitude, best.data.longitude, toMlZoom(zoom));
                return;
            }
        }

        // Priority 4: Cold start - center (0,0) zoom 3, show waiting modal
        mapSetView(0, 0, toMlZoom(3));
        showWaitingModal();
    }

    function showWaitingModal() {
        waitingForPosition = true;
        const modal = document.getElementById('waiting-modal');
        if (modal) modal.classList.remove('hidden');

        const btnClose = document.getElementById('btn-close-waiting');
        if (btnClose) {
            btnClose.addEventListener('click', () => {
                modal.classList.add('hidden');
                showWaitingToast();
            });
        }
        // Also close when clicking backdrop
        if (modal) {
            modal.addEventListener('click', (e) => {
                if (e.target === modal) {
                    modal.classList.add('hidden');
                    showWaitingToast();
                }
            });
        }
    }

    // Issue #218 round 14: the waiting message is a row of the layout (see style.css .toast), so
    // nothing here computes a position. Round 13 measured a clear spot and
    // still lost at 344-375px wide and 320x400, where no clear spot exists:
    // 802.2 / 330.2 / 3120.8 px^2 at 344x800, 715 / 279 / 2988 at 360x800,
    // 632.2 / 230.4 / 1568.8 at 375x800, all over the GPX panel, its Load GPX
    // button and the legend. A row cannot overlap anything it does not float
    // over, which is also why this needs no collision check at all.
    function showWaitingToast() {
        const chip = document.getElementById('status-chip');
        if (chip) chip.classList.remove('hidden');
    }

    function dismissWaiting() {
        waitingForPosition = false;
        const modal = document.getElementById('waiting-modal');
        if (modal) modal.classList.add('hidden');
        const chip = document.getElementById('status-chip');
        if (chip) chip.classList.add('hidden');
    }


    // --- My Position Marker ---
    function updateMyPositionMarker() {
        // Remove old pin marker if present
        if (myPositionPinMarker) {
            myPositionPinMarker.remove();
            myPositionPinMarker = null;
        }

        const mp = config.station?.my_position;
        if (!mp || !mp.type) {
            scheduleStationRefresh();
            return;
        }

        if (mp.type === 'pin' && mp.latitude != null && mp.longitude != null) {
            if (!map) return;  // no WebGL2 / map failed to init
            const el = document.createElement('div');
            el.className = 'my-position-pin';
            el.style.width = '24px';   // old Leaflet iconSize [24, 24]
            el.style.height = '24px';
            el.innerHTML = '<div class="pin-marker">📌</div>';
            const popupEl = document.createElement('div');
            popupEl.innerHTML = '<b>My Position</b><br>(dropped pin)<br>';
            const btn = document.createElement('button');
            btn.className = 'popup-btn popup-btn-remove';
            btn.textContent = 'Remove Pin';
            btn.addEventListener('click', window._removeMyPosition);
            popupEl.appendChild(btn);
            // Bottom-anchored like the old Leaflet divIcon (iconAnchor [12, 24]).
            myPositionPinMarker = new maplibregl.Marker({ element: el, anchor: 'bottom' })
                .setLngLat([mp.longitude, mp.latitude])
                .setPopup(new maplibregl.Popup({ offset: 24 }).setDOMContent(popupEl))
                .addTo(map);
        } else if (mp.type === 'station' && mp.callsign) {
            // Highlight handled via the is_my glow layers (stations-my-glow*).
            scheduleStationRefresh();
        }
        updateMyLocationOverlay();
    }

    async function saveMyPosition(myPosition) {
        try {
            const resp = await fetch(API_BASE + '/config', {
                method: 'PUT',
                headers: _apiHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ station: { my_position: myPosition } }),
            });
            if (resp.ok) {
                if (!config.station) config.station = {};
                config.station.my_position = myPosition;
                updateMyPositionMarker();
                // Re-render all popups
                for (const cs of Object.keys(stations)) {
                    updateStationPopup(cs);
                }
                // Fly to position
                const pos = getMyPosition();
                if (pos) {
                    const zoom = config.station?.zoom || 12;
                    mapFlyTo(pos.lat, pos.lng, toMlZoom(zoom));
                }
                // Dismiss waiting if active
                if (waitingForPosition) dismissWaiting();
            }
        } catch (e) {
            console.error('Failed to save my position:', e);
        }
    }

    // Expose to popup onclick handlers
    window._setMyPositionStation = function (callsign) {
        saveMyPosition({ type: 'station', callsign: callsign });
        closeActivePopup();
    };

    window._removeMyPosition = function () {
        saveMyPosition(null);
        closeActivePopup();
    };

    // --- Weather Modal ---
    window._viewWeather = function (callsign) {
        closeActivePopup();
        openWeatherModal(callsign);
    };

    window._downloadGpx = function (callsign) {
        closeActivePopup();
        // Trigger download via a temporary link
        const url = API_BASE + '/station/' + encodeURIComponent(callsign) + '/gpx?hours=24';
        const a = document.createElement('a');
        a.href = url;
        a.download = callsign.replace(/\s/g, '_') + '_24h.gpx';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
    };

    let weatherTempChart = null;
    let weatherPressureChart = null;

    function initWeatherModal() {
        const modal = document.getElementById('weather-modal');
        const closeBtn = document.getElementById('btn-close-weather');

        closeBtn.addEventListener('click', closeWeatherModal);
        modal.addEventListener('click', (e) => {
            if (e.target === modal) closeWeatherModal();
        });
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && !modal.classList.contains('hidden')) {
                closeWeatherModal();
            }
        });
    }

    function initAboutModal() {
        const modal = document.getElementById('about-modal');
        const closeBtn = document.getElementById('btn-close-about');
        const openBtn = document.getElementById('btn-about');
        const logoBtn = document.getElementById('btn-about-logo');

        function openAbout(e) {
            e.preventDefault();
            const ver = document.getElementById('app-version')?.textContent || '1.0.0';
            document.getElementById('about-version').textContent = ver;
            modal.classList.remove('hidden');
        }

        openBtn.addEventListener('click', openAbout);
        if (logoBtn) logoBtn.addEventListener('click', openAbout);
        closeBtn.addEventListener('click', () => modal.classList.add('hidden'));
        modal.addEventListener('click', (e) => {
            if (e.target === modal) modal.classList.add('hidden');
        });
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && !modal.classList.contains('hidden')) {
                modal.classList.add('hidden');
            }
        });
    }

    function initChangelogModal() {
        const modal = document.getElementById('changelog-modal');
        const closeBtn = document.getElementById('btn-close-changelog');
        const showBtn = document.getElementById('btn-show-changelog');

        showBtn.addEventListener('click', (e) => {
            e.preventDefault();
            // Close about modal and open changelog
            document.getElementById('about-modal').classList.add('hidden');
            openChangelogModal();
        });
        closeBtn.addEventListener('click', () => modal.classList.add('hidden'));
        modal.addEventListener('click', (e) => {
            if (e.target === modal) modal.classList.add('hidden');
        });
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && !modal.classList.contains('hidden')) {
                modal.classList.add('hidden');
            }
        });
    }

    async function openChangelogModal() {
        const modal = document.getElementById('changelog-modal');
        const loading = document.getElementById('changelog-loading');
        const content = document.getElementById('changelog-content');

        loading.classList.remove('hidden');
        content.classList.add('hidden');
        modal.classList.remove('hidden');

        try {
            const resp = await fetch(API_BASE + '/changelog');
            if (!resp.ok) throw new Error('Failed to load changelog');
            let md = await resp.text();
            // Strip the top-level title and intro line (already in modal header)
            md = md.replace(/^# Changelog\n+(?:All notable.*\n+)?/, '');
            content.innerHTML = renderMarkdown(md);
            loading.classList.add('hidden');
            content.classList.remove('hidden');
        } catch (err) {
            loading.textContent = 'Failed to load changelog.';
        }
    }

    function renderMarkdown(md) {
        // Simple markdown to HTML for changelogs
        // First, join continuation lines (indented lines following a list item)
        const rawLines = md.split('\n');
        const lines = [];
        for (let i = 0; i < rawLines.length; i++) {
            const line = rawLines[i];
            // Continuation: non-empty line starting with spaces, previous was a list item or continuation
            if (line.match(/^  \S/) && lines.length > 0 && lines[lines.length - 1].match(/^- /)) {
                // Append to previous line
                lines[lines.length - 1] += ' ' + line.trim();
            } else {
                lines.push(line);
            }
        }

        let html = '';
        let inList = false;

        for (let i = 0; i < lines.length; i++) {
            let line = lines[i];

            // Headings
            if (line.startsWith('### ')) {
                if (inList) { html += '</ul>'; inList = false; }
                html += '<h4 class="cl-h4">' + escHtml(line.slice(4)) + '</h4>';
                continue;
            }
            if (line.startsWith('## ')) {
                if (inList) { html += '</ul>'; inList = false; }
                html += '<h3 class="cl-h3">' + escHtml(line.slice(3)) + '</h3>';
                continue;
            }
            if (line.startsWith('# ')) {
                if (inList) { html += '</ul>'; inList = false; }
                html += '<h2 class="cl-h2">' + escHtml(line.slice(2)) + '</h2>';
                continue;
            }

            // List items
            if (line.match(/^- /)) {
                if (!inList) { html += '<ul>'; inList = true; }
                html += '<li>' + inlineFormat(line.slice(2)) + '</li>';
                continue;
            }

            // Blank line
            if (line.trim() === '') {
                if (inList) { html += '</ul>'; inList = false; }
                continue;
            }

            // Continuation of list context (indented text after a list)
            if (inList && line.match(/^\s/)) {
                // Append to last li — reopen it
                html = html.replace(/<\/li>$/, ' ' + inlineFormat(line.trim()) + '</li>');
                continue;
            }

            // Plain text
            if (inList) { html += '</ul>'; inList = false; }
            html += '<p>' + inlineFormat(line) + '</p>';
        }
        if (inList) html += '</ul>';
        return html;
    }

    function escHtml(str) {
        // Escape &, <, >, and " so this is safe in both text content and
        // double-quoted HTML attribute values (#60)
        return String(str || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function inlineFormat(str) {
        let s = escHtml(str);
        // Bold
        s = s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        // Inline code
        s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
        // Links [text](url) — only allow safe schemes (http/https/mailto);
        // other URLs (e.g. javascript:) are left as plain text. (issue #90)
        s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (m, text, url) => {
            const trimmed = url.trim();
            if (/^(https?:|mailto:)/i.test(trimmed)) {
                return `<a href="${escHtml(trimmed)}" target="_blank" rel="noopener">${text}</a>`;
            }
            return m;
        });
        return s;
    }

    async function openWeatherModal(callsign) {
        const modal = document.getElementById('weather-modal');
        const title = document.getElementById('weather-modal-title');
        const loading = document.getElementById('weather-loading');
        const currentEl = document.getElementById('weather-current');
        const chartsEl = document.getElementById('weather-charts');
        const noDataEl = document.getElementById('weather-no-data');

        title.textContent = `Weather: ${callsign}`;
        loading.classList.remove('hidden');
        currentEl.classList.add('hidden');
        chartsEl.classList.add('hidden');
        noDataEl.classList.add('hidden');
        modal.classList.remove('hidden');

        // Destroy old charts
        if (weatherTempChart) { weatherTempChart.destroy(); weatherTempChart = null; }
        if (weatherPressureChart) { weatherPressureChart.destroy(); weatherPressureChart = null; }

        try {
            const resp = await fetch(API_BASE + '/weather/' + encodeURIComponent(callsign) + '?hours=24');
            if (!resp.ok) throw new Error('Failed to fetch weather data');
            const data = await resp.json();
            const reports = data.reports || [];

            loading.classList.add('hidden');

            if (reports.length === 0) {
                noDataEl.classList.remove('hidden');
                return;
            }

            // Show current conditions (latest report)
            const latest = reports[reports.length - 1];
            currentEl.innerHTML = buildCurrentWeatherHtml(latest);
            currentEl.classList.remove('hidden');

            // If multiple reports, show charts
            if (reports.length > 1) {
                chartsEl.classList.remove('hidden');
                renderWeatherCharts(reports);
            }
        } catch (err) {
            loading.classList.add('hidden');
            noDataEl.classList.remove('hidden');
            noDataEl.querySelector('p').textContent = 'Error loading weather data: ' + err.message;
        }
    }

    function closeWeatherModal() {
        document.getElementById('weather-modal').classList.add('hidden');
        if (weatherTempChart) { weatherTempChart.destroy(); weatherTempChart = null; }
        if (weatherPressureChart) { weatherPressureChart.destroy(); weatherPressureChart = null; }
    }

    function buildCurrentWeatherHtml(report) {
        const fields = [];
        if (report.temperature != null) fields.push(['Temperature', fmtTemp(report.temperature)]);
        if (report.dewpoint != null) fields.push(['Dewpoint', fmtTemp(report.dewpoint)]);
        if (report.humidity != null) fields.push(['Humidity', report.humidity + '%']);
        if (report.pressure != null) fields.push(['Pressure', fmtPressure(report.pressure)]);
        if (report.wind_speed != null) fields.push(['Wind Speed', fmtWind(report.wind_speed)]);
        if (report.wind_direction != null) fields.push(['Wind Dir', report.wind_direction + '\u00B0']);
        if (report.wind_gust != null) fields.push(['Gust', fmtWind(report.wind_gust)]);
        if (report.rain_1h != null) fields.push(['Rain 1h', fmtRain(report.rain_1h)]);
        if (report.rain_24h != null) fields.push(['Rain 24h', fmtRain(report.rain_24h)]);
        if (report.luminosity != null) fields.push(['Luminosity', report.luminosity + ' W/m\u00B2']);

        let html = '<div class="weather-current-grid">';
        for (const [label, value] of fields) {
            html += `<div class="weather-current-item"><span class="weather-current-label">${label}</span><span class="weather-current-value">${value}</span></div>`;
        }
        html += '</div>';
        if (report.timestamp) {
            const d = new Date(report.timestamp * 1000);
            html += `<div class="weather-current-time">Last report: ${d.toLocaleString()}</div>`;
        }
        return html;
    }

    function renderWeatherCharts(reports) {
        const isDark = document.documentElement.getAttribute('data-theme') !== 'light';
        const textColor = isDark ? '#e0e0e0' : '#333';
        const gridColor = isDark ? 'rgba(255,255,255,0.1)' : 'rgba(0,0,0,0.1)';

        const labels = reports.map(r => {
            const d = new Date(r.timestamp * 1000);
            return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        });

        // Temperature + Dewpoint chart (values follow the Units setting)
        const tempData = reports.map(r => r.temperature != null
            ? (isImperial() ? cToF(r.temperature) : r.temperature) : r.temperature);
        const dewData = reports.map(r => r.dewpoint != null
            ? (isImperial() ? cToF(r.dewpoint) : r.dewpoint) : r.dewpoint);
        const tempUnit = isImperial() ? '\u00B0F' : '\u00B0C';
        const hasTempData = tempData.some(v => v != null);
        const hasDewData = dewData.some(v => v != null);

        if (hasTempData || hasDewData) {
            const datasets = [];
            if (hasTempData) {
                datasets.push({
                    label: 'Temperature (' + tempUnit + ')',
                    data: tempData,
                    borderColor: '#ff6384',
                    backgroundColor: 'rgba(255, 99, 132, 0.1)',
                    borderWidth: 2,
                    tension: 0.3,
                    pointRadius: 2,
                    spanGaps: true,
                });
            }
            if (hasDewData) {
                datasets.push({
                    label: 'Dewpoint (' + tempUnit + ')',
                    data: dewData,
                    borderColor: '#36a2eb',
                    backgroundColor: 'rgba(54, 162, 235, 0.1)',
                    borderWidth: 2,
                    tension: 0.3,
                    pointRadius: 2,
                    spanGaps: true,
                });
            }

            const ctx = document.getElementById('weather-chart-temp').getContext('2d');
            weatherTempChart = new Chart(ctx, {
                type: 'line',
                data: { labels, datasets },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { labels: { color: textColor } },
                        title: { display: true, text: 'Temperature & Dewpoint', color: textColor },
                    },
                    scales: {
                        x: { ticks: { color: textColor, maxTicksLimit: 12 }, grid: { color: gridColor } },
                        y: { ticks: { color: textColor }, grid: { color: gridColor }, title: { display: true, text: tempUnit, color: textColor } },
                    },
                },
            });
        }

        // Barometric Pressure chart (values follow the Units setting)
        const pressureData = reports.map(r => r.pressure != null
            ? (isImperial() ? hpaToInHg(r.pressure) : r.pressure) : r.pressure);
        const pressureUnit = isImperial() ? 'inHg' : 'hPa';
        const hasPressure = pressureData.some(v => v != null);

        if (hasPressure) {
            const ctx2 = document.getElementById('weather-chart-pressure').getContext('2d');
            weatherPressureChart = new Chart(ctx2, {
                type: 'line',
                data: {
                    labels,
                    datasets: [{
                        label: 'Pressure (' + pressureUnit + ')',
                        data: pressureData,
                        borderColor: '#4bc0c0',
                        backgroundColor: 'rgba(75, 192, 192, 0.1)',
                        borderWidth: 2,
                        tension: 0.3,
                        pointRadius: 2,
                        spanGaps: true,
                    }],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { labels: { color: textColor } },
                        title: { display: true, text: 'Barometric Pressure', color: textColor },
                    },
                    scales: {
                        x: { ticks: { color: textColor, maxTicksLimit: 12 }, grid: { color: gridColor } },
                        y: { ticks: { color: textColor }, grid: { color: gridColor }, title: { display: true, text: pressureUnit, color: textColor } },
                    },
                },
            });
        }
    }

    // --- Drop Pin ---
    function initDropPin() {
        const btn = document.getElementById('btn-drop-pin');
        if (btn) {
            btn.addEventListener('click', () => togglePinMode());
        }

        // Right-click on map for context popup
        map.on('contextmenu', (e) => {
            showPinContextPopup(e.lngLat);
        });

        // Long-press on map (mobile)
        let longPressTimer = null;
        map.on('mousedown', (e) => {
            if (!e.originalEvent) return;
            longPressTimer = setTimeout(() => {
                showPinContextPopup(e.lngLat);
            }, 700);
        });
        map.on('mouseup', () => { clearTimeout(longPressTimer); });
        map.on('mousemove', () => { clearTimeout(longPressTimer); });
        map.on('drag', () => { clearTimeout(longPressTimer); });
    }

    function togglePinMode() {
        pinModeActive = !pinModeActive;
        const btn = document.getElementById('btn-drop-pin');
        if (btn) btn.classList.toggle('active', pinModeActive);
        if (pinModeActive) {
            map.getContainer().style.cursor = 'crosshair';
            map.once('click', onPinPlacementClick);
        } else {
            map.getContainer().style.cursor = '';
            map.off('click', onPinPlacementClick);
        }
    }

    function onPinPlacementClick(e) {
        pinModeActive = false;
        const btn = document.getElementById('btn-drop-pin');
        if (btn) btn.classList.remove('active');
        map.getContainer().style.cursor = '';
        dropPinAt(e.lngLat.lat, e.lngLat.lng);
    }

    function dropPinAt(lat, lng) {
        saveMyPosition({ type: 'pin', latitude: lat, longitude: lng });
    }

    function showPinContextPopup(latlng) {
        const lat = latlng.lat.toFixed(6);
        const lng = latlng.lng.toFixed(6);
        const ctxDiv = document.createElement('div');
        ctxDiv.innerHTML = `<b>${lat}, ${lng}</b><br>`;
        const ctxBtn = document.createElement('button');
        ctxBtn.className = 'popup-btn popup-btn-set';
        ctxBtn.textContent = 'Set as My Position';
        ctxBtn.addEventListener('click', () => {
            closeActivePopup();
            dropPinAt(latlng.lat, latlng.lng);
        });
        ctxDiv.appendChild(ctxBtn);
        closeActivePopup();
        activePopup = new maplibregl.Popup()
            .setLngLat([lng, lat])
            .setDOMContent(ctxDiv)
            .addTo(map);
    }

    // --- Decode APRS Packet ---

    let decodeMiniMap = null;

    function initDecode() {
        const btn = document.getElementById('btn-decode');
        const modal = document.getElementById('decode-modal');
        const closeBtn = document.getElementById('btn-close-decode');
        const input = document.getElementById('decode-input');
        const submitBtn = document.getElementById('decode-submit-btn');
        const clearBtn = document.getElementById('decode-clear-btn');

        if (!btn || !modal) return;

        btn.addEventListener('click', openDecodeModal);
        closeBtn.addEventListener('click', closeDecodeModal);

        // Close on backdrop click
        modal.addEventListener('click', (e) => {
            if (e.target === modal) closeDecodeModal();
        });

        // Close on Escape
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && !modal.classList.contains('hidden')) {
                closeDecodeModal();
            }
        });

        // Ctrl+K / Cmd+K shortcut
        document.addEventListener('keydown', (e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
                e.preventDefault();
                openDecodeModal();
            }
        });

        // Input events
        input.addEventListener('input', () => {
            const hasValue = input.value.trim().length > 0;
            submitBtn.disabled = !hasValue;
            clearBtn.classList.toggle('hidden', !hasValue);
        });

        // Clear button
        clearBtn.addEventListener('click', () => {
            input.value = '';
            submitBtn.disabled = true;
            clearBtn.classList.add('hidden');
            document.getElementById('decode-results').innerHTML = '';
            input.focus();
        });

        // Submit
        submitBtn.addEventListener('click', submitDecode);

        // Enter key submits (Shift+Enter for newline)
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                if (!submitBtn.disabled) submitDecode();
            }
        });
    }

    function openDecodeModal() {
        const modal = document.getElementById('decode-modal');
        modal.classList.remove('hidden');
        document.getElementById('decode-input').focus();
        document.getElementById('decode-results').innerHTML = '';
    }

    function closeDecodeModal() {
        document.getElementById('decode-modal').classList.add('hidden');
        if (decodeMiniMap) {
            decodeMiniMap.remove();
            decodeMiniMap = null;
        }
    }

    async function submitDecode() {
        const input = document.getElementById('decode-input');
        const rawPacket = input.value.trim();
        if (!rawPacket) return;

        const resultsDiv = document.getElementById('decode-results');
        resultsDiv.innerHTML = '<div style="text-align:center;color:var(--text-secondary);padding:20px;">Decoding...</div>';

        try {
            const resp = await fetch('/api/decode', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ raw_packet: rawPacket }),
            });
            const data = await resp.json();
            renderDecodeResult(data);
        } catch (err) {
            resultsDiv.innerHTML = renderDecodeError('Network error: ' + err.message);
        }
    }

    function renderDecodeResult(data) {
        const resultsDiv = document.getElementById('decode-results');

        if (!data.success) {
            resultsDiv.innerHTML = renderDecodeError(data.error);
            return;
        }

        const sections = data.sections;
        const annotations = data.annotations;
        const raw = data.raw;
        const pathStations = data.path_stations || {};
        let html = '';

        // Packet type badge
        if (sections.station && sections.station.format) {
            const fmt = sections.station.format.replace(/-/g, '_').replace(/ /g, '_').toLowerCase();
            html += `<div class="decode-packet-type"><span class="packet-type-badge packet-type-${fmt}">${sections.station.format.toUpperCase()}</span></div>`;
        }

        // Annotated raw packet
        html += '<div class="decode-annotated-section">';
        html += '<div class="decode-section-header">Raw Packet (color-coded)</div>';
        html += '<div class="packet-annotated">';
        let lastEnd = 0;
        for (const ann of annotations) {
            if (ann.start > lastEnd) {
                html += `<span class="packet-segment-plain">${escHtml(raw.substring(lastEnd, ann.start))}</span>`;
            }
            html += `<span class="packet-segment packet-segment-${ann.color}" title="${ann.field}">${escHtml(raw.substring(ann.start, ann.end))}</span>`;
            lastEnd = ann.end;
        }
        if (lastEnd < raw.length) {
            html += `<span class="packet-segment-plain">${escHtml(raw.substring(lastEnd))}</span>`;
        }
        html += '</div>';
        html += '<div class="packet-legend">';
        html += '<span class="decode-legend-item"><span class="decode-legend-color legend-source"></span> Source</span>';
        html += '<span class="decode-legend-item"><span class="decode-legend-color legend-destination"></span> Destination</span>';
        html += '<span class="decode-legend-item"><span class="decode-legend-color legend-path"></span> Path</span>';
        html += '<span class="decode-legend-item"><span class="decode-legend-color legend-datatype"></span> Data Type</span>';
        html += '</div></div>';

        // Structured tables
        html += '<div class="decode-tables">';

        // Station section
        if (sections.station) {
            html += '<div class="decode-section">';
            html += '<div class="decode-section-header" style="color:' + themeColor('--decode-header-station') + ';">Station</div>';
            html += '<table class="decode-table">';
            html += `<tr><td class="decode-label">From</td><td class="decode-value"><code>${escHtml(sections.station.from)}</code></td></tr>`;
            html += `<tr><td class="decode-label">To</td><td class="decode-value"><code>${escHtml(sections.station.to)}</code></td></tr>`;
            if (sections.station.path && sections.station.path.length > 0) {
                html += '<tr><td class="decode-label">Path</td><td class="decode-value">';
                html += '<div class="decode-path-chain">';
                const genericRe = /^(WIDE\d?|RELAY|TRACE\d?|qA.|TCPIP|TCPXX|RFONLY|NOGATE)/i;
                for (let i = 0; i < sections.station.path.length; i++) {
                    const hop = sections.station.path[i];
                    const cleanCall = hop.replace('*', '').trim().toUpperCase();
                    const isUsed = hop.endsWith('*');
                    const isGeneric = genericRe.test(cleanCall);
                    const hasLocation = cleanCall in pathStations;
                    if (i > 0) html += '<span class="decode-path-arrow">&rarr;</span>';
                    html += `<span class="decode-path-hop${isUsed ? ' decode-path-used' : ''}">`;
                    html += `<code>${escHtml(hop)}</code>`;
                    if (hasLocation) html += '<span class="decode-path-loc-dot" title="Location known"></span>';
                    html += '</span>';
                }
                html += '</div></td></tr>';
            }
            html += '</table></div>';
        }

        // Position section
        if (sections.position) {
            html += '<div class="decode-section">';
            html += '<div class="decode-section-header" style="color:' + themeColor('--decode-header-position') + ';">Position</div>';
            html += '<table class="decode-table">';
            if (sections.position.latitude != null) {
                html += `<tr><td class="decode-label">Latitude</td><td class="decode-value">${sections.position.latitude.toFixed(5)}&deg;</td></tr>`;
            }
            if (sections.position.longitude != null) {
                html += `<tr><td class="decode-label">Longitude</td><td class="decode-value">${sections.position.longitude.toFixed(5)}&deg;</td></tr>`;
            }
            if (sections.position.timestamp) {
                html += `<tr><td class="decode-label">Timestamp</td><td class="decode-value">${sections.position.timestamp}</td></tr>`;
            }
            if (sections.position.symbol) {
                const symTable = sections.position.symbol_table || '/';
                const symChar = sections.position.symbol || '>';
                const symPos = getSymbolSpritePosition(symChar);
                const symInfo = parseSymbolTable(symTable);
                const symName = getSymbolName(symTable, symChar);
                html += '<tr><td class="decode-label">Symbol</td><td class="decode-value decode-symbol-cell">';
                html += `<span class="decode-symbol-icon" style="background-image:url('${symInfo.spriteUrl}');background-position:${symPos.x}px ${symPos.y}px;"></span>`;
                html += `<code>${escHtml(symTable + symChar)}</code>`;
                if (symName) html += `<span class="decode-symbol-name">${escHtml(symName)}</span>`;
                html += '</td></tr>';
            }
            if (sections.position.altitude != null) {
                html += `<tr><td class="decode-label">Altitude</td><td class="decode-value">${sections.position.altitude} m</td></tr>`;
            }
            if (sections.position.speed != null) {
                html += `<tr><td class="decode-label">Speed</td><td class="decode-value">${sections.position.speed.toFixed(1)} km/h</td></tr>`;
            }
            if (sections.position.course != null) {
                const c = sections.position.course;
                let cardinal = '';
                if (c >= 337.5 || c < 22.5) cardinal = 'N';
                else if (c < 67.5) cardinal = 'NE';
                else if (c < 112.5) cardinal = 'E';
                else if (c < 157.5) cardinal = 'SE';
                else if (c < 202.5) cardinal = 'S';
                else if (c < 247.5) cardinal = 'SW';
                else if (c < 292.5) cardinal = 'W';
                else cardinal = 'NW';
                html += `<tr><td class="decode-label">Course</td><td class="decode-value">`;
                html += `<span class="decode-course-arrow" style="transform:rotate(${c}deg);">&#x2191;</span>`;
                html += `${c}&deg; <span class="decode-course-cardinal">${cardinal}</span>`;
                html += '</td></tr>';
            }
            html += '</table></div>';
        }

        // Weather section
        if (sections.weather) {
            html += '<div class="decode-section decode-section-wide">';
            html += '<div class="decode-section-header" style="color:' + themeColor('--decode-header-weather') + ';">Weather</div>';
            html += '<div class="decode-weather-grid">';
            const wxFields = [
                ['wind_direction', 'Wind Dir', (v) => v + '\u00B0'],
                ['wind_speed', 'Wind Spd', (v) => fmtWind(v)],
                ['wind_gust', 'Gust', (v) => fmtWind(v)],
                ['temperature', 'Temp', (v) => fmtTemp(v)],
                ['humidity', 'Humidity', (v) => v + '%'],
                ['pressure', 'Pressure', (v) => fmtPressure(v)],
                ['rain_1h', 'Rain 1h', (v) => fmtRain(v)],
                ['rain_24h', 'Rain 24h', (v) => fmtRain(v)],
            ];
            for (const [key, label, fmt] of wxFields) {
                if (sections.weather[key] != null) {
                    html += `<div class="decode-weather-item"><span class="decode-weather-label">${label}</span><span class="decode-weather-value">${fmt(sections.weather[key])}</span></div>`;
                }
            }
            html += '</div></div>';
        }

        // Message section
        if (sections.message) {
            html += '<div class="decode-section decode-section-wide">';
            html += '<div class="decode-section-header" style="color:' + themeColor('--decode-header-message') + ';">Message</div>';
            html += '<table class="decode-table">';
            html += `<tr><td class="decode-label">To</td><td class="decode-value"><code>${escHtml(sections.message.addressee)}</code></td></tr>`;
            html += `<tr><td class="decode-label">Message</td><td class="decode-value">${escHtml(sections.message.message_text)}</td></tr>`;
            if (sections.message.msgNo) {
                html += `<tr><td class="decode-label">Msg #</td><td class="decode-value">${escHtml(sections.message.msgNo)}</td></tr>`;
            }
            html += '</table></div>';
        }

        // Telemetry section
        if (sections.telemetry) {
            html += '<div class="decode-section decode-section-wide">';
            html += '<div class="decode-section-header" style="color:' + themeColor('--decode-header-telemetry') + ';">Telemetry</div>';
            html += '<table class="decode-table">';
            for (const [key, value] of Object.entries(sections.telemetry)) {
                html += `<tr><td class="decode-label">${escHtml(key)}</td><td class="decode-value">${escHtml(String(value))}</td></tr>`;
            }
            html += '</table></div>';
        }

        // Comment section
        if (sections.comment) {
            html += '<div class="decode-section decode-section-wide">';
            html += '<div class="decode-section-header" style="color:' + themeColor('--decode-header-comment') + ';">Comment</div>';
            html += `<div class="decode-comment">${escHtml(sections.comment.text)}</div>`;
            html += '</div>';
        }

        html += '</div>'; // close decode-tables

        // Mini map placeholder
        if (sections.position && sections.position.latitude != null && sections.position.longitude != null) {
            html += '<div class="decode-section decode-section-wide" style="margin-top:12px;">';
            html += '<div class="decode-section-header" style="color:' + themeColor('--decode-header-location') + ';">Location</div>';
            html += '<div id="decode-mini-map"></div>';
            html += '</div>';
        }

        // Action buttons
        html += '<div class="decode-actions">';
        html += '<button class="decode-btn decode-btn-secondary" id="decode-copy-btn">Copy Raw</button>';
        html += '</div>';

        resultsDiv.innerHTML = html;

        // Bind copy button
        const copyBtn = document.getElementById('decode-copy-btn');
        if (copyBtn) {
            copyBtn.addEventListener('click', () => {
                const rawText = document.getElementById('decode-input').value;
                copyToClipboard(rawText).then(() => {
                    copyBtn.textContent = 'Copied!';
                    setTimeout(() => { copyBtn.textContent = 'Copy Raw'; }, 1500);
                });
            });
        }

        // Init mini map if position data present
        if (sections.position && sections.position.latitude != null && sections.position.longitude != null) {
            initDecodeMiniMap(sections, pathStations);
        }
    }

    function initDecodeMiniMap(sections, pathStations) {
        const container = document.getElementById('decode-mini-map');
        if (!container) return;

        // Clean up previous map
        if (decodeMiniMap) {
            decodeMiniMap.remove();
            decodeMiniMap = null;
        }

        const stationLat = sections.position.latitude;
        const stationLon = sections.position.longitude;
        const symbolTable = sections.position.symbol_table || '/';
        const symbolChar = sections.position.symbol || '>';
        const stationCall = sections.station.from || '';
        const pathOrder = sections.station.path || [];

        // Small MapLibre instance on the same kit style + region archive as
        // the main map (issue #128; replaces the old Leaflet map + the OSM tile proxy).
        if (typeof maplibregl === 'undefined' || !document.createElement('canvas').getContext('webgl2')) {
            container.outerHTML = '<div class="decode-map-unavailable">Map unavailable (WebGL2 required)</div>';
            return;
        }
        const thisMap = new maplibregl.Map({
            container: container,
            style: { version: 8, sources: {}, layers: [] },
            center: [stationLon, stationLat],
            zoom: 12,
            attributionControl: { compact: true },
        });
        decodeMiniMap = thisMap;
        thisMap.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-left');
        fetchStyleForTheme(_currentTheme).then(function (style) {
            if (decodeMiniMap === thisMap) thisMap.setStyle(style);
        }).catch(function (e) { console.warn('Decode mini-map style failed:', e); });

        function addMarker(lat, lon, table, sym, call) {
            new maplibregl.Marker({ element: createSymbolIconElement(table, sym, call), anchor: 'center' })
                .setLngLat([lon, lat])
                .setPopup(new maplibregl.Popup({ offset: 12 }).setHTML('<strong>' + escHtml(call) + '</strong>'))
                .addTo(thisMap);
        }

        // Collect all points ([lon, lat]) for bounds fitting
        const allPoints = [[stationLon, stationLat]];
        addMarker(stationLat, stationLon, symbolTable, symbolChar, stationCall);

        // Path station markers + polyline
        const linePoints = [[stationLon, stationLat]];
        for (const hop of pathOrder) {
            const clean = hop.replace('*', '').trim().toUpperCase();
            if (pathStations[clean]) {
                const pos = pathStations[clean];
                allPoints.push([pos.longitude, pos.latitude]);
                linePoints.push([pos.longitude, pos.latitude]);
                addMarker(pos.latitude, pos.longitude, pos.symbol_table || '/', pos.symbol || '>', clean);
            }
        }

        if (linePoints.length > 1) {
            const lineColor = themeColor('--segment-path-fg');
            const addPathLine = function () {
                if (thisMap.getSource('decode-path')) return;
                thisMap.addSource('decode-path', {
                    type: 'geojson',
                    data: { type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: linePoints } },
                });
                thisMap.addLayer({
                    id: 'decode-path-line',
                    type: 'line',
                    source: 'decode-path',
                    paint: {
                        'line-color': lineColor,
                        'line-width': 2,
                        // Leaflet dashArray '6, 8' at weight 2 -> line-width units
                        'line-dasharray': [3, 4],
                        'line-opacity': 0.7,
                    },
                });
            };
            thisMap.on('style.load', addPathLine);
        }

        // Fit bounds or set view (Leaflet maxZoom 14 / zoom 13 -> MapLibre z-1)
        if (allPoints.length > 1) {
            const bounds = new maplibregl.LngLatBounds(allPoints[0], allPoints[0]);
            allPoints.forEach(function (p) { bounds.extend(p); });
            thisMap.fitBounds(bounds, { padding: 40, maxZoom: toMlZoom(14), duration: 0 });
        } else {
            thisMap.jumpTo({ center: [stationLon, stationLat], zoom: toMlZoom(13) });
        }
    }

    function renderDecodeError(message) {
        return `<div class="decode-error">
            <div class="decode-error-icon">!</div>
            <div class="decode-error-message">${escHtml(message)}</div>
            <div class="decode-error-hint">Check that the packet follows APRS format, e.g.:<br>
            <code>CALLSIGN&gt;DEST,PATH:data</code></div>
        </div>`;
    }

    function copyToClipboard(text) {
        if (navigator.clipboard && window.isSecureContext) {
            return navigator.clipboard.writeText(text);
        }
        // Fallback for non-HTTPS (e.g. http://pi:8080)
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.left = '-9999px';
        document.body.appendChild(ta);
        ta.select();
        try { document.execCommand('copy'); } catch (e) { /* ignore */ }
        document.body.removeChild(ta);
        return Promise.resolve();
    }

    // --- Debug: Simulate stations at various angles/distances from home ---
    function simulateStations() {
        const myPos = getMyPosition();
        const homeLat = myPos ? myPos.lat : (config.station?.latitude || null);
        const homeLng = myPos ? myPos.lng : (config.station?.longitude || null);
        if (!homeLat || !homeLng) {
            console.warn('simulateStations: no position configured');
            return;
        }
        // Place stations at different bearings and distances from home
        // Each entry: [callsign, bearingDeg, distanceKm, symbol, symbolTable]
        const simStations = [
            ['SIM-N',    0,    5,  '>', '/'],  // North, 5 km
            ['SIM-NE',  45,   12,  '>', '/'],  // NE, 12 km
            ['SIM-E',   90,   25,  '-', '/'],  // East, 25 km
            ['SIM-SE', 135,    8,  'k', '/'],  // SE, 8 km
            ['SIM-S',  180,   40,  '>', '/'],  // South, 40 km
            ['SIM-SW', 225,   18,  '-', '/'],  // SW, 18 km
            ['SIM-W',  270,    3,  '>', '/'],  // West, 3 km (short)
            ['SIM-NW', 315,   55,  'k', '/'],  // NW, 55 km (long)
            ['SIM-FAR', 60,  100,  '>', '/'],  // ENE, 100 km (very long)
            ['SIM-NEAR',200,   1,  '-', '/'],  // SSW, 1 km (very short)
        ];
        const deg2rad = Math.PI / 180;
        const R = 6371; // Earth radius in km
        let delay = 0;
        for (const [call, bearing, distKm, sym, symTbl] of simStations) {
            const brng = bearing * deg2rad;
            const lat1 = homeLat * deg2rad;
            const lng1 = homeLng * deg2rad;
            const lat2 = Math.asin(
                Math.sin(lat1) * Math.cos(distKm / R) +
                Math.cos(lat1) * Math.sin(distKm / R) * Math.cos(brng)
            );
            const lng2 = lng1 + Math.atan2(
                Math.sin(brng) * Math.sin(distKm / R) * Math.cos(lat1),
                Math.cos(distKm / R) - Math.sin(lat1) * Math.sin(lat2)
            );
            const lat = lat2 / deg2rad;
            const lng = lng2 / deg2rad;
            const packet = {
                timestamp: Date.now() / 1000,
                tx: false,
                from_call: call,
                to_call: 'APRS',
                type: 'GPSPacket',
                latitude: lat,
                longitude: lng,
                symbol: sym,
                symbol_table: symTbl,
                path: [],
                comment: `Simulated ${distKm} km @ ${bearing}°`,
                human_info: `[SIM ${distKm}km ${bearing}°]`,
                msg_no: '',
                raw_packet: `${call}>APRS:!${lat.toFixed(4)}/${lng.toFixed(4)}>${sym}`,
                audio_level: null,
            };
            setTimeout(() => onPacket(packet), delay);
            delay += 800; // stagger so animations don't overlap
        }
        console.log(`simulateStations: injecting ${simStations.length} stations over ${delay}ms`);
    }
    // Debug helper — only exposed when the page is loaded with ?debug=1,
    // so it is not reachable from a console in normal operation. (issue #88)
    if (new URLSearchParams(window.location.search).get('debug') === '1') {
        window.simulateStations = simulateStations;
    }

    // -----------------------------------------------------------------------
    // Station List Modal (#33) + CSV Export (#34)
    // -----------------------------------------------------------------------

    /** Format a Unix timestamp as a short human-readable string. */
    function formatAge(ts) {
        if (!ts) return '—';
        const secs = Math.round(Date.now() / 1000 - ts);
        if (secs < 60)  return secs + 's ago';
        if (secs < 3600) return Math.floor(secs / 60) + 'm ago';
        if (secs < 86400) return Math.floor(secs / 3600) + 'h ago';
        return Math.floor(secs / 86400) + 'd ago';
    }

    /** Coerce a cell value for sorting (strings lower-cased, nulls to end). */
    function sortValue(row, key) {
        const v = row[key];
        if (v === null || v === undefined) return key === 'callsign' ? '' : -Infinity;
        if (typeof v === 'string') return v.toLowerCase();
        return v;
    }

    let _stationListData = [];   // full list from last fetch
    let _slSortKey = 'callsign'; // current sort column
    let _slSortAsc = true;       // ascending?
    let _slFilter  = '';         // current search filter

    function _renderStationTable() {
        const body  = document.getElementById('station-list-body');
        const count = document.getElementById('station-list-count');
        if (!body) return;

        const filter = _slFilter.toLowerCase();
        const visible = _stationListData.filter(s =>
            !filter || s.callsign.toLowerCase().includes(filter)
        );

        // Sort
        visible.sort((a, b) => {
            const av = sortValue(a, _slSortKey);
            const bv = sortValue(b, _slSortKey);
            if (av < bv) return _slSortAsc ? -1 : 1;
            if (av > bv) return _slSortAsc ? 1 : -1;
            return 0;
        });

        if (count) {
            count.textContent = visible.length === _stationListData.length
                ? `${visible.length} station${visible.length !== 1 ? 's' : ''}`
                : `${visible.length} / ${_stationListData.length} stations`;
        }

        body.innerHTML = '';
        for (const s of visible) {
            const tr = document.createElement('tr');

            // Callsign + symbol
            const callTd = document.createElement('td');
            callTd.className = 'sl-callsign';
            if (s.symbol_table && s.symbol) {
                const icon = document.createElement('span');
                icon.className = 'sl-symbol';
                // Reuse the CSS sprite classes from the compact log renderer
                const tableChar = s.symbol_table === '/' ? 0 : 1;
                const symIdx = s.symbol.charCodeAt(0) - 33;
                const bgX = -(symIdx % 16) * 24;
                const bgY = -(Math.floor(symIdx / 16) * 24);
                icon.style.cssText = `display:inline-block;width:16px;height:16px;background:url(/static/symbols/aprs-symbols-24-${tableChar}.png) ${bgX}px ${bgY}px;background-size:384px 96px;vertical-align:middle;margin-right:4px;`;
                callTd.appendChild(icon);
            }
            const callLink = document.createElement('a');
            callLink.href = '#';
            callLink.textContent = s.callsign;
            callLink.addEventListener('click', (e) => {
                e.preventDefault();
                // Pan map to station and close modal
                const stn = stations[s.callsign];
                if (stn && stn.data.latitude != null) {
                    document.getElementById('station-list-modal').classList.add('hidden');
                    focusStation(s.callsign);
                }
            });
            callTd.appendChild(callLink);
            tr.appendChild(callTd);

            // Last seen
            const ageTd = document.createElement('td');
            ageTd.className = 'sl-age';
            ageTd.textContent = formatAge(s.last_seen);
            ageTd.dataset.ts = s.last_seen || 0;
            tr.appendChild(ageTd);

            // Packet count
            const pktTd = document.createElement('td');
            pktTd.className = 'sl-packets';
            pktTd.textContent = s.packet_count || 0;
            tr.appendChild(pktTd);

            // Position
            const posTd = document.createElement('td');
            posTd.className = 'sl-position';
            if (s.latitude != null && s.longitude != null) {
                posTd.textContent = s.latitude.toFixed(4) + ', ' + s.longitude.toFixed(4);
            } else {
                posTd.textContent = '—';
            }
            tr.appendChild(posTd);

            // Actions
            const actTd = document.createElement('td');
            actTd.className = 'sl-actions';
            const viewBtn = document.createElement('button');
            viewBtn.className = 'sl-btn';
            viewBtn.textContent = 'Focus';
            viewBtn.addEventListener('click', () => {
                const stn = stations[s.callsign];
                if (stn && stn.data.latitude != null) {
                    document.getElementById('station-list-modal').classList.add('hidden');
                    focusStation(s.callsign);
                }
            });
            actTd.appendChild(viewBtn);
            tr.appendChild(actTd);

            body.appendChild(tr);
        }
    }

    async function _loadStationListData() {
        try {
            const resp = await fetch(API_BASE + '/stations');
            if (!resp.ok) return;
            _stationListData = await resp.json();
            _renderStationTable();
        } catch (e) {
            console.warn('Station list fetch error:', e);
        }
    }

    function _exportStationsCsv() {
        const filter = _slFilter.toLowerCase();
        const rows = _stationListData.filter(s =>
            !filter || s.callsign.toLowerCase().includes(filter)
        );
        rows.sort((a, b) => {
            const av = sortValue(a, _slSortKey);
            const bv = sortValue(b, _slSortKey);
            if (av < bv) return _slSortAsc ? -1 : 1;
            if (av > bv) return _slSortAsc ? 1 : -1;
            return 0;
        });

        const headers = ['callsign', 'last_seen', 'packet_count', 'latitude', 'longitude', 'symbol', 'symbol_table', 'last_comment'];
        const lines = [headers.join(',')];
        for (const s of rows) {
            const cells = headers.map(h => {
                let v = s[h];
                if (v === null || v === undefined) v = '';
                v = String(v);
                // Quote cells containing comma, newline, or double-quote
                if (v.includes(',') || v.includes('"') || v.includes('\n')) {
                    v = '"' + v.replace(/"/g, '""') + '"';
                }
                return v;
            });
            lines.push(cells.join(','));
        }

        const blob = new Blob([lines.join('\r\n')], { type: 'text/csv' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'direwolf-stations-' + new Date().toISOString().slice(0, 10) + '.csv';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    }

    function initStationListModal() {
        const modal   = document.getElementById('station-list-modal');
        const btnOpen = document.getElementById('btn-station-list');
        const btnClose = document.getElementById('btn-close-station-list');
        const btnExport = document.getElementById('btn-export-csv');
        const searchInput = document.getElementById('station-search');
        const thead = document.querySelector('#station-list-table thead');

        if (!modal) return;

        function openModal() {
            _slFilter = '';
            if (searchInput) searchInput.value = '';
            modal.classList.remove('hidden');
            _loadStationListData();
        }

        function closeModal() {
            modal.classList.add('hidden');
        }

        if (btnOpen) btnOpen.addEventListener('click', openModal);
        if (btnClose) btnClose.addEventListener('click', closeModal);
        modal.addEventListener('click', (e) => { if (e.target === modal) closeModal(); });

        if (btnExport) btnExport.addEventListener('click', _exportStationsCsv);

        // Column sort
        if (thead) {
            thead.addEventListener('click', (e) => {
                const th = e.target.closest('th[data-sort]');
                if (!th) return;
                const key = th.dataset.sort;
                if (_slSortKey === key) {
                    _slSortAsc = !_slSortAsc;
                } else {
                    _slSortKey = key;
                    _slSortAsc = true;
                }
                // Update header classes
                thead.querySelectorAll('th[data-sort]').forEach(t => {
                    t.classList.remove('sort-asc', 'sort-desc');
                    if (t.dataset.sort === _slSortKey) {
                        t.classList.add(_slSortAsc ? 'sort-asc' : 'sort-desc');
                    }
                });
                _renderStationTable();
            });
        }

        // Search filter
        if (searchInput) {
            searchInput.addEventListener('input', (e) => {
                _slFilter = e.target.value;
                _renderStationTable();
            });
        }

        // Keyboard shortcut: S to open/close
        document.addEventListener('keydown', (e) => {
            if (e.key === 's' || e.key === 'S') {
                const tag = document.activeElement?.tagName;
                if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;
                if (modal.classList.contains('hidden')) {
                    openModal();
                } else {
                    closeModal();
                }
            }
        });

        // Live age refresh every 30s while modal is open
        // Store handle so the timer is not duplicated if initStationListModal() is
        // called more than once (#62)
        if (_slAgeTimer) clearInterval(_slAgeTimer);
        _slAgeTimer = setInterval(() => {
            if (modal.classList.contains('hidden')) return;
            document.querySelectorAll('#station-list-body .sl-age').forEach(td => {
                const ts = parseFloat(td.dataset.ts);
                if (ts) td.textContent = formatAge(ts);
            });
        }, 30000);
    }

    // -----------------------------------------------------------------------
    // Messages Panel (#31)
    // -----------------------------------------------------------------------

    let _mpFilter = '';
    let _mpData = [];
    const MESSAGES_MY_CALL_KEY = 'direwolf_messages_my_call';

    function initMessagesPanelModal() {
        const modal       = document.getElementById('messages-panel-modal');
        const btnOpen     = document.getElementById('btn-messages-panel');
        const btnClose    = document.getElementById('btn-close-messages-panel');
        const searchInput = document.getElementById('messages-search');
        const myCallInput = document.getElementById('messages-my-callsign');

        if (!modal) return;

        // Restore saved "my call" from localStorage
        if (myCallInput) {
            const saved = localStorage.getItem(MESSAGES_MY_CALL_KEY) || '';
            myCallInput.value = saved;
            myCallInput.addEventListener('change', () => {
                localStorage.setItem(MESSAGES_MY_CALL_KEY, myCallInput.value.trim().toUpperCase());
                _renderMessageThreads();
            });
        }

        function openModal() {
            _mpFilter = '';
            if (searchInput) searchInput.value = '';
            modal.classList.remove('hidden');
            _loadMessagesData();
        }

        function closeModal() {
            modal.classList.add('hidden');
        }

        if (btnOpen)  btnOpen.addEventListener('click', openModal);
        if (btnClose) btnClose.addEventListener('click', closeModal);
        modal.addEventListener('click', (e) => { if (e.target === modal) closeModal(); });

        if (searchInput) {
            searchInput.addEventListener('input', (e) => {
                _mpFilter = e.target.value;
                _renderMessageThreads();
            });
        }

        // M key shortcut
        document.addEventListener('keydown', (e) => {
            if (e.key === 'm' || e.key === 'M') {
                const tag = document.activeElement?.tagName;
                if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;
                if (modal.classList.contains('hidden')) openModal();
                else closeModal();
            }
        });

        // Auto-refresh every 30s while open (#62)
        if (_mpRefreshTimer) clearInterval(_mpRefreshTimer);
        _mpRefreshTimer = setInterval(() => {
            if (modal.classList.contains('hidden')) return;
            _loadMessagesData();
        }, 30000);
    }

    async function _loadMessagesData() {
        try {
            const resp = await fetch(API_BASE + '/messages?limit=500');
            if (!resp.ok) throw new Error('Failed to load messages');
            _mpData = await resp.json();
        } catch (_) {
            _mpData = [];
        }
        _renderMessageThreads();
    }

    function _renderMessageThreads() {
        const container = document.getElementById('messages-thread-list');
        const countEl   = document.getElementById('messages-panel-count');
        if (!container) return;

        const myCallInput = document.getElementById('messages-my-callsign');
        const myCall = (myCallInput ? myCallInput.value.trim().toUpperCase() : '') ||
                       (config.station?.my_position?.callsign || '').toUpperCase();

        const filter = _mpFilter.trim().toUpperCase();

        // Group messages into threads keyed by sorted pair of callsigns
        const threads = {};
        for (const m of _mpData) {
            const a = (m.from_call || '').toUpperCase();
            const b = (m.to_call   || '').toUpperCase();
            if (!a && !b) continue;
            if (filter && !a.includes(filter) && !b.includes(filter)) continue;
            const key = [a, b].sort().join('\u2194');  // ↔ separator
            if (!threads[key]) {
                threads[key] = { a, b, messages: [], addressed_to_me: false };
            }
            threads[key].messages.push(m);
            if (myCall && (b === myCall || a === myCall)) {
                threads[key].addressed_to_me = true;
            }
        }

        // Sort threads: addressed-to-me first, then by most-recent timestamp
        const threadList = Object.values(threads);
        threadList.sort((ta, tb) => {
            if (ta.addressed_to_me !== tb.addressed_to_me) return ta.addressed_to_me ? -1 : 1;
            const latestA = Math.max(...ta.messages.map(m => m.timestamp || 0));
            const latestB = Math.max(...tb.messages.map(m => m.timestamp || 0));
            return latestB - latestA;
        });

        if (countEl) countEl.textContent = `${threadList.length} thread${threadList.length !== 1 ? 's' : ''}`;

        if (threadList.length === 0) {
            container.innerHTML = '<div class="messages-empty">No APRS messages received yet.</div>';
            return;
        }

        container.innerHTML = threadList.map(thread => {
            const msgs = thread.messages.slice().sort((a, b) => (a.timestamp || 0) - (b.timestamp || 0));
            const highlight = thread.addressed_to_me ? ' messages-thread-mine' : '';
            const header = `<div class="messages-thread-header${highlight}">` +
                `<span class="messages-thread-calls">${escHtml(thread.a)} &#x2194; ${escHtml(thread.b)}</span>` +
                `<span class="messages-thread-count">${msgs.length} msg${msgs.length !== 1 ? 's' : ''}</span>` +
                '</div>';
            const rows = msgs.map(m => {
                const ts = m.timestamp ? new Date(m.timestamp * 1000).toLocaleTimeString() : '';
                const fromMe = myCall && (m.from_call || '').toUpperCase() === myCall;
                const cls = fromMe ? 'messages-msg messages-msg-out' : 'messages-msg messages-msg-in';
                const msgNo = m.msg_no ? ` <span class="messages-msg-no">[${escHtml(String(m.msg_no))}]</span>` : '';
                const text = m.comment || m.human_info || '';
                return `<div class="${cls}">` +
                    `<span class="messages-msg-from">${escHtml((m.from_call || '?').toUpperCase())}</span>` +
                    `<span class="messages-msg-arrow">&#x27A1;</span>` +
                    `<span class="messages-msg-to">${escHtml((m.to_call || '?').toUpperCase())}</span>` +
                    `<span class="messages-msg-text">${escHtml(text)}</span>` +
                    msgNo +
                    `<span class="messages-msg-ts">${escHtml(ts)}</span>` +
                    '</div>';
            }).join('');
            return `<div class="messages-thread">${header}${rows}</div>`;
        }).join('');
    }

    // -----------------------------------------------------------------------
    // Weather Panel Modal (#38)
    // -----------------------------------------------------------------------

    let _wpSortKey = 'callsign';
    let _wpSortAsc = true;
    let _wpFilter = '';
    let _wpData = [];

    function initWeatherPanelModal() {
        const modal     = document.getElementById('weather-panel-modal');
        const btnOpen   = document.getElementById('btn-weather-panel');
        const btnClose  = document.getElementById('btn-close-weather-panel');
        const searchInput = document.getElementById('weather-panel-search');
        const thead     = document.querySelector('#weather-panel-table thead');

        if (!modal) return;

        function openModal() {
            _wpFilter = '';
            if (searchInput) searchInput.value = '';
            modal.classList.remove('hidden');
            _loadWeatherPanelData();
        }

        function closeModal() {
            modal.classList.add('hidden');
        }

        if (btnOpen)  btnOpen.addEventListener('click', openModal);
        if (btnClose) btnClose.addEventListener('click', closeModal);
        modal.addEventListener('click', (e) => { if (e.target === modal) closeModal(); });

        // Column sort
        if (thead) {
            thead.addEventListener('click', (e) => {
                const th = e.target.closest('th[data-sort]');
                if (!th) return;
                const key = th.dataset.sort;
                if (_wpSortKey === key) {
                    _wpSortAsc = !_wpSortAsc;
                } else {
                    _wpSortKey = key;
                    _wpSortAsc = true;
                }
                thead.querySelectorAll('th[data-sort]').forEach(t => {
                    t.classList.remove('sort-asc', 'sort-desc');
                    if (t.dataset.sort === _wpSortKey) {
                        t.classList.add(_wpSortAsc ? 'sort-asc' : 'sort-desc');
                    }
                });
                _renderWeatherTable();
            });
        }

        if (searchInput) {
            searchInput.addEventListener('input', (e) => {
                _wpFilter = e.target.value;
                _renderWeatherTable();
            });
        }

        // W key shortcut
        document.addEventListener('keydown', (e) => {
            if (e.key === 'w' || e.key === 'W') {
                const tag = document.activeElement?.tagName;
                if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;
                if (modal.classList.contains('hidden')) openModal();
                else closeModal();
            }
        });

        // Live refresh every 60s (#62)
        if (_wpRefreshTimer) clearInterval(_wpRefreshTimer);
        _wpRefreshTimer = setInterval(() => {
            if (modal.classList.contains('hidden')) return;
            _loadWeatherPanelData();
        }, 60000);
    }

    async function _loadWeatherPanelData() {
        try {
            const resp = await fetch(API_BASE + '/weather/stations');
            if (!resp.ok) throw new Error('Failed to load weather stations');
            _wpData = await resp.json();
        } catch (err) {
            _wpData = [];
        }
        _renderWeatherTable();
    }

    function _renderWeatherTable() {
        const tbody = document.getElementById('weather-panel-body');
        const countEl = document.getElementById('weather-panel-count');
        if (!tbody) return;

        const filter = _wpFilter.trim().toUpperCase();
        let rows = _wpData.filter(s => !filter || (s.callsign || '').toUpperCase().includes(filter));

        rows.sort((a, b) => {
            let av, bv;
            if (_wpSortKey === 'callsign') {
                av = a.callsign || '';
                bv = b.callsign || '';
            } else {
                av = (a.last_weather && a.last_weather[_wpSortKey] != null) ? a.last_weather[_wpSortKey] : -Infinity;
                bv = (b.last_weather && b.last_weather[_wpSortKey] != null) ? b.last_weather[_wpSortKey] : -Infinity;
            }
            if (av < bv) return _wpSortAsc ? -1 : 1;
            if (av > bv) return _wpSortAsc ? 1 : -1;
            return 0;
        });

        if (countEl) countEl.textContent = `${rows.length} station${rows.length !== 1 ? 's' : ''}`;

        tbody.innerHTML = rows.map(s => {
            const w = s.last_weather || {};
            const temp    = w.temperature != null ? fmtTemp(w.temperature) : '—';
            const wind    = w.wind_speed   != null
                ? `${fmtWind(w.wind_speed)}${w.wind_direction != null ? ' @ ' + w.wind_direction.toFixed(0) + '°' : ''}`
                : '—';
            const humid   = w.humidity   != null ? w.humidity.toFixed(0) + '%' : '—';
            const pres    = w.pressure   != null ? fmtPressure(w.pressure) : '—';
            const ts      = w.timestamp  != null ? formatAge(w.timestamp) : '—';
            const cs      = s.callsign || '?';
            return `<tr>
                <td class="sl-callsign">${escHtml(cs)}</td>
                <td>${escHtml(temp)}</td>
                <td>${escHtml(wind)}</td>
                <td>${escHtml(humid)}</td>
                <td>${escHtml(pres)}</td>
                <td class="sl-age" data-ts="${w.timestamp || ''}">${escHtml(ts)}</td>
                <td><button class="popup-btn popup-btn-weather wp-view-btn" data-cs="${escHtml(cs)}" title="View charts">&#127777; Charts</button></td>
            </tr>`;
        }).join('');

        // Wire "Charts" buttons
        tbody.querySelectorAll('.wp-view-btn').forEach(btn => {
            btn.addEventListener('click', () => window._viewWeather(btn.dataset.cs));
        });
    }

    // -----------------------------------------------------------------------
    // Keyboard Help Modal (#39)
    // -----------------------------------------------------------------------

    function initKeyboardHelpModal() {
        const modal    = document.getElementById('keyboard-help-modal');
        const btnOpen  = document.getElementById('btn-keyboard-help');
        const btnClose = document.getElementById('btn-close-keyboard-help');

        if (!modal) return;

        function openModal()  { modal.classList.remove('hidden'); }
        function closeModal() { modal.classList.add('hidden'); }

        if (btnOpen)  btnOpen.addEventListener('click', openModal);
        if (btnClose) btnClose.addEventListener('click', closeModal);
        modal.addEventListener('click', (e) => { if (e.target === modal) closeModal(); });

        // ? key to show shortcuts
        document.addEventListener('keydown', (e) => {
            if (e.key === '?') {
                const tag = document.activeElement?.tagName;
                if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;
                e.preventDefault();
                if (modal.classList.contains('hidden')) openModal();
                else closeModal();
            }
        });
    }


    // Toolbar bridge (issue #218): expose the main map and station registry to
    // the top-toolbar controls (Find + station count).
    window.__dwToolbar = {
        getStation: function (cs) { return stations[cs] || null; },
        flyTo: function (lat, lng, zoom) { mapFlyTo(lat, lng, toMlZoom(zoom)); },
        stationCount: function () {
            var n = 0;
            for (var k in stations) {
                if (Object.prototype.hasOwnProperty.call(stations, k)) n++;
            }
            return n;
        },
    };
})();
