from flask import Flask, request, jsonify
import requests
import json
import os
import time
import hashlib
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
SHOPIFY_STORE_URL = os.getenv('SHOPIFY_STORE_URL')
SHOPIFY_ADMIN_TOKEN = os.getenv('SHOPIFY_ADMIN_TOKEN')
COURIER_API_KEY = os.getenv('COURIER_API_KEY')
COURIER_BASE_URL = "https://transimpexexpress.am"

app = Flask(__name__)

# Simple in-memory stores
processed_webhooks = {}
pending_orders = set()

print("🚀 Server starting...")

class CourierAutomation:
    def __init__(self):
        self.shopify_headers = {
            'Content-Type': 'application/json',
            'X-Shopify-Access-Token': SHOPIFY_ADMIN_TOKEN
        }
        self.courier_headers = {
            'Authorization': f'Bearer {COURIER_API_KEY}',
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        }

    def extract_customer_data(self, shopify_order):
        """Simple customer data extraction"""
        shipping_address = shopify_order.get('shipping_address', {})
        billing_address = shopify_order.get('billing_address', {})
        customer = shopify_order.get('customer', {})
        
        # Get name
        name = "Customer"
        if shipping_address.get('first_name') or shipping_address.get('last_name'):
            name = f"{shipping_address.get('first_name', '')} {shipping_address.get('last_name', '')}".strip()
        elif billing_address.get('first_name') or billing_address.get('last_name'):
            name = f"{billing_address.get('first_name', '')} {billing_address.get('last_name', '')}".strip()
        
        # Get address
        address = "Address Not Provided"
        if shipping_address.get('address1'):
            address = shipping_address.get('address1', '')
        elif billing_address.get('address1'):
            address = billing_address.get('address1', '')
        
        # Get phone
        phone = "+374 00 000 000"
        if shipping_address.get('phone'):
            phone = shipping_address.get('phone', '')
        elif billing_address.get('phone'):
            phone = billing_address.get('phone', '')
        
        # Get city
        city = "Yerevan"
        if shipping_address.get('city'):
            city = shipping_address.get('city', '')
        elif billing_address.get('city'):
            city = billing_address.get('city', '')
        
        return {
            'name': name,
            'address': address,
            'phone': phone,
            'city': city,
            'email': customer.get('email', '')
        }

    def create_courier_order(self, shopify_order):
        """Create draft order with courier"""
        print("Creating courier order...")

        customer_data = self.extract_customer_data(shopify_order)
        
        line_items = shopify_order.get('line_items', [])
        order_products = []
        for item in line_items:
            price_in_cents = int(float(item['price']) * 100)
            order_products.append({
                "name": item['name'][:50],
                "price": price_in_cents
            })

        if not order_products:
            order_products.append({"name": "Online Order Items", "price": 100})

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
            "notes": f"Shopify Order #{shopify_order.get('order_number', '')}",
            "label": 0
        }

        courier_url = f"{COURIER_BASE_URL}/api/create-draft-order"
        response = requests.post(courier_url, json=courier_order_data, headers=self.courier_headers)

        if response.status_code == 200:
            print("✅ Courier order created successfully!")
            try:
                courier_response = response.json()
                tracking_number = courier_response.get('order', {}).get('key') or str(shopify_order['id'])
                print(f"✅ Tracking number: {tracking_number}")
                return tracking_number
            except:
                return str(shopify_order['id'])
        else:
            print(f"❌ Courier API Error: {response.status_code}")
            return None

    def update_shopify_tracking(self, order_id, tracking_number):
        """Add tracking number to Shopify order"""
        print(f"Updating Shopify order {order_id} with tracking {tracking_number}")

        try:
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
            response = requests.post(fulfill_url, json=fulfillment_data, headers=self.shopify_headers)

            if response.status_code in [201, 200]:
                print("✅ Shopify order updated with tracking!")
                return True
            else:
                print(f"❌ Shopify fulfillment failed: {response.status_code}")
                return False
                
        except Exception as e:
            print(f"❌ Error updating Shopify: {str(e)}")
            return False

    def process_order_immediately(self, order_id):
        """Process order immediately"""
        print(f"🚀 PROCESSING ORDER {order_id}")
        
        try:
            # Get order details from Shopify
            order_url = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-10/orders/{order_id}.json"
            response = requests.get(order_url, headers=self.shopify_headers)

            if response.status_code != 200:
                print(f"❌ Failed to fetch order {order_id}: {response.status_code}")
                return False

            shopify_order = response.json().get('order', {})
            
            # Create order with courier
            tracking_number = self.create_courier_order(shopify_order)

            if tracking_number:
                # Update Shopify with tracking
                success = self.update_shopify_tracking(order_id, tracking_number)
                if success:
                    print(f"✅ Order {order_id} fully processed! Tracking: {tracking_number}")
                    return True
                else:
                    print(f"❌ Failed to update Shopify with tracking")
                    return False
            else:
                print(f"❌ Failed to create courier order")
                return False
                
        except Exception as e:
            print(f"💥 ERROR processing order {order_id}: {str(e)}")
            return False

