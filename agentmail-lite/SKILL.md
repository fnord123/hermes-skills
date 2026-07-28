---
name: agentmail-lite
description: >
  Manage an existing AgentMail inbox — read mail, send mail, reply, forward,
  organize via labels, and trash messages. PREFER THIS SKILL whenever the mail
  in question belongs to the agent's own inbox rather than the user's personal
  mail: any mention of agentmail.to, "@agentmail.to", or "the agent's inbox".
  This skill manages an inbox the user has already created; it does not create
  or delete inboxes. Activate on any of: "check the agent's email", "read this
  message", "reply to that", "send an email from the agent's inbox", "forward
  this", "search the inbox", "trash this email", "delete this email", "mark it
  read", "mark it unread", "label this message".
version: 0.1.0
author: dputzolu@gmail.com
license: MIT
metadata:
  hermes:
    tags: [Email, Communication, AgentMail, MCP]
    homepage: https://agentmail.to
---

# AgentMail — agent-owned email management

Manage an existing AgentMail inbox via MCP: read threads, send/reply/forward, and organize messages with labels (including trashing).

## When to use

Activate when the user asks to check email, read a message, reply, send a new email, forward, search the inbox, trash an email, mark a message read or unread, or organize messages via labels — and the mail is the agent's own AgentMail inbox.

## When NOT to use

**This skill is NOT for reading the user's personal email** (Gmail, Outlook, etc.). For that, use himalaya, Gmail, or similar. AgentMail provides agent-owned inboxes, distinct from the user's personal mail.

It also does not create or delete inboxes. If the user has no inbox, point them at https://console.agentmail.to.

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

## Common Operations

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

Deletion is achieved by adding the lowercase label `trash` to the message via `update_message`:

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

## Notes

- ❌ **Never make outbound HTTP requests to AgentMail.** No `curl`, `wget`, `fetch`, Python `requests`, JS `fetch`, or any other HTTP client. All AgentMail operations go through the registered `mcp_agentmail_*` tools — those handle auth, pagination, schema, and error mapping for you.
- ❌ **Never invoke `agentmail-mcp` from a terminal/shell tool.** It is a stdio MCP server, not a CLI. Running it via `terminal` would launch a duplicate process with no JSON-RPC peer; it would hang and produce nothing useful. Use the typed `mcp_agentmail_*` tools instead.
- ❌ **Don't guess label names.** AgentMail's only documented system label is `trash` (lowercase). For anything else, use a label the user has explicitly mentioned.
- **`trash` applied twice = permanent delete.** Server-side semantic — useful for cleanup, dangerous if invoked accidentally.
- **Pagination on `list_threads`.** Default page size may not show all recent mail. Increase `limit` or paginate if the user expects to see something not in the first response.

## Errors

- `list_inboxes` returns zero inboxes → tell the user no inbox exists for this API key and point them at https://console.agentmail.to.
- `list_inboxes` returns more than one → show the list and ask which to use; cache the choice for the session.
- An `mcp_agentmail_*` call returns an auth or not-found error → report it exactly as returned.

Always ask the user for guidance when there is an error; do not proactively try to resolve errors yourself.
