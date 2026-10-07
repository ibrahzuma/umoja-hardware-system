from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Sum
from django.utils import timezone


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

    # Money that never left the till: settling an order out of what the
    # supplier already holds of ours. See apps/finance/credit.py.
    from_credit = models.BooleanField(
        default=False, db_index=True,
        help_text="Settled from the supplier's credit rather than fresh money")

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending', db_index=True)
    approved_by = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='approved_supplier_payments',
                                    help_text="Admin who approved or rejected it")
    approved_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True, help_text="Admin's reason, most useful on a rejection")

    # A rejection is not the end of the line: it goes back to the cashier, who
    # answers the Admin's note (amending the entry if that is what was wrong)
    # and sends it round again. The Admin's note is kept through the loop so
    # they can see their own objection beside the reply to it.
    cashier_note = models.TextField(blank=True, help_text="Cashier's answer to a rejection")
    resubmitted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-payment_date', '-id']

    @property
    def is_paid(self):
        return self.status == 'paid'

    @property
    def is_editable(self):
        """Settled money is closed. Anything still in the loop can be amended."""
        return self.status in ('pending', 'rejected')

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


# ---------------------------------------------------------------------------
# Accounting — every sale, however it was settled, lands here to be posted
# ---------------------------------------------------------------------------

class SalesLedgerEntry(models.Model):
    """One row per sale, waiting on Accounts.

    Every sale made at the till arrives here as `pending`, whatever way it was
    settled — paid in cash, part paid on a deposit, or wholly on credit. The
    accountant works the list and **posts** each one, which is what says the
    books have taken it up; anything that does not look right can be **queried**
    with a note and posted later once it is sorted out.

    The row is a snapshot, kept in step with the sale while it is still
    pending or queried. Once posted it freezes: a posted figure is what the
    books were told, and it must not quietly move afterwards. Only
    `sale_status` keeps following, so a sale cancelled after posting shows up
    as needing a reversal rather than vanishing.

    The link is the invoice number, as in the CRM register: the FK is there for
    convenience but nothing cascades off it.
    """
    SETTLEMENTS = (
        ('paid', 'Paid'),
        ('part_paid', 'Part Paid'),
        ('credit', 'On Credit'),
    )
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('posted', 'Posted'),
        ('queried', 'Queried'),
    )

    invoice_number = models.CharField(max_length=50, unique=True, db_index=True)
    sale = models.ForeignKey('sales.Sale', on_delete=models.SET_NULL, null=True, blank=True,
                             related_name='ledger_entries')

    sale_date = models.DateField(db_index=True)
    customer_name = models.CharField(max_length=200, blank=True)
    branch = models.ForeignKey('inventory.Branch', on_delete=models.SET_NULL, null=True, blank=True,
                               related_name='sales_ledger_entries')
    sold_by = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='sales_ledger_entries')

    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    settlement = models.CharField(max_length=12, choices=SETTLEMENTS, default='credit', db_index=True)
    methods = models.CharField(max_length=120, blank=True,
                               help_text="How it was paid, e.g. 'Cash, Mobile Money'")
    sale_status = models.CharField(max_length=20, blank=True,
                                   help_text="The sale's own state at last sync")

    # Frozen with the entry so a later change to a product's cost cannot move
    # the profit on a sale that has already been posted.
    cost_of_sales = models.DecimalField(max_digits=12, decimal_places=2, default=0.00,
                                        help_text="What the goods cost us, at the time of the sale")
    commission_total = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending', db_index=True)
    posted_by = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='posted_sales_entries')
    posted_at = models.DateTimeField(null=True, blank=True)
    note = models.TextField(blank=True, help_text="Accountant's note, most useful on a query")

    # --- Money in ---------------------------------------------------------
    # What the till recorded is a claim, not a receipt. Nothing counts as
    # money until the accountant confirms it, names how it came in, and
    # attaches the invoice. Until then the sale is revenue earned but cash
    # not yet in hand.
    PAYMENT_STATES = (
        ('awaiting', 'Awaiting Confirmation'),
        ('confirmed', 'Payment Confirmed'),
    )
    CONFIRMED_METHODS = (
        ('cash', 'Cash'),
        ('bank', 'Bank Transfer'),
        ('mobile', 'Mobile Money'),
        ('cheque', 'Cheque'),
        ('other', 'Other'),
    )
    payment_status = models.CharField(max_length=10, choices=PAYMENT_STATES, default='awaiting',
                                      db_index=True)
    confirmed_method = models.CharField(max_length=10, choices=CONFIRMED_METHODS, blank=True)
    confirmed_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0.00,
                                           help_text="Money the accountant has confirmed as received")
    confirmed_reference = models.CharField(max_length=100, blank=True,
                                           help_text="Bank ref, transaction ID, cheque no")
    invoice_document = models.FileField(upload_to='sales_invoices/%Y/%m/', null=True, blank=True,
                                        help_text="The invoice, attached when the payment is confirmed")
    confirmed_by = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='confirmed_sales_entries')
    confirmed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-sale_date', '-id']
        verbose_name = 'Sales Ledger Entry'
        verbose_name_plural = 'Sales Ledger Entries'

    @property
    def balance(self):
        """What the till says is still owed on the invoice."""
        return (self.total_amount or 0) - (self.amount_paid or 0)

    @property
    def cash_outstanding(self):
        """What Accounts have not yet confirmed as received — the figure that
        matters, since the till's word is only a claim."""
        return (self.total_amount or 0) - (self.confirmed_amount or 0)

    @property
    def gross_profit(self):
        return (self.total_amount or 0) - (self.cost_of_sales or 0)

    @property
    def is_open(self):
        """Still the accountant's to change. A posted row is closed."""
        return self.status in ('pending', 'queried')

    def __str__(self):
        return f"{self.invoice_number} - {self.total_amount} ({self.get_status_display()})"


class PettyCashRequest(models.Model):
    """Somebody asks for cash; an Admin allows it; the cashier hands it over.

        raised --approve (Admin)--> approved --issue (Cashier)--> issued
           |                                                        |
           +--reject (Admin)--> rejected            the float moves here,
                                                    not a moment earlier

    Anyone may raise one for themselves — that is the point, it is how a driver
    gets fuel money without finding the cashier first. Nothing leaves the float
    until the cashier actually hands the money over and says so: `issue()`
    writes the `PettyCashTransaction` and the two are linked, so the float and
    this list can never tell different stories.
    """
    STATUS_CHOICES = (
        ('pending', 'Awaiting Approval'),
        ('approved', 'Approved - To Issue'),
        ('rejected', 'Rejected'),
        ('issued', 'Issued'),
    )

    requested_by = models.ForeignKey('users.User', on_delete=models.CASCADE,
                                     related_name='petty_cash_requests')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    purpose = models.TextField(help_text="What the money is for")
    category = models.ForeignKey(ExpenseCategory, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='petty_cash_requests')
    branch = models.ForeignKey('inventory.Branch', on_delete=models.SET_NULL, null=True, blank=True,
                               related_name='petty_cash_requests')

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending', db_index=True)

    approved_by = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='approved_petty_cash_requests')
    approved_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True, help_text="Admin's reason, most useful on a rejection")

    issued_by = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='issued_petty_cash_requests')
    issued_at = models.DateTimeField(null=True, blank=True)
    issue_note = models.TextField(blank=True)
    transaction = models.OneToOneField(PettyCashTransaction, on_delete=models.SET_NULL,
                                       null=True, blank=True, related_name='request',
                                       help_text="The float movement this request produced")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at', '-id']
        verbose_name = 'Petty Cash Request'

    @property
    def is_open(self):
        """Still the requester's to amend or withdraw."""
        return self.status == 'pending'

    def __str__(self):
        return f"{self.requested_by} - {self.amount} ({self.get_status_display()})"


