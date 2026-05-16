from flask import Flask, jsonify
import requests, re, os

TOKEN   = os.environ.get("BOT_TOKEN", "")
CHANNEL = -1003598540317

app = Flask(__name__)


def fetch_prices():
    """اجلب آخر رسالة من القناة عبر Bot API"""
    url  = f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset=-1&limit=1"
    r    = requests.get(url, timeout=10)
    data = r.json()

    if not data.get("ok") or not data["result"]:
        return None

    # خذ آخر رسالة
    update = data["result"][-1]
    post   = update.get("channel_post", {})
    text   = post.get("text", "")

    # لو آخر رسالة ليست أسعار → ابحث في آخر 20 رسالة
    if "XAU999" not in text:
        url2  = f"https://api.telegram.org/bot{TOKEN}/getUpdates?limit=20"
        r2    = requests.get(url2, timeout=10)
        data2 = r2.json()
        if data2.get("ok"):
            for update in reversed(data2["result"]):
                t = update.get("channel_post", {}).get("text", "")
                if "XAU999" in t:
                    text = t
                    break

    if "XAU999" not in text:
        return None

    patterns = {
        "gold_999":   r"XAU999=(\d+)",
        "silver_999": r"XAG999=(\d+)",
        "eur":        r"EUR=(\d+)",
        "usd":        r"USD=(\d+)",
    }

    prices = {}
    for key, pattern in patterns.items():
        match = re.search(pattern, text)
        if match:
            prices[key] = float(match.group(1))

    return prices if prices else None


@app.route("/prices")
def get_prices():
    prices = fetch_prices()
    if prices:
        return jsonify({"ok": True, "prices": prices})
    return jsonify({"ok": False, "error": "لا توجد أسعار"}), 500


@app.route("/")
def home():
    return "Gold Price API ✅"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
