"""Parsing of bulk customer uploads for the CRM register.

Kept separate from views.py so the column matching and value coercion can be
tested directly. Accepts .xlsx/.xlsm (openpyxl) and .csv, because staff
routinely "save as CSV" out of Excel.
"""

import csv
import io
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from openpyxl import load_workbook

# How many data rows we will read from one upload. Guards memory on a
# mis-selected file; the UI states the limit.
MAX_ROWS = 5000

# Normalised header -> model field. Users write these headers a dozen ways,
# so match on a squashed form (lowercase, letters and digits only).
COLUMN_ALIASES = {
    'date': 'date',
    'tarehe': 'date',
    'transactiondate': 'date',
    'saledate': 'date',

    'receiptno': 'receipt_number',
    'receiptnumber': 'receipt_number',
    'receipt': 'receipt_number',
    'risiti': 'receipt_number',

    'efdreceiptno': 'efd_receipt_number',
    'efdreceiptnumber': 'efd_receipt_number',
    'efdreceipt': 'efd_receipt_number',
    'efdno': 'efd_receipt_number',
    'efd': 'efd_receipt_number',

    'customername': 'customer_name',
    'customer': 'customer_name',
    'name': 'customer_name',
    'jinalamteja': 'customer_name',

    'tin': 'tin',
    'tinno': 'tin',
    'tinnumber': 'tin',

    'salesamount': 'sales_amount',
    'amount': 'sales_amount',
    'sales': 'sales_amount',
    'total': 'sales_amount',
    'totalamount': 'sales_amount',

    'amountpaid': 'amount_paid',
    'paid': 'amount_paid',
    'paidamount': 'amount_paid',
    'payment': 'amount_paid',
    'amountreceived': 'amount_paid',
}

REQUIRED_FIELDS = ('date', 'customer_name')

TEMPLATE_HEADERS = ['Date', 'Receipt No', 'EFD Receipt No', 'Customer Name', 'TIN',
                    'Sales Amount', 'Amount Paid']

DATE_FORMATS = ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%d.%m.%Y', '%Y/%m/%d', '%m/%d/%Y')

# Excel stores dates as days since this epoch (the 1900 system, with its
# well-known leap-year quirk baked in).
EXCEL_EPOCH = datetime(1899, 12, 30)


class ImportError_(Exception):
    """Raised for problems with the file as a whole (not a single row)."""


def _normalise(header):
    return re.sub(r'[^a-z0-9]', '', str(header or '').lower())


def _map_headers(raw_headers):
    """Map a header row onto model fields. Unknown columns are ignored."""
    mapping = {}
    for index, header in enumerate(raw_headers):
        field = COLUMN_ALIASES.get(_normalise(header))
        if field and field not in mapping.values():
            mapping[index] = field
    missing = [f for f in REQUIRED_FIELDS if f not in mapping.values()]
    if missing:
        labels = {'date': 'Date', 'customer_name': 'Customer Name'}
        raise ImportError_(
            'The file is missing required column(s): '
            + ', '.join(labels[f] for f in missing)
            + '. Expected headers: ' + ', '.join(TEMPLATE_HEADERS) + '.'
        )
    return mapping


def parse_date(value):
    if value in (None, ''):
        raise ValueError('Date is required')
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        return (EXCEL_EPOCH + timedelta(days=float(value))).date()
    text = str(value).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f'"{text}" is not a date we recognise (use YYYY-MM-DD or DD/MM/YYYY)')


def parse_amount(value):
    if value in (None, ''):
        return Decimal('0.00')
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    # Blank means zero, but text that carries no number at all is an error —
    # silently zeroing a sales figure would hide bad data in the register.
    text = re.sub(r'[^0-9.\-]', '', str(value).strip())
    if text in ('', '-', '.'):
        raise ValueError(f'"{value}" is not a number')
    try:
        return Decimal(text)
    except InvalidOperation:
        raise ValueError(f'"{value}" is not a number')


def _clean_text(value, limit):
    if value is None:
        return ''
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()[:limit]


def _iter_rows(upload):
    """Yield raw row tuples from an .xlsx or .csv upload, header row first."""
    name = (getattr(upload, 'name', '') or '').lower()
    data = upload.read()
    if not data:
        raise ImportError_('The uploaded file is empty.')

    if name.endswith('.csv') or name.endswith('.txt'):
        try:
            text = data.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = data.decode('latin-1')
        yield from csv.reader(io.StringIO(text))
        return

    if not name.endswith(('.xlsx', '.xlsm')):
        raise ImportError_(
            'Unsupported file type. Upload an Excel .xlsx file (or .csv). '
            'Old .xls files must be re-saved as .xlsx.'
        )

    try:
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:
        raise ImportError_('That file could not be read as an Excel workbook.')
    try:
        yield from workbook[workbook.sheetnames[0]].iter_rows(values_only=True)
    finally:
        workbook.close()


def parse_upload(upload):
    """Read an upload into CRM record dicts.

    Returns (rows, errors). Rows that fail validation are reported in `errors`
    and skipped — a bad line never blocks the good ones. Raises ImportError_
    when the file itself is unusable (wrong type, no headers, missing columns).
    """
    rows_iter = _iter_rows(upload)

    header = None
    for candidate in rows_iter:
        if candidate and any(_normalise(c) for c in candidate):
            header = candidate
            break
    if header is None:
        raise ImportError_('No header row found in the file.')

    mapping = _map_headers(header)

    rows, errors = [], []
    # +2: spreadsheet rows are 1-based and the header occupies the first one,
    # so the numbers we report line up with what the user sees in Excel.
    for offset, raw in enumerate(rows_iter, start=2):
        if len(rows) >= MAX_ROWS:
            errors.append(f'Stopped after {MAX_ROWS} rows — split the file and upload the rest.')
            break
        if not raw or not any(str(cell).strip() for cell in raw if cell is not None):
            continue  # blank spacer row

        values = {field: (raw[i] if i < len(raw) else None) for i, field in mapping.items()}
        try:
            record = {
                'date': parse_date(values.get('date')),
                'receipt_number': _clean_text(values.get('receipt_number'), 50),
                'efd_receipt_number': _clean_text(values.get('efd_receipt_number'), 50),
                'customer_name': _clean_text(values.get('customer_name'), 200),
                'tin': _clean_text(values.get('tin'), 40),
                'sales_amount': parse_amount(values.get('sales_amount')),
                # Optional column. Absent means "nothing recorded as paid"; it
                # is not the same as a zero the user actually typed, but for an
                # opening import the two behave identically.
                'amount_paid': parse_amount(values.get('amount_paid')),
            }
        except ValueError as exc:
            errors.append(f'Row {offset}: {exc}')
            continue

        if not record['customer_name']:
            errors.append(f'Row {offset}: Customer Name is required')
            continue
        if record['amount_paid'] < 0:
            errors.append(f'Row {offset}: Amount Paid cannot be negative')
            continue

        rows.append(record)

    return rows, errors
