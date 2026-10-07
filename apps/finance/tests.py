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

import json
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from rest_framework.exceptions import ValidationError as RestValidationError

from apps.inventory.models import Branch, PurchaseOrder, Supplier
from apps.sales.models import Customer, Sale, Transaction
from apps.users.models import User
from .models import (
    AccountingAuditLog, AccountingSettings, BankAccount, Currency, ExchangeRate,
    FinancialYear, GeneralLedgerEntry, Invoice, LedgerAccount, SupplierPayment,
    SalesLedgerEntry, Voucher, VoucherAllocation, VoucherType,
)
from . import accounting_reports, vouchers
from .balancing import BalancingService, suggest_balancing_line
from .ledger_services import trial_balance_rows
from .numbering import VoucherNumberService
from .statements import balance_sheet, cash_flow, profit_and_loss


class LedgerTestCase(TestCase):

    @classmethod
    def setUpTestData(cls):
        """Seed the role groups once per test class.

        The sensitive voucher actions go by Django permission
        (`finance.post_voucher` and friends), and those are granted by
        `create_roles`. Running it here rather than handing out permissions by
        name is deliberate: it makes these tests fail if a role ever drifts
        from the screens it is meant to work, which CLAUDE.md names as the
        historical source of 403 bugs.
        """
        call_command('create_roles', verbosity=0)

    def setUp(self):
        self.accountant = User.objects.create_user(username='acc', password='pw', role='accountant')
        self.accountant.groups.add(Group.objects.get(name='Accountant'))
        self.cashier = User.objects.create_user(username='till', password='pw', role='cashier')
        self.cashier.groups.add(Group.objects.get(name='Cashier'))
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
        self.assertEqual(data['number'], 'RV-2026-000001')
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
        self.assertEqual(res.json()['number'], 'RV-2026-000002')
        self.assertEqual(Voucher.objects.get(voucher_type='payment').number, 'PV-2026-000001')

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

    def test_a_receipt_cannot_touch_a_supplier_or_a_payable(self):
        """The restriction table from the specification (apps/finance/restrictions.py):
        a Receipt may use any ledger *except* a supplier or payable one, on
        either side. That is looser than requiring a bank line — a receipt is
        money-in however it is booked — but a supplier is categorically wrong
        on one, and that is what is refused."""
        res = self.post('receipt', [self.line('debit', self.bank_ledger, 100),
                                    self.line('credit', self.supplier_ledger, 100)])
        self.assertEqual(res.status_code, 400)
        self.assertIn('cannot be credited', res.json()['lines'])
        res = self.post('receipt', [self.line('debit', self.supplier_ledger, 100),
                                    self.line('credit', self.sales, 100)])
        self.assertEqual(res.status_code, 400)
        self.assertIn('cannot be debited', res.json()['lines'])

    def test_a_payment_cannot_touch_a_customer_or_a_receivable(self):
        """The mirror rule: a Payment may use any ledger except a customer or
        receivable one."""
        res = self.post('payment', [self.line('debit', self.customer_ledger, 100),
                                    self.line('credit', self.bank_ledger, 100)])
        self.assertEqual(res.status_code, 400)
        self.assertIn('cannot be debited', res.json()['lines'])

    def test_payment_sides(self):
        """A supplier debited and the bank credited is the everyday shape, and
        the reverse is allowed too — paying a supplier *from* a supplier
        ledger is odd but not nonsense, and the table does not forbid it."""
        ok = self.post('payment', [self.line('debit', self.supplier_ledger, 500),
                                   self.line('credit', self.bank_ledger, 500)])
        self.assertEqual(ok.status_code, 201, ok.content)

    def test_contra_keeps_both_sides_in_money(self):
        ok = self.post('contra', [self.line('debit', self.cash, 300), self.line('credit', self.bank_ledger, 300)])
        self.assertEqual(ok.status_code, 201, ok.content)
        self.assertEqual(ok.json()['number'], 'CV-2026-000001')
        bad = self.post('contra', [self.line('debit', self.cash, 300), self.line('credit', self.sales, 300)])
        self.assertEqual(bad.status_code, 400)

    def test_journal_takes_any_ledger(self):
        res = self.post('journal', [
            self.line('debit', self.rent, 250), self.line('credit', self.supplier_ledger, 250)])
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()['number'], 'JV-2026-000001')

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

    def test_a_posted_voucher_cannot_be_edited_or_deleted(self):
        """There is no update at all, and `destroy` refuses a posted voucher
        with the reason rather than a bare 405 — a draft is the only thing
        that can be deleted, and a posted voucher is reversed instead."""
        vid = self.post('receipt', [
            self.line('debit', self.bank_ledger, 100), self.line('credit', self.sales, 100)]).json()['id']
        self.assertEqual(self.client.patch(f'/api/vouchers/{vid}/', {},
                                           content_type='application/json').status_code, 405)
        res = self.client.delete(f'/api/vouchers/{vid}/')
        self.assertEqual(res.status_code, 400)
        self.assertIn('draft', str(res.json()).lower())
        self.assertTrue(Voucher.objects.filter(pk=vid).exists())


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
        self.assertEqual(d['number'], 'SV-2026-000001')
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
        """A Sales voucher may use any ledger except a supplier or payable one
        — the specification's table. A supplier debited is refused; crediting
        an expense ledger is merely unusual, so it is allowed and left to the
        accountant's judgement."""
        res = self.sale([self.line('debit', self.supplier_ledger, 100),
                         self.line('credit', self.sales, 100)], invoice_number='BAD-1')
        self.assertEqual(res.status_code, 400)
        self.assertIn('cannot be debited', res.json()['lines'])

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
        self.assertEqual(d['number'], 'PU-2026-000001')
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
        self.assertEqual([v['number'] for v in r['vouchers']], ['SV-2026-000001', 'RV-2026-000001', 'PV-2026-000001'])
        sv = Voucher.objects.get(number='SV-2026-000001')
        self.assertEqual(sv.customer, self.customer)
        self.assertEqual(sv.invoice_number, 'INV-0451')
        self.assertEqual(sv.payment_status, 'credit')
        self.assertEqual(sv.vat_amount, Decimal('180000'))
        # The receipt row said "Against INV-0451": 1,180,000 owed less 500,000.
        self.assertEqual(Decimal(vouchers.outstanding_invoices(self.customer)[0]['outstanding']),
                         Decimal('680000'))
        self.assertEqual(Voucher.objects.get(number='PV-2026-000001').date.isoformat(), '2026-07-31')
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
        self.assertEqual(Voucher.objects.get().number, 'CV-2026-000001')

    def test_only_accounts_may_import(self):
        self.client.force_login(self.cashier)
        res = self.client.post('/api/vouchers/bulk_upload/', {'file': self.workbook(self.GOOD)})
        self.assertEqual(res.status_code, 403)


