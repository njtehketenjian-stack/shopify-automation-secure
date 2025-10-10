from flask import Flask, request, jsonify
import requests
import json
import os
import time
import threading
import hashlib
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

# Global in-memory store for webhook idempotency
processed_webhooks = {}
processed_orders = {}

class EHDMService:
    def __init__(self):
        self.base_url = "https://store.payx.am"
        self.token = None
        # FIX: Added courier headers
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

        # DEBUG: Check what address data we're receiving from Shopify
        print("=== DEBUG Shopify Address Data ===")
        print(f"Shipping Address: {shipping_address}")
        print(f"Billing Address: {billing_address}")
        print("=== END DEBUG ===")

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

        # Construct the API payload - FIXED: Use REAL customer data from Shopify
        address_to = f"{shipping_address.get('address1', '')} {shipping_address.get('address2', '')}".strip()
        person_name = f"{shipping_address.get('first_name', '')} {shipping_address.get('last_name', '')}".strip()
        phone = shipping_address.get('phone', '')
        city = shipping_address.get('city', '')

        # Use fallbacks only if data is completely missing
        if not address_to:
            address_to = "Address Not Provided"
        if not person_name:
            person_name = "Customer"
        if not phone:
            phone = "000000000"
        if not city:
            city = "Unknown"

        courier_order_data = {
            "address_to": address_to[:100],
            "province_id": self.map_region_to_province(shipping_address.get('province')),
            "city": city[:50],
            "package_type": "Parcel",
            "parcel_weight": "1.0",
            "order_products": order_products,
            "recipient_type": "Individual",
            "person_name": person_name[:50],
            "phone": phone[:20],
            "barcode_id": str(shopify_order['id']),
            "is_payed": 1,
            "delivery_method": "home",
            "return_receipt": False,
            "notes": f"Shopify Order #{shopify_order.get('order_number', '')}",
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

            # DEBUG: Log the full response to see what tracking data we get
            print(f"=== DEBUG Courier Response: {response.text}")

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

    def cancel_courier_order(self, shopify_order_id, barcode_id):
        """Cancel order in TransImpex when cancelled in Shopify"""
        print(f"🔄 Attempting to cancel courier order for Shopify order {shopify_order_id}")
        
        try:
            # First, we need to find the TransImpex order ID using barcode_id
            search_url = f"{COURIER_BASE_URL}/api/orders?barcode_id={barcode_id}"
            search_response = requests.get(search_url, headers=self.courier_headers)
            
            if search_response.status_code == 200:
                orders_data = search_response.json()
                if orders_data.get('data') and len(orders_data['data']) > 0:
                    transimpex_order_id = orders_data['data'][0]['id']
                    
                    # Cancel the order
                    cancel_url = f"{COURIER_BASE_URL}/api/orders/{transimpex_order_id}/cancel"
                    cancel_response = requests.post(cancel_url, headers=self.courier_headers)
                    
                    if cancel_response.status_code == 200:
                        print(f"✅ Successfully cancelled TransImpex order {transimpex_order_id} for Shopify order {shopify_order_id}")
                        return True
                    else:
                        print(f"❌ Failed to cancel TransImpex order: {cancel_response.status_code} - {cancel_response.text}")
                else:
                    print(f"⚠️ No TransImpex order found with barcode_id: {barcode_id}")
            else:
                print(f"❌ Failed to search for TransImpex order: {search_response.status_code} - {search_response.text}")
                
        except Exception as e:
            print(f"❌ Error cancelling courier order: {str(e)}")
        
        return False

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
                        # Debug the response
                        print(f"=== DEBUG Shopify Response: {response.text} ===")
            else:
                print(f"❌ Failed to get fulfillment orders: {fulfillment_response.status_code} - {fulfillment_response.text}")

        except Exception as e:
            print(f"❌ Error updating Shopify tracking: {str(e)}")

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
            # Check if order is already being processed to prevent duplicates
            if f"processing_{shopify_order_id}" in processed_orders:
                print(f"🔄 Order {shopify_order_id} is already being processed, skipping duplicate")
                return False

            # Check order status in Shopify
            order_url = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-10/orders/{shopify_order_id}.json"
            response = requests.get(order_url, headers=self.shopify_headers)

            if response.status_code == 200:
                order_data = response.json().get('order', {})
                tags = order_data.get('tags', '').split(',')
                
                # DEBUG: Print order status
                print(f"=== DEBUG Order Status ===")
                print(f"Order ID: {shopify_order_id}")
                print(f"Tags: {tags}")
                print(f"Fulfillment Status: {order_data.get('fulfillment_status')}")
                print(f"Financial Status: {order_data.get('financial_status')}")
                print("=== END DEBUG ===")

                # Check if order already has fulfillment (already processed)
                if order_data.get('fulfillment_status') in ['fulfilled', 'partial']:
                    print(f"✅ Order {shopify_order_id} already fulfilled, skipping")
                    return False

                # Check if order is confirmed
                if 'confirmed' in [tag.strip().lower() for tag in tags]:
                    print(f"Order {shopify_order_id} confirmed! Processing...")

                    # Mark as processing to prevent duplicates
                    processed_orders[f"processing_{shopify_order_id}"] = True

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
                    # Trigger cancellation in TransImpex
                    self.cancel_order_in_transimpex(shopify_order_id)
                    return False

            print(f"Attempt {attempt + 1}: Order not confirmed yet. Waiting 5 minutes...")
            time.sleep(300)  # Wait 5 minutes

        print(f"Order {shopify_order_id} confirmation timeout after 24 hours")
        return False

    def cancel_order_in_transimpex(self, shopify_order_id):
        """Cancel order in TransImpex when cancelled in Shopify"""
        print(f"🔄 Processing cancellation for Shopify order {shopify_order_id}")
        
        ehdm_service = EHDMService()
        success = ehdm_service.cancel_courier_order(shopify_order_id, str(shopify_order_id))
        
        if success:
            print(f"✅ Successfully processed cancellation for order {shopify_order_id}")
        else:
            print(f"❌ Failed to cancel TransImpex order for {shopify_order_id}")

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
        print(f"🎯 BACKGROUND THREAD STARTED for order {order_id}")
        try:
            automation = CourierAutomation()
            print(f"🔧 CourierAutomation created for order {order_id}")
            
            if automation.wait_for_confirmation(order_id):
                print(f"✅ CONFIRMED! Processing order {order_id}")
                process_confirmed_order(order_id)
            else:
                print(f"❌ Order {order_id} was not confirmed or was cancelled")
                
        except Exception as e:
            print(f"💥 BACKGROUND THREAD CRASHED for order {order_id}: {str(e)}")
            import traceback
            print(f"📋 Stack trace: {traceback.format_exc()}")

    thread = threading.Thread(target=run_check)
    thread.daemon = True
    thread.start()
    print(f"🎯 Background thread launched for order {order_id}")

def process_confirmed_order(order_id):
    """Process order that has been confirmed"""
    # Check if already processed to prevent duplicates
    if f"processed_{order_id}" in processed_orders:
        print(f"🔄 Order {order_id} already processed, skipping duplicate")
        return

    # Mark as processing
    processed_orders[f"processed_{order_id}"] = True

    automation = CourierAutomation()
    ehdm_service = EHDMService()

    # Get order details from Shopify
    order_url = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-10/orders/{order_id}.json"
    response = requests.get(order_url, headers=automation.shopify_headers)

    if response.status_code == 200:
        shopify_order = response.json().get('order', {})
        
        # Check if order already has fulfillment
        if shopify_order.get('fulfillment_status') in ['fulfilled', 'partial']:
            print(f"✅ Order {order_id} already fulfilled, skipping")
            return
        
        # NEW: Generate fiscal receipt with PayX first
        if ehdm_service.login():
            print("✅ PayX login successful, ready for receipt generation")
            # TODO: Add receipt generation logic here
        else:
            print("❌ PayX login failed, but continuing with shipping")
        
        # Create order with courier using EHDMService (which now has proper headers)
        tracking_number = ehdm_service.create_courier_order(shopify_order)

        if tracking_number:
            # Update Shopify with tracking and fulfill
            success = ehdm_service.update_shopify_tracking(order_id, tracking_number, automation.shopify_headers)

            if success:
                # Notify courier team
                ehdm_service.notify_team(shopify_order, tracking_number)
                print(f"✅ Order {order_id} fully processed! Tracking: {tracking_number}")
                
                # Clean up processing flag
                if f"processing_{order_id}" in processed_orders:
                    del processed_orders[f"processing_{order_id}"]
            else:
                print(f"❌ Failed to update Shopify with tracking for order {order_id}")
        else:
            print(f"❌ Failed to create courier order for order {order_id}")

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
    print("Starting Shipping Automation Server...")
    app.run(host='0.0.0.0', port=5000, debug=True)
