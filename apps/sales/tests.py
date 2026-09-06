"""POS customer capture, and newest-first ordering of the dated lists."""

from decimal import Decimal

from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase

from apps.inventory.models import Branch, Category, Product, PurchaseOrder, Supplier
from apps.sales.models import Customer, Quotation, Sale, Transaction
from apps.users.models import User


class PosCustomerCaptureTest(TestCase):
    """At the till the cashier types a name: an existing customer is matched,
    a new one is registered. Both need the API to cooperate."""

    @classmethod
    def setUpTestData(cls):
        # Real permissions come from the seeded groups, not the role field.
        call_command('create_roles', verbosity=0)

    def setUp(self):
        self.branch = Branch.objects.create(name='Main Branch')
        self.rep = User.objects.create_user(username='till_rep', password='pw', role='sales_rep')
        self.rep.groups.add(Group.objects.get(name='Sales Representative'))
        self.client.force_login(self.rep)

    def test_sales_rep_can_see_the_customers_to_pick_from(self):
        Customer.objects.create(name='Kibo Traders', phone='0700000001')
        response = self.client.get('/api/customers/')
        self.assertEqual(response.status_code, 200)
        names = [c['name'] for c in response.json()]
        self.assertIn('Kibo Traders', names)

    def test_sales_rep_can_register_a_new_customer_from_the_till(self):
        """The POS posts here when the typed name is not already on the system.
        A 403 would strand the cashier mid-sale."""
        response = self.client.post(
            '/api/customers/', {'name': 'Mbeya Hardware', 'phone': '0755123456'},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 201, response.content[:200])
        self.assertTrue(Customer.objects.filter(name='Mbeya Hardware').exists())

    def test_customers_come_back_in_alphabetical_order(self):
        """The till picks from this list by name, so it is ordered by name."""
        for name in ('Zanzibar Stores', 'Arusha Depot', 'Mwanza Const'):
            Customer.objects.create(name=name)
        names = [c['name'] for c in self.client.get('/api/customers/').json()]
        self.assertEqual(names, sorted(names))


class NewestFirstOrderingTest(TestCase):
    """Dated records read newest at the top, wherever they are listed."""

    def setUp(self):
        self.branch = Branch.objects.create(name='Main Branch')
        self.category = Category.objects.create(name='Construction')
        self.supplier = Supplier.objects.create(name='Kibo Suppliers')
        self.product = Product.objects.create(
            name='Cement', category=self.category, price=18000, cost=15000)
        self.admin = User.objects.create_superuser(
            username='order_admin', password='pw', email='a@example.com')
        self.client.force_login(self.admin)

    def test_sales_are_listed_newest_first(self):
        for i in range(3):
            Sale.objects.create(invoice_number=f'INV-{i}', branch=self.branch,
                                total_amount=Decimal('1000'))
        invoices = [s['invoice_number'] for s in self.client.get('/api/sales/').json()]
        self.assertEqual(invoices, ['INV-2', 'INV-1', 'INV-0'])

    def test_purchase_orders_are_listed_newest_first(self):
        made = [PurchaseOrder.objects.create(supplier=self.supplier, branch=self.branch)
                for _ in range(3)]
        ids = [po['id'] for po in self.client.get('/api/purchase-orders/').json()]
        self.assertEqual(ids, [po.id for po in reversed(made)])

    def test_quotations_are_listed_newest_first(self):
        made = [Quotation.objects.create(branch=self.branch, customer_name=f'C{i}')
                for i in range(3)]
        ids = [q['id'] for q in self.client.get('/api/quotations/').json()]
        self.assertEqual(ids, [q.id for q in reversed(made)])

    def test_payments_are_listed_newest_first(self):
        sale = Sale.objects.create(invoice_number='INV-PAY', branch=self.branch,
                                   total_amount=Decimal('3000'))
        made = [Transaction.objects.create(sale=sale, amount=Decimal('1000')) for _ in range(3)]
        ids = [t['id'] for t in self.client.get('/api/transactions/').json()]
        self.assertEqual(ids, [t.id for t in reversed(made)])

    def test_default_ordering_is_declared_on_the_models(self):
        """Ordering lives on the model so every listing inherits it — templates,
        API and reports alike — rather than each view remembering."""
        self.assertEqual(list(Sale._meta.ordering), ['-created_at', '-id'])
        self.assertEqual(list(PurchaseOrder._meta.ordering), ['-created_at', '-id'])
        self.assertEqual(list(Transaction._meta.ordering), ['-created_at', '-id'])
        self.assertEqual(list(Quotation._meta.ordering), ['-created_at', '-id'])
        # Master data stays alphabetical: it is searched by name, not read as a timeline.
        self.assertEqual(list(Customer._meta.ordering), ['name'])
        self.assertEqual(list(Supplier._meta.ordering), ['name'])
