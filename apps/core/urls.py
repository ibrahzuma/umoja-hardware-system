from django.urls import path
from .views import DashboardView, SettingsView, download_app
from .views_sms import SmsNotificationsView
from apps.inventory.views import download_product_template

urlpatterns = [
    # Dashboard
    path('', DashboardView.as_view(), name='dashboard'),
    path('dashboard/', DashboardView.as_view(), name='dashboard_alias'),
    
    # Reports
    path('reports/', DashboardView.as_view(), name='reports_list'),

    # Settings
    # Settings
    path('settings/', SettingsView.as_view(), name='settings_list'),
    path('sms-notifications/', SmsNotificationsView.as_view(), name='sms_notifications'),
    
    path('download-product-template/', download_product_template, name='download_product_template'),
    path('download-app/', download_app, name='download_app'),
]
