import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sms_project.settings")
django.setup()

from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from apps.inventory.models import PurchaseOrder, Truck, TruckAllocation, Supplier, Stock
from apps.finance.models import SupplierPayment

def setup_group():
    group, created = Group.objects.get_or_create(name='Afisa Ugavi')
    print(f"Group 'Afisa Ugavi' {'created' if created else 'exists'}.")

    # Define models to grant permissions for
    models_to_grant = [
        (PurchaseOrder, ['add', 'change', 'delete', 'view']),
        (Truck, ['add', 'change', 'delete', 'view']),
        (TruckAllocation, ['add', 'change', 'delete', 'view']),
        (Supplier, ['add', 'change', 'delete', 'view']),
        (SupplierPayment, ['add', 'change', 'delete', 'view']),
        (Stock, ['view', 'change']), # Allow checking and updating stock
    ]

    permissions_to_add = []
    for model_cls, perms in models_to_grant:
        content_type = ContentType.objects.get_for_model(model_cls)
        for perm_code in perms:
            codename = f"{perm_code}_{model_cls._meta.model_name}"
            try:
                perm = Permission.objects.get(content_type=content_type, codename=codename)
                permissions_to_add.append(perm)
            except Permission.DoesNotExist:
                print(f"Warning: Permission {codename} not found.")

    group.permissions.set(permissions_to_add)
    print(f"Assigned {len(permissions_to_add)} permissions to 'Afisa Ugavi'.")

if __name__ == '__main__':
    setup_group()
