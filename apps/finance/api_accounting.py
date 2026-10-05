"""The REST side of the accounting area.

The screens in `views_accounting.py` are server-rendered; these ViewSets are
for the page JavaScript, the mobile app and anything else that wants the same
figures as JSON. Every one of them is behind `IsAccounting` — admins plus the
accountant — and the writes that matter are additionally gated on the Django
permission, the same way the screens are.

Three things stay deliberately read-only here:

  * the **General Ledger**, because nothing but posting a voucher may write it;
  * the **invoice register**, because a row is raised by posting a Sales or
    Purchase voucher, never typed in;
  * the **audit trail**, because a trail somebody can edit is not one.
"""

from django.utils import timezone
from rest_framework import mixins, permissions, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from . import accounting_reports as reports
from . import vouchers as engine
from .balancing import BalancingService
from .ledger_services import bank_cash_balances, trial_balance_rows
from .models import (
    AccountingAuditLog, AccountingSettings, Currency, ExchangeRate, FinancialYear, Invoice,
    VoucherType,
)
from .posting import check_period
from .serializers import (
    AccountingAuditLogSerializer, AccountingSettingsSerializer, CurrencySerializer,
    ExchangeRateSerializer, FinancialYearSerializer, InvoiceSerializer, VoucherTypeSerializer,
)
from .views import IsAccounting


class AccountingViewSet(viewsets.ModelViewSet):
    """Shared base: the accounting gate, plus Django model permissions on the
    writes. `DjangoModelPermissions` is what makes the role groups seeded by
    `create_roles` actually bite on the API, as elsewhere in the system."""

    permission_classes = [permissions.IsAuthenticated, IsAccounting,
                          permissions.DjangoModelPermissions]


class CurrencyViewSet(AccountingViewSet):
    queryset = Currency.objects.all()
    serializer_class = CurrencySerializer
    filterset_fields = ['is_active', 'is_base']

    @action(detail=True, methods=['get'])
    def rate(self, request, pk=None):
        """The rate on a date: `?date=YYYY-MM-DD` (today when left out)."""
        currency = self.get_object()
        from django.utils.dateparse import parse_date
        on_date = parse_date(request.query_params.get('date') or '') or timezone.localdate()
        rate = currency.rate_on(on_date)
        return Response({'currency': currency.code, 'date': on_date.isoformat(),
                         'is_base': currency.is_base,
                         'rate': str(rate) if rate is not None else None})


class ExchangeRateViewSet(AccountingViewSet):
    queryset = ExchangeRate.objects.select_related('currency', 'created_by').all()
    serializer_class = ExchangeRateSerializer
    filterset_fields = ['currency']

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class FinancialYearViewSet(AccountingViewSet):
    queryset = FinancialYear.objects.all()
    serializer_class = FinancialYearSerializer
    filterset_fields = ['is_active', 'is_closed']

    def perform_destroy(self, instance):
        if instance.vouchers.exists():
            raise ValidationError({'detail': 'This year carries vouchers. Close it rather than '
                                             'deleting it.'})
        instance.delete()

    @action(detail=False, methods=['get'])
    def current(self, request):
        """The active year, and whether a given date could be posted into it:
        `?date=YYYY-MM-DD`."""
        year = FinancialYear.current()
        data = {'year': self.get_serializer(year).data if year else None}
        asked = request.query_params.get('date')
        if asked:
            errors, covering = check_period(asked, request.user)
            data['can_post'] = not errors
            data['errors'] = errors
            data['covering_year'] = self.get_serializer(covering).data if covering else None
        return Response(data)


class VoucherTypeViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin,
                          mixins.UpdateModelMixin, viewsets.GenericViewSet):
    """The six types are fixed; only their prefix and label can change."""
    queryset = VoucherType.objects.all()
    serializer_class = VoucherTypeSerializer
    permission_classes = [permissions.IsAuthenticated, IsAccounting,
                          permissions.DjangoModelPermissions]

    def list(self, request, *args, **kwargs):
        VoucherType.ensure_defaults()
        return super().list(request, *args, **kwargs)


class AccountingSettingsViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin,
                                 mixins.UpdateModelMixin, viewsets.GenericViewSet):
    """A singleton, so the list is one row and there is no create."""
    queryset = AccountingSettings.objects.all()
    serializer_class = AccountingSettingsSerializer
    permission_classes = [permissions.IsAuthenticated, IsAccounting,
                          permissions.DjangoModelPermissions]

    def list(self, request, *args, **kwargs):
        row = AccountingSettings.get_solo()
        return Response(self.get_serializer(row).data)

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)

    @action(detail=False, methods=['post'])
    def open_books(self, request):
        """Create the base currency, a financial year, the voucher types and
        the chart of accounts if any are missing. Safe to call repeatedly."""
        if not request.user.has_perm('finance.change_accountingsettings'):
            raise PermissionDenied("You cannot change the accounting configuration.")
        created = engine.open_the_books()
        return Response({'ledgers_created': created,
                         'financial_year': FinancialYear.current().code
                         if FinancialYear.current() else None})


class InvoiceViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """The invoice register, read-only. `?outstanding=1` narrows it to what is
    still owed, which is what the allocation screens ask for."""
    queryset = Invoice.objects.select_related('customer', 'supplier', 'voucher').all()
    serializer_class = InvoiceSerializer
    permission_classes = [permissions.IsAuthenticated, IsAccounting]
    filterset_fields = ['kind', 'status', 'customer', 'supplier']

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        if params.get('outstanding') in ('1', 'true', 'yes'):
            qs = qs.outstanding()
        if params.get('from'):
            qs = qs.filter(invoice_date__gte=params['from'])
        if params.get('to'):
            qs = qs.filter(invoice_date__lte=params['to'])
        if params.get('number'):
            qs = qs.filter(invoice_number__icontains=params['number'])
        return qs.order_by('invoice_date', 'id')

    @action(detail=False, methods=['get'])
    def ageing(self, request):
        """Outstanding invoices or bills, grouped by party and aged.
        `?party=customer|supplier`, `?as_of=YYYY-MM-DD`."""
        from django.utils.dateparse import parse_date
        party = request.query_params.get('party', 'customer')
        if party not in ('customer', 'supplier'):
            raise ValidationError({'party': "Use 'customer' or 'supplier'."})
        as_of = parse_date(request.query_params.get('as_of') or '')
        data = reports.outstanding(party, as_of=as_of)
        return Response({
            'as_of': data['as_of'].isoformat(),
            'buckets': {k: str(v) for k, v in data['buckets'].items()},
            'grand_total': str(data['grand_total']),
            'groups': [{
                'party': group['party'].name,
                'party_id': group['party'].pk,
                'total': str(group['total']),
                'rows': [{
                    'invoice': row['invoice'].invoice_number,
                    'date': row['invoice'].invoice_date.isoformat(),
                    'original': str(row['invoice'].original_amount),
                    'outstanding': str(row['outstanding']),
                    'days': row['days'], 'bucket': row['bucket'],
                } for row in group['rows']],
            } for group in data['groups']],
        })


class AccountingAuditLogViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin,
                                 viewsets.GenericViewSet):
    """Read-only on purpose."""
    queryset = AccountingAuditLog.objects.select_related('user', 'voucher').all()
    serializer_class = AccountingAuditLogSerializer
    permission_classes = [permissions.IsAuthenticated, IsAccounting]
    filterset_fields = ['action', 'user', 'voucher']

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        if params.get('from'):
            qs = qs.filter(timestamp__date__gte=params['from'])
        if params.get('to'):
            qs = qs.filter(timestamp__date__lte=params['to'])
        if params.get('q'):
            from django.db.models import Q
            q = params['q']
            qs = qs.filter(Q(voucher_number__icontains=q) | Q(description__icontains=q)
                           | Q(username__icontains=q))
        return qs


class AccountingReportViewSet(viewsets.ViewSet):
    """The reports as JSON, for anything that would rather have the figures
    than the page. The screens render the same builders."""

    permission_classes = [permissions.IsAuthenticated, IsAccounting]

    @staticmethod
    def _dates(params):
        from django.utils.dateparse import parse_date
        return parse_date(params.get('from') or ''), parse_date(params.get('to') or '')

    def list(self, request):
        """What reports exist, and where each one lives on the web."""
        from django.urls import reverse
        return Response([{
            'slug': slug, 'title': title, 'description': description,
            'url': reverse(f'finance:report_{slug}'),
        } for slug, title, _icon, description in reports.REPORTS])

    @action(detail=False, methods=['get'])
    def trial_balance(self, request):
        from django.utils.dateparse import parse_date
        as_of = parse_date(request.query_params.get('as_of') or '')
        year = FinancialYear.objects.filter(
            pk=request.query_params.get('financial_year') or 0).first()
        rows, totals = trial_balance_rows(
            as_of=as_of, financial_year=year,
            include_zero=request.query_params.get('include_zero') in ('1', 'true', 'yes'))
        return Response({
            'rows': [{
                'id': row['account'].pk, 'code': row['account'].code,
                'name': row['account'].name,
                'account_type': row['account'].account_type,
                'opening': str(row['opening']),
                'period_debit': str(row['period_debit']),
                'period_credit': str(row['period_credit']),
                'debit': str(row['debit']), 'credit': str(row['credit']),
            } for row in rows],
            'totals': {k: str(v) for k, v in totals.items()},
        })

    @action(detail=False, methods=['get'])
    def vat(self, request):
        start, end = self._dates(request.query_params)
        data = reports.vat_report(start, end)
        return Response({
            'output_total': str(data['output_total']),
            'input_total': str(data['input_total']),
            'net_vat': str(data['net_vat']),
            'output_rows': [{
                'voucher': row['voucher'].number, 'date': row['voucher'].date.isoformat(),
                'invoice_number': row['voucher'].invoice_number,
                'efd_number': row['voucher'].efd_number,
                'party': row['party'].name if row['party'] else '',
                'net': str(row['net']), 'vat': str(row['amount']),
            } for row in data['output_rows']],
            'input_rows': [{
                'voucher': row['voucher'].number, 'date': row['voucher'].date.isoformat(),
                'invoice_number': row['voucher'].invoice_number,
                'efd_number': row['voucher'].efd_number,
                'party': row['party'].name if row['party'] else '',
                'net': str(row['net']), 'vat': str(row['amount']),
            } for row in data['input_rows']],
        })

    @action(detail=False, methods=['get'])
    def cash_and_bank(self, request):
        """Every cash book and bank account with its balance."""
        return Response([{'id': account.pk, 'code': account.code, 'name': account.name,
                          'kind': account.kind, 'balance': str(balance)}
                         for account, balance in bank_cash_balances()])

    @action(detail=False, methods=['post'])
    def balance_check(self, request):
        """The balancing rule, server-side:
        POST {"lines": [{"debit": ..., "credit": ...}, ...]}"""
        lines = request.data.get('lines')
        if not isinstance(lines, list):
            raise ValidationError({'lines': 'Send a list of lines.'})
        service = BalancingService(lines,
                                   currency=AccountingSettings.get_solo().currency_symbol)
        return Response(service.as_dict())
