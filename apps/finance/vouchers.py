"""The books proper: chart of accounts, voucher posting and the General Ledger.

A voucher is the only way an entry reaches the General Ledger, and
`post_voucher` is the only way a voucher is written. It refuses anything that
does not balance, anything that puts a ledger on a side its voucher type does
not allow, and any allocation that clears more of an invoice or bill than is
owed on it. Everything it accepts is written in one transaction — voucher,
lines, allocations and GL rows — so the ledger can never hold half a voucher.

Outstanding figures for allocation are derived, never stored, in the same
spirit as the CRM and supplier-credit balances:

    invoice owed = sale total − till payments − earlier voucher allocations
    bill owed    = order total − approved supplier payments − earlier allocations

Nothing here writes back to a sale, a purchase order or a supplier payment.
The ledger mirrors the shop floor; it does not drive it.
"""

from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Sum, Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.inventory.models import PurchaseOrder, Supplier
from apps.sales.models import Customer, Sale
from .models import (
    BankAccount, GeneralLedgerEntry, LedgerAccount, SupplierPayment, Voucher,
    VoucherAllocation, VoucherLine,
)

ZERO = Decimal('0.00')

# Ledgers every set of books needs from day one. Created once by
# `sync_chart_of_accounts`; renaming them afterwards is fine, the sync goes by
# name only to avoid creating them twice.
DEFAULT_LEDGERS = (
    ('cash', 'Main Cash Book'),
    ('cash', 'Petty Cash'),
    ('income', 'Sales Revenue'),
    ('income', 'Other Income'),
    ('expense', 'Purchases'),
    ('expense', 'General Expenses'),
    ('expense', 'Salaries & Wages'),
    ('expense', 'Transport & Fuel'),
    ('expense', 'Rent'),
    ('expense', 'Utilities'),
    ('asset', 'Stock'),
    ('tax', 'VAT Payable'),
    ('tax', 'PAYE Payable'),
    ('liability', 'Loans'),
    ('equity', 'Capital'),
)


# ---------------------------------------------------------------------------
# Chart of accounts
# ---------------------------------------------------------------------------

def sync_chart_of_accounts():
    """Make sure every bank account, customer and supplier has a ledger, and
    that the default ledgers exist. Safe to run any number of times; returns
    how many ledgers it created."""
    created = 0

    # Seed the defaults only while the chart has no ledger of its own yet.
    # Linked ledgers do not count: the signals may well have created a
    # customer's or a bank's ledger before anyone opened the books.
    if not (LedgerAccount.objects
            .filter(bank_account__isnull=True, customer__isnull=True, supplier__isnull=True)
            .exists()):
        for kind, name in DEFAULT_LEDGERS:
            LedgerAccount.objects.create(kind=kind, name=name)
            created += 1

    for bank in BankAccount.objects.filter(ledger__isnull=True):
        LedgerAccount.objects.create(kind='bank', name=bank.name, bank_account=bank,
                                     is_active=bank.is_active)
        created += 1
    for customer in Customer.objects.filter(ledger__isnull=True):
        LedgerAccount.objects.create(kind='customer', name=customer.name, customer=customer)
        created += 1
    for supplier in Supplier.objects.filter(ledger__isnull=True):
        LedgerAccount.objects.create(kind='supplier', name=supplier.name, supplier=supplier)
        created += 1
    return created


def ensure_ledger_for(instance):
    """Signal hook: a bank account, customer or supplier was saved."""
    if isinstance(instance, BankAccount):
        ledger, _ = LedgerAccount.objects.get_or_create(
            bank_account=instance, defaults={'kind': 'bank', 'name': instance.name})
        if ledger.name != instance.name or ledger.is_active != instance.is_active:
            ledger.name = instance.name
            ledger.is_active = instance.is_active
            ledger.save(update_fields=['name', 'is_active'])
    elif isinstance(instance, Customer):
        ledger, _ = LedgerAccount.objects.get_or_create(
            customer=instance, defaults={'kind': 'customer', 'name': instance.name})
        if ledger.name != instance.name:
            ledger.name = instance.name
            ledger.save(update_fields=['name'])
    elif isinstance(instance, Supplier):
        ledger, _ = LedgerAccount.objects.get_or_create(
            supplier=instance, defaults={'kind': 'supplier', 'name': instance.name})
        if ledger.name != instance.name:
            ledger.name = instance.name
            ledger.save(update_fields=['name'])