def generate_webhook_id(webhook_data):
    """Generate unique ID for webhook to prevent duplicates"""
    webhook_str = json.dumps(webhook_data, sort_keys=True)
    return hashlib.md5(webhook_str.encode()).hexdigest()

@app.route('/webhook/order-paid', methods=['POST'])
def handle_order_paid():
    """Webhook endpoint that Shopify calls when order is paid"""
    print("🔄 Received new order webhook")

    try:
        shopify_order = request.json
        order_id = shopify_order['id']
        order_number = shopify_order.get('order_number', 'Unknown')

        # Webhook idempotency
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
            pending_orders.add(order_id)
            print(f"✅ Order {order_number} added to pending orders")
            
            return jsonify({
                "success": True,
                "message": "Order saved. Add 'confirmed' tag and call /process-confirmed"
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

@app.route('/process-order/<order_id>', methods=['POST'])
def process_order_manual(order_id):
    """Manual endpoint to process an order immediately"""
    print(f"🔄 Manual order processing requested for {order_id}")
    
    try:
        automation = CourierAutomation()
        success = automation.process_order_immediately(order_id)
        
        if success:
            if order_id in pending_orders:
                pending_orders.remove(order_id)
            return jsonify({"success": True, "message": f"Order {order_id} processed successfully"}), 200
        else:
            return jsonify({"success": False, "message": f"Failed to process order {order_id}"}), 500
            
    except Exception as e:
        print(f"❌ Error in manual order processing: {str(e)}")
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/process-confirmed', methods=['POST'])
def process_confirmed_orders():
    """Process all orders with 'confirmed' tag"""
    print("🔄 Processing confirmed orders...")
    
    try:
        automation = CourierAutomation()
        processed_count = 0
        
        for order_id in list(pending_orders):
            # Check if order is confirmed
            order_url = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-10/orders/{order_id}.json"
            response = requests.get(order_url, headers=automation.shopify_headers)
            
            if response.status_code == 200:
                shopify_order = response.json().get('order', {})
                tags = [tag.strip().lower() for tag in shopify_order.get('tags', '').split(',')]
                
                if 'confirmed' in tags:
                    print(f"🎉 Order {order_id} is confirmed! Processing...")
                    success = automation.process_order_immediately(order_id)
                    if success:
                        pending_orders.remove(order_id)
                        processed_count += 1
        
        return jsonify({
            "success": True, 
            "message": f"Processed {processed_count} confirmed orders. {len(pending_orders)} orders still pending."
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
    return jsonify({"status": "healthy", "message": "Server is running"}), 200

@app.route('/')
def home():
    return """
    🚚 Shipping Automation Server<br><br>
    <strong>Endpoints:</strong><br>
    - POST /webhook/order-paid<br>
    - POST /process-order/&lt;order_id&gt;<br>
    - POST /process-confirmed<br>
    - GET /pending-orders<br>
    - GET /health<br>
    """

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    print(f"🌐 Server starting on port {port}...")
    app.run(host='0.0.0.0', port=port, debug=False)
