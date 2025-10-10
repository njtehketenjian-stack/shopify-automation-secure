from flask import Flask, request, jsonify
import requests
import json
import os
import time
import hashlib
import threading
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
SHOPIFY_STORE_URL = os.getenv('SHOPIFY_STORE_URL')
SHOPIFY_ADMIN_TOKEN = os.getenv('SHOPIFY_ADMIN_TOKEN')
COURIER_API_KEY = os.getenv('COURIER_API_KEY')
COURIER_BASE_URL = "https://transimpexexpress.am"
EHDM_USERNAME = os.getenv('EHDM_USERNAME')
EHDM_PASSWORD = os.getenv('EHDM_PASSWORD')

app = Flask(__name__)

# Simple in-memory stores
processed_webhooks = {}
pending_orders = set()

print("🚀 Starting Shipping Automation Server...")

class EHDMService:
    def __init__(self):
        self.base_url = "https://store.payx.am"
        self.token = None
        self.courier_headers = {
            'Authorization': f'Bearer {COURIER_API_KEY}',
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        }
    
    def login(self):
        """Get JWT token from E-HDM API"""
        try:
            login_url = f"{self.base_url}/api/Login/LoginUser"
            credentials = {
                "username": EHDM_USERNAME,
                "password": EHDM_PASSWORD
            }
            
            print(f"🔐 Attempting PayX login with: {EHDM_USERNAME}")
            response = requests.post(login_url, json=credentials)
            
            if response.status_code == 200:
                self.token = response.headers.get('token')
                if self.token:
                    print("✅ PayX JWT token obtained successfully!")
                    return True
                else:
                    print("⚠️  Login successful but no token in 'token' header")
            else:
                print(f"❌ PayX login failed: {response.status_code} - {response.text}")
                
        except Exception as e:
            print(f"❌ PayX login error: {str(e)}")
        
        return False

    def extract_customer_data(self, shopify_order):
        """
        COMPLETE customer data extraction from FULL Shopify order data
        Uses multiple fallback sources to get real customer information
        """
        shipping_address = shopify_order.get('shipping_address', {})
        billing_address = shopify_order.get('billing_address', {})
        customer = shopify_order.get('customer', {})
        
        print("=== DEBUG: Customer Data Extraction ===")
        print(f"Shipping keys: {list(shipping_address.keys())}")
        print(f"Billing keys: {list(billing_address.keys())}")
        print(f"Customer keys: {list(customer.keys())}")
        
        # 1. EXTRACT NAME - Priority: shipping > billing > customer
        name = "Customer"
        if shipping_address.get('first_name') or shipping_address.get('last_name'):
            first_name = shipping_address.get('first_name', '').strip()
            last_name = shipping_address.get('last_name', '').strip()
            name = f"{first_name} {last_name}".strip()
            print(f"✅ Name from SHIPPING: {name}")
        elif billing_address.get('first_name') or billing_address.get('last_name'):
            first_name = billing_address.get('first_name', '').strip()
            last_name = billing_address.get('last_name', '').strip()
            name = f"{first_name} {last_name}".strip()
            print(f"✅ Name from BILLING: {name}")
        elif customer.get('first_name') or customer.get('last_name'):
            first_name = customer.get('first_name', '').strip()
            last_name = customer.get('last_name', '').strip()
            name = f"{first_name} {last_name}".strip()
            print(f"✅ Name from CUSTOMER: {name}")
        else:
            print("⚠️  Using default name: Customer")

        # 2. EXTRACT ADDRESS - Priority: shipping > billing
        address = "Address Not Provided"
        if shipping_address.get('address1'):
            address1 = shipping_address.get('address1', '').strip()
            address2 = shipping_address.get('address2', '').strip()
            address = f"{address1} {address2}".strip()
            print(f"✅ Address from SHIPPING: {address}")
        elif billing_address.get('address1'):
            address1 = billing_address.get('address1', '').strip()
            address2 = billing_address.get('address2', '').strip()
            address = f"{address1} {address2}".strip()
            print(f"✅ Address from BILLING: {address}")
        else:
            print("⚠️  Using default address: Address Not Provided")

        # 3. EXTRACT PHONE - Priority: shipping > billing > customer
        phone = "+374 00 000 000"
        if shipping_address.get('phone'):
            phone = shipping_address.get('phone', '').strip()
            print(f"✅ Phone from SHIPPING: {phone}")
        elif billing_address.get('phone'):
            phone = billing_address.get('phone', '').strip()
            print(f"✅ Phone from BILLING: {phone}")
        elif customer.get('phone'):
            phone = customer.get('phone', '').strip()
            print(f"✅ Phone from CUSTOMER: {phone}")
        else:
            print("⚠️  Using default phone: +374 00 000 000")

        # 4. EXTRACT CITY - Priority: shipping > billing
        city = "Yerevan"
        if shipping_address.get('city'):
            city = shipping_address.get('city', '').strip()
            print(f"✅ City from SHIPPING: {city}")
        elif billing_address.get('city'):
            city = billing_address.get('city', '').strip()
            print(f"✅ City from BILLING: {city}")
        else:
            print("⚠️  Using default city: Yerevan")

        customer_data = {
            'name': name,
            'address': address,
            'phone': phone,
            'city': city,
            'email': customer.get('email', '')
        }
        
        print(f"🎯 FINAL Customer Data: {customer_data}")
        print("=== END DEBUG ===")
        
        return customer_data

    def create_courier_order(self, shopify_order):
        """Create draft order with courier using REAL customer data"""
        print("🔄 Creating courier order with REAL customer data...")

        # Extract COMPLETE customer data
        customer_data = self.extract_customer_data(shopify_order)
        
        if not customer_data:
            print("❌ Cannot create courier order: No customer data found")
            return None

        line_items = shopify_order.get('line_items', [])

        # Build order products array
        order_products = []
        for item in line_items:
            price_in_cents = int(float(item['price']) * 100)
            order_products.append({
                "name": item['name'][:50],
                "price": price_in_cents
            })

        # If no products, add a default item
        if not order_products:
            order_products.append({
                "name": "Online Order Items",
                "price": 100
            })

        # Construct the API payload with REAL customer data
        courier_order_data = {
            "address_to": customer_data['address'][:100],
            "province_id": 11,  # Default to Yerevan
            "city": customer_data['city'][:50],
            "package_type": "Parcel",
            "parcel_weight": "1.0",
            "order_products": order_products,
            "recipient_type": "Individual",
            "person_name": customer_data['name'][:50],
            "phone": customer_data['phone'][:20],
            "barcode_id": str(shopify_order['id']),
            "is_payed": 1,
            "delivery_method": "home",
            "return_receipt": False,
            "notes": f"Shopify Order #{shopify_order.get('order_number', '')} - {customer_data['email']}",
            "label": 0
        }

        # DEBUG: Print the actual payload being sent
        print("=== DEBUG: Courier Payload ===")
        print(json.dumps(courier_order_data, indent=2))
        print("=== END DEBUG ===")

        # Make API call to create draft order
        courier_url = f"{COURIER_BASE_URL}/api/create-draft-order"
        response = requests.post(courier_url, json=courier_order_data, headers=self.courier_headers)

        if response.status_code == 200:
            print("✅ Courier order created successfully!")

            try:
                courier_response = response.json()
                # Try to extract real tracking number from response
                tracking_number = (
                    courier_response.get('order', {}).get('key') or  # Use the 'key' field as tracking number
                    courier_response.get('order', {}).get('barcode_id') or
                    courier_response.get('order', {}).get('id') or
                    str(shopify_order['id'])  # Fallback to Shopify ID
                )
                print(f"✅ Real tracking number: {tracking_number}")
                return tracking_number
            except:
                print("⚠️ Could not parse courier response, using Shopify ID as tracking")
                return str(shopify_order['id'])
        else:
            print(f"❌ Courier API Error: {response.status_code} - {response.text}")
            return None

    def update_shopify_tracking(self, order_id, tracking_number, shopify_headers):
        """Add tracking number to Shopify order and fulfill it"""
        print(f"📦 Updating Shopify order {order_id} with tracking {tracking_number}")

        try:
            # Simple fulfillment without fulfillment orders
            fulfillment_data = {
                "fulfillment": {
                    "location_id": 1,
                    "tracking_number": str(tracking_number),
                    "tracking_company": "TransImpex Express",
                    "tracking_url": f"https://transimpexexpress.am/tracking/{tracking_number}",
                    "notify_customer": True
                }
            }

            fulfill_url = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-10/orders/{order_id}/fulfillments.json"
            response = requests.post(fulfill_url, json=fulfillment_data, headers=shopify_headers)

            if response.status_code in [201, 200]:
                print("✅ Shopify order updated with tracking successfully!")
                return True
            else:
                print(f"❌ Shopify fulfillment failed: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            print(f"❌ Error updating Shopify tracking: {str(e)}")
            return False

class CourierAutomation:
    def __init__(self):
        self.shopify_headers = {
            'Content-Type': 'application/json',
            'X-Shopify-Access-Token': SHOPIFY_ADMIN_TOKEN
        }

    def check_and_process_confirmed_orders(self):
        """Check all pending orders and process confirmed ones"""
        print(f"🔍 Checking {len(pending_orders)} pending orders for confirmation...")
        
        if not pending_orders:
            print("📭 No pending orders to check")
            return
        
        processed_count = 0
        
        for order_id in list(pending_orders):
            try:
                print(f"🔍 Checking order {order_id}...")
                
                # Get COMPLETE order details from Shopify API (not webhook data)
                order_url = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-10/orders/{order_id}.json"
                response = requests.get(order_url, headers=self.shopify_headers)

                if response.status_code != 200:
                    print(f"❌ Failed to fetch order {order_id}: {response.status_code}")
                    continue

                shopify_order = response.json().get('order', {})
                
                # Check if order is confirmed
                tags = [tag.strip().lower() for tag in shopify_order.get('tags', '').split(',')]
                print(f"🏷️ Order {order_id} tags: {tags}")
                
                if 'confirmed' in tags:
                    print(f"🎉 Order {order_id} is confirmed! Processing...")
                    success = self.process_order_immediately(order_id)
                    
                    if success:
                        pending_orders.remove(order_id)
                        processed_count += 1
                        print(f"✅ Successfully processed order {order_id}")
                    else:
                        print(f"❌ Failed to process order {order_id}")
                else:
                    print(f"⏳ Order {order_id} still pending confirmation")
                    
            except Exception as e:
                print(f"❌ Error checking order {order_id}: {str(e)}")
        
        if processed_count > 0:
            print(f"🎊 Processed {processed_count} confirmed orders!")
        else:
            print("📋 No confirmed orders found")

    def process_order_immediately(self, order_id):
        """Process order immediately with PayX and Courier"""
        print(f"🚀 PROCESSING ORDER {order_id}")
        
        try:
            # Get COMPLETE order details from Shopify API
            order_url = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-10/orders/{order_id}.json"
            response = requests.get(order_url, headers=self.shopify_headers)

            if response.status_code != 200:
                print(f"❌ Failed to fetch order {order_id}: {response.status_code}")
                return False

            shopify_order = response.json().get('order', {})
            
            # Check if order already has OUR fulfillment
            has_our_tracking = False
            for fulfillment in shopify_order.get('fulfillments', []):
                if fulfillment.get('tracking_company') == 'TransImpex Express':
                    has_our_tracking = True
                    break
            
            if has_our_tracking:
                print(f"✅ Order {order_id} already processed by our system, skipping")
                return True
            
            # Process with EHDM service
            ehdm_service = EHDMService()
            
            # Generate fiscal receipt with PayX first
            if ehdm_service.login():
                print("✅ PayX login successful, ready for receipt generation")
                # TODO: Add receipt generation logic here
            else:
                print("❌ PayX login failed, but continuing with shipping")
            
            # Create order with courier using REAL customer data
            tracking_number = ehdm_service.create_courier_order(shopify_order)

            if tracking_number:
                # Update Shopify with tracking and fulfill
                success = ehdm_service.update_shopify_tracking(order_id, tracking_number, self.shopify_headers)

                if success:
                    print(f"✅ Order {order_id} fully processed! Tracking: {tracking_number}")
                    return True
                else:
                    print(f"❌ Failed to update Shopify with tracking for order {order_id}")
                    return False
            else:
                print(f"❌ Failed to create courier order for order {order_id}")
                return False
                
        except Exception as e:
            print(f"💥 ERROR processing order {order_id}: {str(e)}")
            return False

def generate_webhook_id(webhook_data):
    """Generate unique ID for webhook to prevent duplicates"""
    webhook_str = json.dumps(webhook_data, sort_keys=True)
    return hashlib.md5(webhook_str.encode()).hexdigest()

def background_order_checker():
    """Simple background thread that checks for confirmed orders every 5 minutes"""
    print("🔄 Starting automatic order checker (5-minute intervals)...")
    
    while True:
        try:
            automation = CourierAutomation()
            automation.check_and_process_confirmed_orders()
        except Exception as e:
            print(f"❌ Background checker error: {str(e)}")
        
        # Wait 5 minutes before next check
        print("⏰ Next automatic check in 5 minutes...")
        time.sleep(300)  # 5 minutes

@app.route('/webhook/order-paid', methods=['POST'])
def handle_order_paid():
    """Webhook endpoint that Shopify calls when order is paid"""
    print("🔄 Received new order webhook")

    try:
        shopify_order = request.json
        order_id = shopify_order['id']
        order_number = shopify_order.get('order_number', 'Unknown')

        # Webhook idempotency - prevent duplicate processing
        webhook_id = generate_webhook_id(shopify_order)
        if webhook_id in processed_webhooks:
            print(f"🔄 Duplicate webhook detected for order {order_number}, skipping")
            return jsonify({"success": True, "message": "Webhook already processed"}), 200
        
        processed_webhooks[webhook_id] = True

        print(f"Processing order #{order_number} (ID: {order_id})")

        # Add "pending-confirmation" tag to the order
        automation = CourierAutomation()
        update_data = {
            "order": {
                "id": order_id,
                "tags": "pending-confirmation"
            }
        }

        update_url = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-10/orders/{order_id}.json"
        response = requests.put(update_url, json=update_data, headers=automation.shopify_headers)

        if response.status_code == 200:
            # Add to pending orders for automatic processing
            pending_orders.add(order_id)
            print(f"✅ Order {order_number} added to pending orders (total: {len(pending_orders)})")
            print(f"🎯 System will auto-process when 'confirmed' tag is added (checks every 5 minutes)")
            
            return jsonify({
                "success": True,
                "message": "Order saved. System auto-processes when 'confirmed' tag is added."
            }), 200
        else:
            print(f"❌ Failed to update order tags: {response.text}")
            return jsonify({
                "success": False,
                "message": "Failed to update order tags"
            }), 500

    except Exception as e:
        print(f"❌ Error processing webhook: {str(e)}")
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/process-confirmed', methods=['POST'])
def process_confirmed_orders():
    """Manual endpoint to process all confirmed orders immediately"""
    print("🔄 Manual processing of confirmed orders requested")
    
    try:
        automation = CourierAutomation()
        automation.check_and_process_confirmed_orders()
        
        return jsonify({
            "success": True, 
            "message": f"Processed confirmed orders. {len(pending_orders)} orders still pending."
        }), 200
        
    except Exception as e:
        print(f"❌ Error processing confirmed orders: {str(e)}")
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/pending-orders', methods=['GET'])
def get_pending_orders():
    """Get list of pending orders"""
    return jsonify({
        "success": True,
        "pending_orders": list(pending_orders),
        "count": len(pending_orders)
    }), 200

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "healthy", "message": "Shipping automation server is running"}), 200