# ===========================================================================
# The accounting engine proper: drafts, period locking, reversal, currencies,
# the invoice register, the reports, and the screens that drive them.
# ===========================================================================

class FinancialYearTest(LedgerTestCase):
    """A voucher can only be posted into a year that exists, is open, and is
    not locked up to its date."""

    def test_opening_the_books_is_idempotent(self):
        vouchers.open_the_books()
        before = (Currency.objects.count(), FinancialYear.objects.count(),
                  VoucherType.objects.count(), LedgerAccount.objects.count())
        vouchers.open_the_books()
        self.assertEqual(
            (Currency.objects.count(), FinancialYear.objects.count(),
             VoucherType.objects.count(), LedgerAccount.objects.count()), before)
        self.assertTrue(Currency.base().is_base)
        self.assertEqual(Currency.base().code, 'TZS')

    def test_a_date_outside_every_year_cannot_be_posted(self):
        res = self.post('journal', [self.line('debit', self.rent, 100),
                                    self.line('credit', self.cash, 100)],
                        date='2019-03-04')
        self.assertEqual(res.status_code, 400)
        self.assertIn('No financial year covers', str(res.json()))
        self.assertEqual(Voucher.objects.count(), 0)

    def test_a_closed_year_refuses_the_accountant_but_not_an_admin(self):
        vouchers.open_the_books()
        year = FinancialYear.objects.get(code='2026')
        year.is_closed = True
        year.save()

        res = self.post('journal', [self.line('debit', self.rent, 100),
                                    self.line('credit', self.cash, 100)])
        self.assertEqual(res.status_code, 400)
        self.assertIn('closed', str(res.json()).lower())

        # An admin holds `post_closed_period`, so the same voucher goes in.
        admin = User.objects.create_superuser(username='boss', password='pw')
        ok = self.post('journal', [self.line('debit', self.rent, 100),
                                   self.line('credit', self.cash, 100)], user=admin)
        self.assertEqual(ok.status_code, 201, ok.content)

    def test_a_lock_date_closes_everything_up_to_it(self):
        vouchers.open_the_books()
        year = FinancialYear.objects.get(code='2026')
        year.lock_date = date(2026, 9, 30)
        year.save()

        blocked = self.post('journal', [self.line('debit', self.rent, 100),
                                        self.line('credit', self.cash, 100)],
                            date='2026-09-10')
        self.assertEqual(blocked.status_code, 400)
        self.assertIn('locked', str(blocked.json()).lower())

        allowed = self.post('journal', [self.line('debit', self.rent, 100),
                                        self.line('credit', self.cash, 100)],
                            date='2026-10-01')
        self.assertEqual(allowed.status_code, 201, allowed.content)

    def test_the_company_wide_lock_applies_too(self):
        vouchers.open_the_books()
        settings_row = AccountingSettings.get_solo()
        settings_row.period_lock_date = date(2026, 9, 30)
        settings_row.save()
        res = self.post('journal', [self.line('debit', self.rent, 100),
                                    self.line('credit', self.cash, 100)],
                        date='2026-09-10')
        self.assertEqual(res.status_code, 400)
        self.assertIn('company policy', str(res.json()))


class VoucherNumberingTest(LedgerTestCase):
    """Numbers come from a sequence, carry the financial year by default, and
    follow whatever the configuration says."""

    def test_the_year_and_padding_come_from_the_configuration(self):
        vouchers.open_the_books()
        settings_row = AccountingSettings.get_solo()
        settings_row.include_financial_year_in_number = False
        settings_row.voucher_number_padding = 4
        settings_row.number_separator = '/'
        settings_row.save()

        res = self.post('journal', [self.line('debit', self.rent, 100),
                                    self.line('credit', self.cash, 100)])
        self.assertEqual(res.json()['number'], 'JV/0001')

    def test_the_prefix_is_editable_per_type(self):
        vouchers.open_the_books()
        VoucherType.objects.filter(code='journal').update(prefix='GJ')
        res = self.post('journal', [self.line('debit', self.rent, 100),
                                    self.line('credit', self.cash, 100)])
        self.assertEqual(res.json()['number'], 'GJ-2026-000001')

    def test_peek_does_not_consume_a_number(self):
        vouchers.open_the_books()
        service = VoucherNumberService()
        year = FinancialYear.current()
        first = service.peek_next('receipt', year)
        self.assertEqual(first, service.peek_next('receipt', year))
        res = self.post('receipt', [self.line('debit', self.cash, 10),
                                    self.line('credit', self.sales, 10)])
        self.assertEqual(res.json()['number'], first)


class DraftWorkflowTest(LedgerTestCase):
    """A draft may be unbalanced and edited; posting never is."""

    def draft(self, lines, **extra):
        self.client.force_login(self.accountant)
        return self.client.post('/api/vouchers/draft/', {
            'voucher_type': 'journal', 'date': '2026-09-10', 'lines': lines, **extra,
        }, content_type='application/json')

    def test_a_draft_is_numbered_but_reaches_no_ledger(self):
        res = self.draft([self.line('debit', self.rent, 100),
                          self.line('credit', self.cash, 100)])
        self.assertEqual(res.status_code, 201, res.content)
        data = res.json()
        self.assertEqual(data['status'], 'draft')
        self.assertTrue(data['number'].startswith('JV-2026-'))
        self.assertEqual(GeneralLedgerEntry.objects.count(), 0)

    def test_posting_a_draft_writes_the_ledger_once(self):
        voucher_id = self.draft([self.line('debit', self.rent, 100),
                                 self.line('credit', self.cash, 100)]).json()['id']
        res = self.client.post(f'/api/vouchers/{voucher_id}/post_to_ledger/')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['status'], 'posted')
        self.assertEqual(GeneralLedgerEntry.objects.filter(voucher_id=voucher_id).count(), 2)

        # Posting a second time is refused rather than doubling the entries.
        again = self.client.post(f'/api/vouchers/{voucher_id}/post_to_ledger/')
        self.assertEqual(again.status_code, 400)
        self.assertEqual(GeneralLedgerEntry.objects.filter(voucher_id=voucher_id).count(), 2)

    def test_validate_lists_what_stops_a_draft_posting(self):
        voucher = Voucher.objects.create(voucher_type='journal', date=date(2026, 9, 10),
                                         status='draft', financial_year=FinancialYear.current())
        self.client.force_login(self.accountant)
        res = self.client.get(f'/api/vouchers/{voucher.pk}/validate/')
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.json()['can_post'])
        self.assertTrue(res.json()['errors'])

    def test_only_a_draft_can_be_deleted(self):
        draft_id = self.draft([self.line('debit', self.rent, 100),
                               self.line('credit', self.cash, 100)]).json()['id']
        self.assertEqual(self.client.delete(f'/api/vouchers/{draft_id}/').status_code, 204)
        self.assertFalse(Voucher.objects.filter(pk=draft_id).exists())

        posted_id = self.post('journal', [self.line('debit', self.rent, 50),
                                          self.line('credit', self.cash, 50)]).json()['id']
        res = self.client.delete(f'/api/vouchers/{posted_id}/')
        self.assertEqual(res.status_code, 400)
        self.assertTrue(Voucher.objects.filter(pk=posted_id).exists())


