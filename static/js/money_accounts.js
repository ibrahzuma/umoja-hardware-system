/* "Received into": the cash books and bank accounts a payment can go into.
 *
 * MoneyAccounts.fill(select, method, selectedId, branchId) fills a <select>
 * with the ledgers that suit the method — Cash shows the cash books, Bank /
 * Mobile / Cheque the bank accounts (all of them when none of that kind
 * exists), and the method 'all' shows every cash book and bank together.
 * With a shop, only that shop's own cash book and the company-wide
 * ones are offered, and the shop's own is picked first. The one already
 * picked is kept if it is still offered. Each option carries the ledger's
 * kind on data-kind, so the caller can tell a cash ledger from a bank one.
 */
(function () {
    const cache = {};

    function load(branchId) {
        const key = branchId ? String(branchId) : '';
        if (!cache[key]) {
            const url = '/api/transactions/money_accounts/' + (key ? '?branch=' + encodeURIComponent(key) : '');
            cache[key] = fetch(url, { credentials: 'same-origin' })
                .then(r => (r.ok ? r.json() : []))
                .catch(() => []);
        }
        return cache[key];
    }

    function kindFor(method) {
        return method === 'cash' || method === 'other' ? 'cash' : 'bank';
    }

    async function fill(select, method, selectedId, branchId) {
        if (!select) return;
        const all = await load(branchId);
        let rows;
        if (method === 'all') {
            rows = all.slice();                 // every cash book and bank
        } else {
            const kind = kindFor(method);
            rows = all.filter(a => a.kind === kind);
            if (!rows.length) rows = all;
        }
        const keep = selectedId != null ? String(selectedId) : select.value;
        select.innerHTML = '';
        rows.forEach(a => {
            const opt = document.createElement('option');
            opt.value = a.id;
            opt.dataset.kind = a.kind;
            opt.textContent = a.name + (a.code ? ' (' + a.code + ')' : '');
            select.appendChild(opt);
        });
        if (!rows.length) {
            const opt = document.createElement('option');
            opt.value = '';
            opt.textContent = 'No cash book or bank set up';
            select.appendChild(opt);
        }
        if (keep && rows.some(a => String(a.id) === keep)) {
            select.value = keep;
        } else if (branchId) {
            const own = rows.find(a => String(a.branch) === String(branchId));
            if (own) select.value = own.id;
        }
    }

    window.MoneyAccounts = { load, fill, kindFor };
})();
