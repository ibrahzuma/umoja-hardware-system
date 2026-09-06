"""The stock ledger: what came in, what went out, item by item.

Nothing here is stored. Every row is read back off the document that actually
moved the stock, so the ledger can never drift away from the paperwork it
reports on, and the reference on each row is the number a person would quote
when they ring up asking about it — PO-00012, an invoice number, a supplier's
delivery note, ADJ-00007.

Stock is credited by:
  * a delivery round taken into stock (`DeliveryCheck` -> `_receive_delivery`)
  * a direct purchase (`Purchase`)
  * a goods received note line (`GRNItem`)
  * an inbound branch transfer, or an 'addition' adjustment

and debited by:
  * a dispatched sale — stock leaves at dispatch, not when the sale is written
  * an outbound branch transfer, or a 'deduction' adjustment

A 'correction' adjustment sets the quantity outright, so it is neither an in
nor an out: it carries `set_to` and the running balance restarts from there.

The balance column is run forward over an item's whole history and then anchored
so the last row lands on the quantity actually on hand — stock that predates the
records (an opening quantity typed in with the product, say) shows up as the
brought-forward opening rather than as a silent discrepancy at the bottom.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from django.db.models import Q
from django.utils import timezone

from .models import (
    DeliveryCheckItem, GRNItem, Purchase, StockAdjustment, StockTransfer,
)

# Delivery rounds whose quantities reached stock. A short round only counts once
# an Admin has confirmed it; everything else is still in flight.
RECEIVED_DECISIONS = ('complete', 'approved')


@dataclass
class Movement:
    """One line of the ledger."""
    date: datetime
    reference: str
    kind: str          # slug used for the row's colour/icon
    label: str         # what happened, in words
    detail: str        # who it was with — supplier, customer, other branch
    branch: str
    product_id: int
    qty_in: float = 0
    qty_out: float = 0
    set_to: Optional[float] = None   # a correction: the balance was set to this
    balance: float = field(default=0, init=False)

    @property
    def is_correction(self):
        return self.set_to is not None


def reference_for(prefix, pk):
    """PO-00012 — the padded form used across the delivery screens."""
    return f"{prefix}-{pk:05d}"


# --------------------------------------------------------------------------
# Sources. Each returns a queryset filtered the same way for both the scoping
# pass (which items moved at all?) and the ledger pass (what were the moves?).
# --------------------------------------------------------------------------

def _date_window(qs, path, date_from, date_to):
    if date_from:
        qs = qs.filter(**{f'{path}__date__gte': date_from})
    if date_to:
        qs = qs.filter(**{f'{path}__date__lte': date_to})
    return qs


def _po_lines(product_ids=None, branch_id=None, date_from=None, date_to=None):
    qs = DeliveryCheckItem.objects.filter(
        delivery_check__decision__in=RECEIVED_DECISIONS,
        delivered_quantity__gt=0,
    ).select_related(
        'delivery_check__purchase_order__supplier',
        'delivery_check__purchase_order__branch',
        'item',
    )
    if product_ids is not None:
        qs = qs.filter(item__product_id__in=product_ids)
    if branch_id:
        qs = qs.filter(delivery_check__purchase_order__branch_id=branch_id)
    return _date_window(qs, 'delivery_check__checked_at', date_from, date_to)


def _purchases(product_ids=None, branch_id=None, date_from=None, date_to=None):
    qs = Purchase.objects.select_related('supplier', 'branch')
    if product_ids is not None:
        qs = qs.filter(product_id__in=product_ids)
    if branch_id:
        qs = qs.filter(branch_id=branch_id)
    return _date_window(qs, 'date_purchased', date_from, date_to)


def _grn_items(product_ids=None, branch_id=None, date_from=None, date_to=None):
    qs = GRNItem.objects.select_related('grn__branch', 'grn__purchase_order')
    if product_ids is not None:
        qs = qs.filter(product_id__in=product_ids)
    if branch_id:
        qs = qs.filter(grn__branch_id=branch_id)
    return _date_window(qs, 'grn__received_date', date_from, date_to)


def _transfers(product_ids=None, branch_id=None, date_from=None, date_to=None,
               direction=''):
    qs = StockTransfer.objects.select_related('from_branch', 'to_branch')
    if product_ids is not None:
        qs = qs.filter(product_id__in=product_ids)
    if branch_id:
        # With a branch chosen, a transfer is an in *or* an out, never both.
        if direction == 'in':
            qs = qs.filter(to_branch_id=branch_id)
        elif direction == 'out':
            qs = qs.filter(from_branch_id=branch_id)
        else:
            qs = qs.filter(Q(from_branch_id=branch_id) | Q(to_branch_id=branch_id))
    return _date_window(qs, 'date', date_from, date_to)


def _adjustments(product_ids=None, branch_id=None, date_from=None, date_to=None,
                 direction=''):
    qs = StockAdjustment.objects.select_related('branch', 'user')
    if product_ids is not None:
        qs = qs.filter(product_id__in=product_ids)
    if branch_id:
        qs = qs.filter(branch_id=branch_id)
    if direction == 'in':
        qs = qs.filter(adjustment_type='addition')
    elif direction == 'out':
        qs = qs.filter(adjustment_type='deduction')
    return _date_window(qs, 'created_at', date_from, date_to)


def _sale_items(product_ids=None, branch_id=None, date_from=None, date_to=None):
    from apps.sales.models import SaleItem  # sales imports inventory

    qs = SaleItem.objects.filter(sale__status='dispatched').select_related(
        'sale__branch', 'sale__customer')
    if product_ids is not None:
        qs = qs.filter(product_id__in=product_ids)
    if branch_id:
        qs = qs.filter(sale__branch_id=branch_id)
    return _date_window(qs, 'sale__created_at', date_from, date_to)


# --------------------------------------------------------------------------
# Which items moved, and how
# --------------------------------------------------------------------------

def moved_product_ids(branch_id=None, date_from=None, date_to=None, direction=''):
    """Ids of every product that moved in the window — the report's item list.

    Filtering the items this way (instead of listing the whole catalogue) keeps
    a page of the report to a page's worth of ledgers.
    """
    ids = set()
    scope = dict(branch_id=branch_id, date_from=date_from, date_to=date_to)

    if direction != 'out':
        ids.update(_po_lines(**scope).values_list('item__product_id', flat=True))
        ids.update(_purchases(**scope).values_list('product_id', flat=True))
        ids.update(_grn_items(**scope).values_list('product_id', flat=True))
    if direction != 'in':
        ids.update(_sale_items(**scope).values_list('product_id', flat=True))

    ids.update(_transfers(direction=direction, **scope).values_list('product_id', flat=True))
    ids.update(_adjustments(direction=direction, **scope).values_list('product_id', flat=True))
    return ids


def collect_movements(product_ids, branch_id=None):
    """Every movement of the given products, whatever the date.

    The whole history is needed even when the user asked for one month: the
    balance a month's rows carry only means anything if it was run from the
    beginning.
    """
    product_ids = list(product_ids)
    if not product_ids:
        return []

    rows = []
    scope = dict(product_ids=product_ids, branch_id=branch_id)

    for line in _po_lines(**scope):
        check = line.delivery_check
        po = check.purchase_order
        label = 'PO delivery'
        if check.round_number > 1:
            label = f'PO delivery (round {check.round_number})'
        rows.append(Movement(
            date=check.checked_at,
            reference=reference_for('PO', po.id),
            kind='po',
            label=label,
            detail=po.supplier.name if po.supplier else 'Supplier',
            branch=po.branch.name,
            product_id=line.item.product_id,
            qty_in=line.delivered_quantity,
        ))

    for purchase in _purchases(**scope):
        rows.append(Movement(
            date=purchase.date_purchased,
            reference=reference_for('PUR', purchase.id),
            kind='purchase',
            label='Purchase',
            detail=purchase.supplier.name if purchase.supplier else 'Supplier',
            branch=purchase.branch.name,
            product_id=purchase.product_id,
            qty_in=purchase.quantity,
        ))

    for item in _grn_items(**scope):
        grn = item.grn
        detail = 'Goods received note'
        if grn.purchase_order_id:
            detail = f'Against {reference_for("PO", grn.purchase_order_id)}'
        rows.append(Movement(
            date=grn.received_date,
            reference=grn.receipt_number or reference_for('GRN', grn.id),
            kind='grn',
            label='Goods received',
            detail=detail,
            branch=grn.branch.name,
            product_id=item.product_id,
            qty_in=item.quantity_received,
        ))

    for transfer in _transfers(**scope):
        reference = reference_for('TRF', transfer.id)
        # No branch filter means both sides of the move belong to the ledger,
        # and they net to nothing — which is exactly what a transfer does.
        if not branch_id or transfer.to_branch_id == int(branch_id):
            rows.append(Movement(
                date=transfer.date,
                reference=reference,
                kind='transfer_in',
                label='Transfer in',
                detail=f'From {transfer.from_branch.name}',
                branch=transfer.to_branch.name,
                product_id=transfer.product_id,
                qty_in=transfer.quantity,
            ))
        if not branch_id or transfer.from_branch_id == int(branch_id):
            rows.append(Movement(
                date=transfer.date,
                reference=reference,
                kind='transfer_out',
                label='Transfer out',
                detail=f'To {transfer.to_branch.name}',
                branch=transfer.from_branch.name,
                product_id=transfer.product_id,
                qty_out=transfer.quantity,
            ))

    for adj in _adjustments(**scope):
        who = adj.user.get_username() if adj.user else 'system'
        reason = (adj.reason or '').strip().splitlines()[0] if adj.reason else ''
        detail = f'{reason} — by {who}' if reason else f'By {who}'
        row = Movement(
            date=adj.created_at,
            reference=reference_for('ADJ', adj.id),
            kind=f'adjust_{adj.adjustment_type}',
            label=adj.get_adjustment_type_display(),
            detail=detail,
            branch=adj.branch.name,
            product_id=adj.product_id,
        )
        if adj.adjustment_type == 'addition':
            row.qty_in = adj.quantity
        elif adj.adjustment_type == 'deduction':
            row.qty_out = adj.quantity
        else:
            row.set_to = adj.quantity
        rows.append(row)

    for item in _sale_items(**scope):
        sale = item.sale
        customer = sale.customer.name if sale.customer else (sale.customer_name or 'Walk-in Customer')
        rows.append(Movement(
            date=sale.created_at,
            reference=sale.invoice_number,
            kind='sale',
            label='Sale dispatched',
            detail=customer,
            branch=sale.branch.name,
            product_id=item.product_id,
            qty_out=item.quantity,
        ))

    return rows


# --------------------------------------------------------------------------
# Balancing
# --------------------------------------------------------------------------

def _run_balance(rows, opening):
    """Walk the rows oldest-first, stamping each with the balance it leaves."""
    balance = opening
    for row in rows:
        if row.is_correction:
            balance = row.set_to
        else:
            balance = balance + row.qty_in - row.qty_out
        row.balance = balance
    return balance


def build_ledger(rows, on_hand, date_from=None, date_to=None, direction=''):
    """Balance an item's movements, then keep only the ones asked for.

    Returns the rows to show, the balance brought forward into that window, and
    the totals. `on_hand` anchors the walk: the last row of the full history
    lands on the quantity the branch actually holds, and whatever is left over
    is the opening balance the records don't account for.
    """
    rows = sorted(rows, key=lambda r: (r.date, r.reference))
    opening = on_hand - _run_balance(rows, 0)
    _run_balance(rows, opening)

    shown, total_in, total_out = [], 0, 0
    brought_forward = opening
    for row in rows:
        if not _in_window(row, date_from, date_to, direction):
            if not shown:
                brought_forward = row.balance
            continue
        shown.append(row)
        total_in += row.qty_in
        total_out += row.qty_out

    return {
        'rows': shown,
        'opening': brought_forward,
        'closing': shown[-1].balance if shown else brought_forward,
        'total_in': total_in,
        'total_out': total_out,
        'on_hand': on_hand,
    }


def _in_window(row, date_from, date_to, direction):
    # Match the day the database lookups would have picked: local, not UTC.
    moment = row.date
    if hasattr(moment, 'date'):
        if timezone.is_aware(moment):
            moment = timezone.localtime(moment)
        moment = moment.date()
    if date_from and moment < date_from:
        return False
    if date_to and moment > date_to:
        return False
    if direction == 'in' and not row.qty_in:
        return False
    if direction == 'out' and not row.qty_out:
        return False
    return True
