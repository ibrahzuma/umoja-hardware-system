from django.db import models

class ExpenseCategory(models.Model):
    name = models.CharField(max_length=100)

    class Meta:
        verbose_name_plural = "Expense Categories"

    def __str__(self):
        return self.name

class Expense(models.Model):
    branch = models.ForeignKey('inventory.Branch', on_delete=models.CASCADE, related_name='expenses')
    category = models.ForeignKey(ExpenseCategory, on_delete=models.SET_NULL, null=True)
    description = models.TextField()
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    date_incurred = models.DateField()
    created_by = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    receipt_image = models.ImageField(upload_to='receipts/%Y/%m/', blank=True, null=True)

    def __str__(self):
        return f"{self.category} - {self.amount}"

class Income(models.Model):
    branch = models.ForeignKey('inventory.Branch', on_delete=models.CASCADE, related_name='other_incomes')
    source = models.CharField(max_length=100, help_text="e.g. Rent, Interest, Scrap Sale")
    description = models.TextField(blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    date_received = models.DateField()
    created_by = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.source} - {self.amount}"

class SupplierPayment(models.Model):
    PAYMENT_METHODS = (
        ('cash', 'Cash'),
        ('bank_transfer', 'Bank Transfer'),
        ('check', 'Check'),
        ('mobile_money', 'Mobile Money'),
    )
    supplier = models.ForeignKey('inventory.Supplier', on_delete=models.CASCADE, related_name='payments')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_date = models.DateField()
    method = models.CharField(max_length=20, choices=PAYMENT_METHODS, default='bank_transfer')
    reference = models.CharField(max_length=100, blank=True, help_text="Check No, Transaction ID, etc.")
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Payment to {self.supplier} - {self.amount}"

class TaxPayment(models.Model):
    TAX_TYPES = (
        ('vat', 'VAT'),
        ('paye', 'PAYE'),
        ('sdl', 'SDL'),
        ('service_levy', 'Service Levy'),
        ('corporate_tax', 'Corporate Tax'),
        ('other', 'Other'),
    )
    tax_type = models.CharField(max_length=50, choices=TAX_TYPES)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_date = models.DateField()
    period = models.CharField(max_length=50, help_text="e.g. January 2026")
    reference = models.CharField(max_length=100, blank=True, help_text="Payment Ref / Receipt No")
    description = models.TextField(blank=True)
    created_by = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.get_tax_type_display()} - {self.amount} ({self.period})"
