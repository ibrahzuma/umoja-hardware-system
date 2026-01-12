
import requests
import json

url = "http://127.0.0.1:8000/api/sales/"
headers = {
    # If auth is required, we might need a token. 
    # But for reproduction we'll try without first or add basic auth if needed.
    "Content-Type": "application/json"
}
# Assuming auth is needed, let's try to get a token or use session if possible. 
# For now, let's just assume we can hit it or we'll get 403. 
# To be safe, I'll use the user's running session if I could, but I can't.
# I'll rely on the 500 error from the server logs to confirm.

# Payload that might cause the crash (missing method)
payload = {
    "items": [
        {"product": 1, "quantity": 1} # Need a valid product ID
    ],
    "payment_details": {
        "amount": 100
        # "method" is missing
    },
    "customer_name": "Test Customer",
    "branch": 1, # Need a valid branch ID
    "user": 1 # Need a valid user ID
}

# Getting valid IDs might be tricky without querying first. 
# I will try to make a GET request to /api/products/ first to get a valid product.
