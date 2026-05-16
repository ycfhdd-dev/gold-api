from flask import Flask, jsonify
from telethon.sync import TelegramClient
import re, os

API_ID   = 32801016
API_HASH = "06fcbfc7afcc89f286b635a71b6e89c2"
CHANNEL  = -1003598540317

app = Flask(__name__)

def fetch_prices():
    with TelegramClient('gold_session', API_ID, API_HASH) as client:
        messages = client.get_messages(CHANNEL, limit=1)
        if not messages:
            return None
        text = messages[0].text
        patterns = {
            'gold_999':   r'XAU999=(\d+)',
            'silver_999': r'XAG999=(\d+)',
            'eur':        r'EUR=(\d+)',
            'usd':        r'USD=(\d+)',
        }
        prices = {}
        for key, pattern in patterns.items():
            match = re.search(pattern, text)
            if match:
                prices[key] = float(match.group(1))
        return prices

@app.route('/prices')
def get_prices():
    prices = fetch_prices()
    if prices:
        return jsonify({"ok": True, "prices": prices})
    return jsonify({"ok": False}), 500

@app.route('/')
def home():
    return "Gold Price API ✅"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
