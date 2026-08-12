from django.urls import path

from . import views

app_name = 'crm'

urlpatterns = [
    path('', views.CrmListView.as_view(), name='customer_records'),
    path('export/', views.CrmExportView.as_view(), name='export'),
    path('import-template/', views.CrmImportTemplateView.as_view(), name='import_template'),
]
