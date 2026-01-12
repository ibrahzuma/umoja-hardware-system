from rest_framework import permissions

class IsStoreManager(permissions.BasePermission):
    """
    Allocates permissions to Store Managers and Superusers.
    """
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        return request.user.is_superuser or getattr(request.user, 'is_manager', False)

class IsSalesManager(permissions.BasePermission):
    """
    Allocates permissions to Sales Managers and Superusers.
    """
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        return request.user.is_superuser or getattr(request.user, 'is_sales_manager', False)

class IsAdminRole(permissions.BasePermission):
    """
    Allocates permissions to Admins and Superusers.
    """
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        return request.user.is_superuser or getattr(request.user, 'is_admin_role', False)
