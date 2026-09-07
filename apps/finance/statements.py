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
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.db.models import F, Sum

from apps.inventory.models import PurchaseOrder, Stock
from .credit import credit_balances
from .models import (
    Expense, Income, OtherPayment, PettyCashTransaction, SalesLedgerEntry,
    SupplierPayment, TaxPayment,
)

ZERO = Decimal('0')


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

    gross_profit = revenue - cost
    operating_costs = expenses + petty + other_out + commission
    operating_profit = gross_profit - operating_costs
    net_profit = operating_profit + other_income - taxes

    return {
        'date_from': str(date_from),
        'date_to': str(date_to),
        'sales_count': posted.count(),
        'revenue': str(revenue),
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
            _line('Cost of sales', cost, 'Product cost at the time of each sale'),
            _line('Sales commission', commission, 'Commission frozen on each sale line'),
            _line('Expenses', expenses, 'Finance > Expenses'),
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

    money_in = receipts + other_income
    money_out = supplier_cash + expenses + petty_out + other_out + taxes
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
        'invoiced_posted': str(invoiced),
        'not_yet_collected': str(invoiced - receipts),
        'in_lines': [
            _line('Sales receipts confirmed', receipts,
                  'Sales accounting, dated by when Accounts confirmed the money', 'revenue'),
            _line('Other income', other_income, 'Finance > Other Income', 'revenue'),
        ],
        'out_lines': [
            _line('Supplier payments', supplier_cash, 'Approved payments, excluding credit applications'),
            _line('Expenses', expenses, 'Finance > Expenses'),
            _line('Petty cash paid out', petty_out, 'Cashier > Petty Cash'),
            _line('Other payments', other_out, 'Cashier > Other Payments'),
            _line('Taxes paid', taxes, 'Finance > Taxes & Govt'),
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
    """Posted invoices whose money Accounts have not confirmed."""
    rows = SalesLedgerEntry.objects.filter(status='posted').aggregate(
        invoiced=Sum('total_amount'), confirmed=Sum('confirmed_amount'))
    return max((rows['invoiced'] or ZERO) - (rows['confirmed'] or ZERO), ZERO)


def _creditors():
    """What is still owed on purchase orders, per order — being short on one
    order does not cancel an overpayment on another, that is a credit."""
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

    assets = cash + stock + debtors + supplier_credit
    liabilities = creditors
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
        'asset_lines': [
            _line('Cash and bank', cash,
                  'Confirmed receipts and income, less every payment out, since the books began',
                  'revenue'),
            _line('   of which petty cash float', petty, 'Cashier > Petty Cash, in less out', 'memo'),
            _line('Stock at cost', stock, 'Quantity on hand x product cost', 'revenue'),
            _line('Debtors', debtors, 'Posted invoices Accounts have not confirmed as paid', 'revenue'),
            _line('Credit held with suppliers', supplier_credit, 'Overpayments not yet applied', 'revenue'),
        ],
        'liability_lines': [
            _line('Owed to suppliers', creditors, 'Purchase orders less approved payments'),
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
