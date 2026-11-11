"""SMS notifications: signing, phone normalisation, the pending-sale alert and the page."""
import hashlib
import hmac
import json
from unittest import mock

from django.test import TestCase
from django.urls import reverse

from apps.core import sms
from apps.core.models import SmsLog, SmsRecipient, SmsSettings
from apps.inventory.models import Branch
from apps.sales.models import Sale
from apps.users.models import User


class FakeResp:
    def __init__(self, data, code=200):
        self._data, self.status_code, self.text = data, code, json.dumps(data)

    def json(self):
        return self._data


SENDERS = {'success': True, 'data': {'own': [{'id': 'uuid-ehub', 'sender_id': 'ehub', 'status': 'approved'}],
                                      'shared': [], 'public': []}}
SENT = {'success': True, 'data': {'message_id': 'm1', 'status': 'sent'}}


def fake_gateway(calls):
    def request(method, url, data=None, headers=None, timeout=None):
        calls.append((method, url, data, headers))
        return FakeResp(SENDERS if url.endswith('/sender-ids') else SENT)
    return request


class PhoneTest(TestCase):
    def test_normalize(self):
        self.assertEqual(sms.normalize_phone('0747 093 762'), '255747093762')
        self.assertEqual(sms.normalize_phone('+255747093762'), '255747093762')
        self.assertEqual(sms.normalize_phone('747093762'), '255747093762')
        self.assertEqual(sms.normalize_phone('12345'), '')


class SigningTest(TestCase):
    def test_signature_covers_timestamp_method_full_path_and_body(self):
        calls = []
        with mock.patch('apps.core.sms.requests.request', side_effect=fake_gateway(calls)):
            sms.EhubClient('k', 's').send('255747093762', 'hi', 'uuid-ehub')
        method, url, body, headers = calls[0]
        self.assertEqual(url, 'https://sms.ehub.co.tz/api/v1/sms/send')
        payload = f"{headers['X-Timestamp']}\nPOST\n/api/v1/sms/send\n{body.decode()}"
        expected = hmac.new(b's', payload.encode(), hashlib.sha256).hexdigest()
        self.assertEqual(headers['X-Signature'], expected)
        self.assertEqual(headers['Authorization'], 'Bearer k')
        self.assertEqual(json.loads(body)['sender_id'], 'uuid-ehub')


class PendingSaleAlertTest(TestCase):
    def setUp(self):
        sms.ASYNC = False
        self.branch = Branch.objects.create(name='Main')
        self.admin = User.objects.create_user('boss', password='pw', role='admin')
        self.rep = User.objects.create_user('rep', password='pw', role='sales_rep')
        SmsRecipient.objects.create(user=self.admin, phone='255747093762')
        SmsRecipient.objects.create(user=self.rep, phone='255700000000')  # not an approver
        s = SmsSettings.load()
        s.enabled, s.api_key, s.api_secret = True, 'k', 's'
        s.save()

    def tearDown(self):
        sms.ASYNC = True

    def _sale(self, status='pending'):
        return Sale.objects.create(invoice_number='INV1', user=self.rep, branch=self.branch,
                                   total_amount=150000, status=status)

    def test_texts_only_approvers_with_sender_resolved(self):
        calls = []
        with mock.patch('apps.core.sms.requests.request', side_effect=fake_gateway(calls)):
            logs = sms.notify_pending_sale(self._sale().pk)
        self.assertEqual([l.to for l in logs], ['255747093762'])
        self.assertEqual(logs[0].status, 'sent')
        self.assertIn('INV1', logs[0].message)
        self.assertIn('150,000', logs[0].message)
        self.assertEqual(SmsSettings.load().sender_uuid, 'uuid-ehub')

    def test_nothing_when_switched_off_or_not_pending(self):
        s = SmsSettings.load()
        s.enabled = False
        s.save()
        with mock.patch('apps.core.sms.requests.request') as req:
            self.assertEqual(sms.notify_pending_sale(self._sale().pk), [])
            s.enabled = True
            s.save()
            Sale.objects.filter(invoice_number='INV1').update(status='approved')
            self.assertEqual(sms.notify_pending_sale(Sale.objects.get().pk), [])
        req.assert_not_called()

    def test_gateway_failure_is_logged_not_raised(self):
        with mock.patch('apps.core.sms.requests.request',
                        return_value=FakeResp({'success': False, 'message': 'Invalid signature'}, 401)):
            logs = sms.notify_pending_sale(self._sale().pk)
        self.assertEqual(logs[0].status, 'failed')
        self.assertIn('Invalid signature', logs[0].error)


class SmsPageTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user('boss', password='pw', role='admin')
        self.rep = User.objects.create_user('rep', password='pw', role='sales_rep')
        self.url = reverse('sms_notifications')

    def test_admin_only(self):
        self.client.force_login(self.rep)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_save_settings_keeps_secret_when_blank(self):
        self.client.force_login(self.admin)
        self.client.post(self.url, {'action': 'save_settings', 'enabled': 'on', 'api_key': 'k',
                                    'api_secret': 's', 'sender_name': 'ehub'})
        self.client.post(self.url, {'action': 'save_settings', 'enabled': 'on', 'api_key': '',
                                    'api_secret': '', 'sender_name': 'ehub'})
        s = SmsSettings.load()
        self.assertTrue(s.enabled)
        self.assertEqual((s.api_key, s.api_secret), ('k', 's'))

    def test_save_recipient_phone(self):
        self.client.force_login(self.admin)
        self.client.post(self.url, {'action': 'save_recipients', f'phone_{self.admin.pk}': '0747093762',
                                    f'active_{self.admin.pk}': 'on'})
        self.assertEqual(SmsRecipient.objects.get(user=self.admin).phone, '255747093762')

    def test_sidebar_link_for_admin(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('settings_list')), self.url)

    def test_test_sms(self):
        s = SmsSettings.load()
        s.api_key, s.api_secret = 'k', 's'
        s.save()
        self.client.force_login(self.admin)
        with mock.patch('apps.core.sms.requests.request', side_effect=fake_gateway([])):
            self.client.post(self.url, {'action': 'test', 'test_number': '0747093762'})
        self.assertEqual(SmsLog.objects.get().status, 'sent')
