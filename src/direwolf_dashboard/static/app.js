/**
 * Direwolf Dashboard — Client-side application
 *
 * Handles: WebSocket connection, Leaflet map, packet log, filters, settings
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
    let stations = {};      // callsign -> { marker, track, data }
    let autoScroll = true;
    const MAX_LOG_ROWS = 500;
    let trailHours = 1;  // Current trail duration in hours
    let showRouteDistances = true; // updated from config on load
    let waitingForPosition = false;
    let myPositionPinMarker = null;
    let pinModeActive = false;

    // --- Log View State ---
    const LOG_STATES = ['expanded', 'peek', 'hidden'];
    const LOG_STATE_KEY = 'logViewState';
    let logViewState = 'peek';
    let resizeEnabled = true;

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

    // --- APRS Symbol Sprites ---
    const SYMBOL_SIZE = 24;
    const SPRITE_COLS = 16;
    const PRIMARY_SPRITE = 'https://raw.githubusercontent.com/hessu/aprs-symbols/master/png/aprs-symbols-24-0.png';
    const SECONDARY_SPRITE = 'https://raw.githubusercontent.com/hessu/aprs-symbols/master/png/aprs-symbols-24-1.png';

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

    function createSymbolIcon(symbolTable, symbolChar, callsign) {
        const pos = getSymbolSpritePosition(symbolChar);
        const { spriteUrl, overlay } = parseSymbolTable(symbolTable);

        let html = '<div style="position:relative;width:24px;height:24px;">';
        html += `<div style="width:24px;height:24px;background-image:url('${spriteUrl}');background-position:${pos.x}px ${pos.y}px;background-repeat:no-repeat;image-rendering:pixelated;"></div>`;

        if (overlay) {
            html += `<div style="position:absolute;top:0;left:0;width:24px;height:24px;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:bold;color:#000;text-shadow:0 0 2px #fff, 0 0 2px #fff;">${overlay}</div>`;
        }

        if (callsign) {
            html += `<span class="station-label" style="position:absolute;left:26px;top:50%;transform:translateY(-50%);white-space:nowrap;">${callsign}</span>`;
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
        const LegendControl = L.Control.extend({
            options: { position: 'bottomright' },
            onAdd: function () {
                const container = L.DomUtil.create('div', 'map-legend');
                container.innerHTML = `
                    <div class="map-legend-header">
                        Legend &#9660;
                    </div>
                    <div class="legend-body">
                        <div class="legend-section-title">Animations</div>
                        <div class="legend-item">
                            <svg width="16" height="16" viewBox="0 0 16 16">
                                <path d="M 3 6 A 6 6 0 0 1 3 10" fill="none" stroke="#ff0000" stroke-width="1.5" stroke-linecap="round" stroke-opacity="0.8"/>
                                <path d="M 13 6 A 6 6 0 0 0 13 10" fill="none" stroke="#ff0000" stroke-width="1.5" stroke-linecap="round" stroke-opacity="0.8"/>
                            </svg>
                            <span>RF Transmit</span>
                        </div>
                        <div class="legend-item">
                            <svg width="16" height="16" viewBox="0 0 16 16">
                                <path d="M 3 6 A 6 6 0 0 1 3 10" fill="none" stroke="#555555" stroke-width="1.5" stroke-linecap="round" stroke-opacity="0.8"/>
                                <path d="M 13 6 A 6 6 0 0 0 13 10" fill="none" stroke="#555555" stroke-width="1.5" stroke-linecap="round" stroke-opacity="0.8"/>
                            </svg>
                            <span>Internet (TCPIP)</span>
                        </div>
                        <div class="legend-item">
                            <svg width="16" height="16" viewBox="0 0 16 16">
                                <path d="M 6 3 A 6 6 0 0 1 10 3" fill="none" stroke="#00cc00" stroke-width="1.5" stroke-linecap="round" stroke-opacity="0.8"/>
                                <path d="M 6 13 A 6 6 0 0 0 10 13" fill="none" stroke="#00cc00" stroke-width="1.5" stroke-linecap="round" stroke-opacity="0.8"/>
                            </svg>
                            <span>Receiving</span>
                        </div>
                        <div class="legend-section-title">Trails</div>
                        <div class="legend-item">
                            <div style="width:20px;height:3px;background:#00b4d8;opacity:0.7;border-radius:2px;"></div>
                            <span>Movement trail</span>
                        </div>
                        <div class="legend-item">
                            <div style="width:20px;height:0;border-top:3px dashed #0000ff;opacity:0.6;"></div>
                            <span>Packet route</span>
                        </div>
                    </div>
                `;
                const header = container.querySelector('.map-legend-header');
                header.addEventListener('click', () => container.classList.toggle('collapsed'));
                L.DomEvent.disableClickPropagation(container);
                L.DomEvent.disableScrollPropagation(container);
                return container;
            },
        });
        new LegendControl().addTo(map);
    }

    // --- Init ---
    document.addEventListener('DOMContentLoaded', async () => {
        // Init decode modal immediately (doesn't depend on config/network)
        initDecode();
        await loadConfig();
        initMap();
        initLegend();
        await loadStations();
        loadStationPositions();
        loadTracks();
        await initMapCenter();
        updateMyPositionMarker();
        connectWebSocket();
        initFilters();
        initSettings();
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
            }
        } catch (e) {
            console.error('Failed to load config:', e);
        }
    }

    // --- Map ---
    function initMap() {
        const lat = config.station?.latitude || 0;
        const lon = config.station?.longitude || 0;
        const zoom = config.station?.zoom || 12;

        map = L.map('map').setView([lat, lon], zoom);

        L.tileLayer(API_BASE + '/tiles/{z}/{x}/{y}.png', {
            attribution: '&copy; OpenStreetMap contributors',
            maxZoom: 18,
        }).addTo(map);

        setInterval(cleanupAnimations, 10000);
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
            // Clear all existing tracks
            for (const cs of Object.keys(stations)) {
                if (stations[cs].track) {
                    stations[cs].track.setLatLngs([]);
                }
            }
            // Draw tracks from historical data
            for (const [cs, points] of Object.entries(tracks)) {
                if (!stations[cs]) continue;
                const latlngs = points.map(p => [p[0], p[1]]);  // [lat, lon]
                stations[cs].track.setLatLngs(latlngs);
            }
        } catch (e) {
            console.error('Failed to load tracks:', e);
        }
    }

    function addOrUpdateStation(data) {
        if (!data.latitude || !data.longitude) return;

        const cs = data.callsign;
        const symbolChar = data.symbol || '-';
        const symbolTable = data.symbol_table || '/';

        if (stations[cs]) {
            // Update existing marker
            stations[cs].marker.setLatLng([data.latitude, data.longitude]);
            // Update icon if symbol changed
            const oldKey = (stations[cs].data.symbol_table || '/') + (stations[cs].data.symbol || '-');
            const newKey = symbolTable + symbolChar;
            if (oldKey !== newKey) {
                stations[cs].marker.setIcon(createSymbolIcon(symbolTable, symbolChar, cs));
            }
            stations[cs].data = data;
            updateStationPopup(cs);
        } else {
            // Create new marker with APRS symbol icon
            const icon = createSymbolIcon(symbolTable, symbolChar, cs);
            const marker = L.marker([data.latitude, data.longitude], { icon: icon }).addTo(map);

            stations[cs] = {
                marker: marker,
                track: L.polyline([], { color: '#00b4d8', weight: 1.5, opacity: 0.6 }).addTo(map),
                data: data,
            };

            updateStationPopup(cs);
        }
    }

    function updateStationPopup(callsign) {
        const s = stations[callsign];
        if (!s) return;

        const d = s.data;
        let html = `<b>${callsign}</b><br>`;
        if (d.last_comment) html += `${d.last_comment}<br>`;
        if (d.packet_count) html += `Packets: ${d.packet_count}<br>`;
        if (d.last_seen) {
            const ago = Math.round((Date.now() / 1000 - d.last_seen) / 60);
            html += `Last seen: ${ago}m ago<br>`;
        }
        // "Set as My Position" / "Remove as My Position" button
        const mp = config.station?.my_position;
        const isMyStation = mp && mp.type === 'station' && mp.callsign === callsign;
        if (isMyStation) {
            html += `<button class="popup-btn popup-btn-remove" onclick="window._removeMyPosition()">Remove as My Position</button>`;
        } else {
            html += `<button class="popup-btn popup-btn-set" onclick="window._setMyPositionStation('${callsign}')">Set as My Position</button>`;
        }
        s.marker.bindPopup(html);
    }

    function updateStationTrack(callsign, lat, lon) {
        const s = stations[callsign];
        if (!s) return;
        const latlngs = s.track.getLatLngs();
        latlngs.push([lat, lon]);
        s.track.setLatLngs(latlngs);
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

    // --- SVG Arc Animation Icons ---
    function createTransmitArcIcon(index, color) {
        const strokeColor = color || '#ff0000';
        const size = 40 + (index * 8);
        const center = size / 2;
        const radius = 10 + (index * 7);
        const arcAngle = 50;
        const startAngle = 180 + (90 - arcAngle / 2);
        const endAngle = startAngle + arcAngle;
        function polarToCartesian(cx, cy, r, angleDeg) {
            const rad = (angleDeg * Math.PI) / 180;
            return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
        }
        const lStart = polarToCartesian(center, center, radius, startAngle);
        const lEnd = polarToCartesian(center, center, radius, endAngle);
        const leftArc = `M ${lStart.x} ${lStart.y} A ${radius} ${radius} 0 0 1 ${lEnd.x} ${lEnd.y}`;
        const rStartAngle = 360 - endAngle + 180;
        const rEndAngle = rStartAngle + arcAngle;
        const rStart = polarToCartesian(center, center, radius, rStartAngle);
        const rEnd = polarToCartesian(center, center, radius, rEndAngle);
        const rightArc = `M ${rStart.x} ${rStart.y} A ${radius} ${radius} 0 0 1 ${rEnd.x} ${rEnd.y}`;
        const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
            <path d="${leftArc}" fill="none" stroke="${strokeColor}" stroke-width="3" stroke-linecap="round" stroke-opacity="0.7"/>
            <path d="${rightArc}" fill="none" stroke="${strokeColor}" stroke-width="3" stroke-linecap="round" stroke-opacity="0.7"/>
        </svg>`;
        const svgUrl = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
        return L.icon({ iconUrl: svgUrl, iconSize: [size, size], iconAnchor: [center, center] });
    }

    function createReceiveArcIcon(index) {
        const size = 40 + (index * 8);
        const center = size / 2;
        const radius = 10 + (index * 7);
        const arcAngle = 50;
        const startAngle = 90 + (90 - arcAngle / 2);
        const endAngle = startAngle + arcAngle;
        function polarToCartesian(cx, cy, r, angleDeg) {
            const rad = (angleDeg * Math.PI) / 180;
            return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
        }
        const tStart = polarToCartesian(center, center, radius, startAngle);
        const tEnd = polarToCartesian(center, center, radius, endAngle);
        const topArc = `M ${tStart.x} ${tStart.y} A ${radius} ${radius} 0 0 1 ${tEnd.x} ${tEnd.y}`;
        const bStartAngle = 360 - endAngle + 180;
        const bEndAngle = bStartAngle + arcAngle;
        const bStart = polarToCartesian(center, center, radius, bStartAngle);
        const bEnd = polarToCartesian(center, center, radius, bEndAngle);
        const bottomArc = `M ${bStart.x} ${bStart.y} A ${radius} ${radius} 0 0 1 ${bEnd.x} ${bEnd.y}`;
        const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
            <path d="${topArc}" fill="none" stroke="#00cc00" stroke-width="3" stroke-linecap="round" stroke-opacity="0.7"/>
            <path d="${bottomArc}" fill="none" stroke="#00cc00" stroke-width="3" stroke-linecap="round" stroke-opacity="0.7"/>
        </svg>`;
        const svgUrl = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
        return L.icon({ iconUrl: svgUrl, iconSize: [size, size], iconAnchor: [center, center] });
    }

    // --- Route Polylines ---
    function createRoutingPolylines(stationLat, stationLng, pathInfo) {
        if (!pathInfo) return [];
        const waypoints = [{ lat: stationLat, lng: stationLng }];
        for (const digiCall of pathInfo.digipeaters) {
            const pos = getStationPosition(digiCall);
            if (pos) waypoints.push(pos);
        }
        if (pathInfo.igate) {
            const pos = getStationPosition(pathInfo.igate);
            if (pos) waypoints.push(pos);
        }
        if (waypoints.length < 2) return [];
        const elements = [];
        for (let i = 0; i < waypoints.length - 1; i++) {
            const from = L.latLng(waypoints[i].lat, waypoints[i].lng);
            const to = L.latLng(waypoints[i + 1].lat, waypoints[i + 1].lng);
            const line = L.polyline(
                [[from.lat, from.lng], [to.lat, to.lng]],
                { color: '#0000ff', weight: 3, opacity: 0.6, dashArray: '4,8', lineCap: 'round', lineJoin: 'round' }
            );
            elements.push(line);

            if (showRouteDistances) {
                const distMeters = from.distanceTo(to);
                const distKm = distMeters / 1000;
                const distMiles = distMeters / 1609.344;
                const label = `${distKm.toFixed(2)} km / ${distMiles.toFixed(2)} mi`;
                // Midpoint in screen space
                const fromPt = map.latLngToContainerPoint(from);
                const toPt = map.latLngToContainerPoint(to);
                const midPt = L.point((fromPt.x + toPt.x) / 2, (fromPt.y + toPt.y) / 2);
                // Compute screen-space angle from the two endpoints
                const dx = toPt.x - fromPt.x;
                const dy = toPt.y - fromPt.y;
                let angle = Math.atan2(dy, dx) * 180 / Math.PI;
                // Keep text readable (not upside-down)
                if (angle > 90) angle -= 180;
                if (angle < -90) angle += 180;
                // Offset perpendicular to the line (above it) by 8 pixels
                const len = Math.sqrt(dx * dx + dy * dy) || 1;
                const perpX = -dy / len * 8;
                const perpY =  dx / len * 8;
                const offsetPt = L.point(midPt.x + perpX, midPt.y + perpY);
                const offsetLatLng = map.containerPointToLatLng(offsetPt);
                const icon = L.divIcon({
                    className: 'route-distance-label',
                    html: `<span style="transform:rotate(${angle.toFixed(1)}deg);transform-origin:center center">${label}</span>`,
                    iconSize: [120, 16],
                    iconAnchor: [60, 8],
                });
                const marker = L.marker(offsetLatLng, { icon, interactive: false });
                elements.push(marker);
            }
        }
        return elements;
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
                try { map.removeLayer(entry.element); } catch (e) { /* already removed */ }
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
        const latLng = [lat, lng];
        const arcMarkers = [];
        for (let i = 1; i <= 3; i++) {
            const m = L.marker(latLng, { icon: createReceiveArcIcon(i), interactive: false, zIndexOffset: 1000 });
            arcMarkers.push(m);
            trackAnimationElement(m, now + 1100);
        }
        arcMarkers[2].addTo(map);
        setTimeout(() => { arcMarkers[1].addTo(map); }, 150);
        setTimeout(() => { arcMarkers[0].addTo(map); }, 300);
        setTimeout(() => { map.removeLayer(arcMarkers[2]); }, 800);
        setTimeout(() => { map.removeLayer(arcMarkers[1]); }, 900);
        setTimeout(() => { map.removeLayer(arcMarkers[0]); }, 1000);
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
        const latLng = [lat, lng];
        const pathInfo = parsePath(pathArray);
        const txColor = pathInfo.isTcpip ? '#333333' : '#ff0000';
        const arcMarkers = [];
        for (let i = 1; i <= 3; i++) {
            const m = L.marker(latLng, { icon: createTransmitArcIcon(i, txColor), interactive: false, zIndexOffset: 1000 });
            arcMarkers.push(m);
            trackAnimationElement(m, now + 1100);
        }
        const routingLines = createRoutingPolylines(lat, lng, pathInfo);
        for (const line of routingLines) {
            trackAnimationElement(line, now + 5500);
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
        arcMarkers[0].addTo(map);
        setTimeout(() => { arcMarkers[1].addTo(map); }, 150);
        setTimeout(() => {
            arcMarkers[2].addTo(map);
            for (const line of routingLines) line.addTo(map);
        }, 300);
        setTimeout(() => { map.removeLayer(arcMarkers[0]); }, 800);
        setTimeout(() => { map.removeLayer(arcMarkers[1]); }, 900);
        setTimeout(() => { map.removeLayer(arcMarkers[2]); }, 1000);
        for (let i = 0; i < receivers.length; i++) {
            const rx = receivers[i];
            setTimeout(() => { playReceiveAnimation(rx.callsign, rx.lat, rx.lng); }, 300 + (i * 300));
        }
        if (routingLines.length > 0) {
            setTimeout(() => {
                for (const line of routingLines) map.removeLayer(line);
            }, 5000);
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
                const msg = JSON.parse(evt.data);
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
        addLogRow(packet);
        if (packet.latitude != null && packet.longitude != null) {
            updatePositionCache(packet.from_call, packet.latitude, packet.longitude);
        }
        if (packet.latitude && packet.longitude) {
            if (waitingForPosition) {
                dismissWaiting();
                const zoom = config.station?.zoom || 12;
                map.flyTo([packet.latitude, packet.longitude], zoom);
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
            });
            if (!packet.position_from_db) {
                updateStationTrack(packet.from_call, packet.latitude, packet.longitude);
            }
        }
        const myPos = getMyPosition();
        if (packet.tx) {
            if (myPos) {
                playTransmitAnimation('My Station', myPos.lat, myPos.lng, []);
            }
        } else {
            if (packet.latitude && packet.longitude) {
                playTransmitAnimation(packet.from_call, packet.latitude, packet.longitude, packet.path || []);
            }
            if (myPos) {
                const myCall = config.station?.my_position?.callsign;
                if (myCall) {
                    setTimeout(() => {
                        playReceiveAnimation(myCall, myPos.lat, myPos.lng);
                    }, 300);
                }
            }
        }
    }

    function onStats(stats) {
        // Could update a stats display — for now just update status
        if (stats.agw_connected !== undefined) {
            setStatus(stats.agw_connected);
        }
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

    // --- Packet Log ---
    function addLogRow(packet) {
        const logList = document.getElementById('log-list');

        // Create row
        const row = document.createElement('div');
        row.className = 'log-row';
        row.dataset.callsign = packet.from_call || '';
        row.dataset.type = packet.type || '';
        row.dataset.tx = packet.tx ? 'tx' : 'rx';

        // Expand toggle
        const expand = document.createElement('span');
        expand.className = 'log-expand';
        expand.textContent = '\u25B6';  // ▶

        // Compact log content
        const content = document.createElement('span');
        content.className = 'log-content';
        content.innerHTML = packet.compact_log || `${packet.from_call} > ${packet.to_call}`;

        row.appendChild(expand);
        row.appendChild(content);

        // Raw log lines (hidden by default)
        const rawDiv = document.createElement('div');
        rawDiv.className = 'log-raw';

        // Show the clean APRS string prominently, then raw log lines below
        const aprsStr = packet.raw_packet || '';
        if (aprsStr) {
            const aprsLine = document.createElement('div');
            aprsLine.className = 'log-raw-aprs';

            const aprsText = document.createElement('code');
            aprsText.textContent = aprsStr;
            aprsLine.appendChild(aprsText);

            const copyBtn = document.createElement('button');
            copyBtn.className = 'log-raw-copy';
            copyBtn.title = 'Copy APRS packet';
            copyBtn.innerHTML = '&#x1F4CB;';
            copyBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                navigator.clipboard.writeText(aprsStr).then(() => {
                    copyBtn.innerHTML = '&#x2713;';
                    copyBtn.classList.add('copied');
                    setTimeout(() => { copyBtn.innerHTML = '&#x1F4CB;'; copyBtn.classList.remove('copied'); }, 1500);
                });
            });
            aprsLine.appendChild(copyBtn);

            const decodeBtn = document.createElement('button');
            decodeBtn.className = 'log-raw-copy';
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
            aprsLine.appendChild(decodeBtn);

            rawDiv.appendChild(aprsLine);
        }

        // Show additional raw log lines (Direwolf log) if available
        if (packet.raw_log && packet.raw_log.length > 0) {
            const logLines = document.createElement('div');
            logLines.className = 'log-raw-extra';
            logLines.textContent = packet.raw_log.join('\n');
            rawDiv.appendChild(logLines);
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
        const typeSelect = document.getElementById('filter-type');
        const txrxSelect = document.getElementById('filter-txrx');
        const trailSelect = document.getElementById('trail-duration');

        callsignInput.addEventListener('input', applyFilters);
        typeSelect.addEventListener('change', applyFilters);
        txrxSelect.addEventListener('change', applyFilters);

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
    }

    function applyFilters() {
        const callsign = document.getElementById('filter-callsign').value.toUpperCase();
        const type = document.getElementById('filter-type').value;
        const txrx = document.getElementById('filter-txrx').value;

        const rows = document.querySelectorAll('.log-row');
        rows.forEach(row => {
            applyFilterToRow(row, callsign, type, txrx);
        });
    }

    function applyFilterToRow(row, callsign, type, txrx) {
        callsign = callsign || document.getElementById('filter-callsign').value.toUpperCase();
        type = type || document.getElementById('filter-type').value;
        txrx = txrx || document.getElementById('filter-txrx').value;

        let visible = true;
        if (callsign && !row.dataset.callsign.includes(callsign)) visible = false;
        if (type && row.dataset.type !== type) visible = false;
        if (txrx && row.dataset.tx !== txrx) visible = false;

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
    }

    function populateSettings() {
        document.getElementById('cfg-latitude').value = config.station?.latitude || 0;
        document.getElementById('cfg-longitude').value = config.station?.longitude || 0;
        document.getElementById('cfg-zoom').value = config.station?.zoom || 12;
        document.getElementById('cfg-agw-host').value = config.direwolf?.agw_host || 'localhost';
        document.getElementById('cfg-agw-port').value = config.direwolf?.agw_port || 8000;
        document.getElementById('cfg-log-file').value = config.direwolf?.log_file || '';
        document.getElementById('cfg-server-port').value = config.server?.port || 8080;
        document.getElementById('cfg-retention').value = config.storage?.retention_days || 7;
        document.getElementById('cfg-tile-mode').value = config.tiles?.cache_mode || 'lazy';
        document.getElementById('cfg-max-cache').value = config.tiles?.max_cache_mb || 500;

        // Map display settings
        document.getElementById('cfg-show-route-distances').checked = showRouteDistances;

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
            },
        };

        const feedback = document.getElementById('settings-feedback');
        try {
            const resp = await fetch(API_BASE + '/config', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
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
                // Auto-close settings modal after a short delay
                setTimeout(() => {
                    document.getElementById('settings-modal').classList.add('hidden');
                    feedback.classList.add('hidden');
                }, 1500);
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

    // --- Tile Preload ---
    async function estimatePreload() {
        const bbox = getPreloadBbox();
        if (!bbox) return;

        try {
            const resp = await fetch(API_BASE + '/tiles/preload', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
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
                headers: { 'Content-Type': 'application/json' },
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
            await fetch(API_BASE + '/tiles/preload', { method: 'DELETE' });
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
            logViewState = 'peek';
        }

        applyLogViewState();

        // Click to cycle states
        btn.addEventListener('click', cycleLogViewState);

        // Keyboard shortcut: L to cycle (when not focused on an input)
        document.addEventListener('keydown', (e) => {
            if (e.key === 'l' || e.key === 'L') {
                const tag = document.activeElement?.tagName;
                if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;
                e.preventDefault();
                cycleLogViewState();
            }
        });

        // After CSS transition ends on map container, tell Leaflet to resize
        mapContainer.addEventListener('transitionend', (e) => {
            if (e.propertyName === 'height' || e.propertyName === 'flex') {
                if (map) map.invalidateSize();
            }
        });
    }

    function cycleLogViewState() {
        const idx = LOG_STATES.indexOf(logViewState);
        logViewState = LOG_STATES[(idx + 1) % LOG_STATES.length];
        localStorage.setItem(LOG_STATE_KEY, logViewState);
        applyLogViewState();
    }

    function applyLogViewState() {
        const body = document.body;
        const btn = document.getElementById('btn-toggle-log');
        const mapContainer = document.getElementById('map-container');

        // Remove all state classes, apply current
        for (const s of LOG_STATES) {
            body.classList.remove('log-' + s);
        }
        body.classList.add('log-' + logViewState);

        // Clear any inline height from manual resize so CSS classes take effect
        mapContainer.style.height = '';

        // Update button icon and title
        const icons = { expanded: '\u25BC', peek: '\u2500', hidden: '\u25B2' };  // ▼ ─ ▲
        const titles = {
            expanded: 'Collapse log to peek (L)',
            peek: 'Hide log (L)',
            hidden: 'Show log (L)',
        };
        btn.innerHTML = '<span class="toggle-icon">' + icons[logViewState] + '</span>';
        btn.title = titles[logViewState];

        // Enable/disable resize
        resizeEnabled = (logViewState === 'expanded');

        // Invalidate map size after a brief delay for transition
        if (map) {
            setTimeout(() => map.invalidateSize(), 350);
        }
    }

    // --- Map Resize ---
    function initMapResize() {
        const handle = document.getElementById('map-resize-handle');
        const mapContainer = document.getElementById('map-container');
        let startY, startHeight;

        handle.addEventListener('mousedown', (e) => {
            if (!resizeEnabled) return;
            startY = e.clientY;
            startHeight = mapContainer.offsetHeight;
            document.addEventListener('mousemove', onResize);
            document.addEventListener('mouseup', stopResize);
            e.preventDefault();
        });

        function onResize(e) {
            const newHeight = startHeight + (e.clientY - startY);
            mapContainer.style.height = Math.max(150, newHeight) + 'px';
            map.invalidateSize();
        }

        function stopResize() {
            document.removeEventListener('mousemove', onResize);
            document.removeEventListener('mouseup', stopResize);
        }
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

        // Sync mobile controls -> desktop controls and trigger actions
        const mobileTrail = document.getElementById('mobile-trail-duration');
        const mobileCallsign = document.getElementById('mobile-filter-callsign');
        const mobileType = document.getElementById('mobile-filter-type');
        const mobileTxrx = document.getElementById('mobile-filter-txrx');
        const desktopTrail = document.getElementById('trail-duration');
        const desktopCallsign = document.getElementById('filter-callsign');
        const desktopType = document.getElementById('filter-type');
        const desktopTxrx = document.getElementById('filter-txrx');

        mobileTrail.addEventListener('change', () => {
            desktopTrail.value = mobileTrail.value;
            desktopTrail.dispatchEvent(new Event('change'));
        });
        mobileCallsign.addEventListener('input', () => {
            desktopCallsign.value = mobileCallsign.value;
            desktopCallsign.dispatchEvent(new Event('input'));
        });
        mobileType.addEventListener('change', () => {
            desktopType.value = mobileType.value;
            desktopType.dispatchEvent(new Event('change'));
        });
        mobileTxrx.addEventListener('change', () => {
            desktopTxrx.value = mobileTxrx.value;
            desktopTxrx.dispatchEvent(new Event('change'));
        });

        // Mobile buttons -> delegate to desktop buttons
        document.getElementById('mobile-btn-toggle-log').addEventListener('click', () => {
            cycleLogViewState();
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
        // Priority 1: my_position resolved coords
        const myPos = getMyPosition();
        if (myPos) {
            const zoom = config.station?.zoom || 12;
            map.setView([myPos.lat, myPos.lng], zoom);
            return;
        }

        // Priority 2: config lat/lon if non-zero
        const lat = config.station?.latitude || 0;
        const lon = config.station?.longitude || 0;
        if (lat !== 0 || lon !== 0) {
            const zoom = config.station?.zoom || 12;
            map.setView([lat, lon], zoom);
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
                if (s.data && s.data.latitude && s.data.longitude) {
                    const t = s.data.last_seen || 0;
                    if (!best || t > bestTime) {
                        best = s;
                        bestTime = t;
                    }
                }
            }
            if (best) {
                const zoom = config.station?.zoom || 12;
                map.setView([best.data.latitude, best.data.longitude], zoom);
                return;
            }
        }

        // Priority 4: Cold start - center (0,0) zoom 3, show waiting modal
        map.setView([0, 0], 3);
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
        // Remove old marker if present
        if (myPositionPinMarker) {
            map.removeLayer(myPositionPinMarker);
            myPositionPinMarker = null;
        }
        // Remove my-station class from all station markers
        for (const cs of Object.keys(stations)) {
            const el = stations[cs].marker?.getElement?.();
            if (el) el.classList.remove('my-station');
        }

        const mp = config.station?.my_position;
        if (!mp || !mp.type) return;

        if (mp.type === 'pin' && mp.latitude != null && mp.longitude != null) {
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
        } else if (mp.type === 'station' && mp.callsign) {
            const s = stations[mp.callsign];
            if (s && s.marker) {
                const el = s.marker.getElement?.();
                if (el) el.classList.add('my-station');
            }
        }
    }

    async function saveMyPosition(myPosition) {
        try {
            const resp = await fetch(API_BASE + '/config', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
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
                    map.flyTo([pos.lat, pos.lng], zoom);
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
        map.closePopup();
    };

    window._removeMyPosition = function () {
        saveMyPosition(null);
        map.closePopup();
    };

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
            showPinContextPopup(e.latlng);
        });

        // Long-press on map (mobile)
        let longPressTimer = null;
        map.on('mousedown', (e) => {
            if (!e.originalEvent) return;
            longPressTimer = setTimeout(() => {
                showPinContextPopup(e.latlng);
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
        dropPinAt(e.latlng.lat, e.latlng.lng);
    }

    function dropPinAt(lat, lng) {
        saveMyPosition({ type: 'pin', latitude: lat, longitude: lng });
    }

    function showPinContextPopup(latlng) {
        const lat = latlng.lat.toFixed(6);
        const lng = latlng.lng.toFixed(6);
        L.popup()
            .setLatLng(latlng)
            .setContent(`<b>${lat}, ${lng}</b><br><button class="popup-btn popup-btn-set" onclick="window._dropPinFromPopup(${lat}, ${lng})">Set as My Position</button>`)
            .openOn(map);
    }

    window._dropPinFromPopup = function (lat, lng) {
        map.closePopup();
        dropPinAt(lat, lng);
    };

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
                html += `<span class="packet-segment-plain">${escapeHtml(raw.substring(lastEnd, ann.start))}</span>`;
            }
            html += `<span class="packet-segment packet-segment-${ann.color}" title="${ann.field}">${escapeHtml(raw.substring(ann.start, ann.end))}</span>`;
            lastEnd = ann.end;
        }
        if (lastEnd < raw.length) {
            html += `<span class="packet-segment-plain">${escapeHtml(raw.substring(lastEnd))}</span>`;
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
            html += '<div class="decode-section-header" style="color:#58a6ff;">Station</div>';
            html += '<table class="decode-table">';
            html += `<tr><td class="decode-label">From</td><td class="decode-value"><code>${escapeHtml(sections.station.from)}</code></td></tr>`;
            html += `<tr><td class="decode-label">To</td><td class="decode-value"><code>${escapeHtml(sections.station.to)}</code></td></tr>`;
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
                    html += `<code>${escapeHtml(hop)}</code>`;
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
            html += '<div class="decode-section-header" style="color:#ff7b72;">Position</div>';
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
                html += `<code>${escapeHtml(symTable + symChar)}</code>`;
                if (symName) html += `<span class="decode-symbol-name">${escapeHtml(symName)}</span>`;
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
            html += '<div class="decode-section-header" style="color:#79c0ff;">Weather</div>';
            html += '<div class="decode-weather-grid">';
            const wxFields = [
                ['wind_direction', 'Wind Dir', (v) => v + '\u00B0'],
                ['wind_speed', 'Wind Spd', (v) => v.toFixed(1) + ' m/s'],
                ['wind_gust', 'Gust', (v) => v.toFixed(1) + ' m/s'],
                ['temperature', 'Temp', (v) => v.toFixed(1) + '\u00B0C'],
                ['humidity', 'Humidity', (v) => v + '%'],
                ['pressure', 'Pressure', (v) => v.toFixed(1) + ' hPa'],
                ['rain_1h', 'Rain 1h', (v) => v.toFixed(1) + ' mm'],
                ['rain_24h', 'Rain 24h', (v) => v.toFixed(1) + ' mm'],
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
            html += '<div class="decode-section-header" style="color:#d29922;">Message</div>';
            html += '<table class="decode-table">';
            html += `<tr><td class="decode-label">To</td><td class="decode-value"><code>${escapeHtml(sections.message.addressee)}</code></td></tr>`;
            html += `<tr><td class="decode-label">Message</td><td class="decode-value">${escapeHtml(sections.message.message_text)}</td></tr>`;
            if (sections.message.msgNo) {
                html += `<tr><td class="decode-label">Msg #</td><td class="decode-value">${escapeHtml(sections.message.msgNo)}</td></tr>`;
            }
            html += '</table></div>';
        }

        // Telemetry section
        if (sections.telemetry) {
            html += '<div class="decode-section decode-section-wide">';
            html += '<div class="decode-section-header" style="color:#bc8cff;">Telemetry</div>';
            html += '<table class="decode-table">';
            for (const [key, value] of Object.entries(sections.telemetry)) {
                html += `<tr><td class="decode-label">${escapeHtml(key)}</td><td class="decode-value">${escapeHtml(String(value))}</td></tr>`;
            }
            html += '</table></div>';
        }

        // Comment section
        if (sections.comment) {
            html += '<div class="decode-section decode-section-wide">';
            html += '<div class="decode-section-header" style="color:#8b949e;">Comment</div>';
            html += `<div class="decode-comment">${escapeHtml(sections.comment.text)}</div>`;
            html += '</div>';
        }

        html += '</div>'; // close decode-tables

        // Mini map placeholder
        if (sections.position && sections.position.latitude != null && sections.position.longitude != null) {
            html += '<div class="decode-section decode-section-wide" style="margin-top:12px;">';
            html += '<div class="decode-section-header" style="color:#ff7b72;">Location</div>';
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
                navigator.clipboard.writeText(rawText).then(() => {
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
        stationMarker.bindPopup('<strong>' + escapeHtml(stationCall) + '</strong>');

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
                pathMarker.bindPopup('<strong>' + escapeHtml(clean) + '</strong>');
            }
        }

        // Draw path polyline
        if (linePoints.length > 1) {
            L.polyline(linePoints, {
                color: '#58a6ff',
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
            <div class="decode-error-message">${escapeHtml(message)}</div>
            <div class="decode-error-hint">Check that the packet follows APRS format, e.g.:<br>
            <code>CALLSIGN&gt;DEST,PATH:data</code></div>
        </div>`;
    }

    function escapeHtml(str) {
        if (!str) return '';
        return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
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
                human_info: '',
                msg_no: '',
                raw_log: [],
                raw_packet: `${call}>APRS:!${lat.toFixed(4)}/${lng.toFixed(4)}>${sym}`,
                audio_level: null,
                compact_log: `<span style="color:#1AA730">RX&#x2193;</span> <span style="color:cyan">GPSPacket</span> <span style="color:#C70039">${call}</span><span style="color:#1AA730">&#x2192;</span><span style="color:#D033FF">APRS</span> <span style="color:#888">[SIM ${distKm}km ${bearing}°]</span>`,
            };
            setTimeout(() => onPacket(packet), delay);
            delay += 800; // stagger so animations don't overlap
        }
        console.log(`simulateStations: injecting ${simStations.length} stations over ${delay}ms`);
    }
    window.simulateStations = simulateStations;

})();
