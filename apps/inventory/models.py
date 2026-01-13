from django.db import models
from django.conf import settings
from django.contrib.auth.models import User

class Branch(models.Model):
    name = models.CharField(max_length=100)
    address = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

class Category(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    commission_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=0.00, help_text="Commission percentage for sales of products in this category (e.g., 2.00 for 2%)")


    class Meta:
        verbose_name_plural = "Categories"

    def __str__(self):
        return self.name

import random
import string

class Product(models.Model):
    name = models.CharField(max_length=200)
    sku = models.CharField(max_length=50, unique=True, null=True, blank=True)
    TYPE_CHOICES = (
        ('product', 'Product'),
        ('service', 'Service'),
    )
    product_type = models.CharField(max_length=10, choices=TYPE_CHOICES, default='product')
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='products')
    price = models.DecimalField(max_digits=10, decimal_places=2)
    cost = models.DecimalField(max_digits=10, decimal_places=2)
    weight = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, help_text="Weight in kg")
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.sku:
            # Generate a unique SKU if not provided
            prefix = "PROD" if self.product_type == 'product' else "SERV"
            while True:
                random_str = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
                new_sku = f"{prefix}-{random_str}"
                if not Product.objects.filter(sku=new_sku).exists():
                    self.sku = new_sku
                    break
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.sku})"

class Stock(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='stocks')
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name='stocks')
    quantity = models.IntegerField(default=0)
    low_stock_threshold = models.IntegerField(default=10)

    class Meta:
        unique_together = ('product', 'branch')

    def __str__(self):
        return f"{self.product.name} - {self.branch.name}: {self.quantity}"

class Supplier(models.Model):
    name = models.CharField(max_length=200)
    contact_name = models.CharField(max_length=100, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, blank=True)

    def __str__(self):
        return self.name

class Purchase(models.Model):
    supplier = models.ForeignKey(Supplier, on_delete=models.SET_NULL, null=True)
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField()
    unit_cost = models.DecimalField(max_digits=10, decimal_places=2)
    total_cost = models.DecimalField(max_digits=12, decimal_places=2)
    date_purchased = models.DateTimeField(auto_now_add=True)
    # This model records inbound stock. In a real app we might have a 'PurchaseOrder' parent.
    # For simplicity, we record individual line item purchases or simple records.
    # User asked for "Purchases (Supplier orders)". I will keep it simple.

    def save(self, *args, **kwargs):
        self.total_cost = self.quantity * self.unit_cost
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Purchase {self.product.name} ({self.quantity})"

class StockTransfer(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    from_branch = models.ForeignKey(Branch, related_name='transfers_out', on_delete=models.CASCADE)
    to_branch = models.ForeignKey(Branch, related_name='transfers_in', on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField()
    date = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"Transfer {self.product.name} ({self.quantity}) from {self.from_branch} to {self.to_branch}"
class StockAdjustment(models.Model):
    ADJUSTMENT_TYPES = (
        ('addition', 'Addition (+)'),
        ('deduction', 'Deduction (-)'),
        ('correction', 'Set Quantity (=)'),
    )
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE)
    adjustment_type = models.CharField(max_length=20, choices=ADJUSTMENT_TYPES)
    quantity = models.FloatField()
    reason = models.TextField()
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.adjustment_type} for {self.product.name}"
