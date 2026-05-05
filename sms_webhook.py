"""
soslog.org — Twilio Inbound SMS Webhook
----------------------------------------
Receives inbound SMS, geocodes city/state → lat/lng,
writes to Firestore under phone-first hierarchy:

  users/{phone}/emails/{encodedEmail}/records/{messageId}
  users/{phone}/emails/unlinked/records/{messageId}  ← if phone not yet linked to email

Install:
    pip install flask twilio firebase-admin requests

Environment variables needed:
    TWILIO_AUTH_TOKEN         — Twilio console
    GOOGLE_GEOCODING_API_KEY  — Google Cloud (same project as Maps JS API)
    FIREBASE_CREDENTIALS_PATH — path to serviceAccountKey.json
                                (Firebase Console → Project Settings → Service Accounts → Generate key)
"""

from flask import Flask, request, jsonify
from twilio.twiml.messaging_response import MessagingResponse
from twilio.request_validator import RequestValidator
from datetime import datetime, timezone
import os
import requests as http

import firebase_admin
from firebase_admin import credentials, firestore

app = Flask(__name__)

# ── Config ───────────────────────────────────────────────────────────────
TWILIO_AUTH_TOKEN    = os.environ.get("TWILIO_AUTH_TOKEN", "your_twilio_auth_token")
GEOCODING_KEY        = os.environ.get("AIzaSyC8GDl5em6tzE4Kf2nomqLGycWMIAlPf_4", "your_geocoding_key")
FIREBASE_CREDS       = os.environ.get("FIREBASE_CREDENTIALS_PATH", "serviceAccountKey.json")
VALIDATE_TWILIO      = True
AUTO_REPLY           = "✅ SOSLog received your message. Your record is saved. Reply STOP to opt out."

# ── Firebase ─────────────────────────────────────────────────────────────
if not firebase_admin._apps:
    cred = credentials.Certificate(FIREBASE_CREDS)
    firebase_admin.initialize_app(cred)
db = firestore.client()

# ── Helpers ──────────────────────────────────────────────────────────────
def enc(email): return email.replace('.','_dot_').replace('@','_at_')

def records_ref(phone, email_key):
    return (db.collection('users').document(phone)
              .collection('emails').document(email_key)
              .collection('records'))

def find_email_key(phone):
    """Return encoded email key linked to this phone, or 'unlinked'."""
    try:
        docs = list(db.collection('users').document(phone)
                      .collection('emails').stream())
        for d in docs:
            if d.id != 'unlinked':
                return d.id
    except Exception as e:
        app.logger.warning(f"find_email_key: {e}")
    return 'unlinked'

import re

# ── Address patterns ──────────────────────────────────────────────────────
# Matches street addresses like:
#   "123 Main St Arlington TX"
#   "at 500 W 3rd St, Fort Worth TX 76102"
#   "located at 1234 Elm Avenue, Dallas, Texas"
ADDRESS_PATTERNS = [
    # "at <address>" or "located at <address>" or "incident at <address>"
    r'(?:at|located at|incident at|location[:\s]+|address[:\s]+)\s*'
    r'(\d+\s+[A-Za-z0-9\s\.,#-]+(?:St|Street|Ave|Avenue|Blvd|Boulevard|Rd|Road|Dr|Drive|Ln|Lane|Way|Ct|Court|Pl|Place|Hwy|Highway|Pkwy|Parkway|Suite|Ste|Apt|#)[A-Za-z0-9\s\.,#-]*'
    r'(?:[A-Z]{2}\s+\d{5})?)',

    # Plain street address: "1234 Main St Dallas TX"
    r'(\d+\s+[A-Za-z0-9\s\.,#-]+(?:St|Street|Ave|Avenue|Blvd|Boulevard|Rd|Road|Dr|Drive|Ln|Lane|Way|Ct|Court|Pl|Place|Hwy|Highway|Pkwy|Parkway)[A-Za-z0-9\s\.,#-]+'
    r'(?:[A-Z]{2}\s+\d{5})?)',
]

def extract_address(text: str) -> str | None:
    """
    Try to extract a street address from SMS message body.
    Returns the address string or None.
    """
    for pattern in ADDRESS_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            addr = match.group(1).strip().rstrip('.,')
            if len(addr) > 8:  # sanity check
                return addr
    return None

def geocode(address_or_city: str, state: str = '', country: str = '') -> dict | None:
    """
    Geocode an address string or city/state/country → lat/lng.
    If full address provided, use it directly for better precision.
    """
    if not address_or_city:
        return None

    # If state/country provided, it's a city-level fallback
    if state or country:
        query = ', '.join(p for p in [address_or_city, state, country] if p)
    else:
        query = address_or_city  # full address string

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

def valid_twilio(req):
    if not VALIDATE_TWILIO:
        return True
    v = RequestValidator(TWILIO_AUTH_TOKEN)
    return v.validate(req.url, req.form.to_dict(),
                      req.headers.get("X-Twilio-Signature",""))

# ── Inbound SMS ───────────────────────────────────────────────────────────
@app.route("/sms/inbound", methods=["POST"])
def inbound_sms():
    if not valid_twilio(request):
        return jsonify({"error": "Forbidden"}), 403

    phone   = request.form.get("From","").strip()   # E.164: +12145551234
    body    = request.form.get("Body","").strip()
    sid     = request.form.get("MessageSid")
    city    = request.form.get("FromCity","")
    state   = request.form.get("FromState","")
    country = request.form.get("FromCountry","")
    zipcode = request.form.get("FromZip","")
    n_media = int(request.form.get("NumMedia",0))

    # 1. Try to extract a street address from the message body
    extracted_address = extract_address(body)

    # 2. Geocode — street address if found, otherwise city/state fallback
    if extracted_address:
        geo = geocode(extracted_address)
        app.logger.info(f"Address extracted: '{extracted_address}' → {geo}")
    else:
        geo = geocode(city, state, country)
        app.logger.info(f"City-level geocode: '{city}, {state}' → {geo}")

    record = {
        "body":           body,
        "messageSid":     sid,
        "from":           phone,
        "timestamp":      firestore.SERVER_TIMESTAMP,
        "timestampLocal": datetime.now(timezone.utc).isoformat(),
        "source":         "sms",
        "location": {
            # Twilio carrier data (city level)
            "city":      city,
            "state":     state,
            "country":   country,
            "zip":       zipcode,
            # Geocoded coordinates
            "lat":       geo["lat"]       if geo else None,
            "lng":       geo["lng"]       if geo else None,
            "formatted": geo["formatted"] if geo else f"{city}, {state}".strip(", "),
            # Precision: "address" if extracted from message, "city" if carrier data
            "precision": geo.get("precision", "city") if geo else "unknown",
            # The address string extracted from message body (if any)
            "extractedAddress": extracted_address or None,
        },
        "hasMedia":   n_media > 0,
        "mediaUrls":  [request.form.get(f"MediaUrl{i}") for i in range(n_media)],
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

# ── Health ────────────────────────────────────────────────────────────────
@app.route("/health")
def health():
    return jsonify({"status":"ok","service":"soslog-sms-webhook"})

if __name__ == "__main__":
    app.run(debug=True, port=5000)
