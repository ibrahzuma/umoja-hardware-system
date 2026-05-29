from django.contrib.auth import get_user_model

User = get_user_model()
for uname in ['salesrep', 'umoja']:
    u = User.objects.get(username=uname)
    print(f"--- {uname} ---")
    print("  is_superuser:", u.is_superuser, "is_staff:", u.is_staff)
    print("  groups:", list(u.groups.values_list('name', flat=True)))
    print("  has hr.view_employee:", u.has_perm('hr.view_employee'))
    print("  has hr.view_payslip:", u.has_perm('hr.view_payslip'))
