"""The accountant's screens: configuration, the chart of accounts, voucher
entry, the General Ledger browser and the thirteen reports.

Access is the same predicate the rest of the accounting area uses —
`views.can_use_accounting`, which is admins plus the accountant — and on top
of that the four sensitive actions go by Django permission:
`finance.post_voucher`, `finance.cancel_voucher`, `finance.reverse_voucher`
and `finance.post_closed_period`. That is what lets a data-entry clerk key
and post work without being able to reverse a posted document, and what
keeps a closed period closed to everyone but an administrator.

The reports all share one view class: each subclass says what it is called
and how to build it, and the base class handles the filter form, the print
layout (`?print=1`), the CSV (`?export=csv`) and the Excel file
(`?export=xlsx`).
"""

import calendar
import json
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q, Sum
from django.http import Http404, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DetailView, ListView, TemplateView, UpdateView

from apps.inventory.models import Supplier
from apps.sales.models import Customer

from . import accounting_reports as reports
from . import vouchers as engine
from .accounting_forms import (
    AccountImportForm, AccountRangeForm, AccountingSettingsForm, AllocationFormSet, AsOfForm,
    CancelVoucherForm, CurrencyForm, CustomerRangeForm, DateRangeForm, ExchangeRateForm,
    FinancialYearForm, LedgerAccountFilterForm, LedgerAccountForm, ReverseVoucherForm,
    SupplierRangeForm, VoucherFilterForm, VoucherHeaderForm, VoucherLineFormSet,
    VoucherTypeFormSet,
)
from .audit import log_action
from .balancing import BalancingService
from .drafts import VoucherDraftService
from .exports import export_response
from .ledger_services import account_movements, bank_cash_balances, ledger_statement
from .models import (
    AccountingAuditLog, AccountingSettings, Currency, ExchangeRate, FinancialYear,
    GeneralLedgerEntry, Invoice, LedgerAccount, Voucher, VoucherType,
)
from .money import ZERO, quantize
from .posting import (
    VoucherCancellationService, VoucherPostingService, VoucherReversalService,
    VoucherValidationError, allocations_of,
)
from .restrictions import allowed_accounts, describe_restriction
from .views import can_use_accounting

VOUCHER_SLUGS = dict(Voucher.TYPES)


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------

class AccountingMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Admins and the accountant. The same gate as the sales ledger and the
    statements, so the whole accounting area is one decision."""

    def test_func(self):
        return can_use_accounting(self.request.user)


class AccountingPermissionMixin(AccountingMixin):
    """...and, on top of that, one Django permission.

    A friendlier refusal than a bare 403: the user is told what they lack and
    sent back where they came from, because the commonest cause is a role
    that has not had `create_roles` re-run for it.
    """

    permission_required = ''
    permission_denied_message = "You do not have permission to do that."

    def test_func(self):
        if not super().test_func():
            return False
        return (not self.permission_required
                or self.request.user.has_perm(self.permission_required))

    def handle_no_permission(self):
        if not self.request.user.is_authenticated:
            return super().handle_no_permission()
        messages.error(self.request, self.permission_denied_message)
        referer = self.request.META.get('HTTP_REFERER')
        if self.request.method == 'GET':
            return redirect(referer or reverse('finance:accounting_dashboard'))
        raise PermissionDenied(self.permission_denied_message)


def _guard(request, permission=None):
    """The same gate for the function-based views."""
    if not can_use_accounting(request.user):
        raise PermissionDenied("The books are for Accounts.")
    if permission and not request.user.has_perm(permission):
        raise PermissionDenied(f"You need the '{permission}' permission for that.")


# ---------------------------------------------------------------------------
# The accounting dashboard
# ---------------------------------------------------------------------------

def _report_links():
    """(url, title, icon, description) per report — resolved here rather than
    in the template, since the URL name is built from the slug."""
    return [(reverse(f'finance:report_{slug}'), title, icon, description)
            for slug, title, icon, description in reports.REPORTS]


def _month_series(financial_year, voucher_type):
    """Monthly totals of posted vouchers of one type across a financial year."""
    labels, values = [], []
    if financial_year is None:
        return labels, values
    year, month = financial_year.start_date.year, financial_year.start_date.month
    for _ in range(12):
        start = date(year, month, 1)
        if start > financial_year.end_date:
            break
        end = date(year, month, calendar.monthrange(year, month)[1])
        total = (Voucher.objects
                 .filter(voucher_type=voucher_type, status='posted', date__range=(start, end))
                 .aggregate(t=Sum('total'))['t'] or ZERO)
        labels.append(start.strftime('%b %Y'))
        values.append(float(total))
        month += 1
        if month > 12:
            month, year = 1, year + 1
    return labels, values


class AccountingDashboardView(AccountingMixin, TemplateView):
    """Where the books stand: sales and purchases for the year, what is owed
    each way, what is in the bank, and what is still sitting in draft."""

    template_name = 'finance/accounting_dashboard.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        engine.open_the_books()

        financial_year = FinancialYear.current()
        posted = Voucher.objects.filter(status='posted')
        year_filter = Q(financial_year=financial_year) if financial_year else Q()

        receivable = LedgerAccount.objects.filter(is_customer_control=True, is_active=True).first()
        payable = LedgerAccount.objects.filter(is_supplier_control=True, is_active=True).first()

        balances = bank_cash_balances()
        customer_invoices = Invoice.objects.outstanding().filter(customer__isnull=False)
        supplier_bills = Invoice.objects.outstanding().filter(supplier__isnull=False)

        status_counts = {row['status']: row['n'] for row in
                         Voucher.objects.values('status').annotate(n=Count('id'))}
        labels, sales_values = _month_series(financial_year, 'sales')
        _, purchase_values = _month_series(financial_year, 'purchase')

        ctx.update({
            'financial_year': financial_year,
            'sales_total': quantize(posted.filter(year_filter, voucher_type='sales')
                                    .aggregate(t=Sum('total'))['t'] or ZERO),
            'purchase_total': quantize(posted.filter(year_filter, voucher_type='purchase')
                                       .aggregate(t=Sum('total'))['t'] or ZERO),
            'receivables': receivable.balance() if receivable else ZERO,
            'payables': -payable.balance() if payable else ZERO,
            'cash_balance': quantize(sum((b for a, b in balances if a.kind == 'cash'), ZERO)),
            'bank_balance': quantize(sum((b for a, b in balances if a.kind == 'bank'), ZERO)),
            'bank_cash_balances': balances,
            'customer_outstanding_count': customer_invoices.count(),
            'customer_outstanding_total': quantize(
                sum((i.outstanding_amount for i in customer_invoices), ZERO)),
            'supplier_outstanding_count': supplier_bills.count(),
            'supplier_outstanding_total': quantize(
                sum((i.outstanding_amount for i in supplier_bills), ZERO)),
            'draft_count': status_counts.get('draft', 0),
            'posted_count': status_counts.get('posted', 0),
            'reversed_count': status_counts.get('reversed', 0),
            'recent_vouchers': (Voucher.objects
                                .select_related('customer', 'supplier', 'created_by')
                                .order_by('-created_at')[:10]),
            'chart_labels': json.dumps(labels),
            'chart_sales': json.dumps(sales_values),
            'chart_purchases': json.dumps(purchase_values),
            'voucher_types': Voucher.TYPES,
            'reports': _report_links(),
        })
        return ctx


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class AccountingSettingsView(AccountingPermissionMixin, UpdateView):
    model = AccountingSettings
    form_class = AccountingSettingsForm
    template_name = 'finance/accounting_settings.html'
    permission_required = 'finance.change_accountingsettings'
    success_url = reverse_lazy('finance:accounting_settings')

    def get_object(self, queryset=None):
        return AccountingSettings.get_solo()

    def get_type_formset(self):
        VoucherType.ensure_defaults()
        data = self.request.POST if self.request.method == 'POST' else None
        return VoucherTypeFormSet(data, prefix='types',
                                  queryset=VoucherType.objects.order_by('pk'))

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.setdefault('type_formset', self.get_type_formset())
        return ctx

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        form = self.get_form()
        type_formset = self.get_type_formset()
        if form.is_valid() and type_formset.is_valid():
            type_formset.save()
            return self.form_valid(form)
        messages.error(request, "Please correct the errors below.")
        return self.render_to_response(self.get_context_data(form=form,
                                                             type_formset=type_formset))

    def form_valid(self, form):
        form.instance.updated_by = self.request.user
        response = super().form_valid(form)
        log_action(AccountingAuditLog.SETTINGS, request=self.request, obj=self.object,
                   description="Accounting configuration / voucher numbering updated")
        messages.success(self.request, "Accounting configuration saved.")
        return response


class FinancialYearListView(AccountingMixin, ListView):
    model = FinancialYear
    template_name = 'finance/financial_year_list.html'

    def get_queryset(self):
        return FinancialYear.objects.annotate(voucher_count=Count('vouchers'))


class FinancialYearCreateView(AccountingPermissionMixin, CreateView):
    model = FinancialYear
    form_class = FinancialYearForm
    template_name = 'finance/financial_year_form.html'
    permission_required = 'finance.add_financialyear'
    success_url = reverse_lazy('finance:financial_year_list')

    def form_valid(self, form):
        response = super().form_valid(form)
        log_action(AccountingAuditLog.CREATED, request=self.request, obj=self.object,
                   description=f"Financial year {self.object.code} created")
        messages.success(self.request, f"Financial year {self.object.code} created.")
        return response


class FinancialYearUpdateView(AccountingPermissionMixin, UpdateView):
    model = FinancialYear
    form_class = FinancialYearForm
    template_name = 'finance/financial_year_form.html'
    permission_required = 'finance.change_financialyear'
    success_url = reverse_lazy('finance:financial_year_list')

    def form_valid(self, form):
        response = super().form_valid(form)
        log_action(AccountingAuditLog.SETTINGS, request=self.request, obj=self.object,
                   description=(f"Financial year {self.object.code} updated "
                                f"(closed={self.object.is_closed}, lock={self.object.lock_date})"))
        messages.success(self.request, f"Financial year {self.object.code} updated.")
        return response


class CurrencyListView(AccountingMixin, ListView):
    model = Currency
    template_name = 'finance/currency_list.html'

    def get_queryset(self):
        return Currency.objects.annotate(rate_count=Count('rates')).order_by('-is_base', 'code')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['base'] = Currency.base()
        ctx['rates'] = (ExchangeRate.objects.select_related('currency')
                        .order_by('-rate_date', 'currency__code')[:50])
        return ctx


class CurrencyCreateView(AccountingPermissionMixin, CreateView):
    model = Currency
    form_class = CurrencyForm
    template_name = 'finance/currency_form.html'
    permission_required = 'finance.add_currency'
    success_url = reverse_lazy('finance:currency_list')

    def form_valid(self, form):
        response = super().form_valid(form)
        log_action(AccountingAuditLog.CREATED, request=self.request, obj=self.object,
                   description=f"Currency {self.object.code} created")
        messages.success(self.request,
                         f"Currency {self.object.code} added. Give it an exchange rate next.")
        return response


class CurrencyUpdateView(AccountingPermissionMixin, UpdateView):
    model = Currency
    form_class = CurrencyForm
    template_name = 'finance/currency_form.html'
    permission_required = 'finance.change_currency'
    success_url = reverse_lazy('finance:currency_list')

    def form_valid(self, form):
        response = super().form_valid(form)
        log_action(AccountingAuditLog.EDITED, request=self.request, obj=self.object,
                   description=f"Currency {self.object.code} updated")
        messages.success(self.request, f"Currency {self.object.code} updated.")
        return response


class ExchangeRateCreateView(AccountingPermissionMixin, CreateView):
    model = ExchangeRate
    form_class = ExchangeRateForm
    template_name = 'finance/exchange_rate_form.html'
    permission_required = 'finance.add_exchangerate'
    success_url = reverse_lazy('finance:currency_list')

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        response = super().form_valid(form)
        log_action(AccountingAuditLog.CREATED, request=self.request, obj=self.object,
                   description=f"Exchange rate {self.object}")
        messages.success(self.request, f"Rate saved: {self.object}.")
        return response


class ExchangeRateUpdateView(AccountingPermissionMixin, UpdateView):
    model = ExchangeRate
    form_class = ExchangeRateForm
    template_name = 'finance/exchange_rate_form.html'
    permission_required = 'finance.change_exchangerate'
    success_url = reverse_lazy('finance:currency_list')

    def form_valid(self, form):
        response = super().form_valid(form)
        log_action(AccountingAuditLog.EDITED, request=self.request, obj=self.object,
                   description=f"Exchange rate {self.object} updated")
        messages.success(self.request, f"Rate updated: {self.object}.")
        return response


class AuditTrailView(AccountingMixin, ListView):
    """Who did what in the books. Read-only by design — an audit trail nobody
    can edit is the only kind worth keeping."""

    model = AccountingAuditLog
    template_name = 'finance/audit_trail.html'
    paginate_by = 50

    def get_queryset(self):
        qs = AccountingAuditLog.objects.select_related('user', 'voucher')
        params = self.request.GET
        if params.get('q'):
            q = params['q']
            qs = qs.filter(Q(voucher_number__icontains=q) | Q(description__icontains=q)
                           | Q(object_repr__icontains=q) | Q(username__icontains=q))
        if params.get('action'):
            qs = qs.filter(action=params['action'])
        if params.get('user'):
            qs = qs.filter(user_id=params['user'])
        if params.get('date_from'):
            qs = qs.filter(timestamp__date__gte=params['date_from'])
        if params.get('date_to'):
            qs = qs.filter(timestamp__date__lte=params['date_to'])
        return qs

    def get_context_data(self, **kwargs):
        from django.contrib.auth import get_user_model
        ctx = super().get_context_data(**kwargs)
        ctx['actions'] = AccountingAuditLog.ACTIONS
        ctx['users'] = get_user_model().objects.order_by('username')
        return ctx


# ---------------------------------------------------------------------------
# Chart of accounts
# ---------------------------------------------------------------------------

class LedgerAccountListView(AccountingMixin, ListView):
    """The chart, with a balance against every row. A group or control
    account shows the sum of everything beneath it, which is the whole point
    of having one."""

    model = LedgerAccount
    template_name = 'finance/account_list.html'

    def get_queryset(self):
        engine.open_the_books()
        self.filter_form = LedgerAccountFilterForm(self.request.GET or None)
        qs = (LedgerAccount.objects
              .select_related('parent', 'customer', 'supplier', 'bank_account', 'currency')
              .order_by('code'))
        if self.filter_form.is_valid():
            data = self.filter_form.cleaned_data
            if data['q']:
                qs = qs.filter(Q(code__icontains=data['q']) | Q(name__icontains=data['q']))
            if data['account_type']:
                qs = qs.filter(account_type=data['account_type'])
            kind = data['kind']
            if kind in ('bank', 'cash', 'customer', 'supplier'):
                qs = qs.filter(kind=kind)
            elif kind == 'vat':
                qs = qs.exclude(vat_kind='')
            elif kind == 'group':
                qs = qs.filter(is_group=True)
            elif kind == 'control':
                qs = qs.filter(Q(is_customer_control=True) | Q(is_supplier_control=True))
            if not data['show_inactive']:
                qs = qs.filter(is_active=True)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['filter_form'] = self.filter_form
        movements = account_movements()

        # The whole chart is read once and the tree built in memory. A group's
        # balance is the sum of everything beneath it, and walking that per
        # row against the database would be a query per level per group — fine
        # for the twenty-odd default ledgers, not for a chart somebody has
        # bulk-uploaded.
        leaf, children = {}, {}
        for account in LedgerAccount.objects.all():
            children.setdefault(account.parent_id, []).append(account.pk)
            if not account.is_group:
                debit, credit = movements.get(account.pk, (ZERO, ZERO))
                leaf[account.pk] = quantize(account.signed_opening_balance + debit - credit)

        def subtree_total(account_pk):
            """This ledger's own balance plus every balance below it."""
            total = leaf.get(account_pk, ZERO)
            frontier = list(children.get(account_pk, ()))
            while frontier:
                node = frontier.pop()
                total += leaf.get(node, ZERO)
                frontier.extend(children.get(node, ()))
            return quantize(total)

        rows = []
        for account in ctx['object_list']:
            if account.is_group or account.is_control_account:
                balance = subtree_total(account.pk)
            else:
                balance = leaf.get(account.pk, ZERO)
            rows.append({'account': account, 'balance': balance})
        ctx['rows'] = rows

        # The opening balances ought to net to nothing. When they do not, the
        # trial balance will not balance either, so the figure is shown here
        # rather than left to be discovered in a report.
        opening = LedgerAccount.objects.filter(is_group=False)
        ctx['opening_difference'] = quantize(
            sum((a.opening_balance for a in opening.filter(opening_side='debit')), ZERO)
            - sum((a.opening_balance for a in opening.filter(opening_side='credit')), ZERO))
        return ctx


