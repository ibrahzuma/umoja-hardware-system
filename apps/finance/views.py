import io
from decimal import Decimal, InvalidOperation
from datetime import date
from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from django.db.models import Sum, Count, Max, Q
from django.http import HttpResponse, Http404
from apps.users.permissions import (
    IsAccountant, CanRecordSupplierPayment, CanHandleCash, IsAdminOrSuperUser, is_privileged,
)
from apps.sales.models import Sale
from apps.inventory.models import Branch, PurchaseOrder, Supplier
from .models import Expense, ExpenseCategory
from django.shortcuts import render
from django.utils import timezone
from django.contrib.auth import get_user_model
from apps.core.notify import notify
from .credit import credit_balances, available_credit, pending_credit_use, spendable_credit
from .statements import profit_and_loss, cash_flow, balance_sheet, period_from
from django.urls import reverse_lazy
from django.views.generic import ListView, CreateView, TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from .forms import ExpenseForm
from .models import (
    Expense, ExpenseCategory, Income, SupplierPayment, TaxPayment, PaymentReceipt,
    BankAccount, PettyCashTransaction, OtherPayment, SalesLedgerEntry,
)
from .serializers import (
    ExpenseSerializer, ExpenseCategorySerializer, IncomeSerializer, SupplierPaymentSerializer,
    TaxPaymentSerializer, PaymentReceiptSerializer, BankAccountSerializer,
    PettyCashTransactionSerializer, OtherPaymentSerializer, SalesLedgerEntrySerializer,
)

class ExpenseListView(LoginRequiredMixin, TemplateView):
    template_name = 'finance/expense_list.html'

class ExpenseCreateView(LoginRequiredMixin, CreateView):
    model = Expense
    form_class = ExpenseForm
    template_name = 'finance/add_expense.html'
    success_url = reverse_lazy('finance:expenses_list')

    def form_valid(self, form):
        if hasattr(self.request.user, 'branch'):
            form.instance.branch = self.request.user.branch
        form.instance.created_by = self.request.user
        return super().form_valid(form)

class RecentExpenseListView(LoginRequiredMixin, TemplateView):
    template_name = 'finance/recent_expenses.html'

class ExpenseCategoryListView(LoginRequiredMixin, TemplateView):
    template_name = 'finance/category_list.html'

class ExpenseCategoryViewSet(viewsets.ModelViewSet):
    queryset = ExpenseCategory.objects.all()
    serializer_class = ExpenseCategorySerializer
    permission_classes = [permissions.DjangoModelPermissions]

class BankAccountViewSet(viewsets.ModelViewSet):
    queryset = BankAccount.objects.all().order_by('name')
    serializer_class = BankAccountSerializer
    permission_classes = [permissions.DjangoModelPermissions]

class BankListView(LoginRequiredMixin, TemplateView):
    template_name = 'finance/bank_list.html'

class ExpenseViewSet(viewsets.ModelViewSet):
    queryset = Expense.objects.select_related('category', 'bank', 'branch').order_by('-date_incurred')
    serializer_class = ExpenseSerializer
    permission_classes = [permissions.DjangoModelPermissions]

class IncomeListView(LoginRequiredMixin, TemplateView):
    template_name = 'finance/income_list.html'

class IncomeViewSet(viewsets.ModelViewSet):
    queryset = Income.objects.all().order_by('-date_received')
    serializer_class = IncomeSerializer
    permission_classes = [permissions.DjangoModelPermissions]

def _notify_admins_of_payment(payment, actor, resubmitted=False):
    """Tell the Admins a payment is waiting on them.

    Every admin gets the notice — approval is not one person's desk, and a
    payment sitting unseen is the whole point of the queue.
    """
    User = get_user_model()
    admins = User.objects.filter(is_active=True).filter(
        Q(is_superuser=True) | Q(role='admin') | Q(groups__name='Admin')
    ).distinct()
    who = actor.get_full_name() or actor.username
    against = f" against PO #{payment.purchase_order_id}" if payment.purchase_order_id else ""
    if resubmitted:
        title = "Rejected payment sent back for approval"
        body = (f"{who} answered your note on {payment.amount} to {payment.supplier}{against}"
                + (f": {payment.cashier_note}" if payment.cashier_note else "."))
    else:
        title = "Supplier payment needs approval"
        body = f"{who} recorded {payment.amount} to {payment.supplier}{against}."

    for admin in admins:
        notify(admin, title, body,
               url='/finance/supplier-payments/approvals/', level='warning')


