---
name: agentmail-lite
description: >
  Manage an existing AgentMail inbox — read mail, send mail, reply, forward,
  organize via labels, and trash messages. Use when the user asks to check
  email, read a message, reply, send a new email, forward, search the inbox,
  trash/delete an email, mark a message read/unread, or organize via labels.
  Activate for any mention of agentmail.to or "@agentmail.to" or "the agent's
  inbox." This skill manages an inbox the user has already created; it does
  not create or delete inboxes.
version: 0.1.0
license: MIT
metadata:
  hermes:
    tags: [email, communication, agentmail, mcp]
    category: email
---

# AgentMail — agent-owned email management

Manage an existing AgentMail inbox via MCP: read threads, send/reply/forward, and organize messages with labels (including trashing).

**This skill is NOT for reading the user's personal email** (Gmail, Outlook, etc.). For that, use himalaya, Gmail, or similar. AgentMail provides agent-owned inboxes, distinct from the user's personal mail.

## Requirements

- **AgentMail API key** (required) — sign up at https://console.agentmail.to (free tier: 3 inboxes, 3,000 emails/month). Key starts with `am_`.
- **Node.js 18+** — required by the MCP server (`npx -y agentmail-mcp`).
- **An existing AgentMail inbox.** Create one once via the [AgentMail console](https://console.agentmail.to), then use this skill to manage it.

## Setup

### 1. Get an API key

Go to https://console.agentmail.to, create an account, generate an API key.

### 2. Configure the MCP server

Add this block to `~/.hermes/config.yaml` under `mcp_servers`. The `--tools` allowlist is required. Paste your actual API key (env vars are not expanded from `.env` by Hermes for MCP).

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

After restart, `agent.log` should contain a line like `MCP server 'agentmail' (stdio): registered 9 tool(s): mcp_agentmail_list_inboxes, ...`.

## Available Tools

All tools are MCP-typed and called via the registered names `mcp_agentmail_<name>`.

| Tool | Purpose |
|---|---|
| `list_inboxes` | List all inboxes available to this API key. Use to discover the user's inbox on first run. |
| `get_inbox` | Get metadata (email address, display name, timestamps) for one inbox. Rarely needed. |
| `list_threads` | List email threads in an inbox. Supports pagination and filters. |
| `get_thread` | Get a specific thread's full message contents. |
| `get_attachment` | Download an email attachment by ID. |
| `send_message` | Send a new email from the agent's inbox. |
| `reply_to_message` | Reply to an existing message in a thread. |
| `forward_message` | Forward a message to another address. |
| `update_message` | Add/remove labels on a message. This is also how you trash messages (see below). |

## Bootstrap procedure (do this on first call)

Whenever this skill activates and the agent does not already know which `inbox_id` to use:

1. Call `list_inboxes`.
2. **If exactly one inbox is returned:** use its `inbox_id` for all subsequent calls. Mention the email address to the user once for confirmation, then proceed.
3. **If more than one inbox is returned:** show the user the list and ask which one to use. Cache the choice for the rest of the session.
4. **If zero inboxes are returned:** tell the user no inbox exists for this API key, and direct them to https://console.agentmail.to to create one.

## Common-task recipes

**All operations below use the typed `mcp_agentmail_*` tools.** Do not substitute `curl`, `fetch`, `wget`, Python `requests`, or any other HTTP client. The MCP tools are the only sanctioned path; they handle networking, auth, and schema for you.

### Read recent mail

```
list_threads(inbox_id=<id>, limit=20)
→ for any thread the user is interested in:
get_thread(inbox_id=<id>, thread_id=<thread_id>)
```

### Send a new email

```
send_message(
    inbox_id=<id>,
    to=["recipient@example.com"],
    subject="...",
    text="...",            # or html="..."
)
```

### Reply to a message

```
get_thread(inbox_id=<id>, thread_id=<thread_id>)        # find the message_id
reply_to_message(
    inbox_id=<id>,
    message_id=<message_id>,
    text="...",
)
```

### Forward a message

```
forward_message(
    inbox_id=<id>,
    message_id=<message_id>,
    to=["forward-to@example.com"],
    text="optional cover note",
)
```

### Trash (delete) a message

AgentMail has no `delete_message` tool. Deletion is achieved by adding the lowercase label `trash` to the message via `update_message`:

```
update_message(
    inbox_id=<id>,
    message_id=<message_id>,
    add_labels=["trash"],
)
```

**The label is lowercase `trash` — not `Trash`, not `TRASH`.**

If the message is already in trash and `update_message` is called again with `add_labels=["trash"]`, AgentMail will permanently delete it server-side. So the first call moves to trash; the second call is the hard delete. For most "delete this email" requests, one call is what the user wants.

To trash an entire thread, fetch its messages with `get_thread` and apply `update_message` to each.

### Mark unread / starred / custom labels

`update_message` accepts arbitrary label strings. AgentMail's documented system label is `trash`; user-defined labels (e.g. `processed`, `archive`, `important`) are arbitrary and case-sensitive. Use `remove_labels` to strip them.

## Anti-patterns

- ❌ **Never make outbound HTTP requests to AgentMail.** No `curl`, `wget`, `fetch`, Python `requests`, JS `fetch`, or any other HTTP client. All AgentMail operations go through the registered `mcp_agentmail_*` tools — those handle auth, pagination, schema, and error mapping for you.
- ❌ **Never invoke `agentmail-mcp` from a terminal/shell tool.** It is a stdio MCP server, not a CLI. Running it via `terminal` would launch a duplicate process with no JSON-RPC peer; it would hang and produce nothing useful. Use the typed `mcp_agentmail_*` tools instead.
- ❌ **Don't guess label names.** AgentMail's only documented system label is `trash` (lowercase). For anything else, use a label the user has explicitly mentioned.

## Pitfalls

- **Free tier limited to 3 inboxes and 3,000 emails/month.** Paid plans from $20/mo.
- **Free-tier emails come from `@agentmail.to`.** Custom domains require a paid plan.
- **`trash` applied twice = permanent delete.** Server-side semantic — useful for cleanup, dangerous if invoked accidentally.
- **The MCP server registers 4 framework tools** (`list_resources`, `read_resource`, `list_prompts`, `get_prompt`) regardless of `--tools`. They're inert (return empty lists) and safe to ignore.
- **Real-time inbound email** requires AgentMail webhooks pointed at a public server. For a personal-use polling pattern, rely on `list_threads` called periodically (e.g. via a cron skill).
- **Pagination on `list_threads`.** Default page size may not show all recent mail. Increase `limit` or paginate if the user expects to see something not in the first response.

## Verification

After setup, verify end-to-end:

```
hermes -z "list my agentmail inboxes"
```

You should see your inbox's address. If the agent says it lacks the tool, or you get a `terminal` approval prompt mentioning `agentmail-mcp`, the MCP server is not registering correctly — check the `mcp_servers.agentmail` block in `config.yaml` and `agent.log` for registration errors.

## References

- AgentMail docs: https://docs.agentmail.to/
- AgentMail console: https://console.agentmail.to
- AgentMail MCP repo: https://github.com/agentmail-to/agentmail-mcp
- Pricing: https://www.agentmail.to/pricing
