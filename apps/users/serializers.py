from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission, Group

User = get_user_model()

# Group name -> role code. A user can hold several groups (roles); the single
# `role` field tracks the *primary* (first) one for display and the few places
# that read it directly. Group names mirror the ROLE_CHOICES labels exactly
# (see users/management/commands/create_roles.py), so we invert that here.
_ROLE_LABEL_TO_CODE = {label: code for code, label in User.ROLE_CHOICES}


class UserSerializer(serializers.ModelSerializer):
    groups = serializers.PrimaryKeyRelatedField(many=True, queryset=Group.objects.all(), required=False)
    password = serializers.CharField(write_only=True, required=False)
    role_name = serializers.SerializerMethodField(read_only=True)
    role_names = serializers.SerializerMethodField(read_only=True)
    branch_name = serializers.CharField(source='branch.name', read_only=True)
    full_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ('id', 'username', 'first_name', 'last_name', 'full_name', 'email', 'role', 'groups', 'branch', 'date_joined', 'password', 'role_name', 'role_names', 'branch_name')
        read_only_fields = ('role', 'date_joined', 'role_name', 'role_names', 'branch_name')

    def get_full_name(self, obj):
        return obj.get_full_name()

    def get_role_names(self, obj):
        """All roles assigned to the user (one badge per group)."""
        return list(obj.groups.values_list('name', flat=True))

    def get_role_name(self, obj):
        """Primary role label for compact displays; falls back to the role field."""
        names = self.get_role_names(obj)
        if names:
            return ', '.join(names)
        return obj.get_role_display()

    def _sync_primary_role(self, user):
        """Keep the single `role` field aligned with the first assigned group so
        get_role_display(), the navbar and dashboard role checks stay accurate."""
        first = user.groups.first()
        code = _ROLE_LABEL_TO_CODE.get(first.name) if first else 'staff'
        if code and user.role != code:
            user.role = code
            user.save(update_fields=['role'])

    def create(self, validated_data):
        password = validated_data.pop('password', None)
        groups = validated_data.pop('groups', [])
        user = User.objects.create(**validated_data)
        if password:
            user.set_password(password)
        if groups:
            user.groups.set(groups)
        user.save()
        self._sync_primary_role(user)
        return user

    def update(self, instance, validated_data):
        if 'password' in validated_data:
            password = validated_data.pop('password')
            instance.set_password(password)

        groups_changed = 'groups' in validated_data
        if groups_changed:
            groups = validated_data.pop('groups')
            instance.groups.set(groups)

        user = super().update(instance, validated_data)
        if groups_changed:
            self._sync_primary_role(user)
        return user

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
