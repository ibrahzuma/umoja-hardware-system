from django.db import models
from django.conf import settings

class SystemActivity(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    activity_type = models.CharField(max_length=50) # e.g., 'sale', 'stock', 'expense', 'transfer'
    description = models.TextField()
    icon_class = models.CharField(max_length=50, default='bi-info-circle')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name_plural = "System Activities"

    def __str__(self):
        return f"{self.activity_type}: {self.description[:30]}"


class Notification(models.Model):
    """A per-user inbox item. Created via apps.core.notify.notify()."""
    LEVELS = (('info', 'Info'), ('success', 'Success'), ('warning', 'Warning'), ('danger', 'Danger'))
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notifications')
    title = models.CharField(max_length=150)
    message = models.TextField(blank=True)
    url = models.CharField(max_length=300, blank=True, help_text="Where clicking the notification takes the user")
    level = models.CharField(max_length=10, choices=LEVELS, default='info')
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.recipient}: {self.title}"

class SystemSettings(models.Model):
    company_name = models.CharField(max_length=100, default="Umoja Hardware")
    currency = models.CharField(max_length=10, default="TZS")
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=18.00, help_text="Percentage (e.g. 18.00)")
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    website = models.CharField(max_length=120, blank=True)
    address = models.TextField(blank=True)
    tin = models.CharField(max_length=40, blank=True, help_text="Taxpayer Identification Number")
    vrn = models.CharField(max_length=40, blank=True, help_text="VAT Registration Number")
    logo = models.ImageField(upload_to='company_logo/', blank=True, null=True)
    # Trial run: the shelves are not counted in yet, so the system must not
    # refuse a sale because its stock rows say zero. Admin switches it off once
    # opening stock is in; with it off nothing is sold that is not there.
    allow_negative_stock = models.BooleanField(
        default=False,
        help_text="Trial run: allow selling items whose stock is zero or negative.")

    @classmethod
    def negative_stock_allowed(cls):
        s = cls.objects.only('allow_negative_stock').first()
        return bool(s and s.allow_negative_stock)

    def save(self, *args, **kwargs):
        if not self.pk and SystemSettings.objects.exists():
            return # Prevent creating multiple settings
        return super().save(*args, **kwargs)

    def __str__(self):
        return "System Configuration"



class SmsSettings(models.Model):
    """eHub SMS gateway settings — a singleton, edited on /sms-notifications/.

    The API key and secret come from eHub's dashboard (Developer -> API Keys).
    They can also be supplied as EHUB_SMS_API_KEY / EHUB_SMS_API_SECRET in the
    environment; a value saved here wins over the environment.
    """
    enabled = models.BooleanField(default=False, help_text="Master switch: no SMS leaves while this is off.")
    api_key = models.CharField(max_length=255, blank=True)
    api_secret = models.CharField(max_length=255, blank=True)
    sender_name = models.CharField(max_length=11, default="ehub",
                                   help_text="The approved sender ID as it shows on the phone.")
    # eHub sends by the sender ID's UUID; resolved from sender_name and cached.
    sender_uuid = models.CharField(max_length=64, blank=True)
    notify_pending_sale = models.BooleanField(
        default=True, help_text="Text the approvers when a sale is waiting for approval.")
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def load(cls):
        obj = cls.objects.first()
        return obj or cls.objects.create()

    def save(self, *args, **kwargs):
        if not self.pk and SmsSettings.objects.exists():
            self.pk = SmsSettings.objects.first().pk
        return super().save(*args, **kwargs)

    @property
    def key(self):
        import os
        return self.api_key or os.getenv('EHUB_SMS_API_KEY', '')

    @property
    def secret(self):
        import os
        return self.api_secret or os.getenv('EHUB_SMS_API_SECRET', '')

    @property
    def has_credentials(self):
        return bool(self.key and self.secret)

    def __str__(self):
        return "SMS Settings"


class SmsRecipient(models.Model):
    """The phone an approver receives SMS alerts on. One row per user."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name='sms_recipient')
    phone = models.CharField(max_length=20, blank=True)
    active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.user} ({self.phone})"


class SmsLog(models.Model):
    """Every SMS the system tried to send, and what the gateway said."""
    STATUS_CHOICES = (('sent', 'Sent'), ('failed', 'Failed'))
    to = models.CharField(max_length=20)
    message = models.TextField()
    event = models.CharField(max_length=40, blank=True)  # e.g. 'sale_pending', 'test'
    status = models.CharField(max_length=10, choices=STATUS_CHOICES)
    message_id = models.CharField(max_length=64, blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f"{self.to} [{self.status}]"
