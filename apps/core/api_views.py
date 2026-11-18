from django.db.models import Q
from rest_framework import viewsets, permissions
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from .models import SystemActivity, Notification
from .serializers import ActivitySerializer, NotificationSerializer

class ActivityViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = SystemActivity.objects.all()[:15]
    serializer_class = ActivitySerializer
    permission_classes = [permissions.IsAuthenticated]


class NotificationViewSet(viewsets.ReadOnlyModelViewSet):
    """Per-user notification inbox. Users only ever see their own."""
    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Notification.objects.filter(recipient=self.request.user)

    @action(detail=False, methods=['get'])
    def unread_count(self, request):
        return Response({'count': self.get_queryset().filter(is_read=False).count()})

    @action(detail=False, methods=['post'])
    def mark_all_read(self, request):
        self.get_queryset().filter(is_read=False).update(is_read=True)
        return Response({'status': 'ok'})

    @action(detail=True, methods=['post'])
    def mark_read(self, request, pk=None):
        n = self.get_object()
        n.is_read = True
        n.save(update_fields=['is_read'])
        return Response({'status': 'ok'})


class DashboardViewSet(viewsets.ViewSet):
    """Every figure on the operations dashboard for one period, beside the
    period before it (see apps/core/dashboard.py)."""
    permission_classes = [permissions.IsAuthenticated]

    def list(self, request):
        from .dashboard import overview
        return Response(overview(request.user, request.query_params))


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def global_search(request):
    """Top-bar search: products, sales, customers, suppliers, purchase orders.

    Returns a small ranked list grouped by type. Each hit carries the URL the
    front-end should open on click, so the dropdown stays dumb.
    """
    q = (request.query_params.get('q') or '').strip()
    if len(q) < 2:
        return Response({'q': q, 'results': []})

    from apps.inventory.models import Product, Supplier, PurchaseOrder
    from apps.sales.models import Sale, Customer

    results = []
    limit_per_type = 5

    # Products
    for p in (Product.objects
              .filter(Q(name__icontains=q) | Q(sku__icontains=q))
              .order_by('name')[:limit_per_type]):
        results.append({
            'type': 'product',
            'label': p.name,
            'detail': p.sku or '',
            'url': f'/inventory/products/?q={q}',
        })

    # Sales (invoice no, PI no, order no, LP no — any reference)
    sale_qs = Sale.objects.filter(
        Q(invoice_number__icontains=q)
        | Q(pi_number__icontains=q)
        | Q(order_number__icontains=q)
        | Q(lp_number__icontains=q)
    ).select_related('customer', 'branch').order_by('-created_at')[:limit_per_type]
    for s in sale_qs:
        customer = (s.customer.name if s.customer_id
                    else (s.customer_name or 'Walk-in'))
        extra = []
        if getattr(s, 'pi_number', None):
            extra.append(f"PI {s.pi_number}")
        extra.append(customer)
        results.append({
            'type': 'sale',
            'label': s.invoice_number,
            'detail': ' · '.join(extra),
            'url': f'/sales/history/?q={s.invoice_number}',
        })

    # Customers
    for c in (Customer.objects
              .filter(Q(name__icontains=q) | Q(phone__icontains=q)
                      | Q(email__icontains=q))
              .order_by('name')[:limit_per_type]):
        results.append({
            'type': 'customer',
            'label': c.name,
            'detail': c.phone or c.email or '',
            'url': f'/sales/customers/?q={q}',
        })

    # Suppliers
    for s in (Supplier.objects
              .filter(Q(name__icontains=q) | Q(phone__icontains=q))
              .order_by('name')[:limit_per_type]):
        results.append({
            'type': 'supplier',
            'label': s.name,
            'detail': s.phone or '',
            'url': f'/inventory/suppliers/?q={q}',
        })

    # Purchase orders — by number (#NN) or supplier name
    po_qs = PurchaseOrder.objects.select_related('supplier').order_by('-created_at')
    numeric = q.lstrip('#').strip()
    po_filter = Q(supplier__name__icontains=q)
    if numeric.isdigit():
        po_filter |= Q(id=int(numeric))
    for po in po_qs.filter(po_filter)[:limit_per_type]:
        supplier = po.supplier.name if po.supplier_id else 'No supplier'
        results.append({
            'type': 'purchase_order',
            'label': f"PO #{po.id}",
            'detail': supplier,
            'url': '/inventory/purchase-orders/',
        })

    return Response({'q': q, 'results': results})