def accounts_for(side_rule):
    """The ledgers a dropdown may offer, given one half of `Voucher.RULES`."""
    qs = LedgerAccount.objects.filter(is_active=True)
    if side_rule is None:
        return qs
    if side_rule == 'non_money':
        return qs.exclude(kind__in=LedgerAccount.MONEY_KINDS)
    return qs.filter(kind__in=side_rule)


def _allowed(account, side_rule):
    if side_rule is None:
        return True
    if side_rule == 'non_money':
        return not account.is_money
    return account.kind in side_rule


# ---------------------------------------------------------------------------
# What is still owed, for allocation
# ---------------------------------------------------------------------------

def _allocated_by(field, ids):
    """Voucher allocations already posted against these sales or orders."""
    rows = (VoucherAllocation.objects
            .filter(**{f'{field}__in': ids}, line__voucher__status='posted')
            .values(field).annotate(t=Sum('amount')))
    return {row[field]: row['t'] or ZERO for row in rows}


def outstanding_invoices(customer):
    """The customer's invoices that still carry a balance, oldest first."""
    sales = (Sale.objects.filter(customer=customer)
             .exclude(status='cancelled')
             .annotate(till_paid=Sum('transactions__amount',
                                     filter=Q(transactions__transaction_type='income')))
             .order_by('created_at', 'id'))
    allocated = _allocated_by('sale', [s.id for s in sales])
    rows = []
    for sale in sales:
        total = sale.total_amount or ZERO
        paid = (sale.till_paid or ZERO) + allocated.get(sale.id, ZERO)
        balance = total - paid
        if balance <= 0:
            continue
        rows.append({
            'id': sale.id,
            'reference': sale.invoice_number,
            'date': sale.created_at.date().isoformat(),
            'total': str(total),
            'paid': str(paid),
            'outstanding': str(balance),
        })
    return rows


def outstanding_bills(supplier):
    """The supplier's purchase orders that still carry a balance, oldest first.

    Every live order counts, drafts included — those are placed orders waiting
    for delivery, and the money on them is owed (see statements._creditors).
    """
    orders = (PurchaseOrder.objects.filter(supplier=supplier)
              .exclude(status='cancelled')
              .order_by('created_at', 'id'))
    ids = [o.id for o in orders]
    paid = {
        row['purchase_order']: row['t'] or ZERO
        for row in SupplierPayment.objects
        .filter(purchase_order__in=ids, status='paid')
        .values('purchase_order').annotate(t=Sum('amount'))
    }
    allocated = _allocated_by('purchase_order', ids)
    rows = []
    for order in orders:
        total = order.total_amount or ZERO
        settled = paid.get(order.id, ZERO) + allocated.get(order.id, ZERO)
        balance = total - settled
        if balance <= 0:
            continue
        rows.append({
            'id': order.id,
            'reference': f'PO-{order.id}',
            'date': order.order_date.isoformat() if order.order_date else '',
            'total': str(total),
            'paid': str(settled),
            'outstanding': str(balance),
        })
    return rows


def outstanding_for(account):
    """Whatever this ledger can have money allocated against, or []."""
    if account.kind == 'customer' and account.customer_id:
        return outstanding_invoices(account.customer)
    if account.kind == 'supplier' and account.supplier_id:
        return outstanding_bills(account.supplier)
    return []


def allocation_side(account):
    """Which side of a voucher an allocation makes sense on for this ledger:
    a customer is credited when they pay us, a supplier debited when we pay
    them. Anywhere else an allocation means nothing."""
    if account.kind == 'customer':
        return 'credit'
    if account.kind == 'supplier':
        return 'debit'
    return None


# ---------------------------------------------------------------------------
# Posting
# ---------------------------------------------------------------------------

def _money(value, what):
    try:
        amount = Decimal(str(value)).quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError({'lines': f'{what} is not a number.'})
    return amount


