"""The accounting reports.

Thirteen reports, every one of them built from posted General Ledger entries,
the invoice register and the allocations against it — nothing stored, nothing
hard-coded. Each returns a plain dict with `columns` and `export_rows`
alongside whatever the template needs, which is what lets one view class
serve the screen, the print layout, the CSV and the Excel file.

These sit beside, not instead of, the three statements in `statements.py`.
Those answer "how is the business doing" from the till and the cash desk; the
reports here answer "what do the books say", off the ledger.
"""

from collections import OrderedDict

from django.utils import timezone

from .ledger_services import ledger_statement, party_statement, trial_balance_rows
from .models import GeneralLedgerEntry, Invoice, LedgerAccount, Voucher
from .money import ZERO, quantize

# (slug, title, bootstrap icon, one-line description) — the report index.
REPORTS = [
    ('general_ledger', 'General Ledger', 'bi-journal-text',
     'Ledger movements with running balances'),
    ('trial_balance', 'Trial Balance', 'bi-columns-gap',
     'Debit and credit balances of every ledger'),
    ('customer_statement', 'Customer Statement', 'bi-person-lines-fill',
     'Transactions and balance for one customer'),
    ('supplier_statement', 'Supplier Statement', 'bi-truck',
     'Transactions and balance for one supplier'),
    ('customer_outstanding', 'Customer Outstanding Invoices', 'bi-receipt',
     'Unpaid customer invoices, with ageing'),
    ('supplier_outstanding', 'Supplier Outstanding Bills', 'bi-receipt-cutoff',
     'Unpaid supplier bills, with ageing'),
    ('sales', 'Sales Register', 'bi-cart-check', 'Posted sales vouchers'),
    ('purchases', 'Purchase Register', 'bi-bag-check', 'Posted purchase vouchers'),
    ('receipts', 'Receipt Register', 'bi-cash-coin', 'Posted receipt vouchers'),
    ('payments', 'Payment Register', 'bi-credit-card', 'Posted payment vouchers'),
    ('contra', 'Contra Register', 'bi-arrow-left-right', 'Cash and bank transfers'),
    ('journal', 'Journal Register', 'bi-journal-bookmark', 'Posted journal vouchers'),
    ('vat', 'VAT Report', 'bi-percent', 'Output VAT against Input VAT'),
]
REPORT_TITLES = {slug: title for slug, title, _icon, _desc in REPORTS}


# ---------------------------------------------------------------------------
# General Ledger
# ---------------------------------------------------------------------------

def general_ledger(account=None, start=None, end=None):
    """One ledger's statement, or every ledger that has moved, each with its
    opening figure, its entries and its closing figure."""
    if account is not None:
        accounts = [account]
    else:
        accounts = list(LedgerAccount.objects
                        .filter(is_group=False, gl_entries__isnull=False)
                        .distinct().order_by('code'))
    columns = ['Date', 'Voucher', 'Related Ledger', 'Description', 'Debit', 'Credit', 'Balance']
    sections, export_rows = [], []
    for ledger in accounts:
        opening, rows, closing = ledger_statement(ledger, start, end)
        if not rows and opening == 0 and account is None:
            continue
        sections.append({
            'account': ledger, 'opening': opening, 'rows': rows, 'closing': closing,
            'debit': sum((r['entry'].debit for r in rows), ZERO),
            'credit': sum((r['entry'].credit for r in rows), ZERO),
        })
        export_rows.append(['', '', ledger.code, f"{ledger.name} — opening balance", '', '', opening])
        for row in rows:
            entry = row['entry']
            export_rows.append([
                entry.date, entry.voucher_number,
                ', '.join(str(a) for a in row['related']),
                entry.description, entry.debit, entry.credit, row['balance'],
            ])
        export_rows.append(['', '', ledger.code, f"{ledger.name} — closing balance", '', '', closing])
    return {'sections': sections, 'columns': columns, 'export_rows': export_rows,
            'start': start, 'end': end, 'account': account}


# ---------------------------------------------------------------------------
# Trial balance
# ---------------------------------------------------------------------------

def trial_balance(as_of=None, financial_year=None, include_zero=False):
    rows, totals = trial_balance_rows(as_of=as_of, financial_year=financial_year,
                                      include_zero=include_zero)
    columns = ['Code', 'Ledger', 'Type', 'Debit', 'Credit']
    export_rows = [[r['account'].code, r['account'].name,
                    r['account'].get_account_type_display() or '', r['debit'], r['credit']]
                   for r in rows]
    export_rows.append(['', 'TOTAL', '', totals['debit'], totals['credit']])
    return {'rows': rows, 'totals': totals, 'columns': columns, 'export_rows': export_rows,
            'as_of': as_of, 'financial_year': financial_year}


