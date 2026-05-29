from django.contrib.auth.mixins import LoginRequiredMixin
from django.utils import timezone
from django.views.generic import TemplateView
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import (
    AttendanceRecord, Department, DisciplinaryAction, Employee, EmployeeDocument,
    JobPosition, LeaveRequest, LeaveType, PayrollPeriod, Payslip, PerformanceReview,
)
from .serializers import (
    AttendanceRecordSerializer, DepartmentSerializer, DisciplinaryActionSerializer,
    EmployeeDocumentSerializer, EmployeeSerializer, JobPositionSerializer,
    LeaveRequestSerializer, LeaveTypeSerializer, PayrollPeriodSerializer,
    PayslipSerializer, PerformanceReviewSerializer,
)


class StrictDjangoModelPermissions(permissions.DjangoModelPermissions):
    """Like DjangoModelPermissions but ALSO requires the `view` permission for
    reads. Stock DRF leaves GET/HEAD open to any authenticated user, which
    would expose salaries, payslips and disciplinary records to every login.
    HR data must be readable only by users with the explicit hr.view_* perm.
    """
    perms_map = {
        'GET': ['%(app_label)s.view_%(model_name)s'],
        'OPTIONS': [],
        'HEAD': ['%(app_label)s.view_%(model_name)s'],
        'POST': ['%(app_label)s.add_%(model_name)s'],
        'PUT': ['%(app_label)s.change_%(model_name)s'],
        'PATCH': ['%(app_label)s.change_%(model_name)s'],
        'DELETE': ['%(app_label)s.delete_%(model_name)s'],
    }


# ---------------------------------------------------------------------------
# ViewSets
# ---------------------------------------------------------------------------


class DepartmentViewSet(viewsets.ModelViewSet):
    queryset = Department.objects.all()
    serializer_class = DepartmentSerializer
    permission_classes = [StrictDjangoModelPermissions]


class JobPositionViewSet(viewsets.ModelViewSet):
    queryset = JobPosition.objects.select_related('department').all()
    serializer_class = JobPositionSerializer
    permission_classes = [StrictDjangoModelPermissions]


class EmployeeViewSet(viewsets.ModelViewSet):
    queryset = Employee.objects.select_related('department', 'position', 'branch', 'user').all()
    serializer_class = EmployeeSerializer
    permission_classes = [StrictDjangoModelPermissions]

    def get_queryset(self):
        qs = super().get_queryset()
        status_param = self.request.query_params.get('status')
        dept = self.request.query_params.get('department')
        branch = self.request.query_params.get('branch')
        search = self.request.query_params.get('search')
        if status_param:
            qs = qs.filter(status=status_param)
        if dept:
            qs = qs.filter(department_id=dept)
        if branch:
            qs = qs.filter(branch_id=branch)
        if search:
            qs = qs.filter(employee_number__icontains=search) | qs.filter(
                first_name__icontains=search) | qs.filter(last_name__icontains=search)
        return qs


class LeaveTypeViewSet(viewsets.ModelViewSet):
    queryset = LeaveType.objects.all()
    serializer_class = LeaveTypeSerializer
    permission_classes = [StrictDjangoModelPermissions]


class LeaveRequestViewSet(viewsets.ModelViewSet):
    queryset = LeaveRequest.objects.select_related('employee', 'leave_type', 'approved_by').all()
    serializer_class = LeaveRequestSerializer
    permission_classes = [StrictDjangoModelPermissions]

    @action(detail=True, methods=['POST'])
    def approve(self, request, pk=None):
        leave = self.get_object()
        if leave.status != 'pending':
            return Response({'error': 'Only pending requests can be approved'},
                            status=status.HTTP_400_BAD_REQUEST)
        leave.status = 'approved'
        leave.approved_by = request.user
        leave.approved_at = timezone.now()
        leave.decision_note = request.data.get('note', '')
        leave.save()
        # Flip the employee status to on_leave during the leave window if it starts today
        if leave.start_date <= timezone.now().date() <= leave.end_date:
            leave.employee.status = 'on_leave'
            leave.employee.save(update_fields=['status'])
        return Response(LeaveRequestSerializer(leave).data)

    @action(detail=True, methods=['POST'])
    def reject(self, request, pk=None):
        leave = self.get_object()
        if leave.status != 'pending':
            return Response({'error': 'Only pending requests can be rejected'},
                            status=status.HTTP_400_BAD_REQUEST)
        leave.status = 'rejected'
        leave.approved_by = request.user
        leave.approved_at = timezone.now()
        leave.decision_note = request.data.get('note', '')
        leave.save()
        return Response(LeaveRequestSerializer(leave).data)


