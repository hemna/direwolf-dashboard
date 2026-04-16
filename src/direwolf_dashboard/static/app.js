/**
 * Direwolf Dashboard — Client-side application
 *
 * Handles: WebSocket connection, Leaflet map, packet log, filters, settings
 */

(function () {
    'use strict';

    // --- State ---
    let map = null;
    let ws = null;
    let wsReconnectDelay = 1000;
    let config = {};
    let stations = {};      // callsign -> { marker, track, data }
    let autoScroll = true;
    const MAX_LOG_ROWS = 500;
    let trailHours = 1;  // Current trail duration in hours
    let showRouteDistances = localStorage.getItem('showRouteDistances') !== 'false'; // default true

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

    // --- Symbol Picker ---
    function updateSymbolPreview() {
        const sym = document.getElementById('cfg-symbol').value || '-';
        const tbl = document.getElementById('cfg-symbol-table').value || '/';
        const preview = document.getElementById('symbol-preview');
        const nameEl = document.getElementById('symbol-preview-name');

        const pos = getSymbolSpritePosition(sym);
        const { spriteUrl } = parseSymbolTable(tbl);
        const overlay = (tbl !== '/' && tbl !== '\\') ? tbl : null;

        let html = `<div style="width:24px;height:24px;background-image:url('${spriteUrl}');background-position:${pos.x}px ${pos.y}px;background-repeat:no-repeat;image-rendering:pixelated;"></div>`;
        if (overlay) {
            html += `<div style="position:absolute;top:0;left:0;width:24px;height:24px;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:bold;color:#000;text-shadow:0 0 2px #fff;">${overlay}</div>`;
        }
        preview.innerHTML = html;
        nameEl.textContent = getSymbolName(tbl, sym) + ' (' + tbl + sym + ')';
    }

    function renderSymbolGrid(table) {
        const grid = document.getElementById('symbol-picker-grid');
        const currentSym = document.getElementById('cfg-symbol').value;
        const currentTbl = document.getElementById('cfg-symbol-table').value;
        const spriteUrl = table === '/' ? PRIMARY_SPRITE : SECONDARY_SPRITE;
        const names = table === '/' ? PRIMARY_SYMBOLS : ALTERNATE_SYMBOLS;

        grid.innerHTML = '';

        for (let i = 33; i <= 126; i++) {
            const ch = String.fromCharCode(i);
            const pos = getSymbolSpritePosition(ch);
            const name = names[ch] || 'Symbol';
            const isSelected = (table === currentTbl && ch === currentSym);

            const cell = document.createElement('button');
            cell.type = 'button';
            cell.className = 'symbol-cell' + (isSelected ? ' symbol-cell-selected' : '');
            cell.dataset.table = table;
            cell.dataset.symbol = ch;
            cell.dataset.name = name;
            cell.title = table + ch + ' - ' + name;

            // Use 36px scale: 48px * 0.75
            const x = (pos.x / 24) * 36;
            const y = (pos.y / 24) * 36;
            cell.innerHTML = `<div class="symbol-icon" style="background-image:url('${spriteUrl}');background-position:${x}px ${y}px;"></div>`;

            grid.appendChild(cell);
        }
    }

    function initSymbolPicker() {
        const pickerModal = document.getElementById('symbol-picker-modal');
        const btnPick = document.getElementById('btn-pick-symbol');
        const preview = document.getElementById('symbol-preview');
        const btnClose = document.getElementById('btn-close-symbol-picker');
        const tabPrimary = document.getElementById('symbol-tab-primary');
        const tabAlt = document.getElementById('symbol-tab-alternate');
        const grid = document.getElementById('symbol-picker-grid');
        const info = document.getElementById('symbol-picker-info');
        let activeTable = '/';

        function openPicker() {
            const tbl = document.getElementById('cfg-symbol-table').value || '/';
            activeTable = (tbl === '/' || tbl === '\\') ? tbl : '\\';
            tabPrimary.classList.toggle('active', activeTable === '/');
            tabAlt.classList.toggle('active', activeTable === '\\');
            renderSymbolGrid(activeTable);
            pickerModal.classList.remove('hidden');
        }

        function closePicker() {
            pickerModal.classList.add('hidden');
        }

        btnPick.addEventListener('click', openPicker);
        preview.addEventListener('click', openPicker);
        btnClose.addEventListener('click', closePicker);
        pickerModal.addEventListener('click', (e) => {
            if (e.target === pickerModal) closePicker();
        });

        tabPrimary.addEventListener('click', () => {
            if (activeTable === '/') return;
            activeTable = '/';
            tabPrimary.classList.add('active');
            tabAlt.classList.remove('active');
            renderSymbolGrid('/');
        });

        tabAlt.addEventListener('click', () => {
            if (activeTable === '\\') return;
            activeTable = '\\';
            tabAlt.classList.add('active');
            tabPrimary.classList.remove('active');
            renderSymbolGrid('\\');
        });

        // Click on a symbol cell
        grid.addEventListener('click', (e) => {
            const cell = e.target.closest('.symbol-cell');
            if (!cell) return;
            document.getElementById('cfg-symbol').value = cell.dataset.symbol;
            document.getElementById('cfg-symbol-table').value = cell.dataset.table;
            updateSymbolPreview();
            closePicker();
        });

        // Hover info
        grid.addEventListener('mouseover', (e) => {
            const cell = e.target.closest('.symbol-cell');
            if (cell) {
                info.textContent = cell.dataset.table + cell.dataset.symbol + ' - ' + cell.dataset.name;
            }
        });
        grid.addEventListener('mouseleave', () => {
            info.textContent = 'Hover over a symbol to see details';
        });
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
        await loadConfig();
        initMap();
        initLegend();
        await loadStations();
        loadStationPositions();
        loadTracks();
        connectWebSocket();
        initFilters();
        initSettings();
        initSymbolPicker();
        initMapResize();
        initLogToggle();
        initMobileMenu();
    });

    // --- Config ---
    async function loadConfig() {
        try {
            const resp = await fetch('/api/config');
            config = await resp.json();
            if (config.version) {
                const el = document.getElementById('app-version');
                if (el) el.textContent = config.version;
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

        L.tileLayer('/api/tiles/{z}/{x}/{y}.png', {
            attribution: '&copy; OpenStreetMap contributors',
            maxZoom: 18,
        }).addTo(map);

        // Add station marker if configured — register it in `stations`
        // so that loadStations() / WebSocket updates don't create a duplicate.
        if (lat !== 0 || lon !== 0) {
            const callsign = config.station?.callsign || 'Home';
            const sym = config.station?.symbol || '-';
            const symTable = config.station?.symbol_table || '/';
            const icon = createSymbolIcon(symTable, sym, callsign);
            const marker = L.marker([lat, lon], { icon: icon })
                .addTo(map)
                .bindPopup(`<b>${callsign}</b><br>Home Station`);

            stations[callsign] = {
                marker: marker,
                track: L.polyline([], { color: '#00b4d8', weight: 1.5, opacity: 0.6 }).addTo(map),
                data: {
                    callsign: callsign,
                    latitude: lat,
                    longitude: lon,
                    symbol: sym,
                    symbol_table: symTable,
                },
            };
        }

        setInterval(cleanupAnimations, 10000);
    }

    async function loadStations() {
        try {
            const resp = await fetch('/api/stations');
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
            const resp = await fetch('/api/stations/positions');
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
            const resp = await fetch(`/api/stations/tracks?hours=${trailHours}`);
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
            html += `Last seen: ${ago}m ago`;
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
        const homeCall = config.station?.callsign;
        if (homeCall && callsign.toUpperCase() === homeCall.toUpperCase()) {
            const lat = config.station?.latitude;
            const lng = config.station?.longitude;
            if (lat != null && lng != null) return { lat, lng };
        }
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
        const homeCall = config.station?.callsign;
        if (homeCall) {
            const homePos = getStationPosition(homeCall);
            if (homePos && (waypoints.length === 0 ||
                waypoints[waypoints.length - 1].lat !== homePos.lat ||
                waypoints[waypoints.length - 1].lng !== homePos.lng)) {
                waypoints.push(homePos);
            }
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
                // Midpoint
                const midLat = (from.lat + to.lat) / 2;
                const midLng = (from.lng + to.lng) / 2;
                // Compute screen-space angle from the two endpoints
                const fromPt = map.latLngToContainerPoint(from);
                const toPt = map.latLngToContainerPoint(to);
                let angle = Math.atan2(toPt.y - fromPt.y, toPt.x - fromPt.x) * 180 / Math.PI;
                // Keep text readable (not upside-down)
                if (angle > 90) angle -= 180;
                if (angle < -90) angle += 180;
                const icon = L.divIcon({
                    className: 'route-distance-label',
                    html: `<span style="transform:rotate(${angle.toFixed(1)}deg);transform-origin:center center">${label}</span>`,
                    iconSize: [120, 16],
                    iconAnchor: [60, 8],
                });
                const marker = L.marker([midLat, midLng], { icon, interactive: false });
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
        const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        const url = `${proto}//${location.host}/ws`;

        ws = new WebSocket(url);

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
            case 'ping':
                // Respond with pong (keep-alive)
                break;
        }
    }

    function onPacket(packet) {
        addLogRow(packet);
        if (packet.latitude != null && packet.longitude != null) {
            updatePositionCache(packet.from_call, packet.latitude, packet.longitude);
        }
        if (packet.latitude && packet.longitude) {
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
        const homeLat = config.station?.latitude;
        const homeLon = config.station?.longitude;
        const homeCall = config.station?.callsign;
        if (packet.tx) {
            if (homeLat != null && homeLon != null) {
                playTransmitAnimation(homeCall || 'Home', homeLat, homeLon, []);
            }
        } else {
            if (packet.latitude && packet.longitude) {
                playTransmitAnimation(packet.from_call, packet.latitude, packet.longitude, packet.path || []);
            }
            if (homeLat != null && homeLon != null && homeCall) {
                setTimeout(() => {
                    playReceiveAnimation(homeCall, homeLat, homeLon);
                }, 300);
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
        if (packet.raw_log && packet.raw_log.length > 0) {
            rawDiv.textContent = packet.raw_log.join('\n');
        } else if (packet.raw_packet) {
            rawDiv.textContent = packet.raw_packet;
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

        // Import from Direwolf conf
        document.getElementById('btn-import-dw-conf').addEventListener('click', importDirewolfConf);

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
        document.getElementById('cfg-callsign').value = config.station?.callsign || '';
        document.getElementById('cfg-latitude').value = config.station?.latitude || 0;
        document.getElementById('cfg-longitude').value = config.station?.longitude || 0;
        document.getElementById('cfg-zoom').value = config.station?.zoom || 12;
        document.getElementById('cfg-symbol').value = config.station?.symbol || '-';
        document.getElementById('cfg-symbol-table').value = config.station?.symbol_table || '/';
        document.getElementById('cfg-agw-host').value = config.direwolf?.agw_host || 'localhost';
        document.getElementById('cfg-agw-port').value = config.direwolf?.agw_port || 8000;
        document.getElementById('cfg-log-file').value = config.direwolf?.log_file || '';
        document.getElementById('cfg-conf-file').value = config.direwolf?.conf_file || '';
        document.getElementById('cfg-server-port').value = config.server?.port || 8080;
        document.getElementById('cfg-retention').value = config.storage?.retention_days || 7;
        document.getElementById('cfg-tile-mode').value = config.tiles?.cache_mode || 'lazy';
        document.getElementById('cfg-max-cache').value = config.tiles?.max_cache_mb || 500;

        // Map display settings (localStorage)
        document.getElementById('cfg-show-route-distances').checked = showRouteDistances;

        // Show/hide preload section
        if (config.tiles?.cache_mode === 'preload') {
            document.getElementById('preload-section').classList.remove('hidden');
        }

        updateSymbolPreview();
    }

    async function saveSettings() {
        const updates = {
            station: {
                callsign: document.getElementById('cfg-callsign').value,
                latitude: parseFloat(document.getElementById('cfg-latitude').value),
                longitude: parseFloat(document.getElementById('cfg-longitude').value),
                zoom: parseInt(document.getElementById('cfg-zoom').value) || 12,
                symbol: document.getElementById('cfg-symbol').value || '-',
                symbol_table: document.getElementById('cfg-symbol-table').value || '/',
            },
            direwolf: {
                agw_host: document.getElementById('cfg-agw-host').value,
                agw_port: parseInt(document.getElementById('cfg-agw-port').value),
                log_file: document.getElementById('cfg-log-file').value,
                conf_file: document.getElementById('cfg-conf-file').value,
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
        };

        // Save client-side map display settings to localStorage
        showRouteDistances = document.getElementById('cfg-show-route-distances').checked;
        localStorage.setItem('showRouteDistances', showRouteDistances);

        const feedback = document.getElementById('settings-feedback');
        try {
            const resp = await fetch('/api/config', {
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

    async function importDirewolfConf() {
        const confPath = document.getElementById('cfg-conf-file').value.trim();
        const feedback = document.getElementById('import-feedback');

        if (!confPath) {
            feedback.className = 'error';
            feedback.textContent = 'Enter a config file path first';
            feedback.classList.remove('hidden');
            return;
        }

        feedback.className = '';
        feedback.textContent = 'Importing...';
        feedback.classList.remove('hidden');

        try {
            const resp = await fetch('/api/import-direwolf-conf', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ conf_path: confPath }),
            });
            const data = await resp.json();

            if (!resp.ok) {
                feedback.className = 'error';
                feedback.textContent = data.detail || 'Import failed';
                return;
            }

            // Populate the settings form with extracted values
            if (data.callsign) document.getElementById('cfg-callsign').value = data.callsign;
            if (data.latitude) document.getElementById('cfg-latitude').value = data.latitude;
            if (data.longitude) document.getElementById('cfg-longitude').value = data.longitude;
            if (data.symbol) document.getElementById('cfg-symbol').value = data.symbol;
            if (data.symbol_table) document.getElementById('cfg-symbol-table').value = data.symbol_table;

            const fields = Object.keys(data).filter(k => data[k]).join(', ');
            feedback.className = 'success';
            feedback.textContent = `Imported: ${fields}. Click Save to apply.`;
            updateSymbolPreview();
        } catch (e) {
            feedback.className = 'error';
            feedback.textContent = `Error: ${e.message}`;
        }
    }

    // --- Tile Preload ---
    async function estimatePreload() {
        const bbox = getPreloadBbox();
        if (!bbox) return;

        try {
            const resp = await fetch('/api/tiles/preload', {
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
            await fetch('/api/tiles/preload', {
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
            await fetch('/api/tiles/preload', { method: 'DELETE' });
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

})();
