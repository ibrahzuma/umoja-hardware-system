from decimal import Decimal
from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db.models import Sum
from apps.users.permissions import IsAccountant
from apps.sales.models import Sale
from .models import Expense, ExpenseCategory
from django.shortcuts import render
from django.urls import reverse_lazy
from django.views.generic import ListView, CreateView, TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin
from .forms import ExpenseForm
from .models import Expense, ExpenseCategory, Income, SupplierPayment, TaxPayment, PaymentReceipt
from .serializers import ExpenseSerializer, ExpenseCategorySerializer, IncomeSerializer, SupplierPaymentSerializer, TaxPaymentSerializer, PaymentReceiptSerializer

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

class ExpenseViewSet(viewsets.ModelViewSet):
    queryset = Expense.objects.all().order_by('-date_incurred')
    serializer_class = ExpenseSerializer
    permission_classes = [permissions.DjangoModelPermissions]

class IncomeListView(LoginRequiredMixin, TemplateView):
    template_name = 'finance/income_list.html'

class IncomeViewSet(viewsets.ModelViewSet):
    queryset = Income.objects.all().order_by('-date_received')
    serializer_class = IncomeSerializer
    permission_classes = [permissions.DjangoModelPermissions]

class SupplierPaymentViewSet(viewsets.ModelViewSet):
    queryset = SupplierPayment.objects.all().order_by('-payment_date')
    serializer_class = SupplierPaymentSerializer
    permission_classes = [permissions.IsAuthenticated, IsAccountant]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

class SupplierPaymentListView(LoginRequiredMixin, TemplateView):
    template_name = 'finance/supplier_payment_list.html'

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
