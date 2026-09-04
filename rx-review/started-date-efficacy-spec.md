# Spec — medication start dates → before/after lab comparison

**Status:** patch spec (not applied — repo is externally owned; hand these to the owner)
**Date:** 2026-08-22
**Invariant (do not break):** *scripts do arithmetic, LLMs do judgment.* No drug↔marker
knowledge is hardcoded anywhere in a script. The marker list always comes from the
substance's **question-4** research answer (a cached part file); the new script verb only
splits a dated number series at a date.

---

## What this adds

1. A **`Started`** column on the regimen table. Medications carry a date (e.g. `2026-04`);
   supplements leave it **blank → zero behavior change**.
2. A **deterministic `before-after` verb** in `rx.py`: split one marker's dated lab series at a
   start date; print pre values, post values, delta, and the post-start draw count. Pure
   date/number arithmetic, same family as `trends()`.
3. **One extra card per dated medication** in Stage 6a (substances family), gated on that
   medication's own research synthesis. The card reads the q4 marker list, runs the verb per
   marker, and writes a before/after report.
4. **"Too early to tell" is a first-class outcome** — the report always states the post-start
   draw count, even when it is 0 or 1.

The three existing structural gates already do their job and need **no change**: the Stage 3
barrier (regimen settled — where `Started` lives) and the Stage 5 barrier (labs transcribed
**and user-confirmed**). A before/after delta over a misread transcription is confidently
wrong, which is exactly why this cannot live in Stage 4/5.

---

## Part 1 — the `Started` column (schema + plumbing)

The regimen flows in one direction; `Started` must ride every hop so it lands in
`regimen-final.md` (the sole Stage 6 research source):

```
user doc → regimen.txt → Stage 2 draft (regimen-draft.txt)
        → Stage 3 per-item (regimen-item-<slug>.md) → Stage 3 barrier → regimen-final.md
        → fanout.read_substances()
```

New column position: **after `Schedule`, before `Confidence`** (both "when" fields sit
together). Data tuple shape changes 5→6 everywhere it crosses a table boundary.

### 1.1 `rx.py` — Stage 2 draft worker prompt (`REGIMEN_BODY`, ~line 820)

Current format line:
```
    product | brand | quantity | schedule
```
Change to 5 fields and document the new one:
```
    product | brand | quantity | schedule | started
```
And add to the field glossary:
```
    started    when the user STARTED taking it — a month or date (e.g. 2026-04 or 2026-04-01) —
               or empty when not stated (supplements are usually empty)
```

### 1.2 `rx.py` — `_draft_regimen_rows()` (~line 911)

Currently `rsplit("|", 3)` → 4 parts. Change to `rsplit("|", 4)` and return a 4-tuple
`(name, quantity, schedule, started)`. Tolerate both 4- and 5-field lines (start dates may
not be present):
```
parts = [c.strip() for c in line.strip("|").rsplit("|", 4)]
if len(parts) < 4:
    continue
product, brand, quantity, schedule = parts[:4]
started = parts[4] if len(parts) >= 5 else ""
...
out.append((name, quantity, schedule, started))
```
(Keep the existing product/brand→name logic and the `_flat(name)` dedup unchanged.)

### 1.3 `rx.py` — Stage 3 worker prompt (`REGIMEN_INTAKE_BODY`, ~line 839)

The `Regimen Intake:` worker is told what to write. Add `Started` to its table and pass the
value through (it is user-provided, not researched):
```
Write {itemfile} as this table. Ingredients = the active ingredients and serving size from the
label; Quantity = what the user takes ({quantity}); Schedule = {schedule}; Started = {started}:
    | Name | Ingredients | Quantity | Schedule | Started | Confidence |
    |---|---|---|---|---|---|
    | {name} | active ingredients and serving size | {quantity} | {schedule} | {started} | high |
```
(`{started}` is the date or empty. Do NOT add it to the idempotency key — the label lookup the
worker performs is independent of the start date, so the `rx-regitem` key stays slug-only.)

### 1.4 `rx.py` — `cmd_intake_regimen_items()` (~line 945)

