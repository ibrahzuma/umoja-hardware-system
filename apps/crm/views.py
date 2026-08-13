import csv
from datetime import date as date_cls
from decimal import Decimal

from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db.models import Count, DecimalField, F, Max, Min, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.http import Http404, HttpResponse
from django.views.generic import TemplateView
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from apps.sales.models import Sale
from apps.users.permissions import IsAccountant

from .imports import TEMPLATE_HEADERS, ImportError_, parse_upload
from .models import CrmPayment, CustomerRecord
from .reports import customer_statement, customers_report
from .serializers import CrmPaymentSerializer, CustomerRecordSerializer


def can_use_crm(user):
    """CRM is an admin + finance register. Same rule everywhere: the template
    view, the API and the sidebar entry all read from this."""
    return bool(
        user and user.is_authenticated
        and (user.is_superuser or user.is_admin_role or user.is_accountant)
    )


def _filtered_records(params):
    """Apply the list screen's search / date filters to the CRM queryset."""
    qs = CustomerRecord.objects.all()
    search = (params.get('search') or '').strip()
    if search:
        qs = qs.filter(
            Q(customer_name__icontains=search)
            | Q(tin__icontains=search)
            | Q(receipt_number__icontains=search)
            | Q(efd_receipt_number__icontains=search)
        )
    # Exact name — how the customer detail screen scopes itself to one customer.
    customer = (params.get('customer') or '').strip()
    if customer:
        qs = qs.filter(customer_name=customer)
    start = (params.get('start') or '').strip()
    end = (params.get('end') or '').strip()
    if start:
        qs = qs.filter(date__gte=start)
    if end:
        qs = qs.filter(date__lte=end)

    # Payment state is derived from the payments table, so it is filtered via a
    # subquery — annotating here would join payments and inflate the SUM/COUNT
    # aggregates that _customer_rows() computes on this same queryset.
    status_filter = (params.get('status') or '').strip()
    if status_filter in ('paid', 'partial', 'unpaid'):
        paid = CustomerRecord.objects.annotate(
            paid_total=Coalesce(Sum('payments__amount'), Value(Decimal('0')),
                                output_field=DecimalField(max_digits=14, decimal_places=2))
        )
        if status_filter == 'unpaid':
            matching = paid.filter(paid_total__lte=0, sales_amount__gt=0)
        elif status_filter == 'partial':
            matching = paid.filter(paid_total__gt=0, paid_total__lt=F('sales_amount'))
        else:
            matching = paid.filter(paid_total__gte=F('sales_amount'))
        qs = qs.filter(pk__in=matching.values('pk'))
    return qs


def _with_payments(qs):
    """Attach each record's paid total, so a page of rows costs one query."""
    return qs.annotate(
        paid_total=Coalesce(Sum('payments__amount'), Value(Decimal('0')),
                            output_field=DecimalField(max_digits=14, decimal_places=2))
    )


def _paid_by_customer(qs):
    """{customer_name: amount paid} across a record selection.

    Deliberately a separate query rather than another annotation on the
    grouping above — joining payments there would multiply each record row by
    its number of payments and inflate the sales totals.
    """
    rows = (
        CrmPayment.objects.filter(record__in=qs.values('pk'))
        .values('record__customer_name')
        .annotate(paid=Sum('amount'))
    )
    return {row['record__customer_name']: row['paid'] or Decimal('0') for row in rows}


def _customer_rows(qs):
    """Group a record queryset into one row per customer.

    Shared by the customers API action and the PDF report so the screen and
    the printout can never disagree.
    """
    grouped = (
        qs.values('customer_name')
        .annotate(
            records=Count('id'),
            total_amount=Sum('sales_amount'),
            last_transaction=Max('date'),
            first_transaction=Min('date'),
        )
        .order_by('-total_amount', 'customer_name')
    )

    # Latest non-blank TIN per customer, in one pass rather than per row.
    tins = {}
    for name, tin in (
        qs.exclude(tin='').order_by('date', 'id')
        .values_list('customer_name', 'tin')
    ):
        tins[name] = tin

    paid_map = _paid_by_customer(qs)

    rows = []
    for row in grouped:
        total = Decimal(str(row['total_amount'] or 0))
        paid = Decimal(str(paid_map.get(row['customer_name'], 0)))
        balance = total - paid
        rows.append({
            'customer_name': row['customer_name'],
            'tin': tins.get(row['customer_name'], ''),
            'records': row['records'],
            'total_amount': total,
            'amount_paid': paid,
            'balance': balance,
            'payment_status': 'paid' if balance <= 0 else ('partial' if paid > 0 else 'unpaid'),
            'last_transaction': row['last_transaction'],
            'first_transaction': row['first_transaction'],
        })
    return rows