# ---------------------------------------------------------------------------
# Accounting configuration: currencies, financial years, numbering, audit
# ---------------------------------------------------------------------------

class Currency(models.Model):
    """A currency the books can transact in. Exactly one is the base
    (reporting) currency — the General Ledger, the trial balance and every
    statement are kept in it, whatever a voucher was entered in."""

    code = models.CharField("Currency code", max_length=10, unique=True,
                            help_text="ISO code, e.g. USD")
    name = models.CharField(max_length=60)
    symbol = models.CharField(max_length=10, blank=True,
                              help_text="Shown next to amounts, e.g. $")
    is_base = models.BooleanField(
        "Base (reporting) currency", default=False,
        help_text="The General Ledger, trial balance and all reports are kept in this currency")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['-is_base', 'code']
        verbose_name_plural = 'currencies'

    def __str__(self):
        return f"{self.code} - {self.name}"

    def clean(self):
        from django.core.exceptions import ValidationError
        self.code = (self.code or '').strip().upper()
        if self.is_base and Currency.objects.filter(is_base=True).exclude(pk=self.pk).exists():
            raise ValidationError({'is_base': 'Another currency is already the base currency.'})
        if not self.is_base and self.pk and Currency.objects.get(pk=self.pk).is_base:
            raise ValidationError({'is_base': 'The base currency cannot be switched off; '
                                              'mark another one instead.'})

    def save(self, *args, **kwargs):
        self.code = (self.code or '').strip().upper()
        return super().save(*args, **kwargs)

    @classmethod
    def base(cls):
        return cls.objects.filter(is_base=True).first()

    @property
    def display_symbol(self):
        return self.symbol or self.code

    def rate_on(self, on_date):
        """Units of base currency per 1 unit of this currency on `on_date`
        (the latest rate on or before it), or None when there is none."""
        if self.is_base:
            return Decimal('1')
        rate = self.rates.filter(rate_date__lte=on_date).order_by('-rate_date').first()
        return rate.rate if rate else None


class ExchangeRate(models.Model):
    """One currency's rate against the base currency on a date."""

    currency = models.ForeignKey(Currency, on_delete=models.CASCADE, related_name='rates')
    rate_date = models.DateField("Date", db_index=True)
    rate = models.DecimalField(
        "Rate", max_digits=18, decimal_places=6,
        validators=[MinValueValidator(Decimal('0.000001'))],
        help_text="Units of the base currency for 1 unit of this currency, e.g. 1 USD = 2650.00 TZS")
    note = models.CharField(max_length=150, blank=True)
    created_by = models.ForeignKey('users.User', null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='exchange_rates')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['currency', '-rate_date']
        constraints = [models.UniqueConstraint(fields=['currency', 'rate_date'],
                                               name='exchangerate_unique_per_day')]

    def __str__(self):
        return f"{self.currency.code} @ {self.rate} on {self.rate_date:%d/%m/%Y}"

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.currency_id and self.currency.is_base:
            raise ValidationError({'currency': 'The base currency does not need an exchange rate.'})


class FinancialYearQuerySet(models.QuerySet):
    def for_date(self, date):
        return self.filter(start_date__lte=date, end_date__gte=date).first()

    def open(self):
        return self.filter(is_closed=False)


class FinancialYear(models.Model):
    """A year of account. Its `code` goes into voucher numbers, and posting
    into it is refused once it is closed or locked — see
    `apps/finance/posting.py::check_period`."""

    code = models.CharField(max_length=20, unique=True,
                            help_text="Used in voucher numbers, e.g. 2026 or 2025-26")
    name = models.CharField(max_length=100)
    start_date = models.DateField()
    end_date = models.DateField()
    is_active = models.BooleanField(default=True, help_text="Default financial year for new vouchers")
    is_closed = models.BooleanField(default=False,
                                    help_text="Closed years reject postings unless the user is authorised")
    lock_date = models.DateField(null=True, blank=True,
                                 help_text="Postings dated on or before this date are locked for normal users")
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = FinancialYearQuerySet.as_manager()

    class Meta:
        ordering = ['-start_date']
        verbose_name = 'Financial year'

    def __str__(self):
        return f"{self.name} ({self.start_date:%d/%m/%Y} - {self.end_date:%d/%m/%Y})"

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.start_date and self.end_date:
            if self.end_date <= self.start_date:
                raise ValidationError({'end_date': 'End date must be after the start date.'})
            overlapping = FinancialYear.objects.filter(
                start_date__lte=self.end_date, end_date__gte=self.start_date).exclude(pk=self.pk)
            if overlapping.exists():
                raise ValidationError('This financial year overlaps an existing financial year.')

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_active:
            FinancialYear.objects.exclude(pk=self.pk).update(is_active=False)

    def contains(self, date):
        return self.start_date <= date <= self.end_date

    def is_locked_for(self, date):
        """True when a transaction on `date` falls into a locked part of this year."""
        if self.is_closed:
            return True
        return bool(self.lock_date and date <= self.lock_date)

    @classmethod
    def for_date(cls, date):
        return cls.objects.for_date(date)

    @classmethod
    def current(cls):
        return cls.objects.filter(is_active=True).first() or cls.objects.for_date(timezone.localdate())


