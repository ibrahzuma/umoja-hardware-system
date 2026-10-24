"""Delivery cross-check flow: store check -> Afisa Ugavi -> Admin -> stock.

The rule these tests pin down: a short delivery puts *nothing* into stock until
an Admin confirms it, and confirming takes in only what actually arrived while
the balance stays owing on the same order.
"""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.inventory.models import (
    Branch, Category, DeliveryCheck, DeliveryCheckItem, Product, PurchaseOrder,
    PurchaseOrderItem, Stock, Supplier,
)
from apps.users.models import User


class DeliveryFlowTestCase(TestCase):
    """Shared fixture: one PO for 100 cement + 50 iron sheets."""

    def setUp(self):
        self.branch = Branch.objects.create(name='Main Branch')
        self.category = Category.objects.create(name='Construction')
        self.supplier = Supplier.objects.create(name='Kibo Suppliers')
        self.cement = Product.objects.create(
            name='Cement 50kg', category=self.category, price=18000, cost=15000)
        self.iron = Product.objects.create(
            name='Iron Sheets', category=self.category, price=32000, cost=28000)

        self.afisa = User.objects.create_user(username='afisa', password='pw', role='afisa_ugavi')
        self.store_manager = User.objects.create_user(username='sm', password='pw', role='store_manager')
        self.admin = User.objects.create_user(username='boss', password='pw', role='admin')

        self.po = PurchaseOrder.objects.create(
            supplier=self.supplier, branch=self.branch, status='sent', created_by=self.afisa)
        self.cement_item = PurchaseOrderItem.objects.create(
            purchase_order=self.po, product=self.cement, quantity=100, unit_cost=15000)
        self.iron_item = PurchaseOrderItem.objects.create(
            purchase_order=self.po, product=self.iron, quantity=50, unit_cost=28000)

    # --- helpers ---------------------------------------------------------

    def stock_of(self, product):
        stock = Stock.objects.filter(product=product, branch=self.branch).first()
        return stock.quantity if stock else 0

    def confirm(self, user=None):
        self.client.force_login(user or self.afisa)
        return self.client.post(f'/api/purchase-orders/{self.po.id}/confirm/')

    def cross_check(self, cement, iron, user=None, note=''):
        self.client.force_login(user or self.store_manager)
        return self.client.post(
            f'/api/purchase-orders/{self.po.id}/cross_check/',
            {'store_note': note, 'items': [
                {'item': self.cement_item.id, 'delivered_quantity': cement},
                {'item': self.iron_item.id, 'delivered_quantity': iron, 'note': 'not loaded'},
            ]},
            content_type='application/json',
        )

    def comment(self, text='Supplier ran out of sheets.', user=None):
        self.client.force_login(user or self.afisa)
        return self.client.post(f'/api/purchase-orders/{self.po.id}/comment/',
                                {'comment': text}, content_type='application/json')

    def decide(self, decision, note='', user=None):
        self.client.force_login(user or self.admin)
        return self.client.post(f'/api/purchase-orders/{self.po.id}/admin-decision/',
                                {'decision': decision, 'note': note}, content_type='application/json')


class FullDeliveryTest(DeliveryFlowTestCase):
    def test_complete_delivery_needs_no_admin_and_goes_to_stock(self):
        self.confirm()
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, 'awaiting_check')

        response = self.cross_check(100, 50)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['has_discrepancy'])

        self.po.refresh_from_db()
        self.assertEqual(self.po.status, 'received')
        self.assertEqual(self.stock_of(self.cement), 100)
        self.assertEqual(self.stock_of(self.iron), 50)
        self.cement_item.refresh_from_db()
        self.assertEqual(self.cement_item.outstanding, 0)


