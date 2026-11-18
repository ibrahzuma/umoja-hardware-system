"""The figures behind the operations dashboard (`/api/dashboard/`).

One call returns every widget for a period, each beside the same-length
period before it, so the page can say "+8.3% vs last period" honestly:

    kpis           revenue, orders, customers, average sale — now and before
    profit         gross profit (sales less the cost of what was sold), with a
                   day-by-day series for both periods
    settlement     how this period's sales were settled: paid / part paid / credit
    weekdays       orders per day of the week
    repeat         of the customers who bought this period, how many had bought before
    best_sellers   the five products sold most, by quantity
    attention      what is waiting on someone right now

Cancelled sales are not sales and count nowhere. Money figures (revenue,
profit, average sale, best-seller revenue) are left out for users who may not
see them; `can_see_money` is the one rule. Staff tied to a shop see that shop.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import DecimalField, ExpressionWrapper, F, Sum
from django.utils import timezone

ZERO = Decimal('0')


def can_see_money(user):
    """The same people the old Monthly Revenue card was shown to."""
    from apps.users.permissions import is_privileged
    return bool(is_privileged(user) or getattr(user, 'is_sales_manager', False))


def period_for(params, today=None):
    """(start, end, previous_start, previous_end) — inclusive dates.

    `?days=7|30|90`, `?range=month|last_month`, or `?date_from=&date_to=`.
    The previous period is the same number of days immediately before; for a
    calendar month it is the month before, to the same day.
    """
    today = today or timezone.localdate()
    rng = (params.get('range') or '').strip()
    if rng == 'month':
        start, end = today.replace(day=1), today
        prev_end_month = start - timedelta(days=1)
        prev_start = prev_end_month.replace(day=1)
        prev_end = min(prev_start.replace(day=1) + timedelta(days=(end - start).days), prev_end_month)
        return start, end, prev_start, prev_end
    if rng == 'last_month':
        end = today.replace(day=1) - timedelta(days=1)
        start = end.replace(day=1)
        prev_end = start - timedelta(days=1)
        return start, end, prev_end.replace(day=1), prev_end
    try:
        start = date.fromisoformat(params.get('date_from'))
        end = date.fromisoformat(params.get('date_to'))
    except (TypeError, ValueError):
        try:
            days = max(1, min(int(params.get('days') or 30), 366))
        except ValueError:
            days = 30
        end = today
        start = end - timedelta(days=days - 1)
    if end < start:
        start, end = end, start
    length = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    return start, end, prev_end - timedelta(days=length - 1), prev_end


def _sales(start, end, branch):
    from apps.sales.models import Sale
    qs = (Sale.objects.exclude(status='cancelled')
          .filter(created_at__date__gte=start, created_at__date__lte=end))
    return qs.filter(branch=branch) if branch is not None else qs


def _cost(sales):
    """What the goods on these sales cost, at each product's cost price."""
    from apps.sales.models import SaleItem
    line = ExpressionWrapper(F('quantity') * F('product__cost'),
                             output_field=DecimalField(max_digits=16, decimal_places=2))
    return (SaleItem.objects.filter(sale__in=sales)
            .aggregate(t=Sum(line))['t'] or ZERO)


def _block(sales):
    revenue = sales.aggregate(t=Sum('total_amount'))['t'] or ZERO
    orders = sales.count()
    customers = sales.exclude(customer__isnull=True).values('customer').distinct().count()
    walk_ins = sales.filter(customer__isnull=True).count()
    cost = _cost(sales)
    return {
        'revenue': revenue,
        'orders': orders,
        'customers': customers + walk_ins,
        'average': (revenue / orders) if orders else ZERO,
        'profit': revenue - cost,
    }


def _daily_profit(sales, start, end):
    """Gross profit per day, one figure for every day in the window."""
    from apps.sales.models import SaleItem
    revenue = defaultdict(lambda: ZERO)
    for day, total in sales.values_list('created_at__date', 'total_amount'):
        revenue[day] += total or ZERO
    cost = defaultdict(lambda: ZERO)
    rows = (SaleItem.objects.filter(sale__in=sales)
            .values_list('sale__created_at__date', 'quantity', 'product__cost'))
    for day, qty, unit_cost in rows:
        cost[day] += (unit_cost or ZERO) * qty
    days = (end - start).days + 1
    return [(start + timedelta(days=i),
             revenue[start + timedelta(days=i)] - cost[start + timedelta(days=i)])
            for i in range(days)]


def _settlement(sales):
    """Paid in full, part paid or on credit — what the till recorded."""
    from apps.sales.models import Transaction
    paid = defaultdict(lambda: ZERO)
    for sale_id, amount in (Transaction.objects
                            .filter(sale__in=sales, transaction_type='income')
                            .values_list('sale_id', 'amount')):
        paid[sale_id] += amount or ZERO
    out = {'paid': [0, ZERO], 'part': [0, ZERO], 'credit': [0, ZERO]}
    for sale_id, total in sales.values_list('id', 'total_amount'):
        got = paid[sale_id]
        key = 'paid' if got >= (total or ZERO) else ('part' if got > 0 else 'credit')
        out[key][0] += 1
        out[key][1] += total or ZERO
    return out


