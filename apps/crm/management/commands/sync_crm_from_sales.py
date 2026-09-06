"""Bring existing sales into the CRM register.

New sales sync themselves (apps/crm/signals.py). This command is for the sales
already in the database when the sync was switched on, and for putting the
register right if it ever drifts. It is safe to run repeatedly: rows are matched
on the invoice number and payments on the originating transaction, so nothing is
duplicated.

    python manage.py sync_crm_from_sales
    python manage.py sync_crm_from_sales --since 2026-01-01
    python manage.py sync_crm_from_sales --dry-run
"""

from django.core.management.base import BaseCommand

from apps.crm import sync
from apps.crm.models import CustomerRecord
from apps.sales.models import Sale


class Command(BaseCommand):
    help = "Mirror existing sales (and their payments) into the CRM register."

    def add_arguments(self, parser):
        parser.add_argument('--since', help='Only sales created on or after this date (YYYY-MM-DD)')
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would change without writing anything')

    def handle(self, *args, **options):
        sales = Sale.objects.all().order_by('created_at')
        if options['since']:
            sales = sales.filter(created_at__date__gte=options['since'])

        total = sales.count()
        if options['dry_run']:
            existing = set(
                CustomerRecord.objects.exclude(source_invoice='')
                .values_list('source_invoice', flat=True)
            )
            new = sum(1 for s in sales if s.invoice_number not in existing
                      and s.status != 'cancelled')
            self.stdout.write(
                f'{total} sale(s) in scope: {new} would be added, '
                f'{total - new} already present or cancelled.'
            )
            return

        done, skipped = sync.sync_all(sales)
        self.stdout.write(self.style.SUCCESS(
            f'CRM register synced: {done} sale(s) written, {skipped} skipped (cancelled or unnumbered).'
        ))
