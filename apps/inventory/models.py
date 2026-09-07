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

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

class Category(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    commission_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=0.00, help_text="Commission percentage for sales of products in this category (e.g., 2.00 for 2%)")


    class Meta:
        ordering = ['name']
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

    class Meta:
        ordering = ['name']

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

    class Meta:
        ordering = ['-date_purchased', '-id']

    def __str__(self):
        return f"Purchase {self.product.name} ({self.quantity})"

class PurchaseOrder(models.Model):
    """An order raised by Afisa Ugavi and delivered against, possibly in parts.

    Delivery lifecycle (see DeliveryCheck for one round of it):

        draft/sent --confirm--> awaiting_check      (with the Store Manager)
        awaiting_check --all arrived--> received
        awaiting_check --short--> discrepancy       (Afisa Ugavi must comment)
        discrepancy --comment--> awaiting_admin     (Admin confirms or rejects)
        awaiting_admin --reject--> discrepancy      (back for a better comment)
        awaiting_admin --confirm--> received        (nothing outstanding)
                                 \\-> partial       (balance still owed;
                                                     Afisa Ugavi confirms again
                                                     when the rest arrives)

    Nothing reaches stock on a short delivery until the Admin confirms it, and
    only the quantity that actually arrived is added.
    """
    # 'draft' is a historical value: these orders are placed and simply have
    # not arrived yet, so they read as "Waiting for Delivery" everywhere and
    # count as money owed on the balance sheet. The stored value is left alone
    # rather than migrated, so nothing that filters on it breaks.
    STATUS_CHOICES = (
        ('draft', 'Waiting for Delivery'),
        ('sent', 'Sent'),
        ('awaiting_check', 'Awaiting Store Check'),
        ('discrepancy', 'Discrepancy'),
        ('awaiting_admin', 'Awaiting Admin Decision'),
        ('partial', 'Partially Received'),
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

    # Store Manager cross-check (delivered vs ordered)
    checked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                   blank=True, related_name='checked_purchase_orders')
    checked_at = models.DateTimeField(null=True, blank=True)
    has_discrepancy = models.BooleanField(default=False)
    store_note = models.TextField(blank=True, help_text="Store Manager's note during cross-check")
    afisa_comment = models.TextField(blank=True, help_text="Afisa Ugavi's explanation for a discrepancy")

    # Admin decision on a short delivery. These mirror the latest DeliveryCheck
    # so the order list can show the current state without a second query.
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                   blank=True, related_name='decided_purchase_orders')
    decided_at = models.DateTimeField(null=True, blank=True)
    admin_note = models.TextField(blank=True, help_text="Admin's reason for confirming or rejecting a short delivery")

    class Meta:
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f"PO #{self.id} - {self.supplier}"

    @property
    def latest_check(self):
        """The most recent delivery round, or None before the first check."""
        return self.checks.first()

    @property
    def next_round_number(self):
        return self.checks.count() + 1

    @property
    def is_fully_received(self):
        return all(it.outstanding == 0 for it in self.items.all())

    def outstanding_items(self):
        """Line items still owed by the supplier — what the next delivery round
        is checked against."""
        return [it for it in self.items.all() if it.outstanding > 0]

class PurchaseOrderItem(models.Model):
    UNIT_CHOICES = [
        ('pcs', 'Pcs'), ('item', 'Item'), ('bundle', 'Bundle'), ('box', 'Box'),
        ('carton', 'Carton'), ('dozen', 'Dozen'), ('bag', 'Bag'), ('roll', 'Roll'),
        ('kg', 'Kg'), ('ton', 'Ton'), ('meter', 'Meter'), ('litre', 'Litre'),
        ('set', 'Set'), ('pair', 'Pair'), ('other', 'Other'),
    ]
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField()
    delivered_quantity = models.PositiveIntegerField(null=True, blank=True,
                                                     help_text="Quantity delivered in the latest cross-check round")
    received_quantity = models.PositiveIntegerField(
        default=0,
        help_text="Cumulative quantity accepted into stock across all delivery rounds",
    )
    unit = models.CharField(max_length=20, choices=UNIT_CHOICES, default='pcs')
    unit_cost = models.DecimalField(max_digits=10, decimal_places=2)
    total_cost = models.DecimalField(max_digits=12, decimal_places=2)

    def save(self, *args, **kwargs):
        self.total_cost = self.quantity * self.unit_cost
        super().save(*args, **kwargs)
        # Assuming we update the PO total in a signal or manual method, keeping it simple here.

    @property
    def outstanding(self):
        """Still owed by the supplier. Only ever reaches 0 — over-delivery is
        clamped at cross-check so the balance loop always terminates."""
        return max(self.quantity - (self.received_quantity or 0), 0)

    def __str__(self):
        return f"{self.product.name} x {self.quantity}"


