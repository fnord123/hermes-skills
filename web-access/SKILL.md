---
name: web-access
description: >
  Searches the web, reads the text of web pages including PDFs, and carries out
  multi-step tasks on a website in a real browser. PREFER THIS SKILL for anything
  that needs the internet — product labels and Supplement Facts panels, prices,
  documentation, news, papers, any page the user names by URL, and any goal that
  takes several steps on a site such as applying a site's own filters, paging
  through listings, or operating a web app on the user's behalf. Reading and
  searching are read-only; a task that signs in, submits, buys, books, posts, or
  sends requires the user's explicit approval first. Activate on any of: "search
  the web", "search for", "look up", "google", "find a page about", "read this
  page", "open this url", "what does this link say", "get the supplement facts",
  "check the label", "look up the price", "find the documentation", "fetch this
  pdf", "browse to", "go to X and …", "on the website …", "check the site for
  …", "work through …", "operate …", "fill out …", "navigate …".
version: 0.2.0
license: MIT
metadata:
  hermes:
    tags: [Web, Search, Browser, Browsing, Automation, Research, Reading]
    requires_toolsets: [terminal]
---

# Web access

## When to use

- The user asks a question whose answer is on the internet.
- The user gives a URL and wants to know what it says.
- A claim needs a source, or a source needs checking.
- A product's label, panel, dose, or price is needed.
- The result takes several steps on a site: applying filters, paging through
  listings, following a flow across screens, operating a web app.

## When NOT to use

- The answer is already in the conversation or in a file you can read.
- The thing is not on the public web — a desktop app, a local file.

## Tools

| Verb | Purpose |
|---|---|
| `search` | Finds pages matching a query. Returns titles, URLs, and short snippets. |
| `search --scope literature` | Searches the research databases for papers, trials and reviews. |
| `search --scope products` | Searches the open web for manufacturer and retailer pages. |
| `fetch` | Reads one page and returns its text. Handles PDFs and pages that only render in a browser. |
| `do` | Carries out a multi-step task on a site and reports what it found. |
| `do --confirm` | Carries out a task that must act on a site. Use only after the user approved this exact task. |

```
python3 ~/hermes-skills/web-access/scripts/web_access.py search --query "QUERY" [--scope literature|products|web] [--max 10]
python3 ~/hermes-skills/web-access/scripts/web_access.py fetch  --url "URL" [--max-chars 20000]
python3 ~/hermes-skills/web-access/scripts/web_access.py do     --task "TASK" [--start-url URL] [--max-steps 25] [--confirm]
```

Use `fetch` when you have a URL and want what the page says. Use `do` when
reaching the answer takes several steps on a site.

`--scope` picks where to search:

- `literature` — the research databases: PubMed, Semantic Scholar, OpenAlex, Crossref, arXiv.
  Use it for evidence, trials, reviews, mechanisms and adverse effects.
- `products` — the open web: manufacturer pages, retailers, labels.
- `web` — the open web, used when `--scope` is omitted.

The open-web scopes ask one high-quality engine first and broaden automatically if it returns
nothing, so results stay clean without you choosing an engine. `widened` in the output records
which happened.

For `do`, **name the site's own URL in `--start-url` whenever the task is about a
particular site**, so the session begins there. Raise `--max-steps` for longer flows.

## Turning the user's words into calls

| The user says | Call |
|---|---|
| "search for X" / "look up X" / "google X" | `search --query "X"` |
| "what does the research say about X" | `search --query "X" --scope literature` |
| "find the label or panel for X" | `search --query "X supplement facts" --scope products` |
| "read this page" / "what does this link say" (URL given) | `fetch --url "URL"` |
| "what's in Thorne Super EPA" | `search --query "Thorne Super EPA supplement facts"`, then `fetch` the manufacturer's page |
| "find the price of X" | `search --query "X price"`, then `fetch` a listing |
| "check REI for the price of the X jacket in medium" | `do --task "Find the price and availability of the <X> jacket in size medium" --start-url https://www.rei.com/` |
| "what times is the Ferry Building open on Sunday" | `do --task "Find the Ferry Building Marketplace hours for Sunday"` |
| "find the cheapest nonstop SFO→JFK on Aug 1" | `do --task "Find the cheapest nonstop flight from SFO to JFK on 2026-08-01 and report the airline, time, and price" --max-steps 40` |
| "book that flight" (after you showed it and they approved) | `do --task "Book the 9am United nonstop SFO→JFK on 2026-08-01" --confirm` |

## Output

One JSON object.

`search` returns `ok`, `query`, `count`, and `results` — each with `title`,
`url`, `snippet`, `engine`.

`fetch` returns `ok`, `url`, `outcome`, `via`, `chars`, `truncated`, and `text`.
`via` names where the text came from: `cache`, `ncbi-api`, `http`,
`hermes-cache`, or `browser`.

`do` returns `ok`, `status`, and `answer`:

- `status: "complete"` → relay the `answer` to the user in plain language.
- `status: "needs_input"` → a `question` is included. Ask the user that question,
  then call again with their answer folded into `--task`.

Search returns snippets, not documents. Read the page with `fetch` before drawing
a conclusion from it.

## Common flows

**Answer a question from the web**

```
python3 ~/hermes-skills/web-access/scripts/web_access.py search --query "magnesium bisglycinate absorption"
python3 ~/hermes-skills/web-access/scripts/web_access.py fetch --url "https://example.com/article"
```

Quote from the returned `text`.

**Get a product's Supplement Facts panel**

```
python3 ~/hermes-skills/web-access/scripts/web_access.py search --query "Thorne Super EPA supplement facts"
python3 ~/hermes-skills/web-access/scripts/web_access.py fetch --url "https://www.thorne.com/products/dp/super-epa"
```

Manufacturer product pages are usually built in a browser. `fetch` renders them
when it needs to and returns the full panel; `via` says `browser` when it did.

**A task that acts on a site**

```
# 1. Describe the exact action and get the user's explicit go-ahead.
# 2. Only then:
python3 ~/hermes-skills/web-access/scripts/web_access.py do --task "<the approved action>" --confirm
```

## Errors

`fetch` reports what happened in `outcome`:

- `ok` — `text` is the page.
- `unreadable` — every tier including a real browser was tried and the document
  never arrived. It needs a sign-in or is not there. Say so, and name the URL.
- `unreachable` — no usable response arrived. You did not read the page. Report
  which URL failed. Never state that a page lacks something when you were unable
  to read it, and never fill the gap from memory.

`do` reports `status: "max_rounds"` or `"timed_out"` when the task ran long. The
`error` carries the partial finding — report it and suggest narrowing the task.

Always ask the user for guidance when there is an error; do not proactively try to resolve errors yourself.

## Empty results

`search` returning `count: 0` is a real answer, not a failure — the words found
nothing. Try different or broader terms, then tell the user what you searched for
and that it found nothing.

If `do` returns an empty `answer`, tell the user plainly that the site did not
yield it. If a task needed to act but ran without `--confirm`, the `answer`
describes what action would be required; relay that and ask whether to proceed.
