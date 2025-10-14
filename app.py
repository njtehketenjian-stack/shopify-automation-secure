from flask import Flask, request, jsonify
import requests
import json
import os
import time
import hashlib
import random
import string
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
        # Store receipt data for return handling
        self.receipts_processed = {}
    
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

def _prepare_receipt_data(self, shopify_order):
    """
    Prepare receipt data for EHDM API from Shopify order
    Returns: dict with products, amounts, and receipt details
    """
    try:
        line_items = shopify_order.get('line_items', [])
        products = []
        total_amount = 0
        
        for index, item in enumerate(line_items):
            quantity = int(item.get('quantity', 1))
            price = float(item.get('price', 0))
            total_price = price * quantity
            total_amount += total_price
            
            # Extract product data
            sku = item.get('sku', '')
            product_name = item.get('name', 'Product')[:50]  # Max 50 chars for EHDM
            
            # Generate product codes based on available data
            if sku:
                good_code = sku[:20]  # Use SKU as goodCode if available
                # Try to extract HS code from SKU or use default
                adg_code = self._extract_hs_code(sku) or "8471"  # Default: Automatic data processing machines
            else:
                # Default codes for products without SKU
                good_code = f"SHOP{index+1:03d}"
                adg_code = "8471"  # Default HS code for general goods
            
            product_data = {
                # REQUIRED FIELDS FOR EHDM API:
                "adgCode": adg_code,  # HS Code (Harmonized System)
                "goodCode": good_code,  # Internal code / Barcode
                "goodName": product_name,  # Product name (max 50 chars)
                "quantity": float(quantity),  # Must be double type
                "unit": "piece",  # Unit of measurement
                "price": round(float(total_price), 2),  # Price with max 2 decimal places
                "discount": 0,  # Product discount amount
                "discountType": 0,  # 0 = no discount, 1 = percentage, 2 = fixed amount
                "receiptProductId": index,  # Product index (starts from 0)
                "dep": 1  # Taxation department: 1 = VAT taxable
            }
            products.append(product_data)
        
        # If no products, add a default item with required fields
        if not products:
            total_amount = float(shopify_order.get('total_price', 0))
            products = [{
                "adgCode": "8471",  # Default HS code
                "goodCode": "ONLINE001",
                "goodName": "Online Order Items",
                "quantity": 1.0,
                "unit": "piece", 
                "price": round(float(total_amount), 2),
                "discount": 0,
                "discountType": 0,
                "receiptProductId": 0,
                "dep": 1  # VAT taxable
            }]
        
        # Determine payment method (cash vs card)
        payment_gateway = shopify_order.get('gateway', '').lower()
        if 'cash' in payment_gateway:
            cash_amount = round(float(total_amount), 2)
            card_amount = 0.0
        else:
            cash_amount = 0.0
            card_amount = round(float(total_amount), 2)
        
        receipt_data = {
            "products": products,
            "additionalDiscount": 0,
            "additionalDiscountType": 0,
            "cashAmount": cash_amount,
            "cardAmount": card_amount,
            "partialAmount": 0,
            "prePaymentAmount": 0,
            "partnerTin": "0"  # Use "0" when no TIN available
        }
        
        print(f"✅ Prepared receipt data for {len(products)} products, total: {total_amount}")
        
        # DEBUG: Print product structure
        print("=== DEBUG EHDM PRODUCT STRUCTURE ===")
        for product in products:
            print(f"Product: {product}")
        print("=== END DEBUG ===")
        
        return receipt_data
        
    except Exception as e:
        print(f"❌ Error preparing receipt data: {str(e)}")
        return None

