"""The debit/credit balancing engine.

The golden rule of the books: a voucher is only ever posted when
**Total Debit = Total Credit**. This module is the single statement of that
rule. The same arithmetic runs in the browser for instant feedback (the
voucher form's JS) and here on the server, where it cannot be bypassed.

The entry rule the spec asks for is in `suggest_balancing_line`: whenever the
two sides differ, the next line on the *short* side is offered, pre-filled
with the difference — the user only has to pick the ledger.
"""

from dataclasses import dataclass
from decimal import Decimal

from .money import ZERO, quantize


@dataclass
class Totals:
    total_debit: Decimal
    total_credit: Decimal

    @property
    def difference(self):
        return quantize(self.total_debit - self.total_credit)

    @property
    def is_balanced(self):
        return self.difference == 0

    def as_dict(self):
        return {
            'total_debit': str(self.total_debit),
            'total_credit': str(self.total_credit),
            'difference': str(self.difference),
            'is_balanced': self.is_balanced,
        }


def compute_totals(lines):
    """`lines` is anything exposing `debit` and `credit` — dicts, forms,
    `VoucherLine` rows (whose `debit`/`credit` are derived from `side`)."""
    total_debit = total_credit = ZERO
    for line in lines:
        if isinstance(line, dict):
            debit, credit = line.get('debit'), line.get('credit')
            if debit is None and credit is None and 'side' in line:
                amount = line.get('amount')
                debit, credit = ((amount, 0) if line.get('side') == 'debit' else (0, amount))
        else:
            debit, credit = getattr(line, 'debit', 0), getattr(line, 'credit', 0)
        total_debit += quantize(debit)
        total_credit += quantize(credit)
    return Totals(quantize(total_debit), quantize(total_credit))


def suggest_balancing_line(lines):
    """The automatic next line needed to balance, or None when balanced.

    Debit > Credit  -> a credit line for the difference
    Credit > Debit  -> a debit line for the difference
    """
    difference = compute_totals(lines).difference
    if difference == 0:
        return None
    if difference > 0:
        return {'side': 'credit', 'debit': ZERO, 'credit': difference, 'amount': difference}
    return {'side': 'debit', 'debit': -difference, 'credit': ZERO, 'amount': -difference}


class BalancingService:
    """What the API endpoint and the posting validator both ask."""

    def __init__(self, lines, currency='TZS'):
        self.lines = list(lines)
        self.currency = currency
        self.totals = compute_totals(self.lines)

    @property
    def suggestion(self):
        return suggest_balancing_line(self.lines)

    def unbalanced_message(self):
        difference = self.totals.difference
        if difference == 0:
            return ''
        return (
            "Voucher cannot be posted. Debit and Credit are not balanced. "
            f"Remaining difference: {self.currency} {abs(difference):,.2f} "
            f"({'Debit exceeds Credit' if difference > 0 else 'Credit exceeds Debit'})."
        )

    def as_dict(self):
        data = self.totals.as_dict()
        suggestion = self.suggestion
        data['suggested_line'] = (
            {'side': suggestion['side'], 'amount': str(suggestion['amount'])} if suggestion else None)
        data['message'] = self.unbalanced_message()
        return data
