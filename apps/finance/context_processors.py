"""What every accounting screen's shell needs.

The Pradeep system's `base.html` reads `company`, `current_financial_year` and
`user_role` from a context processor. This supplies the same three, drawn from
where this system actually keeps them: the company name from
`core.SystemSettings` (the shop's own details), the year and currency from the
books' `AccountingSettings`.

It is registered for all templates but does almost nothing on a page that does
not use it: two cached-cheap queries, and only on a request from a signed-in
user.
"""

from django.utils import timezone

from .models import Currency, FinancialYear


def accounting(request):
    """Shell context for `finance/accounting_base.html`."""
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return {}

    # Only pay for this on the accounting screens — everything else extends
    # core/base.html and never reads these.
    if not request.path.startswith('/finance/'):
        return {}

    base = Currency.base()
    company = _company()
    return {
        'company': company,
        'company_name': company.name,
        'current_financial_year': FinancialYear.current(),
        'accounting_currency_code': base.code if base else 'TZS',
        'user_role': _role_label(user),
        # The carried-over report headers print "Generated {{ today }}".
        'today': timezone.localdate(),
    }


class _Company:
    """The letterhead the ported screens print.

    They were written against a model with `name`, `address`, `tax_number`,
    `phone` and `email`; this system keeps the same details on
    `core.SystemSettings` under slightly different names. Rather than edit
    every template, the shape they expect is assembled here.
    """

    __slots__ = ('name', 'address', 'tax_number', 'phone', 'email')

    def __init__(self, name, address='', tax_number='', phone='', email=''):
        self.name = name
        self.address = address
        self.tax_number = tax_number
        self.phone = phone
        self.email = email

    def __str__(self):
        return self.name


def _company():
    try:
        from apps.core.models import SystemSettings
        row = SystemSettings.objects.first()
    except Exception:                                    # pragma: no cover
        # The shell must render even mid-migration or with no settings row.
        row = None
    if row is None:
        return _Company('Umoja Hardware')
    # TIN and VRN are two separate fields here; the letterhead wants one line.
    tax = ' / '.join(part for part in [(row.tin or '').strip(),
                                       (row.vrn or '').strip()] if part)
    return _Company(
        name=row.company_name or 'Umoja Hardware',
        address=row.address or '',
        tax_number=tax,
        phone=row.phone or '',
        email=row.email or '',
    )


def _role_label(user):
    """What to show on the user button — the same idea as the Pradeep
    system's `get_user_role`, but over this system's own roles."""
    if user.is_superuser:
        return 'Administrator'
    role = getattr(user, 'role', '') or ''
    if role:
        return role.replace('_', ' ').title()
    group = user.groups.values_list('name', flat=True).first()
    return group or 'No role'
