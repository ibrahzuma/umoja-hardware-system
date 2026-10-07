"""Forms for the accounting screens.

Everything here wears Bootstrap, because every page in the system extends
`core/base.html` and Bootstrap 5 is what it loads. `BootstrapFormMixin` is
the one place that is arranged, so a new form does not have to repeat a
`widget=forms.Select(attrs={'class': 'form-select'})` on every field.
"""

from decimal import Decimal

from django import forms
from django.forms import BaseFormSet, formset_factory, modelformset_factory
from django.utils import timezone

from apps.inventory.models import Supplier
from apps.sales.models import Customer

from .models import (
    AccountingSettings, Currency, ExchangeRate, FinancialYear, Invoice, LedgerAccount,
    Voucher, VoucherType,
)
from .money import ZERO, quantize
from .restrictions import account_allowed, postable_accounts, restriction_error


class BootstrapFormMixin:
    """Bootstrap classes on every widget, and native date pickers on date
    fields."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            css = widget.attrs.get('class', '')
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs['class'] = f"{css} form-check-input".strip()
            elif isinstance(widget, (forms.Select, forms.SelectMultiple)):
                widget.attrs['class'] = f"{css} form-select".strip()
            else:
                widget.attrs['class'] = f"{css} form-control".strip()
            if isinstance(field, forms.DateField):
                widget.input_type = 'date'
                widget.format = '%Y-%m-%d'


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class AccountingSettingsForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = AccountingSettings
        fields = [
            'financial_year_start', 'financial_year_end',
            'voucher_number_padding', 'include_financial_year_in_number',
            'reset_sequence_each_year', 'number_separator',
            'efd_enabled', 'efd_serial_number', 'efd_duplicate_policy', 'default_vat_rate',
            'period_lock_date', 'allow_backdated_entries',
            'default_cash_account', 'default_bank_account', 'default_sales_account',
            'default_purchase_account', 'default_output_vat_account', 'default_input_vat_account',
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        postable = postable_accounts()
        self.fields['default_cash_account'].queryset = postable.filter(kind='cash')
        self.fields['default_bank_account'].queryset = postable.filter(kind='bank')
        self.fields['default_sales_account'].queryset = postable.filter(
            account_type=LedgerAccount.INCOME)
        self.fields['default_purchase_account'].queryset = postable.filter(
            account_type__in=[LedgerAccount.EXPENSE, LedgerAccount.ASSET]).exclude(
            kind__in=LedgerAccount.MONEY_KINDS)
        # The VAT defaults go by the flag; `is_vat_ledger` also accepts a
        # ledger merely *named* VAT, but a default worth storing should be
        # one somebody has deliberately marked.
        self.fields['default_output_vat_account'].queryset = postable.filter(
            vat_kind=LedgerAccount.VAT_OUTPUT)
        self.fields['default_input_vat_account'].queryset = postable.filter(
            vat_kind=LedgerAccount.VAT_INPUT)
        for name in self.fields:
            if name.startswith('default_'):
                self.fields[name].required = False
                self.fields[name].empty_label = '— none —'


class VoucherTypeForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = VoucherType
        fields = ['name', 'prefix', 'is_active']

    def clean_prefix(self):
        prefix = (self.cleaned_data['prefix'] or '').strip().upper()
        if not prefix.isalnum():
            raise forms.ValidationError("A prefix can only hold letters and digits.")
        return prefix


VoucherTypeFormSet = modelformset_factory(VoucherType, form=VoucherTypeForm, extra=0,
                                          can_delete=False)


class FinancialYearForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = FinancialYear
        fields = ['code', 'name', 'start_date', 'end_date', 'is_active', 'is_closed',
                  'lock_date', 'notes']
        widgets = {'notes': forms.Textarea(attrs={'rows': 2})}


class CurrencyForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Currency
        fields = ['code', 'name', 'symbol', 'is_base', 'is_active']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        base = Currency.base()
        if self.instance.pk and self.instance.is_base:
            self.fields['is_base'].disabled = True
            self.fields['is_base'].help_text = "This is the base currency of the books."
        elif base is not None:
            self.fields['is_base'].disabled = True
            self.fields['is_base'].help_text = (
                f"{base.code} is the base currency. Changing it once vouchers exist is not "
                f"supported — every ledger entry is kept in it.")

    def clean_code(self):
        return (self.cleaned_data['code'] or '').strip().upper()


class ExchangeRateForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = ExchangeRate
        fields = ['currency', 'rate_date', 'rate', 'note']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['currency'].queryset = Currency.objects.filter(
            is_base=False, is_active=True).order_by('code')
        self.fields['rate_date'].initial = timezone.localdate()
        base = Currency.base()
        if base is not None:
            self.fields['rate'].help_text = (
                f"Units of {base.code} for 1 unit of the currency chosen.")

    def clean(self):
        cleaned = super().clean()
        currency, rate_date = cleaned.get('currency'), cleaned.get('rate_date')
        if currency and rate_date:
            clash = (ExchangeRate.objects.filter(currency=currency, rate_date=rate_date)
                     .exclude(pk=self.instance.pk))
            if clash.exists():
                self.add_error('rate_date', f"A rate for {currency.code} on this date already "
                                            f"exists — edit that one instead.")
        return cleaned


# ---------------------------------------------------------------------------
# Chart of accounts
# ---------------------------------------------------------------------------

class LedgerAccountForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = LedgerAccount
        fields = ['code', 'name', 'kind', 'account_type', 'category', 'parent', 'is_group',
                  'is_customer_control', 'is_supplier_control', 'vat_kind', 'currency',
                  'is_active', 'opening_balance', 'opening_side', 'notes']
        widgets = {'notes': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from django.db.models import Q
        parents = LedgerAccount.objects.filter(
            Q(is_group=True) | Q(is_customer_control=True) | Q(is_supplier_control=True))
        if self.instance.pk:
            parents = parents.exclude(pk__in=self.instance.descendant_ids())
        self.fields['parent'].queryset = parents.order_by('code')
        self.fields['parent'].required = False
        self.fields['parent'].empty_label = '— top level —'
        self.fields['code'].required = False
        self.fields['code'].help_text = "Left blank, a code is generated from the kind."
        self.fields['account_type'].required = False
        self.fields['account_type'].help_text = "Left blank, it follows the kind."
        self.fields['category'].required = False
        self.fields['vat_kind'].required = False
        self.fields['opening_side'].required = False
        self.fields['currency'].queryset = Currency.objects.filter(is_active=True).order_by(
            '-is_base', 'code')
        self.fields['currency'].required = False
        base = Currency.base()
        self.fields['currency'].empty_label = (
            f"Base currency ({base.code})" if base else 'Base currency')

    def clean(self):
        cleaned = super().clean()
        if self.instance.pk and cleaned.get('is_group') and self.instance.gl_entries.exists():
            self.add_error('is_group', "This ledger has entries, so it cannot become a group "
                                       "account — a group is never posted to.")
        return cleaned


class LedgerAccountFilterForm(forms.Form):
    q = forms.CharField(required=False, widget=forms.TextInput(attrs={
        'class': 'form-control', 'placeholder': 'Search code or name'}))
    account_type = forms.ChoiceField(
        required=False, choices=[('', 'All types')] + list(LedgerAccount.TYPES),
        widget=forms.Select(attrs={'class': 'form-select'}))
    kind = forms.ChoiceField(required=False, choices=[
        ('', 'All ledgers'), ('bank', 'Bank accounts'), ('cash', 'Cash books'),
        ('customer', 'Customer ledgers'), ('supplier', 'Supplier ledgers'),
        ('vat', 'VAT ledgers'), ('group', 'Group accounts'), ('control', 'Control accounts'),
    ], widget=forms.Select(attrs={'class': 'form-select'}))
    show_inactive = forms.BooleanField(required=False, widget=forms.CheckboxInput(
        attrs={'class': 'form-check-input'}))


class AccountImportForm(forms.Form):
    file = forms.FileField(
        label="Excel / CSV file",
        help_text="Use the template below, or a CSV with the same headings.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['file'].widget.attrs.update({'class': 'form-control', 'accept': '.xlsx,.csv'})


# ---------------------------------------------------------------------------
# Voucher entry
# ---------------------------------------------------------------------------

CURRENCY_FIELDS = ['currency', 'exchange_rate']

# What each voucher type asks for on its header. Sales and Purchase take the
# two invoice references and nothing else — the party, the payment status,
# the amounts and the VAT ledger are read off the lines (see
# `drafts.VoucherDraftService`), so there is no second place to get them wrong.
HEADER_FIELDS = {
    'sales': ['date', 'invoice_number', 'efd_number', 'description'] + CURRENCY_FIELDS,
    'purchase': ['date', 'invoice_number', 'efd_number', 'description'] + CURRENCY_FIELDS,
    'receipt': ['date', 'description'] + CURRENCY_FIELDS,
    'payment': ['date', 'description'] + CURRENCY_FIELDS,
    'contra': ['date', 'reference', 'description'] + CURRENCY_FIELDS,
    'journal': ['date', 'reference', 'description'] + CURRENCY_FIELDS,
}


class VoucherHeaderForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Voucher
        fields = ['date', 'invoice_number', 'efd_number', 'reference', 'description',
                  'currency', 'exchange_rate']
        widgets = {'description': forms.Textarea(attrs={'rows': 2})}
        labels = {'description': 'Description / narration'}

    def __init__(self, *args, voucher_type, **kwargs):
        super().__init__(*args, **kwargs)
        self.voucher_type = voucher_type
        keep = HEADER_FIELDS[voucher_type]
        for name in list(self.fields):
            if name not in keep:
                del self.fields[name]
        self.fields['date'].widget.attrs.update({'autofocus': True})
        self._setup_currency()
        if 'invoice_number' in self.fields:
            self.fields['invoice_number'].required = True
            self.fields['invoice_number'].label = (
                'Sales invoice number' if voucher_type == 'sales'
                else "Supplier's invoice number")
            self.fields['efd_number'].label = 'EFD receipt (RCT) number'
            self.fields['efd_number'].required = False

    def _setup_currency(self):
        """Currency and rate show only once a second currency exists, so a
        single-currency set of books looks exactly as it did before."""
        currencies = Currency.objects.filter(is_active=True).order_by('-is_base', 'code')
        base = Currency.base()
        self.fields['currency'].queryset = currencies
        self.fields['currency'].required = False
        self.fields['currency'].empty_label = None
        self.fields['exchange_rate'].required = False
        self.fields['exchange_rate'].label = 'Exchange rate'
        if base is not None:
            self.fields['exchange_rate'].help_text = (
                f"{base.code} for 1 unit of the voucher currency")
        self.multi_currency = currencies.count() > 1
        if not self.multi_currency:
            self.fields['currency'].widget = forms.HiddenInput()
            self.fields['exchange_rate'].widget = forms.HiddenInput()
            self.fields['currency'].initial = base.pk if base else None
            self.fields['exchange_rate'].initial = Decimal('1')
            return
        if not self.instance.pk and base is not None:
            self.fields['currency'].initial = base.pk
            self.fields['exchange_rate'].initial = Decimal('1')
        self.fields['currency'].widget.attrs.update({'id': 'id_currency'})
        self.fields['exchange_rate'].widget.attrs.update(
            {'class': 'form-control text-end', 'step': '0.000001', 'min': '0',
             'id': 'id_exchange_rate'})

    def clean_date(self):
        value = self.cleaned_data['date']
        if FinancialYear.for_date(value) is None:
            raise forms.ValidationError(
                f"No financial year covers {value:%d/%m/%Y}. Ask an administrator to create it.")
        return value

    def clean(self):
        cleaned = super().clean()
        currency = cleaned.get('currency') or Currency.base()
        rate = cleaned.get('exchange_rate')
        if currency is not None and currency.is_base:
            cleaned['exchange_rate'] = Decimal('1')
        elif currency is not None and (not rate or rate <= 0):
            rate = currency.rate_on(cleaned.get('date') or timezone.localdate())
            if rate is None:
                self.add_error('exchange_rate',
                               f"No exchange rate for {currency.code} on this date — enter one "
                               f"here, or add it under Currencies.")
            cleaned['exchange_rate'] = rate
        return cleaned


class VoucherLineForm(forms.Form):
    """One accounting line. A row with nothing on it at all is ignored, which
    is what lets the form keep spare rows open."""

    account = forms.ModelChoiceField(queryset=LedgerAccount.objects.none(), required=False)
    debit = forms.DecimalField(required=False, min_value=0, max_digits=18, decimal_places=2)
    credit = forms.DecimalField(required=False, min_value=0, max_digits=18, decimal_places=2)
    description = forms.CharField(required=False, max_length=200)

    def __init__(self, *args, voucher_type=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.voucher_type = voucher_type
        self.fields['account'].queryset = postable_accounts().order_by('code')

    @property
    def is_empty(self):
        data = getattr(self, 'cleaned_data', {}) or {}
        return (not data.get('account') and not quantize(data.get('debit'))
                and not quantize(data.get('credit')))

    def clean(self):
        cleaned = super().clean()
        debit, credit = quantize(cleaned.get('debit')), quantize(cleaned.get('credit'))
        cleaned['debit'], cleaned['credit'] = debit, credit
        account = cleaned.get('account')
        if not account and debit == 0 and credit == 0:
            return cleaned                      # a blank row, left alone
        if debit > 0 and credit > 0:
            raise forms.ValidationError("A line cannot carry both a debit and a credit amount.")
        if debit == 0 and credit == 0:
            raise forms.ValidationError("Enter a debit or a credit amount.")
        if not account:
            side = 'debit' if debit > 0 else 'credit'
            raise forms.ValidationError(
                f"Pick a ledger for the {side} line of {max(debit, credit):,.2f}, "
                f"or clear the line.")
        side = 'debit' if debit > 0 else 'credit'
        if self.voucher_type and not account_allowed(self.voucher_type, side, account):
            raise forms.ValidationError(restriction_error(self.voucher_type, side, account))
        return cleaned


class BaseVoucherLineFormSet(BaseFormSet):
    def __init__(self, *args, voucher_type=None, **kwargs):
        self.voucher_type = voucher_type
        super().__init__(*args, **kwargs)

    def get_form_kwargs(self, index):
        kwargs = super().get_form_kwargs(index)
        kwargs['voucher_type'] = self.voucher_type
        return kwargs

    def lines(self):
        """The filled-in lines, in the order they were submitted, in the shape
        the draft service wants."""
        out = []
        for form in self.forms:
            if not hasattr(form, 'cleaned_data') or form.is_empty:
                continue
            data = form.cleaned_data
            side = 'debit' if data['debit'] > 0 else 'credit'
            out.append({'account': data['account'], 'side': side,
                        'amount': data['debit'] or data['credit'],
                        'debit': data['debit'], 'credit': data['credit'],
                        'narration': data.get('description', '')})
        return out

    def clean(self):
        if any(self.errors):
            return
        if not self.lines():
            raise forms.ValidationError("Enter at least one accounting line.")


VoucherLineFormSet = formset_factory(VoucherLineForm, formset=BaseVoucherLineFormSet,
                                     extra=0, can_delete=False, max_num=500)


class AllocationForm(forms.Form):
    """One allocation of this voucher's money against one open item.

    The entry screen posts the open item as a single `invoice` field, the way
    the Pradeep system's did. Here an open item may be a till sale or a
    purchase order as well as an invoice, so that field carries a
    `"<target>:<pk>"` token — `voucher:12`, `sale:5` — and this is where it is
    taken apart again into the FK `VoucherAllocation` actually sets.
    """

    invoice = forms.CharField(required=False, max_length=40)
    reference = forms.CharField(required=False, max_length=60)
    amount = forms.DecimalField(required=False, min_value=0, max_digits=18, decimal_places=2)

    TARGETS = ('invoice', 'sale', 'purchase_order', 'voucher')

    def clean(self):
        cleaned = super().clean()
        cleaned['amount'] = quantize(cleaned.get('amount'))
        if cleaned['amount'] <= 0:
            return cleaned

        target, target_id = self._split(cleaned.get('invoice'))
        if target is None:
            raise forms.ValidationError("An allocation has to say which invoice it clears.")
        cleaned['target'], cleaned['target_id'] = target, target_id

        # A register invoice can be checked here and now; the other three are
        # checked against what is outstanding when the voucher is posted.
        if target == 'invoice':
            invoice = Invoice.objects.filter(pk=target_id).first()
            if invoice is None:
                raise forms.ValidationError("That invoice no longer exists.")
            if cleaned['amount'] > invoice.outstanding_amount:
                raise forms.ValidationError(
                    f"Allocating to {invoice.invoice_number} is more than the "
                    f"{invoice.outstanding_amount:,.2f} outstanding on it.")
        return cleaned

    @staticmethod
    def _split(token):
        """`"voucher:12"` -> `('voucher', 12)`. A bare number is an invoice,
        so a caller that predates the token still works."""
        raw = (token or '').strip()
        if not raw:
            return None, None
        target, _, pk = raw.partition(':')
        if not pk:
            target, pk = 'invoice', target
        if target not in AllocationForm.TARGETS or not pk.isdigit():
            return None, None
        return target, int(pk)


class BaseAllocationFormSet(BaseFormSet):
    def allocations(self):
        out = []
        for form in self.forms:
            data = getattr(form, 'cleaned_data', None) or {}
            if data.get('amount', ZERO) <= 0 or not data.get('target'):
                continue
            out.append({
                data['target']: data['target_id'],
                'amount': data['amount'],
                'reference': data.get('reference') or '',
            })
        return out


AllocationFormSet = formset_factory(AllocationForm, formset=BaseAllocationFormSet,
                                    extra=0, max_num=500)


# ---------------------------------------------------------------------------
# Filters and small action forms
# ---------------------------------------------------------------------------

class VoucherFilterForm(forms.Form):
    q = forms.CharField(required=False, label='Search', widget=forms.TextInput(attrs={
        'class': 'form-control',
        'placeholder': 'Voucher no, invoice no, EFD no, description'}))
    voucher_type = forms.ChoiceField(
        required=False, choices=[('', 'All types')] + list(Voucher.TYPES),
        widget=forms.Select(attrs={'class': 'form-select'}))
    status = forms.ChoiceField(
        required=False, choices=[('', 'All statuses')] + list(Voucher.STATUS_CHOICES),
        widget=forms.Select(attrs={'class': 'form-select'}))
    date_from = forms.DateField(required=False, widget=forms.DateInput(
        attrs={'type': 'date', 'class': 'form-control'}))
    date_to = forms.DateField(required=False, widget=forms.DateInput(
        attrs={'type': 'date', 'class': 'form-control'}))
    invoice_number = forms.CharField(required=False, widget=forms.TextInput(
        attrs={'class': 'form-control'}))
    efd_number = forms.CharField(required=False, label='EFD no', widget=forms.TextInput(
        attrs={'class': 'form-control'}))
    customer = forms.ModelChoiceField(required=False, queryset=Customer.objects.all(),
                                      widget=forms.Select(attrs={'class': 'form-select'}))
    supplier = forms.ModelChoiceField(required=False, queryset=Supplier.objects.all(),
                                      widget=forms.Select(attrs={'class': 'form-select'}))
    account = forms.ModelChoiceField(required=False,
                                     queryset=LedgerAccount.objects.filter(is_group=False),
                                     widget=forms.Select(attrs={'class': 'form-select'}))
    financial_year = forms.ModelChoiceField(required=False, queryset=FinancialYear.objects.all(),
                                            widget=forms.Select(attrs={'class': 'form-select'}))


class CancelVoucherForm(forms.Form):
    reason = forms.CharField(required=True, widget=forms.Textarea(
        attrs={'rows': 2, 'class': 'form-control'}))


class ReverseVoucherForm(forms.Form):
    reversal_date = forms.DateField(required=True, widget=forms.DateInput(
        attrs={'type': 'date', 'class': 'form-control'}))
    reason = forms.CharField(required=True, widget=forms.Textarea(
        attrs={'rows': 2, 'class': 'form-control'}))


# ---------------------------------------------------------------------------
# Report filters
# ---------------------------------------------------------------------------

class DateRangeForm(forms.Form):
    date_from = forms.DateField(required=False, widget=forms.DateInput(
        attrs={'type': 'date', 'class': 'form-control'}))
    date_to = forms.DateField(required=False, widget=forms.DateInput(
        attrs={'type': 'date', 'class': 'form-control'}))


class AccountRangeForm(DateRangeForm):
    account = forms.ModelChoiceField(
        required=False,
        queryset=LedgerAccount.objects.filter(is_group=False).order_by('code'),
        widget=forms.Select(attrs={'class': 'form-select'}))


class CustomerRangeForm(DateRangeForm):
    customer = forms.ModelChoiceField(required=True, queryset=Customer.objects.order_by('name'),
                                      widget=forms.Select(attrs={'class': 'form-select'}))


class SupplierRangeForm(DateRangeForm):
    supplier = forms.ModelChoiceField(required=True, queryset=Supplier.objects.order_by('name'),
                                      widget=forms.Select(attrs={'class': 'form-select'}))


class AsOfForm(forms.Form):
    as_of = forms.DateField(required=False, widget=forms.DateInput(
        attrs={'type': 'date', 'class': 'form-control'}))
    financial_year = forms.ModelChoiceField(
        required=False, queryset=FinancialYear.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'}),
        help_text="Limit the movements to one financial year")
    include_zero = forms.BooleanField(required=False, widget=forms.CheckboxInput(
        attrs={'class': 'form-check-input'}))


class GeneralLedgerFilterForm(forms.Form):
    """The filter bar on the General Ledger browser — the same five boxes the
    Pradeep system's `ledger/gl_list.html` has, in the same order."""

    q = forms.CharField(required=False, widget=forms.TextInput(attrs={
        'class': 'form-control form-control-sm',
        'placeholder': 'Voucher no, description, reference'}))
    account = forms.ModelChoiceField(
        required=False, queryset=LedgerAccount.objects.filter(is_group=False).order_by('code'),
        widget=forms.Select(attrs={'class': 'form-select form-select-sm'}))
    voucher_type = forms.ChoiceField(
        required=False, choices=[('', 'All types')] + list(Voucher.TYPES),
        widget=forms.Select(attrs={'class': 'form-select form-select-sm'}))
    date_from = forms.DateField(required=False, widget=forms.DateInput(
        attrs={'type': 'date', 'class': 'form-control form-control-sm'}))
    date_to = forms.DateField(required=False, widget=forms.DateInput(
        attrs={'type': 'date', 'class': 'form-control form-control-sm'}))


class PartyFilterForm(forms.Form):
    """Search and the show-inactive switch on the customer and supplier
    registers. A party is "inactive" here when its sub-ledger is closed —
    these models carry no active flag of their own."""

    q = forms.CharField(required=False, widget=forms.TextInput(attrs={
        'class': 'form-control form-control-sm', 'placeholder': 'Name, phone or email'}))
    show_inactive = forms.BooleanField(required=False, widget=forms.CheckboxInput(
        attrs={'class': 'form-check-input'}))


class StatementFilterForm(forms.Form):
    date_from = forms.DateField(required=False, widget=forms.DateInput(
        attrs={'type': 'date', 'class': 'form-control form-control-sm'}))
    date_to = forms.DateField(required=False, widget=forms.DateInput(
        attrs={'type': 'date', 'class': 'form-control form-control-sm'}))
