from django.db import models
from django.conf import settings
from django.contrib.auth.models import User
from simple_history.models import HistoricalRecords
import random
import string

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
    history = HistoricalRecords()

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
    history = HistoricalRecords()

    class Meta:
        unique_together = ('product', 'branch')

    def __str__(self):
        return f"{self.product.name} - {self.branch.name}: {self.quantity}"

class Supplier(models.Model):
    name = models.CharField(max_length=200)
    contact_name = models.CharField(max_length=100, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    address = models.TextField(blank=True)

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
    history = HistoricalRecords()

    def save(self, *args, **kwargs):
        self.total_cost = self.quantity * self.unit_cost
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Purchase {self.product.name} ({self.quantity})"

class PurchaseOrder(models.Model):
    STATUS_CHOICES = (
        ('draft', 'Draft'),
        ('sent', 'Sent'),
        ('received', 'Received'),
        ('cancelled', 'Cancelled'),
    )
    supplier = models.ForeignKey(Supplier, on_delete=models.SET_NULL, null=True, related_name='purchase_orders')
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name='purchase_orders')
    order_date = models.DateField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"PO #{self.id} - {self.supplier}"

class PurchaseOrderItem(models.Model):
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField()
    unit_cost = models.DecimalField(max_digits=10, decimal_places=2)
    total_cost = models.DecimalField(max_digits=12, decimal_places=2)

    def save(self, *args, **kwargs):
        self.total_cost = self.quantity * self.unit_cost
        super().save(*args, **kwargs)
        # Assuming we update the PO total in a signal or manual method, keeping it simple here.

    def __str__(self):
        return f"{self.product.name} x {self.quantity}"

class GoodsReceivedNote(models.Model):
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name='grns', null=True, blank=True)
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name='grns')
    received_date = models.DateTimeField(auto_now_add=True)
    receipt_number = models.CharField(max_length=50, unique=True, help_text="Delivery Note / Receipt Number from Supplier")
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)

    def __str__(self):
        return f"GRN #{self.id} - {self.receipt_number}"

class GRNItem(models.Model):
    grn = models.ForeignKey(GoodsReceivedNote, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    quantity_received = models.PositiveIntegerField()
    remarks = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return f"{self.product.name} - {self.quantity_received}"


class Truck(models.Model):
    STATUS_CHOICES = (
        ('available', 'Available'),
        ('in_transit', 'In Transit'),
        ('maintenance', 'Maintenance'),
    )
    registration_number = models.CharField(max_length=20, unique=True)
    driver_name = models.CharField(max_length=100, blank=True)
    capacity = models.CharField(max_length=50, help_text="e.g. 5 Ton", blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='available')
    
    def __str__(self):
        return f"{self.registration_number} ({self.status})"

class Driver(models.Model):
    STATUS_CHOICES = (
        ('active', 'Active'),
        ('on_leave', 'On Leave'),
        ('suspended', 'Suspended'),
        ('inactive', 'Inactive'),
    )
    name = models.CharField(max_length=100)
    license_number = models.CharField(max_length=50, unique=True, blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

class DriverIssue(models.Model):
    driver = models.ForeignKey(Driver, on_delete=models.CASCADE, related_name='issues')
    issue_date = models.DateField()
    issue_type = models.CharField(max_length=50, choices=(('disciplinary', 'Disciplinary'), ('accident', 'Accident'), ('health', 'Health'), ('other', 'Other')))
    description = models.TextField()
    resolved = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.driver} - {self.issue_type}"

class TruckMaintenance(models.Model):
    MAINTENANCE_TYPES = (
        ('fuel', 'Fuel'),
        ('spare', 'Spare Parts'),
        ('repair', 'Repair'),
        ('service', 'Service'),
    )
    truck = models.ForeignKey(Truck, on_delete=models.CASCADE, related_name='maintenance_logs')
    maintenance_type = models.CharField(max_length=20, choices=MAINTENANCE_TYPES)
    date = models.DateField()
    cost = models.DecimalField(max_digits=12, decimal_places=2)
    description = models.TextField(blank=True, help_text="Details of fuel liters, spare parts used, etc.")
    performed_by = models.CharField(max_length=100, blank=True, help_text="Mechanic or Service Station name")
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)

    def __str__(self):
        return f"{self.truck} - {self.maintenance_type} ({self.cost})"

class TruckAllocation(models.Model):
    truck = models.ForeignKey(Truck, on_delete=models.CASCADE, related_name='allocations')
    driver = models.ForeignKey(Driver, on_delete=models.SET_NULL, null=True, blank=True)
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.SET_NULL, null=True, blank=True, related_name='truck_allocations', help_text="Optional: Link to a PO being transported.")
    destination = models.CharField(max_length=200)
    start_date = models.DateTimeField()
    end_date = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    
    def __str__(self):
        return f"Allocation: {self.truck} to {self.destination}"


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
