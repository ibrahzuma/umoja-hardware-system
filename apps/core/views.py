import json
from datetime import date, timedelta

from django.conf import settings
from django.http import FileResponse, Http404
from django.shortcuts import redirect
from django.views.generic import TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import F, Sum
from django.db.models.functions import TruncDay, TruncWeek, TruncMonth, TruncYear
from django.utils import timezone

from apps.sales.models import Sale, Vehicle, Quotation, Customer
from apps.inventory.models import (
    Stock, Purchase, PurchaseOrder, Product,
    GoodsReceivedNote, StockAdjustment,
)
from apps.finance.models import Expense


def download_app(request):
    """Serve the Umoja Hardware Android app (APK) for download.

    Public on purpose — it's linked from the login page so staff can install
    the mobile app before signing in. The file lives at media/app/umoja-hardware.apk
    (replace it to ship a new build).
    """
    apk_path = settings.MEDIA_ROOT / 'app' / 'umoja-hardware.apk'
    if not apk_path.exists():
        raise Http404("The app is not available for download yet.")
    return FileResponse(
        open(apk_path, 'rb'),
        as_attachment=True,
        filename='umoja-hardware.apk',
        content_type='application/vnd.android.package-archive',
    )


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "dashboard.html"

    @staticmethod
    def _is_privileged(user):
        """Admins/managers see the full cross-branch operations dashboard;
        everyone else gets a dashboard scoped to their own remit."""
        return user.is_superuser or user.role == 'manager' or getattr(user, 'is_admin_role', False)

    def get(self, request, *args, **kwargs):
        user = request.user
        # HR-only staff get the dedicated HR dashboard rather than the
        # operations one — it's already complete and on-scope for them.
        if (getattr(user, 'is_hr', False) and not self._is_privileged(user)
                and not getattr(user, 'is_accountant', False)):
            return redirect('hr:dashboard')
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        is_privileged = self._is_privileged(user)

        # --- Role-scoped dashboards. Each is self-contained: it renders only
        #     what's within that role's remit and returns early. ---
        if not is_privileged:
            # Accountant — expenses-only.
            if getattr(user, 'is_accountant', False):
                context['is_accountant_dashboard'] = True
                self._add_expense_dashboard(context)
                return context

            # Purchases manager (Afisa Ugavi) — procurement-only.
            if getattr(user, 'is_procurement_officer', False):
                context['is_procurement_dashboard'] = True
                self._add_procurement_dashboard(context)
                return context

            # Sales representative — their own sales, quotations, customers.
            if getattr(user, 'is_sales_rep', False):
                context['is_sales_rep_dashboard'] = True
                self._add_sales_rep_dashboard(context)
                return context

            # Stock controller — goods received, stock balances, adjustments.
            if getattr(user, 'is_stock_controller', False):
                context['is_stock_controller_dashboard'] = True
                self._add_stock_controller_dashboard(context)
                return context

            # Store keeper — physical stock, GRN verification, loading/dispatch.
            if getattr(user, 'is_store_keeper', False):
                context['is_store_keeper_dashboard'] = True
                self._add_store_keeper_dashboard(context)
                return context

        # --- Operations dashboard (admin / manager / store manager) ---
        # Store managers (is_manager, not the privileged "manager" role) own
        # dispatch and the fleet, so they get the operations-center figures too.
        if is_privileged or user.is_manager or user.is_store_manager:
            context['ready_to_dispatch'] = Sale.objects.filter(status='approved').count()
            context['active_vehicles'] = Vehicle.objects.filter(status='active').count()
            context['low_stock_alert'] = Stock.objects.filter(quantity__lte=F('low_stock_threshold')).count()
            context['recent_approved'] = Sale.objects.filter(status='approved').order_by('-approved_at')[:5]

        if is_privileged or getattr(user, 'is_sales_manager', False):
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

    def _add_expense_dashboard(self, context):
        """Build expenses-only data: totals by period + time series + by
        category and by bank. Used for the accountant dashboard."""
        today = timezone.now().date()
        expenses = Expense.objects.all()

        def total(qs):
            return float(qs.aggregate(t=Sum('amount'))['t'] or 0)

        week_start = today - timedelta(days=today.weekday())          # Monday
        month_start = today.replace(day=1)
        year_start = today.replace(month=1, day=1)

        context['exp_today'] = total(expenses.filter(date_incurred=today))
        context['exp_week'] = total(expenses.filter(date_incurred__gte=week_start))
        context['exp_month'] = total(expenses.filter(date_incurred__gte=month_start))
        context['exp_year'] = total(expenses.filter(date_incurred__gte=year_start))

        def series(trunc, since, fmt):
            rows = (expenses.filter(date_incurred__gte=since)
                    .annotate(p=trunc('date_incurred'))
                    .values('p').annotate(t=Sum('amount')).order_by('p'))
            return {
                'labels': [r['p'].strftime(fmt) for r in rows if r['p']],
                'data': [float(r['t']) for r in rows if r['p']],
            }

        time_series = {
            'day': series(TruncDay, today - timedelta(days=13), '%d %b'),       # last 14 days
            'week': series(TruncWeek, today - timedelta(weeks=11), '%d %b'),    # last 12 weeks
            'month': series(TruncMonth, date(today.year - 1, today.month, 1), '%b %Y'),  # last ~12 months
            'year': series(TruncYear, date(today.year - 4, 1, 1), '%Y'),       # last 5 years
        }
        context['exp_time_series'] = json.dumps(time_series)

        # Breakdown by category and by bank (this year)
        ytd = expenses.filter(date_incurred__gte=year_start)

        cat_rows = (ytd.values('category__name')
                    .annotate(t=Sum('amount')).order_by('-t'))
        cat = [{'name': r['category__name'] or 'Uncategorized', 'total': float(r['t'])} for r in cat_rows]

        bank_rows = (ytd.values('bank__name')
                     .annotate(t=Sum('amount')).order_by('-t'))
        bank = [{'name': r['bank__name'] or 'Cash / none', 'total': float(r['t'])} for r in bank_rows]

        context['exp_by_category'] = cat
        context['exp_by_bank'] = bank
        context['exp_by_category_json'] = json.dumps(cat)
        context['exp_by_bank_json'] = json.dumps(bank)
        context['exp_year_label'] = str(today.year)

    def _add_procurement_dashboard(self, context):
        """Build a purchases-only dashboard for the purchases manager
        (Afisa Ugavi): spend by period, the purchase-order pipeline, top
        suppliers and the items that need re-ordering. Deliberately excludes
        sales revenue, dispatch and other figures outside their remit."""
        today = timezone.now().date()
        purchases = Purchase.objects.all()

        def total(qs):
            return float(qs.aggregate(t=Sum('total_cost'))['t'] or 0)

        week_start = today - timedelta(days=today.weekday())          # Monday
        month_start = today.replace(day=1)
        year_start = today.replace(month=1, day=1)

        context['pur_today'] = total(purchases.filter(date_purchased__date=today))
        context['pur_week'] = total(purchases.filter(date_purchased__date__gte=week_start))
        context['pur_month'] = total(purchases.filter(date_purchased__date__gte=month_start))
        context['pur_year'] = total(purchases.filter(date_purchased__date__gte=year_start))

        # Purchase-order pipeline
        context['po_draft'] = PurchaseOrder.objects.filter(status='draft').count()
        context['po_sent'] = PurchaseOrder.objects.filter(status='sent').count()
        context['po_received'] = PurchaseOrder.objects.filter(status='received').count()
        context['recent_orders'] = (PurchaseOrder.objects
                                     .select_related('supplier', 'branch')
                                     .order_by('-created_at')[:6])

        # Spend over time, with granularity toggle (same shape as accountant)
        def series(trunc, since, fmt):
            rows = (purchases.filter(date_purchased__date__gte=since)
                    .annotate(p=trunc('date_purchased'))
                    .values('p').annotate(t=Sum('total_cost')).order_by('p'))
            return {
                'labels': [r['p'].strftime(fmt) for r in rows if r['p']],
                'data': [float(r['t']) for r in rows if r['p']],
            }

        time_series = {
            'day': series(TruncDay, today - timedelta(days=13), '%d %b'),       # last 14 days
            'week': series(TruncWeek, today - timedelta(weeks=11), '%d %b'),    # last 12 weeks
            'month': series(TruncMonth, date(today.year - 1, today.month, 1), '%b %Y'),  # ~12 months
            'year': series(TruncYear, date(today.year - 4, 1, 1), '%Y'),       # last 5 years
        }
        context['pur_time_series'] = json.dumps(time_series)

        # Top suppliers by spend (this year)
        ytd = purchases.filter(date_purchased__date__gte=year_start)
        sup_rows = (ytd.values('supplier__name')
                    .annotate(t=Sum('total_cost')).order_by('-t')[:8])
        sup = [{'name': r['supplier__name'] or 'Unknown supplier', 'total': float(r['t'])}
               for r in sup_rows]
        context['pur_by_supplier'] = sup
        context['pur_by_supplier_json'] = json.dumps(sup)

        # Items at/below their re-order threshold
        context['reorder_items'] = (Stock.objects
                                     .select_related('product', 'branch')
                                     .filter(quantity__lte=F('low_stock_threshold'))
                                     .order_by('quantity')[:10])
        context['pur_year_label'] = str(today.year)

    def _add_sales_rep_dashboard(self, context):
        """Sales-rep dashboard: scoped to this rep's own sales and quotations
        plus the shared customer book. Deliberately excludes revenue totals,
        inventory value, expenses and dispatch — outside a rep's remit."""
        user = self.request.user
        today = timezone.now().date()

        my_sales = Sale.objects.filter(user=user)
        my_quotes = Quotation.objects.filter(created_by=user)

        todays = my_sales.filter(created_at__date=today)
        context['rep_today_count'] = todays.count()
        context['rep_today_value'] = float(todays.aggregate(t=Sum('total_amount'))['t'] or 0)
        context['rep_pending'] = my_sales.filter(status='pending').count()
        context['rep_quotes_open'] = my_quotes.filter(status__in=['draft', 'sent']).count()
        context['rep_customers'] = Customer.objects.count()

        context['rep_recent_sales'] = (my_sales.select_related('customer')
                                       .order_by('-created_at')[:6])
        context['rep_recent_quotes'] = (my_quotes.select_related('customer')
                                        .order_by('-created_at')[:6])

        # My sales over the last 14 days (value per day)
        since = today - timedelta(days=13)
        rows = (my_sales.filter(created_at__date__gte=since)
                .annotate(p=TruncDay('created_at'))
                .values('p').annotate(t=Sum('total_amount')).order_by('p'))
        context['rep_chart_labels'] = json.dumps(
            [r['p'].strftime('%d %b') for r in rows if r['p']])
        context['rep_chart_data'] = json.dumps(
            [float(r['t']) for r in rows if r['p']])

    def _add_stock_controller_dashboard(self, context):
        """Stock-controller dashboard: goods received, stock health and recent
        adjustments. No revenue, sales or finance figures."""
        stocks = Stock.objects.all()
        low_qs = stocks.filter(quantity__lte=F('low_stock_threshold'))

        context['sc_total_skus'] = Product.objects.filter(product_type='product').count()
        context['sc_low_stock'] = low_qs.count()
        context['sc_out_of_stock'] = stocks.filter(quantity__lte=0).count()
        context['sc_grns_total'] = GoodsReceivedNote.objects.count()

        # Stock-health doughnut: healthy vs low vs out
        out = context['sc_out_of_stock']
        low = max(context['sc_low_stock'] - out, 0)          # low but not fully out
        healthy = max(stocks.count() - context['sc_low_stock'], 0)
        context['sc_health_json'] = json.dumps(
            [{'name': 'Healthy', 'total': healthy},
             {'name': 'Low', 'total': low},
             {'name': 'Out of stock', 'total': out}])

        context['sc_reorder_items'] = (low_qs.select_related('product', 'branch')
                                       .order_by('quantity')[:10])
        context['sc_recent_grns'] = (GoodsReceivedNote.objects
                                     .select_related('branch', 'created_by')
                                     .order_by('-received_date')[:6])
        context['sc_recent_adjustments'] = (StockAdjustment.objects
                                            .select_related('product', 'branch')
                                            .order_by('-created_at')[:6])

    def _add_store_keeper_dashboard(self, context):
        """Store-keeper dashboard: physical stock to verify, goods received and
        the loading/offloading queue (approved orders awaiting dispatch)."""
        stocks = Stock.objects.all()

        context['sk_total_items'] = stocks.count()
        context['sk_low_stock'] = stocks.filter(quantity__lte=F('low_stock_threshold')).count()
        context['sk_to_dispatch'] = Sale.objects.filter(status='approved').count()
        context['sk_grns_total'] = GoodsReceivedNote.objects.count()

        context['sk_recent_grns'] = (GoodsReceivedNote.objects
                                     .select_related('branch', 'created_by')
                                     .order_by('-received_date')[:6])
        context['sk_dispatch_queue'] = (Sale.objects.filter(status='approved')
                                        .select_related('customer', 'branch')
                                        .order_by('-approved_at')[:8])
        context['sk_low_items'] = (stocks.filter(quantity__lte=F('low_stock_threshold'))
                                   .select_related('product', 'branch')
                                   .order_by('quantity')[:8])

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
        settings.currency = 'TZS'  # Currency is fixed to Tanzanian Shilling.
        settings.tax_rate = request.POST.get('tax_rate', settings.tax_rate)
        settings.phone = request.POST.get('phone', settings.phone)
        settings.email = request.POST.get('email', settings.email)
        settings.website = request.POST.get('website', settings.website)
        settings.address = request.POST.get('address', settings.address)
        settings.tin = request.POST.get('tin', settings.tin)
        settings.vrn = request.POST.get('vrn', settings.vrn)

        if 'logo' in request.FILES:
            settings.logo = request.FILES['logo']
            
        settings.save()
        messages.success(request, 'System settings updated successfully.')
        return self.get(request, *args, **kwargs)
