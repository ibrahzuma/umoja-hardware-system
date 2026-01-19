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

class SystemSettings(models.Model):
    company_name = models.CharField(max_length=100, default="Umoja Hardware")
    currency = models.CharField(max_length=10, default="TZS")
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=18.00, help_text="Percentage (e.g. 18.00)")
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    logo = models.ImageField(upload_to='company_logo/', blank=True, null=True)

    def save(self, *args, **kwargs):
        if not self.pk and SystemSettings.objects.exists():
            return # Prevent creating multiple settings
        return super().save(*args, **kwargs)

    def __str__(self):
        return "System Configuration"

