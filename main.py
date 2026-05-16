from flask import Flask, jsonify
import requests, re, os

TOKEN    = os.environ.get("BOT_TOKEN", "")
CHANNEL  = "@golderpdz"   # اسم القناة العامة

app = Flask(__name__)


def fetch_prices():
    """
    اجلب آخر رسائل القناة عبر الاسم العام للقناة.
    نستخدم forwardMessage لجلب آخر رسالة تحتوي أسعار.
    """
    # جلب آخر 10 تحديثات
    for offset in ["-1", None]:
        params = {"limit": 20}
        if offset:
            params["offset"] = offset

        url  = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
        r    = requests.get(url, params=params, timeout=10)
        data = r.json()

        if not data.get("ok"):
            continue

        for update in reversed(data.get("result", [])):
            text = (
                update.get("channel_post", {}).get("text", "") or
                update.get("message", {}).get("text", "")
            )
            if "XAU999" in text:
                return _extract(text)

    return None


def _extract(text):
    patterns = {
        "gold_999":   r"XAU999=(\d+(?:\.\d+)?)",
        "silver_999": r"XAG999=(\d+(?:\.\d+)?)",
        "eur":        r"EUR=(\d+(?:\.\d+)?)",
        "usd":        r"USD=(\d+(?:\.\d+)?)",
    }
    prices = {}
    for key, pattern in patterns.items():
        match = re.search(pattern, text)
        if match:
            prices[key] = float(match.group(1))
    return prices if len(prices) == 4 else None


@app.route("/prices")
def get_prices():
    prices = fetch_prices()
    if prices:
        return jsonify({"ok": True, "prices": prices})
    return jsonify({"ok": False, "error": "لا توجد أسعار"}), 500


@app.route("/debug")
def debug():
    """للتشخيص فقط — يعرض آخر رسائل البوت"""
    url  = f"https://api.telegram.org/bot{TOKEN}/getUpdates?limit=20"
    r    = requests.get(url, timeout=10)
    return r.json()


@app.route("/")
def home():
    return "Gold Price API ✅"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
