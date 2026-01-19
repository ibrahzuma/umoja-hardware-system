from .models import Branch, Category, Product, Stock, Purchase, Supplier, StockTransfer, PurchaseOrder, PurchaseOrderItem, Truck, TruckAllocation, StockAdjustment, GoodsReceivedNote, GRNItem, Driver, TruckMaintenance, TruckAllocation
from rest_framework import serializers

class BranchSerializer(serializers.ModelSerializer):
    class Meta:
        model = Branch
        fields = '__all__'

class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = '__all__'

class ProductSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)
    opening_stock = serializers.IntegerField(write_only=True, required=False, default=0)
    low_stock_threshold = serializers.IntegerField(write_only=True, required=False, default=10)
    
    class Meta:
        model = Product
        fields = ['id', 'name', 'sku', 'product_type', 'category', 'category_name', 'price', 'cost', 'weight', 'description', 'created_at', 'opening_stock', 'low_stock_threshold']

    def create(self, validated_data):
        opening_stock = validated_data.pop('opening_stock', 0)
        low_stock_val = validated_data.pop('low_stock_threshold', 10)
        
        product = Product.objects.create(**validated_data)
        
        # Get or Create default branch
        branch, _ = Branch.objects.get_or_create(name="Main Branch")
        
        # Create or Get Stock
        Stock.objects.get_or_create(
            product=product,
            branch=branch,
            defaults={
                'quantity': opening_stock,
                'low_stock_threshold': low_stock_val
            }
        )
        return product

class StockSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    product_sku = serializers.CharField(source='product.sku', read_only=True)
    branch_name = serializers.CharField(source='branch.name', read_only=True)

    class Meta:
        model = Stock
        fields = '__all__'

class SupplierSerializer(serializers.ModelSerializer):
    class Meta:
        model = Supplier
        fields = '__all__'

class PurchaseSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    supplier_name = serializers.CharField(source='supplier.name', read_only=True)

    class Meta:
        model = Purchase
        fields = '__all__'

class StockTransferSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    from_branch_name = serializers.CharField(source='from_branch.name', read_only=True)
    to_branch_name = serializers.CharField(source='to_branch.name', read_only=True)

    class Meta:
        model = StockTransfer
        fields = '__all__'

class StockAdjustmentSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    branch_name = serializers.CharField(source='branch.name', read_only=True)
    user_name = serializers.CharField(source='user.username', read_only=True)

    class Meta:
        model = StockAdjustment
        fields = '__all__'
        read_only_fields = ('user', 'created_at')

class PurchaseOrderItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    
    class Meta:
        model = PurchaseOrderItem
        fields = '__all__'
        read_only_fields = ('total_cost',)

class PurchaseOrderSerializer(serializers.ModelSerializer):
    supplier_name = serializers.CharField(source='supplier.name', read_only=True)
    branch_name = serializers.CharField(source='branch.name', read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)
    items = PurchaseOrderItemSerializer(many=True, read_only=True)

    class Meta:
        model = PurchaseOrder
        fields = '__all__'
        read_only_fields = ('created_by', 'created_at', 'updated_at', 'total_amount')

class TruckSerializer(serializers.ModelSerializer):
    class Meta:
        model = Truck
        fields = '__all__'

class TruckAllocationSerializer(serializers.ModelSerializer):
    truck_reg = serializers.CharField(source='truck.registration_number', read_only=True)
    po_ref = serializers.CharField(source='purchase_order.__str__', read_only=True)

    class Meta:
        model = TruckAllocation
        fields = '__all__'

class GRNItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    
    class Meta:
        model = GRNItem
        fields = '__all__'

class GoodsReceivedNoteSerializer(serializers.ModelSerializer):
    branch_name = serializers.CharField(source='branch.name', read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)
    items = GRNItemSerializer(many=True, read_only=True)
    po_ref = serializers.CharField(source='purchase_order.__str__', read_only=True)

    class Meta:
        model = GoodsReceivedNote
        fields = '__all__'
        read_only_fields = ('created_by', 'received_date')

class DriverSerializer(serializers.ModelSerializer):
    class Meta:
        model = Driver
        fields = '__all__'

class TruckMaintenanceSerializer(serializers.ModelSerializer):
    truck_reg = serializers.CharField(source='truck.registration_number', read_only=True)
    performed_by_name = serializers.CharField(source='recorded_by.username', read_only=True)

    class Meta:
        model = TruckMaintenance
        fields = '__all__'
        read_only_fields = ('recorded_by',)
