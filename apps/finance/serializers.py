from decimal import Decimal
from rest_framework import serializers
from django.db.models import Sum
from .models import (
    AccountingAuditLog, AccountingSettings, Currency, ExchangeRate, Expense, ExpenseCategory,
    FinancialYear, Income, Invoice, SupplierPayment, TaxPayment, PaymentReceipt,
    BankAccount, PettyCashTransaction, OtherPayment, SalesLedgerEntry,
    PettyCashRequest, LedgerAccount, Voucher, VoucherLine, VoucherAllocation,
    VoucherType, GeneralLedgerEntry,
)
from apps.sales.models import Sale

class ExpenseCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ExpenseCategory
        fields = '__all__'

class BankAccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = BankAccount
        fields = '__all__'

class ExpenseSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)
    bank_name = serializers.CharField(source='bank.name', read_only=True, default='')
    branch_name = serializers.CharField(source='branch.name', read_only=True, default='')

    class Meta:
        model = Expense
        fields = '__all__'

class IncomeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Income
        fields = '__all__'

class SupplierPaymentSerializer(serializers.ModelSerializer):
    supplier_name = serializers.CharField(source='supplier.name', read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)
    po_number = serializers.SerializerMethodField()
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    approved_by_name = serializers.CharField(source='approved_by.username', read_only=True, default='')
    is_editable = serializers.BooleanField(read_only=True)

    def get_po_number(self, obj):
        return f"PO #{obj.purchase_order_id}" if obj.purchase_order_id else ''

    class Meta:
        model = SupplierPayment
        fields = '__all__'
        # The order is what the payer picks; the supplier follows from it.
        # Status is not the payer's to set — it moves only through the Admin's
        # approve/reject actions on the viewset.
        extra_kwargs = {'supplier': {'required': False}}
        read_only_fields = ('status', 'approved_by', 'approved_at', 'decision_note',
                            'cashier_note', 'resubmitted_at', 'created_by')

    def validate(self, attrs):
        order = attrs.get('purchase_order')
        supplier = attrs.get('supplier')

        if order is not None:
            if order.supplier_id is None:
                raise serializers.ValidationError(
                    {'purchase_order': "That purchase order has no supplier on it."})
            if supplier is not None and supplier.pk != order.supplier_id:
                raise serializers.ValidationError(
                    {'supplier': "That supplier did not raise this purchase order."})
            attrs['supplier'] = order.supplier
        elif supplier is None and not self.partial:
            raise serializers.ValidationError(
                {'purchase_order': "Choose the purchase order this payment settles."})

        # Credit can only be spent down to zero, and only once: applications
        # already queued for approval have claimed their share of it.
        if attrs.get('from_credit') and self.instance is None:
            from .credit import spendable_credit
            supplier = attrs.get('supplier')
            spendable = spendable_credit(supplier.pk) if supplier else Decimal('0')
            amount = attrs.get('amount') or Decimal('0')
            if amount > spendable:
                raise serializers.ValidationError({'amount': (
                    f"{supplier} only holds {spendable} of ours"
                    + (" once payments already awaiting approval are counted." if spendable else ".")
                )})

        return attrs

class TaxPaymentSerializer(serializers.ModelSerializer):
    tax_type_display = serializers.CharField(source='get_tax_type_display', read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)

    class Meta:
        model = TaxPayment
        fields = '__all__'
        read_only_fields = ('created_by',)


class PaymentReceiptSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(read_only=True)
    issued_by_name = serializers.SerializerMethodField()
    created_by_name = serializers.CharField(source='created_by.username', read_only=True, default='')

    def get_issued_by_name(self, obj):
        u = obj.issued_by
        if not u:
            return ''
        return u.get_full_name() or u.username
    invoice_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    outstanding_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    fully_paid = serializers.BooleanField(read_only=True)

    class Meta:
        model = PaymentReceipt
        fields = [
            'id', 'invoice_number', 'amount_paid', 'payment_date', 'reference',
            'notes', 'receipt_image',
            'sale', 'customer', 'customer_name', 'invoice_amount',
            'outstanding_amount', 'fully_paid', 'issued_by', 'issued_by_name',
            'created_by', 'created_by_name', 'created_at',
        ]
        read_only_fields = (
            'sale', 'customer', 'customer_name', 'invoice_amount',
            'outstanding_amount', 'issued_by', 'created_by', 'created_at',
        )

    def validate_invoice_number(self, value):
        sale = Sale.objects.filter(invoice_number=value).first()
        if sale is None:
            raise serializers.ValidationError("No invoice found with that number.")
        self._sale = sale
        return value

    def create(self, validated_data):
        sale = getattr(self, '_sale', None)
        amount_paid = validated_data.get('amount_paid') or Decimal('0')

        if sale is not None:
            prior_paid = sale.payment_receipts.aggregate(total=Sum('amount_paid'))['total'] or Decimal('0')
            invoice_amount = sale.total_amount or Decimal('0')
            validated_data['sale'] = sale
            validated_data['customer'] = sale.customer
            validated_data['customer_name'] = sale.customer.name if sale.customer else sale.customer_name
            validated_data['invoice_amount'] = invoice_amount
            validated_data['outstanding_amount'] = invoice_amount - prior_paid - amount_paid
            validated_data['issued_by'] = sale.user

        return super().create(validated_data)


class PettyCashTransactionSerializer(serializers.ModelSerializer):
    entry_type_display = serializers.CharField(source='get_entry_type_display', read_only=True)
    category_name = serializers.CharField(source='category.name', read_only=True, default='')
    branch_name = serializers.CharField(source='branch.name', read_only=True, default='')
    bank_name = serializers.CharField(source='bank.name', read_only=True, default='')
    created_by_name = serializers.CharField(source='created_by.username', read_only=True, default='')

    class Meta:
        model = PettyCashTransaction
        fields = '__all__'
        read_only_fields = ('voucher_number', 'created_by', 'created_at')


class OtherPaymentSerializer(serializers.ModelSerializer):
    payment_type_display = serializers.CharField(source='get_payment_type_display', read_only=True)
    method_display = serializers.CharField(source='get_method_display', read_only=True)
    bank_name = serializers.CharField(source='bank.name', read_only=True, default='')
    branch_name = serializers.CharField(source='branch.name', read_only=True, default='')
    created_by_name = serializers.CharField(source='created_by.username', read_only=True, default='')

    class Meta:
        model = OtherPayment
        fields = '__all__'
        read_only_fields = ('created_by', 'created_at')


