"""Bring the books that already exist up to the new engine.

The voucher engine gained a base currency, financial years, numbered
sequences, account types and a draft status. Everything already posted has to
land somewhere sensible in all of that, so:

  * TZS becomes the base currency (the shop trades in it; `settings.py` has
    said so since the start);
  * one financial year is created per calendar year that already carries a
    voucher or a ledger entry, plus the current one, so no posted voucher is
    left outside a year of account;
  * every voucher and ledger entry is filed under the year its date falls in;
  * the number sequences are wound forward past the numbers already issued,
    so the next voucher cannot collide with an old one;
  * `total_debit` / `total_credit` / `base_amount` are filled from the
    totals and line amounts that were already there;
  * every ledger takes the account type and category its kind implies, the
    two control accounts are created, and the party sub-ledgers are adopted
    under them.

Deliberately *not* done: nothing is re-posted and no GL row is rewritten.
Everything already in the ledger stays exactly as it was.
"""

from datetime import date
from decimal import Decimal

from django.db import migrations
from django.db.models import F

# These mirror LedgerAccount.TYPE_FOR_KIND / CATEGORY_FOR_KIND at the time of
# this migration. Copied rather than imported: a historical migration must not
# change meaning when the model does.
TYPE_FOR_KIND = {
    'bank': 'ASSET', 'cash': 'ASSET', 'customer': 'ASSET', 'asset': 'ASSET',
    'supplier': 'LIABILITY', 'liability': 'LIABILITY', 'tax': 'LIABILITY',
    'income': 'INCOME', 'expense': 'EXPENSE', 'equity': 'EQUITY',
}
CATEGORY_FOR_KIND = {
    'bank': 'CURRENT_ASSET', 'cash': 'CURRENT_ASSET', 'customer': 'CURRENT_ASSET',
    'asset': 'OTHER_ASSET', 'supplier': 'CURRENT_LIABILITY', 'liability': 'CURRENT_LIABILITY',
    'tax': 'CURRENT_LIABILITY', 'income': 'REVENUE', 'expense': 'OPERATING_EXPENSE',
    'equity': 'EQUITY',
}
VOUCHER_TYPES = (
    ('sales', 'Sales', 'SV'), ('purchase', 'Purchase', 'PU'), ('receipt', 'Receipt', 'RV'),
    ('payment', 'Payment', 'PV'), ('contra', 'Contra', 'CV'), ('journal', 'Journal', 'JV'),
)