class AttendanceRecordViewSet(viewsets.ModelViewSet):
    queryset = AttendanceRecord.objects.select_related('employee').all()
    serializer_class = AttendanceRecordSerializer
    permission_classes = [StrictDjangoModelPermissions]

    # Self-service actions are available to ANY authenticated employee; the
    # HR-wide list/create/update/delete still require hr.* model permissions.
    SELF_SERVICE_ACTIONS = {'clock_in', 'clock_out', 'my_today', 'my_history'}

    def get_permissions(self):
        if getattr(self, 'action', None) in self.SELF_SERVICE_ACTIONS:
            return [permissions.IsAuthenticated()]
        return super().get_permissions()

    def _resolve_self_employee(self, request) -> 'Employee | None':
        return getattr(request.user, 'employee', None)

    @action(detail=False, methods=['GET'])
    def my_today(self, request):
        """Return the calling user's attendance record for today (or null)."""
        emp = self._resolve_self_employee(request)
        if not emp:
            return Response({'error': 'No employee record linked to this user'},
                            status=status.HTTP_404_NOT_FOUND)
        today = timezone.localdate()
        rec = AttendanceRecord.objects.filter(employee=emp, date=today).first()
        return Response({
            'employee': {'id': emp.id, 'name': emp.full_name,
                         'number': emp.employee_number},
            'record': AttendanceRecordSerializer(rec).data if rec else None,
            'date': today,
        })

    @action(detail=False, methods=['GET'])
    def my_history(self, request):
        """Calling user's last N days of attendance (default 14)."""
        emp = self._resolve_self_employee(request)
        if not emp:
            return Response({'error': 'No employee record linked to this user'},
                            status=status.HTTP_404_NOT_FOUND)
        try:
            days = max(1, min(int(request.query_params.get('days', 14)), 60))
        except ValueError:
            days = 14
        cutoff = timezone.localdate() - timezone.timedelta(days=days)
        qs = AttendanceRecord.objects.filter(employee=emp, date__gte=cutoff).order_by('-date')
        return Response(AttendanceRecordSerializer(qs, many=True).data)

    @action(detail=False, methods=['POST'])
    def clock_in(self, request):
        emp = self._resolve_self_employee(request)
        if not emp:
            return Response({'error': 'No employee record linked to this user'},
                            status=status.HTTP_404_NOT_FOUND)
        now = timezone.localtime()
        today = now.date()
        rec, created = AttendanceRecord.objects.get_or_create(
            employee=emp, date=today, defaults={'status': 'present', 'clock_in': now.time()},
        )
        if not created and rec.clock_in:
            return Response({'error': 'Already clocked in today',
                             'record': AttendanceRecordSerializer(rec).data},
                            status=status.HTTP_400_BAD_REQUEST)
        # Mark late if past 09:00 local (configurable later)
        late_cutoff = now.replace(hour=9, minute=0, second=0, microsecond=0)
        rec.clock_in = now.time()
        rec.status = 'late' if now > late_cutoff else 'present'
        rec.save()
        return Response(AttendanceRecordSerializer(rec).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['POST'])
    def clock_out(self, request):
        emp = self._resolve_self_employee(request)
        if not emp:
            return Response({'error': 'No employee record linked to this user'},
                            status=status.HTTP_404_NOT_FOUND)
        now = timezone.localtime()
        today = now.date()
        rec = AttendanceRecord.objects.filter(employee=emp, date=today).first()
        if not rec or not rec.clock_in:
            return Response({'error': "You haven't clocked in today yet"},
                            status=status.HTTP_400_BAD_REQUEST)
        if rec.clock_out:
            return Response({'error': 'Already clocked out today',
                             'record': AttendanceRecordSerializer(rec).data},
                            status=status.HTTP_400_BAD_REQUEST)
        rec.clock_out = now.time()
        # Compute hours worked from same-date clock_in -> clock_out
        from datetime import datetime as _dt, timedelta as _td
        start = _dt.combine(today, rec.clock_in)
        end = _dt.combine(today, rec.clock_out)
        if end < start:
            end += _td(days=1)  # shouldn't happen for same-day but defensive
        hours = (end - start).total_seconds() / 3600.0
        rec.hours_worked = round(hours, 2)
        rec.save()
        return Response(AttendanceRecordSerializer(rec).data)


