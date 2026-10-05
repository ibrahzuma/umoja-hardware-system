"""Saving a voucher as a draft.

A draft may be unbalanced, wrong and half-finished — that is what makes it a
draft. It is still numbered (so it can be referred to and audited), still
checked for the things that are structurally impossible rather than merely
incomplete (a line with no ledger, a negative amount), and still carries its
allocations so they are there when somebody comes back to post it.

Three things are *derived* here rather than typed, which is what keeps the
header and the lines from ever disagreeing:

  * the currency and the exchange rate, and from them every line's
    base-currency amount — the ledger is always kept in the base currency;
  * on a Sales or Purchase voucher, the party, the payment status, the
    VAT-exclusive / VAT / total amounts and the VAT ledger, all read off the
    accounting lines;
  * on a Receipt or Payment, the customer or supplier, read off the single
    party ledger on the money-in / money-out side.
"""

from datetime import date, datetime
from decimal import Decimal

from django.db import transaction
from django.utils.dateparse import parse_date

from .audit import log_action
from .models import (
    AccountingAuditLog, Currency, FinancialYear, Invoice, LedgerAccount, Voucher,
    VoucherAllocation, VoucherLine,
)
from .money import ZERO, quantize
from .numbering import VoucherNumberService
from .posting import VoucherValidationError

ONE = Decimal('1')


def as_date(value):
    """A `date`, whatever shape the caller had it in.

    The REST API hands dates over as `YYYY-MM-DD` strings, a form hands over
    a `date`, and a spreadsheet import can hand over a `datetime`. Everything
    downstream formats and compares dates, so they are normalised once, here,
    rather than being guessed at in each place.
    """
    if value in (None, ''):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    parsed = parse_date(str(value).strip()[:10])
    if parsed is None:
        raise VoucherValidationError([f"'{value}' is not a date (use YYYY-MM-DD)."])
    return parsed


def _side(data):
    """A line's side, whether it arrived as `side` or as debit/credit columns."""
    if data.get('side') in ('debit', 'credit'):
        return data['side']
    return 'debit' if quantize(data.get('debit')) > 0 else 'credit'


def _amount(data):
    if data.get('amount') not in (None, ''):
        return quantize(data['amount'])
    return quantize(data.get('debit')) or quantize(data.get('credit'))


def _debit(data):
    return _amount(data) if _side(data) == 'debit' else ZERO


def _credit(data):
    return _amount(data) if _side(data) == 'credit' else ZERO


