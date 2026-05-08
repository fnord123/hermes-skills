#!/usr/bin/env bash
# install.sh — Archivist Hermes profile + digest pipeline installer.
#
# Tested on Ubuntu 24.04. PRs welcome for other distros and operating systems.
#
# Interactive: confirms before each major step. Idempotent: safe to re-run.
# Discord prep (creating the application + bot, inviting it to the server)
# must be done manually first — see archivist/README.md.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROFILE_NAME="archivist"
PROFILE_DIR="$HOME/.hermes/profiles/$PROFILE_NAME"
PIPELINE_DIR="$HOME/archivist-digest"
DISCORD_API="https://discord.com/api/v10"

# CLI flags
for arg in "$@"; do
    case "$arg" in
        --help|-h)
            cat <<EOF
Usage: $0

The Archivist installer. No flags. Run from a clone of the repo:

    git clone https://github.com/fnord123/hermes-skills.git
    cd hermes-skills/archivist
    ./install.sh

Idempotent — safe to re-run after fixing a typo'd token, etc.
EOF
            exit 0
            ;;
    esac
done

# ── ANSI colors ──────────────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { printf "${GREEN}==>${NC} %s\n" "$*"; }
warn()  { printf "${YELLOW}!! ${NC}%s\n" "$*"; }
errlog(){ printf "${RED}xx${NC}  %s\n" "$*" >&2; }
die()   { errlog "$1"; exit 1; }

prompt_yn() {
    local msg="$1"; local default="${2:-y}"
    local hint
    [[ "$default" == "y" ]] && hint="[Y/n]" || hint="[y/N]"
    local ans
    while true; do
        printf "${YELLOW}?? ${NC}%s %s " "$msg" "$hint"
        read -r ans
        ans="${ans:-$default}"
        case "${ans,,}" in
            y|yes) return 0 ;;
            n|no)  return 1 ;;
        esac
    done
}

prompt_value() {
    local msg="$1" var
    printf "${YELLOW}?? ${NC}%s: " "$msg" >&2
    read -r var
    printf '%s' "$var"
}

prompt_continue() {
    local msg="${1:-Press Enter to continue}"
    printf "${YELLOW}>> ${NC}%s\n" "$msg"
    local _
    read -r _ || true
}

