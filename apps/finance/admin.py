from django.contrib import admin
from .models import PaymentReceipt, BankAccount


@admin.register(BankAccount)
class BankAccountAdmin(admin.ModelAdmin):
    list_display = ('name', 'account_number', 'branch', 'is_active', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('name', 'account_number', 'branch')


@admin.register(PaymentReceipt)
class PaymentReceiptAdmin(admin.ModelAdmin):
    list_display = ('invoice_number', 'customer_name', 'amount_paid', 'outstanding_amount', 'issued_by', 'created_by', 'created_at')
    list_filter = ('payment_date', 'created_at')
    search_fields = ('invoice_number', 'customer_name', 'reference')
    readonly_fields = ('invoice_amount', 'outstanding_amount', 'customer', 'issued_by', 'created_by', 'created_at')
    date_hierarchy = 'payment_date'
