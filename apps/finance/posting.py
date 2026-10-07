"""The accounting core: posting, cancelling and reversing a voucher.

`VoucherPostingService.post()` is the only way an entry reaches the General
Ledger. It:

    1. validates the header, the financial year and the period locks
    2. validates every line — ledger, amount, voucher-type restriction
    3. validates Total Debit = Total Credit
    4. validates the party rules and the invoice references
    5. validates every allocation against what is still outstanding
    6. writes one `GeneralLedgerEntry` per line
    7. raises the customer invoice / supplier bill for a sales or purchase
       voucher, recording any part settled on the spot as an allocation
    8. marks the voucher posted and writes the audit trail

...all inside one transaction. Any failure rolls the lot back, so the ledger
can never hold half a voucher.

A posted voucher is never edited. `VoucherReversalService` posts the mirror
image as a Journal and marks the original `reversed`; `VoucherCancellationService`
handles a draft that was never posted.
"""

from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .audit import log_action
from .balancing import BalancingService
from .models import (
    AccountingAuditLog, AccountingSettings, FinancialYear, GeneralLedgerEntry, Invoice,
    LedgerAccount, Voucher, VoucherAllocation, VoucherLine,
)
from .money import ZERO, quantize
from .restrictions import account_allowed, restriction_error

ONE = Decimal('1')


def _may(user, permission):
    """Does this user hold an accounting permission?

    Superusers and the `admin` role hold all of them, by the same
    `is_privileged` rule the rest of the system uses — a specialist
    permission must never be able to lock an administrator out of the books.
    """
    if user is None:
        return True                      # a system caller, e.g. a migration
    from apps.users.permissions import is_privileged
    if is_privileged(user):
        return True
    return user.has_perm(permission)


def may_post(user):
    return _may(user, 'finance.post_voucher')


def may_reverse(user):
    return _may(user, 'finance.reverse_voucher')


def may_post_closed_period(user):
    """Deliberately *not* routed through `is_privileged`'s role check alone —
    it is, but only because an administrator is exactly who should be able to
    re-open a locked period. An accountant is not given this one."""
    return _may(user, 'finance.post_closed_period')


class VoucherValidationError(Exception):
    """A voucher failed validation. `errors` is a list of readable messages —
    all of them, not just the first, so the user can fix the lot in one pass."""

    def __init__(self, errors):
        if isinstance(errors, str):
            errors = [errors]
        self.errors = list(errors)
        super().__init__('; '.join(self.errors))


def allocations_of(voucher):
    """A voucher's allocations. They hang off its lines, so a line being
    replaced takes its allocations with it."""
    return VoucherAllocation.objects.filter(line__voucher=voucher)


def check_period(transaction_date, user, settings=None):
    """(errors, financial_year) for posting on `transaction_date`.

    The transaction date is independent of today's date — back-dated work is
    ordinary accounting. What is *not* ordinary is posting into a year that
    has been closed, or before a lock date, and that needs the
    `finance.post_closed_period` permission.
    """
    from .drafts import as_date

    errors = []
    settings = settings or AccountingSettings.get_solo()
    transaction_date = as_date(transaction_date)
    if transaction_date is None:
        return ["Give the voucher a date."], None
    year = FinancialYear.for_date(transaction_date)
    if year is None:
        errors.append(
            f"No financial year covers the transaction date "
            f"{transaction_date:%d/%m/%Y}. Create the financial year before posting.")
        return errors, None

    can_override = may_post_closed_period(user)
    if year.is_closed and not can_override:
        errors.append(f"Financial year {year.code} is closed. You are not authorised to "
                      f"post into a closed period.")
    elif year.lock_date and transaction_date <= year.lock_date and not can_override:
        errors.append(f"The period up to {year.lock_date:%d/%m/%Y} is locked in financial year "
                      f"{year.code}. You are not authorised to post into a locked period.")
    if (settings.period_lock_date and transaction_date <= settings.period_lock_date
            and not can_override):
        errors.append(f"Transactions dated on or before {settings.period_lock_date:%d/%m/%Y} "
                      f"are locked by company policy.")
    if (not settings.allow_backdated_entries and transaction_date < timezone.localdate()
            and not can_override):
        errors.append("Prior-dated transactions are switched off in the accounting configuration.")
    return errors, year


