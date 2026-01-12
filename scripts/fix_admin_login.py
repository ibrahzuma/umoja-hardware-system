import os
import django
from django.contrib.auth import get_user_model

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sms_project.settings')
django.setup()

def fix_admin_login():
    User = get_user_model()
    username = 'admin'
    password = 'admin123'
    
    print("--- FIXING ADMIN LOGIN ---")
    
    # 1. Check for duplicates
    users = User.objects.filter(username__iexact=username)
    count = users.count()
    print(f"Found {count} users matching '{username}' (case-insensitive).")
    
    if count > 1:
        print("Duplicate users found! Cleaning up...")
        # Keep the one with the lowest ID (likely the original)
        u_keep = users.order_by('id').first()
        for u in users:
            if u.id != u_keep.id:
                print(f"Deleting duplicate user: {u.username} (ID: {u.id})")
                u.delete()
        admin = u_keep
    elif count == 1:
        admin = users.first()
    else:
        print("No admin user found. Creating new one.")
        admin = User(username=username)
    
    # 2. Update the canonical admin user
    print(f"Updating user: {admin.username} (ID: {admin.id})")
    admin.username = username # Ensure lowercase
    admin.email = 'admin@example.com' 
    admin.is_active = True
    admin.is_staff = True
    admin.is_superuser = True
    admin.role = 'admin' # Ensure role field is set if it exists
    admin.set_password(password)
    admin.save()
    
    print(f"SUCCESS: Password for '{admin.username}' set to '{password}'.")
    print(f"Status: Active={admin.is_active}, Superuser={admin.is_superuser}, Staff={admin.is_staff}")

if __name__ == "__main__":
    fix_admin_login()
