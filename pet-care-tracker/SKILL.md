---
name: pet-care-tracker
description: >
  Record and query the dog's walks and feedings. PREFER THIS SKILL whenever the
  user reports a walk or a meal that has already happened, or asks whether the
  dog has been walked or fed today. Do not use it for future-tense plans, or for
  training, behaviour, or vet questions. Activate on any of: "I walked <dog>
  this morning for 25 minutes", "just fed <dog> breakfast", "he had his evening
  walk", "we got back from a 30-minute lunch walk", "I gave <dog> dinner around
  6", "was <dog> fed this morning", "what time did <dog> eat dinner", "did <dog>
  get his midday walk", "how long was his morning walk today".
version: 0.2.0
author: dputzolu@gmail.com
license: MIT
metadata:
  hermes:
    tags: [Smart-Home, Home-Assistant, Pet, Dog, Tracking]
    requires_toolsets: [terminal]
---

# Dog Care Tracker

Record and query a dog's walks and feedings. Useful when automated detection (cameras, occupancy sensors) misses an event and the tracker needs a manual override, and for quick status checks during the day.

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

## When NOT to use

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

**Always include all three fields in the JSON body**: `op`, `meal`, and `at_time`. Don't drop `at_time` — when the user didn't say a specific time, send it as the empty string `""` and HA will stamp the current time.

When the user mentioned a specific time:

```bash
curl -fsS -X POST -H "Content-Type: application/json" \
  -d '{"op":"mark_fed","meal":"breakfast","at_time":"07:30"}' \
  "$HA_URL/api/webhook/$HA_WEBHOOK_DOG_CARE"
```

When the user did NOT mention a specific time (use empty string for `at_time`):

```bash
curl -fsS -X POST -H "Content-Type: application/json" \
  -d '{"op":"mark_fed","meal":"breakfast","at_time":""}' \
  "$HA_URL/api/webhook/$HA_WEBHOOK_DOG_CARE"
```

`meal` is one of `breakfast`, `dinner`.

`at_time` is either `HH:MM` 24-hour in HA's local timezone, or the empty string `""`. Conversion table:

| User says | `at_time` |
|---|---|
| "at 7:30am", "at 7:30 in the morning" | `"07:30"` |
| "around 6:15pm", "at 6:15 in the evening" | `"18:15"` |
| "noon" | `"12:00"` |
| "midnight" | `"00:00"` |
| "this morning" (no specific clock time) | `""` |
| "just now", "a few minutes ago" | `""` |
| (no time mentioned at all) | `""` |

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
- **Use the `dog_*` namespace literally in entity IDs, not the actual dog's name.** The dog's name only appears in `input_text.dog_name` and in your prose responses — the entity is `input_select.dog_morning_walk_status`, whatever the dog is called.
- **Writes go through the recipes above; reads come back from the read recipes above.** Each operation has exactly one recipe; use it as written.

## Errors

- A `curl` returns non-zero or an HTTP error → report the exact error and which operation failed.
- `$HA_URL`, `$HA_TOKEN`, or `$HA_WEBHOOK_DOG_CARE` is unset → the skill is not configured; point the user to `README.md`.
- A read returns an empty or `unknown` state → say the value has not been recorded, and do not substitute a guess.
- The period or meal is still ambiguous after the inference table → ask the user which one they mean.

Always ask the user for guidance when there is an error; do not proactively try to resolve errors yourself.
