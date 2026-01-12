from rest_framework import viewsets, permissions
from .models import SystemActivity
from .serializers import ActivitySerializer

class ActivityViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = SystemActivity.objects.all()[:15]
    serializer_class = ActivitySerializer
    permission_classes = [permissions.IsAuthenticated]
