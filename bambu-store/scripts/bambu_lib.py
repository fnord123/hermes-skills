"""bambu_lib.py - shared helpers: env, AgentMail code read, login, totals."""
import os, re, json, time, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta

AGENTMAIL_BASE = "https://api.agentmail.to"

def envv(k):
    p = os.path.expanduser("~/.hermes/.env")
    if os.path.exists(p):
        for l in open(p):
            l = l.strip()
            if l and not l.startswith("#") and l.startswith(k + "="):
                return l.split("=", 1)[1]
    return None

def _from_config(k):
    p = os.path.expanduser("~/.hermes/config.yaml")
    if not os.path.exists(p):
        return None
    for l in open(p):
        st = l.strip()
        if st.startswith(k + ":"):
            return st.split(":", 1)[1].strip().strip('"').strip("'")
    return None

def get_agentmail_key():
    return os.environ.get("AGENTMAIL_API_KEY") or envv("AGENTMAIL_API_KEY") or _from_config("AGENTMAIL_API_KEY")

def am_get(url, key):
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)

def _clist(d, k):
    if isinstance(d, list): return d
    if isinstance(d, dict): return d.get(k) or []
    return []

def extract_code(text):
    import re as _re
    v = _re.sub(r"<[^>]+>", " ", text)
    v = _re.sub(r"https?://\S+", " ", v)
    v = _re.sub(r"[A-Za-z0-9_]*[A-Za-z][A-Za-z0-9_]*-[A-Za-z0-9_./-]{6,}", " ", v)
    m = _re.search(r"\bcode\b[^0-9]{0,60}?(\d{6})\b", v, _re.I)
    if m: return m.group(1)
    cands = _re.findall(r"(?<![\w\-/.])(\d{6})(?![\w\-/.])", v)
    return cands[0] if cands else None


def poll_login_code(key, since, timeout=150):
    after = (since - timedelta(seconds=45)).strftime("%Y-%m-%dT%H:%M:%SZ")
    inboxes = _clist(am_get(AGENTMAIL_BASE + "/v0/inboxes", key), "inboxes")
    ids = [(i.get("inbox_id") or i.get("id") or i.get("email_address")) for i in inboxes]
    deadline = time.time() + timeout
    seen = set()
    while time.time() < deadline:
        for iid in ids:
            q = urllib.parse.urlencode({"limit": "20", "after": after})
            try:
                th = _clist(am_get(AGENTMAIL_BASE + "/v0/inboxes/" + urllib.parse.quote(iid, safe="@") + "/threads?" + q, key), "threads")
            except Exception:
                th = []
            for t in th:
                tid = t.get("thread_id") or t.get("id")
                subj = str(t.get("subject", "")); sl = subj.lower()
                if not tid or tid in seen: continue
                if not ("is your code" in sl or "verification code" in sl or "bambu" in sl): continue
                seen.add(tid)
                m = re.search(r"\b(\d{6})\b", subj)
                if not m:
                    try:
                        full = am_get(AGENTMAIL_BASE + "/v0/inboxes/" + urllib.parse.quote(iid, safe="@") + "/threads/" + urllib.parse.quote(tid), key)
                    except Exception:
                        continue
                    body = ""
                    for _mm in (full.get("messages") or []):
                        body = _mm.get("text") or _mm.get("extracted_text") or body
                        if body: break
                    if not body: body = json.dumps(full)
                    _c = extract_code(body)
                    m = re.search(r"(\d{6})", _c) if _c else None
                if m:
                    return {"code": m.group(1), "subject": subj}
        time.sleep(5)
    return None

def needs_login(page):
    for _ in range(25):
        u = page.url or ""
        if "/checkouts/" in u:
            return False
        if "/login" in u or "/code" in u or page.query_selector("#customer-authentication-web-email"):
            return True
        page.wait_for_timeout(1000)
    return bool(page.query_selector("#customer-authentication-web-email"))

def login(page, email, key, timeout=150):
    """On a Shopify customer-accounts login page: email -> code -> submit.
    Returns dict {ok, stage, subject?}."""
    try:
        page.wait_for_selector("#customer-authentication-web-email", timeout=25000)
        page.fill("#customer-authentication-web-email", email)
    except Exception as e:
        return {"ok": False, "stage": "email_field", "err": str(e)}
    t0 = datetime.now(timezone.utc)
    page.click("button:has-text('Submit'), button[type=submit]")
    page.wait_for_timeout(2500)
    res = poll_login_code(key, t0, timeout=timeout) if key else None
    if not res:
        return {"ok": False, "stage": "code_read"}
    code = res["code"]; filled = False
    for sel in ["input[autocomplete='one-time-code']", "input[name*='code']", "input[inputmode='numeric']", "input[maxlength='6']"]:
        el = page.query_selector(sel)
        if el and el.is_visible():
            try: el.fill(code); filled = True; break
            except Exception: pass
    if not filled:
        try: page.keyboard.type(code); filled = True
        except Exception: pass
    if not filled:
        return {"ok": False, "stage": "code_fill", "subject": res["subject"]}
    page.click("button:has-text('Submit'), button:has-text('Continue'), button:has-text('Verify'), button[type=submit]")
    page.wait_for_timeout(3500)
    return {"ok": True, "stage": "done", "subject": res["subject"]}

def _money_to_cents(s):
    if not s: return None
    s = s.replace(",", "").replace("$", "").strip()
    try: return int(round(float(s) * 100))
    except Exception: return None

def parse_totals(page):
    """Parse Shopify checkout order summary into cents."""
    body = page.inner_text("body")
    out = {"raw_total_line": None}
    m = re.search(r"total price[:\s]*\$([0-9,]+\.[0-9]{2})", body, re.I)
    if m:
        out["total_cents"] = _money_to_cents(m.group(1)); out["raw_total_line"] = m.group(0)
    def grab(label):
        mm = re.search("(?:" + label + r")[^$]{0,30}\$([0-9,]+\.[0-9]{2})", body, re.I)
        return _money_to_cents(mm.group(1)) if mm else None
    out["subtotal_cents"] = grab(r"Subtotal")
    out["shipping_cents"] = grab(r"Shipping|Delivery")
    out["tax_cents"] = grab(r"Tax|Taxes|Estimated tax")
    if "total_cents" not in out:
        t = grab(r"\bTotal\b")
        if t: out["total_cents"] = t
    for k in ["total_cents", "subtotal_cents", "shipping_cents", "tax_cents"]:
        c = out.get(k)
        out[k.replace("_cents", "")] = ("$%.2f" % (c / 100.0)) if isinstance(c, int) else None
    return out

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
VIEWPORT = {"width": 1280, "height": 1700}
PROFILE_DIR = os.path.expanduser("~/.hermes/cache/bambu-store/browser-profile")

def launch(p):
    os.makedirs(PROFILE_DIR, exist_ok=True)
    return p.chromium.launch_persistent_context(PROFILE_DIR, headless=True, user_agent=UA, viewport=VIEWPORT, args=["--disable-blink-features=AutomationControlled"])

def wait_cloudflare(page, timeout=35):
    import time as _t
    end = _t.time() + timeout
    while _t.time() < end:
        try: ttl = (page.title() or "").lower()
        except Exception: ttl = ""
        if "just a moment" not in ttl and "verify" not in ttl and "checking" not in ttl:
            return True
        page.wait_for_timeout(1500)
    return False
