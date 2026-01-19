from rest_framework import viewsets, permissions
from .models import Branch, Category, Product, Stock, Purchase, Supplier, StockTransfer, PurchaseOrder, PurchaseOrderItem, Truck, TruckAllocation, StockAdjustment, GoodsReceivedNote, GRNItem, Driver, TruckMaintenance, DriverIssue
from .serializers import (
    BranchSerializer, CategorySerializer, ProductSerializer, 
    StockSerializer, PurchaseSerializer, SupplierSerializer, StockTransferSerializer,
    PurchaseOrderSerializer, PurchaseOrderItemSerializer, TruckSerializer, TruckAllocationSerializer, StockAdjustmentSerializer,
    GoodsReceivedNoteSerializer, GRNItemSerializer, DriverSerializer, TruckMaintenanceSerializer
)
import io
import csv
import openpyxl
from django.db import transaction
from rest_framework import status
from rest_framework.response import Response
from django.db.models import Q
from rest_framework.decorators import action
from django.http import HttpResponse
from django.views.generic import TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin

from apps.users.permissions import IsStoreManager, IsStoreKeeper, IsStockController, IsAfisaUgavi

class BranchViewSet(viewsets.ModelViewSet):
    queryset = Branch.objects.all()
    serializer_class = BranchSerializer
    permission_classes = [permissions.DjangoModelPermissions]

class CategoryViewSet(viewsets.ModelViewSet):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    permission_classes = [permissions.DjangoModelPermissions]

