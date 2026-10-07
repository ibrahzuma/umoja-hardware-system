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


# ---------------------------------------------------------------------------
# Posting a sale into the books
# ---------------------------------------------------------------------------

# How the money came in -> which kind of money ledger it went into.
_BANK_METHODS = ('bank', 'mobile', 'cheque')


def _ledger(kind, name):
    """A ledger the books keep by name, created the first time it is needed."""
    from .models import LedgerAccount
    ledger = (LedgerAccount.objects.filter(kind=kind, name=name, is_group=False)
              .order_by('id').first())
    if ledger is None:
        ledger = LedgerAccount.objects.create(kind=kind, name=name)
    return ledger


def _first(qs):
    return qs.filter(is_active=True, is_group=False).order_by('id').first()


def _revenue_ledger(settings):
    from .models import LedgerAccount
    return (settings.default_sales_account
            or _first(LedgerAccount.objects.filter(kind='income', name='Sales Revenue'))
            or _ledger('income', 'Sales Revenue'))


def _money_ledger(settings, method):
    """Cash goes to the cash book; bank, mobile money and cheques to the bank."""
    from .models import LedgerAccount
    cash = (settings.default_cash_account
            or _first(LedgerAccount.objects.filter(kind='cash', name='Main Cash Book'))
            or _ledger('cash', 'Main Cash Book'))
    if method in _BANK_METHODS:
        return (settings.default_bank_account
                or _first(LedgerAccount.objects.filter(kind='bank'))
                or cash)
    return cash


def _customer_ledger(entry):
    """The sale's customer's own sub-ledger, or None for a walk-in."""
    sale = entry.sale
    customer = getattr(sale, 'customer', None) if sale else None
    if customer is None:
        return None
    from .vouchers import ensure_ledger_for
    ledger = getattr(customer, 'ledger', None)
    if ledger is None:
        ensure_ledger_for(customer)
        customer.refresh_from_db()
        ledger = getattr(customer, 'ledger', None)
    return ledger


def books_voucher(entry):
    """The live Sales voucher this entry was posted to, if any."""
    from .models import Voucher
    return (entry.vouchers.filter(status__in=Voucher.LIVE_STATUSES, reversal_of__isnull=True)
            .order_by('-id').first())


def post_to_books(entry, user, request=None):
    """Write the posted sale into the General Ledger as a Sales voucher.

    The accountant's Post is one act — the sale goes into the books and the
    money is recorded as received — so the voucher says both:

        Dr  cash book / bank        the amount confirmed
        Dr  the customer's ledger   whatever is still owed (walk-in: Walk-in Debtors)
        Cr  Sales Revenue           the sale's total
        Cr  the customer's ledger   anything paid over the total (walk-in: Customer Deposits)

    It is dated on the sale and numbered like any other Sales voucher, so it
    shows in the voucher register, the General Ledger, the trial balance and
    the customer's statement. It raises **no** invoice-register row: the till
    sale is already offered for allocation as itself, and the statements
    already count it through this entry (see `Voucher.sales_entry`).

    Idempotent: an entry already in the books returns its voucher. Raises a
    DRF `ValidationError` — and the caller's transaction rolls back — when
    the books refuse it (a closed period, a duplicate invoice number…).
    """
    from .models import AccountingSettings
    from .posting import VoucherPostingService, VoucherValidationError
    from .vouchers import open_the_books, post_voucher
    from rest_framework.exceptions import ValidationError

    existing = books_voucher(entry)
    if existing is not None:
        return existing

    open_the_books()
    settings = AccountingSettings.get_solo()
    total = Decimal(entry.total_amount or 0)
    paid = Decimal(entry.confirmed_amount or 0)
    if total <= 0:
        raise ValidationError({'detail': 'A sale of nothing cannot go into the books.'})

    who = entry.customer_name or 'Walk-in Customer'
    customer = _customer_ledger(entry)
    lines = []
    received = min(paid, total)
    if received > 0:
        lines.append({'account': _money_ledger(settings, entry.confirmed_method).id,
                      'side': 'debit', 'amount': str(received),
                      'narration': f'Received ({entry.get_confirmed_method_display() or "cash"})'
                                   + (f' {entry.confirmed_reference}' if entry.confirmed_reference else '')})
    owed = total - received
    if owed > 0:
        lines.append({'account': (customer or _ledger('asset', 'Walk-in Debtors')).id,
                      'side': 'debit', 'amount': str(owed), 'narration': f'Owed by {who}'})
    lines.append({'account': _revenue_ledger(settings).id, 'side': 'credit',
                  'amount': str(total), 'narration': f'Sale {entry.invoice_number}'})
    over = paid - total
    if over > 0:
        lines.append({'account': (customer or _ledger('liability', 'Customer Deposits')).id,
                      'side': 'credit', 'amount': str(over), 'narration': f'Paid over the invoice by {who}'})

    voucher = post_voucher(
        'sales', entry.sale_date,
        f'Sale {entry.invoice_number} to {who} — posted from Sales Accounting',
        lines, user, header={'invoice_number': entry.invoice_number},
        request=request, reference=entry.invoice_number, draft_only=True,
    )
    voucher.sales_entry = entry
    voucher.save(update_fields=['sales_entry'])
    try:
        VoucherPostingService(voucher, user, request).post()
    except VoucherValidationError as exc:
        raise ValidationError({'detail': exc.errors})
    voucher.refresh_from_db()
    return voucher