class SalesLedgerEntrySerializer(serializers.ModelSerializer):
    """The accountant's view of a sale. Everything that came off the sale is
    read-only here — the ledger reports the till, it does not edit it. Only the
    post/query actions on the viewset move `status`."""
    settlement_display = serializers.CharField(source='get_settlement_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    branch_name = serializers.CharField(source='branch.name', read_only=True, default='')
    sold_by_name = serializers.SerializerMethodField()
    posted_by_name = serializers.CharField(source='posted_by.username', read_only=True, default='')
    balance = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    voucher = serializers.SerializerMethodField()

    def get_voucher(self, obj):
        """The Sales voucher the Post button wrote into the General Ledger."""
        # Read off the prefetched vouchers, so a page of entries costs one query.
        live = [v for v in obj.vouchers.all()
                if v.status in ('posted', 'reversed') and v.reversal_of_id is None]
        v = max(live, key=lambda v: v.id) if live else None
        return {'id': v.id, 'number': v.number, 'status': v.status} if v else None

    def get_sold_by_name(self, obj):
        u = obj.sold_by
        if not u:
            return ''
        return u.get_full_name() or u.username

    class Meta:
        model = SalesLedgerEntry
        fields = '__all__'
        read_only_fields = tuple(
            f.name for f in SalesLedgerEntry._meta.fields if f.name != 'id'
        )


class PettyCashRequestSerializer(serializers.ModelSerializer):
    """A request for cash. Everything past `amount`, `purpose`, `category` and
    `branch` belongs to the people who act on it, so the requester cannot set
    it: status moves only through approve / reject / issue."""
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    requested_by_name = serializers.SerializerMethodField()
    approved_by_name = serializers.CharField(source='approved_by.username', read_only=True, default='')
    issued_by_name = serializers.CharField(source='issued_by.username', read_only=True, default='')
    category_name = serializers.CharField(source='category.name', read_only=True, default='')
    branch_name = serializers.CharField(source='branch.name', read_only=True, default='')
    voucher_number = serializers.CharField(source='transaction.voucher_number',
                                           read_only=True, default='')

    def get_requested_by_name(self, obj):
        u = obj.requested_by
        if not u:
            return ''
        return u.get_full_name() or u.username

    class Meta:
        model = PettyCashRequest
        fields = '__all__'
        read_only_fields = ('requested_by', 'status', 'approved_by', 'approved_at',
                            'decision_note', 'issued_by', 'issued_at', 'issue_note',
                            'transaction', 'created_at', 'updated_at')

    def validate_amount(self, value):
        if value is None or value <= 0:
            raise serializers.ValidationError("Ask for more than nothing.")
        return value


# ---------------------------------------------------------------------------
# General ledger
# ---------------------------------------------------------------------------

class LedgerAccountSerializer(serializers.ModelSerializer):
    kind_display = serializers.CharField(source='get_kind_display', read_only=True)
    ledger_kind = serializers.CharField(read_only=True)
    account_type_display = serializers.CharField(source='get_account_type_display',
                                                 read_only=True)
    parent_code = serializers.CharField(source='parent.code', read_only=True, default='')
    currency_code = serializers.CharField(read_only=True)
    is_money = serializers.BooleanField(read_only=True)
    is_postable = serializers.BooleanField(read_only=True)
    normal_side = serializers.CharField(read_only=True)
    level = serializers.IntegerField(read_only=True)
    # Filled in by the viewset from one aggregate, not one query per row.
    balance = serializers.SerializerMethodField()
    linked_to = serializers.SerializerMethodField()

    class Meta:
        model = LedgerAccount
        fields = [
            'id', 'code', 'name', 'kind', 'kind_display', 'ledger_kind', 'is_money',
            'normal_side', 'account_type', 'account_type_display', 'category',
            'parent', 'parent_code', 'is_group', 'is_customer_control', 'is_supplier_control',
            'is_postable', 'vat_kind', 'currency', 'currency_code', 'level',
            'bank_account', 'customer', 'supplier', 'linked_to',
            'opening_balance', 'opening_side', 'is_active', 'notes', 'created_at', 'balance',
        ]
        read_only_fields = ['bank_account', 'customer', 'supplier', 'created_at']
        extra_kwargs = {
            'code': {'required': False, 'allow_blank': True},
            'account_type': {'required': False, 'allow_blank': True},
            'category': {'required': False, 'allow_blank': True},
        }

    def get_balance(self, obj):
        balances = self.context.get('balances') or {}
        signed = balances.get(obj.id)
        if signed is None:
            return None
        return str(signed if obj.normal_side == 'debit' else -signed)

    def get_linked_to(self, obj):
        if obj.bank_account_id:
            return 'bank'
        if obj.customer_id:
            return 'customer'
        if obj.supplier_id:
            return 'supplier'
        return ''

    def validate(self, attrs):
        # A linked ledger's kind is fixed by what it is linked to.
        instance = self.instance
        if instance is not None and 'kind' in attrs and attrs['kind'] != instance.kind:
            if instance.bank_account_id or instance.customer_id or instance.supplier_id:
                raise serializers.ValidationError(
                    {'kind': 'This ledger is linked to a bank account, customer or supplier; '
                             'its kind cannot change.'})
        if attrs.get('opening_balance', 0) < 0:
            raise serializers.ValidationError({'opening_balance': 'Use the side, not a minus sign.'})
        return attrs


class VoucherAllocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = VoucherAllocation
        fields = ['id', 'invoice', 'sale', 'purchase_order', 'voucher', 'reference',
                  'amount', 'notes']


class VoucherLineSerializer(serializers.ModelSerializer):
    account_code = serializers.CharField(source='account.code', read_only=True)
    account_name = serializers.CharField(source='account.name', read_only=True)
    account_kind = serializers.CharField(source='account.kind', read_only=True)
    allocations = VoucherAllocationSerializer(many=True, read_only=True)

    class Meta:
        model = VoucherLine
        fields = ['id', 'account', 'account_code', 'account_name', 'account_kind',
                  'side', 'amount', 'base_amount', 'narration', 'position', 'allocations']


class VoucherSerializer(serializers.ModelSerializer):
    """Read shape. Posting goes through `vouchers.post_voucher`, which takes the
    raw lines — see VoucherViewSet.create."""
    voucher_type_display = serializers.CharField(source='get_voucher_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    payment_status_display = serializers.CharField(source='get_payment_status_display', read_only=True)
    customer_name = serializers.CharField(source='customer.name', read_only=True, default='')
    supplier_name = serializers.CharField(source='supplier.name', read_only=True, default='')
    party_name = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()
    cancelled_by_name = serializers.SerializerMethodField()
    posted_by_name = serializers.SerializerMethodField()
    financial_year_code = serializers.CharField(source='financial_year.code', read_only=True,
                                                default='')
    currency_code = serializers.CharField(read_only=True)
    is_foreign_currency = serializers.BooleanField(read_only=True)
    difference = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    is_balanced = serializers.BooleanField(read_only=True)
    reversal_of_number = serializers.CharField(source='reversal_of.number', read_only=True,
                                               default='')
    lines = VoucherLineSerializer(many=True, read_only=True)
    accounts_summary = serializers.SerializerMethodField()
    invoice_status = serializers.SerializerMethodField()
    invoice_outstanding = serializers.SerializerMethodField()

    class Meta:
        model = Voucher
        fields = [
            'id', 'number', 'sequence_number', 'voucher_type', 'voucher_type_display',
            'date', 'description', 'reference',
            'total', 'total_debit', 'total_credit', 'base_total_debit', 'base_total_credit',
            'difference', 'is_balanced', 'status', 'status_display',
            'financial_year', 'financial_year_code',
            'currency', 'currency_code', 'exchange_rate', 'is_foreign_currency',
            'customer', 'customer_name', 'supplier', 'supplier_name', 'party_name',
            'invoice_number', 'efd_number', 'payment_status', 'payment_status_display',
            'net_amount', 'vat_amount', 'vat_account',
            'reversal_of', 'reversal_of_number',
            'created_by', 'created_by_name', 'created_at',
            'posted_by', 'posted_by_name', 'posted_at',
            'cancelled_by', 'cancelled_by_name', 'cancelled_at', 'cancel_reason',
            'lines', 'accounts_summary', 'invoice_status', 'invoice_outstanding',
        ]

    def _name(self, user):
        if not user:
            return ''
        return user.get_full_name() or user.username

    def get_created_by_name(self, obj):
        return self._name(obj.created_by)

    def get_party_name(self, obj):
        party = obj.party
        return party.name if party else ''

    def get_cancelled_by_name(self, obj):
        return self._name(obj.cancelled_by)

    def get_posted_by_name(self, obj):
        return self._name(obj.posted_by)

    def _invoice(self, obj):
        """The invoice-register row this voucher raised, if any."""
        return getattr(obj, 'invoice', None)

    def get_invoice_status(self, obj):
        invoice = self._invoice(obj)
        return invoice.status if invoice else ''

    def get_invoice_outstanding(self, obj):
        invoice = self._invoice(obj)
        return str(invoice.outstanding_amount) if invoice else None

    def get_accounts_summary(self, obj):
        """'Dr CRDB Main / Cr Kibo Traders' for the register listing."""
        lines = list(obj.lines.all())
        debits = [l.account.name for l in lines if l.side == 'debit']
        credits = [l.account.name for l in lines if l.side == 'credit']
        return {'debit': debits, 'credit': credits}


class GeneralLedgerEntrySerializer(serializers.ModelSerializer):
    account_code = serializers.CharField(source='account.code', read_only=True)
    account_name = serializers.CharField(source='account.name', read_only=True)
    account_kind = serializers.CharField(source='account.kind', read_only=True)

    class Meta:
        model = GeneralLedgerEntry
        fields = ['id', 'voucher', 'account', 'account_code', 'account_name', 'account_kind',
                  'financial_year', 'date', 'voucher_type', 'voucher_number', 'reference',
                  'description', 'debit', 'credit',
                  'currency', 'exchange_rate', 'foreign_debit', 'foreign_credit',
                  'customer', 'supplier']


# ---------------------------------------------------------------------------
# Accounting configuration and the invoice register
# ---------------------------------------------------------------------------

class CurrencySerializer(serializers.ModelSerializer):
    display_symbol = serializers.CharField(read_only=True)

    class Meta:
        model = Currency
        fields = ['id', 'code', 'name', 'symbol', 'display_symbol', 'is_base', 'is_active']


class ExchangeRateSerializer(serializers.ModelSerializer):
    currency_code = serializers.CharField(source='currency.code', read_only=True)

    class Meta:
        model = ExchangeRate
        fields = ['id', 'currency', 'currency_code', 'rate_date', 'rate', 'note',
                  'created_by', 'created_at']
        read_only_fields = ['created_by', 'created_at']


class FinancialYearSerializer(serializers.ModelSerializer):
    class Meta:
        model = FinancialYear
        fields = ['id', 'code', 'name', 'start_date', 'end_date', 'is_active', 'is_closed',
                  'lock_date', 'notes', 'created_at']
        read_only_fields = ['created_at']


class VoucherTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = VoucherType
        fields = ['id', 'code', 'name', 'prefix', 'description', 'is_active']
        read_only_fields = ['code']


class AccountingSettingsSerializer(serializers.ModelSerializer):
    currency_code = serializers.CharField(read_only=True)
    currency_symbol = serializers.CharField(read_only=True)

    class Meta:
        model = AccountingSettings
        fields = [
            'id', 'financial_year_start', 'financial_year_end',
            'voucher_number_padding', 'include_financial_year_in_number',
            'reset_sequence_each_year', 'number_separator',
            'efd_enabled', 'efd_serial_number', 'efd_duplicate_policy', 'default_vat_rate',
            'period_lock_date', 'allow_backdated_entries',
            'default_cash_account', 'default_bank_account', 'default_sales_account',
            'default_purchase_account', 'default_output_vat_account',
            'default_input_vat_account',
            'currency_code', 'currency_symbol', 'updated_at', 'updated_by',
        ]
        read_only_fields = ['updated_at', 'updated_by']


class InvoiceSerializer(serializers.ModelSerializer):
    """The books' own invoice register. Read-only over the API: a row is
    raised by posting a Sales or Purchase voucher, never typed in here."""
    kind_display = serializers.CharField(source='get_kind_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    party_name = serializers.SerializerMethodField()
    allocated_amount = serializers.SerializerMethodField()
    outstanding_amount = serializers.SerializerMethodField()
    voucher_number = serializers.CharField(source='voucher.number', read_only=True, default='')

    class Meta:
        model = Invoice
        fields = ['id', 'kind', 'kind_display', 'customer', 'supplier', 'party_name',
                  'voucher', 'voucher_number', 'invoice_number', 'efd_number',
                  'invoice_date', 'due_date', 'original_amount',
                  'allocated_amount', 'outstanding_amount',
                  'status', 'status_display', 'description', 'created_at']

    def get_party_name(self, obj):
        party = obj.party
        return party.name if party else ''

    def get_allocated_amount(self, obj):
        return str(obj.allocated_amount)

    def get_outstanding_amount(self, obj):
        return str(obj.outstanding_amount)


class AccountingAuditLogSerializer(serializers.ModelSerializer):
    action_display = serializers.CharField(source='get_action_display', read_only=True)

    class Meta:
        model = AccountingAuditLog
        fields = ['id', 'user', 'username', 'action', 'action_display',
                  'voucher', 'voucher_number', 'model_name', 'object_repr',
                  'previous_status', 'new_status', 'description', 'ip_address', 'timestamp']
