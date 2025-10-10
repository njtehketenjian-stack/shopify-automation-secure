from flask import Flask, request, jsonify
import requests
import json
import os
import time
import hashlib
import schedule
from dotenv import load_dotenv
import threading

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

# Simple in-memory store
processed_webhooks = {}
pending_orders = set()  # Track orders waiting for confirmation

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
        Extract customer data using priority-based fallback system
        Now includes customer.default_address as a data source
        """
        shipping_address = shopify_order.get('shipping_address', {})
        billing_address = shopify_order.get('billing_address', {})
        customer = shopify_order.get('customer', {})
        default_address = customer.get('default_address', {})
        
        print("=== DEBUG Customer Data Extraction ===")
        print(f"Shipping Address: {shipping_address}")
        print(f"Billing Address: {billing_address}")
        print(f"Customer Object: {list(customer.keys()) if customer else 'No customer'}")
        print(f"Default Address: {default_address}")
        
        # Priority 1: Extract name with fallbacks (now includes default_address)
        name = self._extract_name(shipping_address, billing_address, customer, default_address)
        
        # Priority 2: Extract address with fallbacks (now includes default_address)
        address = self._extract_address(shipping_address, billing_address, default_address)
        
        # Priority 3: Extract phone with fallbacks (now includes default_address)
        phone = self._extract_phone(shipping_address, billing_address, customer, default_address)
        
        # Priority 4: Extract city with fallbacks (now includes default_address)
        city = self._extract_city(shipping_address, billing_address, default_address)
        
        # Priority 5: Extract province with fallbacks (now includes default_address)
        province = self._extract_province(shipping_address, billing_address, default_address)
        
        customer_data = {
            'name': name,
            'address': address,
            'phone': phone,
            'city': city,
            'province': province,
            'email': customer.get('email', '')  # Add email for better identification
        }
        
        print(f"Extracted Customer Data: {customer_data}")
        print("=== END DEBUG ===")
        
        return customer_data

    def _extract_name(self, shipping_address, billing_address, customer, default_address):
        """Extract customer name with fallbacks including default_address"""
        # Try shipping address first
        if shipping_address.get('first_name') or shipping_address.get('last_name'):
            first_name = shipping_address.get('first_name', '').strip()
            last_name = shipping_address.get('last_name', '').strip()
            if first_name or last_name:
                return f"{first_name} {last_name}".strip()
        
        # Try billing address
        if billing_address.get('first_name') or billing_address.get('last_name'):
            first_name = billing_address.get('first_name', '').strip()
            last_name = billing_address.get('last_name', '').strip()
            if first_name or last_name:
                return f"{first_name} {last_name}".strip()
        
        # Try default address
        if default_address.get('first_name') or default_address.get('last_name'):
            first_name = default_address.get('first_name', '').strip()
            last_name = default_address.get('last_name', '').strip()
            if first_name or last_name:
                return f"{first_name} {last_name}".strip()
        
        # Try customer object
        if customer.get('first_name') or customer.get('last_name'):
            first_name = customer.get('first_name', '').strip()
            last_name = customer.get('last_name', '').strip()
            if first_name or last_name:
                return f"{first_name} {last_name}".strip()
        
        # Use email as last resort for identification
        email = customer.get('email', '')
        if email:
            return email.split('@')[0]  # Use part before @ as name
        
        # Final fallback
        return "Customer"

    def _extract_address(self, shipping_address, billing_address, default_address):
        """Extract address with fallbacks including default_address"""
        # Try shipping address first
        if shipping_address.get('address1'):
            address1 = shipping_address.get('address1', '').strip()
            address2 = shipping_address.get('address2', '').strip()
            address = f"{address1} {address2}".strip()
            if address:
                return address
        
        # Try billing address
        if billing_address.get('address1'):
            address1 = billing_address.get('address1', '').strip()
            address2 = billing_address.get('address2', '').strip()
            address = f"{address1} {address2}".strip()
            if address:
                return address
        
        # Try default address
        if default_address.get('address1'):
            address1 = default_address.get('address1', '').strip()
            address2 = default_address.get('address2', '').strip()
            address = f"{address1} {address2}".strip()
            if address:
                return address
        
        # Final fallback
        return "Address Not Provided"

    def _extract_phone(self, shipping_address, billing_address, customer, default_address):
        """Extract phone number with fallbacks including default_address"""
        # Try shipping address first
        if shipping_address.get('phone'):
            phone = shipping_address.get('phone', '').strip()
            if phone:
                return phone
        
        # Try billing address
        if billing_address.get('phone'):
            phone = billing_address.get('phone', '').strip()
            if phone:
                return phone
        
        # Try default address
        if default_address.get('phone'):
            phone = default_address.get('phone', '').strip()
            if phone:
                return phone
        
        # Try customer object
        if customer.get('phone'):
            phone = customer.get('phone', '').strip()
            if phone:
                return phone
        
        # Final fallback
        return "+374 00 000 000"

    def _extract_city(self, shipping_address, billing_address, default_address):
        """Extract city with fallbacks including default_address"""
        # Try shipping address first
        if shipping_address.get('city'):
            city = shipping_address.get('city', '').strip()
            if city:
                return city
        
        # Try billing address
        if billing_address.get('city'):
            city = billing_address.get('city', '').strip()
            if city:
                return city
        
        # Try default address
        if default_address.get('city'):
            city = default_address.get('city', '').strip()
            if city:
                return city
        
        # Final fallback
        return "Yerevan"  # Most common city in Armenia

    def _extract_province(self, shipping_address, billing_address, default_address):
        """Extract province with fallbacks including default_address"""
        # Try shipping address first
        if shipping_address.get('province'):
            return shipping_address.get('province')
        
        # Try billing address
        if billing_address.get('province'):
            return billing_address.get('province')
        
        # Try default address
        if default_address.get('province'):
            return default_address.get('province')
        
        # Final fallback - default to Yerevan
        return "Yerevan"

    def create_courier_order(self, shopify_order):
        """Create draft order with courier and get tracking number"""
        print("Creating courier order...")

        # Extract customer data using priority-based fallback system
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
            "province_id": self.map_region_to_province(customer_data['province']),
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
        print("=== DEBUG Courier Payload ===")
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
        print(f"Updating Shopify order {order_id} with tracking {tracking_number}")

        try:
            # Get fulfillment order ID
            fulfillment_url = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-10/orders/{order_id}/fulfillment_orders.json"
            fulfillment_response = requests.get(fulfillment_url, headers=shopify_headers)

            if fulfillment_response.status_code == 200:
                fulfillment_data = fulfillment_response.json()
                if fulfillment_data.get('fulfillment_orders'):
                    fulfillment_order_id = fulfillment_data['fulfillment_orders'][0]['id']

                    # Create fulfillment with tracking
                    fulfillment_data = {
                        "fulfillment": {
                            "location_id": 1,  # Add default location ID
                            "tracking_info": {
                                "number": str(tracking_number),
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
                    response = requests.post(fulfill_url, json=fulfillment_data, headers=shopify_headers)

                    if response.status_code == 201 or response.status_code == 200:
                        print("✅ Shopify order updated with tracking successfully!")
                        return True
                    else:
                        print(f"❌ Shopify fulfillment failed: {response.status_code} - {response.text}")
                        # Try alternative fulfillment method
                        return self._alternative_shopify_fulfillment(order_id, tracking_number, shopify_headers)
            else:
                print(f"❌ Failed to get fulfillment orders: {fulfillment_response.status_code} - {fulfillment_response.text}")
                # Try alternative fulfillment method
                return self._alternative_shopify_fulfillment(order_id, tracking_number, shopify_headers)

        except Exception as e:
            print(f"❌ Error updating Shopify tracking: {str(e)}")
            # Try alternative fulfillment method
            return self._alternative_shopify_fulfillment(order_id, tracking_number, shopify_headers)

        return False

    def _alternative_shopify_fulfillment(self, order_id, tracking_number, shopify_headers):
        """Alternative method to update Shopify tracking if primary method fails"""
        print(f"🔄 Trying alternative Shopify fulfillment for order {order_id}")
        
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
                print("✅ Shopify order updated with tracking (alternative method)!")
                return True
            else:
                print(f"❌ Alternative Shopify fulfillment failed: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            print(f"❌ Alternative Shopify fulfillment error: {str(e)}")
            return False

    def notify_team(self, shopify_order, tracking_number):
        """Notify about the new order"""
        # Extract customer data for notification
        customer_data = self.extract_customer_data(shopify_order)
        
        message = f"🚚 NEW SHIPPING ORDER\n"
        message += f"Order #: {shopify_order.get('order_number')}\n"
        message += f"Customer: {customer_data['name']}\n"
        message += f"Email: {customer_data['email']}\n"
        message += f"Tracking ID: {tracking_number}\n"
        message += f"Address: {customer_data['address']}\n"
        message += f"Phone: {customer_data['phone']}\n"
        message += f"City: {customer_data['city']}"

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

class CourierAutomation:
    def __init__(self):
        self.shopify_headers = {
            'Content-Type': 'application/json',
            'X-Shopify-Access-Token': SHOPIFY_ADMIN_TOKEN
        }

    def check_and_process_confirmed_orders(self):
        """Check all pending orders and process confirmed ones"""
        print("🔍 Checking for confirmed orders...")
        
        if not pending_orders:
            print("📭 No pending orders to check")
            return
        
        processed_orders = []
        
        for order_id in list(pending_orders):  # Create a copy to avoid modification during iteration
            try:
                # Get order details from Shopify
                order_url = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-10/orders/{order_id}.json"
                response = requests.get(order_url, headers=self.shopify_headers)

                if response.status_code != 200:
                    print(f"❌ Failed to fetch order {order_id}: {response.status_code}")
                    continue

                shopify_order = response.json().get('order', {})
                
                # Check if order is confirmed
                tags = [tag.strip().lower() for tag in shopify_order.get('tags', '').split(',')]
                
                if 'confirmed' in tags:
                    print(f"🎉 Order {order_id} is confirmed! Processing...")
                    success = self.process_order_immediately(order_id)
                    
                    if success:
                        pending_orders.remove(order_id)
                        processed_orders.append(order_id)
                else:
                    print(f"⏳ Order {order_id} still pending confirmation")
                    
            except Exception as e:
                print(f"❌ Error checking order {order_id}: {str(e)}")
        
        if processed_orders:
            print(f"✅ Processed {len(processed_orders)} orders: {processed_orders}")
        else:
            print("📋 No new confirmed orders found")

    def process_order_immediately(self, order_id):
        """Process order immediately"""
        print(f"🚀 PROCESSING ORDER {order_id} IMMEDIATELY")
        
        try:
            # Get order details from Shopify
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
            
            # Create order with courier
            tracking_number = ehdm_service.create_courier_order(shopify_order)

            if tracking_number:
                # Update Shopify with tracking and fulfill
                success = ehdm_service.update_shopify_tracking(order_id, tracking_number, self.shopify_headers)

                if success:
                    # Notify courier team
                    ehdm_service.notify_team(shopify_order, tracking_number)
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
            import traceback
            print(f"📋 Stack trace: {traceback.format_exc()}")
            return False

def generate_webhook_id(webhook_data):
    """Generate unique ID for webhook to prevent duplicates"""
    webhook_str = json.dumps(webhook_data, sort_keys=True)
    return hashlib.md5(webhook_str.encode()).hexdigest()

def background_checker():
    """Background thread to check for confirmed orders every 2 minutes"""
    while True:
        try:
            automation = CourierAutomation()
            automation.check_and_process_confirmed_orders()
        except Exception as e:
            print(f"❌ Background checker error: {str(e)}")
        
        # Wait 2 minutes before next check
        time.sleep(120)

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
            print(f"💡 Add 'confirmed' tag in Shopify - system will auto-process within 2 minutes")
            
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

@app.route('/process-order/<order_id>', methods=['POST'])
def process_order_manual(order_id):
    """Manual endpoint to process an order immediately"""
    print(f"🔄 Manual order processing requested for {order_id}")
    
    try:
        automation = CourierAutomation()
        success = automation.process_order_immediately(order_id)
        
        if success:
            # Remove from pending if it was there
            if order_id in pending_orders:
                pending_orders.remove(order_id)
            return jsonify({"success": True, "message": f"Order {order_id} processed successfully"}), 200
        else:
            return jsonify({"success": False, "message": f"Failed to process order {order_id}"}), 500
            
    except Exception as e:
        print(f"❌ Error in manual order processing: {str(e)}")
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
    return "🚚 Shipping Automation Server is Running!<br><br>Endpoints:<br>- POST /webhook/order-paid<br>- POST /process-order/&lt;order_id&gt;<br>- GET /pending-orders<br>- GET /health"

# Start background checker when app starts
print("🔄 Starting background order checker...")
checker_thread = threading.Thread(target=background_checker)
checker_thread.daemon = True
checker_thread.start()

if __name__ == '__main__':
    print("Starting Shipping Automation Server...")
    app.run(host='0.0.0.0', port=5000, debug=False)
