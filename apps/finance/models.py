from django.db import models
from django.db.models import Sum

class ExpenseCategory(models.Model):
    name = models.CharField(max_length=100)

    class Meta:
        verbose_name_plural = "Expense Categories"

    def __str__(self):
        return self.name

class BankAccount(models.Model):
    """A company bank account expenses can be paid from."""
    name = models.CharField(max_length=120, help_text="e.g. CRDB - Main Account")
    account_number = models.CharField(max_length=50, blank=True)
    branch = models.CharField(max_length=120, blank=True, help_text="Bank branch")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

class Expense(models.Model):
    branch = models.ForeignKey('inventory.Branch', on_delete=models.CASCADE, related_name='expenses')
    category = models.ForeignKey(ExpenseCategory, on_delete=models.SET_NULL, null=True)
    bank = models.ForeignKey(BankAccount, on_delete=models.SET_NULL, null=True, blank=True, related_name='expenses', help_text="Bank account the money was taken from")
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
    """Money paid to a supplier, against the purchase order it settles.

    The supplier is never picked out of thin air: the payer chooses one of the
    purchase orders Afisa Ugavi raised, and the supplier follows from it. The
    FK is nullable only so payments recorded before this rule (and the rare
    off-order settlement) still have a home — new payments come in with it set.

    A payment the cashier records is a *request*, not a settlement: it lands as
    `pending` and only an Admin turns it into `paid`. Until then it counts
    against nothing — `payable_orders` and `by_supplier` total the paid ones —
    so an order's balance never falls on an entry nobody has approved.
    """
    PAYMENT_METHODS = (
        ('cash', 'Cash'),
        ('bank_transfer', 'Bank Transfer'),
        ('check', 'Check'),
        ('mobile_money', 'Mobile Money'),
    )
    STATUS_CHOICES = (
        ('pending', 'Pending Approval'),
        ('paid', 'Paid'),
        ('rejected', 'Rejected'),
    )
    supplier = models.ForeignKey('inventory.Supplier', on_delete=models.CASCADE, related_name='payments')
    purchase_order = models.ForeignKey('inventory.PurchaseOrder', on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='payments',
                                       help_text="The order this payment settles")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_date = models.DateField()
    method = models.CharField(max_length=20, choices=PAYMENT_METHODS, default='bank_transfer')
    reference = models.CharField(max_length=100, blank=True, help_text="Check No, Transaction ID, etc.")
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending', db_index=True)
    approved_by = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='approved_supplier_payments',
                                    help_text="Admin who approved or rejected it")
    approved_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True, help_text="Admin's reason, most useful on a rejection")

    class Meta:
        ordering = ['-payment_date', '-id']

    @property
    def is_paid(self):
        return self.status == 'paid'

    def __str__(self):
        return f"Payment to {self.supplier} - {self.amount} ({self.get_status_display()})"

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

class PaymentReceipt(models.Model):
    """Proof-of-payment a customer sends (e.g. over WhatsApp) for an invoice.

    Finance uploads the receipt image, ties it to a Sale (invoice), and records
    the amount on the receipt. We snapshot the invoice total and the invoice
    issuer at record time, and compute the outstanding balance the customer
    still owes (a receivable / "credit") after this and all prior receipts on
    the same invoice. This is a standalone Finance ledger — it does not write
    back to the Sale/Transaction records.
    """
    sale = models.ForeignKey('sales.Sale', on_delete=models.SET_NULL, null=True, blank=True, related_name='payment_receipts')
    invoice_number = models.CharField(max_length=50, db_index=True, help_text="Invoice the customer paid against")
    customer = models.ForeignKey('sales.Customer', on_delete=models.SET_NULL, null=True, blank=True, related_name='payment_receipts')
    customer_name = models.CharField(max_length=200, blank=True, help_text="Snapshot of the customer name")

    invoice_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0.00, help_text="Invoice total at record time")
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, help_text="Figure shown on the receipt")
    outstanding_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0.00, help_text="Balance still owed after this receipt")

    receipt_image = models.ImageField(upload_to='payment_receipts/%Y/%m/', blank=True, null=True)
    payment_date = models.DateField()
    reference = models.CharField(max_length=100, blank=True, help_text="Mobile money txn ID, bank ref, etc.")
    notes = models.TextField(blank=True)

    issued_by = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='issued_invoice_receipts', help_text="Staff who issued the invoice")
    created_by = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, related_name='recorded_receipts', help_text="Finance user who logged the receipt")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    @property
    def fully_paid(self):
        return self.outstanding_amount is not None and self.outstanding_amount <= 0

    def __str__(self):
        return f"Receipt for Invoice #{self.invoice_number} - {self.amount_paid}"


