"""The books proper: chart of accounts, voucher posting and the General Ledger.

A voucher is the only way an entry reaches the General Ledger, and
`post_voucher` is the only way a voucher is written. It refuses anything that
does not balance, anything that puts a ledger on a side its voucher type does
not allow, and any allocation that clears more of an invoice or bill than is
owed on it. Everything it accepts is written in one transaction — voucher,
lines, allocations and GL rows — so the ledger can never hold half a voucher.

Outstanding figures for allocation are derived, never stored, in the same
spirit as the CRM and supplier-credit balances. An invoice is one of three
things — a till sale, a purchase order, or a Sales/Purchase voucher posted
here — and each is owed what is left after what has already been set against it:

    till sale owed      = sale total − till payments − earlier voucher allocations
    purchase order owed = order total − approved supplier payments − earlier allocations
    invoice voucher owed = the part left on the customer/supplier ledger − earlier allocations

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
    ('tax', 'Output VAT'),
    ('tax', 'Input VAT'),
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

    # The two VAT ledgers the Sales and Purchase vouchers split tax into are
    # wanted even in a chart seeded before they were part of the defaults.
    for kind, name in (('tax', 'Output VAT'), ('tax', 'Input VAT')):
        if not LedgerAccount.objects.filter(kind=kind, name__iexact=name).exists():
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


def _row(target, obj_id, reference, date, total, paid):
    """One open invoice or bill, in the shape the allocation screen shows:
    number, date, original amount, already settled, still outstanding.
    `target` names which link an allocation to it sets."""
    return {
        'target': target,
        'id': obj_id,
        'reference': reference,
        'date': date,
        'total': str(total),
        'paid': str(paid),
        'outstanding': str(total - paid),
    }


def _outstanding_invoice_vouchers(voucher_type, party_field, party, ledger, side):
    """Sales (or Purchase) vouchers posted for this party whose customer (or
    supplier) ledger still carries a balance. What is owed on an invoice
    voucher is the part that went on the party ledger — a cash sale left
    nothing there — less what later vouchers have set against it."""
    vouchers = (Voucher.objects
                .filter(voucher_type=voucher_type, status='posted', **{party_field: party})
                .order_by('date', 'id'))
    ids = [v.id for v in vouchers]
    on_ledger = {
        row['voucher']: row['t'] or ZERO
        for row in VoucherLine.objects
        .filter(voucher__in=ids, account=ledger, side=side)
        .values('voucher').annotate(t=Sum('amount'))
    }
    allocated = _allocated_by('voucher', ids)
    rows = []
    for v in vouchers:
        total = on_ledger.get(v.id, ZERO)
        paid = allocated.get(v.id, ZERO)
        if total - paid <= 0:
            continue
        rows.append(_row('voucher', v.id, v.invoice_number or v.number, v.date.isoformat(), total, paid))
    return rows


def outstanding_invoices(customer):
    """The customer's invoices that still carry a balance, oldest first:
    till sales and Sales vouchers alike."""
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
        if total - paid <= 0:
            continue
        rows.append(_row('sale', sale.id, sale.invoice_number, sale.created_at.date().isoformat(), total, paid))
    ledger = getattr(customer, 'ledger', None)
    if ledger is not None:
        rows += _outstanding_invoice_vouchers('sales', 'customer', customer, ledger, 'debit')
    rows.sort(key=lambda r: (r['date'], r['id']))
    return rows


def outstanding_bills(supplier):
    """The supplier's bills that still carry a balance, oldest first: purchase
    orders and Purchase vouchers alike.

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
        if total - settled <= 0:
            continue
        rows.append(_row('purchase_order', order.id, f'PO-{order.id}',
                         order.order_date.isoformat() if order.order_date else '', total, settled))
    ledger = getattr(supplier, 'ledger', None)
    if ledger is not None:
        rows += _outstanding_invoice_vouchers('purchase', 'supplier', supplier, ledger, 'credit')
    rows.sort(key=lambda r: (r['date'], r['id']))
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


ALLOCATION_TARGETS = ('sale', 'purchase_order', 'voucher')


def _check_allocations(account, allocations):
    """Each allocation must point at one of the ledger's own open invoices or
    bills — by `sale`, `purchase_order` or `voucher` id — and stay within what
    is still owed on it. Returns (target field, id, reference, amount)."""
    owed = {(row['target'], row['id']): row for row in outstanding_for(account)}
    out = []
    for raw, amount in allocations:
        target = next((t for t in ALLOCATION_TARGETS if raw.get(t) not in (None, '')), None)
        try:
            target_id = int(raw.get(target))
        except (TypeError, ValueError):
            raise ValidationError({'lines': f'{account.name}: an allocation is missing its invoice.'})
        row = owed.get((target, target_id))
        if row is None:
            raise ValidationError({'lines': f'{account.name}: that invoice has nothing outstanding.'})
        if amount > Decimal(row['outstanding']):
            raise ValidationError({'lines': (
                f"{row['reference']}: only {row['outstanding']} is outstanding, "
                f"cannot allocate {amount}.")})
        out.append((target, target_id, row['reference'], amount))
    return out


