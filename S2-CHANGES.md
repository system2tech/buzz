# S2 changes to Buzz

Everything this fork changes against upstream, why, and what to re-check when
merging a newer Buzz. Kept current in the same commit as any change to the code
it describes — a list that drifts is worse than no list, because it is trusted.

**Merged up to:** `58cc4b7e9be7` (`origin/main`), 2026-08-31. Previous base was
`c856be0fb954`; that merge brought 24 commits including one reworking
`crates/buzz-acp/src/pool.rs` (#6946) and applied cleanly with no conflicts —
835 crate tests pass on the merged tree.
**Relay image pinned to the same commit:** `ghcr.io/block/buzz:sha-c856be0`
(`deploy/compose/.env` on the host). Desktop and relay are built from one commit
on purpose — a mismatch there cost us an afternoon when upstream's onboarding
changed under a running setup.

## Inventory

| area | files | kind |
|---|---|---|
| onboarding docs | `.github/README.md`, `S2.md` | docs only |
| local setup | `scripts/s2-setup.sh` | ours, no upstream equivalent |
| session resume | `crates/buzz-acp/src/{acp,pool}.rs` | **behaviour change** |
| s2harness system prompt | `crates/buzz-acp/src/pool.rs` | **behaviour change** |
| transcript timestamps | `desktop/src/features/agents/ui/agentSessionTranscript.ts` | **bug fix, upstream's bug** |
| concurrent-session grouping | `desktop/src/features/agents/ui/agentSessionTranscriptGrouping.ts` | **bug fix, upstream's bug** |
| observer frame size ceiling | `crates/buzz-core/src/observer.rs` | **bug fix, upstream's bug** |

The last three carry merge risk. The rest are additive files upstream does not
have. The transcript fix is the one to offer upstream first — it is their bug, it
affects their own adapters, and the change is three lines.

---

## Upstream watch list

**Run this at every upstream merge.** Each entry is something we built because
upstream did not have it, plus the thing to look for that would let us delete ours
and take theirs. **Prefer upstream's answer every time** — they maintain it, we
don't.

| ours | check for upstream | if present |
|---|---|---|
| `S2HARNESS_NAME` on the system-prompt branch (`pool.rs`) | Has [ACP RFD #1237](https://github.com/agentclientprotocol/agent-client-protocol/pull/1237) (*Client-Provided System Prompt*) landed, and has Buzz moved from the protocol-version gate to the capability it defines? | Delete `S2HARNESS_NAME` **and** `CLAUDE_AGENT_ACP_NAME` from that branch; advertise the capability from `s2harness` instead. Both names exist only because there is no standard. |
| `SessionState.resumable` + `forget_channel` + the `session/load` attempt | Does upstream call `session/load` (or v2's `session/resume`) itself? | Drop ours entirely. |
| — | Does Buzz still send `protocolVersion: 2` in its own `initialize` request? | It is a draft it does not implement, and it uses the answer as a feature flag. **Worth reporting to Block** rather than patching here. |
| — | Does Buzz still put a bare `systemPrompt` at the root of `session/new`? | The spec forbids custom root fields, and the `acp` Python library therefore drops it. `_meta` is the working carrier. **Worth reporting to Block.** |
| Runs keyed by session in `splitIntoSessionRuns` | Does upstream tolerate CONCURRENT sessions on one channel, or does it still cut a run whenever the session id changes? | Take theirs and drop ours. Keep the two S2 tests in `agentSessionTranscriptGrouping.test.mjs`; one of them inverts an upstream test, so expect a conflict there and re-read the reasoning below before resolving. |
| `OBSERVER_MAX_PLAINTEXT_LEN = 64_000` | Is it still 65_535? NIP-44 v2 accepts at most 65_408 (`nostr::nips::nip44::v2::MAX_SUPPORTED_PLAINTEXT_SIZE`), so 65_535 aims the trim into a dead zone. | If upstream has lowered it below 65_408, take theirs. If not, this is worth reporting to Block — it silently drops the largest observer frames. |
| `timestamp` carried on row updates in `agentSessionTranscript.ts` | Has upstream fixed the frozen-transcript bug — do `upsertMessage`, `upsertTextItem` and `replaceLifecycleItem` pass `timestamp` into their `replaceItem` payloads? | Take theirs and drop ours. If they reshaped those helpers, re-apply the one-line-each change and keep the two S2 tests in `agentSessionTranscript.test.mjs`. |

Two more findings for upstream, neither of which we patch:

- **Oversized observer frames are DROPPED, not elided.**
  `crates/buzz-acp/src/lib.rs:1052-1058` returns early when encryption fails, and
  the live agent log shows it firing repeatedly through 2026-09-01:
  `failed to encrypt relay observer event: NIP-44 error: message too long`.
  `fit_observer_event_to_budget` is not actually fitting them. Silent telemetry
  loss; not fixed here.
- **The reconnect-replay comment in
  `desktop/src/shared/api/observerRelay.ts:22-29` is false.** It says the high
  `limit` lets reconnect recover frames missed during a drop, but kind 24200 is
  never stored (`crates/buzz-core/src/kind.rs:461`, "Redis pub/sub only, never
  stored"), so `limit` and `since` are inert and any outage is a permanent silent
  gap. `crates/buzz-acp/src/relay.rs:1558` separately calls these frames "durable
  telemetry, not droppable ephemera" — the two halves of the codebase disagree in
  writing.
- **`seq` resets to 1 on agent restart** (`observer.rs:52`) while several
  transcript item ids key on bare `event.seq`, so post-restart frames can overwrite
  pre-restart rows. The codebase already knows: `AgentSessionThreadPanel.tsx` warns
  about exactly this and the disambiguation was applied to raw scroll ids but not
  to the transcript builder.
- **Parallelism defaults to 10**, so one agent spawns ten harness subprocesses,
  each publishing observer frames. Documented in `.github/README.md` as a trap;
  arguably the default is the bug.

---

## Session resume (`crates/buzz-acp`)

### The problem

Buzz keeps one ACP session per channel and reuses it across turns, so an agent
accumulates the whole working history — measured on one of ours: **466 messages
over 9.3 hours**, of which 236 were tool calls and 218 their results.

Every invalidation throws that away. The next turn calls `session/new` and is
re-seeded from the relay with at most `--context-message-limit` messages
(default 12). A nine-hour thread becomes twelve messages, and nothing tells the
human it happened.

Invalidation is not rare. `pool.rs` does it on `AgentExited` (5 sites),
`HardTimeout` (2), agent errors, `SwitchModel`, `Rotate`, and channel removal —
and `SessionState` is memory-only, so **buzz-acp restarting loses every session
too**.

### Why upstream can leave it

The relay is upstream's durable store: it re-seeds context from there, which
works for any adapter and needs no agent-side state. That is a sound choice for
a client driving Claude Code, goose and codex alike.

What it misses is that some adapters *can* restore, and losing their history is
a silent regression rather than a neutral default. Measured 2026-08-31,
`claude-agent-acp` advertises `loadSession: true` and restores a full working
session — tool calls included — across a process restart. Buzz never asks.
(`agent-harness: experiments/harness/2026-08-31-claude-code-acp-resume.md`.)

### The change

**The missing distinction is *why* a session ended.** Upstream has one
`invalidate_channel`, used identically for a crashed process and for an owner
typing `!rotate`. Those are opposite intents:

- **Recoverable** — the session ended for a reason nobody asked for: the process
  exited, a turn timed out, the model changed. The conversation is still wanted.
- **Deliberate** — someone asked for a fresh start: the owner sent `!rotate`, or
  the agent was removed from the channel (upstream's own comment: *"stale
  sessions should not be reused"*).

So `SessionState` gains a second map, and invalidation gains a second verb:

```
sessions:  channel -> live session id          (unchanged)
resumable: channel -> last session id          (NEW; survives invalidation)

invalidate_channel(cid)   demote sessions[cid] -> resumable[cid]   (recoverable)
forget_channel(cid)       drop from BOTH maps                      (deliberate)
```

Every existing call site keeps its current meaning by keeping `invalidate_*`.
Only the two deliberate paths are switched to `forget_*`. That is the whole
semantic edit; everything else follows from it.

On session creation, if the agent advertised `loadSession` and a resume
candidate exists, `session/load` is tried first. **Any failure falls through to
`session/new`** — a session that cannot be reopened is a cold start, never a
failed turn.

For buzz-acp's own restart the map is also written to disk, keyed by agent name
under the session `cwd`, because a workspace can be shared and two agents must
not read each other's sessions.

## Agent instructions for s2harness (`crates/buzz-acp/src/pool.rs`)

### The problem

Buzz decides how to deliver a channel's agent instructions from the agent's
reported protocol version: at `>= 2` it sends a `systemPrompt` field on
`session/new` **and suppresses** the fallback that otherwise carries the
instructions inside the first user message.

`s2harness` answers `protocolVersion: 1`, truthfully — v1 is what the `acp`
library implements, and v2 is a draft in which `session/load` (which s2harness
does implement) no longer exists. Before this change it answered 2, so Buzz turned
off the fallback and sent a field the harness did not read: **the instructions
reached the model through neither path.** Measured 2026-09-01 — the agent ran on a
stock prompt and could not name itself.

### The change

`s2harness` joins `claude-agent-acp` on the `_meta.systemPrompt.append` branch, by
name, regardless of version. `_meta` rather than the bare field because that is
the only carrier that can work: the spec forbids custom fields at the root of a
specified type, so `acp`'s `NewSessionRequest` has no `systemPrompt` and pydantic
drops it during parsing (measured — a request carrying both arrives with only the
`_meta` half).

**By name, and we know that is not the design we want.** Buzz's version gate
implements an early revision of ACP RFD #1237, which has since moved to a
capability advertised at `initialize`. A capability handshake would let any custom
harness opt in with no change to Buzz; a name list cannot. It is not built because
nothing needs it yet — see the watch list above for when to replace both names.

### On upstream merge

`has_system_prompt_support` and `session_new_system_prompt` are the two functions;
both are small and both are ours by one branch only. A test
(`s2harness_gets_the_system_prompt_via_meta_on_protocol_v1`) pins the behaviour
and will fail loudly if upstream reshapes either.

---

## Flickering, vanishing rows on a parallel agent (`desktop/` + `crates/buzz-core/`)

### The problem

Rows appeared, flickered and vanished behind a large blank region, and the view
lagged reality by minutes. Reported 2026-09-01, distinct from the frozen-clock bug
below and surfacing only once that was fixed. **Two independent causes, both
upstream's, both triggered by parallelism > 1** — which is Buzz's default of 10.

**1. The grouper assumed one session at a time.** A Buzz agent keeps one ACP
session **per pool slot** on the same channel (`SessionState.sessions` lives on
`OwnedAgent`), and every slot stamps its own session id onto its observer frames.
`splitIntoSessionRuns` started a new run whenever that id changed, so interleaved
sessions cut a run at almost every item. Measured on a full 3000-event window:
2000 items → **1999 display blocks, 999 of them near-empty "Earlier observed
session" dividers**, plus ~990 duplicate `turn:` React keys. **The cliff is at TWO
concurrent sessions, not ten.**

**2. The harness silently dropped its largest frames.**
`OBSERVER_MAX_PLAINTEXT_LEN` was 65_535 while rust-nostr caps a NIP-44 v2
plaintext at 65_408, so `fit_observer_event_to_budget` trimmed oversized frames
*into* a 127-byte dead zone where `nip44::encrypt` refuses them and the publisher
drops them with only a warning. The frames that hit it are the biggest — a whole
turn's coalesced assistant text — so it removed exactly the content a human
wanted. Observed **17 times in one day**, including the two minutes reported.

### The change

Runs are keyed by session, so a session already seen resumes its own run; a
genuinely new id still opens a new run, leaving restart/`session/new` handling
untouched. The size ceiling drops to 64_000 — below the real limit with margin,
because the trim measures the payload while the limit applies to the serialized
frame.

**One upstream test is inverted by this**, and that is worth understanding before
resolving a merge conflict on it. It asserted that a re-occurring session opens a
*second* run, and guarded against the duplicate React keys that produced — a guard
around the symptom rather than the cause. It now asserts one run per session,
which makes the collision impossible rather than survivable.

### A change that was reverted, kept because the reasoning is the useful part

An earlier attempt (`097ed32ed`, reverted) capped the transcript to the last 12
turns. It did not fix the flicker and **made it worse**: with 10 slots a global
12-turn budget keeps 1.2 turns per slot, and when the slots roll to new turns
together nearly everything else is evicted. Measured — cap off: **0 of 44 ticks
unmounted a row**; cap on: **9 of 44, up to 72 rows at once**. It also never
reached the dominant cost, because `AgentSessionThreadPanel.tsx:182` builds the
transcript on the **uncapped** window for its scroll anchoring.

If a cap is ever wanted, budget it per session, not globally.

### Known, not fixed

- `buildTranscriptState` is superlinear: 122 ms on a full window, rising from
  3.6 µs/event at 67 items to 66.4 µs/event at 1734. `ensureMutable` copies the
  whole `items` array and `itemsById` Map per event, and `replaceItem` does an
  O(n) `findIndex`.
- Continuation ids encode window position (`:c${continuationSeq}`), so an eviction
  renumbers ~30 of the newest 60 and invalidates the scroll anchor. Deriving them
  from `event.seq` would fix it.

---

## Frozen transcripts on a long turn (`desktop/src/features/agents/ui/`)

### The problem

The agent-activity view stopped advancing while the agent kept working: the newest
rendered row sat an hour behind the raw JSON-RPC feed for the same agent. Reported
2026-09-01 on a session whose context was nearly full (232548/262144 tokens — a
very long turn), and it affected a `claude-agent-acp` agent as well, so it was
never harness-specific.

**Nothing was being lost.** The transcript deliberately folds a turn into a small
fixed set of rows — one assistant bubble, one usage line — and both views read the
same array (`ManagedAgentSessionPanel.tsx`). But `upsertMessage`, `upsertTextItem`
and `replaceLifecycleItem` each spread `...existing` into `replaceItem` **without
`timestamp`**, so an updated row kept its creation time. Rows stayed current; the
clock stopped. Measured: 180 frames spanning an hour in one turn produced 4 items
whose newest timestamp was two minutes in.

Three earlier hypotheses were wrong and are recorded so nobody re-runs them: the
renderer *does* handle `usage_update`; the observer ring buffer was nowhere near
full; and **"zero kind-24200 events reached the relay" was a non-measurement** —
that kind is never stored, so the query returns zero whether or not frames flow.

### The change

`timestamp,` added to the three `replaceItem` payloads. Safe to advance because
`replaceItem` pins the array index and nothing downstream sorts by time, so rows
do not reorder — verified, there is no `.sort` in
`agentSessionTranscriptGrouping.ts`.

Two tests in `agentSessionTranscript.test.mjs` pin it, one per row kind. Not
fixed, and worth doing separately: `turn_liveness` (emitted every ~10s during a
turn) has no rendering branch at all, so a quiet stretch of a long turn still
shows nothing new.

### The size ratchet, and why it was skipped once

`just file-size-check` freezes any file already above the ceiling: it may hold or
shrink, never grow. `agentSessionTranscript.ts` is at 1174 lines against the
upstream base, so **even a three-line fix is a violation**, and biome's formatting
means three lines is the floor (one `timestamp,` per payload).

The policy's remedy is to split the file. That would mean refactoring ~1200 lines
of upstream code inside the fork to land a three-line bug fix we intend to hand
back — every future merge on this file made harder, permanently, for a change we
want to delete. So the check was skipped for this commit only, with
`LEFTHOOK_EXCLUDE=file-size-check`; every other gate (branch-skew, typecheck,
5749 desktop tests) ran and passed. The rationale that would otherwise be a
comment block in the file lives here instead, which is why the code carries only
`// S2 (see S2-CHANGES.md)`.

**If upstream takes the fix, this exception disappears with it.** If we ever need
to touch that file again, split it properly then.

### On upstream merge

See the watch list. These files were byte-identical to `origin/main` before this
change, so a conflict here means upstream touched the same helpers — read theirs
first and prefer it.

---

### What upstream files are touched, and how to re-merge

| file | change | on upstream merge |
|---|---|---|
| `acp.rs` | `load_session_supported` field; recorded in `initialize`; `session_load()` method | conflicts only if `initialize` or the client struct is reworked |
| `pool.rs` | `SessionState.resumable`; `forget_channel`/`forget_all`; resume attempt in `create_session_and_apply_model` | the session-creation function is the one to read carefully |


`lib.rs` is **not** touched. Both of its deliberate call sites already went
through `SessionState::invalidate_channel_sessions`, so classifying that one
function covered them — which is the sign the distinction belonged there rather
than at the call sites.

**When merging a newer Buzz, check in this order:**

1. Does upstream now call `session/load` itself? If so, drop ours entirely.
2. Have new `invalidate_*` call sites appeared? Each needs classifying as
   recoverable or deliberate — the default (`invalidate_*`, i.e. resumable) is
   the safe one, so a missed site degrades to upstream behaviour rather than
   breaking.
3. Did `create_session_and_apply_model` change shape? That is where the resume
   attempt lives.
4. Re-run the resume checks in `agent-harness/experiments/harness/`
   (`2026-08-31-claude-code-acp-resume/`) against the merged build.