class ReversalTest(LedgerTestCase):
    """A posted voucher is corrected by reversal: the mirror image is posted
    as a journal, and both documents stay in the books."""

    def test_reversing_posts_the_mirror_and_leaves_both_standing(self):
        original_id = self.post('receipt', [
            self.line('debit', self.bank_ledger, 500),
            self.line('credit', self.sales, 500),
        ]).json()['id']

        self.client.force_login(self.accountant)
        res = self.client.post(f'/api/vouchers/{original_id}/reverse/',
                               {'date': '2026-09-12', 'reason': 'Keyed twice'},
                               content_type='application/json')
        self.assertEqual(res.status_code, 201, res.content)
        reversal = res.json()
        self.assertEqual(reversal['voucher_type'], 'journal')
        self.assertEqual(reversal['reversal_of'], original_id)

        original = Voucher.objects.get(pk=original_id)
        self.assertEqual(original.status, 'reversed')
        # Nothing was erased — the original's rows stay, cancelled out by the
        # reversing journal's, so every ledger nets to nothing.
        self.assertEqual(GeneralLedgerEntry.objects.filter(voucher_id=original_id).count(), 2)
        balances = vouchers.account_balances()
        self.assertEqual(balances[self.bank_ledger.id], Decimal('0'))
        self.assertEqual(balances[self.sales.id], Decimal('0'))

    def test_a_reversed_invoice_number_can_be_keyed_again(self):
        """Reversing and re-entering is the ordinary way to correct an
        invoice, so the number it used has to be free again — both in the
        voucher check and in the register's own unique index."""
        self.client.force_login(self.accountant)

        def sale(amount):
            return self.client.post('/api/vouchers/', {
                'voucher_type': 'sales', 'date': '2026-09-10', 'invoice_number': 'FIX-1',
                'customer': self.customer.id,
                'lines': [self.line('debit', self.customer_ledger, amount),
                          self.line('credit', self.sales, amount)],
            }, content_type='application/json')

        first = sale(1000)
        self.assertEqual(first.status_code, 201, first.content)

        # While it stands, the number is taken.
        clash = sale(1000)
        self.assertEqual(clash.status_code, 400)
        self.assertIn('already in the books', str(clash.json()))

        # Reverse it, and the number is free for the corrected invoice.
        self.client.post(f"/api/vouchers/{first.json()['id']}/reverse/",
                         {'reason': 'wrong amount'}, content_type='application/json')
        corrected = sale(1200)
        self.assertEqual(corrected.status_code, 201, corrected.content)
        self.assertEqual(Decimal(corrected.json()['total']), Decimal('1200'))

        # One standing invoice for that number, and it is the corrected one.
        standing = Invoice.objects.filter(invoice_number='FIX-1').exclude(status='cancelled')
        self.assertEqual(standing.count(), 1)
        self.assertEqual(standing.get().original_amount, Decimal('1200'))

    def test_a_draft_cannot_be_reversed(self):
        voucher = Voucher.objects.create(voucher_type='journal', date=date(2026, 9, 10),
                                         status='draft', financial_year=FinancialYear.current())
        self.client.force_login(self.accountant)
        res = self.client.post(f'/api/vouchers/{voucher.pk}/reverse/',
                               {'reason': 'nope'}, content_type='application/json')
        self.assertEqual(res.status_code, 400)

    def test_an_invoice_with_receipts_against_it_cannot_be_reversed_first(self):
        """Reversing a sale somebody has already paid against would leave the
        receipt pointing at nothing, so the receipt is reversed first."""
        self.client.force_login(self.accountant)
        sale = self.client.post('/api/vouchers/', {
            'voucher_type': 'sales', 'date': '2026-09-10', 'invoice_number': 'REV-1',
            'customer': self.customer.id,
            'lines': [self.line('debit', self.customer_ledger, 1000),
                      self.line('credit', self.sales, 1000)],
        }, content_type='application/json').json()
        receipt = self.post('receipt', [
            self.line('debit', self.bank_ledger, 400),
            self.line('credit', self.customer_ledger, 400,
                      allocations=[{'voucher': sale['id'], 'amount': '400'}]),
        ])
        self.assertEqual(receipt.status_code, 201, receipt.content)

        res = self.client.post(f"/api/vouchers/{sale['id']}/reverse/",
                               {'reason': 'wrong'}, content_type='application/json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('Reverse those first', str(res.json()))

        # Reverse the receipt, and the sale can go. Reversing the receipt also
        # has to release what it cleared: a reversed allocation has no
        # mirror-image of its own, so it stops counting (EFFECTIVE_STATUSES).
        invoice = Invoice.objects.get(voucher_id=sale['id'])
        self.assertEqual(invoice.outstanding_amount, Decimal('600'))
        self.client.post(f"/api/vouchers/{receipt.json()['id']}/reverse/",
                         {'reason': 'wrong too'}, content_type='application/json')
        self.assertEqual(Invoice.objects.get(pk=invoice.pk).outstanding_amount, Decimal('1000'))

        ok = self.client.post(f"/api/vouchers/{sale['id']}/reverse/",
                              {'reason': 'wrong'}, content_type='application/json')
        self.assertEqual(ok.status_code, 201, ok.content)


class InvoiceRegisterTest(LedgerTestCase):
    """Posting a Sales or Purchase voucher raises a row in the books' own
    invoice register, and that is what a later Receipt clears."""

    def sale(self, lines, **header):
        header.setdefault('customer', self.customer.id)
        header.setdefault('invoice_number', 'REG-1')
        self.client.force_login(self.accountant)
        return self.client.post('/api/vouchers/', {
            'voucher_type': 'sales', 'date': '2026-09-10', 'lines': lines, **header,
        }, content_type='application/json')

    def test_a_credit_sale_raises_an_open_invoice(self):
        res = self.sale([self.line('debit', self.customer_ledger, 1000),
                         self.line('credit', self.sales, 1000)])
        self.assertEqual(res.status_code, 201, res.content)
        invoice = Invoice.objects.get(voucher_id=res.json()['id'])
        self.assertEqual((invoice.kind, invoice.status), ('sales', 'open'))
        self.assertEqual(invoice.outstanding_amount, Decimal('1000'))
        self.assertEqual(invoice.customer, self.customer)

    def test_a_cash_sale_raises_no_register_row(self):
        """Nothing is owed, and the register is a list of what is owed. A
        cash sale with no customer names nobody to owe it."""
        res = self.sale([self.line('debit', self.cash, 1000),
                         self.line('credit', self.sales, 1000)],
                        customer='', invoice_number='CASH-1')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertFalse(Invoice.objects.filter(voucher_id=res.json()['id']).exists())

    def test_a_part_paid_sale_lands_partly_paid(self):
        res = self.sale([self.line('debit', self.bank_ledger, 300),
                         self.line('debit', self.customer_ledger, 700),
                         self.line('credit', self.sales, 1000)],
                        invoice_number='PART-1')
        invoice = Invoice.objects.get(voucher_id=res.json()['id'])
        self.assertEqual(invoice.status, 'partly_paid')
        self.assertEqual(invoice.outstanding_amount, Decimal('700'))

    def test_a_receipt_against_the_invoice_closes_the_register_row_too(self):
        """An invoice raised by a Sales voucher is allocated against by the
        `voucher` target. The allocation carries the register row's id as
        well, so the register's status and ageing follow without a second
        allocation row that could drift from the first."""
        sale = self.sale([self.line('debit', self.customer_ledger, 1000),
                          self.line('credit', self.sales, 1000)]).json()
        invoice = Invoice.objects.get(voucher_id=sale['id'])
        self.assertEqual(invoice.outstanding_amount, Decimal('1000'))

        res = self.post('receipt', [
            self.line('debit', self.bank_ledger, 1000),
            self.line('credit', self.customer_ledger, 1000,
                      allocations=[{'voucher': sale['id'], 'amount': '1000'}]),
        ])
        self.assertEqual(res.status_code, 201, res.content)
        allocation = VoucherAllocation.objects.get(voucher_id=sale['id'])
        self.assertEqual(allocation.invoice_id, invoice.pk)

        invoice.refresh_from_db()
        self.assertEqual(invoice.refresh_status(), 'paid')
        self.assertEqual(invoice.outstanding_amount, Decimal('0'))
        # And the invoice has left the outstanding list.
        self.assertEqual(vouchers.outstanding_invoices(self.customer), [])

    def test_allocating_more_than_is_owed_is_refused(self):
        sale = self.sale([self.line('debit', self.customer_ledger, 1000),
                          self.line('credit', self.sales, 1000)]).json()
        res = self.post('receipt', [
            self.line('debit', self.bank_ledger, 5000),
            self.line('credit', self.customer_ledger, 5000,
                      allocations=[{'voucher': sale['id'], 'amount': '5000'}]),
        ])
        self.assertEqual(res.status_code, 400)
        self.assertIn('outstanding', str(res.json()).lower())
        self.assertEqual(Voucher.objects.filter(voucher_type='receipt').count(), 0)

    def test_an_opening_balance_invoice_is_allocated_by_its_own_id(self):
        """A register row no voucher raised — an opening balance, or a bill
        keyed straight into the books — is the one case where the `invoice`
        target is used directly."""
        invoice = Invoice.objects.create(
            kind=Invoice.SALES, customer=self.customer, invoice_number='OB-1',
            invoice_date=date(2026, 1, 1), original_amount=Decimal('400'),
            description='Brought forward')
        rows = vouchers.outstanding_invoices(self.customer)
        self.assertEqual([(r['target'], r['reference']) for r in rows],
                         [('invoice', 'OB-1')])

        res = self.post('receipt', [
            self.line('debit', self.bank_ledger, 400),
            self.line('credit', self.customer_ledger, 400,
                      allocations=[{'invoice': invoice.pk, 'amount': '400'}]),
        ])
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(invoice.refresh_status(), 'paid')

    def test_the_ageing_report_buckets_by_age(self):
        self.sale([self.line('debit', self.customer_ledger, 1000),
                   self.line('credit', self.sales, 1000)], invoice_number='AGE-1')
        data = accounting_reports.outstanding('customer', as_of=date(2026, 12, 25))
        self.assertEqual(data['grand_total'], Decimal('1000'))
        # 2026-09-10 to 2026-12-25 is 106 days.
        self.assertEqual(data['buckets']['90+'], Decimal('1000'))
        self.assertEqual(data['groups'][0]['party'], self.customer)


class MultiCurrencyTest(LedgerTestCase):
    """The ledger is always kept in the base currency; a voucher may be keyed
    in another and is converted at its own rate."""

    def setUp(self):
        super().setUp()
        vouchers.open_the_books()
        self.usd = Currency.objects.create(code='USD', name='US Dollar', symbol='$')
        ExchangeRate.objects.create(currency=self.usd, rate_date=date(2026, 9, 1),
                                    rate=Decimal('2650'))

    def test_a_foreign_voucher_reaches_the_ledger_in_the_base_currency(self):
        voucher = vouchers.post_voucher(
            'journal', '2026-09-10', 'A dollar invoice',
            [self.line('debit', self.rent, 100), self.line('credit', self.cash, 100)],
            self.accountant, currency=self.usd)
        self.assertEqual(voucher.currency, self.usd)
        self.assertEqual(voucher.exchange_rate, Decimal('2650'))
        self.assertEqual(voucher.total_debit, Decimal('100'))          # as keyed
        self.assertEqual(voucher.base_total_debit, Decimal('265000'))  # in the books

        entry = GeneralLedgerEntry.objects.get(voucher=voucher, account=self.rent)
        self.assertEqual(entry.debit, Decimal('265000'))      # the ledger is in TZS
        self.assertEqual(entry.foreign_debit, Decimal('100'))  # what was keyed
        self.assertEqual(entry.exchange_rate, Decimal('2650'))

        # The trial balance, being in the base currency, still balances.
        self.assertEqual(Decimal(vouchers.trial_balance()['difference']), Decimal('0'))

    def test_the_rate_is_looked_up_when_none_is_given(self):
        voucher = vouchers.post_voucher(
            'journal', '2026-09-10', '',
            [self.line('debit', self.rent, 10), self.line('credit', self.cash, 10)],
            self.accountant, currency=self.usd)
        self.assertEqual(voucher.exchange_rate, Decimal('2650'))

    def test_a_voucher_in_a_currency_with_no_rate_is_refused(self):
        kes = Currency.objects.create(code='KES', name='Kenyan Shilling')
        with self.assertRaises(RestValidationError) as caught:
            vouchers.post_voucher(
                'journal', '2026-09-10', '',
                [self.line('debit', self.rent, 10), self.line('credit', self.cash, 10)],
                self.accountant, currency=kes)
        self.assertIn('No exchange rate', str(caught.exception))

    def test_a_foreign_ledger_cannot_sit_on_a_base_currency_voucher(self):
        usd_bank = LedgerAccount.objects.create(kind='bank', name='USD Account',
                                                currency=self.usd)
        with self.assertRaises(RestValidationError) as caught:
            vouchers.post_voucher(
                'journal', '2026-09-10', '',
                [self.line('debit', usd_bank, 10), self.line('credit', self.cash, 10)],
                self.accountant)
        self.assertIn('another currency', str(caught.exception))


class ChartOfAccountsHierarchyTest(LedgerTestCase):
    """Groups, control accounts and the sub-ledgers beneath them."""

    def test_party_ledgers_hang_under_the_control_accounts(self):
        vouchers.open_the_books()
        receivable = LedgerAccount.objects.get(is_customer_control=True)
        payable = LedgerAccount.objects.get(is_supplier_control=True)
        self.customer_ledger.refresh_from_db()
        self.supplier_ledger.refresh_from_db()
        self.assertEqual(self.customer_ledger.parent, receivable)
        self.assertEqual(self.supplier_ledger.parent, payable)

    def test_a_control_account_equals_the_sum_of_its_parties(self):
        vouchers.open_the_books()
        other = Customer.objects.create(name='Second Customer')
        vouchers.sync_chart_of_accounts()
        self.post('sales' if False else 'journal', [
            self.line('debit', self.customer_ledger, 700),
            self.line('credit', self.sales, 700),
        ])
        self.post('journal', [
            self.line('debit', other.ledger, 300),
            self.line('credit', self.sales, 300),
        ])
        receivable = LedgerAccount.objects.get(is_customer_control=True)
        self.assertEqual(receivable.balance(), Decimal('1000'))

    def test_a_group_account_is_never_posted_to(self):
        group = LedgerAccount.objects.create(kind='expense', name='OVERHEADS', is_group=True)
        res = self.post('journal', [self.line('debit', group, 100),
                                    self.line('credit', self.cash, 100)])
        self.assertEqual(res.status_code, 400)
        self.assertIn('group account', str(res.json()))

    def test_a_control_account_is_never_posted_to(self):
        vouchers.open_the_books()
        receivable = LedgerAccount.objects.get(is_customer_control=True)
        res = self.post('journal', [self.line('debit', receivable, 100),
                                    self.line('credit', self.cash, 100)])
        self.assertEqual(res.status_code, 400)
        self.assertIn('control account', str(res.json()))

    def test_account_types_follow_the_kind_unless_given(self):
        ledger = LedgerAccount.objects.create(kind='expense', name='Insurance')
        self.assertEqual(ledger.account_type, LedgerAccount.EXPENSE)
        self.assertEqual(ledger.normal_balance, 'DR')
        tax = LedgerAccount.objects.get(name='Output VAT')
        self.assertEqual(tax.account_type, LedgerAccount.LIABILITY)
        self.assertEqual(tax.vat_kind, LedgerAccount.VAT_OUTPUT)


class ChartOfAccountsImportTest(LedgerTestCase):
    """The spreadsheet route into the chart of accounts."""

    HEADERS = ['Code', 'Name', 'Type', 'Parent Code', 'Kind', 'Currency',
               'Opening Balance', 'Dr/Cr', 'Description']

    def workbook(self, rows):
        import io as _io
        from django.core.files.uploadedfile import SimpleUploadedFile
        from openpyxl import Workbook
        book = Workbook()
        sheet = book.active
        sheet.append(self.HEADERS)
        for row in rows:
            sheet.append(row)
        buffer = _io.BytesIO()
        book.save(buffer)
        return SimpleUploadedFile('chart.xlsx', buffer.getvalue())

    def upload(self, rows):
        self.client.force_login(self.accountant)
        return self.client.post('/finance/accounting/ledgers/import/',
                                {'file': self.workbook(rows)})

    def test_rows_are_created_and_a_second_upload_updates_them(self):
        rows = [
            ['9000', 'OVERHEADS', 'Expense', '', 'Group', '', '', '', ''],
            ['9001', 'INSURANCE', 'Expense', '9000', '', '', '120000', 'Dr', 'Annual cover'],
        ]
        self.upload(rows)
        group = LedgerAccount.objects.get(code='9000')
        child = LedgerAccount.objects.get(code='9001')
        self.assertTrue(group.is_group)
        self.assertEqual(child.parent, group)
        self.assertEqual(child.account_type, LedgerAccount.EXPENSE)
        self.assertEqual((child.opening_balance, child.opening_side),
                         (Decimal('120000'), 'debit'))
        self.assertEqual(child.notes, 'Annual cover')

        rows[1][1] = 'INSURANCE & LICENCES'
        self.upload(rows)
        child.refresh_from_db()
        self.assertEqual(child.name, 'INSURANCE & LICENCES')
        self.assertEqual(LedgerAccount.objects.filter(code='9001').count(), 1)

    def test_a_bad_row_is_named_and_the_good_ones_still_land(self):
        response = self.upload([
            ['9100', 'GOOD LEDGER', 'Expense', '', '', '', '', '', ''],
            ['9101', 'BAD TYPE', 'Nonsense', '', '', '', '', '', ''],
            ['', 'NO CODE', 'Expense', '', '', '', '', '', ''],
        ])
        results = response.context['results']
        self.assertTrue(results[0].ok)
        self.assertFalse(results[1].ok)
        self.assertFalse(results[2].ok)
        self.assertIn('Unknown Type', results[1].errors[0])
        self.assertTrue(LedgerAccount.objects.filter(code='9100').exists())
        self.assertFalse(LedgerAccount.objects.filter(code='9101').exists())

    def test_a_posted_ledger_keeps_its_type(self):
        self.post('journal', [self.line('debit', self.rent, 100),
                              self.line('credit', self.cash, 100)])
        response = self.upload([[self.rent.code, self.rent.name, 'Income', '', '', '', '', '', '']])
        result = response.context['results'][0]
        self.assertFalse(result.ok)
        self.assertIn('cannot be changed', result.errors[0])
        self.rent.refresh_from_db()
        self.assertEqual(self.rent.account_type, LedgerAccount.EXPENSE)

    def test_the_template_downloads(self):
        self.client.force_login(self.accountant)
        res = self.client.get('/finance/accounting/ledgers/import/template/')
        self.assertEqual(res.status_code, 200)
        self.assertIn('spreadsheetml', res['Content-Type'])


class BalancingEngineTest(LedgerTestCase):
    """The golden rule, and the balancing line the entry screen writes."""

    def test_the_short_side_gets_the_next_line(self):
        suggestion = suggest_balancing_line([
            {'debit': '1000000', 'credit': 0}, {'debit': 0, 'credit': '800000'}])
        self.assertEqual((suggestion['side'], suggestion['amount']),
                         ('credit', Decimal('200000')))
        suggestion = suggest_balancing_line([
            {'debit': '800000', 'credit': 0}, {'debit': 0, 'credit': '1000000'}])
        self.assertEqual((suggestion['side'], suggestion['amount']),
                         ('debit', Decimal('200000')))
        self.assertIsNone(suggest_balancing_line([
            {'debit': '100', 'credit': 0}, {'debit': 0, 'credit': '100'}]))

    def test_the_message_names_the_remaining_difference(self):
        service = BalancingService([{'debit': '1000000', 'credit': 0},
                                    {'debit': 0, 'credit': '800000'}], currency='TZS')
        self.assertIn('200,000.00', service.unbalanced_message())
        self.assertIn('Debit exceeds Credit', service.unbalanced_message())

    def test_the_server_side_endpoint_agrees(self):
        self.client.force_login(self.accountant)
        res = self.client.post('/finance/accounting/api/balance/',
                               json.dumps({'lines': [{'debit': '100', 'credit': 0},
                                                      {'debit': 0, 'credit': '60'}]}),
                               content_type='application/json')
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data['difference'], '40.00')
        self.assertFalse(data['is_balanced'])
        self.assertEqual(data['suggested_line'], {'side': 'credit', 'amount': '40.00'})


