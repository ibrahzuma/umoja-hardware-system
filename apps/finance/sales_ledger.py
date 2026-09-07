"""Mirror every sale into the accounting ledger.

A sale made at the till is money the books have to take up, whether it was paid
in cash, part paid on a deposit, or handed over wholly on credit. Each one
arrives here as `pending` and waits for the accountant to post it.

Two rules keep the two sides from fighting, both borrowed from the CRM sync:

* **The link is the invoice number.** The FK to the sale is a convenience;
  nothing cascades off it. What leaves the ledger leaves because `drop_sale`
  decided it should.
* **A posted row is frozen.** While an entry is pending or queried its figures
  follow the sale, so a deposit taken an hour later shows up. Once posted, the
  numbers are what the books were told and must not quietly move. Only
  `sale_status` keeps following — a sale cancelled after posting then reads as
  needing a reversal instead of silently disappearing.
"""

from __future__ import annotations

from decimal import Decimal

from .models import SalesLedgerEntry

ZERO = Decimal('0')

# Transaction.PAYMENT_METHODS -> what the ledger shows
METHOD_LABELS = {
    'cash': 'Cash',
    'bank': 'Bank Transfer',
    'mobile': 'Mobile Money',
    'credit': 'Credit',
}


def customer_name_for(sale):
    if sale.customer_id and sale.customer:
        return sale.customer.name
    return sale.customer_name or 'Walk-in Customer'


def _paid_and_methods(sale):
    """What has been received against this sale, and by what means.

    A credit sale with no deposit writes no transaction at all, so an empty set
    here is exactly what "on credit" looks like.
    """
    paid = ZERO
    methods = []
    for txn in sale.transactions.all():
        if txn.transaction_type != 'income':
            continue
        paid += txn.amount or ZERO
        label = METHOD_LABELS.get(txn.payment_method, txn.payment_method)
        if label and label not in methods:
            methods.append(label)
    return paid, ', '.join(methods)


def _cost_and_commission(sale):
    """What the goods on this sale cost us, and the commission earned on them.

    Taken at sync time and frozen with the entry once it is posted: a product's
    cost moves, and the profit on a sale already in the books must not move
    with it.
    """
    cost = ZERO
    commission = ZERO
    for item in sale.items.all():
        unit_cost = getattr(item.product, 'cost', None) or ZERO
        cost += (item.quantity or 0) * unit_cost
        commission += item.commission_amount or ZERO
    return cost, commission


def settlement_for(total, paid):
    if paid <= 0:
        return 'credit'
    if paid >= (total or ZERO):
        return 'paid'
    return 'part_paid'


def sync_sale(sale):
    """Create or refresh the ledger entry for one sale.

    Returns the entry, or None when the sale does not belong in the ledger
    (no invoice number to key on).
    """
    if not sale.invoice_number:
        return None

    entry = SalesLedgerEntry.objects.filter(invoice_number=sale.invoice_number).first()

    if sale.status == 'cancelled':
        # A cancelled sale that Accounts never took up simply goes. One they
        # have posted stays, flagged, because the books have to be put right by
        # hand rather than by a row disappearing.
        if entry is None:
            return None
        if entry.status == 'posted':
            entry.sale_status = sale.status
            entry.save(update_fields=['sale_status', 'updated_at'])
            return entry
        entry.delete()
        return None

    total = sale.total_amount or ZERO
    paid, methods = _paid_and_methods(sale)
    cost, commission = _cost_and_commission(sale)

    if entry is None:
        return SalesLedgerEntry.objects.create(
            invoice_number=sale.invoice_number,
            sale=sale,
            sale_date=sale.created_at.date() if sale.created_at else None,
            customer_name=customer_name_for(sale),
            branch=sale.branch,
            sold_by=sale.user,
            total_amount=total,
            discount=sale.discount or ZERO,
            amount_paid=paid,
            settlement=settlement_for(total, paid),
            methods=methods,
            cost_of_sales=cost,
            commission_total=commission,
            sale_status=sale.status,
            status='pending',
        )

    # Always re-point the FK: a re-created sale can reuse an invoice number.
    entry.sale = sale
    entry.sale_status = sale.status

    if entry.is_open:
        entry.customer_name = customer_name_for(sale)
        entry.branch = sale.branch
        entry.sold_by = sale.user
        entry.total_amount = total
        entry.discount = sale.discount or ZERO
        entry.amount_paid = paid
        entry.settlement = settlement_for(total, paid)
        entry.methods = methods
        entry.cost_of_sales = cost
        entry.commission_total = commission

    entry.save()
    return entry


def sync_transaction(transaction):
    """Money received against a sale changes how that sale reads."""
    if transaction.sale_id and transaction.sale:
        return sync_sale(transaction.sale)
    return None


def drop_sale(sale):
    """A deleted sale takes its pending entry with it; a posted one stays.

    The posted row is what the books were told. It outlives the sale so the
    accountant can see there is something to reverse.
    """
    if not sale.invoice_number:
        return
    entry = SalesLedgerEntry.objects.filter(invoice_number=sale.invoice_number).first()
    if entry is None:
        return
    if entry.status == 'posted':
        entry.sale = None
        entry.sale_status = 'deleted'
        entry.save(update_fields=['sale', 'sale_status', 'updated_at'])
    else:
        entry.delete()


def backfill():
    """Bring the ledger up to date with every sale on record.

    Safe to re-run: it refreshes what is open and leaves posted rows alone.
    Returns (created, refreshed).
    """
    from apps.sales.models import Sale

    created = refreshed = 0
    known = set(SalesLedgerEntry.objects.values_list('invoice_number', flat=True))
    for sale in Sale.objects.prefetch_related('transactions', 'items__product').select_related(
            'customer', 'branch', 'user').iterator(chunk_size=500):
        if not sale.invoice_number:
            continue
        was_known = sale.invoice_number in known
        if sync_sale(sale) is not None:
            if was_known:
                refreshed += 1
            else:
                created += 1
    return created, refreshed
