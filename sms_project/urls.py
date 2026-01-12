from django.contrib import admin
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from apps.inventory.views import BranchViewSet, CategoryViewSet, ProductViewSet, StockViewSet, SupplierViewSet, PurchaseViewSet, StockTransferViewSet
from apps.sales.views import SaleViewSet, SaleItemViewSet, TransactionViewSet, CustomerViewSet, VehicleViewSet
from apps.finance.views import ExpenseViewSet, ExpenseCategoryViewSet
from apps.users.views import UserViewSet, GroupViewSet

from apps.users.views import UserViewSet, GroupViewSet, PermissionViewSet

router = DefaultRouter()
router.register(r'branches', BranchViewSet)
router.register(r'categories', CategoryViewSet)
router.register(r'products', ProductViewSet)
router.register(r'stocks', StockViewSet)
router.register(r'suppliers', SupplierViewSet)
router.register(r'purchases', PurchaseViewSet)
router.register(r'transfers', StockTransferViewSet)
router.register(r'customers', CustomerViewSet)
router.register(r'sales', SaleViewSet)
router.register(r'vehicles', VehicleViewSet)
router.register(r'sale-items', SaleItemViewSet)
router.register(r'transactions', TransactionViewSet)
router.register(r'expenses', ExpenseViewSet)
router.register(r'expense-categories', ExpenseCategoryViewSet)
router.register(r'users', UserViewSet)
router.register(r'roles', GroupViewSet)
router.register(r'permissions', PermissionViewSet)

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include(router.urls)),
    path('inventory/', include('apps.inventory.urls', namespace='inventory')),
    path('sales/', include('apps.sales.urls', namespace='sales')),
    path('finance/', include('apps.finance.urls', namespace='finance')),
    path('', include('apps.core.urls')),
    path('', include('apps.users.urls')),
]

from django.conf import settings
from django.conf.urls.static import static

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
