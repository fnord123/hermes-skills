# Archivist

A dedicated Hermes profile + a digest pipeline that turns a Discord channel into a personal link archive backed by plain markdown files. Drop URLs into the channel; the agent fetches each, classifies it, summarizes it, tags it, and writes a structured entry to the vault. Once a week a cron-driven digest of the three things most worth revisiting posts back to the same channel via webhook.

## Heads up: this is "as-is" and takes some assembly

Tested on Ubuntu 24.04. PRs and issue reports welcome for other distros and operating systems. The included [`install.sh`](./install.sh) automates most of the work, but you still need to do the Discord application + bot setup manually first (Discord deliberately has no API for that). Plan for ~30–45 minutes the first time.

There is no installer for Hermes itself — install Hermes Agent first per [hermes-agent.nousresearch.com/docs](https://hermes-agent.nousresearch.com/docs/).

## What it does

```
You drop a URL in #archivist
       │
       ▼
   Archivist bot                ← runs in its own Hermes profile
       │ (fetches URL, classifies, tags, summarizes)
       ▼
  Vault directory
   ├── INDEX.md                 ← master, newest first
   └── 2026-05-07.md            ← today's daily note
       │
       ▼
   Saturday 09:00 cron
       │ (rank.py reads INDEX.md, scores, formats)
       ▼
   Discord webhook → posts digest to #archivist
```

You can also tell the agent in plain English:

- "forget the deepseek paper"
- "delete the last save"
- "show me what I saved about RAG"
- "what's the most recent github repo I saved"
- "show me entries similar to that vector DB one"

All of these are part of the agent's behavior defined in [`examples/SOUL.md`](./examples/SOUL.md), and operate against the same vault.

## Why a separate Hermes profile

**Isolation.** The Archivist runs on its own bot, in its own channel, with its own SOUL.md and skill set. No cross-talk with email, daily-briefing, or anything else. The profile's `.env` is separate so the vault path and bot token can't leak into other profiles. The two-layer Discord channel lockdown (per-channel role permissions + Hermes config allowlist) keeps the bot pinned to one place.

**Smaller surface = better local-LLM behavior.** A tightly-scoped profile with a narrow skillset is significantly more amenable to local models (Qwen-class on consumer hardware) than a do-everything profile. Fewer skills means less skill-selection ambiguity for the model. A trimmed `platform_toolsets` list means fewer tools for it to mis-select against. Less per-turn context means more attention budget for the actual task. And per-profile memory means archivist-specific patterns don't compete for retrieval space with everything else the user does. The dedicated profile pattern is doing real work for small models, not just bookkeeping.

## Vault layout

```
$ARCHIVIST_VAULT_PATH/
├── INDEX.md           # all entries, newest first
└── YYYY-MM-DD.md      # one per day with at least one save
```

Each entry follows the shape:

```markdown
### [Title]
- **URL**: https://...
- **Type**: github | article | x-post | tool | video | paper | other
- **Tags**: #tag1 #tag2 #tag3
- **Added**: YYYY-MM-DD
- **Summary**: 3–5 sentences
- **Note**: (optional) free-form context
```

Tag frequency for the digest is computed on demand from `INDEX.md`. There's no `interest_areas.md`, no pending-suggestions cache: tags ARE the signal.

## Prerequisites

| Tool | Why | Ubuntu install |
|---|---|---|
| `hermes` | The agent runtime | https://hermes-agent.nousresearch.com/docs/ |
| `python3` 3.10+ | Ranking script | usually preinstalled; `apt install python3` if not |
| `jq` | JSON for Discord API + webhook payload | `apt install jq` |
| `curl` | HTTP for Discord API | `apt install curl` |
| `git` | Cloning this repo | `apt install git` |
| user crontab | Scheduling the digest | already present |

## Discord setup (walked through interactively by install.sh)

`install.sh` is a guided wizard for the Discord side: it prints the click-by-click for each step, pauses for you to do it, then collects the resulting tokens / IDs / URLs by paste prompt. The detail below is the same content the wizard shows — read ahead for context, or just run the script and follow the prompts.

> **Important — set Installation URL to "None" before touching Bot settings.** Discord's UI requires an Installation URL to be configured on the application page before bot toggles like "Public Bot off" can be saved. If you skip ahead to the Bot tab first, you'll see a save error that doesn't explain why. Do **step 2 before step 3**.

### 1. Create the application

Go to [discord.com/developers/applications](https://discord.com/developers/applications) → click **New Application** → name it "Archivist" → confirm.

### 2. Set Installation URL to None

On the application's left sidebar, click **Installation**. In the **Install Link** dropdown, choose **None**. Save. **Do this before step 3.**

### 3. Configure the bot

Click **Bot** in the left sidebar.

- **Public Bot**: uncheck (so others can't add your bot to their servers).
- **Requires OAuth2 Code Grant**: uncheck (if present in your version of the Developer Portal).
- **Reset Token** → confirm → **copy the token immediately**. You'll paste it into install.sh later. Tokens are only shown once on reset — if you miss it, reset again.

### 4. Generate the OAuth invite URL

Click **OAuth2** → **URL Generator** in the left sidebar.

- **Scopes**: check `bot`.
- **Bot Permissions**: check **only** the following — the bot never needs any "manage" permissions:
  - `View Channels`
  - `Send Messages`
  - `Read Message History`
  - `Embed Links`

Copy the generated URL.

### 5. Invite the bot to your server

Open the URL in a browser **logged in as the server owner**. Select your server → authorize. The bot appears in your member list with the application's role auto-attached.

### 6. Find your server (guild) ID

Enable Developer Mode in Discord (User Settings → Advanced → Developer Mode), then right-click your server icon → **Copy Server ID**. You'll paste this into install.sh.

### 7. Create the `#archivist` channel

In Discord, click the `+` next to your server's channel list → choose **Text Channel** → name it `archivist`. install.sh will discover it by name via the Discord API — no need to copy the channel ID up front (the script does that for you).

### 7a. Make the channel private (recommended)

By default the channel inherits `@everyone`'s **View Channel** permission, so any server member can read it. Lock it down to just you and the bot:

1. **`#archivist` → Edit Channel → Permissions**.
2. Find **`@everyone`** in the role list (or click **Add members or roles** if it isn't shown). Set **View Channel** to **Deny** (red ✗).
3. Click **+** (Add members or roles) → add **yourself** (your user account) → set **View Channel** and **Send Messages** to **Allow** (green ✓).
4. Click **+** again → add the **Archivist** role (the bot's auto-created role from the OAuth invite) → set **View Channel** and **Send Messages** to **Allow**.
5. Save.

If the bot's auto-managed role doesn't appear in the list yet, the bot hasn't been authorized — re-check that you visited the OAuth URL from step 4 and authorized for this server. The role appears automatically after the OAuth grant.

### 8. Create the webhook for cron-driven digest posts

On the `#archivist` channel, click the gear icon (Edit Channel) → **Integrations** → **Webhooks** → **New Webhook**. Name it `Archivist Digest`. Click **Copy Webhook URL** — you'll paste this into install.sh.

### 9. Optional: lock the bot to #archivist at the Discord level

The Hermes `config.yaml` allowlist that install.sh writes already restricts the bot at the agent level. If you want defense-in-depth at the Discord level too:

- **Server Settings → Roles** → find the bot's auto-managed role (named after your application, e.g. "Archivist") → uncheck `View Channels`. The bot now sees no channels by default.
- **`#archivist` → Edit Channel → Permissions → +** → add the bot's role → allow `View Channels`, `Send Messages`, `Read Message History`. The bot regains access on this one channel only.

If a future server-wide change (e.g. a new channel) needs the bot's access denied, the role-level deny handles it automatically.

You don't need to do this for the digest to work — the agent-level allowlist is sufficient for most use cases. But because we never grant the bot any "manage" permissions, this lockdown is the only way to enforce restriction at the Discord layer.

## Run the installer

```bash
git clone https://github.com/fnord123/hermes-skills.git
cd hermes-skills/archivist
./install.sh
```

The script walks you through everything interactively. The Discord side is a guided wizard inside the installer — for each Discord step (create application, configure bot, invite to server, create channel, create webhook), the script prints the instructions, pauses for you to do it, then collects the resulting value via paste prompt.

You'll be paste-prompted along the way for:

- Bot token (after the Developer Portal Bot tab steps)
- Server (guild) ID (after enabling Developer Mode and Copy Server ID)
- Webhook URL (after creating the webhook on `#archivist`)

Everything else is automated:

| # | What it does |
|---|---|
| 1 | Verifies prerequisites — explains per tool what each is needed for if any are missing |
| 2 | Discord setup walkthrough — guides through application creation, bot config, server invite, channel creation, webhook creation; verifies bot token, constructs the OAuth invite URL, looks up the channel by name, captures IDs/URLs by paste |
| 3 | `hermes profile create archivist --clone` (lowercase — Hermes profile aliases are case-insensitive at lookup, so we keep the directory name lowercase to match) |
| 4 | Creates `~/.hermes/profiles/archivist/vault/` + stub `INDEX.md` |
| 5 | Writes profile `.env` with bot token, vault path, channel ID |
| 6 | Writes profile `config.yaml` (Discord-only, channel allowlist, all skills disabled) |
| 7 | Copies `examples/SOUL.md` to the profile dir — this is the agent's behavior spec |
| 8 | Copies pipeline scripts to `~/archivist-digest/` and writes pipeline `.env` |
| 9 | Adds Saturday 09:00 cron line to user crontab |
| 10 | Installs and starts the per-profile gateway |
| 11 | Runs a test digest post |

The script is idempotent — re-running is safe and skips steps already complete.

The bot only ever has basic chat permissions — no `Manage Roles`, no `Manage Webhooks`, no `Manage Channels`. That's why channel and webhook creation are interactive manual steps inside the wizard; the script verifies and looks up but cannot create.

## Manual setup (if you don't want to run install.sh)

For users on non-Ubuntu systems, or who prefer to script their own install, the same outcome can be reached step by step. Each step matches install.sh.

### 1. Create the Hermes profile

```bash
hermes profile create archivist --clone
```

This creates `~/.hermes/profiles/archivist/` with `config.yaml`, `.env`, and `SOUL.md` cloned from your default profile.

### 2. Vault directory

```bash
mkdir -p ~/.hermes/profiles/archivist/vault
cat > ~/.hermes/profiles/archivist/vault/INDEX.md <<'EOF'
# Archivist — Index

(no entries yet)
EOF
```

### 3. Place SOUL.md in the profile

The Archivist's full behavior (URL archiving + forget + search) lives in `examples/SOUL.md`. Copy it directly to the profile dir:

```bash
cp ~/hermes-skills/archivist/examples/SOUL.md ~/.hermes/profiles/archivist/SOUL.md
```

There is no `hermes skills install` step. Archivist is a dedicated profile — its full behavior (URL archiving, forget, search) lives in the SOUL.md you just copied.

### 4. Profile `.env`

Edit `~/.hermes/profiles/archivist/.env`. Required values:

```bash
DISCORD_BOT_TOKEN=<your bot token>
ARCHIVIST_VAULT_PATH=/home/<you>/.hermes/profiles/archivist/vault
ARCHIVIST_CHANNEL_ID=<channel ID>
```

Plus any model/provider keys carried over from the default `.env`.

### 5. Profile `config.yaml`

Edit `~/.hermes/profiles/archivist/config.yaml`:

- `platforms.discord.allowed_channels` — your `#archivist` channel ID
- Disable any other platforms (`telegram`, `slack`, etc.) cloned from default
- `skills.disabled_pattern: "*"` with `skills.enabled: []` — the Archivist's behavior is in SOUL.md, no skills needed
- `platform_toolsets.discord` — keep `file`, `web`, `memory`, `terminal`; drop `image_gen`, `tts`, `vision`, `code_execution`

(Verify the exact YAML keys against your Hermes version — the keys above match the docs as of writing but may evolve.)

### 6. Discord channel + webhook + (optional) lockdown

These are part of the manual Discord prep section above (steps 7–9), and apply equally whether you ran install.sh or are doing manual setup. Recap:

- Create `#archivist` text channel in Discord (`+` next to channel list).
- Create a webhook on `#archivist` named `Archivist Digest`; copy the URL.
- (Optional) Lock the bot's role to one channel via Server Settings → Roles + per-channel permissions.

### 7. Pipeline runtime

```bash
mkdir -p ~/archivist-digest
cp ~/hermes-skills/archivist/examples/rank.py ~/archivist-digest/
cp ~/hermes-skills/archivist/examples/post-digest.sh ~/archivist-digest/
chmod +x ~/archivist-digest/post-digest.sh
cp ~/hermes-skills/archivist/examples/.env.example ~/archivist-digest/.env
```

Edit `~/archivist-digest/.env` with the webhook URL and vault path.

### 8. Cron

```bash
(crontab -l 2>/dev/null; echo "0 9 * * 6 $HOME/archivist-digest/post-digest.sh") | crontab -
```

### 9. Gateway

```bash
archivist gateway install
archivist gateway start
```

(`archivist <subcommand>` is the alias form for `hermes -p archivist <subcommand>` — both work. Use lowercase consistently: Hermes alias lookup is case-insensitive, but the on-disk profile dir is case-preserving, so a capital alias paired with a lowercase dir is fine, but mixed-case dirs cause silent collisions.)

### 10. Test

```bash
~/archivist-digest/post-digest.sh
```

Should post a sample digest to `#archivist` (or "Archive is empty" if you have no entries yet).

## Tuning the digest ranking

Ranking weights live in `examples/rank.py`'s `score_entry` function:

| Component | Default | Rationale |
|---|---|---|
| `recency_score` | up to 1.0 | linear decay over 30 days |
| `forgotten_score` | 0.6 flat | bonus for entries 30–180 days old |
| `tag_popularity` | × 0.5 | weighted by tags' counts in last 30 days |
| `note_bonus` | 0.3 | entries with `**Note**:` field |

Edit `~/archivist-digest/rank.py` to adjust. Changes take effect on the next cron firing — no restart needed.

## Files in this skill

```
archivist/
├── README.md                    this file
├── install.sh                   interactive Ubuntu installer
└── examples/
    ├── SOUL.md                  agent behavior — single source of truth
    ├── rank.py                  digest ranking script
    ├── post-digest.sh           cron entry point
    └── .env.example             pipeline env template
```

## Troubleshooting

**The bot doesn't reply when I drop a URL.**
- `archivist gateway status` — is it running?
- Check `~/.hermes/profiles/archivist/logs/` for errors.
- Is the channel ID in `config.yaml` correct? Right-click the channel → Copy Channel ID.

**Discord 401 / 403 errors during install.**
- Bot token may be wrong — reset in the Developer Portal and re-paste.
- Bot may not be in the guild — re-visit the OAuth URL and authorize.
- The script only does read-only Discord API calls (verify identity, list channels). If those fail with 403, your bot's OAuth scope is missing `View Channels` — re-invite with the correct permissions.

**Saturday came and went, no digest.**
- `crontab -l` — is the line there?
- `~/archivist-digest/post-digest.sh` — does it run when invoked manually?
- Check `~/.bash_profile` or shell init: cron uses minimal PATH; if `python3` or `jq` aren't in `/usr/bin`, the script may fail silently. Try absolute paths in the cron line: `0 9 * * 6 /bin/bash -c '$HOME/archivist-digest/post-digest.sh'`.
- Webhook URL may have been deleted/regenerated in Discord — check Server Settings → Integrations → Webhooks.

## Roadmap

- Multi-vault support (one Archivist instance, multiple vault directories).
- Configurable digest schedule (currently hard-coded to Saturday 9am via crontab).
- Soft-delete option ("forget" moves to a trash dir instead of hard delete).

## Credits

The SOUL.md pattern is adapted from this r/hermesagent post:

> [My simplest yet effective Hermes Agent profile](https://www.reddit.com/r/hermesagent/comments/1t66lhy/my_simplest_yet_effective_hermes_agent_profile/)

The structure, principles, entry format, and tag conventions are preserved here, with adaptations for `$ARCHIVIST_VAULT_PATH`, the dedicated Discord channel pattern, the cron-driven Saturday digest, and the natural-language forget/search operations.

## License

MIT — see the parent [hermes-skills](https://github.com/fnord123/hermes-skills) repo.
