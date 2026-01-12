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
});

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
