from django.contrib import admin

# Register your models here.


from .models import SmsLog as _SmsLog


@admin.register(_SmsLog)
class SmsLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'to', 'event', 'status')
    list_filter = ('status', 'event')
    search_fields = ('to', 'message')
