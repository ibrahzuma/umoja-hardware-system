from rest_framework import serializers
from .models import Sale, SaleItem, Transaction, Customer, Vehicle
from apps.inventory.models import Product

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
    
    store_keeper_name = serializers.CharField(source='store_keeper.username', read_only=True)
    approved_by_name = serializers.CharField(source='approved_by.username', read_only=True)
    dispatch_manager_name = serializers.CharField(source='dispatch_manager.username', read_only=True)
    created_by_name = serializers.CharField(source='user.username', read_only=True)
    
    vehicle_details = VehicleSerializer(source='vehicle', read_only=True)
    vehicle = serializers.PrimaryKeyRelatedField(
        queryset=Vehicle.objects.all(), required=False, allow_null=True
    )

    class Meta:
        model = Sale
        fields = [
            'id', 'invoice_number', 'status', 'branch', 'user', 'created_by_name', 'customer_name', 
            'total_amount', 'created_at', 'items', 'items_response', 'payment_details', 
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
            
        sale.total_amount = total
        sale.save()

        # Handle Payment
        if payment_data and payment_data.get('amount'):
            from .models import Transaction
            Transaction.objects.create(
                sale=sale,
                amount=payment_data['amount'],
                payment_method=payment_data.get('method', 'cash'),
                transaction_type='income'
            )

        return sale

class TransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Transaction
        fields = '__all__'
