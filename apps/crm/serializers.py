from rest_framework import serializers

from .models import CrmCredit, CrmPayment, CustomerRecord


class CrmCreditSerializer(serializers.ModelSerializer):
    source_display = serializers.CharField(source='get_source_display', read_only=True)
    method_display = serializers.CharField(source='get_method_display', read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True, default='')

    class Meta:
        model = CrmCredit
        fields = '__all__'
        read_only_fields = ('created_by', 'created_at')

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError('Credit amount must be greater than zero.')
        return value


class CrmPaymentSerializer(serializers.ModelSerializer):
    method_display = serializers.CharField(source='get_method_display', read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True, default='')

    class Meta:
        model = CrmPayment
        fields = '__all__'
        read_only_fields = ('created_by', 'created_at')

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError('Payment amount must be greater than zero.')
        return value

    def validate(self, attrs):
        """A payment may not exceed what is still outstanding.

        Catches the common fat-finger (an extra zero) at the point of entry —
        without it the register would quietly show a negative balance.
        """
        record = attrs.get('record') or getattr(self.instance, 'record', None)
        amount = attrs.get('amount') or getattr(self.instance, 'amount', 0)
        if record is None:
            return attrs

        outstanding = record.balance
        if self.instance is not None:
            outstanding += self.instance.amount  # editing: ignore its own old value
        if amount > outstanding:
            raise serializers.ValidationError({
                'amount': (
                    f'Payment of {amount:,.2f} exceeds the outstanding balance '
                    f'of {outstanding:,.2f} on this sale. Use "receive" to put '
                    f'the excess on the customer\'s account as credit.'
                )
            })
        return attrs


class CustomerRecordSerializer(serializers.ModelSerializer):
    created_by_name = serializers.CharField(source='created_by.username', read_only=True, default='')
    amount_paid = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    balance = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    payment_status = serializers.CharField(read_only=True)

    class Meta:
        model = CustomerRecord
        fields = '__all__'
        read_only_fields = ('created_by', 'created_at', 'updated_at', 'source_invoice')
