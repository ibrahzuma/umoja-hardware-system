"""A supplier's statement off the shop floor's own records.

What we ordered from them (purchase orders, cancelled ones aside) against
what we have actually paid them (Admin-approved supplier payments), with a
running balance — the account Afisa Ugavi deals with. It reads the same money
as the cash desk's By Supplier screen, line by line.

A payment made *from credit* moves no money: it only spends an earlier
overpayment, which is already in the balance. It is listed for the trail but
does not move the balance, or the overpayment would be counted twice.
Payments still awaiting an Admin are listed apart and owe nothing yet.
"""
from decimal import Decimal

from apps.inventory.models import PurchaseOrder

from .models import SupplierPayment

ZERO = Decimal('0')


def can_view_supplier_statements(user):
    from apps.users.permissions import is_privileged
    return bool(user and user.is_authenticated and (
        is_privileged(user)
        or getattr(user, 'is_procurement_officer', False)
        or getattr(user, 'is_accountant', False)
        or getattr(user, 'is_cashier', False)))


def _lines(supplier):
    orders = (PurchaseOrder.objects.filter(supplier=supplier)
              .exclude(status='cancelled').order_by('order_date', 'id'))
    for po in orders:
        yield {
            'date': po.order_date, 'kind': 'order', 'sort': (po.order_date, 0, po.id),
            'reference': f'PO #{po.id}',
            'description': f'Purchase order ({po.get_status_display()})',
            'ordered': po.total_amount or ZERO, 'paid': ZERO, 'url_po': po.id,
        }
    payments = (SupplierPayment.objects.filter(supplier=supplier, status='paid')
                .select_related('purchase_order').order_by('payment_date', 'id'))
    for p in payments:
        against = f' against PO #{p.purchase_order_id}' if p.purchase_order_id else ''
        if p.from_credit:
            desc = f'Credit applied{against} (no money moved)'
            paid = ZERO
        else:
            desc = f'Payment - {p.get_method_display()}{against}'
            paid = p.amount
        yield {
            'date': p.payment_date, 'kind': 'credit' if p.from_credit else 'payment',
            'sort': (p.payment_date, 1, p.id), 'reference': p.reference or '',
            'description': desc, 'ordered': ZERO, 'paid': paid,
            'credit_amount': p.amount if p.from_credit else ZERO,
        }


def statement(supplier, start=None, end=None):
    lines = sorted(_lines(supplier), key=lambda r: r['sort'])
    opening = ZERO
    rows = []
    balance = ZERO
    for line in lines:
        if start and line['date'] < start:
            opening += line['ordered'] - line['paid']
            continue
        if end and line['date'] > end:
            continue
        rows.append(line)
    balance = opening
    for line in rows:
        balance += line['ordered'] - line['paid']
        line['balance'] = balance
    pending = list(SupplierPayment.objects.filter(supplier=supplier, status='pending')
                   .order_by('payment_date', 'id'))
    return {
        'opening': opening, 'rows': rows, 'closing': balance,
        'ordered_total': sum((r['ordered'] for r in rows), ZERO),
        'paid_total': sum((r['paid'] for r in rows), ZERO),
        'pending': pending,
        'pending_total': sum((p.amount for p in pending), ZERO),
    }


def summary():
    """One line per supplier with any order or payment: ordered, paid, owed."""
    from apps.inventory.models import Supplier
    from django.db.models import Sum
    ordered = dict(PurchaseOrder.objects.filter(supplier__isnull=False).exclude(status='cancelled')
                   .values('supplier').annotate(t=Sum('total_amount')).values_list('supplier', 't'))
    paid = dict(SupplierPayment.objects.filter(status='paid', from_credit=False)
                .values('supplier').annotate(t=Sum('amount')).values_list('supplier', 't'))
    rows = []
    for s in Supplier.objects.order_by('name'):
        o, p = ordered.get(s.id) or ZERO, paid.get(s.id) or ZERO
        rows.append({'supplier': s, 'ordered': o, 'paid': p, 'balance': o - p})
    return rows
