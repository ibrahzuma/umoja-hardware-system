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


class TillChangesTest(TestCase):
    """The trial-run stock switch, a price changed at the till, and the cash
    book or bank a payment is received into."""

    @classmethod
    def setUpTestData(cls):
        call_command('create_roles', verbosity=0)

    def setUp(self):
        from apps.core.models import SystemSettings
        from apps.finance.models import LedgerAccount
        from apps.inventory.models import Stock
        self.settings, _ = SystemSettings.objects.get_or_create(id=1)
        self.branch = Branch.objects.create(name='Main Branch')
        cat = Category.objects.create(name='POWDER')
        self.cement = Product.objects.create(name='CEMENT', category=cat, cost=15000, price=20000)
        self.nails = Product.objects.create(name='NAILS', category=cat, cost=1000, price=2000)
        Stock.objects.create(product=self.cement, branch=self.branch, quantity=5)
        Stock.objects.create(product=self.nails, branch=self.branch, quantity=0)
        self.drawer = LedgerAccount.objects.create(kind='cash', name='Shop Drawer')
        self.revenue = LedgerAccount.objects.create(kind='income', name='Odd Income')
        self.rep = User.objects.create_user(username='till_rep', password='pw', role='sales_rep')
        self.rep.groups.add(Group.objects.get(name='Sales Representative'))
        self.admin = User.objects.create_superuser(username='boss', password='pw', role='admin')
        self.client.force_login(self.rep)

    def trial(self, on):
        self.settings.allow_negative_stock = on
        self.settings.save()

    def sell(self, lines, **payment):
        body = {'branch': self.branch.id, 'customer_name': 'Walk-in Customer',
                'items': [{'product': p.id, 'quantity': q, **({'price_at_sale': pr} if pr is not None else {})}
                          for p, q, pr in lines]}
        if payment:
            body['payment_details'] = payment
        return self.client.post('/api/sales/', body, content_type='application/json')

    # --- 1. trial run --------------------------------------------------------
    def test_out_of_stock_is_refused_outside_the_trial_run(self):
        self.trial(False)
        self.assertEqual(self.sell([(self.nails, 3, None)]).status_code, 400)

    def test_the_trial_run_sells_out_of_stock_items(self):
        self.trial(True)
        res = self.sell([(self.nails, 3, None), (self.cement, 9, None)])
        self.assertEqual(res.status_code, 201, res.content)

    def test_dispatch_in_the_trial_run_takes_stock_below_zero(self):
        from apps.inventory.models import Stock
        self.trial(True)
        sale = Sale.objects.get(id=self.sell([(self.cement, 8, None)]).json()['id'])
        sale.status = 'approved'
        sale.save()
        self.client.force_login(self.admin)
        res = self.client.post(f'/api/sales/{sale.id}/dispatch_order/',
                               {'store_keeper': self.admin.id, 'lorry_info': 'T 123 ABC'},
                               content_type='application/json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(Stock.objects.get(product=self.cement, branch=self.branch).quantity, -3)

    def test_a_refused_dispatch_moves_no_stock(self):
        from apps.inventory.models import Stock
        sale = Sale.objects.create(invoice_number='D-1', branch=self.branch, total_amount=1, status='approved')
        from apps.sales.models import SaleItem
        SaleItem.objects.create(sale=sale, product=self.cement, quantity=2, price_at_sale=1, subtotal=2)
        SaleItem.objects.create(sale=sale, product=self.nails, quantity=4, price_at_sale=1, subtotal=4)
        self.trial(False)
        self.client.force_login(self.admin)
        res = self.client.post(f'/api/sales/{sale.id}/dispatch_order/',
                               {'store_keeper': self.admin.id, 'lorry_info': 'T 123 ABC'},
                               content_type='application/json')
        self.assertEqual(res.status_code, 400)
        # The cement line was fine, but nothing leaves on a refused dispatch.
        self.assertEqual(Stock.objects.get(product=self.cement, branch=self.branch).quantity, 5)

    def test_only_an_admin_flips_the_switch(self):
        self.trial(False)
        manager = User.objects.create_user(username='mgr', password='pw', role='manager')
        self.client.force_login(manager)
        self.client.post('/settings/', {'company_name': 'Umoja', 'tax_rate': '18',
                                        'allow_negative_stock': 'on'})
        self.settings.refresh_from_db()
        self.assertFalse(self.settings.allow_negative_stock)
        self.client.force_login(self.admin)
        self.client.post('/settings/', {'company_name': 'Umoja', 'tax_rate': '18',
                                        'allow_negative_stock': 'on'})
        self.settings.refresh_from_db()
        self.assertTrue(self.settings.allow_negative_stock)

    # --- 2. price ------------------------------------------------------------
    def test_the_seller_can_change_the_price(self):
        res = self.sell([(self.cement, 2, 18500)])
        self.assertEqual(res.status_code, 201, res.content)
        sale = Sale.objects.get(id=res.json()['id'])
        self.assertEqual(sale.items.get().price_at_sale, Decimal('18500'))
        self.assertEqual(sale.total_amount, Decimal('37000'))

    def test_a_negative_price_is_refused(self):
        self.assertEqual(self.sell([(self.cement, 1, -5)]).status_code, 400)

    # --- 4. received into ----------------------------------------------------
    def test_the_payment_records_where_the_money_went(self):
        res = self.sell([(self.cement, 1, None)], method='cash', amount=20000, account=self.drawer.id)
        self.assertEqual(res.status_code, 201, res.content)
        txn = Transaction.objects.get(sale_id=res.json()['id'])
        self.assertEqual(txn.deposit_to, self.drawer)

    def test_money_cannot_be_received_into_an_income_ledger(self):
        res = self.sell([(self.cement, 1, None)], method='cash', amount=20000, account=self.revenue.id)
        self.assertEqual(res.status_code, 400)
        self.assertFalse(Sale.objects.exists())

    def test_the_seller_sees_the_cash_books_and_banks(self):
        rows = self.client.get('/api/transactions/money_accounts/').json()
        names = {r['name'] for r in rows}
        self.assertIn('Shop Drawer', names)
        self.assertNotIn('Odd Income', names)
        self.assertTrue(all(r['kind'] in ('cash', 'bank') for r in rows))

    def test_the_pos_page_renders(self):
        res = self.client.get('/sales/pos/')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'id="itemPicker"')
        self.assertContains(res, 'id="receivedInto"')
        self.assertContains(res, 'id="walkinName"')


