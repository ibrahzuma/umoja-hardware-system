from rest_framework import serializers
from .models import Expense, ExpenseCategory, Income, SupplierPayment, TaxPayment

class ExpenseCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ExpenseCategory
        fields = '__all__'

class ExpenseSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)
    
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

    class Meta:
        model = SupplierPayment
        fields = '__all__'

class TaxPaymentSerializer(serializers.ModelSerializer):
    tax_type_display = serializers.CharField(source='get_tax_type_display', read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)

    class Meta:
        model = TaxPayment
        fields = '__all__'
        read_only_fields = ('created_by',)
