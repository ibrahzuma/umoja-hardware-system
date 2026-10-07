/*
 * Voucher entry screen: dynamic debit/credit lines, real-time automatic
 * balancing, account restrictions per side, invoice allocation panels and
 * VAT helpers.  Everything here is a convenience; the Django backend
 * re-validates every rule before a voucher is saved or posted.
 *
 * This is the Pradeep accounting system's `static/js/voucher_form.js`,
 * carried over so the entry screen behaves and lays out exactly as it does
 * there.  Four names differ, and only these:
 *
 *   cfg.type is lower case here ('receipt', not 'RECEIPT');
 *   the date field is #id_date, not #id_transaction_date;
 *   the EFD field is #id_efd_number, not #id_efd_rct_number;
 *   an outstanding item's `id` is a "<target>:<pk>" token, because an open
 *   item here may be a till sale or a purchase order as well as an invoice.
 */
(function () {
  'use strict';

  var cfg = JSON.parse(document.getElementById('voucherConfig').textContent);
  var UI = window.AccountingUI;
  var body = document.getElementById('linesBody');
  var form = document.getElementById('voucherForm');
  var totalForms = form.querySelector('input[name="lines-TOTAL_FORMS"]');
  var allocTotalForms = form.querySelector('input[name="alloc-TOTAL_FORMS"]');
  var panels = document.getElementById('allocationPanels');
  var btnPost = document.getElementById('btnPost');
  var isReceipt = cfg.type === 'receipt';
  var isPayment = cfg.type === 'payment';
  var isSales = cfg.type === 'sales';
  var isPurchase = cfg.type === 'purchase';
  var accountsById = {};
  cfg.debitAccounts.concat(cfg.creditAccounts).forEach(function (a) { accountsById[a.id] = a; });

  /* ------------------------------------------------------------------ rows */
  function accountOptions(side, selected) {
    var list = side === 'debit' ? cfg.debitAccounts : cfg.creditAccounts;
    var html = '<option value="">-- select ' + side + ' account --</option>';
    list.forEach(function (a) {
      html += '<option value="' + a.id + '"' + (String(a.id) === String(selected || '') ? ' selected' : '') +
        ' data-kind="' + a.kind + '">' + escapeHtml(a.label) + '</option>';
    });
    return html;
  }

  function r2(n) { return Math.round(n * 100) / 100; }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function makeRow(side, data, auto) {
    data = data || {};
    var tr = document.createElement('tr');
    tr.dataset.side = side;
    tr.dataset.auto = auto ? '1' : '0';
    if (auto) { tr.classList.add('line-auto'); }
    tr.innerHTML =
      '<td class="text-muted line-no"></td>' +
      '<td><div class="d-flex align-items-center gap-1">' +
        '<span class="badge side-badge ' + (side === 'debit' ? 'text-bg-success' : 'text-bg-danger') + '">' + (side === 'debit' ? 'Dr' : 'Cr') + '</span>' +
        '<select class="form-select form-select-sm account-select">' + accountOptions(side, data.account) + '</select></div>' +
        '<div class="text-muted-sm account-kind"></div></td>' +
      '<td><input type="text" inputmode="decimal" class="form-control form-control-sm amount-input debit-input" placeholder="0.00"></td>' +
      '<td><input type="text" inputmode="decimal" class="form-control form-control-sm amount-input credit-input" placeholder="0.00"></td>' +
      '<td class="text-end"><input type="hidden" class="desc-input">' +
        '<button type="button" class="btn btn-outline-danger btn-sm remove-line" tabindex="-1" title="Remove line"><i class="bi bi-x-lg"></i></button></td>';
    var amount = side === 'debit' ? data.debit : data.credit;
    tr.querySelector('.desc-input').value = data.description || '';
    applySide(tr, side);
    if (amount && Number(amount) > 0) { activeAmountInput(tr).value = UI.formatMoney(amount); }
    if (auto) { tr.querySelector('.account-kind').textContent = 'Balancing line - select the account'; }
    bindRow(tr);
    return tr;
  }

  function applySide(tr, side) {
    tr.dataset.side = side;
    var debit = tr.querySelector('.debit-input');
    var credit = tr.querySelector('.credit-input');
    var badge = tr.querySelector('.side-badge');
    if (side === 'debit') {
      credit.value = ''; credit.disabled = true; debit.disabled = false;
      badge.textContent = 'Dr'; badge.className = 'badge side-badge text-bg-success';
    } else {
      debit.value = ''; debit.disabled = true; credit.disabled = false;
      badge.textContent = 'Cr'; badge.className = 'badge side-badge text-bg-danger';
    }
    var select = tr.querySelector('.account-select');
    var current = select.value;
    select.innerHTML = accountOptions(side, current);
    if (select.value !== current) { select.value = ''; }
  }

  function activeAmountInput(tr) {
    return tr.dataset.side === 'debit' ? tr.querySelector('.debit-input') : tr.querySelector('.credit-input');
  }

  function rowAmount(tr) { return UI.parseAmount(activeAmountInput(tr).value); }

  function bindRow(tr) {
    var select = tr.querySelector('.account-select');
    select.addEventListener('change', function () {
      if (tr.dataset.auto === '1' && select.value) { markManual(tr); }
      updateKind(tr);
      autoVat();
      recalc();
    });
    tr.querySelectorAll('.amount-input').forEach(function (inp) {
      inp.addEventListener('input', function () {
        if (tr.dataset.auto === '1') { markManual(tr); }
        recalc();
      });
      inp.addEventListener('blur', function () {
        var v = UI.parseAmount(inp.value);
        inp.value = v > 0 ? UI.formatMoney(v) : '';
        autoVat();
        recalc();
      });
      inp.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') {
          e.preventDefault();
          inp.blur();
          var next = tr.nextElementSibling;
          if (next) { next.querySelector('.account-select').focus(); }
          else { recalc(); var auto = body.querySelector('tr[data-auto="1"]'); if (auto) { auto.querySelector('.account-select').focus(); } }
        }
      });
    });
    tr.querySelector('.remove-line').addEventListener('click', function () {
      tr.remove();
      recalc();
    });
  }

  function markManual(tr) {
    tr.dataset.auto = '0';
    tr.classList.remove('line-auto');
    tr.querySelector('.account-kind').textContent = '';
  }

  function updateKind(tr) {
    var a = accountsById[tr.querySelector('.account-select').value];
    tr.querySelector('.account-kind').textContent = a ? a.kind : (tr.dataset.auto === '1' ? 'Balancing line - select the account' : '');
  }

  function addLine(side, data, auto, focus) {
    var tr = makeRow(side, data, auto);
    body.appendChild(tr);
    renumber();
    if (focus) { tr.querySelector('.account-select').focus(); }
    return tr;
  }

  function renumber() {
    var rows = body.querySelectorAll('tr');
    rows.forEach(function (tr, i) {
      tr.querySelector('.line-no').textContent = i + 1;
      tr.querySelector('.account-select').name = 'lines-' + i + '-account';
      tr.querySelector('.debit-input').name = 'lines-' + i + '-debit';
      tr.querySelector('.credit-input').name = 'lines-' + i + '-credit';
      tr.querySelector('.desc-input').name = 'lines-' + i + '-description';
    });
    totalForms.value = rows.length;
  }

  /* ------------------------------------------------------------- balancing */
  function recalc() {
    renumber();
    var rows = Array.prototype.slice.call(body.querySelectorAll('tr'));
    var manual = rows.filter(function (tr) { return tr.dataset.auto !== '1'; });
    var totalDebit = 0, totalCredit = 0;
    manual.forEach(function (tr) {
      var amt = rowAmount(tr);
      if (tr.dataset.side === 'debit') { totalDebit += amt; } else { totalCredit += amt; }
    });
    totalDebit = Math.round(totalDebit * 100) / 100;
    totalCredit = Math.round(totalCredit * 100) / 100;
    var diff = Math.round((totalDebit - totalCredit) * 100) / 100;

    var autoRow = rows.filter(function (tr) { return tr.dataset.auto === '1'; })[0];
    if (diff === 0) {
      if (autoRow) { autoRow.remove(); }
    } else {
      var side = diff > 0 ? 'credit' : 'debit';   // Debit > Credit -> next Credit line, and vice versa
      if (!autoRow) {
        autoRow = makeRow(side, {}, true);
        body.appendChild(autoRow);
      } else if (autoRow.dataset.side !== side) {
        applySide(autoRow, side);
      }
      activeAmountInput(autoRow).value = UI.formatMoney(Math.abs(diff));
    }
    renumber();

    document.getElementById('totalDebit').textContent = UI.formatMoney(totalDebit);
    document.getElementById('totalCredit').textContent = UI.formatMoney(totalCredit);
    var totalEl = document.getElementById('voucherTotal');
    var total = Math.max(totalDebit, totalCredit);
    totalEl.textContent = UI.formatMoney(total);
    totalEl.className = diff === 0 ? 'balanced' : 'unbalanced';
    showBaseEquivalent(total);

    var msg = document.getElementById('balanceMessage');
    var missing = manual.filter(function (tr) { return rowAmount(tr) > 0 && !tr.querySelector('.account-select').value; });
    if (diff !== 0) {
      msg.classList.remove('d-none');
      msg.innerHTML = '<i class="bi bi-exclamation-triangle me-1"></i>Debit and Credit are not balanced. Remaining difference: <strong>' +
        cfg.currency + ' ' + UI.formatMoney(Math.abs(diff)) + '</strong>. A ' + (diff > 0 ? 'credit' : 'debit') +
        ' line has been added for the difference - select its account.';
    } else if (missing.length) {
      msg.classList.remove('d-none');
      msg.innerHTML = '<i class="bi bi-exclamation-triangle me-1"></i>Select an account for every line with an amount.';
    } else {
      msg.classList.add('d-none');
    }
    var ok = diff === 0 && totalDebit > 0 && !missing.length;
    if (btnPost) {
      btnPost.disabled = !ok;
      document.getElementById('postHint').textContent = ok ? 'Balanced - ready to post' :
        (totalDebit === 0 && totalCredit === 0 ? 'Enter accounting lines' : 'Voucher must balance before posting');
    }
    syncAllocationPanels(manual);
  }

  /* Foreign-currency vouchers show what the total comes to in the base currency. */
  function showBaseEquivalent(total) {
    var row = document.getElementById('baseTotalRow');
    var label = document.getElementById('totalCurrency');
    if (!row || !baseCurrency) { return; }
    var c = currentCurrency();
    if (label) { label.textContent = c ? c.code : ''; }
    if (!c || c.isBase) { row.classList.add('d-none'); return; }
    var rate = currentRate();
    row.classList.remove('d-none');
    document.getElementById('baseTotalLabel').textContent =
      'In ' + baseCurrency.code + (rate > 0 ? ' @ ' + UI.formatMoney(rate) : '');
    document.getElementById('baseTotal').textContent = rate > 0 ? UI.formatMoney(r2(total * rate)) : '-';
  }

  /* ------------------------------------------------------ allocations (AJAX) */
  var panelState = {};   // key -> {party, invoices, el}

  function partyKeyForRow(tr) {
    var a = accountsById[tr.querySelector('.account-select').value];
    if (!a) { return null; }
    if (isReceipt && tr.dataset.side === 'credit' && a.customer_id) { return 'customer-' + a.customer_id; }
    if (isPayment && tr.dataset.side === 'debit' && a.supplier_id) { return 'supplier-' + a.supplier_id; }
    return null;
  }

  function syncAllocationPanels(rows) {
    if (!isReceipt && !isPayment) { return; }
    var amounts = {};
    rows.forEach(function (tr) {
      var key = partyKeyForRow(tr);
      var amt = rowAmount(tr);
      if (key && amt > 0) { amounts[key] = (amounts[key] || 0) + amt; }
    });
    Object.keys(panelState).forEach(function (key) {
      if (!amounts[key]) { panelState[key].el.remove(); delete panelState[key]; }
    });
    Object.keys(amounts).forEach(function (key) {
      if (!panelState[key]) { loadPanel(key); }
      else { updatePanelTotals(key, amounts[key]); }
    });
    reindexAllocations();
  }

  function loadPanel(key) {
    var parts = key.split('-');
    var el = document.createElement('div');
    el.className = 'card mb-3 alloc-panel';
    el.innerHTML = '<div class="card-body text-muted"><span class="spinner-border spinner-border-sm me-2"></span>Loading outstanding invoices...</div>';
    panels.appendChild(el);
    panelState[key] = { el: el, invoices: [], party: null };
    var url = cfg.urls.outstanding + '?' + parts[0] + '=' + parts[1] + (cfg.voucherId ? '&voucher=' + cfg.voucherId : '');
    fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' }, credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!panelState[key]) { return; }
        panelState[key].invoices = data.invoices;
        panelState[key].party = data.party;
        renderPanel(key);
        recalc();
      })
      .catch(function () { el.innerHTML = '<div class="card-body text-danger">Could not load outstanding invoices.</div>'; });
  }

  function renderPanel(key) {
    var st = panelState[key];
    var party = st.party;
    var isCust = party.kind === 'customer';
    var html = '<div class="card-header d-flex flex-wrap justify-content-between align-items-center gap-2">' +
      '<span><i class="bi bi-receipt me-1"></i>Outstanding ' + (isCust ? 'invoices' : 'bills') + ' - ' + escapeHtml(party.name) +
      ' <span class="text-muted-sm">(balance ' + UI.formatMoney(party.balance) + ')</span></span>' +
      '<span class="text-muted-sm">On this voucher: <strong class="party-amount">0.00</strong> &middot; Allocated: <strong class="party-allocated">0.00</strong> &middot; Unallocated: <strong class="party-unallocated">0.00</strong></span>' +
      '<button type="button" class="btn btn-outline-primary btn-sm auto-allocate"><i class="bi bi-lightning"></i> Auto-allocate (oldest first)</button></div>';
    if (!st.invoices.length) {
      html += '<div class="card-body text-muted">No outstanding ' + (isCust ? 'invoices' : 'bills') + '. The amount will remain unallocated (on account).</div>';
    } else {
      html += '<div class="table-responsive"><table class="table table-sm mb-0 alloc-table"><thead><tr>' +
        '<th>' + (isCust ? 'Invoice' : 'Bill') + '</th><th>Date</th><th class="num">Original</th><th class="num">Previously paid</th><th class="num">Outstanding</th><th class="num">Allocate</th></tr></thead><tbody>';
      st.invoices.forEach(function (inv) {
        var existing = cfg.existingAllocations.filter(function (a) { return a.invoice === inv.id; })[0];
        var val = existing ? existing.amount : (inv.drafted || '');
        html += '<tr data-invoice="' + inv.id + '" data-outstanding="' + inv.outstanding_amount + '">' +
          '<td>' + escapeHtml(inv.invoice_number) + '<div class="text-muted-sm">' + escapeHtml(inv.kind) + (inv.efd_rct_number ? ' &middot; EFD ' + escapeHtml(inv.efd_rct_number) : '') + '</div></td>' +
          '<td>' + inv.invoice_date + '</td><td class="num">' + UI.formatMoney(inv.original_amount) + '</td>' +
          '<td class="num">' + UI.formatMoney(inv.allocated_amount) + '</td><td class="num fw-semibold">' + UI.formatMoney(inv.outstanding_amount) + '</td>' +
          '<td class="text-end"><input type="hidden" class="alloc-invoice" value="' + inv.id + '">' +
          '<input type="text" inputmode="decimal" class="form-control form-control-sm alloc-input ms-auto" value="' + (val ? UI.formatMoney(val) : '') + '" placeholder="0.00"></td></tr>';
      });
      html += '</tbody></table></div>';
    }
    st.el.innerHTML = html;
    st.el.querySelectorAll('.alloc-input').forEach(function (inp) {
      inp.addEventListener('input', function () { recalc(); });
      inp.addEventListener('blur', function () {
        var tr = inp.closest('tr');
        var max = UI.parseAmount(tr.dataset.outstanding);
        var v = UI.parseAmount(inp.value);
        if (v > max) { v = max; UI.toast('Allocation limited to the outstanding balance of ' + UI.formatMoney(max), 'warning'); }
        inp.value = v > 0 ? UI.formatMoney(v) : '';
        recalc();
      });
    });
    var autoBtn = st.el.querySelector('.auto-allocate');
    if (autoBtn) { autoBtn.addEventListener('click', function () { autoAllocate(key); }); }
  }

  function updatePanelTotals(key, amount) {
    var st = panelState[key];
    if (!st.party) { return; }
    var allocated = 0;
    st.el.querySelectorAll('.alloc-input').forEach(function (inp) { allocated += UI.parseAmount(inp.value); });
    allocated = Math.round(allocated * 100) / 100;
    st.el.querySelector('.party-amount').textContent = UI.formatMoney(amount);
    st.el.querySelector('.party-allocated').textContent = UI.formatMoney(allocated);
    var un = st.el.querySelector('.party-unallocated');
    un.textContent = UI.formatMoney(amount - allocated);
    un.className = 'party-unallocated ' + (allocated > amount + 0.004 ? 'text-danger' : '');
    if (allocated > amount + 0.004 && btnPost) {
      btnPost.disabled = true;
      document.getElementById('postHint').textContent = 'Allocated amount exceeds the amount on the voucher';
    }
  }

  function autoAllocate(key) {
    var st = panelState[key];
    var amount = 0;
    Array.prototype.slice.call(body.querySelectorAll('tr')).forEach(function (tr) {
      if (tr.dataset.auto !== '1' && partyKeyForRow(tr) === key) { amount += rowAmount(tr); }
    });
    var remaining = amount;
    st.el.querySelectorAll('.alloc-table tbody tr').forEach(function (tr) {
      var max = UI.parseAmount(tr.dataset.outstanding);
      var take = Math.min(max, remaining);
      tr.querySelector('.alloc-input').value = take > 0 ? UI.formatMoney(take) : '';
      remaining = Math.round((remaining - take) * 100) / 100;
    });
    recalc();
  }

  function reindexAllocations() {
    var i = 0;
    panels.querySelectorAll('.alloc-table tbody tr').forEach(function (tr) {
      tr.querySelector('.alloc-invoice').name = 'alloc-' + i + '-invoice';
      tr.querySelector('.alloc-input').name = 'alloc-' + i + '-amount';
      i++;
    });
    if (allocTotalForms) { allocTotalForms.value = i; }
  }

  /* ----------------------------------------------------------------- currency */
  var currencySelect = document.getElementById('id_currency');
  var rateInputEl = document.getElementById('id_exchange_rate');
  var dateInput = document.getElementById('id_date');
  var baseCurrency = cfg.baseCurrency || null;

  function currentCurrency() {
    if (!currencySelect) { return baseCurrency; }
    var id = currencySelect.value;
    return (cfg.currencies || []).filter(function (c) { return String(c.id) === String(id); })[0] || baseCurrency;
  }

  function currentRate() {
    var c = currentCurrency();
    if (!c || c.isBase) { return 1; }
    var r = UI.parseAmount(rateInputEl ? rateInputEl.value : '');
    return r > 0 ? r : 0;
  }

  /* The rate box is only meaningful for a foreign currency; fill it from the rate table on change. */
  function refreshRate(force) {
    if (!currencySelect || !rateInputEl) { return; }
    var c = currentCurrency();
    var wrapper = document.getElementById('rateWrapper');
    if (c && c.isBase) {
      rateInputEl.value = '1';
      if (wrapper) { wrapper.classList.add('d-none'); }
      recalc();
      return;
    }
    if (wrapper) { wrapper.classList.remove('d-none'); }
    if (!force && UI.parseAmount(rateInputEl.value) > 0) { recalc(); return; }
    var url = cfg.urls.rate + '?currency=' + encodeURIComponent(currencySelect.value) +
      '&date=' + encodeURIComponent(dateInput ? dateInput.value : '');
    fetch(url, { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.rate) {
          rateInputEl.value = Number(d.rate).toString();
        } else {
          rateInputEl.value = '';
          UI.toast('No exchange rate for ' + d.currency + ' on ' + d.date + ' - enter it here or add it under Settings > Currencies.', 'warning');
        }
        recalc();
      })
      .catch(function () { /* leave whatever is in the box */ });
  }

  if (currencySelect) {
    currencySelect.addEventListener('change', function () { refreshRate(true); });
    if (dateInput) { dateInput.addEventListener('change', function () { refreshRate(true); }); }
    if (rateInputEl) { rateInputEl.addEventListener('input', recalc); }
  }

  /* ------------------------------------------------- sales / purchase auto VAT */
  var vatRate = UI.parseAmount(cfg.vatRate);
  var vatWarned = false;

  function isIncomeAccount(id) { var a = accountsById[id]; return !!a && a.type === 'INCOME'; }

  /* Purchase / expense / inventory ledger on a purchase voucher (not cash, bank, VAT or a party). */
  function isPurchaseAccount(id) {
    var a = accountsById[id];
    return !!a && (a.type === 'EXPENSE' || a.type === 'ASSET') && !a.is_bank_cash && !a.vat_kind &&
      !a.customer_id && !a.supplier_id && a.name.toLowerCase().indexOf('vat') === -1;
  }

  /* VAT rate implied by the sales / purchase ledger: "EXEMPT" / "ZERO RATED" ledgers carry no VAT,
     every other ledger (e.g. "STANDARD RATED SALES") is at the company rate. */
  function rateForAccount(id) {
    var a = accountsById[id];
    if (!a) { return 0; }
    var name = a.name.toLowerCase();
    if (name.indexOf('exempt') !== -1 || name.indexOf('zero') !== -1) { return 0; }
    return vatRate;
  }

  function manualRows() {
    return Array.prototype.filter.call(body.querySelectorAll('tr'), function (tr) { return tr.dataset.auto !== '1'; });
  }

  /* Sales: once a Sales (income) account is chosen on a credit line, the debit total (customer / cash)
     is treated as VAT-inclusive at that account's rate - the sales line gets the net amount and a
     "VAT on sales" credit line is maintained for the VAT portion (none for exempt sales).
     Purchases: the mirror image - a purchase / expense account on a debit line splits the credit total
     (supplier / cash) into the net purchase and a "VAT on purchases" debit line. */
  function autoVat() {
    if (!isSales && !isPurchase) { return; }
    var mainSide = isSales ? 'credit' : 'debit';        // side carrying the sales / purchase ledger
    var grossSide = isSales ? 'debit' : 'credit';       // side carrying the VAT-inclusive total
    var isMain = isSales ? isIncomeAccount : isPurchaseAccount;
    var vatAccount = isSales ? cfg.defaults.outputVat : cfg.defaults.inputVat;
    var rows = manualRows();
    var mainRow = rows.filter(function (tr) {
      return tr.dataset.side === mainSide && isMain(tr.querySelector('.account-select').value);
    })[0];
    var vatRow = rows.filter(function (tr) { return tr.dataset.vatline === '1'; })[0];
    if (!mainRow) { if (vatRow) { vatRow.remove(); } return; }
    var gross = 0;
    rows.forEach(function (tr) { if (tr.dataset.side === grossSide) { gross += rowAmount(tr); } });
    gross = r2(gross);
    if (gross <= 0) { return; }
    var rate = rateForAccount(mainRow.querySelector('.account-select').value);
    var net = rate > 0 ? r2(gross / (1 + rate / 100)) : gross;
    var vat = r2(gross - net);
    activeAmountInput(mainRow).value = net > 0 ? UI.formatMoney(net) : '';
    if (!mainRow.querySelector('.desc-input').value) { mainRow.querySelector('.desc-input').value = isSales ? 'Sales' : 'Purchase'; }
    if (vat > 0) {
      if (!vatAccount) {
        if (!vatWarned) {
          UI.toast('No "VAT on ' + (isSales ? 'sales' : 'purchases') + '" ledger found in the chart of accounts - add the VAT line manually.', 'warning');
          vatWarned = true;
        }
        return;
      }
      if (!vatRow) {
        vatRow = makeRow(mainSide, { account: vatAccount, description: isSales ? 'VAT on sales' : 'VAT on purchases' }, false);
        vatRow.dataset.vatline = '1';
        mainRow.insertAdjacentElement('afterend', vatRow);
        updateKind(vatRow);
      }
      activeAmountInput(vatRow).value = UI.formatMoney(vat);
    } else if (vatRow) {
      vatRow.remove();
    }
  }

  /* -------------------------------------------------------- EFD duplicate check */
  var efdInput = document.getElementById('id_efd_number');
  if (efdInput) {
    efdInput.addEventListener('blur', function () {
      var box = document.getElementById('efdWarning');
      box.innerHTML = '';
      efdInput.classList.remove('is-invalid');
      var n = efdInput.value.trim();
      if (!n) { return; }
      fetch(cfg.urls.efdCheck + '?number=' + encodeURIComponent(n) + '&type=' + cfg.type + (cfg.voucherId ? '&exclude=' + cfg.voucherId : ''), { credentials: 'same-origin' })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (!d.duplicate) { return; }
          var text = 'EFD RCT number already used on voucher <a href="' + d.voucher.url + '" target="_blank">' + escapeHtml(d.voucher.number) + '</a> (' + d.voucher.date + ').';
          if (d.policy === 'BLOCK') { efdInput.classList.add('is-invalid'); box.innerHTML = '<span class="text-danger">' + text + ' Duplicates are blocked.</span>'; }
          else if (d.policy === 'WARN') { box.innerHTML = '<span class="text-warning"><i class="bi bi-exclamation-triangle"></i> ' + text + '</span>'; }
        });
    });
  }

  /* ---------------------------------------------------------- keyboard & submit */
  document.getElementById('addDebit').addEventListener('click', function () { addLine('debit', {}, false, true); });
  document.getElementById('addCredit').addEventListener('click', function () { addLine('credit', {}, false, true); });
  document.addEventListener('keydown', function (e) {
    if (e.altKey && (e.key === 'd' || e.key === 'D')) { e.preventDefault(); addLine('debit', {}, false, true); }
    if (e.altKey && (e.key === 'c' || e.key === 'C')) { e.preventDefault(); addLine('credit', {}, false, true); }
    if ((e.ctrlKey || e.metaKey) && (e.key === 's' || e.key === 'S')) { e.preventDefault(); document.getElementById('btnSave').click(); }
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter' && btnPost && !btnPost.disabled) { e.preventDefault(); btnPost.click(); }
  });

  form.addEventListener('submit', function (e) {
    // Normalise formatted amounts back to plain numbers and drop the pending balancing row if it is unused.
    var action = (e.submitter && e.submitter.value) || 'save';
    body.querySelectorAll('tr[data-auto="1"]').forEach(function (tr) {
      if (!tr.querySelector('.account-select').value) {
        if (action === 'post') { markManual(tr); } // let the server report the missing account clearly
        else { tr.remove(); }
      } else { markManual(tr); }
    });
    form.querySelectorAll('.amount-input, .alloc-input').forEach(function (inp) {
      var v = UI.parseAmount(inp.value);
      inp.value = v > 0 ? v.toFixed(2) : '';
    });
    renumber();
    reindexAllocations();
  });

  /* ---------------------------------------------------------------- initialise */
  if (cfg.existingLines.length) {
    cfg.existingLines.forEach(function (l) { addLine(l.side, l); });
    body.querySelectorAll('tr').forEach(updateKind);
  } else if (isPurchase) {
    addLine('credit', {});
    addLine('debit', {});
  } else {
    addLine('debit', {});
    addLine('credit', {});
  }
  refreshRate(false);
  recalc();
})();
