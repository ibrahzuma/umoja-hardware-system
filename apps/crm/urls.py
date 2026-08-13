from django.urls import path

from . import views

app_name = 'crm'

urlpatterns = [
    path('', views.CrmListView.as_view(), name='customer_records'),
    path('customer/', views.CrmCustomerDetailView.as_view(), name='customer_detail'),
    path('export/', views.CrmExportView.as_view(), name='export'),
    path('report/', views.CrmReportView.as_view(), name='report'),
    path('customer/report/', views.CrmCustomerReportView.as_view(), name='customer_report'),
    path('import-template/', views.CrmImportTemplateView.as_view(), name='import_template'),
]
