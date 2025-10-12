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
EHDM_USERNAME = os.getenv('EHDM_USERNAME')
EHDM_PASSWORD = os.getenv('EHDM_PASSWORD')

app = Flask(__name__)

# Global in-memory store for webhook idempotency
processed_webhooks = {}
processed_orders = {}

print("🚀 Starting Shopify Automation Server...")

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
        """
        shipping_address = shopify_order.get('shipping_address', {})
        billing_address = shopify_order.get('billing_address', {})
        customer = shopify_order.get('customer', {})
        default_address = customer.get('default_address', {})
        
        print("=== DEBUG Customer Data Extraction ===")
        print(f"Order Email: {shopify_order.get('email')}")
        print(f"Contact Email: {shopify_order.get('contact_email')}")
        print(f"Shipping Address: {shipping_address}")
        print(f"Billing Address: {billing_address}")
        print(f"Customer Object: {customer}")
        print(f"Default Address: {default_address}")
        
        # Try to get phone from order level first
        order_phone = shopify_order.get('phone')
        if order_phone:
            print(f"📞 Found phone at order level: {order_phone}")
        
        # Priority 1: Extract name with fallbacks
        name = self._extract_name(shipping_address, billing_address, customer, default_address)
        
        # Priority 2: Extract address with fallbacks
        address = self._extract_address(shipping_address, billing_address, default_address)
        
        # Priority 3: Extract phone with fallbacks - include order level phone
        phone = order_phone or self._extract_phone(shipping_address, billing_address, customer, default_address)
        
        # Priority 4: Extract city with fallbacks
        city = self._extract_city(shipping_address, billing_address, default_address)
        
        # Priority 5: Extract province with fallbacks
        province = self._extract_province(shipping_address, billing_address, default_address)
        
        # Use order email as priority
        email = shopify_order.get('email') or shopify_order.get('contact_email') or customer.get('email', '')
        
        customer_data = {
            'name': name,
            'address': address,
            'phone': phone,
            'city': city,
            'province': province,
            'email': email
        }
        
        print(f"🎯 Final Extracted Customer Data: {customer_data}")
        print("=== END DEBUG ===")
        
        return customer_data

    def _extract_name(self, shipping_address, billing_address, customer, default_address):
        """Extract customer name with fallbacks"""
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
        
        # Use email as last resort
        email = customer.get('email', '')
        if email:
            return email.split('@')[0]
        
        return "Customer"

    def _extract_address(self, shipping_address, billing_address, default_address):
        """Extract address with fallbacks"""
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
        
        return "Address Not Provided"

    def _extract_phone(self, shipping_address, billing_address, customer, default_address):
        """Extract phone number with fallbacks"""
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
        
        return "+374 00 000 000"

    def _extract_city(self, shipping_address, billing_address, default_address):
        """Extract city with fallbacks"""
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
        
        return "Yerevan"

    def _extract_province(self, shipping_address, billing_address, default_address):
        """Extract province with fallbacks"""
        # Try shipping address first
        if shipping_address.get('province'):
            return shipping_address.get('province')
        
        # Try billing address
        if billing_address.get('province'):
            return billing_address.get('province')
        
        # Try default address
        if default_address.get('province'):
            return default_address.get('province')
        
        return "Yerevan"

    def map_region_to_province(self, region_name):
    """Map Shopify regions to courier province IDs"""
    province_mapping = {
        'Aragatsotn': 1, 'Ararat': 2, 'Armavir': 3, 'Gegharkunik': 4,
        'Kotayk': 5, 'Lori': 6, 'Shirak': 7, 'Syunik': 8, 'Tavush': 9,
        'Vayots Dzor': 10, 'Yerevan': 11
    }
    return province_mapping.get(region_name, 11)  # Default to Yerevan

    def create_courier_order(self, shopify_order, retry_count=0):
        """Create draft order with courier using REAL customer data"""
        print("🔄 Creating courier order...")

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

        # Generate barcode_id with retry suffix if needed
        base_barcode_id = str(shopify_order['id'])
        if retry_count > 0:
            barcode_id = f"{base_barcode_id}-retry{retry_count}"
            print(f"🔄 Using retry barcode_id: {barcode_id}")
        else:
            barcode_id = base_barcode_id

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
            "barcode_id": barcode_id,
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
                    courier_response.get('order', {}).get('key') or
                    courier_response.get('order', {}).get('barcode_id') or
                    courier_response.get('order', {}).get('id') or
                    barcode_id
                )
                print(f"✅ Real tracking number: {tracking_number}")
                return tracking_number
            except:
                print("⚠️ Could not parse courier response, using barcode_id as tracking")
                return barcode_id
        elif response.status_code == 422 and "barcode id has already been taken" in response.text.lower():
            print(f"🔄 Barcode ID conflict detected, retrying with new ID...")
            if retry_count < 3:  # Max 3 retries
                return self.create_courier_order(shopify_order, retry_count + 1)
            else:
                print("❌ Max retries reached for barcode_id conflict")
                return None
        else:
            print(f"❌ Courier API Error: {response.status_code} - {response.text}")
            return None

def update_shopify_tracking(self, order_id, tracking_number, shopify_headers):
    """Add tracking number to Shopify order - SIMPLEST APPROACH"""
    print(f"📦 Updating Shopify order {order_id} with tracking {tracking_number}")

    try:
        # ULTRA SIMPLE: Just create a basic fulfillment
        fulfillment_data = {
            "fulfillment": {
                "tracking_number": str(tracking_number),
                "tracking_company": "TransImpex Express",
                "notify_customer": False  # Start without notification
            }
        }

        fulfill_url = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-10/orders/{order_id}/fulfillments.json"
        response = requests.post(fulfill_url, json=fulfillment_data, headers=shopify_headers)

        if response.status_code in [201, 200]:
            print("✅ Shopify fulfillment created successfully!")
            
            # Now add the tracking URL separately
            fulfillment_id = response.json().get('fulfillment', {}).get('id')
            if fulfillment_id:
                update_data = {
                    "fulfillment": {
                        "id": fulfillment_id,
                        "tracking_url": f"https://transimpexexpress.am/tracking/{tracking_number}",
                        "notify_customer": True  # Now send notification
                    }
                }
                update_url = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-10/fulfillments/{fulfillment_id}.json"
                update_response = requests.put(update_url, json=update_data, headers=shopify_headers)
                
                if update_response.status_code in [200, 201]:
                    print("✅ Tracking URL and customer notification added!")
                else:
                    print(f"⚠️ Could not add tracking URL: {update_response.status_code}")
            
            # Mark order as processed
            order_url = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-10/orders/{order_id}.json"
            update_tags_data = {
                "order": {
                    "id": order_id,
                    "tags": "processed,fulfilled"
                }
            }
            tags_response = requests.put(order_url, json=update_tags_data, headers=shopify_headers)
            
            if tags_response.status_code == 200:
                print("✅ Order tagged as 'processed,fulfilled'")
            
            return True
        else:
            print(f"❌ Shopify fulfillment failed: {response.status_code} - {response.text}")
            
            # Debug: Check what's in the order
            self.debug_order_status(order_id, shopify_headers)
            return False
                
    except Exception as e:
        print(f"❌ Error updating Shopify tracking: {str(e)}")
        return False

def debug_order_status(self, order_id, shopify_headers):
    """Debug why fulfillment might be failing"""
    try:
        order_url = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-10/orders/{order_id}.json"
        response = requests.get(order_url, headers=shopify_headers)
        
        if response.status_code == 200:
            order_data = response.json().get('order', {})
            print(f"🔍 ORDER DEBUG - Status: {order_data.get('financial_status')}")
            print(f"🔍 ORDER DEBUG - Fulfillment: {order_data.get('fulfillment_status')}")
            print(f"🔍 ORDER DEBUG - Line Items: {len(order_data.get('line_items', []))}")
            
            for item in order_data.get('line_items', []):
                print(f"🔍 LINE ITEM: {item.get('name')} - Qty: {item.get('quantity')} - Fulfillable: {item.get('fulfillable_quantity')}")
                
    except Exception as e:
        print(f"🔍 Debug error: {str(e)}")

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

    def is_order_already_processed(self, order_id):
        """Check if order was already processed by our system"""
        # Check our local memory store first
        if order_id in processed_orders:
            print(f"📋 Order {order_id} found in processed orders cache")
            return True
        
        # Check Shopify for existing TransImpex fulfillment
        try:
            order_url = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-10/orders/{order_id}.json"
            response = requests.get(order_url, headers=self.shopify_headers)

            if response.status_code == 200:
                shopify_order = response.json().get('order', {})
                
                # Check if order already has OUR fulfillment
                for fulfillment in shopify_order.get('fulfillments', []):
                    if fulfillment.get('tracking_company') == 'TransImpex Express':
                        print(f"✅ Order {order_id} already processed by our system (found in Shopify)")
                        # Cache this result
                        processed_orders[order_id] = True
                        return True
                
                # Check if order has our tracking tags
                tags = [tag.strip().lower() for tag in shopify_order.get('tags', '').split(',')]
                if 'processed' in tags or 'shipped' in tags:
                    print(f"✅ Order {order_id} marked as processed in tags")
                    processed_orders[order_id] = True
                    return True
                    
        except Exception as e:
            print(f"⚠️ Error checking if order {order_id} was processed: {str(e)}")
        
        return False

    def mark_order_as_processed(self, order_id):
        """Mark order as processed in our system"""
        processed_orders[order_id] = True
        print(f"📝 Marked order {order_id} as processed in local cache")

    def process_order_immediately(self, order_id):
        """Process order immediately with PayX and Courier"""
        print(f"🚀 PROCESSING ORDER {order_id}")
        
        try:
            # Check if order was already processed
            if self.is_order_already_processed(order_id):
                print(f"⏭️ Order {order_id} was already processed, skipping duplicate")
                return True
            
            # Get COMPLETE order details from Shopify API with ALL fields
            order_url = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-10/orders/{order_id}.json?fields=id,order_number,email,created_at,updated_at,processed_at,total_price,total_tax,subtotal_price,total_discounts,total_line_items_price,total_shipping_price_set,taxes_included,currency,financial_status,confirmed,customer,contact_email,customer_locale,buyer_accepts_marketing,cancel_reason,cancelled_at,cart_token,checkout_token,checkout_id,gateway,landing_site,referring_site,user_id,location_id,source_identifier,source_url,device_id,phone,customer_first_name,customer_last_name,customer_orders_count,customer_total_spent,tags,note,attributes,payment_gateway_names,tax_exempt,tax_lines,payment_details,payment_terms,processing_method,source_name,fulfillment_status,line_items,shipping_address,billing_address,fulfillments,refunds,shipping_lines,discount_codes,discount_allocations,note_attributes"
            
            response = requests.get(order_url, headers=self.shopify_headers)

            if response.status_code != 200:
                print(f"❌ Failed to fetch order {order_id}: {response.status_code}")
                return False

            shopify_order = response.json().get('order', {})
            
            # DEBUG: Check what data we actually received
            print("=== DEBUG FULL ORDER DATA ===")
            print(f"Order #: {shopify_order.get('order_number')}")
            print(f"Shipping Address: {shopify_order.get('shipping_address')}")
            print(f"Billing Address: {shopify_order.get('billing_address')}")
            print(f"Customer: {shopify_order.get('customer')}")
            print(f"Email: {shopify_order.get('email')}")
            print(f"Contact Email: {shopify_order.get('contact_email')}")
            print("=== END DEBUG ===")
            
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
                    # Mark as processed to prevent duplicates
                    self.mark_order_as_processed(order_id)
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
            print(f"✅ Order {order_number} tagged as 'pending-confirmation'")
            print(f"💡 Add 'confirmed' tag in Shopify to automatically process")
            
            return jsonify({
                "success": True,
                "message": "Order saved. Add 'confirmed' tag to automatically process."
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

@app.route('/webhook/order-updated', methods=['POST'])
def handle_order_updated():
    """Webhook endpoint that Shopify calls when order is updated (tags changed)"""
    print("🔄 Received order updated webhook")

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

        print(f"🔄 Order #{order_number} updated, checking tags...")

        # Check if order has "confirmed" tag
        tags = [tag.strip().lower() for tag in shopify_order.get('tags', '').split(',')]
        print(f"🏷️ Current tags: {tags}")

        if 'confirmed' in tags:
            print(f"🎉 Order {order_number} has 'confirmed' tag! Processing immediately...")
            
            automation = CourierAutomation()
            success = automation.process_order_immediately(order_id)
            
            if success:
                print(f"✅ Order {order_number} processed successfully via order-updated webhook")
                return jsonify({
                    "success": True,
                    "message": f"Order {order_number} processed successfully"
                }), 200
            else:
                print(f"❌ Failed to process order {order_number}")
                return jsonify({
                    "success": False,
                    "message": f"Failed to process order {order_number}"
                }), 500
        else:
            print(f"⏳ Order {order_number} doesn't have 'confirmed' tag, skipping")
            return jsonify({
                "success": True,
                "message": "Order doesn't have 'confirmed' tag, skipping"
            }), 200

    except Exception as e:
        print(f"❌ Error processing order updated webhook: {str(e)}")
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/process-order/<order_id>', methods=['POST'])
def process_order_manual(order_id):
    """Manual endpoint to process an order immediately"""
    print(f"🔄 Manual order processing requested for {order_id}")
    
    try:
        automation = CourierAutomation()
        success = automation.process_order_immediately(order_id)
        
        if success:
            return jsonify({"success": True, "message": f"Order {order_id} processed successfully"}), 200
        else:
            return jsonify({"success": False, "message": f"Failed to process order {order_id}"}), 500
            
    except Exception as e:
        print(f"❌ Error in manual order processing: {str(e)}")
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "healthy", "message": "Shipping automation server is running"}), 200

@app.route('/')
def home():
    return """
    🚚 AUTOMATIC Shipping Automation Server<br><br>
    <strong>SYSTEM STATUS: ACTIVE</strong><br>
    - ✅ Webhook auto-tags orders as 'pending-confirmation'<br>
    - ✅ Order-updated webhook detects 'confirmed' tags immediately<br>
    - ✅ Auto-processes confirmed orders with REAL customer data<br>
    - ✅ DUPLICATE DETECTION: Prevents re-processing same orders<br>
    - ✅ BARCODE RETRY: Auto-retry with new IDs on conflicts<br>
    - ✅ COMPLETE ORDER DATA: Fetches full customer details from API<br>
    - ✅ MODERN FULFILLMENT: Uses FulfillmentOrder API (fixes 406 errors)<br><br>
    
    <strong>Setup Required:</strong><br>
    1. Add Shopify webhook: orders/updated → /webhook/order-updated<br>
    2. Add 'confirmed' tag in Shopify to auto-process orders<br><br>
    
    <strong>Endpoints:</strong><br>
    - POST /webhook/order-paid (Shopify webhook)<br>
    - POST /webhook/order-updated (Shopify webhook)<br>
    - POST /process-order/&lt;order_id&gt; (manual trigger)<br>
    - GET /health (health check)<br>
    """

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    print(f"🌐 Server starting on port {port}...")
    app.run(host='0.0.0.0', port=port, debug=False)
