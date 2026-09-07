"""What is actually sellable, branch by branch.

Stock leaves the shelves at dispatch, not when the sale is written, so a sale
that has been taken but not yet dispatched has *committed* goods that are still
sitting in the `Stock` row. Reading `Stock.quantity` alone therefore lets three
sales reps sell the same last five bags, and the shortfall only surfaces at
dispatch, in front of a customer.

    available = on hand - already committed to sales not yet dispatched

Services carry no stock and are never limited by this.
"""

from __future__ import annotations

from django.db.models import Sum

from apps.inventory.models import Product, Stock
from .models import SaleItem

# Sales that hold goods without having taken them off the shelf yet.
COMMITTING_STATUSES = ('pending', 'approved')


def on_hand(branch_id, product_ids):
    """{product_id: quantity} from the Stock rows at this branch."""
    rows = (Stock.objects
            .filter(branch_id=branch_id, product_id__in=product_ids)
            .values_list('product_id', 'quantity'))
    return {pid: qty or 0 for pid, qty in rows}


def committed(branch_id, product_ids, exclude_sale_id=None):
    """{product_id: quantity} owed to sales taken but not yet dispatched."""
    qs = (SaleItem.objects
          .filter(sale__branch_id=branch_id,
                  sale__status__in=COMMITTING_STATUSES,
                  product_id__in=product_ids))
    if exclude_sale_id is not None:
        qs = qs.exclude(sale_id=exclude_sale_id)
    rows = qs.values('product_id').annotate(total=Sum('quantity'))
    return {r['product_id']: r['total'] or 0 for r in rows}


def available(branch_id, product_ids, exclude_sale_id=None):
    """{product_id: sellable quantity}. Never negative — an oversold line in
    the past is a problem to fix, not a licence to sell more."""
    have = on_hand(branch_id, product_ids)
    owed = committed(branch_id, product_ids, exclude_sale_id)
    return {pid: max(have.get(pid, 0) - owed.get(pid, 0), 0) for pid in product_ids}


def check_lines(branch_id, lines, exclude_sale_id=None):
    """Refuse a sale that cannot be filled.

    `lines` is [(product_id, quantity), ...]; the same product appearing twice
    is added up, because two lines of five are still ten off the shelf.

    Returns a list of human-readable problems — empty when the sale is fine.
    """
    wanted = {}
    for product_id, quantity in lines:
        try:
            pid = int(product_id)
            qty = int(quantity)
        except (TypeError, ValueError):
            continue
        if qty > 0:
            wanted[pid] = wanted.get(pid, 0) + qty

    if not wanted:
        return []

    products = {
        p.id: p for p in Product.objects.filter(id__in=wanted).only('id', 'name', 'product_type')
    }
    stocked = [pid for pid, p in products.items() if p.product_type != 'service']
    if not stocked:
        return []

    free = available(branch_id, stocked, exclude_sale_id)

    problems = []
    for pid in stocked:
        want = wanted[pid]
        have = free.get(pid, 0)
        if want > have:
            name = products[pid].name
            if have <= 0:
                problems.append(f"{name} is out of stock at this branch.")
            else:
                problems.append(
                    f"Only {have} of {name} left at this branch — {want} were ordered.")
    return problems
