/* Top-bar global search — talks to /api/search/.
 *
 * Shows a dropdown of hits grouped by type. Keyboard: Down/Up walks the hits,
 * Enter opens the highlighted one (or the first), Escape closes. Clicking
 * anywhere outside closes.
 */
(function () {
    const input = document.getElementById('globalSearchInput');
    const dropdown = document.getElementById('globalSearchDropdown');
    const wrap = document.getElementById('globalSearchWrap');
    if (!input || !dropdown || !wrap) return;

    const TYPE_ICON = {
        product: 'bi-box-seam',
        sale: 'bi-receipt',
        customer: 'bi-person',
        supplier: 'bi-truck',
        purchase_order: 'bi-file-earmark-text',
    };
    const TYPE_LABEL = {
        product: 'Products',
        sale: 'Sales',
        customer: 'Customers',
        supplier: 'Suppliers',
        purchase_order: 'Purchase Orders',
    };

    let timer = null;
    let lastQuery = '';
    let hits = [];
    let active = -1;

    function hide() {
        dropdown.hidden = true;
        active = -1;
    }
    function show() { dropdown.hidden = false; }

    function render(results, q) {
        hits = results;
        if (!results.length) {
            dropdown.innerHTML = `<div class="search-empty">No results for <strong>${escape(q)}</strong></div>`;
            show();
            return;
        }
        // Group preserving order
        const groups = {};
        const order = [];
        results.forEach((r, i) => {
            if (!groups[r.type]) { groups[r.type] = []; order.push(r.type); }
            r._index = i;
            groups[r.type].push(r);
        });

        const html = order.map(type => {
            const head = `<div class="search-group-head">${TYPE_LABEL[type] || type}</div>`;
            const rows = groups[type].map(r => `
                <a class="search-hit" href="${r.url}" data-index="${r._index}">
                    <span class="hit-icon"><i class="bi ${TYPE_ICON[r.type] || 'bi-dot'}"></i></span>
                    <span class="hit-body">
                        <div class="hit-label">${escape(r.label || '')}</div>
                        <div class="hit-detail">${escape(r.detail || '')}</div>
                    </span>
                </a>`).join('');
            return head + rows;
        }).join('');
        dropdown.innerHTML = html;
        show();
        highlight(0);
    }

    function escape(s) {
        return String(s).replace(/[&<>"']/g, c => (
            { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
        ));
    }

    function highlight(i) {
        const nodes = dropdown.querySelectorAll('.search-hit');
        if (!nodes.length) return;
        if (i < 0) i = nodes.length - 1;
        if (i >= nodes.length) i = 0;
        nodes.forEach(n => n.classList.remove('is-active'));
        nodes[i].classList.add('is-active');
        nodes[i].scrollIntoView({ block: 'nearest' });
        active = i;
    }

    async function search(q) {
        try {
            const r = await fetch(`/api/search/?q=${encodeURIComponent(q)}`, {
                headers: { 'Accept': 'application/json' },
                credentials: 'same-origin',
            });
            if (!r.ok) return;
            const data = await r.json();
            // Ignore stale answers.
            if (data.q !== q && data.q !== lastQuery) return;
            render(data.results || [], q);
        } catch (e) { /* network — leave the dropdown as is */ }
    }

    input.addEventListener('input', () => {
        const q = input.value.trim();
        lastQuery = q;
        clearTimeout(timer);
        if (q.length < 2) { hide(); return; }
        timer = setTimeout(() => search(q), 200);
    });

    input.addEventListener('keydown', (e) => {
        if (dropdown.hidden) return;
        if (e.key === 'ArrowDown') { e.preventDefault(); highlight(active + 1); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); highlight(active - 1); }
        else if (e.key === 'Enter') {
            const nodes = dropdown.querySelectorAll('.search-hit');
            const node = nodes[active] || nodes[0];
            if (node) { e.preventDefault(); window.location.href = node.href; }
        } else if (e.key === 'Escape') { hide(); }
    });

    input.addEventListener('focus', () => {
        if (input.value.trim().length >= 2 && dropdown.innerHTML) show();
    });

    document.addEventListener('click', (e) => {
        if (!wrap.contains(e.target)) hide();
    });
})();
