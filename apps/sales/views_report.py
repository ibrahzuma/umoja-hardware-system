
from django.db.models import Sum, F
from django.utils import timezone
from datetime import timedelta
from django.views.generic import TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin
from apps.users.models import User
from .models import SaleItem

class CommissionReportView(LoginRequiredMixin, TemplateView):
    template_name = 'sales/commission_report.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Get date range from GET params or default to this month
        start_date_str = self.request.GET.get('start_date')
        end_date_str = self.request.GET.get('end_date')
        
        today = timezone.now().date()
        if start_date_str:
            start_date = timezone.datetime.strptime(start_date_str, '%Y-%m-%d').date()
        else:
            start_date = today.replace(day=1)
            
        if end_date_str:
            end_date = timezone.datetime.strptime(end_date_str, '%Y-%m-%d').date()
        else:
            # End of current month approx
            next_month = today.replace(day=28) + timedelta(days=4)
            end_date = next_month - timedelta(days=next_month.day)

        # Aggregate commissions by user
        # We need to traverse SaleItem -> Sale -> User
        sales_data = SaleItem.objects.filter(
            sale__created_at__date__range=[start_date, end_date],
            sale__user__isnull=False
        ).values(
            'sale__user__username', 
            'sale__user__first_name', 
            'sale__user__last_name'
        ).annotate(
            total_sales=Sum('subtotal'),
            total_commission=Sum('commission_amount')
        ).order_by('-total_commission')

        context['sales_data'] = sales_data
        context['start_date'] = start_date
        context['end_date'] = end_date
        return context
