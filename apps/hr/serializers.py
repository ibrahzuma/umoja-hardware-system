from rest_framework import serializers

from .models import (
    AttendanceRecord, Department, DisciplinaryAction, Employee, EmployeeDocument,
    JobPosition, LeaveRequest, LeaveType, PayrollPeriod, Payslip, PerformanceReview,
)


class DepartmentSerializer(serializers.ModelSerializer):
    head_name = serializers.CharField(source='head.full_name', read_only=True)
    employee_count = serializers.IntegerField(source='employees.count', read_only=True)

    class Meta:
        model = Department
        fields = '__all__'


class JobPositionSerializer(serializers.ModelSerializer):
    department_name = serializers.CharField(source='department.name', read_only=True, default='')

    class Meta:
        model = JobPosition
        fields = '__all__'


class EmployeeSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)
    gross_salary = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    department_name = serializers.CharField(source='department.name', read_only=True, default='')
    position_title = serializers.CharField(source='position.title', read_only=True, default='')
    branch_name = serializers.CharField(source='branch.name', read_only=True, default='')

    class Meta:
        model = Employee
        fields = '__all__'


class LeaveTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = LeaveType
        fields = '__all__'


class LeaveRequestSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.full_name', read_only=True)
    leave_type_name = serializers.CharField(source='leave_type.name', read_only=True)
    approved_by_name = serializers.CharField(source='approved_by.username', read_only=True, default='')

    class Meta:
        model = LeaveRequest
        fields = '__all__'
        read_only_fields = ('days', 'approved_by', 'approved_at')


class AttendanceRecordSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.full_name', read_only=True)

    class Meta:
        model = AttendanceRecord
        fields = '__all__'


class PayrollPeriodSerializer(serializers.ModelSerializer):
    payslip_count = serializers.IntegerField(source='payslips.count', read_only=True)

    class Meta:
        model = PayrollPeriod
        fields = '__all__'
        read_only_fields = ('processed_by', 'processed_at')


class PayslipSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.full_name', read_only=True)
    period_label = serializers.SerializerMethodField()

    class Meta:
        model = Payslip
        fields = '__all__'
        read_only_fields = (
            'gross', 'nssf_employee', 'paye', 'nssf_employer',
            'wcf_employer', 'sdl_employer', 'total_deductions', 'net_pay',
        )

    def get_period_label(self, obj):
        return f"{obj.period.year}-{obj.period.month:02d}"


class EmployeeDocumentSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.full_name', read_only=True)

    class Meta:
        model = EmployeeDocument
        fields = '__all__'
        read_only_fields = ('uploaded_by', 'uploaded_at')


class PerformanceReviewSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.full_name', read_only=True)
    reviewer_name = serializers.CharField(source='reviewer.username', read_only=True, default='')
    rating_label = serializers.CharField(source='get_rating_display', read_only=True)

    class Meta:
        model = PerformanceReview
        fields = '__all__'


class DisciplinaryActionSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.full_name', read_only=True)
    action_label = serializers.CharField(source='get_action_type_display', read_only=True)

    class Meta:
        model = DisciplinaryAction
        fields = '__all__'
