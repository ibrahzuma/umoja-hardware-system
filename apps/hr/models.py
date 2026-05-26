"""HR domain models for Umoja Hardware.

Covers the lifecycle of a Tanzanian employee:
  Department / JobPosition  → org chart
  Employee                  → personal + employment record (linked to User)
  LeaveType, LeaveRequest   → time off
  AttendanceRecord          → daily clock-in/out
  PayrollPeriod, Payslip    → monthly payroll with TZA statutory deductions
  EmployeeDocument          → contracts, IDs, certificates
  PerformanceReview         → annual / quarterly reviews
  DisciplinaryAction        → warnings, suspensions
"""

from __future__ import annotations

from decimal import Decimal
from django.conf import settings
from django.db import models
from simple_history.models import HistoricalRecords


# ---------------------------------------------------------------------------
# Org chart
# ---------------------------------------------------------------------------


class Department(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    head = models.ForeignKey(
        'Employee',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
        help_text="Department head (an existing employee)",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self) -> str:
        return self.name


class JobPosition(models.Model):
    title = models.CharField(max_length=120)
    department = models.ForeignKey(
        Department, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='positions',
    )
    min_salary = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    max_salary = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['title']

    def __str__(self) -> str:
        return self.title


# ---------------------------------------------------------------------------
# Employee record
# ---------------------------------------------------------------------------


class Employee(models.Model):
    GENDER_CHOICES = (
        ('male', 'Male'),
        ('female', 'Female'),
        ('other', 'Other'),
    )
    MARITAL_CHOICES = (
        ('single', 'Single'),
        ('married', 'Married'),
        ('divorced', 'Divorced'),
        ('widowed', 'Widowed'),
    )
    EMPLOYMENT_TYPES = (
        ('permanent', 'Permanent'),
        ('contract', 'Contract'),
        ('casual', 'Casual'),
        ('intern', 'Intern'),
    )
    STATUS_CHOICES = (
        ('active', 'Active'),
        ('on_leave', 'On Leave'),
        ('suspended', 'Suspended'),
        ('terminated', 'Terminated'),
    )

    # Link to system login (optional — not every employee needs a system account)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='employee',
    )

    employee_number = models.CharField(max_length=20, unique=True, db_index=True)
    first_name = models.CharField(max_length=80)
    middle_name = models.CharField(max_length=80, blank=True)
    last_name = models.CharField(max_length=80)
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES, default='male')
    date_of_birth = models.DateField(null=True, blank=True)
    marital_status = models.CharField(max_length=10, choices=MARITAL_CHOICES, default='single')

    # National identifiers (Tanzania)
    nida_number = models.CharField(
        max_length=30, blank=True, db_index=True,
        verbose_name='NIDA number',
        help_text='National ID (Kitambulisho cha Taifa)',
    )
    tin_number = models.CharField(
        max_length=15, blank=True, verbose_name='TIN',
        help_text='Taxpayer Identification Number',
    )
    nssf_number = models.CharField(max_length=20, blank=True, verbose_name='NSSF #')
    nhif_number = models.CharField(max_length=20, blank=True, verbose_name='NHIF #')

    # Contact
    phone = models.CharField(max_length=20, blank=True)
    alt_phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    emergency_contact_name = models.CharField(max_length=120, blank=True)
    emergency_contact_phone = models.CharField(max_length=20, blank=True)

    # Employment
    department = models.ForeignKey(
        Department, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='employees',
    )
    position = models.ForeignKey(
        JobPosition, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='employees',
    )
    branch = models.ForeignKey(
        'inventory.Branch', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='employees',
    )
    employment_type = models.CharField(max_length=15, choices=EMPLOYMENT_TYPES, default='permanent')
    hire_date = models.DateField()
    end_date = models.DateField(null=True, blank=True,
                                help_text="Last day if terminated or contract ended")
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='active', db_index=True)

    # Compensation (monthly, TZS)
    basic_salary = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    housing_allowance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    transport_allowance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    other_allowances = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # Bank for salary deposit
    bank_name = models.CharField(max_length=80, blank=True)
    bank_branch = models.CharField(max_length=80, blank=True)
    bank_account = models.CharField(max_length=40, blank=True)

    photo = models.ImageField(upload_to='hr/photos/', blank=True, null=True)
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    history = HistoricalRecords()

    class Meta:
        ordering = ['employee_number']

    def __str__(self) -> str:
        return f"{self.employee_number} — {self.full_name}"

    @property
    def full_name(self) -> str:
        parts = [self.first_name, self.middle_name, self.last_name]
        return ' '.join(p for p in parts if p)

    @property
    def gross_salary(self) -> Decimal:
        return (self.basic_salary + self.housing_allowance
                + self.transport_allowance + self.other_allowances)