class AccountingSettings(models.Model):
    """Singleton: the settings that drive voucher numbering, VAT, EFD policy
    and period locking.

    Kept apart from `core.SystemSettings` (the shop's own company details) on
    purpose — these are the books' rules, and the accountant owns them.
    """

    EFD_WARN, EFD_BLOCK, EFD_ALLOW = 'WARN', 'BLOCK', 'ALLOW'
    EFD_POLICIES = (
        (EFD_WARN, 'Warn on a duplicate EFD receipt number'),
        (EFD_BLOCK, 'Block a duplicate EFD receipt number'),
        (EFD_ALLOW, 'Allow duplicates silently'),
    )

    financial_year_start = models.DateField(help_text="Start of the current financial year")
    financial_year_end = models.DateField(help_text="End of the current financial year")

    # Voucher numbering
    voucher_number_padding = models.PositiveSmallIntegerField(
        default=6, validators=[MinValueValidator(1)],
        help_text="Digits used for the sequence, e.g. 6 -> 000001")
    include_financial_year_in_number = models.BooleanField(
        default=True, help_text="Include the financial year code in voucher numbers (SV-2026-000001)")
    reset_sequence_each_year = models.BooleanField(
        default=True, help_text="Restart sequential numbering for every financial year")
    number_separator = models.CharField(max_length=3, default='-')

    # EFD
    efd_enabled = models.BooleanField("EFD receipts in use", default=True)
    efd_serial_number = models.CharField("EFD serial number", max_length=100, blank=True)
    efd_duplicate_policy = models.CharField(max_length=10, choices=EFD_POLICIES, default=EFD_WARN)
    default_vat_rate = models.DecimalField("Default VAT rate (%)", max_digits=5, decimal_places=2,
                                           default=Decimal('18.00'))

    # Period locking
    period_lock_date = models.DateField(
        null=True, blank=True,
        help_text="Transactions dated on or before this date need the post-into-closed-period permission")
    allow_backdated_entries = models.BooleanField(
        "Allow prior-dated entries", default=True,
        help_text="Allow transaction dates earlier than today (subject to financial-year locking)")

    # Defaults that speed voucher entry up
    default_cash_account = models.ForeignKey('LedgerAccount', null=True, blank=True,
                                             on_delete=models.SET_NULL, related_name='+')
    default_bank_account = models.ForeignKey('LedgerAccount', null=True, blank=True,
                                             on_delete=models.SET_NULL, related_name='+')
    default_sales_account = models.ForeignKey('LedgerAccount', null=True, blank=True,
                                              on_delete=models.SET_NULL, related_name='+')
    default_purchase_account = models.ForeignKey('LedgerAccount', null=True, blank=True,
                                                 on_delete=models.SET_NULL, related_name='+')
    default_output_vat_account = models.ForeignKey('LedgerAccount', null=True, blank=True,
                                                   on_delete=models.SET_NULL, related_name='+')
    default_input_vat_account = models.ForeignKey('LedgerAccount', null=True, blank=True,
                                                  on_delete=models.SET_NULL, related_name='+')

    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey('users.User', null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='+')

    class Meta:
        verbose_name = 'Accounting configuration'
        verbose_name_plural = 'Accounting configuration'

    def __str__(self):
        return "Accounting configuration"

    def clean(self):
        from django.core.exceptions import ValidationError
        if (self.financial_year_start and self.financial_year_end
                and self.financial_year_end <= self.financial_year_start):
            raise ValidationError({'financial_year_end': 'Financial year end must be after the start date.'})

    def save(self, *args, **kwargs):
        """Singleton, the same way `core.SystemSettings` is."""
        if not self.pk and AccountingSettings.objects.exists():
            raise ValueError("There is only ever one accounting configuration row; "
                             "edit the existing one.")
        return super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls):
        """The configuration row, created with sensible defaults when the
        books are new."""
        row = cls.objects.order_by('pk').first()
        if row is None:
            today = timezone.localdate()
            row = cls.objects.create(
                financial_year_start=today.replace(month=1, day=1),
                financial_year_end=today.replace(month=12, day=31),
            )
        return row

    @property
    def currency_code(self):
        base = Currency.base()
        return base.code if base else 'TZS'

    @property
    def currency_symbol(self):
        base = Currency.base()
        return base.display_symbol if base else 'TZS'


class VoucherType(models.Model):
    """Per-type settings — just the label and the prefix. The six codes are
    fixed by the engine; only how they are *numbered* is configurable."""

    code = models.CharField(max_length=10, unique=True)
    name = models.CharField(max_length=50)
    prefix = models.CharField(max_length=10, help_text="Used in voucher numbers, e.g. SV")
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['pk']
        verbose_name = 'Voucher type'

    def __str__(self):
        return f"{self.name} ({self.prefix})"

    @classmethod
    def ensure_defaults(cls):
        for code, label in Voucher.TYPES:
            cls.objects.get_or_create(code=code,
                                      defaults={'name': label, 'prefix': Voucher.PREFIX[code]})

    @classmethod
    def prefix_for(cls, code):
        row = cls.objects.filter(code=code).first()
        return row.prefix if row else Voucher.PREFIX[code]


class VoucherNumberSequence(models.Model):
    """The last sequence number used per voucher type, and per financial year
    when numbering restarts yearly."""

    voucher_type = models.CharField(max_length=10)
    financial_year = models.ForeignKey(FinancialYear, null=True, blank=True, on_delete=models.PROTECT,
                                       related_name='sequences')
    last_number = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['voucher_type', 'financial_year'],
                                               name='unique_sequence_per_type_year')]
        verbose_name = 'Voucher number sequence'

    def __str__(self):
        year = self.financial_year.code if self.financial_year_id else 'all years'
        return f"{self.voucher_type} / {year}: {self.last_number}"


class AccountingAuditLog(models.Model):
    """Who did what in the books, and from which address.

    Separate from `core.SystemActivity`, which is the shop floor's live feed:
    this one is the accounting audit trail, and it keeps the status a document
    moved *from* and *to*, so a voucher's whole life can be read back.
    """

    CREATED, EDITED, POSTED = 'CREATED', 'EDITED', 'POSTED'
    CANCELLED, REVERSED = 'CANCELLED', 'REVERSED'
    ALLOCATED, UNALLOCATED, DELETED, SETTINGS = 'ALLOCATED', 'UNALLOCATED', 'DELETED', 'SETTINGS'
    ACTIONS = (
        (CREATED, 'Created'), (EDITED, 'Edited'), (POSTED, 'Posted'),
        (CANCELLED, 'Cancelled'), (REVERSED, 'Reversed'),
        (ALLOCATED, 'Allocated'), (UNALLOCATED, 'Unallocated'),
        (DELETED, 'Deleted'), (SETTINGS, 'Settings changed'),
    )

    user = models.ForeignKey('users.User', null=True, blank=True, on_delete=models.SET_NULL,
                             related_name='accounting_audit_logs')
    username = models.CharField(max_length=150, blank=True)
    action = models.CharField(max_length=20, choices=ACTIONS, db_index=True)
    voucher = models.ForeignKey('Voucher', null=True, blank=True, on_delete=models.SET_NULL,
                                related_name='audit_logs')
    voucher_number = models.CharField(max_length=40, blank=True, db_index=True)
    model_name = models.CharField(max_length=100, blank=True)
    object_id = models.CharField(max_length=50, blank=True)
    object_repr = models.CharField(max_length=255, blank=True)
    previous_status = models.CharField(max_length=20, blank=True)
    new_status = models.CharField(max_length=20, blank=True)
    description = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['-timestamp', '-pk']
        verbose_name = 'Accounting audit entry'
        verbose_name_plural = 'Accounting audit trail'

    def __str__(self):
        return f"{self.timestamp:%Y-%m-%d %H:%M} {self.username} {self.action} {self.object_repr}"


# ---------------------------------------------------------------------------
# General ledger — the chart of accounts, vouchers, and the entries they post
# ---------------------------------------------------------------------------

