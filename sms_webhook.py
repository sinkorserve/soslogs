"""
soslog.org -- Combined Webhook Server
Handles:
  1. Twilio inbound SMS → saves to Firestore
  2. Stripe webhook → upgrades user plan on payment

Install: pip install flask twilio firebase-admin requests gunicorn stripe

Environment variables:
    TWILIO_AUTH_TOKEN           -- Twilio console
    GOOGLE_GEOCODING_API_KEY    -- Google Cloud
    FIREBASE_CREDENTIALS_JSON   -- Full Firebase service account JSON as string
    STRIPE_SECRET_KEY           -- Stripe Dashboard → Developers → API Keys → Secret key
    STRIPE_WEBHOOK_SECRET       -- Stripe Dashboard → Developers → Webhooks → signing secret
"""

from flask import Flask, request, jsonify
from twilio.twiml.messaging_response import MessagingResponse
from twilio.request_validator import RequestValidator
from datetime import datetime, timezone
import os, re, json
import requests as http
import firebase_admin
from firebase_admin import credentials, firestore
import stripe

app = Flask(__name__)

# -- Config --
TWILIO_AUTH_TOKEN    = os.environ.get("TWILIO_AUTH_TOKEN", "")
GEOCODING_KEY        = os.environ.get("GOOGLE_GEOCODING_API_KEY", "")
VALIDATE_TWILIO      = os.environ.get("VALIDATE_TWILIO", "true").lower() == "true"
AUTO_REPLY           = "SOSLog received your message. Your record is saved. Reply STOP to opt out."
STRIPE_SECRET_KEY    = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

stripe.api_key = STRIPE_SECRET_KEY

# -- Firebase init --
if not firebase_admin._apps:
    creds_json = os.environ.get("FIREBASE_CREDENTIALS_JSON")
    if creds_json:
        cred = credentials.Certificate(json.loads(creds_json))
    else:
        cred = credentials.Certificate("serviceAccountKey.json")
    firebase_admin.initialize_app(cred)

db = firestore.client()

# ══════════════════════════════════════════════════════════════
# STRIPE WEBHOOK
# ══════════════════════════════════════════════════════════════

def find_phone_by_email(email):
    """Find phone number in Firestore by matching email in emails subcollection."""
    try:
        # Query collectionGroup emails where email == customer email
        email_docs = db.collection_group('emails').where('email', '==', email).get()
        for doc in email_docs:
            # Path: users/{phone}/emails/{emailKey}
            phone = doc.reference.parent.parent.id
            if phone and phone.startswith('+'):
                return phone
    except Exception as e:
        app.logger.warning(f"find_phone_by_email error: {e}")
    return None

def upgrade_user_plan(email, plan, subscription_id=None):
    """Update user's plan in Firestore profile."""
    phone = find_phone_by_email(email)
    if not phone:
        app.logger.warning(f"upgrade_user_plan: no phone found for {email}")
        return False
    try:
        profile_ref = db.collection('users').document(phone).collection('profile').document('data')
        update_data = {
            'plan':           plan,
            'planUpdatedAt':  firestore.SERVER_TIMESTAMP,
            'stripeEmail':    email,
        }
        if subscription_id:
            update_data['stripeSubscriptionId'] = subscription_id
        profile_ref.set(update_data, merge=True)
        app.logger.info(f"Plan updated: {phone} ({email}) → {plan}")
        return True
    except Exception as e:
        app.logger.error(f"upgrade_user_plan Firestore error: {e}")
        return False

@app.route("/stripe/webhook", methods=["POST"])
def stripe_webhook():
    payload   = request.get_data()
    sig_header = request.headers.get("Stripe-Signature", "")

    # Verify webhook signature
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except stripe.error.SignatureVerificationError as e:
        app.logger.warning(f"Stripe signature invalid: {e}")
        return jsonify({"error": "Invalid signature"}), 400
    except Exception as e:
        app.logger.error(f"Stripe webhook error: {e}")
        return jsonify({"error": str(e)}), 400

    event_type = event["type"]
    app.logger.info(f"Stripe event received: {event_type}")

    # ── Successful checkout / new subscription ─────────────────
    if event_type == "checkout.session.completed":
        session = event["data"]["object"]
        email   = session.get("customer_details", {}).get("email") or session.get("customer_email")
        sub_id  = session.get("subscription")
        if email:
            upgrade_user_plan(email, "base", sub_id)
            app.logger.info(f"checkout.session.completed: upgraded {email} to base")

    # ── Subscription renewed (monthly) ────────────────────────
    elif event_type == "invoice.payment_succeeded":
        invoice = event["data"]["object"]
        email   = invoice.get("customer_email")
        sub_id  = invoice.get("subscription")
        if email and invoice.get("billing_reason") != "subscription_create":
            # subscription_create is handled by checkout.session.completed
            upgrade_user_plan(email, "base", sub_id)
            app.logger.info(f"invoice.payment_succeeded: renewed {email}")

    # ── Subscription cancelled / payment failed ───────────────
    elif event_type in ("customer.subscription.deleted", "invoice.payment_failed"):
        obj   = event["data"]["object"]
        email = obj.get("customer_email")
        if not email:
            # Look up customer to get email
            try:
                customer_id = obj.get("customer")
                if customer_id:
                    customer = stripe.Customer.retrieve(customer_id)
                    email = customer.get("email")
            except Exception:
                pass
        if email:
            upgrade_user_plan(email, "free")
            app.logger.info(f"{event_type}: downgraded {email} to free")

    return jsonify({"status": "ok"}), 200