Unpack the new draft field and pass it into the body:
```
for name, quantity, schedule, started in rows:
    ...
    wid = create(args, "Regimen Intake: %s" % name,
                 REGIMEN_INTAKE_BODY.format(name=name, quantity=quantity or "as written",
                                            schedule=schedule or "as needed",
                                            started=started or "",
                                            itemfile=itemfile),
                 ...)
```
(If you add a `{started_note}` sentence to the body's first line, fill it conditionally;
otherwise drop the placeholder.)

### 1.5 `rx.py` — `_read_item_file()` (~line 4853)

Read 6 data cells instead of 5:
```
while len(cells) < 6:
    cells.append("")
return cells[0], cells[1], cells[2], cells[3], cells[4], cells[5]   # + started
```
The header skip (`"name" in low and "ingredients" in low`) is unaffected.

### 1.6 `rx.py` — `REGIMEN_FINAL_HEADER` (~line 504)
```
REGIMEN_FINAL_HEADER = "| # | Name | Ingredients | Quantity | Schedule | Started | Confidence |"
```

### 1.7 `rx.py` — `_write_regimen_final_rows()` (~line 4875)

Unpack 6 and write 7 cells (leading `#` + 6). Bump the separator to 7 columns:
```
out = ["# Regimen (final)", "", REGIMEN_FINAL_HEADER, "|---|---|---|---|---|---|---|"]
for i, (name, ing, qty, sch, started, conf) in enumerate(rows, 1):
    cells = [str(i)] + [(c or "").replace("|", "\\|") for c in (name, ing, qty, sch, started, conf)]
    out.append("| " + " | ".join(cells) + " |")
```

### 1.8 `rx.py` — `_read_regimen_final_rows()` (~line 4889)

Rows become 7-tuples `(n, name, ing, qty, sch, started, conf)`:
```
while len(cells) < 7:
    cells.append("")
rows.append((int(cells[0]), cells[1], cells[2], cells[3], cells[4], cells[5], cells[6]))
```

### 1.9 `rx.py` — every consumer of `_read_regimen_final_rows()` (5→6 unpack)

Update each to the 7-tuple (add `started` between `sch` and `conf`):

- `_regimen_final_review()` (~4913) — add a `; started {started}` clause when non-blank so the
  chat review shows the date (this also changes the review fingerprint, which is correct: a
  changed date is a changed review).
- `cmd_confirm()` (~2047) — `for (n, name, ing, qty, sch, started, conf) in rows`; add
  `"started": started` to the JSON.
- `cmd_doctor()` (~2442) — same unpack.
- `cmd_gather_regimen_slugs()` (~5014) — `name, ing, qty, sch, started, conf = parsed`;
  `rows.append((name, ing, qty, sch, started, conf))`.
- `cmd_correct_item_slug_request()` (~5061) — the `kept = [...]` comprehension and the
  `target` unpack become 7-tuples; the printed `LINE:` template gains one `| %s |` (now 6
  data fields, fed from `target[1:]`).
- `cmd_correct_item_slug_response()` (~5107) — `if len(cells) != 5` → `!= 6`;
  `name, ing, qty, sch, started, conf = cells`; the `new_rows` comprehension becomes 7-tuples.
  Keep the "Schedule must not be blank" guard; **do not** require `started` (blank is valid).

`check_regimen()` (~1530) only tests truthiness — no change.

### 1.10 `fanout.py` — `read_substances()` (~line 86)

Parse the new column by header name (column-order independent, as the rest of the function is):
```
i_name = _header_index(cells, "name")
i_when = _header_index(cells, "schedule", "time(s) taken", "when")
i_started = _header_index(cells, "started")
...
started = cells[i_started] if i_started is not None and i_started < len(cells) else ""
found[key] = {"name": name, "type": "", "note": "", "when": when, "started": started}
```
Blank `started` flows through as `""` — nothing downstream changes for supplements.

---

## Part 2 — the `before-after` verb (`rx.py`)

Pure arithmetic. Reuses the existing single implementation of the dated series
(`marker_series()`, ~line 3167) and name→series resolution (`series_for()`, ~line 3064, which
already refuses ambiguous names — blood vs urine — rather than guessing).

### 2.1 `_parse_since(t)` (new, near `_norm_date`, ~line 3138)

