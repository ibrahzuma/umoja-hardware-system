"""Voucher number generation.

Numbers are sequential per voucher type and, when the configuration says so,
per financial year:

    SV-2026-000001, PU-2026-000001, RV-2026-000001, PV-2026-000001 ...

The prefix comes from `VoucherType` (editable under the accounting settings),
and the padding, separator, whether the year appears and whether the sequence
restarts each year all come from `AccountingSettings`.

The sequence row is locked inside a transaction, and the unique constraint on
`Voucher.number` is the final guard — a rare collision is simply retried.
"""

from django.db import IntegrityError, transaction

from .models import AccountingSettings, FinancialYear, VoucherNumberSequence, VoucherType

MAX_RETRIES = 5


class VoucherNumberService:
    def __init__(self, settings=None):
        self.settings = settings or AccountingSettings.get_solo()

    def sequence_key(self, financial_year):
        """Which sequence a voucher draws from: one per year, or one overall."""
        return financial_year if self.settings.reset_sequence_each_year else None

    def format_number(self, voucher_type, financial_year, sequence):
        prefix = VoucherType.prefix_for(voucher_type)
        separator = self.settings.number_separator or '-'
        parts = [prefix]
        if self.settings.include_financial_year_in_number and financial_year is not None:
            parts.append(str(financial_year.code))
        parts.append(str(sequence).zfill(self.settings.voucher_number_padding or 6))
        return separator.join(parts)

    def peek_next(self, voucher_type, financial_year):
        """The number the next voucher of this type would take, without
        reserving it — for showing on an empty form."""
        row = VoucherNumberSequence.objects.filter(
            voucher_type=voucher_type, financial_year=self.sequence_key(financial_year)).first()
        return self.format_number(voucher_type, financial_year,
                                  (row.last_number if row else 0) + 1)

    @transaction.atomic
    def next_number(self, voucher_type, financial_year):
        """Reserve and return (number, sequence_number)."""
        row, _ = VoucherNumberSequence.objects.select_for_update().get_or_create(
            voucher_type=voucher_type, financial_year=self.sequence_key(financial_year))
        row.last_number += 1
        row.save(update_fields=['last_number', 'updated_at'])
        return self.format_number(voucher_type, financial_year, row.last_number), row.last_number

    def assign(self, voucher):
        """Put a fresh number on an unsaved or renumbered voucher."""
        from .models import Voucher

        year = voucher.financial_year or FinancialYear.for_date(voucher.date)
        for _ in range(MAX_RETRIES):
            number, sequence = self.next_number(voucher.voucher_type, year)
            if not Voucher.objects.filter(number=number).exists():
                voucher.number = number
                voucher.sequence_number = sequence
                return number
        raise IntegrityError("Could not allocate a unique voucher number after several attempts.")
