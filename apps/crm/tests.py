import io
from datetime import date
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from openpyxl import Workbook, load_workbook

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
        self.assertEqual(listing['count'], 1)
        self.assertEqual(listing['results'][0]['efd_receipt_number'], '35EFD9921')

        # A search that matches nothing filters the row out
        self.assertEqual(self.client.get('/api/crm-records/?search=nobody').json()['count'], 0)

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


class CrmCustomerListTest(TestCase):
    """The CRM landing screen groups the register by customer."""

    def setUp(self):
        self.accountant = User.objects.create_user(username='crm_acct4', password='pw', role='accountant')
        self.client.force_login(self.accountant)
        CustomerRecord.objects.create(date=date(2026, 8, 1), customer_name='Kibo Traders',
                                      receipt_number='RC-1', tin='', sales_amount=100)
        CustomerRecord.objects.create(date=date(2026, 8, 5), customer_name='Kibo Traders',
                                      receipt_number='RC-2', tin='109-882-441', sales_amount=250)
        CustomerRecord.objects.create(date=date(2026, 8, 3), customer_name='Mwanza Const',
                                      receipt_number='RC-3', tin='122-004-908', sales_amount=900)

    def test_customers_are_grouped_with_totals(self):
        rows = self.client.get('/api/crm-records/customers/').json()['results']
        self.assertEqual([r['customer_name'] for r in rows], ['Mwanza Const', 'Kibo Traders'])

        kibo = next(r for r in rows if r['customer_name'] == 'Kibo Traders')
        self.assertEqual(kibo['records'], 2)
        self.assertEqual(float(kibo['total_amount']), 350.0)
        self.assertEqual(kibo['first_transaction'], '2026-08-01')
        self.assertEqual(kibo['last_transaction'], '2026-08-05')
        # TIN comes from the most recent record that carries one
        self.assertEqual(kibo['tin'], '109-882-441')

    def test_customer_list_respects_filters(self):
        rows = self.client.get('/api/crm-records/customers/?search=Mwanza').json()['results']
        self.assertEqual([r['customer_name'] for r in rows], ['Mwanza Const'])

        # A date window re-totals each customer rather than dropping them
        rows = self.client.get('/api/crm-records/customers/?start=2026-08-04').json()['results']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['customer_name'], 'Kibo Traders')
        self.assertEqual(rows[0]['records'], 1)
        self.assertEqual(float(rows[0]['total_amount']), 250.0)

    def test_records_can_be_scoped_to_one_customer(self):
        rows = self.client.get('/api/crm-records/?customer=Kibo Traders').json()['results']
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r['customer_name'] == 'Kibo Traders' for r in rows))

        summary = self.client.get('/api/crm-records/summary/?customer=Kibo Traders').json()
        self.assertEqual(summary['records'], 2)
        self.assertEqual(float(summary['total_amount']), 350.0)

        # Exact match — a partial name must not leak another customer's rows
        self.assertEqual(self.client.get('/api/crm-records/?customer=Kibo').json()['results'], [])

    def test_detail_page_renders_for_a_known_customer(self):
        response = self.client.get(reverse('crm:customer_detail'), {'name': 'Kibo Traders'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['exists'])
        self.assertEqual(response.context['customer_name'], 'Kibo Traders')
        self.assertEqual(response.context['tin'], '109-882-441')
        self.assertContains(response, 'Kibo Traders')

    def test_detail_page_handles_unknown_customer(self):
        response = self.client.get(reverse('crm:customer_detail'), {'name': 'Nobody Ltd'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['exists'])
        self.assertContains(response, 'No records found')

    def test_detail_export_is_scoped_to_the_customer(self):
        response = self.client.get(reverse('crm:export'), {'customer': 'Kibo Traders'})
        body = response.content.decode()
        self.assertIn('RC-1', body)
        self.assertIn('RC-2', body)
        self.assertNotIn('RC-3', body)

    def test_detail_page_is_closed_to_other_roles(self):
        self.client.force_login(User.objects.create_user(username='crm_rep3', password='pw', role='sales_rep'))
        response = self.client.get(reverse('crm:customer_detail'), {'name': 'Kibo Traders'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get('/api/crm-records/customers/').status_code, 403)


class CrmPaginationTest(TestCase):
    """Neither table may ever return the whole register."""

    def setUp(self):
        self.accountant = User.objects.create_user(username='crm_acct5', password='pw', role='accountant')
        self.client.force_login(self.accountant)
        CustomerRecord.objects.bulk_create([
            CustomerRecord(date=date(2026, 8, 1), customer_name=f'Customer {i:03d}',
                           receipt_number=f'RC-{i:03d}', sales_amount=10 * i)
            for i in range(1, 46)
        ])

    def test_records_default_to_20_per_page(self):
        body = self.client.get('/api/crm-records/').json()
        self.assertEqual(body['count'], 45)
        self.assertEqual(len(body['results']), 20)
        self.assertIsNotNone(body['next'])

    def test_page_size_options(self):
        for size in (10, 20, 50, 100):
            body = self.client.get('/api/crm-records/', {'page_size': size}).json()
            self.assertEqual(len(body['results']), min(size, 45), f'page_size={size}')

    def test_page_size_is_capped_at_100(self):
        body = self.client.get('/api/crm-records/', {'page_size': 5000}).json()
        self.assertEqual(len(body['results']), 45)  # capped, not unbounded
        CustomerRecord.objects.bulk_create([
            CustomerRecord(date=date(2026, 8, 2), customer_name=f'Extra {i}', sales_amount=1)
            for i in range(120)
        ])
        body = self.client.get('/api/crm-records/', {'page_size': 5000}).json()
        self.assertEqual(len(body['results']), 100)

    def test_paging_walks_the_whole_set_without_repeats(self):
        seen, page = [], 1
        while True:
            body = self.client.get('/api/crm-records/', {'page_size': 10, 'page': page}).json()
            seen.extend(r['id'] for r in body['results'])
            if not body['next']:
                break
            page += 1
        self.assertEqual(len(seen), 45)
        self.assertEqual(len(set(seen)), 45)

    def test_customer_list_is_paginated_too(self):
        body = self.client.get('/api/crm-records/customers/', {'page_size': 10}).json()
        self.assertEqual(body['count'], 45)
        self.assertEqual(len(body['results']), 10)

    def test_summary_totals_the_whole_selection_not_the_page(self):
        """The stat tiles must not report only what is on screen."""
        summary = self.client.get('/api/crm-records/summary/', {'page_size': 10}).json()
        self.assertEqual(summary['records'], 45)
        self.assertEqual(summary['customers'], 45)
        self.assertEqual(float(summary['total_amount']), sum(10 * i for i in range(1, 46)))

    def test_csv_export_is_not_paginated(self):
        """Export gives every matching row, not the current page."""
        response = self.client.get(reverse('crm:export'), {'page_size': 10})
        rows = response.content.decode().strip().splitlines()
        self.assertEqual(len(rows), 46)  # 45 records + header


def build_xlsx(rows, headers=None, name='customers.xlsx'):
    """An in-memory .xlsx upload, as the browser would send it."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(headers if headers is not None
                 else ['Date', 'Receipt No', 'EFD Receipt No', 'Customer Name', 'TIN', 'Sales Amount'])
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return SimpleUploadedFile(
        name, buffer.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )


class CrmBulkUploadTest(TestCase):
    def setUp(self):
        self.accountant = User.objects.create_user(username='crm_acct3', password='pw', role='accountant')
        self.client.force_login(self.accountant)

    def post_file(self, upload):
        return self.client.post('/api/crm-records/bulk_upload/', {'file': upload})

    def test_uploads_rows_from_excel(self):
        upload = build_xlsx([
            ['2026-08-01', 'RC-0012', '35EFD9921', 'Kibo Traders', '109-882-441', 1250000],
            [date(2026, 8, 3), 'RC-0013', '35EFD9944', 'Mwanza Const', '122-004-908', '840,500.50'],
        ])
        response = self.post_file(upload)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['created'], 2)

        first = CustomerRecord.objects.get(receipt_number='RC-0012')
        self.assertEqual(first.date, date(2026, 8, 1))
        self.assertEqual(first.customer_name, 'Kibo Traders')
        self.assertEqual(first.tin, '109-882-441')
        self.assertEqual(first.sales_amount, Decimal('1250000.00'))
        self.assertEqual(first.created_by, self.accountant)

        # A real date cell and a comma-formatted amount both survive
        second = CustomerRecord.objects.get(receipt_number='RC-0013')
        self.assertEqual(second.date, date(2026, 8, 3))
        self.assertEqual(second.sales_amount, Decimal('840500.50'))

    def test_headers_are_matched_loosely_and_extras_ignored(self):
        upload = build_xlsx(
            [['01/08/2026', 'Kibo Traders', '35EFD9921', '109-882-441', 500, 'ignored']],
            headers=['  DATE ', 'customer', 'EFD Receipt Number', 'TIN No', 'Amount', 'Notes'],
        )
        response = self.post_file(upload)
        self.assertEqual(response.status_code, 200, response.content)

        record = CustomerRecord.objects.get()
        self.assertEqual(record.date, date(2026, 8, 1))
        self.assertEqual(record.customer_name, 'Kibo Traders')
        self.assertEqual(record.efd_receipt_number, '35EFD9921')
        self.assertEqual(record.sales_amount, Decimal('500'))

    def test_bad_rows_are_reported_and_good_rows_still_import(self):
        upload = build_xlsx([
            ['2026-08-01', 'RC-1', '', 'Good Customer', '', 100],
            ['not a date', 'RC-2', '', 'Bad Date', '', 100],
            ['2026-08-02', 'RC-3', '', '', '', 100],
            [],  # blank spacer row is skipped silently
            ['2026-08-03', 'RC-4', '', 'Bad Amount', '', 'abc'],
            ['2026-08-04', 'RC-5', '', 'Blank Amount', '', ''],
        ])
        response = self.post_file(upload)
        self.assertEqual(response.status_code, 207)
        body = response.json()
        self.assertEqual(body['created'], 2)  # Good Customer + Blank Amount
        self.assertEqual(body['error_count'], 3)
        for row_label in ('Row 3:', 'Row 4:', 'Row 6:'):
            self.assertTrue(any(e.startswith(row_label) for e in body['errors']), body['errors'])
        self.assertEqual(CustomerRecord.objects.filter(customer_name__in=['Bad Date', 'Bad Amount']).count(), 0)
        # A blank amount cell is not an error — it just means nothing recorded yet
        self.assertEqual(CustomerRecord.objects.get(receipt_number='RC-5').sales_amount, Decimal('0.00'))

    def test_reupload_skips_rows_already_in_register(self):
        rows = [['2026-08-01', 'RC-0012', '35EFD9921', 'Kibo Traders', '', 100]]
        self.assertEqual(self.post_file(build_xlsx(rows)).json()['created'], 1)

        again = self.post_file(build_xlsx(rows)).json()
        self.assertEqual(again['created'], 0)
        self.assertEqual(again['skipped'], 1)
        self.assertEqual(CustomerRecord.objects.count(), 1)

    def test_missing_required_column_is_rejected(self):
        upload = build_xlsx([['RC-1', 100]], headers=['Receipt No', 'Amount'])
        response = self.post_file(upload)
        self.assertEqual(response.status_code, 400)
        self.assertIn('Customer Name', response.json()['detail'])
        self.assertEqual(CustomerRecord.objects.count(), 0)

    def test_wrong_file_type_is_rejected(self):
        upload = SimpleUploadedFile('customers.pdf', b'%PDF-1.4 not a spreadsheet', content_type='application/pdf')
        response = self.post_file(upload)
        self.assertEqual(response.status_code, 400)
        self.assertIn('Unsupported file type', response.json()['detail'])

    def test_csv_is_accepted_too(self):
        csv_bytes = (
            'Date,Receipt No,EFD Receipt No,Customer Name,TIN,Sales Amount\n'
            '2026-08-01,RC-0012,35EFD9921,Kibo Traders,109-882-441,1250000\n'
        ).encode('utf-8')
        response = self.post_file(SimpleUploadedFile('customers.csv', csv_bytes, content_type='text/csv'))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(CustomerRecord.objects.get().customer_name, 'Kibo Traders')

    def test_no_file_returns_400(self):
        response = self.client.post('/api/crm-records/bulk_upload/', {})
        self.assertEqual(response.status_code, 400)

    def test_template_download_is_a_readable_workbook(self):
        response = self.client.get(reverse('crm:import_template'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('spreadsheetml', response['Content-Type'])
        sheet = load_workbook(io.BytesIO(response.content)).active
        self.assertEqual(
            [c.value for c in sheet[1]],
            ['Date', 'Receipt No', 'EFD Receipt No', 'Customer Name', 'TIN', 'Sales Amount'],
        )

    def test_upload_is_closed_to_other_roles(self):
        self.client.force_login(User.objects.create_user(username='crm_rep2', password='pw', role='sales_rep'))
        response = self.post_file(build_xlsx([['2026-08-01', 'RC-9', '', 'Sneaky', '', 1]]))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(CustomerRecord.objects.count(), 0)
        self.assertEqual(self.client.get(reverse('crm:import_template')).status_code, 403)
