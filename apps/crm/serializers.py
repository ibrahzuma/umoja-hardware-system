from rest_framework import serializers

from .models import CustomerRecord


class CustomerRecordSerializer(serializers.ModelSerializer):
    created_by_name = serializers.CharField(source='created_by.username', read_only=True, default='')

    class Meta:
        model = CustomerRecord
        fields = '__all__'
        read_only_fields = ('created_by', 'created_at', 'updated_at', 'source_invoice')
