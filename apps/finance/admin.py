from django.contrib import admin
from .models import PaymentReceipt, BankAccount, PettyCashTransaction, OtherPayment


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


@admin.register(PettyCashTransaction)
class PettyCashTransactionAdmin(admin.ModelAdmin):
    list_display = ('voucher_number', 'date', 'entry_type', 'amount', 'payee', 'category', 'branch', 'created_by')
    list_filter = ('entry_type', 'branch', 'category', 'date')
    search_fields = ('voucher_number', 'description', 'payee', 'reference')
    readonly_fields = ('voucher_number', 'created_at')
    date_hierarchy = 'date'


@admin.register(OtherPayment)
class OtherPaymentAdmin(admin.ModelAdmin):
    list_display = ('payment_date', 'payee', 'payment_type', 'amount', 'method', 'bank', 'branch', 'created_by')
    list_filter = ('payment_type', 'method', 'branch', 'payment_date')
    search_fields = ('payee', 'reference', 'description')
    readonly_fields = ('created_at',)
    date_hierarchy = 'payment_date'
