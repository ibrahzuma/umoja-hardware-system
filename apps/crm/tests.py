from datetime import date

from django.test import TestCase
from django.urls import reverse

from apps.crm.models import CustomerRecord
from apps.inventory.models import Branch
from apps.sales.models import Customer, Sale
from apps.users.models import User


class CrmAccessTest(TestCase):
    """CRM is an admin + finance screen; everyone else is locked out."""

    def setUp(self):
        self.admin = User.objects.create_user(username='crm_admin', password='pw', role='admin')
        self.accountant = User.objects.create_user(username='crm_acct', password='pw', role='accountant')
        self.sales_rep = User.objects.create_user(username='crm_rep', password='pw', role='sales_rep')

    def test_admin_and_accountant_can_open_page(self):
        for user in (self.admin, self.accountant):
            self.client.force_login(user)
            response = self.client.get(reverse('crm:customer_records'))
            self.assertEqual(response.status_code, 200, user.role)

    def test_other_roles_are_blocked(self):
        self.client.force_login(self.sales_rep)
        self.assertEqual(self.client.get(reverse('crm:customer_records')).status_code, 403)
        self.assertEqual(self.client.get('/api/crm-records/').status_code, 403)


class CrmRecordApiTest(TestCase):
    def setUp(self):
        self.accountant = User.objects.create_user(username='crm_acct2', password='pw', role='accountant')
        self.client.force_login(self.accountant)

    def test_create_list_and_summary(self):
        payload = {
            'date': '2026-08-01',
            'receipt_number': 'RC-0012',
            'efd_receipt_number': '35EFD9921',
            'customer_name': 'Kibo Traders',
            'tin': '109-882-441',
            'sales_amount': '1250000.00',
        }
        response = self.client.post('/api/crm-records/', payload, content_type='application/json')
        self.assertEqual(response.status_code, 201, response.content)

        record = CustomerRecord.objects.get()
        self.assertEqual(record.customer_name, 'Kibo Traders')
        self.assertEqual(record.created_by, self.accountant)

        listing = self.client.get('/api/crm-records/?search=Kibo').json()
        self.assertEqual(len(listing), 1)
        self.assertEqual(listing[0]['efd_receipt_number'], '35EFD9921')

        # A search that matches nothing filters the row out
        self.assertEqual(len(self.client.get('/api/crm-records/?search=nobody').json()), 0)

        summary = self.client.get('/api/crm-records/summary/').json()
        self.assertEqual(summary['records'], 1)
        self.assertEqual(summary['customers'], 1)
        self.assertEqual(float(summary['total_amount']), 1250000.00)

    def test_csv_export_uses_filters(self):
        CustomerRecord.objects.create(date=date(2026, 8, 1), customer_name='Kibo Traders', sales_amount=100)
        CustomerRecord.objects.create(date=date(2026, 8, 5), customer_name='Mwanza Const', sales_amount=200)

        response = self.client.get(reverse('crm:export'), {'search': 'Kibo'})
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn('Kibo Traders', body)
        self.assertNotIn('Mwanza Const', body)


class CrmImportFromSalesTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='crm_admin2', password='pw', role='admin')
        self.client.force_login(self.admin)
        branch = Branch.objects.create(name='Main', address='Dar es Salaam')
        customer = Customer.objects.create(name='Kibo Traders', phone='0700000000')
        self.sale = Sale.objects.create(
            invoice_number='INV-CRM-1', branch=branch, customer=customer,
            status='approved', total_amount=500000,
        )

    def test_import_creates_editable_records_and_skips_duplicates(self):
        available = self.client.get('/api/crm-records/available_sales/').json()
        self.assertEqual([s['invoice_number'] for s in available], ['INV-CRM-1'])

        response = self.client.post(
            '/api/crm-records/import_sales/',
            {'invoices': ['INV-CRM-1']}, content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['created'], 1)

        record = CustomerRecord.objects.get()
        self.assertEqual(record.customer_name, 'Kibo Traders')
        self.assertEqual(record.receipt_number, 'INV-CRM-1')
        self.assertEqual(record.sales_amount, 500000)

        # Imported rows are plain CRM data: editing one leaves the sale alone
        record.customer_name = 'Kibo Traders Ltd'
        record.sales_amount = 600000
        record.save()
        self.sale.refresh_from_db()
        self.assertEqual(self.sale.total_amount, 500000)

        # The same sale is not offered or imported twice
        self.assertEqual(self.client.get('/api/crm-records/available_sales/').json(), [])
        again = self.client.post(
            '/api/crm-records/import_sales/',
            {'invoices': ['INV-CRM-1']}, content_type='application/json',
        )
        self.assertEqual(again.json()['created'], 0)
        self.assertEqual(CustomerRecord.objects.count(), 1)

    def test_import_requires_invoices(self):
        response = self.client.post('/api/crm-records/import_sales/', {'invoices': []}, content_type='application/json')
        self.assertEqual(response.status_code, 400)