class VoucherPostingService:
    def __init__(self, voucher, user, request=None, settings=None):
        self.voucher = voucher
        self.user = user
        self.request = request
        self.settings = settings or AccountingSettings.get_solo()
        self.lines = list(voucher.lines.select_related(
            'account', 'account__parent', 'account__customer', 'account__supplier'))
        self.allocations = list(allocations_of(voucher).select_related(
            'invoice', 'invoice__customer', 'invoice__supplier', 'line', 'line__account'))
        self.errors = []

    # ------------------------------------------------------------- validation
    def validate(self):
        """Every rule, and the full list of what failed."""
        self.errors = []
        voucher = self.voucher
        if voucher.status != 'draft':
            self.errors.append(f"Voucher {voucher.number} is "
                               f"{voucher.get_status_display().lower()} and cannot be posted again.")
            return self.errors
        if not may_post(self.user):
            self.errors.append("You do not have permission to post vouchers.")
        self._validate_header()
        self._validate_lines()
        self._validate_balance()
        self._validate_party_rules()
        self._validate_allocations()
        return self.errors

    def _validate_header(self):
        voucher = self.voucher
        if not voucher.date:
            self.errors.append("The voucher needs a date.")
            return
        if voucher.voucher_type not in Voucher.PREFIX:
            self.errors.append("The voucher type is not one of the six.")
        if not voucher.number:
            self.errors.append("The voucher number is missing.")
        elif Voucher.objects.filter(number=voucher.number).exclude(pk=voucher.pk).exists():
            self.errors.append(f"Voucher number {voucher.number} is already used by another voucher.")
        period_errors, year = check_period(voucher.date, self.user, self.settings)
        self.errors.extend(period_errors)
        if year is not None and voucher.financial_year_id != year.pk:
            self.errors.append(
                f"The transaction date falls in financial year {year.code} but the voucher is "
                f"numbered in {voucher.financial_year.code if voucher.financial_year_id else '(none)'}. "
                f"Save the voucher again to renumber it.")

    def _validate_lines(self):
        voucher = self.voucher
        if len(self.lines) < 2:
            self.errors.append("A voucher needs at least one debit line and one credit line.")
        if self.lines and not any(l.side == 'debit' for l in self.lines):
            self.errors.append("At least one debit line is required.")
        if self.lines and not any(l.side == 'credit' for l in self.lines):
            self.errors.append("At least one credit line is required.")
        for n, line in enumerate(self.lines, start=1):
            label = f"Line {n}"
            amount = quantize(line.amount)
            if amount < 0:
                self.errors.append(f"{label}: an amount cannot be negative.")
            if amount == 0:
                self.errors.append(f"{label}: the amount has to be more than nothing.")
            account = line.account
            if account is None:
                self.errors.append(f"{label}: pick a ledger.")
                continue
            if not account.is_active:
                self.errors.append(f"{label}: {account} is closed.")
            if account.is_group:
                self.errors.append(f"{label}: {account} is a group account and is never posted to.")
                continue
            if account.is_control_account:
                self.errors.append(f"{label}: {account} is a control account. Post to the "
                                   f"customer's or supplier's own sub-ledger instead.")
                continue
            if not account_allowed(voucher.voucher_type, line.side, account):
                self.errors.append(f"{label}: {restriction_error(voucher.voucher_type, line.side, account)}")

    def _validate_balance(self):
        service = BalancingService(self.lines, currency=self.settings.currency_symbol)
        if not service.totals.is_balanced:
            self.errors.append(service.unbalanced_message())
        elif service.totals.total_debit == 0 and self.lines:
            self.errors.append("A voucher cannot be for nothing.")

    def _party_totals(self, side, party_field):
        """{party_id: amount} on `side`, by the party each line's ledger belongs to."""
        totals = defaultdict(lambda: ZERO)
        for line in self.lines:
            if line.side != side:
                continue
            party_id = getattr(line.account, f'{party_field}_id', None)
            if party_id:
                totals[party_id] += quantize(line.amount)
        return totals

    def _validate_party_rules(self):
        voucher = self.voucher
        if voucher.voucher_type == 'sales':
            self._validate_invoice_voucher(party=voucher.customer, party_label='Customer',
                                           side='debit', party_field='customer',
                                           invoice_kind=Invoice.SALES)
        elif voucher.voucher_type == 'purchase':
            self._validate_invoice_voucher(party=voucher.supplier, party_label='Supplier',
                                           side='credit', party_field='supplier',
                                           invoice_kind=Invoice.PURCHASE)
        if voucher.vat_account_id and voucher.is_invoice:
            expected = (LedgerAccount.VAT_OUTPUT if voucher.voucher_type == 'sales'
                        else LedgerAccount.VAT_INPUT)
            if not voucher.vat_account.is_vat_ledger(expected):
                label = dict(LedgerAccount.VAT_KINDS)[expected]
                self.errors.append(f"{voucher.vat_account} is not a valid {label} ledger.")

    def _validate_invoice_voucher(self, party, party_label, side, party_field, invoice_kind):
        """A Sales or Purchase voucher is an invoice, so it has to say which
        invoice, and its payment status has to agree with its own lines."""
        voucher = self.voucher
        if not voucher.payment_status:
            self.errors.append("The payment status (Cash / Bank / Credit / Partly paid) is missing.")
        if not voucher.invoice_number:
            label = ('sales invoice number' if voucher.voucher_type == 'sales'
                     else "supplier's invoice number")
            self.errors.append(f"Enter the {label}.")

        # Every party ledger on the party side must be the one party's. A
        # voucher is one invoice; two customers on it is two invoices.
        party_totals = self._party_totals(side, party_field)
        strangers = [pid for pid in party_totals if party is None or pid != party.pk]
        if strangers:
            self.errors.append(
                f"The voucher has {party_label.lower()} ledger lines that do not belong to the "
                f"{party_label.lower()} the invoice is on.")

        party_amount = party_totals.get(party.pk, ZERO) if party else ZERO
        money_amount = sum((quantize(l.amount) for l in self.lines
                            if l.side == side and l.account and l.account.is_money), ZERO)
        on_credit = voucher.payment_status in ('credit', 'partly_paid')
        status_label = (voucher.get_payment_status_display() or '').lower()

        if on_credit and party is not None and party_amount <= 0:
            self.errors.append(f"A credit transaction has to {side} the "
                               f"{party_label.lower()}'s ledger.")
        if voucher.payment_status in ('cash', 'bank') and money_amount <= 0:
            self.errors.append(f"A {status_label} transaction has to {side} a cash book or "
                               f"a bank account.")
        if voucher.payment_status == 'partly_paid' and money_amount <= 0:
            self.errors.append("A partly paid transaction needs a cash book or bank account line "
                               "for the part that was paid.")
        if voucher.payment_status in ('cash', 'bank') and party_amount > 0:
            self.errors.append(
                f"The payment status says {status_label} but the {party_label.lower()}'s ledger is "
                f"{side}ed. Use Credit or Partly paid.")

        # The header totals are derived, so a mismatch means something has
        # been tampered with rather than mistyped — worth saying so plainly.
        total_side = quantize(sum((quantize(l.amount) for l in self.lines if l.side == side), ZERO))
        if voucher.total and quantize(voucher.total) != total_side:
            self.errors.append(f"The VAT-inclusive total ({voucher.total:,.2f}) does not match the "
                               f"voucher's own lines ({total_side:,.2f}).")
        if (voucher.net_amount or voucher.vat_amount) and quantize(
                voucher.net_amount + voucher.vat_amount) != (quantize(voucher.total) or total_side):
            self.errors.append("The VAT-exclusive amount plus VAT has to equal the "
                               "VAT-inclusive total.")

        # The invoice number is unique twice over: among this type's posted
        # vouchers, and — in the invoice register — per party.
        if voucher.invoice_number:
            clash = (Voucher.objects
                     .filter(voucher_type=voucher.voucher_type,
                             status__in=Voucher.EFFECTIVE_STATUSES,
                             invoice_number__iexact=voucher.invoice_number)
                     .exclude(pk=voucher.pk))
            if clash.exists():
                self.errors.append(
                    f"Invoice {voucher.invoice_number} is already in the books on "
                    f"{clash.first().number}.")
            if party is not None:
                duplicate = (Invoice.objects
                             .filter(kind=invoice_kind, invoice_number__iexact=voucher.invoice_number,
                                     **{party_field: party})
                             .exclude(voucher=voucher).exclude(status='cancelled'))
                if duplicate.exists():
                    self.errors.append(f"Invoice {voucher.invoice_number} already exists for "
                                       f"{party_label.lower()} {party}.")

        # EFD duplicates, under whichever policy is configured.
        if voucher.efd_number and self.settings.efd_duplicate_policy == AccountingSettings.EFD_BLOCK:
            clash = (Voucher.objects
                     .filter(voucher_type=voucher.voucher_type, efd_number__iexact=voucher.efd_number)
                     .exclude(pk=voucher.pk).exclude(status='cancelled'))
            if voucher.voucher_type == 'purchase' and party is not None:
                clash = clash.filter(supplier=party)
            if clash.exists():
                self.errors.append(f"EFD receipt {voucher.efd_number} is already in the books on "
                                   f"{clash.first().number}.")

    def _validate_allocations(self):
        """An allocation says "this money clears that invoice". It may never
        clear more than is owed, nor more than the party was credited."""
        voucher = self.voucher
        if not self.allocations:
            return
        if voucher.voucher_type == 'receipt':
            side, party_field, label = 'credit', 'customer', 'customer'
        elif voucher.voucher_type == 'payment':
            side, party_field, label = 'debit', 'supplier', 'supplier'
        elif voucher.voucher_type in Voucher.INVOICE_TYPES:
            # The cash part of a sale or purchase is allocated by the posting
            # service itself, not keyed, so there is nothing to check here.
            return
        else:
            self.errors.append("Invoice allocations only make sense on a receipt or a "
                               "payment voucher.")
            return

        # Currency: an invoice belongs to the currency of the voucher that
        # raised it, and money in one currency cannot clear a bill in another.
        mismatched = sorted({
            alloc.invoice.voucher.currency.code for alloc in self.allocations
            if alloc.invoice_id and alloc.invoice.voucher_id
            and alloc.invoice.voucher.currency_id
            and alloc.invoice.voucher.currency_id != voucher.currency_id
        })
        if mismatched:
            self.errors.append(
                f"This voucher is in {voucher.currency_code}; invoices in "
                f"{', '.join(mismatched)} cannot be allocated against it.")

        party_totals = self._party_totals(side, party_field)
        per_party = defaultdict(lambda: ZERO)
        seen = set()
        for alloc in self.allocations:
            amount = quantize(alloc.amount)
            if alloc.line.side != side:
                self.errors.append(
                    f"{alloc.reference}: only a {label} on the {side} side can be allocated "
                    f"to invoices.")
                continue
            if amount <= 0:
                self.errors.append(f"An allocation to {alloc.reference} has to be more "
                                   f"than nothing.")
            party_id = getattr(alloc.line.account, f'{party_field}_id', None)
            if party_id is None:
                self.errors.append(f"{alloc.line.account} is not a {label}'s ledger, so nothing "
                                   f"can be allocated against it.")
                continue
            if party_id not in party_totals:
                self.errors.append(f"{alloc.reference} belongs to another {label} than the one "
                                   f"{side}ed on this voucher.")
                continue
            if alloc.invoice_id:
                invoice = alloc.invoice
                if invoice.pk in seen:
                    self.errors.append(f"Invoice {invoice.invoice_number} is allocated more "
                                       f"than once.")
                seen.add(invoice.pk)
                if invoice.status == 'cancelled':
                    self.errors.append(f"Invoice {invoice.invoice_number} is cancelled and cannot "
                                       f"be allocated against.")
                outstanding = invoice.outstanding_amount
                if amount > outstanding:
                    self.errors.append(
                        f"Allocating {amount:,.2f} to invoice {invoice.invoice_number} is more "
                        f"than the {outstanding:,.2f} outstanding on it.")
            per_party[party_id] += amount

        for party_id, allocated in per_party.items():
            available = party_totals.get(party_id, ZERO)
            if allocated > available:
                self.errors.append(
                    f"Allocated {allocated:,.2f} in all, but only {available:,.2f} was {side}ed "
                    f"to that {label}'s ledger.")

    # ---------------------------------------------------------------- posting
    def post(self):
        """Validate and post, atomically. Raises `VoucherValidationError`."""
        errors = self.validate()
        if errors:
            raise VoucherValidationError(errors)
        with transaction.atomic():
            # Re-read the status under a row lock: two people pressing Post at
            # once must not both get through.
            locked = Voucher.objects.select_for_update().get(pk=self.voucher.pk)
            if locked.status != 'draft':
                raise VoucherValidationError(
                    [f"Voucher {locked.number} has already been "
                     f"{locked.get_status_display().lower()}."])
            if locked.gl_entries.exists():
                raise VoucherValidationError(
                    [f"Voucher {locked.number} already has General Ledger entries."])

            voucher = self.voucher
            now = timezone.now()
            voucher.recalculate_totals(save=False)
            self._write_gl_entries()
            self._raise_invoice()
            voucher.status = 'posted'
            voucher.posted_by = self.user if getattr(self.user, 'is_authenticated', False) else None
            voucher.posted_at = now
            voucher.save()
            self._settle_allocations()
            log_action(
                AccountingAuditLog.POSTED, user=self.user, request=self.request, voucher=voucher,
                previous_status='draft', new_status='posted',
                description=(f"Posted {voucher.get_voucher_type_display()} {voucher.number} dated "
                             f"{voucher.date:%d/%m/%Y}; Dr {voucher.total_debit:,.2f} / "
                             f"Cr {voucher.total_credit:,.2f}; {len(self.lines)} ledger entries."),
            )
        return self.voucher

    def _write_gl_entries(self):
        voucher = self.voucher
        # The invoice number, denormalised onto every row, is what
        # `/api/general-ledger/?reference=` matches on — so it is kept exactly,
        # not blended with the other references. A voucher with no invoice
        # number falls back to its own reference (a cheque number, say), which
        # is better than leaving the column empty.
        reference = voucher.invoice_number or voucher.reference or ''
        rows = []
        for line in self.lines:
            account = line.account
            base = quantize(line.base_amount or line.amount)
            keyed = quantize(line.amount)
            rows.append(GeneralLedgerEntry(
                voucher=voucher, line=line, account=account,
                financial_year=voucher.financial_year, date=voucher.date,
                voucher_type=voucher.voucher_type, voucher_number=voucher.number,
                description=(line.narration or voucher.description or '')[:300],
                # The ledger is kept in the base currency; what was keyed sits beside it.
                debit=(base if line.side == 'debit' else ZERO),
                credit=(base if line.side == 'credit' else ZERO),
                currency=voucher.currency, exchange_rate=(voucher.exchange_rate or ONE),
                foreign_debit=(keyed if line.side == 'debit' else ZERO),
                foreign_credit=(keyed if line.side == 'credit' else ZERO),
                customer_id=account.customer_id, supplier_id=account.supplier_id,
                reference=reference[:150],
                created_by=(self.user if getattr(self.user, 'is_authenticated', False) else None),
            ))
        GeneralLedgerEntry.objects.bulk_create(rows)

    def _raise_invoice(self):
        """Put the customer invoice or supplier bill into the register.

        The part *not* left on the party's ledger was settled there and then,
        so it is written as an allocation from this same voucher — which is
        why a cash sale lands `paid` and a credit sale `open`.
        """
        voucher = self.voucher
        if voucher.sales_entry_id:
            # A till sale posted from Sales Accounting: the sale itself is
            # what gets allocated against, so a register row would be a
            # second copy of the same debt.
            return None
        if voucher.voucher_type == 'sales' and voucher.customer_id:
            kind, party_field, side = Invoice.SALES, 'customer', 'debit'
        elif voucher.voucher_type == 'purchase' and voucher.supplier_id:
            kind, party_field, side = Invoice.PURCHASE, 'supplier', 'credit'
        else:
            return None
        party = getattr(voucher, party_field)
        total = voucher.total_debit if side == 'debit' else voucher.total_credit
        invoice = Invoice.objects.create(
            kind=kind, voucher=voucher, invoice_number=voucher.invoice_number,
            efd_number=voucher.efd_number, invoice_date=voucher.date,
            original_amount=total, description=(voucher.description or '')[:255],
            **{party_field: party},
        )
        party_amount = self._party_totals(side, party_field).get(party.pk, ZERO)
        paid_now = quantize(total - party_amount)
        if paid_now > 0:
            # Hang it off a money line of this voucher — that is the money
            # that did the settling.
            money_line = next((l for l in self.lines
                               if l.side == side and l.account and l.account.is_money), None)
            if money_line is not None:
                VoucherAllocation.objects.create(
                    line=money_line, invoice=invoice, reference=invoice.invoice_number,
                    amount=paid_now, notes="Settled at the time of the transaction",
                    allocated_by=voucher.posted_by or voucher.created_by,
                )
        return invoice

    def _settle_allocations(self):
        """Allocations were saved with the draft; now the voucher is posted,
        they count. Refresh the invoices they touch."""
        voucher = self.voucher
        own = getattr(voucher, 'invoice', None)
        if own is not None:
            own.refresh_status()
        for alloc in allocations_of(voucher).select_related('invoice'):
            if alloc.invoice_id:
                alloc.invoice.refresh_status()
            log_action(AccountingAuditLog.ALLOCATED, user=self.user, request=self.request,
                       voucher=voucher,
                       description=f"Allocated {alloc.amount:,.2f} to {alloc.reference}")


