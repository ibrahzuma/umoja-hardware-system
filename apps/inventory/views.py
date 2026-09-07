from rest_framework import viewsets, permissions
from .models import Branch, Category, Product, Stock, Purchase, Supplier, StockTransfer, PurchaseOrder, PurchaseOrderItem, Truck, TruckAllocation, StockAdjustment, GoodsReceivedNote, GRNItem, Driver, TruckMaintenance, TruckCost, DriverIssue, DeliveryCheck, DeliveryCheckItem
from .serializers import (
    BranchSerializer, CategorySerializer, ProductSerializer,
    StockSerializer, PurchaseSerializer, SupplierSerializer, StockTransferSerializer,
    PurchaseOrderSerializer, PurchaseOrderItemSerializer, TruckSerializer, TruckAllocationSerializer, StockAdjustmentSerializer,
    GoodsReceivedNoteSerializer, GRNItemSerializer, DriverSerializer, TruckMaintenanceSerializer, TruckCostSerializer
)
import io
import csv
import json
import openpyxl
from datetime import date
from decimal import Decimal
from django.db import transaction
from rest_framework import status
from rest_framework.response import Response
from django.db.models import Q, Sum
from django.contrib.auth import get_user_model
from rest_framework.decorators import action
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.views.generic import TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin

from apps.core.models import SystemActivity
from apps.core.notify import notify
from . import movements
from apps.users.permissions import IsStoreManager, IsStoreKeeper, IsStockController, IsAfisaUgavi, CanManageFleet, CanHandleGRN, CanManagePurchaseOrders, IsAdminOrSuperUser

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
            # total_cost is computed (read-only in the serializer) so clients
            # only send quantity + unit_cost.
            total_cost = serializer.validated_data['quantity'] * serializer.validated_data['unit_cost']
            purchase = serializer.save(total_cost=total_cost)
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

def _admin_recipients():
    """Users who decide on short deliveries: superusers and the admin role."""
    from apps.users.models import User
    return User.objects.filter(
        Q(is_superuser=True) | Q(role='admin') | Q(groups__name__in=['Admin', 'admin'])
    ).distinct()


def _receive_delivery(check):
    """Take one approved delivery round into stock.

    Adds each line's delivered quantity to the branch stock and moves the same
    amount onto the item's cumulative `received_quantity`, so what is still
    owed stays correct across rounds. Call inside a transaction; safe to call
    only once per round (guarded by the round's decision state).
    """
    po = check.purchase_order
    for line in check.lines.select_related('item__product'):
        if line.delivered_quantity <= 0:
            continue
        item = line.item
        stock, _ = Stock.objects.get_or_create(
            product=item.product, branch=po.branch, defaults={'quantity': 0}
        )
        stock.quantity += line.delivered_quantity
        stock.save()
        item.received_quantity = (item.received_quantity or 0) + line.delivered_quantity
        item.save(update_fields=['received_quantity'])


def _offer_supplier_credit(po, actor):
    """Ask the cash desk to spend the supplier's credit on a new order.

    An overpayment is only useful if somebody remembers it, so the moment an
    order is raised for a supplier holding our money the cashiers are told —
    with the figure, so they know whether it covers the order or only part.
    """
    if po.supplier_id is None:
        return
    from apps.finance.credit import spendable_credit
    from apps.core.notify import notify

    credit = spendable_credit(po.supplier_id)
    if credit <= 0:
        return

    total = po.total_amount or 0
    covers = credit >= total > 0
    User = get_user_model()
    cashiers = User.objects.filter(is_active=True).filter(
        Q(role='cashier') | Q(groups__name='Cashier')
    ).distinct()
    for cashier in cashiers:
        notify(
            cashier,
            f"{po.supplier} holds credit — apply it to PO #{po.id}",
            f"{po.supplier} is holding {credit} of ours."
            + (f" That covers PO #{po.id} in full." if covers
               else f" PO #{po.id} is {total}; the rest would still need paying."),
            url='/finance/supplier-payments/',
            level='info',
        )