class LedgerAccountDetailView(AccountingMixin, DetailView):
    model = LedgerAccount
    template_name = 'finance/account_detail.html'
    context_object_name = 'account'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        opening, rows, closing = ledger_statement(self.object)
        ctx.update({
            'opening': opening, 'closing': closing,
            'rows': rows[-200:], 'entry_count': len(rows),
            'children': self.object.children.order_by('code'),
        })
        return ctx


class LedgerAccountCreateView(AccountingPermissionMixin, CreateView):
    model = LedgerAccount
    form_class = LedgerAccountForm
    template_name = 'finance/account_form.html'
    permission_required = 'finance.add_ledgeraccount'

    def get_initial(self):
        initial = super().get_initial()
        parent_id = self.request.GET.get('parent')
        if parent_id:
            parent = LedgerAccount.objects.filter(pk=parent_id).first()
            if parent is not None:
                initial.update({'parent': parent, 'account_type': parent.account_type,
                                'category': parent.category})
        return initial

    def form_valid(self, form):
        response = super().form_valid(form)
        log_action(AccountingAuditLog.CREATED, request=self.request, obj=self.object,
                   description=f"Ledger {self.object} created")
        messages.success(self.request, f"Ledger {self.object} created.")
        return response

    def get_success_url(self):
        return reverse('finance:account_detail', args=[self.object.pk])


class LedgerAccountUpdateView(AccountingPermissionMixin, UpdateView):
    model = LedgerAccount
    form_class = LedgerAccountForm
    template_name = 'finance/account_form.html'
    permission_required = 'finance.change_ledgeraccount'

    def form_valid(self, form):
        response = super().form_valid(form)
        log_action(AccountingAuditLog.EDITED, request=self.request, obj=self.object,
                   description=f"Ledger {self.object} updated")
        messages.success(self.request, f"Ledger {self.object} updated.")
        return response

    def get_success_url(self):
        return reverse('finance:account_detail', args=[self.object.pk])