class SupplierPaymentViewSet(viewsets.ModelViewSet):
    queryset = SupplierPayment.objects.select_related(
        'supplier', 'purchase_order', 'created_by'
    ).order_by('-payment_date', '-id')
    serializer_class = SupplierPaymentSerializer
    permission_classes = [permissions.IsAuthenticated, CanRecordSupplierPayment]
    filterset_fields = ['supplier', 'purchase_order', 'method', 'status']

    def perform_create(self, serializer):
        """A recorded payment is a request. It waits for an Admin."""
        payment = serializer.save(created_by=self.request.user, status='pending')
        _notify_admins_of_payment(payment, self.request.user)

    def perform_update(self, serializer):
        """Settled money is closed. A payment still in the loop can be amended
        — that is how the cashier answers a rejection about the figure itself."""
        if not serializer.instance.is_editable:
            raise ValidationError(
                {'detail': "A payment that has been paid can no longer be edited."})
        serializer.save()

    def perform_destroy(self, instance):
        if not instance.is_editable:
            raise ValidationError(
                {'detail': "A payment that has been paid can no longer be deleted."})
        instance.delete()

    @action(detail=True, methods=['post'])
    def resubmit(self, request, pk=None):
        """The cashier answers a rejection and sends the payment round again.

        The Admin's note is kept, so when it comes back they see their own
        objection beside the reply to it.
        """
        payment = self.get_object()
        if payment.status != 'rejected':
            return Response(
                {'detail': "Only a rejected payment goes back for another look."},
                status=400,
            )

        payment.status = 'pending'
        payment.cashier_note = (request.data.get('note') or '').strip()
        payment.resubmitted_at = timezone.now()
        payment.approved_by = None
        payment.approved_at = None
        payment.save(update_fields=['status', 'cashier_note', 'resubmitted_at',
                                    'approved_by', 'approved_at'])

        _notify_admins_of_payment(payment, request.user, resubmitted=True)
        return Response(self.get_serializer(payment).data)

    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated, IsAdminOrSuperUser])
    def approve(self, request, pk=None):
        """Admin turns a pending payment into settled money."""
        return self._decide(request, 'paid')

    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated, IsAdminOrSuperUser])
    def reject(self, request, pk=None):
        """Admin sends a payment back. It counts against nothing."""
        return self._decide(request, 'rejected')

    def _decide(self, request, new_status):
        payment = self.get_object()
        if payment.status != 'pending':
            return Response(
                {'detail': f"That payment is already {payment.get_status_display().lower()}."},
                status=400,
            )

        payment.status = new_status
        payment.approved_by = request.user
        payment.approved_at = timezone.now()
        payment.decision_note = (request.data.get('note') or '').strip()
        payment.save(update_fields=['status', 'approved_by', 'approved_at', 'decision_note'])

        approved = new_status == 'paid'
        verb = 'approved' if approved else 'rejected'
        notify(
            payment.created_by,
            f"Supplier payment {verb}",
            f"Your {payment.amount} payment to {payment.supplier} was {verb}"
            + (f": {payment.decision_note}" if payment.decision_note else ".")
            + ("" if approved else " Amend it and send it back."),
            url='/finance/supplier-payments/',
            level='success' if approved else 'warning',
        )
        return Response(self.get_serializer(payment).data)

    @action(detail=False, methods=['get'],
            permission_classes=[permissions.IsAuthenticated, IsAdminOrSuperUser])
    def pending(self, request):
        """The Admin's approval queue, oldest request first — the one that has
        been waiting longest is the one to look at."""
        qs = self.get_queryset().filter(status='pending').order_by('created_at', 'id')
        return Response(self.get_serializer(qs, many=True).data)

    @action(detail=False, methods=['get'])
    def credits(self, request):
        """Suppliers holding money of ours, most first.

        An overpayment is not a loss — it is the next order's deposit. This is
        the section that says so, and what the payment form draws on.
        """
        balances = credit_balances()
        names = dict(Supplier.objects.filter(id__in=balances).values_list('id', 'name'))
        rows = [{
            'supplier': sid,
            'supplier_name': names.get(sid, 'Unknown supplier'),
            'overpaid': str(row['overpaid']),
            'applied': str(row['applied']),
            'available': str(row['available']),
            'pending_use': str(pending_credit_use(sid)),
            'spendable': str(max(row['available'] - pending_credit_use(sid), Decimal('0'))),
        } for sid, row in balances.items()]
        rows.sort(key=lambda r: Decimal(r['available']), reverse=True)

        if (request.query_params.get('all') or '').strip() not in ('1', 'true', 'yes'):
            rows = [r for r in rows if Decimal(r['available']) > 0]
        return Response(rows)

    @action(detail=False, methods=['get'])
    def payable_orders(self, request):
        """The purchase orders a payment can be recorded against.

        Every supplier the payer can choose comes from here: every order Afisa
        Ugavi has raised, drafts included — a supplier is often paid before the
        order is confirmed. Left out are cancelled orders (nothing is owed on
        an order that was called off) and orders with no supplier on them,
        which could not name a payee. Each row carries what has already been
        paid so the form can default to the balance.

        GET ?settled=0 (the default) hides orders that have been paid off; an
        order nobody has paid against yet always shows, whatever its total.

        Only *approved* payments reduce a balance. Money a cashier has recorded
        but no Admin has approved is reported separately as `pending_amount`,
        so the order still reads as owing while it waits.
        """
        orders = (PurchaseOrder.objects
                  .exclude(status='cancelled')
                  .filter(supplier__isnull=False)
                  .select_related('supplier', 'created_by')
                  .annotate(
                      paid_total=Sum('payments__amount', filter=Q(payments__status='paid')),
                      credit_total=Sum('payments__amount',
                                       filter=Q(payments__status='paid', payments__from_credit=True)),
                      pending_total=Sum('payments__amount', filter=Q(payments__status='pending')),
                  )
                  .order_by('-created_at', '-id'))

        include_settled = (request.query_params.get('settled') or '').strip() in ('1', 'true', 'yes')
        # One pass for every supplier on the list, not one query per row.
        spendable = {sid: max(row['available'] - pending_credit_use(sid), Decimal('0'))
                     for sid, row in credit_balances().items()}

        rows = []
        for po in orders:
            total = po.total_amount or Decimal('0')
            paid = po.paid_total or Decimal('0')
            pending = po.pending_total or Decimal('0')
            balance = total - paid
            # Settled means money has actually gone out and cleared the order —
            # not merely that the order totals zero.
            if not include_settled and paid > 0 and balance <= 0:
                continue
            raised_by = po.created_by
            rows.append({
                'id': po.id,
                'label': f"PO #{po.id}",
                'supplier': po.supplier_id,
                'supplier_name': po.supplier.name,
                'order_date': po.order_date,
                'status': po.status,
                'status_display': po.get_status_display(),
                'total_amount': str(total),
                'paid_amount': str(paid),
                'paid_from_credit': str(po.credit_total or Decimal('0')),
                'pending_amount': str(pending),
                'balance': str(balance),
                'supplier_credit': str(spendable.get(po.supplier_id, Decimal('0'))),
                'settled': bool(paid > 0 and balance <= 0),
                'raised_by': (raised_by.get_full_name() or raised_by.username) if raised_by else '',
            })
        return Response(rows)

    @action(detail=False, methods=['get'])
    def by_supplier(self, request):
        """One row per supplier: what was ordered, what has been paid, what is
        still owed — the account the cash desk works from.

        Ordered totals come from the same orders `payable_orders` offers
        (cancelled ones excluded, since nothing is owed on them). Paid totals
        count every *approved* payment for that supplier, including any not
        tied to an order; what a cashier has recorded but no Admin has approved
        is reported separately as `pending_amount` and owes nothing yet.

        GET ?month=YYYY-MM narrows *payments* to that month, leaving the
        ordered figure whole — "what did we pay Steel Ltd in September" is the
        question, against the full account.
        """
        month = (request.query_params.get('month') or '').strip()

        payments = SupplierPayment.objects.filter(status='paid')
        awaiting = SupplierPayment.objects.filter(status='pending')
        if month:
            try:
                year, mon = (int(part) for part in month.split('-'))
                payments = payments.filter(payment_date__year=year, payment_date__month=mon)
            except (ValueError, TypeError):
                return Response({'detail': 'month must look like 2026-09.'}, status=400)

        paid_by_supplier = {
            row['supplier']: row for row in
            payments.values('supplier').annotate(total=Sum('amount'), n=Count('id'))
        }
        pending_by_supplier = {
            row['supplier']: row for row in
            awaiting.values('supplier').annotate(total=Sum('amount'), n=Count('id'))
        }
        last_paid = {
            row['supplier']: row['last'] for row in
            SupplierPayment.objects.filter(status='paid')
            .values('supplier').annotate(last=Max('payment_date'))
        }

        ordered = (PurchaseOrder.objects
                   .exclude(status='cancelled')
                   .filter(supplier__isnull=False)
                   .values('supplier')
                   .annotate(total=Sum('total_amount'), n=Count('id')))
        ordered_by_supplier = {row['supplier']: row for row in ordered}

        credits = credit_balances()
        supplier_ids = (set(ordered_by_supplier) | set(paid_by_supplier)
                        | set(pending_by_supplier) | set(credits))
        names = dict(Supplier.objects.filter(id__in=supplier_ids).values_list('id', 'name'))

        rows = []
        for sid in supplier_ids:
            o = ordered_by_supplier.get(sid) or {}
            p = paid_by_supplier.get(sid) or {}
            w = pending_by_supplier.get(sid) or {}
            order_total = o.get('total') or Decimal('0')
            paid_total = p.get('total') or Decimal('0')
            rows.append({
                'supplier': sid,
                'supplier_name': names.get(sid, 'Unknown supplier'),
                'order_count': o.get('n') or 0,
                'ordered_amount': str(order_total),
                'payment_count': p.get('n') or 0,
                'paid_amount': str(paid_total),
                'pending_count': w.get('n') or 0,
                'pending_amount': str(w.get('total') or Decimal('0')),
                'balance': str(order_total - paid_total),
                'credit_available': str(credits.get(sid, {}).get('available') or Decimal('0')),
                'last_payment': last_paid.get(sid),
            })

        rows.sort(key=lambda r: Decimal(r['balance']), reverse=True)
        return Response(rows)

