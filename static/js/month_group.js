/**
 * Grouping a money ledger by the month it fell in.
 *
 * Shared by the cash desk's three registers (petty cash, supplier payments,
 * other payments) so a month means the same thing on all of them.
 *
 * Months are read straight off the `YYYY-MM-DD` string the API returns, never
 * from a parsed Date: a payment dated the 1st or the 31st would otherwise slide
 * into the neighbouring month under a timezone offset, and money must not move
 * between months because of a browser clock.
 *
 * Each page renders its own heading row — a petty cash month totals cash in and
 * cash out separately, a payment month totals one column — so only the pieces
 * that are genuinely the same live here.
 */
(function (global) {
    'use strict';

    var MONTH_NAMES = ['January', 'February', 'March', 'April', 'May', 'June',
        'July', 'August', 'September', 'October', 'November', 'December'];

    function escapeHtml(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    /** "2026-09-07" -> "2026-09". Anything unparseable groups under ''. */
    function key(dateString) {
        var s = String(dateString || '');
        return /^\d{4}-\d{2}/.test(s) ? s.slice(0, 7) : '';
    }

    /** "2026-09" -> "September 2026". */
    function label(monthKey) {
        if (!monthKey) return 'Undated';
        var parts = monthKey.split('-');
        var name = MONTH_NAMES[Number(parts[1]) - 1];
        return name ? name + ' ' + parts[0] : monthKey;
    }

    /**
     * Group rows into months, newest month first and newest row first inside
     * each. Returns [{key, label, rows}], so the caller can total whichever
     * columns its own table shows.
     */
    function group(rows, dateField) {
        var buckets = {};
        (rows || []).forEach(function (row) {
            var k = key(row[dateField]);
            (buckets[k] = buckets[k] || []).push(row);
        });

        return Object.keys(buckets).sort().reverse().map(function (k) {
            var inMonth = buckets[k].slice().sort(function (a, b) {
                return String(b[dateField]).localeCompare(String(a[dateField]));
            });
            return { key: k, label: label(k), rows: inMonth };
        });
    }

    /**
     * Fill a <select> with the months present in `rows`, newest first, behind
     * an "All months" option. Keeps the current choice if it still exists.
     */
    function populateSelect(select, rows, dateField) {
        if (!select) return;
        var keep = select.value;
        var keys = [];
        (rows || []).forEach(function (row) {
            var k = key(row[dateField]);
            if (keys.indexOf(k) === -1) keys.push(k);
        });
        keys.sort().reverse();

        select.innerHTML = '<option value="">All months</option>';
        keys.forEach(function (k) {
            select.innerHTML += '<option value="' + k + '">' + escapeHtml(label(k)) + '</option>';
        });
        if (keep && keys.indexOf(keep) !== -1) select.value = keep;
    }

    /**
     * Sum `amountField` per distinct `byField` within one month's rows,
     * biggest first: [{name, amount, count}].
     */
    function totalsBy(rows, byField, amountField) {
        var totals = {};
        (rows || []).forEach(function (row) {
            var name = row[byField] || '-';
            if (!totals[name]) totals[name] = { name: name, amount: 0, count: 0 };
            totals[name].amount += Number(row[amountField] || 0);
            totals[name].count += 1;
        });
        return Object.keys(totals).map(function (n) { return totals[n]; })
            .sort(function (a, b) { return b.amount - a.amount; });
    }

    global.MonthGroup = {
        key: key,
        label: label,
        group: group,
        populateSelect: populateSelect,
        totalsBy: totalsBy,
    };
}(window));
