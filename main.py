from flask import Flask, jsonify, request
import requests, re, os, datetime, time

TOKEN    = os.environ.get("BOT_TOKEN", "")
CHANNEL  = "@golderpdz"   # اسم القناة العامة

# كم ساعة ننتظر قبل ما نسجّل "نبضة تأكيد" حتى لو السعر ما تبدّلش
HEARTBEAT_HOURS = 4

# ── إعدادات Supabase (أرشيف الأسعار الدائم) ──────────────────────────────
SUPABASE_URL   = os.environ.get("SUPABASE_URL", "").rstrip("/")   # مثال: https://xxxx.supabase.co
SUPABASE_KEY   = os.environ.get("SUPABASE_KEY", "")               # service_role key
LOG_TOKEN      = os.environ.get("LOG_TOKEN", "")                  # سرّ بسيط لحماية /log
ALERT_CHAT_ID  = os.environ.get("ALERT_CHAT_ID", "")              # (اختياري) محادثتك الخاصة لتنبيهات الأعطال

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



# ══════════════════════════════════════════════════════════════════════════
#  الجلب المباشر للأسعار (بدون Google Sheet ولا تيليجرام)
#  نفس معادلات صفحة Price في الشيت:
#    B7  = سعر الغرام € × سعر بيع اليورو (eurodz)          → local_eur
#    E7  = سعر الغرام € × (USD/EUR) × سعر بيع الدولار      → local_usd
#    B33 = (B7 + E7) / 2                                    → xau_local_avg
#    الأونصة = سعر الغرام € × 31.1035
# ══════════════════════════════════════════════════════════════════════════
OZ_GRAMS      = 31.1035
SUISSE_URL    = "https://suissegold.com/ar/charts/gold"
EURODZ_URL    = "https://eurodz.com/"
HTTP_HEADERS  = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")}

# نفس عتبات سكريبت الشيت: لا تسجيل إلا إذا تغيّر الذهب ≥ 50 دج أو الفضة ≥ 5 دج
XAU_MIN_DELTA = 50
XAG_MIN_DELTA = 5
# حماية: قفزة أكبر من هذه النسبة عن آخر سعر مسجَّل تُرفض (غالباً خطأ في القراءة)
MAX_JUMP_PCT  = 15

_last_alert = {}


def _alert(text, key="default", every_h=1.0):
    """تنبيه اختياري إلى محادثتك الخاصة في تيليجرام. كل نوع (key) لا يتكرر قبل every_h ساعة."""
    if not TOKEN or not ALERT_CHAT_ID:
        return
    now_t = time.time()
    if now_t - _last_alert.get(key, 0.0) < every_h * 3600:
        return
    _last_alert[key] = now_t
    try:
        requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                      data={"chat_id": ALERT_CHAT_ID, "text": text}, timeout=10)
    except Exception:
        pass


def _to_float(text):
    m = re.search(r"\d[\d.,]*", text or "")
    if not m:
        return None
    s = m.group()
    if "," in s and "." in s:
        s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _parse_db_time(s):
    """يقرأ 2026-07-07T03:20:08.123+00:00 أو 2026-07-07 03:20:08 (يتجاهل الكسور والمنطقة)."""
    m = re.match(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})", s or "")
    if not m:
        return None
    return datetime.datetime.strptime(m.group(1) + " " + m.group(2), "%Y-%m-%d %H:%M:%S")


def _fetch_suisse_metals():
    """سعر غرام الذهب والفضة باليورو من بطاقات suissegold: {'gold': .., 'silver': ..}"""
    r = requests.get(SUISSE_URL, headers=HTTP_HEADERS, timeout=15)
    r.raise_for_status()
    html = r.text
    out = {}
    for metal in ("gold", "silver"):
        pattern = r'<a\b[^>]*href="[^"]*charts/%s"[^>]*>(.*?)</a>' % metal
        for a in re.finditer(pattern, html, re.S | re.I):
            # يشترط ظهور EUR بعد الرقم: لو تغيّرت العملة الافتراضية للموقع لا نقبل الرقم
            m = re.search(r'class="price"[^>]*>\s*([\d.,]+)\s*EUR', a.group(1), re.I)
            if m:
                val = _to_float(m.group(1))
                if val and val > 0:
                    out[metal] = val
                    break
    if not out:
        title = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
        raise RuntimeError("suissegold: لا توجد أسعار EUR في الصفحة (الطول %d، العنوان: %s)" % (
            len(html), (title.group(1).strip()[:60] if title else "بلا عنوان")))
    return out