class LedgerAccount(models.Model):
    """One ledger in the chart of accounts.

    Every ledger has a *kind*, and the kind is what the voucher screens use to
    decide which dropdown a ledger belongs in: Receipts debit a bank account or
    cash book and credit anything else, Payments the other way round, Contra
    moves money between bank accounts and cash books, and a Journal may touch
    any ledger at all (see `Voucher.RULES`).

    Bank accounts, customers and suppliers are not typed in twice: a ledger is
    kept for every `BankAccount`, `sales.Customer` and `inventory.Supplier`
    (`apps/finance/vouchers.py::sync_chart_of_accounts`, also run from the
    signals when one is created), linked back through the one-to-one fields
    so a customer ledger knows which invoices to offer for allocation.

    The chart is *hierarchical*: a ledger may sit under a `parent`, which is
    either a group (header) account or one of the two control accounts. A
    group account is never posted to — it only organises the chart and carries
    the sum of its children. The two control accounts, Accounts Receivable and
    Accounts Payable, are the parents every customer and supplier sub-ledger
    hangs under, which is what makes a control account always equal the sum of
    its parties.

    `kind` and `account_type` answer different questions. `kind` decides which
    voucher dropdown a ledger appears in; `account_type` (asset / liability /
    equity / income / expense) decides which side of the trial balance and
    which statement it lands in. A new ledger takes its `account_type` from
    its kind unless one is given.
    """
    ASSET, LIABILITY, EQUITY, INCOME, EXPENSE = 'ASSET', 'LIABILITY', 'EQUITY', 'INCOME', 'EXPENSE'
    TYPES = (
        (ASSET, 'Asset'),
        (LIABILITY, 'Liability'),
        (EQUITY, 'Equity'),
        (INCOME, 'Income'),
        (EXPENSE, 'Expense'),
    )
    CATEGORIES = (
        ('CURRENT_ASSET', 'Current asset'),
        ('FIXED_ASSET', 'Fixed asset'),
        ('OTHER_ASSET', 'Other asset'),
        ('CURRENT_LIABILITY', 'Current liability'),
        ('LONG_TERM_LIABILITY', 'Long-term liability'),
        ('EQUITY', 'Equity'),
        ('REVENUE', 'Revenue'),
        ('OTHER_INCOME', 'Other income'),
        ('COST_OF_SALES', 'Cost of sales'),
        ('OPERATING_EXPENSE', 'Operating expense'),
        ('OTHER_EXPENSE', 'Other expense'),
    )
    VAT_OUTPUT, VAT_INPUT = 'OUTPUT', 'INPUT'
    VAT_KINDS = (
        ('', 'Not a VAT ledger'),
        (VAT_OUTPUT, 'Output VAT (sales)'),
        (VAT_INPUT, 'Input VAT (purchases)'),
    )
    # Which account type each kind belongs to when nobody says otherwise.
    TYPE_FOR_KIND = {
        'bank': ASSET, 'cash': ASSET, 'customer': ASSET, 'asset': ASSET,
        'supplier': LIABILITY, 'liability': LIABILITY, 'tax': LIABILITY,
        'income': INCOME, 'expense': EXPENSE, 'equity': EQUITY,
    }
    CATEGORY_FOR_KIND = {
        'bank': 'CURRENT_ASSET', 'cash': 'CURRENT_ASSET', 'customer': 'CURRENT_ASSET',
        'asset': 'OTHER_ASSET', 'supplier': 'CURRENT_LIABILITY', 'liability': 'CURRENT_LIABILITY',
        'tax': 'CURRENT_LIABILITY', 'income': 'REVENUE', 'expense': 'OPERATING_EXPENSE',
        'equity': 'EQUITY',
    }
    KINDS = (
        ('bank', 'Bank Account'),
        ('cash', 'Cash Book'),
        ('customer', 'Customer'),
        ('supplier', 'Supplier'),
        ('income', 'Income'),
        ('expense', 'Expense'),
        ('asset', 'Other Asset'),
        ('liability', 'Other Liability'),
        ('tax', 'Tax'),
        ('equity', 'Equity'),
    )
    # The ledgers money physically sits in — one side of a Receipt or Payment,
    # both sides of a Contra.
    MONEY_KINDS = ('bank', 'cash')
    # Which side a ledger's balance normally sits on.
    DEBIT_KINDS = ('bank', 'cash', 'customer', 'asset', 'expense')
    CODE_PREFIX = {
        'bank': 'BK', 'cash': 'CB', 'customer': 'CU', 'supplier': 'SU', 'income': 'IN',
        'expense': 'EX', 'asset': 'AS', 'liability': 'LI', 'tax': 'TX', 'equity': 'EQ',
    }
    SIDES = (('debit', 'Debit'), ('credit', 'Credit'))

    code = models.CharField(max_length=20, unique=True, blank=True,
                            help_text="Auto-generated from the kind if left blank, e.g. CU-0012")
    name = models.CharField(max_length=150)
    kind = models.CharField(max_length=10, choices=KINDS, db_index=True)
    account_type = models.CharField(max_length=10, choices=TYPES, blank=True, db_index=True,
                                    help_text="Which side of the trial balance this ledger belongs to; "
                                              "taken from the kind when left blank")
    category = models.CharField(max_length=25, choices=CATEGORIES, blank=True)
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT,
                               related_name='children',
                               help_text="A group account, or a customer/supplier control account")
    is_group = models.BooleanField("Group / header account", default=False,
                                   help_text="Group accounts organise the chart and are never posted to")
    is_customer_control = models.BooleanField(
        "Customer control account", default=False,
        help_text="Accounts Receivable control — customer sub-ledgers hang under it")
    is_supplier_control = models.BooleanField(
        "Supplier control account", default=False,
        help_text="Accounts Payable control — supplier sub-ledgers hang under it")
    vat_kind = models.CharField("VAT ledger type", max_length=10, choices=VAT_KINDS, blank=True, default='')
    currency = models.ForeignKey(Currency, null=True, blank=True, on_delete=models.PROTECT,
                                 related_name='accounts',
                                 help_text="Currency of this ledger (blank = base currency). A foreign-currency "
                                           "bank account can only be used on vouchers in the same currency.")

    bank_account = models.OneToOneField(BankAccount, on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name='ledger')
    customer = models.OneToOneField('sales.Customer', on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='ledger')
    supplier = models.OneToOneField('inventory.Supplier', on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='ledger')

    opening_balance = models.DecimalField(max_digits=14, decimal_places=2, default=0,
                                          help_text="Balance brought forward when the books began")
    opening_side = models.CharField(max_length=6, choices=SIDES, blank=True,
                                    help_text="Which side the opening balance sits on; "
                                              "defaults to the ledger's normal side")
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['kind', 'code']
        verbose_name = 'Ledger Account'
        indexes = [models.Index(fields=['account_type'])]

    # -------------------------------------------------------------- what it is
    @property
    def is_money(self):
        return self.kind in self.MONEY_KINDS

    @property
    def is_bank_account(self):
        return self.kind == 'bank'

    @property
    def is_cash_book(self):
        return self.kind == 'cash'

    @property
    def is_cash_or_bank(self):
        return self.is_money

    @property
    def is_customer_account(self):
        return self.customer_id is not None

    @property
    def is_supplier_account(self):
        return self.supplier_id is not None

    @property
    def is_control_account(self):
        return self.is_customer_control or self.is_supplier_control

    @property
    def is_postable(self):
        """A voucher line may only name a ledger, never a group or a control
        account — post to the party's own sub-ledger instead."""
        return self.is_active and not self.is_group and not self.is_control_account

    @property
    def ledger_kind(self):
        """What to call this ledger on screen."""
        if self.is_group:
            return 'Group'
        if self.is_customer_control:
            return 'Customer Control'
        if self.is_supplier_control:
            return 'Supplier Control'
        if self.is_customer_account:
            return 'Customer Account'
        if self.is_supplier_account:
            return 'Supplier Account'
        if self.vat_kind == self.VAT_OUTPUT:
            return 'Output VAT'
        if self.vat_kind == self.VAT_INPUT:
            return 'Input VAT'
        return self.get_kind_display()

    def is_vat_ledger(self, vat_kind):
        """Is this the Output (sales) or Input (purchases) VAT ledger?

        A flagged ledger is matched on `vat_kind`. For a chart seeded before
        the flag existed, a ledger named "VAT ... on sales" / "... purchases"
        — or simply "VAT", which says neither — is accepted too, so the
        default `Output VAT` / `Input VAT` ledgers work without being edited.
        """
        if self.vat_kind:
            return self.vat_kind == vat_kind
        name = (self.name or '').lower()
        if 'vat' not in name:
            return False
        mine, other = ('sale', 'purchase') if vat_kind == self.VAT_OUTPUT else ('purchase', 'sale')
        return mine in name or other not in name

    @property
    def currency_code(self):
        if self.currency_id:
            return self.currency.code
        base = Currency.base()
        return base.code if base else 'TZS'

    # ---------------------------------------------------------- the hierarchy
    def descendants(self):
        """Every ledger below this one, breadth-first, excluding itself."""
        found, frontier = [], [self.pk]
        while frontier:
            children = list(LedgerAccount.objects.filter(parent_id__in=frontier))
            found.extend(children)
            frontier = [c.pk for c in children]
        return found

    def descendant_ids(self, include_self=True):
        ids = [a.pk for a in self.descendants()]
        if include_self:
            ids.append(self.pk)
        return ids

    def ancestors(self):
        found, node = [], self.parent
        while node is not None:
            found.append(node)
            node = node.parent
        return found

    @property
    def level(self):
        return len(self.ancestors())

    # -------------------------------------------------------------- balances
    @property
    def normal_side(self):
        return 'debit' if self.kind in self.DEBIT_KINDS else 'credit'

    @property
    def normal_balance(self):
        """'DR' or 'CR', read off the account type rather than the kind — what
        the trial balance uses."""
        account_type = self.account_type or self.TYPE_FOR_KIND.get(self.kind, self.ASSET)
        return 'DR' if account_type in (self.ASSET, self.EXPENSE) else 'CR'

    @property
    def opening_debit(self):
        """The opening balance expressed as debit-minus-credit."""
        side = self.opening_side or self.normal_side
        amount = self.opening_balance or 0
        return amount if side == 'debit' else -amount

    # `signed_opening_balance` is the name the ported reports use; it is the
    # same figure as `opening_debit`, kept as an alias rather than a second
    # way of working it out.
    @property
    def signed_opening_balance(self):
        return self.opening_debit

    def balance(self, as_of=None, from_date=None, financial_year=None):
        """Signed balance (debit positive) from GL entries plus openings. A
        group or control account aggregates everything beneath it."""
        from .ledger_services import account_balance
        return account_balance(self, as_of=as_of, from_date=from_date, financial_year=financial_year)

    def balance_display(self, as_of=None):
        """(amount, 'DR'|'CR') for presentation."""
        balance = self.balance(as_of=as_of)
        return abs(balance), ('DR' if balance >= 0 else 'CR')

    def foreign_balance(self, as_of=None):
        """Balance in the ledger's own currency (foreign-currency ledgers only)."""
        if not self.currency_id:
            return self.balance(as_of=as_of)
        qs = self.gl_entries.filter(currency_id=self.currency_id)
        if as_of:
            qs = qs.filter(date__lte=as_of)
        agg = qs.aggregate(d=Sum('foreign_debit'), c=Sum('foreign_credit'))
        return (agg['d'] or Decimal('0.00')) - (agg['c'] or Decimal('0.00'))

    # ------------------------------------------------------------------ saving
    def clean(self):
        from django.core.exceptions import ValidationError
        errors = {}
        if self.parent_id:
            if self.parent_id == self.pk:
                errors['parent'] = 'A ledger cannot be its own parent.'
            elif self.pk and self.parent_id in [a.pk for a in self.descendants()]:
                errors['parent'] = 'Cannot move a ledger under one of its own children.'
            elif self.parent and not (self.parent.is_group or self.parent.is_control_account):
                errors['parent'] = ('The parent must be a group account or a customer/supplier '
                                    'control account.')
        if self.is_group and self.is_money:
            errors['is_group'] = 'A group account cannot be a bank account or a cash book.'
        if self.is_customer_control and self.is_supplier_control:
            errors['is_supplier_control'] = ('A ledger cannot be both a customer and a supplier '
                                             'control account.')
        if self.is_control_account and self.is_group:
            errors['is_group'] = 'Control accounts hold sub-ledgers; mark them as control, not group.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if not self.account_type:
            self.account_type = self.TYPE_FOR_KIND.get(self.kind, self.ASSET)
        if not self.category:
            self.category = self.CATEGORY_FOR_KIND.get(self.kind, '')
        if not self.opening_side:
            self.opening_side = self.normal_side
        if not self.code:
            prefix = self.CODE_PREFIX.get(self.kind, 'GL')
            n = LedgerAccount.objects.filter(kind=self.kind).count() + 1
            while LedgerAccount.objects.filter(code=f"{prefix}-{n:04d}").exists():
                n += 1
            self.code = f"{prefix}-{n:04d}"
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse('finance:account_detail', args=[self.pk])

    # ------------------------------------------------ names the screens use
    # The accounting screens are the Pradeep system's templates, carried over
    # unchanged. Where that system named a field differently, the name is
    # kept here as a read-only alias rather than edited out of every template
    # — it is the templates that have to stay faithful, and an alias is far
    # easier to check than a hundred renamed references.
    @property
    def opening_balance_type(self):
        """'DR' / 'CR', the way that system stored the side."""
        return 'DR' if (self.opening_side or self.normal_side) == 'debit' else 'CR'

    @property
    def description(self):
        return self.notes

    def __str__(self):
        return f"{self.code} {self.name}"


