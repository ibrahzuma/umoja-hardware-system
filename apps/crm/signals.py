"""Keep the CRM register in step with the POS.

A sale made at the till appears in the register straight away, and its payment
state follows it: paid in full, part paid after a deposit, or wholly on credit
until the money comes in. Recording a payment later — on the Debtors screen or
Credit Follow-up — updates the same CRM row.

The work itself lives in apps/crm/sync.py; this module only decides when to run
it. Sync failures must never break a sale, so each hook is guarded: a till that
cannot save a sale is a far worse problem than a register that is briefly stale
(`python manage.py sync_crm_from_sales` puts it right).
"""

import logging

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from . import sync

logger = logging.getLogger(__name__)


@receiver(post_save, sender='sales.Sale', dispatch_uid='crm_sync_sale')
def sale_saved(sender, instance, created, **kwargs):
    try:
        sync.sync_sale(instance)
    except Exception:
        logger.exception('CRM sync failed for sale %s', instance.pk)


@receiver(post_save, sender='sales.Transaction', dispatch_uid='crm_sync_transaction')
def transaction_saved(sender, instance, created, **kwargs):
    try:
        sync.sync_transaction(instance)
    except Exception:
        logger.exception('CRM sync failed for transaction %s', instance.pk)


@receiver(post_delete, sender='sales.Transaction', dispatch_uid='crm_unsync_transaction')
def transaction_deleted(sender, instance, **kwargs):
    """Money reversed on the sales side is reversed here too."""
    try:
        from .models import CrmPayment
        CrmPayment.objects.filter(source_transaction=instance.pk).delete()
    except Exception:
        logger.exception('CRM sync failed removing transaction %s', instance.pk)