def _fetch_eurodz():
    """جدول eurodz: {'EUR': {'buy','sell','official'}, 'USD': {...}, ...}"""
    r = requests.get(EURODZ_URL, headers=HTTP_HEADERS, timeout=15)
    r.raise_for_status()
    t = re.search(r"<table\b.*?</table>", r.text, re.S | re.I)
    if not t:
        raise RuntimeError("eurodz: لم يُعثر على الجدول")
    rates = {}
    for tr in re.findall(r"<tr\b.*?</tr>", t.group(0), re.S | re.I):
        cells = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c)).strip()
                 for c in re.findall(r"<td\b.*?</td>", tr, re.S | re.I)]
        if len(cells) < 4:
            continue
        code = re.search(r"\(([A-Z]{3})\)", cells[0])
        buy, sell, off = _to_float(cells[1]), _to_float(cells[2]), _to_float(cells[3])
        if code and buy and sell:
            rates[code.group(1)] = {"buy": buy, "sell": sell, "official": off}
    return rates


def _fetch_devisesalgerie():
    """احتياطي لأسعار الصرف: devisesalgerie.com (نفس نمط 'Euro 274 DA 276 DA' في سكريبت الشيت القديم)."""
    r = requests.get("https://devisesalgerie.com/", headers=HTTP_HEADERS, timeout=15)
    r.raise_for_status()
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", r.text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()

    def rate(name):
        m = re.search(name + r"\s+(\d+(?:[.,]\d+)?)\s*DA\s+(\d+(?:[.,]\d+)?)\s*DA", text, re.I)
        if not m:
            return None
        return {"buy": float(m.group(1).replace(",", ".")),
                "sell": float(m.group(2).replace(",", ".")), "official": None}

    out = {}
    for code, name in (("EUR", "Euro"), ("USD", "Dollar")):
        v = rate(name)
        if v:
            out[code] = v
    return out


def _fx_ok(fx):
    """يتأكد أن صفّي EUR/USD موجودان وبأسعار معقولة (دج لكل وحدة)."""
    try:
        return all(50 < fx[c]["buy"] <= fx[c]["sell"] * 1.05 and 50 < fx[c]["sell"] < 1000
                   for c in ("EUR", "USD"))
    except Exception:
        return False


def _fetch_fx():
    """أسعار الصرف بالسوق الموازي: eurodz أولاً ثم devisesalgerie. يرجع (fx, اسم_المصدر)."""
    errors = []
    for name, fn in (("eurodz", _fetch_eurodz), ("devisesalgerie", _fetch_devisesalgerie)):
        try:
            fx = fn()
            if _fx_ok(fx):
                return fx, name
            errors.append("%s: بيانات غير صالحة" % name)
        except Exception as e:
            errors.append("%s: %s" % (name, e))
    raise RuntimeError("تعذّر جلب أسعار الصرف من كل المصادر — " + "; ".join(errors))


def _fetch_usd_eur(fx):
    """سعر 1 دولار باليورو. مصادر بالترتيب: frankfurter (ECB) ← open.er-api ← أسعار eurodz الرسمية."""
    for url in ("https://api.frankfurter.dev/v1/latest?base=USD&symbols=EUR",
                "https://open.er-api.com/v6/latest/USD"):
        try:
            r = requests.get(url, headers=HTTP_HEADERS, timeout=10)
            r.raise_for_status()
            v = float(r.json()["rates"]["EUR"])
            if 0.5 < v < 1.5:
                return v, url.split("/")[2]
        except Exception as e:
            print(f"فشل مصدر USD/EUR {url}: {e}")
    try:
        off_eur = fx["EUR"]["official"]
        off_usd = fx["USD"]["official"]
        v = off_usd / off_eur          # (دج/$) ÷ (دج/€) = €/$
        if 0.5 < v < 1.5:
            return v, "eurodz-official"
    except Exception:
        pass
    raise RuntimeError("تعذّر جلب سعر USD/EUR من كل المصادر")


def _fetch_metals_fallback(usd_eur, metals):
    """احتياطي إذا فشل suissegold: سعر الأونصة بالدولار من gold-api.com ثم التحويل إلى € للغرام."""
    out = {}
    for metal in metals:
        sym = {"gold": "XAU", "silver": "XAG"}[metal]
        try:
            r = requests.get(f"https://api.gold-api.com/price/{sym}", headers=HTTP_HEADERS, timeout=10)
            r.raise_for_status()
            usd_oz = float(r.json()["price"])
            if usd_oz > 0:
                out[metal] = usd_oz * usd_eur / OZ_GRAMS
        except Exception as e:
            print(f"فشل الاحتياطي لـ {metal}: {e}")
    return out


