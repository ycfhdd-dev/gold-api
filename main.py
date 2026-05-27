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
            # الرسالة الجديدة
            if "XAU_LOCAL_AVG" in text:
                return _extract_new(text)
            # الرسالة القديمة (احتياطي)
            if "XAU999" in text:
                return _extract_old(text)

    return None


def _extract_new(text):
    """تحليل شكل الرسالة الجديد: DATE=...XAU_LOCAL_AVG=..."""
    patterns = {
        "gold_999":   r"XAU_LOCAL_AVG=(\d+(?:\.\d+)?)",
        "silver_999": r"XAG_LOCAL_AVG=(\d+(?:\.\d+)?)",
        "eur":        r"FX_EUR_DZD=(\d+(?:\.\d+)?)",
        "usd":        r"FX_USD_DZD=(\d+(?:\.\d+)?)",
        # حقول إضافية من الرسالة الجديدة
        "gold_world_usd": r"XAU_WORLD_USD=(\d+(?:\.\d+)?)",
        "gold_world_eur": r"XAU_WORLD_EUR=(\d+(?:\.\d+)?)",
        "silver_world_usd": r"XAG_WORLD_USD=(\d+(?:\.\d+)?)",
        "silver_world_eur": r"XAG_WORLD_EUR=(\d+(?:\.\d+)?)",
        "gold_local_usd": r"XAU_LOCAL_USD=(\d+(?:\.\d+)?)",
        "gold_local_eur": r"XAU_LOCAL_EUR=(\d+(?:\.\d+)?)",
        "silver_local_usd": r"XAG_LOCAL_USD=(\d+(?:\.\d+)?)",
        "silver_local_eur": r"XAG_LOCAL_EUR=(\d+(?:\.\d+)?)",
        "eur_usd":    r"FX_USD_EUR=(\d+(?:\.\d+)?)",
    }
    prices = {}
    for key, pattern in patterns.items():
        match = re.search(pattern, text)
        if match:
            prices[key] = float(match.group(1))
    # نتحقق فقط من الحقول الأساسية الأربعة
    required = {"gold_999", "silver_999", "eur", "usd"}
    return prices if required.issubset(prices.keys()) else None


def _extract_old(text):
    """تحليل شكل الرسالة القديم: XAU999=..."""
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
    if not prices:
        return jsonify({"ok": False, "error": "لا توجد أسعار"}), 500

    # هيكل جديد — نُرجع الحقول مسطّحة مباشرة
    # حتى يتوافق مع price_dialog.py الذي يتوقع XAU_LOCAL_AVG مباشرة
    if "gold_world_usd" in prices:
        flat = {
            "XAU_LOCAL_AVG":  prices.get("gold_999",        0),
            "XAG_LOCAL_AVG":  prices.get("silver_999",      0),
            "XAU_LOCAL_USD":  prices.get("gold_local_usd",  0),
            "XAU_LOCAL_EUR":  prices.get("gold_local_eur",  0),
            "XAG_LOCAL_USD":  prices.get("silver_local_usd",0),
            "XAG_LOCAL_EUR":  prices.get("silver_local_eur",0),
            "XAU_WORLD_USD":  prices.get("gold_world_usd",  0),
            "XAU_WORLD_EUR":  prices.get("gold_world_eur",  0),
            "XAG_WORLD_USD":  prices.get("silver_world_usd",0),
            "XAG_WORLD_EUR":  prices.get("silver_world_eur",0),
            "FX_USD_DZD":     prices.get("usd",             0),
            "FX_EUR_DZD":     prices.get("eur",             0),
            "FX_USD_EUR":     prices.get("eur_usd",         0),
        }
        return jsonify(flat)

    # هيكل قديم — نُرجعه كما كان
    return jsonify({"ok": True, "prices": prices})


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
