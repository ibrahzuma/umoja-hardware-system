from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import SystemActivity, Notification
from .serializers import ActivitySerializer, NotificationSerializer

class ActivityViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = SystemActivity.objects.all()[:15]
    serializer_class = ActivitySerializer
    permission_classes = [permissions.IsAuthenticated]


class NotificationViewSet(viewsets.ReadOnlyModelViewSet):
    """Per-user notification inbox. Users only ever see their own."""
    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Notification.objects.filter(recipient=self.request.user)

    @action(detail=False, methods=['get'])
    def unread_count(self, request):
        return Response({'count': self.get_queryset().filter(is_read=False).count()})

    @action(detail=False, methods=['post'])
    def mark_all_read(self, request):
        self.get_queryset().filter(is_read=False).update(is_read=True)
        return Response({'status': 'ok'})

    @action(detail=True, methods=['post'])
    def mark_read(self, request, pk=None):
        n = self.get_object()
        n.is_read = True
        n.save(update_fields=['is_read'])
        return Response({'status': 'ok'})