class Voucher(models.Model):
    """One double-entry document: a set of lines whose debits equal credits.

    Six kinds, told apart by which ledgers each side may use:

        Sales     Dr customer/bank/cash      Cr income/tax          (an invoice we raised)
        Purchase  Dr expense/asset/tax       Cr supplier/bank/cash  (an invoice we got)
        Receipt   Dr bank/cash               Cr anything else       (money coming in)
        Payment   Dr anything else           Cr bank/cash           (money going out)
        Contra    Dr bank/cash               Cr bank/cash           (moving our own money)
        Journal   Dr any ledger              Cr any ledger          (everything else)

    A Sales or Purchase voucher also names the party and carries the invoice
    number and the EFD receipt number as two separate fields, so the books
    can be searched by either. Its payment status is read off its lines —
    all money ledgers on the party side is cash/bank, all customer/supplier
    is credit, a mix is partly paid — and the part left on the customer or
    supplier ledger is what a later Receipt or Payment is allocated against.

    A voucher has a life:

        draft --post--> posted --reverse--> reversed
          |                                  (an equal and opposite journal
          +--cancel--> cancelled              is posted; nothing is erased)

    A draft may be unbalanced, saved and edited at will; **posting never is**.
    `apps/finance/posting.py::VoucherPostingService.post` checks that the two
    sides balance, that every ledger is allowed where it was used, that the
    date falls in an open financial year, and that nothing allocated to an
    invoice or bill exceeds what is owed on it, then writes a
    `GeneralLedgerEntry` per line in the same transaction.

    A posted voucher is never edited or deleted. A wrong one is *reversed* —
    which posts the mirror-image journal and marks the original `reversed`,
    leaving both documents in the books. `cancelled` is for a draft that was
    never posted at all. (`cancel_voucher` also still takes a posted
    voucher's entries back out, for the books opened before reversal existed.)
    """
    TYPES = (
        ('sales', 'Sales'),
        ('purchase', 'Purchase'),
        ('receipt', 'Receipt'),
        ('payment', 'Payment'),
        ('contra', 'Contra'),
        ('journal', 'Journal'),
    )
    PREFIX = {'sales': 'SV', 'purchase': 'PU', 'receipt': 'RV', 'payment': 'PV',
              'contra': 'CV', 'journal': 'JV'}
    # Which kinds of ledger may sit on the debit and credit side of each type:
    # a tuple of kinds, 'non_money' for everything but bank/cash, or None for
    # any ledger.
    RULES = {
        'sales': (('customer',) + LedgerAccount.MONEY_KINDS, ('income', 'tax')),
        'purchase': (('expense', 'asset', 'tax'), ('supplier',) + LedgerAccount.MONEY_KINDS),
        'receipt': (LedgerAccount.MONEY_KINDS, 'non_money'),
        'payment': ('non_money', LedgerAccount.MONEY_KINDS),
        'contra': (LedgerAccount.MONEY_KINDS, LedgerAccount.MONEY_KINDS),
        'journal': (None, None),
    }
    # The two types that are an invoice, and so carry a party, an invoice
    # number, an EFD number and a payment status.
    INVOICE_TYPES = ('sales', 'purchase')
    STATUS_CHOICES = (
        ('draft', 'Draft'),
        ('posted', 'Posted'),
        ('reversed', 'Reversed'),
        ('cancelled', 'Cancelled'),
    )
    # Two different questions, and they have different answers.
    #
    # `LIVE_STATUSES` — whose **ledger rows are in the books**. A reversed
    # voucher's entries stay put: the reversing journal cancels them out
    # rather than erasing them, so both documents are there to be read.
    LIVE_STATUSES = ('posted', 'reversed')
    # `EFFECTIVE_STATUSES` — whose **effects still stand**: its allocations
    # count, and the invoice number on it is still taken. Only `posted`.
    # A reversal undoes the voucher's effect, and it has no mirror-image
    # allocation of its own — so a reversed receipt has to release the invoice
    # it cleared, and a reversed sale has to let its invoice number be keyed
    # again, which is the whole point of reversing and re-entering.
    EFFECTIVE_STATUSES = ('posted',)
    PAYMENT_STATUS = (
        ('cash', 'Cash'),
        ('bank', 'Bank'),
        ('credit', 'Credit'),
        ('partly_paid', 'Partly Paid'),
    )

    voucher_type = models.CharField(max_length=10, choices=TYPES, db_index=True)
    number = models.CharField(max_length=40, unique=True, blank=True,
                              help_text="Assigned when the draft is first saved, e.g. RV-2026-000012")
    sequence_number = models.PositiveIntegerField(default=0,
                                                  help_text="The sequence this number came from")
    financial_year = models.ForeignKey(FinancialYear, on_delete=models.PROTECT, null=True, blank=True,
                                       related_name='vouchers')
    date = models.DateField(db_index=True)
    description = models.TextField(blank=True)
    reference = models.CharField(max_length=100, blank=True,
                                 help_text="Cheque number, external reference, etc.")
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0,
                                help_text="Total of the debit side (which equals the credit side)")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='posted', db_index=True)

    # Currency. The ledger is always kept in the base currency: `total_debit`
    # and `total_credit` are the amounts as entered, `base_total_*` the same
    # at this voucher's rate, and the base figures are what reach the GL.
    currency = models.ForeignKey(Currency, null=True, blank=True, on_delete=models.PROTECT,
                                 related_name='vouchers',
                                 help_text="Currency the amounts are entered in (blank = base currency)")
    exchange_rate = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal('1'),
                                        validators=[MinValueValidator(Decimal('0.000001'))],
                                        help_text="Units of the base currency for 1 unit of the "
                                                  "voucher currency")
    total_debit = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    total_credit = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    base_total_debit = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    base_total_credit = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    # Sales and Purchase vouchers only.
    customer = models.ForeignKey('sales.Customer', on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='vouchers')
    supplier = models.ForeignKey('inventory.Supplier', on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='vouchers')
    invoice_number = models.CharField(max_length=50, blank=True, db_index=True,
                                      help_text="Our sales invoice number, or the supplier's invoice number")
    efd_number = models.CharField(max_length=50, blank=True, db_index=True,
                                  help_text="EFD receipt (RCT) number, kept apart from the invoice number")
    payment_status = models.CharField(max_length=12, choices=PAYMENT_STATUS, blank=True,
                                      help_text="Read off the lines at posting")
    net_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0,
                                     help_text="VAT-exclusive amount")
    vat_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0,
                                     help_text="The part of the total on tax ledgers")
    vat_account = models.ForeignKey('LedgerAccount', null=True, blank=True, on_delete=models.PROTECT,
                                    related_name='vat_vouchers',
                                    help_text="Output VAT ledger (sales) or Input VAT ledger (purchases)")

    # A reversal is an ordinary Journal voucher that points back at what it undid.
    reversal_of = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT,
                                    related_name='reversals')

    created_by = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True,
                                   related_name='vouchers')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_by = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='vouchers_updated')
    updated_at = models.DateTimeField(auto_now=True, null=True)
    posted_by = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='vouchers_posted')
    posted_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='cancelled_vouchers')
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.TextField(blank=True)

    class Meta:
        ordering = ['-date', '-id']
        indexes = [
            models.Index(fields=['voucher_type', 'status']),
            models.Index(fields=['date', 'voucher_type']),
        ]
        permissions = [
            ('post_voucher', 'Can post vouchers to the General Ledger'),
            ('cancel_voucher', 'Can cancel draft vouchers'),
            ('reverse_voucher', 'Can reverse posted vouchers'),
            ('post_closed_period', 'Can post into closed / locked financial periods'),
        ]

    def save(self, *args, **kwargs):
        """A number is normally reserved by `numbering.VoucherNumberService`,
        which is sequence-backed and respects the configured prefix, padding
        and per-year reset. This fallback only covers a voucher saved without
        one — a fixture, say — so a row can never reach the table numberless."""
        if not self.number:
            from .numbering import VoucherNumberService
            VoucherNumberService().assign(self)
        super().save(*args, **kwargs)

    # ------------------------------------------------------------ what it is
    @property
    def is_invoice(self):
        return self.voucher_type in self.INVOICE_TYPES

    @property
    def is_draft(self):
        return self.status == 'draft'

    @property
    def is_posted(self):
        return self.status == 'posted'

    @property
    def is_editable(self):
        """Only a draft. Corrections to a posted voucher go through reversal."""
        return self.status == 'draft'

    @property
    def is_reversal(self):
        return self.reversal_of_id is not None

    @property
    def party(self):
        return self.customer or self.supplier

    @property
    def needs_customer(self):
        return self.voucher_type == 'sales'

    @property
    def needs_supplier(self):
        return self.voucher_type == 'purchase'

    @property
    def type_prefix(self):
        return VoucherType.prefix_for(self.voucher_type)

    # ------------------------------------------------------------- balancing
    @property
    def difference(self):
        return (self.total_debit or Decimal('0.00')) - (self.total_credit or Decimal('0.00'))

    @property
    def is_balanced(self):
        return self.difference == 0 and (self.total_debit or 0) > 0

    @property
    def currency_code(self):
        if self.currency_id:
            return self.currency.code
        base = Currency.base()
        return base.code if base else 'TZS'

    @property
    def is_foreign_currency(self):
        return bool(self.currency_id) and not self.currency.is_base

    def recalculate_totals(self, save=True):
        """Recompute the denormalised totals from the lines, in both the
        voucher currency and the base currency."""
        agg = self.lines.aggregate(
            d=Sum('amount', filter=models.Q(side='debit')),
            c=Sum('amount', filter=models.Q(side='credit')),
            bd=Sum('base_amount', filter=models.Q(side='debit')),
            bc=Sum('base_amount', filter=models.Q(side='credit')),
        )
        zero = Decimal('0.00')
        self.total_debit = agg['d'] or zero
        self.total_credit = agg['c'] or zero
        self.base_total_debit = agg['bd'] or zero
        self.base_total_credit = agg['bc'] or zero
        self.total = self.total_debit
        if save and self.pk:
            Voucher.objects.filter(pk=self.pk).update(
                total_debit=self.total_debit, total_credit=self.total_credit,
                base_total_debit=self.base_total_debit, base_total_credit=self.base_total_credit,
                total=self.total,
            )
        return self.total_debit, self.total_credit

    def delete(self, *args, **kwargs):
        if self.status not in ('draft', 'cancelled'):
            raise models.ProtectedError(
                "Only a draft voucher can be deleted. A posted voucher must be reversed.", [self])
        return super().delete(*args, **kwargs)

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse('finance:voucher_detail', args=[self.pk])

    # ------------------------------------------------ names the screens use
    # Read-only aliases so the accounting templates — the Pradeep system's,
    # carried over unchanged — can keep that system's field names. See the
    # same note on `LedgerAccount`.
    @property
    def voucher_number(self):
        return self.number

    @property
    def transaction_date(self):
        return self.date

    @property
    def narration(self):
        return self.description

    @property
    def efd_rct_number(self):
        return self.efd_number

    @property
    def cancellation_reason(self):
        return self.cancel_reason

    @property
    def vat_exclusive_amount(self):
        return self.net_amount

    @property
    def total_amount(self):
        return self.total

    def __str__(self):
        return f"{self.number} ({self.get_voucher_type_display()}) {self.total}"


