from django.contrib import admin

from .models import CrmCredit, CrmPayment, CustomerRecord


class CrmPaymentInline(admin.TabularInline):
    model = CrmPayment
    extra = 0


@admin.register(CustomerRecord)
class CustomerRecordAdmin(admin.ModelAdmin):
    list_display = ('date', 'receipt_number', 'efd_receipt_number', 'customer_name', 'tin',
                    'sales_amount', 'amount_paid', 'balance', 'payment_status')
    list_filter = ('date',)
    search_fields = ('customer_name', 'tin', 'receipt_number', 'efd_receipt_number')
    date_hierarchy = 'date'
    inlines = [CrmPaymentInline]


@admin.register(CrmPayment)
class CrmPaymentAdmin(admin.ModelAdmin):
    list_display = ('paid_on', 'record', 'amount', 'method', 'from_credit', 'reference')
    list_filter = ('method', 'from_credit', 'paid_on')
    search_fields = ('record__customer_name', 'reference')


@admin.register(CrmCredit)
class CrmCreditAdmin(admin.ModelAdmin):
    list_display = ('received_on', 'customer_name', 'amount', 'source', 'method', 'reference')
    list_filter = ('source', 'method', 'received_on')
    search_fields = ('customer_name', 'reference', 'note')
