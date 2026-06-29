from django.shortcuts import render
from rest_framework import viewsets, permissions
from apps.users.permissions import IsSales, CanManageVehicles
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import Sale, SaleItem, Transaction, Customer, Vehicle, Quotation, QuotationItem
from .serializers import SaleSerializer, SaleItemSerializer, TransactionSerializer, CustomerSerializer, VehicleSerializer, QuotationSerializer, QuotationItemSerializer

import io
import csv
from decimal import Decimal, ROUND_HALF_UP
from django.http import HttpResponse
from django.views.generic import TemplateView, DetailView
from django.contrib.auth.mixins import LoginRequiredMixin
from .views_report import CommissionReportView


def _company_ctx():
    """Company header data + currency + tax rate for PDF documents."""
    from apps.core.models import SystemSettings
    from apps.inventory.models import Branch
    from django.templatetags.static import static
    s = SystemSettings.objects.first()
    logo_url = s.logo.url if (s and s.logo) else static('img/logo.png')
    branch_names = list(Branch.objects.order_by('name').values_list('name', flat=True))[:10]
    company = {
        'name': getattr(s, 'company_name', 'Umoja Hardware') if s else 'Umoja Hardware',
        'address': getattr(s, 'address', '') if s else '',
        'phone': getattr(s, 'phone', '') if s else '',
        'email': getattr(s, 'email', '') if s else '',
        'website': getattr(s, 'website', '') if s else '',
        'tin': getattr(s, 'tin', '') if s else '',
        'vrn': getattr(s, 'vrn', '') if s else '',
        'logo_url': logo_url,
        'branches': ' | '.join(branch_names),
    }
    currency = (getattr(s, 'currency', 'TZS') if s else 'TZS') or 'TZS'
    tax_rate = (getattr(s, 'tax_rate', Decimal('18')) if s else Decimal('18'))
    return company, currency, tax_rate


