import json
from channels.generic.websocket import AsyncWebsocketConsumer

class StockConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.group_name = "stock_updates"
        await self.channel_layer.group_add(
            self.group_name,
            self.channel_name
        )
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(
            self.group_name,
            self.channel_name
        )

    # Receive message from room group
    async def stock_update(self, event):
        message = event['message']
        # Send message to WebSocket
        await self.send(text_data=json.dumps({
            'type': 'stock_update',
            'data': message
        }))

    async def low_stock_alert(self, event):
        message = event['message']
        await self.send(text_data=json.dumps({
            'type': 'low_stock_alert',
            'data': message
        }))
    async def sales_notification(self, event):
        message = event['message']
        await self.send(text_data=json.dumps({
            'type': 'sales_notification',
            'data': message
        }))