# ── Step 1: Prereqs ──────────────────────────────────────────────────────
check_prereqs() {
    info "Checking prerequisites..."
    declare -A reasons=(
        [hermes]="the agent runtime — required for every profile/skill/gateway operation"
        [jq]="JSON parsing for Discord API calls and digest webhook payload"
        [curl]="HTTP client for Discord API calls and webhook posting"
        [python3]="ranking script (rank.py) — Python 3.10+ required"
        [git]="cloning hermes-skills (already done if you're running this from a clone)"
        [crontab]="scheduling the Saturday digest"
    )
    local missing=()
    for cmd in hermes jq curl python3 git crontab; do
        command -v "$cmd" &>/dev/null || missing+=("$cmd")
    done
    if (( ${#missing[@]} == 0 )); then
        info "All prerequisites present."
        return
    fi

    errlog "Missing prerequisites:"
    local apt_missing=()
    for c in "${missing[@]}"; do
        printf "  - %s — %s\n" "$c" "${reasons[$c]}" >&2
        [[ "$c" != "hermes" ]] && apt_missing+=("$c")
    done
    echo "" >&2
    if (( ${#apt_missing[@]} )); then
        info "Install via apt (you'll be asked to confirm):"
        info "  sudo apt install ${apt_missing[*]}"
    fi
    if [[ " ${missing[*]} " == *" hermes "* ]]; then
        info "Install Hermes: see https://hermes-agent.nousresearch.com/docs/"
    fi
    die "Install the missing tools and re-run this script."
}

# ── Step 2: Discord setup walkthrough ───────────────────────────────────
discord_setup_walkthrough() {
    cat <<'EOF'

────────────────────────────────────────────────────────────────────
Discord setup — walking you through interactively.
You'll alt-tab to your browser to do each step, then come back here
to paste the resulting tokens / IDs / URLs.
────────────────────────────────────────────────────────────────────

STEP 3a: Create the Discord application
  1. Open https://discord.com/developers/applications in a browser
  2. Click "New Application"
  3. Name it "Archivist" (or anything you like)
  4. Click Create

STEP 3b: Set Installation URL to "None"
  1. In the application's left sidebar, click "Installation"
  2. Find the "Install Link" dropdown
  3. Choose "None"
  4. Save

  IMPORTANT: This must be done before STEP 3c, or the bot settings
  in the next step won't save.

STEP 3c: Configure the bot + reset its token
  1. In the application's left sidebar, click "Bot"
  2. UNCHECK "Public Bot" (so others can't add the bot to their servers)
  3. UNCHECK "Requires OAuth2 Code Grant" if present
  4. Click "Reset Token" → confirm → COPY the token immediately
     (tokens only show once on reset; if you miss it, reset again)

EOF
    DISCORD_BOT_TOKEN="$(prompt_value 'Paste the bot token here')"
    [[ -z "$DISCORD_BOT_TOKEN" ]] && die "Bot token required."

    info "Verifying bot token..."
    local me
    if ! me="$(curl -fsS -H "Authorization: Bot $DISCORD_BOT_TOKEN" \
        "$DISCORD_API/users/@me" 2>&1)"; then
        die "Bot token invalid or Discord unreachable: $me"
    fi
    BOT_USER_ID="$(echo "$me" | jq -r .id)"
    BOT_NAME="$(echo "$me" | jq -r .username)"
    info "Bot identified as @$BOT_NAME (user ID $BOT_USER_ID)."

    # Construct OAuth invite URL using application ID
    local app_info app_id perms_int oauth_url
    app_info="$(curl -fsS -H "Authorization: Bot $DISCORD_BOT_TOKEN" \
        "$DISCORD_API/oauth2/applications/@me")"
    app_id="$(echo "$app_info" | jq -r .id)"
    # View Channels (1<<10) + Send Messages (1<<11) + Embed Links (1<<14) + Read Message History (1<<16)
    perms_int="$(python3 -c "print((1<<10) | (1<<11) | (1<<14) | (1<<16))")"
    oauth_url="https://discord.com/api/oauth2/authorize?client_id=$app_id&permissions=$perms_int&scope=bot"

    cat <<EOF

STEP 3d: Invite the bot to your Discord server
  Visit this URL in a browser logged in as your server's OWNER:

      $oauth_url

  Select your server → Authorize. The bot appears in your member list
  with a role auto-attached named after your application.

STEP 3e: Find your server (guild) ID
  1. In Discord, enable Developer Mode if you haven't:
       User Settings → Advanced → Developer Mode → ON
  2. Right-click your server icon in the sidebar
  3. Click "Copy Server ID"

EOF
    DISCORD_GUILD_ID="$(prompt_value 'Paste the server (guild) ID')"
    [[ -z "$DISCORD_GUILD_ID" ]] && die "Server ID required."

    info "Verifying bot is in the server..."
    if ! curl -fsS -H "Authorization: Bot $DISCORD_BOT_TOKEN" \
        "$DISCORD_API/users/@me/guilds" \
        | jq -e --arg gid "$DISCORD_GUILD_ID" '.[] | select(.id == $gid)' >/dev/null; then
        die "Bot is not in guild $DISCORD_GUILD_ID. Re-visit the OAuth URL above and authorize for the right server."
    fi
    info "Bot is in the target server."

    cat <<'EOF'

STEP 3f: Create the #archivist channel
  1. In Discord, click the "+" next to your server's channel list
  2. Choose "Text Channel"
  3. Name it "archivist" (lowercase, no #)
  4. Create

EOF
    prompt_continue 'Press Enter when the channel exists'

    info "Looking for #archivist in your server..."
    local channels
    channels="$(curl -fsS -H "Authorization: Bot $DISCORD_BOT_TOKEN" \
        "$DISCORD_API/guilds/$DISCORD_GUILD_ID/channels")"
    DISCORD_CHANNEL_ID="$(echo "$channels" | jq -r '
        [.[] | select(.type == 0 and .name == "archivist")] | .[0].id // empty')"
    if [[ -n "$DISCORD_CHANNEL_ID" ]]; then
        info "Found #archivist (ID $DISCORD_CHANNEL_ID)."
    else
        warn "Couldn't find a channel named 'archivist'. Either it wasn't created yet, or the bot can't see it (check that it's a Text Channel)."
        DISCORD_CHANNEL_ID="$(prompt_value 'Paste the channel ID directly (Developer Mode → right-click channel → Copy Channel ID)')"
        [[ -z "$DISCORD_CHANNEL_ID" ]] && die "Channel ID required."
    fi

    cat <<'EOF'

STEP 3g: Create the webhook for cron-driven digest posts
  1. On your #archivist channel, click the gear icon (Edit Channel)
  2. Integrations → Webhooks → New Webhook
  3. Name it "Archivist Digest"
  4. Click "Copy Webhook URL"

EOF
    ARCHIVIST_WEBHOOK_URL="$(prompt_value 'Paste the webhook URL')"
    [[ -z "$ARCHIVIST_WEBHOOK_URL" ]] && die "Webhook URL required."

    cat <<'EOF'

STEP 3h (optional): Lock the bot to #archivist at the Discord level
  This is defense-in-depth. The Hermes config.yaml allowlist this
  installer writes will already restrict the bot at the agent level.

  If you want extra Discord-side lockdown:
    1. Server Settings → Roles → find the bot's auto-managed role
    2. UNCHECK "View Channels"
    3. Save
    4. #archivist → Edit Channel → Permissions → +
    5. Add the bot's role
    6. Allow View Channels, Send Messages, Read Message History
    7. Save

  If you skip this, the agent-level allowlist still keeps the bot
  out of all other channels at the agent layer — the Discord-level
  lock is optional.

EOF
    prompt_continue 'Press Enter to continue (whether you applied the lockdown or skipped it)'
}

# ── Step 3: Hermes profile ──────────────────────────────────────────────
create_profile() {
    # Detect case-insensitive collision. Hermes profile-create is case-preserving,
    # but alias lookup is case-insensitive — having both 'archivist' and 'Archivist'
    # would silently break gateway commands (they'd resolve to the wrong dir).
    local existing
    existing="$(find "$HOME/.hermes/profiles" -maxdepth 1 -mindepth 1 -type d \
        -iname "$PROFILE_NAME" 2>/dev/null)"
    if [[ -n "$existing" ]]; then
        if [[ "$existing" == "$PROFILE_DIR" ]]; then
            warn "Profile $PROFILE_NAME already exists at $PROFILE_DIR."
            prompt_yn "Reuse it?" y || die "Cannot continue without profile."
            return
        else
            errlog "A profile dir exists with DIFFERENT CASE from '$PROFILE_NAME':"
            printf "    %s\n" $existing >&2
            errlog "Hermes profile-create is case-preserving but alias lookup is case-insensitive."
            errlog "Having both would silently route gateway commands to the wrong dir."
            die "Move or rename the existing dir before re-running this installer."
        fi
    fi
    if prompt_yn "Create Hermes profile '$PROFILE_NAME' (clones config from current default)?" y; then
        hermes profile create "$PROFILE_NAME" --clone
        # --clone deep-copies the entire global skills/ directory (~8 MB of cruft)
        # plus Hermes state files (.bundled_manifest, .curator_state, etc.) into
        # the profile. The Archivist's behavior is in SOUL.md; config.yaml
        # disables all skills via disabled_pattern: "*". So all of that copied
        # content is dead weight — wipe it.
        if [[ -d "$PROFILE_DIR/skills" ]]; then
            info "Trimming cloned skills/ (Archivist runs entirely from SOUL.md, no skills needed)..."
            rm -rf "$PROFILE_DIR/skills"
            mkdir -p "$PROFILE_DIR/skills"
        fi
    else
        die "Cannot continue without profile."
    fi
}

# ── Step 4: Vault ───────────────────────────────────────────────────────
create_vault() {
    local vault="$PROFILE_DIR/vault"
    mkdir -p "$vault"
    if [[ ! -f "$vault/INDEX.md" ]]; then
        cat > "$vault/INDEX.md" <<'EOF'
# Index

(no entries yet)
EOF
        info "Created stub $vault/INDEX.md."
    else
        info "$vault/INDEX.md already exists."
    fi
}

# ── Step 5: Profile .env ────────────────────────────────────────────────
configure_profile_env() {
    local env="$PROFILE_DIR/.env"
    local backup="${env}.bak.$(date +%s)"
    [[ -f "$env" ]] && cp "$env" "$backup" && info "Backed up existing .env → $backup"

    {
        if [[ -f "$backup" ]]; then
            grep -v -E '^(DISCORD_BOT_TOKEN|ARCHIVIST_VAULT_PATH|ARCHIVIST_CHANNEL_ID)=' "$backup" || true
        fi
        echo "DISCORD_BOT_TOKEN=$DISCORD_BOT_TOKEN"
        echo "ARCHIVIST_VAULT_PATH=$PROFILE_DIR/vault"
        echo "ARCHIVIST_CHANNEL_ID=$DISCORD_CHANNEL_ID"
    } > "$env"
    chmod 600 "$env"
    info "Wrote $env."
}

# ── Step 6: Profile config.yaml ─────────────────────────────────────────
configure_profile_config() {
    local cfg="$PROFILE_DIR/config.yaml"
    [[ -f "$cfg" ]] || die "Expected $cfg to exist after profile create."

    # The cloned config (~15 KB) carries critical runtime settings: model,
    # providers, agent config, terminal, mcp_servers, etc. We must NOT replace
    # it wholesale — the agent depends on those. Apply only targeted edits.
    # All edits use sed + awk (no extra prereqs).

    local backup="${cfg}.bak.$(date +%s)"
    cp "$cfg" "$backup"
    info "Backed up $cfg → $backup"

    # Single-line discord overrides via sed.
    # Cloned config has these as defaults that don't fit a dedicated archive bot.
    sed -i "s|^  allowed_channels: ''|  allowed_channels: '$DISCORD_CHANNEL_ID'|" "$cfg"
    sed -i "s|^  require_mention: true|  require_mention: false|" "$cfg"
    sed -i "s|^  auto_thread: true|  auto_thread: false|" "$cfg"
    sed -i "s|^  free_response_channels: .*|  free_response_channels: ''|" "$cfg"

    # Multi-line block edits via awk: replace channel_prompts (under discord:)
    # and mcp_servers (top-level) with empty maps. Both contain cloned content
    # irrelevant to Archivist that adds prompt-context noise for small LLMs.
    # The skip ranges run from the section header to the next sibling at same
    # indent (deeper-indented children get dropped).
    awk '
        /^  channel_prompts:/ { print "  channel_prompts: {}"; skip_d = 1; next }
        /^mcp_servers:/        { print "mcp_servers: {}";       skip_t = 1; next }
        skip_d && /^  [a-zA-Z]/ { skip_d = 0 }
        skip_t && /^[a-zA-Z]/   { skip_t = 0 }
        !skip_d && !skip_t      { print }
    ' "$cfg" > "$cfg.tmp" && mv "$cfg.tmp" "$cfg"

    info "Applied Discord overrides (allowed_channels, require_mention, auto_thread, free_response_channels)."
    info "Cleared channel_prompts and mcp_servers (cloned cruft irrelevant to Archivist)."

    # Skill disabling: the per-profile skills/ directory is emptied in
    # create_profile (the canonical "no skills" mechanism). The cloned
    # config's skills.disabled list is irrelevant when skills/ is empty.
    # Note: Hermes also loads skills from ~/.hermes/skills/ globally — that's
    # a Hermes-side concern not yet covered by this installer.
}

# ── Step 7: SOUL.md ─────────────────────────────────────────────────────
place_soul() {
    local src="$SCRIPT_DIR/examples/SOUL.md"
    [[ -f "$src" ]] || die "SOUL.md template missing at $src — run install.sh from a clone of hermes-skills."

    local dst="$PROFILE_DIR/SOUL.md"
    # Always overwrite. --clone seeded a SOUL.md from the source profile (likely
    # a generic stub), and this profile's whole identity IS our SOUL.md. If you
    # had a custom SOUL you wanted to preserve, the backup file below has it.
    if [[ -f "$dst" ]]; then
        local backup="${dst}.bak.$(date +%s)"
        cp "$dst" "$backup"
        info "Backed up existing SOUL.md → $backup"
    fi
    # Substitute the literal vault path for $ARCHIVIST_VAULT_PATH placeholders.
    # Hermes file tools don't shell-expand env vars in path arguments, so the
    # agent would otherwise pass "$ARCHIVIST_VAULT_PATH/INDEX.md" verbatim and
    # get File-not-found. Bake the actual path into the deployed SOUL.md.
    sed "s|\$ARCHIVIST_VAULT_PATH|$PROFILE_DIR/vault|g" "$src" > "$dst"
    info "Placed SOUL.md at $dst (vault path substituted to $PROFILE_DIR/vault)."
}

# ── Step 8: Pipeline runtime ────────────────────────────────────────────
setup_pipeline() {
    local src="$SCRIPT_DIR/examples"

    mkdir -p "$PIPELINE_DIR"
    cp "$src/rank.py" "$PIPELINE_DIR/"
    cp "$src/post-digest.sh" "$PIPELINE_DIR/"
    chmod +x "$PIPELINE_DIR/post-digest.sh"

    local pipeline_env="$PIPELINE_DIR/.env"
    {
        echo "ARCHIVIST_WEBHOOK_URL=$ARCHIVIST_WEBHOOK_URL"
        echo "ARCHIVIST_VAULT_PATH=$PROFILE_DIR/vault"
    } > "$pipeline_env"
    chmod 600 "$pipeline_env"
    info "Pipeline ready at $PIPELINE_DIR."
}

# ── Step 9: Cron ────────────────────────────────────────────────────────
add_cron() {
    local entry="0 9 * * 6 $PIPELINE_DIR/post-digest.sh"
    if (crontab -l 2>/dev/null | grep -qF "$PIPELINE_DIR/post-digest.sh"); then
        info "Cron entry already present."
        return
    fi
    if prompt_yn "Add Saturday 09:00 cron entry?" y; then
        ( (crontab -l 2>/dev/null || true); echo "$entry" ) | crontab -
        info "Cron entry added."
    fi
}

# ── Step 10: Gateway ────────────────────────────────────────────────────
install_gateway() {
    if ! prompt_yn "Install and start the Archivist gateway service?" y; then
        warn "Skipping gateway. Run '$PROFILE_NAME gateway install && $PROFILE_NAME gateway start' when ready."
        return
    fi
    if "$PROFILE_NAME" gateway install 2>/dev/null; then
        info "Gateway installed."
    else
        warn "Gateway install reported an error. Try manually: $PROFILE_NAME gateway install"
    fi
    if "$PROFILE_NAME" gateway start 2>/dev/null; then
        info "Gateway started."
    else
        warn "Gateway start reported an error. Check: $PROFILE_NAME gateway status"
    fi
}

# ── Step 11: Self-test ──────────────────────────────────────────────────
self_test() {
    if ! prompt_yn "Run a test digest post now (writes to #archivist)?" y; then
        return
    fi
    info "Invoking $PIPELINE_DIR/post-digest.sh..."
    if "$PIPELINE_DIR/post-digest.sh"; then
        info "Self-test passed — check #archivist for the post."
    else
        warn "Self-test failed. Verify $PIPELINE_DIR/.env values and webhook validity."
    fi
}

# ── Main ────────────────────────────────────────────────────────────────
main() {
    info "Archivist installer."
    check_prereqs
    discord_setup_walkthrough
    create_profile
    create_vault
    configure_profile_env
    configure_profile_config
    place_soul
    setup_pipeline
    add_cron
    install_gateway
    self_test
    cat <<EOF

────────────────────────────────────────────────────────────────────
Setup complete.

Next:
  - Drop a URL into #archivist on Discord — the bot should archive it.
  - Saturday at 09:00, the digest will post via the webhook.
  - To run a digest now: $PIPELINE_DIR/post-digest.sh
  - To re-run this installer: $0  (idempotent)
  - Optional: lock the bot to #archivist at the Discord level
    (in addition to the Hermes config.yaml allowlist already in place).
    See archivist/README.md "Optional: lock the bot to #archivist".
────────────────────────────────────────────────────────────────────
EOF
}

main "$@"
