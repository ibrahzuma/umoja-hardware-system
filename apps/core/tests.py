"""Sidebar navigation invariants.

The sidebar is one shared partial rendered for every role, and its collapse
menus are wired by id. A duplicated id or a toggle pointing at a menu that does
not exist makes Bootstrap open the wrong menu — or several at once — so those
two things are checked here rather than found in the browser.
"""

import re

from django.test import TestCase
from django.urls import reverse

from apps.inventory.models import Branch
from apps.users.models import User

MENU_ID = re.compile(r'<ul class="collapse[^"]*" id="([A-Za-z]+)"')
TOGGLE_TARGET = re.compile(r'<a href="#([A-Za-z]+)" data-bs-toggle="collapse"')


class SidebarStructureTest(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(name='Main Branch')
        # A superuser sees every section, so one render covers the whole sidebar.
        self.admin = User.objects.create_superuser(
            username='root', password='pw', email='root@example.com')
        self.client.force_login(self.admin)
        self.html = self.client.get(reverse('dashboard')).content.decode()

    def test_menu_ids_are_unique(self):
        """Two menus sharing an id would make one toggle open both."""
        ids = MENU_ID.findall(self.html)
        self.assertTrue(ids, 'no collapse menus found — did the sidebar markup change?')
        duplicates = {i for i in ids if ids.count(i) > 1}
        self.assertFalse(duplicates, f'duplicate sidebar menu ids: {duplicates}')

    def test_every_toggle_points_at_a_menu_that_exists(self):
        ids = set(MENU_ID.findall(self.html))
        for target in TOGGLE_TARGET.findall(self.html):
            self.assertIn(target, ids, f'toggle #{target} has no matching menu')

    def test_workspaces_come_before_the_general_sections(self):
        """A role user's own workspace is their daily work — it stays at the top."""
        self.assertLess(
            self.html.index('My Workspace'), self.html.index('>Catalog<'),
            'the workspace section should be rendered above Catalog',
        )


class SidebarRoleVisibilityTest(TestCase):
    """Spot-check that role menus are not shown to roles that cannot use them."""

    def setUp(self):
        Branch.objects.create(name='Main Branch')

    def render_for(self, role):
        user = User.objects.create_user(username=f'u_{role}', password='pw', role=role)
        self.client.force_login(user)
        response = self.client.get(reverse('dashboard'), follow=True)
        return response.content.decode()

    def test_store_keeper_sees_their_workspace_only(self):
        html = self.render_for('store_keeper')
        self.assertIn('storeKeeperSubmenu', html)
        self.assertNotIn('accountantSubmenu', html)
        self.assertNotIn('usersSubmenu', html)

    def test_delivery_approvals_is_admin_only(self):
        approvals = reverse('inventory:po_delivery_approvals')
        self.assertNotIn(approvals, self.render_for('store_manager'))

        admin = User.objects.create_user(username='boss', password='pw', role='admin')
        self.client.force_login(admin)
        self.assertIn(approvals, self.client.get(reverse('dashboard')).content.decode())


class DashboardOverviewTest(TestCase):
    """The operations dashboard's figures (/api/dashboard/): each period beside
    the one before it, cancelled sales left out, money shown only to those who
    may see it."""

    def setUp(self):
        from datetime import timedelta
        from decimal import Decimal
        from django.utils import timezone
        from apps.inventory.models import Category, Product, Stock
        from apps.sales.models import Customer, Sale, SaleItem, Transaction
        self.Decimal = Decimal
        self.branch = Branch.objects.create(name='Main')
        self.admin = User.objects.create_superuser(username='boss', password='pw', role='admin')
        cat = Category.objects.create(name='POWDER')
        self.cement = Product.objects.create(name='CEMENT', category=cat, unit='bags', cost=15000, price=20000)
        Stock.objects.create(product=self.cement, branch=self.branch, quantity=40)
        kibo = Customer.objects.create(name='Kibo')
        now = timezone.now()

        def sale(number, qty, when, status='approved', customer=None, paid=0):
            s = Sale.objects.create(invoice_number=number, branch=self.branch, customer=customer,
                                    total_amount=Decimal(qty * 20000), status=status)
            Sale.objects.filter(pk=s.pk).update(created_at=when)
            SaleItem.objects.create(sale=s, product=self.cement, quantity=qty,
                                    price_at_sale=20000, subtotal=qty * 20000)
            if paid:
                Transaction.objects.create(sale=s, amount=Decimal(paid))
            return s

        sale('D-1', 5, now, customer=kibo, paid=100000)          # this period, paid
        sale('D-2', 2, now, paid=10000)                           # this period, part paid
        sale('D-3', 9, now, status='cancelled')                   # never counts
        sale('D-4', 1, now - timedelta(days=40), customer=kibo)   # last period, on credit

    def get(self, user, **params):
        self.client.force_login(user)
        res = self.client.get('/api/dashboard/', {'days': 30, **params})
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()

    def test_each_figure_beside_last_period(self):
        d = self.get(self.admin)
        D = self.Decimal
        self.assertEqual(d['kpis']['orders'], {'now': 2, 'before': 1})
        self.assertEqual(D(str(d['kpis']['revenue']['now'])), D('140000'))
        self.assertEqual(D(str(d['kpis']['revenue']['before'])), D('20000'))
        # 7 bags at 20,000 less 7 at 15,000.
        self.assertEqual(D(str(d['profit']['now'])), D('35000'))
        self.assertEqual(len(d['profit']['series']), 30)
        self.assertEqual(len(d['profit']['previous_series']), 30)

    def test_settlement_best_sellers_and_repeat(self):
        d = self.get(self.admin)
        self.assertEqual(d['settlement']['paid']['count'], 1)
        self.assertEqual(d['settlement']['part']['count'], 1)
        self.assertEqual(d['settlement']['credit']['count'], 0)
        best = d['best_sellers'][0]
        self.assertEqual((best['name'], best['sold'], best['unit'], best['stock']), ('CEMENT', 7, 'Bags', 40))
        # Kibo bought last period too.
        self.assertEqual(d['repeat'], {'rate': 100.0, 'buyers': 1, 'returning': 1})
        self.assertEqual(sum(d['weekdays']), 2)

    def test_money_is_only_for_those_who_may_see_it(self):
        keeper = User.objects.create_user(username='sm', password='pw', role='store_manager')
        d = self.get(keeper)
        self.assertFalse(d['can_see_money'])
        self.assertNotIn('revenue', d['kpis'])
        self.assertNotIn('profit', d)
        self.assertNotIn('revenue', d['best_sellers'][0])
        self.assertNotIn('amount', d['settlement']['paid'])

    def test_the_page_renders_the_new_dashboard(self):
        self.client.force_login(self.admin)
        res = self.client.get('/')
        self.assertContains(res, 'id="dash"')
        self.assertContains(res, 'id="dashProfitChart"')
        self.assertContains(res, 'dashboard_overview.js')

    def test_a_calendar_month_compares_with_the_month_before(self):
        from datetime import date
        from apps.core.dashboard import period_for
        self.assertEqual(period_for({'range': 'month'}, today=date(2026, 10, 14)),
                         (date(2026, 10, 1), date(2026, 10, 14), date(2026, 9, 1), date(2026, 9, 14)))
        self.assertEqual(period_for({'range': 'last_month'}, today=date(2026, 3, 10)),
                         (date(2026, 2, 1), date(2026, 2, 28), date(2026, 1, 1), date(2026, 1, 31)))
        start, end, ps, pe = period_for({'days': '7'}, today=date(2026, 10, 14))
        self.assertEqual((start, end, ps, pe),
                         (date(2026, 10, 8), date(2026, 10, 14), date(2026, 10, 1), date(2026, 10, 7)))
