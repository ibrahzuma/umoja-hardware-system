/**
 * Page every long table in the app, without touching the code that fills it.
 *
 * Screens here render their rows in wildly different ways — some from a fetch,
 * some from a Django loop, some redrawn on every keystroke — so this works at
 * the DOM level instead: it watches a table's <tbody>, and after anything
 * changes it shows one page of rows and draws the controls. Nothing a page
 * already does has to change.
 *
 * Rules:
 *  - A table is paged once it has more rows than the chosen page size, so short
 *    tables look exactly as they always did.
 *  - `data-no-paginate` on a <table> opts out. Use it where every row has to be
 *    on screen at once: a cart being built, the items of one order being
 *    cross-checked, a payslip breakdown.
 *  - Tables inside a modal are left alone.
 *  - A placeholder row ("Loading…", "No data found" — one cell with a colspan)
 *    is never treated as data and never hidden.
 *  - The chosen size is remembered per browser, so a user who works in 100s
 *    keeps them.
 */
(function () {
    'use strict';

    var SIZES = [10, 25, 50, 100];
    var STORAGE_KEY = 'umoja.tablePageSize';
    var DEFAULT_SIZE = 10;

    function storedSize() {
        try {
            var n = Number(window.localStorage.getItem(STORAGE_KEY));
            return SIZES.indexOf(n) !== -1 ? n : DEFAULT_SIZE;
        } catch (e) {
            return DEFAULT_SIZE;
        }
    }

    function rememberSize(n) {
        try {
            window.localStorage.setItem(STORAGE_KEY, String(n));
        } catch (e) {
            /* private window, blocked storage — paging still works, it just
               will not be remembered next time. */
        }
    }

    /** A row that is a message, not data: one cell spanning the table. */
    function isPlaceholder(tr) {
        var cells = tr.children;
        return cells.length === 1 && cells[0].hasAttribute('colspan');
    }

    function TablePager(table) {
        this.table = table;
        this.tbody = table.tBodies[0];
        this.size = storedSize();
        this.page = 1;
        this.controls = null;
        this.rows = [];
    }

    TablePager.prototype.dataRows = function () {
        var out = [];
        for (var i = 0; i < this.tbody.rows.length; i++) {
            var tr = this.tbody.rows[i];
            if (!isPlaceholder(tr)) out.push(tr);
        }
        return out;
    };

    TablePager.prototype.buildControls = function () {
        var wrap = document.createElement('div');
        wrap.className = 'table-pager d-flex flex-wrap justify-content-between align-items-center '
            + 'gap-2 px-3 py-2 border-top small';

        var left = document.createElement('div');
        left.className = 'd-flex align-items-center gap-2 text-muted';
        var select = document.createElement('select');
        select.className = 'form-select form-select-sm';
        select.style.width = 'auto';
        SIZES.forEach(function (n) {
            var opt = document.createElement('option');
            opt.value = String(n);
            opt.textContent = String(n);
            select.appendChild(opt);
        });
        select.value = String(this.size);
        var self = this;
        select.addEventListener('change', function () {
            self.size = Number(select.value);
            rememberSize(self.size);
            self.page = 1;
            self.refresh();
        });
        left.appendChild(document.createTextNode('Show'));
        left.appendChild(select);
        left.appendChild(document.createTextNode('per page'));

        var middle = document.createElement('div');
        middle.className = 'text-muted';

        var right = document.createElement('div');
        right.className = 'btn-group btn-group-sm';

        wrap.appendChild(left);
        wrap.appendChild(middle);
        wrap.appendChild(right);

        this.controls = wrap;
        this.sizeSelect = select;
        this.summary = middle;
        this.buttons = right;

        // Sit the controls directly under whatever wraps the table — usually a
        // .table-responsive inside a card body — so they read as part of it.
        var anchor = this.table.parentElement;
        if (anchor && anchor.classList.contains('table-responsive')) {
            anchor.parentElement.insertBefore(wrap, anchor.nextSibling);
        } else if (anchor) {
            anchor.insertBefore(wrap, this.table.nextSibling);
        }
    };

    TablePager.prototype.pageButton = function (label, page, opts) {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn btn-outline-secondary' + (opts && opts.active ? ' active' : '');
        btn.textContent = label;
        if (opts && opts.disabled) {
            btn.disabled = true;
        } else {
            var self = this;
            btn.addEventListener('click', function () {
                self.page = page;
                self.refresh();
            });
        }
        return btn;
    };

    /** Page numbers around the current one, so a 60-page table stays usable. */
    TablePager.prototype.renderButtons = function (pages) {
        this.buttons.innerHTML = '';
        this.buttons.appendChild(this.pageButton('‹', this.page - 1, { disabled: this.page <= 1 }));

        var first = Math.max(1, this.page - 2);
        var last = Math.min(pages, first + 4);
        first = Math.max(1, Math.min(first, last - 4));

        if (first > 1) {
            this.buttons.appendChild(this.pageButton('1', 1, {}));
            if (first > 2) this.buttons.appendChild(this.pageButton('…', 1, { disabled: true }));
        }
        for (var p = first; p <= last; p++) {
            this.buttons.appendChild(this.pageButton(String(p), p, { active: p === this.page }));
        }
        if (last < pages) {
            if (last < pages - 1) this.buttons.appendChild(this.pageButton('…', 1, { disabled: true }));
            this.buttons.appendChild(this.pageButton(String(pages), pages, {}));
        }

        this.buttons.appendChild(this.pageButton('›', this.page + 1, { disabled: this.page >= pages }));
    };

    TablePager.prototype.refresh = function () {
        var rows = this.dataRows();
        var total = rows.length;

        // Short table: leave it exactly as it was.
        if (total <= SIZES[0]) {
            rows.forEach(function (tr) { tr.style.display = ''; });
            if (this.controls) this.controls.classList.add('d-none');
            return;
        }

        if (!this.controls) this.buildControls();
        this.controls.classList.remove('d-none');
        this.sizeSelect.value = String(this.size);

        var pages = Math.max(1, Math.ceil(total / this.size));
        if (this.page > pages) this.page = pages;
        if (this.page < 1) this.page = 1;

        var start = (this.page - 1) * this.size;
        var end = Math.min(start + this.size, total);
        rows.forEach(function (tr, i) {
            tr.style.display = (i >= start && i < end) ? '' : 'none';
        });

        this.summary.textContent = 'Showing ' + (start + 1) + ' to ' + end + ' of ' + total;
        this.renderButtons(pages);
    };

    TablePager.prototype.watch = function () {
        var self = this;
        var queued = false;
        // Only childList: our own paging sets row.style, and reacting to that
        // would loop forever.
        new MutationObserver(function () {
            if (queued) return;
            queued = true;
            window.requestAnimationFrame(function () {
                queued = false;
                // A redraw is a new list; start at the top of it.
                self.page = 1;
                self.refresh();
            });
        }).observe(self.tbody, { childList: true });
        self.refresh();
    };

    function eligible(table) {
        if (table.hasAttribute('data-no-paginate')) return false;
        if (!table.tBodies.length) return false;
        if (table.closest('.modal')) return false;
        if (table.closest('[data-no-paginate]')) return false;
        return true;
    }

    function attachAll() {
        var tables = document.querySelectorAll('table');
        for (var i = 0; i < tables.length; i++) {
            if (!eligible(tables[i])) continue;
            new TablePager(tables[i]).watch();
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', attachAll);
    } else {
        attachAll();
    }

    window.TablePager = TablePager;
}());