# ---------------------------------------------------------------------------
# Leave
# ---------------------------------------------------------------------------


class LeaveType(models.Model):
    name = models.CharField(max_length=60, unique=True)
    days_per_year = models.PositiveIntegerField(default=28)
    is_paid = models.BooleanField(default=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ['name']

    def __str__(self) -> str:
        return self.name


class LeaveRequest(models.Model):
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('cancelled', 'Cancelled'),
    )
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='leave_requests')
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT)
    start_date = models.DateField()
    end_date = models.DateField()
    days = models.PositiveIntegerField(default=0,
                                        help_text="Working days requested. Auto-computed on save.")
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='pending', db_index=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='approved_leaves',
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if self.start_date and self.end_date and not self.days:
            delta = (self.end_date - self.start_date).days + 1
            self.days = max(delta, 1)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.employee.full_name} — {self.leave_type.name} ({self.start_date} → {self.end_date})"


# ---------------------------------------------------------------------------
# Attendance
# ---------------------------------------------------------------------------


class AttendanceRecord(models.Model):
    STATUS_CHOICES = (
        ('present', 'Present'),
        ('absent', 'Absent'),
        ('late', 'Late'),
        ('leave', 'On Leave'),
        ('half_day', 'Half Day'),
        ('holiday', 'Holiday'),
    )
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='attendance')
    date = models.DateField(db_index=True)
    clock_in = models.TimeField(null=True, blank=True)
    clock_out = models.TimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='present')
    hours_worked = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', 'employee']
        unique_together = [('employee', 'date')]

    def __str__(self) -> str:
        return f"{self.employee.employee_number} · {self.date} · {self.status}"


# ---------------------------------------------------------------------------
# Payroll
# ---------------------------------------------------------------------------


class PayrollPeriod(models.Model):
    STATUS_CHOICES = (
        ('draft', 'Draft'),
        ('processed', 'Processed'),
        ('paid', 'Paid'),
        ('cancelled', 'Cancelled'),
    )
    month = models.PositiveSmallIntegerField()  # 1-12
    year = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default='draft')
    notes = models.TextField(blank=True)
    processed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='processed_payrolls',
    )
    processed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-year', '-month']
        unique_together = [('year', 'month')]

    def __str__(self) -> str:
        return f"Payroll {self.year}-{self.month:02d} ({self.status})"


def compute_tza_paye(taxable: Decimal) -> Decimal:
    """Compute monthly PAYE per Tanzania Revenue Authority brackets.

    Brackets as of FY 2024/2025 (subject to government change):
        0 – 270,000          : 0%
        270,001 – 520,000    : 8% of amount over 270,000
        520,001 – 760,000    : TZS 20,000 + 20% of amount over 520,000
        760,001 – 1,000,000  : TZS 68,000 + 25% of amount over 760,000
        Over 1,000,000       : TZS 128,000 + 30% of amount over 1,000,000
    """
    t = Decimal(taxable or 0)
    if t <= 270_000:
        return Decimal('0')
    if t <= 520_000:
        return (t - 270_000) * Decimal('0.08')
    if t <= 760_000:
        return Decimal('20000') + (t - 520_000) * Decimal('0.20')
    if t <= 1_000_000:
        return Decimal('68000') + (t - 760_000) * Decimal('0.25')
    return Decimal('128000') + (t - 1_000_000) * Decimal('0.30')