class PurchaseOrderViewSet(viewsets.ModelViewSet):
    queryset = PurchaseOrder.objects.all()
    serializer_class = PurchaseOrderSerializer
    permission_classes = [permissions.IsAuthenticated, CanManagePurchaseOrders]

    def perform_create(self, serializer):
        po = serializer.save(created_by=self.request.user)
        _offer_supplier_credit(po, self.request.user)

    @action(detail=False, methods=['GET'])
    def supplier_credit(self, request):
        """What a supplier already holds of ours, for the order form.

        Afisa Ugavi sees this while choosing the supplier: an overpayment on an
        earlier order is the next order's deposit, and they should know it is
        there before committing to fresh money.

        GET ?supplier=<id>
        """
        supplier_id = (request.query_params.get('supplier') or '').strip()
        if not supplier_id.isdigit():
            return Response({'detail': 'supplier must be an id.'}, status=status.HTTP_400_BAD_REQUEST)

        from apps.finance.credit import available_credit, pending_credit_use, spendable_credit
        sid = int(supplier_id)
        supplier = Supplier.objects.filter(id=sid).first()
        return Response({
            'supplier': sid,
            'supplier_name': supplier.name if supplier else '',
            'available': str(available_credit(sid)),
            'pending_use': str(pending_credit_use(sid)),
            'spendable': str(spendable_credit(sid)),
        })

    def destroy(self, request, *args, **kwargs):
        po = self.get_object()
        if po.status in ('received', 'partial'):
            return Response(
                {'detail': 'Cannot delete an order whose goods were already received (stock has been updated).'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=['POST'], permission_classes=[permissions.IsAuthenticated, IsAfisaUgavi])
    def confirm(self, request, pk=None):
        """Afisa Ugavi confirms goods have arrived — the order goes to the Store
        Manager to cross-check them.

        Also used for the balance of a partially received order: when the rest
        of the goods turn up he confirms again and a fresh check round starts.
        """
        po = self.get_object()
        if po.status not in ('draft', 'sent', 'partial'):
            return Response({'detail': 'Only draft, sent or partially received orders can be confirmed.'},
                            status=status.HTTP_400_BAD_REQUEST)

        is_balance = po.status == 'partial'
        po.status = 'awaiting_check'
        po.save(update_fields=['status'])

        detail = ('Balance delivery confirmed and sent to the Store Manager for cross-check.'
                  if is_balance else
                  'Order confirmed and sent to the Store Manager for cross-check.')
        return Response({'detail': detail, 'status': po.status}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['POST'], permission_classes=[permissions.IsAuthenticated, IsStoreManager])
    def cross_check(self, request, pk=None):
        """Store Manager records what actually arrived, item by item.

        Everything owed arrived -> straight to Received and into stock.
        Anything short -> nothing goes to stock yet; the round is flagged and
        Afisa Ugavi is asked to explain it, after which an Admin decides.
        """
        from django.utils import timezone
        po = self.get_object()
        if po.status != 'awaiting_check':
            return Response({'detail': 'This order is not awaiting a store cross-check.'},
                            status=status.HTTP_400_BAD_REQUEST)

        delivered_map = {}
        notes_map = {}
        for ln in (request.data.get('items') or []):
            try:
                item_id = int(ln.get('item'))
            except (TypeError, ValueError):
                continue
            try:
                delivered_map[item_id] = max(0, int(ln.get('delivered_quantity') or 0))
            except (TypeError, ValueError):
                delivered_map[item_id] = 0
            notes_map[item_id] = (ln.get('note') or '').strip()[:255]
        note = (request.data.get('store_note') or '').strip()

        pending = po.outstanding_items()
        if not pending:
            return Response({'detail': 'Every item on this order has already been received.'},
                            status=status.HTTP_400_BAD_REQUEST)

        # Every outstanding item must be counted — this screen is a per-item
        # check, so a missing line means the Store Manager has not finished.
        unchecked = [it for it in pending if it.id not in delivered_map]
        if unchecked:
            return Response(
                {'detail': f'Check every item before submitting — {len(unchecked)} still not counted.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        mismatch = False
        with transaction.atomic():
            check = DeliveryCheck.objects.create(
                purchase_order=po,
                round_number=po.next_round_number,
                checked_by=request.user,
                store_note=note,
            )
            for it in pending:
                expected = it.outstanding
                # Clamp over-delivery: accepting more than was ordered would
                # leave a negative balance and the round loop would not settle.
                delivered = min(delivered_map.get(it.id, 0), expected)
                DeliveryCheckItem.objects.create(
                    delivery_check=check, item=it, expected_quantity=expected,
                    delivered_quantity=delivered, note=notes_map.get(it.id, ''),
                )
                it.delivered_quantity = delivered
                it.save(update_fields=['delivered_quantity'])
                if delivered != expected:
                    mismatch = True

            check.has_discrepancy = mismatch
            check.decision = 'pending_comment' if mismatch else 'complete'
            check.save(update_fields=['has_discrepancy', 'decision'])

            po.checked_by = request.user
            po.checked_at = timezone.now()
            po.store_note = note
            po.has_discrepancy = mismatch
            po.status = 'discrepancy' if mismatch else 'received'

            if not mismatch:
                # Full delivery needs no admin sign-off — receive it now.
                _receive_delivery(check)

            po.save()

        if mismatch:
            supplier = po.supplier.name if po.supplier else 'supplier'
            notify(
                po.created_by,
                title=f"Delivery discrepancy on PO-{po.id:05d}",
                message=(f"The Store Manager found that the delivery for PO-{po.id:05d} ({supplier}) "
                         f"does not match what was ordered. Please open the order and add a comment "
                         f"explaining why the goods are not complete — it then goes to the Admin."),
                url='/inventory/purchase-orders/',
                level='warning',
            )
            SystemActivity.objects.create(
                user=request.user, activity_type='purchase',
                description=f"Delivery discrepancy flagged on PO-{po.id:05d} at store cross-check",
                icon_class='bi-exclamation-triangle',
            )

        return Response({
            'detail': ('Cross-check saved. Shortfall flagged — Afisa Ugavi has been asked to comment. '
                       'Nothing has been added to stock yet.'
                       if mismatch else
                       'Cross-check saved. Delivery matches the order; stock updated.'),
            'status': po.status, 'has_discrepancy': mismatch, 'round': check.round_number,
        }, status=status.HTTP_200_OK)

    @action(detail=True, methods=['POST'], permission_classes=[permissions.IsAuthenticated, IsAfisaUgavi])
    def comment(self, request, pk=None):
        """Afisa Ugavi explains why a flagged delivery is incomplete, which sends
        it on to an Admin to confirm or reject."""
        from django.utils import timezone
        po = self.get_object()
        if po.status != 'discrepancy':
            return Response({'detail': 'This order is not waiting for your comment.'},
                            status=status.HTTP_400_BAD_REQUEST)
        text = (request.data.get('comment') or '').strip()
        if not text:
            return Response({'detail': 'Comment is required.'}, status=status.HTTP_400_BAD_REQUEST)

        check = po.latest_check
        if check is None:
            return Response({'detail': 'This order has not been cross-checked yet.'},
                            status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            check.afisa_comment = text
            check.commented_at = timezone.now()
            check.decision = 'pending_admin'
            check.save(update_fields=['afisa_comment', 'commented_at', 'decision'])
            po.afisa_comment = text
            po.status = 'awaiting_admin'
            po.save(update_fields=['afisa_comment', 'status'])

        supplier = po.supplier.name if po.supplier else 'supplier'
        for admin in _admin_recipients():
            notify(
                admin,
                title=f"Short delivery to approve: PO-{po.id:05d}",
                message=(f"PO-{po.id:05d} ({supplier}) arrived incomplete. Afisa Ugavi has explained why. "
                         f"Confirm to take the delivered goods into stock and keep the balance owing, "
                         f"or reject to send it back for a better explanation."),
                url='/inventory/deliveries/approvals/',
                level='warning',
            )
        return Response({'detail': 'Comment saved and sent to the Admin for a decision.',
                         'status': po.status}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['POST'], url_path='admin-decision',
            permission_classes=[permissions.IsAuthenticated, IsAdminOrSuperUser])
    def admin_decision(self, request, pk=None):
        """Admin confirms or rejects a short delivery.

        Confirm — what arrived is fine: only the delivered quantity goes into
        stock, and any balance stays owing on the order, back with Afisa Ugavi
        as 'partial' until the rest turns up.
        Reject — back to Afisa Ugavi for a better explanation; no stock moves.
        """
        from django.utils import timezone
        po = self.get_object()
        if po.status != 'awaiting_admin':
            return Response({'detail': 'This order is not awaiting an admin decision.'},
                            status=status.HTTP_400_BAD_REQUEST)

        decision = (request.data.get('decision') or '').strip().lower()
        if decision not in ('confirm', 'reject'):
            return Response({'detail': "Decision must be 'confirm' or 'reject'."},
                            status=status.HTTP_400_BAD_REQUEST)
        admin_note = (request.data.get('note') or '').strip()
        if decision == 'reject' and not admin_note:
            return Response({'detail': 'A reason is required when rejecting.'},
                            status=status.HTTP_400_BAD_REQUEST)

        check = po.latest_check
        if check is None:
            return Response({'detail': 'This order has not been cross-checked yet.'},
                            status=status.HTTP_400_BAD_REQUEST)

        supplier = po.supplier.name if po.supplier else 'supplier'
        with transaction.atomic():
            check.decided_by = request.user
            check.decided_at = timezone.now()
            check.admin_note = admin_note
            po.decided_by = request.user
            po.decided_at = timezone.now()
            po.admin_note = admin_note

            if decision == 'confirm':
                check.decision = 'approved'
                _receive_delivery(check)
                po.status = 'received' if po.is_fully_received else 'partial'
            else:
                check.decision = 'rejected'
                po.status = 'discrepancy'

            check.save(update_fields=['decision', 'decided_by', 'decided_at', 'admin_note'])
            po.save(update_fields=['status', 'decided_by', 'decided_at', 'admin_note'])

        if decision == 'confirm':
            outstanding = po.outstanding_items()
            if outstanding:
                owed = ', '.join(f"{it.product.name} ({it.outstanding})" for it in outstanding)
                message = (f"The Admin confirmed the delivery on PO-{po.id:05d} ({supplier}). What arrived "
                           f"has been added to stock. Still owed: {owed}. Confirm the order again when the "
                           f"balance is delivered.")
            else:
                message = (f"The Admin confirmed the final delivery on PO-{po.id:05d} ({supplier}). "
                           f"The order is now fully received.")
            level = 'success'
            title = f"Delivery confirmed: PO-{po.id:05d}"
            activity = f"Admin confirmed the short delivery on PO-{po.id:05d}"
            icon = 'bi-check2-circle'
        else:
            message = (f"The Admin rejected the explanation for the short delivery on PO-{po.id:05d} "
                       f"({supplier}): {admin_note} Please review the order and comment again.")
            level = 'danger'
            title = f"Delivery explanation rejected: PO-{po.id:05d}"
            activity = f"Admin rejected the short delivery explanation on PO-{po.id:05d}"
            icon = 'bi-x-octagon'

        notify(po.created_by, title=title, message=message,
               url='/inventory/purchase-orders/', level=level)
        SystemActivity.objects.create(
            user=request.user, activity_type='purchase', description=activity, icon_class=icon,
        )

        return Response({'detail': message, 'status': po.status}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['GET'])
    def pdf(self, request, pk=None):
        po = self.get_object()
        # Reuse the shared branded PDF document (same layout as invoice/quotation)
        from apps.sales.utils import render_to_pdf
        from apps.sales.views import _company_ctx, _money_breakdown, _person, _amount_in_words
        company, currency, tax_rate = _company_ctx()
        items = [{
            'code': getattr(it.product, 'sku', '') or '',
            'description': it.product.name,
            'qty': it.quantity,
            'uom': it.get_unit_display(),
            'price': it.unit_cost,
            'total': it.total_cost,
        } for it in po.items.all()]
        subtotal_ex, tax_amount, total = _money_breakdown(po.total_amount, tax_rate)
        sup = po.supplier
        ctx = {
            'doc': {
                'type': 'PURCHASE ORDER', 'number_label': 'P.O. No.',
                'number': 'PO-%05d' % po.id, 'date': po.order_date or po.created_at,
                'valid_until': None, 'page': '1/1',
                'recipient_label': 'Supplier',
                'branch': po.branch.name if po.branch else '',
                'contact': _person(po.created_by),
                'authorized_by': _person(po.created_by),
                'status': None, 'payment_term': 'As agreed',
                'delivery_label': '', 'authorised_block': True,
            },
            'company': company,
            'customer': {
                'name': sup.name if sup else '',
                'phone': (sup.phone if sup else '') or (sup.contact_name if sup else ''),
                'address': sup.address if sup else '',
                'tin': '',
            },
            'items': items, 'currency': currency,
            'subtotal_ex': subtotal_ex, 'tax_rate': tax_rate,
            'tax_amount': tax_amount, 'total': total,
            'amount_words': _amount_in_words(total, currency),
        }
        return render_to_pdf('sales/pdf_document.html', ctx)

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
    permission_classes = [permissions.IsAuthenticated, CanManageFleet]

class TruckAllocationViewSet(viewsets.ModelViewSet):
    queryset = TruckAllocation.objects.all()
    serializer_class = TruckAllocationSerializer
    permission_classes = [permissions.IsAuthenticated, CanManageFleet]

class GoodsReceivedNoteViewSet(viewsets.ModelViewSet):
    queryset = GoodsReceivedNote.objects.all()
    serializer_class = GoodsReceivedNoteSerializer
    # Stock Controller creates GRN; Store Keeper verifies it in the UI — both need access.
    permission_classes = [permissions.IsAuthenticated, CanHandleGRN]

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

class PurchaseOrderCheckView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    """Store Manager screen: the list of confirmed deliveries waiting to be
    verified. Each one is opened on its own page and checked item by item."""
    template_name = 'inventory/po_cross_check.html'

    def test_func(self):
        u = self.request.user
        return u.is_superuser or u.is_admin_role or u.is_store_manager


class PurchaseOrderCheckDetailView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    """One purchase order, checked one item at a time."""
    template_name = 'inventory/po_cross_check_detail.html'

    def test_func(self):
        u = self.request.user
        return u.is_superuser or u.is_admin_role or u.is_store_manager

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        po = get_object_or_404(PurchaseOrder, pk=kwargs['pk'])
        context['po'] = po
        context['pending_items'] = po.outstanding_items()
        context['history'] = po.checks.prefetch_related('lines__item__product')
        return context


class DeliveryApprovalView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    """Admin screen: confirm or reject short deliveries that Afisa Ugavi has
    explained. Confirming is what actually moves the goods into stock."""
    template_name = 'inventory/po_delivery_approvals.html'

    def test_func(self):
        u = self.request.user
        return u.is_superuser or u.is_admin_role

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

class TruckCostViewSet(viewsets.ModelViewSet):
    queryset = TruckCost.objects.select_related('truck', 'allocation', 'recorded_by').all()
    serializer_class = TruckCostSerializer
    permission_classes = [permissions.IsAuthenticated, IsAfisaUgavi]
    filterset_fields = ['truck', 'cost_type']

    def perform_create(self, serializer):
        serializer.save(recorded_by=self.request.user)

class DriverListView(LoginRequiredMixin, TemplateView):
    template_name = 'inventory/driver_list.html'

class TruckMaintenanceView(LoginRequiredMixin, TemplateView):
    template_name = 'inventory/truck_maintenance.html'


# ----------------------------------------------------------------------------
# Purchases Report (filterable) + Excel / PDF export
# Built for the purchases manager (Afisa Ugavi); mirrors the finance
# expense report so the look-and-feel stays consistent.
# ----------------------------------------------------------------------------

def _filter_purchases(params):
    """Filter the Purchase queryset from request GET params.

    Supported filters (all optional): date_from, date_to, supplier, branch,
    q (product name search).
    """
    qs = (Purchase.objects.select_related('supplier', 'branch', 'product')
          .order_by('-date_purchased'))
    date_from = (params.get('date_from') or '').strip()
    date_to = (params.get('date_to') or '').strip()
    supplier = (params.get('supplier') or '').strip()
    branch = (params.get('branch') or '').strip()
    q = (params.get('q') or '').strip()

    if date_from:
        qs = qs.filter(date_purchased__date__gte=date_from)
    if date_to:
        qs = qs.filter(date_purchased__date__lte=date_to)
    if supplier:
        qs = qs.filter(supplier_id=supplier)
    if branch:
        qs = qs.filter(branch_id=branch)
    if q:
        qs = qs.filter(product__name__icontains=q)
    return qs


def _active_purchase_filter_labels(params):
    """Human-readable summary of the applied filters, for report headers."""
    labels = []
    if params.get('date_from') or params.get('date_to'):
        labels.append("Period: %s to %s" % (params.get('date_from') or 'start', params.get('date_to') or 'today'))
    if params.get('supplier'):
        s = Supplier.objects.filter(id=params.get('supplier')).first()
        if s:
            labels.append("Supplier: %s" % s.name)
    if params.get('branch'):
        br = Branch.objects.filter(id=params.get('branch')).first()
        if br:
            labels.append("Branch: %s" % br.name)
    if params.get('q'):
        labels.append('Product: "%s"' % params.get('q'))
    return labels or ["All purchases (no filters)"]


def _purchase_company_name():
    try:
        from apps.core.models import SystemSettings
        s = SystemSettings.objects.first()
        if s and getattr(s, 'company_name', None):
            return s.company_name
    except Exception:
        pass
    return "Umoja Hardware"


def _purchase_rows(qs):
    """Common row data for both exporters."""
    for p in qs:
        yield [
            p.date_purchased.strftime('%Y-%m-%d') if p.date_purchased else '',
            p.supplier.name if p.supplier else 'Unknown',
            p.product.name if p.product else '',
            p.branch.name if p.branch else '',
            int(p.quantity or 0),
            float(p.unit_cost or 0),
            float(p.total_cost or 0),
        ]


class PurchaseReportView(LoginRequiredMixin, TemplateView):
    template_name = 'inventory/purchase_report.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        params = self.request.GET
        qs = _filter_purchases(params)
        totals = qs.aggregate(amount=Sum('total_cost'), qty=Sum('quantity'))

        from urllib.parse import urlencode
        clean = {k: v for k, v in params.items() if k != 'format' and v}

        ctx.update({
            'purchases': qs,
            'total': totals['amount'] or Decimal('0'),
            'total_qty': totals['qty'] or 0,
            'count': qs.count(),
            'suppliers': Supplier.objects.all().order_by('name'),
            'branches': Branch.objects.all().order_by('name'),
            'filter_querystring': urlencode(clean),
            'f': params,  # echo back selected filter values into the form
        })
        return ctx


def _export_purchases_excel(qs, params):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    wb = Workbook()
    ws = wb.active
    ws.title = "Purchases Report"

    headers = ['Date', 'Supplier', 'Product', 'Branch', 'Qty', 'Unit Cost (TZS)', 'Total (TZS)']
    bold = Font(bold=True)
    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(bold=True, color="FFFFFF")
    thin = Side(style='thin', color='DDDDDD')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    ws['A1'] = _purchase_company_name()
    ws['A1'].font = Font(bold=True, size=14)
    ws['A2'] = "Purchases Report"
    ws['A2'].font = Font(bold=True, size=12, color="555555")
    row = 3
    for line in _active_purchase_filter_labels(params):
        ws.cell(row=row, column=1, value=line).font = Font(italic=True, color="666666")
        row += 1
    row += 1

    header_row = row
    for col, h in enumerate(headers, start=1):
        c = ws.cell(row=header_row, column=col, value=h)
        c.fill = header_fill
        c.font = header_font
        c.border = border
        c.alignment = Alignment(horizontal='center')

    total = Decimal('0')
    total_qty = 0
    r = header_row + 1
    for data in _purchase_rows(qs):
        for col, val in enumerate(data, start=1):
            c = ws.cell(row=r, column=col, value=val)
            c.border = border
            if col in (5, 6, 7):
                c.alignment = Alignment(horizontal='right')
                if col in (6, 7):
                    c.number_format = '#,##0'
        total += Decimal(str(data[6]))
        total_qty += int(data[4])
        r += 1

    ws.cell(row=r, column=4, value="TOTAL").font = bold
    qc = ws.cell(row=r, column=5, value=total_qty)
    qc.font = bold
    qc.alignment = Alignment(horizontal='right')
    tc = ws.cell(row=r, column=7, value=float(total))
    tc.font = bold
    tc.number_format = '#,##0'
    tc.alignment = Alignment(horizontal='right')

    widths = [14, 26, 34, 18, 10, 18, 18]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[chr(64 + i)].width = w

    resp = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = 'attachment; filename="purchases_report_%s.xlsx"' % date.today().isoformat()
    wb.save(resp)
    return resp


def _export_purchases_pdf(qs, params):
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4),
                            leftMargin=14 * mm, rightMargin=14 * mm,
                            topMargin=14 * mm, bottomMargin=14 * mm,
                            title="Purchases Report")
    styles = getSampleStyleSheet()
    cell = ParagraphStyle('cell', parent=styles['Normal'], fontSize=8, leading=10)
    elements = []
    elements.append(Paragraph(_purchase_company_name(), styles['Title']))
    elements.append(Paragraph("Purchases Report", styles['Heading2']))
    for line in _active_purchase_filter_labels(params):
        elements.append(Paragraph(line, styles['Italic']))
    elements.append(Spacer(1, 8))

    data = [['Date', 'Supplier', 'Product', 'Branch', 'Qty', 'Unit Cost', 'Total (TZS)']]
    total = Decimal('0')
    total_qty = 0
    for r in _purchase_rows(qs):
        data.append([
            r[0],
            Paragraph(str(r[1])[:120], cell),
            Paragraph(str(r[2])[:160], cell),
            r[3],
            str(r[4]),
            '{:,.0f}'.format(r[5]),
            '{:,.0f}'.format(r[6]),
        ])
        total += Decimal(str(r[6]))
        total_qty += int(r[4])
    data.append(['', '', '', 'TOTAL', str(total_qty), '', '{:,.0f}'.format(total)])

    table = Table(data, repeatRows=1, colWidths=[22 * mm, 50 * mm, 70 * mm, 30 * mm, 16 * mm, 28 * mm, 30 * mm])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1F4E78')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('ALIGN', (4, 0), (-1, -1), 'RIGHT'),
        ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#DDDDDD')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor('#F5F7FA')]),
        ('FONTNAME', (3, -1), (-1, -1), 'Helvetica-Bold'),
        ('LINEABOVE', (0, -1), (-1, -1), 0.6, colors.black),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    elements.append(table)
    doc.build(elements)

    resp = HttpResponse(buf.getvalue(), content_type='application/pdf')
    resp['Content-Disposition'] = 'attachment; filename="purchases_report_%s.pdf"' % date.today().isoformat()
    return resp


class PurchaseReportExportView(LoginRequiredMixin, TemplateView):
    """GET ?format=excel|pdf plus the same filter params as the report page."""

    def get(self, request, *args, **kwargs):
        qs = _filter_purchases(request.GET)
        fmt = (request.GET.get('format') or 'excel').lower()
        if fmt == 'pdf':
            return _export_purchases_pdf(qs, request.GET)
        return _export_purchases_excel(qs, request.GET)


# ----------------------------------------------------------------------------
# Transport Management — all costs related to running the trucks.
# Built for the purchases manager (Afisa Ugavi).
# ----------------------------------------------------------------------------

def _filter_truck_costs(params):
    """Filter the TruckCost queryset from request GET params.

    Supported filters (all optional): date_from, date_to, truck, cost_type.
    """
    qs = TruckCost.objects.select_related('truck', 'allocation', 'recorded_by')
    date_from = (params.get('date_from') or '').strip()
    date_to = (params.get('date_to') or '').strip()
    truck = (params.get('truck') or '').strip()
    cost_type = (params.get('cost_type') or '').strip()

    if date_from:
        qs = qs.filter(date__gte=date_from)
    if date_to:
        qs = qs.filter(date__lte=date_to)
    if truck:
        qs = qs.filter(truck_id=truck)
    if cost_type:
        qs = qs.filter(cost_type=cost_type)
    return qs


def _active_truck_cost_filter_labels(params):
    labels = []
    if params.get('date_from') or params.get('date_to'):
        labels.append("Period: %s to %s" % (params.get('date_from') or 'start', params.get('date_to') or 'today'))
    if params.get('truck'):
        t = Truck.objects.filter(id=params.get('truck')).first()
        if t:
            labels.append("Truck: %s" % t.registration_number)
    if params.get('cost_type'):
        labels.append("Type: %s" % dict(TruckCost.COST_TYPES).get(params.get('cost_type'), params.get('cost_type')))
    return labels or ["All truck costs (no filters)"]


def _truck_cost_company_name():
    try:
        from apps.core.models import SystemSettings
        s = SystemSettings.objects.first()
        if s and getattr(s, 'company_name', None):
            return s.company_name
    except Exception:
        pass
    return "Umoja Hardware"


class TransportManagementView(LoginRequiredMixin, TemplateView):
    template_name = 'inventory/transport_management.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        params = self.request.GET
        qs = _filter_truck_costs(params)
        today = date.today()

        all_costs = TruckCost.objects.all()
        month_total = (all_costs.filter(date__gte=today.replace(day=1))
                       .aggregate(t=Sum('amount'))['t'] or Decimal('0'))
        year_total = (all_costs.filter(date__gte=today.replace(month=1, day=1))
                      .aggregate(t=Sum('amount'))['t'] or Decimal('0'))

        # Breakdown by cost type (filtered set)
        type_map = dict(TruckCost.COST_TYPES)
        by_type = [
            {'label': type_map.get(r['cost_type'], r['cost_type']), 'total': float(r['t'])}
            for r in qs.values('cost_type').annotate(t=Sum('amount')).order_by('-t')
        ]
        # Breakdown by truck (filtered set)
        by_truck = [
            {'reg': r['truck__registration_number'] or '-', 'total': float(r['t'])}
            for r in qs.values('truck__registration_number').annotate(t=Sum('amount')).order_by('-t')
        ]

        from urllib.parse import urlencode
        clean = {k: v for k, v in params.items() if k != 'format' and v}

        ctx.update({
            'costs': qs.order_by('-date', '-created_at'),
            'total': qs.aggregate(t=Sum('amount'))['t'] or Decimal('0'),
            'count': qs.count(),
            'month_total': month_total,
            'year_total': year_total,
            'by_type': by_type,
            'by_type_json': json.dumps(by_type),
            'by_truck': by_truck,
            'trucks': Truck.objects.all().order_by('registration_number'),
            'cost_types': TruckCost.COST_TYPES,
            'filter_querystring': urlencode(clean),
            'f': params,
        })
        return ctx


def _truck_cost_rows(qs):
    for c in qs:
        yield [
            c.date.strftime('%Y-%m-%d') if c.date else '',
            c.truck.registration_number if c.truck else '',
            c.get_cost_type_display(),
            c.vendor or '',
            c.reference or '',
            c.description or '',
            float(c.amount or 0),
        ]


def _export_truck_costs_excel(qs, params):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    wb = Workbook()
    ws = wb.active
    ws.title = "Transport Costs"

    headers = ['Date', 'Truck', 'Type', 'Vendor', 'Reference', 'Description', 'Amount (TZS)']
    bold = Font(bold=True)
    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(bold=True, color="FFFFFF")
    thin = Side(style='thin', color='DDDDDD')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    ws['A1'] = _truck_cost_company_name()
    ws['A1'].font = Font(bold=True, size=14)
    ws['A2'] = "Transport Cost Report"
    ws['A2'].font = Font(bold=True, size=12, color="555555")
    row = 3
    for line in _active_truck_cost_filter_labels(params):
        ws.cell(row=row, column=1, value=line).font = Font(italic=True, color="666666")
        row += 1
    row += 1

    header_row = row
    for col, h in enumerate(headers, start=1):
        c = ws.cell(row=header_row, column=col, value=h)
        c.fill = header_fill
        c.font = header_font
        c.border = border
        c.alignment = Alignment(horizontal='center')

    total = Decimal('0')
    r = header_row + 1
    for data in _truck_cost_rows(qs):
        for col, val in enumerate(data, start=1):
            c = ws.cell(row=r, column=col, value=val)
            c.border = border
            if col == 7:
                c.number_format = '#,##0'
                c.alignment = Alignment(horizontal='right')
        total += Decimal(str(data[6]))
        r += 1

    ws.cell(row=r, column=6, value="TOTAL").font = bold
    tc = ws.cell(row=r, column=7, value=float(total))
    tc.font = bold
    tc.number_format = '#,##0'
    tc.alignment = Alignment(horizontal='right')

    widths = [14, 18, 18, 22, 18, 40, 18]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[chr(64 + i)].width = w

    resp = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = 'attachment; filename="transport_costs_%s.xlsx"' % date.today().isoformat()
    wb.save(resp)
    return resp


def _export_truck_costs_pdf(qs, params):
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4),
                            leftMargin=14 * mm, rightMargin=14 * mm,
                            topMargin=14 * mm, bottomMargin=14 * mm,
                            title="Transport Cost Report")
    styles = getSampleStyleSheet()
    cell = ParagraphStyle('cell', parent=styles['Normal'], fontSize=8, leading=10)
    elements = []
    elements.append(Paragraph(_truck_cost_company_name(), styles['Title']))
    elements.append(Paragraph("Transport Cost Report", styles['Heading2']))
    for line in _active_truck_cost_filter_labels(params):
        elements.append(Paragraph(line, styles['Italic']))
    elements.append(Spacer(1, 8))

    data = [['Date', 'Truck', 'Type', 'Vendor', 'Reference', 'Description', 'Amount (TZS)']]
    total = Decimal('0')
    for r in _truck_cost_rows(qs):
        data.append([
            r[0], r[1], r[2], r[3], r[4],
            Paragraph(str(r[5])[:160], cell),
            '{:,.0f}'.format(r[6]),
        ])
        total += Decimal(str(r[6]))
    data.append(['', '', '', '', '', 'TOTAL', '{:,.0f}'.format(total)])

    table = Table(data, repeatRows=1, colWidths=[22 * mm, 26 * mm, 28 * mm, 36 * mm, 28 * mm, 70 * mm, 30 * mm])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1F4E78')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('ALIGN', (6, 0), (6, -1), 'RIGHT'),
        ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#DDDDDD')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor('#F5F7FA')]),
        ('FONTNAME', (5, -1), (-1, -1), 'Helvetica-Bold'),
        ('LINEABOVE', (0, -1), (-1, -1), 0.6, colors.black),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    elements.append(table)
    doc.build(elements)

    resp = HttpResponse(buf.getvalue(), content_type='application/pdf')
    resp['Content-Disposition'] = 'attachment; filename="transport_costs_%s.pdf"' % date.today().isoformat()
    return resp


