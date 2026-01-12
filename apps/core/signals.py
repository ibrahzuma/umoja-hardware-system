from django.db.models.signals import post_save
from django.dispatch import receiver
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
import json

def broadcast_activity(activity):
    channel_layer = get_channel_layer()
    if channel_layer:
        async_to_sync(channel_layer.group_send)(
            "stock_updates",
            {
                "type": "activity_update",
                "data": {
                    "id": activity.id,
                    "type": activity.activity_type,
                    "description": activity.description,
                    "icon": activity.icon_class,
                    "user": activity.user.username if activity.user else "System",
                    "time": "Just now"
                }
            }
        )

@receiver(post_save, sender='sales.Sale')
def sale_activity(sender, instance, created, **kwargs):
    # Only create activity if it has a total amount
    # This avoids logging 0.00 during the initial creation in the serializer
    if instance.total_amount > 0:
        from .models import SystemActivity
        # Check if we already logged this sale to avoid duplicates on status updates
        if not SystemActivity.objects.filter(activity_type='sale', description__contains=instance.invoice_number).exists():
            desc = f"New Sale #{instance.invoice_number} - TZS {instance.total_amount:,.2f}"
            activity = SystemActivity.objects.create(
                user=instance.user,
                activity_type='sale',
                description=desc,
                icon_class='bi-cart-check-fill'
            )
            broadcast_activity(activity)

@receiver(post_save, sender='inventory.StockAdjustment')
def stock_adj_activity(sender, instance, created, **kwargs):
    if created:
        from .models import SystemActivity
        desc = f"Stock adjusted for {instance.product.name} at {instance.branch.name}"
        activity = SystemActivity.objects.create(
            user=instance.user,
            activity_type='stock',
            description=desc,
            icon_class='bi-box-seam'
        )
        broadcast_activity(activity)

@receiver(post_save, sender='inventory.StockTransfer')
def stock_transfer_activity(sender, instance, created, **kwargs):
    if created:
        from .models import SystemActivity
        desc = f"Stock transfer: {instance.product.name} from {instance.from_branch.name} to {instance.to_branch.name}"
        activity = SystemActivity.objects.create(
            activity_type='transfer',
            description=desc,
            icon_class='bi-arrow-left-right'
        )
        broadcast_activity(activity)

@receiver(post_save, sender='finance.Expense')
def expense_activity(sender, instance, created, **kwargs):
    if created:
        from .models import SystemActivity
        desc = f"New Expense: {instance.category.name if instance.category else 'General'} - TZS {instance.amount:,.2f}"
        activity = SystemActivity.objects.create(
            user=instance.created_by,
            activity_type='expense',
            description=desc,
            icon_class='bi-wallet2'
        )
        broadcast_activity(activity)
