"""What a supplier still holds of ours.

Pay a supplier 20m against a 10m order and the extra 10m does not vanish — it
sits with them, and the next order should draw on it before any fresh money
goes out. That is a supplier *credit*.

Like the CRM's customer credit, it is **derived, never stored**:

    credit = overpaid - applied

    overpaid = per order, how far approved cash payments ran past the order
               total, summed across the supplier's orders (never negative per
               order: being short on one order does not cancel an overpayment
               on another — that is a balance still owed, not a credit)
    applied  = approved payments the cashier settled out of the credit
               (`from_credit=True`), which move no money

A credit application is therefore an ordinary `SupplierPayment` row carrying
`from_credit=True`: it settles the order it points at, and it draws the credit
down by the same amount. It is excluded from `overpaid` so applying credit can
never manufacture more of it.
"""

from decimal import Decimal

from django.db.models import Sum, Q

from apps.inventory.models import PurchaseOrder
from .models import SupplierPayment

ZERO = Decimal('0')


def _overpaid_by_supplier(supplier_id=None):
    """Per supplier, how far cash payments ran past each order's total."""
    orders = (PurchaseOrder.objects
              .exclude(status='cancelled')
              .filter(supplier__isnull=False))
    if supplier_id is not None:
        orders = orders.filter(supplier_id=supplier_id)
    orders = (orders
              .annotate(cash_paid=Sum(
                  'payments__amount',
                  filter=Q(payments__status='paid', payments__from_credit=False),
              ))
              .values_list('supplier_id', 'total_amount', 'cash_paid'))

    totals = {}
    for sid, total, cash_paid in orders:
        over = (cash_paid or ZERO) - (total or ZERO)
        if over > 0:
            totals[sid] = totals.get(sid, ZERO) + over
    return totals


def _applied_by_supplier(supplier_id=None):
    """Per supplier, how much credit has already been spent on other orders."""
    qs = SupplierPayment.objects.filter(status='paid', from_credit=True)
    if supplier_id is not None:
        qs = qs.filter(supplier_id=supplier_id)
    return {
        row['supplier']: row['total'] or ZERO
        for row in qs.values('supplier').annotate(total=Sum('amount'))
    }


def credit_balances(supplier_id=None):
    """{supplier_id: {'overpaid', 'applied', 'available'}} for every supplier
    who has ever overpaid. `available` never goes below zero."""
    overpaid = _overpaid_by_supplier(supplier_id)
    applied = _applied_by_supplier(supplier_id)

    out = {}
    for sid in set(overpaid) | set(applied):
        over = overpaid.get(sid, ZERO)
        used = applied.get(sid, ZERO)
        out[sid] = {
            'overpaid': over,
            'applied': used,
            'available': max(over - used, ZERO),
        }
    return out


def available_credit(supplier_id):
    """What this supplier holds of ours, ready to put against an order."""
    row = credit_balances(supplier_id).get(supplier_id)
    return row['available'] if row else ZERO


def pending_credit_use(supplier_id):
    """Credit already spoken for by applications still awaiting approval.

    Two applications queued against the same credit would both look affordable
    on their own; this is what keeps the second one honest.
    """
    total = (SupplierPayment.objects
             .filter(supplier_id=supplier_id, status='pending', from_credit=True)
             .aggregate(t=Sum('amount'))['t'])
    return total or ZERO


def spendable_credit(supplier_id):
    """Credit that can still be committed right now: what is available, less
    what pending applications have already claimed."""
    return max(available_credit(supplier_id) - pending_credit_use(supplier_id), ZERO)