class ProductViewSet(viewsets.ModelViewSet):
    queryset = Product.objects.all()
    serializer_class = ProductSerializer
    permission_classes = [permissions.DjangoModelPermissions]
    filterset_fields = ['product_type', 'category']

    @action(detail=False, methods=['POST'], url_path='import')
    def import_products(self, request):
        file = request.FILES.get('file')
        if not file:
            return Response({"error": "No file uploaded"}, status=status.HTTP_400_BAD_REQUEST)

        file_name = file.name
        headers = []
        rows = []
        imported_count = 0
        errors = []

        try:
            print(f"Starting import for file: {file_name}")
            if file_name.endswith('.csv'):
                decoded_file = file.read().decode('utf-8')
                io_string = io.StringIO(decoded_file)
                reader = csv.DictReader(io_string)
                rows = list(reader)
                headers = reader.fieldnames or []
            elif file_name.endswith(('.xlsx', '.xls')):
                wb = openpyxl.load_workbook(file, data_only=True)
                sheet = wb.active
                # Get headers and strip them
                headers = [str(cell.value).strip() if cell.value else "" for cell in sheet[1]]
                for row_idx, row_data in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
                    row_dict = dict(zip(headers, row_data))
                    if any(row_dict.values()):  # Skip empty rows
                        rows.append(row_dict)
            else:
                return Response({"error": "Unsupported file format. Please upload CSV or Excel."}, status=status.HTTP_400_BAD_REQUEST)
            print(f"Read {len(rows)} rows from file. Headers: {headers}")
        except Exception as e:
            print(f"Error reading file: {str(e)}")
            return Response({"error": f"Failed to read file: {str(e)}"}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            for i, row in enumerate(rows):
                try:
                    # Case-insensitive row lookup helper
                    def get_case_insensitive(d, key, default=None):
                        low_key = key.lower()
                        for k, v in d.items():
                            if k and k.lower() == low_key:
                                return v
                        return default

                    category_name = get_case_insensitive(row, 'Category')
                    if not category_name:
                        errors.append(f"Row {i+2}: Missing Category")
                        continue
                        
                    category, _ = Category.objects.get_or_create(name=str(category_name).strip())

                    sku = get_case_insensitive(row, 'SKU')
                    sku = str(sku).strip() if sku else None

                    product_name = get_case_insensitive(row, 'Name')
                    if not product_name:
                        errors.append(f"Row {i+2}: Missing Product Name")
                        continue

                    def get_val(key, default=''):
                        val = get_case_insensitive(row, key)
                        return val if val is not None else default

                    # If SKU exists, try to find by SKU. If not, try to find by Name + Category to prevent duplicates
                    product = None
                    created = False
                    
                    if sku:
                        product, created = Product.objects.get_or_create(
                            sku=sku,
                            defaults={
                                'name': str(product_name).strip(),
                                'category': category,
                                'product_type': str(get_val('Type', 'product')).lower().strip(),
                                'cost': float(str(get_val('Cost', 0) or 0).replace(',', '')),
                                'price': float(str(get_val('Price', 0) or 0).replace(',', '')),
                                'weight': float(str(get_val('Weight (kg)', 0) or 0).replace(',', '')),
                                'description': str(get_val('Description', '')).strip(),
                            }
                        )
                    else:
                        # No SKU provided, check by Name and Category
                        product, created = Product.objects.get_or_create(
                            name=str(product_name).strip(),
                            category=category,
                            defaults={
                                'product_type': str(get_val('Type', 'product')).lower().strip(),
                                'cost': float(str(get_val('Cost', 0) or 0).replace(',', '')),
                                'price': float(str(get_val('Price', 0) or 0).replace(',', '')),
                                'weight': float(str(get_val('Weight (kg)', 0) or 0).replace(',', '')),
                                'description': str(get_val('Description', '')).strip(),
                            }
                        )

                    # Handle Opening Stock (Additive)
                    try:
                        raw_opening = get_val('Opening Stock', 0)
                        raw_low = get_val('Low Stock Alert', 10)
                        opening_stock = int(float(str(raw_opening or 0).replace(',', '')))
                        low_stock = int(float(str(raw_low or 10).replace(',', '')))
                    except (ValueError, TypeError):
                        opening_stock = 0
                        low_stock = 10
                    
                    # Ensure Stock record exists in the Main Branch for 'product' types
                    product_type = str(get_val('Type', 'product')).lower().strip()
                    if product_type == 'product':
                        branch, _ = Branch.objects.get_or_create(name="Main Branch")
                        stock_obj, s_created = Stock.objects.get_or_create(
                            product=product,
                            branch=branch,
                            defaults={
                                'quantity': opening_stock,
                                'low_stock_threshold': low_stock
                            }
                        )
                        if not s_created:
                            # If stock record already existed, ADD to it
                            stock_obj.quantity += opening_stock
                            stock_obj.save()

                    imported_count += 1
                except Exception as e:
                    errors.append(f"Row {i+2}: {str(e)}")

        return Response({
            "message": f"Processed {len(rows)} rows, successfully imported/found {imported_count} products.",
            "errors": errors,
            "detected_headers": headers
        }, status=status.HTTP_201_CREATED if not errors else status.HTTP_207_MULTI_STATUS)

class StockViewSet(viewsets.ModelViewSet):
    queryset = Stock.objects.all()
    serializer_class = StockSerializer
    permission_classes = [permissions.DjangoModelPermissions]
    filterset_fields = ['branch', 'product']

class SupplierViewSet(viewsets.ModelViewSet):
    queryset = Supplier.objects.all()
    serializer_class = SupplierSerializer
    permission_classes = [permissions.DjangoModelPermissions]

class PurchaseViewSet(viewsets.ModelViewSet):
    queryset = Purchase.objects.all()
    serializer_class = PurchaseSerializer
    permission_classes = [permissions.DjangoModelPermissions]

    def create(self, request, *args, **kwargs):
        payload = request.data.copy()
        serializer = self.get_serializer(data=payload)
        serializer.is_valid(raise_exception=True)
        
        with transaction.atomic():
            purchase = serializer.save()
            # Increase Stock
            stock, _ = Stock.objects.get_or_create(
                product=purchase.product, 
                branch=purchase.branch,
                defaults={'quantity': 0}
            )
            stock.quantity += purchase.quantity
            stock.save()

        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

class StockTransferViewSet(viewsets.ModelViewSet):
    queryset = StockTransfer.objects.all()
    serializer_class = StockTransferSerializer
    permission_classes = [permissions.DjangoModelPermissions]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        transfer = serializer.save()

        # Update Stocks
        with transaction.atomic():
            # Decrease Source
            source_stock, _ = Stock.objects.get_or_create(product=transfer.product, branch=transfer.from_branch)
            source_stock.quantity -= transfer.quantity
            source_stock.save()

            # Increase Dest
            dest_stock, _ = Stock.objects.get_or_create(product=transfer.product, branch=transfer.to_branch)
            dest_stock.quantity += transfer.quantity
            dest_stock.save()

        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

class PurchaseOrderViewSet(viewsets.ModelViewSet):
    queryset = PurchaseOrder.objects.all()
    serializer_class = PurchaseOrderSerializer
    permission_classes = [permissions.IsAuthenticated, IsAfisaUgavi]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @action(detail=True, methods=['POST'])
    def add_item(self, request, pk=None):
        po = self.get_object()
        product_id = request.data.get('product')
        quantity = int(request.data.get('quantity', 0))
        unit_cost = float(request.data.get('unit_cost', 0))
        
        product = Product.objects.get(pk=product_id)
        
        item = PurchaseOrderItem.objects.create(
            purchase_order=po,
            product=product,
            quantity=quantity,
            unit_cost=unit_cost,
            total_cost=quantity * unit_cost
        )
        
        # Update PO total
        po.total_amount = sum(i.total_cost for i in po.items.all())
        po.save()
        
        return Response(PurchaseOrderItemSerializer(item).data)

class TruckViewSet(viewsets.ModelViewSet):
    queryset = Truck.objects.all()
    serializer_class = TruckSerializer
    permission_classes = [permissions.IsAuthenticated, IsAfisaUgavi]

class TruckAllocationViewSet(viewsets.ModelViewSet):
    queryset = TruckAllocation.objects.all()
    serializer_class = TruckAllocationSerializer
    permission_classes = [permissions.IsAuthenticated, IsAfisaUgavi]

class GoodsReceivedNoteViewSet(viewsets.ModelViewSet):
    queryset = GoodsReceivedNote.objects.all()
    serializer_class = GoodsReceivedNoteSerializer
    # Stock Controller creates GRN (as per original requirements). Store Keeper verifies in UI.
    permission_classes = [permissions.IsAuthenticated, IsStockController]

    def perform_create(self, serializer):
        grn = serializer.save(created_by=self.request.user)
        
        # If successfully created, and PO is linked, update PO status to 'received'
        if grn.purchase_order:
            grn.purchase_order.status = 'received'
            grn.purchase_order.save()

    @action(detail=True, methods=['post'])
    def add_item(self, request, pk=None):
        grn = self.get_object()
        product_id = request.data.get('product')
        qty = request.data.get('quantity_received')
        
        try:
            product = Product.objects.get(id=product_id)
            item = GRNItem.objects.create(
                grn=grn,
                product=product,
                quantity_received=qty,
                remarks=request.data.get('remarks', '')
            )
            
            # UPDATE STOCK
            stock, created = Stock.objects.get_or_create(
                product=product, 
                branch=grn.branch,
                defaults={'quantity': 0}
            )
            stock.quantity += int(qty)
            stock.save()

            return Response(GRNItemSerializer(item).data)
        except Exception as e:
            return Response({'error': str(e)}, status=400)

class GRNListView(LoginRequiredMixin, TemplateView):
    template_name = 'inventory/grn_list.html'

class GRNCreateView(LoginRequiredMixin, TemplateView):
    template_name = 'inventory/grn_form.html'


class InventoryListView(LoginRequiredMixin, TemplateView):
    template_name = 'inventory/stock_list.html'

class BranchListView(LoginRequiredMixin, TemplateView):
    template_name = 'inventory/branch_list.html'

class BranchCreateView(LoginRequiredMixin, TemplateView):
    template_name = 'inventory/branch_create.html'

class SupplierListView(LoginRequiredMixin, TemplateView):
    template_name = 'inventory/supplier_list.html'

class PurchaseListView(LoginRequiredMixin, TemplateView):
    template_name = 'inventory/purchase_list.html'

class PurchaseCreateView(LoginRequiredMixin, TemplateView):
    template_name = 'inventory/purchase_create.html'

class RecentPurchaseListView(LoginRequiredMixin, TemplateView):
    template_name = 'inventory/recent_purchases.html'

class ProductListView(LoginRequiredMixin, TemplateView):
    template_name = 'product_list.html'
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Products'
        context['resource'] = 'products'
        return context

class ProductCreateView(LoginRequiredMixin, TemplateView):
    template_name = 'product_list.html'
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Create Product'
        context['resource'] = 'products'
        return context

class ProductImportView(LoginRequiredMixin, TemplateView):
    template_name = 'product_import.html'

class CategoryListView(LoginRequiredMixin, TemplateView):
    template_name = 'category_list.html'
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Categories'
        context['resource'] = 'categories'
        return context

class ServicesListView(LoginRequiredMixin, TemplateView):
    template_name = 'product_list.html'
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Services'
        context['resource'] = 'products?product_type=service'
        return context

class StockManagementView(LoginRequiredMixin, TemplateView):
    template_name = 'inventory_management.html'
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Stock Management'
        context['resource'] = 'stocks'
        return context

class InventoryTransferView(LoginRequiredMixin, TemplateView):
    template_name = 'inventory_transfer.html'
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Transfer'
        context['resource'] = 'transfers'
        return context

class InventoryHealthView(LoginRequiredMixin, TemplateView):
    template_name = 'inventory_health.html'
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Inventory Health'
        context['resource'] = 'stocks'
        return context

class InventoryAgingView(LoginRequiredMixin, TemplateView):
    template_name = 'inventory_aging.html'
    def get_context_data(self, **kwargs):
        from apps.inventory.models import Stock, Purchase
        from django.utils import timezone
        context = super().get_context_data(**kwargs)
        
        stocks = Stock.objects.select_related('product', 'branch').all()
        now = timezone.now()
        
        aging_data = []
        for stock in stocks:
            # Find the latest purchase for this product in this branch
            last_purchase = Purchase.objects.filter(
                product=stock.product, 
                branch=stock.branch
            ).order_by('-date_purchased').first()
            
            last_date = last_purchase.date_purchased if last_purchase else stock.product.created_at
            days = (now - last_date).days
            
            aging_data.append({
                'product_name': stock.product.name,
                'branch_name': stock.branch.name,
                'quantity': stock.quantity,
                'last_date': last_date,
                'days': days,
                'status': 'danger' if days > 90 else 'warning' if days > 60 else 'info' if days > 30 else 'success'
            })
            
        context['aging_data'] = sorted(aging_data, key=lambda x: x['days'], reverse=True)
        context['title'] = 'Inventory Aging'
        context['resource'] = 'stocks'
        return context

class ABCAnalysisView(LoginRequiredMixin, TemplateView):
    template_name = 'inventory_abc.html'
    def get_context_data(self, **kwargs):
        from apps.sales.models import SaleItem
        from django.db.models import Sum, F
        context = super().get_context_data(**kwargs)
        
        # Calculate total revenue per product
        product_revenue = SaleItem.objects.values(
            'product__id', 'product__name'
        ).annotate(
            total_revenue=Sum(F('quantity') * F('price_at_sale'))
        ).order_by('-total_revenue')
        
        total_sum = sum(item['total_revenue'] for item in product_revenue) or 1
        
        cumulative_revenue = 0
        abc_data = []
        
        for item in product_revenue:
            cumulative_revenue += item['total_revenue']
            percentage = (cumulative_revenue / total_sum) * 100
            
            if percentage <= 70:
                abc_class = 'A'
                label = 'danger' # High importance
            elif percentage <= 90:
                abc_class = 'B'
                label = 'warning'
            else:
                abc_class = 'C'
                label = 'success'
                
            abc_data.append({
                'product_name': item['product__name'],
                'revenue': item['total_revenue'],
                'class': abc_class,
                'label': label
            })
            
        context['abc_data'] = abc_data
        context['title'] = 'ABC Analysis'
        context['resource'] = 'stocks'
        return context

class ProfitabilityReportView(LoginRequiredMixin, TemplateView):
    template_name = 'inventory_profitability.html'
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Profitability Report'
        context['resource'] = 'stocks'
        return context

from django.contrib.auth.decorators import login_required

@login_required
def download_product_template(request):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="product_import_template.csv"'

    writer = csv.writer(response)
    # Headers
    writer.writerow(['Name', 'SKU', 'Category', 'Type', 'Cost', 'Price', 'Weight (kg)', 'Description', 'Opening Stock', 'Low Stock Alert'])
    # Sample Row
    writer.writerow(['Hammer', 'HMR-001', 'Tools', 'product', '15000', '25000', '1.5', 'Heavy duty steel hammer', '50', '5'])

    return response

class StockAdjustmentViewSet(viewsets.ModelViewSet):
    queryset = StockAdjustment.objects.all().order_by('-created_at')
    serializer_class = StockAdjustmentSerializer
    permission_classes = [permissions.DjangoModelPermissions]

    def perform_create(self, serializer):
        # Set the user to the current request user if any
        serializer.save(user=self.request.user if self.request.user.is_authenticated else None)

    def create(self, request, *args, **kwargs):
        payload = request.data.copy()
        serializer = self.get_serializer(data=payload)
        serializer.is_valid(raise_exception=True)
        
        with transaction.atomic():
            adjustment = serializer.save(user=request.user if request.user.is_authenticated else None)
            
            # Update Stock
            stock, _ = Stock.objects.get_or_create(
                product=adjustment.product, 
                branch=adjustment.branch,
                defaults={'quantity': 0}
            )
            
            if adjustment.adjustment_type == 'addition':
                stock.quantity += adjustment.quantity
            elif adjustment.adjustment_type == 'deduction':
                stock.quantity -= adjustment.quantity
            elif adjustment.adjustment_type == 'correction':
                stock.quantity = adjustment.quantity
            
            stock.save()

        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)
    
