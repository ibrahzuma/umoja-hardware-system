from django.contrib import admin

from .models import CustomerRecord


@admin.register(CustomerRecord)
class CustomerRecordAdmin(admin.ModelAdmin):
    list_display = ('date', 'receipt_number', 'efd_receipt_number', 'customer_name', 'tin', 'sales_amount')
    list_filter = ('date',)
    search_fields = ('customer_name', 'tin', 'receipt_number', 'efd_receipt_number')
    date_hierarchy = 'date'
