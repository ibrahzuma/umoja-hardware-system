from django.db import models
from django.conf import settings

class Customer(models.Model):
    name = models.CharField(max_length=200)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

class Vehicle(models.Model):
    TYPES = (
        ('lorry', 'Lorry'),
        ('van', 'Van'),
        ('pickup', 'Pickup'),
        ('truck', 'Truck'),
        ('bike', 'Motorbike'),
        ('other', 'Other'),
    )
    STATUSES = (
        ('active', 'Active'),
        ('busy', 'In Transit'),
        ('maintenance', 'Maintenance'),
        ('inactive', 'Inactive'),
    )
    registration_number = models.CharField(max_length=20, unique=True)
    driver_name = models.CharField(max_length=100)
    vehicle_type = models.CharField(max_length=20, choices=TYPES, default='lorry')
    status = models.CharField(max_length=20, choices=STATUSES, default='active', db_index=True)
    current_mileage = models.DecimalField(max_digits=10, decimal_places=1, default=0.0)
    last_condition = models.CharField(max_length=200, blank=True, help_text="Condition recorded at last return")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.registration_number} ({self.driver_name})"

class Sale(models.Model):
    STATUS_CHOICES = (
        ('pending', 'Pending Approval'),
        ('approved', 'Approved'),
        ('dispatched', 'Dispatched'),
        ('cancelled', 'Cancelled'),
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    
    # Approval info
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='approved_sales')
    approved_at = models.DateTimeField(null=True, blank=True)
    dispatch_manager = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='managed_dispatches')
    
    # Delivery info
    store_keeper = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_deliveries')
    vehicle = models.ForeignKey(Vehicle, on_delete=models.SET_NULL, null=True, blank=True, related_name='deliveries')
    lorry_info = models.CharField(max_length=100, blank=True)
    
    invoice_number = models.CharField(max_length=50, unique=True)
    branch = models.ForeignKey('inventory.Branch', on_delete=models.CASCADE, related_name='sales')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='sales')
    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, blank=True, related_name='sales')
    customer_name = models.CharField(max_length=100, blank=True, default="Walk-in Customer")
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Invoice #{self.invoice_number}"

class SaleItem(models.Model):
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey('inventory.Product', on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField()
    price_at_sale = models.DecimalField(max_digits=10, decimal_places=2)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2)
    commission_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    def save(self, *args, **kwargs):
        self.subtotal = self.quantity * self.price_at_sale
        
        # Calculate commission if not already set (to preserve historical data if category rate changes)
        if self.commission_amount == 0 and self.product.category.commission_percentage > 0:
            commission_rate = self.product.category.commission_percentage / 100
            self.commission_amount = self.subtotal * commission_rate
            
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.product.name} x {self.quantity}"

class Transaction(models.Model):
    PAYMENT_METHODS = (
        ('cash', 'Cash'),
        ('credit', 'Credit'),
        ('bank', 'Bank Transfer'),
        ('mobile', 'Mobile Money'),
    )
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='transactions', null=True, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHODS, default='cash')
    reference = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    # Can also be used for generic income/outcome if we expand
    transaction_type = models.CharField(max_length=10, default='income', choices=(('income', 'Income'), ('expense', 'Expense')))

    def __str__(self):
        return f"{self.payment_method} - {self.amount}"
