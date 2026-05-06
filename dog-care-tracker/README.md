# dog-care-tracker — setup

One-time setup for the [dog-care-tracker](./SKILL.md) Hermes skill. The agent never reads this file; it's purely for the human standing the skill up.

## Prerequisites

- **Home Assistant** 2024.x or later, reachable from the host running Hermes
- **A Home Assistant Long-Lived Access Token** (HA profile page → "Long-Lived Access Tokens")
- **A Home Assistant webhook ID** (created in step 1 below)
- **`curl` and `jq`** on the Hermes host (used in every recipe in `SKILL.md`)

## Setup

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

## Caveats

**Localized HA labels won't work** without adjusting [`examples/setup.yaml`](./examples/setup.yaml). The skill assumes `input_select` option lists are exactly `[Walked, Due, Overdue]` and `[Fed, Due, Overdue]` in English.