# ---------------------------------------------------------------------------
# Party statements
# ---------------------------------------------------------------------------

def statement(party, start=None, end=None):
    """A customer's or supplier's statement off their own sub-ledger."""
    opening, rows, closing = party_statement(party, start, end)
    columns = ['Date', 'Reference', 'Voucher', 'Description', 'Debit', 'Credit', 'Balance']
    export_rows = [['', '', '', 'Opening balance', '', '', opening]]
    for row in rows:
        entry = row['entry']
        export_rows.append([entry.date, entry.reference, entry.voucher_number, entry.description,
                            entry.debit, entry.credit, row['balance']])
    export_rows.append(['', '', '', 'Closing balance', '', '', closing])
    return {
        'party': party, 'opening': opening, 'rows': rows, 'closing': closing,
        'columns': columns, 'export_rows': export_rows, 'start': start, 'end': end,
        'debit_total': sum((r['entry'].debit for r in rows), ZERO),
        'credit_total': sum((r['entry'].credit for r in rows), ZERO),
    }


# ---------------------------------------------------------------------------
# Outstanding, with ageing
# ---------------------------------------------------------------------------

AGE_BUCKETS = ('0-30', '31-60', '61-90', '90+')


def age_bucket(days):
    if days <= 30:
        return '0-30'
    if days <= 60:
        return '31-60'
    if days <= 90:
        return '61-90'
    return '90+'


def outstanding(party_field, as_of=None):
    """Unpaid invoices (`customer`) or bills (`supplier`), grouped by party
    and aged into the four usual buckets."""
    as_of = as_of or timezone.localdate()
    invoices = (Invoice.objects.outstanding()
                .filter(**{f'{party_field}__isnull': False}, invoice_date__lte=as_of)
                .select_related('customer', 'supplier', 'voucher')
                .order_by(f'{party_field}__name', 'invoice_date', 'id'))
    groups, export_rows = OrderedDict(), []
    buckets = {b: ZERO for b in AGE_BUCKETS}
    grand = ZERO
    for invoice in invoices:
        party = getattr(invoice, party_field)
        amount = invoice.outstanding_amount
        days = (as_of - invoice.invoice_date).days
        bucket = age_bucket(days)
        buckets[bucket] += amount
        grand += amount
        group = groups.setdefault(party.pk, {'party': party, 'rows': [], 'total': ZERO})
        group['rows'].append({'invoice': invoice, 'outstanding': amount,
                              'days': days, 'bucket': bucket})
        group['total'] += amount
        export_rows.append([party.name, invoice.invoice_number, invoice.invoice_date,
                            invoice.original_amount, invoice.allocated_amount, amount,
                            days, bucket])
    columns = ['Name', 'Invoice', 'Date', 'Original', 'Paid', 'Outstanding', 'Days', 'Ageing']
    export_rows.append(['TOTAL', '', '', '', '', grand, '', ''])
    return {'groups': list(groups.values()), 'buckets': buckets, 'grand_total': quantize(grand),
            'columns': columns, 'export_rows': export_rows, 'as_of': as_of}


# ---------------------------------------------------------------------------
# Voucher registers
# ---------------------------------------------------------------------------

def _register_queryset(voucher_type, start=None, end=None):
    qs = Voucher.objects.filter(voucher_type=voucher_type, status__in=Voucher.LIVE_STATUSES)
    if start:
        qs = qs.filter(date__gte=start)
    if end:
        qs = qs.filter(date__lte=end)
    return (qs.select_related('customer', 'supplier', 'financial_year', 'created_by', 'posted_by')
            .prefetch_related('lines__account').order_by('date', 'number'))


def invoice_register(voucher_type, start=None, end=None):
    """The sales or purchase register: one row per invoice, VAT split out."""
    party_field = 'customer' if voucher_type == 'sales' else 'supplier'
    rows, export_rows = [], []
    totals = {'net': ZERO, 'vat': ZERO, 'total': ZERO}
    for voucher in _register_queryset(voucher_type, start, end):
        net = (quantize(voucher.net_amount) if voucher.net_amount
               else quantize(voucher.total - voucher.vat_amount))
        rows.append({'voucher': voucher, 'net': net})
        totals['net'] += net
        totals['vat'] += quantize(voucher.vat_amount)
        totals['total'] += quantize(voucher.total)
        party = getattr(voucher, party_field)
        export_rows.append([
            voucher.date, voucher.number, voucher.invoice_number, voucher.efd_number,
            (party.name if party else ''), voucher.get_payment_status_display() or '',
            net, voucher.vat_amount, voucher.total, voucher.get_status_display(),
        ])
    columns = ['Date', 'Voucher', 'Invoice No', 'EFD No', party_field.title(), 'Payment',
               'VAT excl.', 'VAT', 'Total', 'Status']
    export_rows.append(['', 'TOTAL', '', '', '', '', totals['net'], totals['vat'],
                        totals['total'], ''])
    return {'rows': rows, 'totals': totals, 'columns': columns, 'export_rows': export_rows,
            'start': start, 'end': end, 'party_label': party_field.title()}