def _repeat(sales, start):
    """Of the named customers who bought this period, how many had bought
    before it. Walk-in sales name nobody and cannot repeat."""
    from apps.sales.models import Sale
    buyers = set(sales.exclude(customer__isnull=True).values_list('customer_id', flat=True))
    if not buyers:
        return {'rate': None, 'buyers': 0, 'returning': 0}
    returning = set(Sale.objects.exclude(status='cancelled')
                    .filter(customer_id__in=buyers, created_at__date__lt=start)
                    .values_list('customer_id', flat=True))
    # Bought twice inside the period is a repeat too.
    counts = defaultdict(int)
    for cid in sales.exclude(customer__isnull=True).values_list('customer_id', flat=True):
        counts[cid] += 1
    returning |= {cid for cid, n in counts.items() if n > 1}
    return {'rate': round(len(returning) / len(buyers) * 100, 1),
            'buyers': len(buyers), 'returning': len(returning)}


def _best_sellers(sales, branch, limit=5):
    from apps.inventory.models import Stock
    from apps.sales.models import SaleItem
    rows = (SaleItem.objects.filter(sale__in=sales)
            .values('product_id', 'product__name', 'product__sku', 'product__unit',
                    'product__product_type')
            .annotate(sold=Sum('quantity'), revenue=Sum('subtotal'))
            .order_by('-sold', '-revenue', 'product_id')[:limit])
    rows = list(rows)
    stock = Stock.objects.filter(product_id__in=[r['product_id'] for r in rows])
    if branch is not None:
        stock = stock.filter(branch=branch)
    left = defaultdict(int)
    for pid, qty in stock.values_list('product_id', 'quantity'):
        left[pid] += qty or 0
    from apps.inventory.models import Product
    units = dict(Product.UNIT_CHOICES)
    return [{
        'id': r['product_id'], 'name': r['product__name'], 'sku': r['product__sku'],
        'unit': units.get(r['product__unit'], r['product__unit'] or ''),
        'sold': r['sold'], 'revenue': r['revenue'] or ZERO,
        'stock': None if r['product__product_type'] == 'service' else left[r['product_id']],
    } for r in rows]


def _attention(user, branch):
    from apps.inventory.models import Stock
    from apps.sales.models import Sale, Vehicle
    sales = Sale.objects.all() if branch is None else Sale.objects.filter(branch=branch)
    stock = Stock.objects.all() if branch is None else Stock.objects.filter(branch=branch)
    return {
        'pending_approval': sales.filter(status='pending').count(),
        'ready_to_dispatch': sales.filter(status='approved').count(),
        'low_stock': stock.filter(quantity__lte=F('low_stock_threshold')).count(),
        'active_vehicles': Vehicle.objects.filter(status='active').count(),
    }


def overview(user, params):
    from apps.inventory.models import Branch
    from apps.users.permissions import home_branch

    start, end, prev_start, prev_end = period_for(params)
    branch = home_branch(user)
    if branch is None and str(params.get('branch') or '').isdigit():
        branch = Branch.objects.filter(pk=int(params['branch'])).first()

    now_sales = _sales(start, end, branch)
    before_sales = _sales(prev_start, prev_end, branch)
    now, before = _block(now_sales), _block(before_sales)
    money = can_see_money(user)

    weekdays = [0] * 7                 # Sun .. Sat, the way the page draws them
    for d in now_sales.values_list('created_at__date', flat=True):
        weekdays[(d.weekday() + 1) % 7] += 1

    data = {
        'period': {'from': start, 'to': end, 'previous_from': prev_start, 'previous_to': prev_end},
        'branch': {'id': branch.pk, 'name': branch.name} if branch else None,
        'branch_locked': home_branch(user) is not None,
        'can_see_money': money,
        'kpis': {
            'orders': {'now': now['orders'], 'before': before['orders']},
            'customers': {'now': now['customers'], 'before': before['customers']},
        },
        'weekdays': weekdays,
        'repeat': _repeat(now_sales, start),
        'best_sellers': _best_sellers(now_sales, branch),
        'attention': _attention(user, branch),
    }
    settlement = _settlement(now_sales)
    data['settlement'] = {k: {'count': v[0], **({'amount': v[1]} if money else {})}
                          for k, v in settlement.items()}
    if money:
        data['kpis']['revenue'] = {'now': now['revenue'], 'before': before['revenue']}
        data['kpis']['average'] = {'now': now['average'], 'before': before['average']}
        data['profit'] = {
            'now': now['profit'], 'before': before['profit'],
            'series': [{'date': d, 'amount': a} for d, a in _daily_profit(now_sales, start, end)],
            'previous_series': [{'date': d, 'amount': a}
                                for d, a in _daily_profit(before_sales, prev_start, prev_end)],
        }
    else:
        for row in data['best_sellers']:
            row.pop('revenue', None)
    return data