class VoucherLine(models.Model):
    """One side of one leg of a voucher: this ledger, this much, debit or credit.

    `amount` is what was keyed, in the voucher's currency; `base_amount` is
    the same at the voucher's exchange rate, and that is the figure the
    General Ledger takes. On a base-currency voucher the two are equal.
    """
    SIDES = LedgerAccount.SIDES

    voucher = models.ForeignKey(Voucher, on_delete=models.CASCADE, related_name='lines')
    account = models.ForeignKey(LedgerAccount, on_delete=models.PROTECT, related_name='voucher_lines')
    side = models.CharField(max_length=6, choices=SIDES)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    base_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0,
                                      help_text="`amount` converted to the base currency")
    narration = models.CharField(max_length=200, blank=True)
    position = models.PositiveSmallIntegerField(default=0)
    # Denormalised off the account so a statement can be filtered by party
    # without a join through the chart of accounts.
    customer = models.ForeignKey('sales.Customer', on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='voucher_lines')
    supplier = models.ForeignKey('inventory.Supplier', on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='voucher_lines')

    class Meta:
        ordering = ['voucher', 'side', 'position', 'id']

    @property
    def is_debit(self):
        return self.side == 'debit'

    @property
    def debit(self):
        return self.amount if self.side == 'debit' else Decimal('0.00')

    @property
    def credit(self):
        return self.amount if self.side == 'credit' else Decimal('0.00')

    # Aliases for the carried-over accounting templates; see `LedgerAccount`.
    # Note the pair is crossed: a *line's* narration is that system's
    # `description`, while a *voucher's* description is its `narration`.
    @property
    def line_number(self):
        return (self.position or 0) + 1

    @property
    def description(self):
        return self.narration

    def save(self, *args, **kwargs):
        """Keep the party references and the base amount in step with the
        line, so neither can drift from the ledger it names."""
        if self.account_id:
            self.customer_id = self.account.customer_id
            self.supplier_id = self.account.supplier_id
        if not self.base_amount:
            self.base_amount = self.amount
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.voucher.number} {self.side} {self.account} {self.amount}"