@app.route('/')
def home():
    return """
    🚚 AUTOMATIC Shipping Automation Server<br><br>
    <strong>SYSTEM STATUS: ACTIVE</strong><br>
    - ✅ Webhook auto-tags orders as 'pending-confirmation'<br>
    - ✅ System checks for 'confirmed' tags every 5 minutes<br>
    - ✅ Auto-processes confirmed orders with REAL customer data<br><br>
    
    <strong>Endpoints:</strong><br>
    - POST /webhook/order-paid (Shopify webhook)<br>
    - POST /process-confirmed (manual trigger)<br>
    - GET /pending-orders (view pending orders)<br>
    - GET /health (health check)<br>
    """

# Start the automatic background checker when app starts
print("✅ Starting automatic order checker...")
checker_thread = threading.Thread(target=background_order_checker)
checker_thread.daemon = True  # Daemon thread will be killed when main thread exits
checker_thread.start()

def keep_alive():
    """Background thread to ping app every 10 minutes to prevent Render sleep"""
    def ping():
        import requests
        while True:
            try:
                # Ping our own health endpoint
                requests.get("https://shopify-automation-secure.onrender.com/health", timeout=5)
                print("🔄 Keep-alive ping sent")
            except Exception as e:
                print(f"⚠️ Keep-alive ping failed: {e}")
            time.sleep(600)  # Wait 10 minutes
    
    thread = threading.Thread(target=ping)
    thread.daemon = True
    thread.start()
    print("✅ Keep-alive service started")

# Start keep-alive when app loads
keep_alive()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    print(f"🌐 Server starting on port {port}...")
    app.run(host='0.0.0.0', port=port, debug=False)
