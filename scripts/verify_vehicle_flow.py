import os
import django
import sys
from decimal import Decimal

# Setup Django environment
sys.path.append('C:\\Users\\admin\\Downloads\\Umoja Hadware System')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sms_project.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.sales.models import Sale, SaleItem, Vehicle
from apps.inventory.models import Product, Branch, Category
from rest_framework.test import APIClient

def verify_vehicle_flow():
    User = get_user_model()
    admin = User.objects.get(username='admin')
    
    # 1. Create a Vehicle
    print("Creating or Getting Vehicle...")
    vehicle, created = Vehicle.objects.get_or_create(
        registration_number='T 777 TES',
        defaults={
            'driver_name': 'Test Driver',
            'vehicle_type': 'lorry',
            'status': 'active'
        }
    )
    print(f"Vehicle: {vehicle}")

    # 2. Create a Sale
    print("Creating Sale...")
    branch = Branch.objects.first()
    product = Product.objects.first()
    
    if not branch or not product:
        print("Error: No branch or product found.")
        return

    import uuid
    sale = Sale.objects.create(
        branch=branch,
        user=admin,
        status='pending',
        total_amount=Decimal('1000.00'),
        invoice_number=f"TEST-{uuid.uuid4().hex[:6].upper()}"
    )
    SaleItem.objects.create(sale=sale, product=product, quantity=1, price_at_sale=1000, subtotal=1000)
    print(f"Sale created: {sale.invoice_number}")

    # 3. Approve Sale
    print("Approving Sale...")
    sale.status = 'approved'
    sale.approved_by = admin
    sale.save()
    print("Sale approved.")

    # 4. Dispatch with Vehicle via API
    print("Dispatching with Vehicle...")
    client = APIClient()
    client.force_authenticate(user=admin)
    
    data = {
        'store_keeper': admin.id,
        'vehicle_id': vehicle.id,
        'lorry_info': '' # Should be auto-filled
    }
    
    response = client.post(f'/api/sales/{sale.id}/dispatch_order/', data, format='json')
    
    if response.status_code == 200:
        sale.refresh_from_db()
        print(f"Dispatch Success! Status: {sale.status}")
        print(f"Assigned Vehicle: {sale.vehicle}")
        print(f"Auto-filled Info: {sale.lorry_info}")
        
        if sale.vehicle == vehicle and str(vehicle.registration_number) in sale.lorry_info:
            print("VERIFICATION PASSED: Vehicle linked correctly.")
        else:
            print("VERIFICATION FAILED: Vehicle linkage incorrect.")
    else:
        print(f"Dispatch Failed: {response.content}")

if __name__ == '__main__':
    verify_vehicle_flow()