def _filter_labels(params):
    """Human-readable description of the filters a report was run with, so a
    printed page always says what selection it represents."""
    labels = []
    search = (params.get('search') or '').strip()
    start = (params.get('start') or '').strip()
    end = (params.get('end') or '').strip()
    status_filter = (params.get('status') or '').strip()
    if search:
        labels.append(f'Search: "{search}"')
    if status_filter in ('paid', 'partial', 'unpaid'):
        labels.append({'paid': 'Fully paid only', 'partial': 'Partly paid only',
                       'unpaid': 'Unpaid only'}[status_filter])
    if start and end:
        labels.append(f'Period: {start} to {end}')
    elif start:
        labels.append(f'Period: from {start}')
    elif end:
        labels.append(f'Period: up to {end}')
    if not labels:
        labels.append('All records')
    return labels


class CrmPagination(PageNumberPagination):
    """Both CRM tables page through their rows — the register grows without
    bound, so neither screen may ever fetch everything. The UI offers
    10/20/50/100 via ?page_size=; anything larger is clamped to 100."""
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


class CustomerRecordViewSet(viewsets.ModelViewSet):
    """CRM records API. Admin/superuser and Accountants only (IsAccountant
    allows privileged users through — see apps/users/permissions.py)."""
    queryset = CustomerRecord.objects.all()
    serializer_class = CustomerRecordSerializer
    permission_classes = [permissions.IsAuthenticated, IsAccountant]
    pagination_class = CrmPagination

    def get_queryset(self):
        return _with_payments(_filtered_records(self.request.query_params))

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @action(detail=False, methods=['get'])
    def summary(self, request):
        qs = _filtered_records(request.query_params)
        total = Decimal(str(qs.aggregate(total=Sum('sales_amount'))['total'] or 0))
        paid = Decimal(str(
            CrmPayment.objects.filter(record__in=qs.values('pk'))
            .aggregate(total=Sum('amount'))['total'] or 0
        ))
        return Response({
            'records': qs.count(),
            'customers': qs.values('customer_name').distinct().count(),
            'total_amount': total,
            'amount_paid': paid,
            'balance': total - paid,
        })

    @action(detail=True, methods=['post'])
    def mark_paid(self, request, pk=None):
        """Settle a sale in one click: records a payment for exactly whatever
        is still outstanding, so the payment history stays truthful instead of
        the sale just being flagged."""
        record = self.get_object()
        outstanding = record.balance
        if outstanding <= 0:
            return Response({'detail': 'This sale is already fully paid.'},
                            status=status.HTTP_400_BAD_REQUEST)

        payment = CrmPayment.objects.create(
            record=record,
            amount=outstanding,
            paid_on=request.data.get('paid_on') or date_cls.today(),
            method=request.data.get('method') or 'cash',
            reference=request.data.get('reference', ''),
            created_by=request.user,
        )
        return Response({
            'detail': f'Marked as fully paid — {outstanding:,.2f} recorded.',
            'payment': CrmPaymentSerializer(payment).data,
        })

    @action(detail=True, methods=['get'])
    def payments(self, request, pk=None):
        """Payment history for one sale."""
        record = self.get_object()
        return Response({
            'sales_amount': record.sales_amount,
            'amount_paid': record.amount_paid,
            'balance': record.balance,
            'payment_status': record.payment_status,
            'payments': CrmPaymentSerializer(record.payments.all(), many=True).data,
        })

    @action(detail=False, methods=['get'])
    def customers(self, request):
        """One row per customer for the CRM landing screen.

        Records are grouped by customer_name — the register has no customer
        entity of its own by design, so the name written on the transaction is
        the identity. TIN is reported from the customer's most recent record
        that carries one, since older rows are often left blank.
        """
        # Deliberately NOT self.get_queryset(): that one is annotated with the
        # payments join, which would multiply each record by its payment count
        # and inflate the per-customer record counts and sales totals.
        rows = _customer_rows(_filtered_records(request.query_params))
        # Paginated like the record list — a busy register has thousands of
        # customers and the landing screen must not fetch them all.
        page = self.paginate_queryset(rows)
        if page is not None:
            return self.get_paginated_response(page)
        return Response(rows)

    @action(detail=False, methods=['get'])
    def available_sales(self, request):
        """Sales that have not been pulled into the CRM register yet.

        Read-only preview for the "pull from sales" action — nothing is linked,
        the invoice number is only used to avoid importing the same sale twice.
        """
        already = set(
            CustomerRecord.objects.exclude(source_invoice='')
            .values_list('source_invoice', flat=True)
        )
        sales = Sale.objects.select_related('customer').exclude(status='cancelled')
        start = (request.query_params.get('start') or '').strip()
        end = (request.query_params.get('end') or '').strip()
        if start:
            sales = sales.filter(created_at__date__gte=start)
        if end:
            sales = sales.filter(created_at__date__lte=end)

        rows = []
        for sale in sales.order_by('-created_at')[:500]:
            if sale.invoice_number in already:
                continue
            rows.append({
                'invoice_number': sale.invoice_number,
                'date': sale.created_at.date(),
                'customer_name': sale.customer.name if sale.customer else (sale.customer_name or 'Walk-in Customer'),
                'sales_amount': sale.total_amount,
            })
        return Response(rows)

    @action(detail=False, methods=['post'])
    def import_sales(self, request):
        """Copy the chosen sales into the CRM register as ordinary, editable
        rows. POST {"invoices": ["INV-1", "INV-2"]}."""
        invoices = request.data.get('invoices') or []
        if not isinstance(invoices, list) or not invoices:
            return Response({'detail': 'No invoices selected.'}, status=status.HTTP_400_BAD_REQUEST)

        already = set(
            CustomerRecord.objects.filter(source_invoice__in=invoices)
            .values_list('source_invoice', flat=True)
        )
        created = 0
        for sale in Sale.objects.select_related('customer').filter(invoice_number__in=invoices):
            if sale.invoice_number in already:
                continue
            CustomerRecord.objects.create(
                date=sale.created_at.date(),
                receipt_number=sale.invoice_number,
                efd_receipt_number='',
                customer_name=sale.customer.name if sale.customer else (sale.customer_name or 'Walk-in Customer'),
                tin='',
                sales_amount=sale.total_amount,
                source_invoice=sale.invoice_number,
                created_by=request.user,
            )
            created += 1
        return Response({
            'created': created,
            'skipped': len(invoices) - created,
            'detail': f'{created} record(s) added to CRM.',
        })


    @action(detail=False, methods=['post'], url_path='bulk_upload')
    def bulk_upload(self, request):
        """Import many customer records from an Excel (.xlsx) or CSV upload.

        Rows that fail validation are reported back with their spreadsheet row
        number and skipped — the rest still import. A row whose receipt or EFD
        receipt number already exists is skipped too, so re-uploading the same
        file does not double the register.
        """
        upload = request.FILES.get('file')
        if not upload:
            return Response({'detail': 'No file uploaded.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            rows, errors = parse_upload(upload)
        except ImportError_ as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        # Existing receipt numbers, so a repeated upload is a no-op.
        seen = set(
            CustomerRecord.objects.exclude(receipt_number='')
            .values_list('receipt_number', flat=True)
        )
        seen |= set(
            CustomerRecord.objects.exclude(efd_receipt_number='')
            .values_list('efd_receipt_number', flat=True)
        )

        to_create, skipped = [], 0
        for row in rows:
            keys = {k for k in (row['receipt_number'], row['efd_receipt_number']) if k}
            if keys & seen:
                skipped += 1
                continue
            seen |= keys
            to_create.append(CustomerRecord(created_by=request.user, **row))

        CustomerRecord.objects.bulk_create(to_create)

        message = f'{len(to_create)} record(s) imported.'
        if skipped:
            message += f' {skipped} skipped as already in the register.'
        if errors:
            message += f' {len(errors)} row(s) had problems.'
        return Response(
            {
                'created': len(to_create),
                'skipped': skipped,
                'errors': errors[:50],
                'error_count': len(errors),
                'detail': message,
            },
            status=status.HTTP_200_OK if not errors else status.HTTP_207_MULTI_STATUS,
        )


class CrmListView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = 'crm/crm_list.html'

    def test_func(self):
        return can_use_crm(self.request.user)


class CrmPaymentViewSet(viewsets.ModelViewSet):
    """Payments received against CRM sales. Same admin + finance gate."""
    queryset = CrmPayment.objects.select_related('record').all()
    serializer_class = CrmPaymentSerializer
    permission_classes = [permissions.IsAuthenticated, IsAccountant]
    pagination_class = None  # a single sale's history is short; the UI shows it whole

    def get_queryset(self):
        qs = super().get_queryset()
        record = (self.request.query_params.get('record') or '').strip()
        if record.isdigit():
            qs = qs.filter(record_id=int(record))
        return qs

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class CrmCustomerDetailView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    """Everything on file for one customer, reached by picking them from the
    CRM list. The name arrives as a query parameter rather than a path segment
    because customer names legitimately contain slashes and dots."""
    template_name = 'crm/crm_customer_detail.html'

    def test_func(self):
        return can_use_crm(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        name = (self.request.GET.get('name') or '').strip()
        records = CustomerRecord.objects.filter(customer_name=name)
        context['customer_name'] = name
        context['exists'] = records.exists()
        context['tin'] = (
            records.exclude(tin='').order_by('-date', '-id')
            .values_list('tin', flat=True).first() or ''
        )
        return context


class CrmExportView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    """CSV of the current filter selection."""

    def test_func(self):
        return can_use_crm(self.request.user)

    def get(self, request, *args, **kwargs):
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="crm_customers.csv"'
        writer = csv.writer(response)
        writer.writerow(['Date', 'Receipt No', 'EFD Receipt No', 'Customer Name', 'TIN',
                         'Sales Amount', 'Amount Paid', 'Balance', 'Status'])
        for r in _with_payments(_filtered_records(request.GET)):
            writer.writerow([
                r.date, r.receipt_number, r.efd_receipt_number,
                r.customer_name, r.tin, r.sales_amount,
                r.amount_paid, r.balance, r.payment_status,
            ])
        return response


class CrmReportView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    """PDF of every customer in the register, honouring the screen's filters."""

    def test_func(self):
        return can_use_crm(self.request.user)

    def get(self, request, *args, **kwargs):
        rows = _customer_rows(_filtered_records(request.GET))
        return customers_report(
            rows,
            filters=_filter_labels(request.GET),
            generated_by=request.user.get_full_name() or request.user.username,
        )


class CrmCustomerReportView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    """PDF statement for a single customer."""

    def test_func(self):
        return can_use_crm(self.request.user)

    def get(self, request, *args, **kwargs):
        name = (request.GET.get('name') or '').strip()
        if not name:
            raise Http404('No customer specified.')

        params = request.GET.copy()
        params['customer'] = name
        # Annotated: the report reads amount_paid/balance on every row and must
        # not fire a query per record.
        records = _with_payments(_filtered_records(params)).order_by('date', 'id')
        if not CustomerRecord.objects.filter(customer_name=name).exists():
            raise Http404('No records for this customer.')

        tin = (
            CustomerRecord.objects.filter(customer_name=name).exclude(tin='')
            .order_by('-date', '-id').values_list('tin', flat=True).first() or ''
        )
        return customer_statement(
            name, tin, list(records),
            filters=_filter_labels(request.GET),
            generated_by=request.user.get_full_name() or request.user.username,
        )


class CrmImportTemplateView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    """Blank .xlsx with the expected headers and one example row."""

    def test_func(self):
        return can_use_crm(self.request.user)

    def get(self, request, *args, **kwargs):
        from openpyxl import Workbook
        from openpyxl.styles import Font

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = 'Customers'
        sheet.append(TEMPLATE_HEADERS)
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        sheet.append(['2026-08-01', 'RC-0012', '35EFD9921', 'Kibo Traders', '109-882-441', 1250000])
        for column, width in zip('ABCDEF', (14, 16, 18, 28, 18, 16)):
            sheet.column_dimensions[column].width = width

        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = 'attachment; filename="crm_import_template.xlsx"'
        workbook.save(response)
        return response