class Payslip(models.Model):
    period = models.ForeignKey(PayrollPeriod, on_delete=models.CASCADE, related_name='payslips')
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name='payslips')

    # Earnings snapshot
    basic_salary = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    housing_allowance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    transport_allowance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    other_allowances = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    overtime = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    bonus = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    gross = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # Employee deductions (statutory + voluntary)
    nssf_employee = models.DecimalField(max_digits=12, decimal_places=2, default=0,
                                         help_text="10% of gross")
    nhif = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    paye = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    heslb = models.DecimalField(max_digits=12, decimal_places=2, default=0,
                                 help_text="HESLB student loan repayment")
    other_deductions = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    advance_repayment = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_deductions = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # Employer contributions (not deducted from employee but tracked for reporting)
    nssf_employer = models.DecimalField(max_digits=12, decimal_places=2, default=0,
                                         help_text="10% of gross")
    wcf_employer = models.DecimalField(max_digits=12, decimal_places=2, default=0,
                                        help_text="0.5% of gross")
    sdl_employer = models.DecimalField(max_digits=12, decimal_places=2, default=0,
                                        help_text="4% of gross — payable when total employees > 4")

    net_pay = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-period__year', '-period__month', 'employee']
        unique_together = [('period', 'employee')]

    def __str__(self) -> str:
        return f"{self.employee.employee_number} · {self.period}"

    def recalculate(self) -> None:
        """Recompute every derived field from the earnings inputs.

        Call before save to ensure totals are consistent.
        """
        earnings = (self.basic_salary + self.housing_allowance
                    + self.transport_allowance + self.other_allowances
                    + self.overtime + self.bonus)
        self.gross = earnings
        # Statutory employee contributions
        self.nssf_employee = (earnings * Decimal('0.10')).quantize(Decimal('0.01'))
        # PAYE is computed on (gross - NSSF) which is the typical TRA taxable income
        taxable = earnings - self.nssf_employee
        self.paye = compute_tza_paye(taxable).quantize(Decimal('0.01'))
        # Employer side
        self.nssf_employer = (earnings * Decimal('0.10')).quantize(Decimal('0.01'))
        self.wcf_employer = (earnings * Decimal('0.005')).quantize(Decimal('0.01'))
        self.sdl_employer = (earnings * Decimal('0.04')).quantize(Decimal('0.01'))
        # Totals
        self.total_deductions = (self.nssf_employee + self.nhif + self.paye
                                 + self.heslb + self.other_deductions
                                 + self.advance_repayment)
        self.net_pay = earnings - self.total_deductions

    def save(self, *args, **kwargs):
        self.recalculate()
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


class EmployeeDocument(models.Model):
    KIND_CHOICES = (
        ('contract', 'Contract'),
        ('id', 'ID / NIDA'),
        ('cv', 'CV / Resume'),
        ('certificate', 'Certificate'),
        ('letter', 'Letter'),
        ('other', 'Other'),
    )
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='documents')
    kind = models.CharField(max_length=15, choices=KIND_CHOICES, default='other')
    title = models.CharField(max_length=160)
    file = models.FileField(upload_to='hr/documents/')
    issued_on = models.DateField(null=True, blank=True)
    expires_on = models.DateField(null=True, blank=True,
                                   help_text="For contracts, visas, certificates that expire")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+',
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-uploaded_at']

    def __str__(self) -> str:
        return f"{self.employee.employee_number} · {self.title}"


# ---------------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------------


class PerformanceReview(models.Model):
    RATING_CHOICES = (
        (1, 'Needs improvement'),
        (2, 'Below expectations'),
        (3, 'Meets expectations'),
        (4, 'Exceeds expectations'),
        (5, 'Outstanding'),
    )
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='reviews')
    period_start = models.DateField()
    period_end = models.DateField()
    rating = models.PositiveSmallIntegerField(choices=RATING_CHOICES, default=3)
    strengths = models.TextField(blank=True)
    areas_for_improvement = models.TextField(blank=True)
    goals = models.TextField(blank=True)
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='conducted_reviews',
    )
    acknowledged = models.BooleanField(default=False,
                                        help_text="Set True once the employee has signed off")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-period_end']

    def __str__(self) -> str:
        return f"{self.employee.employee_number} · {self.period_start} → {self.period_end}"


# ---------------------------------------------------------------------------
# Disciplinary
# ---------------------------------------------------------------------------


class DisciplinaryAction(models.Model):
    ACTION_CHOICES = (
        ('verbal_warning', 'Verbal warning'),
        ('written_warning', 'Written warning'),
        ('final_warning', 'Final warning'),
        ('suspension', 'Suspension'),
        ('termination', 'Termination'),
    )
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='disciplinary_actions')
    action_type = models.CharField(max_length=20, choices=ACTION_CHOICES)
    date = models.DateField()
    reason = models.TextField()
    details = models.TextField(blank=True)
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='disciplinary_issued',
    )
    suspension_end = models.DateField(null=True, blank=True,
                                       help_text="Required when action_type=suspension")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date']

    def __str__(self) -> str:
        return f"{self.employee.employee_number} · {self.action_type} · {self.date}"
