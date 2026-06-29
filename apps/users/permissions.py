from rest_framework import permissions


def is_privileged(user):
    """Top-level access. Django superusers and the in-app 'admin' role can use
    every endpoint — they oversee the whole system. Keep this the single source
    of truth so individual role permissions don't each re-implement it."""
    return bool(
        user and user.is_authenticated
        and (user.is_superuser or getattr(user, 'is_admin_role', False))
    )


class _RolePermission(permissions.BasePermission):
    """Base for role-scoped API access.

    A request is allowed when the user is privileged (superuser/admin) OR has
    any of the User boolean role-properties named in `allowed`. Subclasses only
    declare `allowed`, so admins never get locked out of a specialist endpoint.
    """
    allowed = ()

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if is_privileged(user):
            return True
        return any(getattr(user, prop, False) for prop in self.allowed)


class _ReadAnyWriteRole(_RolePermission):
    """Any authenticated user may read; only privileged users or the listed
    roles may write. For shared, non-sensitive resources that several
    workspaces need to *see* but only some may edit (mirrors how DRF's
    DjangoModelPermissions already leaves GET open across the app)."""
    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        return super().has_permission(request, view)


class IsAdminOrSuperUser(_RolePermission):
    allowed = ()


class IsManager(_RolePermission):
    allowed = ('is_manager',)


class IsAccountant(_RolePermission):
    allowed = ('is_accountant',)


class IsStoreManager(_RolePermission):
    allowed = ('is_store_manager',)


class IsSales(_RolePermission):
    allowed = ('is_sales_rep', 'is_manager')


class IsStockController(_RolePermission):
    allowed = ('is_stock_controller',)


class IsAfisaUgavi(_RolePermission):
    allowed = ('is_procurement_officer',)


class IsStoreKeeper(_RolePermission):
    allowed = ('is_store_keeper',)


class CanManageFleet(_RolePermission):
    """Truck pages live in both the Afisa Ugavi and Store Manager workspaces;
    managers oversee operations — so all of them may add/allocate trucks."""
    allowed = ('is_manager', 'is_store_manager', 'is_procurement_officer')


class CanHandleGRN(_RolePermission):
    """Goods Received Notes: stock controllers raise them, store keepers verify
    them — both workspaces link to the GRN screens."""
    allowed = ('is_stock_controller', 'is_store_keeper')


class CanRecordSupplierPayment(_RolePermission):
    """Supplier payments appear in both the Accountant and the Afisa Ugavi
    (procurement) workspaces, so either role may record them."""
    allowed = ('is_accountant', 'is_procurement_officer')


class CanManagePurchaseOrders(_ReadAnyWriteRole):
    """Afisa Ugavi (and admins) create/edit purchase orders; receiving roles
    (stock controller, store keeper) must be able to read them to raise GRNs."""
    allowed = ('is_procurement_officer',)


class CanManageVehicles(_ReadAnyWriteRole):
    """Sales reps and managers manage the delivery fleet; store keepers and
    other dispatch staff need to read vehicles for loading/offloading."""
    allowed = ('is_sales_rep', 'is_manager', 'is_store_manager')


class CanApproveSales(_RolePermission):
    """Approving/declining pending sales orders is a supervisory action: the
    Sales & Credit Manager (is_sales_manager) and general managers, plus
    admins. Deliberately NOT sales reps, who raise the orders."""
    allowed = ('is_sales_manager', 'is_manager')