def can_record_supplier_payment(user):
    """Who may see and use the supplier payment screen: the cash desk, and
    admins. Same rule as `CanRecordSupplierPayment` guards on the API, so the
    sidebar can never offer a screen that then returns 403."""
    return bool(
        user and user.is_authenticated
        and (is_privileged(user) or getattr(user, 'is_cashier', False))
    )


class SupplierPaymentListView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = 'finance/supplier_payment_list.html'

    def test_func(self):
        return can_record_supplier_payment(self.request.user)


class SupplierPaymentApprovalView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    """The Admin's queue. Cashiers record; only an Admin turns it into paid."""
    template_name = 'finance/supplier_payment_approvals.html'

    def test_func(self):
        u = self.request.user
        return u.is_superuser or getattr(u, 'is_admin_role', False)


class SupplierAccountListView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    """The same money as the payment ledger, read supplier by supplier."""
    template_name = 'finance/supplier_accounts.html'

    def test_func(self):
        return can_record_supplier_payment(self.request.user)

class TaxPaymentViewSet(viewsets.ModelViewSet):
    queryset = TaxPayment.objects.all().order_by('-payment_date')
    serializer_class = TaxPaymentSerializer
    permission_classes = [permissions.IsAuthenticated, IsAccountant]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

class TaxPaymentListView(LoginRequiredMixin, TemplateView):
    template_name = 'finance/tax_payment_list.html'