class PurchaseOrderListView(LoginRequiredMixin, TemplateView):
    template_name = 'inventory/purchase_order_list.html'

class PurchaseOrderCreateView(LoginRequiredMixin, TemplateView):
    template_name = 'inventory/purchase_order_form.html'

class TruckListView(LoginRequiredMixin, TemplateView):
    template_name = 'inventory/truck_list.html'

class StockAdjustmentView(LoginRequiredMixin, TemplateView):
    template_name = 'inventory/stock_adjustment.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Stock Adjustment'
        context['resource'] = 'stocks'
        return context

class DriverViewSet(viewsets.ModelViewSet):
    queryset = Driver.objects.all()
    serializer_class = DriverSerializer
    permission_classes = [permissions.IsAuthenticated, IsStoreManager]

class TruckMaintenanceViewSet(viewsets.ModelViewSet):
    queryset = TruckMaintenance.objects.all().order_by('-date')
    serializer_class = TruckMaintenanceSerializer
    permission_classes = [permissions.IsAuthenticated, IsStoreManager]

    def perform_create(self, serializer):
        serializer.save(recorded_by=self.request.user)

class DriverListView(LoginRequiredMixin, TemplateView):
    template_name = 'inventory/driver_list.html'

class TruckMaintenanceView(LoginRequiredMixin, TemplateView):
    template_name = 'inventory/truck_maintenance.html'
