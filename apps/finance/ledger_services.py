"""Balances and statements, every one of them derived.

Nothing in here is stored. Each figure is built from `GeneralLedgerEntry`
rows plus the opening balances on the chart of accounts, which is what keeps
a control account always equal to the sum of its parties and a trial balance
always equal to itself.

A group or control account aggregates everything beneath it in the chart; a
leaf ledger is only itself.
"""

from django.db.models import DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce

from .models import GeneralLedgerEntry, LedgerAccount
from .money import ZERO, quantize

DEC = DecimalField(max_digits=18, decimal_places=2)


def _movement(qs):
    agg = qs.aggregate(d=Coalesce(Sum('debit'), Value(ZERO), output_field=DEC),
                       c=Coalesce(Sum('credit'), Value(ZERO), output_field=DEC))
    return quantize(agg['d']), quantize(agg['c'])


def _account_ids(account):
    """The ledgers a balance for this account covers."""
    if account.is_group or account.is_control_account:
        return account.descendant_ids()
    return [account.pk]


def account_balance(account, as_of=None, from_date=None, financial_year=None, include_opening=True):
    """Signed balance (debit positive, credit negative).

    `from_date` makes it the movement over a period rather than a balance, so
    the opening figures are left out in that case — they are not movement.
    """
    ids = _account_ids(account)
    qs = GeneralLedgerEntry.objects.filter(account_id__in=ids)
    if as_of:
        qs = qs.filter(date__lte=as_of)
    if from_date:
        qs = qs.filter(date__gte=from_date)
    if financial_year:
        qs = qs.filter(financial_year=financial_year)
    debit, credit = _movement(qs)
    balance = debit - credit
    if include_opening and not from_date:
        opening = LedgerAccount.objects.filter(pk__in=ids).aggregate(
            dr=Coalesce(Sum('opening_balance', filter=Q(opening_side='debit')), Value(ZERO), output_field=DEC),
            cr=Coalesce(Sum('opening_balance', filter=Q(opening_side='credit')), Value(ZERO), output_field=DEC),
        )
        balance += quantize(opening['dr']) - quantize(opening['cr'])
    return quantize(balance)


def account_movements(as_of=None, from_date=None, financial_year=None):
    """{account_id: (debit, credit)} for every ledger, in one query."""
    qs = GeneralLedgerEntry.objects.all()
    if as_of:
        qs = qs.filter(date__lte=as_of)
    if from_date:
        qs = qs.filter(date__gte=from_date)
    if financial_year:
        qs = qs.filter(financial_year=financial_year)
    rows = qs.values('account_id').annotate(
        d=Coalesce(Sum('debit'), Value(ZERO), output_field=DEC),
        c=Coalesce(Sum('credit'), Value(ZERO), output_field=DEC))
    return {r['account_id']: (quantize(r['d']), quantize(r['c'])) for r in rows}


def trial_balance_rows(as_of=None, financial_year=None, include_zero=False):
    """Trial balance rows for every postable ledger: opening balance plus
    movements, landed on whichever side the result falls. Returns
    (rows, totals); the two totals agree, which is the point of it."""
    movements = account_movements(as_of=as_of, financial_year=financial_year)
    rows = []
    total_debit = total_credit = ZERO
    accounts = (LedgerAccount.objects.filter(is_group=False)
                .select_related('parent').order_by('code'))
    for account in accounts:
        debit, credit = movements.get(account.pk, (ZERO, ZERO))
        balance = quantize(account.signed_opening_balance) + debit - credit
        if balance == 0 and not include_zero:
            continue
        row_debit = balance if balance > 0 else ZERO
        row_credit = -balance if balance < 0 else ZERO
        total_debit += row_debit
        total_credit += row_credit
        rows.append({
            'account': account,
            'opening': quantize(account.signed_opening_balance),
            'period_debit': debit, 'period_credit': credit,
            'debit': row_debit, 'credit': row_credit,
        })
    totals = {'debit': quantize(total_debit), 'credit': quantize(total_credit),
              'difference': quantize(total_debit - total_credit)}
    return rows, totals


