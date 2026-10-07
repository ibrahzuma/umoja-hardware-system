"""Write every posted sale that has no Sales voucher yet into the books.

Accounts' Post button does this as it goes; this catches the sales posted
before it did. Idempotent — a sale already in the books is left alone.

    python manage.py post_sales_to_books --dry-run
    python manage.py post_sales_to_books
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.finance.models import SalesLedgerEntry
from apps.finance.sales_ledger import books_voucher, post_to_books


class Command(BaseCommand):
    help = "Post every posted sale that has no Sales voucher into the General Ledger."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help="List them, write nothing.")

    def handle(self, *args, dry_run=False, **options):
        written = refused = 0
        for entry in SalesLedgerEntry.objects.filter(status='posted').order_by('sale_date', 'id'):
            if books_voucher(entry) is not None:
                continue
            if dry_run:
                self.stdout.write(f"would post {entry.invoice_number} ({entry.total_amount})")
                written += 1
                continue
            try:
                with transaction.atomic():
                    voucher = post_to_books(entry, entry.posted_by)
            except ValidationError as exc:
                refused += 1
                self.stderr.write(f"{entry.invoice_number}: refused — {exc.detail}")
                continue
            written += 1
            self.stdout.write(f"{entry.invoice_number} -> {voucher.number}")
        verb = 'to post' if dry_run else 'posted'
        self.stdout.write(self.style.SUCCESS(f"{written} {verb}, {refused} refused."))
