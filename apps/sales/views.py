from django.shortcuts import render
from rest_framework import viewsets, permissions
from apps.users.permissions import IsSales
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import Sale, SaleItem, Transaction, Customer, Vehicle, Quotation, QuotationItem
from .serializers import SaleSerializer, SaleItemSerializer, TransactionSerializer, CustomerSerializer, VehicleSerializer, QuotationSerializer, QuotationItemSerializer

import io
import csv
from django.http import HttpResponse
from django.views.generic import TemplateView, DetailView
from django.contrib.auth.mixins import LoginRequiredMixin
from .views_report import CommissionReportView

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
        return render_to_pdf('sales/pdf_invoice.html', {'sale': sale})

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
    permission_classes = [permissions.IsAuthenticated, IsSales] # Sales Reps need to see vehicles for dispatch? Logic says yes.

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


