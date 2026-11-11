"""eHub SMS gateway (https://sms.ehub.co.tz/api-docs) and the alerts sent through it.

Every request is signed: HMAC-SHA256 over
``"{timestamp}\\n{METHOD}\\n{/api/v1/path}\\n{raw body}"`` with the API secret,
sent as ``X-Signature`` beside ``X-Timestamp`` and ``Authorization: Bearer <key>``.

Alerts never hold up the shop floor: they are sent after the database commit,
on a background thread, and a gateway failure is logged (``SmsLog``), never
raised into the request that triggered it.
"""
import hashlib
import hmac
import json
import logging
import re
import threading
import time

import requests
from django.db import transaction

logger = logging.getLogger(__name__)

BASE_URL = 'https://sms.ehub.co.tz'
API_PREFIX = '/api/v1'
TIMEOUT = 15


class SmsError(Exception):
    pass


def normalize_phone(raw):
    """'0747 093 762', '+255747093762', '747093762' -> '255747093762'.
    Returns '' for anything that is not a Tanzanian mobile number."""
    digits = re.sub(r'\D', '', raw or '')
    if digits.startswith('0') and len(digits) == 10:
        digits = '255' + digits[1:]
    elif len(digits) == 9:
        digits = '255' + digits
    return digits if re.fullmatch(r'255[67]\d{8}', digits) else ''


