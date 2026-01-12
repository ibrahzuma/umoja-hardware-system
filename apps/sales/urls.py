from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

app_name = 'sales'

router = DefaultRouter()
# Register any sales viewsets here if not already registered in main urls
# But usually we keep APIs in main urls for simplicity, or here if we want modularity.
# The user's initial state showed sales viewsets in sms_project/urls.py
# So here we just add the template views.

urlpatterns = [
    path('pos/', views.POSView.as_view(), name='pos'),
    path('history/', views.SaleListView.as_view(), name='sale_list'),
    path('credit/', views.CreditSaleListView.as_view(), name='credit_sales'),
    path('recent/', views.RecentSaleListView.as_view(), name='recent_sales'),
    path('customers/', views.CustomerListView.as_view(), name='customers_list'),
    path('customers/create/', views.CustomerCreateView.as_view(), name='customer_create'),
    path('customers/import/', views.CustomerImportView.as_view(), name='customer_import'),
    path('customers/template/', views.download_customer_template, name='download_customer_template'),
    path('orders/', views.OrderManagementView.as_view(), name='order_management'),
    path('dispatch/', views.DispatchDashboardView.as_view(), name='dispatch_dashboard'),
    path('vehicles/', views.VehicleManagementView.as_view(), name='vehicle_list'),
    path('reports/commissions/', views.CommissionReportView.as_view(), name='commission_report'),
]

