/**
 * Toolbar glue for issue #218/#219 - the top-level toolbar (nav/status band +
 * slim tool row), the Map drawer, and the overflow menu. One surface open at a
 * time; the drawer holds the five former map cards and the overflow menu holds
 * the former TOOLS items.
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


    // --- Map drawer + overflow menu (#219) -----------------------------------
    // One slim toolbar: the Map drawer holds the five former floating cards,
    // the overflow menu holds the former TOOLS items. Only one surface is open
    // at a time. app.js resizes the map when the drawer opens/closes so tiles
    // repaint for the new map width.
    var drawer = $('map-drawer');
    var scrim = $('drawer-scrim');
    var drawerBtn = $('btn-map-drawer');
    var overflow = $('overflow-menu');
    var overflowBtn = $('btn-overflow');

    function setDrawer(open) {
        if (!drawer) return;
        if (open) setOverflow(false);
        drawer.classList.toggle('hidden', !open);
        drawer.setAttribute('aria-hidden', open ? 'false' : 'true');
        if (scrim) scrim.classList.toggle('hidden', !open);
        var mc = $('map-container');
        if (mc) mc.classList.toggle('drawer-open', open);
        if (drawerBtn) {
            drawerBtn.classList.toggle('on', open);
            drawerBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
        }
        if (window.__dwDrawerChanged) window.__dwDrawerChanged();
    }
    function setOverflow(open) {
        if (!overflow) return;
        if (open) setDrawer(false);
        overflow.classList.toggle('hidden', !open);
        if (overflowBtn) {
            overflowBtn.classList.toggle('on', open);
            overflowBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
        }
    }
    window.__dwSetDrawer = setDrawer;

    if (drawerBtn) {
        drawerBtn.addEventListener('click', function () {
            setDrawer(drawer && drawer.classList.contains('hidden'));
        });
    }
    var drawerClose = $('btn-map-drawer-close');
    if (drawerClose) drawerClose.addEventListener('click', function () { setDrawer(false); });
    if (scrim) scrim.addEventListener('click', function () { setDrawer(false); });

    if (overflowBtn) {
        overflowBtn.addEventListener('click', function (e) {
            e.stopPropagation();
            setOverflow(overflow && overflow.classList.contains('hidden'));
        });
    }
    // Each overflow item forwards its click to the matching hidden action
    // button, then closes the menu.
    var items = Array.prototype.slice.call(document.querySelectorAll('#overflow-menu [data-action]'));
    items.forEach(function (row) {
        row.addEventListener('click', function () {
            var id = row.getAttribute('data-action');
            if (id && $(id)) $(id).click();
            setOverflow(false);
        });
    });
    // Click outside the overflow menu closes it.
    document.addEventListener('click', function (e) {
        if (!overflow || overflow.classList.contains('hidden')) return;
        if (e.target === overflowBtn || (overflowBtn && overflowBtn.contains(e.target))) return;
        if (!overflow.contains(e.target)) setOverflow(false);
    });
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') { setOverflow(false); }
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