def compute_prices_direct():
    """يرجع (prices, info) بنفس مفاتيح _extract_new حتى يعمل باقي الكود كما هو."""
    fx, fx_src = _fetch_fx()
    usd_eur, usd_eur_src = _fetch_usd_eur(fx)      # مثل FX_USD_EUR (0.8927)
    eur_usd = 1.0 / usd_eur                        # مثل F4 في الشيت (1.1204)

    info = {"fx_source": fx_src, "usd_eur_source": usd_eur_src, "metals_source": "suissegold"}
    try:
        g = _fetch_suisse_metals()
    except Exception as e:
        print(f"فشل suissegold: {e}")
        info["suisse_error"] = str(e)[:200]
        g = {}
    missing = [m for m in ("gold", "silver") if not g.get(m)]
    if missing:
        fb = _fetch_metals_fallback(usd_eur, missing)
        g.update(fb)
        info["metals_source"] = "fallback" if len(missing) == 2 else "suissegold + fallback"
        info["fallback_used_for"] = sorted(fb)      # المعادن التي جاءت من المصدر الاحتياطي
    if not g.get("gold") or not g.get("silver"):
        raise RuntimeError("تعذّر جلب سعر الذهب/الفضة من كل المصادر")
    info["gram_eur"] = g

    eur_sell, usd_sell = fx["EUR"]["sell"], fx["USD"]["sell"]

    def calc(gram_eur):
        local_eur = gram_eur * eur_sell                       # B7 / B48
        local_usd = gram_eur * eur_usd * usd_sell             # E7 / D48
        return {
            "avg":       int(round((local_eur + local_usd) / 2)),
            "local_eur": round(local_eur, 2),
            "local_usd": round(local_usd, 2),
            "world_eur": round(gram_eur * OZ_GRAMS, 2),
            "world_usd": round(gram_eur * OZ_GRAMS * eur_usd, 2),
        }

    au, ag = calc(g["gold"]), calc(g["silver"])
    prices = {
        "gold_999": au["avg"],            "silver_999": ag["avg"],
        "eur": eur_sell,                  "usd": usd_sell,
        "fx_eur_dzd_buy": fx["EUR"]["buy"], "fx_usd_dzd_buy": fx["USD"]["buy"],
        "eur_usd": round(usd_eur, 4),
        "gold_world_usd": au["world_usd"],   "gold_world_eur": au["world_eur"],
        "silver_world_usd": ag["world_usd"], "silver_world_eur": ag["world_eur"],
        "gold_local_usd": au["local_usd"],   "gold_local_eur": au["local_eur"],
        "silver_local_usd": ag["local_usd"], "silver_local_eur": ag["local_eur"],
    }
    return prices, info


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


def _check_log_auth():
    if not LOG_TOKEN or request.args.get("token") != LOG_TOKEN:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    return None


@app.route("/log")
def log_price():
    """المسار القديم: يقرأ رسالة تيليجرام ثم يسجّلها. يبقى كما هو حتى نتأكد من المسار المباشر."""
    denied = _check_log_auth()
    if denied:
        return denied

    if not SUPABASE_URL or not SUPABASE_KEY:
        return jsonify({"ok": False, "error": "Supabase غير مهيأ"}), 500

    prices = fetch_prices()
    if not prices:
        # "لا توجد رسالة تيليجرام جديدة" حالة طبيعية، لا تُرجَع كـ 500 حتى لا
        # يعطّل cron-job.org المهمة تلقائياً بعد تراكم الفشل.
        return jsonify({
            "ok": True,
            "skipped": True,
            "reason": "لا توجد رسالة سعر جديدة من تليجرام حالياً (حالة طبيعية)"
        }), 200

    return _store_prices(prices, "cron")


@app.route("/log_direct")
def log_direct():
    """المسار الجديد: يجلب الأسعار من المواقع مباشرة ويحسبها بمعادلات الشيت.
    ?dry=1 → يعرض الأرقام فقط بدون أي كتابة في Supabase (للمقارنة مع الشيت)."""
    denied = _check_log_auth()
    if denied:
        return denied

    dry = request.args.get("dry") in ("1", "true", "yes")
    if not dry and (not SUPABASE_URL or not SUPABASE_KEY):
        return jsonify({"ok": False, "error": "Supabase غير مهيأ"}), 500

    try:
        prices, info = compute_prices_direct()
    except Exception as e:
        print(f"log_direct فشل: {e}")
        _alert(f"⚠️ تعذّر تحديث الأسعار\n{e}", key="fail", every_h=2)
        # 200 وليس 500: حتى لا يعطّل cron-job.org المهمة بعد الفشل المتكرر
        return jsonify({"ok": False, "skipped": True, "error": str(e)}), 200

    if dry:
        return jsonify({"ok": True, "dry_run": True, "prices": prices, "info": info})

    return _store_prices(prices, "direct", direct=True)