# ---------------------------------------------------------------------------
# Cashier — the money that leaves the counter in cash
# ---------------------------------------------------------------------------

class PettyCashTransaction(models.Model):
    """The cash float the cashier holds, one row per movement.

    Two kinds of row: cash put *into* the float ('in' — a top-up drawn from a
    bank account or handed over by the office) and cash paid *out* of it
    ('out' — a petty cash voucher). The float balance is the difference, so it
    is derived from this table and never stored; see `balance()`.
    """
    ENTRY_TYPES = (
        ('in', 'Cash In (Top-up)'),
        ('out', 'Cash Out (Payment)'),
    )

    entry_type = models.CharField(max_length=3, choices=ENTRY_TYPES, default='out')
    voucher_number = models.CharField(max_length=20, unique=True, blank=True,
                                      help_text="Auto-generated on save, e.g. PC-000123")
    branch = models.ForeignKey('inventory.Branch', on_delete=models.SET_NULL, null=True, blank=True,
                               related_name='petty_cash')
    category = models.ForeignKey(ExpenseCategory, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='petty_cash')
    bank = models.ForeignKey(BankAccount, on_delete=models.SET_NULL, null=True, blank=True,
                             related_name='petty_cash_topups',
                             help_text="Account a top-up was drawn from")
    payee = models.CharField(max_length=150, blank=True, help_text="Who received the cash")
    description = models.TextField()
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    date = models.DateField()
    reference = models.CharField(max_length=100, blank=True, help_text="Receipt no, slip no, etc.")
    receipt_image = models.ImageField(upload_to='petty_cash/%Y/%m/', blank=True, null=True)
    created_by = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True,
                                   related_name='petty_cash_entries')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-id']
        verbose_name = 'Petty Cash Transaction'

    def save(self, *args, **kwargs):
        if not self.voucher_number:
            # Sequential voucher numbers, skipping any already taken (rows can
            # be deleted, so max(id)+1 alone is not enough).
            last = PettyCashTransaction.objects.order_by('-id').first()
            n = (last.id + 1) if last else 1
            while PettyCashTransaction.objects.filter(voucher_number=f"PC-{n:06d}").exists():
                n += 1
            self.voucher_number = f"PC-{n:06d}"
        super().save(*args, **kwargs)

    @classmethod
    def balance(cls, branch=None):
        """Cash still in the float: everything paid in, less everything paid out."""
        qs = cls.objects.all()
        if branch:
            qs = qs.filter(branch=branch)
        totals = qs.values('entry_type').annotate(total=Sum('amount'))
        by_type = {row['entry_type']: row['total'] or 0 for row in totals}
        return (by_type.get('in') or 0) - (by_type.get('out') or 0)

    def __str__(self):
        return f"{self.voucher_number} - {self.get_entry_type_display()} {self.amount}"


class OtherPayment(models.Model):
    """Money paid out that is neither a supplier invoice nor a statutory tax.

    Casual labour, salary advances, customer refunds, utilities, licences — the
    cashier's catch-all outgoing register. Deliberately standalone: it does not
    touch Expense (which is the accountant's cost ledger) so the cashier can
    record a payout at the counter without owning the chart of accounts.
    """
    PAYMENT_TYPES = (
        ('casual_labour', 'Casual Labour'),
        ('salary_advance', 'Salary Advance'),
        ('customer_refund', 'Customer Refund'),
        ('utility', 'Utility Bill'),
        ('rent', 'Rent'),
        ('transport', 'Transport / Fuel'),
        ('licence', 'Licence / Permit'),
        ('loan_repayment', 'Loan Repayment'),
        ('other', 'Other'),
    )

    payment_type = models.CharField(max_length=30, choices=PAYMENT_TYPES, default='other')
    payee = models.CharField(max_length=150, help_text="Person or organisation paid")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_date = models.DateField()
    method = models.CharField(max_length=20, choices=SupplierPayment.PAYMENT_METHODS, default='cash')
    bank = models.ForeignKey(BankAccount, on_delete=models.SET_NULL, null=True, blank=True,
                             related_name='other_payments',
                             help_text="Account the money left, if not cash")
    branch = models.ForeignKey('inventory.Branch', on_delete=models.SET_NULL, null=True, blank=True,
                               related_name='other_payments')
    reference = models.CharField(max_length=100, blank=True, help_text="Check no, transaction ID, etc.")
    description = models.TextField(blank=True)
    receipt_image = models.ImageField(upload_to='other_payments/%Y/%m/', blank=True, null=True)
    created_by = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True,
                                   related_name='other_payments')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-payment_date', '-id']

    def __str__(self):
        return f"{self.payee} - {self.amount} ({self.get_payment_type_display()})"
