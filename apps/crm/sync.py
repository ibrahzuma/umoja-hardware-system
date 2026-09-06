"""Mirror POS sales into the CRM register.

Every sale made at the POS shows up in the CRM register, carrying its payment
state with it — paid in full, part paid, or wholly on credit — and stays in step
as money comes in against it.

Two things keep the two sides from fighting each other:

* The link is **by invoice number, not a foreign key.** `CustomerRecord.source_invoice`
  holds the sale's invoice number as plain text. Nothing here cascades: what
  leaves the register does so because `drop_sale` decided it should, not because
  the database took it away. That is what lets a row a person has worked on —
  their own payment entry, an EFD receipt number, a TIN — outlive the sale it
  came from, while a sale deleted as a mistake takes its row with it.
* Every mirrored payment records the transaction it came from
  (`CrmPayment.source_transaction`), so re-running the sync never double-counts and
  a payment typed into the CRM by hand is never mistaken for one from the POS.

Fields a person maintains — TIN, EFD receipt number, the receipt number itself —
are written once when the row is created and never overwritten afterwards. The
figures that must not drift (date, customer, amount) follow the sale.
"""

from __future__ import annotations

from decimal import Decimal

from .models import CrmPayment, CustomerRecord

# Transaction.PAYMENT_METHODS -> CrmPayment.METHODS
METHOD_MAP = {
    'cash': 'cash',
    'bank': 'bank',
    'mobile': 'mobile',
    # A 'credit' transaction is not money in hand; the POS does not create one
    # (a credit sale with no deposit writes no transaction at all), but map it
    # rather than drop a row silently if one ever appears.
    'credit': 'other',
}


def customer_name_for(sale):
    if sale.customer_id and sale.customer:
        return sale.customer.name
    return sale.customer_name or 'Walk-in Customer'


def sync_sale(sale, *, user=None):
    """Create or refresh the CRM row for one sale, payments included.

    Returns the CustomerRecord, or None when the sale should not be in the
    register (cancelled, or no invoice number to key on).
    """
    if not sale.invoice_number:
        return None

    if sale.status == 'cancelled':
        drop_sale(sale)
        return None

    record = CustomerRecord.objects.filter(source_invoice=sale.invoice_number).first()
    if record is None:
        record = CustomerRecord.objects.create(
            date=sale.created_at.date(),
            receipt_number=sale.invoice_number,
            customer_name=customer_name_for(sale),
            sales_amount=sale.total_amount or Decimal('0'),
            source_invoice=sale.invoice_number,
            created_by=user or sale.user,
        )
    else:
        # Only the figures that must not drift. Anything a person types
        # (tin, efd_receipt_number, receipt_number) is left alone.
        record.date = sale.created_at.date()
        record.customer_name = customer_name_for(sale)
        record.sales_amount = sale.total_amount or Decimal('0')
        record.save(update_fields=['date', 'customer_name', 'sales_amount', 'updated_at'])

    sync_payments(sale, record)
    return record


def sync_payments(sale, record=None):
    """Mirror the sale's transactions onto its CRM row, without duplicating."""
    if record is None:
        record = CustomerRecord.objects.filter(source_invoice=sale.invoice_number).first()
    if record is None:
        return

    seen = []
    for txn in sale.transactions.all():
        seen.append(txn.id)
        CrmPayment.objects.update_or_create(
            source_transaction=txn.id,
            defaults={
                'record': record,
                'amount': txn.amount,
                'paid_on': txn.created_at.date(),
                'method': METHOD_MAP.get(txn.payment_method, 'other'),
                'reference': txn.reference or '',
                'created_by': record.created_by,
            },
        )

    # A transaction removed on the sales side should not linger here. Only
    # mirrored rows are touched — payments entered by hand have no
    # source_transaction and are never swept up.
    stale = record.payments.filter(source_transaction__isnull=False)
    if seen:
        stale = stale.exclude(source_transaction__in=seen)
    stale.delete()


def sync_transaction(txn):
    """One payment landed against a sale — mirror just that sale's row."""
    if txn.sale_id is None:
        return
    sale = txn.sale
    record = CustomerRecord.objects.filter(source_invoice=sale.invoice_number).first()
    if record is None:
        # First money against a sale not yet in the register (e.g. a sale that
        # predates the sync): bring the whole sale over.
        sync_sale(sale)
        return
    sync_payments(sale, record)


def drop_sale(sale):
    """Take a sale back out of the register when it is cancelled or deleted.

    A sale that never really happened should not linger in the register as a
    debt or a figure in the totals, so the row goes with it.

    The one exception is work a person has done on that row by hand: their own
    payment entry, or the EFD receipt number / TIN they typed in. Those are
    filed records, not a mirror of the sale, so the row is left for a human to
    deal with rather than deleted from under them.

    Returns True when the row was removed.
    """
    record = CustomerRecord.objects.filter(source_invoice=sale.invoice_number).first()
    if record is None:
        return False
    touched_by_hand = (
        record.payments.filter(source_transaction__isnull=True).exists()
        or bool(record.tin)
        or bool(record.efd_receipt_number)
    )
    if touched_by_hand:
        return False
    record.delete()
    return True


def sync_all(queryset=None, *, user=None):
    """Backfill: bring a queryset of sales (default: all) into the register.

    Returns (created_or_updated, skipped).
    """
    from apps.sales.models import Sale

    sales = queryset if queryset is not None else Sale.objects.all()
    done = skipped = 0
    for sale in sales.select_related('customer', 'user').prefetch_related('transactions'):
        if sync_sale(sale, user=user) is None:
            skipped += 1
        else:
            done += 1
    return done, skipped
