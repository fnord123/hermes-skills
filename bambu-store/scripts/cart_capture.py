#!/usr/bin/env python3
"""cart_capture.py - capture the REAL add-to-cart XHR from us.store.bambulab.com
using camoufox + the saved (guest) storage_state. Discovers the exact cart-add
endpoint, request body, and the real internal SKU id (the storefront uses an
internal id, NOT the Shopify-shared highlightProductSkuId from search).

Guest session only: no login, no 2FA, no rate-limit risk. One add to a throwaway
guest cart (reversible).

Usage:
  .venv/bin/python bin/cart_capture.py [seoCode]   # default: asa-filament
"""
import os, sys, json
from pathlib import Path
from camoufox.sync_api import Camoufox

COOKIES = os.path.expanduser("~/.hermes/cache/bambu-store/bambu_cookies.json")
seo = sys.argv[1] if len(sys.argv) > 1 else "asa-filament"
URL = f"https://us.store.bambulab.com/products/{seo}"

captured = []  # XHR to the store API we care about

def interesting(url):
    return "us-store-api.bambulab.com" in url and (
        "/cart/" in url or "/product/" in url or "/sku" in url.lower())

def main():
    out = {"url": URL}
    with Camoufox(headless=True, geoip=True, humanize=True) as browser:
        ctx = browser.new_context(storage_state=COOKIES)
        page = ctx.new_page()

        def on_response(resp):
            try:
                u = resp.url
                if not interesting(u):
                    return
                req = resp.request
                body = None
                try:
                    body = req.post_data
                except Exception:
                    pass
                txt = ""
                try:
                    if "application/json" in (resp.headers.get("content-type", "")):
                        txt = resp.text()[:1500]
                except Exception:
                    pass
                # capture auth-relevant request headers (the 10012 on raw guest
                # cart/add points at a header/token the storefront attaches)
                rhdrs = {}
                try:
                    for k, v in (req.headers or {}).items():
                        if k.lower() in ("authorization", "shunt-id", "x-bbl-shunt-id",
                                         "bbl-trace-id", "x-bbl-store-gid", "x-device-id",
                                         "device-id", "x-bbl-account-access", "token"):
                            rhdrs[k] = (v[:60] + "…") if k.lower() == "authorization" else v
                except Exception:
                    pass
                captured.append({"method": req.method, "url": u, "req_headers": rhdrs,
                                 "req_body": body, "resp": txt, "status": resp.status})
            except Exception:
                pass
        page.on("response", on_response)

        page.goto(URL, wait_until="domcontentloaded", timeout=70000)
        # clear Cloudflare if present
        for _ in range(25):
            page.wait_for_timeout(2000)
            t = (page.title() or "").lower()
            if "moment" not in t and "verify" not in t and "checking" not in t:
                break
        page.wait_for_timeout(4000)  # let detail XHR settle
        out["title"] = (page.title() or "")[:80]
        out["final_url"] = (page.url or "")[:90]

        try:
            page.screenshot(path="/tmp/asa_pdp.png", full_page=True)
            out["screenshot"] = "/tmp/asa_pdp.png"
        except Exception as e:
            out["shot_err"] = str(e)[:80]

        def buy_label():
            return page.evaluate(
                """() => { const b=[...document.querySelectorAll('button')]
                     .find(e=>e.offsetParent!==null && /select a color|add to cart|sold out|out of stock/i.test(e.innerText||''));
                     return b ? (b.innerText||'').trim() : null; }""")
        out["buy_label_initial"] = buy_label()

        # wait until the buy button has rendered AND color chips exist (lazy render)
        for _ in range(15):
            if buy_label():
                break
            page.wait_for_timeout(1000)
        page.wait_for_timeout(3000)  # extra settle for the swatch row

        # Select a color in-page via JS .click() (reliably flips the buy label).
        # The add-to-cart click itself is done with a TRUSTED Playwright click below.
        out["select_color"] = page.evaluate(
            """async () => {
                const sleep=ms=>new Promise(r=>setTimeout(r,ms));
                const label=()=>{const b=[...document.querySelectorAll('button')]
                  .find(e=>e.offsetParent!==null&&/select a color|add to cart/i.test(e.innerText||''));
                  return b?(b.innerText||'').trim():null;};
                const addBtn=()=>[...document.querySelectorAll('button')]
                  .find(e=>e.offsetParent!==null&&/^add to cart$/i.test((e.innerText||'').trim())
                    &&!e.disabled&&e.getBoundingClientRect().width>0);
                // candidate color chips: pointer-cursor elements near the buy button
                const buy=[...document.querySelectorAll('button')]
                  .find(e=>/select a color|add to cart/i.test(e.innerText||''));
                if(!buy) return {err:'no buy button'};
                let panel=buy; for(let i=0;i<5&&panel.parentElement;i++) panel=panel.parentElement;
                const chips=[...panel.querySelectorAll('*')].filter(e=>{
                  if(!(e instanceof HTMLElement)) return false;
                  if(typeof e.click!=='function') return false;
                  if(e.offsetParent===null) return false;
                  const st=getComputedStyle(e); if(st.cursor!=='pointer') return false;
                  if(/add to cart|select a color|checkout|bundle|bulk/i.test(e.innerText||'')) return false;
                  const r=e.getBoundingClientRect();
                  return r.width>=14&&r.width<=110&&r.height>=14&&r.height<=110;
                });
                const tried=[];
                for(let i=0;i<chips.length;i++){
                  try{ chips[i].click(); }catch(e){ continue; } await sleep(700);
                  const lab=label();
                  tried.push({i, lab});
                  if(lab&&/add to cart/i.test(lab)){
                    // color selected; do NOT JS-click add (needs a trusted event)
                    return {selected_chip:i, label_after:lab, n_chips:chips.length};
                  }
                }
                return {selected_chip:null, label_after:label(),
                        n_chips:chips.length, tried_tail:tried.slice(-8)};
            }""")
        # --- add via a TRUSTED, FORCED Playwright click on the MAIN add button ---
        # There are many 'Add to Cart' buttons (sticky + recommended carousels); the
        # main product one is the topmost visible. Tag it, then force-click (trusted
        # event, skips the obscured-by-overlay actionability wait).
        n_add = page.evaluate(
            """() => {
                const btns=[...document.querySelectorAll('button')].filter(e=>
                  /^add to cart$/i.test((e.innerText||'').trim()) && !e.disabled &&
                  e.offsetParent!==null && e.getBoundingClientRect().width>0);
                btns.sort((a,b)=>a.getBoundingClientRect().top-b.getBoundingClientRect().top);
                btns.forEach((b,i)=>b.setAttribute('data-mainadd', i));
                return btns.length;
            }""")
        out["add_btn_count"] = n_add
        clicked = False
        for i in range(min(n_add, 3)):  # try the top few visible ones
            b = page.query_selector(f"[data-mainadd='{i}']")
            if not b:
                continue
            for force in (False, True):
                try:
                    b.click(timeout=3500, force=force)
                    clicked = True; out["clicked_idx"] = i; out["clicked_force"] = force
                    break
                except Exception as e:
                    out["add_click_err"] = f"i={i} force={force}: " + str(e)[:100]
            if clicked:
                page.wait_for_timeout(4000)
                # did a cart/add fire?
                if any('/cart/add' in c['url'] for c in captured):
                    break
                clicked = False  # this button didn't trigger an add; try next
        out["clicked"] = any('/cart/add' in c['url'] for c in captured)
        page.wait_for_timeout(3000)  # let cart/query settle
        out["buy_label_after"] = buy_label()

        out["captured"] = captured
    print(json.dumps(out, indent=2))

if __name__ == "__main__":
    main()
