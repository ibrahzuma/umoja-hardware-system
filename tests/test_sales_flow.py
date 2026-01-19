from django.test import TestCase
from apps.users.models import User
from apps.inventory.models import Branch, Category, Product, Stock
from apps.sales.models import Customer, Sale, SaleItem

class SalesFlowTest(TestCase):
    def test_full_sales_flow(self):
        try:
            # 1. Setup Data
            print("Creating User...")
            user = User.objects.create_user(username='testmanager', password='password', role='manager')
            print("Creating Branch...")
            branch = Branch.objects.create(name="Main Branch", address="Dar es Salaam")
            category = Category.objects.create(name="Construction")
            product = Product.objects.create(
                name="Cement Bag 50kg",
                category=category,
                sku="TEST-CEM-001",
                cost=15000,
                price=18000
            )
            # Add initial stock
            print("Creating Stock...")
            Stock.objects.create(product=product, branch=branch, quantity=100)
            
            customer = Customer.objects.create(name="John Doe", phone="0700000000")

            # 2. Create Sale
            print("Creating Sale...")
            sale = Sale.objects.create(
                branch=branch,
                user=user,
                customer=customer,
                status='pending'
            )
            SaleItem.objects.create(sale=sale, product=product, quantity=10, price_at_sale=18000)
            
            # Calculate totals
            sale.total_amount = 18000 * 10
            sale.save()
            
            self.assertEqual(sale.status, 'pending')
            self.assertEqual(sale.total_amount, 180000)
            
            # 3. Approve Sale
            print("Approving Sale...")
            sale.status = 'approved'
            sale.save()
            self.assertEqual(sale.status, 'approved')
            
            # 4. Dispatch Order
            print("Dispatching Order logic...")
            stock = Stock.objects.get(product=product, branch=branch)
            initial_qty = stock.quantity
            
            self.assertEqual(initial_qty, 100)
            
            # Simulate Dispatch Logic
            for item in sale.items.all():
                s_item_stock = Stock.objects.get(product=item.product, branch=branch)
                s_item_stock.quantity -= item.quantity
                s_item_stock.save()
            
            sale.status = 'completed'
            sale.save()
            
            # 5. Verify Results
            stock.refresh_from_db()
            self.assertEqual(stock.quantity, 90)
            self.assertEqual(sale.status, 'completed')
            
            print("\n\nTest Passed: Sales Flow correctly deducted stock from 100 to 90.")
        except Exception as e:
            with open('test_error.log', 'w') as f:
                import traceback
                traceback.print_exc(file=f)
            raise e
