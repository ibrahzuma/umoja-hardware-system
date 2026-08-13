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
    created_by = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='crm_payments')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-paid_on', '-id']
        verbose_name = 'CRM Payment'
        verbose_name_plural = 'CRM Payments'

    def __str__(self):
        return f"{self.record.customer_name} - {self.amount} on {self.paid_on}"
