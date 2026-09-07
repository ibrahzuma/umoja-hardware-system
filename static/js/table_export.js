/**
 * Export a table to CSV, exactly as it stands on screen.
 *
 * Put `data-export="Petty cash"` on a <table> and a small Export bar appears
 * above it. What comes out is what the filters left behind — including the
 * rows paging has scrolled past, since somebody exporting wants the whole
 * filtered set, not the ten they can see.
 *
 * Group heading rows (a month, say) are exported as they appear rather than
 * dropped: in a cash book those headings are half the point.
 *
 * Deliberately client-side. These screens already hold every row they are
 * showing, so an export needs no round trip and no new endpoint to keep in
 * step with the filters.
 */
(function () {
    'use strict';

    /** A row that is a message, not data: one cell spanning the table. */
    function isPlaceholder(tr) {
        return tr.children.length === 1 && tr.children[0].hasAttribute('colspan');
    }

    function cellText(cell) {
        // Collapse the whitespace that comes from templated markup, and keep
        // sub-lines (a note under a badge) on one line, separated.
        return (cell.innerText || cell.textContent || '')
            .replace(/\s*\n\s*/g, ' — ')
            .replace(/\s+/g, ' ')
            .trim();
    }

    function quote(value) {
        return '"' + String(value == null ? '' : value).replace(/"/g, '""') + '"';
    }

    function rowsOf(table) {
        var out = [];
        var head = table.tHead;
        if (head) {
            for (var h = 0; h < head.rows.length; h++) {
                out.push(Array.prototype.map.call(head.rows[h].cells, cellText));
            }
        }
        var body = table.tBodies[0];
        if (body) {
            for (var i = 0; i < body.rows.length; i++) {
                var tr = body.rows[i];
                if (isPlaceholder(tr)) continue;
                out.push(Array.prototype.map.call(tr.cells, cellText));
            }
        }
        var foot = table.tFoot;
        if (foot) {
            for (var f = 0; f < foot.rows.length; f++) {
                out.push(Array.prototype.map.call(foot.rows[f].cells, cellText));
            }
        }
        return out;
    }

    function download(name, rows) {
        var lines = rows.map(function (cells) {
            return cells.map(quote).join(',');
        });
        // A BOM so Excel opens it as UTF-8 rather than mangling it, and CRLF
        // because that is what Excel expects in a CSV.
        var blob = new Blob(['﻿' + lines.join('\r\n')],
            { type: 'text/csv;charset=utf-8;' });
        var url = URL.createObjectURL(blob);
        var a = document.createElement('a');
        a.href = url;
        a.download = name;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    }

    function slug(label) {
        return String(label || 'export').toLowerCase().replace(/[^a-z0-9]+/g, '-')
            .replace(/^-|-$/g, '') || 'export';
    }

    function attach(table) {
        var label = table.getAttribute('data-export') || 'Export';

        var bar = document.createElement('div');
        bar.className = 'table-export d-flex justify-content-end gap-2 px-3 pt-2';

        var csv = document.createElement('button');
        csv.type = 'button';
        csv.className = 'btn btn-sm btn-outline-secondary';
        csv.innerHTML = '<i class="bi bi-filetype-csv"></i> Export CSV';
        csv.addEventListener('click', function () {
            var rows = rowsOf(table);
            if (rows.length <= 1) {
                csv.blur();
                return;
            }
            download(slug(label) + '-' + new Date().toISOString().slice(0, 10) + '.csv', rows);
        });

        var print = document.createElement('button');
        print.type = 'button';
        print.className = 'btn btn-sm btn-outline-secondary';
        print.innerHTML = '<i class="bi bi-printer"></i> Print';
        print.addEventListener('click', function () { window.print(); });

        bar.appendChild(csv);
        bar.appendChild(print);

        // Sit the bar above whatever wraps the table, so it reads as part of
        // the card rather than floating inside the scroll area.
        var anchor = table.parentElement;
        if (anchor && anchor.classList.contains('table-responsive')) {
            anchor.parentElement.insertBefore(bar, anchor);
        } else if (anchor) {
            anchor.insertBefore(bar, table);
        }
    }

    function attachAll() {
        var tables = document.querySelectorAll('table[data-export]');
        for (var i = 0; i < tables.length; i++) {
            attach(tables[i]);
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', attachAll);
    } else {
        attachAll();
    }

    window.TableExport = { rowsOf: rowsOf, download: download };
}());