class AuditTrailTest(LedgerTestCase):
    """Every act on a voucher is recorded, with who did it."""

    def test_posting_and_reversing_are_both_recorded(self):
        voucher_id = self.post('journal', [self.line('debit', self.rent, 100),
                                           self.line('credit', self.cash, 100)]).json()['id']
        actions = list(AccountingAuditLog.objects.filter(voucher_id=voucher_id)
                       .values_list('action', flat=True))
        self.assertIn('CREATED', actions)
        self.assertIn('POSTED', actions)
        self.assertEqual(AccountingAuditLog.objects.filter(voucher_id=voucher_id,
                                                           action='POSTED').first().username,
                         'acc')

        self.client.force_login(self.accountant)
        self.client.post(f'/api/vouchers/{voucher_id}/reverse/',
                         {'reason': 'mistake'}, content_type='application/json')
        self.assertTrue(AccountingAuditLog.objects.filter(voucher_id=voucher_id,
                                                          action='REVERSED').exists())

    def test_the_trail_is_read_only_over_the_api(self):
        self.client.force_login(self.accountant)
        self.assertEqual(self.client.get('/api/accounting-audit/').status_code, 200)
        self.assertEqual(self.client.post('/api/accounting-audit/', {}).status_code, 405)


class ReportsTest(LedgerTestCase):
    """The thirteen reports, their print layouts and their exports."""

    def setUp(self):
        super().setUp()
        vouchers.open_the_books()
        self.post('journal', [self.line('debit', self.bank_ledger, 1000),
                              self.line('credit', self.sales, 1000)])

    def test_every_report_page_opens(self):
        self.client.force_login(self.accountant)
        for slug, _title, _icon, _desc in accounting_reports.REPORTS:
            res = self.client.get(reverse(f'finance:report_{slug}'))
            self.assertEqual(res.status_code, 200, f'{slug} returned {res.status_code}')

    def test_the_index_and_the_dashboard_open(self):
        self.client.force_login(self.accountant)
        for name in ('finance:report_index', 'finance:accounting_dashboard',
                     'finance:account_list', 'finance:voucher_register',
                     'finance:gl_entries', 'finance:audit_trail',
                     'finance:financial_year_list', 'finance:currency_list',
                     'finance:accounting_settings'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)

    def test_the_entry_screen_opens_for_every_voucher_type(self):
        self.client.force_login(self.accountant)
        for code, _label in Voucher.TYPES:
            res = self.client.get(reverse('finance:voucher_entry',
                                          kwargs={'voucher_type': code}))
            self.assertEqual(res.status_code, 200, code)

    def test_csv_and_excel_export(self):
        self.client.force_login(self.accountant)
        csv_response = self.client.get(reverse('finance:report_trial_balance') + '?export=csv')
        self.assertEqual(csv_response.status_code, 200)
        self.assertIn('text/csv', csv_response['Content-Type'])
        # A UTF-8 BOM so Excel opens it cleanly, then the column headings.
        self.assertTrue(csv_response.content.startswith(b'\xef\xbb\xbfCode,Ledger,Type'))
        self.assertIn(b',TOTAL,', csv_response.content)

        xlsx = self.client.get(reverse('finance:report_trial_balance') + '?export=xlsx')
        self.assertEqual(xlsx.status_code, 200)
        self.assertIn('spreadsheetml', xlsx['Content-Type'])

    def test_the_print_layout_drops_the_chrome(self):
        self.client.force_login(self.accountant)
        res = self.client.get(reverse('finance:report_trial_balance') + '?print=1')
        self.assertEqual(res.status_code, 200)
        # The carried-over shell: `auto-print` makes accounting_app.js open the
        # print dialog, and accounting.css hides the sidebar, topbar and filters.
        self.assertIn(b'<body class="auto-print">', res.content)
        self.assertIn(b'js/accounting_app.js', res.content)

    def test_the_trial_balance_balances(self):
        rows, totals = trial_balance_rows()
        self.assertEqual(totals['difference'], Decimal('0'))
        self.assertEqual(totals['debit'], Decimal('1000'))
        self.assertEqual(totals['credit'], Decimal('1000'))

    def test_the_general_ledger_report_carries_a_running_balance(self):
        data = accounting_reports.general_ledger(account=self.bank_ledger)
        section = data['sections'][0]
        self.assertEqual(section['opening'], Decimal('0'))
        self.assertEqual(section['closing'], Decimal('1000'))
        self.assertEqual(section['rows'][0]['balance'], Decimal('1000'))
        # The "related ledger" column names the other side of the voucher.
        self.assertEqual([a.name for a in section['rows'][0]['related']], ['Sales Revenue'])

    def test_the_vat_report_nets_output_against_input(self):
        output_vat = LedgerAccount.objects.get(name='Output VAT')
        input_vat = LedgerAccount.objects.get(name='Input VAT')
        self.post('journal', [self.line('debit', self.bank_ledger, 180),
                              self.line('credit', output_vat, 180)])
        self.post('journal', [self.line('debit', input_vat, 50),
                              self.line('credit', self.bank_ledger, 50)])
        data = accounting_reports.vat_report()
        self.assertEqual(data['output_total'], Decimal('180'))
        self.assertEqual(data['input_total'], Decimal('50'))
        self.assertEqual(data['net_vat'], Decimal('130'))

    def test_a_cashier_is_kept_out_of_all_of_it(self):
        self.client.force_login(self.cashier)
        for name in ('finance:accounting_dashboard', 'finance:report_index',
                     'finance:account_list', 'finance:voucher_register',
                     'finance:audit_trail'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)


