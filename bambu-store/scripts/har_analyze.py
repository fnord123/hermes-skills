#!/usr/bin/env python3
"""har_analyze.py - extract the store checkout flow from a DevTools HAR.

Prints, in order, every us-store-api.bambulab.com request: method, path,
request body, and a response snippet - so we can map cart->order->checkout->
payment endpoints. Redacts the Bearer token and obvious secrets.

Usage: .venv/bin/python bin/har_analyze.py /path/to/checkout.har
"""
import sys, json, re

def redact(s):
    if not s:
        return s
    s = re.sub(r'(Bearer\s+TC\s+)[\w.\-]+', r'\1<JWT>', s)
    s = re.sub(r'("(?:token|refreshToken|cardNumber|cvc|cvv|number)"\s*:\s*")[^"]+', r'\1<redacted>', s)
    return s

def main():
    if len(sys.argv) < 2:
        print("usage: har_analyze.py <file.har>"); return
    har = json.load(open(sys.argv[1]))
    entries = har.get("log", {}).get("entries", [])
    print("# %d total entries" % len(entries))
    for e in entries:
        req = e.get("request", {}); resp = e.get("response", {})
        url = req.get("url", "")
        if "us-store-api.bambulab.com" not in url:
            continue
        if req.get("method") == "OPTIONS":
            continue
        path = url.split("bambulab.com", 1)[-1]
        body = ""
        pd = req.get("postData", {})
        if pd:
            body = pd.get("text", "")
        rtext = ""
        content = resp.get("content", {})
        if content.get("text"):
            rtext = content["text"]
        print("\n%s %s  [%s]" % (req.get("method"), path[:90], resp.get("status")))
        if body:
            print("  REQ:  " + redact(body)[:400])
        if rtext:
            print("  RESP: " + redact(rtext)[:400])

if __name__ == "__main__":
    main()
