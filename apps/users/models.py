from django.contrib.auth.models import AbstractUser
from django.db import models

class User(AbstractUser):
    ROLE_CHOICES = (
        ('admin', 'Admin'),
        ('manager', 'Manager'),
        ('staff', 'Staff'),
    )
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default='staff')
    branch = models.ForeignKey('inventory.Branch', on_delete=models.SET_NULL, null=True, blank=True, related_name='users')

    @property
    def is_manager(self):
        return self.role == 'manager' or self.groups.filter(name__in=['Manager', 'Store Manager', 'manager']).exists()

    @property
    def is_admin_role(self):
        return self.role == 'admin' or self.groups.filter(name__in=['Admin', 'admin']).exists()

    @property
    def is_sales_manager(self):
        return self.groups.filter(name__in=['Sales Manager', 'sales_manager']).exists()

    def __str__(self):
        return f"{self.username} ({self.role})"