Normalize a start marker to `YYYY-MM-DD`. `YYYY-MM` → first of that month; delegate full dates
to the existing `_norm_date`; return `""` on anything unparseable (a bad date is a parse
failure, never a silently-one-sided split).
```
def _parse_since(t):
    t = (t or "").strip()
    m = re.fullmatch(r"(\d{4})-(\d{1,2})", t)
    if m:
        y, mo = m.groups(); mo = int(mo)
        return ("%s-%02d-01" % (y, mo)) if 1 <= mo <= 12 else ""
    return _norm_date(t)
```

### 2.2 `before_after(marker, since)` (new, near `trends()`, ~line 3184)

```
def before_after(marker, since):
    """Split one marker's dated series at `since`. Arithmetic only — no judgement, no drug
    knowledge. The CALLER (an LLM card) decides which markers this substance moves; this only
    splits the numbers a confirmed lab series already holds."""
    pts = series_for(marker_series(), marker)     # [] when absent OR ambiguous
    since_d = _parse_since(since)
    base = {"marker": marker, "since": since_d, "pre": [], "post": [],
            "pre_n": 0, "post_n": 0, "baseline": None, "endpoint": None,
            "delta": None, "pct": None, "direction": None, "too_early": True}
    if not pts:
        return dict(base, found=False,
                    reason="no single reading series (not measured, or ambiguous name)")
    if not since_d:
        return dict(base, found=True, error="unparseable start date: %r" % since)
    pre  = [(d, n) for d, n, _ in pts if d <  since_d]
    post = [(d, n) for d, n, _ in pts if d >= since_d]
    baseline = pre[-1][1]  if pre  else None     # last pre-start draw = baseline
    endpoint = post[-1][1] if post else None     # latest post-start draw
    delta  = (endpoint - baseline) if (baseline is not None and endpoint is not None) else None
    pct    = (100.0 * delta / baseline) if (delta is not None and baseline) else None
    direction = None
    if len(post) >= 2:
        rising  = all(b[1] > a[1] for a, b in zip(post, post[1:]))
        falling = all(b[1] < a[1] for a, b in zip(post, post[1:]))
        direction = "rising" if rising else ("falling" if falling else "mixed")
    return dict(base, found=True, pre=pre, post=post, pre_n=len(pre), post_n=len(post),
                baseline=baseline, endpoint=endpoint, delta=delta, pct=pct,
                direction=direction, too_early=(len(post) < 2))
```

Notes:
- `post_n < 2` ⇒ `too_early=True`. One post draw is a single point, not a trend. `post_n == 0`
  ⇒ "no post-start readings yet." Both are valid, reportable outcomes.
- `pre_n == 0` ⇒ baseline unavailable; the verb still reports the post series and says so.
- Month granularity is a known limitation: `--since 2026-04` treats 2026-04-01 as the split, so
  the first post draw may pre-date actual first dose. The card must not over-read a single
  early post value (see the "dull result" rule in Part 3).

### 2.3 `cmd_before_after(args)` (new, near `cmd_trends`, ~line 2076)

Mirror `cmd_trends`'s shape (human + `--json`):
```
def cmd_before_after(args):
    r = before_after(args.marker, args.since)
    if args.json:
        print(json.dumps({"ok": True, **r}, default=str)); return 0
    if not r.get("found"):
        print("%s: %s" % (r["marker"], r.get("reason", "no readings")))
        return 0
    if r.get("error"):
        print("%s: %s" % (r["marker"], r["error"])); return 1
    def fmt(seg): return " -> ".join("%s: %g" % (d, n) for d, n in seg) or "(none)"
    print("%s  (since %s)" % (r["marker"], r["since"]))
    print("   pre  (%d): %s" % (r["pre_n"],  fmt(r["pre"])))
    print("   post (%d): %s" % (r["post_n"], fmt(r["post"])))
    if r["baseline"] is None:
        print("   baseline: none (no pre-start readings) — direction only")
    else:
        d = r["delta"]; p = (" (%.1f%%)" % r["pct"]) if r["pct"] is not None else ""
        print("   delta: %s -> %s  =  %s%s  post-direction: %s"
              % (r["baseline"], r["endpoint"],
                 ("%.4g" % d) if d is not None else "n/a", p, r["direction"] or "n/a"))
    if r["too_early"]:
        print("   TOO EARLY TO TELL — %d post-start draw(s)." % r["post_n"])
    return 0
```

### 2.4 CLI registration (`main()`, ~line 5662)