class TransportCostExportView(LoginRequiredMixin, TemplateView):
    """GET ?format=excel|pdf plus the same filter params as the page."""

    def get(self, request, *args, **kwargs):
        qs = _filter_truck_costs(request.GET).order_by('-date', '-created_at')
        fmt = (request.GET.get('format') or 'excel').lower()
        if fmt == 'pdf':
            return _export_truck_costs_pdf(qs, request.GET)
        return _export_truck_costs_excel(qs, request.GET)


# ---------------------------------------------------------------------------
# Reports: stock in vs out
# ---------------------------------------------------------------------------

def _movement_filters(params):
    """Read the report's filters off the querystring.

    Dates come back as `date` objects (or None) so the same values can drive
    both the database lookups and the in-memory window in `movements`.
    """
    def as_date(key):
        raw = (params.get(key) or '').strip()
        try:
            return date.fromisoformat(raw) if raw else None
        except ValueError:
            return None

    direction = (params.get('direction') or '').strip().lower()
    return {
        'date_from': as_date('date_from'),
        'date_to': as_date('date_to'),
        'branch_id': (params.get('branch') or '').strip() or None,
        'category_id': (params.get('category') or '').strip() or None,
        'q': (params.get('q') or '').strip(),
        'direction': direction if direction in ('in', 'out') else '',
    }


