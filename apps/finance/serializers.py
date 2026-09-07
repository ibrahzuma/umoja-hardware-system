from decimal import Decimal
from rest_framework import serializers
from django.db.models import Sum
from .models import (
    Expense, ExpenseCategory, Income, SupplierPayment, TaxPayment, PaymentReceipt,
    BankAccount, PettyCashTransaction, OtherPayment,
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

    def get_po_number(self, obj):
        return f"PO #{obj.purchase_order_id}" if obj.purchase_order_id else ''

    class Meta:
        model = SupplierPayment
        fields = '__all__'
        # The order is what the payer picks; the supplier follows from it.
        extra_kwargs = {'supplier': {'required': False}}

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