class VoucherCancellationService:
    """Cancel a voucher.

    A draft is simply marked cancelled — it never reached the ledger, so
    there is nothing to undo. A posted voucher's entries are taken back out
    (this is the behaviour the books had before reversal existed, and it is
    kept so nothing that relied on it breaks); the preferred correction for a
    posted voucher is a reversal, which leaves both documents standing.
    """

    def __init__(self, voucher, user, request=None):
        self.voucher, self.user, self.request = voucher, user, request

    @transaction.atomic
    def cancel(self, reason='', drafts_only=True):
        voucher = Voucher.objects.select_for_update().get(pk=self.voucher.pk)
        if voucher.status == 'cancelled':
            raise VoucherValidationError(["That voucher is already cancelled."])
        if drafts_only and voucher.status != 'draft':
            raise VoucherValidationError(
                ["Only a draft voucher can be cancelled. A posted voucher must be reversed."])
        previous = voucher.status
        if voucher.gl_entries.exists():
            voucher.gl_entries.all().delete()
        invoice = getattr(voucher, 'invoice', None)
        if invoice is not None:
            invoice.status = 'cancelled'
            invoice.save(update_fields=['status'])
        touched = [a.invoice for a in allocations_of(voucher).select_related('invoice')
                   if a.invoice_id]
        voucher.status = 'cancelled'
        voucher.cancelled_by = self.user if getattr(self.user, 'is_authenticated', False) else None
        voucher.cancelled_at = timezone.now()
        voucher.cancel_reason = (reason or '').strip()
        voucher.save(update_fields=['status', 'cancelled_by', 'cancelled_at', 'cancel_reason'])
        for inv in touched:
            inv.refresh_status()
        log_action(AccountingAuditLog.CANCELLED, user=self.user, request=self.request,
                   voucher=voucher, previous_status=previous, new_status='cancelled',
                   description=reason)
        self.voucher.refresh_from_db()
        return self.voucher