class ShortDeliveryTest(DeliveryFlowTestCase):
    def test_shortfall_adds_nothing_to_stock_until_admin_confirms(self):
        self.confirm()
        response = self.cross_check(100, 30)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['has_discrepancy'])

        # Flagged and parked with Afisa Ugavi — stock untouched, including the
        # line that arrived in full.
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, 'discrepancy')
        self.assertEqual(self.stock_of(self.cement), 0)
        self.assertEqual(self.stock_of(self.iron), 0)

        self.assertEqual(self.comment().status_code, 200)
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, 'awaiting_admin')
        self.assertEqual(self.stock_of(self.cement), 0)

        self.assertEqual(self.decide('confirm').status_code, 200)
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, 'partial')
        # Only what arrived went in.
        self.assertEqual(self.stock_of(self.cement), 100)
        self.assertEqual(self.stock_of(self.iron), 30)
        self.iron_item.refresh_from_db()
        self.assertEqual(self.iron_item.received_quantity, 30)
        self.assertEqual(self.iron_item.outstanding, 20)

    def test_admin_rejection_returns_to_afisa_with_no_stock_movement(self):
        self.confirm()
        self.cross_check(100, 30)
        self.comment()

        response = self.decide('reject', note='Call the supplier first.')
        self.assertEqual(response.status_code, 200)

        self.po.refresh_from_db()
        self.assertEqual(self.po.status, 'discrepancy')
        self.assertEqual(self.stock_of(self.cement), 0)
        self.assertEqual(self.po.latest_check.decision, 'rejected')

        # Afisa Ugavi can comment again and it goes back to the Admin.
        self.assertEqual(self.comment('Supplier confirmed the balance ships Friday.').status_code, 200)
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, 'awaiting_admin')

    def test_rejection_requires_a_reason(self):
        self.confirm()
        self.cross_check(100, 30)
        self.comment()
        self.assertEqual(self.decide('reject').status_code, 400)


class BalanceDeliveryTest(DeliveryFlowTestCase):
    def test_balance_round_checks_only_what_is_still_owed(self):
        # Round 1: 20 iron sheets short, confirmed by the Admin.
        self.confirm()
        self.cross_check(100, 30)
        self.comment()
        self.decide('confirm')

        # Round 2: the balance arrives and Afisa Ugavi confirms again.
        self.assertEqual(self.confirm().status_code, 200)
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, 'awaiting_check')

        # Only the outstanding item needs checking now.
        self.assertEqual([it.id for it in self.po.outstanding_items()], [self.iron_item.id])

        self.client.force_login(self.store_manager)
        response = self.client.post(
            f'/api/purchase-orders/{self.po.id}/cross_check/',
            {'items': [{'item': self.iron_item.id, 'delivered_quantity': 20}]},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['has_discrepancy'])

        self.po.refresh_from_db()
        self.assertEqual(self.po.status, 'received')
        self.assertEqual(self.stock_of(self.iron), 50)      # 30 + 20, never double-counted
        self.assertEqual(self.stock_of(self.cement), 100)   # untouched by round 2
        self.assertEqual(self.po.checks.count(), 2)

        round_two = self.po.checks.get(round_number=2)
        line = round_two.lines.get()
        self.assertEqual(line.expected_quantity, 20)        # the balance, not the original 50

    def test_a_short_balance_loops_through_the_admin_again(self):
        self.confirm()
        self.cross_check(100, 30)
        self.comment()
        self.decide('confirm')

        self.confirm()
        self.client.force_login(self.store_manager)
        self.client.post(
            f'/api/purchase-orders/{self.po.id}/cross_check/',
            {'items': [{'item': self.iron_item.id, 'delivered_quantity': 5}]},
            content_type='application/json',
        )
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, 'discrepancy')

        self.comment('Still short.')
        self.decide('confirm')
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, 'partial')
        self.assertEqual(self.stock_of(self.iron), 35)
        self.iron_item.refresh_from_db()
        self.assertEqual(self.iron_item.outstanding, 15)


class CrossCheckValidationTest(DeliveryFlowTestCase):
    def test_every_outstanding_item_must_be_counted(self):
        self.confirm()
        self.client.force_login(self.store_manager)
        response = self.client.post(
            f'/api/purchase-orders/{self.po.id}/cross_check/',
            {'items': [{'item': self.cement_item.id, 'delivered_quantity': 100}]},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('every item', response.json()['detail'].lower())
        self.assertEqual(DeliveryCheck.objects.count(), 0)

    def test_over_delivery_is_clamped_to_what_was_owed(self):
        self.confirm()
        self.cross_check(150, 50)
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, 'received')
        self.assertEqual(self.stock_of(self.cement), 100)
        self.cement_item.refresh_from_db()
        self.assertEqual(self.cement_item.received_quantity, 100)

    def test_cross_check_rejected_unless_awaiting_check(self):
        response = self.cross_check(100, 50)   # never confirmed by Afisa Ugavi
        self.assertEqual(response.status_code, 400)

    def test_comment_rejected_unless_flagged(self):
        self.confirm()
        self.assertEqual(self.comment().status_code, 400)

    def test_admin_decision_rejected_unless_awaiting_admin(self):
        self.confirm()
        self.cross_check(100, 30)
        self.assertEqual(self.decide('confirm').status_code, 400)   # no comment yet