class DebtorListView(LoginRequiredMixin, TemplateView):
    template_name = 'finance/debtor_list.html'


class PaymentReceiptViewSet(viewsets.ModelViewSet):
    queryset = PaymentReceipt.objects.select_related(
        'sale', 'customer', 'issued_by', 'created_by'
    ).order_by('-created_at')
    serializer_class = PaymentReceiptSerializer
    permission_classes = [permissions.IsAuthenticated, IsAccountant]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @action(detail=False, methods=['get'])
    def lookup(self, request):
        """Preview an invoice before a receipt is logged against it.

        GET /api/payment-receipts/lookup/?invoice=INV-123
        Returns the invoice total, customer, issuer, and how much has already
        been received via prior receipts, so finance sees the outstanding
        balance before saving.
        """
        invoice = (request.query_params.get('invoice') or '').strip()
        if not invoice:
            return Response({'found': False, 'detail': 'No invoice number provided.'})

        sale = Sale.objects.select_related('customer', 'user').filter(invoice_number=invoice).first()
        if sale is None:
            return Response({'found': False, 'detail': 'No invoice found with that number.'})

        invoice_amount = sale.total_amount or Decimal('0')
        already_paid = sale.payment_receipts.aggregate(total=Sum('amount_paid'))['total'] or Decimal('0')
        issuer = sale.user
        return Response({
            'found': True,
            'invoice_number': sale.invoice_number,
            'customer_name': sale.customer.name if sale.customer else (sale.customer_name or ''),
            'issued_by_name': (issuer.get_full_name() or issuer.username) if issuer else '',
            'invoice_amount': str(invoice_amount),
            'already_paid': str(already_paid),
            'outstanding': str(invoice_amount - already_paid),
        })