def _extract_hs_code(self, sku):
    """
    Extract HS code from SKU if possible, or use category mapping
    You can customize this based on your product categories
    """
    # Example mapping - customize based on your products
    category_mapping = {
        'CLOTH': '6109',  # T-shirts
        'ELEC': '8517',   # Telephones
        'FOOD': '1905',   # Bread, pastry
        'BOOK': '4901',   # Books
        'BEAUTY': '3304', # Beauty products
    }
    
    # Check if SKU contains category codes
    for category, hs_code in category_mapping.items():
        if category in sku.upper():
            return hs_code
    
    return None  # Will use default

    def _generate_unique_code(self, shopify_order):
        """
        Generate unique code for EHDM receipt (max 30 chars)
        Format: SHOP{order_id}_{random_chars}
        """
        order_id = str(shopify_order['id'])
        random_chars = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        unique_code = f"SHOP{order_id}_{random_chars}"[:30]
        print(f"🔑 Generated unique code: {unique_code}")
        return unique_code

    def generate_fiscal_receipt(self, shopify_order):
        """
        Generate fiscal receipt using EHDM API immediately after order confirmation
        Returns: (success, receipt_data, error_message)
        """
        print("🧾 Generating fiscal receipt for EHDM...")
        
        try:
            # Check if receipt was already generated for this order
            order_id = shopify_order['id']
            if str(order_id) in self.receipts_processed:
                print(f"📋 Receipt already generated for order {order_id}, skipping")
                return True, self.receipts_processed[str(order_id)], "Receipt already exists"
            
            # Validate we have a valid token
            if not self.token:
                print("❌ No valid token for EHDM API")
                return False, None, "No valid authentication token"
            
            # Prepare receipt data
            receipt_data = self._prepare_receipt_data(shopify_order)
            if not receipt_data:
                return False, None, "Failed to prepare receipt data"
            
            # Generate unique code (non-repeating, max 30 chars)
            unique_code = self._generate_unique_code(shopify_order)
            
            # Prepare the complete payload
            payload = {
                "products": receipt_data['products'],
                "additionalDiscount": receipt_data.get('additionalDiscount', 0),
                "additionalDiscountType": receipt_data.get('additionalDiscountType', 0),
                "cashAmount": receipt_data['cashAmount'],
                "cardAmount": receipt_data['cardAmount'],
                "partialAmount": 0,  # No partial payments for new orders
                "prePaymentAmount": 0,  # No prepayments
                "partnerTin": "0",  # Use "0" when no TIN is available
                "uniqueCode": unique_code
            }
            
            # DEBUG: Print payload for verification
            print("=== DEBUG EHDM Receipt Payload ===")
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            print("=== END DEBUG ===")
            
            # Make API call to generate receipt
            headers = {
                'Authorization': f'Bearer {self.token}',
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            }
            
            print("📤 Sending receipt to EHDM API...")
            response = requests.post(f"{self.base_url}/api/Hdm/Print", 
                                   json=payload, 
                                   headers=headers)
            
            if response.status_code == 200:
                result = response.json()
                print("✅ Fiscal receipt generated successfully!")
                
                # DEBUG: Print full response to see what we're getting
                print("=== DEBUG EHDM API Response ===")
                print(json.dumps(result, indent=2, ensure_ascii=False))
                print("=== END DEBUG ===")
                
                # Extract receipt URL and ID from response
                receipt_url = result.get('link')
                receipt_id = result.get('receiptId')
                
                if not receipt_url:
                    print("⚠️ No receipt URL found in API response")
                    # Try to construct URL from pattern if not provided
                    if receipt_id:
                        receipt_url = f"https://store.payx.am/Receipt/2025/{EHDM_USERNAME}/productSale/10/13/{EHDM_USERNAME}_{receipt_id}_2009582.pdf"
                        print(f"🔄 Constructed receipt URL: {receipt_url}")
                
                # Store receipt data for future reference
                receipt_info = {
                    'receipt_id': receipt_id,
                    'unique_code': unique_code,
                    'link': receipt_url,
                    'response_data': result
                }
                
                # Cache the receipt
                self.receipts_processed[str(order_id)] = receipt_info
                
                # Send receipt via email to customer
                if receipt_url:
                    self._send_receipt_email_with_url(receipt_url, shopify_order)
                else:
                    print("⚠️ Cannot send email - no receipt URL available")
                
                return True, receipt_info, "Receipt generated successfully"
                
            else:
                error_msg = f"EHDM API Error: {response.status_code} - {response.text}"
                print(f"❌ {error_msg}")
                return False, None, error_msg
                
        except Exception as e:
            error_msg = f"Receipt generation error: {str(e)}"
            print(f"❌ {error_msg}")
            return False, None, error_msg

    def _send_receipt_email_with_url(self, receipt_url, shopify_order):
        """
        Send receipt via email to customer using the receipt PDF URL
        """
        try:
            customer_email = shopify_order.get('email') or shopify_order.get('contact_email')
            if not customer_email:
                print("⚠️ No customer email found for receipt sending")
                return False
            
            # Since we can't use EHDM's email API without receiptId,
            # we'll log the receipt URL for manual sending or implement custom email
            print(f"📧 Receipt PDF URL for customer {customer_email}: {receipt_url}")
            print(f"💡 Manually send this URL to customer or implement custom email service")
            
            # TODO: Implement custom email service to send receipt PDF
            # For now, we log the URL and can manually email customers
            
            return True
                
        except Exception as e:
            print(f"⚠️ Error preparing receipt email: {str(e)}")
            return False

    def process_order_refund(self, shopify_order, refund_amount=None):
        """
        Process refund/return for an order using EHDM Reverse API
        """
        try:
            order_id = shopify_order['id']
            
            # Check if we have receipt data for this order
            if str(order_id) not in self.receipts_processed:
                print(f"❌ No receipt found for order {order_id}, cannot process refund")
                return False, "No receipt found for this order"
            
            receipt_data = self.receipts_processed[str(order_id)]
            receipt_id = receipt_data.get('receipt_id')
            
            if not receipt_id:
                print(f"❌ No receipt ID found for order {order_id}")
                return False, "No receipt ID available"
            
            # Use provided refund amount or full order amount
            if refund_amount is None:
                refund_amount = float(shopify_order.get('total_price', 0))
            
            headers = {
                'Authorization': f'Bearer {self.token}',
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            }
            
            # For full refund, we use ReverseByReceiptId
            refund_data = {
                "receiptId": receipt_id
            }
            
            print(f"🔄 Processing refund for order {order_id}, amount: {refund_amount}")
            response = requests.post(f"{self.base_url}/api/Hdm/ReverseByReceiptId", 
                                   json=refund_data, 
                                   headers=headers)
            
            if response.status_code == 200:
                print("✅ Refund processed successfully in EHDM system!")
                return True, "Refund processed successfully"
            else:
                error_msg = f"Refund API Error: {response.status_code} - {response.text}"
                print(f"❌ {error_msg}")
                return False, error_msg
                
        except Exception as e:
            error_msg = f"Refund processing error: {str(e)}"
            print(f"❌ {error_msg}")
            return False, error_msg

    def _create_fulfillment_simple(self, order_id, tracking_number, shopify_headers, receipt_url=None):
        """
        Simple fulfillment approach that should work with your API permissions
        """
        try:
            tracking_url = f"https://transimpexexpress.am/track?key={tracking_number}"
            
            fulfillment_data = {
                "fulfillment": {
                    "location_id": self._get_primary_location_id(shopify_headers),
                    "tracking_number": str(tracking_number),
                    "tracking_company": "TransImpex Express",
                    "tracking_urls": [tracking_url],
                    "notify_customer": True
                }
            }

            fulfill_url = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-10/orders/{order_id}/fulfillments.json"
            response = requests.post(fulfill_url, json=fulfillment_data, headers=shopify_headers)

            if response.status_code in [201, 200]:
                print("✅ Simple fulfillment created successfully!")
                
                # Add receipt URL to order notes if available
                if receipt_url:
                    self._update_order_with_receipt_url(order_id, receipt_url, shopify_headers)
                
                return True
            else:
                print(f"❌ Simple fulfillment failed: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            print(f"❌ Simple fulfillment error: {str(e)}")
            return False

    def _get_primary_location_id(self, shopify_headers):
        """
        Get primary location ID for fulfillment
        """
        try:
            locations_url = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-10/locations.json"
            response = requests.get(locations_url, headers=shopify_headers)
            
            if response.status_code == 200:
                locations = response.json().get('locations', [])
                if locations:
                    return locations[0]['id']
        except Exception as e:
            print(f"⚠️ Could not get location ID: {str(e)}")
        
        return None

    def update_shopify_tracking_with_shipping_links(self, order_id, tracking_number, shopify_headers, receipt_url=None):
        """Enhanced: Update Shopify with proper shipping links AND receipt URL"""
        print(f"📦 Updating Shopify order {order_id} with tracking {tracking_number}")
        
        # Try simple fulfillment first (most reliable)
        if self._create_fulfillment_simple(order_id, tracking_number, shopify_headers, receipt_url):
            self._mark_order_processed(order_id, shopify_headers)
            return True
        
        # Fallback: Manual order update
        print("🔄 Trying manual order update with tracking URL and receipt URL...")
        return self._update_order_manually(order_id, tracking_number, shopify_headers, receipt_url)

    def _update_order_manually(self, order_id, tracking_number, shopify_headers, receipt_url=None):
        """
        Manual order update as fallback
        """
        try:
            tracking_url = f"https://transimpexexpress.am/track?key={tracking_number}"
            
            note_attributes = [
                {
                    "name": "tracking_number",
                    "value": str(tracking_number)
                },
                {
                    "name": "tracking_url", 
                    "value": tracking_url
                },
                {
                    "name": "courier",
                    "value": "TransImpex Express"
                }
            ]
            
            # Add receipt URL if provided
            if receipt_url:
                note_attributes.append({
                    "name": "fiscal_receipt_url",
                    "value": receipt_url
                })
                print(f"📄 Adding fiscal receipt URL to order: {receipt_url}")
            
            order_update_data = {
                "order": {
                    "id": order_id,
                    "fulfillment_status": "fulfilled",
                    "tags": "processed,fulfilled",
                    "note_attributes": note_attributes
                }
            }
            
            order_url = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-10/orders/{order_id}.json"
            response = requests.put(order_url, json=order_update_data, headers=shopify_headers)
            
            if response.status_code == 200:
                print("✅ Order manually updated with tracking URL and receipt URL!")
                return True
            else:
                print(f"❌ Manual update failed: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            print(f"❌ Manual update error: {str(e)}")
            return False

    def _update_order_with_receipt_url(self, order_id, receipt_url, shopify_headers):
        """Helper method to update order with receipt URL"""
        try:
            update_data = {
                "order": {
                    "id": order_id,
                    "note_attributes": [
                        {
                            "name": "fiscal_receipt_url",
                            "value": receipt_url
                        }
                    ]
                }
            }
            order_url = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-10/orders/{order_id}.json"
            response = requests.put(order_url, json=update_data, headers=shopify_headers)
            if response.status_code == 200:
                print("✅ Receipt URL added to order notes!")
            else:
                print(f"⚠️ Could not add receipt URL to order: {response.status_code}")
        except Exception as e:
            print(f"⚠️ Error adding receipt URL: {str(e)}")

    def extract_customer_data(self, shopify_order):
        """
        Extract customer data - ORDER DATA FIRST, customer data as fallback
        """
        # PRIMARY: Order-level shipping address (always contains checkout data)
        shipping_address = shopify_order.get('shipping_address', {})
        billing_address = shopify_order.get('billing_address', {})
        
        # SECONDARY: Customer object (fallback only)
        customer = shopify_order.get('customer', {})
        default_address = customer.get('default_address', {})
        
        print("=== DEBUG ORDER-BASED DATA EXTRACTION ===")
        print(f"ORDER Shipping: {shipping_address}")
        print(f"ORDER Billing: {billing_address}")
        print(f"ORDER Email: {shopify_order.get('email')}")
        print(f"ORDER Contact Email: {shopify_order.get('contact_email')}")
        print(f"ORDER Phone: {shopify_order.get('phone')}")
        
        # PRIORITY 1: ORDER-LEVEL DATA (always use this first)
        name = self._extract_name_from_order(shipping_address, billing_address, shopify_order)
        address = self._extract_address_from_order(shipping_address, billing_address)
        phone = shopify_order.get('phone') or self._extract_phone_from_order(shipping_address, billing_address)
        email = shopify_order.get('email') or shopify_order.get('contact_email', '')
        
        # PRIORITY 2: Only use customer data as FALLBACK if order data is missing
        if not name or name == "Customer":
            name = self._extract_name_from_customer(customer, default_address)
            print("🔄 Using customer fallback for name")
        
        if not address or address == "Address Not Provided":
            address = self._extract_address_from_customer(default_address)
            print("🔄 Using customer fallback for address")
        
        if not phone or phone == "+374 00 000 000":
            phone = self._extract_phone_from_customer(customer, default_address)
            print("🔄 Using customer fallback for phone")
        
        if not email:
            email = customer.get('email', '')
            print("🔄 Using customer fallback for email")
        
        customer_data = {
            'name': name,
            'address': address,
            'phone': phone,
            'city': self._extract_city(shipping_address, billing_address, default_address),
            'province': self._extract_province(shipping_address, billing_address, default_address),
            'email': email
        }
        
        print(f"🎯 FINAL Customer Data: {customer_data}")
        print("=== END DEBUG ===")
        
        return customer_data

    def _extract_name_from_order(self, shipping_address, billing_address, order):
        """Extract name from ORDER data first"""
        # Try shipping address from order
        if shipping_address.get('first_name') or shipping_address.get('last_name'):
            first_name = shipping_address.get('first_name', '').strip()
            last_name = shipping_address.get('last_name', '').strip()
            if first_name or last_name:
                return f"{first_name} {last_name}".strip()
        
        # Try billing address from order
        if billing_address.get('first_name') or billing_address.get('last_name'):
            first_name = billing_address.get('first_name', '').strip()
            last_name = billing_address.get('last_name', '').strip()
            if first_name or last_name:
                return f"{first_name} {last_name}".strip()
        
        return ""  # Empty string to trigger fallback

    def _extract_address_from_order(self, shipping_address, billing_address):
        """Extract address from ORDER data first"""
        # Try shipping address from order
        if shipping_address.get('address1'):
            address1 = shipping_address.get('address1', '').strip()
            address2 = shipping_address.get('address2', '').strip()
            address = f"{address1} {address2}".strip()
            if address:
                return address
        
        # Try billing address from order
        if billing_address.get('address1'):
            address1 = billing_address.get('address1', '').strip()
            address2 = billing_address.get('address2', '').strip()
            address = f"{address1} {address2}".strip()
            if address:
                return address
        
        return ""  # Empty string to trigger fallback

    def _extract_phone_from_order(self, shipping_address, billing_address):
        """Extract phone from ORDER data first"""
        if shipping_address.get('phone'):
            return shipping_address.get('phone', '').strip()
        
        if billing_address.get('phone'):
            return billing_address.get('phone', '').strip()
        
        return ""  # Empty string to trigger fallback

    def _extract_name_from_customer(self, customer, default_address):
        """Extract name from customer data (fallback only)"""
        if customer.get('first_name') or customer.get('last_name'):
            first_name = customer.get('first_name', '').strip()
            last_name = customer.get('last_name', '').strip()
            if first_name or last_name:
                return f"{first_name} {last_name}".strip()
        
        if default_address.get('first_name') or default_address.get('last_name'):
            first_name = default_address.get('first_name', '').strip()
            last_name = default_address.get('last_name', '').strip()
            if first_name or last_name:
                return f"{first_name} {last_name}".strip()
        
        return "Customer"

    def _extract_address_from_customer(self, default_address):
        """Extract address from customer data (fallback only)"""
        if default_address.get('address1'):
            address1 = default_address.get('address1', '').strip()
            address2 = default_address.get('address2', '').strip()
            address = f"{address1} {address2}".strip()
            if address:
                return address
        
        return "Address Not Provided"

    def _extract_phone_from_customer(self, customer, default_address):
        """Extract phone from customer data (fallback only)"""
        if customer.get('phone'):
            return customer.get('phone', '').strip()
        
        if default_address.get('phone'):
            return default_address.get('phone', '').strip()
        
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
        """Add tracking number to Shopify order - MULTI-APPROACH"""
        print(f"📦 Updating Shopify order {order_id} with tracking {tracking_number}")

        # Use simple fulfillment approach
        success = self._create_fulfillment_simple(order_id, tracking_number, shopify_headers)
        
        if success:
            self._mark_order_processed(order_id, shopify_headers)
            return True
        else:
            # Fallback to manual update
            return self._update_order_manually(order_id, tracking_number, shopify_headers)

    def _mark_order_processed(self, order_id, shopify_headers):
        """Mark order as processed in Shopify"""
        try:
            order_url = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-10/orders/{order_id}.json"
            update_tags_data = {
                "order": {
                    "id": order_id,
                    "tags": "processed,fulfilled"
                }
            }
            requests.put(order_url, json=update_tags_data, headers=shopify_headers)
            print("✅ Order tagged as 'processed,fulfilled'")
        except Exception as e:
            print(f"⚠️ Could not update order tags: {str(e)}")

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
            
            # Get COMPLETE order details from Shopify API - NO FIELD FILTERING!
            order_url = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-10/orders/{order_id}.json"
            
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

    def process_order_from_webhook(self, shopify_order):
        """Process order using webhook data directly"""
        order_id = shopify_order['id']
        print(f"🚀 PROCESSING ORDER FROM WEBHOOK {order_id}")
        
        try:
            # Check if order was already processed
            if self.is_order_already_processed(order_id):
                print(f"⏭️ Order {order_id} was already processed, skipping duplicate")
                return True
            
            # DEBUG: Check the complete webhook data
            print("=== DEBUG WEBHOOK DATA ===")
            print(f"Order #: {shopify_order.get('order_number')}")
            print(f"Shipping Address: {shopify_order.get('shipping_address')}")
            print(f"Billing Address: {shopify_order.get('billing_address')}")
            print(f"Email: {shopify_order.get('email')}")
            print(f"Phone: {shopify_order.get('phone')}")
            print("=== END DEBUG ===")
            
            # Process with EHDM service using webhook data
            ehdm_service = EHDMService()
            
            # Generate fiscal receipt with PayX first - FIX: ADDED RECEIPT GENERATION
            if ehdm_service.login():
                print("✅ PayX login successful, generating fiscal receipt...")
                
                # GENERATE EHDM RECEIPT BEFORE COURIER ORDER
                receipt_success, receipt_data, receipt_message = ehdm_service.generate_fiscal_receipt(shopify_order)
                
                if receipt_success:
                    print(f"✅ Fiscal receipt generated successfully! Receipt ID: {receipt_data.get('receipt_id')}")
                else:
                    print(f"❌ Failed to generate fiscal receipt: {receipt_message}")
                    # Continue with shipping even if receipt fails? Or stop processing?
                    # For now, we'll continue but log the error
            else:
                print("❌ PayX login failed, but continuing with shipping")
            
            # Create order with courier using REAL customer data from webhook
            tracking_number = ehdm_service.create_courier_order(shopify_order)
            
            if tracking_number:
                # Update Shopify with tracking and fulfill - ENHANCED: With shipping links AND receipt URL
                receipt_url = receipt_data.get('link') if receipt_success else None
                success = ehdm_service.update_shopify_tracking_with_shipping_links(
                    order_id, tracking_number, self.shopify_headers, receipt_url
                )
                
                if success:
                    print(f"✅ Order {order_id} fully processed! Tracking: {tracking_number}")
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
            
            # Process using webhook data directly
            automation = CourierAutomation()
            success = automation.process_order_from_webhook(shopify_order)
            
            if success:
                print(f"✅ Order {order_number} processed successfully via webhook!")
                return jsonify({"success": True, "message": f"Order {order_number} processed"}), 200
            else:
                print(f"❌ Failed to process order {order_number}")
                return jsonify({"success": False, "message": f"Failed to process order {order_number}"}), 500
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
    - ✅ MODERN FULFILLMENT: Uses FulfillmentOrder API<br><br>
    
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
