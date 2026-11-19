"""Bank statement reconciliation.

A statement file (CSV / Excel / PDF) is uploaded against a bank account, its
rows are parsed into `BankStatementLine`s, and those rows are then compared
against the GL rows on that bank's ledger. Matching links one statement row
to one GL row; everything unmatched on either side is what the accountant
chases up.

The parsers are permissive: they look for column names rather than fixed
positions, so the same code handles exports from different banks. A missing
column is just absent — the row is still read for what is there.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Iterable, List, Optional

from django.db import transaction
from django.utils import timezone

from .models import BankStatementLine, BankStatementUpload


# --------------------------------------------------------------------------
# Header matching — one dict key per concept we care about, each a list of
# column-name fragments we accept as that concept. Lowercased / no-punctuation
# compare, so "Transaction Date" and "DATE_POSTED" both land on 'date'.
# --------------------------------------------------------------------------

# NB: within a key, the first PATTERN that matches a column name wins; the
# `_map_columns` loop then tries the next key. Columns are scanned left to
# right, so the first matching COLUMN on each key wins. For the standard
# TZ bank layout (Book Date | Value Date | Ref 1 | Narration | Ref 2 |
# Cheque No | DEBIT | CREDIT | BALANCE), 'Book Date' is picked over
# 'Value Date' because it sits in the earlier column.
HEADER_PATTERNS = {
    'date': ('bookdate', 'date', 'transactiondate', 'posteddate', 'valuedate',
             'txndate'),
    'description': ('narration', 'description', 'particulars', 'details', 'memo'),
    'reference': ('ref1', 'reference', 'ref', 'transactionid', 'txnid',
                  'chequeno', 'chqno', 'documentno', 'docno', 'refno'),
    'debit': ('debit', 'dr', 'withdrawal', 'moneyout', 'amountout', 'paid'),
    'credit': ('credit', 'cr', 'deposit', 'moneyin', 'amountin', 'received'),
    # Just 'amount' — a plain 'value' would collide with 'Value Date' and
    # hijack that column, mis-reading the date as a number on any row whose
    # debit/credit are both blank.
    'amount': ('amount', 'txnamount', 'transactionamount'),
    'balance': ('balance', 'runningbalance', 'closingbalance'),
}

# The empty template the "Download Template" button hands out — one row per
# column, headers only. Order matches what the TZ banks export.
TEMPLATE_HEADERS = (
    'Book Date', 'Value Date', 'Ref 1', 'Narration', 'Ref 2',
    'Cheque No', 'DEBIT', 'CREDIT', 'BALANCE',
)


def _normalise(header: str) -> str:
    return re.sub(r'[^a-z0-9]', '', (header or '').lower())


def _map_columns(headers: List[str]) -> dict:
    """{'date': 0, 'credit': 2, …} — whichever columns we recognise."""
    out = {}
    for i, h in enumerate(headers):
        norm = _normalise(h)
        if not norm:
            continue
        for key, patterns in HEADER_PATTERNS.items():
            if key in out:
                continue
            if any(p in norm for p in patterns):
                out[key] = i
                break
    return out


def _parse_amount(raw) -> Decimal:
    """'1,234.50' / '1 234,50' / '(1234.50)' / '' → Decimal.

    Parentheses are the accounting sign for a negative — honoured, and the
    caller can flip the sign if they need to.
    """
    if raw in (None, ''):
        return Decimal('0')
    s = str(raw).strip()
    if not s:
        return Decimal('0')
    negative = False
    if s.startswith('(') and s.endswith(')'):
        negative = True
        s = s[1:-1]
    # Strip currency, commas, and non-breaking spaces.
    s = s.replace(',', '').replace(' ', '').replace(' ', '')
    s = re.sub(r'[^0-9.\-]', '', s)
    if not s or s in ('-', '.'):
        return Decimal('0')
    try:
        amt = Decimal(s)
    except InvalidOperation:
        return Decimal('0')
    return -amt if negative else amt


_DATE_FORMATS = (
    '%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%m/%d/%Y', '%d %b %Y',
    '%d-%b-%Y', '%d %B %Y', '%d/%m/%y', '%Y/%m/%d',
)


def _parse_date(raw) -> Optional[date]:
    if raw in (None, ''):
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    s = str(raw).strip()
    if not s:
        return None
    # Strip time part if present (bank exports often include "00:00:00").
    s = s.split()[0] if ' ' in s and ':' in s else s
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------
# Parsers — each returns a list of {date, description, reference, debit,
# credit, balance} dicts. Caller decides which bank/upload they belong to.
# --------------------------------------------------------------------------

def _row_from_mapping(row: list, columns: dict) -> Optional[dict]:
    """Convert one spreadsheet row to the common dict, or None if it's not
    really a data row (no date, no amounts)."""
    def get(key):
        i = columns.get(key)
        return row[i] if i is not None and i < len(row) else ''

    d = _parse_date(get('date'))
    if d is None:
        return None

    debit = _parse_amount(get('debit'))
    credit = _parse_amount(get('credit'))
    # Single "Amount" column: a negative is a withdrawal, a positive a deposit.
    if 'amount' in columns and not debit and not credit:
        amt = _parse_amount(get('amount'))
        if amt > 0:
            credit = amt
        elif amt < 0:
            debit = -amt

    if not debit and not credit:
        return None

    return {
        'date': d,
        'description': str(get('description') or '').strip()[:500],
        'reference': str(get('reference') or '').strip()[:120],
        'debit': debit,
        'credit': credit,
        'balance': _parse_amount(get('balance')) if columns.get('balance') is not None else None,
    }


def parse_csv(raw: bytes) -> List[dict]:
    """Parse a CSV export. UTF-8, UTF-8-BOM and Windows-1252 all handled."""
    for encoding in ('utf-8-sig', 'utf-8', 'cp1252', 'latin-1'):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode('utf-8', errors='replace')

    # Sniff the delimiter — some banks export semi-colon-separated.
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=',;\t|')
    except csv.Error:
        dialect = csv.excel

    reader = csv.reader(io.StringIO(text), dialect)
    rows = list(reader)
    return _parse_tabular(rows)


def parse_xlsx(raw: bytes) -> List[dict]:
    """Parse an Excel file via openpyxl (already a project dependency)."""
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(raw), data_only=True, read_only=True)
    ws = wb.active
    rows = []
    for row in ws.iter_rows(values_only=True):
        rows.append(['' if c is None else c for c in row])
    return _parse_tabular(rows)


def _parse_tabular(rows: List[list]) -> List[dict]:
    """Find the header row (first row with a recognised column), then read
    every row after it. Blank rows are skipped silently."""
    columns = {}
    header_at = None
    for i, row in enumerate(rows[:20]):
        candidate = _map_columns([str(c) for c in row])
        # A header needs at least a date + one of (debit, credit, amount).
        if 'date' in candidate and (
                'debit' in candidate or 'credit' in candidate or 'amount' in candidate):
            columns = candidate
            header_at = i
            break
    if header_at is None:
        return []

    out = []
    for row in rows[header_at + 1:]:
        line = _row_from_mapping(row, columns)
        if line:
            out.append(line)
    return out


def parse(raw: bytes, content_type: str, filename: str = '') -> List[dict]:
    """Dispatch on content type / extension. PDF is **not supported yet** —
    the parser raises so the caller can show a helpful error."""
    name = (filename or '').lower()
    ct = (content_type or '').lower()

    if name.endswith('.csv') or 'csv' in ct:
        return parse_csv(raw)
    if name.endswith(('.xlsx', '.xlsm', '.xltx')) or 'spreadsheet' in ct or 'excel' in ct:
        return parse_xlsx(raw)
    if name.endswith('.pdf') or 'pdf' in ct:
        raise ValueError(
            "PDF statements are not auto-parsed yet. "
            "Please export the statement as Excel or CSV from the bank portal."
        )
    # Fall back to CSV: many banks put a .txt export that is comma-separated.
    try:
        return parse_csv(raw)
    except Exception:
        raise ValueError(
            f"Could not read the statement '{filename}' — upload as Excel or CSV."
        )


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------

@transaction.atomic
def store_lines(upload: BankStatementUpload, lines: Iterable[dict]) -> int:
    """Save parsed rows against an upload, setting its period if blank."""
    objs = []
    for line in lines:
        objs.append(BankStatementLine(
            upload=upload,
            date=line['date'],
            description=line['description'],
            reference=line['reference'],
            debit=line['debit'],
            credit=line['credit'],
            balance=line.get('balance'),
        ))
    BankStatementLine.objects.bulk_create(objs)
    if objs:
        dates = sorted(o.date for o in objs)
        if upload.period_start is None:
            upload.period_start = dates[0]
        if upload.period_end is None:
            upload.period_end = dates[-1]
        upload.save(update_fields=['period_start', 'period_end'])
    return len(objs)


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------

def ledger_entries_for(bank_account_name, period_start=None, period_end=None):
    """Return the unmatched GL rows on the bank's ledger, within the period.

    The bank account is matched to a ledger by name — the ledger carries the
    bank's name verbatim (see `apps/finance/signals.py::ensure_ledger_for`).
    """
    from .models import GeneralLedgerEntry, LedgerAccount

    ledger = LedgerAccount.objects.filter(name=bank_account_name).first()
    if ledger is None:
        return GeneralLedgerEntry.objects.none()

    qs = (GeneralLedgerEntry.objects
          .filter(account=ledger, statement_lines__isnull=True)
          .select_related('voucher', 'account')
          .order_by('date', 'id'))
    if period_start:
        qs = qs.filter(date__gte=period_start)
    if period_end:
        qs = qs.filter(date__lte=period_end)
    return qs


def suggest_matches(upload: BankStatementUpload, window_days: int = 7):
    """Pair unmatched statement lines with GL rows that match on amount+date.

    Returns a list of (statement_line, [candidate_gl_entries]). The match is
    loose on purpose — the accountant presses a button to confirm.
    """
    bank_name = upload.bank_account.name
    ledger_qs = ledger_entries_for(bank_name, upload.period_start, upload.period_end)
    ledger_rows = list(ledger_qs)
    unmatched = upload.lines.filter(matched_entry__isnull=True)
    suggestions = []
    for line in unmatched:
        want = line.credit if line.credit else -line.debit
        cands = []
        for gl in ledger_rows:
            # On the books, a bank ledger is debited when money comes IN
            # (statement credit) and credited when money goes OUT (statement
            # debit). Match the signed amount.
            gl_signed = (gl.debit or Decimal('0')) - (gl.credit or Decimal('0'))
            if gl_signed == want and abs((gl.date - line.date).days) <= window_days:
                cands.append(gl)
        if cands:
            suggestions.append((line, cands))
    return suggestions


def match(line: BankStatementLine, gl_entry, user) -> None:
    """Link one statement line to one GL row."""
    line.matched_entry = gl_entry
    line.matched_at = timezone.now()
    line.matched_by = user
    line.save(update_fields=['matched_entry', 'matched_at', 'matched_by'])


def unmatch(line: BankStatementLine, user=None) -> None:
    line.matched_entry = None
    line.matched_at = None
    line.matched_by = None
    line.save(update_fields=['matched_entry', 'matched_at', 'matched_by'])
