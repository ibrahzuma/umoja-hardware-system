from django.core.management.base import BaseCommand

from apps.finance import sales_ledger


class Command(BaseCommand):
    help = ("Bring the accounting sales ledger up to date with every sale on "
            "record. Safe to re-run: open entries are refreshed, posted ones "
            "are left alone.")

    def handle(self, *args, **options):
        created, refreshed = sales_ledger.backfill()
        self.stdout.write(self.style.SUCCESS(
            f'Sales ledger synced: {created} added, {refreshed} refreshed.'))
