from flask import Flask, request, jsonify
import requests
import json
import os
import time
import threading
from dotenv import load_dotenv

# Configuration - SECURE VERSION
import os

# ONLY from environment variables - NO HARDCODED SECRETS
SHOPIFY_STORE_URL = os.getenv('SHOPIFY_STORE_URL')
SHOPIFY_ADMIN_TOKEN = os.getenv('SHOPIFY_ADMIN_TOKEN')
COURIER_API_KEY = os.getenv('COURIER_API_KEY')
COURIER_BASE_URL = "https://transimpexexpress.am"
EHDM_USERNAME = os.getenv('EHDM_USERNAME')
EHDM_PASSWORD = os.getenv('EHDM_PASSWORD')

# Debug: Check if env vars are loading
print(f"🔧 DEBUG: SHOPIFY_STORE_URL loaded: {bool(SHOPIFY_STORE_URL)}")
print(f"🔧 DEBUG: SHOPIFY_ADMIN_TOKEN loaded: {bool(SHOPIFY_ADMIN_TOKEN)}")
print(f"🔧 DEBUG: COURIER_API_KEY loaded: {bool(COURIER_API_KEY)}")
print(f"🔧 DEBUG: EHDM_USERNAME loaded: {bool(EHDM_USERNAME)}")
print(f"🔧 DEBUG: EHDM_PASSWORD loaded: {bool(EHDM_PASSWORD)}")

app = Flask(__name__)

class EHDMService:
    def __init__(self):
        self.base_url = "http://store.payx.am"
        self.token = None

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
            # FIX: PayX returns token in 'token' header (lowercase)
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

def test_payx_connection():
    """Test PayX API connection with your credentials"""
    print("🧪 Testing PayX API connection...")

    test_service = EHDMService()
    if test_service.login():
        print("✅ PayX Login SUCCESSFUL!")
        return True
    else:
        print("❌ PayX Login FAILED!")
        return False


