# Buzz ACP integration observations — 2026-08-27

Historical findings from the [agent-harness integration record](https://github.com/system2tech/mr-fix/blob/main/rd/agent-harness/notes.md). Verify against current code before acting.

### Two things about Buzz, found while integrating
- **Routing is a tag comparison, not a decision.** `event_mentions_agent` is set-membership over the
  event's `p` tags; there is no primacy rule and no router LLM anywhere in the dispatch path. So a
  message mentioning two agents wakes **both**, in parallel — orchestration-by-mention only works
  with exactly one mention per message.
- `buzz-acp`'s `models` printer reads `configId`/`displayName` where ACP defines `id`/`name`
  (`displayName` appears nowhere in the 0.12.1 schema), so every ACP agent renders as
  `? (configId: ?)` there. Cosmetic — the model-switch path has an `id` fallback and the `--json`
  path Desktop consumes is correct.
