from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group, Permission
from apps.users.models import User

class Command(BaseCommand):
    help = 'Creates default groups and permissions based on ROLE_CHOICES'

    def handle(self, *args, **options):
        # Map ROLE_CHOICES code to readable Group Name
        ROLE_MAP = {
            'admin': 'Admin',
            'manager': 'Manager',
            'staff': 'Staff',
            'afisa_ugavi': 'Afisa Ugavi',
            'stock_controller': 'Stock Controller',
            'sales_rep': 'Sales Representative',
            'store_manager': 'Store Manager',
            'accountant': 'Accountant',
            'store_keeper': 'Store Keeper',
        }
        # Define permissions for each role
        # Format: 'app_label.action_model'
        PERMISSIONS = {
            'Admin': '__all__', # Handle separately
            'Manager': [
                'users.view_user', 'users.add_user', 'users.change_user',
                'inventory.view_product', 'inventory.add_product', 'inventory.change_product',
                'inventory.view_param_report',
                'sales.view_sale', 'sales.change_sale', 'sales.delete_sale',
                'sales.view_report',
            ],
            'Afisa Ugavi': [
                'inventory.view_purchaseorder', 'inventory.add_purchaseorder', 'inventory.change_purchaseorder',
                'inventory.view_supplier', 'inventory.add_supplier', 'inventory.change_supplier',
                'inventory.view_truck', 'inventory.add_truck', 'inventory.change_truck',
                'inventory.view_truckallocation', 'inventory.add_truckallocation', 'inventory.change_truckallocation',
            ],
            'Stock Controller': [
                'inventory.view_goodsreceivednote', 'inventory.add_goodsreceivednote', 'inventory.change_goodsreceivednote',
                'inventory.view_stock', 'inventory.change_stock',
                'inventory.view_product',
            ],
            'Sales Representative': [
                'sales.view_sale', 'sales.add_sale',
                'sales.view_quotation', 'sales.add_quotation', 'sales.change_quotation',
                'sales.view_customer', 'sales.add_customer',
                'sales.view_vehicle',
                'inventory.view_product',
            ],
            'Store Manager': [
                'inventory.view_driver', 'inventory.add_driver', 'inventory.change_driver',
                'inventory.view_truckmaintenance', 'inventory.add_truckmaintenance',
                'inventory.view_stocktransfer', 'inventory.add_stocktransfer',
                'sales.dispatch_order',
            ],
            'Accountant': [
                'finance.view_expense', 'finance.add_expense', 'finance.change_expense',
                'finance.view_taxpayment', 'finance.add_taxpayment',
                'sales.view_transaction',
            ],
            'Store Keeper': [
                'inventory.view_stock',
                'sales.view_sale',
            ],
            'Staff': [],
        }

        from django.contrib.auth.models import ContentType

        for role_code, role_label in User.ROLE_CHOICES:
            group_name = ROLE_MAP.get(role_code, role_label)
            group, created = Group.objects.get_or_create(name=group_name)
            
            perms_to_add = PERMISSIONS.get(group_name, [])
            
            if perms_to_add == '__all__':
                # Admin gets everything
                permissions = Permission.objects.all()
                group.permissions.set(permissions)
                self.stdout.write(f'Assigned ALL permissions to {group_name}')
                continue

            for perm_str in perms_to_add:
                try:
                    app_label, codename = perm_str.split('.')
                    permission = Permission.objects.get(content_type__app_label=app_label, codename=codename)
                    group.permissions.add(permission)
                    self.stdout.write(f'  + {perm_str} to {group_name}')
                except Permission.DoesNotExist:
                    self.stdout.write(self.style.WARNING(f'  ! Permission {perm_str} not found'))

            self.stdout.write(self.style.SUCCESS(f'Updated permissions for: {group_name}'))

        self.stdout.write(self.style.SUCCESS('Successfully synced roles and permissions'))