class EhubClient:
    def __init__(self, key, secret, base_url=BASE_URL):
        if not (key and secret):
            raise SmsError('No eHub API key/secret set. Add them on the SMS Notifications page.')
        self.key, self.secret, self.base_url = key, secret, base_url.rstrip('/')

    def _headers(self, method, path, body):
        ts = str(int(time.time()))
        payload = f"{ts}\n{method}\n{path}\n{body}"
        sig = hmac.new(self.secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        return {
            'Authorization': f'Bearer {self.key}',
            'X-Timestamp': ts,
            'X-Signature': sig,
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }

    def request(self, method, endpoint, data=None):
        path = API_PREFIX + endpoint
        body = json.dumps(data, separators=(',', ':')) if data is not None else ''
        try:
            resp = requests.request(method, self.base_url + path, data=body.encode() if body else None,
                                    headers=self._headers(method, path, body), timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise SmsError(f'Could not reach eHub: {exc}') from exc
        try:
            payload = resp.json()
        except ValueError:
            payload = {}
        if resp.status_code >= 400 or payload.get('success') is False:
            msg = payload.get('message') or payload.get('error') or resp.text[:200]
            errors = payload.get('errors')
            if errors:
                msg = f"{msg} {errors}"
            raise SmsError(f'eHub {resp.status_code}: {msg}')
        return payload.get('data', payload)

    def balance(self):
        return self.request('GET', '/wallet/balance')

    def sender_ids(self):
        """Every sender ID this account may use, as a flat list of dicts."""
        data = self.request('GET', '/sender-ids')
        if isinstance(data, list):
            return data
        rows = []
        for group in ('own', 'shared', 'public', 'results', 'items'):
            if isinstance(data.get(group), list):
                rows.extend(data[group])
        return rows

    def send(self, to, message, sender_uuid):
        return self.request('POST', '/sms/send', {'to': to, 'message': message, 'sender_id': sender_uuid})


def _sender_label(row):
    for k in ('sender_id', 'name', 'sender_name', 'value', 'title'):
        if isinstance(row.get(k), str):
            return row[k]
    return ''


def resolve_sender(settings_obj, client=None):
    """The UUID for settings_obj.sender_name, looked up once and cached."""
    if settings_obj.sender_uuid:
        return settings_obj.sender_uuid
    client = client or EhubClient(settings_obj.key, settings_obj.secret)
    wanted = settings_obj.sender_name.strip().lower()
    rows = client.sender_ids()
    for row in rows:
        if _sender_label(row).strip().lower() == wanted:
            status = str(row.get('status', 'approved')).lower()
            if status not in ('approved', 'active'):
                raise SmsError(f'Sender ID "{settings_obj.sender_name}" is {status}, not approved yet.')
            settings_obj.sender_uuid = str(row['id'])
            settings_obj.save(update_fields=['sender_uuid'])
            return settings_obj.sender_uuid
    available = ', '.join(filter(None, (_sender_label(r) for r in rows))) or 'none'
    raise SmsError(f'Sender ID "{settings_obj.sender_name}" not found on this eHub account (available: {available}).')


def send_sms(to, message, event='', settings_obj=None):
    """Send one SMS now and log it. Returns the SmsLog row; never raises."""
    from .models import SmsLog, SmsSettings
    s = settings_obj or SmsSettings.load()
    phone = normalize_phone(to)
    if not phone:
        return SmsLog.objects.create(to=str(to)[:20], message=message, event=event, status='failed',
                                     error='Not a valid Tanzanian mobile number.')
    try:
        client = EhubClient(s.key, s.secret)
        data = client.send(phone, message[:640], resolve_sender(s, client))
        return SmsLog.objects.create(to=phone, message=message, event=event, status='sent',
                                     message_id=str((data or {}).get('message_id', '')))
    except Exception as exc:  # the gateway must never break the caller
        logger.warning('SMS to %s failed: %s', phone, exc)
        return SmsLog.objects.create(to=phone, message=message, event=event, status='failed', error=str(exc))


def sale_approvers():
    """Active users who can approve a sale: admins/superusers, managers and the
    Sales & Credit Manager — the same people CanApproveSales lets through."""
    from django.contrib.auth import get_user_model
    from django.db.models import Q
    User = get_user_model()
    return (User.objects.filter(is_active=True)
            .filter(Q(is_superuser=True) | Q(role__in=('admin', 'manager', 'sales_credit_manager'))
                    | Q(groups__name__in=('Admin', 'admin', 'Manager', 'manager', 'Store Manager',
                                          'Sales Manager', 'sales_manager', 'Sales & Credit Manager')))
            .distinct().order_by('first_name', 'username'))


def pending_sale_message(sale):
    from .models import SystemSettings
    company = getattr(SystemSettings.objects.first(), 'company_name', '') or 'Umoja Hardware'
    seller = ''
    if sale.user:
        seller = sale.user.get_full_name() or sale.user.username
    customer = sale.customer.name if sale.customer_id else (sale.customer_name or '')
    total = f"{int(sale.total_amount or 0):,}"
    parts = [f"{company}: Sale {sale.invoice_number} of TZS {total}"]
    if customer:
        parts.append(f"for {customer}")
    if seller:
        parts.append(f"by {seller}")
    return ' '.join(parts) + " is pending your approval."


def notify_pending_sale(sale_id):
    """Text every approver with an active phone that a sale awaits approval."""
    from apps.sales.models import Sale
    from .models import SmsRecipient, SmsSettings
    s = SmsSettings.load()
    if not (s.enabled and s.notify_pending_sale and s.has_credentials):
        return []
    sale = Sale.objects.select_related('user', 'customer').filter(pk=sale_id).first()
    if not sale or sale.status != 'pending':
        return []
    message = pending_sale_message(sale)
    phones = set()
    for r in SmsRecipient.objects.filter(active=True, user__in=sale_approvers()).exclude(phone=''):
        p = normalize_phone(r.phone)
        if p:
            phones.add(p)
    return [send_sms(p, message, event='sale_pending', settings_obj=s) for p in sorted(phones)]


def _run_in_background(fn, *args):
    def target():
        from django.db import close_old_connections
        try:
            fn(*args)
        except Exception:
            logger.exception('SMS alert failed')
        finally:
            close_old_connections()
    threading.Thread(target=target, daemon=True).start()


# Tests set this to False so alerts run inline and can be asserted on.
ASYNC = True


def queue_pending_sale_alert(sale_id):
    """Called from SaleViewSet.perform_create: send after commit, off the request."""
    def go():
        if ASYNC:
            _run_in_background(notify_pending_sale, sale_id)
        else:
            notify_pending_sale(sale_id)
    transaction.on_commit(go)
