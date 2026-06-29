from rest_framework import permissions

class IsAdminOrSuperUser(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and (request.user.is_superuser or request.user.is_admin_role)

class IsManager(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and (request.user.is_superuser or request.user.is_manager)

class IsAccountant(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and (request.user.is_superuser or request.user.is_accountant)

class IsStoreManager(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and (request.user.is_superuser or request.user.is_store_manager)

class IsSales(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and (request.user.is_superuser or request.user.is_sales_rep or request.user.is_manager)

class IsStockController(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and (request.user.is_superuser or request.user.is_stock_controller)

class IsAfisaUgavi(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and (request.user.is_superuser or request.user.is_procurement_officer)

class IsStoreKeeper(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and (request.user.is_superuser or request.user.is_store_keeper)

class CanManageFleet(permissions.BasePermission):
    """Roles allowed to manage the truck fleet and allocations. The truck pages
    appear in both the Afisa Ugavi (procurement) and Store Manager workspaces,
    and admins/managers oversee everything — so all of them may add/allocate."""
    def has_permission(self, request, view):
        u = request.user
        return u.is_authenticated and (
            u.is_superuser or u.is_admin_role or u.is_manager
            or u.is_store_manager or u.is_procurement_officer
        )
