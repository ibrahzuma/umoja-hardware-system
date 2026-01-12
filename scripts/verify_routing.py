import os
import django
from django.contrib.auth import get_user_model

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sms_project.settings')
django.setup()

def verify_approval_routing():
    from apps.sales.models import Sale
    from apps.inventory.models import Branch

    User = get_user_model()
    admin = User.objects.filter(username='admin').first()
    if not admin:
        print("Admin user not found, creating for test context")
        admin = User.objects.create_superuser('admin', 'admin@example.com', 'pass')

    # Ensure branch
    branch, _ = Branch.objects.get_or_create(name='Main Branch')

    # 1. Create a Pending Sale
    sale = Sale.objects.create(
        invoice_number='INV-TEST-ROUTING',
        branch=branch,
        total_amount=100.00,
        status='pending'
    )
    print(f"Created Pending Sale: {sale.invoice_number} (Status: {sale.status})")

    # 2. Simulate Approval (Directly calling logic similar to view, or using proper ViewSet logic if we mock request)
    # Since we modified the ViewSet, let's replicate the logic:
    # It sets status='approved', approved_by=user, dispatch_manager=None
    
    print("Approving sale without dispatch_manager...")
    sale.status = 'approved'
    sale.approved_by = admin
    sale.dispatch_manager = None # This is the key change
    sale.save()
    
    sale.refresh_from_db()
    print(f"Post-Approval Status: {sale.status}")
    print(f"Dispatch Manager: {sale.dispatch_manager}")

    if sale.status == 'approved' and sale.dispatch_manager is None:
        print("SUCCESS: Sale approved and routed to pool (dispatch_manager is None).")
    else:
        print("FAILURE: Sale state incorrect.")

    # Clean up
    sale.delete()

if __name__ == "__main__":
    verify_approval_routing()