def _movement_items(filters, product_ids):
    """Build one balanced ledger per item, in the order the ids are given."""
    scoped = list(product_ids)
    if not scoped:
        return []

    branch_id = filters['branch_id']
    rows = movements.collect_movements(scoped, branch_id=branch_id)
    by_product = {}
    for row in rows:
        by_product.setdefault(row.product_id, []).append(row)

    stock_qs = Stock.objects.filter(product_id__in=scoped)
    if branch_id:
        stock_qs = stock_qs.filter(branch_id=branch_id)
    on_hand = {r['product_id']: r['qty'] or 0 for r in
               stock_qs.values('product_id').annotate(qty=Sum('quantity'))}

    products = {p.id: p for p in Product.objects.filter(id__in=scoped).select_related('category')}

    items = []
    for pid in scoped:
        product = products.get(pid)
        if product is None:
            continue
        ledger = movements.build_ledger(
            by_product.get(pid, []),
            on_hand.get(pid, 0),
            date_from=filters['date_from'],
            date_to=filters['date_to'],
            direction=filters['direction'],
        )
        ledger['product'] = product
        items.append(ledger)
    return items


def _movement_products(filters):
    """The items to report on, ordered for a stable, paginable list."""
    ids = movements.moved_product_ids(
        branch_id=filters['branch_id'],
        date_from=filters['date_from'],
        date_to=filters['date_to'],
        direction=filters['direction'],
    )
    qs = Product.objects.filter(id__in=ids)
    if filters['category_id']:
        qs = qs.filter(category_id=filters['category_id'])
    if filters['q']:
        qs = qs.filter(Q(name__icontains=filters['q']) | Q(sku__icontains=filters['q']))
    # A unique tiebreaker: name alone would shuffle same-named rows between pages.
    return qs.select_related('category').order_by('name', 'id')


