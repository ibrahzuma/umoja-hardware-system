"""The sale API says what Accounts did with a sale, so Sales History can tell
"paid at the till" apart from "in a cash book"."""
from decimal import Decimal

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.finance.models import LedgerAccount, SalesLedgerEntry
from apps.inventory.models import Branch
from apps.sales.models import Sale, Transaction
from apps.users.models import User


class SaleAccountsStatusTest(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(name='Main')
        self.admin = User.objects.create_superuser('root', 'r@example.com', 'pw')
        self.client.force_login(self.admin)

    def _sale(self, inv, total=Decimal('4800000')):
        sale = Sale.objects.create(invoice_number=inv, user=self.admin, branch=self.branch,
                                   total_amount=total, status='dispatched')
        Transaction.objects.create(sale=sale, amount=total, payment_method='cash',
                                   transaction_type='income')
        return sale

    def _accounts(self, sale):
        return self.client.get(f'/api/sales/{sale.pk}/').json()['accounts']

    def test_paid_at_till_but_not_posted_is_waiting(self):
        sale = self._sale('INV1')
        self.assertEqual(self._accounts(sale)['status'], 'pending')

    def test_posted_names_the_cash_book_and_amount_confirmed(self):
        sale = self._sale('INV2')
        book = LedgerAccount.objects.create(code='CB-T1', name='Cash Book - SHOP 2', kind='cash')
        entry, _ = SalesLedgerEntry.objects.get_or_create(
            invoice_number='INV2', defaults={'sale': sale, 'sale_date': timezone.localdate()})
        SalesLedgerEntry.objects.filter(pk=entry.pk).update(
            sale=sale, status='posted', payment_status='confirmed', confirmed_method='cash',
            confirmed_amount=Decimal('4780000'), confirmed_account=book,
            posted_by=self.admin, posted_at=timezone.now())
        a = self._accounts(sale)
        self.assertEqual(a['status'], 'posted')
        self.assertEqual(a['account'], 'Cash Book - SHOP 2')
        self.assertEqual(Decimal(a['confirmed_amount']), Decimal('4780000'))
        self.assertEqual(a['method'], 'Cash')

    def test_list_does_not_query_per_sale(self):
        for i in range(3):
            self._sale(f'A{i}')
        with CaptureQueriesContext(connection) as few:
            self.client.get('/api/sales/')
        for i in range(6):
            self._sale(f'B{i}')
        with CaptureQueriesContext(connection) as many:
            self.client.get('/api/sales/')
        self.assertEqual(len(few), len(many))
