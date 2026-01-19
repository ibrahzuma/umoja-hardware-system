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
        
        if user.is_superuser or user.role == 'manager' or user.is_admin_role:
            context['ready_to_dispatch'] = Sale.objects.filter(status='approved').count()
            context['active_vehicles'] = Vehicle.objects.filter(status='active').count()
            context['low_stock_alert'] = Stock.objects.filter(quantity__lte=F('low_stock_threshold')).count()
            context['recent_approved'] = Sale.objects.filter(status='approved').order_by('-approved_at')[:5]

        if user.is_superuser or getattr(user, 'is_sales_manager', False):
            context['pending_approvals'] = Sale.objects.filter(status='pending').count()
            context['todays_sales_count'] = Sale.objects.filter(created_at__date=timezone.now().date()).count()
            context['todays_revenue'] = Sale.objects.filter(created_at__date=timezone.now().date()).aggregate(
                total=Sum('total_amount'))['total'] or 0
        
        # Chart Data: Sales per Day (Last 7 Days)
        from django.db.models.functions import TruncDate
        last_7_days = timezone.now() - timezone.timedelta(days=7)
        sales_per_day = Sale.objects.filter(created_at__gte=last_7_days).annotate(
            date=TruncDate('created_at')
        ).values('date').annotate(total=Sum('total_amount')).order_by('date')
        
        import json
        dates = [s['date'].strftime('%Y-%m-%d') for s in sales_per_day]
        revenues = [float(s['total']) for s in sales_per_day]
        
        context['sales_chart_labels'] = json.dumps(dates)
        context['sales_chart_data'] = json.dumps(revenues)

        return context

class GenericListView(LoginRequiredMixin, TemplateView):
    template_name = "list_page.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = self.kwargs.get('title', 'List')
        context['resource'] = self.kwargs.get('resource', '')
        context['resource'] = self.kwargs.get('resource', '')
        return context

class SettingsView(LoginRequiredMixin, TemplateView):
    template_name = "settings.html"

    def get_context_data(self, **kwargs):
        from .models import SystemSettings
        context = super().get_context_data(**kwargs)
        settings, _ = SystemSettings.objects.get_or_create(id=1)
        context['settings'] = settings
        return context

    def post(self, request, *args, **kwargs):
        from .models import SystemSettings
        from django.contrib import messages
        
        settings, _ = SystemSettings.objects.get_or_create(id=1)
        
        settings.company_name = request.POST.get('company_name', settings.company_name)
        settings.currency = request.POST.get('currency', settings.currency)
        settings.tax_rate = request.POST.get('tax_rate', settings.tax_rate)
        settings.phone = request.POST.get('phone', settings.phone)
        settings.email = request.POST.get('email', settings.email)
        settings.address = request.POST.get('address', settings.address)
        
        if 'logo' in request.FILES:
            settings.logo = request.FILES['logo']
            
        settings.save()
        messages.success(request, 'System settings updated successfully.')
        return self.get(request, *args, **kwargs)
