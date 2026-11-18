/* Operations dashboard: one call to /api/dashboard/ fills every widget.
 * Needs Chart.js, ApiService and SocketService on the page.
 */
(function () {
    const api = new ApiService();
    const root = document.getElementById('dash');
    if (!root) return;
    const MONEY = root.dataset.money === '1';
    const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
    const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    let profitChart = null;
    let last = null;

    // ---- formatting ------------------------------------------------------
    const num = v => Number(v || 0);
    const whole = v => Math.round(num(v)).toLocaleString();
    function short(v) {                       // TZS 1.2M, TZS 446.7K
        const n = num(v), a = Math.abs(n);
        if (a >= 1e9) return (n / 1e9).toFixed(1).replace(/\.0$/, '') + 'B';
        if (a >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
        if (a >= 1e4) return (n / 1e3).toFixed(1).replace(/\.0$/, '') + 'K';
        return whole(n);
    }
    // Dates arrive as YYYY-MM-DD and are read as written, never through a
    // Date — so a timezone cannot move a sale to the day before.
    function day(iso) {
        const [y, m, d] = String(iso).split('-').map(Number);
        return d + ' ' + MONTHS[m - 1] + (y !== new Date().getFullYear() ? ' ' + y : '');
    }
    function escapeHtml(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, c =>
            ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    }

    function pill(el, now, before) {
        if (!el) return;
        el.className = 'dash-pill';
        if (!num(before)) {
            el.textContent = '';
            return;
        }
        const pct = (num(now) - num(before)) / Math.abs(num(before)) * 100;
        const flat = Math.abs(pct) < 0.05;
        el.classList.add(flat ? 'is-flat' : (pct > 0 ? 'is-up' : 'is-down'));
        el.innerHTML = (flat ? '' : `<i class="bi bi-caret-${pct > 0 ? 'up' : 'down'}-fill"></i>`)
            + (Math.abs(pct) >= 1000 ? '999%+' : Math.abs(pct).toFixed(1) + '%');
    }

    // ---- period ---------------------------------------------------------
    function query() {
        const v = document.getElementById('dashPeriod').value;
        const branch = document.getElementById('dashBranch');
        const shop = branch && branch.value ? '&branch=' + branch.value : '';
        return (v === 'month' || v === 'last_month' ? 'range=' + v : 'days=' + v) + shop;
    }

    async function load() {
        try {
            const d = await api.get('/api/dashboard/?' + query());
            last = d;
            render(d);
        } catch (e) {
            console.error('Dashboard failed to load', e);
        }
    }

    // ---- widgets --------------------------------------------------------
    function render(d) {
        document.getElementById('dashDates').textContent = day(d.period.from) + ' – ' + day(d.period.to);

        const k = d.kpis;
        const label = { revenue: v => 'TZS ' + short(v), average: v => 'TZS ' + short(v),
                        orders: whole, customers: whole };
        Object.keys(k).forEach(key => {
            const v = root.querySelector(`[data-kpi="${key}"]`);
            if (v) v.textContent = label[key](k[key].now);
            pill(root.querySelector(`[data-pill="${key}"]`), k[key].now, k[key].before);
            const foot = root.querySelector(`[data-foot="${key}"]`);
            if (foot) foot.textContent = 'vs. ' + label[key](k[key].before) + ' last period';
        });

        Object.entries(d.attention).forEach(([key, value]) => {
            root.querySelectorAll(`[data-attn="${key}"], [data-attn-big="${key}"]`)
                .forEach(el => { el.textContent = whole(value); });
        });

        if (MONEY && d.profit) renderProfit(d.profit);
        renderSplit(d.settlement);
        renderDays(d.weekdays);
        renderRepeat(d.repeat);
        renderBest(d.best_sellers);
    }

    function renderProfit(p) {
        document.querySelector('[data-profit="now"]').textContent = 'TZS ' + short(p.now);
        pill(document.querySelector('[data-pill="profit"]'), p.now, p.before);

        const canvas = document.getElementById('dashProfitChart');
        if (!canvas || typeof Chart === 'undefined') return;
        const ctx = canvas.getContext('2d');
        const fill = ctx.createLinearGradient(0, 0, 0, 210);
        fill.addColorStop(0, 'rgba(47, 107, 255, 0.18)');
        fill.addColorStop(1, 'rgba(47, 107, 255, 0)');
        const labels = p.series.map(r => day(r.date));
        const data = {
            labels,
            datasets: [
                { label: 'This period', data: p.series.map(r => num(r.amount)), borderColor: '#2f6bff',
                  backgroundColor: fill, fill: true, tension: 0.35, borderWidth: 2.2,
                  pointRadius: 0, pointHoverRadius: 5, pointBackgroundColor: '#2f6bff' },
                { label: 'Last period', data: p.previous_series.map(r => num(r.amount)), borderColor: '#b6bfcd',
                  borderDash: [5, 5], fill: false, tension: 0.35, borderWidth: 1.6,
                  pointRadius: 0, pointHoverRadius: 4, pointBackgroundColor: '#b6bfcd' },
            ],
        };
        if (profitChart) profitChart.destroy();
        profitChart = new Chart(ctx, {
            type: 'line',
            data,
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: '#fff', titleColor: '#0f172a', bodyColor: '#334155',
                        borderColor: '#e2e8f0', borderWidth: 1, padding: 12, usePointStyle: true,
                        callbacks: {
                            title: items => items.length ? labels[items[0].dataIndex] : '',
                            label: item => {
                                const prev = p.previous_series[item.dataIndex];
                                return item.datasetIndex === 0
                                    ? ' TZS ' + whole(item.raw) + ' this period'
                                    : ' TZS ' + whole(item.raw) + ' last period' + (prev ? ' (' + day(prev.date) + ')' : '');
                            },
                        },
                    },
                },
                scales: {
                    y: { beginAtZero: true, border: { display: false },
                         grid: { color: '#eef1f6', borderDash: [4, 4] },
                         ticks: { color: '#94a3b8', maxTicksLimit: 5, callback: v => short(v) } },
                    x: { grid: { display: false }, border: { display: false },
                         ticks: { color: '#94a3b8', maxTicksLimit: 6, maxRotation: 0 } },
                },
            },
        });
    }

    function renderSplit(s) {
        const parts = [
            ['paid', 'Paid in full', 'bi-check-circle-fill'],
            ['part', 'Part paid', 'bi-pie-chart-fill'],
            ['credit', 'On credit', 'bi-hourglass-split'],
        ];
        const total = parts.reduce((a, [key]) => a + num(s[key].count), 0);
        document.getElementById('dashSplit').innerHTML = parts.map(([key, name, icon]) => {
            const n = num(s[key].count);
            const grow = total ? Math.max(n / total, 0.12) : 1;
            const amount = MONEY && s[key].amount != null ? ' · TZS ' + short(s[key].amount) : '';
            return `<div class="dash-split-part is-${key}" style="flex:${grow} 1 0">
                        <b><i class="bi ${icon}"></i>${whole(n)}</b>
                        <small>${name}${amount}</small>
                    </div>`;
        }).join('');
    }

    function renderDays(counts) {
        const max = Math.max(...counts, 0);
        const top = max > 0 ? counts.indexOf(max) : -1;
        document.getElementById('dashDays').innerHTML = counts.map((n, i) => `
            <div class="dash-day ${i === top ? 'is-top' : ''}" title="${n} order${n === 1 ? '' : 's'} on ${DAYS[i]}s">
                <span class="dash-day-count">${whole(n)}</span>
                <div class="dash-day-bar" style="height:${max ? Math.max(n / max * 78, 9) : 9}%"></div>
                <span class="dash-day-name">${DAYS[i]}</span>
            </div>`).join('');
        document.getElementById('dashDayNote').textContent = top >= 0 ? 'orders by day' : 'no orders yet';
    }

    // A half-ring of ticks, lit up to the rate — like the design's gauge.
    function renderRepeat(r) {
        const svg = document.getElementById('dashGauge');
        const ticks = 44, cx = 120, cy = 124, inner = 82, outer = 108;
        const rate = r.rate == null ? 0 : r.rate;
        const lit = Math.round(rate / 100 * ticks);
        let out = '';
        for (let i = 0; i < ticks; i++) {
            const a = Math.PI - (i / (ticks - 1)) * Math.PI;
            const cos = Math.cos(a), sin = Math.sin(a);
            const t = i / Math.max(lit - 1, 1);
            // Light to deep green along the lit part, grey beyond it.
            const colour = i < lit ? `rgb(${Math.round(134 - 112 * t)}, ${Math.round(239 - 76 * t)}, ${Math.round(172 - 98 * t)})` : '#e5e9f0';
            out += `<line x1="${(cx + inner * cos).toFixed(1)}" y1="${(cy - inner * sin).toFixed(1)}"
                          x2="${(cx + outer * cos).toFixed(1)}" y2="${(cy - outer * sin).toFixed(1)}"
                          stroke="${colour}" stroke-width="4" stroke-linecap="round"/>`;
        }
        svg.innerHTML = out;
        document.getElementById('dashRepeat').textContent = r.rate == null ? '—' : Math.round(rate) + '%';
        document.getElementById('dashRepeatNote').textContent = r.buyers
            ? `${r.returning} of ${r.buyers} came back`
            : 'No named customers';
    }

    function renderBest(rows) {
        const body = document.getElementById('dashBest');
        const cols = MONEY ? 5 : 4;
        if (!rows.length) {
            body.innerHTML = `<tr><td colspan="${cols}" class="text-center text-muted py-4">No sales in this period</td></tr>`;
            return;
        }
        body.innerHTML = rows.map(r => `
            <tr>
                <td class="text-muted font-monospace small">${escapeHtml(r.sku || '#' + r.id)}</td>
                <td><div class="dash-prod">
                    <span class="dash-prod-icon"><i class="bi ${r.stock == null ? 'bi-tools' : 'bi-box-seam'}"></i></span>
                    <span class="dash-prod-name" title="${escapeHtml(r.name)}">${escapeHtml(r.name)}</span>
                </div></td>
                <td class="text-end text-nowrap">${whole(r.sold)} ${escapeHtml(r.unit)}</td>
                ${MONEY ? `<td class="text-end"><span class="dash-money"><i class="bi bi-arrow-up-circle"></i> TZS ${whole(r.revenue)}</span></td>` : ''}
                <td class="text-end ${r.stock != null && r.stock <= 0 ? 'text-danger fw-semibold' : ''}">${r.stock == null ? '—' : whole(r.stock)}</td>
            </tr>`).join('');
    }

    // ---- export ---------------------------------------------------------
    function exportCsv() {
        if (!last) return;
        const d = last, rows = [];
        const q = v => '"' + String(v == null ? '' : v).replace(/"/g, '""') + '"';
        rows.push(['Dashboard', d.period.from + ' to ' + d.period.to, d.branch ? d.branch.name : 'All shops']);
        rows.push([]);
        rows.push(['Figure', 'This period', 'Last period']);
        Object.entries(d.kpis).forEach(([k, v]) => rows.push([k, v.now, v.before]));
        if (d.profit) rows.push(['gross profit', d.profit.now, d.profit.before]);
        rows.push(['repeat customer rate %', d.repeat.rate == null ? '' : d.repeat.rate, '']);
        rows.push([]);
        rows.push(['Settlement', 'Orders'].concat(d.can_see_money ? ['Amount'] : []));
        Object.entries(d.settlement).forEach(([k, v]) => rows.push([k, v.count].concat(d.can_see_money ? [v.amount] : [])));
        rows.push([]);
        rows.push(['Orders by day'].concat(DAYS));
        rows.push([''].concat(d.weekdays));
        rows.push([]);
        rows.push(['SKU', 'Product', 'Sold', 'Unit'].concat(d.can_see_money ? ['Revenue'] : []).concat(['In stock']));
        d.best_sellers.forEach(r => rows.push([r.sku, r.name, r.sold, r.unit]
            .concat(d.can_see_money ? [r.revenue] : []).concat([r.stock == null ? '' : r.stock])));
        const csv = '﻿' + rows.map(r => r.map(q).join(',')).join('\r\n');
        const a = document.createElement('a');
        a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
        a.download = `dashboard_${d.period.from}_${d.period.to}.csv`;
        document.body.appendChild(a);
        a.click();
        setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
    }

    // ---- live activity --------------------------------------------------
    function addActivityItem(text, type, icon = 'bi-info-circle', time = 'Just now', prepend = true) {
        const list = document.getElementById('stock-updates-list');
        const placeholder = document.getElementById('activity-placeholder');
        if (placeholder) placeholder.remove();
        const item = document.createElement('div');
        item.className = 'update-item';
        if (type === 'danger') item.classList.add('is-danger');
        else if (type === 'sale' || type === 'success') item.classList.add('is-success');
        const colour = type === 'danger' ? 'text-danger' : (type === 'sale' || type === 'success' ? 'text-success' : 'text-muted');
        item.innerHTML = `
            <div class="d-flex align-items-center justify-content-between gap-2">
                <div class="d-flex align-items-center gap-2 text-truncate">
                    <i class="bi ${escapeHtml(icon)} ${colour}"></i>
                    <span class="fw-semibold text-truncate">${escapeHtml(text)}</span>
                </div>
                <span class="text-muted smaller" style="white-space: nowrap;">${escapeHtml(time)}</span>
            </div>`;
        if (prepend) list.prepend(item); else list.appendChild(item);
        if (list.children.length > 15) list.lastChild.remove();
    }

    async function loadActivities() {
        try {
            const data = await api.get('/api/activities/');
            const items = Array.isArray(data) ? data : data.results;
            items.forEach(a => addActivityItem(a.description, a.activity_type === 'low_stock' ? 'danger' : 'info',
                                               a.icon_class, a.time_ago, false));
        } catch (e) { console.error(e); }
    }

    let reloadTimer = null;
    function reloadSoon() {                     // a burst of events, one reload
        clearTimeout(reloadTimer);
        reloadTimer = setTimeout(load, 1500);
    }

    document.addEventListener('DOMContentLoaded', async () => {
        document.getElementById('dashPeriod').addEventListener('change', load);
        const branch = document.getElementById('dashBranch');
        if (branch) {
            try {
                const data = await api.get('/api/branches/');
                (Array.isArray(data) ? data : data.results || []).forEach(b => {
                    const opt = document.createElement('option');
                    opt.value = b.id;
                    opt.textContent = b.name;
                    branch.appendChild(opt);
                });
                // One shop: nothing to choose between.
                if (branch.options.length <= 2) branch.closest('.dash-range').classList.add('d-none');
            } catch (e) { console.error(e); }
            branch.addEventListener('change', load);
        }
        document.getElementById('dashExport').addEventListener('click', exportCsv);
        load();
        loadActivities();
        if (typeof SocketService !== 'undefined') {
            const socket = new SocketService('stock');
            socket.connect();
            socket.on('stock_update', reloadSoon);
            socket.on('activity_update', data => {
                addActivityItem(data.description, data.type === 'low_stock' ? 'danger' : 'info', data.icon, data.time);
                reloadSoon();
            });
        }
    });

    window.loadStats = load;                    // the header's Refresh button
})();