@login_required
def account_import(request):
    """Bulk upload of the chart of accounts. Every row is reported back —
    created, updated or refused with the reason — because a 200-row chart
    with three bad rows should land the 197 and name the three."""
    _guard(request, 'finance.add_ledgeraccount')
    from .account_imports import AccountImportError, AccountImportService

    results = None
    if request.method == 'POST':
        form = AccountImportForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                results = AccountImportService(request, request.user).run(
                    form.cleaned_data['file'])
            except AccountImportError as exc:
                form.add_error('file', str(exc))
            else:
                created = [r for r in results if r.created]
                updated = [r for r in results if r.account is not None and not r.created]
                failed = [r for r in results if r.account is None]
                for result in created + updated:
                    log_action(
                        AccountingAuditLog.CREATED if result.created else AccountingAuditLog.EDITED,
                        request=request, obj=result.account,
                        description=(f"Ledger {result.account} "
                                     f"{'created' if result.created else 'updated'} "
                                     f"by bulk upload"))
                summary = f"{len(created)} ledger(s) created, {len(updated)} updated."
                if failed:
                    messages.warning(request, summary + f" {len(failed)} row(s) could not be "
                                                        f"read — see below.")
                else:
                    messages.success(request, summary)
    else:
        form = AccountImportForm()
    return render(request, 'finance/account_import.html', {'form': form, 'results': results})