class InvoiceQuerySet(models.QuerySet):
    def open(self):
        return self.exclude(status='cancelled')

    def with_allocated(self):
        return self.annotate(
            allocated=models.functions.Coalesce(
                Sum('allocations__amount',
                    filter=models.Q(
                        allocations__line__voucher__status__in=Voucher.EFFECTIVE_STATUSES)),
                models.Value(Decimal('0.00')),
                output_field=models.DecimalField(max_digits=18, decimal_places=2),
            )
        )

    def outstanding(self):
        return self.open().with_allocated().filter(allocated__lt=models.F('original_amount'))


class Invoice(models.Model):
    """A customer invoice or supplier bill *as the books know it*.

    One row per posted Sales or Purchase voucher that names a party, plus one
    per party opening balance. This is the register a Receipt or a Payment is
    allocated against, and what the outstanding and ageing reports read.

    It does not replace the shop floor's own records: a till `Sale` and a
    `PurchaseOrder` are still offered for allocation in their own right (see
    `apps/finance/vouchers.py::outstanding_invoices`). An `Invoice` row is the
    books' record of an invoice the till never saw — back-dated work, a
    supplier bill keyed straight into the ledger, an opening balance.
    """

    SALES, PURCHASE, OPENING = 'sales', 'purchase', 'opening'
    KINDS = (
        (SALES, 'Customer invoice'),
        (PURCHASE, 'Supplier bill'),
        (OPENING, 'Opening balance'),
    )
    STATUSES = (
        ('open', 'Open'),
        ('partly_paid', 'Partly paid'),
        ('paid', 'Paid'),
        ('cancelled', 'Cancelled'),
    )

    kind = models.CharField(max_length=10, choices=KINDS, db_index=True)
    customer = models.ForeignKey('sales.Customer', null=True, blank=True, on_delete=models.CASCADE,
                                 related_name='ledger_invoices')
    supplier = models.ForeignKey('inventory.Supplier', null=True, blank=True, on_delete=models.CASCADE,
                                 related_name='ledger_invoices')
    voucher = models.OneToOneField(Voucher, null=True, blank=True, on_delete=models.CASCADE,
                                   related_name='invoice')
    invoice_number = models.CharField(max_length=60, db_index=True)
    efd_number = models.CharField(max_length=60, blank=True, db_index=True)
    invoice_date = models.DateField(db_index=True)
    due_date = models.DateField(null=True, blank=True)
    original_amount = models.DecimalField(max_digits=18, decimal_places=2)
    status = models.CharField(max_length=12, choices=STATUSES, default='open', db_index=True)
    description = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = InvoiceQuerySet.as_manager()

    class Meta:
        ordering = ['invoice_date', 'id']
        constraints = [
            models.CheckConstraint(condition=models.Q(customer__isnull=False) | models.Q(supplier__isnull=False),
                                   name='invoice_has_party'),
            # Cancelled rows are excluded on purpose. Reversing a sales
            # voucher cancels the invoice it raised, and the corrected invoice
            # is then keyed under the same number — which the index would
            # otherwise refuse with an IntegrityError rather than a message.
            models.UniqueConstraint(fields=['kind', 'customer', 'invoice_number'],
                                    condition=models.Q(customer__isnull=False)
                                    & ~models.Q(status='cancelled'),
                                    name='unique_customer_invoice_number'),
            models.UniqueConstraint(fields=['kind', 'supplier', 'invoice_number'],
                                    condition=models.Q(supplier__isnull=False)
                                    & ~models.Q(status='cancelled'),
                                    name='unique_supplier_invoice_number'),
        ]

    def __str__(self):
        return f"{self.invoice_number} ({self.get_kind_display()})"

    @property
    def party(self):
        return self.customer or self.supplier

    @property
    def allocated_amount(self):
        """Only allocations on a posted voucher count. A draft allocation is
        a plan rather than a payment, and a reversed one has been undone."""
        if hasattr(self, 'allocated'):
            return self.allocated or Decimal('0.00')
        agg = self.allocations.filter(
            line__voucher__status__in=Voucher.EFFECTIVE_STATUSES).aggregate(t=Sum('amount'))
        return agg['t'] or Decimal('0.00')

    @property
    def outstanding_amount(self):
        if self.status == 'cancelled':
            return Decimal('0.00')
        return (self.original_amount or Decimal('0.00')) - self.allocated_amount

    def refresh_status(self, save=True):
        if self.status == 'cancelled':
            return self.status
        if hasattr(self, 'allocated'):
            del self.allocated  # force a fresh aggregate
        allocated = self.allocated_amount
        if allocated <= 0:
            self.status = 'open'
        elif allocated < (self.original_amount or Decimal('0.00')):
            self.status = 'partly_paid'
        else:
            self.status = 'paid'
        if save and self.pk:
            Invoice.objects.filter(pk=self.pk).update(status=self.status)
        return self.status


