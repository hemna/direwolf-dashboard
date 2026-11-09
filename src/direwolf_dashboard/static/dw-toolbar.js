/**
 * Toolbar glue for issue #218 - the top-level toolbar (nav/status band + tool
 * row) and the collapsible left tool panel, matching haminfo's layout.
 */
(function () {
    'use strict';

    function $(id) { return document.getElementById(id); }

    // --- UTC clock + local time + LIVE badge --------------------------------
    function pad(n) { return (n < 10 ? '0' : '') + n; }
    function tickClock() {
        var now = new Date();
        var utc = $('utc-clock');
        if (utc) utc.textContent = now.toUTCString().slice(17, 25);
        var local = $('dw-local');
        if (local) {
            local.textContent = pad(now.getHours()) + ':' + pad(now.getMinutes()) + ':' + pad(now.getSeconds());
        }
    }
    setInterval(tickClock, 1000);
    tickClock();

    var statusEl = $('status-indicator');
    var liveBadge = $('live-badge');
    function syncLive() {
        if (statusEl && liveBadge) {
            liveBadge.classList.toggle('on', statusEl.classList.contains('connected'));
        }
    }
    if (statusEl && window.MutationObserver) {
        new MutationObserver(syncLive).observe(statusEl, { attributes: true, attributeFilter: ['class'] });
    }
    setInterval(syncLive, 2000);
    syncLive();

    // --- Nav pills -----------------------------------------------------------
    var navButtons = Array.prototype.slice.call(document.querySelectorAll('.nav-item'));
    var PANEL_ACTIONS = {
        stations: 'btn-station-list',
        weather: 'btn-weather-panel',
        messages: 'btn-messages-panel',
        settings: 'btn-settings',
    };
    navButtons.forEach(function (btn) {
        btn.addEventListener('click', function () {
            navButtons.forEach(function (b) { b.classList.remove('active'); });
            btn.classList.add('active');
            var target = PANEL_ACTIONS[btn.getAttribute('data-panel')];
            if (target && $(target)) $(target).click();
        });
    });

    // --- Tool panel (collapsible left) ---------------------------------------
    var panel = $('toolpanel');
    function setPanel(open) {
        if (!panel) return;
        panel.classList.toggle('hidden', !open);
        var t = $('btn-tools');
        if (t) t.classList.toggle('on', open);
    }
    var btnTools = $('btn-tools');
    if (btnTools) {
        btnTools.addEventListener('click', function () {
            setPanel(panel && panel.classList.contains('hidden'));
        });
    }
    var closeBtn = $('btn-toolpanel-close');
    if (closeBtn) closeBtn.addEventListener('click', function () { setPanel(false); });
    var rows = Array.prototype.slice.call(document.querySelectorAll('#toolpanel [data-action]'));
    rows.forEach(function (row) {
        row.addEventListener('click', function () {
            var id = row.getAttribute('data-action');
            if (id && $(id)) $(id).click();
        });
    });

    // --- Find ----------------------------------------------------------------
    var findInput = $('dw-find');
    function doFind() {
        if (!findInput || !window.__dwToolbar) return;
        var cs = (findInput.value || '').trim().toUpperCase();
        if (!cs) return;
        var st = window.__dwToolbar.getStation(cs);
        if (st && st.data && typeof st.data.latitude === 'number' && typeof st.data.longitude === 'number') {
            window.__dwToolbar.flyTo(st.data.latitude, st.data.longitude, 12);
        } else {
            var listBtn = $('btn-station-list');
            if (listBtn) listBtn.click();
        }
    }
    if (findInput) {
        findInput.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') doFind();
        });
    }

    // --- Station count --------------------------------------------------------
    function updateCount() {
        var el = $('dw-station-count');
        if (!el) return;
        var n = window.__dwToolbar && window.__dwToolbar.stationCount ? window.__dwToolbar.stationCount() : 0;
        el.textContent = n + ' station' + (n === 1 ? '' : 's');
    }
    setInterval(updateCount, 3000);
    updateCount();
})();
