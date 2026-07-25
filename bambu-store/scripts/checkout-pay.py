#!/usr/bin/env python3
"""checkout-pay.py - reach payment, verify total, (optionally) pay via Link.

SAFETY: dry-run by default. Real money requires BOTH --confirm and a
matching --expect-total (cents). Amount charged == payment-page total ==
--expect-total. Never pads or estimates.
"""
import os, sys, json, re, argparse, subprocess, tempfile
from pathlib import Path
HERE = Path(__file__).resolve().parent
VENV_PY = HERE.parent / ".venv" / "bin" / "python"
if VENV_PY.exists() and sys.executable != str(VENV_PY):
    os.execv(str(VENV_PY), [str(VENV_PY), *sys.argv])
sys.path.insert(0, str(HERE))
import bambu_lib as B
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

MAX_CENTS_DEFAULT = 50000

def advance_to_payment(page):
    for sel in ["button:has-text('Continue to payment')", "button:has-text('Continue to Payment')"]:
        el = page.query_selector(sel)
        if el and el.is_visible():
            try:
                el.click(); page.wait_for_load_state("domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000); return True
            except Exception:
                pass
    return "payment" in (page.url or "")

def dump_payment_fields(page):
    info = {"frames": [], "top_inputs": []}
    for fr in page.frames:
        finfo = {"name": fr.name, "url_has": [w for w in ["card-fields","number","expiry","verification","name"] if w in (fr.url or "")]}
        try:
            inps = [{"name": el.get_attribute("name"), "id": el.get_attribute("id"),
                     "placeholder": el.get_attribute("placeholder")} for el in fr.query_selector_all("input")]
            if inps: finfo["inputs"] = inps[:8]
        except Exception:
            pass
        if finfo.get("inputs") or finfo["url_has"]:
            info["frames"].append(finfo)
    for el in page.query_selector_all("input"):
        try:
            if el.is_visible():
                info["top_inputs"].append(el.get_attribute("name"))
        except Exception:
            pass
    return info

def fill_card(page, card):
    filled = {}
    targets = [("number", card.get("number")), ("expiry", card.get("expiry")),
               ("verification_value", card.get("cvc")), ("name", card.get("name"))]
    for fr in page.frames:
        for fname, val in targets:
            if not val or fname in filled: continue
            el = fr.query_selector("input[name='%s']" % fname)
            if el:
                try: el.fill(val); filled[fname] = True
                except Exception: pass
    return filled

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cart-url", required=True)
    ap.add_argument("--expect-total", type=int)
    ap.add_argument("--max-cents", type=int, default=MAX_CENTS_DEFAULT)
    ap.add_argument("--context", default="Bambu Lab filament/parts order placed by Hermes on the user's behalf, with explicit chat approval.")
    ap.add_argument("--confirm", action="store_true")
    ap.add_argument("--shotdir", default="/tmp/bambu-checkout")
    a = ap.parse_args()
    os.makedirs(a.shotdir, exist_ok=True)
    email = B.envv("BAMBU_EMAIL"); key = B.get_agentmail_key()
    out = {"mode": "confirm" if a.confirm else "dry_run", "key_loaded": bool(key)}
    with Stealth().use_sync(sync_playwright()) as p:
        ctx = B.launch(p)
        page = ctx.new_page()
        page.goto(a.cart_url, wait_until="domcontentloaded", timeout=60000)
        B.wait_cloudflare(page)
        if B.needs_login(page):
            li = B.login(page, email, key); out["login"] = li
            if not li.get("ok"):
                print(json.dumps(out, indent=2)); ctx.close(); return
        else:
            out["login"] = {"ok": True, "stage": "already_logged_in"}
        try: page.wait_for_url("**/checkouts/**", timeout=30000)
        except Exception: pass
        B.wait_cloudflare(page); page.wait_for_timeout(3000)
        advance_to_payment(page); B.wait_cloudflare(page); page.wait_for_timeout(2000)
        out["url"] = page.url
        totals = B.parse_totals(page); out["totals"] = totals
        total = totals.get("total_cents")
        if not total:
            out["status"] = "abort_no_total"; print(json.dumps(out, indent=2)); ctx.close(); return
        if total > a.max_cents:
            out["status"] = "abort_over_cap"; print(json.dumps(out, indent=2)); ctx.close(); return
        if a.confirm and a.expect_total is not None and total != a.expect_total:
            out["status"] = "abort_total_mismatch"; out["expected"] = a.expect_total
            print(json.dumps(out, indent=2)); ctx.close(); return
        out["payment_fields"] = dump_payment_fields(page)
        try: page.screenshot(path=str(Path(a.shotdir) / "payment.png"))
        except Exception: pass
        if not a.confirm or a.expect_total is None:
            out["status"] = "dry_run_ok"; out["would_charge_cents"] = total
            print(json.dumps(out, indent=2)); ctx.close(); return
        # ---- REAL MONEY PATH (gated) ----
        tmpf = tempfile.NamedTemporaryFile(prefix="linkcard_", suffix=".json", delete=False).name
        cmd = ["link-cli", "spend-request", "create", "--credential-type", "card",
               "--amount", str(total), "-m", "Bambu Lab", "--merchant-url",
               "https://bambulab-us.myshopify.com", "--context", a.context,
               "--output-file", tmpf, "--format", "json"]
        out["spend_request"] = "submitted_awaiting_approval"
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600,
                           env={**os.environ, "PATH": os.environ.get("PATH","") + ":" + os.path.expanduser("~/.npm-global/bin")})
        if r.returncode != 0:
            out["status"] = "spend_request_failed"; out["stderr"] = r.stderr[-500:]
            print(json.dumps(out, indent=2)); ctx.close(); return
        try:
            card = json.load(open(tmpf))
        except Exception as e:
            out["status"] = "card_read_failed"; out["err"] = str(e); print(json.dumps(out, indent=2)); ctx.close(); return
        cd = card.get("card") or card
        norm = {"number": cd.get("number") or cd.get("card_number"),
                "expiry": (str(cd.get("exp_month")).zfill(2) + "/" + str(cd.get("exp_year"))[-2:]) if cd.get("exp_month") else cd.get("expiry"),
                "cvc": cd.get("cvc") or cd.get("cvv") or cd.get("verification_value"),
                "name": cd.get("name") or "David Putzolu"}
        out["card_filled"] = fill_card(page, norm)
        try: os.unlink(tmpf)
        except Exception: pass
        page.click("button:has-text('Pay now')"); page.wait_for_timeout(8000)
        body = page.inner_text("body")
        m = re.search(r"(?:Order|Confirmation)[^0-9#]{0,20}#?\s*([A-Z0-9-]{4,})", body)
        out["order_number"] = m.group(1) if m else None
        out["status"] = "paid" if (m or "thank you" in body.lower()) else "submitted_unconfirmed"
        try: page.screenshot(path=str(Path(a.shotdir) / "confirmation.png"))
        except Exception: pass
        ctx.close()
    print(json.dumps(out, indent=2))

if __name__ == "__main__":
    main()