class VoucherAllocation(models.Model):
    """Part of a voucher line set against one customer invoice or supplier bill.

    A customer ledger credited on a Receipt is money the customer has paid;
    the accountant says which invoices it clears. A supplier ledger debited on
    a Payment is the mirror image against bills. An invoice is one of four
    things: a till sale, a purchase order, a Sales/Purchase voucher posted in
    the books, or an `Invoice` row of the books' own register — exactly one of
    the four links is set. The link is kept by reference as well as by FK, as
    everywhere else in the books, so the allocation still reads sensibly if
    the sale or order is later removed.
    """
    line = models.ForeignKey(VoucherLine, on_delete=models.CASCADE, related_name='allocations')
    sale = models.ForeignKey('sales.Sale', on_delete=models.SET_NULL, null=True, blank=True,
                             related_name='voucher_allocations')
    purchase_order = models.ForeignKey('inventory.PurchaseOrder', on_delete=models.SET_NULL,
                                       null=True, blank=True, related_name='voucher_allocations')
    voucher = models.ForeignKey(Voucher, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='settlements',
                                help_text="The Sales or Purchase voucher this settles")
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, null=True, blank=True,
                                related_name='allocations',
                                help_text="The invoice register row this settles")
    reference = models.CharField(max_length=60, help_text="Invoice number, PO number or voucher number")
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    notes = models.CharField(max_length=255, blank=True)
    allocated_by = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='+')
    allocated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f"{self.reference}: {self.amount}"


class GeneralLedgerEntry(models.Model):
    """The General Ledger: one row per voucher line, in posting order.

    Denormalised on purpose — the date, voucher number and description are
    copied in — so a ledger statement or trial balance is one query over one
    table. Rows are only ever written by `vouchers.post_voucher` and only
    ever removed by `vouchers.cancel_voucher`.
    """
    voucher = models.ForeignKey(Voucher, on_delete=models.CASCADE, related_name='gl_entries')
    line = models.OneToOneField(VoucherLine, on_delete=models.CASCADE, related_name='gl_entry')
    account = models.ForeignKey(LedgerAccount, on_delete=models.PROTECT, related_name='gl_entries')
    financial_year = models.ForeignKey(FinancialYear, on_delete=models.PROTECT, null=True, blank=True,
                                       related_name='gl_entries')
    date = models.DateField(db_index=True)
    voucher_type = models.CharField(max_length=10, choices=Voucher.TYPES)
    voucher_number = models.CharField(max_length=40, db_index=True)
    reference = models.CharField(max_length=150, blank=True, db_index=True,
                                 help_text="Invoice number / EFD receipt number / external reference")
    description = models.CharField(max_length=300, blank=True)
    # The ledger is always kept in the base (reporting) currency.
    debit = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    credit = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    # What was actually keyed, when the voucher was in a foreign currency.
    currency = models.ForeignKey(Currency, null=True, blank=True, on_delete=models.PROTECT,
                                 related_name='gl_entries')
    exchange_rate = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal('1'))
    foreign_debit = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    foreign_credit = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    customer = models.ForeignKey('sales.Customer', null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name='gl_entries')
    supplier = models.ForeignKey('inventory.Supplier', null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name='gl_entries')
    created_by = models.ForeignKey('users.User', null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='gl_entries_created')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['date', 'id']
        verbose_name = 'General Ledger Entry'
        verbose_name_plural = 'General Ledger Entries'
        indexes = [
            models.Index(fields=['account', 'date']),
            models.Index(fields=['customer', 'date']),
            models.Index(fields=['supplier', 'date']),
        ]

    @property
    def amount(self):
        return self.debit if self.debit else self.credit

    @property
    def signed_amount(self):
        return self.debit - self.credit

    # The ported reports speak of `transaction_date`; it is this row's `date`.
    @property
    def transaction_date(self):
        return self.date

    def __str__(self):
        return f"{self.date} {self.voucher_number} {self.account}: Dr {self.debit} Cr {self.credit}"