def _store_prices(prices, source, direct=False):
    """التحقق + قرار التسجيل + الكتابة في price_history (مشترك بين المسارين)."""
    # تحقق من سلامة البيانات: لو أي حقل سعري أساسي = 0 أو فاضٍ، نرفض التسجيل
    # بالكامل بدل ما نحفظ صفاً ناقصاً في قاعدة البيانات.
    _critical_fields = [
        "gold_999", "silver_999", "eur", "usd",
        "gold_world_usd", "gold_world_eur",
        "silver_world_usd", "silver_world_eur",
        "gold_local_usd", "gold_local_eur",
        "silver_local_usd", "silver_local_eur",
    ]
    _bad_fields = [
        f for f in _critical_fields
        if not (prices.get(f) or 0)
    ]
    if _bad_fields:
        return jsonify({
            "ok": False,
            "skipped": True,
            "reason": "تم تجاهل التسجيل: قيم صفرية/ناقصة في: " + ", ".join(_bad_fields)
        }), 200

    now  = _now_algeria()
    last = _get_last_row()

    def _r2(v):
        try:
            return round(float(v), 2)
        except Exception:
            return None

    # حماية من القراءة الخاطئة (المسار المباشر فقط): قفزة كبيرة عن آخر سعر → نرفض
    if direct and last:
        for key, label in (("xau_local_avg", "gold_999"), ("xag_local_avg", "silver_999")):
            old, new = _r2(last.get(key)), _r2(prices.get(label))
            if old and new and abs(new - old) / old * 100 > MAX_JUMP_PCT:
                msg = f"قفزة مشبوهة في {label}: {old} → {new} (أكثر من {MAX_JUMP_PCT}%) — لم يُسجَّل"
                _alert("⚠️ لم يُسجَّل السعر الجديد\n" + msg, key="jump", every_h=2)
                return jsonify({"ok": False, "skipped": True, "reason": msg}), 200

    same_price = False
    if last:
        if direct:
            # نفس قاعدة الشيت: التغيير الصغير في المتوسطين = "لا جديد"
            same_price = (
                abs((_r2(last.get("xau_local_avg")) or 0) - prices["gold_999"])   < XAU_MIN_DELTA and
                abs((_r2(last.get("xag_local_avg")) or 0) - prices["silver_999"]) < XAG_MIN_DELTA
            )
        else:
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
        last_dt = _parse_db_time(last.get("recorded_at"))
        if last_dt:
            elapsed_h = (now - last_dt).total_seconds() / 3600.0
            if elapsed_h < HEARTBEAT_HOURS:
                should_log = False
                reason = f"السعر ثابت (آخر تسجيل قبل {elapsed_h:.1f} ساعة، أقل من {HEARTBEAT_HOURS})"
            else:
                reason = f"نبضة تأكيد كل {HEARTBEAT_HOURS} ساعات (السعر ما زال ثابتاً)"
        # لو فشل تحليل التاريخ، الأسلم أننا نسجّل بدل ما نفوّت بيانات

    if not should_log:
        return jsonify({"ok": True, "skipped": True, "reason": reason})

    row = {
        "recorded_at":     now.strftime("%Y-%m-%d %H:%M:%S"),
        "source":          source,
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


HEALTH_MAX_HOURS = 6   # أكبر من HEARTBEAT_HOURS (4) حتى لا ينذر بلا سبب


@app.route("/health")
def health():
    """للمراقبة الخارجية (UptimeRobot): 200 إذا الأسعار حديثة، 503 إذا توقف التسجيل."""
    last = _get_last_row()
    dt = _parse_db_time(last.get("recorded_at")) if last else None
    if not dt:
        return jsonify({"ok": False, "error": "لا توجد بيانات أو تعذّر الوصول إلى Supabase"}), 503
    age = (_now_algeria() - dt).total_seconds() / 3600.0
    ok = age <= HEALTH_MAX_HOURS
    return jsonify({"ok": ok, "last_record_hours_ago": round(age, 2),
                    "max_hours": HEALTH_MAX_HOURS}), (200 if ok else 503)


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
