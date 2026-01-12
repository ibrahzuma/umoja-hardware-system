from rest_framework import serializers
from .models import Branch, Category, Product, Stock, Purchase, Supplier, StockTransfer

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
        fields = '__all__'

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

from .models import Branch, Category, Product, Stock, Purchase, Supplier, StockTransfer

# ... existing code ...

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
from .models import StockAdjustment

class StockAdjustmentSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    branch_name = serializers.CharField(source='branch.name', read_only=True)
    user_name = serializers.CharField(source='user.username', read_only=True)

    class Meta:
        model = StockAdjustment
        fields = '__all__'
        read_only_fields = ('user', 'created_at')
