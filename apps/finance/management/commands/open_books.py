"""Open the books: everything the voucher engine needs before anything can
be posted.

Run this after `migrate` on a fresh install, and again after a release that
adds a default ledger — it is idempotent, so running it twice costs nothing
and changes nothing. The accounting screens call the same function, so a
system somebody has already used will find it has nothing to do.

    python manage.py open_books
    python manage.py open_books --year 2027          # and a year for next year
"""

from datetime import date

from django.core.management.base import BaseCommand

from apps.finance import vouchers
from apps.finance.models import (
    AccountingSettings, Currency, FinancialYear, LedgerAccount, VoucherType,
)


class Command(BaseCommand):
    help = "Create the base currency, financial year, voucher types and chart of accounts."

    def add_arguments(self, parser):
        parser.add_argument(
            '--year', type=int, default=None,
            help="Also create a financial year for this calendar year, if it is missing.")

    def handle(self, *args, **options):
        created = vouchers.open_the_books()

        year = options['year']
        if year:
            row, made = FinancialYear.objects.get_or_create(
                code=str(year),
                defaults={'name': f"Financial year {year}",
                          'start_date': date(year, 1, 1), 'end_date': date(year, 12, 31),
                          'is_active': False,
                          'notes': "Created by `manage.py open_books`."})
            self.stdout.write(f"Financial year {row.code}: "
                              f"{'created' if made else 'already there'}")

        base = Currency.base()
        current = FinancialYear.current()
        settings_row = AccountingSettings.get_solo()

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("The books are open."))
        self.stdout.write(f"  Base currency    {base.code if base else '(none)'}")
        self.stdout.write(f"  Financial year   {current.code if current else '(none)'}")
        self.stdout.write(f"  Voucher types    {VoucherType.objects.count()}")
        self.stdout.write(f"  Ledgers          {LedgerAccount.objects.count()} "
                          f"({created} created just now)")
        self.stdout.write(f"  Numbering        "
                          f"{'with' if settings_row.include_financial_year_in_number else 'without'}"
                          f" the year, {settings_row.voucher_number_padding} digits")
        if settings_row.period_lock_date:
            self.stdout.write(f"  Locked up to     {settings_row.period_lock_date}")

        missing = []
        if not LedgerAccount.objects.filter(is_customer_control=True).exists():
            missing.append("a customer control account")
        if not LedgerAccount.objects.filter(is_supplier_control=True).exists():
            missing.append("a supplier control account")
        if missing:
            self.stdout.write(self.style.WARNING(
                "Still missing: " + ", ".join(missing)))