class DeliveryPermissionTest(DeliveryFlowTestCase):
    def test_store_manager_cannot_take_the_admin_decision(self):
        self.confirm()
        self.cross_check(100, 30)
        self.comment()
        self.assertEqual(self.decide('confirm', user=self.store_manager).status_code, 403)
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, 'awaiting_admin')
        self.assertEqual(self.stock_of(self.cement), 0)

    def test_afisa_ugavi_cannot_cross_check(self):
        self.confirm()
        self.assertEqual(self.cross_check(100, 30, user=self.afisa).status_code, 403)

    def test_store_manager_cannot_comment_for_afisa_ugavi(self):
        self.confirm()
        self.cross_check(100, 30)
        self.assertEqual(self.comment(user=self.store_manager).status_code, 403)

    def test_approvals_screen_is_admin_only(self):
        url = reverse('inventory:po_delivery_approvals')
        self.client.force_login(self.store_manager)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_check_screens_are_store_manager_only(self):
        self.client.force_login(self.afisa)
        self.assertEqual(self.client.get(reverse('inventory:po_cross_check')).status_code, 403)
        self.assertEqual(
            self.client.get(reverse('inventory:po_cross_check_detail', args=[self.po.id])).status_code, 403)
        self.client.force_login(self.store_manager)
        self.assertEqual(
            self.client.get(reverse('inventory:po_cross_check_detail', args=[self.po.id])).status_code, 200)