class VoucherScreenTest(LedgerTestCase):
    """The server-rendered entry screen: drafts in, posted out."""

    def entry_post(self, voucher_type, rows, action='post', **header):
        """Submit the entry form the way the browser does, formsets and all."""
        data = {
            'date': '2026-09-10', 'description': 'Keyed on the screen',
            'action': action,
            'lines-TOTAL_FORMS': str(len(rows)), 'lines-INITIAL_FORMS': '0',
            'lines-MIN_NUM_FORMS': '0', 'lines-MAX_NUM_FORMS': '500',
            'alloc-TOTAL_FORMS': '0', 'alloc-INITIAL_FORMS': '0',
            'alloc-MIN_NUM_FORMS': '0', 'alloc-MAX_NUM_FORMS': '500',
        }
        data.update(header)
        for index, (account, debit, credit) in enumerate(rows):
            data[f'lines-{index}-account'] = str(account.id)
            data[f'lines-{index}-debit'] = str(debit or '')
            data[f'lines-{index}-credit'] = str(credit or '')
            data[f'lines-{index}-description'] = ''
        self.client.force_login(self.accountant)
        return self.client.post(
            reverse('finance:voucher_entry', kwargs={'voucher_type': voucher_type}), data)

    def test_saving_a_draft_then_posting_it(self):
        vouchers.open_the_books()
        res = self.entry_post('journal', [(self.rent, 500, 0), (self.cash, 0, 500)],
                              action='save')
        self.assertEqual(res.status_code, 302)
        voucher = Voucher.objects.get()
        self.assertEqual(voucher.status, 'draft')
        self.assertEqual(GeneralLedgerEntry.objects.count(), 0)

        posted = self.client.post(reverse('finance:voucher_post', args=[voucher.pk]))
        self.assertEqual(posted.status_code, 302)
        voucher.refresh_from_db()
        self.assertEqual(voucher.status, 'posted')
        self.assertEqual(GeneralLedgerEntry.objects.count(), 2)

    def test_posting_straight_from_the_screen(self):
        vouchers.open_the_books()
        res = self.entry_post('journal', [(self.rent, 500, 0), (self.cash, 0, 500)])
        self.assertEqual(res.status_code, 302)
        self.assertEqual(Voucher.objects.get().status, 'posted')

    def test_an_unbalanced_submission_is_saved_as_a_draft_not_posted(self):
        vouchers.open_the_books()
        res = self.entry_post('journal', [(self.rent, 500, 0), (self.cash, 0, 300)])
        self.assertEqual(res.status_code, 302)
        voucher = Voucher.objects.get()
        self.assertEqual(voucher.status, 'draft')
        self.assertEqual(GeneralLedgerEntry.objects.count(), 0)

    def test_the_detail_and_print_pages_open(self):
        vouchers.open_the_books()
        self.entry_post('journal', [(self.rent, 500, 0), (self.cash, 0, 500)])
        voucher = Voucher.objects.get()
        self.client.force_login(self.accountant)
        self.assertEqual(
            self.client.get(reverse('finance:voucher_detail', args=[voucher.pk])).status_code, 200)
        printed = self.client.get(reverse('finance:voucher_print', args=[voucher.pk]))
        self.assertEqual(printed.status_code, 200)
        self.assertIn(voucher.number.encode(), printed.content)