class DeliveryCheck(models.Model):
    """One round of the Store Manager's cross-check against a purchase order.

    A short delivery does not go straight to stock: the round travels to Afisa
    Ugavi for an explanation, then to an Admin who confirms (accept what
    arrived, keep the balance owing) or rejects (back to Afisa Ugavi). Each
    round is its own row, so an order delivered in three instalments keeps all
    three counts, comments and decisions.
    """
    DECISION_CHOICES = (
        ('complete', 'Delivered in full'),
        ('pending_comment', 'Awaiting Afisa Ugavi comment'),
        ('pending_admin', 'Awaiting admin decision'),
        ('approved', 'Confirmed by admin'),
        ('rejected', 'Rejected by admin'),
    )
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name='checks')
    round_number = models.PositiveSmallIntegerField(default=1)

    checked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                   blank=True, related_name='delivery_checks')
    checked_at = models.DateTimeField(auto_now_add=True)
    store_note = models.TextField(blank=True, help_text="Store Manager's note on this delivery")
    has_discrepancy = models.BooleanField(default=False)

    afisa_comment = models.TextField(blank=True, help_text="Afisa Ugavi's explanation for the shortfall")
    commented_at = models.DateTimeField(null=True, blank=True)

    decision = models.CharField(max_length=20, choices=DECISION_CHOICES, default='pending_comment')
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                   blank=True, related_name='delivery_decisions')
    decided_at = models.DateTimeField(null=True, blank=True)
    admin_note = models.TextField(blank=True, help_text="Admin's reason for confirming or rejecting")

    class Meta:
        ordering = ['-round_number']
        unique_together = ('purchase_order', 'round_number')
        verbose_name = 'Delivery Check'

    def __str__(self):
        return f"PO-{self.purchase_order_id:05d} round {self.round_number}"


class DeliveryCheckItem(models.Model):
    """What the Store Manager counted for one line item in one round."""
    # Named `delivery_check`, not `check`: a field called `check` would shadow
    # Django's Model.check() classmethod (models.E020).
    delivery_check = models.ForeignKey(DeliveryCheck, on_delete=models.CASCADE, related_name='lines')
    item = models.ForeignKey(PurchaseOrderItem, on_delete=models.CASCADE, related_name='check_lines')
    expected_quantity = models.PositiveIntegerField(help_text="What was still owed when this round was checked")
    delivered_quantity = models.PositiveIntegerField(default=0)
    note = models.CharField(max_length=255, blank=True, help_text="Store Manager's note on this item")

    class Meta:
        ordering = ['id']

    @property
    def shortfall(self):
        return max(self.expected_quantity - self.delivered_quantity, 0)

    def __str__(self):
        return f"{self.item.product.name}: {self.delivered_quantity}/{self.expected_quantity}"

class GoodsReceivedNote(models.Model):
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name='grns', null=True, blank=True)
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name='grns')
    received_date = models.DateTimeField(auto_now_add=True)
    receipt_number = models.CharField(max_length=50, unique=True, help_text="Delivery Note / Receipt Number from Supplier")
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)

    class Meta:
        ordering = ['-received_date', '-id']

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


class TruckCost(models.Model):
    """All costs related to running the trucks, recorded by Afisa Ugavi under
    Transport Management: fuel, spare parts, repairs, service, insurance,
    licensing, tolls, parking, fines, driver allowances, etc."""
    COST_TYPES = (
        ('fuel', 'Fuel'),
        ('spare_parts', 'Spare Parts'),
        ('repair', 'Repair'),
        ('service', 'Service'),
        ('insurance', 'Insurance'),
        ('license', 'License / Permit'),
        ('toll', 'Toll / Weighbridge'),
        ('parking', 'Parking'),
        ('fine', 'Fine / Penalty'),
        ('driver_allowance', 'Driver Allowance'),
        ('other', 'Other'),
    )
    truck = models.ForeignKey(Truck, on_delete=models.CASCADE, related_name='costs')
    allocation = models.ForeignKey(
        TruckAllocation, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='costs', help_text="Optional: link this cost to a specific trip/allocation.",
    )
    cost_type = models.CharField(max_length=20, choices=COST_TYPES, default='fuel')
    date = models.DateField()
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    vendor = models.CharField(max_length=120, blank=True, help_text="Station, garage, authority, etc.")
    reference = models.CharField(max_length=80, blank=True, help_text="Receipt / invoice number")
    description = models.TextField(blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-created_at']

    def __str__(self):
        return f"{self.truck} - {self.get_cost_type_display()} ({self.amount})"


class StockTransfer(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    from_branch = models.ForeignKey(Branch, related_name='transfers_out', on_delete=models.CASCADE)
    to_branch = models.ForeignKey(Branch, related_name='transfers_in', on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField()
    date = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-id']

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