def _clean_lines(voucher_type, raw_lines):
    """Turn the request's lines into (account, side, amount, narration,
    allocations) tuples, or raise with a message the form can show."""
    debit_rule, credit_rule = Voucher.RULES[voucher_type]
    if not isinstance(raw_lines, list) or not raw_lines:
        raise ValidationError({'lines': 'A voucher needs at least one debit and one credit line.'})

    ids = {int(l.get('account')) for l in raw_lines if l.get('account')}
    accounts = {a.id: a for a in LedgerAccount.objects.filter(id__in=ids)}

    cleaned = []
    for n, raw in enumerate(raw_lines, start=1):
        side = raw.get('side')
        if side not in ('debit', 'credit'):
            raise ValidationError({'lines': f'Line {n}: side must be debit or credit.'})
        account = accounts.get(int(raw.get('account') or 0))
        if account is None:
            raise ValidationError({'lines': f'Line {n}: pick a ledger.'})
        if not account.is_active:
            raise ValidationError({'lines': f'Line {n}: {account} is closed.'})
        amount = _money(raw.get('amount'), f'Line {n} amount')
        if amount <= 0:
            raise ValidationError({'lines': f'Line {n}: the amount has to be more than nothing.'})
        rule = debit_rule if side == 'debit' else credit_rule
        if not _allowed(account, rule):
            raise ValidationError({'lines': (
                f'Line {n}: {account.name} ({account.get_kind_display()}) cannot be '
                f'{side}ed on a {Voucher.PREFIX[voucher_type]} {voucher_type} voucher.')})

        allocations = []
        for alloc in raw.get('allocations') or []:
            alloc_amount = _money(alloc.get('amount'), f'Line {n} allocation')
            if alloc_amount <= 0:
                continue
            allocations.append((alloc, alloc_amount))
        if allocations and allocation_side(account) != side:
            raise ValidationError({'lines': (
                f'Line {n}: only a customer on the credit side or a supplier on the '
                f'debit side can be allocated to invoices.')})
        if sum(a for _, a in allocations) > amount:
            raise ValidationError({'lines': f'Line {n}: allocated more than the line amount.'})

        cleaned.append((account, side, amount, (raw.get('narration') or '')[:200], allocations))
    return cleaned


def _check_allocations(account, allocations):
    """Each allocation must point at one of the ledger's own open invoices or
    bills and stay within what is still owed on it."""
    owed = {row['id']: row for row in outstanding_for(account)}
    out = []
    for raw, amount in allocations:
        target = raw.get('sale') if account.kind == 'customer' else raw.get('purchase_order')
        try:
            target = int(target)
        except (TypeError, ValueError):
            raise ValidationError({'lines': f'{account.name}: an allocation is missing its invoice.'})
        row = owed.get(target)
        if row is None:
            raise ValidationError({'lines': f'{account.name}: that invoice has nothing outstanding.'})
        if amount > Decimal(row['outstanding']):
            raise ValidationError({'lines': (
                f"{row['reference']}: only {row['outstanding']} is outstanding, "
                f"cannot allocate {amount}.")})
        out.append((target, row['reference'], amount))
    return out


@transaction.atomic
def post_voucher(voucher_type, date, description, raw_lines, user):
    """Validate and post a voucher, returning it. Raises ValidationError."""
    if voucher_type not in Voucher.PREFIX:
        raise ValidationError({'voucher_type': 'Unknown voucher type.'})
    if not date:
        raise ValidationError({'date': 'Give the voucher a date.'})

    lines = _clean_lines(voucher_type, raw_lines)
    total_debit = sum(a for _, s, a, _, _ in lines if s == 'debit')
    total_credit = sum(a for _, s, a, _, _ in lines if s == 'credit')
    if total_debit == 0 or total_credit == 0:
        raise ValidationError({'lines': 'A voucher needs at least one debit and one credit line.'})
    if total_debit != total_credit:
        raise ValidationError({'lines': (
            f'Debits ({total_debit}) and credits ({total_credit}) do not balance — '
            f'difference {abs(total_debit - total_credit)}.')})

    voucher = Voucher.objects.create(
        voucher_type=voucher_type, date=date, description=(description or '').strip(),
        total=total_debit, created_by=user,
    )
    for position, (account, side, amount, narration, allocations) in enumerate(lines):
        line = VoucherLine.objects.create(
            voucher=voucher, account=account, side=side, amount=amount,
            narration=narration, position=position,
        )
        for target, reference, alloc_amount in _check_allocations(account, allocations):
            VoucherAllocation.objects.create(
                line=line, reference=reference, amount=alloc_amount,
                sale_id=target if account.kind == 'customer' else None,
                purchase_order_id=target if account.kind == 'supplier' else None,
            )
        GeneralLedgerEntry.objects.create(
            voucher=voucher, line=line, account=account, date=voucher.date,
            voucher_type=voucher_type, voucher_number=voucher.number,
            description=(narration or voucher.description)[:300],
            debit=amount if side == 'debit' else ZERO,
            credit=amount if side == 'credit' else ZERO,
        )
    return voucher


@transaction.atomic
def cancel_voucher(voucher, user, reason=''):
    """Take a voucher's entries back out of the ledger. The document stays."""
    if voucher.status == 'cancelled':
        raise ValidationError({'detail': 'That voucher is already cancelled.'})
    voucher.gl_entries.all().delete()
    voucher.status = 'cancelled'
    voucher.cancelled_by = user
    voucher.cancelled_at = timezone.now()
    voucher.cancel_reason = (reason or '').strip()
    voucher.save(update_fields=['status', 'cancelled_by', 'cancelled_at', 'cancel_reason'])
    return voucher


