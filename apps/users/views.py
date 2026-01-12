from rest_framework import serializers, viewsets, permissions
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission, Group
from django.views.generic import TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin
from rest_framework.response import Response
from rest_framework.decorators import action
from django.db.models import Q

User = get_user_model()

class UserSerializer(serializers.ModelSerializer):
    groups = serializers.PrimaryKeyRelatedField(many=True, queryset=Group.objects.all(), required=False)
    password = serializers.CharField(write_only=True)
    role_name = serializers.CharField(source='groups.first.name', read_only=True)
    branch_name = serializers.CharField(source='branch.name', read_only=True)
    full_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ('id', 'username', 'first_name', 'last_name', 'full_name', 'email', 'role', 'groups', 'branch', 'date_joined', 'password', 'role_name', 'branch_name')
        read_only_fields = ('role', 'date_joined', 'role_name', 'branch_name')

    def get_full_name(self, obj):
        return obj.get_full_name()

    def create(self, validated_data):
        password = validated_data.pop('password')
        groups = validated_data.pop('groups', [])
        user = User.objects.create(**validated_data)
        user.set_password(password)
        if groups:
            user.groups.set(groups)
        user.save()
        return user

    def update(self, instance, validated_data):
        if 'password' in validated_data:
            password = validated_data.pop('password')
            instance.set_password(password)
        
        if 'groups' in validated_data:
            groups = validated_data.pop('groups')
            instance.groups.set(groups)
            
        return super().update(instance, validated_data)

class GroupSerializer(serializers.ModelSerializer):
    permissions_details = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Group
        fields = ('id', 'name', 'permissions', 'permissions_details')

    def get_permissions_details(self, obj):
        return [{'id': p.id, 'name': p.name, 'codename': p.codename} for p in obj.permissions.all()]

class PermissionSerializer(serializers.ModelSerializer):
    app_label = serializers.CharField(source='content_type.app_label', read_only=True)
    model = serializers.CharField(source='content_type.model', read_only=True)

    class Meta:
        model = Permission
        fields = ('id', 'name', 'codename', 'app_label', 'model')

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
