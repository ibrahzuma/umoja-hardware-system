from django.views.generic import TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import F, Sum
from django.utils import timezone
from apps.sales.models import Sale, Vehicle
from apps.inventory.models import Stock

class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        
        if user.is_superuser or user.is_manager or user.is_admin_role:
            context['ready_to_dispatch'] = Sale.objects.filter(status='approved').count()
            context['active_vehicles'] = Vehicle.objects.filter(status='active').count()
            # Simple low stock count
            context['low_stock_alert'] = Stock.objects.filter(quantity__lte=F('low_stock_threshold')).count()
            
            # Recent approved orders
            context['recent_approved'] = Sale.objects.filter(status='approved').order_by('-approved_at')[:5]

        if user.is_superuser or getattr(user, 'is_sales_manager', False):
            context['pending_approvals'] = Sale.objects.filter(status='pending').count()
            context['todays_sales_count'] = Sale.objects.filter(created_at__date=timezone.now().date()).count()
            context['todays_revenue'] = Sale.objects.filter(created_at__date=timezone.now().date()).aggregate(
                total=Sum('total_amount'))['total'] or 0
            
        return context

class GenericListView(LoginRequiredMixin, TemplateView):
    template_name = "list_page.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = self.kwargs.get('title', 'List')
        context['resource'] = self.kwargs.get('resource', '')
        return context
