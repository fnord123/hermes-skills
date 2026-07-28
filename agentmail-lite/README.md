# agentmail-lite — setup

One-time setup for the [agentmail-lite](./SKILL.md) Hermes skill. The agent never reads this file; it's purely for the human standing the skill up.

## Prerequisites

- **AgentMail account and API key** — sign up at https://console.agentmail.to. Free tier: 3 inboxes, 3,000 emails/month. Paid plans from $20/mo. Free-tier emails come from `@agentmail.to`; custom domains require a paid plan. Keys start with `am_`.
- **Node.js 18+** on the host running Hermes — required by the MCP server (`npx -y agentmail-mcp`).
- **An existing AgentMail inbox.** Create one once via the [AgentMail console](https://console.agentmail.to). This skill manages inboxes; it does not create or delete them.

## Setup

### 1. Get an API key

Go to https://console.agentmail.to, create an account, generate an API key.

### 2. Configure the MCP server

Add this block to `~/.hermes/config.yaml` under `mcp_servers`. The `--tools` allowlist is required — it's what limits the surface to the 9 operations this skill expects. Paste your actual API key (env vars are not expanded from `.env` by Hermes for MCP).

```yaml
mcp_servers:
  agentmail:
    command: "npx"
    args:
      - "-y"
      - "agentmail-mcp"
      - "--tools"
      - "list_inboxes,get_inbox,list_threads,get_thread,get_attachment,send_message,reply_to_message,forward_message,update_message"
    env:
      AGENTMAIL_API_KEY: "am_your_key_here"
```

### 3. Restart Hermes

```bash
hermes gateway restart
```

After restart, `~/.hermes/logs/agent.log` should contain a line like:

```
MCP server 'agentmail' (stdio): registered 9 tool(s): mcp_agentmail_list_inboxes, ...
```

If you see a different tool count or no registration line, the MCP server isn't loading correctly — check the `mcp_servers.agentmail` block in `config.yaml`.

Alongside the 9 allowlisted tools, the MCP server registers 4 framework tools (`list_resources`, `read_resource`, `list_prompts`, `get_prompt`) regardless of `--tools`. They are inert. This is recorded here rather than in SKILL.md: naming a tool in model context is what puts it back on the table, and there is nothing for the model to do about these.

Likewise, AgentMail exposes no `delete_message` tool — deletion is a `trash` label via `update_message`, which is what SKILL.md documents positively.

## Verification

```
hermes -z "list my agentmail inboxes"
```

You should see your inbox's address. If the agent says it lacks the tool, or you get a `terminal` approval prompt mentioning `agentmail-mcp`, the MCP server is not registering correctly — check `agent.log` for registration errors.

## Real-time inbound email

Real-time inbound email requires AgentMail webhooks pointed at a public server. For a personal-use polling pattern, run `list_threads` periodically (e.g. via a cron skill).

## References

- AgentMail docs: https://docs.agentmail.to/
- AgentMail console: https://console.agentmail.to
- AgentMail MCP repo: https://github.com/agentmail-to/agentmail-mcp
- Pricing: https://www.agentmail.to/pricing
