# hermes-skills

Skills for [Hermes Agent](https://hermes-agent.nousresearch.com/), designed to work well with **local language models**.

## Why this exists

Many existing agent skills assume a frontier model (Claude, GPT-4, Gemini) on the other end. They expose large tool surfaces, lean on the model to discover state dynamically, and leave undocumented idioms or intermediate steps for the model to figure out.

Local models are a different problem. They tend to:

1. **Mis-select among large tool sets** — picking a generic `terminal` tool and hallucinating a CLI invocation rather than calling a typed MCP tool.
2. **Hallucinate dangerous calls** — invoking `create_inbox` when the user clearly meant "use the existing one."
3. **Get stuck on undocumented idioms** — without a worked recipe, the model improvises and fails.

The skills in this repo are deliberately **smaller, more prescriptive, and free of obvious footguns**, so a local model has fewer ways to go wrong.

## Currently used with

- `qwen3.6-27b`

Other models welcome — open an [issue](https://github.com/fnord123/hermes-skills/issues) with what you tried, what worked, and what didn't.

## Skills included

| Skill | Purpose |
|---|---|
| [`agentmail-lite`](./agentmail-lite/) | Trimmed [AgentMail](https://agentmail.to/) wrapper — read, send, reply, trash. Drops inbox-lifecycle tools to remove the most common failure modes. |
| [`stock-investment-analysis`](./stock-investment-analysis/) | Rigorous, evidence-based equity research on a single stock. Produces an institutional-style report with citations. |
| [`investment-hypothesis-investigation`](./investment-hypothesis-investigation/) | Adversarial multi-angle research on a thematic or macro investment hypothesis. Quantifies consensus, edge, and trade construction. |
| [`pet-care-tracker`](./pet-care-tracker/) | Record and query a dog's walks and feedings via Home Assistant. Writes go through one webhook; reads are targeted REST GETs. Bundles a complete HA setup template (helpers, scripts, webhook dispatcher) so a new user can stand the whole thing up in 15 minutes. |

## Installation

Each skill installs independently via `hermes skills install`:

```bash
hermes skills install fnord123/hermes-skills/agentmail-lite
hermes skills install fnord123/hermes-skills/stock-investment-analysis
hermes skills install fnord123/hermes-skills/investment-hypothesis-investigation
hermes skills install fnord123/hermes-skills/pet-care-tracker
```

Some skills require additional setup (API keys, MCP server config). See each skill's `SKILL.md` for details.

## Feedback

Open an [issue](https://github.com/fnord123/hermes-skills/issues) for:

- Bugs or unexpected behavior
- Reports of how a different local model performed
- Suggestions for new skills or improvements to existing ones
- Documentation gaps

## Roadmap

- `libby` — OverDrive/Libby library e-book manager. Held back pending parameterization to remove hard-coded library system names (currently makes the upstream version geo-identifying).

## License

MIT — see [LICENSE](./LICENSE).
