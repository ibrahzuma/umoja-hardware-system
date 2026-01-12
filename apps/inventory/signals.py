from django.db.models.signals import post_save
from django.dispatch import receiver
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from .models import Stock

@receiver(post_save, sender=Stock)
def stock_update_handler(sender, instance, created, **kwargs):
    channel_layer = get_channel_layer()
    
    # Broadcast stock update
    async_to_sync(channel_layer.group_send)(
        "stock_updates",
        {
            "type": "stock_update",
            "message": {
                "stock_id": instance.id,
                "product_id": instance.product.id,
                "branch_id": instance.branch.id,
                "quantity": instance.quantity
            }
        }
    )

    # Check for Low Stock
    if instance.quantity <= instance.low_stock_threshold:
        async_to_sync(channel_layer.group_send)(
            "stock_updates",
            {
                "type": "low_stock_alert",
                "message": {
                    "product_name": instance.product.name,
                    "branch_name": instance.branch.name,
                    "quantity": instance.quantity
                }
            }
        )