Add to the verb table:
```
("before-after", cmd_before_after, "before/after a marker split at a start date"),
```
And an argument block:
```
if name == "before-after":
    p.add_argument("--marker", required=True, help="the lab marker name")
    p.add_argument("--since",  required=True, help="start date: YYYY-MM or YYYY-MM-DD")
```

---

## Part 3 — the efficacy card (`fanout.py`)

One card per dated medication, in the substances family, gated on that medication's **research
synthesis** (returned by `shard()`). Gating on the synthesis — rather than on the part-2 card
specifically — is deliberately the looser-but-cleaner gate: the synthesis completes only after
part 2 (which holds the q4 marker answer and the q5 labs hypothesis) has written
`PART-research-<slug>-2.md`, so the file the efficacy card reads is guaranteed to exist, and
`shard()`'s return contract is left **unchanged**. The small extra latency (waiting for parts
1/3, which run in parallel) is not worth the contract change. If the tighter gate is
wanted later, `shard()` must also return the part ids — a signature change touching all three
families that call it.

### 3.1 `EFFICACY_BODY` (new, with the other card bodies)

```
EFFICACY_BODY = """Assess whether {name} is moving the lab markers it is known to affect.

The user's recorded start date for this substance is: {started}

1. Read {reports}/PART-research-{slug}-2.md (the part-2 fragment — it holds the answers to
   questions 4 and 5) and find its answer to QUESTION 4 — which lab markers {name} is known to
   move, and in which direction. That answer is the ONLY marker list you use; do not re-research
   the literature on this card.
2. For EACH lab marker named there, run:
       python3 ~/hermes-skills/rx-review/scripts/rx.py before-after --marker <marker> --since {started}
   The verb splits the user's confirmed dated lab series at the start date and prints the pre
   values, the post values, the delta, and the number of post-start draws. It is pure
   arithmetic — use its numbers as-is; do not recompute or adjust them.
3. Compare the observed direction/magnitude against what question 4 said to expect.

Write {reports}/efficacy-{slug}.md:
- One entry per marker: the expected direction (from question 4), the observed pre→post values,
  the delta, and the post-start draw count.
- "Too early to tell" is a valid, first-class result — the verb says so when there are fewer
  than two post-start draws. ALWAYS report the post-start draw count, even when it is 0 or 1.
- Where the observed change is consistent with the expected effect, say so plainly. Where it is
  not, or the data are too thin, say that plainly. A single early post draw is not evidence of
  effect — do not over-read it.
- Do NOT recommend a dose, a change, or a stop. You supply the comparison; a clinician decides.
- Carry the part-2 citation for each "expected to move X" claim into the endnotes, and label
  the observed values as "from the user's labs" (no external citation for the arithmetic).
"""
```

### 3.2 Create it in `phase_research_family()` (substances branch, ~line 929)

After `shard()` returns the synthesis id, and only when the substance has a start date:
```
if family == "substances":
    for s in read_substances():
        note = ("\nNOTE: %s." % s["note"]) if s["note"] else ""
        slug = rxkanban.slugify(s["name"], 48)
        synth = shard(args, "Research", s["name"], slug,
                      "Research {name} as taken by the user.{note}".format(name=s["name"], note=note),
                      SUBSTANCE_PARTS, SUBSTANCE_SYNTH, "substance-%s.md" % slug, fmt, priority=50,
                      subject="substance",
                      qfmt={"timing_q": TIMING_Q.format(when=s["when"]) if s["when"] else ""},
                      labs_parts={2})
        if not synth or rxkanban.is_dry(synth):
            continue                                   # excluded subject — no cards at all
        synth_ids.append(synth)
        started = (s.get("started") or "").strip()
        if started:
            eff = create(args, "Efficacy: %s" % s["name"], "rx-research",
                         EFFICACY_BODY.format(name=s["name"], slug=slug, started=started,
                                              reports=REPORTS),
                         parents=[synth] if not rxkanban.is_dry(synth) else [],
                         runtime=SYNTH_RUNTIME, priority=48)
            if eff and not rxkanban.is_dry(eff):
                synth_ids.append(eff)                 # splice in front of the 6a barrier too
```
Because the efficacy id is appended to `synth_ids`, the existing
`rxkanban.splice(synth_ids, d["barrier"])` puts it in front of the 6a Barrier — Stage 6 cannot
complete (and Stage 8 cannot assemble a brief) until the efficacy report exists. `create()`
already gives it the `rxfan-` idempotency key and dry-run handling for free.