class PayrollPeriodViewSet(viewsets.ModelViewSet):
    queryset = PayrollPeriod.objects.all()
    serializer_class = PayrollPeriodSerializer
    permission_classes = [StrictDjangoModelPermissions]

    @action(detail=True, methods=['POST'])
    def generate_payslips(self, request, pk=None):
        """Generate a Payslip for every active employee in this period.

        Idempotent: if a payslip already exists for (period, employee), it's
        recomputed in place rather than duplicated.
        """
        period = self.get_object()
        if period.status not in ('draft', 'processed'):
            return Response({'error': 'Can only generate while period is draft or processed'},
                            status=status.HTTP_400_BAD_REQUEST)

        active = Employee.objects.filter(status='active')
        created, updated = 0, 0
        for emp in active:
            slip, was_created = Payslip.objects.get_or_create(period=period, employee=emp)
            slip.basic_salary = emp.basic_salary
            slip.housing_allowance = emp.housing_allowance
            slip.transport_allowance = emp.transport_allowance
            slip.other_allowances = emp.other_allowances
            slip.save()  # recalculate fires here
            if was_created:
                created += 1
            else:
                updated += 1

        period.status = 'processed'
        period.processed_by = request.user
        period.processed_at = timezone.now()
        period.save()
        return Response({
            'created': created,
            'updated': updated,
            'total': created + updated,
            'status': period.status,
        })

    @action(detail=True, methods=['POST'])
    def mark_paid(self, request, pk=None):
        period = self.get_object()
        if period.status != 'processed':
            return Response({'error': 'Period must be Processed before marking Paid'},
                            status=status.HTTP_400_BAD_REQUEST)
        period.status = 'paid'
        period.save()
        return Response(PayrollPeriodSerializer(period).data)


class PayslipViewSet(viewsets.ModelViewSet):
    queryset = Payslip.objects.select_related('period', 'employee').all()
    serializer_class = PayslipSerializer
    permission_classes = [StrictDjangoModelPermissions]


class EmployeeDocumentViewSet(viewsets.ModelViewSet):
    queryset = EmployeeDocument.objects.select_related('employee').all()
    serializer_class = EmployeeDocumentSerializer
    permission_classes = [StrictDjangoModelPermissions]

    def perform_create(self, serializer):
        serializer.save(uploaded_by=self.request.user)


class PerformanceReviewViewSet(viewsets.ModelViewSet):
    queryset = PerformanceReview.objects.select_related('employee', 'reviewer').all()
    serializer_class = PerformanceReviewSerializer
    permission_classes = [StrictDjangoModelPermissions]

    def perform_create(self, serializer):
        serializer.save(reviewer=self.request.user)


class DisciplinaryActionViewSet(viewsets.ModelViewSet):
    queryset = DisciplinaryAction.objects.select_related('employee', 'issued_by').all()
    serializer_class = DisciplinaryActionSerializer
    permission_classes = [StrictDjangoModelPermissions]

    def perform_create(self, serializer):
        serializer.save(issued_by=self.request.user)


# ---------------------------------------------------------------------------
# Template views
# ---------------------------------------------------------------------------


class HrDashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'hr/dashboard.html'


class EmployeeListView(LoginRequiredMixin, TemplateView):
    template_name = 'hr/employee_list.html'


class EmployeeCreateView(LoginRequiredMixin, TemplateView):
    template_name = 'hr/employee_form.html'


class DepartmentListView(LoginRequiredMixin, TemplateView):
    template_name = 'hr/department_list.html'


class LeaveRequestListView(LoginRequiredMixin, TemplateView):
    template_name = 'hr/leave_list.html'


class AttendanceListView(LoginRequiredMixin, TemplateView):
    template_name = 'hr/attendance_list.html'


class MyAttendanceView(LoginRequiredMixin, TemplateView):
    template_name = 'hr/my_attendance.html'


class PayrollListView(LoginRequiredMixin, TemplateView):
    template_name = 'hr/payroll_list.html'


class PayrollDetailView(LoginRequiredMixin, TemplateView):
    template_name = 'hr/payroll_detail.html'


class PerformanceListView(LoginRequiredMixin, TemplateView):
    template_name = 'hr/performance_list.html'


class DisciplinaryListView(LoginRequiredMixin, TemplateView):
    template_name = 'hr/disciplinary_list.html'
