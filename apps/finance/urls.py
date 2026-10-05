from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views, views_accounting as acc

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
    path('expenses/report/', views.ExpenseReportView.as_view(), name='expense_report'),
    path('expenses/report/export/', views.ExpenseReportExportView.as_view(), name='expense_report_export'),
    path('income/', views.IncomeListView.as_view(), name='other_income'),
    path('income/create/', views.ExpenseCreateView.as_view(), name='income_create'), # Placeholder/Reuse
    
    path('supplier-payments/', views.SupplierPaymentListView.as_view(), name='supplier_payment_list'),
    path('supplier-payments/by-supplier/', views.SupplierAccountListView.as_view(), name='supplier_accounts'),
    path('supplier-payments/approvals/', views.SupplierPaymentApprovalView.as_view(),
         name='supplier_payment_approvals'),

    # Cashier desk
    path('petty-cash/', views.PettyCashListView.as_view(), name='petty_cash'),
    path('petty-cash/requests/', views.PettyCashRequestView.as_view(),
         name='petty_cash_requests'),
    path('petty-cash/approvals/', views.PettyCashApprovalView.as_view(),
         name='petty_cash_approvals'),
    path('other-payments/', views.OtherPaymentListView.as_view(), name='other_payments'),


    path('sales-ledger/', views.SalesLedgerView.as_view(), name='sales_ledger'),
    path('profit-loss/', views.ProfitLossView.as_view(), name='profit_loss'),
    path('cash-flow/', views.CashFlowView.as_view(), name='cash_flow'),
    path('balance-sheet/', views.BalanceSheetView.as_view(), name='balance_sheet'),

    # ------------------------------------------------------------------
    # The books. The REST-driven screens (`chart_of_accounts`, `voucher_list`,
    # `general_ledger`) and the server-rendered ones from the voucher engine
    # (`account_list`, `voucher_register`, `gl_entries`) both stand: the first
    # set is the quick everyday view the page JS drives, the second is the
    # full accounting workflow with drafts, reversal, print and exports.
    # ------------------------------------------------------------------
    path('accounts/', views.ChartOfAccountsView.as_view(), name='chart_of_accounts'),
    path('vouchers/', views.VoucherListView.as_view(), name='voucher_list'),
    path('vouchers/new/<str:voucher_type>/', views.VoucherFormView.as_view(), name='voucher_new'),
    path('vouchers/<int:pk>/', acc.voucher_detail, name='voucher_detail'),
    path('general-ledger/', views.GeneralLedgerView.as_view(), name='general_ledger'),

    # ------------------------------------------------------------------
    # Accounting: the accountant's own area
    # ------------------------------------------------------------------
    path('accounting/', acc.AccountingDashboardView.as_view(), name='accounting_dashboard'),

    # Configuration
    path('accounting/settings/', acc.AccountingSettingsView.as_view(), name='accounting_settings'),
    path('accounting/financial-years/', acc.FinancialYearListView.as_view(),
         name='financial_year_list'),
    path('accounting/financial-years/new/', acc.FinancialYearCreateView.as_view(),
         name='financial_year_create'),
    path('accounting/financial-years/<int:pk>/', acc.FinancialYearUpdateView.as_view(),
         name='financial_year_edit'),
    path('accounting/currencies/', acc.CurrencyListView.as_view(), name='currency_list'),
    path('accounting/currencies/new/', acc.CurrencyCreateView.as_view(), name='currency_create'),
    path('accounting/currencies/<int:pk>/', acc.CurrencyUpdateView.as_view(), name='currency_edit'),
    path('accounting/rates/new/', acc.ExchangeRateCreateView.as_view(), name='rate_create'),
    path('accounting/rates/<int:pk>/', acc.ExchangeRateUpdateView.as_view(), name='rate_edit'),
    path('accounting/audit-trail/', acc.AuditTrailView.as_view(), name='audit_trail'),

    # Chart of accounts
    path('accounting/ledgers/', acc.LedgerAccountListView.as_view(), name='account_list'),
    path('accounting/ledgers/new/', acc.LedgerAccountCreateView.as_view(), name='account_create'),
    path('accounting/ledgers/import/', acc.account_import, name='account_import'),
    path('accounting/ledgers/import/template/', acc.account_import_template,
         name='account_import_template'),
    path('accounting/ledgers/<int:pk>/', acc.LedgerAccountDetailView.as_view(),
         name='account_detail'),
    path('accounting/ledgers/<int:pk>/edit/', acc.LedgerAccountUpdateView.as_view(),
         name='account_edit'),

    # Voucher entry
    path('accounting/vouchers/', acc.VoucherRegisterView.as_view(), name='voucher_register'),
    path('accounting/vouchers/new/<str:voucher_type>/', acc.voucher_create, name='voucher_entry'),
    path('accounting/vouchers/<int:pk>/edit/', acc.voucher_edit, name='voucher_edit'),
    path('accounting/vouchers/<int:pk>/print/', acc.voucher_print, name='voucher_print'),
    path('accounting/vouchers/<int:pk>/post/', acc.voucher_post, name='voucher_post'),
    path('accounting/vouchers/<int:pk>/cancel/', acc.voucher_cancel, name='voucher_cancel'),
    path('accounting/vouchers/<int:pk>/reverse/', acc.voucher_reverse, name='voucher_reverse'),
    path('accounting/vouchers/<int:pk>/delete/', acc.voucher_delete, name='voucher_delete'),

    # The General Ledger browser
    path('accounting/gl-entries/', acc.GeneralLedgerEntryListView.as_view(), name='gl_entries'),

    # AJAX the voucher form runs on
    path('accounting/api/outstanding/', acc.api_outstanding, name='api_outstanding'),
    path('accounting/api/efd-check/', acc.api_efd_check, name='api_efd_check'),
    path('accounting/api/rate/', acc.api_rate, name='api_rate'),
    path('accounting/api/balance/', acc.api_balance, name='api_balance'),

    # Reports
    path('accounting/reports/', acc.ReportIndexView.as_view(), name='report_index'),
    *[path(f'accounting/reports/{slug.replace("_", "-")}/',
           view.as_view(), name=f'report_{slug}')
      for slug, view in acc.REPORT_VIEWS.items()],
    path('taxes/', views.TaxPaymentListView.as_view(), name='tax_payment_list'),
    path('debtors/', views.DebtorListView.as_view(), name='debtors_list'),
    path('receipts/', views.PaymentReceiptListView.as_view(), name='payment_receipt_list'),
    path('banks/', views.BankListView.as_view(), name='bank_list'),
    
    path('api/', include(router.urls)),
]
