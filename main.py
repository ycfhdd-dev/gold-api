from flask import Flask, jsonify, request
import requests, re, os, datetime

TOKEN    = os.environ.get("BOT_TOKEN", "")
CHANNEL  = "@golderpdz"   # اسم القناة العامة

# كم ساعة ننتظر قبل ما نسجّل "نبضة تأكيد" حتى لو السعر ما تبدّلش
HEARTBEAT_HOURS = 4

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


def _get_last_row():
    """يجيب آخر صف مسجَّل في price_history (أو None عند الفشل/الجدول فارغ)."""
    try:
        params = {
            "select": ("recorded_at,xau_local_avg,xag_local_avg,fx_eur_dzd,fx_usd_dzd,"
                       "xau_world_usd,xau_world_eur,xag_world_usd,xag_world_eur,"
                       "fx_usd_dzd_buy,fx_eur_dzd_buy,fx_usd_eur"),
            "order":  "recorded_at.desc",
            "limit":  "1",
        }
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/price_history",
            headers=_supabase_headers(),
            params=params,
            timeout=15,
        )
        r.raise_for_status()
        rows = r.json()
        return rows[0] if rows else None
    except Exception:
        return None


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
    """المصدر الأساسي لِـ /prices: آخر سعر محفوظ في قاعدة البيانات — بدل
    قراءة تيليجرام مباشرة في كل طلب (كانت تستهلك طابور getUpdates وتسبب
    قيماً صفرية عشوائية عند تزاحم الطلبات مع الـ cron)."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    try:
        params = {
            "select": ("xau_local_avg,xag_local_avg,fx_eur_dzd,fx_usd_dzd,"
                       "xau_world_usd,xau_world_eur,xag_world_usd,xag_world_eur,"
                       "fx_usd_dzd_buy,fx_eur_dzd_buy,fx_usd_eur,"
                       "xau_local_usd,xau_local_eur,xag_local_usd,xag_local_eur"),
            "order": "recorded_at.desc",
            "limit": "1"
        }
        r = requests.get(f"{SUPABASE_URL}/rest/v1/price_history", headers=_supabase_headers(), params=params, timeout=15)
        r.raise_for_status()
        rows = r.json()
        if rows and len(rows) > 0:
            row = rows[0]
            return {
                "gold_999":          row.get("xau_local_avg"),
                "silver_999":        row.get("xag_local_avg"),
                "eur":               row.get("fx_eur_dzd"),
                "usd":               row.get("fx_usd_dzd"),
                "gold_world_usd":    row.get("xau_world_usd"),
                "gold_world_eur":    row.get("xau_world_eur"),
                "silver_world_usd":  row.get("xag_world_usd"),
                "silver_world_eur":  row.get("xag_world_eur"),
                "fx_usd_dzd_buy":    row.get("fx_usd_dzd_buy"),
                "fx_eur_dzd_buy":    row.get("fx_eur_dzd_buy"),
                "eur_usd":           row.get("fx_usd_eur"),
                "gold_local_usd":    row.get("xau_local_usd"),
                "gold_local_eur":    row.get("xau_local_eur"),
                "silver_local_usd":  row.get("xag_local_usd"),
                "silver_local_eur":  row.get("xag_local_eur"),
            }
    except Exception as e:
        print(f"فشل جلب السعر من Supabase: {e}")
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
        # 🆕 أسعار شراء الصرف — لم تكن تُقرأ إطلاقاً من قبل
        "fx_usd_dzd_buy": r"FX_USD_DZD_BUY=(\d+(?:\.\d+)?)",
        "fx_eur_dzd_buy": r"FX_EUR_DZD_BUY=(\d+(?:\.\d+)?)",
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
    # ⚠️ لا نستدعي fetch_prices() هنا (كانت تستهلك رسالة تيليجرام من الطابور
    # مباشرة عند كل فتح للنافذة/تحديث الصفحة). المصدر الوحيد الآن هو آخر
    # صف محفوظ في Supabase، اللي يُحدَّث فقط عبر /log (الـ cron كل 5 دقائق).
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
            "usd": prices.get("usd"),
            "XAU_WORLD_USD": prices.get("gold_world_usd", 0) or 0,
            "XAU_WORLD_EUR": prices.get("gold_world_eur", 0) or 0,
            "XAG_WORLD_USD": prices.get("silver_world_usd", 0) or 0,
            "XAG_WORLD_EUR": prices.get("silver_world_eur", 0) or 0,
            "FX_USD_DZD_BUY": prices.get("fx_usd_dzd_buy", 0) or 0,
            "FX_EUR_DZD_BUY": prices.get("fx_eur_dzd_buy", 0) or 0,
            "FX_USD_EUR": prices.get("eur_usd", 0) or 0,
        }
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

    now  = _now_algeria()
    last = _get_last_row()

    def _r2(v):
        try:
            return round(float(v), 2)
        except Exception:
            return None

    same_price = False
    if last:
        same_price = (
            _r2(last.get("xau_local_avg"))   == _r2(prices.get("gold_999")) and
            _r2(last.get("xag_local_avg"))   == _r2(prices.get("silver_999")) and
            _r2(last.get("fx_eur_dzd"))      == _r2(prices.get("eur")) and
            _r2(last.get("fx_usd_dzd"))      == _r2(prices.get("usd")) and
            _r2(last.get("xau_world_usd"))   == _r2(prices.get("gold_world_usd")) and
            _r2(last.get("xau_world_eur"))   == _r2(prices.get("gold_world_eur")) and
            _r2(last.get("xag_world_usd"))   == _r2(prices.get("silver_world_usd")) and
            _r2(last.get("xag_world_eur"))   == _r2(prices.get("silver_world_eur")) and
            _r2(last.get("fx_usd_dzd_buy"))  == _r2(prices.get("fx_usd_dzd_buy")) and
            _r2(last.get("fx_eur_dzd_buy"))  == _r2(prices.get("fx_eur_dzd_buy")) and
            _r2(last.get("fx_usd_eur"))      == _r2(prices.get("eur_usd"))
        )

    should_log = True
    reason     = "سعر جديد"

    if last and same_price:
        should_log = True  # افتراضياً نسجّل، إلا لو تأكدنا إن الوقت لم يحن بعد
        try:
            last_dt_str = (last.get("recorded_at") or "").replace("T", " ").replace("Z", "")
            if "." in last_dt_str:
                last_dt_str = last_dt_str.split(".")[0]
            last_dt = datetime.datetime.strptime(last_dt_str, "%Y-%m-%d %H:%M:%S")
            elapsed_h = (now - last_dt).total_seconds() / 3600.0
            if elapsed_h < HEARTBEAT_HOURS:
                should_log = False
                reason = f"السعر ثابت (آخر تسجيل قبل {elapsed_h:.1f} ساعة، أقل من {HEARTBEAT_HOURS})"
            else:
                reason = f"نبضة تأكيد كل {HEARTBEAT_HOURS} ساعات (السعر ما زال ثابتاً)"
        except Exception:
            pass  # لو فشل تحليل التاريخ، الأسلم أننا نسجّل بدل ما نفوّت بيانات

    if not should_log:
        return jsonify({"ok": True, "skipped": True, "reason": reason})

    row = {
        "recorded_at":     now.strftime("%Y-%m-%d %H:%M:%S"),
        "source":          "cron",
        "xau_local_avg":   prices.get("gold_999"),
        "xag_local_avg":   prices.get("silver_999"),
        "fx_eur_dzd":      prices.get("eur"),
        "fx_usd_dzd":      prices.get("usd"),
        "xau_world_usd":   prices.get("gold_world_usd"),
        "xau_world_eur":   prices.get("gold_world_eur"),
        "xag_world_usd":   prices.get("silver_world_usd"),
        "xag_world_eur":   prices.get("silver_world_eur"),
        "fx_usd_dzd_buy":  prices.get("fx_usd_dzd_buy"),
        "fx_eur_dzd_buy":  prices.get("fx_eur_dzd_buy"),
        "fx_usd_eur":      prices.get("eur_usd"),
        "xau_local_usd":   prices.get("gold_local_usd"),
        "xau_local_eur":   prices.get("gold_local_eur"),
        "xag_local_usd":   prices.get("silver_local_usd"),
        "xag_local_eur":   prices.get("silver_local_eur"),
    }

    try:
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/price_history",
            headers=_supabase_headers(),
            json=row,
            timeout=20,
        )
        r.raise_for_status()
    except Exception as e:
        return jsonify({"ok": False, "error": f"فشل الحفظ في Supabase: {e}"}), 500

    return jsonify({"ok": True, "logged": row, "reason": reason})


@app.route("/history")
def get_history():
    if not SUPABASE_URL or not SUPABASE_KEY:
        return jsonify({"ok": False, "error": "Supabase غير مهيأ"}), 500

    since = request.args.get("since", "").strip()
    
    params = {
        "select": ("recorded_at,source,xau_local_avg,xag_local_avg,fx_eur_dzd,fx_usd_dzd,"
                   "xau_local_usd,xau_local_eur,xag_local_usd,xag_local_eur,"
                   "xau_world_usd,xau_world_eur,xag_world_usd,xag_world_eur,"
                   "fx_usd_dzd_buy,fx_eur_dzd_buy,fx_usd_eur"),
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
            timeout=20,
        )
        r.raise_for_status()
        raw_rows = r.json()

        # تحويل أسماء الأعمدة الجديدة لنفس المفاتيح القديمة اللي يتوقعها
        # البرنامج المحلي (gold_999/silver_999/eur/usd) — بدون كسر التوافق،
        # وبدون أي طلب احتياطي إضافي (كان يسبب بطء غير ضروري).
        rows = []
        for row in raw_rows:
            clean_dt = (row.get("recorded_at") or "").replace("T", " ").replace("Z", "")
            if "." in clean_dt:
                clean_dt = clean_dt.split(".")[0]
            rows.append({
                "recorded_at": clean_dt,
                "source":      row.get("source"),
                "gold_999":    row.get("xau_local_avg"),
                "silver_999":  row.get("xag_local_avg"),
                "eur":         row.get("fx_eur_dzd"),
                "usd":         row.get("fx_usd_dzd"),
                "gold_usd":    row.get("xau_local_usd"),
                "gold_eur":    row.get("xau_local_eur"),
                "silver_usd":  row.get("xag_local_usd"),
                "silver_eur":  row.get("xag_local_eur"),
                "gold_world_usd":   row.get("xau_world_usd"),
                "gold_world_eur":   row.get("xau_world_eur"),
                "silver_world_usd": row.get("xag_world_usd"),
                "silver_world_eur": row.get("xag_world_eur"),
                "eur_buy":     row.get("fx_eur_dzd_buy"),
                "usd_buy":     row.get("fx_usd_dzd_buy"),
                "usd_eur":     row.get("fx_usd_eur"),
            })

    except Exception as e:
        return jsonify({"ok": False, "error": f"فشل الجلب من Supabase: {e}"}), 500

    return jsonify({"ok": True, "count": len(rows), "history": rows})


@app.route("/history/view")
def get_history_view():
    """صفحة HTML بسيطة لعرض أرشيف الأسعار بشكل جدول مقروء (للفحص اليدوي فقط)."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return "<h2>Supabase غير مهيأ</h2>", 500

    params = {
        "select": ("recorded_at,source,xau_local_avg,xag_local_avg,fx_eur_dzd,fx_usd_dzd,"
                   "xau_local_usd,xau_local_eur,xag_local_usd,xag_local_eur,"
                   "xau_world_usd,xau_world_eur,xag_world_usd,xag_world_eur,"
                   "fx_usd_dzd_buy,fx_eur_dzd_buy,fx_usd_eur"),
        "order":  "recorded_at.desc",
        "limit":  "200",   # آخر 200 سجل فقط للعرض
    }

    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/price_history",
            headers=_supabase_headers(),
            params=params,
            timeout=20,
        )
        r.raise_for_status()
        rows = r.json()
    except Exception as e:
        return f"<h2>فشل الجلب من Supabase: {e}</h2>", 500

    rows_html = ""
    for row in rows:
        recorded_at = (row.get("recorded_at") or "").replace("T", " ").replace("Z", "")
        if "." in recorded_at:
            recorded_at = recorded_at.split(".")[0]

        def _v(key, dec=None):
            val = row.get(key)
            if val in (None, ''):
                return '—'
            try:
                return f"{float(val):,.{dec}f}" if dec is not None else str(val)
            except Exception:
                return str(val)

        rows_html += f"""
        <tr>
            <td>{recorded_at}</td>
            <td>{row.get('source', '')}</td>
            <td>{_v('xau_local_avg')}</td>
            <td>{_v('xau_local_usd')}</td>
            <td>{_v('xau_local_eur')}</td>
            <td>{_v('xag_local_avg')}</td>
            <td>{_v('xag_local_usd')}</td>
            <td>{_v('xag_local_eur')}</td>
            <td>{_v('xau_world_usd', 2)}</td>
            <td>{_v('xau_world_eur', 2)}</td>
            <td>{_v('xag_world_usd', 2)}</td>
            <td>{_v('xag_world_eur', 2)}</td>
            <td>{_v('fx_usd_dzd')}</td>
            <td>{_v('fx_usd_dzd_buy')}</td>
            <td>{_v('fx_eur_dzd')}</td>
            <td>{_v('fx_eur_dzd_buy')}</td>
            <td>{_v('fx_usd_eur', 4)}</td>
        </tr>"""

    html = f"""
    <!DOCTYPE html>
    <html dir="rtl" lang="ar">
    <head>
        <meta charset="UTF-8">
        <title>أرشيف الأسعار</title>
        <style>
            body {{ font-family: 'Segoe UI', Tahoma, sans-serif; background:#f3f3f3; margin:0; padding:20px; }}
            h2 {{ color:#1a1a1a; }}
            table {{ border-collapse: collapse; width:100%; background:#fff; box-shadow:0 1px 3px rgba(0,0,0,.1); }}
            th, td {{ padding:8px 12px; text-align:center; border-bottom:1px solid #e0e0e0; font-size:12px; white-space:nowrap; }}
            th {{ background:#0067c0; color:#fff; position:sticky; top:0; }}
            tr:nth-child(even) {{ background:#fafafa; }}
            .count {{ color:#5a5a5a; margin-bottom:10px; }}
            .grp1 {{ background:#fff7e0 !important; }}
            .grp2 {{ background:#e8f4ff !important; }}
        </style>
    </head>
    <body>
        <h2>📊 أرشيف الأسعار (آخر {len(rows)} سجل)</h2>
        <div class="count">أحدث سجل أولاً — الذهب: متوسط / بورصة $ / بورصة € — الفضة: متوسط / بورصة $ / بورصة € — العالمي: أونصة $/€ — الصرف: بيع/شراء $ و€ + $ مقابل €</div>
        <table>
            <thead>
                <tr>
                    <th>التاريخ والوقت</th>
                    <th>المصدر</th>
                    <th>ذهب متوسط</th>
                    <th>ذهب $</th>
                    <th>ذهب €</th>
                    <th>فضة متوسط</th>
                    <th>فضة $</th>
                    <th>فضة €</th>
                    <th>ذهب عالمي $</th>
                    <th>ذهب عالمي €</th>
                    <th>فضة عالمية $</th>
                    <th>فضة عالمية €</th>
                    <th>بيع $</th>
                    <th>شراء $</th>
                    <th>بيع €</th>
                    <th>شراء €</th>
                    <th>$ مقابل €</th>
                </tr>
            </thead>
            <tbody>
                {rows_html}
            </tbody>
        </table>
    </body>
    </html>
    """
    return html


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
