from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission, Group

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
        password = validated_data.pop('password', None)
        groups = validated_data.pop('groups', [])
        user = User.objects.create(**validated_data)
        if password:
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