class StockMovementReportView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    """Stock in vs out — every item's movements with a running balance."""

    template_name = 'inventory/stock_movement_report.html'
    paginate_by = 20

    def test_func(self):
        return self.request.user.can_view_reports

    def get_context_data(self, **kwargs):
        from django.core.paginator import Paginator
        from urllib.parse import urlencode

        ctx = super().get_context_data(**kwargs)
        params = self.request.GET
        filters = _movement_filters(params)

        products = _movement_products(filters)
        paginator = Paginator(products, self.paginate_by)
        page = paginator.get_page(params.get('page'))
        items = _movement_items(filters, [p.id for p in page.object_list])

        # A handful of items reads better opened; twenty tables do not.
        expanded = len(items) <= 3
        for item in items:
            item['expanded'] = expanded

        clean = {k: v for k, v in params.items() if k not in ('page', 'format') and v}
        ctx.update({
            'items': items,
            'page_obj': page,
            'paginator': paginator,
            'is_paginated': page.has_other_pages(),
            'item_count': paginator.count,
            'grand_in': sum(i['total_in'] for i in items),
            'grand_out': sum(i['total_out'] for i in items),
            'branches': Branch.objects.all().order_by('name'),
            'categories': Category.objects.all().order_by('name'),
            'filter_querystring': urlencode(clean),
            'f': params,
            'title': 'Stock In vs Out',
        })
        return ctx


