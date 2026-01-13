from django.urls import path
from . import views

app_name = 'finance'

urlpatterns = [
    path('expenses/', views.ExpenseListView.as_view(), name='expenses_list'),
    path('expenses/create/', views.ExpenseCreateView.as_view(), name='expense_create'),
    path('expenses/recent/', views.RecentExpenseListView.as_view(), name='recent_expenses'),
    path('income/', views.IncomeListView.as_view(), name='other_income'),
    path('income/create/', views.ExpenseCreateView.as_view(), name='income_create'), # Placeholder/Reuse
]