class PaymentReceiptListView(LoginRequiredMixin, TemplateView):
    template_name = 'finance/payment_receipt_list.html'


# ----------------------------------------------------------------------------
# Cashier — petty cash, supplier payments (above) and other payments
# ----------------------------------------------------------------------------

def can_use_cashier(user):
    """Who gets the Cashier desk. Same rule everywhere: the template views, the
    API viewsets and the sidebar section all read from this, so the menu can
    never offer a screen that then returns 403."""
    return bool(
        user and user.is_authenticated
        and (is_privileged(user) or getattr(user, 'is_cashier', False)
             or getattr(user, 'is_accountant', False))
    )


class CashierAccessMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return can_use_cashier(self.request.user)


class PettyCashViewSet(viewsets.ModelViewSet):
    queryset = PettyCashTransaction.objects.select_related(
        'branch', 'category', 'bank', 'created_by'
    ).all()
    serializer_class = PettyCashTransactionSerializer
    permission_classes = [permissions.IsAuthenticated, CanHandleCash]
    filterset_fields = ['entry_type', 'branch', 'category']

    def perform_create(self, serializer):
        # Default the branch to the cashier's own if they did not pick one.
        branch = serializer.validated_data.get('branch') or getattr(self.request.user, 'branch', None)
        serializer.save(created_by=self.request.user, branch=branch)

    @action(detail=False, methods=['get'])
    def summary(self, request):
        """Float balance plus the in/out totals behind it, for the stat cards."""
        qs = self.filter_queryset(self.get_queryset())
        totals = {
            row['entry_type']: row['total'] or Decimal('0')
            for row in qs.values('entry_type').annotate(total=Sum('amount'))
        }
        cash_in = totals.get('in') or Decimal('0')
        cash_out = totals.get('out') or Decimal('0')
        return Response({
            'cash_in': str(cash_in),
            'cash_out': str(cash_out),
            'balance': str(cash_in - cash_out),
            'count': qs.count(),
        })


class PettyCashListView(CashierAccessMixin, TemplateView):
    template_name = 'finance/petty_cash.html'


class OtherPaymentViewSet(viewsets.ModelViewSet):
    queryset = OtherPayment.objects.select_related('bank', 'branch', 'created_by').all()
    serializer_class = OtherPaymentSerializer
    permission_classes = [permissions.IsAuthenticated, CanHandleCash]
    filterset_fields = ['payment_type', 'method', 'branch']

    def perform_create(self, serializer):
        branch = serializer.validated_data.get('branch') or getattr(self.request.user, 'branch', None)
        serializer.save(created_by=self.request.user, branch=branch)


class OtherPaymentListView(CashierAccessMixin, TemplateView):
    template_name = 'finance/other_payment_list.html'


# ----------------------------------------------------------------------------
# Expense Report (filterable) + Excel / PDF export
# ----------------------------------------------------------------------------

def _filter_expenses(params):
    """Filter the expense queryset from request GET params.

    Supported filters (all optional): date_from, date_to, category, bank
    ('none' = cash/no bank), branch, q (description search).
    """
    qs = Expense.objects.select_related('category', 'bank', 'branch').order_by('-date_incurred')
    date_from = (params.get('date_from') or '').strip()
    date_to = (params.get('date_to') or '').strip()
    category = (params.get('category') or '').strip()
    bank = (params.get('bank') or '').strip()
    branch = (params.get('branch') or '').strip()
    q = (params.get('q') or '').strip()

    if date_from:
        qs = qs.filter(date_incurred__gte=date_from)
    if date_to:
        qs = qs.filter(date_incurred__lte=date_to)
    if category:
        qs = qs.filter(category_id=category)
    if bank:
        if bank == 'none':
            qs = qs.filter(bank__isnull=True)
        else:
            qs = qs.filter(bank_id=bank)
    if branch:
        qs = qs.filter(branch_id=branch)
    if q:
        qs = qs.filter(description__icontains=q)
    return qs


