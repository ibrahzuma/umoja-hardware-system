"""Create one test user per role for the user-guide screenshots.

Run via:  venv/bin/python manage.py shell < scripts/seed_role_users.py
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from apps.inventory.models import Branch

User = get_user_model()

branch = Branch.objects.first()
print(f"using branch: {branch}")

# (username, role_code, group_name, password)
USERS = [
    ("smanager",   "store_manager",    "Store Manager",          "Manager@2026"),
    ("salesrep",   "sales_rep",        "Sales Representative",   "Sales@2026"),
    ("procurement","afisa_ugavi",      "Afisa Ugavi",            "Procurement@2026"),
    ("stockctrl",  "stock_controller", "Stock Controller",       "StockCtl@2026"),
    ("keeper",     "store_keeper",     "Store Keeper",           "Keeper@2026"),
    ("accountant", "accountant",       "Accountant",             "Accountant@2026"),
]

for username, role, group_name, password in USERS:
    u, created = User.objects.get_or_create(
        username=username,
        defaults={
            "email": f"{username}@umoja.example",
            "role": role,
            "branch": branch,
            "first_name": role.replace("_", " ").title(),
            "is_active": True,
            "is_staff": True,
        },
    )
    u.email = f"{username}@umoja.example"
    u.role = role
    u.branch = branch
    u.is_active = True
    u.is_staff = True
    u.set_password(password)
    u.save()
    try:
        group = Group.objects.get(name=group_name)
        u.groups.set([group])
    except Group.DoesNotExist:
        print(f"  WARN: group '{group_name}' missing — run `manage.py create_roles`")
    print(f"  {'created' if created else 'updated'}: {username} / {password}  role={role}")
