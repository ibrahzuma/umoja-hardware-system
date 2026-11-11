"""SMS Notifications page (/sms-notifications/) — Admin only.

One screen, plain form posts told apart by an ``action`` field:
  save_settings   gateway key/secret, sender ID, the master switch and events
  save_recipients the phone each approver is texted on
  test            send one SMS to a number now
  balance         ask eHub for the wallet balance
"""
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.shortcuts import redirect
from django.views.generic import TemplateView

from apps.users.permissions import is_privileged

from . import sms
from .models import SmsLog, SmsRecipient, SmsSettings


class SmsNotificationsView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = 'core/sms_notifications.html'

    def test_func(self):
        return is_privileged(self.request.user)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        s = SmsSettings.load()
        phones = {r.user_id: r for r in SmsRecipient.objects.all()}
        ctx.update({
            'sms': s,
            'key_from_env': bool(s.key and not s.api_key),
            'approvers': [(u, phones.get(u.pk)) for u in sms.sale_approvers()],
            'logs': SmsLog.objects.all()[:50],
            'balance': self.request.session.pop('sms_balance', None),
        })
        return ctx

    def post(self, request, *args, **kwargs):
        action = request.POST.get('action')
        handler = getattr(self, f'_{action}', None) if action in (
            'save_settings', 'save_recipients', 'test', 'balance') else None
        if handler:
            handler(request, SmsSettings.load())
        return redirect('sms_notifications')

    def _save_settings(self, request, s):
        p = request.POST
        s.enabled = p.get('enabled') == 'on'
        s.notify_pending_sale = p.get('notify_pending_sale') == 'on'
        name = (p.get('sender_name') or '').strip()[:11]
        if name and name != s.sender_name:
            s.sender_name, s.sender_uuid = name, ''
        # Blank means "keep what is saved" — the secret is never echoed back.
        if p.get('api_key', '').strip():
            s.api_key, s.sender_uuid = p['api_key'].strip(), ''
        if p.get('api_secret', '').strip():
            s.api_secret = p['api_secret'].strip()
        s.save()
        if s.enabled and not s.has_credentials:
            messages.warning(request, 'SMS is switched on but no API key/secret is set, so nothing will be sent.')
        else:
            messages.success(request, 'SMS settings saved.')

    def _save_recipients(self, request, s):
        bad = []
        for user in sms.sale_approvers():
            raw = request.POST.get(f'phone_{user.pk}', '').strip()
            active = request.POST.get(f'active_{user.pk}') == 'on'
            if raw and not sms.normalize_phone(raw):
                bad.append(f'{user.get_full_name() or user.username} ({raw})')
                continue
            if raw or SmsRecipient.objects.filter(user=user).exists():
                SmsRecipient.objects.update_or_create(
                    user=user, defaults={'phone': sms.normalize_phone(raw) if raw else '', 'active': active})
        if bad:
            messages.error(request, 'Not valid mobile numbers, not saved: ' + ', '.join(bad))
        else:
            messages.success(request, 'Recipients saved.')

    def _test(self, request, s):
        to = request.POST.get('test_number', '')
        log = sms.send_sms(to, request.POST.get('test_message') or
                           'Test from Umoja Hardware: SMS notifications are working.', event='test', settings_obj=s)
        if log.status == 'sent':
            messages.success(request, f'Test SMS sent to {log.to}.')
        else:
            messages.error(request, f'Test SMS failed: {log.error}')

    def _balance(self, request, s):
        try:
            request.session['sms_balance'] = sms.EhubClient(s.key, s.secret).balance()
        except sms.SmsError as exc:
            messages.error(request, str(exc))
