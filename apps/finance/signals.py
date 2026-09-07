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
