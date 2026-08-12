from django.db import models


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
