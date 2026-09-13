from decimal import Decimal
from rest_framework import serializers
from django.db.models import Sum
from .models import (
    Expense, ExpenseCategory, Income, SupplierPayment, TaxPayment, PaymentReceipt,
    BankAccount, PettyCashTransaction, OtherPayment, SalesLedgerEntry,
    PettyCashRequest, LedgerAccount, Voucher, VoucherLine, VoucherAllocation,
    GeneralLedgerEntry,
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
    is_money = serializers.BooleanField(read_only=True)
    normal_side = serializers.CharField(read_only=True)
    # Filled in by the viewset from one aggregate, not one query per row.
    balance = serializers.SerializerMethodField()
    linked_to = serializers.SerializerMethodField()

    class Meta:
        model = LedgerAccount
        fields = [
            'id', 'code', 'name', 'kind', 'kind_display', 'is_money', 'normal_side',
            'bank_account', 'customer', 'supplier', 'linked_to',
            'opening_balance', 'opening_side', 'is_active', 'notes', 'created_at', 'balance',
        ]
        read_only_fields = ['bank_account', 'customer', 'supplier', 'created_at']
        extra_kwargs = {'code': {'required': False, 'allow_blank': True}}

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
        fields = ['id', 'sale', 'purchase_order', 'reference', 'amount']


class VoucherLineSerializer(serializers.ModelSerializer):
    account_code = serializers.CharField(source='account.code', read_only=True)
    account_name = serializers.CharField(source='account.name', read_only=True)
    account_kind = serializers.CharField(source='account.kind', read_only=True)
    allocations = VoucherAllocationSerializer(many=True, read_only=True)

    class Meta:
        model = VoucherLine
        fields = ['id', 'account', 'account_code', 'account_name', 'account_kind',
                  'side', 'amount', 'narration', 'position', 'allocations']


class VoucherSerializer(serializers.ModelSerializer):
    """Read shape. Posting goes through `vouchers.post_voucher`, which takes the
    raw lines — see VoucherViewSet.create."""
    voucher_type_display = serializers.CharField(source='get_voucher_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    created_by_name = serializers.SerializerMethodField()
    cancelled_by_name = serializers.SerializerMethodField()
    lines = VoucherLineSerializer(many=True, read_only=True)
    accounts_summary = serializers.SerializerMethodField()

    class Meta:
        model = Voucher
        fields = [
            'id', 'number', 'voucher_type', 'voucher_type_display', 'date', 'description',
            'total', 'status', 'status_display', 'created_by', 'created_by_name', 'created_at',
            'cancelled_by', 'cancelled_by_name', 'cancelled_at', 'cancel_reason',
            'lines', 'accounts_summary',
        ]

    def _name(self, user):
        if not user:
            return ''
        return user.get_full_name() or user.username

    def get_created_by_name(self, obj):
        return self._name(obj.created_by)

    def get_cancelled_by_name(self, obj):
        return self._name(obj.cancelled_by)

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
                  'date', 'voucher_type', 'voucher_number', 'description', 'debit', 'credit']