# ══════════════════════════════════════════════════════════════
# TWILIO SMS WEBHOOK
# ══════════════════════════════════════════════════════════════

def enc(email):
    return email.replace('.', '_dot_').replace('@', '_at_')

def records_ref(phone, email_key):
    return (db.collection('users').document(phone)
              .collection('emails').document(email_key)
              .collection('records'))

def find_email_key(phone):
    try:
        docs = list(db.collection('users').document(phone)
                      .collection('emails').stream())
        for d in docs:
            if d.id != 'unlinked':
                return d.id
    except Exception as e:
        app.logger.warning(f"find_email_key: {e}")
    return 'unlinked'

def valid_twilio(req):
    if not VALIDATE_TWILIO:
        return True
    v = RequestValidator(TWILIO_AUTH_TOKEN)
    return v.validate(req.url, req.form.to_dict(),
                      req.headers.get("X-Twilio-Signature", ""))

ADDRESS_PATTERNS = [
    r'(?:at|located at|incident at|location[:\s]+|address[:\s]+)\s*'
    r'(\d+\s+[A-Za-z0-9\s\.,#-]+'
    r'(?:St|Street|Ave|Avenue|Blvd|Boulevard|Rd|Road|Dr|Drive|Ln|Lane|Way|Ct|Court|Pl|Place|Hwy|Highway|Pkwy|Parkway)'
    r'[A-Za-z0-9\s\.,#-]*(?:[A-Z]{2}\s+\d{5})?)',
    r'(\d+\s+[A-Za-z0-9\s\.,#-]+'
    r'(?:St|Street|Ave|Avenue|Blvd|Boulevard|Rd|Road|Dr|Drive|Ln|Lane|Way|Ct|Court|Pl|Place|Hwy|Highway|Pkwy|Parkway)'
    r'[A-Za-z0-9\s\.,#-]*(?:[A-Z]{2}\s+\d{5})?)',
]

def extract_address(text):
    for pattern in ADDRESS_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            addr = match.group(1).strip().rstrip('.,')
            if len(addr) > 8:
                return addr
    return None

def geocode(address, state='', country=''):
    if not address:
        return None
    query = ', '.join(p for p in [address, state, country] if p) if (state or country) else address
    try:
        r = http.get(
            'https://maps.googleapis.com/maps/api/geocode/json',
            params={'address': query, 'key': GEOCODING_KEY},
            timeout=5
        )
        data = r.json()
        if data.get('status') == 'OK' and data.get('results'):
            loc = data['results'][0]['geometry']['location']
            return {
                'lat':       loc['lat'],
                'lng':       loc['lng'],
                'formatted': data['results'][0].get('formatted_address', query),
                'precision': 'address' if (not state and not country) else 'city'
            }
    except Exception as e:
        app.logger.warning(f"Geocoding failed '{query}': {e}")
    return None

@app.route("/sms/inbound", methods=["POST"])
def inbound_sms():
    if not valid_twilio(request):
        return jsonify({"error": "Forbidden"}), 403

    phone   = request.form.get("From", "").strip()
    body    = request.form.get("Body", "").strip()
    sid     = request.form.get("MessageSid")
    city    = request.form.get("FromCity", "")
    state   = request.form.get("FromState", "")
    country = request.form.get("FromCountry", "")
    zipcode = request.form.get("FromZip", "")
    n_media = int(request.form.get("NumMedia", 0))

    extracted_address = extract_address(body)
    if extracted_address:
        geo = geocode(extracted_address)
    else:
        geo = geocode(city, state, country)

    record = {
        "body":           body,
        "messageSid":     sid,
        "from":           phone,
        "timestamp":      firestore.SERVER_TIMESTAMP,
        "timestampLocal": datetime.now(timezone.utc).isoformat(),
        "source":         "sms",
        "location": {
            "city":             city,
            "state":            state,
            "country":          country,
            "zip":              zipcode,
            "lat":              geo["lat"]       if geo else None,
            "lng":              geo["lng"]       if geo else None,
            "formatted":        geo["formatted"] if geo else f"{city}, {state}".strip(", "),
            "precision":        geo.get("precision", "city") if geo else "unknown",
            "extractedAddress": extracted_address,
        },
        "hasMedia":  n_media > 0,
        "mediaUrls": [request.form.get(f"MediaUrl{i}") for i in range(n_media)],
    }

    try:
        email_key = find_email_key(phone)
        records_ref(phone, email_key).document(sid).set(record)
        app.logger.info(f"Saved: users/{phone}/emails/{email_key}/records/{sid}")
    except Exception as e:
        app.logger.error(f"Firestore write failed: {e}")

    resp = MessagingResponse()
    resp.message(AUTO_REPLY)
    return str(resp), 200, {"Content-Type": "text/xml"}


# ══════════════════════════════════════════════════════════════
# HEALTH CHECK
# ══════════════════════════════════════════════════════════════

@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "soslog-webhook"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
