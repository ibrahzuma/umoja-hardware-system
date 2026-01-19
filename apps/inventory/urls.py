from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register(r'branches', views.BranchViewSet)
router.register(r'categories', views.CategoryViewSet)
router.register(r'products', views.ProductViewSet)
router.register(r'stocks', views.StockViewSet)
router.register(r'suppliers', views.SupplierViewSet)
router.register(r'purchases', views.PurchaseViewSet)
router.register(r'stock-adjustments', views.StockAdjustmentViewSet)
router.register(r'purchase-orders', views.PurchaseOrderViewSet)
router.register(r'trucks', views.TruckViewSet)
router.register(r'truck-allocations', views.TruckAllocationViewSet)
router.register(r'grns', views.GoodsReceivedNoteViewSet)
router.register(r'drivers', views.DriverViewSet)
router.register(r'truck-maintenance', views.TruckMaintenanceViewSet)

app_name = 'inventory'

urlpatterns = [
    path('', views.InventoryListView.as_view(), name='inventory_list'),
    path('management/', views.StockManagementView.as_view(), name='stock_management'),
    path('transfer/', views.InventoryTransferView.as_view(), name='inventory_transfer'),
    path('health/', views.InventoryHealthView.as_view(), name='inventory_health'),
    path('aging/', views.InventoryAgingView.as_view(), name='inventory_aging'),
    path('aging/', views.InventoryAgingView.as_view(), name='inventory_aging'),
    path('abc/', views.ABCAnalysisView.as_view(), name='abc_analysis'),
    path('profitability/', views.ProfitabilityReportView.as_view(), name='profitability_report'),
    path('stock/adjustment/', views.StockAdjustmentView.as_view(), name='stock_adjustment'),

    path('branches/', views.BranchListView.as_view(), name='branches_list'),
    path('branches/create/', views.BranchCreateView.as_view(), name='branch_create'),
    
    path('products/', views.ProductListView.as_view(), name='products_list'),
    path('products/create/', views.ProductCreateView.as_view(), name='product_create'),
    path('products/import/', views.ProductImportView.as_view(), name='product_import'),
    path('categories/', views.CategoryListView.as_view(), name='categories_list'),

    path('services/', views.ServicesListView.as_view(), name='services_list'),
    path('services/create/', views.ProductCreateView.as_view(), name='service_create'),

    path('suppliers/', views.SupplierListView.as_view(), name='supplier_list'),
    path('suppliers/manage/', views.SupplierListView.as_view(), name='manage_supplier'),
    
    path('purchases/', views.PurchaseListView.as_view(), name='purchase_list'),
    path('purchases/list/', views.PurchaseListView.as_view(), name='purchases_list'),
    path('purchases/create/', views.PurchaseCreateView.as_view(), name='purchase_create'),
    path('purchases/recent/', views.RecentPurchaseListView.as_view(), name='recent_purchases'),
    
    path('purchase-orders/', views.PurchaseOrderListView.as_view(), name='purchase_order_list'),
    path('purchase-orders/create/', views.PurchaseOrderCreateView.as_view(), name='purchase_order_create'),

    path('grns/', views.GRNListView.as_view(), name='grn_list'),
    path('grns/create/', views.GRNCreateView.as_view(), name='grn_create'),

    path('trucks/', views.TruckListView.as_view(), name='truck_list'),

    path('drivers/', views.DriverListView.as_view(), name='driver_list'),
    path('trucks/maintenance/', views.TruckMaintenanceView.as_view(), name='truck_maintenance'),

    path('import/template/', views.download_product_template, name='download_product_template'),
    path('api/', include(router.urls)),
]
