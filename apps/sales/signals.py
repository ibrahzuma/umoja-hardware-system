from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Sale
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

@receiver(post_save, sender=Sale)
def sale_notification(sender, instance, created, **kwargs):
    channel_layer = get_channel_layer()
    
    if created:
        user_name = instance.user.username if instance.user else "Unknown User"
        message = f"New Sale Created: #{instance.invoice_number} by {user_name}"
        title = "New Sale"
    else:
        # Avoid notifying for every minor save, maybe just status changes?
        # For now, let's notify on status changes if meaningful
        message = f"Sale #{instance.invoice_number} updated to {instance.status}"
        title = "Order Update"

    async_to_sync(channel_layer.group_send)(
        "stock_updates",  # Using the existing group from StockConsumer
        {
            "type": "sales_notification",
            "message": {
                "title": title,
                "body": message,
                "sale_id": instance.id,
                "status": instance.status,
                "timestamp": instance.created_at.strftime("%H:%M:%S")
            }
        }
    )