def related_ledgers(entries, account_ids):
    """For each entry, the *other* ledgers on the same voucher.

    This is the "Related Ledger Name" column of a ledger statement: a receipt
    into the bank shows the customer or income ledger it came from, a payment
    shows where it went. Ledgers on the opposite side of the entry come first,
    since they are the contra; same-side ledgers follow.
    """
    voucher_ids = {e.voucher_id for e in entries}
    if not voucher_ids:
        return {}
    others = (GeneralLedgerEntry.objects.filter(voucher_id__in=voucher_ids)
              .exclude(account_id__in=account_ids)
              .select_related('account').order_by('voucher_id', 'id'))
    by_voucher = {}
    for other in others:
        by_voucher.setdefault(other.voucher_id, []).append(other)
    result = {}
    for entry in entries:
        seen, opposite, same = set(), [], []
        for other in by_voucher.get(entry.voucher_id, []):
            if other.account_id in seen:
                continue
            seen.add(other.account_id)
            is_opposite = (other.credit > 0) if entry.debit > 0 else (other.debit > 0)
            (opposite if is_opposite else same).append(other.account)
        result[entry.pk] = opposite + same
    return result


def ledger_statement(account, start=None, end=None, credit_positive=False):
    """(opening, rows, closing) for one ledger with a running balance.

    `credit_positive` flips the sign for the statements people read that way
    round — a supplier's, where what is owed *to* them is the positive figure.
    """
    from datetime import timedelta

    ids = _account_ids(account)
    if start:
        opening = account_balance(account, as_of=start - timedelta(days=1))
    else:
        opening = quantize(sum((a.signed_opening_balance
                                for a in LedgerAccount.objects.filter(pk__in=ids)), ZERO))
    qs = GeneralLedgerEntry.objects.filter(account_id__in=ids)
    if start:
        qs = qs.filter(date__gte=start)
    if end:
        qs = qs.filter(date__lte=end)
    qs = (qs.select_related('account', 'voucher', 'customer', 'supplier', 'created_by')
          .order_by('date', 'voucher_id', 'id'))

    sign = -1 if credit_positive else 1
    entries = list(qs)
    related = related_ledgers(entries, ids)
    rows, running = [], opening
    for entry in entries:
        running += entry.debit - entry.credit
        rows.append({'entry': entry, 'balance': quantize(running * sign),
                     'related': related.get(entry.pk, [])})
    return quantize(opening * sign), rows, quantize(running * sign)


def party_statement(party, start=None, end=None):
    """A customer's or supplier's statement, off their own sub-ledger.

    A customer's balance is debit-positive (what they owe us); a supplier's is
    credit-positive (what we owe them). Which one this is is decided by the
    ledger the party carries, not by guessing from the class.
    """
    ledger = getattr(party, 'ledger', None)
    if ledger is None:
        return ZERO, [], ZERO
    return ledger_statement(ledger, start=start, end=end,
                            credit_positive=(ledger.kind == 'supplier'))


def bank_cash_balances(as_of=None):
    """[(ledger, balance)] for every cash book and bank account."""
    accounts = (LedgerAccount.objects
                .filter(is_active=True, is_group=False, kind__in=LedgerAccount.MONEY_KINDS)
                .order_by('code'))
    movements = account_movements(as_of=as_of)
    out = []
    for account in accounts:
        debit, credit = movements.get(account.pk, (ZERO, ZERO))
        out.append((account, quantize(account.signed_opening_balance + debit - credit)))
    return out


def type_total(account_type, start=None, end=None, as_of=None):
    """Total movement for an account type over a period — credit-positive for
    income and liabilities, debit-positive for assets and expenses, so every
    figure comes back as the positive number a reader expects."""
    qs = GeneralLedgerEntry.objects.filter(account__account_type=account_type)
    if start:
        qs = qs.filter(date__gte=start)
    if end or as_of:
        qs = qs.filter(date__lte=(end or as_of))
    debit, credit = _movement(qs)
    if account_type in (LedgerAccount.ASSET, LedgerAccount.EXPENSE):
        return quantize(debit - credit)
    return quantize(credit - debit)


def verify_voucher_double_entry(voucher):
    """True when a voucher's own GL rows balance exactly. Cheap enough to
    assert after posting, and the thing a bug would break first."""
    debit, credit = _movement(voucher.gl_entries.all())
    return debit == credit and debit > 0