def _money_breakdown(total, tax_rate):
    """Treat `total` as the VAT-inclusive final amount; back-calculate the
    pre-tax subtotal and VAT so the Total Amount never changes."""
    total = Decimal(str(total or 0))
    rate = Decimal(str(tax_rate or 0)) / Decimal('100')
    if rate > 0:
        subtotal_ex = (total / (1 + rate)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    else:
        subtotal_ex = total
    tax_amount = (total - subtotal_ex).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return subtotal_ex, tax_amount, total


def _person(user):
    if not user:
        return ''
    return user.get_full_name() or user.username


def _amount_in_words(amount, currency='TZS'):
    """Render a money amount as words, e.g. 'Three Hundred Fifty-Three
    Million Tanzanian Shillings Only'. Handles up to billions."""
    amount = Decimal(str(amount or 0))
    whole = int(amount.quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    ones = ['', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight',
            'Nine', 'Ten', 'Eleven', 'Twelve', 'Thirteen', 'Fourteen', 'Fifteen',
            'Sixteen', 'Seventeen', 'Eighteen', 'Nineteen']
    tens = ['', '', 'Twenty', 'Thirty', 'Forty', 'Fifty', 'Sixty', 'Seventy', 'Eighty', 'Ninety']

    def three(n):
        s = ''
        h, r = n // 100, n % 100
        if h:
            s += ones[h] + ' Hundred'
        if r:
            if s:
                s += ' '
            if r < 20:
                s += ones[r]
            else:
                s += tens[r // 10] + ('-' + ones[r % 10] if r % 10 else '')
        return s

    if whole == 0:
        words = 'Zero'
    else:
        parts, n = [], whole
        for val, name in [(10**9, 'Billion'), (10**6, 'Million'), (10**3, 'Thousand'), (1, '')]:
            if n >= val:
                seg = three(n // val)
                n = n % val
                parts.append(seg + (' ' + name if name else ''))
        words = ' '.join(p for p in parts if p).strip()

    cur = 'Tanzanian Shillings' if (currency or 'TZS').upper() == 'TZS' else currency
    result = '%s %s' % (words, cur)
    return result + ' Only'

class SaleViewSet(viewsets.ModelViewSet):
    queryset = Sale.objects.all().order_by('-created_at')
    serializer_class = SaleSerializer
    permission_classes = [permissions.DjangoModelPermissions]

    def get_queryset(self):
        queryset = super().get_queryset()
        status = self.request.query_params.get('status')
        if status == 'credit':
            from django.db.models import Sum, F, Coalesce
            queryset = queryset.annotate(
                total_paid=Coalesce(Sum('transactions__amount'), 0.0)
            ).filter(total_paid__lt=F('total_amount'))
        return queryset

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def perform_destroy(self, instance):
        # Restore stock if dispatched
        if instance.status == 'dispatched':
            from apps.inventory.models import Stock
            for item in instance.items.all():
                try:
                    stock = Stock.objects.get(product=item.product, branch=instance.branch)
                    stock.quantity += item.quantity
                    stock.save()
                except Stock.DoesNotExist:
                    pass # Should not happen, but safe to ignore if stock record gone
        
        instance.delete()
    @action(detail=True, methods=['POST'])
    def approve(self, request, pk=None):
        from rest_framework.response import Response
        from rest_framework import status
        sale = self.get_object()
        if sale.status != 'pending':
            return Response({"error": "Only pending orders can be approved"}, status=status.HTTP_400_BAD_REQUEST)
        
        # Dispatch Manager is no longer manually assigned.
        # It is open to all Store Managers.
        
        from django.utils import timezone
        sale.status = 'approved'
        sale.approved_by = request.user
        sale.approved_at = timezone.now()
        sale.dispatch_manager = None # Ensure it is null so any manager can pick it up (or logic elsewhere handles it)
        sale.save()
        
        return Response({
            "message": f"Order #{sale.invoice_number} approved and sent to Dispatch.", 
            "status": sale.status
        })

    @action(detail=True, methods=['POST'])
    def decline(self, request, pk=None):
        sale = self.get_object()
        if sale.status != 'pending':
            from rest_framework.response import Response
            from rest_framework import status
            return Response({"error": "Only pending orders can be declined"}, status=status.HTTP_400_BAD_REQUEST)
        
        sale.status = 'cancelled'
        sale.save()
        
        from rest_framework.response import Response
        return Response({"message": "Order declined successfully", "status": sale.status})

    @action(detail=True, methods=['POST'], permission_classes=[permissions.IsAuthenticated])
    def dispatch_order(self, request, pk=None):
        if not (request.user.is_superuser or request.user.is_manager or request.user.is_admin_role):
             from rest_framework.response import Response
             from rest_framework import status
             return Response({"error": "You do not have permission to dispatch orders"}, status=status.HTTP_403_FORBIDDEN)

        sale = self.get_object()
        if sale.status != 'approved':
            from rest_framework.response import Response
            from rest_framework import status
            return Response({"error": "Only approved orders can be dispatched"}, status=status.HTTP_400_BAD_REQUEST)
        
        store_keeper_id = request.data.get('store_keeper')
        vehicle_id = request.data.get('vehicle_id')
        lorry_info = request.data.get('lorry_info')
        
        if not store_keeper_id:
            from rest_framework.response import Response
            from rest_framework import status
            return Response({"error": "Store keeper is required"}, status=status.HTTP_400_BAD_REQUEST)

        if not vehicle_id and not lorry_info:
            from rest_framework.response import Response
            from rest_framework import status
            return Response({"error": "Vehicle or Lorry Info is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        from django.contrib.auth import get_user_model
        User = get_user_model()
        try:
            store_keeper = User.objects.get(id=store_keeper_id)
        except User.DoesNotExist:
            from rest_framework.response import Response
            from rest_framework import status
            return Response({"error": "Invalid store keeper"}, status=status.HTTP_400_BAD_REQUEST)

        # 1. Deduct Stock
        from apps.inventory.models import Stock
        errors = []
        for item in sale.items.all():
            try:
                stock = Stock.objects.get(product=item.product, branch=sale.branch)
                if stock.quantity < item.quantity:
                    errors.append(f"Insufficient stock for {item.product.name}")
                stock.quantity -= item.quantity
                stock.save()
            except Stock.DoesNotExist:
                errors.append(f"No stock record for {item.product.name} at this branch")

        if errors:
            from rest_framework.response import Response
            from rest_framework import status
            return Response({"error": errors}, status=status.HTTP_400_BAD_REQUEST)

        # 2. Update Sale Status
        sale.status = 'dispatched'
        sale.store_keeper = store_keeper
        if vehicle_id:
            sale.vehicle_id = vehicle_id
            v = Vehicle.objects.get(id=vehicle_id)
            v.status = 'busy'
            v.save()
            
            if not lorry_info:
                 sale.lorry_info = f"{v.registration_number} ({v.driver_name})"
            else:
                 sale.lorry_info = lorry_info
        else:
            sale.lorry_info = lorry_info
        sale.save()
        
        # 3. Generate PDF (Mock for now, will implement utility next)
        from rest_framework.response import Response
        return Response({
            "message": "Order dispatched successfully and stock deducted",
            "invoice_url": f"/api/sales/{sale.id}/receipt/",
            "delivery_note_url": f"/api/sales/{sale.id}/delivery_note/"
        })

    @action(detail=True, methods=['GET'])
    def receipt(self, request, pk=None):
        sale = self.get_object()
        from .utils import render_to_pdf
        company, currency, tax_rate = _company_ctx()
        items = [{
            'code': getattr(it.product, 'sku', '') or '',
            'description': it.product.name,
            'qty': it.quantity,
            'uom': 'PCS',
            'price': it.price_at_sale,
            'total': it.subtotal,
        } for it in sale.items.all()]
        subtotal_ex, tax_amount, total = _money_breakdown(sale.total_amount, tax_rate)
        ctx = {
            'doc': {
                'type': 'TAX INVOICE', 'number_label': 'Invoice No.',
                'number': sale.invoice_number, 'date': sale.created_at,
                'valid_until': None, 'page': '1/1',
                'branch': sale.branch.name if sale.branch else '',
                'contact': _person(sale.user),
                'authorized_by': _person(sale.approved_by),
                'status': sale.status, 'payment_term': 'Cash Basis',
                'delivery_label': '', 'authorised_block': True,
                'auth_note': 'To be printed, signed and stamped before issuing to the customer.',
            },
            'company': company,
            'customer': {
                'name': sale.customer.name if sale.customer else sale.customer_name,
                'phone': sale.customer.phone if sale.customer else '',
                'address': sale.customer.address if sale.customer else '',
                'tin': '',
            },
            'items': items, 'currency': currency,
            'subtotal_ex': subtotal_ex, 'tax_rate': tax_rate,
            'tax_amount': tax_amount, 'total': total,
            'amount_words': _amount_in_words(total, currency),
        }
        return render_to_pdf('sales/pdf_document.html', ctx)

    @action(detail=True, methods=['GET'])
    def delivery_note(self, request, pk=None):
        sale = self.get_object()
        from .utils import render_to_pdf
        return render_to_pdf('sales/pdf_delivery_note.html', {'sale': sale})

class POSView(LoginRequiredMixin, TemplateView):
    template_name = 'sales/pos.html'

class SaleListView(LoginRequiredMixin, TemplateView):
    template_name = 'sales/sale_list.html'

class CreditSaleListView(LoginRequiredMixin, TemplateView):
    template_name = 'sales/credit_sales.html'

class RecentSaleListView(LoginRequiredMixin, TemplateView):
    template_name = 'sales/recent_sales.html'

class CustomerListView(LoginRequiredMixin, TemplateView):
    template_name = 'sales/customer_list.html'

class CustomerCreateView(LoginRequiredMixin, TemplateView):
    template_name = 'sales/customer_create.html'

class CustomerImportView(LoginRequiredMixin, TemplateView):
    template_name = 'sales/customer_import.html'

from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin

class OrderManagementView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = 'sales/order_management.html'

    def test_func(self):
        return self.request.user.is_superuser or self.request.user.is_admin_role

class DispatchDashboardView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = 'sales/dispatch_dashboard.html'

    def test_func(self):
        return self.request.user.is_superuser or self.request.user.is_manager or self.request.user.is_admin_role

class SaleItemViewSet(viewsets.ModelViewSet):
    queryset = SaleItem.objects.all()
    serializer_class = SaleItemSerializer
    permission_classes = [permissions.DjangoModelPermissions]

class VehicleViewSet(viewsets.ModelViewSet):
    queryset = Vehicle.objects.all().order_by('registration_number')
    serializer_class = VehicleSerializer
    permission_classes = [permissions.IsAuthenticated, CanManageVehicles]

    @action(detail=True, methods=['post'])
    def return_vehicle(self, request, pk=None):
        from rest_framework.response import Response
        vehicle = self.get_object()
        vehicle.status = 'active'
        vehicle.save()
        return Response({'status': 'active', 'message': 'Vehicle marked as available'})

class VehicleManagementView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = 'sales/vehicle_list.html'

    def test_func(self):
        return self.request.user.is_superuser or self.request.user.is_manager or self.request.user.is_admin_role


class TransactionViewSet(viewsets.ModelViewSet):
    queryset = Transaction.objects.all()
    serializer_class = TransactionSerializer
    permission_classes = [permissions.DjangoModelPermissions]


class CustomerViewSet(viewsets.ModelViewSet):
    queryset = Customer.objects.all()
    serializer_class = CustomerSerializer
    permission_classes = [permissions.DjangoModelPermissions]

    @action(detail=False, methods=['POST'], url_path='import')
    def import_customers(self, request):
        file = request.FILES.get('file')
        if not file:
            return Response({"error": "No file uploaded"}, status=status.HTTP_400_BAD_REQUEST)

        decoded_file = file.read().decode('utf-8')
        io_string = io.StringIO(decoded_file)
        reader = csv.DictReader(io_string)

        imported_count = 0
        errors = []

        for row in reader:
            try:
                # Basic validation or defaults
                name = row.get('Name')
                if not name:
                    continue

                Customer.objects.update_or_create(
                    name=name,
                    defaults={
                        'phone': row.get('Phone', ''),
                        'email': row.get('Email', ''),
                        'address': row.get('Address', ''),
                    }
                )
                imported_count += 1
            except Exception as e:
                errors.append(f"Error importing {row.get('Name')}: {str(e)}")

        from rest_framework.response import Response
        from rest_framework import status
        return Response({
            "message": f"Successfully imported {imported_count} customers",
            "errors": errors
        }, status=status.HTTP_201_CREATED if not errors else status.HTTP_207_MULTI_STATUS)

class QuotationViewSet(viewsets.ModelViewSet):
    queryset = Quotation.objects.all().order_by('-created_at')
    serializer_class = QuotationSerializer
    permission_classes = [permissions.IsAuthenticated, IsSales]

    def perform_create(self, serializer):
        # Assign current user and ensure Branch is set (defaulting to first or user's branch logic)
        branch = self.request.user.branch
        if not branch:
             from apps.inventory.models import Branch
             branch = Branch.objects.first()
        serializer.save(created_by=self.request.user, branch=branch)

    @action(detail=True, methods=['GET'])
    def pdf(self, request, pk=None):
        quote = self.get_object()
        from .utils import render_to_pdf
        company, currency, tax_rate = _company_ctx()
        items = [{
            'code': getattr(it.product, 'sku', '') or '',
            'description': it.product.name,
            'qty': it.quantity,
            'uom': 'PCS',
            'price': it.unit_price,
            'total': it.total_price,
        } for it in quote.items.all()]
        subtotal_ex, tax_amount, total = _money_breakdown(quote.total_amount, tax_rate)
        ctx = {
            'doc': {
                'type': 'QUOTATION', 'number_label': 'Quotation No.',
                'number': 'QT-%s' % quote.id, 'date': quote.created_at,
                'valid_until': quote.valid_until, 'page': '1/1',
                'branch': quote.branch.name if quote.branch else '',
                'contact': _person(quote.created_by),
                'authorized_by': _person(quote.created_by),
                'status': None, 'payment_term': 'Valid as quoted',
                'delivery_label': '', 'authorised_block': False,
            },
            'company': company,
            'customer': {
                'name': quote.customer.name if quote.customer else quote.customer_name,
                'phone': quote.customer.phone if quote.customer else '',
                'address': quote.customer.address if quote.customer else '',
                'tin': '',
            },
            'items': items, 'currency': currency,
            'subtotal_ex': subtotal_ex, 'tax_rate': tax_rate,
            'tax_amount': tax_amount, 'total': total,
            'amount_words': _amount_in_words(total, currency),
        }
        return render_to_pdf('sales/pdf_document.html', ctx)

class QuotationListView(LoginRequiredMixin, TemplateView):
    template_name = 'sales/quotation_list.html'

class QuotationCreateView(LoginRequiredMixin, TemplateView):
    template_name = 'sales/quotation_create.html'



from django.contrib.auth.decorators import login_required

@login_required
def download_customer_template(request):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="customer_import_template.csv"'

    writer = csv.writer(response)
    writer.writerow(['Name', 'Phone', 'Email', 'Address'])
    writer.writerow(['John Doe', '1234567890', 'john@example.com', '123 Main St, City'])


