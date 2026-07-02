from rest_framework import serializers
from .models import SystemActivity, Notification
from apps.users.serializers import UserSerializer


class NotificationSerializer(serializers.ModelSerializer):
    time_ago = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        fields = ['id', 'title', 'message', 'url', 'level', 'is_read', 'created_at', 'time_ago']
        read_only_fields = fields

    def get_time_ago(self, obj):
        from django.utils.timesince import timesince
        return timesince(obj.created_at) + " ago"

class ActivitySerializer(serializers.ModelSerializer):
    user_details = UserSerializer(source='user', read_only=True)
    time_ago = serializers.SerializerMethodField()

    class Meta:
        model = SystemActivity
        fields = ['id', 'user', 'user_details', 'activity_type', 'description', 'icon_class', 'created_at', 'time_ago']

    def get_time_ago(self, obj):
        from django.utils.timesince import timesince
        return timesince(obj.created_at) + " ago"
