from django.urls import path

from . import views

app_name = 'hr'

urlpatterns = [
    path('', views.HrDashboardView.as_view(), name='dashboard'),
    path('employees/', views.EmployeeListView.as_view(), name='employee_list'),
    path('employees/create/', views.EmployeeCreateView.as_view(), name='employee_create'),
    path('departments/', views.DepartmentListView.as_view(), name='department_list'),
    path('leave/', views.LeaveRequestListView.as_view(), name='leave_list'),
    path('attendance/', views.AttendanceListView.as_view(), name='attendance_list'),
    path('my-attendance/', views.MyAttendanceView.as_view(), name='my_attendance'),
    path('payroll/', views.PayrollListView.as_view(), name='payroll_list'),
    path('payroll/<int:pk>/', views.PayrollDetailView.as_view(), name='payroll_detail'),
    path('performance/', views.PerformanceListView.as_view(), name='performance_list'),
    path('discipline/', views.DisciplinaryListView.as_view(), name='disciplinary_list'),
]