class StockMovementExportView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    """The same report as an Excel workbook — every item, not just this page."""

    def test_func(self):
        return self.request.user.can_view_reports

    def get(self, request, *args, **kwargs):
        filters = _movement_filters(request.GET)
        products = _movement_products(filters)
        items = _movement_items(filters, list(products.values_list('id', flat=True)))
        return _export_movements_excel(items, filters)


def _movement_filter_labels(filters):
    labels = []
    if filters['date_from'] or filters['date_to']:
        labels.append("Period: %s to %s" % (filters['date_from'] or 'start',
                                            filters['date_to'] or 'today'))
    if filters['branch_id']:
        branch = Branch.objects.filter(id=filters['branch_id']).first()
        labels.append("Branch: %s" % (branch.name if branch else filters['branch_id']))
    if filters['category_id']:
        category = Category.objects.filter(id=filters['category_id']).first()
        labels.append("Category: %s" % (category.name if category else filters['category_id']))
    if filters['q']:
        labels.append("Search: %s" % filters['q'])
    if filters['direction']:
        labels.append("Showing: %s only" % ('goods in' if filters['direction'] == 'in' else 'goods out'))
    return labels


def _export_movements_excel(items, filters):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from django.utils import timezone
    from apps.core.models import SystemSettings

    wb = Workbook()
    ws = wb.active
    ws.title = "Stock In vs Out"

    company = SystemSettings.objects.first()
    ws['A1'] = company.company_name if company else 'Umoja Hardware'
    ws['A1'].font = Font(bold=True, size=14)
    ws['A2'] = 'Stock In vs Out'
    ws['A2'].font = Font(bold=True, size=12, color="555555")

    row = 3
    for line in _movement_filter_labels(filters):
        ws.cell(row=row, column=1, value=line).font = Font(italic=True, color="666666")
        row += 1
    row += 1

    headers = ['Item', 'Date', 'Reference', 'Movement', 'Details', 'Branch', 'In', 'Out', 'Balance']
    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(bold=True, color="FFFFFF")
    thin = Side(style='thin', color='DDDDDD')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    for col, head in enumerate(headers, start=1):
        cell = ws.cell(row=row, column=col, value=head)
        cell.fill = header_fill
        cell.font = header_font
        cell.border = border
        cell.alignment = Alignment(horizontal='center')
    row += 1

    for item in items:
        product = item['product']
        name = "%s (%s)" % (product.name, product.sku) if product.sku else product.name

        ws.cell(row=row, column=1, value=name).font = Font(bold=True)
        ws.cell(row=row, column=4, value='Opening balance')
        ws.cell(row=row, column=9, value=item['opening'])
        row += 1

        for mv in item['rows']:
            moment = mv.date
            if timezone.is_aware(moment):
                moment = timezone.localtime(moment).replace(tzinfo=None)
            cell = ws.cell(row=row, column=2, value=moment)
            cell.number_format = 'YYYY-MM-DD'
            ws.cell(row=row, column=3, value=mv.reference)
            ws.cell(row=row, column=4, value=mv.label)
            ws.cell(row=row, column=5, value=mv.detail)
            ws.cell(row=row, column=6, value=mv.branch)
            if mv.qty_in:
                ws.cell(row=row, column=7, value=mv.qty_in)
            if mv.qty_out:
                ws.cell(row=row, column=8, value=mv.qty_out)
            ws.cell(row=row, column=9, value=mv.balance)
            row += 1

        ws.cell(row=row, column=4, value='Total for item').font = Font(bold=True)
        for col, value in ((7, item['total_in']), (8, item['total_out']), (9, item['closing'])):
            ws.cell(row=row, column=col, value=value).font = Font(bold=True)
        row += 2

    widths = [34, 12, 18, 22, 34, 18, 10, 10, 12]
    for col, width in enumerate(widths, start=1):
        ws.column_dimensions[ws.cell(row=1, column=col).column_letter].width = width

    buf = io.BytesIO()
    wb.save(buf)
    resp = HttpResponse(
        buf.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = 'attachment; filename="stock_in_vs_out_%s.xlsx"' % date.today().isoformat()
    return resp
