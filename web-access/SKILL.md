---
name: web-access
description: Search the web and read web pages. Use for any question needing current information, product labels, prices, documentation, papers, or anything not already in context. Provides a search verb and a page-reading verb that handles PDFs, rate limits, retries, and JavaScript-rendered pages.
---

# Web access

Two commands. Each prints one JSON object.

## Search the web

Find pages relevant to a query.

```
python3 ~/hermes-skills/web-access/scripts/web_access.py search --query "thorne super epa supplement facts"
```

Options: `--max N` (default 10).

Returns `results`, each with `title`, `url`, `snippet`, `engine`. Search gives you snippets,
not documents — read the page itself before drawing any conclusion from it.

`count: 0` means the search found nothing. That is a real answer. Try different words. Do not
switch to another search tool; there is not one.

## Read a page

Get the text of one URL.

```
python3 ~/hermes-skills/web-access/scripts/web_access.py fetch --url "https://example.com/page"
```

Options:
- `--browser` — render the page in a real browser. Slower. See below for when.
- `--max-chars N` (default 20000) — how much text to return.
- `--min-chars N` (default 200) — shorter responses are treated as not-a-document.

Returns `ok`, `outcome`, `via`, `chars`, `text`, and often `next`.

`via` names where the text came from: `cache`, `ncbi-api`, `http`, `hermes-cache`, or
`browser`. Cheap sources are tried first automatically.

### Read the outcome before you use the text

**`ok: true`** — `text` is the page. Quote from it directly.

**`outcome: "unreadable"`** — the server answered but sent no document: a JavaScript shell, a
bot wall, or a login page. Run the same command again with `--browser`. That renders the page
properly and usually returns the content.

**`outcome: "unreachable"`** — no usable response arrived. This says nothing about what the page
contains. Report that you could not read the source. Never write that a page "did not mention"
something you were unable to read.

When a fetch fails, say which URL failed and why. A missing source is a normal, reportable
result; an invented one is not. Never fill a gap with recalled or assumed content — if you
could not read a label, price, or figure, ask the user for it.

## When to use `--browser`

Use it when a plain `fetch` returned `unreadable`. Many manufacturer and retailer product pages
are built entirely in JavaScript and return an empty shell otherwise — Thorne's product pages
return 141 characters to a plain fetch and about 8,000 with `--browser`, including the full
Supplement Facts panel.

Do not pass `--browser` on the first attempt. Most pages need no browser at all — Amazon, for
one, returns fully over plain HTTP — and the browser costs seconds where the others cost
milliseconds.

## Notes

Both commands are rate-limited per site and shared across every process, so a burst of requests
to one host paces itself automatically. Slowness is that working, not a hang.

PDFs are converted to text automatically; fetch a PDF URL exactly like any other page.

These two commands are the only web access available. If neither can reach something, report
that plainly and ask the user.