# ---------------------------------------------------------------------------
# Reading the ledger
# ---------------------------------------------------------------------------

def _signed(account, debit_minus_credit):
    """A balance the way the account holder reads it: positive on the
    ledger's normal side."""
    return debit_minus_credit if account.normal_side == 'debit' else -debit_minus_credit


def account_balances(date_to=None):
    """Debit-minus-credit per ledger id, opening balance included."""
    qs = GeneralLedgerEntry.objects.all()
    if date_to:
        qs = qs.filter(date__lte=date_to)
    movement = {
        row['account']: (row['d'] or ZERO) - (row['c'] or ZERO)
        for row in qs.values('account').annotate(d=Sum('debit'), c=Sum('credit'))
    }
    return {
        a.id: Decimal(a.opening_debit) + movement.get(a.id, ZERO)
        for a in LedgerAccount.objects.all()
    }


def account_statement(account, date_from=None, date_to=None):
    """One ledger's entries with a running balance, opening figure first."""
    before = GeneralLedgerEntry.objects.filter(account=account)
    if date_from:
        before = before.filter(date__lt=date_from)
    else:
        before = before.none()
    agg = before.aggregate(d=Sum('debit'), c=Sum('credit'))
    opening = Decimal(account.opening_debit) + (agg['d'] or ZERO) - (agg['c'] or ZERO)

    qs = GeneralLedgerEntry.objects.filter(account=account).select_related('voucher')
    if date_from:
        qs = qs.filter(date__gte=date_from)
    if date_to:
        qs = qs.filter(date__lte=date_to)

    running = opening
    rows = []
    total_debit = total_credit = ZERO
    for e in qs.order_by('date', 'id'):
        running += e.debit - e.credit
        total_debit += e.debit
        total_credit += e.credit
        rows.append({
            'id': e.id,
            'date': e.date.isoformat(),
            'voucher': e.voucher_id,
            'voucher_number': e.voucher_number,
            'voucher_type': e.voucher_type,
            'description': e.description,
            'debit': str(e.debit),
            'credit': str(e.credit),
            'balance': str(_signed(account, running)),
        })
    return {
        'account': {'id': account.id, 'code': account.code, 'name': account.name,
                    'kind': account.kind, 'kind_display': account.get_kind_display(),
                    'normal_side': account.normal_side},
        'opening_balance': str(_signed(account, opening)),
        'total_debit': str(total_debit),
        'total_credit': str(total_credit),
        'closing_balance': str(_signed(account, running)),
        'rows': rows,
    }


def trial_balance(date_from=None, date_to=None):
    """Every ledger's debit and credit totals for the period, with the
    balance carried in and out. The two closing columns always agree — that
    is what double entry buys."""
    entries = GeneralLedgerEntry.objects.all()
    if date_to:
        entries = entries.filter(date__lte=date_to)
    in_period = entries.filter(date__gte=date_from) if date_from else entries
    before = entries.filter(date__lt=date_from) if date_from else entries.none()

    movement = {row['account']: (row['d'] or ZERO, row['c'] or ZERO)
                for row in in_period.values('account').annotate(d=Sum('debit'), c=Sum('credit'))}
    carried = {row['account']: (row['d'] or ZERO) - (row['c'] or ZERO)
               for row in before.values('account').annotate(d=Sum('debit'), c=Sum('credit'))}

    rows = []
    total_debit = total_credit = ZERO
    for account in LedgerAccount.objects.all():
        debit, credit = movement.get(account.id, (ZERO, ZERO))
        opening = Decimal(account.opening_debit) + carried.get(account.id, ZERO)
        closing = opening + debit - credit
        if debit == 0 and credit == 0 and opening == 0 and closing == 0:
            continue
        rows.append({
            'id': account.id, 'code': account.code, 'name': account.name,
            'kind': account.kind, 'kind_display': account.get_kind_display(),
            'opening': str(opening),
            'debit': str(debit), 'credit': str(credit),
            'closing_debit': str(closing if closing > 0 else ZERO),
            'closing_credit': str(-closing if closing < 0 else ZERO),
        })
        total_debit += closing if closing > 0 else ZERO
        total_credit += -closing if closing < 0 else ZERO
    return {
        'rows': rows,
        'total_debit': str(total_debit),
        'total_credit': str(total_credit),
        'difference': str(total_debit - total_credit),
    }
