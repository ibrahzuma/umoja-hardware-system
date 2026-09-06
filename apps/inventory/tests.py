"""Delivery cross-check flow: store check -> Afisa Ugavi -> Admin -> stock.

The rule these tests pin down: a short delivery puts *nothing* into stock until
an Admin confirms it, and confirming takes in only what actually arrived while
the balance stays owing on the same order.
"""

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