# ---------------------------------------------------------------------------
# Sales and Purchase vouchers: the invoice header
# ---------------------------------------------------------------------------

def _party_for(voucher_type, header):
    """The customer (sales) or supplier (purchase) named on the voucher."""
    if voucher_type == 'sales':
        model, key = Customer, 'customer'
    else:
        model, key = Supplier, 'supplier'
    raw = (header or {}).get(key)
    if raw in (None, ''):
        return key, None
    try:
        return key, model.objects.get(pk=int(raw))
    except (TypeError, ValueError, model.DoesNotExist):
        raise ValidationError({key: f'Pick the {key} from the list.'})


def _clean_header(voucher_type, header, lines):
    """What a Sales or Purchase voucher carries beyond its lines, checked:

    * the invoice number is required, and no other posted voucher of the
      same type may carry it; the EFD number, when given, is likewise unique
      (for purchases, per supplier — every supplier's machine numbers its own
      receipts);
    * a customer/supplier ledger on the party side must belong to the party
      named on the voucher, and naming a party is required as soon as one is;
    * the payment status is read off the party side: all money ledgers is
      cash or bank, all party ledger is credit, a mix is partly paid;
    * `vat_amount` is the part of the goods side on tax ledgers, `net_amount`
      the rest.
    """
    header = header or {}
    party_side, goods_side = ('debit', 'credit') if voucher_type == 'sales' else ('credit', 'debit')
    party_kind = 'customer' if voucher_type == 'sales' else 'supplier'
    key, party = _party_for(voucher_type, header)

    invoice_number = (header.get('invoice_number') or '').strip()[:50]
    efd_number = (header.get('efd_number') or '').strip()[:50]
    label = 'sales invoice number' if voucher_type == 'sales' else "supplier's invoice number"
    if not invoice_number:
        raise ValidationError({'invoice_number': f'Enter the {label}.'})
    posted = Voucher.objects.filter(voucher_type=voucher_type, status='posted')
    if posted.filter(invoice_number__iexact=invoice_number).exists():
        raise ValidationError({'invoice_number': (
            f'Invoice {invoice_number} is already in the books on another '
            f'{voucher_type} voucher.')})
    if efd_number:
        clash = posted.filter(efd_number__iexact=efd_number)
        if voucher_type == 'purchase' and party is not None:
            clash = clash.filter(supplier=party)
        if clash.exists():
            raise ValidationError({'efd_number': (
                f'EFD receipt {efd_number} is already in the books on {clash.first().number}.')})

    party_lines = [l for l in lines if l[1] == party_side]
    party_ledger_lines = [l for l in party_lines if l[0].kind == party_kind]
    if party_ledger_lines:
        if party is None:
            raise ValidationError({key: f'Pick the {party_kind} this invoice belongs to.'})
        own = getattr(party, 'ledger', None)
        for account, *_ in party_ledger_lines:
            if own is None or account.id != own.id:
                raise ValidationError({'lines': (
                    f'{account.name} is not the ledger of {party.name}; a {voucher_type} '
                    f'voucher can only be on the {party_kind} it names.')})

    money_lines = [l for l in party_lines if l[0].is_money]
    if party_ledger_lines and money_lines:
        payment_status = 'partly_paid'
    elif party_ledger_lines:
        payment_status = 'credit'
    elif money_lines and all(l[0].kind == 'cash' for l in money_lines):
        payment_status = 'cash'
    else:
        payment_status = 'bank'

    vat = sum((l[2] for l in lines if l[1] == goods_side and l[0].kind == 'tax'), ZERO)
    total = sum((l[2] for l in lines if l[1] == 'debit'), ZERO)
    return {
        'customer': party if voucher_type == 'sales' else None,
        'supplier': party if voucher_type == 'purchase' else None,
        'invoice_number': invoice_number,
        'efd_number': efd_number,
        'payment_status': payment_status,
        'vat_amount': vat,
        'net_amount': total - vat,
    }


@transaction.atomic
def post_voucher(voucher_type, date, description, raw_lines, user, header=None):
    """Validate and post a voucher, returning it. Raises ValidationError.

    `header` is only read for Sales and Purchase vouchers: the party
    (`customer` / `supplier` id), `invoice_number` and `efd_number`."""
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

    extra = _clean_header(voucher_type, header, lines) if voucher_type in Voucher.INVOICE_TYPES else {}

    voucher = Voucher.objects.create(
        voucher_type=voucher_type, date=date, description=(description or '').strip(),
        total=total_debit, created_by=user, **extra,
    )
    for position, (account, side, amount, narration, allocations) in enumerate(lines):
        line = VoucherLine.objects.create(
            voucher=voucher, account=account, side=side, amount=amount,
            narration=narration, position=position,
        )
        for target, target_id, reference, alloc_amount in _check_allocations(account, allocations):
            VoucherAllocation.objects.create(
                line=line, reference=reference, amount=alloc_amount, **{f'{target}_id': target_id},
            )
        GeneralLedgerEntry.objects.create(
            voucher=voucher, line=line, account=account, date=voucher.date,
            voucher_type=voucher_type, voucher_number=voucher.number,
            reference=voucher.invoice_number,
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
            'reference': e.reference,
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
