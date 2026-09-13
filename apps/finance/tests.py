"""The books: voucher entry and the General Ledger.

What these pin down, straight from the voucher data-entry requirements:

* a voucher posts only when Total Debit = Total Credit;
* each voucher type offers the right ledgers on each side — Receipts debit a
  bank account or cash book and credit anything else, Payments the reverse,
  Contra keeps both sides in money ledgers, a Journal may use any ledger;
* a customer credited on a Receipt (or a supplier debited on a Payment) can
  have the amount allocated to their outstanding invoices, never past what
  is owed;
* voucher numbers are generated, and every posted voucher writes one General
  Ledger entry per line — and cancelling takes exactly those back out.
"""

from decimal import Decimal

from django.test import TestCase

from apps.inventory.models import Branch, PurchaseOrder, Supplier
from apps.sales.models import Customer, Sale, Transaction
from apps.users.models import User
from .models import (
    BankAccount, GeneralLedgerEntry, LedgerAccount, SupplierPayment, Voucher,
)
from . import vouchers


class LedgerTestCase(TestCase):

    def setUp(self):
        self.accountant = User.objects.create_user(username='acc', password='pw', role='accountant')
        self.cashier = User.objects.create_user(username='till', password='pw', role='cashier')
        self.branch = Branch.objects.create(name='Main')

        # Bank accounts, customers and suppliers grow a ledger on save.
        self.bank = BankAccount.objects.create(name='CRDB Main')
        self.customer = Customer.objects.create(name='Mwananchi Traders')
        self.supplier = Supplier.objects.create(name='Kibo Suppliers')

        vouchers.sync_chart_of_accounts()
        self.bank_ledger = self.bank.ledger
        self.customer_ledger = self.customer.ledger
        self.supplier_ledger = self.supplier.ledger
        self.cash = LedgerAccount.objects.get(name='Main Cash Book')
        self.sales = LedgerAccount.objects.get(name='Sales Revenue')
        self.rent = LedgerAccount.objects.get(name='Rent')

    # --- helpers -----------------------------------------------------------

    def post(self, voucher_type, lines, date='2026-09-10', description='', user=None):
        self.client.force_login(user or self.accountant)
        return self.client.post('/api/vouchers/', {
            'voucher_type': voucher_type, 'date': date, 'description': description, 'lines': lines,
        }, content_type='application/json')

    @staticmethod
    def line(side, account, amount, **extra):
        return {'side': side, 'account': account.id, 'amount': str(amount), **extra}

    def invoice(self, total, paid=0, number='INV-1'):
        sale = Sale.objects.create(invoice_number=number, branch=self.branch, customer=self.customer,
                                   customer_name=self.customer.name, total_amount=total, status='approved')
        if paid:
            Transaction.objects.create(sale=sale, amount=paid, payment_method='cash')
        return sale


class ChartOfAccountsTest(LedgerTestCase):

    def test_bank_customer_and_supplier_get_ledgers_of_the_right_kind(self):
        self.assertEqual(self.bank_ledger.kind, 'bank')
        self.assertEqual(self.customer_ledger.kind, 'customer')
        self.assertEqual(self.supplier_ledger.kind, 'supplier')
        self.assertTrue(self.bank_ledger.code.startswith('BK-'))

    def test_renaming_a_customer_renames_the_ledger(self):
        self.customer.name = 'Mwananchi Traders Ltd'
        self.customer.save()
        self.customer_ledger.refresh_from_db()
        self.assertEqual(self.customer_ledger.name, 'Mwananchi Traders Ltd')

    def test_sync_is_idempotent(self):
        before = LedgerAccount.objects.count()
        self.assertEqual(vouchers.sync_chart_of_accounts(), 0)
        self.assertEqual(LedgerAccount.objects.count(), before)

    def test_dropdown_groups(self):
        self.client.force_login(self.accountant)
        money = {a['kind'] for a in self.client.get('/api/ledger-accounts/?group=money').json()}
        other = {a['kind'] for a in self.client.get('/api/ledger-accounts/?group=non_money').json()}
        self.assertEqual(money, {'bank', 'cash'})
        self.assertFalse(other & {'bank', 'cash'})
        self.assertIn('customer', other)

    def test_only_accounts_and_admins_may_use_the_books(self):
        self.client.force_login(self.cashier)
        self.assertEqual(self.client.get('/api/ledger-accounts/').status_code, 403)
        self.assertEqual(self.client.get('/finance/vouchers/').status_code, 403)
        self.client.force_login(self.accountant)
        self.assertEqual(self.client.get('/finance/vouchers/').status_code, 200)
        self.assertEqual(self.client.get('/finance/vouchers/new/receipt/').status_code, 200)
        self.assertEqual(self.client.get('/finance/general-ledger/').status_code, 200)
        self.assertEqual(self.client.get('/finance/accounts/').status_code, 200)


