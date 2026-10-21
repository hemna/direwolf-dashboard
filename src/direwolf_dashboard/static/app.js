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
    let gpxLayer = null;    // Current GPX overlay layer (L.GPX instance)
    let gpxControlEl = null; // Reference to the GPX control DOM element
    let myLocationOverlayEl = null; // Reference to My Location overlay DOM element
    let waitingForPosition = false;
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
    const stationPositionCache = {};
    const POSITION_CACHE_MAX = 1000;
    const animationThrottle = {};
    const ANIMATION_COOLDOWN_MS = 3000;
    const activeAnimationElements = [];
    const GENERIC_PATH_ALIASES = new Set([
        'WIDE', 'WIDE1', 'WIDE2', 'WIDE3', 'WIDE1-1', 'WIDE2-1', 'WIDE2-2', 'WIDE3-3',
        'RELAY', 'TRACE', 'TCPIP', 'CQ', 'QST', 'APRS', 'RFONLY', 'NOGATE',
    ]);

    // --- Theme Support ---
    const _embedded = window.__dwEmbedded || false;
    let _currentTheme = window.__dwTheme || 'dark';

    /** Read a CSS custom property value from :root / [data-theme]. */
    function themeColor(varName) {
        return getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
    }

    function applyTheme(theme) {
        if (theme !== 'light' && theme !== 'dark') return;
        _currentTheme = theme;
        document.documentElement.setAttribute('data-theme', theme);
        if (!_embedded) localStorage.setItem('dw-theme', theme);
        updateThemeIcon();
        // Reload the maplibre style for the new theme; station layers are
        // re-added on the 'style.load' event (theme-dependent paint colors).
        if (mainMapIsMaplibre && map) {
            fetchStyleForTheme(theme).then(function (style) {
                map.setStyle(style);
            }).catch(function (e) {
                console.error('Failed to switch map style:', e);
            });
        }
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

    function createSymbolIcon(symbolTable, symbolChar, callsign) {
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

        return L.divIcon({
            html: html,
            className: 'aprs-symbol-icon',
            iconSize: [24, 24],
            iconAnchor: [12, 12],
            popupAnchor: [0, -12],
        });
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
        const container = mlCreate('map-legend');
        var txColor = themeColor('--route-tx-rf');
        var tcpipColor = themeColor('--route-tx-tcpip');
        var rxColor = themeColor('--route-rx');
        var trackColor = themeColor('--station-track');
        var routeColor = themeColor('--route-tcpip-line');
        container.innerHTML = `
                    <div class="map-legend-header">
                        Legend <span class="legend-collapse-icon">&#9650;</span>
                    </div>
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
                    </div>
                `;
        const header = container.querySelector('.map-legend-header');
        const legendIcon = container.querySelector('.legend-collapse-icon');
        header.addEventListener('click', () => {
            container.classList.toggle('collapsed');
            const isCollapsed = container.classList.contains('collapsed');
            legendIcon.textContent = isCollapsed ? '\u25BC' : '\u25B2';
            localStorage.setItem('dw-legend-collapsed', isCollapsed ? '1' : '0');
        });
        if (localStorage.getItem('dw-legend-collapsed') === '1') {
            container.classList.add('collapsed');
            legendIcon.textContent = '\u25BC';
        }
        map.addControl(mlControl(function () { return container; }, 'bottom-left'));
    }

    // --- Stats Overlay ---
    var statsOverlayEl = null;

    function initStatsOverlay() {
        if (!map) return;
        var container = mlCreate('stats-overlay');
        container.innerHTML =
            '<div class="stats-overlay-header">Stats <span class="stats-collapse-icon">\u25B2</span></div>' +
            '<div class="stats-overlay-body">' +
            '<div class="stats-overlay-row"><span class="stats-label">Stations</span><span class="stats-value" id="stats-stations">0</span></div>' +
            '<div class="stats-overlay-row"><span class="stats-label">Packets</span><span class="stats-value" id="stats-packets">0</span></div>' +
            '<div class="stats-overlay-row"><span class="stats-label">Pkts/hr</span><span class="stats-value" id="stats-rate">0</span></div>' +
            '<div class="stats-overlay-row"><span class="stats-label">Tiles</span><span class="stats-value" id="stats-tiles">0</span></div>' +
            '<div class="stats-overlay-row"><span class="stats-label">Uptime</span><span class="stats-value" id="stats-uptime">0s</span></div>' +
            '</div>';
        var statsHeader = container.querySelector('.stats-overlay-header');
        var statsIcon = container.querySelector('.stats-collapse-icon');
        statsHeader.addEventListener('click', function () {
            container.classList.toggle('collapsed');
            var isCollapsed = container.classList.contains('collapsed');
            statsIcon.textContent = isCollapsed ? '\u25BC' : '\u25B2';
            localStorage.setItem('dw-stats-collapsed', isCollapsed ? '1' : '0');
        });
        if (localStorage.getItem('dw-stats-collapsed') === '1') {
            container.classList.add('collapsed');
            statsIcon.textContent = '\u25BC';
        }
        statsOverlayEl = container;
        map.addControl(mlControl(function () { return container; }, 'top-left'));
        if (!showStatsOverlay && statsOverlayEl) {
            statsOverlayEl.style.display = 'none';
        }
    }

    function updateStatsOverlay(stats) {
        if (!statsOverlayEl) return;
        var el;
        el = document.getElementById('stats-stations');
        if (el) el.textContent = stats.stations_active || 0;
        el = document.getElementById('stats-packets');
        if (el) el.textContent = stats.packets_total || 0;
        el = document.getElementById('stats-rate');
        if (el) el.textContent = stats.packets_last_hour || 0;
        el = document.getElementById('stats-tiles');
        if (el) {
            var tc = stats.tile_cache || {};
            el.textContent = (tc.cache_size_mb || 0) + ' MB';
        }
        el = document.getElementById('stats-uptime');
        if (el) el.textContent = formatUptime(stats.uptime_seconds || 0);
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

    // --- Filter Overlay ---
    function initFilterOverlay() {
        if (!map) return;
        var container = mlCreate('filter-overlay');
        container.innerHTML =
            '<div class="filter-overlay-header">Filters <span class="filter-collapse-icon">\u25B2</span></div>' +
            '<div class="filter-overlay-body">' +
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
            '</div>';
        var filterHeader = container.querySelector('.filter-overlay-header');
        var filterIcon = container.querySelector('.filter-collapse-icon');
        filterHeader.addEventListener('click', function () {
            container.classList.toggle('collapsed');
            var isCollapsed = container.classList.contains('collapsed');
            filterIcon.textContent = isCollapsed ? '\u25BC' : '\u25B2';
            localStorage.setItem('dw-filter-collapsed', isCollapsed ? '1' : '0');
        });
        if (localStorage.getItem('dw-filter-collapsed') === '1') {
            container.classList.add('collapsed');
            filterIcon.textContent = '\u25BC';
        }
        map.addControl(mlControl(function () { return container; }, 'top-right'));
    }

    // --- My Location Overlay ---
    function initMyLocationOverlay() {
        if (!map) return;
        var container = mlCreate('my-location-overlay');
        container.innerHTML =
            '<div class="my-location-header">' +
                '<span class="my-location-title">My Location</span>' +
                '<span class="my-location-actions">' +
                    '<span class="my-location-btn my-location-center" title="Center map on my location">&#8982;</span>' +
                    '<span class="my-location-btn my-location-clear hidden" title="Clear my location">&times;</span>' +
                    '<span class="my-location-collapse-icon">\u25B2</span>' +
                '</span>' +
            '</div>' +
            '<div class="my-location-body">' +
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
        // Wire up collapse toggle
        var myLocHeader = container.querySelector('.my-location-header');
        var myLocIcon = container.querySelector('.my-location-collapse-icon');
        myLocHeader.addEventListener('click', function (e) {
            if (e.target.closest('.my-location-btn')) return;
            container.classList.toggle('collapsed');
            var isCollapsed = container.classList.contains('collapsed');
            myLocIcon.textContent = isCollapsed ? '\u25BC' : '\u25B2';
            localStorage.setItem('dw-myloc-collapsed', isCollapsed ? '1' : '0');
        });
        if (localStorage.getItem('dw-myloc-collapsed') === '1') {
            container.classList.add('collapsed');
            myLocIcon.textContent = '\u25BC';
        }
        myLocationOverlayEl = container;
        map.addControl(mlControl(function () { return container; }, 'top-right'));
        if (!showMyLocationOverlay && myLocationOverlayEl) {
            myLocationOverlayEl.style.display = 'none';
        }
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

    function initGpxOverlay() {
        // TODO(#127): GPX overlay not ported to MapLibre yet.
        if (mainMapIsMaplibre) return;
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
                    var isCollapsed = container.classList.contains('collapsed');
                    collapseIcon.textContent = isCollapsed ? '\u25BC' : '\u25B2';
                    localStorage.setItem('dw-gpx-collapsed', isCollapsed ? '1' : '0');
                });
                if (localStorage.getItem('dw-gpx-collapsed') === '1') {
                    container.classList.add('collapsed');
                    collapseIcon.textContent = '\u25BC';
                }

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
        gpxControlEl = controlEl;

        // Apply initial visibility from config
        if (!showGpxOverlay) {
            controlEl.style.display = 'none';
        }

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
                        color: themeColor('--gpx-accent'),
                        opacity: 0.8,
                        weight: 3,
                        lineCap: 'round',
                        lineJoin: 'round'
                    },
                    {
                        color: themeColor('--gpx-accent'),
                        opacity: 0.8,
                        weight: 2,
                        dashArray: '8,6',
                        lineCap: 'round',
                        lineJoin: 'round'
                    }
                ],
                markers: {
                    startIcon: gpxCircleIcon(themeColor('--gpx-start'), 6),
                    endIcon: gpxCircleIcon(themeColor('--gpx-end'), 6),
                    wptIcons: { '': gpxCircleIcon(themeColor('--gpx-waypoint'), 5) },
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
        initSettings();
        initConfirmWipeModal();
        initClearDataFab();
        initDropPin();
        initMapResize();
        initLogToggle();
        initMobileMenu();
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

        map = new maplibregl.Map({
            container: 'map',
            style: await fetchStyleForTheme(_currentTheme),
            center: [lon, lat],
            zoom: toMlZoom(zoom),
        });
        map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-left');

        // Persist map center across page refreshes (zoom is always from config)
        map.on('moveend', function () {
            const c = map.getCenter();
            localStorage.setItem('dw-map-lat', c.lat);
            localStorage.setItem('dw-map-lng', c.lng);
        });

        // (Re-)add the station source + layers after every style load, including
        // after theme switches via map.setStyle().
        map.on('style.load', addStationLayers);
        map.on('style.load', addOverlaySources);

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
        const resp = await fetch('/static/basemap/style.' + name + '.json');
        const style = await resp.json();
        if (style.sources && style.sources.protomaps) {
            style.sources.protomaps.url = 'pmtiles://' + new URL('/basemap/region.pmtiles', window.location.href).href;
        }
        return absolutizeStyleUrls(style, window.location.href);
    }

    /** One GeoJSON source + symbol layers for all stations:
     *  base APRS icon (aprs:aprs-p-XX / aprs:aprs-a-XX), optional overlay
     *  icon (aprs:aprs-o-YY) stacked on top, and a callsign text label. */
    function addStationLayers() {
        if (!mainMapIsMaplibre || !map) return;
        if (map.getSource('stations')) return;

        map.addSource('stations', { type: 'geojson', data: stationFeatureCollection });

        map.addLayer({
            id: 'stations-symbol-base',
            type: 'symbol',
            source: 'stations',
            layout: {
                'icon-image': [
                    'case',
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
                'text-font': ['NotoSansRegular'],
                'text-size': 11,
                'text-anchor': 'top',
                'text-offset': [0, 1.2],
                'text-allow-overlap': true,
                'text-ignore-placement': true,
            },
            paint: {
                'text-color': [
                    'case',
                    ['get', 'is_my'],
                    themeColor('--accent'),
                    themeColor('--text-primary'),
                ],
                'text-halo-color': themeColor('--label-shadow'),
                'text-halo-width': 1,
            },
        });

        scheduleStationRefresh();
    }

    // --- Dynamic GeoJSON line sources (tracks, popup overlay, tx arcs) ---
    // Added below the station symbol layers; re-added after every style.load (so
    // theme switches survive). Their data is stored in JS state, not the map.
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
            feats.push(stationFeatures[cs]);
        }
        return { type: 'FeatureCollection', features: feats };
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
     *  L.Control.extend pattern for the main map). */
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
            const now = Date.now();
            for (const [callsign, pos] of Object.entries(data)) {
                stationPositionCache[callsign] = { lat: pos.lat, lng: pos.lng, updatedAt: now };
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
                stationTracks[cs] = points.map(p => [p[0], p[1]]);  // [lat, lon]
            }
            refreshStationTrackSource();
        } catch (e) {
            console.error('Failed to load tracks:', e);
        }
    }

    function addOrUpdateStation(data) {
        if (data.latitude == null || data.longitude == null) return;

        const cs = data.callsign;
        const symbolChar = data.symbol || '-';
        const symbolTable = data.symbol_table || '/';

        if (!stations[cs]) {
            stations[cs] = { data: data, popupDiv: null };
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

    function updateStationTrack(callsign, lat, lon) {
        const s = stations[callsign];
        if (!s) return;
        if (!stationTracks[callsign]) stationTracks[callsign] = [];
        stationTracks[callsign].push([lat, lon]);
        refreshStationTrackSource();
    }

    // --- APRS Path Parsing ---
    function parsePath(pathArray) {
        const result = { digipeaters: [], igate: null, isTcpip: false };
        if (!pathArray || !Array.isArray(pathArray) || pathArray.length === 0) return result;
        if (pathArray.some(p => p === 'TCPIP' || p === 'TCPIP*')) result.isTcpip = true;
        if (pathArray.some(p => p === 'qAC' || p === 'qAU')) result.isTcpip = true;
        for (let i = 0; i < pathArray.length; i++) {
            const part = pathArray[i];
            if (part.startsWith('qA') && i + 1 < pathArray.length) {
                result.igate = pathArray[i + 1];
                break;
            }
            if (part.endsWith('*')) {
                const callsign = part.slice(0, -1);
                if (!GENERIC_PATH_ALIASES.has(callsign) && !GENERIC_PATH_ALIASES.has(callsign.replace(/-\d+$/, ''))) {
                    result.digipeaters.push(callsign);
                }
            }
        }
        return result;
    }

    // --- Station overlay: path lines, distance, log highlight ---
    function haversineKm(lat1, lon1, lat2, lon2) {
        const R = 6371;
        const dLat = (lat2 - lat1) * Math.PI / 180;
        const dLon = (lon2 - lon1) * Math.PI / 180;
        const a = Math.sin(dLat / 2) ** 2 +
            Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) * Math.sin(dLon / 2) ** 2;
        return R * 2 * Math.asin(Math.sqrt(a));
    }

    var _selectedCallsign = null;

    function clearStationOverlay() {
        _overlayFC = { type: 'FeatureCollection', features: [] };
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
        const cached = stationPositionCache[callsign];
        if (cached) return { lat: cached.lat, lng: cached.lng };
        return null;
    }

    function updatePositionCache(callsign, lat, lng) {
        stationPositionCache[callsign] = { lat, lng, updatedAt: Date.now() };
        const keys = Object.keys(stationPositionCache);
        if (keys.length > POSITION_CACHE_MAX) {
            let oldestKey = keys[0];
            let oldestTime = stationPositionCache[oldestKey].updatedAt;
            for (const key of keys) {
                if (stationPositionCache[key].updatedAt < oldestTime) {
                    oldestKey = key;
                    oldestTime = stationPositionCache[key].updatedAt;
                }
            }
            delete stationPositionCache[oldestKey];
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
            case 'preload_progress':
                onPreloadProgress(msg.data);
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
        if (packet.latitude != null && packet.longitude != null) {
            updatePositionCache(packet.from_call, packet.latitude, packet.longitude);
        }
        if (packet.latitude != null && packet.longitude != null) {
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
                updateStationTrack(packet.from_call, packet.latitude, packet.longitude);
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

        // Tile mode toggle
        document.getElementById('cfg-tile-mode').addEventListener('change', (e) => {
            const preloadSection = document.getElementById('preload-section');
            if (e.target.value === 'preload') {
                preloadSection.classList.remove('hidden');
            } else {
                preloadSection.classList.add('hidden');
            }
        });

        // Preload controls
        document.getElementById('btn-use-map-view').addEventListener('click', () => {
            if (map) {
                const bounds = map.getBounds();
                document.getElementById('preload-south').value = bounds.getSouth().toFixed(4);
                document.getElementById('preload-west').value = bounds.getWest().toFixed(4);
                document.getElementById('preload-north').value = bounds.getNorth().toFixed(4);
                document.getElementById('preload-east').value = bounds.getEast().toFixed(4);
            }
        });

        document.getElementById('btn-estimate').addEventListener('click', estimatePreload);
        document.getElementById('btn-download').addEventListener('click', startPreload);
        document.getElementById('btn-cancel-download').addEventListener('click', cancelPreload);

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
        document.getElementById('cfg-tile-mode').value = config.tiles?.cache_mode || 'lazy';
        document.getElementById('cfg-max-cache').value = config.tiles?.max_cache_mb || 500;

        // Fetch and display tile cache stats
        fetch(API_BASE + '/stats').then(function (r) { return r.json(); }).then(function (stats) {
            var tc = stats.tile_cache || {};
            var el = document.getElementById('tile-cache-stats');
            if (el) {
                el.textContent = 'Current usage: ' + (tc.cache_size_mb || 0) + ' MB (' + (tc.tile_count || 0) + ' tiles)';
            }
        }).catch(function () {});

        // Map display settings
        document.getElementById('cfg-show-route-distances').checked = showRouteDistances;
        document.getElementById('cfg-show-gpx-overlay').checked = showGpxOverlay;
        document.getElementById('cfg-show-stats-overlay').checked = showStatsOverlay;
        document.getElementById('cfg-show-my-location-overlay').checked = showMyLocationOverlay;

        // Packet log settings
        document.getElementById('cfg-show-timestamps').checked = showTimestamps;

        // Log color inputs
        _populateLogColorInputs();

        // Show/hide preload section
        if (config.tiles?.cache_mode === 'preload') {
            document.getElementById('preload-section').classList.remove('hidden');
        }
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
            tiles: {
                cache_mode: document.getElementById('cfg-tile-mode').value,
                max_cache_mb: parseInt(document.getElementById('cfg-max-cache').value),
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
                config = { ...config, ...updates };
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
            var s = stations[call];
            if (!mainMapIsMaplibre) {
                if (s.marker && map) map.removeLayer(s.marker);
                if (s.track && map) map.removeLayer(s.track);
            }
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
        Object.keys(stationPositionCache).forEach(function (k) {
            delete stationPositionCache[k];
        });
        // Re-apply my position marker (uses saved coords from config as fallback)
        updateMyPositionMarker();
    }

    // --- Tile Preload ---
    async function estimatePreload() {
        const bbox = getPreloadBbox();
        if (!bbox) return;

        try {
            const resp = await fetch(API_BASE + '/tiles/preload', {
                method: 'POST',
                headers: _apiHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify(bbox),
            });
            const est = await resp.json();

            const el = document.getElementById('preload-estimate');
            el.textContent = `${est.estimated_tiles} tiles (~${est.estimated_size_mb} MB)`;
            el.classList.remove('hidden');
            document.getElementById('btn-download').classList.remove('hidden');
        } catch (e) {
            console.error('Estimate failed:', e);
        }
    }

    async function startPreload() {
        const bbox = getPreloadBbox();
        if (!bbox) return;
        bbox.confirm = true;

        try {
            await fetch(API_BASE + '/tiles/preload', {
                method: 'POST',
                headers: _apiHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify(bbox),
            });

            document.getElementById('btn-cancel-download').classList.remove('hidden');
            document.getElementById('preload-progress').classList.remove('hidden');
        } catch (e) {
            console.error('Preload start failed:', e);
        }
    }

    async function cancelPreload() {
        try {
            await fetch(API_BASE + '/tiles/preload', { method: 'DELETE', headers: _apiHeaders() });
            document.getElementById('btn-cancel-download').classList.add('hidden');
            document.getElementById('preload-progress').classList.add('hidden');
        } catch (e) {
            console.error('Cancel failed:', e);
        }
    }

    function onPreloadProgress(data) {
        const pct = Math.round((data.done / data.total) * 100);
        document.querySelector('.progress-fill').style.width = pct + '%';
        document.getElementById('preload-progress-text').textContent =
            `${data.done} / ${data.total} tiles (${pct}%)`;

        if (data.done >= data.total) {
            document.getElementById('btn-cancel-download').classList.add('hidden');
            document.getElementById('preload-progress-text').textContent = 'Complete!';
        }
    }

    function getPreloadBbox() {
        const south = parseFloat(document.getElementById('preload-south').value);
        const west = parseFloat(document.getElementById('preload-west').value);
        const north = parseFloat(document.getElementById('preload-north').value);
        const east = parseFloat(document.getElementById('preload-east').value);
        const minZoom = parseInt(document.getElementById('preload-min-zoom').value);
        const maxZoom = parseInt(document.getElementById('preload-max-zoom').value);

        if (isNaN(south) || isNaN(west) || isNaN(north) || isNaN(east)) {
            alert('Please fill in all bounding box coordinates.');
            return null;
        }

        return { bbox: [south, west, north, east], min_zoom: minZoom, max_zoom: maxZoom };
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
            var toolbar = document.getElementById('toolbar');
            var footer = document.getElementById('footer');
            var chrome = (toolbar ? toolbar.offsetHeight : 40) + (footer ? footer.offsetHeight : 24);
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
            var toolbar = document.getElementById('toolbar');
            var footer = document.getElementById('footer');
            var minMap = 150;
            var minLog = 80;
            var maxMap = window.innerHeight - (toolbar ? toolbar.offsetHeight : 40) - (footer ? footer.offsetHeight : 24) - minLog;
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

    // --- Mobile Menu ---
    function initMobileMenu() {
        const btn = document.getElementById('btn-mobile-menu');
        const menu = document.getElementById('mobile-menu');
        if (!btn || !menu) return;

        // Toggle menu
        btn.addEventListener('click', () => {
            menu.classList.toggle('hidden');
        });

        // Mobile buttons -> delegate to desktop buttons
        document.getElementById('mobile-btn-toggle-log').addEventListener('click', () => {
            toggleLogViewState();
            menu.classList.add('hidden');
        });
        document.getElementById('mobile-btn-settings').addEventListener('click', () => {
            document.getElementById('btn-settings').click();
            menu.classList.add('hidden');
        });

        // Close menu when clicking outside
        document.addEventListener('click', (e) => {
            if (!menu.classList.contains('hidden') &&
                !menu.contains(e.target) &&
                !btn.contains(e.target)) {
                menu.classList.add('hidden');
            }
        });
    }

    // --- Cold Start / Map Centering ---
    async function initMapCenter() {
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

    function showWaitingToast() {
        const toast = document.getElementById('waiting-toast');
        if (toast) toast.classList.remove('hidden');
    }

    function dismissWaiting() {
        waitingForPosition = false;
        const modal = document.getElementById('waiting-modal');
        if (modal) modal.classList.add('hidden');
        const toast = document.getElementById('waiting-toast');
        if (toast) toast.classList.add('hidden');
    }

    // --- My Position Marker ---
    function updateMyPositionMarker() {
        // Remove old pin marker if present
        if (myPositionPinMarker) {
            if (mainMapIsMaplibre) myPositionPinMarker.remove();
            else map.removeLayer(myPositionPinMarker);
            myPositionPinMarker = null;
        }

        const mp = config.station?.my_position;
        if (!mp || !mp.type) {
            if (mainMapIsMaplibre) scheduleStationRefresh();
            return;
        }

        if (mp.type === 'pin' && mp.latitude != null && mp.longitude != null) {
            if (mainMapIsMaplibre) {
                if (!map) return;  // no WebGL2 / map failed to init
                const el = document.createElement('div');
                el.className = 'my-position-pin';
                el.innerHTML = '<div class="pin-marker">📌</div>';
                const popupEl = document.createElement('div');
                popupEl.innerHTML = '<b>My Position</b><br>(dropped pin)<br>';
                const btn = document.createElement('button');
                btn.className = 'popup-btn popup-btn-remove';
                btn.textContent = 'Remove Pin';
                btn.addEventListener('click', window._removeMyPosition);
                popupEl.appendChild(btn);
                myPositionPinMarker = new maplibregl.Marker({ element: el })
                    .setLngLat([mp.longitude, mp.latitude])
                    .setPopup(new maplibregl.Popup({ offset: 20 }).setDOMContent(popupEl))
                    .addTo(map);
            } else {
                const icon = L.divIcon({
                    className: 'my-position-pin',
                    html: '<div class="pin-marker">📌</div>',
                    iconSize: [24, 24],
                    iconAnchor: [12, 24],
                    popupAnchor: [0, -24],
                });
                myPositionPinMarker = L.marker([mp.latitude, mp.longitude], { icon: icon })
                    .addTo(map)
                    .bindPopup('<b>My Position</b><br>(dropped pin)<br><button class="popup-btn popup-btn-remove" onclick="window._removeMyPosition()">Remove Pin</button>');
            }
        } else if (mp.type === 'station' && mp.callsign) {
            if (mainMapIsMaplibre) {
                // Highlight handled via the is_my label color in the GeoJSON source.
                scheduleStationRefresh();
            } else {
                const s = stations[mp.callsign];
                if (s && s.marker) {
                    const el = s.marker.getElement?.();
                    if (el) el.classList.add('my-station');
                }
            }
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
        const mobileBtn = document.getElementById('mobile-btn-drop-pin');
        if (btn) {
            btn.addEventListener('click', () => togglePinMode());
        }
        if (mobileBtn) {
            mobileBtn.addEventListener('click', () => {
                togglePinMode();
                document.getElementById('mobile-menu')?.classList.add('hidden');
            });
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

        // Create the mini map using our local tile proxy
        decodeMiniMap = L.map('decode-mini-map', {
            zoomControl: true,
            attributionControl: true,
            scrollWheelZoom: true,
            dragging: true,
        });

        L.tileLayer('/api/tiles/{z}/{x}/{y}.png', {
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>',
            maxZoom: 19,
        }).addTo(decodeMiniMap);

        // Collect all points for bounds fitting
        const allPoints = [[stationLat, stationLon]];

        // Add station marker with APRS symbol icon
        const stationIcon = createSymbolIcon(symbolTable, symbolChar, stationCall);
        const stationMarker = L.marker([stationLat, stationLon], { icon: stationIcon }).addTo(decodeMiniMap);
        stationMarker.bindPopup('<strong>' + escHtml(stationCall) + '</strong>');

        // Add path station markers and build polyline
        const linePoints = [[stationLat, stationLon]];

        for (const hop of pathOrder) {
            const clean = hop.replace('*', '').trim().toUpperCase();
            if (pathStations[clean]) {
                const pos = pathStations[clean];
                allPoints.push([pos.latitude, pos.longitude]);
                linePoints.push([pos.latitude, pos.longitude]);

                const pathIcon = createSymbolIcon(
                    pos.symbol_table || '/',
                    pos.symbol || '>',
                    clean
                );
                const pathMarker = L.marker([pos.latitude, pos.longitude], { icon: pathIcon }).addTo(decodeMiniMap);
                pathMarker.bindPopup('<strong>' + escHtml(clean) + '</strong>');
            }
        }

        // Draw path polyline
        if (linePoints.length > 1) {
            L.polyline(linePoints, {
                color: themeColor('--segment-path-fg'),
                weight: 2,
                dashArray: '6, 8',
                opacity: 0.7,
            }).addTo(decodeMiniMap);
        }

        // Fit bounds or set view
        if (allPoints.length > 1) {
            decodeMiniMap.fitBounds(allPoints, { padding: [40, 40], maxZoom: 14 });
        } else {
            decodeMiniMap.setView([stationLat, stationLon], 13);
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

})();
