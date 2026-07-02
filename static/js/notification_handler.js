document.addEventListener('DOMContentLoaded', function () {
    // Initialize Socket Service
    // Endpoint 'stock' maps to ws/stock/ which StockConsumer listens to
    const socketService = new SocketService('stock');
    socketService.connect();

    // Handle Sales Notifications
    socketService.on('sales_notification', function (data) {
        showToast(data.data.title, data.data.body, 'success');
    });

    // Handle Low Stock Alerts (existing feature, ensuring it works)
    socketService.on('low_stock_alert', function (data) {
        showToast('Low Stock Warning', data.data.message, 'warning');
    });

    // --- Persistent notification inbox (bell dropdown) ---
    if (document.getElementById('notifBell')) {
        loadNotifications();
        setInterval(loadNotifications, 45000);   // refresh unread every 45s
        document.getElementById('notifBell').addEventListener('click', loadNotifications);
        const markAll = document.getElementById('notifMarkAll');
        if (markAll) markAll.addEventListener('click', markAllRead);
    }
});

const NOTIF_LEVEL_ICON = { info: 'bi-info-circle text-primary', success: 'bi-check-circle text-success', warning: 'bi-exclamation-triangle text-warning', danger: 'bi-x-octagon text-danger' };

async function loadNotifications() {
    try {
        const res = await fetch('/api/notifications/');
        if (!res.ok) return;
        const data = await res.json();
        renderNotifications(Array.isArray(data) ? data : (data.results || []));
    } catch (e) { /* silent */ }
}

function renderNotifications(list) {
    const badge = document.getElementById('notification-badge');
    const box = document.getElementById('notifList');
    const unread = list.filter(n => !n.is_read).length;

    if (badge) {
        badge.innerText = unread > 9 ? '9+' : unread;
        badge.style.display = unread > 0 ? '' : 'none';
    }
    if (!box) return;

    if (!list.length) {
        box.innerHTML = '<div class="text-center text-muted py-4 small">No notifications yet.</div>';
        return;
    }

    box.innerHTML = list.slice(0, 15).map(n => {
        const icon = NOTIF_LEVEL_ICON[n.level] || NOTIF_LEVEL_ICON.info;
        return `<a href="${n.url || 'javascript:void(0)'}" class="dropdown-item d-flex gap-2 py-2 border-bottom ${n.is_read ? '' : 'bg-light'}"
                   style="white-space: normal;" onclick="markRead(${n.id})">
                    <i class="bi ${icon} mt-1"></i>
                    <div class="flex-grow-1">
                        <div class="fw-semibold small">${escapeHtml(n.title)}</div>
                        <div class="text-muted" style="font-size:.78rem;">${escapeHtml(n.message || '')}</div>
                        <div class="text-muted" style="font-size:.7rem;">${n.time_ago || ''}</div>
                    </div>
                </a>`;
    }).join('');
}

async function markRead(id) {
    try {
        await fetch(`/api/notifications/${id}/mark_read/`, { method: 'POST', headers: { 'X-CSRFToken': CSRF_TOKEN } });
    } catch (e) { /* navigation proceeds regardless */ }
}

async function markAllRead() {
    try {
        await fetch('/api/notifications/mark_all_read/', { method: 'POST', headers: { 'X-CSRFToken': CSRF_TOKEN } });
        loadNotifications();
    } catch (e) { /* silent */ }
}

function escapeHtml(s) {
    return (s || '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function showToast(title, message, type = 'info') {
    // Create toast container if not exists
    let toastContainer = document.getElementById('toast-container');
    if (!toastContainer) {
        toastContainer = document.createElement('div');
        toastContainer.id = 'toast-container';
        toastContainer.className = 'toast-container position-fixed bottom-0 end-0 p-3';
        document.body.appendChild(toastContainer);
    }

    // Determine color
    let bgClass = 'text-bg-primary';
    if (type === 'success') bgClass = 'text-bg-success';
    if (type === 'warning') bgClass = 'text-bg-warning';
    if (type === 'danger') bgClass = 'text-bg-danger';

    // Create Toast HTML
    const toastId = 'toast-' + Date.now();
    const toastHtml = `
        <div id="${toastId}" class="toast align-items-center ${bgClass} border-0" role="alert" aria-live="assertive" aria-atomic="true">
            <div class="d-flex">
                <div class="toast-body">
                    <strong>${title}</strong><br>
                    ${message}
                </div>
                <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>
            </div>
        </div>
    `;

    // Append to container
    toastContainer.insertAdjacentHTML('beforeend', toastHtml);

    // Initialize and show
    const toastElement = document.getElementById(toastId);
    const toast = new bootstrap.Toast(toastElement, { delay: 5000 });
    toast.show();
}
