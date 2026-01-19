from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register(r'expenses', views.ExpenseViewSet)
router.register(r'income', views.IncomeViewSet) # Check if this makes sense
router.register(r'supplier-payments', views.SupplierPaymentViewSet)
router.register(r'taxes', views.TaxPaymentViewSet)

app_name = 'finance'

urlpatterns = [
    path('expenses/', views.ExpenseListView.as_view(), name='expenses_list'),
    path('expenses/create/', views.ExpenseCreateView.as_view(), name='expense_create'),
    path('expenses/recent/', views.RecentExpenseListView.as_view(), name='recent_expenses'),
    path('income/', views.IncomeListView.as_view(), name='other_income'),
    path('income/create/', views.ExpenseCreateView.as_view(), name='income_create'), # Placeholder/Reuse
    
    path('supplier-payments/', views.SupplierPaymentListView.as_view(), name='supplier_payment_list'),
    
    path('taxes/', views.TaxPaymentListView.as_view(), name='tax_payment_list'),
    path('debtors/', views.DebtorListView.as_view(), name='debtors_list'),
    
    path('api/', include(router.urls)),
]
