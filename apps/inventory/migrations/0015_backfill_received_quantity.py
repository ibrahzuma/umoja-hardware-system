from django.db import migrations


def backfill_received(apps, schema_editor):
    """Set `received_quantity` on orders that predate the delivery-round flow.

    Before this change the cross-check added the delivered quantity to stock
    immediately — even on a discrepancy. So whatever `delivered_quantity` holds
    is exactly what already reached stock, and that is what the new cumulative
    field must start from; otherwise every old order would look completely
    outstanding and could be received a second time.
    """
    PurchaseOrderItem = apps.get_model('inventory', 'PurchaseOrderItem')
    for item in PurchaseOrderItem.objects.select_related('purchase_order').iterator():
        if item.delivered_quantity is not None:
            received = item.delivered_quantity
        elif item.purchase_order.status == 'received':
            received = item.quantity
        else:
            received = 0
        if received != item.received_quantity:
            item.received_quantity = received
            item.save(update_fields=['received_quantity'])


def noop(apps, schema_editor):
    """Nothing to undo — the column is dropped by the reverse of 0014."""


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0014_purchaseorder_admin_note_purchaseorder_decided_at_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill_received, noop),
    ]
