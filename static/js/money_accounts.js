/* "Received into": the cash books and bank accounts a payment can go into.
 *
 * MoneyAccounts.fill(select, method, selectedId) fills a <select> with the
 * ledgers that suit the method — Cash shows the cash books, Bank / Mobile /
 * Cheque the bank accounts (all of them when none of that kind exists) — and
 * keeps the one picked if it is still offered. Loaded once per page.
 */
(function () {
    let cache = null;

    function load() {
        if (!cache) {
            cache = fetch('/api/transactions/money_accounts/', { credentials: 'same-origin' })
                .then(r => (r.ok ? r.json() : []))
                .catch(() => []);
        }
        return cache;
    }

    function kindFor(method) {
        return method === 'cash' || method === 'other' ? 'cash' : 'bank';
    }

    async function fill(select, method, selectedId) {
        if (!select) return;
        const all = await load();
        const kind = kindFor(method);
        let rows = all.filter(a => a.kind === kind);
        if (!rows.length) rows = all;
        const keep = selectedId != null ? String(selectedId) : select.value;
        select.innerHTML = '';
        rows.forEach(a => {
            const opt = document.createElement('option');
            opt.value = a.id;
            opt.textContent = a.name + (a.code ? ' (' + a.code + ')' : '');
            select.appendChild(opt);
        });
        if (!rows.length) {
            const opt = document.createElement('option');
            opt.value = '';
            opt.textContent = 'No cash book or bank set up';
            select.appendChild(opt);
        }
        if (keep && rows.some(a => String(a.id) === keep)) select.value = keep;
    }

    window.MoneyAccounts = { load, fill, kindFor };
})();
