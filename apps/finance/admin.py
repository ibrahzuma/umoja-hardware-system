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


# --- General ledger ----------------------------------------------------------
# Read-mostly: vouchers are posted through the API and cancelled, never edited.

from .models import LedgerAccount, Voucher, VoucherLine, VoucherAllocation, GeneralLedgerEntry


@admin.register(LedgerAccount)
class LedgerAccountAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'kind', 'opening_balance', 'opening_side', 'is_active')
    list_filter = ('kind', 'is_active')
    search_fields = ('code', 'name')
    readonly_fields = ('bank_account', 'customer', 'supplier', 'created_at')


class VoucherLineInline(admin.TabularInline):
    model = VoucherLine
    extra = 0
    can_delete = False
    readonly_fields = ('account', 'side', 'amount', 'narration', 'position')


@admin.register(Voucher)
class VoucherAdmin(admin.ModelAdmin):
    list_display = ('number', 'voucher_type', 'date', 'total', 'status', 'created_by', 'created_at')
    list_filter = ('voucher_type', 'status', 'date')
    search_fields = ('number', 'description')
    readonly_fields = ('number', 'voucher_type', 'date', 'total', 'status', 'created_by', 'created_at',
                       'cancelled_by', 'cancelled_at', 'cancel_reason')
    inlines = [VoucherLineInline]
    date_hierarchy = 'date'

    def has_add_permission(self, request):
        return False


@admin.register(GeneralLedgerEntry)
class GeneralLedgerEntryAdmin(admin.ModelAdmin):
    list_display = ('date', 'voucher_number', 'account', 'debit', 'credit', 'description')
    list_filter = ('voucher_type', 'account__kind', 'date')
    search_fields = ('voucher_number', 'description', 'account__name', 'account__code')
    date_hierarchy = 'date'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
