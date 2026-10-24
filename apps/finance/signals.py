"""Keep the accounting ledger in step with the till.

Every sale lands in the ledger as pending the moment it is made, and its
figures follow it while Accounts have not posted it yet — a deposit taken an
hour after a credit sale shows up on the same row.

The work lives in apps/finance/sales_ledger.py; this module only decides when
to run it. A sync failure must never break a sale: a till that cannot save is a
far worse problem than a ledger that is briefly stale, and
`python manage.py sync_sales_ledger` puts it right.
"""

import logging

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from . import sales_ledger

logger = logging.getLogger(__name__)


@receiver(post_save, sender='sales.Sale', dispatch_uid='ledger_sync_sale')
def sale_saved(sender, instance, created, **kwargs):
    try:
        sales_ledger.sync_sale(instance)
    except Exception:
        logger.exception('Sales ledger sync failed for sale %s', instance.pk)


@receiver(post_delete, sender='sales.Sale', dispatch_uid='ledger_drop_sale')
def sale_deleted(sender, instance, **kwargs):
    try:
        sales_ledger.drop_sale(instance)
    except Exception:
        logger.exception('Sales ledger sync failed removing sale %s', instance.pk)


@receiver(post_save, sender='sales.Transaction', dispatch_uid='ledger_sync_transaction')
def transaction_saved(sender, instance, created, **kwargs):
    try:
        sales_ledger.sync_transaction(instance)
    except Exception:
        logger.exception('Sales ledger sync failed for transaction %s', instance.pk)


@receiver(post_delete, sender='sales.Transaction', dispatch_uid='ledger_unsync_transaction')
def transaction_deleted(sender, instance, **kwargs):
    """Money reversed on the sales side is reversed here too."""
    try:
        if instance.sale_id:
            from apps.sales.models import Sale
            sale = Sale.objects.filter(pk=instance.sale_id).first()
            if sale is not None:
                sales_ledger.sync_sale(sale)
    except Exception:
        logger.exception('Sales ledger sync failed removing transaction %s', instance.pk)


# --- Chart of accounts -------------------------------------------------------
# A bank account, customer or supplier gets a ledger the moment it exists, so
# the voucher dropdowns never lag behind the rest of the system. Renames follow
# too. See apps/finance/vouchers.py::ensure_ledger_for.

def _ledger_follows(sender, instance, **kwargs):
    try:
        from . import vouchers
        vouchers.ensure_ledger_for(instance)
    except Exception:
        logger.exception('Could not keep a ledger for %s %s', sender.__name__, instance.pk)


for _model, _uid in (('finance.BankAccount', 'ledger_for_bank'),
                     ('sales.Customer', 'ledger_for_customer'),
                     ('inventory.Supplier', 'ledger_for_supplier')):
    post_save.connect(_ledger_follows, sender=_model, dispatch_uid=_uid, weak=False)


@receiver(post_save, sender='finance.LedgerAccount', dispatch_uid='party_for_ledger')
def _party_follows_ledger(sender, instance, created, raw=False, **kwargs):
    """The mirror of the above: a customer/supplier ledger keyed straight into
    the chart of accounts grows the sales.Customer / inventory.Supplier it
    stands for, so a customer created there shows on the POS and in the register
    too. No-op for a ledger that already names its party (the usual path) or one
    that is not a party ledger — see vouchers.ensure_party_for."""
    if raw:
        return
    if instance.kind not in ('customer', 'supplier') or instance.is_group:
        return
    if instance.is_customer_control or instance.is_supplier_control:
        return
    if instance.customer_id or instance.supplier_id:
        return
    try:
        from . import vouchers
        vouchers.ensure_party_for(instance)
    except Exception:
        logger.exception('Could not keep a party for ledger %s', instance.pk)


@receiver(post_save, sender='inventory.Branch', dispatch_uid='ledger_branch_cash_book')
def _branch_gets_a_cash_book(sender, instance, created, **kwargs):
    """A new shop gets its own cash book (see vouchers.ensure_branch_cash_books)."""
    if created:
        from .vouchers import ensure_branch_cash_books
        ensure_branch_cash_books()
