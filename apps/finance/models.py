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
    """
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

    @property
    def is_money(self):
        return self.kind in self.MONEY_KINDS

    @property
    def normal_side(self):
        return 'debit' if self.kind in self.DEBIT_KINDS else 'credit'

    @property
    def opening_debit(self):
        """The opening balance expressed as debit-minus-credit."""
        side = self.opening_side or self.normal_side
        amount = self.opening_balance or 0
        return amount if side == 'debit' else -amount

    def save(self, *args, **kwargs):
        if not self.opening_side:
            self.opening_side = self.normal_side
        if not self.code:
            prefix = self.CODE_PREFIX.get(self.kind, 'GL')
            n = LedgerAccount.objects.filter(kind=self.kind).count() + 1
            while LedgerAccount.objects.filter(code=f"{prefix}-{n:04d}").exists():
                n += 1
            self.code = f"{prefix}-{n:04d}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.code} {self.name}"


class Voucher(models.Model):
    """One double-entry document: a set of lines whose debits equal credits.

    Four kinds, told apart only by which ledgers each side may use:

        Receipt   Dr bank/cash        Cr anything else   (money coming in)
        Payment   Dr anything else    Cr bank/cash       (money going out)
        Contra    Dr bank/cash        Cr bank/cash       (moving our own money)
        Journal   Dr any ledger       Cr any ledger      (everything else)

    A voucher is posted the moment it is saved — `vouchers.post_voucher` checks
    that the two sides balance, that every ledger is allowed where it was used,
    and that nothing allocated to an invoice or bill exceeds what is owed on
    it, then writes a `GeneralLedgerEntry` per line in the same transaction.
    Posted vouchers are never edited. A wrong one is *cancelled*, which takes
    its entries back out of the ledger while leaving the document itself for
    the audit trail.
    """
    TYPES = (
        ('receipt', 'Receipt'),
        ('payment', 'Payment'),
        ('contra', 'Contra'),
        ('journal', 'Journal'),
    )
    PREFIX = {'receipt': 'RV', 'payment': 'PV', 'contra': 'CV', 'journal': 'JV'}
    # Which kinds of ledger may sit on the debit and credit side of each type:
    # a tuple of kinds, 'non_money' for everything but bank/cash, or None for
    # any ledger.
    RULES = {
        'receipt': (LedgerAccount.MONEY_KINDS, 'non_money'),
        'payment': ('non_money', LedgerAccount.MONEY_KINDS),
        'contra': (LedgerAccount.MONEY_KINDS, LedgerAccount.MONEY_KINDS),
        'journal': (None, None),
    }
    STATUS_CHOICES = (
        ('posted', 'Posted'),
        ('cancelled', 'Cancelled'),
    )

    voucher_type = models.CharField(max_length=10, choices=TYPES, db_index=True)
    number = models.CharField(max_length=20, unique=True, blank=True,
                              help_text="Auto-generated on post, e.g. RV-000012")
    date = models.DateField(db_index=True)
    description = models.TextField(blank=True)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0,
                                help_text="Total of the debit side (which equals the credit side)")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='posted', db_index=True)

    created_by = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True,
                                   related_name='vouchers')
    created_at = models.DateTimeField(auto_now_add=True)
    cancelled_by = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='cancelled_vouchers')
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.TextField(blank=True)

    class Meta:
        ordering = ['-date', '-id']

    def save(self, *args, **kwargs):
        if not self.number:
            prefix = self.PREFIX[self.voucher_type]
            n = Voucher.objects.filter(voucher_type=self.voucher_type).count() + 1
            while Voucher.objects.filter(number=f"{prefix}-{n:06d}").exists():
                n += 1
            self.number = f"{prefix}-{n:06d}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.number} ({self.get_voucher_type_display()}) {self.total}"


class VoucherLine(models.Model):
    """One side of one leg of a voucher: this ledger, this much, debit or credit."""
    SIDES = LedgerAccount.SIDES

    voucher = models.ForeignKey(Voucher, on_delete=models.CASCADE, related_name='lines')
    account = models.ForeignKey(LedgerAccount, on_delete=models.PROTECT, related_name='voucher_lines')
    side = models.CharField(max_length=6, choices=SIDES)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    narration = models.CharField(max_length=200, blank=True)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['voucher', 'side', 'position', 'id']

    def __str__(self):
        return f"{self.voucher.number} {self.side} {self.account} {self.amount}"


class VoucherAllocation(models.Model):
    """Part of a voucher line set against one customer invoice or supplier bill.

    A customer ledger credited on a Receipt is money the customer has paid;
    the accountant says which invoices it clears. A supplier ledger debited on
    a Payment is the mirror image against purchase orders. The link is kept by
    reference as well as by FK, as everywhere else in the books, so the
    allocation still reads sensibly if the sale or order is later removed.
    """
    line = models.ForeignKey(VoucherLine, on_delete=models.CASCADE, related_name='allocations')
    sale = models.ForeignKey('sales.Sale', on_delete=models.SET_NULL, null=True, blank=True,
                             related_name='voucher_allocations')
    purchase_order = models.ForeignKey('inventory.PurchaseOrder', on_delete=models.SET_NULL,
                                       null=True, blank=True, related_name='voucher_allocations')
    reference = models.CharField(max_length=50, help_text="Invoice number or PO number")
    amount = models.DecimalField(max_digits=14, decimal_places=2)

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
    date = models.DateField(db_index=True)
    voucher_type = models.CharField(max_length=10, choices=Voucher.TYPES)
    voucher_number = models.CharField(max_length=20, db_index=True)
    description = models.CharField(max_length=300, blank=True)
    debit = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    credit = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['date', 'id']
        verbose_name = 'General Ledger Entry'
        verbose_name_plural = 'General Ledger Entries'

    def __str__(self):
        return f"{self.date} {self.voucher_number} {self.account}: Dr {self.debit} Cr {self.credit}"