@login_required
def account_import_template(request):
    _guard(request, 'finance.add_ledgeraccount')
    from .account_imports import build_template
    response = HttpResponse(
        build_template(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename="chart_of_accounts_template.xlsx"'
    return response


# ---------------------------------------------------------------------------
# Voucher entry: draft, post, cancel, reverse
# ---------------------------------------------------------------------------

VOUCHER_BLURBS = {
    'sales': ('An invoice we raised',
              'Debit the customer for a credit sale, or the bank account or cash book for a cash '
              'sale; credit the sales ledger and, where it applies, Output VAT.'),
    'purchase': ('An invoice we received',
                 'Debit purchases, stock or the expense and, where it applies, Input VAT; credit '
                 'the supplier for a credit purchase, or the bank account or cash book paid from.'),
    'receipt': ('Money coming in',
                'Debit the bank account or cash book that received it; credit where it came from: '
                'a customer, income, or any other ledger.'),
    'payment': ('Money going out',
                'Debit what it was for: a supplier, an expense, tax, or any other ledger; credit '
                'the bank account or cash book it left.'),
    'contra': ('Moving our own money',
               'Bank to cash, cash to bank, or between two bank accounts. Both sides are bank '
               'accounts or cash books.'),
    'journal': ('Any other entry',
                'Any ledger on either side: adjustments, accruals, corrections.'),
}


class VoucherRegisterView(AccountingMixin, ListView):
    """The voucher register: everything, filtered, newest first."""

    model = Voucher
    template_name = 'finance/voucher_register.html'
    paginate_by = 25

    def get_queryset(self):
        self.filter_form = VoucherFilterForm(self.request.GET or None)
        qs = Voucher.objects.select_related('customer', 'supplier', 'financial_year',
                                            'created_by', 'posted_by')
        kind = self.request.GET.get('kind')
        if kind in VOUCHER_SLUGS:
            qs = qs.filter(voucher_type=kind)
        if self.filter_form.is_valid():
            data = self.filter_form.cleaned_data
            if data['q']:
                q = data['q']
                qs = qs.filter(Q(number__icontains=q) | Q(invoice_number__icontains=q)
                               | Q(efd_number__icontains=q) | Q(description__icontains=q)
                               | Q(reference__icontains=q))
            if data['voucher_type']:
                qs = qs.filter(voucher_type=data['voucher_type'])
            if data['status']:
                qs = qs.filter(status=data['status'])
            if data['date_from']:
                qs = qs.filter(date__gte=data['date_from'])
            if data['date_to']:
                qs = qs.filter(date__lte=data['date_to'])
            if data['invoice_number']:
                qs = qs.filter(invoice_number__icontains=data['invoice_number'])
            if data['efd_number']:
                qs = qs.filter(efd_number__icontains=data['efd_number'])
            if data['customer']:
                qs = qs.filter(Q(customer=data['customer'])
                               | Q(lines__customer=data['customer'])).distinct()
            if data['supplier']:
                qs = qs.filter(Q(supplier=data['supplier'])
                               | Q(lines__supplier=data['supplier'])).distinct()
            if data['account']:
                qs = qs.filter(lines__account=data['account']).distinct()
            if data['financial_year']:
                qs = qs.filter(financial_year=data['financial_year'])
        # `annotate` is not used here, but ordering with a unique tiebreaker
        # is the house rule for anything paginated.
        return qs.order_by('-date', '-id')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['filter_form'] = self.filter_form
        ctx['kinds'] = Voucher.TYPES
        ctx['current_kind'] = self.request.GET.get('kind', '')
        ctx['can_post'] = self.request.user.has_perm('finance.post_voucher')
        return ctx


def _account_payload(qs):
    return [{
        'id': a.pk, 'code': a.code, 'name': a.name, 'label': f"{a.code} — {a.name}",
        'kind': a.kind, 'kindLabel': a.ledger_kind, 'type': a.account_type,
        'isMoney': a.is_money,
        'customerId': a.customer_id, 'supplierId': a.supplier_id,
        'vatKind': a.vat_kind,
        'currency': a.currency_id,
    } for a in qs]


def _vat_fallback(accounts, vat_kind):
    """The VAT ledger when no default is configured: one flagged for it, or
    failing that one whose name identifies it."""
    flagged = accounts.filter(vat_kind=vat_kind).first()
    if flagged is not None:
        return flagged
    return next((a for a in accounts.filter(vat_kind='', name__icontains='vat')
                 if a.is_vat_ledger(vat_kind)), None)


def _form_config(voucher_type, voucher, settings_row):
    """The JSON the voucher form's JavaScript runs on."""
    existing_lines, existing_allocations = [], []
    if voucher is not None and voucher.pk:
        for line in voucher.lines.select_related('account'):
            existing_lines.append({
                'account': line.account_id, 'side': line.side,
                'debit': str(line.debit), 'credit': str(line.credit),
                'description': line.narration,
            })
        for alloc in allocations_of(voucher):
            existing_allocations.append({
                'invoice': alloc.invoice_id, 'sale': alloc.sale_id,
                'purchase_order': alloc.purchase_order_id, 'voucher': alloc.voucher_id,
                'reference': alloc.reference, 'amount': str(alloc.amount),
            })

    debit_accounts = allowed_accounts(voucher_type, 'debit').order_by('code')
    credit_accounts = allowed_accounts(voucher_type, 'credit').order_by('code')
    pk_of = lambda account: account.pk if account else None       # noqa: E731
    base = Currency.base()
    return {
        'type': voucher_type,
        'typeLabel': VOUCHER_SLUGS[voucher_type],
        'prefix': Voucher.PREFIX[voucher_type],
        'isInvoice': voucher_type in Voucher.INVOICE_TYPES,
        'partyKind': ('customer' if voucher_type == 'sales'
                      else 'supplier' if voucher_type == 'purchase' else ''),
        'currencySymbol': settings_row.currency_symbol,
        'vatRate': str(settings_row.default_vat_rate),
        'efdPolicy': settings_row.efd_duplicate_policy,
        'debitAccounts': _account_payload(debit_accounts),
        'creditAccounts': _account_payload(credit_accounts),
        'restrictions': {'debit': describe_restriction(voucher_type, 'debit'),
                         'credit': describe_restriction(voucher_type, 'credit')},
        'defaults': {
            'cash': pk_of(settings_row.default_cash_account),
            'bank': pk_of(settings_row.default_bank_account),
            'sales': pk_of(settings_row.default_sales_account),
            'purchase': pk_of(settings_row.default_purchase_account),
            'outputVat': (pk_of(settings_row.default_output_vat_account)
                          or pk_of(_vat_fallback(credit_accounts, LedgerAccount.VAT_OUTPUT))),
            'inputVat': (pk_of(settings_row.default_input_vat_account)
                         or pk_of(_vat_fallback(debit_accounts, LedgerAccount.VAT_INPUT))),
        },
        'currencies': [{'id': c.pk, 'code': c.code, 'symbol': c.display_symbol,
                        'isBase': c.is_base}
                       for c in Currency.objects.filter(is_active=True).order_by('-is_base', 'code')],
        'baseCurrency': ({'id': base.pk, 'code': base.code, 'symbol': base.display_symbol}
                         if base else None),
        'existingLines': existing_lines,
        'existingAllocations': existing_allocations,
        'voucherId': voucher.pk if voucher is not None and voucher.pk else None,
        'urls': {
            'outstanding': reverse('finance:api_outstanding'),
            'efdCheck': reverse('finance:api_efd_check'),
            'rate': reverse('finance:api_rate'),
            'balance': reverse('finance:api_balance'),
        },
    }


def _submitted_lines(formset):
    """What the user actually submitted, so a validation error re-renders
    their work rather than the last saved state."""
    rows = []
    for form in formset.forms:
        raw = {name: form.data.get(form.add_prefix(name), '')
               for name in ('account', 'debit', 'credit', 'description')}
        if not raw['account'] and not raw['debit'] and not raw['credit']:
            continue
        side = ('debit' if quantize(raw['debit']) > 0 or not quantize(raw['credit'])
                else 'credit')
        rows.append({'account': int(raw['account']) if raw['account'].isdigit() else None,
                     'side': side,
                     'debit': str(quantize(raw['debit'])), 'credit': str(quantize(raw['credit'])),
                     'description': raw['description']})
    return rows


def _submitted_allocations(formset):
    rows = []
    for form in formset.forms:
        amount = form.data.get(form.add_prefix('amount'), '')
        if quantize(amount) <= 0:
            continue
        row = {'amount': str(quantize(amount)),
               'reference': form.data.get(form.add_prefix('reference'), '')}
        for name in ('invoice', 'sale', 'purchase_order', 'voucher'):
            value = form.data.get(form.add_prefix(name), '')
            if value.isdigit():
                row[name] = int(value)
                break
        rows.append(row)
    return rows


def _efd_warning(request, voucher, settings_row):
    """Under the Warn policy a duplicate EFD number is flagged but allowed —
    duplicates happen legitimately, and an accountant is better placed than a
    rule to tell which."""
    if (not voucher.efd_number
            or settings_row.efd_duplicate_policy != AccountingSettings.EFD_WARN):
        return
    clash = (Voucher.objects
             .filter(voucher_type=voucher.voucher_type, efd_number__iexact=voucher.efd_number)
             .exclude(pk=voucher.pk).exclude(status='cancelled').first())
    if clash is not None:
        messages.warning(request, f"EFD receipt {voucher.efd_number} is also on voucher "
                                  f"{clash.number} dated {clash.date:%d/%m/%Y}.")


def _handle_voucher_form(request, voucher_type, voucher=None):
    settings_row = AccountingSettings.get_solo()
    engine.open_the_books()
    is_new = voucher is None
    instance = voucher or Voucher(voucher_type=voucher_type)

    if request.method == 'POST':
        header = VoucherHeaderForm(request.POST, instance=instance, voucher_type=voucher_type)
        lines = VoucherLineFormSet(request.POST, prefix='lines', voucher_type=voucher_type)
        allocations = AllocationFormSet(request.POST, prefix='alloc')
        action = request.POST.get('action', 'save')
        if action == 'post' and not request.user.has_perm('finance.post_voucher'):
            messages.error(request, "You cannot post vouchers, so this was saved as a draft.")
            action = 'save'

        if header.is_valid() and lines.is_valid() and allocations.is_valid():
            draft = header.save(commit=False)
            draft.voucher_type = voucher_type
            try:
                draft = VoucherDraftService(request.user, request).save(
                    draft, lines.lines(), allocations.allocations())
            except VoucherValidationError as exc:
                for error in exc.errors:
                    messages.error(request, error)
            else:
                _efd_warning(request, draft, settings_row)
                if action == 'post':
                    try:
                        VoucherPostingService(draft, request.user, request).post()
                    except VoucherValidationError as exc:
                        messages.warning(request, f"Draft {draft.number} was saved but could "
                                                  f"not be posted:")
                        for error in exc.errors:
                            messages.error(request, error)
                        return redirect('finance:voucher_edit', pk=draft.pk)
                    messages.success(request,
                                     f"Voucher {draft.number} posted to the General Ledger.")
                    return redirect('finance:voucher_detail', pk=draft.pk)
                messages.success(request, f"Draft {draft.number} saved.")
                if action == 'save_new':
                    return redirect('finance:voucher_new', voucher_type=voucher_type)
                return redirect('finance:voucher_detail', pk=draft.pk)
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        initial = {'date': timezone.localdate()} if is_new else None
        header = VoucherHeaderForm(instance=instance, voucher_type=voucher_type, initial=initial)
        lines = VoucherLineFormSet(prefix='lines', voucher_type=voucher_type)
        allocations = AllocationFormSet(prefix='alloc')

    config = _form_config(voucher_type, voucher, settings_row)
    if request.method == 'POST':
        config['existingLines'] = _submitted_lines(lines)
        config['existingAllocations'] = _submitted_allocations(allocations)

    from .numbering import VoucherNumberService
    financial_year = FinancialYear.current()
    next_number = (voucher.number if voucher is not None
                   else VoucherNumberService(settings_row).peek_next(voucher_type, financial_year))

    return render(request, 'finance/voucher_entry.html', {
        'header': header, 'lines': lines, 'allocs': allocations, 'voucher': voucher,
        'voucher_type': voucher_type, 'type_label': VOUCHER_SLUGS[voucher_type],
        # The dict, not a JSON string — the template hands it to `json_script`,
        # which escapes it properly for embedding in the page.
        'blurb': VOUCHER_BLURBS[voucher_type], 'config': config,
        'next_number': next_number, 'financial_year': financial_year,
        'can_post': request.user.has_perm('finance.post_voucher'),
        'multi_currency': getattr(header, 'multi_currency', False),
        'restriction_debit': describe_restriction(voucher_type, 'debit'),
        'restriction_credit': describe_restriction(voucher_type, 'credit'),
        'is_invoice': voucher_type in Voucher.INVOICE_TYPES,
        'voucher_types': Voucher.TYPES,
        'settings': settings_row,
    })


@login_required
def voucher_create(request, voucher_type):
    _guard(request, 'finance.add_voucher')
    if voucher_type not in VOUCHER_SLUGS:
        raise Http404("Unknown voucher type")
    return _handle_voucher_form(request, voucher_type)


@login_required
def voucher_edit(request, pk):
    _guard(request, 'finance.change_voucher')
    voucher = get_object_or_404(Voucher, pk=pk)
    if not voucher.is_editable:
        messages.error(request, f"Voucher {voucher.number} is "
                                f"{voucher.get_status_display().lower()} and cannot be edited. "
                                f"Correct it with a reversal.")
        return redirect('finance:voucher_detail', pk=pk)
    return _handle_voucher_form(request, voucher.voucher_type, voucher)


@login_required
def voucher_detail(request, pk):
    _guard(request)
    voucher = get_object_or_404(
        Voucher.objects.select_related('customer', 'supplier', 'financial_year', 'currency',
                                       'created_by', 'posted_by', 'cancelled_by', 'reversal_of'),
        pk=pk)
    lines = voucher.lines.select_related('account', 'account__customer', 'account__supplier')
    validation_errors = []
    if voucher.is_draft:
        validation_errors = VoucherPostingService(voucher, request.user, request).validate()
    return render(request, 'finance/voucher_detail.html', {
        'voucher': voucher,
        'lines': lines,
        'debits': [l for l in lines if l.side == 'debit'],
        'credits': [l for l in lines if l.side == 'credit'],
        'allocations': allocations_of(voucher).select_related('invoice', 'sale',
                                                              'purchase_order', 'voucher'),
        'invoice': getattr(voucher, 'invoice', None),
        'validation_errors': validation_errors,
        'gl_entries': voucher.gl_entries.select_related('account'),
        'audit_logs': voucher.audit_logs.select_related('user')[:30],
        'reversals': voucher.reversals.all(),
        'cancel_form': CancelVoucherForm(),
        'reverse_form': ReverseVoucherForm(initial={'reversal_date': timezone.localdate()}),
        'can_post': request.user.has_perm('finance.post_voucher'),
        'can_cancel': request.user.has_perm('finance.cancel_voucher'),
        'can_reverse': request.user.has_perm('finance.reverse_voucher'),
        'settings': AccountingSettings.get_solo(),
    })


@login_required
def voucher_print(request, pk):
    """A clean page that prints itself — company details, the document's own
    references, its lines, its totals and who prepared and posted it."""
    _guard(request)
    from .views import _company_name
    voucher = get_object_or_404(
        Voucher.objects.select_related('customer', 'supplier', 'currency', 'created_by',
                                       'posted_by'), pk=pk)
    return render(request, 'finance/voucher_print.html', {
        'voucher': voucher,
        'lines': voucher.lines.select_related('account'),
        'allocations': allocations_of(voucher),
        'company': _company_name(),
        'settings': AccountingSettings.get_solo(),
    })


@login_required
@require_POST
def voucher_post(request, pk):
    _guard(request, 'finance.post_voucher')
    voucher = get_object_or_404(Voucher, pk=pk)
    try:
        VoucherPostingService(voucher, request.user, request).post()
    except VoucherValidationError as exc:
        for error in exc.errors:
            messages.error(request, error)
    else:
        messages.success(request, f"Voucher {voucher.number} posted. "
                                  f"General Ledger entries created.")
    return redirect('finance:voucher_detail', pk=pk)


@login_required
@require_POST
def voucher_cancel(request, pk):
    _guard(request, 'finance.cancel_voucher')
    voucher = get_object_or_404(Voucher, pk=pk)
    form = CancelVoucherForm(request.POST)
    if not form.is_valid():
        messages.error(request, "A cancellation needs a reason.")
        return redirect('finance:voucher_detail', pk=pk)
    try:
        VoucherCancellationService(voucher, request.user, request).cancel(
            form.cleaned_data['reason'])
    except VoucherValidationError as exc:
        for error in exc.errors:
            messages.error(request, error)
    else:
        messages.success(request, f"Voucher {voucher.number} cancelled.")
    return redirect('finance:voucher_detail', pk=pk)


@login_required
@require_POST
def voucher_reverse(request, pk):
    _guard(request, 'finance.reverse_voucher')
    voucher = get_object_or_404(Voucher, pk=pk)
    form = ReverseVoucherForm(request.POST)
    if not form.is_valid():
        messages.error(request, "A reversal needs a date and a reason.")
        return redirect('finance:voucher_detail', pk=pk)
    try:
        reversal = VoucherReversalService(voucher, request.user, request).reverse(
            reversal_date=form.cleaned_data['reversal_date'],
            reason=form.cleaned_data['reason'])
    except VoucherValidationError as exc:
        for error in exc.errors:
            messages.error(request, error)
        return redirect('finance:voucher_detail', pk=pk)
    messages.success(request, f"Voucher {voucher.number} reversed by journal {reversal.number}.")
    return redirect('finance:voucher_detail', pk=reversal.pk)


@login_required
@require_POST
def voucher_delete(request, pk):
    _guard(request, 'finance.delete_voucher')
    voucher = get_object_or_404(Voucher, pk=pk)
    try:
        number = VoucherDraftService(request.user, request).delete(voucher)
    except VoucherValidationError as exc:
        for error in exc.errors:
            messages.error(request, error)
        return redirect('finance:voucher_detail', pk=pk)
    messages.success(request, f"Draft voucher {number} deleted.")
    return redirect('finance:voucher_register')


# ---------------------------------------------------------------------------
# AJAX the voucher form runs on
# ---------------------------------------------------------------------------

@login_required
def api_outstanding(request):
    """What a party still owes, or is owed: `?customer=<id>` / `?supplier=<id>`,
    optionally `?voucher=<id>` to pre-fill the allocations already drafted."""
    _guard(request)
    customer_id, supplier_id = request.GET.get('customer'), request.GET.get('supplier')
    if customer_id:
        party = get_object_or_404(Customer, pk=customer_id)
        items = engine.outstanding_invoices(party)
        kind = 'customer'
    elif supplier_id:
        party = get_object_or_404(Supplier, pk=supplier_id)
        items = engine.outstanding_bills(party)
        kind = 'supplier'
    else:
        return HttpResponseBadRequest("customer or supplier is required")

    # Allocations already drafted on the voucher being edited come back
    # pre-filled, so re-opening a draft shows what was keyed, not a blank.
    drafted = {}
    current = request.GET.get('voucher', '')
    if current.isdigit():
        draft = Voucher.objects.filter(pk=int(current)).first()
        if draft is not None:
            for alloc in allocations_of(draft):
                for name in ('invoice', 'sale', 'purchase_order', 'voucher'):
                    target_id = getattr(alloc, f'{name}_id', None)
                    if target_id:
                        drafted[(name, target_id)] = str(alloc.amount)
                        break
    for item in items:
        item['drafted'] = drafted.get((item['target'], item['id']), '')

    ledger = getattr(party, 'ledger', None)
    return JsonResponse({
        'party': {'id': party.pk, 'name': party.name, 'kind': kind,
                  'account': ledger.pk if ledger else None,
                  'balance': str(ledger.balance() if ledger else ZERO)},
        'items': items,
    })


@login_required
def api_efd_check(request):
    """Is this EFD receipt number already in the books? `?number=&type=`"""
    _guard(request)
    number = (request.GET.get('number') or '').strip()
    voucher_type = request.GET.get('type', 'sales')
    if not number:
        return JsonResponse({'duplicate': False})
    qs = (Voucher.objects
          .filter(voucher_type=voucher_type, efd_number__iexact=number)
          .exclude(status='cancelled'))
    exclude = request.GET.get('exclude', '')
    if exclude.isdigit():
        qs = qs.exclude(pk=int(exclude))
    clash = qs.first()
    settings_row = AccountingSettings.get_solo()
    return JsonResponse({
        'duplicate': clash is not None,
        'policy': settings_row.efd_duplicate_policy,
        'voucher': ({'number': clash.number, 'date': clash.date.isoformat(),
                     'url': reverse('finance:voucher_detail', args=[clash.pk])}
                    if clash else None),
    })


@login_required
def api_rate(request):
    """A currency's rate on a date: `?currency=<id>&date=YYYY-MM-DD`."""
    _guard(request)
    currency = Currency.objects.filter(pk=request.GET.get('currency') or 0).first()
    if currency is None:
        return HttpResponseBadRequest("unknown currency")
    on_date = parse_date(request.GET.get('date') or '') or timezone.localdate()
    rate = currency.rate_on(on_date)
    return JsonResponse({'currency': currency.code, 'isBase': currency.is_base,
                         'date': on_date.isoformat(),
                         'rate': str(rate) if rate is not None else None})


@login_required
@require_POST
def api_balance(request):
    """The balancing check, server-side: POST {"lines":[{"debit":..,"credit":..}]}.

    The browser does the same arithmetic for instant feedback; this is the
    copy that cannot be edited by whoever is looking at the page.
    """
    _guard(request)
    try:
        payload = json.loads(request.body or '{}')
        lines = payload.get('lines', [])
        if not isinstance(lines, list):
            raise ValueError
    except (ValueError, TypeError):
        return HttpResponseBadRequest("Invalid JSON")
    service = BalancingService(lines, currency=AccountingSettings.get_solo().currency_symbol)
    return JsonResponse(service.as_dict())


# ---------------------------------------------------------------------------
# The General Ledger browser
# ---------------------------------------------------------------------------

class GeneralLedgerEntryListView(AccountingMixin, ListView):
    """Every ledger entry, filtered — the "what hit the books" view, as
    against a single ledger's statement."""

    model = GeneralLedgerEntry
    template_name = 'finance/gl_entry_list.html'
    paginate_by = 50

    def get_queryset(self):
        qs = (GeneralLedgerEntry.objects
              .select_related('account', 'voucher', 'customer', 'supplier', 'currency')
              .order_by('-date', '-voucher_id', '-id'))
        params = self.request.GET
        if params.get('q'):
            q = params['q']
            qs = qs.filter(Q(voucher_number__icontains=q) | Q(description__icontains=q)
                           | Q(reference__icontains=q))
        if params.get('account'):
            qs = qs.filter(account_id=params['account'])
        if params.get('voucher_type'):
            qs = qs.filter(voucher_type=params['voucher_type'])
        if params.get('date_from'):
            qs = qs.filter(date__gte=params['date_from'])
        if params.get('date_to'):
            qs = qs.filter(date__lte=params['date_to'])
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['accounts'] = LedgerAccount.objects.filter(is_group=False).order_by('code')
        ctx['voucher_types'] = Voucher.TYPES
        ctx['params'] = self.request.GET
        return ctx


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

class ReportIndexView(AccountingMixin, TemplateView):
    template_name = 'finance/report_index.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['reports'] = _report_links()
        return ctx


class BaseReportView(AccountingMixin, TemplateView):
    """One view for all thirteen reports.

    A subclass says its `title`, its `slug` and its `form_class`, and
    implements `build(form)` returning a context dict with `columns` and
    `export_rows`. The screen, the print layout, the CSV and the Excel file
    then all come from the same figures — which is the only way they stay in
    step with each other.
    """

    template_name = 'finance/report.html'
    form_class = DateRangeForm
    title = ''
    slug = ''

    def default_range(self):
        """The current financial year, or this calendar year so far."""
        financial_year = FinancialYear.current()
        if financial_year is not None:
            return financial_year.start_date, financial_year.end_date
        today = timezone.localdate()
        return today.replace(month=1, day=1), today

    def dates(self, form):
        if form is not None:
            return form.cleaned_data.get('date_from'), form.cleaned_data.get('date_to')
        return self.default_range()

    def get(self, request, *args, **kwargs):
        form = self.form_class(request.GET or None)
        valid = form.is_valid()
        ctx = self.get_context_data(form=form, **kwargs)
        if valid or not request.GET:
            ctx.update(self.build(form if valid else None))
        else:
            ctx.update({'columns': [], 'rows': [], 'export_rows': []})
        fmt = request.GET.get('export')
        if fmt in ('csv', 'xlsx') and ctx.get('export_rows') is not None:
            return export_response(fmt, self.slug, ctx['columns'], ctx['export_rows'],
                                   title=self.title)
        return self.render_to_response(ctx)

    def get_context_data(self, **kwargs):
        from .views import _company_name
        ctx = super().get_context_data(**kwargs)
        ctx.update({
            'title': self.title, 'slug': self.slug,
            'print_mode': self.request.GET.get('print') == '1',
            'company': _company_name(),
            'settings': AccountingSettings.get_solo(),
            'report_template': f'finance/reports/_{self.slug}.html',
            'params': self.request.GET,
        })
        return ctx

    def build(self, form):                                      # pragma: no cover
        raise NotImplementedError


class GeneralLedgerReportView(BaseReportView):
    title, slug, form_class = 'General Ledger', 'general_ledger', AccountRangeForm

    def build(self, form):
        start, end = self.dates(form)
        account = form.cleaned_data.get('account') if form else None
        return reports.general_ledger(account=account, start=start, end=end)


class TrialBalanceReportView(BaseReportView):
    title, slug, form_class = 'Trial Balance', 'trial_balance', AsOfForm

    def build(self, form):
        data = form.cleaned_data if form else {}
        return reports.trial_balance(as_of=data.get('as_of'),
                                     financial_year=data.get('financial_year'),
                                     include_zero=data.get('include_zero', False))


class CustomerStatementReportView(BaseReportView):
    title, slug, form_class = 'Customer Statement', 'customer_statement', CustomerRangeForm

    def build(self, form):
        if form is None:
            return {'columns': [], 'export_rows': [], 'party': None}
        return reports.statement(form.cleaned_data['customer'],
                                 form.cleaned_data.get('date_from'),
                                 form.cleaned_data.get('date_to'))


class SupplierStatementReportView(BaseReportView):
    title, slug, form_class = 'Supplier Statement', 'supplier_statement', SupplierRangeForm

    def build(self, form):
        if form is None:
            return {'columns': [], 'export_rows': [], 'party': None}
        return reports.statement(form.cleaned_data['supplier'],
                                 form.cleaned_data.get('date_from'),
                                 form.cleaned_data.get('date_to'))


class CustomerOutstandingReportView(BaseReportView):
    title, slug, form_class = ('Customer Outstanding Invoices', 'customer_outstanding', AsOfForm)

    def build(self, form):
        return reports.outstanding('customer',
                                   as_of=(form.cleaned_data.get('as_of') if form else None))


class SupplierOutstandingReportView(BaseReportView):
    title, slug, form_class = ('Supplier Outstanding Bills', 'supplier_outstanding', AsOfForm)

    def build(self, form):
        return reports.outstanding('supplier',
                                   as_of=(form.cleaned_data.get('as_of') if form else None))


class _InvoiceRegisterReportView(BaseReportView):
    voucher_type = 'sales'

    def build(self, form):
        start, end = self.dates(form)
        return reports.invoice_register(self.voucher_type, start, end)


class SalesReportView(_InvoiceRegisterReportView):
    title, slug, voucher_type = 'Sales Register', 'sales', 'sales'


class PurchaseReportView(_InvoiceRegisterReportView):
    title, slug, voucher_type = 'Purchase Register', 'purchases', 'purchase'


class _SimpleRegisterReportView(BaseReportView):
    voucher_type = 'receipt'

    def build(self, form):
        start, end = self.dates(form)
        return reports.simple_register(self.voucher_type, start, end)


class ReceiptReportView(_SimpleRegisterReportView):
    title, slug, voucher_type = 'Receipt Register', 'receipts', 'receipt'


class PaymentReportView(_SimpleRegisterReportView):
    title, slug, voucher_type = 'Payment Register', 'payments', 'payment'


class ContraReportView(_SimpleRegisterReportView):
    title, slug, voucher_type = 'Contra Register', 'contra', 'contra'


class JournalReportView(_SimpleRegisterReportView):
    title, slug, voucher_type = 'Journal Register', 'journal', 'journal'


class VatReportView(BaseReportView):
    title, slug = 'VAT Report', 'vat'

    def build(self, form):
        start, end = self.dates(form)
        return reports.vat_report(start, end)


REPORT_VIEWS = {
    'general_ledger': GeneralLedgerReportView,
    'trial_balance': TrialBalanceReportView,
    'customer_statement': CustomerStatementReportView,
    'supplier_statement': SupplierStatementReportView,
    'customer_outstanding': CustomerOutstandingReportView,
    'supplier_outstanding': SupplierOutstandingReportView,
    'sales': SalesReportView,
    'purchases': PurchaseReportView,
    'receipts': ReceiptReportView,
    'payments': PaymentReportView,
    'contra': ContraReportView,
    'journal': JournalReportView,
    'vat': VatReportView,
}
