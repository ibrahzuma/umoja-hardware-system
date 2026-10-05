/*
 * Voucher entry.
 *
 * The golden rule — Total Debit = Total Credit — is enforced on the server by
 * `apps/finance/posting.py`. This file is the convenience half: it does the
 * same arithmetic as you type so the form tells you where you are, and it
 * writes the balancing line for you.
 *
 * The entry rule, from the specification:
 *
 *   Whenever the two sides differ, the difference is written into the next
 *   empty amount field on the *short* side — a new line only when none is
 *   open. A figure the system wrote keeps re-sizing as the other lines change,
 *   until the user types over it; from then on it is theirs and is left alone.
 *
 * Keyboard: Alt+D adds a debit line, Alt+C a credit line, Ctrl+S saves the
 * draft, Ctrl+Enter posts, and Enter walks amount → description → next line.
 */
(function () {
    'use strict';

    const configEl = document.getElementById('voucherConfig');
    if (!configEl) { return; }
    const CFG = JSON.parse(configEl.textContent);

    const tbody = document.getElementById('lineRows');
    const template = document.getElementById('lineTemplate');
    const form = document.getElementById('voucherForm');
    if (!tbody || !template || !form) { return; }

    const totalFormsInput = document.querySelector('[name="lines-TOTAL_FORMS"]');
    const allocTotalInput = document.querySelector('[name="alloc-TOTAL_FORMS"]');
    const allocContainer = document.getElementById('allocRows');
    const allocPanel = document.getElementById('allocPanel');

    const MIN_ROWS = 4;
    let rowIndex = 0;

    // ---------------------------------------------------------------- helpers
    const num = (value) => {
        const parsed = parseFloat(String(value == null ? '' : value).replace(/,/g, ''));
        return isNaN(parsed) ? 0 : parsed;
    };
    const money = (value) => num(value).toLocaleString(undefined, {
        minimumFractionDigits: 2, maximumFractionDigits: 2,
    });
    const round2 = (value) => Math.round(num(value) * 100) / 100;

    function accountsFor(side) {
        return side === 'debit' ? CFG.debitAccounts : CFG.creditAccounts;
    }

    function accountById(id) {
        const all = CFG.debitAccounts.concat(CFG.creditAccounts);
        return all.find((a) => String(a.id) === String(id)) || null;
    }

    // ------------------------------------------------------------- line rows
    function addRow(data) {
        const node = template.content.firstElementChild.cloneNode(true);
        const index = rowIndex++;
        node.dataset.index = index;

        node.querySelectorAll('[data-name]').forEach((field) => {
            field.name = `lines-${index}-${field.dataset.name}`;
            field.id = `id_lines-${index}-${field.dataset.name}`;
        });

        const select = node.querySelector('[data-name="account"]');
        // Every postable ledger is offered; which side it is allowed on is
        // checked when the amount lands on that side, so a user is never
        // left hunting for a ledger that is simply in the other list.
        const options = ['<option value="">— pick a ledger —</option>'];
        const seen = new Set();
        CFG.debitAccounts.concat(CFG.creditAccounts).forEach((account) => {
            if (seen.has(account.id)) { return; }
            seen.add(account.id);
            options.push(`<option value="${account.id}">${account.label}</option>`);
        });
        select.innerHTML = options.join('');

        if (data) {
            if (data.account) { select.value = data.account; }
            node.querySelector('[data-name="debit"]').value = num(data.debit) ? round2(data.debit) : '';
            node.querySelector('[data-name="credit"]').value = num(data.credit) ? round2(data.credit) : '';
            node.querySelector('[data-name="description"]').value = data.description || '';
        }

        tbody.appendChild(node);
        wireRow(node);
        return node;
    }

    function wireRow(row) {
        const debit = row.querySelector('[data-name="debit"]');
        const credit = row.querySelector('[data-name="credit"]');
        const select = row.querySelector('[data-name="account"]');

        [debit, credit].forEach((input) => {
            input.addEventListener('input', () => {
                // Typing in an amount makes it the user's own figure, so the
                // balancing logic stops overwriting it.
                row.dataset.auto = '';
                row.classList.remove('line-auto');
                // One side only: entering a debit clears any credit, and back.
                const other = input === debit ? credit : debit;
                if (num(input.value) && num(other.value)) { other.value = ''; }
                recalculate();
            });
            input.addEventListener('keydown', onAmountKey);
        });

        row.querySelector('[data-name="description"]')
            .addEventListener('keydown', onDescriptionKey);

        select.addEventListener('change', () => {
            onAccountChosen(row);
            recalculate();
        });
        row.querySelector('.remove-line').addEventListener('click', () => {
            row.remove();
            ensureSpareRows();
            recalculate();
        });
    }

    function rows() {
        return Array.from(tbody.querySelectorAll('tr'));
    }

    function readRow(row) {
        return {
            row,
            accountId: row.querySelector('[data-name="account"]').value,
            debit: num(row.querySelector('[data-name="debit"]').value),
            credit: num(row.querySelector('[data-name="credit"]').value),
        };
    }

    function blankRows() {
        return rows().filter((row) => {
            const read = readRow(row);
            return !read.accountId && !read.debit && !read.credit;
        });
    }

    function ensureSpareRows() {
        // Always leave one empty row open to type into, and keep the table at
        // a readable height. Recounted after each addition, so this settles
        // rather than adding a row per call.
        let guard = MIN_ROWS + 2;                 // nothing here can run away
        while (guard-- > 0 && (rows().length < MIN_ROWS || blankRows().length === 0)) {
            addRow();
        }
        syncTotalForms();
    }

    function syncTotalForms() {
        if (totalFormsInput) { totalFormsInput.value = String(rowIndex); }
    }

    // ------------------------------------------------------------ the VAT rule
    function onAccountChosen(row) {
        /* Choosing a sales ledger on a credit line — or a purchase/expense
         * ledger on a debit line — treats the amount on the *other* side as
         * VAT-inclusive, and adds the VAT line for the difference. A ledger
         * whose name says EXEMPT or ZERO is 0%, so nothing is added.
         *
         * This is a convenience, not a rule: the VAT line it writes is an
         * ordinary line on the Output/Input VAT ledger and can be edited or
         * deleted like any other.
         */
        if (!CFG.isInvoice) { return; }
        const read = readRow(row);
        const account = accountById(read.accountId);
        if (!account) { return; }

        const isSalesSide = CFG.type === 'sales' && read.credit > 0 && account.type === 'INCOME';
        const isPurchaseSide = CFG.type === 'purchase' && read.debit > 0
            && (account.type === 'EXPENSE' || account.type === 'ASSET') && !account.isMoney;
        if (!isSalesSide && !isPurchaseSide) { return; }

        const name = (account.name || '').toUpperCase();
        const rate = (name.includes('EXEMPT') || name.includes('ZERO'))
            ? 0 : num(CFG.vatRate);
        if (!rate) { return; }

        const vatAccountId = isSalesSide ? CFG.defaults.outputVat : CFG.defaults.inputVat;
        if (!vatAccountId) { return; }

        const goodsSide = isSalesSide ? 'credit' : 'debit';
        const inclusive = isSalesSide
            ? rows().reduce((sum, r) => sum + readRow(r).debit, 0)
            : rows().reduce((sum, r) => sum + readRow(r).credit, 0);
        if (!inclusive) { return; }

        const net = round2(inclusive / (1 + rate / 100));
        const vat = round2(inclusive - net);
        if (vat <= 0) { return; }

        // The goods line becomes the net, and the VAT goes on its own line.
        row.querySelector(`[data-name="${goodsSide}"]`).value = net;
        row.dataset.auto = '';

        let vatRow = rows().find((r) => {
            const read2 = readRow(r);
            return String(read2.accountId) === String(vatAccountId) && r !== row;
        });
        if (!vatRow) {
            vatRow = rows().find((r) => {
                const read2 = readRow(r);
                return !read2.accountId && !read2.debit && !read2.credit;
            }) || addRow();
            vatRow.querySelector('[data-name="account"]').value = vatAccountId;
        }
        vatRow.querySelector(`[data-name="${goodsSide}"]`).value = vat;
        vatRow.querySelector('[data-name="description"]').value = `VAT ${rate}%`;
        vatRow.dataset.auto = '';
        ensureSpareRows();
    }

    // ------------------------------------------------------ balancing the form
    function recalculate() {
        let debit = 0;
        let credit = 0;
        rows().forEach((row) => {
            const read = readRow(row);
            debit += read.debit;
            credit += read.credit;
        });
        debit = round2(debit);
        credit = round2(credit);
        let difference = round2(debit - credit);

        // Re-size, or write, the balancing line on the short side.
        if (difference !== 0) {
            const shortSide = difference > 0 ? 'credit' : 'debit';
            const amount = Math.abs(difference);
            let target = rows().find((row) => row.dataset.auto === shortSide);
            if (!target) {
                target = rows().find((row) => {
                    const read = readRow(row);
                    return !read.debit && !read.credit;
                });
            }
            if (!target) { target = addRow(); }
            target.dataset.auto = shortSide;
            target.classList.add('line-auto');
            target.querySelector(`[data-name="${shortSide}"]`).value = amount;
            target.querySelector(
                `[data-name="${shortSide === 'debit' ? 'credit' : 'debit'}"]`).value = '';

            if (shortSide === 'credit') { credit = round2(credit + amount); }
            else { debit = round2(debit + amount); }
            difference = round2(debit - credit);
        } else {
            // Balanced: an auto line that is no longer needed stops being one.
            rows().forEach((row) => {
                if (row.dataset.auto && !num(row.querySelector(
                        `[data-name="${row.dataset.auto}"]`).value)) {
                    row.dataset.auto = '';
                    row.classList.remove('line-auto');
                }
            });
        }

        document.getElementById('totalDebit').textContent = money(debit);
        document.getElementById('totalCredit').textContent = money(credit);
        const differenceEl = document.getElementById('difference');
        differenceEl.textContent = money(Math.abs(difference));
        differenceEl.className = difference === 0 ? 'fw-bold diff-ok' : 'fw-bold diff-bad';

        const balanced = difference === 0 && debit > 0;
        const note = document.getElementById('balanceNote');
        if (balanced) {
            note.className = 'small diff-ok';
            note.textContent = 'Balanced — this voucher can be posted.';
        } else if (debit === 0 && credit === 0) {
            note.className = 'small text-muted';
            note.textContent = 'Enter the lines; the balancing line is written for you.';
        } else {
            note.className = 'small diff-bad';
            note.textContent = difference > 0
                ? `Debit exceeds credit by ${money(Math.abs(difference))} — pick the ledger for the credit line.`
                : `Credit exceeds debit by ${money(Math.abs(difference))} — pick the ledger for the debit line.`;
        }

        const postButton = document.getElementById('postButton');
        if (postButton) { postButton.disabled = !balanced; }

        maybeLoadOutstanding();
        ensureSpareRows();
        syncTotalForms();
    }

    // ----------------------------------------------------- invoice allocation
    let allocationKey = '';

    function allocationTarget() {
        /* A receipt allocates against a customer credited; a payment against a
         * supplier debited. Anywhere else allocation means nothing.
         *
         * The amount is the *total* put against that party across the lines,
         * not the first line's — allocation can never exceed it, and a party
         * may legitimately appear on more than one line. */
        if (CFG.type !== 'receipt' && CFG.type !== 'payment') { return null; }
        const side = CFG.type === 'receipt' ? 'credit' : 'debit';
        const field = CFG.type === 'receipt' ? 'customerId' : 'supplierId';
        let party = null;
        let amount = 0;
        rows().forEach((row) => {
            const read = readRow(row);
            if (!read[side]) { return; }
            const account = accountById(read.accountId);
            if (!account || !account[field]) { return; }
            if (party === null) { party = account[field]; }
            if (account[field] === party) { amount += read[side]; }
        });
        return party === null ? null : { party, field, amount: round2(amount) };
    }

    function maybeLoadOutstanding() {
        const target = allocationTarget();
        if (!target) {
            if (allocPanel) { allocPanel.classList.add('d-none'); }
            allocationKey = '';
            return;
        }
        const key = `${target.field}:${target.party}`;
        if (key === allocationKey) { return; }
        allocationKey = key;

        const params = new URLSearchParams();
        params.set(target.field === 'customerId' ? 'customer' : 'supplier', target.party);
        if (CFG.voucherId) { params.set('voucher', CFG.voucherId); }
        fetch(`${CFG.urls.outstanding}?${params.toString()}`, {
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
        })
            .then((response) => (response.ok ? response.json() : null))
            .then((data) => { if (data) { renderAllocations(data); } })
            .catch(() => { /* the form still works without the panel */ });
    }

    function renderAllocations(data) {
        if (!allocPanel || !allocContainer) { return; }
        allocPanel.classList.remove('d-none');
        document.getElementById('allocParty').textContent = data.party.name;
        document.getElementById('allocBalance').textContent = money(data.party.balance);

        if (!data.items.length) {
            allocContainer.innerHTML =
                '<tr><td colspan="6" class="text-muted small py-3 text-center">' +
                'Nothing outstanding — this money stays on account.</td></tr>';
            if (allocTotalInput) { allocTotalInput.value = '0'; }
            return;
        }

        allocContainer.innerHTML = data.items.map((item, index) => `
            <tr>
                <td class="small">${item.reference}
                    <input type="hidden" name="alloc-${index}-${item.target}" value="${item.id}">
                    <input type="hidden" name="alloc-${index}-reference" value="${item.reference}">
                </td>
                <td class="small text-muted">${item.target.replace('_', ' ')}</td>
                <td class="small">${item.date}</td>
                <td class="text-end small">${money(item.total)}</td>
                <td class="text-end small">${money(item.paid)}</td>
                <td class="text-end"><strong>${money(item.outstanding)}</strong></td>
                <td>
                    <input type="number" step="0.01" min="0" max="${item.outstanding}"
                           class="form-control form-control-sm amount-input alloc-amount"
                           name="alloc-${index}-amount" value="${item.drafted || ''}"
                           data-outstanding="${item.outstanding}">
                </td>
            </tr>
        `).join('');
        if (allocTotalInput) { allocTotalInput.value = String(data.items.length); }

        allocContainer.querySelectorAll('.alloc-amount').forEach((input) => {
            input.addEventListener('input', () => {
                const cap = num(input.dataset.outstanding);
                if (num(input.value) > cap) { input.value = cap; }
                updateAllocationTotal();
            });
        });
        updateAllocationTotal();
    }

    function updateAllocationTotal() {
        let total = 0;
        allocContainer.querySelectorAll('.alloc-amount').forEach((input) => {
            total += num(input.value);
        });
        const target = allocationTarget();
        const available = target ? target.amount : 0;
        const totalEl = document.getElementById('allocTotal');
        totalEl.textContent = money(total);
        const warning = document.getElementById('allocWarning');
        if (total > available) {
            warning.textContent = `Allocated ${money(total)} but only ${money(available)} `
                + 'was put against this party on the voucher.';
            warning.classList.remove('d-none');
        } else {
            warning.classList.add('d-none');
        }
    }

    // ----------------------------------------------------------- EFD checking
    const efdInput = document.getElementById('id_efd_number');
    if (efdInput) {
        let timer = null;
        efdInput.addEventListener('input', () => {
            clearTimeout(timer);
            timer = setTimeout(() => {
                const number = efdInput.value.trim();
                const notice = document.getElementById('efdNotice');
                if (!number) { notice.classList.add('d-none'); return; }
                const params = new URLSearchParams({ number, type: CFG.type });
                if (CFG.voucherId) { params.set('exclude', CFG.voucherId); }
                fetch(`${CFG.urls.efdCheck}?${params.toString()}`)
                    .then((response) => (response.ok ? response.json() : null))
                    .then((data) => {
                        if (!data || !data.duplicate) { notice.classList.add('d-none'); return; }
                        const blocked = data.policy === 'BLOCK';
                        notice.className = `small mt-1 ${blocked ? 'text-danger' : 'text-warning'}`;
                        notice.innerHTML = `${blocked ? 'Already used' : 'Also used'} on `
                            + `<a href="${data.voucher.url}">${data.voucher.number}</a> `
                            + `dated ${data.voucher.date}.`
                            + (blocked ? ' Posting will be refused.' : '');
                        notice.classList.remove('d-none');
                    })
                    .catch(() => notice.classList.add('d-none'));
            }, 350);
        });
    }

    // --------------------------------------------------- currency and its rate
    const currencySelect = document.getElementById('id_currency');
    const rateInput = document.getElementById('id_exchange_rate');
    const dateInput = document.getElementById('id_date');
    function refreshRate() {
        if (!currencySelect || !rateInput) { return; }
        const currency = CFG.currencies.find(
            (c) => String(c.id) === String(currencySelect.value));
        const isBase = currency ? currency.isBase : true;
        rateInput.readOnly = isBase;
        if (isBase) { rateInput.value = '1'; return; }
        const params = new URLSearchParams({ currency: currencySelect.value });
        if (dateInput && dateInput.value) { params.set('date', dateInput.value); }
        fetch(`${CFG.urls.rate}?${params.toString()}`)
            .then((response) => (response.ok ? response.json() : null))
            .then((data) => {
                if (data && data.rate) { rateInput.value = data.rate; }
            })
            .catch(() => { /* the user can type the rate */ });
    }
    if (currencySelect) {
        currencySelect.addEventListener('change', refreshRate);
        if (dateInput) { dateInput.addEventListener('change', refreshRate); }
    }

    // ------------------------------------------------------- keyboard working
    function onAmountKey(event) {
        /* Enter walks amount -> description -> the next line's ledger, which
         * is how a keyboard-only operator gets down a voucher. */
        if (event.key !== 'Enter') { return; }
        event.preventDefault();
        const row = event.target.closest('tr');
        row.querySelector('[data-name="description"]').focus();
    }

    function onDescriptionKey(event) {
        if (event.key !== 'Enter') { return; }
        event.preventDefault();
        const row = event.target.closest('tr');
        const next = row.nextElementSibling || addRow();
        next.querySelector('[data-name="account"]').focus();
    }

    document.addEventListener('keydown', (event) => {
        if (event.altKey && (event.key === 'd' || event.key === 'D')) {
            event.preventDefault();
            addRow().querySelector('[data-name="account"]').focus();
        } else if (event.altKey && (event.key === 'c' || event.key === 'C')) {
            event.preventDefault();
            addRow().querySelector('[data-name="account"]').focus();
        } else if (event.ctrlKey && event.key === 'Enter') {
            event.preventDefault();
            const button = document.getElementById('postButton');
            if (button && !button.disabled) { button.click(); }
        } else if (event.ctrlKey && (event.key === 's' || event.key === 'S')) {
            event.preventDefault();
            document.getElementById('saveButton').click();
        }
    });

    document.getElementById('addLine').addEventListener('click', () => {
        addRow().querySelector('[data-name="account"]').focus();
    });

    // Submitting keeps every row, blank ones included — the server ignores an
    // empty line, and dropping them here would renumber the formset mid-post.
    form.addEventListener('submit', () => { syncTotalForms(); });

    // -------------------------------------------------------------- first draw
    (CFG.existingLines.length ? CFG.existingLines : [null, null]).forEach((line) => addRow(line));
    ensureSpareRows();
    recalculate();
    refreshRate();
})();