class VoucherReversalService:
    """Reverse a posted voucher by posting an equal and opposite Journal.

    Nothing is erased: the original keeps its entries and is marked
    `reversed`, and the journal that undid it carries `reversal_of` back to
    it. An invoice the original raised is cancelled, and anything allocated
    *by* it is released.
    """

    def __init__(self, voucher, user, request=None):
        self.voucher, self.user, self.request = voucher, user, request

    def validate(self):
        voucher = self.voucher
        errors = []
        if voucher.status != 'posted':
            errors.append("Only a posted voucher can be reversed.")
        if not may_reverse(self.user):
            errors.append("You do not have permission to reverse vouchers.")
        invoice = getattr(voucher, 'invoice', None)
        if invoice is not None:
            others = (invoice.allocations
                      .exclude(line__voucher=voucher)
                      .filter(line__voucher__status__in=Voucher.EFFECTIVE_STATUSES)
                      .select_related('line__voucher'))
            if others.exists():
                numbers = ', '.join(sorted({a.line.voucher.number for a in others}))
                errors.append(f"Invoice {invoice.invoice_number} has receipts or payments "
                              f"allocated to it ({numbers}). Reverse those first.")
        return errors

    @transaction.atomic
    def reverse(self, reversal_date=None, reason=''):
        from .drafts import as_date
        from .numbering import VoucherNumberService

        errors = self.validate()
        if errors:
            raise VoucherValidationError(errors)
        original = Voucher.objects.select_for_update().get(pk=self.voucher.pk)
        if original.status != 'posted':
            raise VoucherValidationError(["That voucher is no longer posted."])

        reversal_date = as_date(reversal_date) or timezone.localdate()
        period_errors, year = check_period(reversal_date, self.user)
        if period_errors:
            raise VoucherValidationError(period_errors)

        reversal = Voucher(
            voucher_type='journal', financial_year=year, date=reversal_date,
            customer=original.customer, supplier=original.supplier,
            reference=original.number, currency=original.currency,
            exchange_rate=original.exchange_rate, status='draft',
            description=(f"Reversal of {original.number}" + (f": {reason}" if reason else "")),
            reversal_of=original, created_by=self.user,
        )
        VoucherNumberService().assign(reversal)
        reversal.save()
        for position, line in enumerate(original.lines.select_related('account')):
            VoucherLine.objects.create(
                voucher=reversal, account=line.account,
                side=('credit' if line.side == 'debit' else 'debit'),
                amount=line.amount, base_amount=line.base_amount or line.amount,
                narration=f"Reversal of {original.number}: {line.narration}"[:200],
                position=position,
            )
        reversal.recalculate_totals()
        log_action(AccountingAuditLog.CREATED, user=self.user, request=self.request,
                   voucher=reversal, new_status='draft',
                   description=f"Reversing journal for {original.number}")
        VoucherPostingService(reversal, self.user, self.request).post()

        # Undo what the original did outside the ledger.
        invoice = getattr(original, 'invoice', None)
        if invoice is not None:
            invoice.status = 'cancelled'
            invoice.save(update_fields=['status'])
        released = [a.invoice for a in allocations_of(original).select_related('invoice')
                    if a.invoice_id]
        original.status = 'reversed'
        original.cancelled_by = self.user if getattr(self.user, 'is_authenticated', False) else None
        original.cancelled_at = timezone.now()
        original.cancel_reason = reason
        original.save(update_fields=['status', 'cancelled_by', 'cancelled_at', 'cancel_reason'])
        for inv in released:
            inv.refresh_status()
            log_action(AccountingAuditLog.UNALLOCATED, user=self.user, request=self.request,
                       voucher=original,
                       description=f"Allocation to invoice {inv.invoice_number} released "
                                   f"by the reversal")
        log_action(AccountingAuditLog.REVERSED, user=self.user, request=self.request,
                   voucher=original, previous_status='posted', new_status='reversed',
                   description=f"Reversed by {reversal.number}. {reason}".strip())
        self.voucher.refresh_from_db()
        return reversal
