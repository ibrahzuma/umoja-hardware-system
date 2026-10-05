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
    list_display = ('number', 'voucher_type', 'date', 'invoice_number', 'efd_number', 'payment_status',
                    'total', 'status', 'created_by', 'created_at')
    list_filter = ('voucher_type', 'status', 'payment_status', 'date')
    search_fields = ('number', 'description', 'invoice_number', 'efd_number', 'customer__name', 'supplier__name')
    readonly_fields = ('number', 'voucher_type', 'date', 'total', 'status', 'created_by', 'created_at',
                       'customer', 'supplier', 'invoice_number', 'efd_number', 'payment_status',
                       'net_amount', 'vat_amount', 'cancelled_by', 'cancelled_at', 'cancel_reason')
    inlines = [VoucherLineInline]
    date_hierarchy = 'date'

    def has_add_permission(self, request):
        return False


@admin.register(GeneralLedgerEntry)
class GeneralLedgerEntryAdmin(admin.ModelAdmin):
    list_display = ('date', 'voucher_number', 'reference', 'account', 'debit', 'credit', 'description')
    list_filter = ('voucher_type', 'account__kind', 'date')
    search_fields = ('voucher_number', 'reference', 'description', 'account__name', 'account__code')
    date_hierarchy = 'date'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


# ---------------------------------------------------------------------------
# The accounting configuration, the invoice register and the audit trail.
#
# The obscured admin site is for low-level maintenance, not daily work — every
# one of these has a proper screen under /finance/accounting/. What is useful
# here is being able to *see* and search them, so the register and the trail
# are read-only, as they are everywhere else.
# ---------------------------------------------------------------------------

from .models import (
    AccountingAuditLog, AccountingSettings, Currency, ExchangeRate, FinancialYear, Invoice,
    VoucherNumberSequence, VoucherType,
)


@admin.register(Currency)
class CurrencyAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'symbol', 'is_base', 'is_active')
    list_filter = ('is_base', 'is_active')
    search_fields = ('code', 'name')


@admin.register(ExchangeRate)
class ExchangeRateAdmin(admin.ModelAdmin):
    list_display = ('currency', 'rate_date', 'rate', 'note', 'created_by')
    list_filter = ('currency',)
    date_hierarchy = 'rate_date'


@admin.register(FinancialYear)
class FinancialYearAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'start_date', 'end_date', 'is_active', 'is_closed',
                    'lock_date')
    list_filter = ('is_active', 'is_closed')
    search_fields = ('code', 'name')


@admin.register(AccountingSettings)
class AccountingSettingsAdmin(admin.ModelAdmin):
    """A singleton — one row, and the model's `save` refuses a second."""
    list_display = ('__str__', 'financial_year_start', 'financial_year_end',
                    'efd_duplicate_policy', 'period_lock_date', 'updated_at')

    def has_add_permission(self, request):
        return not AccountingSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(VoucherType)
class VoucherTypeAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'prefix', 'is_active')
    readonly_fields = ('code',)

    def has_add_permission(self, request):
        return False       # the six types are fixed by the engine

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(VoucherNumberSequence)
class VoucherNumberSequenceAdmin(admin.ModelAdmin):
    """Shown so a stuck sequence can be diagnosed. Editing one by hand is a
    last resort — the unique constraint on `Voucher.number` is what actually
    prevents a collision."""
    list_display = ('voucher_type', 'financial_year', 'last_number', 'updated_at')
    list_filter = ('voucher_type', 'financial_year')


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ('invoice_number', 'kind', 'party', 'invoice_date', 'original_amount',
                    'status', 'voucher')
    list_filter = ('kind', 'status', 'invoice_date')
    search_fields = ('invoice_number', 'efd_number', 'customer__name', 'supplier__name')
    date_hierarchy = 'invoice_date'
    readonly_fields = ('kind', 'customer', 'supplier', 'voucher', 'invoice_number',
                       'efd_number', 'invoice_date', 'original_amount', 'created_at')

    def has_add_permission(self, request):
        return False       # a row is raised by posting a voucher

    @admin.display(description='Party')
    def party(self, obj):
        party = obj.party
        return party.name if party else '—'


@admin.register(AccountingAuditLog)
class AccountingAuditLogAdmin(admin.ModelAdmin):
    """Read-only, as an audit trail has to be."""
    list_display = ('timestamp', 'username', 'action', 'voucher_number', 'object_repr',
                    'ip_address')
    list_filter = ('action', 'timestamp')
    search_fields = ('username', 'voucher_number', 'object_repr', 'description')
    date_hierarchy = 'timestamp'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
