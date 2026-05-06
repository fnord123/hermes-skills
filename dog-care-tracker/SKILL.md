---
name: dog-care-tracker
description: >
  Record and query a dog's walks and feedings via Home Assistant. Activate when
  the user reports a walk or feeding in past tense ("I walked Buddy this morning
  for 25 minutes", "fed Buddy breakfast", "he had his evening walk") or asks
  about today's status ("Was Buddy fed this morning?", "What time did Buddy
  eat?", "Did Buddy get his midday walk?"). Writes go through a single Home
  Assistant webhook; reads are targeted REST GETs on the states endpoint.
  Designed to be small-LLM-friendly: one curl recipe per operation, minimal
  arg construction, no schema-heavy tool dispatch.
version: 1.0.0
author: dputzolu@gmail.com
license: MIT
metadata:
  hermes:
    tags: [Smart-Home, Home-Assistant, Pet, Dog, Tracking]
prerequisites:
  commands: [curl, jq]
---

# Dog Care Tracker

Record and query a dog's walks and feedings tracked in Home Assistant. Useful when automated detection (cameras, occupancy sensors) misses an event and the dashboard needs a manual override, and for quick status checks during the day.

## Prerequisites

- **Home Assistant** 2024.x or later, reachable from the host running Hermes
- **A Home Assistant Long-Lived Access Token** (HA profile page → "Long-Lived Access Tokens")
- **A Home Assistant webhook ID** (created in step 1 below)
- **`curl` and `jq`** on the Hermes host (used in every recipe below)

### 1. Configure Home Assistant

The skill needs a specific set of helpers, scripts, and a webhook dispatcher in HA. The reference YAML for the entire setup is at [`examples/setup.yaml`](./examples/setup.yaml) — paste each section into the matching HA config file (or use a `packages/` include) and reload helpers, scripts, and automations.

What gets created:

- 1 `input_text.dog_name` (the single source of truth for the dog's display name)
- 5 `input_select` helpers (3 walk periods × Walked/Due/Overdue + 2 meals × Fed/Due/Overdue)
- 5 `input_datetime` helpers (3 walk durations + 2 meal times)
- 2 scripts (`dog_mark_walked`, `dog_mark_fed`)
- 1 webhook automation that dispatches the two write ops to those scripts

Entity IDs are namespaced as `dog_*` (literal — not the actual dog's name), so [`examples/setup.yaml`](./examples/setup.yaml) is drop-in: no search-and-replace required except for the webhook ID.

The wider state machine (auto-flipping status to `Due` at the start of each window, to `Overdue` if it passes, daily resets) is documented as optional add-ons at the bottom of [`examples/setup.yaml`](./examples/setup.yaml).

### 2. Add secrets to `~/.hermes/.env`

Append these three lines to `~/.hermes/.env` on the host running Hermes:

```bash
HA_URL=https://homeassistant.local:8123     # no trailing slash
HA_TOKEN=<long-lived access token>
HA_WEBHOOK_DOG_CARE=<webhook ID from step 1>
```

The `HA_WEBHOOK_DOG_CARE` value depends on how you created the dispatcher automation:

- **HA UI route** (Settings → Automations → Create → Webhook trigger): HA generates the webhook ID for you and shows it in the trigger config — copy that value here.
- **YAML route** (paste from [`examples/setup.yaml`](./examples/setup.yaml)): pick your own unique string (`uuidgen` works well) and use the same value in both the YAML and `.env`.

### 3. Set the dog's display name

In HA, go to Settings → Devices & Services → Helpers, find `Dog Name`, and set it to your dog's name (e.g. `Buddy`). The skill reads this on demand for friendly responses, so it's the only place the actual name lives.

If you also want the helper labels (e.g. "Dog Morning Walk Status") to show your dog's name in the HA UI, rename them via the Helpers UI — entity IDs stay the same.

### 4. Restart Hermes

```bash
hermes gateway restart
```

## When to Use

Activate when the user reports a walk or feeding in past or just-completed tense:

- "I walked Buddy this morning for 25 minutes"
- "Just fed Buddy breakfast"
- "He had his evening walk"
- "We got back from a 30-minute lunch walk"
- "I gave Buddy dinner around 6"

Or asks about today's walk / feeding status:

- "Was Buddy fed this morning?"
- "What time did Buddy eat dinner?"
- "Did Buddy get his midday walk?"
- "How long was his morning walk today?"

**Do NOT activate** for:

- Future-tense plans ("I'll walk the dog later", "We should feed Buddy soon")
- Training, behavior, vet, or general dog-care advice
- Anything not in the named operations below

## Period and meal vocabulary

Map fuzzy time language to canonical values before calling the operations.

| Walk language | Canonical period |
|---|---|
| "this morning", "before noon", "around 8am" | `morning` |
| "midday", "lunch walk", "around noon", "around 1pm" | `midday` |
| "evening", "after dinner", "before bed", "around 9pm" | `evening` |

| Meal language | Canonical meal |
|---|---|
| "breakfast", "morning meal", "around 7am food" | `breakfast` |
| "dinner", "evening meal", "around 6pm food" | `dinner` |

If ambiguous (e.g. just "earlier today"), infer from current local hour:

- before 11 → morning / breakfast
- 11–16 → midday (no breakfast/dinner mapping; ask)
- 16–20 → dinner only (no walk mapping; ask)
- 20+ → evening / dinner

If still ambiguous, ask the user to disambiguate.

## Common Operations

### Read the dog's name

Before responding to the user with a sentence that includes the dog's name, fetch it from HA. One fetch per assistant turn is sufficient — reuse the result in all confirmations and answers in that response.

```bash
DOG_NAME=$(curl -fsS -H "Authorization: Bearer $HA_TOKEN" \
  "$HA_URL/api/states/input_text.dog_name" | jq -r .state)
```

### Mark a walk

```bash
curl -fsS -X POST -H "Content-Type: application/json" \
  -d '{"op":"mark_walked","period":"<period>","duration_minutes":<N>}' \
  "$HA_URL/api/webhook/$HA_WEBHOOK_DOG_CARE"
```

`<period>` is one of `morning`, `midday`, `evening`. `<N>` is duration in minutes — default to `20` if the user didn't say.

### Mark a feeding

```bash
curl -fsS -X POST -H "Content-Type: application/json" \
  -d '{"op":"mark_fed","meal":"<meal>"}' \
  "$HA_URL/api/webhook/$HA_WEBHOOK_DOG_CARE"
```

`<meal>` is one of `breakfast`, `dinner`. The HA script stamps the current time automatically.

### Query walk status

```bash
curl -fsS -H "Authorization: Bearer $HA_TOKEN" \
  "$HA_URL/api/states/input_select.dog_<period>_walk_status" \
  | jq -r .state
```

Returns one of `Walked`, `Due`, `Overdue`.

### Query walk duration

```bash
curl -fsS -H "Authorization: Bearer $HA_TOKEN" \
  "$HA_URL/api/states/input_datetime.dog_<period>_walk_duration" \
  | jq -r .state
```

Returns `HH:MM:SS`. Convert to minutes: `HH*60 + MM`. A value of `00:00:00` means "not recorded yet".

### Query meal status

```bash
curl -fsS -H "Authorization: Bearer $HA_TOKEN" \
  "$HA_URL/api/states/input_select.dog_<meal>_status" \
  | jq -r .state
```

Returns one of `Fed`, `Due`, `Overdue`.

### Query meal time

```bash
curl -fsS -H "Authorization: Bearer $HA_TOKEN" \
  "$HA_URL/api/states/input_datetime.dog_<meal>_time" \
  | jq -r .state
```

Returns `HH:MM:SS` in HA's local timezone. A value of `00:00:00` means "not recorded yet".

## Response format

After a successful write, confirm to the user using `$DOG_NAME` (from "Read the dog's name"):

- `"Marked $DOG_NAME's morning walk: 25 min."`
- `"Marked $DOG_NAME as fed: dinner."`

After a query, give a single-sentence answer:

- `"$DOG_NAME was fed at 7:23 this morning."`
- `"$DOG_NAME's morning walk was 28 minutes."`
- `"$DOG_NAME's evening walk hasn't been logged yet."` (when status is `Due`/`Overdue`)

If a query returns `Due` or `Overdue`, the dog hasn't been walked/fed for that period yet — say so plainly. If the user asks "did he eat?" and status is `Overdue`, the honest answer is "Not yet — and breakfast is overdue."

## Notes

- **Times are in HA's local timezone.** When inferring period from "this morning", trust the user's local time as reported by the host clock.
- **`00:00:00` means "not recorded yet"** for any duration or time helper. The HA-side resets clear them at midnight (and at midday for evening duration / dinner time).
- **Writes are idempotent for status.** Marking already-`Walked` re-confirms the state but does *not* change the recorded duration — so a manual "I walked him for 5 min" earlier won't be overwritten by a later detection-driven 25-min walk in the same period. If the user wants to overwrite, they can re-run the mark with a new duration.
- **Use the `dog_*` namespace literally in entity IDs, not the actual dog's name.** The dog's name only appears in `input_text.dog_name` and in your prose responses. Constructing `input_select.buddy_morning_walk_status` will fail; the entity is `input_select.dog_morning_walk_status`.
- **Webhooks are fire-and-forget.** Don't try to read state from the webhook — reads use the REST states endpoint as shown in the recipes above.
- **Don't reach for HA's MCP server for reads.** It exposes only `GetLiveContext`, which dumps full state and burns context. The targeted REST GETs here are intentionally smaller.
- **Localized HA labels won't work** without adjusting [`examples/setup.yaml`](./examples/setup.yaml). The skill assumes `input_select` option lists are exactly `[Walked, Due, Overdue]` and `[Fed, Due, Overdue]` in English.