Blank `started` ⇒ no efficacy card ⇒ supplements' graph is byte-identical to today.

---

## Part 4 — downstream (Stage 7/8) — one decision point

`efficacy-<slug>.md` lands in `reports/`, so the Stage 7 lens/citation fan-out picks it up
automatically (they glob `reports/*.md`, skipping only `PART-` prefix and the `SKIP` set).
Two consequences, both handled by the card body above (carry part-2 citations; label the
arithmetic "from the user's labs").

**But the brief assembles ONLY from `VETTED.md`** — the Stage 8 `SYNTH` prompt (`fanout.py`
~line 350) reads no other report. So a new brief section alone would produce nothing: the
comparison must first survive reconciliation. Two edits, in order:

1. **`RECONCILE` prompt (~line 313)** — add efficacy to the input list:
   ```
   Inputs in {reports}/: CONTEXT-AUDIT.md, the four lens reports LOGIC.md, REFUTATION.md,
   OVERREACH.md and NULLHYP.md, plus the substance, marker, interaction, SCHEDULE and
   EFFICACY reports. Apply the same survival rule to timing claims as to dose claims.
   ```
   (The efficacy reports' "expected direction" claims are audited like any claim; the
   observed pre/post values are arithmetic over the user's confirmed labs — they stand or
   fall on the lab confirmation, not on a citation audit.)

2. **`SYNTH` prompt (~line 350)** — add a section so the comparison reaches the user instead
   of being dead output:
   ```
     6. Medication efficacy — from the efficacy-*.md reports (via VETTED.md): for each dated
        medication, the before/after comparison of the markers it is expected to move, with
        the post-start draw count. Frame as observation, not conclusion; carry "too early to
        tell" through verbatim.
     7. Lab observations, explicitly framed as hypotheses.     (renumber old 6, 7, 8)
   ```
   If you would rather keep the brief's section numbers stable, fold it into existing
   section 6 (Lab observations) as a sub-bullet instead.

---

## Testing / verification

1. **Blank Started = no-op.** Run a regimen where every `Started` is empty. Assert: the 6a
   graph is identical to today (no `Efficacy:` cards), and `regimen-final.md` parses with empty
   `started` on every row.
2. **One dated medication.** Seed a statin row with `Started = 2026-04`, labs with LDL draws in
   Feb/Mar (pre) and May/Jun/Jul (post). Assert:
   - `rx.py before-after --marker "LDL" --since 2026-04` prints 2 pre, 3 post, a negative
     delta, and does NOT print "too early."
   - An `Efficacy:` card is created, parented on the statin synthesis, and spliced in front of
     the 6a barrier.
   - `efficacy-<slug>.md` appears in the run dir and is referenced by the Stage 8 brief.
3. **Too-early path.** A marker with only 1 post draw ⇒ verb prints "TOO EARLY TO TELL — 1";
   the report carries that verbatim.
4. **No-pre path.** First draw post-start ⇒ baseline "none," post series still reported.
5. **Unmeasured / ambiguous marker.** `before-after --marker <absent>` ⇒ "not measured";
   an ambiguous name (blood vs urine) ⇒ refused, not guessed.
6. **`rx.py reset --confirm`** still clears `regimen-final.md` / per-item files (no new reset
   entries needed — `Started` lives in the already-cleared `inputs/` files).
7. Run the existing `rx_test.py` suite; add cases mirroring 1–5.

---

## Non-goals / caveats

- **No drug knowledge in scripts.** The verb knows nothing about statins or LDL; it splits a
  series at a date. The marker list is always the LLM's q4 answer.
- **Month-granularity confound.** `--since YYYY-MM` splits at the 1st; a mid-month start means
  the first post draw may be pre-dose. The card is instructed not to over-read a single early
  post value.
- **Start date is user-provided, not verified.** It is taken from the user's regimen doc and
  can be corrected at the Stage 3 review (now a 6th field). The pipeline does not infer it.
- **One efficacy card per dated medication** — the extra cost per future review is one small
  card that reads a cached part file and runs arithmetic; substance research itself is
  content-cached and is not re-run.
