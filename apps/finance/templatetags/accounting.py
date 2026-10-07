"""Template filters for the accounting screens.

This is the Pradeep accounting system's `core/templatetags/accounting_extras.py`,
carried over with the same filter names and the same behaviour, so its
templates work here unchanged: `money`, `money_or_blank`, `status_badge`,
`query_replace`, `get_item`, `abs_value` and `has_perm`.

Money is rendered to two decimals here, not in whole shillings as the shop
floor does — a ledger that rounds is a ledger that does not balance.

`dr_cr` and `abs_money` are the two additions, used by the screens that show a
signed balance on whichever side it falls.
"""

from django import template
from django.utils.safestring import mark_safe

from ..money import ZERO, format_money, quantize

register = template.Library()


@register.filter
def money(value):
    """1234567.8 -> 1,234,567.80"""
    return format_money(value)


@register.filter
def money_or_blank(value):
    """A zero in an accounting column is noise, so it is left empty."""
    amount = quantize(value)
    return format_money(amount) if amount != ZERO else ''


@register.filter
def abs_value(value):
    return abs(quantize(value))


@register.filter
def abs_money(value):
    return format_money(abs(quantize(value)))


@register.filter
def dr_cr(value):
    """Which side a signed (debit-positive) balance sits on."""
    return 'Dr' if quantize(value) >= ZERO else 'Cr'


@register.filter
def status_badge(status):
    colours = {
        'DRAFT': 'secondary', 'POSTED': 'success', 'CANCELLED': 'danger', 'REVERSED': 'warning',
        'OPEN': 'warning', 'PARTLY_PAID': 'info', 'PAID': 'success',
        # The same values as this system stores them, in lower case.
        'draft': 'secondary', 'posted': 'success', 'cancelled': 'danger', 'reversed': 'warning',
        'open': 'warning', 'partly_paid': 'info', 'paid': 'success',
        'pending': 'warning', 'queried': 'danger',
        # Audit-trail actions.
        'CREATED': 'secondary', 'EDITED': 'info', 'ALLOCATED': 'info',
        'UNALLOCATED': 'warning', 'DELETED': 'danger', 'SETTINGS': 'secondary',
    }
    colour = colours.get(str(status), colours.get(str(status).upper(), 'secondary'))
    label = str(status).replace('_', ' ').title()
    return mark_safe(f'<span class="badge text-bg-{colour}">{label}</span>')


@register.simple_tag(takes_context=True)
def query_replace(context, **kwargs):
    """A querystring with the current filters kept and these keys replaced —
    what paging, printing and the export buttons are built on."""
    params = context['request'].GET.copy()
    for key, value in kwargs.items():
        if value is None or value == '':
            params.pop(key, None)
        else:
            params[key] = value
    return params.urlencode()


@register.filter
def get_item(mapping, key):
    try:
        return mapping.get(key)
    except AttributeError:
        return None


@register.filter
def has_perm(user, perm):
    return user.has_perm(perm)
