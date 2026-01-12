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
