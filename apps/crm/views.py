import csv

from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db.models import Q, Sum
from django.http import HttpResponse
from django.views.generic import TemplateView
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.sales.models import Sale
from apps.users.permissions import IsAccountant

from .models import CustomerRecord
from .serializers import CustomerRecordSerializer


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
    start = (params.get('start') or '').strip()
    end = (params.get('end') or '').strip()
    if start:
        qs = qs.filter(date__gte=start)
    if end:
        qs = qs.filter(date__lte=end)
    return qs


class CustomerRecordViewSet(viewsets.ModelViewSet):
    """CRM records API. Admin/superuser and Accountants only (IsAccountant
    allows privileged users through — see apps/users/permissions.py)."""
    queryset = CustomerRecord.objects.all()
    serializer_class = CustomerRecordSerializer
    permission_classes = [permissions.IsAuthenticated, IsAccountant]

    def get_queryset(self):
        return _filtered_records(self.request.query_params)

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @action(detail=False, methods=['get'])
    def summary(self, request):
        qs = self.get_queryset()
        total = qs.aggregate(total=Sum('sales_amount'))['total'] or 0
        return Response({
            'records': qs.count(),
            'customers': qs.values('customer_name').distinct().count(),
            'total_amount': total,
        })

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


class CrmListView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = 'crm/crm_list.html'

    def test_func(self):
        return can_use_crm(self.request.user)


class CrmExportView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    """CSV of the current filter selection."""

    def test_func(self):
        return can_use_crm(self.request.user)

    def get(self, request, *args, **kwargs):
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="crm_customers.csv"'
        writer = csv.writer(response)
        writer.writerow(['Date', 'Receipt No', 'EFD Receipt No', 'Customer Name', 'TIN', 'Sales Amount'])
        for r in _filtered_records(request.GET):
            writer.writerow([
                r.date, r.receipt_number, r.efd_receipt_number,
                r.customer_name, r.tin, r.sales_amount,
            ])
        return response
