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

    // --- Init ---
    document.addEventListener('DOMContentLoaded', async () => {
        await loadConfig();
        initMap();
        await loadStations();
        connectWebSocket();
        initFilters();
        initSettings();
        initSymbolPicker();
        initMapResize();
    });

    // --- Config ---
    async function loadConfig() {
        try {
            const resp = await fetch('/api/config');
            config = await resp.json();
        } catch (e) {
            console.error('Failed to load config:', e);
        }
    }

    // --- Map ---
    function initMap() {
        const lat = config.station?.latitude || 0;
        const lon = config.station?.longitude || 0;
        const zoom = 10;

        map = L.map('map').setView([lat, lon], zoom);

        L.tileLayer('/api/tiles/{z}/{x}/{y}.png', {
            attribution: '&copy; OpenStreetMap contributors',
            maxZoom: 18,
        }).addTo(map);

        // Add station marker if configured
        if (lat !== 0 || lon !== 0) {
            const callsign = config.station?.callsign || 'Home';
            const sym = config.station?.symbol || '-';
            const symTable = config.station?.symbol_table || '/';
            const icon = createSymbolIcon(symTable, sym, callsign);
            L.marker([lat, lon], { icon: icon })
                .addTo(map)
                .bindPopup(`<b>${callsign}</b><br>Home Station`);
        }
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
        // Keep last 50 points
        if (latlngs.length > 50) latlngs.shift();
        s.track.setLatLngs(latlngs);
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
        // Add to log
        addLogRow(packet);

        // Update map
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
            // Only update track if this is a real position report (not DB lookup)
            if (!packet.position_from_db) {
                updateStationTrack(packet.from_call, packet.latitude, packet.longitude);
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

        callsignInput.addEventListener('input', applyFilters);
        typeSelect.addEventListener('change', applyFilters);
        txrxSelect.addEventListener('change', applyFilters);

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

    // --- Map Resize ---
    function initMapResize() {
        const handle = document.getElementById('map-resize-handle');
        const mapContainer = document.getElementById('map-container');
        let startY, startHeight;

        handle.addEventListener('mousedown', (e) => {
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

})();
