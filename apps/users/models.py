from django.contrib.auth.models import AbstractUser
from django.db import models

class User(AbstractUser):
    ROLE_CHOICES = (
        ('admin', 'Admin'),
        ('manager', 'Manager'),
        ('staff', 'Staff'),
        ('afisa_ugavi', 'Afisa Ugavi'),
        ('stock_controller', 'Stock Controller'),
        ('sales_rep', 'Sales Representative'),
        ('store_manager', 'Store Manager'),
        ('accountant', 'Accountant'),
        ('store_keeper', 'Store Keeper'),
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='staff')
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

    @property
    def is_procurement_officer(self):
        return self.role == 'afisa_ugavi' or self.groups.filter(name__in=['Afisa Ugavi', 'Procurement Officer']).exists()

    @property
    def is_stock_controller(self):
        return self.role == 'stock_controller' or self.groups.filter(name__in=['Stock Controller', 'stock_controller']).exists()

    @property
    def is_sales_rep(self):
        return self.role == 'sales_rep' or self.groups.filter(name__in=['Sales Representative', 'Sales Rep']).exists()

    @property
    def is_store_manager(self):
        return self.role == 'store_manager' or self.groups.filter(name__in=['Store Manager']).exists()

    @property
    def is_accountant(self):
        return self.role == 'accountant' or self.groups.filter(name__in=['Accountant']).exists()

    @property
    def is_store_keeper(self):
        return self.role == 'store_keeper' or self.groups.filter(name__in=['Store Keeper']).exists()

    def __str__(self):
        return f"{self.username} ({self.role})"
