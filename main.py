from flask import Flask, jsonify, request
import requests, re, os, datetime

TOKEN    = os.environ.get("BOT_TOKEN", "")
CHANNEL  = "@golderpdz"   # اسم القناة العامة

# ── إعدادات Supabase (أرشيف الأسعار الدائم) ──────────────────────────────
SUPABASE_URL   = os.environ.get("SUPABASE_URL", "").rstrip("/")   # مثال: https://xxxx.supabase.co
SUPABASE_KEY   = os.environ.get("SUPABASE_KEY", "")               # service_role key
LOG_TOKEN      = os.environ.get("LOG_TOKEN", "")                  # سرّ بسيط لحماية /log

app = Flask(__name__)


def _now_algeria():
    """الجزائر UTC+1 طوال السنة."""
    return datetime.datetime.utcnow() + datetime.timedelta(hours=1)


def _supabase_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }


def fetch_prices():
    """اجلب آخر رسائل القناة عبر getUpdates."""
    for offset in ["-1", None]:
        params = {"limit": 20}
        if offset:
            params["offset"] = offset

        url  = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
        try:
            r    = requests.get(url, params=params, timeout=10)
            data = r.json()
        except Exception:
            continue

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


def fetch_last_from_supabase():
    """حبل النجاة للمستخدم: جلب آخر سعر حقيقي مسجل من قاعدة البيانات في حال تعطل تليجرام"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    try:
        params = {
            "select": "gold_999,silver_999,eur,usd",
            "order": "recorded_at.desc",
            "limit": "1"
        }
        r = requests.get(f"{SUPABASE_URL}/rest/v1/price_history", headers=_supabase_headers(), params=params, timeout=5)
        rows = r.json()
        if rows and len(rows) > 0:
            last_row = rows[0]
            return {
                "gold_999": last_row.get("gold_999"),
                "silver_999": last_row.get("silver_999"),
                "eur": last_row.get("eur"),
                "usd": last_row.get("usd")
            }
    except Exception as e:
        print(f"فشل جلب الاحتياطي من Supabase: {e}")
    return None


def _extract_new(text):
    """تحليل شكل الرسالة الجديد: DATE=...XAU_LOCAL_AVG=..."""
    patterns = {
        "gold_999":   r"XAU_LOCAL_AVG=(\d+(?:\.\d+)?)",
        "silver_999": r"XAG_LOCAL_AVG=(\d+(?:\.\d+)?)",
        "eur":        r"FX_EUR_DZD=(\d+(?:\.\d+)?)",
        "usd":        r"FX_USD_DZD=(\d+(?:\.\d+)?)",
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
        prices = fetch_last_from_supabase()

    if prices:
        # تطابق كامل مع متطلبات دالة fetch_live_api_price_999 في تطبيق المستخدم
        formatted_prices = {
            "XAU_LOCAL_AVG": prices.get("gold_999"),
            "XAG_LOCAL_AVG": prices.get("silver_999"),
            "XAU_LOCAL_USD": prices.get("gold_local_usd") or prices.get("gold_999"),
            "XAG_LOCAL_USD": prices.get("silver_local_usd") or prices.get("silver_999"),
            "XAU_LOCAL_EUR": prices.get("gold_local_eur") or prices.get("gold_999"),
            "XAG_LOCAL_EUR": prices.get("silver_local_eur") or prices.get("silver_999"),
            "XAU999": prices.get("gold_999"),
            "XAG999": prices.get("silver_999"),
            "FX_EUR_DZD": prices.get("eur"),
            "FX_USD_DZD": prices.get("usd"),
            "EUR": prices.get("eur"),
            "USD": prices.get("usd"),
            "gold_999": prices.get("gold_999"),
            "silver_999": prices.get("silver_999"),
            "eur": prices.get("eur"),
            "usd": prices.get("usd")
        }
        # إرجاع المتغيرات مباشرة في الـ root JSON لتسهيل قراءتها من التطبيق
        formatted_prices["ok"] = True
        return jsonify(formatted_prices)
        
    return jsonify({"ok": False, "error": "لا توجد أسعار متاحة حالياً"}), 500


@app.route("/log")
def log_price():
    if not LOG_TOKEN or request.args.get("token") != LOG_TOKEN:
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    if not SUPABASE_URL or not SUPABASE_KEY:
        return jsonify({"ok": False, "error": "Supabase غير مهيأ"}), 500

    prices = fetch_prices()
    if not prices:
        return jsonify({"ok": False, "error": "لا توجد رسائل جديدة لتسجيلها من تليجرام"}), 500

    row = {
        "recorded_at": _now_algeria().strftime("%Y-%m-%d %H:%M:%S"),
        "source":      "cron",
        "gold_999":    prices.get("gold_999"),
        "silver_999":  prices.get("silver_999"),
        "eur":         prices.get("eur"),
        "usd":         prices.get("usd"),
    }

    try:
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/price_history",
            headers=_supabase_headers(),
            json=row,
            timeout=10,
        )
        r.raise_for_status()
    except Exception as e:
        return jsonify({"ok": False, "error": f"فشل الحفظ في Supabase: {e}"}), 500

    return jsonify({"ok": True, "logged": row})


@app.route("/history")
def get_history():
    if not SUPABASE_URL or not SUPABASE_KEY:
        return jsonify({"ok": False, "error": "Supabase غير مهيأ"}), 500

    since = request.args.get("since", "").strip()
    
    params = {
        "select": "recorded_at,source,gold_999,silver_999,eur,usd",
        "order":  "recorded_at.asc",
        "limit":  "20000",
    }
    
    if since:
        since_clean = since.replace(" ", "T")
        params["recorded_at"] = f"gt.{since_clean}"

    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/price_history",
            headers=_supabase_headers(),
            params=params,
            timeout=15,
        )
        r.raise_for_status()
        rows = r.json()
        
        # 💡 خدعة ذكية لراحة البرنامج: 
        # إذا كان جهاز المستخدم محدثاً بالكامل (rows فارغة)، نرسل له آخر سجل مسجل لدينا في السيرفر 
        # البرنامج المحلي سيحاول إدخاله، وسيفشل الإدخال محلياً لأنه مكرر (بأمان تام)، 
        # ولكن الدالة ستعيد عدد صفوف أكبر من 0 للواجهة، مما يمنع ظهور رسالة التحذير المزعجة!
        if not rows:
            fallback_params = {
                "select": "recorded_at,source,gold_999,silver_999,eur,usd",
                "order":  "recorded_at.desc",
                "limit":  "1", # نرسل صف واحد فقط كإشارة نجاح
            }
            r_fb = requests.get(f"{SUPABASE_URL}/rest/v1/price_history", headers=_supabase_headers(), params=fallback_params, timeout=10)
            if r_fb.status_code == 200 and r_fb.json():
                rows = r_fb.json()

        # تنظيف التواريخ
        for row in rows:
            if "recorded_at" in row and row["recorded_at"]:
                clean_dt = row["recorded_at"].replace("T", " ").replace("Z", "")
                if "." in clean_dt:
                    clean_dt = clean_dt.split(".")[0]
                row["recorded_at"] = clean_dt
                
    except Exception as e:
        return jsonify({"ok": False, "error": f"فشل الجلب من Supabase: {e}"}), 500

    return jsonify({"ok": True, "count": len(rows), "history": rows})

@app.route("/debug")
def debug():
    url  = f"https://api.telegram.org/bot{TOKEN}/getUpdates?limit=20"
    r    = requests.get(url, timeout=10)
    return r.json()


@app.route("/")
def home():
    return "Gold Price API ✅"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
