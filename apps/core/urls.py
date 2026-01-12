from django.urls import path
from .views import DashboardView, GenericListView

from apps.inventory.views import download_product_template

urlpatterns = [
    # Dashboard
    path('', DashboardView.as_view(), name='dashboard'),
    path('dashboard/', DashboardView.as_view(), name='dashboard_alias'),
    
    # Reports
    path('reports/', DashboardView.as_view(), name='reports_list'),

    # Settings
    path('settings/', DashboardView.as_view(), name='settings_list'),
]