class WalkinNameIsReferenceOnlyTest(TestCase):
    """A name typed for a walk-in at the till is a reference on the sale only. It
    is NOT turned into a sales.Customer and so never reaches the chart of
    accounts. Only a customer the seller actually picks is linked and on file."""

    @classmethod
    def setUpTestData(cls):
        call_command('create_roles', verbosity=0)

    def setUp(self):
        from apps.inventory.models import Stock
        self.branch = Branch.objects.create(name='Main Branch')
        cat = Category.objects.create(name='POWDER')
        self.cement = Product.objects.create(name='CEMENT', category=cat, cost=15000, price=20000)
        Stock.objects.create(product=self.cement, branch=self.branch, quantity=50)
        self.rep = User.objects.create_user(username='till_rep', password='pw', role='sales_rep')
        self.rep.groups.add(Group.objects.get(name='Sales Representative'))
        self.client.force_login(self.rep)

    def sell(self, **extra):
        body = {'branch': self.branch.id,
                'items': [{'product': self.cement.id, 'quantity': 1}], **extra}
        return self.client.post('/api/sales/', body, content_type='application/json')

    def test_a_typed_walkin_name_stays_a_reference(self):
        from apps.finance.models import LedgerAccount
        before = Customer.objects.count()
        res = self.sell(customer_name='Juma Mason')
        self.assertEqual(res.status_code, 201, res.content)
        sale = Sale.objects.get(id=res.json()['id'])
        self.assertEqual(sale.customer_name, 'Juma Mason')          # kept on the sale
        self.assertIsNone(sale.customer)                            # but not a Customer
        self.assertEqual(Customer.objects.count(), before)         # nobody created
        self.assertFalse(LedgerAccount.objects.filter(             # not on the chart of accounts
            kind='customer', name__iexact='Juma Mason').exists())

    def test_an_anonymous_walkin_creates_nobody(self):
        before = Customer.objects.count()
        res = self.sell(customer_name='Walk-in Customer')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(Customer.objects.count(), before)
        self.assertIsNone(Sale.objects.get(id=res.json()['id']).customer)

    def test_a_typed_name_is_not_linked_even_if_it_matches_a_customer(self):
        # Reference only: a typed name never auto-links. To bill a known
        # customer the seller picks them (see the next test).
        Customer.objects.create(name='Existing Co')
        res = self.sell(customer_name='existing co')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(Customer.objects.filter(name__iexact='existing co').count(), 1)
        self.assertIsNone(Sale.objects.get(id=res.json()['id']).customer)

    def test_a_picked_customer_is_linked_to_the_sale(self):
        picked = Customer.objects.create(name='Picked Ltd')
        res = self.sell(customer=picked.id, customer_name='Picked Ltd')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(Sale.objects.get(id=res.json()['id']).customer, picked)


