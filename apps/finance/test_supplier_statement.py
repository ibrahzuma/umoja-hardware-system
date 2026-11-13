from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.inventory.models import Branch, PurchaseOrder, Supplier
from apps.users.models import User

from .models import SupplierPayment
from .supplier_statement import statement


class SupplierStatementTest(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(name='Main')
        self.supplier = Supplier.objects.create(name='Steel Ltd')
        self.po1 = PurchaseOrder.objects.create(supplier=self.supplier, branch=self.branch,
                                                total_amount=Decimal('10000'))
        self.po2 = PurchaseOrder.objects.create(supplier=self.supplier, branch=self.branch,
                                                total_amount=Decimal('10000'))
        PurchaseOrder.objects.create(supplier=self.supplier, branch=self.branch,
                                     total_amount=Decimal('99999'), status='cancelled')
        today = date.today()
        # Overpay the first order, then spend the overpayment on the second.
        SupplierPayment.objects.create(supplier=self.supplier, purchase_order=self.po1,
                                       amount=Decimal('20000'), payment_date=today, status='paid')
        SupplierPayment.objects.create(supplier=self.supplier, purchase_order=self.po2,
                                       amount=Decimal('10000'), payment_date=today, status='paid',
                                       from_credit=True)
        SupplierPayment.objects.create(supplier=self.supplier, amount=Decimal('500'),
                                       payment_date=today, status='pending')

    def test_balance_counts_orders_against_cash_paid_once(self):
        data = statement(self.supplier)
        self.assertEqual(data['ordered_total'], Decimal('20000'))
        self.assertEqual(data['paid_total'], Decimal('20000'))
        self.assertEqual(data['closing'], Decimal('0'))
        self.assertEqual(data['pending_total'], Decimal('500'))
        self.assertEqual(len(data['rows']), 4)

    def test_afisa_ugavi_can_open_it(self):
        u = User.objects.create_user(username='rob', password='pw', role='afisa_ugavi')
        self.client.force_login(u)
        url = reverse('finance:supplier_statements')
        self.assertContains(self.client.get(url), 'Steel Ltd')
        page = self.client.get(url, {'supplier': self.supplier.pk})
        self.assertContains(page, f'PO #{self.po1.pk}')
        self.assertContains(page, 'Awaiting Admin approval')
        self.assertContains(self.client.get('/'), 'Supplier Statements')

    def test_other_roles_are_refused(self):
        u = User.objects.create_user(username='rep', password='pw', role='sales_rep')
        self.client.force_login(u)
        self.assertEqual(self.client.get(reverse('finance:supplier_statements')).status_code, 403)
