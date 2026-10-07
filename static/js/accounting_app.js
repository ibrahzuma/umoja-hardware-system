/* The accounting area's global UI behaviour.

   This is the Pradeep accounting system's `static/js/app.js`, carried over
   unchanged so the books' screens behave exactly as they do there: Django
   messages as toasts, the sidebar toggle on small screens, `data-confirm`
   dialogs, auto-print on `?print=1`, tooltips, and the `AccountingUI`
   helpers the voucher form builds on.

   Loaded only by `finance/accounting_base.html`, so it never meets the rest
   of the system's JavaScript. */

/* Global UI behaviour: toasts, sidebar toggle, confirm dialogs, number formatting helpers. */
(function () {
  'use strict';

  document.addEventListener('DOMContentLoaded', function () {
    // Toast notifications for Django messages
    document.querySelectorAll('#toastContainer .toast').forEach(function (el) {
      var toast = new bootstrap.Toast(el);
      toast.show();
    });

    // Sidebar toggle on small screens
    var toggle = document.getElementById('sidebarToggle');
    var sidebar = document.getElementById('sidebar');
    if (toggle && sidebar) {
      toggle.addEventListener('click', function () { sidebar.classList.toggle('show'); });
      document.addEventListener('click', function (e) {
        if (sidebar.classList.contains('show') && !sidebar.contains(e.target) && !toggle.contains(e.target)) {
          sidebar.classList.remove('show');
        }
      });
    }

    // Generic confirm on forms/buttons with data-confirm
    document.querySelectorAll('[data-confirm]').forEach(function (el) {
      el.addEventListener('submit', function (e) {
        if (!window.confirm(el.getAttribute('data-confirm'))) { e.preventDefault(); }
      });
    });

    // Auto-print pages opened with ?print=1
    if (document.body.classList.contains('auto-print')) {
      setTimeout(function () { window.print(); }, 300);
    }

    // Bootstrap tooltips
    document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(function (el) { new bootstrap.Tooltip(el); });
  });

  window.AccountingUI = {
    formatMoney: function (value) {
      var n = Number(value || 0);
      return n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    },
    parseAmount: function (value) {
      if (value === null || value === undefined) { return 0; }
      var cleaned = String(value).replace(/,/g, '').trim();
      var n = parseFloat(cleaned);
      return isNaN(n) ? 0 : Math.round(n * 100) / 100;
    },
    csrfToken: function () {
      var input = document.querySelector('input[name="csrfmiddlewaretoken"]');
      if (input) { return input.value; }
      var m = document.cookie.match(/csrftoken=([^;]+)/);
      return m ? m[1] : '';
    },
    toast: function (message, level) {
      var container = document.getElementById('toastContainer');
      if (!container) { return; }
      var el = document.createElement('div');
      el.className = 'toast align-items-center text-bg-' + (level || 'info') + ' border-0';
      el.setAttribute('role', 'alert');
      el.innerHTML = '<div class="d-flex"><div class="toast-body"></div>' +
        '<button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button></div>';
      el.querySelector('.toast-body').textContent = message;
      container.appendChild(el);
      new bootstrap.Toast(el, { delay: 6000 }).show();
    }
  };
})();