def _active_filter_labels(params):
    """Human-readable summary of the applied filters, for report headers."""
    labels = []
    if params.get('date_from') or params.get('date_to'):
        labels.append("Period: %s to %s" % (params.get('date_from') or 'start', params.get('date_to') or 'today'))
    if params.get('category'):
        cat = ExpenseCategory.objects.filter(id=params.get('category')).first()
        if cat:
            labels.append("Category: %s" % cat.name)
    if params.get('bank'):
        if params.get('bank') == 'none':
            labels.append("Bank: Cash / none")
        else:
            b = BankAccount.objects.filter(id=params.get('bank')).first()
            if b:
                labels.append("Bank: %s" % b.name)
    if params.get('branch'):
        br = Branch.objects.filter(id=params.get('branch')).first()
        if br:
            labels.append("Branch: %s" % br.name)
    if params.get('q'):
        labels.append('Search: "%s"' % params.get('q'))
    return labels or ["All expenses (no filters)"]


def _company_name():
    try:
        from apps.core.models import SystemSettings
        s = SystemSettings.objects.first()
        if s and getattr(s, 'company_name', None):
            return s.company_name
    except Exception:
        pass
    return "Umoja Hardware"


class ExpenseReportView(LoginRequiredMixin, TemplateView):
    template_name = 'finance/expense_report.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        params = self.request.GET
        qs = _filter_expenses(params)
        total = qs.aggregate(t=Sum('amount'))['t'] or Decimal('0')

        # querystring carrying the current filters (minus export format) for the buttons
        from urllib.parse import urlencode
        clean = {k: v for k, v in params.items() if k != 'format' and v}

        ctx.update({
            'expenses': qs,
            'total': total,
            'count': qs.count(),
            'categories': ExpenseCategory.objects.all().order_by('name'),
            'banks': BankAccount.objects.all().order_by('name'),
            'branches': Branch.objects.all().order_by('name'),
            'filter_querystring': urlencode(clean),
            'f': params,  # echo back selected filter values into the form
        })
        return ctx


def _expense_rows(qs):
    """Common row data for both exporters."""
    for e in qs:
        yield [
            e.date_incurred.strftime('%Y-%m-%d') if e.date_incurred else '',
            e.category.name if e.category else 'Uncategorized',
            e.description or '',
            e.branch.name if e.branch else '',
            e.bank.name if e.bank else 'Cash / none',
            float(e.amount or 0),
        ]


def _export_excel(qs, params):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    wb = Workbook()
    ws = wb.active
    ws.title = "Expense Report"

    headers = ['Date', 'Category', 'Description', 'Branch', 'Bank', 'Amount (TZS)']
    bold = Font(bold=True)
    title_font = Font(bold=True, size=14)
    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(bold=True, color="FFFFFF")
    thin = Side(style='thin', color='DDDDDD')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    ws['A1'] = _company_name()
    ws['A1'].font = title_font
    ws['A2'] = "Expense Report"
    ws['A2'].font = Font(bold=True, size=12, color="555555")
    row = 3
    for line in _active_filter_labels(params):
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
    for data in _export_rows_for_excel(qs):
        for col, val in enumerate(data, start=1):
            c = ws.cell(row=r, column=col, value=val)
            c.border = border
            if col == 6:
                c.number_format = '#,##0'
                c.alignment = Alignment(horizontal='right')
        total += Decimal(str(data[5]))
        r += 1

    ws.cell(row=r, column=5, value="TOTAL").font = bold
    tc = ws.cell(row=r, column=6, value=float(total))
    tc.font = bold
    tc.number_format = '#,##0'
    tc.alignment = Alignment(horizontal='right')

    widths = [14, 20, 40, 18, 22, 16]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[chr(64 + i)].width = w

    resp = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = 'attachment; filename="expense_report_%s.xlsx"' % date.today().isoformat()
    wb.save(resp)
    return resp


def _export_rows_for_excel(qs):
    return list(_expense_rows(qs))