class StatementsAndTheLedgerTest(LedgerTestCase):
    """The three statements read the till *and* the voucher ledger, and count
    each thing exactly once.

    Nothing auto-posts a voucher, so the two records are disjoint by
    construction. These tests are what keeps that true: if anything ever
    starts mirroring a sale into a voucher, the double counting shows up here
    rather than in somebody's accounts.
    """

    def setUp(self):
        super().setUp()
        vouchers.open_the_books()
        self.window = ('2026-09-01', '2026-09-30')

    def test_a_voucher_sale_reaches_the_profit_and_loss(self):
        before = profit_and_loss(*self.window)
        self.assertEqual(Decimal(before['revenue']), Decimal('0'))

        self.post('journal', [self.line('debit', self.bank_ledger, 500000),
                              self.line('credit', self.sales, 500000)])
        after = profit_and_loss(*self.window)
        self.assertEqual(Decimal(after['revenue_vouchers']), Decimal('500000'))
        self.assertEqual(Decimal(after['revenue_till']), Decimal('0'))
        self.assertEqual(Decimal(after['revenue']), Decimal('500000'))
        self.assertEqual(Decimal(after['net_profit']), Decimal('500000'))

    def test_a_voucher_expense_reaches_the_profit_and_loss(self):
        self.post('journal', [self.line('debit', self.rent, 90000),
                              self.line('credit', self.bank_ledger, 90000)])
        data = profit_and_loss(*self.window)
        self.assertEqual(Decimal(data['operating_costs']), Decimal('90000'))
        self.assertEqual(Decimal(data['net_profit']), Decimal('-90000'))

    def test_a_till_sale_and_a_voucher_sale_are_each_counted_once(self):
        """The two records are disjoint, so the P&L is the sum of them — and
        neither one is doubled."""
        sale = Sale.objects.create(invoice_number='TILL-1', branch=self.branch,
                                   customer=self.customer, customer_name=self.customer.name,
                                   total_amount=Decimal('300000'), status='approved')
        entry = SalesLedgerEntry.objects.get(invoice_number='TILL-1')
        entry.status = 'posted'
        entry.sale_date = date(2026, 9, 15)
        entry.total_amount = Decimal('300000')
        entry.save()

        self.post('journal', [self.line('debit', self.bank_ledger, 500000),
                              self.line('credit', self.sales, 500000)])

        data = profit_and_loss(*self.window)
        self.assertEqual(Decimal(data['revenue_till']), Decimal('300000'))
        self.assertEqual(Decimal(data['revenue_vouchers']), Decimal('500000'))
        self.assertEqual(Decimal(data['revenue']), Decimal('800000'))
        self.assertEqual(sale.invoice_number, 'TILL-1')

    def test_a_reversal_takes_the_revenue_back_out(self):
        voucher_id = self.post('journal', [self.line('debit', self.bank_ledger, 500000),
                                            self.line('credit', self.sales, 500000)]).json()['id']
        self.assertEqual(Decimal(profit_and_loss(*self.window)['revenue']), Decimal('500000'))

        self.client.force_login(self.accountant)
        self.client.post(f'/api/vouchers/{voucher_id}/reverse/',
                         {'date': '2026-09-20', 'reason': 'keyed twice'},
                         content_type='application/json')
        # The original's entries stay; the reversing journal cancels them, so
        # the figure nets back to nothing rather than being erased.
        self.assertEqual(Decimal(profit_and_loss(*self.window)['revenue']), Decimal('0'))

    def test_money_moved_on_a_voucher_shows_in_the_cash_flow(self):
        self.post('receipt', [self.line('debit', self.bank_ledger, 400000),
                              self.line('credit', self.sales, 400000)])
        self.post('payment', [self.line('debit', self.rent, 150000),
                              self.line('credit', self.cash, 150000)])
        data = cash_flow(*self.window)
        self.assertEqual(Decimal(data['voucher_receipts']), Decimal('400000'))
        self.assertEqual(Decimal(data['voucher_payments']), Decimal('150000'))
        self.assertEqual(Decimal(data['net_movement']), Decimal('250000'))

    def test_a_contra_nets_to_nothing_in_the_cash_flow(self):
        """Moving our own money between pockets is not a flow."""
        self.post('contra', [self.line('debit', self.cash, 200000),
                             self.line('credit', self.bank_ledger, 200000)])
        data = cash_flow(*self.window)
        self.assertEqual(Decimal(data['voucher_receipts']), Decimal('200000'))
        self.assertEqual(Decimal(data['voucher_payments']), Decimal('200000'))
        self.assertEqual(Decimal(data['net_movement']), Decimal('0'))

    def test_a_register_invoice_is_a_debtor_on_the_balance_sheet(self):
        before = Decimal(balance_sheet()['debtors'])
        self.client.force_login(self.accountant)
        self.client.post('/api/vouchers/', {
            'voucher_type': 'sales', 'date': '2026-09-10', 'invoice_number': 'BS-1',
            'customer': self.customer.id,
            'lines': [self.line('debit', self.customer_ledger, 250000),
                      self.line('credit', self.sales, 250000)],
        }, content_type='application/json')
        after = balance_sheet()
        self.assertEqual(Decimal(after['debtors']) - before, Decimal('250000'))

    def test_a_register_bill_is_a_creditor_on_the_balance_sheet(self):
        before = Decimal(balance_sheet()['creditors'])
        self.client.force_login(self.accountant)
        res = self.client.post('/api/vouchers/', {
            'voucher_type': 'purchase', 'date': '2026-09-10', 'invoice_number': 'BP-1',
            'supplier': self.supplier.id,
            'lines': [self.line('debit', LedgerAccount.objects.get(name='Purchases'), 180000),
                      self.line('credit', self.supplier_ledger, 180000)],
        }, content_type='application/json')
        self.assertEqual(res.status_code, 201, res.content)
        after = balance_sheet()
        self.assertEqual(Decimal(after['creditors']) - before, Decimal('180000'))

    def test_the_balance_sheet_cash_follows_voucher_money(self):
        before = Decimal(balance_sheet()['cash'])
        self.post('receipt', [self.line('debit', self.bank_ledger, 120000),
                              self.line('credit', self.sales, 120000)])
        self.assertEqual(Decimal(balance_sheet()['cash']) - before, Decimal('120000'))

    def test_the_statement_pages_still_open(self):
        self.client.force_login(self.accountant)
        for name in ('finance:profit_loss', 'finance:cash_flow', 'finance:balance_sheet',
                     'finance:sales_ledger'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        for url in ('/api/profit-loss/', '/api/cash-flow/', '/api/balance-sheet/'):
            self.assertEqual(self.client.get(url).status_code, 200, url)


class SalesAccountingPostsToTheBooksTest(LedgerTestCase):
    """Accounts' Post on Sales Accounting writes the sale into the General
    Ledger as a Sales voucher — and the statements still count it once."""

    def setUp(self):
        super().setUp()
        vouchers.open_the_books()

    def make_sale(self, number, total, customer=True):
        Sale.objects.create(invoice_number=number, branch=self.branch,
                            customer=self.customer if customer else None,
                            customer_name=self.customer.name if customer else 'Walk-in Customer',
                            total_amount=Decimal(total), status='approved')
        return SalesLedgerEntry.objects.get(invoice_number=number)

    def post_entry(self, entry, method='cash', amount=None):
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.client.force_login(self.accountant)
        data = {'method': method,
                'invoice_document': SimpleUploadedFile('inv.pdf', b'%PDF-1.4', 'application/pdf')}
        if amount is not None:
            data['amount'] = str(amount)
        return self.client.post(f'/api/sales-ledger/{entry.id}/post_entry/', data)

    def gl(self, voucher):
        return {(e.account_id, e.debit, e.credit)
                for e in GeneralLedgerEntry.objects.filter(voucher=voucher)}

    def test_posting_a_paid_sale_writes_a_sales_voucher(self):
        entry = self.make_sale('PS-1', 300000)
        res = self.post_entry(entry)
        self.assertEqual(res.status_code, 200, res.content)
        voucher = Voucher.objects.get(sales_entry=entry)
        self.assertEqual(voucher.voucher_type, 'sales')
        self.assertEqual(voucher.status, 'posted')
        self.assertEqual(voucher.invoice_number, 'PS-1')
        self.assertEqual(self.gl(voucher), {(self.cash.id, Decimal('300000'), Decimal('0')),
                                            (self.sales.id, Decimal('0'), Decimal('300000'))})
        self.assertEqual(res.json()['voucher']['number'], voucher.number)
        # The till sale is what gets allocated against — no second copy.
        self.assertFalse(Invoice.objects.filter(voucher=voucher).exists())

    def test_a_bank_payment_goes_to_the_bank(self):
        entry = self.make_sale('PS-2', 100000)
        self.post_entry(entry, method='bank')
        voucher = Voucher.objects.get(sales_entry=entry)
        self.assertIn((self.bank_ledger.id, Decimal('100000'), Decimal('0')), self.gl(voucher))

    def test_a_part_payment_leaves_the_rest_on_the_customer(self):
        entry = self.make_sale('PS-3', 500000)
        self.post_entry(entry, amount=200000)
        voucher = Voucher.objects.get(sales_entry=entry)
        self.assertEqual(self.gl(voucher), {
            (self.cash.id, Decimal('200000'), Decimal('0')),
            (self.customer_ledger.id, Decimal('300000'), Decimal('0')),
            (self.sales.id, Decimal('0'), Decimal('500000'))})
        # Offered for allocation once — as the till sale, not again as a voucher.
        targets = [r['target'] for r in vouchers.outstanding_invoices(self.customer)]
        self.assertNotIn('voucher', targets)

    def test_a_walk_in_part_payment_goes_to_walk_in_debtors(self):
        entry = self.make_sale('PS-4', 50000, customer=False)
        self.post_entry(entry, amount=20000)
        voucher = Voucher.objects.get(sales_entry=entry)
        debtors = LedgerAccount.objects.get(name='Walk-in Debtors')
        self.assertIn((debtors.id, Decimal('30000'), Decimal('0')), self.gl(voucher))

    def test_the_statements_do_not_count_the_sale_twice(self):
        entry = self.make_sale('PS-5', 300000)
        self.post_entry(entry)
        entry.refresh_from_db()
        day = entry.sale_date.isoformat()
        data = profit_and_loss(day, day)
        self.assertEqual(Decimal(data['revenue_till']), Decimal('300000'))
        self.assertEqual(Decimal(data['revenue_vouchers']), Decimal('0'))
        self.assertEqual(Decimal(cash_flow(day, day)['voucher_receipts']), Decimal('0'))

    def test_a_refused_posting_changes_nothing(self):
        entry = self.make_sale('PS-6', 300000)
        FinancialYear.objects.update(is_closed=True)
        res = self.post_entry(entry)
        self.assertEqual(res.status_code, 400)
        self.assertIn('books refused', res.json()['detail'])
        entry.refresh_from_db()
        self.assertEqual(entry.status, 'pending')
        self.assertFalse(Voucher.objects.filter(sales_entry=entry).exists())
