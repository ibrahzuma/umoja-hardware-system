from rest_framework import serializers
from .models import Sale, SaleItem, Transaction, Customer, Vehicle, Quotation, QuotationItem, load_of
from apps.inventory.models import Product
from .stock_guard import check_lines


def money_account(value, field='deposit_to'):
    """The cash book or bank ledger a payment went into, or None if not given.

    Only a live, postable cash/bank ledger will do — anything else would put
    the money somewhere the cash book cannot see it."""
    if value in (None, ''):
        return None
    from apps.finance.models import LedgerAccount
    pk = getattr(value, 'pk', value)
    ledger = (LedgerAccount.objects
              .filter(pk=pk, kind__in=LedgerAccount.MONEY_KINDS, is_group=False, is_active=True)
              .first())
    if ledger is None:
        raise serializers.ValidationError({field: 'Pick a cash book or bank account.'})
    return ledger

class VehicleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Vehicle
        fields = '__all__'

class CustomerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Customer
        fields = '__all__'

class SaleItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    product_weight = serializers.DecimalField(source='product.unit_weight', max_digits=10, decimal_places=2,
                                              read_only=True, allow_null=True)
    product_unit = serializers.CharField(source='product.get_unit_display', read_only=True)
    
    class Meta:
        model = SaleItem
        fields = '__all__'
        read_only_fields = ('sale', 'subtotal') 

