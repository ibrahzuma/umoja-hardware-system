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

    def save(self, *args, **kwargs):
        if not self.pk and SystemSettings.objects.exists():
            return # Prevent creating multiple settings
        return super().save(*args, **kwargs)

    def __str__(self):
        return "System Configuration"