def _export_pdf(qs, params):
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4),
                            leftMargin=14 * mm, rightMargin=14 * mm,
                            topMargin=14 * mm, bottomMargin=14 * mm,
                            title="Expense Report")
    styles = getSampleStyleSheet()
    cell = ParagraphStyle('cell', parent=styles['Normal'], fontSize=8, leading=10)
    elements = []
    elements.append(Paragraph(_company_name(), styles['Title']))
    elements.append(Paragraph("Expense Report", styles['Heading2']))
    for line in _active_filter_labels(params):
        elements.append(Paragraph(line, styles['Italic']))
    elements.append(Spacer(1, 8))

    data = [['Date', 'Category', 'Description', 'Branch', 'Bank', 'Amount (TZS)']]
    total = Decimal('0')
    for r in _expense_rows(qs):
        data.append([
            r[0], r[1],
            Paragraph(str(r[2])[:200], cell),
            r[3], r[4],
            '{:,.0f}'.format(r[5]),
        ])
        total += Decimal(str(r[5]))
    data.append(['', '', '', '', 'TOTAL', '{:,.0f}'.format(total)])

    table = Table(data, repeatRows=1, colWidths=[22 * mm, 30 * mm, 90 * mm, 30 * mm, 35 * mm, 30 * mm])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1F4E78')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('ALIGN', (5, 0), (5, -1), 'RIGHT'),
        ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#DDDDDD')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor('#F5F7FA')]),
        ('FONTNAME', (4, -1), (-1, -1), 'Helvetica-Bold'),
        ('LINEABOVE', (0, -1), (-1, -1), 0.6, colors.black),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    elements.append(table)
    doc.build(elements)

    resp = HttpResponse(buf.getvalue(), content_type='application/pdf')
    resp['Content-Disposition'] = 'attachment; filename="expense_report_%s.pdf"' % date.today().isoformat()
    return resp


class ExpenseReportExportView(LoginRequiredMixin, TemplateView):
    """GET ?format=excel|pdf plus the same filter params as the report page."""

    def get(self, request, *args, **kwargs):
        qs = _filter_expenses(request.GET)
        fmt = (request.GET.get('format') or 'excel').lower()
        if fmt in ('xlsx', 'excel'):
            return _export_excel(qs, request.GET)
        if fmt == 'pdf':
            return _export_pdf(qs, request.GET)
        raise Http404("Unknown export format")


# ----------------------------------------------------------------------------
# Accounting — the sales the books still have to take up
# ----------------------------------------------------------------------------

def can_use_accounting(user):
    """Who works the sales ledger: Accounts, and admins. One rule for the
    template view, the API and the sidebar entry."""
    return bool(
        user and user.is_authenticated
        and (is_privileged(user) or getattr(user, 'is_accountant', False))
    )


class IsAccounting(permissions.BasePermission):
    def has_permission(self, request, view):
        return can_use_accounting(request.user)


class SalesLedgerViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only by design: the ledger reports the till rather than editing it.
    The only things that move are `post` and `query`."""
    queryset = SalesLedgerEntry.objects.select_related(
        'sale', 'branch', 'sold_by', 'posted_by'
    ).all()
    serializer_class = SalesLedgerEntrySerializer
    permission_classes = [permissions.IsAuthenticated, IsAccounting]
    filterset_fields = ['status', 'settlement', 'branch', 'sold_by']

    @action(detail=True, methods=['post'],
            parser_classes=[MultiPartParser, FormParser, JSONParser])
    def post_entry(self, request, pk=None):
        """Take the sale into the books and record the money, in one step.

        Accounts do one thing to a sale: satisfy themselves it is real, say how
        the money came in, attach the invoice, and post it. Two buttons only
        invited half-done rows, so the method and the attachment are required
        here and the figures freeze on the same click.

        An entry posted before this became one action can still be completed:
        the posting is left as it was and only the money is recorded.
        """
        entry = self.get_object()
        if entry.status == 'posted' and entry.payment_status == 'confirmed':
            return Response({'detail': 'That sale is already posted and paid.'}, status=400)

        method = (request.data.get('method') or '').strip()
        valid = dict(SalesLedgerEntry.CONFIRMED_METHODS)
        if method not in valid:
            return Response(
                {'detail': 'Say how the money came in: ' + ', '.join(valid)},
                status=400,
            )

        document = request.FILES.get('invoice_document')
        if document is None and not entry.invoice_document:
            return Response({'detail': 'Attach the invoice for this sale.'}, status=400)

        raw_amount = request.data.get('amount')
        try:
            amount = (Decimal(str(raw_amount)) if raw_amount not in (None, '')
                      else (entry.total_amount or Decimal('0')))
        except (InvalidOperation, TypeError):
            return Response({'detail': 'That amount is not a number.'}, status=400)
        if amount <= 0:
            return Response({'detail': 'A confirmed payment has to be more than nothing.'}, status=400)

        now = timezone.now()
        if entry.status != 'posted':
            entry.status = 'posted'
            entry.posted_by = request.user
            entry.posted_at = now
        note = (request.data.get('note') or '').strip()
        if note:
            entry.note = note

        entry.payment_status = 'confirmed'
        entry.confirmed_method = method
        entry.confirmed_amount = amount
        entry.confirmed_reference = (request.data.get('reference') or '').strip()
        entry.confirmed_by = request.user
        entry.confirmed_at = now
        if document is not None:
            entry.invoice_document = document
        entry.save()
        return Response(self.get_serializer(entry).data)

    @action(detail=True, methods=['post'])
    def query(self, request, pk=None):
        """Something does not look right. Flag it with a note and tell whoever
        made the sale; it can still be posted once it is sorted out."""
        entry = self.get_object()
        if entry.status == 'posted':
            return Response(
                {'detail': 'That sale is already posted. Reverse it in the books instead.'},
                status=400,
            )

        entry.status = 'queried'
        entry.note = (request.data.get('note') or '').strip()
        entry.save(update_fields=['status', 'note', 'updated_at'])

        notify(
            entry.sold_by,
            f"Accounts have queried invoice {entry.invoice_number}",
            entry.note or 'No reason was given.',
            url='/sales/sales/',
            level='warning',
        )
        return Response(self.get_serializer(entry).data)

    @action(detail=False, methods=['get'])
    def summary(self, request):
        """The stat cards: how much is waiting, and how it was settled."""
        qs = self.filter_queryset(self.get_queryset())
        by_status = {row['status']: row for row in
                     qs.values('status').annotate(n=Count('id'), total=Sum('total_amount'))}
        pending = by_status.get('pending') or {}
        money = qs.aggregate(invoiced=Sum('total_amount'), confirmed=Sum('confirmed_amount'))
        invoiced = money['invoiced'] or Decimal('0')
        confirmed = money['confirmed'] or Decimal('0')
        return Response({
            'pending_count': pending.get('n') or 0,
            'pending_value': str(pending.get('total') or Decimal('0')),
            'posted_count': (by_status.get('posted') or {}).get('n') or 0,
            'queried_count': (by_status.get('queried') or {}).get('n') or 0,
            'total_count': qs.count(),
            'awaiting_payment_count': qs.filter(payment_status='awaiting').count(),
            'cash_confirmed': str(confirmed),
            'cash_outstanding': str(invoiced - confirmed),
        })


class SalesLedgerView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = 'finance/sales_ledger.html'

    def test_func(self):
        return can_use_accounting(self.request.user)


# ----------------------------------------------------------------------------
# The statements. The figures themselves live in apps/finance/statements.py.
# ----------------------------------------------------------------------------

class ProfitLossViewSet(viewsets.ViewSet):
    """Read-only statement. Same gate as the sales ledger it is built from."""
    permission_classes = [permissions.IsAuthenticated, IsAccounting]

    def list(self, request):
        date_from, date_to = period_from(request.query_params)
        return Response(profit_and_loss(date_from, date_to))


class CashFlowViewSet(viewsets.ViewSet):
    """Money that actually moved, in the window it moved."""
    permission_classes = [permissions.IsAuthenticated, IsAccounting]

    def list(self, request):
        date_from, date_to = period_from(request.query_params)
        return Response(cash_flow(date_from, date_to))


class BalanceSheetViewSet(viewsets.ViewSet):
    """Where the business stands today. No date: see statements.balance_sheet."""
    permission_classes = [permissions.IsAuthenticated, IsAccounting]

    def list(self, request):
        return Response(balance_sheet())


class ProfitLossView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = 'finance/profit_loss.html'

    def test_func(self):
        return can_use_accounting(self.request.user)


class CashFlowView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = 'finance/cash_flow.html'

    def test_func(self):
        return can_use_accounting(self.request.user)


class BalanceSheetView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = 'finance/balance_sheet.html'

    def test_func(self):
        return can_use_accounting(self.request.user)
