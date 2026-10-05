"""The three statements: profit & loss, balance sheet, cash flow.

One rule runs through all of them, and it is the rule the sales ledger already
enforces: **revenue is what has been earned, cash is what has been collected,
and the two are never added together.**

* Profit & loss counts a sale from the moment Accounts *post* it.
* Cash flow counts it only once Accounts *confirm* the money, with the method
  and the invoice — and it ignores supplier payments settled out of credit,
  because no money left the till for those.
* The balance sheet reads both: stock and debtors as assets, what is owed to
  suppliers as a liability.

Where the system does not hold a figure, these say so rather than invent one.
There are no capital accounts here — no opening balances, no drawings, no fixed
assets — so the balance sheet reports what it can measure and names the
remainder as unrecorded rather than quietly plugging it into equity.

**The voucher ledger is counted here too, and it is counted once.** Nothing
auto-posts a voucher: `signals.py` mirrors a sale into the *sales* ledger and
keeps a chart-of-accounts row per party, and that is all. So every
`GeneralLedgerEntry` came from a voucher somebody keyed — a back-dated
invoice, a supplier bill the till never saw, a journal — and none of it is
also in `SalesLedgerEntry`, `Expense`, `Income`, `TaxPayment`,
`PettyCashTransaction`, `OtherPayment` or `SupplierPayment`. The two sources
are disjoint by construction, which is why they can simply be added.

Each statement therefore reports the ledger's contribution on its own named
lines ("… (vouchers)") rather than blending it into the till's figures: the
totals are complete, and anyone reading can still see which record a number
came from.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.db.models import F, Sum

from apps.inventory.models import PurchaseOrder, Stock
from .credit import credit_balances
from .models import (
    Expense, GeneralLedgerEntry, Income, Invoice, LedgerAccount, OtherPayment,
    PettyCashTransaction, SalesLedgerEntry, SupplierPayment, TaxPayment,
)

ZERO = Decimal('0')


# ---------------------------------------------------------------------------
# What the voucher ledger contributes
# ---------------------------------------------------------------------------

def _ledger_movement(account_type, date_from=None, date_to=None, kinds=None,
                     exclude_kinds=None):
    """Movement on one account type over a window, read the way that type is
    read: debit-positive for assets and expenses, credit-positive for income,
    liabilities and equity. So every figure comes back as the positive number
    a reader expects, and a reversal shows as a reduction rather than a second
    entry.
    """
    qs = GeneralLedgerEntry.objects.filter(account__account_type=account_type)
    if kinds:
        qs = qs.filter(account__kind__in=kinds)
    if exclude_kinds:
        qs = qs.exclude(account__kind__in=exclude_kinds)
    if date_from:
        qs = qs.filter(date__gte=date_from)
    if date_to:
        qs = qs.filter(date__lte=date_to)
    agg = qs.aggregate(d=Sum('debit'), c=Sum('credit'))
    debit, credit = (agg['d'] or ZERO), (agg['c'] or ZERO)
    if account_type in (LedgerAccount.ASSET, LedgerAccount.EXPENSE):
        return debit - credit
    return credit - debit


def _voucher_money_flow(date_from, date_to):
    """What moved through the cash books and bank accounts on vouchers.

    `in` is what was debited to a money ledger and `out` what was credited —
    a Receipt brings money in, a Payment takes it out, and a Contra does both
    at once, which is why a transfer between our own pockets nets to nothing
    here instead of being double-counted.
    """
    qs = GeneralLedgerEntry.objects.filter(account__kind__in=LedgerAccount.MONEY_KINDS)
    if date_from:
        qs = qs.filter(date__gte=date_from)
    if date_to:
        qs = qs.filter(date__lte=date_to)
    agg = qs.aggregate(d=Sum('debit'), c=Sum('credit'))
    return (agg['d'] or ZERO), (agg['c'] or ZERO)


def _register_outstanding(party_field):
    """What the books' own invoice register still shows as owed, one way or
    the other. Derived from the register, never stored."""
    rows = (Invoice.objects.outstanding()
            .filter(**{f'{party_field}__isnull': False}))
    return sum((row.outstanding_amount for row in rows), ZERO)


def period_from(params):
    """The window a statement covers. Defaults to the month to date."""
    today = date.today()
    start = (params.get('date_from') or '').strip() or today.replace(day=1).isoformat()
    end = (params.get('date_to') or '').strip() or today.isoformat()
    return start, end


def _sum(qs, field='amount'):
    return qs.aggregate(t=Sum(field))['t'] or ZERO


def _line(label, amount, source, kind='cost'):
    return {'label': label, 'amount': str(amount), 'source': source, 'kind': kind}


# ---------------------------------------------------------------------------
# Profit & loss
# ---------------------------------------------------------------------------

def profit_and_loss(date_from, date_to):
    """Revenue is **posted sales only** — a sale the accountant has not taken
    up is not in the books, whatever the till did with it. Cash confirmed is
    reported beside it but never in place of it."""
    posted = SalesLedgerEntry.objects.filter(
        status='posted', sale_date__gte=date_from, sale_date__lte=date_to)

    sales = posted.aggregate(
        revenue=Sum('total_amount'),
        discounts=Sum('discount'),
        cost=Sum('cost_of_sales'),
        commission=Sum('commission_total'),
        confirmed=Sum('confirmed_amount'),
    )
    revenue = sales['revenue'] or ZERO
    cost = sales['cost'] or ZERO
    commission = sales['commission'] or ZERO
    confirmed = sales['confirmed'] or ZERO

    expenses = _sum(Expense.objects.filter(
        date_incurred__gte=date_from, date_incurred__lte=date_to))
    petty = _sum(PettyCashTransaction.objects.filter(
        entry_type='out', date__gte=date_from, date__lte=date_to))
    other_out = _sum(OtherPayment.objects.filter(
        payment_date__gte=date_from, payment_date__lte=date_to))
    taxes = _sum(TaxPayment.objects.filter(
        payment_date__gte=date_from, payment_date__lte=date_to))
    other_income = _sum(Income.objects.filter(
        date_received__gte=date_from, date_received__lte=date_to))

    # The voucher ledger's own income and expense over the same window. These
    # are the invoices and journals the till never saw, so they are added to
    # rather than reconciled against the figures above (see the module note).
    voucher_income = _ledger_movement(LedgerAccount.INCOME, date_from, date_to)
    voucher_expense = _ledger_movement(LedgerAccount.EXPENSE, date_from, date_to)

    gross_profit = revenue + voucher_income - cost
    operating_costs = expenses + petty + other_out + commission + voucher_expense
    operating_profit = gross_profit - operating_costs
    net_profit = operating_profit + other_income - taxes

    return {
        'date_from': str(date_from),
        'date_to': str(date_to),
        'sales_count': posted.count(),
        'revenue': str(revenue + voucher_income),
        'revenue_till': str(revenue),
        'revenue_vouchers': str(voucher_income),
        'discounts': str(sales['discounts'] or ZERO),
        'cost_of_sales': str(cost),
        'gross_profit': str(gross_profit),
        'operating_costs': str(operating_costs),
        'operating_profit': str(operating_profit),
        'other_income': str(other_income),
        'taxes': str(taxes),
        'net_profit': str(net_profit),
        'cash_confirmed': str(confirmed),
        'cash_outstanding': str(revenue - confirmed),
        'lines': [
            _line('Revenue (posted sales)', revenue, 'Sales accounting, posted only', 'revenue'),
            _line('Revenue (sales vouchers)', voucher_income,
                  'General Ledger — income on posted vouchers', 'revenue'),
            _line('Cost of sales', cost, 'Product cost at the time of each sale'),
            _line('Sales commission', commission, 'Commission frozen on each sale line'),
            _line('Expenses', expenses, 'Finance > Expenses'),
            _line('Expenses (purchase vouchers)', voucher_expense,
                  'General Ledger — expense on posted vouchers'),
            _line('Petty cash paid out', petty, 'Cashier > Petty Cash'),
            _line('Other payments', other_out, 'Cashier > Other Payments'),
            _line('Other income', other_income, 'Finance > Other Income', 'revenue'),
            _line('Taxes paid', taxes, 'Finance > Taxes & Govt'),
        ],
    }


# ---------------------------------------------------------------------------
# Cash flow
# ---------------------------------------------------------------------------

def cash_flow(date_from, date_to):
    """Money that actually moved, in the window it moved.

    Receipts are dated by **when Accounts confirmed them**, not by when the
    sale was made — that is the whole point of the confirmation step. Supplier
    payments settled from credit are left out: they settle an order but no
    money leaves the business.

    Petty cash top-ups are a transfer between our own pockets, not a flow, so
    they are reported separately and never counted in either direction.

    Money that moved on a **voucher** counts too, on its own lines: what was
    debited to a cash book or bank account came in, what was credited went
    out. A Contra does both at once, so moving money between our own pockets
    nets to nothing here rather than appearing twice.
    """
    receipts = _sum(SalesLedgerEntry.objects.filter(
        payment_status='confirmed',
        confirmed_at__date__gte=date_from, confirmed_at__date__lte=date_to,
    ), 'confirmed_amount')
    other_income = _sum(Income.objects.filter(
        date_received__gte=date_from, date_received__lte=date_to))

    supplier_cash = _sum(SupplierPayment.objects.filter(
        status='paid', from_credit=False,
        payment_date__gte=date_from, payment_date__lte=date_to))
    supplier_credit_used = _sum(SupplierPayment.objects.filter(
        status='paid', from_credit=True,
        payment_date__gte=date_from, payment_date__lte=date_to))
    expenses = _sum(Expense.objects.filter(
        date_incurred__gte=date_from, date_incurred__lte=date_to))
    petty_out = _sum(PettyCashTransaction.objects.filter(
        entry_type='out', date__gte=date_from, date__lte=date_to))
    petty_in = _sum(PettyCashTransaction.objects.filter(
        entry_type='in', date__gte=date_from, date__lte=date_to))
    other_out = _sum(OtherPayment.objects.filter(
        payment_date__gte=date_from, payment_date__lte=date_to))
    taxes = _sum(TaxPayment.objects.filter(
        payment_date__gte=date_from, payment_date__lte=date_to))

    voucher_in, voucher_out = _voucher_money_flow(date_from, date_to)

    money_in = receipts + other_income + voucher_in
    money_out = supplier_cash + expenses + petty_out + other_out + taxes + voucher_out
    net = money_in - money_out

    # What was invoiced in the window but has not been confirmed as received —
    # the gap between trading well and being paid.
    invoiced = _sum(SalesLedgerEntry.objects.filter(
        status='posted', sale_date__gte=date_from, sale_date__lte=date_to), 'total_amount')

    return {
        'date_from': str(date_from),
        'date_to': str(date_to),
        'money_in': str(money_in),
        'money_out': str(money_out),
        'net_movement': str(net),
        'receipts': str(receipts),
        'other_income': str(other_income),
        'supplier_payments': str(supplier_cash),
        'supplier_credit_used': str(supplier_credit_used),
        'expenses': str(expenses),
        'petty_cash_out': str(petty_out),
        'petty_cash_in': str(petty_in),
        'other_payments': str(other_out),
        'taxes': str(taxes),
        'voucher_receipts': str(voucher_in),
        'voucher_payments': str(voucher_out),
        'invoiced_posted': str(invoiced),
        'not_yet_collected': str(invoiced - receipts),
        'in_lines': [
            _line('Sales receipts confirmed', receipts,
                  'Sales accounting, dated by when Accounts confirmed the money', 'revenue'),
            _line('Other income', other_income, 'Finance > Other Income', 'revenue'),
            _line('Received on vouchers', voucher_in,
                  'General Ledger — cash books and bank accounts debited', 'revenue'),
        ],
        'out_lines': [
            _line('Supplier payments', supplier_cash, 'Approved payments, excluding credit applications'),
            _line('Expenses', expenses, 'Finance > Expenses'),
            _line('Petty cash paid out', petty_out, 'Cashier > Petty Cash'),
            _line('Other payments', other_out, 'Cashier > Other Payments'),
            _line('Taxes paid', taxes, 'Finance > Taxes & Govt'),
            _line('Paid on vouchers', voucher_out,
                  'General Ledger — cash books and bank accounts credited'),
        ],
        'notes': [
            ('Petty cash top-ups of %s are a transfer between our own pockets, '
             'so they count in neither direction.') % petty_in,
            ('%s of supplier orders were settled out of credit they already held — '
             'no money left the business for those.') % supplier_credit_used,
        ],
    }


# ---------------------------------------------------------------------------
# Balance sheet
# ---------------------------------------------------------------------------

def _stock_at_cost():
    """What the goods on the shelves cost us."""
    rows = Stock.objects.select_related('product').annotate(
        value=F('quantity') * F('product__cost')).values_list('value', flat=True)
    return sum((v or ZERO for v in rows), ZERO)


def _debtors():
    """Invoices whose money Accounts have not confirmed as received.

    Not limited to posted rows. Posting and confirming the money are one act
    now, so a sale still waiting on Accounts is precisely one nobody has been
    paid for — which is what a debtor is. Cancelled and deleted sales are left
    out; there is nothing to collect on those.
    """
    rows = (SalesLedgerEntry.objects
            .filter(payment_status='awaiting')
            .exclude(sale_status__in=['cancelled', 'deleted'])
            .aggregate(invoiced=Sum('total_amount'), confirmed=Sum('confirmed_amount')))
    return max((rows['invoiced'] or ZERO) - (rows['confirmed'] or ZERO), ZERO)


def _creditors():
    """What is still owed on purchase orders, per order — being short on one
    order does not cancel an overpayment on another, that is a credit.

    Every order that has not been cancelled counts, including those still
    marked 'draft' in the database: those are placed orders waiting for
    delivery, not something somebody is half-way through typing, so the money
    on them is owed.
    """
    orders = (PurchaseOrder.objects
              .exclude(status='cancelled')
              .filter(supplier__isnull=False)
              .values_list('total_amount', 'id'))
    paid_by_order = {
        row['purchase_order']: row['t'] or ZERO
        for row in SupplierPayment.objects.filter(status='paid', purchase_order__isnull=False)
        .values('purchase_order').annotate(t=Sum('amount'))
    }
    owed = ZERO
    for total, po_id in orders:
        owed += max((total or ZERO) - paid_by_order.get(po_id, ZERO), ZERO)
    return owed


def balance_sheet():
    """Where the business stands **today**.

    Deliberately not as at an arbitrary past date: stock is counted as it
    stands now, and the system keeps no history of what it was, so a sheet
    dated backwards would be a guess wearing a date.
    """
    # Cash is derived from what has actually moved since the books began:
    # confirmed receipts and other income, less every payment out. Without it
    # a balance sheet would show net assets *falling* when a customer pays,
    # because the debtor would clear with nothing to replace it. The petty cash
    # float is part of this total, reported below it as a memo rather than
    # added again.
    to_date = cash_flow('1900-01-01', date.today().isoformat())
    cash = Decimal(to_date['net_movement'])
    petty = PettyCashTransaction.balance()
    stock = _stock_at_cost()
    debtors = _debtors()
    supplier_credit = sum(
        (row['available'] for row in credit_balances().values()), ZERO)

    creditors = _creditors()

    # The books' own invoice register: invoices and bills the till never saw,
    # so they are owed in addition to the above rather than instead of it.
    # An `Invoice` row is only ever raised by posting a Sales or Purchase
    # voucher, or as a party opening balance — never by a `Sale` or a
    # `PurchaseOrder` — so the two cannot overlap.
    register_debtors = _register_outstanding('customer')
    register_creditors = _register_outstanding('supplier')

    assets = cash + stock + debtors + register_debtors + supplier_credit
    liabilities = creditors + register_creditors
    net_assets = assets - liabilities

    # Everything the business has earned since it started keeping these books.
    earned = profit_and_loss('1900-01-01', date.today().isoformat())
    retained = Decimal(earned['net_profit'])

    return {
        'as_at': date.today().isoformat(),
        'assets': str(assets),
        'liabilities': str(liabilities),
        'net_assets': str(net_assets),
        'retained_earnings': str(retained),
        'unrecorded': str(net_assets - retained),
        'cash': str(cash),
        'petty_cash_float': str(petty),
        'debtors': str(debtors + register_debtors),
        'creditors': str(creditors + register_creditors),
        'asset_lines': [
            _line('Cash and bank', cash,
                  'Confirmed receipts and income, less every payment out, since the books began',
                  'revenue'),
            _line('   of which petty cash float', petty, 'Cashier > Petty Cash, in less out', 'memo'),
            _line('Stock at cost', stock, 'Quantity on hand x product cost', 'revenue'),
            _line('Debtors', debtors, 'Invoices Accounts have not confirmed as paid', 'revenue'),
            _line('Debtors (invoice register)', register_debtors,
                  'Unpaid customer invoices in the books themselves', 'revenue'),
            _line('Credit held with suppliers', supplier_credit, 'Overpayments not yet applied', 'revenue'),
        ],
        'liability_lines': [
            _line('Owed to suppliers', creditors,
                  'Every live purchase order, less approved payments'),
            _line('Owed to suppliers (invoice register)', register_creditors,
                  'Unpaid supplier bills in the books themselves'),
        ],
        'caveats': [
            'Cash is derived from the movements this system holds, not read from a bank statement — '
            'it has no opening balance, so it is what has moved through the books, not what is in '
            'the account.',
            'There are no capital, drawings or fixed-asset records, so the difference between '
            'net assets and retained earnings is shown as unrecorded rather than plugged into equity.',
            'Stock is valued at what the goods cost today, on the quantities on hand today.',
        ],
    }