class SaleSerializer(serializers.ModelSerializer):
    items_response = SaleItemSerializer(source='items', many=True, read_only=True)
    items = serializers.ListField(child=serializers.DictField(), write_only=True)
    payment_details = serializers.DictField(write_only=True, required=False)
    
    amount_paid = serializers.SerializerMethodField()
    balance = serializers.SerializerMethodField()
    payment_status = serializers.SerializerMethodField()
    total_weight = serializers.SerializerMethodField()
    unweighed_items = serializers.SerializerMethodField()
    
    store_keeper_name = serializers.CharField(source='store_keeper.username', read_only=True)
    approved_by_name = serializers.CharField(source='approved_by.username', read_only=True)
    dispatch_manager_name = serializers.CharField(source='dispatch_manager.username', read_only=True)
    created_by_name = serializers.CharField(source='user.username', read_only=True)
    
    vehicle_details = VehicleSerializer(source='vehicle', read_only=True)
    godown_name = serializers.CharField(source='godown.name', read_only=True, default='')
    vehicle = serializers.PrimaryKeyRelatedField(
        queryset=Vehicle.objects.all(), required=False, allow_null=True
    )

    class Meta:
        model = Sale
        fields = [
            'id', 'invoice_number', 'status', 'branch', 'godown', 'godown_name', 'user','created_by_name', 'customer_name',
            'lp_number', 'pi_number', 'order_number',
            'total_amount', 'discount', 'total_weight', 'unweighed_items', 'created_at', 'items', 'items_response', 'payment_details',
            'amount_paid', 'balance', 'payment_status', 'approved_by', 'approved_by_name',
            'approved_at', 'dispatch_manager', 'dispatch_manager_name', 'store_keeper',
            'store_keeper_name', 'lorry_info', 'vehicle', 'vehicle_details'
        ]
        read_only_fields = ('total_amount', 'invoice_number', 'amount_paid', 'balance', 'payment_status', 'status', 'approved_at')

    def get_amount_paid(self, obj):
        return sum(t.amount for t in obj.transactions.all())

    def get_balance(self, obj):
        return obj.total_amount - self.get_amount_paid(obj)

    def get_payment_status(self, obj):
        paid = self.get_amount_paid(obj)
        if paid >= obj.total_amount:
            return 'Paid'
        elif paid > 0:
            return 'Partial'
        return 'Credit'

    def get_total_weight(self, obj):
        """kg of every line whose weight is known (see `load_of`)."""
        return load_of(obj.items.all())[0]

    def get_unweighed_items(self, obj):
        """Products on the sale with no weight set — the load is at least
        `total_weight`, not exactly it."""
        return load_of(obj.items.all())[1]

    def validate(self, attrs):
        """Nothing is sold that is not there to sell.

        Enforced here rather than in a screen so it holds for the web POS, the
        Android app and anything else that posts a sale. Stock still leaves at
        dispatch — this only refuses to promise goods the branch does not have,
        counting what earlier undispatched sales have already spoken for.
        """
        items = attrs.get('items') or []
        for line in items:
            # The seller may change the price on a line; never below nothing.
            if line.get('price_at_sale') not in (None, ''):
                try:
                    price = float(line['price_at_sale'])
                except (TypeError, ValueError):
                    raise serializers.ValidationError({'items': ['A price is not a number.']})
                if price < 0:
                    raise serializers.ValidationError({'items': ['A price cannot be negative.']})
        payment = attrs.get('payment_details') or {}
        if payment.get('amount'):
            money_account(payment.get('account'), 'payment_details')
        branch = attrs.get('branch') or getattr(self.instance, 'branch', None)
        request = self.context.get('request')
        if request is not None and branch is not None:
            from apps.users.permissions import home_branch, home_godown
            home = home_branch(request.user)
            if home is not None and getattr(branch, 'pk', branch) != home.pk:
                raise serializers.ValidationError(
                    {'branch': f'You sell from {home.name} only.'})
            # Staff tied to a godown sell from it; others may name any godown
            # of the branch, or none (the branch's default).
            tied = home_godown(request.user)
            if tied is not None:
                if attrs.get('godown') not in (None, tied):
                    raise serializers.ValidationError({'godown': f'You sell from {tied.name} only.'})
                attrs['godown'] = tied
        godown = attrs.get('godown') or getattr(self.instance, 'godown', None)
        if godown is not None and branch is not None and godown.branch_id != getattr(branch, 'pk', branch):
            raise serializers.ValidationError({'godown': f'{godown.name} is not in that branch.'})
        if not items or branch is None:
            return attrs

        problems = check_lines(
            branch.pk if hasattr(branch, 'pk') else branch,
            [(i.get('product'), i.get('quantity')) for i in items],
            exclude_sale_id=self.instance.pk if self.instance else None,
            godown=godown,
        )
        if problems:
            raise serializers.ValidationError({'items': problems})
        return attrs

    def create(self, validated_data):
        from decimal import Decimal
        items_data = validated_data.pop('items')
        payment_data = validated_data.pop('payment_details', None)
        
        # Generate Invoice Number
        import uuid
        validated_data['invoice_number'] = str(uuid.uuid4())[:8].upper()
        validated_data['status'] = 'pending'
        
        sale = Sale.objects.create(**validated_data)

        # Handle Subtotals ONLY (No stock deduction here anymore)
        total = Decimal('0.00')
        for item in items_data:
            product = Product.objects.get(id=item['product'])
            qty = int(item['quantity'])
            price = Decimal(str(item.get('price_at_sale', product.price)))

            subtotal = price * qty
            SaleItem.objects.create(sale=sale, product=product, quantity=qty, price_at_sale=price, subtotal=subtotal)
            total += subtotal

        # Apply the flat discount (never below zero).
        discount = Decimal(str(sale.discount or '0.00'))
        if discount < 0:
            discount = Decimal('0.00')
        if discount > total:
            discount = total
        sale.discount = discount
        sale.total_amount = total - discount
        sale.save()

        # Handle Payment
        if payment_data and payment_data.get('amount'):
            from .models import Transaction
            Transaction.objects.create(
                sale=sale,
                amount=payment_data['amount'],
                payment_method=payment_data.get('method', 'cash'),
                deposit_to=money_account(payment_data.get('account'), 'payment_details'),
                reference=str(payment_data.get('reference') or '')[:100],
                transaction_type='income'
            )

        return sale

class TransactionSerializer(serializers.ModelSerializer):
    deposit_to_name = serializers.CharField(source='deposit_to.name', read_only=True)

    class Meta:
        model = Transaction
        fields = '__all__'

    def validate_deposit_to(self, value):
        return money_account(value)

class QuotationItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    product_unit = serializers.CharField(source='product.get_unit_display', read_only=True)
    
    class Meta:
        model = QuotationItem
        fields = '__all__'
        read_only_fields = ('total_price',)

class QuotationSerializer(serializers.ModelSerializer):
    items = QuotationItemSerializer(many=True, read_only=True)
    customer_name_display = serializers.CharField(source='customer.name', read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)

    class Meta:
        model = Quotation
        fields = '__all__'
        read_only_fields = ('created_at', 'total_amount', 'created_by')
