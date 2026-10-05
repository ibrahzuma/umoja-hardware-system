"""Money handling for the books: one way of turning any input into a figure.

Everything in the ledger is a two-decimal `Decimal`. `quantize` never raises
— a blank, a stray comma or an unparseable string all come back as zero — so
a form, a spreadsheet cell and an API payload can all be read the same way
and the validation that follows is what rejects bad input, with a message,
rather than a traceback.
"""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

TWO_PLACES = Decimal('0.01')
ZERO = Decimal('0.00')


def quantize(value):
    """Any numeric input as a 2-decimal Decimal; zero for anything unreadable."""
    if value in (None, ''):
        return ZERO
    try:
        return Decimal(str(value).replace(',', '').strip()).quantize(
            TWO_PLACES, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        return ZERO


def format_money(value, symbol=''):
    text = f"{quantize(value):,.2f}"
    return f"{symbol} {text}".strip() if symbol else text
