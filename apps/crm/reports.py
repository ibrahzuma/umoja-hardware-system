"""PDF reports for the CRM register.

Two reports: every customer with their totals, and a statement for one
customer. Both are built with reportlab to match the existing report exports
in finance/inventory (see apps/finance/views.py::_export_pdf), and both carry
the company logo and letterhead.
"""

import io
import os
from datetime import date, datetime
from decimal import Decimal

from django.conf import settings as django_settings
from django.contrib.staticfiles import finders
from django.http import HttpResponse
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

# Same blue the finance and purchases PDFs use, so CRM reports look like the
# rest of the system rather than a bolt-on.
BRAND = colors.HexColor('#1F4E78')
RULE = colors.HexColor('#DDDDDD')
ZEBRA = colors.HexColor('#F5F7FA')

STATUS_LABELS = {'paid': 'Paid', 'partial': 'Part paid', 'unpaid': 'Unpaid'}

# Landscape A4 less the 14mm margins. Both reports run wide because they carry
# the money columns (total, paid, balance) alongside the identifiers.
PAGE_SIZE = landscape(A4)
USABLE_WIDTH = 269 * mm


def _logo_path():
    """Filesystem path of the logo, or None.

    Prefers the logo uploaded in System Settings and falls back to the bundled
    static one. reportlab needs a real path — it cannot fetch a URL.
    """
    try:
        from apps.core.models import SystemSettings
        s = SystemSettings.objects.first()
        if s and s.logo:
            path = s.logo.path
            if os.path.isfile(path):
                return path
    except Exception:
        # A missing/unreadable logo must never break the report.
        pass

    found = finders.find('img/logo.png')
    if found:
        return found if isinstance(found, str) else found[0]
    if getattr(django_settings, 'STATIC_ROOT', None):
        path = os.path.join(django_settings.STATIC_ROOT, 'img', 'logo.png')
        if os.path.isfile(path):
            return path
    return None


def company_info():
    try:
        from apps.core.models import SystemSettings
        s = SystemSettings.objects.first()
    except Exception:
        s = None
    return {
        'name': (getattr(s, 'company_name', '') or 'Umoja Hardware') if s else 'Umoja Hardware',
        'address': (getattr(s, 'address', '') or '') if s else '',
        'phone': (getattr(s, 'phone', '') or '') if s else '',
        'email': (getattr(s, 'email', '') or '') if s else '',
        'tin': (getattr(s, 'tin', '') or '') if s else '',
        'vrn': (getattr(s, 'vrn', '') or '') if s else '',
        'currency': ((getattr(s, 'currency', '') or 'TZS') if s else 'TZS'),
    }


def _logo_flowable(max_height=20 * mm, max_width=45 * mm):
    path = _logo_path()
    if not path:
        return None
    try:
        width, height = ImageReader(path).getSize()
        scale = min(max_width / width, max_height / height)
        return Image(path, width=width * scale, height=height * scale)
    except Exception:
        return None


def _letterhead(company, styles):
    """Logo on the left, company details on the right, under a brand rule."""
    right = ParagraphStyle('co', parent=styles['Normal'], fontSize=8.5, leading=11, alignment=2)
    lines = [f"<b><font size=13 color='#1F4E78'>{company['name']}</font></b>"]
    if company['address']:
        lines.append(company['address'].replace('\n', '<br/>'))
    contact = ' | '.join(x for x in (company['phone'], company['email']) if x)
    if contact:
        lines.append(contact)
    fiscal = '  '.join(x for x in (
        f"TIN: {company['tin']}" if company['tin'] else '',
        f"VRN: {company['vrn']}" if company['vrn'] else '',
    ) if x)
    if fiscal:
        lines.append(fiscal)

    logo = _logo_flowable()
    header = Table(
        [[logo or '', Paragraph('<br/>'.join(lines), right)]],
        colWidths=[50 * mm, USABLE_WIDTH - 50 * mm],
    )
    header.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('LINEBELOW', (0, 0), (-1, -1), 1.2, BRAND),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    return header


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont('Helvetica', 7.5)
    canvas.setFillColor(colors.HexColor('#777777'))
    canvas.drawString(14 * mm, 10 * mm, doc.crm_footer_note)
    canvas.drawRightString(PAGE_SIZE[0] - 14 * mm, 10 * mm, f'Page {doc.page}')
    canvas.restoreState()


def _build(title, elements, filename, footer_note):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=PAGE_SIZE,
        leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm, bottomMargin=18 * mm,
        title=title,
    )
    doc.crm_footer_note = footer_note
    doc.build(elements, onFirstPage=_footer, onLaterPages=_footer)

    response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    # inline: opens in the browser's viewer, where the user can print or save.
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    return response


def _meta_lines(styles, filters, generated_by):
    small = ParagraphStyle('meta', parent=styles['Normal'], fontSize=8.5, leading=11,
                           textColor=colors.HexColor('#555555'))
    out = []
    for line in filters:
        out.append(Paragraph(line, small))
    stamp = datetime.now().strftime('%d %b %Y %H:%M')
    who = f' by {generated_by}' if generated_by else ''
    out.append(Paragraph(f'Generated {stamp}{who}', small))
    return out


def _money(value):
    return '{:,.0f}'.format(Decimal(str(value or 0)))