def test_payx_detailed():
    """Detailed test of PayX connection"""
    print("🧪 Detailed PayX test starting...")
    
    import requests
    login_url = "https://store.payx.am/api/Login/LoginUser"
    credentials = {
        "username": EHDM_USERNAME,
        "password": EHDM_PASSWORD
    }
    
    print(f"🔐 Testing with username: {EHDM_USERNAME}")
    print(f"🔗 URL: {login_url}")
    
    try:
        response = requests.post(login_url, json=credentials, timeout=10)
        print(f"📡 Response status: {response.status_code}")
        print(f"📡 Response headers: {dict(response.headers)}")
        print(f"📡 Response body: {response.text}")
        
        if response.status_code == 200:
            token = response.headers.get('Authorization')
            print(f"✅ SUCCESS! Token received: {bool(token)}")
            return True
        else:
            print(f"❌ FAILED: Status {response.status_code}")
            return False
            
    except Exception as e:
        print(f"💥 ERROR: {str(e)}")
        return False


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

    def wait_for_confirmation(self, shopify_order_id):
        """Wait for order to be tagged as confirmed"""
        print(f"Waiting for confirmation on order {shopify_order_id}")

        max_attempts = 288  # Check for 24 hours (every 5 minutes)

        for attempt in range(max_attempts):
            # Check order status in Shopify
            order_url = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-10/orders/{shopify_order_id}.json"
            response = requests.get(order_url, headers=self.shopify_headers)

            if response.status_code == 200:
                order_data = response.json().get('order', {})
                tags = order_data.get('tags', '').split(',')

                # Check if order is confirmed
                if 'confirmed' in [tag.strip().lower() for tag in tags]:
                    print(f"Order {shopify_order_id} confirmed! Processing...")

                    # Remove all tags and set only "confirmed"
                    update_data = {
                        "order": {
                            "id": shopify_order_id,
                            "tags": "confirmed"  # REPLACE all tags with just "confirmed"
                        }
                    }
                    requests.put(order_url, json=update_data, headers=self.shopify_headers)
                    print("✅ Tags updated: 'pending-confirmation' removed, only 'confirmed' remains")

                    return True
                # Check if order is cancelled
                elif 'cancelled' in [tag.strip().lower() for tag in tags]:
                    print(f"Order {shopify_order_id} was cancelled.")
                    return False

            print(f"Attempt {attempt + 1}: Order not confirmed yet. Waiting 5 minutes...")
            time.sleep(300)  # Wait 5 minutes

        print(f"Order {shopify_order_id} confirmation timeout after 24 hours")
        return False

    def create_courier_order(self, shopify_order):
        """Create draft order with courier and get tracking number"""
        print("Creating courier order...")

        # Use shipping address OR fallback to billing address
        shipping_address = shopify_order.get('shipping_address')
        billing_address = shopify_order.get('billing_address')

        # If no shipping address, use billing address
        if not shipping_address and billing_address:
            print("⚠️ No shipping address found, using billing address instead")
            shipping_address = billing_address
        elif not shipping_address:
            print("❌ Cannot create courier order: No shipping or billing address found")
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

        # Construct the API payload
        courier_order_data = {
            "address_to": shipping_address.get('address1', '')[:100],
            "province_id": self.map_region_to_province(shipping_address.get('province')),
            "city": shipping_address.get('city', '')[:50],
            "package_type": "Parcel",
            "parcel_weight": "1.0",
            "order_products": order_products,
            "recipient_type": "Individual",
            "person_name": f"{shipping_address.get('first_name', '')} {shipping_address.get('last_name', '')}"[:50],
            "phone": shipping_address.get('phone', '123456789')[:20],
            "barcode_id": str(shopify_order['id']),
            "is_payed": 1,
            "delivery_method": "home",
            "return_receipt": False,
            "notes": f"Shopify Order #{shopify_order.get('order_number', '')}",
            "label": 0
        }

        # Make API call to create draft order
        courier_url = f"{COURIER_BASE_URL}/api/create-draft-order"
        response = requests.post(courier_url, json=courier_order_data, headers=self.courier_headers)

        if response.status_code == 200:
            print("✅ Courier order created successfully!")

            # DEBUG: Log the full response to see what tracking data we get
            print(f"=== DEBUG Courier Response: {response.text}")

            try:
                courier_response = response.json()
                # Try to extract real tracking number from response
                tracking_number = (
                    courier_response.get('tracking_number') or
                    courier_response.get('barcode_id') or
                    courier_response.get('id') or
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

    def update_shopify_tracking(self, order_id, tracking_number):
        """Add tracking number to Shopify order and fulfill it"""
        print(f"Updating Shopify order {order_id} with tracking {tracking_number}")

        # Get fulfillment order ID
        fulfillment_url = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-10/orders/{order_id}/fulfillment_orders.json"
        fulfillment_response = requests.get(fulfillment_url, headers=self.shopify_headers)

        if fulfillment_response.status_code == 200:
            fulfillment_data = fulfillment_response.json()
            if fulfillment_data.get('fulfillment_orders'):
                fulfillment_order_id = fulfillment_data['fulfillment_orders'][0]['id']

                # Create fulfillment with tracking
                fulfillment_data = {
                    "fulfillment": {
                        "tracking_info": {
                            "number": tracking_number,
                            "company": "TransImpex Express",
                            "url": f"https://transimpexexpress.am/tracking/{tracking_number}"
                        },
                        "notify_customer": True,
                        "line_items_by_fulfillment_order": [
                            {
                                "fulfillment_order_id": fulfillment_order_id
                            }
                        ]
                    }
                }

                fulfill_url = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-10/fulfillments.json"
                response = requests.post(fulfill_url, json=fulfillment_data, headers=self.shopify_headers)

                if response.status_code == 200:
                    print("✅ Shopify order updated with tracking successfully!")
                    return True

        print("❌ Failed to update Shopify with tracking")
        return False

    def notify_team(self, shopify_order, tracking_number):
        """Notify about the new order"""
        message = f"🚚 NEW SHIPPING ORDER\n"
        message += f"Order #: {shopify_order.get('order_number')}\n"

        # Use shipping or billing address for customer name
        address = shopify_order.get('shipping_address') or shopify_order.get('billing_address') or {}
        message += f"Customer: {address.get('first_name', '')} {address.get('last_name', '')}\n"
        message += f"Tracking ID: {tracking_number}\n"
        message += f"Address: {address.get('address1', 'No address')}\n"
        message += f"Phone: {address.get('phone', 'N/A')}"

        print("📢 TEAM NOTIFICATION:")
        print(message)

    def map_region_to_province(self, region_name):
        """Map Shopify regions to courier province IDs"""
        province_mapping = {
            'Aragatsotn': 1, 'Ararat': 2, 'Armavir': 3, 'Gegharkunik': 4,
            'Kotayk': 5, 'Lori': 6, 'Shirak': 7, 'Syunik': 8, 'Tavush': 9,
            'Vayots Dzor': 10, 'Yerevan': 11
        }
        return province_mapping.get(region_name, 11)

def check_confirmation_in_background(order_id):
    """Run confirmation check in background thread"""
    def run_check():
        automation = CourierAutomation()
        if automation.wait_for_confirmation(order_id):
            process_confirmed_order(order_id)
        else:
            print(f"Order {order_id} was not confirmed - no courier notified")

    thread = threading.Thread(target=run_check)
    thread.daemon = True
    thread.start()

def process_confirmed_order(order_id):
    """Process order that has been confirmed"""
    automation = CourierAutomation()

    # Get order details from Shopify
    order_url = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-10/orders/{order_id}.json"
    response = requests.get(order_url, headers=automation.shopify_headers)

    if response.status_code == 200:
        shopify_order = response.json().get('order', {})

        # Create order with courier
        tracking_number = automation.create_courier_order(shopify_order)

        if tracking_number:
            # Update Shopify with tracking and fulfill
            success = automation.update_shopify_tracking(order_id, tracking_number)

            if success:
                # Notify courier team
                automation.notify_team(shopify_order, tracking_number)
                print(f"✅ Order {order_id} fully processed! Tracking: {tracking_number}")
            else:
                print(f"❌ Failed to update Shopify with tracking for order {order_id}")
        else:
            print(f"❌ Failed to create courier order for order {order_id}")

@app.route('/webhook/order-paid', methods=['POST'])
def handle_order_paid():
    """Webhook endpoint that Shopify calls when order is paid"""
    print("🔄 Received new order webhook")

    try:
        shopify_order = request.json
        order_id = shopify_order['id']
        order_number = shopify_order.get('order_number', 'Unknown')

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
            # Start confirmation checking process in background
            check_confirmation_in_background(order_id)

            print(f"✅ Order {order_number} saved. Add 'confirmed' tag in Shopify to ship.")
            return jsonify({
                "success": True,
                "message": "Order saved pending confirmation. Tag order with 'confirmed' when ready to ship."
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

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "healthy", "message": "Shipping automation server is running"}), 200

@app.route('/')
def home():
    return "Shipping Automation Server is Running! 🚚"

 # TEMPORARY TEST - Add this at the bottom
print("🚀 Starting detailed PayX connection test...")
test_payx_detailed()

if __name__ == '__main__':
    print("Starting Shipping Automation Server...")
    app.run(host='0.0.0.0', port=5000, debug=True)
