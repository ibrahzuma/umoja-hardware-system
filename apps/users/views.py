from rest_framework import serializers, viewsets, permissions
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission, Group
from django.views.generic import TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin
from rest_framework.response import Response
from rest_framework.decorators import action
from django.db.models import Q

User = get_user_model()

from .serializers import UserSerializer, GroupSerializer, PermissionSerializer

class UserListView(LoginRequiredMixin, TemplateView):
    template_name = 'users/user_list.html'

class UserCreateView(LoginRequiredMixin, TemplateView):
    template_name = 'users/user_create.html'

class UserEditView(LoginRequiredMixin, TemplateView):
    template_name = 'users/user_edit.html'

class RecentUserListView(LoginRequiredMixin, TemplateView):
    template_name = 'users/recent_users.html'

class RoleListView(LoginRequiredMixin, TemplateView):
    template_name = 'users/role_list.html'

class RoleCreateView(LoginRequiredMixin, TemplateView):
    template_name = 'users/role_create.html'

class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all()

    def get_queryset(self):
        queryset = User.objects.all()
        role = self.request.query_params.get('role')
        if role:
            queryset = queryset.filter(
                Q(role__iexact=role) | 
                Q(groups__name__iexact=role) |
                Q(groups__name__iexact=role.replace('_', ' '))
            ).distinct()
        return queryset
    serializer_class = UserSerializer
    permission_classes = [permissions.DjangoModelPermissions]

    def perform_destroy(self, instance):
        from rest_framework.exceptions import ValidationError
        if instance.is_superuser:
            raise ValidationError("Cannot delete a superuser account.")
        instance.delete()

class GroupViewSet(viewsets.ModelViewSet):
    queryset = Group.objects.all()
    serializer_class = GroupSerializer
    permission_classes = [permissions.DjangoModelPermissions]

    def get_queryset(self):
        return Group.objects.exclude(name__iexact='admin')

    def perform_destroy(self, instance):
        from rest_framework.exceptions import ValidationError
        if instance.name.lower() == 'admin':
            raise ValidationError("Cannot delete the 'Admin' role directly.")
        instance.delete()

class PermissionViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Permission.objects.filter(
        content_type__app_label__in=['inventory', 'sales', 'finance', 'users']
    ).select_related('content_type').order_by('content_type__app_label', 'content_type__model')
    serializer_class = PermissionSerializer
    permission_classes = [permissions.IsAdminUser]
    pagination_class = None # Show all for selection