def _table_style(total_row=True, right_cols=()):
    style = [
        ('BACKGROUND', (0, 0), (-1, 0), BRAND),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.4, RULE),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]
    for col in right_cols:
        style.append(('ALIGN', (col, 0), (col, -1), 'RIGHT'))
    if total_row:
        style += [
            ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, ZEBRA]),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#EEF2F7')),
            ('LINEABOVE', (0, -1), (-1, -1), 0.8, BRAND),
        ]
    else:
        style.append(('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, ZEBRA]))
    return TableStyle(style)


def customers_report(rows, filters=(), generated_by=''):
    """All customers, one row each, with the register's grand total."""
    company = company_info()
    styles = getSampleStyleSheet()
    cell = ParagraphStyle('cell', parent=styles['Normal'], fontSize=8, leading=10)

    elements = [_letterhead(company, styles), Spacer(1, 8),
                Paragraph("CRM &ndash; Customer Report", styles['Heading2'])]
    elements += _meta_lines(styles, filters, generated_by)
    elements.append(Spacer(1, 8))

    currency = company['currency']
    data = [['#', 'Customer Name', 'TIN', 'Records', 'First', 'Last',
             f'Total ({currency})', f'Paid ({currency})', f'Balance ({currency})', 'Status']]
    widths = [9 * mm, 54 * mm, 25 * mm, 16 * mm, 20 * mm, 20 * mm,
              34 * mm, 34 * mm, 34 * mm, 23 * mm]
    money_cols = (6, 7, 8)

    total = Decimal('0')
    paid_total = Decimal('0')
    records = 0
    for index, row in enumerate(rows, start=1):
        data.append([
            str(index),
            Paragraph(str(row['customer_name']), cell),
            row['tin'] or '-',
            str(row['records']),
            str(row['first_transaction'] or '-'),
            str(row['last_transaction'] or '-'),
            _money(row['total_amount']),
            _money(row.get('amount_paid', 0)),
            _money(row.get('balance', row['total_amount'])),
            STATUS_LABELS.get(row.get('payment_status', ''), '-'),
        ])
        total += Decimal(str(row['total_amount'] or 0))
        paid_total += Decimal(str(row.get('amount_paid') or 0))
        records += row['records']

    if len(data) == 1:
        data.append(['', Paragraph('No customers match this selection.', cell)] + [''] * 8)
        table = Table(data, repeatRows=1, colWidths=widths)
        table.setStyle(_table_style(total_row=False, right_cols=money_cols))
    else:
        data.append(['', f'TOTAL — {len(rows)} customer(s)', '', str(records), '', '',
                     _money(total), _money(paid_total), _money(total - paid_total), ''])
        table = Table(data, repeatRows=1, colWidths=widths)
        table.setStyle(_table_style(right_cols=money_cols))

    elements.append(table)
    return _build(
        'CRM Customer Report', elements,
        f'crm_customers_{date.today().isoformat()}.pdf',
        f"{company['name']} — CRM Customer Report",
    )


def customer_statement(customer_name, tin, records, filters=(), generated_by=''):
    """Every transaction on file for one customer."""
    company = company_info()
    styles = getSampleStyleSheet()
    cell = ParagraphStyle('cell', parent=styles['Normal'], fontSize=8, leading=10)
    label = ParagraphStyle('label', parent=styles['Normal'], fontSize=9, leading=12)

    elements = [_letterhead(company, styles), Spacer(1, 8),
                Paragraph("CRM &ndash; Customer Statement", styles['Heading2']),
                Paragraph(f"<b><font size=12>{customer_name}</font></b>", label),
                Paragraph(f"TIN: <b>{tin or 'not recorded'}</b>", label),
                Spacer(1, 4)]
    elements += _meta_lines(styles, filters, generated_by)
    elements.append(Spacer(1, 8))

    currency = company['currency']
    data = [['Date', 'Receipt No', 'EFD Receipt No', 'TIN',
             f'Sales Amount ({currency})', f'Paid ({currency})', f'Balance ({currency})', 'Status']]
    widths = [22 * mm, 28 * mm, 38 * mm, 28 * mm, 40 * mm, 40 * mm, 40 * mm, 33 * mm]
    money_cols = (4, 5, 6)

    total = Decimal('0')
    paid_total = Decimal('0')
    for record in records:
        paid = Decimal(str(record.amount_paid))
        data.append([
            str(record.date),
            record.receipt_number or '-',
            record.efd_receipt_number or '-',
            record.tin or '-',
            _money(record.sales_amount),
            _money(paid),
            _money(record.balance),
            STATUS_LABELS.get(record.payment_status, '-'),
        ])
        total += Decimal(str(record.sales_amount or 0))
        paid_total += paid

    count = len(data) - 1
    if not count:
        data.append([Paragraph('No transactions match this selection.', cell)] + [''] * 7)
        table = Table(data, repeatRows=1, colWidths=widths)
        table.setStyle(_table_style(total_row=False, right_cols=money_cols))
    else:
        data.append(['', f'TOTAL — {count} transaction(s)', '', '',
                     _money(total), _money(paid_total), _money(total - paid_total), ''])
        table = Table(data, repeatRows=1, colWidths=widths)
        table.setStyle(_table_style(right_cols=money_cols))
    elements.append(table)

    if count:
        outstanding = total - paid_total
        elements.append(Spacer(1, 8))
        elements.append(Paragraph(
            f"Average per transaction: <b>{_money(total / count)} {currency}</b>"
            f" &nbsp;&nbsp;|&nbsp;&nbsp; Outstanding balance: "
            f"<b><font color='{'#B00020' if outstanding > 0 else '#1B7F3B'}'>"
            f"{_money(outstanding)} {currency}</font></b>", label))

    safe_name = ''.join(c if c.isalnum() else '_' for c in customer_name)[:40] or 'customer'
    return _build(
        f'CRM Statement - {customer_name}', elements,
        f'crm_{safe_name}_{date.today().isoformat()}.pdf',
        f"{company['name']} — CRM Statement: {customer_name}",
    )
