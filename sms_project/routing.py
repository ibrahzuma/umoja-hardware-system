from django.urls import re_path
from apps.inventory.consumers import StockConsumer

websocket_urlpatterns = [
    re_path(r'ws/stock/$', StockConsumer.as_asgi()),
    re_path(r'ws/inventory/$', StockConsumer.as_asgi()),
]
