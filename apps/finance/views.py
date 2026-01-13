from rest_framework import viewsets, permissions
from .models import Expense, ExpenseCategory
from django.shortcuts import render
from django.urls import reverse_lazy
from django.views.generic import ListView, CreateView, TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin
from .forms import ExpenseForm
from .models import Expense, ExpenseCategory, Income
from .serializers import ExpenseSerializer, ExpenseCategorySerializer, IncomeSerializer

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