class VoucherDraftService:
    def __init__(self, user, request=None):
        self.user = user
        self.request = request

    @transaction.atomic
    def save(self, voucher, lines, allocations=None):
        """Persist `voucher` (new, or an existing draft) with `lines`.

        `lines` — dicts of account, side + amount (or debit/credit), narration,
        and optionally their own `allocations`.
        `allocations` — dicts of invoice/sale/purchase_order/voucher + amount,
        for callers that keep them apart from the lines.
        """
        is_new = voucher.pk is None
        if not is_new and voucher.status != 'draft':
            raise VoucherValidationError(["Only a draft voucher can be edited."])
        voucher.date = as_date(voucher.date)
        if not voucher.date:
            raise VoucherValidationError(["Give the voucher a date."])

        year = FinancialYear.for_date(voucher.date)
        if year is None:
            raise VoucherValidationError(
                [f"No financial year covers {voucher.date:%d/%m/%Y}. Create the financial "
                 f"year first."])

        self._apply_currency(voucher, lines)

        # A draft moved into another financial year is renumbered, so its
        # number never claims a year it does not belong to.
        renumbered = False
        if is_new or voucher.financial_year_id != year.pk or not voucher.number:
            previous = voucher.number
            voucher.financial_year = year
            VoucherNumberService().assign(voucher)
            renumbered = not is_new and previous != voucher.number

        if voucher.voucher_type == 'sales':
            self._derive_sales_header(voucher, lines)
        elif voucher.voucher_type == 'purchase':
            self._derive_purchase_header(voucher, lines)
        elif voucher.voucher_type == 'receipt':
            voucher.customer = self._sole_party(lines, 'credit', 'customer')
        elif voucher.voucher_type == 'payment':
            voucher.supplier = self._sole_party(lines, 'debit', 'supplier')

        if is_new:
            voucher.created_by = self.user if getattr(self.user, 'is_authenticated', False) else None
            voucher.status = 'draft'
        voucher.updated_by = self.user if getattr(self.user, 'is_authenticated', False) else None
        voucher.save()

        self._check_structure(lines)

        voucher.lines.all().delete()   # takes their allocations with them
        for position, data in enumerate(lines):
            line = VoucherLine.objects.create(
                voucher=voucher, account=data['account'], side=_side(data),
                amount=_amount(data), base_amount=quantize(data.get('base_amount')) or _amount(data),
                narration=(data.get('narration') or data.get('description') or '')[:200],
                position=position,
            )
            for raw in (data.get('allocations') or []):
                self._save_allocation(line, raw)
        voucher.recalculate_totals()

        # Allocations given apart from the lines attach to the party line they
        # belong to — a caller that sends them this way is not saying which
        # line, only which invoice.
        for raw in (allocations or []):
            line = self._line_for_allocation(voucher, raw)
            if line is not None:
                self._save_allocation(line, raw)

        log_action(
            AccountingAuditLog.CREATED if is_new else AccountingAuditLog.EDITED,
            user=self.user, request=self.request, voucher=voucher,
            previous_status='' if is_new else 'draft', new_status='draft',
            description=(f"{'Created' if is_new else 'Edited'} draft "
                         f"{voucher.get_voucher_type_display()} {voucher.number}; "
                         f"{len(lines)} line(s); Dr {voucher.total_debit:,.2f} / "
                         f"Cr {voucher.total_credit:,.2f}"
                         + ("; renumbered" if renumbered else "")),
        )
        return voucher

    # ----------------------------------------------------------- the pieces
    @staticmethod
    def _check_structure(lines):
        """The things that are impossible rather than merely unfinished."""
        errors = []
        for n, data in enumerate(lines, start=1):
            if data.get('account') is None:
                errors.append(f"Line {n}: pick a ledger.")
            debit, credit = quantize(data.get('debit')), quantize(data.get('credit'))
            if debit < 0 or credit < 0 or _amount(data) < 0:
                errors.append(f"Line {n}: an amount cannot be negative.")
            if debit > 0 and credit > 0:
                errors.append(f"Line {n}: a line cannot carry both a debit and a credit amount.")
            if _amount(data) == 0:
                errors.append(f"Line {n}: the amount has to be more than nothing.")
        if errors:
            raise VoucherValidationError(errors)

    def _save_allocation(self, line, raw):
        """One allocation, pointed at whichever of the four kinds of invoice
        it names.

        An allocation against a Sales or Purchase **voucher** also links the
        register `Invoice` that voucher raised. One row, both links: the
        `voucher` link is what the allocation screen and the outstanding
        figures go by, and the `invoice` link is what lets the register's own
        status and ageing stay right without a second allocation row to keep
        in step with the first.
        """
        amount = quantize(raw.get('amount'))
        if amount <= 0:
            return None
        reference = (raw.get('reference') or '')
        fields = {}
        for name in ('invoice', 'sale', 'purchase_order', 'voucher'):
            value = raw.get(name)
            if value in (None, ''):
                continue
            target_id = int(getattr(value, 'pk', value))
            fields[f'{name}_id'] = target_id
            if not reference:
                reference = self._reference_for(name, target_id)
            if name == 'voucher':
                raised = Invoice.objects.filter(voucher_id=target_id).first()
                if raised is not None:
                    fields['invoice_id'] = raised.pk
            break
        if not fields:
            return None
        return VoucherAllocation.objects.create(
            line=line, amount=amount, reference=reference[:60],
            allocated_by=(self.user if getattr(self.user, 'is_authenticated', False) else None),
            notes=(raw.get('notes') or '')[:255], **fields,
        )

    @staticmethod
    def _reference_for(name, target_id):
        if name == 'invoice':
            row = Invoice.objects.filter(pk=target_id).first()
            return row.invoice_number if row else ''
        if name == 'voucher':
            row = Voucher.objects.filter(pk=target_id).first()
            return (row.invoice_number or row.number) if row else ''
        if name == 'purchase_order':
            return f'PO-{target_id}'
        from apps.sales.models import Sale
        row = Sale.objects.filter(pk=target_id).first()
        return row.invoice_number if row else ''

    @staticmethod
    def _line_for_allocation(voucher, raw):
        """Which line a loose allocation belongs to: the party's own line."""
        invoice = raw.get('invoice')
        if invoice not in (None, ''):
            row = Invoice.objects.filter(pk=int(getattr(invoice, 'pk', invoice))).first()
            if row is not None:
                party = row.party
                ledger = getattr(party, 'ledger', None) if party else None
                if ledger is not None:
                    return voucher.lines.filter(account=ledger).first()
        return voucher.lines.filter(
            account__kind__in=('customer', 'supplier')).first()

    @staticmethod
    def _sole_party(lines, side, party_field):
        """The one customer (or supplier) on `side`, or None when there are
        none or several — a header names a party only when it is unambiguous."""
        parties = {getattr(data['account'], party_field) for data in lines
                   if data.get('account') is not None and _side(data) == side
                   and getattr(data['account'], f'{party_field}_id', None)}
        return parties.pop() if len(parties) == 1 else None

    @staticmethod
    def _apply_currency(voucher, lines):
        """Settle the currency and rate, then convert every line to the base
        currency. `amount` is what was keyed; `base_amount` is what the
        ledger takes."""
        base = Currency.base()
        if voucher.currency_id is None:
            voucher.currency = base
        currency = voucher.currency
        if currency is not None and not currency.is_active and currency != base:
            raise VoucherValidationError([f"Currency {currency.code} is not in use."])

        if currency is None or currency.is_base:
            voucher.exchange_rate = ONE
        else:
            rate = Decimal(str(voucher.exchange_rate)) if voucher.exchange_rate else None
            # A rate of exactly 1 on a foreign-currency voucher is the model
            # default rather than a real rate, so look the real one up.
            if rate is None or rate <= 0 or rate == ONE:
                rate = currency.rate_on(voucher.date)
                if rate is None:
                    raise VoucherValidationError(
                        [f"No exchange rate for {currency.code} on or before "
                         f"{voucher.date:%d/%m/%Y}. Add one under Currencies."])
            voucher.exchange_rate = rate

        rate = voucher.exchange_rate or ONE
        voucher_currency_id = voucher.currency_id or (base.pk if base else None)
        wrong = sorted({str(data['account']) for data in lines
                        if data.get('account') is not None and data['account'].currency_id
                        and data['account'].currency_id != voucher_currency_id})
        if wrong:
            code = voucher.currency.code if voucher.currency_id else ''
            raise VoucherValidationError(
                [f"{name} is held in another currency and cannot be used on a {code} voucher."
                 for name in wrong])
        for data in lines:
            data['base_amount'] = quantize(_amount(data) * rate)

    @staticmethod
    def _derive_sales_header(voucher, lines):
        """A sales voucher's header is four fields: date, invoice number, EFD
        number, description. The customer, the payment status, the amounts and
        the Output VAT ledger are all read off the lines, so there is no
        second place for them to be wrong."""
        debits = [d for d in lines if d.get('account') is not None and _side(d) == 'debit']
        credits = [d for d in lines if d.get('account') is not None and _side(d) == 'credit']

        customers = {d['account'].customer for d in debits if d['account'].customer_id}
        if len(customers) > 1:
            raise VoucherValidationError(
                ["A sales voucher is one invoice, so it can only debit one customer's ledger."])
        voucher.customer = customers.pop() if customers else None

        settled = [d['account'] for d in debits if d['account'].is_money]
        if voucher.customer and settled:
            voucher.payment_status = 'partly_paid'
        elif voucher.customer:
            voucher.payment_status = 'credit'
        elif any(a.is_bank_account for a in settled):
            voucher.payment_status = 'bank'
        elif settled:
            voucher.payment_status = 'cash'
        else:
            # Owed, but on a plain receivable ledger rather than a customer's own.
            voucher.payment_status = 'credit'

        vat_lines = [d for d in credits
                     if d['account'].is_vat_ledger(LedgerAccount.VAT_OUTPUT)]
        voucher.vat_account = vat_lines[0]['account'] if vat_lines else None
        voucher.vat_amount = quantize(sum((_amount(d) for d in vat_lines), ZERO))
        voucher.total = quantize(sum((_amount(d) for d in credits), ZERO))
        voucher.net_amount = quantize(voucher.total - voucher.vat_amount)

    @staticmethod
    def _derive_purchase_header(voucher, lines):
        """The mirror of the sales header: the supplier is the one credited,
        and the VAT is whatever was debited to the Input VAT ledger."""
        debits = [d for d in lines if d.get('account') is not None and _side(d) == 'debit']
        credits = [d for d in lines if d.get('account') is not None and _side(d) == 'credit']

        suppliers = {d['account'].supplier for d in credits if d['account'].supplier_id}
        if len(suppliers) > 1:
            raise VoucherValidationError(
                ["A purchase voucher is one bill, so it can only credit one supplier's ledger."])
        voucher.supplier = suppliers.pop() if suppliers else None

        settled = [d['account'] for d in credits if d['account'].is_money]
        if voucher.supplier and settled:
            voucher.payment_status = 'partly_paid'
        elif voucher.supplier:
            voucher.payment_status = 'credit'
        elif any(a.is_bank_account for a in settled):
            voucher.payment_status = 'bank'
        elif settled:
            voucher.payment_status = 'cash'
        else:
            voucher.payment_status = 'credit'

        vat_lines = [d for d in debits
                     if d['account'].is_vat_ledger(LedgerAccount.VAT_INPUT)]
        voucher.vat_account = vat_lines[0]['account'] if vat_lines else None
        voucher.vat_amount = quantize(sum((_amount(d) for d in vat_lines), ZERO))
        voucher.total = quantize(sum((_amount(d) for d in debits), ZERO))
        voucher.net_amount = quantize(voucher.total - voucher.vat_amount)

    @transaction.atomic
    def delete(self, voucher):
        if voucher.status != 'draft':
            raise VoucherValidationError(["Only a draft voucher can be deleted."])
        number = voucher.number
        log_action(AccountingAuditLog.DELETED, user=self.user, request=self.request, obj=voucher,
                   previous_status=voucher.status,
                   description=f"Deleted draft voucher {number}")
        voucher.delete()
        return number
