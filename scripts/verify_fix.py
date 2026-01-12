from rest_framework.test import APIClient
from rest_framework import status
from django.contrib.auth import get_user_model
from apps.sales.models import Sale, SaleItem, Customer
from apps.inventory.models import Product, Branch, Stock

def verify():
    print("Starting verification...")
    User = get_user_model()
    # Create necessary data
    user, _ = User.objects.get_or_create(username='testadmin', defaults={'email': 'admin@test.com'})
    branch, _ = Branch.objects.get_or_create(name='Main Branch')
    customer, _ = Customer.objects.get_or_create(name='Test Customer', defaults={'phone': '1234567890'})
    
    sale = Sale.objects.create(
        customer=customer,
        branch=branch,
        status='approved',
        total_amount=1000
    )

    client = APIClient()
    client.force_authenticate(user=user)

    url = f'/api/sales/{sale.id}/dispatch_order/'
    
    print(f"Testing URL: {url}")
    
    try:
        response = client.post(url, {}, format='json')
        print(f"Response Status: {response.status_code}")
        
        if response.status_code == 500:
            print("FAILED: Server Error 500 still occurring.")
        elif response.status_code == 404:
             print("FAILED: Endpoint not found (404). Did routing update?")
        else:
            print("SUCCESS: Endpoint is reachable and not crashing.")

    except Exception as e:
        print(f"CRASHED: {e}")

if __name__ == "__main__":
    verify()