def open_the_books(apps, schema_editor):
    Currency = apps.get_model('finance', 'Currency')
    FinancialYear = apps.get_model('finance', 'FinancialYear')
    AccountingSettings = apps.get_model('finance', 'AccountingSettings')
    VoucherType = apps.get_model('finance', 'VoucherType')
    VoucherNumberSequence = apps.get_model('finance', 'VoucherNumberSequence')
    LedgerAccount = apps.get_model('finance', 'LedgerAccount')
    Voucher = apps.get_model('finance', 'Voucher')
    VoucherLine = apps.get_model('finance', 'VoucherLine')
    GeneralLedgerEntry = apps.get_model('finance', 'GeneralLedgerEntry')

    today = date.today()

    # --- the base currency -------------------------------------------------
    base, _ = Currency.objects.get_or_create(
        code='TZS', defaults={'name': 'Tanzanian Shilling', 'symbol': 'TZS',
                              'is_base': True, 'is_active': True})
    if not Currency.objects.filter(is_base=True).exists():
        base.is_base = True
        base.save(update_fields=['is_base'])
    base = Currency.objects.filter(is_base=True).first()

    # --- the configuration row --------------------------------------------
    if not AccountingSettings.objects.exists():
        AccountingSettings.objects.create(
            financial_year_start=today.replace(month=1, day=1),
            financial_year_end=today.replace(month=12, day=31),
        )

    for code, label, prefix in VOUCHER_TYPES:
        VoucherType.objects.get_or_create(code=code, defaults={'name': label, 'prefix': prefix})

    # --- a financial year for every year that already has entries ---------
    years = set()
    years.update(v.year for v in Voucher.objects.values_list('date', flat=True) if v)
    years.update(e.year for e in GeneralLedgerEntry.objects.values_list('date', flat=True) if e)
    years.add(today.year)
    for year in sorted(years):
        FinancialYear.objects.get_or_create(
            code=str(year),
            defaults={
                'name': f"Financial year {year}",
                'start_date': date(year, 1, 1),
                'end_date': date(year, 12, 31),
                'is_active': year == today.year,
                'notes': "Created when the books were brought onto the voucher engine.",
            })
    # Exactly one active year, and it is this one where possible.
    FinancialYear.objects.update(is_active=False)
    current = (FinancialYear.objects.filter(code=str(today.year)).first()
               or FinancialYear.objects.order_by('-start_date').first())
    if current is not None:
        FinancialYear.objects.filter(pk=current.pk).update(is_active=True)

    by_year = {int(fy.code): fy for fy in FinancialYear.objects.all() if fy.code.isdigit()}

    # --- file what is already posted under its year, in bulk --------------
    for year, fy in by_year.items():
        Voucher.objects.filter(date__year=year, financial_year__isnull=True)\
            .update(financial_year=fy)
        GeneralLedgerEntry.objects.filter(date__year=year, financial_year__isnull=True)\
            .update(financial_year=fy)

    # --- the chart of accounts -------------------------------------------
    for kind, account_type in TYPE_FOR_KIND.items():
        LedgerAccount.objects.filter(kind=kind, account_type='').update(account_type=account_type)
    for kind, category in CATEGORY_FOR_KIND.items():
        LedgerAccount.objects.filter(kind=kind, category='').update(category=category)

    for name, vat_kind in (('Output VAT', 'OUTPUT'), ('Input VAT', 'INPUT')):
        LedgerAccount.objects.filter(kind='tax', name__iexact=name, vat_kind='')\
            .update(vat_kind=vat_kind)

    # The two control accounts, and the party ledgers adopted under them.
    # Only created when the chart already exists — a brand-new install gets
    # them from `vouchers.open_the_books` instead.
    if LedgerAccount.objects.exists():
        receivable = LedgerAccount.objects.filter(is_customer_control=True).first()
        if receivable is None:
            receivable = LedgerAccount.objects.create(
                code=_free_code(LedgerAccount, 'AR'), name='Accounts Receivable', kind='customer',
                account_type='ASSET', category='CURRENT_ASSET', is_customer_control=True,
                opening_side='debit',
                notes="Accounts Receivable control account — customer sub-ledgers hang under it.")
        payable = LedgerAccount.objects.filter(is_supplier_control=True).first()
        if payable is None:
            payable = LedgerAccount.objects.create(
                code=_free_code(LedgerAccount, 'AP'), name='Accounts Payable', kind='supplier',
                account_type='LIABILITY', category='CURRENT_LIABILITY', is_supplier_control=True,
                opening_side='credit',
                notes="Accounts Payable control account — supplier sub-ledgers hang under it.")
        LedgerAccount.objects.filter(kind='customer', customer__isnull=False, parent__isnull=True)\
            .exclude(pk=receivable.pk).update(parent=receivable)
        LedgerAccount.objects.filter(kind='supplier', supplier__isnull=False, parent__isnull=True)\
            .exclude(pk=payable.pk).update(parent=payable)

    # --- line and voucher amounts in the new columns ----------------------
    VoucherLine.objects.filter(base_amount=0).update(base_amount=F('amount'))
    for line in VoucherLine.objects.select_related('account').iterator(chunk_size=500):
        if line.customer_id or line.supplier_id:
            continue
        customer_id = getattr(line.account, 'customer_id', None)
        supplier_id = getattr(line.account, 'supplier_id', None)
        if customer_id or supplier_id:
            VoucherLine.objects.filter(pk=line.pk).update(customer_id=customer_id,
                                                          supplier_id=supplier_id)

    for voucher in Voucher.objects.iterator(chunk_size=500):
        total = voucher.total or Decimal('0.00')
        if voucher.total_debit or voucher.total_credit:
            continue
        Voucher.objects.filter(pk=voucher.pk).update(
            total_debit=total, total_credit=total,
            base_total_debit=total, base_total_credit=total,
            currency=base, exchange_rate=Decimal('1'))

    # Everything already in the ledger was keyed in the base currency.
    GeneralLedgerEntry.objects.filter(currency__isnull=True).update(
        currency=base, exchange_rate=Decimal('1'))
    GeneralLedgerEntry.objects.filter(foreign_debit=0, debit__gt=0)\
        .update(foreign_debit=F('debit'))
    GeneralLedgerEntry.objects.filter(foreign_credit=0, credit__gt=0)\
        .update(foreign_credit=F('credit'))

    # --- wind the sequences past the numbers already issued ---------------
    # The old numbering was `<PREFIX>-<n>` with no year, and the new default
    # puts the year in. Winding the per-year sequence to the highest number
    # ever issued for the type keeps every future number clear of the past,
    # whichever shape it takes.
    for code, _label, _prefix in VOUCHER_TYPES:
        highest = 0
        for number in Voucher.objects.filter(voucher_type=code).values_list('number', flat=True):
            tail = (number or '').rsplit('-', 1)[-1]
            if tail.isdigit():
                highest = max(highest, int(tail))
        if not highest:
            continue
        for fy in list(by_year.values()) + [None]:
            row, _ = VoucherNumberSequence.objects.get_or_create(
                voucher_type=code, financial_year=fy)
            if row.last_number < highest:
                row.last_number = highest
                row.save(update_fields=['last_number'])


def _free_code(LedgerAccount, prefix):
    """A code like AR, or AR-2 if something already holds AR."""
    code, n = prefix, 1
    while LedgerAccount.objects.filter(code=code).exists():
        n += 1
        code = f"{prefix}-{n}"
    return code


def close_the_books(apps, schema_editor):
    """Nothing to undo — the rows this created are additive, and the next
    migration down drops the tables that hold them."""


class Migration(migrations.Migration):

    dependencies = [
        ('finance', '0020_currency_exchangerate_financialyear_invoice_and_more'),
    ]

    operations = [
        migrations.RunPython(open_the_books, close_the_books),
    ]