class ConvertPaymentTest(TestCase):
    """A saved sale can be moved between paid (cash/bank) and credit from the
    sales list, by adding or removing its payments."""

    @classmethod
    def setUpTestData(cls):
        call_command('create_roles', verbosity=0)

    def setUp(self):
        from apps.inventory.models import Stock
        self.branch = Branch.objects.create(name='Main Branch')
        cat = Category.objects.create(name='POWDER')
        self.cement = Product.objects.create(name='CEMENT', category=cat, cost=15000, price=20000)
        Stock.objects.create(product=self.cement, branch=self.branch, quantity=50)
        self.mgr = User.objects.create_user(username='boss', password='pw', role='admin', is_superuser=True)
        self.client.force_login(self.mgr)

    def _make_sale(self, paid=False):
        body = {'branch': self.branch.id, 'customer_name': 'Walk-in Customer',
                'items': [{'product': self.cement.id, 'quantity': 1}]}
        if paid:
            body['payment_details'] = {'amount': '20000', 'method': 'cash'}
        res = self.client.post('/api/sales/', body, content_type='application/json')
        self.assertEqual(res.status_code, 201, res.content)
        return Sale.objects.get(id=res.json()['id'])

    def test_cash_sale_can_be_moved_to_credit(self):
        sale = self._make_sale(paid=True)
        self.assertTrue(sale.transactions.exists())
        res = self.client.post(f'/api/sales/{sale.id}/set_payment/',
                               {'mode': 'credit'}, content_type='application/json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertFalse(sale.transactions.exists())           # money reversed

    def test_credit_sale_can_be_marked_paid(self):
        sale = self._make_sale(paid=False)
        self.assertEqual(sale.transactions.count(), 0)
        res = self.client.post(f'/api/sales/{sale.id}/set_payment/',
                               {'mode': 'paid', 'method': 'cash'},
                               content_type='application/json')
        self.assertEqual(res.status_code, 200, res.content)
        paid = sum(t.amount for t in sale.transactions.all())
        self.assertEqual(paid, sale.total_amount)              # balance recorded

    def test_marking_an_already_paid_sale_paid_adds_nothing(self):
        sale = self._make_sale(paid=True)
        before = sale.transactions.count()
        res = self.client.post(f'/api/sales/{sale.id}/set_payment/',
                               {'mode': 'paid', 'method': 'cash'},
                               content_type='application/json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(sale.transactions.count(), before)

    def test_converting_to_credit_can_assign_a_customer(self):
        sale = self._make_sale(paid=True)
        self.assertIsNone(sale.customer)                       # a walk-in reference
        debtor = Customer.objects.create(name='Abdul Hardware')
        res = self.client.post(f'/api/sales/{sale.id}/set_payment/',
                               {'mode': 'credit', 'customer': debtor.id},
                               content_type='application/json')
        self.assertEqual(res.status_code, 200, res.content)
        sale.refresh_from_db()
        self.assertEqual(sale.customer, debtor)                # pinned to a real debtor
        self.assertEqual(sale.customer_name, 'Abdul Hardware')
        self.assertFalse(sale.transactions.exists())

    def test_an_unknown_customer_is_refused(self):
        sale = self._make_sale(paid=True)
        res = self.client.post(f'/api/sales/{sale.id}/set_payment/',
                               {'mode': 'credit', 'customer': 999999},
                               content_type='application/json')
        self.assertEqual(res.status_code, 400, res.content)

    def test_a_sales_rep_cannot_convert(self):
        sale = self._make_sale(paid=True)
        rep = User.objects.create_user(username='rep2', password='pw', role='sales_rep')
        rep.groups.add(Group.objects.get(name='Sales Representative'))
        self.client.force_login(rep)
        res = self.client.post(f'/api/sales/{sale.id}/set_payment/',
                               {'mode': 'credit'}, content_type='application/json')
        self.assertEqual(res.status_code, 403, res.content)


class DeliveryNoteShowsReferenceNumbersTest(TestCase):
    """The delivery note prints LP No., PI No. and Order No. when the sale
    carries them — the same references the invoice shows."""

    def test_the_numbers_render_when_present(self):
        from django.template.loader import render_to_string
        from django.utils import timezone
        html = render_to_string('sales/pdf_delivery_note.html', {
            'doc': {'type': 'DELIVERY NOTE', 'number_label': 'Delivery No.',
                    'number': 'INV1', 'invoice_ref': 'INV1',
                    'lp_number': 'LP-7', 'pi_number': 'PI-9', 'order_number': 'ORD-3',
                    'date': timezone.now(), 'page': '1/1'},
            'company': {}, 'customer': {}, 'items': [], 'currency': 'TZS',
        })
        for label, value in [('LP No.', 'LP-7'), ('PI No.', 'PI-9'), ('Order No.', 'ORD-3')]:
            self.assertIn(label, html)
            self.assertIn(value, html)

    def test_the_rows_are_hidden_when_blank(self):
        from django.template.loader import render_to_string
        from django.utils import timezone
        html = render_to_string('sales/pdf_delivery_note.html', {
            'doc': {'type': 'DELIVERY NOTE', 'number_label': 'Delivery No.',
                    'number': 'INV1', 'date': timezone.now(), 'page': '1/1'},
            'company': {}, 'customer': {}, 'items': [], 'currency': 'TZS',
        })
        self.assertNotIn('LP No.', html)
        self.assertNotIn('PI No.', html)


class StoreKeeperDispatchTest(TestCase):
    """A store keeper can open the dispatch board and dispatch an approved
    order — the loading-out job is theirs."""

    @classmethod
    def setUpTestData(cls):
        call_command('create_roles', verbosity=0)

    def setUp(self):
        from apps.inventory.models import Stock
        self.branch = Branch.objects.create(name='Main Branch')
        cat = Category.objects.create(name='POWDER')
        self.cement = Product.objects.create(name='CEMENT', category=cat, cost=15000, price=20000)
        Stock.objects.create(product=self.cement, branch=self.branch, quantity=50)
        self.keeper = User.objects.create_user(username='keeper', password='pw', role='store_keeper')
        self.keeper.groups.add(Group.objects.get(name='Store Keeper'))

    def _approved_sale(self):
        sale = Sale.objects.create(branch=self.branch, customer_name='Walk-in Customer',
                                   invoice_number='DISP1', status='approved', total_amount=20000)
        from apps.sales.models import SaleItem
        SaleItem.objects.create(sale=sale, product=self.cement, quantity=1,
                                price_at_sale=20000, subtotal=20000)
        return sale

    def test_store_keeper_sees_the_dispatch_board(self):
        self.client.force_login(self.keeper)
        res = self.client.get('/sales/dispatch/')
        self.assertEqual(res.status_code, 200)

    def test_store_keeper_can_dispatch_an_approved_order(self):
        sale = self._approved_sale()
        self.client.force_login(self.keeper)
        res = self.client.post(f'/api/sales/{sale.id}/dispatch_order/',
                               {'store_keeper': self.keeper.id, 'lorry_info': 'TZ 123 ABC'},
                               content_type='application/json')
        self.assertEqual(res.status_code, 200, res.content)
        sale.refresh_from_db()
        self.assertEqual(sale.status, 'dispatched')

    def test_a_sales_rep_still_cannot_dispatch(self):
        sale = self._approved_sale()
        rep = User.objects.create_user(username='rep3', password='pw', role='sales_rep')
        rep.groups.add(Group.objects.get(name='Sales Representative'))
        self.client.force_login(rep)
        res = self.client.post(f'/api/sales/{sale.id}/dispatch_order/',
                               {'store_keeper': rep.id, 'lorry_info': 'TZ 123 ABC'},
                               content_type='application/json')
        self.assertEqual(res.status_code, 403, res.content)
