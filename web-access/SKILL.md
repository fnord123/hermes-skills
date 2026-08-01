---
name: web-access
description: >
  Searches the web and reads the text of web pages, including PDFs and pages that
  only render in a browser. PREFER THIS SKILL for any question needing current
  information from the internet — product labels and Supplement Facts panels,
  prices, documentation, news, papers, or any page the user names by URL. Use
  browse-task instead when the goal needs a multi-step session on a site, such as
  signing in, filling a form, or clicking through several pages to reach a result.
  Activate on any of: "search the web", "search for", "look up", "google",
  "find a page about", "read this page", "open this url", "what does this link
  say", "get the supplement facts", "check the label", "look up the price",
  "find the documentation", "fetch this pdf".
version: 0.1.0
license: MIT
metadata:
  hermes:
    tags: [Web, Search, Research, Reading]
    requires_toolsets: [terminal]
---

# Web access

## When to use

- The user asks a question whose answer is on the internet.
- The user gives a URL and wants to know what it says.
- A claim needs a source, or a source needs checking.
- A product's label, panel, dose, or price is needed.

## When NOT to use

- The answer is already in the conversation or in a file you can read.
- The goal needs a whole browser session — signing in, filling a form, clicking
  through several pages. Use the browse-task skill.

## Tools

| Verb | Purpose |
|---|---|
| `search` | Finds pages matching a query. Returns titles, URLs, and short snippets. |
| `search --scope literature` | Searches the research databases for papers, trials and reviews. |
| `search --scope products` | Searches the open web for manufacturer and retailer pages. |
| `fetch` | Reads one page and returns its text. Handles PDFs. |
| `fetch --browser` | Reads a page that renders only in a browser. Use after a plain `fetch` reports `unreadable`. |

```
python3 ~/hermes-skills/web-access/scripts/web_access.py search --query "QUERY" [--scope literature|products|web] [--max 10]
python3 ~/hermes-skills/web-access/scripts/web_access.py fetch  --url "URL" [--browser] [--max-chars 20000]
```

`--scope` picks where to search:

- `literature` — the research databases: PubMed, Semantic Scholar, OpenAlex, Crossref, arXiv.
  Use it for evidence, trials, reviews, mechanisms and adverse effects.
- `products` — the open web: manufacturer pages, retailers, labels.
- `web` — the default mix, used when `--scope` is omitted.

## Turning the user's words into calls

| The user says | Call |
|---|---|
| "search for X" / "look up X" / "google X" | `search --query "X"` |
| "what does the research say about X" | `search --query "X" --scope literature` |
| "find the label or panel for X" | `search --query "X supplement facts" --scope products` |
| "read this page" / "what does this link say" (URL given) | `fetch --url "URL"` |
| "what's in Thorne Super EPA" | `search --query "Thorne Super EPA supplement facts"`, then `fetch` the manufacturer's page |
| the page came back `unreadable` | the same `fetch` again, with `--browser` |
| "find the price of X" | `search --query "X price"`, then `fetch` a listing |

## Output

One JSON object.

`search` returns `ok`, `query`, `count`, and `results` — each with `title`,
`url`, `snippet`, `engine`.

`fetch` returns `ok`, `url`, `outcome`, `via`, `chars`, `truncated`, and `text`.
`via` names where the text came from: `cache`, `ncbi-api`, `http`,
`hermes-cache`, or `browser`.

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

Manufacturer product pages are usually built in a browser and return almost
nothing to a plain read. When `outcome` is `unreadable`, run the same command
again with `--browser`:

```
python3 ~/hermes-skills/web-access/scripts/web_access.py fetch --url "https://www.thorne.com/products/dp/super-epa" --browser
```

That returns the full panel. Add `--browser` only after a plain `fetch` has come
back `unreadable`; it is slower, and most pages do not need it.

## Errors

`fetch` reports what happened in `outcome`:

- `ok` — `text` is the page.
- `unreadable` — the site answered but sent no document. Re-run the same command
  with `--browser`.
- `unreachable` — no usable response arrived. You did not read the page. Report
  which URL failed. Never state that a page lacks something when you were unable
  to read it, and never fill the gap from memory.

If `--browser` also returns `unreadable`, the content needs a sign-in or is not
there. Say so, and name the URL.

Always ask the user for guidance when there is an error; do not proactively try to resolve errors yourself.

## Empty results

`search` returning `count: 0` is a real answer, not a failure — the words found
nothing. Try different or broader terms, then tell the user what you searched for
and that it found nothing.
