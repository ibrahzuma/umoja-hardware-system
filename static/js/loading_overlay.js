/* Global loading overlay.
 *
 * Wraps window.fetch once so EVERY AJAX call in the app (raw fetch in the
 * templates and the ApiService class alike) shows a centered spinner pop-up
 * while a request is in flight — no per-page wiring needed.
 *
 * Behaviour tuned to avoid flicker:
 *   • SHOW_DELAY  — fast requests (<200ms) never flash the overlay.
 *   • MIN_VISIBLE — once shown it stays up briefly so it never blinks.
 *   • A counter tracks concurrent requests; the overlay hides only when the
 *     last one settles.
 *
 * Opt out of the overlay for a specific/background request with:
 *     fetch(url, { quiet: true })        // extra init key, ignored by fetch
 *   or by sending an  X-Quiet: 1  header.
 */
(function () {
    const origFetch = window.fetch;
    if (typeof origFetch !== 'function') return;

    const SHOW_DELAY = 200;    // ms before the overlay appears
    const MIN_VISIBLE = 350;   // ms it stays up once shown

    let active = 0;
    let showTimer = null;
    let shownAt = 0;
    let el = null;

    function ensureEl() {
        if (el) return el;
        el = document.createElement('div');
        el.className = 'global-loader';
        el.setAttribute('role', 'status');
        el.setAttribute('aria-live', 'polite');
        el.setAttribute('aria-hidden', 'true');
        el.innerHTML =
            '<div class="global-loader__box">' +
            '<div class="global-loader__spinner" aria-hidden="true"></div>' +
            '<div class="global-loader__text">Loading…</div>' +
            '</div>';
        (document.body || document.documentElement).appendChild(el);
        return el;
    }

    function show() {
        const node = ensureEl();
        node.classList.add('show');
        node.setAttribute('aria-hidden', 'false');
        shownAt = Date.now();
    }

    function hide() {
        if (!el) return;
        el.classList.remove('show');
        el.setAttribute('aria-hidden', 'true');
    }

    function begin() {
        active += 1;
        if (active === 1 && showTimer === null) {
            showTimer = setTimeout(function () { showTimer = null; show(); }, SHOW_DELAY);
        }
    }

    function end() {
        active = Math.max(0, active - 1);
        if (active !== 0) return;
        if (showTimer !== null) {           // settled before the overlay even appeared
            clearTimeout(showTimer);
            showTimer = null;
            hide();
            return;
        }
        const remaining = MIN_VISIBLE - (Date.now() - shownAt);
        if (remaining > 0) setTimeout(hide, remaining);
        else hide();
    }

    function isQuiet(init) {
        if (!init) return false;
        if (init.quiet === true) return true;
        const h = init.headers;
        if (!h) return false;
        if (typeof h.get === 'function') return !!h.get('X-Quiet');
        return !!(h['X-Quiet'] || h['x-quiet']);
    }

    window.fetch = function (input, init) {
        if (isQuiet(init)) return origFetch.apply(this, arguments);
        begin();
        let promise;
        try {
            promise = origFetch.apply(this, arguments);
        } catch (err) {
            end();
            throw err;
        }
        return promise.then(
            function (res) { end(); return res; },
            function (err) { end(); throw err; }
        );
    };

    /* Full-page form submissions reload the page and would otherwise show no
     * feedback during the server round-trip. Show the overlay on submit; the
     * navigation itself tears the page down, so there's no matching end() — the
     * fresh page loads without the overlay. Opt out with data-quiet on the form.
     */
    document.addEventListener('submit', function (ev) {
        const form = ev.target;
        if (!form || form.tagName !== 'FORM') return;
        if (form.hasAttribute('data-quiet')) return;
        if (form.getAttribute('target') === '_blank') return;
        // Skip forms handled in-page by JS (those use fetch and are covered above).
        if (ev.defaultPrevented) return;
        // Defer so a late preventDefault() from another listener can still cancel us.
        setTimeout(function () {
            if (!ev.defaultPrevented) { show(); }
        }, 0);
    }, true);
})();
