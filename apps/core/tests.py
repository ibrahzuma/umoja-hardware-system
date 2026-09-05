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