class VoucherPostingTest(LedgerTestCase):

    def test_receipt_posts_and_writes_the_general_ledger(self):
        res = self.post('receipt', [
            self.line('debit', self.bank_ledger, 1000000),
            self.line('credit', self.customer_ledger, 800000),
            self.line('credit', self.sales, 200000),
        ], description='Cash and a sale')
        self.assertEqual(res.status_code, 201, res.content)
        data = res.json()
        self.assertEqual(data['number'], 'RV-000001')
        self.assertEqual(Decimal(data['total']), Decimal('1000000'))

        entries = GeneralLedgerEntry.objects.filter(voucher_id=data['id'])
        self.assertEqual(entries.count(), 3)
        self.assertEqual(sum(e.debit for e in entries), Decimal('1000000'))
        self.assertEqual(sum(e.credit for e in entries), Decimal('1000000'))
        bank_row = entries.get(account=self.bank_ledger)
        self.assertEqual((bank_row.debit, bank_row.credit), (Decimal('1000000'), Decimal('0')))

    def test_numbers_run_per_type(self):
        self.post('receipt', [self.line('debit', self.bank_ledger, 10), self.line('credit', self.sales, 10)])
        self.post('payment', [self.line('debit', self.rent, 10), self.line('credit', self.bank_ledger, 10)])
        res = self.post('receipt', [self.line('debit', self.cash, 10), self.line('credit', self.sales, 10)])
        self.assertEqual(res.json()['number'], 'RV-000002')
        self.assertEqual(Voucher.objects.get(voucher_type='payment').number, 'PV-000001')

    def test_unbalanced_voucher_is_refused(self):
        res = self.post('receipt', [
            self.line('debit', self.bank_ledger, 1000000),
            self.line('credit', self.customer_ledger, 800000),
        ])
        self.assertEqual(res.status_code, 400)
        self.assertIn('200000', res.json()['lines'])
        self.assertEqual(Voucher.objects.count(), 0)
        self.assertEqual(GeneralLedgerEntry.objects.count(), 0)

    def test_one_sided_voucher_is_refused(self):
        res = self.post('journal', [self.line('debit', self.rent, 100), self.line('debit', self.sales, 100)])
        self.assertEqual(res.status_code, 400)

    def test_receipt_cannot_debit_a_non_money_ledger(self):
        res = self.post('receipt', [self.line('debit', self.rent, 100), self.line('credit', self.sales, 100)])
        self.assertEqual(res.status_code, 400)
        self.assertIn('cannot be debited', res.json()['lines'])

    def test_receipt_cannot_credit_a_bank_account(self):
        res = self.post('receipt', [self.line('debit', self.bank_ledger, 100), self.line('credit', self.cash, 100)])
        self.assertEqual(res.status_code, 400)

    def test_payment_sides(self):
        ok = self.post('payment', [self.line('debit', self.supplier_ledger, 500), self.line('credit', self.bank_ledger, 500)])
        self.assertEqual(ok.status_code, 201, ok.content)
        bad = self.post('payment', [self.line('debit', self.bank_ledger, 500), self.line('credit', self.supplier_ledger, 500)])
        self.assertEqual(bad.status_code, 400)

    def test_contra_keeps_both_sides_in_money(self):
        ok = self.post('contra', [self.line('debit', self.cash, 300), self.line('credit', self.bank_ledger, 300)])
        self.assertEqual(ok.status_code, 201, ok.content)
        self.assertEqual(ok.json()['number'], 'CV-000001')
        bad = self.post('contra', [self.line('debit', self.cash, 300), self.line('credit', self.sales, 300)])
        self.assertEqual(bad.status_code, 400)

    def test_journal_takes_any_ledger(self):
        res = self.post('journal', [
            self.line('debit', self.rent, 250), self.line('credit', self.supplier_ledger, 250)])
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()['number'], 'JV-000001')

    def test_closed_ledger_is_refused(self):
        self.rent.is_active = False
        self.rent.save()
        res = self.post('payment', [self.line('debit', self.rent, 100), self.line('credit', self.cash, 100)])
        self.assertEqual(res.status_code, 400)
        self.assertIn('closed', res.json()['lines'])

    def test_cancel_removes_entries_but_keeps_the_voucher(self):
        vid = self.post('receipt', [
            self.line('debit', self.bank_ledger, 100), self.line('credit', self.sales, 100)]).json()['id']
        res = self.client.post(f'/api/vouchers/{vid}/cancel/', {'reason': 'wrong bank'},
                               content_type='application/json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(GeneralLedgerEntry.objects.filter(voucher_id=vid).count(), 0)
        v = Voucher.objects.get(pk=vid)
        self.assertEqual(v.status, 'cancelled')
        self.assertEqual(v.cancel_reason, 'wrong bank')
        self.assertEqual(v.lines.count(), 2)
        # and never twice
        self.assertEqual(self.client.post(f'/api/vouchers/{vid}/cancel/').status_code, 400)

    def test_vouchers_cannot_be_edited_or_deleted(self):
        vid = self.post('receipt', [
            self.line('debit', self.bank_ledger, 100), self.line('credit', self.sales, 100)]).json()['id']
        self.assertEqual(self.client.patch(f'/api/vouchers/{vid}/', {}, content_type='application/json').status_code, 405)
        self.assertEqual(self.client.delete(f'/api/vouchers/{vid}/').status_code, 405)


class AllocationTest(LedgerTestCase):

    def test_outstanding_invoices_net_off_till_payments_and_earlier_allocations(self):
        inv1 = self.invoice(500000, paid=200000, number='INV-1')
        self.invoice(300000, paid=300000, number='INV-2')     # settled at the till
        inv3 = self.invoice(100000, number='INV-3')

        self.client.force_login(self.accountant)
        res = self.client.get(f'/api/ledger-accounts/{self.customer_ledger.id}/outstanding/')
        self.assertEqual(res.status_code, 200)
        items = {i['reference']: Decimal(i['outstanding']) for i in res.json()['items']}
        self.assertEqual(items, {'INV-1': Decimal('300000'), 'INV-3': Decimal('100000')})
        self.assertEqual(res.json()['side'], 'credit')

        # Receive 250k against INV-1; it now owes 50k.
        res = self.post('receipt', [
            self.line('debit', self.bank_ledger, 250000),
            self.line('credit', self.customer_ledger, 250000,
                      allocations=[{'sale': inv1.id, 'amount': '250000'}]),
        ])
        self.assertEqual(res.status_code, 201, res.content)
        alloc = next(l for l in res.json()['lines'] if l['side'] == 'credit')['allocations']
        self.assertEqual(len(alloc), 1)
        self.assertEqual(alloc[0]['reference'], 'INV-1')

        items = {i['reference']: Decimal(i['outstanding'])
                 for i in self.client.get(f'/api/ledger-accounts/{self.customer_ledger.id}/outstanding/').json()['items']}
        self.assertEqual(items, {'INV-1': Decimal('50000'), 'INV-3': Decimal('100000')})
        self.assertEqual(inv3.voucher_allocations.count(), 0)

    def test_cannot_allocate_more_than_is_owed(self):
        inv = self.invoice(100000, paid=60000)
        res = self.post('receipt', [
            self.line('debit', self.cash, 50000),
            self.line('credit', self.customer_ledger, 50000,
                      allocations=[{'sale': inv.id, 'amount': '50000'}]),
        ])
        self.assertEqual(res.status_code, 400)
        self.assertIn('40000', res.json()['lines'])
        self.assertEqual(Voucher.objects.count(), 0)

    def test_cannot_allocate_more_than_the_line(self):
        inv = self.invoice(100000)
        res = self.post('receipt', [
            self.line('debit', self.cash, 30000),
            self.line('credit', self.customer_ledger, 30000,
                      allocations=[{'sale': inv.id, 'amount': '40000'}]),
        ])
        self.assertEqual(res.status_code, 400)

    def test_allocation_only_on_the_side_that_settles(self):
        inv = self.invoice(100000)
        # A customer debited is a charge, not a payment — nothing to allocate.
        res = self.post('journal', [
            self.line('debit', self.customer_ledger, 100, allocations=[{'sale': inv.id, 'amount': '100'}]),
            self.line('credit', self.sales, 100),
        ])
        self.assertEqual(res.status_code, 400)

    def test_cancelled_allocation_frees_the_invoice_again(self):
        inv = self.invoice(100000)
        vid = self.post('receipt', [
            self.line('debit', self.cash, 100000),
            self.line('credit', self.customer_ledger, 100000,
                      allocations=[{'sale': inv.id, 'amount': '100000'}]),
        ]).json()['id']
        self.assertEqual(vouchers.outstanding_invoices(self.customer), [])
        self.client.post(f'/api/vouchers/{vid}/cancel/')
        self.assertEqual(Decimal(vouchers.outstanding_invoices(self.customer)[0]['outstanding']),
                         Decimal('100000'))

    def test_supplier_bills_net_off_approved_supplier_payments(self):
        po = PurchaseOrder.objects.create(supplier=self.supplier, branch=self.branch,
                                          status='received', total_amount=900000)
        SupplierPayment.objects.create(purchase_order=po, supplier=self.supplier, amount=400000,
                                       status='paid', payment_date='2026-09-01')
        SupplierPayment.objects.create(purchase_order=po, supplier=self.supplier, amount=100000,
                                       status='pending', payment_date='2026-09-02')
        bills = vouchers.outstanding_bills(self.supplier)
        self.assertEqual(len(bills), 1)
        self.assertEqual(Decimal(bills[0]['outstanding']), Decimal('500000'))   # pending money does not count

        res = self.post('payment', [
            self.line('debit', self.supplier_ledger, 500000,
                      allocations=[{'purchase_order': po.id, 'amount': '500000'}]),
            self.line('credit', self.bank_ledger, 500000),
        ])
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(vouchers.outstanding_bills(self.supplier), [])


class ReadingTheLedgerTest(LedgerTestCase):

    def test_statement_running_balance_and_trial_balance_agree(self):
        self.bank_ledger.opening_balance = 1000000
        self.bank_ledger.save()
        self.post('receipt', [self.line('debit', self.bank_ledger, 500000), self.line('credit', self.sales, 500000)],
                  date='2026-09-01')
        self.post('payment', [self.line('debit', self.rent, 200000), self.line('credit', self.bank_ledger, 200000)],
                  date='2026-09-05')

        res = self.client.get(f'/api/ledger-accounts/{self.bank_ledger.id}/statement/')
        s = res.json()
        self.assertEqual(Decimal(s['opening_balance']), Decimal('1000000'))
        self.assertEqual([Decimal(r['balance']) for r in s['rows']], [Decimal('1500000'), Decimal('1300000')])
        self.assertEqual(Decimal(s['closing_balance']), Decimal('1300000'))

        # A period: the first receipt is carried in as opening.
        s = self.client.get(f'/api/ledger-accounts/{self.bank_ledger.id}/statement/?from=2026-09-02').json()
        self.assertEqual(Decimal(s['opening_balance']), Decimal('1500000'))
        self.assertEqual(len(s['rows']), 1)

        tb = self.client.get('/api/general-ledger/trial_balance/').json()
        by_code = {r['code']: r for r in tb['rows']}
        self.assertEqual(Decimal(by_code[self.bank_ledger.code]['closing_debit']), Decimal('1300000'))
        self.assertEqual(Decimal(by_code[self.sales.code]['closing_credit']), Decimal('500000'))
        self.assertEqual(Decimal(by_code[self.rent.code]['closing_debit']), Decimal('200000'))
        # The opening balance is what keeps the two columns apart here —
        # it is capital nobody has posted — so the totals differ by exactly it.
        self.assertEqual(Decimal(tb['total_debit']) - Decimal(tb['total_credit']), Decimal('1000000'))

    def test_trial_balance_balances_when_every_entry_came_through_a_voucher(self):
        self.post('receipt', [self.line('debit', self.cash, 700), self.line('credit', self.sales, 700)])
        self.post('payment', [self.line('debit', self.rent, 300), self.line('credit', self.cash, 300)])
        tb = self.client.get('/api/general-ledger/trial_balance/').json()
        self.assertEqual(Decimal(tb['difference']), Decimal('0'))
        self.assertEqual(Decimal(tb['total_debit']), Decimal('700'))

    def test_credit_ledger_balance_reads_positive_on_its_own_side(self):
        self.post('receipt', [self.line('debit', self.cash, 700), self.line('credit', self.sales, 700)])
        s = self.client.get(f'/api/ledger-accounts/{self.sales.id}/statement/').json()
        self.assertEqual(Decimal(s['closing_balance']), Decimal('700'))
        acc = next(a for a in self.client.get('/api/ledger-accounts/').json() if a['id'] == self.sales.id)
        self.assertEqual(Decimal(acc['balance']), Decimal('700'))

    def test_ledger_with_entries_cannot_be_deleted(self):
        self.post('receipt', [self.line('debit', self.cash, 700), self.line('credit', self.sales, 700)])
        res = self.client.delete(f'/api/ledger-accounts/{self.sales.id}/')
        self.assertEqual(res.status_code, 400)
        self.assertTrue(LedgerAccount.objects.filter(pk=self.sales.pk).exists())


class InvoiceVoucherTest(LedgerTestCase):
    """Sales and Purchase vouchers: the party, the two reference numbers, the
    payment status read off the lines, VAT, and settlement by Receipt/Payment."""

    def setUp(self):
        super().setUp()
        self.output_vat = LedgerAccount.objects.get(name='Output VAT')
        self.input_vat = LedgerAccount.objects.get(name='Input VAT')
        self.purchases = LedgerAccount.objects.get(name='Purchases')

    def sale(self, lines, **header):
        header.setdefault('customer', self.customer.id)
        header.setdefault('invoice_number', 'INV-2026-001')
        self.client.force_login(self.accountant)
        return self.client.post('/api/vouchers/', {
            'voucher_type': 'sales', 'date': '2026-09-10', 'lines': lines, **header,
        }, content_type='application/json')

    def test_credit_sale_with_vat_posts_and_reads_as_credit(self):
        res = self.sale([
            self.line('debit', self.customer_ledger, 1180000),
            self.line('credit', self.sales, 1000000),
            self.line('credit', self.output_vat, 180000),
        ], efd_number='EFD-777')
        self.assertEqual(res.status_code, 201, res.content)
        d = res.json()
        self.assertEqual(d['number'], 'SV-000001')
        self.assertEqual(d['payment_status'], 'credit')
        self.assertEqual(d['invoice_number'], 'INV-2026-001')
        self.assertEqual(d['efd_number'], 'EFD-777')
        self.assertEqual(Decimal(d['net_amount']), Decimal('1000000'))
        self.assertEqual(Decimal(d['vat_amount']), Decimal('180000'))
        self.assertEqual(d['customer_name'], self.customer.name)
        # The invoice number rides on every GL row of the voucher.
        self.assertEqual(set(GeneralLedgerEntry.objects.filter(voucher_id=d['id'])
                             .values_list('reference', flat=True)), {'INV-2026-001'})

    def test_cash_bank_and_partly_paid_are_read_off_the_debit_side(self):
        cash = self.sale([self.line('debit', self.cash, 100), self.line('credit', self.sales, 100)],
                         invoice_number='C-1').json()
        bank = self.sale([self.line('debit', self.bank_ledger, 100), self.line('credit', self.sales, 100)],
                         invoice_number='B-1').json()
        part = self.sale([self.line('debit', self.bank_ledger, 60), self.line('debit', self.customer_ledger, 40),
                          self.line('credit', self.sales, 100)], invoice_number='P-1').json()
        self.assertEqual((cash['payment_status'], bank['payment_status'], part['payment_status']),
                         ('cash', 'bank', 'partly_paid'))

    def test_invoice_number_is_required_and_unique_per_type(self):
        res = self.sale([self.line('debit', self.cash, 100), self.line('credit', self.sales, 100)],
                        invoice_number='')
        self.assertEqual(res.status_code, 400)
        self.assertIn('invoice_number', res.json())
        self.sale([self.line('debit', self.cash, 100), self.line('credit', self.sales, 100)])
        dup = self.sale([self.line('debit', self.cash, 100), self.line('credit', self.sales, 100)])
        self.assertEqual(dup.status_code, 400)
        self.assertIn('already in the books', dup.json()['invoice_number'])
        self.assertEqual(Voucher.objects.filter(voucher_type='sales').count(), 1)

    def test_duplicate_efd_number_is_refused(self):
        self.sale([self.line('debit', self.cash, 100), self.line('credit', self.sales, 100)],
                  invoice_number='A', efd_number='RCT-1')
        dup = self.sale([self.line('debit', self.cash, 100), self.line('credit', self.sales, 100)],
                        invoice_number='B', efd_number='rct-1')
        self.assertEqual(dup.status_code, 400)
        self.assertIn('efd_number', dup.json())

    def test_customer_ledger_must_belong_to_the_named_customer(self):
        other = Customer.objects.create(name='Somebody Else')
        res = self.sale([self.line('debit', other.ledger, 100), self.line('credit', self.sales, 100)])
        self.assertEqual(res.status_code, 400)
        self.assertIn('not the ledger of', res.json()['lines'])

    def test_customer_is_read_off_the_debit_side(self):
        # The form has no customer box: the customer whose ledger is debited
        # is the one the invoice is on.
        res = self.sale([self.line('debit', self.customer_ledger, 100), self.line('credit', self.sales, 100)],
                        customer='')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()['customer'], self.customer.id)
        self.assertEqual(res.json()['customer_name'], self.customer.name)
        # Two customers' ledgers on one invoice is two invoices.
        other = Customer.objects.create(name='Somebody Else')
        res = self.sale([self.line('debit', self.customer_ledger, 50), self.line('debit', other.ledger, 50),
                         self.line('credit', self.sales, 100)], customer='', invoice_number='TWO')
        self.assertEqual(res.status_code, 400)
        self.assertIn('lines', res.json())
        # A cash sale names nobody, and that is fine.
        res = self.sale([self.line('debit', self.cash, 100), self.line('credit', self.sales, 100)],
                        customer='', invoice_number='CASH')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertIsNone(res.json()['customer'])

    def test_sales_voucher_sides_are_restricted(self):
        # An expense cannot be credited on a sales voucher; a supplier cannot be debited.
        res = self.sale([self.line('debit', self.cash, 100), self.line('credit', self.rent, 100)])
        self.assertEqual(res.status_code, 400)
        res = self.sale([self.line('debit', self.supplier_ledger, 100), self.line('credit', self.sales, 100)])
        self.assertEqual(res.status_code, 400)

    def test_credit_sale_is_settled_by_a_receipt(self):
        sv = self.sale([
            self.line('debit', self.bank_ledger, 200000),          # deposit
            self.line('debit', self.customer_ledger, 800000),      # the rest on credit
            self.line('credit', self.sales, 1000000),
        ]).json()
        open_items = vouchers.outstanding_invoices(self.customer)
        self.assertEqual([(r['target'], r['reference'], Decimal(r['outstanding'])) for r in open_items],
                         [('voucher', 'INV-2026-001', Decimal('800000'))])
        self.assertEqual(Decimal(open_items[0]['total']), Decimal('800000'))

        res = self.post('receipt', [
            self.line('debit', self.bank_ledger, 300000),
            self.line('credit', self.customer_ledger, 300000,
                      allocations=[{'voucher': sv['id'], 'amount': '300000'}]),
        ])
        self.assertEqual(res.status_code, 201, res.content)
        alloc = next(l for l in res.json()['lines'] if l['side'] == 'credit')['allocations'][0]
        self.assertEqual(alloc['voucher'], sv['id'])
        self.assertEqual(alloc['reference'], 'INV-2026-001')
        self.assertEqual(Decimal(vouchers.outstanding_invoices(self.customer)[0]['outstanding']),
                         Decimal('500000'))
        # Over-allocation against it is refused like any other invoice.
        res = self.post('receipt', [
            self.line('debit', self.bank_ledger, 600000),
            self.line('credit', self.customer_ledger, 600000,
                      allocations=[{'voucher': sv['id'], 'amount': '600000'}]),
        ])
        self.assertEqual(res.status_code, 400)

    def test_purchase_voucher_and_its_payment(self):
        self.client.force_login(self.accountant)
        res = self.client.post('/api/vouchers/', {
            'voucher_type': 'purchase', 'date': '2026-09-10', 'supplier': self.supplier.id,
            'invoice_number': 'KS-4471', 'efd_number': 'RCT-9', 'lines': [
                self.line('debit', self.purchases, 500000),
                self.line('debit', self.input_vat, 90000),
                self.line('credit', self.supplier_ledger, 590000),
            ]}, content_type='application/json')
        self.assertEqual(res.status_code, 201, res.content)
        d = res.json()
        self.assertEqual(d['number'], 'PU-000001')
        self.assertEqual(d['payment_status'], 'credit')
        self.assertEqual(Decimal(d['vat_amount']), Decimal('90000'))
        self.assertEqual(d['supplier_name'], self.supplier.name)

        bills = vouchers.outstanding_bills(self.supplier)
        self.assertEqual([(b['target'], b['reference'], Decimal(b['outstanding'])) for b in bills],
                         [('voucher', 'KS-4471', Decimal('590000'))])
        res = self.post('payment', [
            self.line('debit', self.supplier_ledger, 590000, allocations=[{'voucher': d['id'], 'amount': '590000'}]),
            self.line('credit', self.bank_ledger, 590000),
        ])
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(vouchers.outstanding_bills(self.supplier), [])

        # The same supplier cannot hand us the same EFD receipt twice; another supplier can.
        again = self.client.post('/api/vouchers/', {
            'voucher_type': 'purchase', 'date': '2026-09-11', 'supplier': self.supplier.id,
            'invoice_number': 'KS-4472', 'efd_number': 'RCT-9', 'lines': [
                self.line('debit', self.purchases, 100), self.line('credit', self.cash, 100)]},
            content_type='application/json')
        self.assertEqual(again.status_code, 400)
        other = Supplier.objects.create(name='Other Supplies')
        res = self.client.post('/api/vouchers/', {
            'voucher_type': 'purchase', 'date': '2026-09-11', 'supplier': other.id,
            'invoice_number': 'OS-1', 'efd_number': 'RCT-9', 'lines': [
                self.line('debit', self.purchases, 100), self.line('credit', self.cash, 100)]},
            content_type='application/json')
        self.assertEqual(res.status_code, 201, res.content)

    def test_vouchers_are_found_by_invoice_efd_and_party(self):
        self.sale([self.line('debit', self.cash, 100), self.line('credit', self.sales, 100)],
                  invoice_number='FIND-ME', efd_number='EFD-42')
        self.sale([self.line('debit', self.cash, 100), self.line('credit', self.sales, 100)],
                  invoice_number='OTHER')
        self.client.force_login(self.accountant)
        self.assertEqual([v['invoice_number'] for v in self.client.get('/api/vouchers/?invoice=find-me').json()],
                         ['FIND-ME'])
        self.assertEqual([v['invoice_number'] for v in self.client.get('/api/vouchers/?efd=EFD-42').json()],
                         ['FIND-ME'])
        self.assertEqual(len(self.client.get(f'/api/vouchers/?customer={self.customer.id}').json()), 2)
        self.assertEqual([v['invoice_number'] for v in self.client.get('/api/vouchers/?q=mwananchi').json()],
                         ['OTHER', 'FIND-ME'])
        gl = self.client.get('/api/general-ledger/?reference=FIND-ME').json()
        self.assertEqual(len(gl), 2)
        self.assertEqual(len(self.client.get('/api/general-ledger/?efd=EFD-42').json()), 2)

    def test_the_form_pages_open(self):
        self.client.force_login(self.accountant)
        self.assertEqual(self.client.get('/finance/vouchers/new/sales/').status_code, 200)
        self.assertEqual(self.client.get('/finance/vouchers/new/purchase/').status_code, 200)


