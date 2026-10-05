"""Which ledgers each voucher type may use, on each side.

This is the restriction table from the accounting specification, and it is
enforced **on the server**; the dropdowns on the entry screen merely mirror
it, so a hand-made POST is refused exactly as a mis-clicked form would be.

    Receipt   Debit / Credit: all ledgers except supplier / payable accounts
    Payment   Debit / Credit: all ledgers except customer / receivable accounts
    Contra    Debit: bank/cash only          Credit: bank/cash only
    Journal   Debit: all ledgers             Credit: all ledgers
    Sales     Debit / Credit: all ledgers except supplier / payable accounts
    Purchase  Debit / Credit: all ledgers except customer / receivable accounts

Group (header) accounts and the two control accounts are never postable —
post to the customer's or supplier's own sub-ledger instead.

Note this is **looser** than the `Voucher.RULES` table the books started
with, which required (for instance) a Receipt to debit a bank account or
cash book. The specification deliberately allows a Receipt to be any
money-in document, and the party on a Sales or Purchase voucher is read off
the lines rather than dictated by them. `Voucher.RULES` is still what the
form uses to *order and preselect* the dropdowns — the everyday shape of each
voucher — while this table is what is actually allowed.
"""

from django.db.models import Q

from .models import LedgerAccount, Voucher

BANK_CASH = "Bank Accounts and Cash Books only"
NON_BANK_CASH = "all ledgers except Bank Accounts and Cash Books"
ALL = "all ledgers"
NON_SUPPLIER = "all ledgers except Supplier / payable accounts"
NON_CUSTOMER = "all ledgers except Customer / receivable accounts"

RESTRICTIONS = {
    'receipt': {'debit': NON_SUPPLIER, 'credit': NON_SUPPLIER},
    'payment': {'debit': NON_CUSTOMER, 'credit': NON_CUSTOMER},
    'contra': {'debit': BANK_CASH, 'credit': BANK_CASH},
    'journal': {'debit': ALL, 'credit': ALL},
    'sales': {'debit': NON_SUPPLIER, 'credit': NON_SUPPLIER},
    'purchase': {'debit': NON_CUSTOMER, 'credit': NON_CUSTOMER},
}

CUSTOMER_WORDS = ('customer', 'receivable', 'debtor')


def describe_restriction(voucher_type, side):
    return RESTRICTIONS[voucher_type][side]


def _money_q():
    return Q(kind__in=LedgerAccount.MONEY_KINDS)


def _vat_q():
    """A VAT ledger: flagged as one, or — for a chart seeded before the flag
    existed — simply named "VAT"."""
    return ~Q(vat_kind='') | Q(name__icontains='vat')


def _supplier_q():
    """Supplier / payable accounts: a supplier's own sub-ledger, anything
    under the supplier control account, and liability ledgers that are not
    VAT (a 'Loans' or 'PAYE Payable' ledger keyed straight into the chart)."""
    return (Q(kind='supplier') | Q(supplier__isnull=False) | Q(parent__is_supplier_control=True)
            | (Q(account_type=LedgerAccount.LIABILITY) & ~_vat_q()))


def _customer_q():
    """Customer / receivable accounts: a customer's own sub-ledger, anything
    under the customer control account, and ledgers whose own or whose
    parent's name says customer / receivable / debtor."""
    named = Q()
    for word in CUSTOMER_WORDS:
        named |= Q(name__icontains=word) | Q(parent__name__icontains=word)
    return Q(kind='customer') | Q(customer__isnull=False) | Q(parent__is_customer_control=True) | named


def postable_accounts():
    """Every ledger a voucher line may name at all."""
    return (LedgerAccount.objects
            .filter(is_active=True, is_group=False,
                    is_customer_control=False, is_supplier_control=False)
            .select_related('parent', 'customer', 'supplier', 'currency'))


def allowed_accounts(voucher_type, side):
    """The ledgers allowed on `side` ('debit'/'credit') of `voucher_type`."""
    base = postable_accounts()
    rule = RESTRICTIONS[voucher_type][side]
    if rule == BANK_CASH:
        return base.filter(_money_q())
    if rule == NON_BANK_CASH:
        return base.exclude(_money_q())
    if rule == ALL:
        return base
    if rule == NON_SUPPLIER:
        return base.exclude(_supplier_q())
    if rule == NON_CUSTOMER:
        return base.exclude(_customer_q())
    raise ValueError(rule)


def _is_customer_like(account):
    names = [(account.name or '').lower()]
    if account.parent_id:
        names.append((account.parent.name or '').lower())
    return (account.kind == 'customer' or account.is_customer_account
            or (account.parent_id is not None and account.parent.is_customer_control)
            or any(word in name for name in names for word in CUSTOMER_WORDS))


def _is_supplier_like(account):
    is_vat = bool(account.vat_kind) or 'vat' in (account.name or '').lower()
    account_type = account.account_type or LedgerAccount.TYPE_FOR_KIND.get(account.kind, '')
    return (account.kind == 'supplier' or account.is_supplier_account
            or (account.parent_id is not None and account.parent.is_supplier_control)
            or (account_type == LedgerAccount.LIABILITY and not is_vat))


def account_allowed(voucher_type, side, account):
    """The same check for one ledger, in Python — this is what posting calls."""
    if account is None or not account.is_postable:
        return False
    rule = RESTRICTIONS[voucher_type][side]
    if rule == BANK_CASH:
        return account.is_money
    if rule == NON_BANK_CASH:
        return not account.is_money
    if rule == ALL:
        return True
    if rule == NON_SUPPLIER:
        return not _is_supplier_like(account)
    if rule == NON_CUSTOMER:
        return not _is_customer_like(account)
    return False


def restriction_error(voucher_type, side, account):
    label = dict(Voucher.TYPES)[voucher_type]
    return (f"{account} cannot be {side}ed on a {label} voucher. "
            f"Allowed: {describe_restriction(voucher_type, side)}.")
