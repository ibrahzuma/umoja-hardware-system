from decimal import Decimal

from django.db import models
from django.db.models import Sum


class CustomerRecord(models.Model):
    """A standalone CRM entry for a customer we have traded with.

    Deliberately NOT linked to sales.Customer or sales.Sale: this register is
    maintained by hand (or seeded once from sales via the "pull from sales"
    action) and must stay editable without touching transactional data. Any
    reference back to a sale is kept as plain text in `source_invoice` — no
    foreign keys, so deleting or amending a sale never rewrites CRM history.
    """
    date = models.DateField(help_text="Date of the transaction")
    receipt_number = models.CharField(max_length=50, blank=True, verbose_name='Receipt No')
    efd_receipt_number = models.CharField(max_length=50, blank=True, verbose_name='EFD Receipt No')
    customer_name = models.CharField(max_length=200)
    tin = models.CharField(max_length=40, blank=True, verbose_name='TIN',
                           help_text="Taxpayer Identification Number")
    sales_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0.00)

    # Free-text breadcrumb when a row was pulled in from the sales module.
    # Plain text on purpose — see the class docstring.
    source_invoice = models.CharField(max_length=50, blank=True, editable=False)

    created_by = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='crm_records')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date', '-id']
        verbose_name = 'CRM Record'
        verbose_name_plural = 'CRM Records'
        indexes = [
            models.Index(fields=['customer_name']),
            models.Index(fields=['date']),
        ]

    def __str__(self):
        return f"{self.customer_name} - {self.receipt_number or self.efd_receipt_number or self.date}"

    @property
    def amount_paid(self):
        """Total received against this sale.

        Prefers a `paid_total` annotation when the queryset supplied one (the
        list endpoints do), so rendering a page of records costs one query
        rather than one per row.
        """
        annotated = getattr(self, 'paid_total', None)
        if annotated is not None:
            return Decimal(annotated)
        return Decimal(self.payments.aggregate(total=Sum('amount'))['total'] or 0)

    @property
    def balance(self):
        return Decimal(self.sales_amount or 0) - self.amount_paid

    @property
    def payment_status(self):
        """unpaid / partial / paid — derived, never stored, so it cannot drift
        out of step with the payments actually recorded."""
        if self.balance <= 0:
            return 'paid'
        return 'partial' if self.amount_paid > 0 else 'unpaid'


class CrmPayment(models.Model):
    """Money received against one CRM sale.

    Payments are kept as individual rows rather than a running total on the
    sale: a customer who settles 500 of a 1,000 sale leaves a record of that
    500 — when it came in, how, and against what reference.
    """
    METHODS = (
        ('cash', 'Cash'),
        ('bank', 'Bank Transfer'),
        ('mobile', 'Mobile Money'),
        ('cheque', 'Cheque'),
        ('other', 'Other'),
    )
    record = models.ForeignKey(CustomerRecord, on_delete=models.CASCADE, related_name='payments')
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    paid_on = models.DateField()
    method = models.CharField(max_length=20, choices=METHODS, default='cash')
    reference = models.CharField(max_length=100, blank=True,
                                help_text="Bank slip, mobile money ref, cheque number")
    from_credit = models.BooleanField(
        default=False,
        help_text="Settled from the customer's credit on account rather than new money received",
    )
    # Set only on payments mirrored from a POS sale (see apps/crm/sync.py). It is
    # the sales Transaction id, kept as a plain integer rather than a foreign key
    # so CRM history outlives the sale. Payments typed in by hand leave it null,
    # which is how the sync knows never to touch them.
    source_transaction = models.PositiveIntegerField(
        null=True, blank=True, unique=True, editable=False,
        help_text="The sales transaction this payment was mirrored from",
    )
    created_by = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='crm_payments')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-paid_on', '-id']
        verbose_name = 'CRM Payment'
        verbose_name_plural = 'CRM Payments'

    def __str__(self):
        return f"{self.record.customer_name} - {self.amount} on {self.paid_on}"


class CrmCredit(models.Model):
    """Money a customer has with us that is not tied to a particular sale.

    Two ways in: a deposit paid ahead of any order, and the excess when a
    customer settles a sale with more than it was worth. Money out is a
    CrmPayment with from_credit=True, drawn against a later sale — so the
    credit balance is always (credits in − credit-funded payments out) and
    never needs storing.

    Keyed on customer_name, matching the rest of the register: the name on the
    transaction is the customer's identity here.
    """
    SOURCES = (
        ('deposit', 'Deposit / advance'),
        ('overpayment', 'Overpayment on a sale'),
        ('adjustment', 'Adjustment'),
    )
    customer_name = models.CharField(max_length=200, db_index=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    received_on = models.DateField()
    source = models.CharField(max_length=20, choices=SOURCES, default='deposit')
    method = models.CharField(max_length=20, choices=CrmPayment.METHODS, default='cash')
    reference = models.CharField(max_length=100, blank=True)
    note = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='crm_credits')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-received_on', '-id']
        verbose_name = 'CRM Credit'
        verbose_name_plural = 'CRM Credits'

    def __str__(self):
        return f"{self.customer_name} - {self.amount} on account"


def credit_balances(customer_names=None):
    """{customer_name: credit still on account} for the given names (or all).

    Credit in, less what has already been drawn against later sales.
    """
    credits = CrmCredit.objects.all()
    spent = CrmPayment.objects.filter(from_credit=True)
    if customer_names is not None:
        names = list(customer_names)
        credits = credits.filter(customer_name__in=names)
        spent = spent.filter(record__customer_name__in=names)

    balances = {}
    for row in credits.values('customer_name').annotate(total=Sum('amount')):
        balances[row['customer_name']] = Decimal(row['total'] or 0)
    for row in spent.values('record__customer_name').annotate(total=Sum('amount')):
        name = row['record__customer_name']
        balances[name] = balances.get(name, Decimal('0')) - Decimal(row['total'] or 0)
    return balances


def credit_balance(customer_name):
    return credit_balances([customer_name]).get(customer_name, Decimal('0'))
