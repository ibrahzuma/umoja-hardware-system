import os
import django
from django.contrib.auth.models import Group
from django.contrib.auth import get_user_model

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sms_project.settings')
django.setup()

def verify_role_deletion():
    User = get_user_model()
    
    # 1. Setup User and Group
    group_name = 'TestDeleteRole'
    group, _ = Group.objects.get_or_create(name=group_name)
    
    username = 'role_test_user'
    user, _ = User.objects.get_or_create(username=username)
    user.set_password('pass123')
    user.is_active = True
    user.groups.add(group)
    user.save()
    
    print(f"SETUP: User '{username}' created and added to group '{group_name}'.")
    print(f"User ID: {user.id}")
    print(f"User Active: {user.is_active}")
    print(f"User Groups: {[g.name for g in user.groups.all()]}")
    
    # 2. Delete the Group
    print(f"\nACTION: Deleting group '{group_name}'...")
    group.delete()
    
    # 3. Verify User Integrity
    try:
        user.refresh_from_db()
        print(f"\nRESULT: User '{username}' still exists.")
        print(f"User Active: {user.is_active}")
        print(f"User Groups: {[g.name for g in user.groups.all()]}")
        
        if not user.is_active:
            print("CRITICAL: User was deactivated!")
    except User.DoesNotExist:
        print("CRITICAL: User was DELETED!")

if __name__ == "__main__":
    verify_role_deletion()
