from django.contrib import admin

from .models import (
    AttendanceRecord, Department, DisciplinaryAction, Employee, EmployeeDocument,
    JobPosition, LeaveRequest, LeaveType, PayrollPeriod, Payslip, PerformanceReview,
)


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ('name', 'head', 'created_at')
    search_fields = ('name',)


@admin.register(JobPosition)
class JobPositionAdmin(admin.ModelAdmin):
    list_display = ('title', 'department', 'min_salary', 'max_salary')
    list_filter = ('department',)
    search_fields = ('title',)


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ('employee_number', 'full_name', 'department', 'position',
                    'branch', 'status', 'hire_date')
    list_filter = ('status', 'department', 'branch', 'employment_type')
    search_fields = ('employee_number', 'first_name', 'last_name', 'nida_number')
    raw_id_fields = ('user',)
    readonly_fields = ('created_at', 'updated_at')


@admin.register(LeaveType)
class LeaveTypeAdmin(admin.ModelAdmin):
    list_display = ('name', 'days_per_year', 'is_paid')


@admin.register(LeaveRequest)
class LeaveRequestAdmin(admin.ModelAdmin):
    list_display = ('employee', 'leave_type', 'start_date', 'end_date', 'days', 'status')
    list_filter = ('status', 'leave_type')
    search_fields = ('employee__first_name', 'employee__last_name')
    readonly_fields = ('days', 'approved_by', 'approved_at')


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    list_display = ('employee', 'date', 'status', 'clock_in', 'clock_out', 'hours_worked')
    list_filter = ('status', 'date')
    search_fields = ('employee__first_name', 'employee__last_name')


@admin.register(PayrollPeriod)
class PayrollPeriodAdmin(admin.ModelAdmin):
    list_display = ('year', 'month', 'status', 'processed_at', 'processed_by')
    list_filter = ('status', 'year')


@admin.register(Payslip)
class PayslipAdmin(admin.ModelAdmin):
    list_display = ('employee', 'period', 'gross', 'paye', 'nssf_employee',
                    'total_deductions', 'net_pay')
    list_filter = ('period__year', 'period__month')
    search_fields = ('employee__first_name', 'employee__last_name', 'employee__employee_number')
    readonly_fields = ('gross', 'nssf_employee', 'nssf_employer', 'wcf_employer',
                       'sdl_employer', 'paye', 'total_deductions', 'net_pay')


@admin.register(EmployeeDocument)
class EmployeeDocumentAdmin(admin.ModelAdmin):
    list_display = ('employee', 'title', 'kind', 'issued_on', 'expires_on', 'uploaded_at')
    list_filter = ('kind',)


@admin.register(PerformanceReview)
class PerformanceReviewAdmin(admin.ModelAdmin):
    list_display = ('employee', 'period_start', 'period_end', 'rating', 'acknowledged')
    list_filter = ('rating', 'acknowledged')


@admin.register(DisciplinaryAction)
class DisciplinaryActionAdmin(admin.ModelAdmin):
    list_display = ('employee', 'action_type', 'date', 'issued_by')
    list_filter = ('action_type',)
