
import requests
import json

url = "http://127.0.0.1:8000/api/sales/"
# We need to find a valid product ID and branch ID first
# Assuming 1 for now or we can fetch.

try:
    # 1. Fetch products to get a valid ID
    r_prod = requests.get("http://127.0.0.1:8000/api/products/")
    if r_prod.status_code == 200:
        products = r_prod.json()
        if isinstance(products, dict) and 'results' in products:
            products = products['results']
        
        if not products:
            print("No products found to test with.")
            exit()
        product_id = products[0]['id']
        price = products[0]['price']
    else:
        print("Failed to fetch products")
        exit()

    # 2. Fetch branches
    r_branch = requests.get("http://127.0.0.1:8000/api/branches/")
    if r_branch.status_code == 200:
        branches = r_branch.json()
        if isinstance(branches, dict) and 'results' in branches:
            branches = branches['results']
        if not branches:
             print("No branches found.")
             exit()
        branch_id = branches[0]['id']
    else:
        print("Failed to fetch branches")
        exit()
    
    # 3. Create Payload
    payload = {
        "items": [
            {"product": product_id, "quantity": 1} 
        ],
        "payment_details": {
            "amount": float(price),
            "method": "cash" # Explicitly sending valid method first
        },
        "customer_name": "Test Customer",
        "branch": branch_id,
        "user": 1 # This might fail if user 1 doesn't exist or we arn't authenticated as such. 
                  # But the view might take request.user. 
                  # SaleViewSet uses perform_create maybe? 
                  # No, the serializer has 'user' in fields. 
                  # But we are not authenticated. 
    }
    
    # Login first?
    # The view requires permissions.DjangoModelPermissions
    # That means we need to be authenticated.
    
    # Let's try to just hit it and see if we get 403 or 500. 
    # If 403, then the user's error is different (they are logged in).
    
    print("Sending payload:", json.dumps(payload, indent=2))
    response = requests.post(url, json=payload)
    
    print(f"Status Code: {response.status_code}")
    if response.status_code == 500:
        print("Server Error Content:")
        print(response.text) # This will show the traceback HTML!
    elif response.status_code != 201:
        print(response.text)

except Exception as e:
    print(f"Error: {e}")