class StockMovementReportTest(TestCase):
    """Stock in vs out: one item, its documents, and the balance they leave.

    The ledger is derived, so these tests build the paperwork (a delivery round,
    a dispatched invoice) and the stock it left behind, then check the report
    tells the same story back.
    """

    def setUp(self):
        from apps.sales.models import Sale, SaleItem

        self.branch = Branch.objects.create(name='Main Branch')
        self.category = Category.objects.create(name='Construction')
        self.supplier = Supplier.objects.create(name='Kibo Suppliers')
        self.cement = Product.objects.create(
            name='Cement 50kg', category=self.category, price=18000, cost=15000)

        self.admin = User.objects.create_user(username='boss', password='pw', role='admin')
        self.rep = User.objects.create_user(username='rep', password='pw', role='sales_rep')

        # 100 bags ordered and delivered in full.
        self.po = PurchaseOrder.objects.create(
            supplier=self.supplier, branch=self.branch, status='received', created_by=self.admin)
        item = PurchaseOrderItem.objects.create(
            purchase_order=self.po, product=self.cement, quantity=100,
            unit_cost=15000, received_quantity=100)
        check = DeliveryCheck.objects.create(
            purchase_order=self.po, round_number=1, decision='complete')
        DeliveryCheckItem.objects.create(
            delivery_check=check, item=item, expected_quantity=100, delivered_quantity=100)

        # 30 of them sold and dispatched.
        self.sale = Sale.objects.create(
            invoice_number='INV-0001', branch=self.branch, status='dispatched',
            customer_name='Juma Builders', total_amount=540000, user=self.rep)
        SaleItem.objects.create(
            sale=self.sale, product=self.cement, quantity=30,
            price_at_sale=18000, subtotal=540000)

        Stock.objects.create(product=self.cement, branch=self.branch, quantity=70)

    def _report(self, user=None, **params):
        self.client.force_login(user or self.admin)
        return self.client.get(reverse('inventory:stock_movement_report'), params)

    def test_ledger_shows_each_movement_with_its_reference_and_balance(self):
        response = self._report()
        self.assertEqual(response.status_code, 200)

        items = response.context['items']
        self.assertEqual(len(items), 1)
        ledger = items[0]
        self.assertEqual(ledger['product'], self.cement)

        rows = ledger['rows']
        self.assertEqual(len(rows), 2)

        delivery, sale = rows
        self.assertEqual(delivery.reference, f'PO-{self.po.id:05d}')
        self.assertEqual(delivery.qty_in, 100)
        self.assertEqual(delivery.qty_out, 0)
        self.assertEqual(delivery.balance, 100)

        self.assertEqual(sale.reference, 'INV-0001')
        self.assertEqual(sale.qty_in, 0)
        self.assertEqual(sale.qty_out, 30)
        self.assertEqual(sale.balance, 70)

        self.assertEqual(ledger['total_in'], 100)
        self.assertEqual(ledger['total_out'], 30)
        self.assertEqual(ledger['closing'], 70)
        # Everything on hand is accounted for by the documents.
        self.assertEqual(ledger['opening'], 0)

    def test_stock_the_documents_do_not_explain_shows_as_opening_balance(self):
        """20 bags that predate the records are brought forward, not lost."""
        stock = Stock.objects.get(product=self.cement, branch=self.branch)
        stock.quantity = 90
        stock.save()

        ledger = self._report().context['items'][0]
        self.assertEqual(ledger['opening'], 20)
        self.assertEqual(ledger['rows'][0].balance, 120)
        self.assertEqual(ledger['closing'], 90)

    def test_goods_in_only_hides_the_sale_but_keeps_the_balance(self):
        ledger = self._report(direction='in').context['items'][0]
        references = [row.reference for row in ledger['rows']]
        self.assertEqual(references, [f'PO-{self.po.id:05d}'])
        self.assertEqual(ledger['total_out'], 0)

    def test_goods_out_only_hides_the_delivery(self):
        ledger = self._report(direction='out').context['items'][0]
        references = [row.reference for row in ledger['rows']]
        self.assertEqual(references, ['INV-0001'])
        self.assertEqual(ledger['total_in'], 0)
        # The balance still knows what came in before it.
        self.assertEqual(ledger['rows'][0].balance, 70)

    def test_a_period_with_no_movement_lists_no_items(self):
        response = self._report(date_from='2000-01-01', date_to='2000-01-31')
        self.assertEqual(response.context['items'], [])

    def test_undelivered_order_is_not_in_the_ledger(self):
        """Nothing reaches stock — or the report — before it is signed off."""
        DeliveryCheck.objects.filter(purchase_order=self.po).update(decision='pending_admin')

        ledger = self._report().context['items'][0]
        self.assertEqual([row.reference for row in ledger['rows']], ['INV-0001'])

    def test_only_roles_that_answer_for_stock_can_open_the_report(self):
        self.client.force_login(self.rep)
        response = self.client.get(reverse('inventory:stock_movement_report'))
        self.assertEqual(response.status_code, 403)

        for role in ('accountant', 'stock_controller', 'store_manager', 'store_keeper',
                     'afisa_ugavi', 'manager'):
            user = User.objects.create_user(username=f'u_{role}', password='pw', role=role)
            self.client.force_login(user)
            self.assertEqual(
                self.client.get(reverse('inventory:stock_movement_report')).status_code, 200,
                f'{role} should be able to open the stock report')

    def test_export_returns_a_workbook(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('inventory:stock_movement_export'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('spreadsheetml', response['Content-Type'])
        self.assertIn('stock_in_vs_out', response['Content-Disposition'])


class ProductImportTypeTest(TestCase):
    """The import's Type column, filled the way people really fill it: blank,
    a short name, or a long description. None of it may fail a row or leave a
    product the edit form cannot save."""

    CSV = (
        '﻿Name,SKU,Category,Type,Cost,Price,Weight (kg),Description,Opening Stock,Low Stock Alert\r\n'
        'A/GREY H/GLOSS,,PAINTS,,,,,,42,\r\n'
        'ANDIKA POWDER,,POWDER ,ANDIKA ,28750,32000,25,BAGS ,751,\r\n'
        '"ANGLE LINE 1.25"" X4MM",,ANGLE LINE ,"A/LINE 1.25""X4MM ",34500,40000,,PCS ,131,\r\n'
        'DELIVERY,,TRANSPORT,Service,0,5000,,,,\r\n'
    )

    def setUp(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.admin = User.objects.create_superuser(username='boss', password='pw', role='admin')
        self.client.force_login(self.admin)
        self.upload = lambda: self.client.post('/api/products/import/', {
            'file': SimpleUploadedFile('products.csv', self.CSV.encode('utf-8'), 'text/csv')})

    def test_every_row_imports_with_a_valid_type(self):
        res = self.upload()
        self.assertEqual(res.status_code, 201, res.json())
        self.assertEqual(res.json()['errors'], [])
        types = dict(Product.objects.values_list('name', 'product_type'))
        self.assertEqual(types, {
            'A/GREY H/GLOSS': 'product', 'ANDIKA POWDER': 'product',
            'ANGLE LINE 1.25" X4MM': 'product', 'DELIVERY': 'service'})
        self.assertEqual(Stock.objects.get(product__name='ANDIKA POWDER').quantity, 751)
        self.assertFalse(Stock.objects.filter(product__name='DELIVERY').exists())

    def test_an_imported_product_can_be_edited(self):
        self.upload()
        p = Product.objects.get(name='ANDIKA POWDER')
        res = self.client.put(f'/api/products/{p.id}/', {
            'name': p.name, 'sku': p.sku, 'category': p.category_id, 'product_type': p.product_type,
            'cost': '28750', 'price': '33000', 'weight': '25', 'description': 'BAGS',
        }, content_type='application/json')
        self.assertEqual(res.status_code, 200, res.content)

    def test_re_uploading_repairs_a_product_saved_with_a_bad_type(self):
        cat = Category.objects.create(name='POWDER')
        Product.objects.create(name='ANDIKA POWDER', category=cat, product_type='andika',
                               cost=0, price=0)
        self.upload()
        p = Product.objects.get(name='ANDIKA POWDER')
        self.assertEqual(p.product_type, 'product')
        self.assertEqual(Stock.objects.get(product=p).quantity, 751)


class ProductImportUnitTest(TestCase):
    """The Unit (measuring unit) column: Nos, Kgs, Pcs, Boxes, Tons, Bags."""

    HEADER = 'Name,SKU,Category,Type,Unit,Cost,Price,Weight (kg),Description,Opening Stock,Low Stock Alert\r\n'

    def setUp(self):
        self.admin = User.objects.create_superuser(username='boss', password='pw', role='admin')
        self.client.force_login(self.admin)

    def upload(self, body):
        from django.core.files.uploadedfile import SimpleUploadedFile
        return self.client.post('/api/products/import/', {
            'file': SimpleUploadedFile('p.csv', (self.HEADER + body).encode('utf-8'), 'text/csv')})

    def unit(self, name):
        return Product.objects.get(name=name).unit

    def test_each_unit_and_its_common_spellings(self):
        res = self.upload(
            'CEMENT,,POWDER,,Bags,1,2,,,1,\r\n'
            'NAILS,,NAILS,,kg,1,2,,,1,\r\n'
            'PIPE,,PIPES,,PCS,1,2,,,1,\r\n'
            'BOLT,,BOLTS,,No,1,2,,,1,\r\n'
            'TILES,,TILES,,box,1,2,,,1,\r\n'
            'STEEL,,STEEL,,Tonnes,1,2,,,1,\r\n')
        self.assertEqual(res.json()['errors'], [])
        self.assertEqual([self.unit(n) for n in ('CEMENT', 'NAILS', 'PIPE', 'BOLT', 'TILES', 'STEEL')],
                         ['bags', 'kgs', 'pcs', 'nos', 'boxes', 'tons'])

    def test_a_blank_unit_comes_from_the_description_then_defaults_to_pcs(self):
        self.upload('ANDIKA POWDER,,POWDER,,,1,2,,BAGS ,1,\r\n'
                    'HAMMER,,TOOLS,,,1,2,,Heavy duty,1,\r\n')
        self.assertEqual(self.unit('ANDIKA POWDER'), 'bags')
        self.assertEqual(self.unit('HAMMER'), 'pcs')

    def test_an_unknown_unit_is_reported_not_guessed(self):
        res = self.upload('ROPE,,ROPES,,Metres,1,2,,,1,\r\n')
        self.assertIn("Unit 'Metres' is not one of", res.json()['errors'][0])
        self.assertFalse(Product.objects.filter(name='ROPE').exists())

    def test_re_uploading_with_a_unit_updates_the_product(self):
        self.upload('CEMENT,,POWDER,,,1,2,,,1,\r\n')
        self.assertEqual(self.unit('CEMENT'), 'pcs')
        self.upload('CEMENT,,POWDER,,Bags,1,2,,,0,\r\n')
        self.assertEqual(self.unit('CEMENT'), 'bags')

    def test_the_template_has_the_unit_column(self):
        res = self.client.get('/inventory/import/template/')
        self.assertIn('Unit', res.content.decode().splitlines()[0].split(','))

    def test_the_unit_is_saved_from_the_product_form(self):
        cat = Category.objects.create(name='POWDER')
        res = self.client.post('/api/products/', {
            'name': 'GYPSUM', 'category': cat.id, 'product_type': 'product', 'unit': 'bags',
            'cost': '100', 'price': '200', 'weight': '0'}, content_type='application/json')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()['unit_display'], 'Bags')


class ProductUnitOnDocumentsTest(TestCase):
    """Invoices, delivery notes and quotations print each product's own unit —
    they used to print PCS for everything."""

    def setUp(self):
        from apps.sales.models import Sale, SaleItem
        self.admin = User.objects.create_superuser(username='boss', password='pw', role='admin')
        self.client.force_login(self.admin)
        branch = Branch.objects.create(name='Main')
        cat = Category.objects.create(name='POWDER')
        self.cement = Product.objects.create(name='CEMENT', category=cat, unit='bags', cost=1, price=2)
        Stock.objects.create(product=self.cement, branch=branch, quantity=100)
        self.sale = Sale.objects.create(invoice_number='U-1', branch=branch, total_amount=10,
                                        status='approved')
        SaleItem.objects.create(sale=self.sale, product=self.cement, quantity=5,
                                price_at_sale=2, subtotal=10)

    def test_the_document_unit_is_the_products(self):
        from apps.sales.views import _uom
        self.assertEqual(_uom(self.cement), 'BAGS')

    def test_the_sale_api_carries_the_unit(self):
        res = self.client.get(f'/api/sales/{self.sale.id}/')
        self.assertEqual(res.json()['items_response'][0]['product_unit'], 'Bags')

    def test_the_invoice_and_delivery_note_render(self):
        for action in ('receipt', 'delivery_note'):
            res = self.client.get(f'/api/sales/{self.sale.id}/{action}/')
            self.assertEqual(res.status_code, 200, action)
            self.assertEqual(res['Content-Type'], 'application/pdf', action)

    def test_a_purchase_order_line_can_be_in_nos(self):
        self.assertIn('nos', dict(PurchaseOrderItem.UNIT_CHOICES))


class VehicleLoadTest(TestCase):
    """Goods against a vehicle: an order weighs Σ quantity × what one unit
    weighs. Kgs and Tons weigh what their unit says; a counted product with
    no weight set is unknown, never zero."""

    def setUp(self):
        from apps.sales.models import Sale, SaleItem
        self.admin = User.objects.create_superuser(username='boss', password='pw', role='admin')
        self.client.force_login(self.admin)
        self.branch = Branch.objects.create(name='Main')
        cat = Category.objects.create(name='GOODS')
        make = lambda name, unit, weight=0, kind='product': Product.objects.create(
            name=name, category=cat, unit=unit, weight=weight, cost=1, price=2, product_type=kind)
        self.cement = make('CEMENT', 'bags', 50)
        self.nails = make('NAILS', 'kgs', 7)          # a stray weight on a Kgs product is ignored
        self.steel = make('STEEL', 'tons')
        self.pipe = make('PIPE', 'pcs')               # no weight set
        self.transport = make('TRANSPORT', 'nos', kind='service')
        self.Sale, self.SaleItem = Sale, SaleItem

    def sale(self, *lines):
        sale = self.Sale.objects.create(invoice_number=f'L-{self.Sale.objects.count() + 1}',
                                        branch=self.branch, total_amount=1, status='approved')
        for product, qty in lines:
            self.SaleItem.objects.create(sale=sale, product=product, quantity=qty,
                                         price_at_sale=1, subtotal=qty)
        return sale

    def test_what_one_unit_weighs(self):
        self.assertEqual(self.cement.unit_weight, Decimal('50'))
        self.assertEqual(self.nails.unit_weight, Decimal('1'))
        self.assertEqual(self.steel.unit_weight, Decimal('1000'))
        self.assertIsNone(self.pipe.unit_weight)

    def test_a_fully_weighed_order(self):
        from apps.sales.models import load_of
        sale = self.sale((self.cement, 40), (self.nails, 25), (self.steel, 2), (self.transport, 1))
        kg, missing = load_of(sale.items.all())
        self.assertEqual(kg, Decimal('4025'))       # 2000 + 25 + 2000; the service is not loaded
        self.assertEqual(missing, [])

    def test_an_unweighed_product_is_reported_not_counted_as_zero(self):
        res = self.client.get(f'/api/sales/{self.sale((self.cement, 10), (self.pipe, 30)).id}/')
        body = res.json()
        self.assertEqual(Decimal(str(body['total_weight'])), Decimal('500'))
        self.assertEqual(body['unweighed_items'], ['PIPE'])

    def test_the_delivery_note_renders_with_a_partial_load(self):
        res = self.client.get(f'/api/sales/{self.sale((self.cement, 10), (self.pipe, 3)).id}/delivery_note/')
        self.assertEqual(res.status_code, 200)

    def test_the_import_reads_both_weight_headers(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        for header, name in (('Weight per unit (kg)', 'GYPSUM'), ('Weight (kg)', 'PUTTY')):
            csv = f'Name,Category,Unit,Cost,Price,{header},Opening Stock\r\n{name},POWDER,Bags,1,2,25,1\r\n'
            self.client.post('/api/products/import/', {
                'file': SimpleUploadedFile('p.csv', csv.encode(), 'text/csv')})
            self.assertEqual(Product.objects.get(name=name).unit_weight, Decimal('25'))


class GodownTest(TestCase):
    """Godowns: stock is counted per godown inside a branch. Mwanza has VOIL
    and Shop 1; selling, dispatching, receiving, adjusting and transferring
    all happen in a named godown, and anything naming none uses the default."""

    @classmethod
    def setUpTestData(cls):
        from django.core.management import call_command
        call_command('create_roles', verbosity=0)

    def setUp(self):
        from django.contrib.auth.models import Group
        from apps.inventory.models import Godown
        from apps.inventory.stock import add_stock
        self.Godown = Godown
        self.mwanza = Branch.objects.create(name='Mwanza Branch')
        self.dar = Branch.objects.create(name='Dar Branch')
        self.voil = Godown.objects.create(branch=self.mwanza, name='VOIL')
        self.shop1 = Godown.objects.create(branch=self.mwanza, name='SHOP 1', kind='shop')
        self.godown1 = Godown.objects.create(branch=self.dar, name='Godown1')
        cat = Category.objects.create(name='POWDER')
        self.cement = Product.objects.create(name='CEMENT', category=cat, unit='bags', cost=15000, price=20000)
        add_stock(self.cement, 10, godown=self.voil)
        self.admin = User.objects.create_superuser(username='boss', password='pw', role='admin')
        self.rep = User.objects.create_user(username='rep', password='pw', role='sales_rep',
                                            branch=self.mwanza, godown=self.shop1)
        self.rep.groups.add(Group.objects.get(name='Sales Representative'))

    def qty(self, godown):
        return Stock.objects.filter(product=self.cement, godown=godown).values_list('quantity', flat=True).first() or 0

    def sell(self, user, godown, qty):
        self.client.force_login(user)
        body = {'branch': self.mwanza.id, 'customer_name': 'Walk-in',
                'items': [{'product': self.cement.id, 'quantity': qty}]}
        if godown is not None:
            body['godown'] = godown.id
        return self.client.post('/api/sales/', body, content_type='application/json')

    def test_the_first_godown_is_the_default_and_there_is_only_one(self):
        self.assertTrue(self.voil.is_default)
        self.shop1.refresh_from_db()
        self.assertFalse(self.shop1.is_default)
        self.shop1.is_default = True
        self.shop1.save()
        self.voil.refresh_from_db()
        self.assertFalse(self.voil.is_default)

    def test_a_branch_with_no_godown_gets_a_main_store(self):
        kigoma = Branch.objects.create(name='Kigoma')
        row = Stock.objects.create(product=self.cement, branch=kigoma, quantity=4)
        self.assertEqual(row.godown.name, 'Main Store')
        self.assertTrue(row.godown.is_default)

    def test_stock_is_counted_per_godown_and_summed_per_branch(self):
        from apps.inventory.stock import add_stock
        add_stock(self.cement, 3, godown=self.shop1)
        self.assertEqual((self.qty(self.voil), self.qty(self.shop1)), (10, 3))
        from django.db.models import Sum
        self.assertEqual(Stock.objects.filter(branch=self.mwanza).aggregate(t=Sum('quantity'))['t'], 13)

    def test_a_sale_is_checked_against_its_godown(self):
        self.assertEqual(self.sell(self.admin, self.shop1, 5).status_code, 400)   # SHOP 1 is empty
        self.assertEqual(self.sell(self.admin, self.voil, 5).status_code, 201)
        # 5 of VOIL's 10 are now promised: only 5 more can be sold there.
        self.assertEqual(self.sell(self.admin, self.voil, 6).status_code, 400)

    def test_a_sale_naming_no_godown_uses_the_default(self):
        self.assertEqual(self.sell(self.admin, None, 10).status_code, 201)
        self.assertEqual(self.sell(self.admin, None, 1).status_code, 400)

    def test_a_godown_from_another_branch_is_refused(self):
        res = self.sell(self.admin, self.godown1, 1)
        self.assertEqual(res.status_code, 400)
        self.assertIn('godown', res.json())

    def test_dispatch_takes_the_goods_from_the_sales_godown(self):
        from apps.inventory.stock import add_stock
        from apps.sales.models import Sale
        add_stock(self.cement, 8, godown=self.shop1)
        sale = Sale.objects.get(pk=self.sell(self.admin, self.shop1, 6).json()['id'])
        sale.status = 'approved'
        sale.save()
        res = self.client.post(f'/api/sales/{sale.id}/dispatch_order/',
                               {'store_keeper': self.admin.id, 'lorry_info': 'T 1 AAA'},
                               content_type='application/json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual((self.qty(self.voil), self.qty(self.shop1)), (10, 2))

    def test_a_seller_tied_to_a_shop_sells_from_it(self):
        from apps.inventory.stock import add_stock
        add_stock(self.cement, 4, godown=self.shop1)
        res = self.sell(self.rep, self.voil, 1)
        self.assertEqual(res.status_code, 400)
        self.assertIn('SHOP 1', str(res.json()))
        res = self.sell(self.rep, None, 2)                 # no godown named: theirs is used
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()['godown'], self.shop1.id)
        self.client.force_login(self.rep)
        page = self.client.get('/sales/pos/')
        self.assertContains(page, f'const HOME_GODOWN = {self.shop1.id};')

    def test_a_transfer_moves_stock_between_godowns(self):
        self.client.force_login(self.admin)
        res = self.client.post('/api/transfers/', {
            'product': self.cement.id, 'from_godown': self.voil.id, 'to_godown': self.shop1.id,
            'quantity': 4}, content_type='application/json')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual((self.qty(self.voil), self.qty(self.shop1)), (6, 4))
        # Across branches too, and never more than the godown holds.
        res = self.client.post('/api/transfers/', {
            'product': self.cement.id, 'from_godown': self.voil.id, 'to_godown': self.godown1.id,
            'quantity': 7}, content_type='application/json')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.qty(self.voil), 6)
        res = self.client.post('/api/transfers/', {
            'product': self.cement.id, 'from_godown': self.voil.id, 'to_godown': self.voil.id,
            'quantity': 1}, content_type='application/json')
        self.assertEqual(res.status_code, 400)

    def test_an_adjustment_lands_in_its_godown(self):
        self.client.force_login(self.admin)
        res = self.client.post('/api/stock-adjustments/', {
            'product': self.cement.id, 'branch': self.mwanza.id, 'godown': self.shop1.id,
            'adjustment_type': 'addition', 'quantity': 7, 'reason': 'count'},
            content_type='application/json')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual((self.qty(self.voil), self.qty(self.shop1)), (10, 7))

    def test_a_delivery_is_received_into_the_orders_godown(self):
        from apps.inventory.views import _receive_delivery
        supplier = Supplier.objects.create(name='Kibo')
        po = PurchaseOrder.objects.create(supplier=supplier, branch=self.mwanza, godown=self.shop1,
                                          status='sent', created_by=self.admin)
        item = PurchaseOrderItem.objects.create(purchase_order=po, product=self.cement, quantity=20,
                                                unit_cost=15000)
        check = DeliveryCheck.objects.create(purchase_order=po)
        DeliveryCheckItem.objects.create(delivery_check=check, item=item, expected_quantity=20, delivered_quantity=20)
        _receive_delivery(check)
        self.assertEqual((self.qty(self.voil), self.qty(self.shop1)), (10, 20))

    def test_godowns_api(self):
        self.client.force_login(self.admin)
        res = self.client.post('/api/godowns/', {'branch': self.mwanza.id, 'name': 'ALAF', 'kind': 'godown'},
                               content_type='application/json')
        self.assertEqual(res.status_code, 201, res.content)
        res = self.client.post('/api/godowns/', {'branch': self.mwanza.id, 'name': 'alaf'},
                               content_type='application/json')
        self.assertEqual(res.status_code, 400)                       # same name in the branch
        res = self.client.post('/api/godowns/', {'branch': self.dar.id, 'name': 'ALAF'},
                               content_type='application/json')
        self.assertEqual(res.status_code, 201)                       # fine in another branch
        self.assertEqual(self.client.delete(f'/api/godowns/{self.voil.id}/').status_code, 400)  # holds stock
        self.assertEqual(self.client.delete(f'/api/godowns/{self.shop1.id}/').status_code, 204)

    def test_the_seller_sees_godowns(self):
        self.client.force_login(self.rep)
        res = self.client.get('/api/godowns/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.client.post('/api/godowns/', {'branch': self.mwanza.id, 'name': 'X'},
                                          content_type='application/json').status_code, 403)
