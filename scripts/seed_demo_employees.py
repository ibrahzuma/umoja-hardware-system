"""Seed a Department, JobPosition, and Employee records linked to existing
users so My Attendance has data to work with.
"""

from django.contrib.auth import get_user_model
from apps.inventory.models import Branch
from apps.hr.models import Department, JobPosition, Employee, LeaveType

User = get_user_model()
branch = Branch.objects.first()

# Departments
operations, _ = Department.objects.get_or_create(name='Operations')
admin_dept, _ = Department.objects.get_or_create(name='Administration')
sales_dept, _ = Department.objects.get_or_create(name='Sales')
finance_dept, _ = Department.objects.get_or_create(name='Finance')

# Positions
gm_pos, _ = JobPosition.objects.get_or_create(
    title='General Manager', department=admin_dept,
    defaults={'min_salary': 2_500_000, 'max_salary': 5_000_000},
)
sm_pos, _ = JobPosition.objects.get_or_create(
    title='Store Manager', department=operations,
    defaults={'min_salary': 1_200_000, 'max_salary': 2_500_000},
)
rep_pos, _ = JobPosition.objects.get_or_create(
    title='Sales Representative', department=sales_dept,
    defaults={'min_salary': 600_000, 'max_salary': 1_200_000},
)
acc_pos, _ = JobPosition.objects.get_or_create(
    title='Accountant', department=finance_dept,
    defaults={'min_salary': 1_000_000, 'max_salary': 2_000_000},
)

# Default leave types
LeaveType.objects.get_or_create(name='Annual Leave',  defaults={'days_per_year': 28, 'is_paid': True})
LeaveType.objects.get_or_create(name='Sick Leave',    defaults={'days_per_year': 14, 'is_paid': True})
LeaveType.objects.get_or_create(name='Maternity',     defaults={'days_per_year': 84, 'is_paid': True})
LeaveType.objects.get_or_create(name='Compassionate', defaults={'days_per_year': 4,  'is_paid': True})
LeaveType.objects.get_or_create(name='Unpaid Leave',  defaults={'days_per_year': 0,  'is_paid': False})

# Employees linked to existing test users
SEED = [
    # (username, employee_number, first, last, position, basic_salary)
    ('umoja',       'UM-001', 'Umoja',     'Admin',       gm_pos,  3_500_000),
    ('smanager',    'UM-002', 'Store',     'Manager',     sm_pos,  1_800_000),
    ('salesrep',    'UM-003', 'Sales',     'Rep',         rep_pos, 800_000),
    ('accountant',  'UM-004', 'Mary',      'Accountant',  acc_pos, 1_400_000),
    ('keeper',      'UM-005', 'Store',     'Keeper',      sm_pos,  700_000),
    ('procurement', 'UM-006', 'Procure',   'Officer',     sm_pos,  1_200_000),
    ('stockctrl',   'UM-007', 'Stock',     'Controller',  sm_pos,  900_000),
]

from datetime import date

for username, emp_no, first, last, pos, salary in SEED:
    try:
        u = User.objects.get(username=username)
    except User.DoesNotExist:
        print(f'  skip {username} — user not found')
        continue
    emp, created = Employee.objects.get_or_create(
        employee_number=emp_no,
        defaults={
            'user': u,
            'first_name': first,
            'last_name': last,
            'department': pos.department,
            'position': pos,
            'branch': branch,
            'hire_date': date(2024, 1, 15),
            'status': 'active',
            'basic_salary': salary,
            'housing_allowance': salary * 0.1,
            'transport_allowance': 100_000,
            'phone': '+255 7XX XXX XXX',
            'email': u.email or f'{username}@umoja.example',
        },
    )
    # Link / relink the user if needed
    if not created and emp.user_id != u.id:
        emp.user = u
        emp.save()
    print(f"  {'created' if created else 'updated'}: {emp_no} {first} {last}  ↔  {username}")
