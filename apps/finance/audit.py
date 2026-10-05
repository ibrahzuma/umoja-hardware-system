"""The accounting audit trail.

Every create, edit, post, cancel, reverse and allocation in the books writes
one `AccountingAuditLog` row with the acting user and the address it came
from. This is deliberately separate from `core.SystemActivity` — that is the
shop floor's live feed, this is the trail an auditor reads.
"""

from .models import AccountingAuditLog


def get_client_ip(request):
    if request is None:
        return None
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def log_action(action, *, user=None, request=None, voucher=None, obj=None,
               previous_status='', new_status='', description=''):
    """Record one business action. `voucher` and `obj` are interchangeable —
    a voucher is also linked by FK so a document's whole life reads back from
    `voucher.audit_logs`."""
    if user is None and request is not None:
        user = getattr(request, 'user', None)
    if user is not None and not getattr(user, 'is_authenticated', False):
        user = None
    target = voucher if voucher is not None else obj
    return AccountingAuditLog.objects.create(
        user=user,
        username=(user.get_username() if user else ''),
        action=action,
        voucher=voucher,
        voucher_number=(getattr(voucher, 'number', '') or ''),
        model_name=(target.__class__.__name__ if target is not None else ''),
        object_id=str(getattr(target, 'pk', '') or ''),
        object_repr=(str(target)[:255] if target is not None else ''),
        previous_status=previous_status or '',
        new_status=new_status or '',
        description=description or '',
        ip_address=get_client_ip(request),
    )