def simple_register(voucher_type, start=None, end=None):
    """Receipts, payments, contras and journals: one row per voucher with the
    ledgers on each side spelt out, which is how somebody scanning a register
    actually recognises a document."""
    rows, export_rows, total = [], [], ZERO
    for voucher in _register_queryset(voucher_type, start, end):
        lines = list(voucher.lines.all())
        debits = ', '.join(f"{l.account.code} {l.account.name}" for l in lines if l.side == 'debit')
        credits = ', '.join(f"{l.account.code} {l.account.name}" for l in lines if l.side == 'credit')
        rows.append({'voucher': voucher, 'debits': debits, 'credits': credits})
        total += quantize(voucher.total_debit or voucher.total)
        party = voucher.party
        export_rows.append([
            voucher.date, voucher.number, debits, credits, (party.name if party else ''),
            voucher.reference, voucher.description,
            quantize(voucher.total_debit or voucher.total), voucher.get_status_display(),
        ])
    columns = ['Date', 'Voucher', 'Debit ledgers', 'Credit ledgers', 'Customer/Supplier',
               'Reference', 'Description', 'Amount', 'Status']
    export_rows.append(['', 'TOTAL', '', '', '', '', '', total, ''])
    return {'rows': rows, 'total': quantize(total), 'columns': columns,
            'export_rows': export_rows, 'start': start, 'end': end}


# ---------------------------------------------------------------------------
# VAT
# ---------------------------------------------------------------------------

def _vat_section(vat_kind, start=None, end=None):
    """Every entry on a VAT ledger of this kind.

    Output VAT is a credit and Input VAT a debit, so each side is read in its
    own direction — which also means a reversal shows up with the opposite
    sign and nets itself off, rather than inflating the figure.
    """
    ledgers = [a.pk for a in LedgerAccount.objects.filter(is_group=False)
               if a.is_vat_ledger(vat_kind)]
    qs = GeneralLedgerEntry.objects.filter(account_id__in=ledgers)
    if start:
        qs = qs.filter(date__gte=start)
    if end:
        qs = qs.filter(date__lte=end)
    qs = (qs.select_related('account', 'voucher', 'voucher__customer', 'voucher__supplier')
          .order_by('date', 'voucher_number'))
    rows, total = [], ZERO
    for entry in qs:
        voucher = entry.voucher
        amount = ((entry.credit - entry.debit) if vat_kind == LedgerAccount.VAT_OUTPUT
                  else (entry.debit - entry.credit))
        total += amount
        net = quantize(voucher.net_amount) if voucher.is_invoice else ZERO
        rows.append({'entry': entry, 'voucher': voucher, 'party': voucher.party,
                     'amount': amount, 'net': net})
    return rows, quantize(total)


def vat_report(start=None, end=None):
    output_rows, output_total = _vat_section(LedgerAccount.VAT_OUTPUT, start, end)
    input_rows, input_total = _vat_section(LedgerAccount.VAT_INPUT, start, end)
    columns = ['Section', 'Date', 'Voucher', 'Invoice No', 'EFD No', 'Party',
               'VAT excl.', 'VAT amount']
    export_rows = []
    for label, rows in (('Output VAT', output_rows), ('Input VAT', input_rows)):
        for row in rows:
            voucher = row['voucher']
            export_rows.append([label, voucher.date, voucher.number, voucher.invoice_number,
                                voucher.efd_number,
                                (row['party'].name if row['party'] else ''),
                                row['net'], row['amount']])
    net_vat = quantize(output_total - input_total)
    export_rows.append(['Output VAT total', '', '', '', '', '', '', output_total])
    export_rows.append(['Input VAT total', '', '', '', '', '', '', input_total])
    export_rows.append(['Net VAT payable', '', '', '', '', '', '', net_vat])
    return {'output_rows': output_rows, 'output_total': output_total,
            'input_rows': input_rows, 'input_total': input_total, 'net_vat': net_vat,
            'columns': columns, 'export_rows': export_rows, 'start': start, 'end': end}