class BulkImportTest(LedgerTestCase):
    """The spreadsheet route into the books: same checks as the form, whole
    file or nothing."""

    HEADERS = ['Ref', 'Type', 'Date', 'Ledger', 'Side', 'Amount', 'Description',
               'Party', 'Invoice No', 'EFD No', 'Against', 'Narration']

    def workbook(self, voucher_rows, ledger_rows=None):
        import io
        from openpyxl import Workbook
        from django.core.files.uploadedfile import SimpleUploadedFile
        wb = Workbook()
        ws = wb.active
        ws.title = 'Vouchers'
        ws.append(self.HEADERS)
        for r in voucher_rows:
            ws.append(r)
        if ledger_rows is not None:
            ls = wb.create_sheet('Ledgers')
            ls.append(['Code', 'Name', 'Kind', 'Opening Balance', 'Opening Side', 'Notes'])
            for r in ledger_rows:
                ls.append(r)
        buf = io.BytesIO()
        wb.save(buf)
        return SimpleUploadedFile('books.xlsx', buf.getvalue())

    def upload(self, file, commit=False, create_parties=False):
        self.client.force_login(self.accountant)
        return self.client.post('/api/vouchers/bulk_upload/', {
            'file': file, 'commit': 'true' if commit else 'false',
            'create_parties': 'true' if create_parties else 'false',
        })

    GOOD = [
        ['1', 'Sales', '2026-07-03', 'Customer', 'Dr', 1180000, 'Cement', 'Mwananchi Traders', 'INV-0451', 'EFD-1', '', ''],
        ['1', 'Sales', '2026-07-03', 'Sales Revenue', 'Cr', 1000000, '', '', '', '', '', ''],
        ['1', 'Sales', '2026-07-03', 'Output VAT', 'Cr', 180000, '', '', '', '', '', ''],
        ['2', 'Receipt', '2026-07-20', 'CRDB Main', 'Dr', 500000, 'Part payment', '', '', '', '', ''],
        ['2', 'Receipt', '2026-07-20', 'Mwananchi Traders', 'Cr', 500000, '', '', '', '', 'INV-0451', ''],
        ['3', 'Payment', '31/07/2026', 'Rent', 'Dr', 800000, 'July rent', '', '', '', '', ''],
        ['3', 'Payment', '31/07/2026', 'CRDB Main', 'Cr', 800000, '', '', '', '', '', ''],
    ]

    def test_template_downloads_with_the_chart_of_accounts(self):
        from openpyxl import load_workbook
        import io
        self.client.force_login(self.accountant)
        res = self.client.get('/api/vouchers/import_template/')
        self.assertEqual(res.status_code, 200)
        wb = load_workbook(io.BytesIO(res.content))
        self.assertEqual(wb.sheetnames, ['Vouchers', 'Ledgers', 'Ledger List', 'How to fill'])
        codes = [r[0] for r in wb['Ledger List'].iter_rows(min_row=2, values_only=True)]
        self.assertIn(self.bank_ledger.code, codes)
        self.assertIn(self.customer_ledger.code, codes)

    def test_dry_run_reports_and_keeps_nothing(self):
        res = self.upload(self.workbook(self.GOOD))
        self.assertEqual(res.status_code, 200, res.content)
        r = res.json()
        self.assertTrue(r['ok'])
        self.assertFalse(r['committed'])
        self.assertEqual((r['posted'], r['failed']), (3, 0))
        self.assertEqual([v['type'] for v in r['vouchers']], ['sales', 'receipt', 'payment'])
        self.assertEqual(r['vouchers'][0]['rows'], '2–4')
        self.assertEqual(Decimal(r['total']), Decimal('2480000'))
        self.assertEqual(Voucher.objects.count(), 0)
        self.assertEqual(GeneralLedgerEntry.objects.count(), 0)

    def test_commit_posts_the_whole_file_in_date_order_with_allocations(self):
        res = self.upload(self.workbook(self.GOOD), commit=True)
        r = res.json()
        self.assertTrue(r['committed'], r)
        self.assertEqual([v['number'] for v in r['vouchers']], ['SV-000001', 'RV-000001', 'PV-000001'])
        sv = Voucher.objects.get(number='SV-000001')
        self.assertEqual(sv.customer, self.customer)
        self.assertEqual(sv.invoice_number, 'INV-0451')
        self.assertEqual(sv.payment_status, 'credit')
        self.assertEqual(sv.vat_amount, Decimal('180000'))
        # The receipt row said "Against INV-0451": 1,180,000 owed less 500,000.
        self.assertEqual(Decimal(vouchers.outstanding_invoices(self.customer)[0]['outstanding']),
                         Decimal('680000'))
        self.assertEqual(Voucher.objects.get(number='PV-000001').date.isoformat(), '2026-07-31')
        self.assertEqual(Decimal(vouchers.trial_balance()['difference']), Decimal('0'))

    def test_one_bad_voucher_blocks_the_file(self):
        rows = self.GOOD + [
            ['9', 'Journal', '2026-08-01', 'Rent', 'Dr', 100, 'Unbalanced', '', '', '', '', ''],
            ['9', 'Journal', '2026-08-01', 'Capital', 'Cr', 90, '', '', '', '', '', ''],
            ['10', 'Receipt', '2026-08-02', 'No Such Ledger', 'Dr', 100, '', '', '', '', '', ''],
            ['10', 'Receipt', '2026-08-02', 'Sales Revenue', 'Cr', 100, '', '', '', '', '', ''],
        ]
        res = self.upload(self.workbook(rows), commit=True)
        r = res.json()
        self.assertFalse(r['ok'])
        self.assertFalse(r['committed'])
        self.assertEqual((r['posted'], r['failed']), (3, 2))
        bad = {v['ref']: v['message'] for v in r['vouchers'] if v['status'] == 'error'}
        self.assertIn('do not balance', bad['9'])
        self.assertIn('No Such Ledger', bad['10'])
        self.assertEqual(Voucher.objects.count(), 0)          # the three good ones were rolled back too

    def test_unreadable_rows_are_named(self):
        rows = [['1', 'Rocket', '2026-07-03', 'Rent', 'Dr', 100, '', '', '', '', '', ''],
                ['1', 'Payment', 'someday', 'Rent', 'Dr', 100, '', '', '', '', '', ''],
                ['1', 'Payment', '2026-07-03', 'Rent', 'Sideways', 100, '', '', '', '', '', ''],
                ['1', 'Payment', '2026-07-03', 'Rent', 'Dr', 'lots', '', '', '', '', '', ''],
                ['2', 'Payment', '2026-07-03', 'Rent', 'Dr', 100, '', '', '', '', '', ''],
                ['2', 'Payment', '2026-07-03', 'CRDB Main', 'Cr', 100, '', '', '', '', '', '']]
        r = self.upload(self.workbook(rows)).json()
        self.assertEqual(len(r['errors']), 4)
        self.assertTrue(all(e.startswith('Vouchers row') for e in r['errors']))
        self.assertFalse(r['ok'])
        self.assertEqual(r['posted'], 1)

    def test_missing_party_is_refused_unless_creation_is_allowed(self):
        rows = [['1', 'Purchase', '2026-07-05', 'Purchases', 'Dr', 250000, '', 'New Steel Ltd', 'NS-77', '', '', ''],
                ['1', 'Purchase', '2026-07-05', 'Supplier', 'Cr', 250000, '', '', '', '', '', '']]
        r = self.upload(self.workbook(rows), commit=True).json()
        self.assertFalse(r['committed'])
        self.assertIn('add them first', r['vouchers'][0]['message'])
        r = self.upload(self.workbook(rows), commit=True, create_parties=True).json()
        self.assertTrue(r['committed'], r)
        supplier = Supplier.objects.get(name='New Steel Ltd')
        self.assertEqual(supplier.ledger.kind, 'supplier')
        self.assertEqual(Decimal(vouchers.outstanding_bills(supplier)[0]['outstanding']), Decimal('250000'))

    def test_ledgers_sheet_creates_ledgers_and_sets_opening_balances(self):
        ledgers = [['', 'Equity Bank', 'bank', 2500000, 'Dr', 'Opening'],
                   ['', 'Capital', 'equity', 2500000, 'Cr', ''],
                   ['', 'Somebody', 'customer', 0, '', '']]           # not allowed here
        rows = [['1', 'Payment', '2026-07-05', 'Rent', 'Dr', 100000, '', '', '', '', '', ''],
                ['1', 'Payment', '2026-07-05', 'Equity Bank', 'Cr', 100000, '', '', '', '', '', '']]
        r = self.upload(self.workbook(rows, ledgers), commit=True).json()
        self.assertFalse(r['committed'])
        self.assertTrue(any('customer and supplier ledgers' in e for e in r['errors']))
        # Drop the offending row and it goes through — the new bank is usable on the same file.
        r = self.upload(self.workbook(rows, ledgers[:2]), commit=True).json()
        self.assertTrue(r['committed'], r)
        bank = LedgerAccount.objects.get(name='Equity Bank')
        self.assertEqual((bank.kind, bank.opening_balance, bank.opening_side), ('bank', Decimal('2500000'), 'debit'))
        capital = LedgerAccount.objects.get(name='Capital')      # existed already: opening balance set
        self.assertEqual((capital.opening_balance, capital.opening_side), (Decimal('2500000'), 'credit'))
        actions = {l['name']: l['action'] for l in r['ledgers']}
        self.assertEqual(actions['Equity Bank'], 'created')
        self.assertIn('opening balance', actions['Capital'])
        self.assertEqual(Decimal(vouchers.trial_balance()['difference']), Decimal('0'))

    def test_csv_works_too(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        text = 'Ref,Type,Date,Ledger,Side,Amount\n1,Contra,2026-07-05,Main Cash Book,Dr,50000\n1,Contra,2026-07-05,CRDB Main,Cr,50000\n'
        r = self.upload(SimpleUploadedFile('c.csv', text.encode('utf-8')), commit=True).json()
        self.assertTrue(r['committed'], r)
        self.assertEqual(Voucher.objects.get().number, 'CV-000001')

    def test_only_accounts_may_import(self):
        self.client.force_login(self.cashier)
        res = self.client.post('/api/vouchers/bulk_upload/', {'file': self.workbook(self.GOOD)})
        self.assertEqual(res.status_code, 403)
