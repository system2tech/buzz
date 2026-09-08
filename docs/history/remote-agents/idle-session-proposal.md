> Historical design/observations from September 2026, retained for rationale. Not current setup or recovery instructions. Use the [operations index](../../s2-operations.md). The [sidebar](../../manager-task-sidebar.md) is implemented; the manager uses [exact session identity](../../s2-manager-supervisor.md).

> This proposed keeping a bridge alive while closing idle sessions. The shipped worker mechanism stops/wakes worker processes and resumes their saved sessions; do not confuse the two.

# Scope: idle sessions close, and reopen invisibly (design B)

Scoped 2026-09-07 at Khoi's request. **Not built.** Conditions for acceptance are
his, stated verbatim in *Acceptance* below; the rest of this file exists to make
those three conditions testable and to keep the next reader out of a hole we have
already been in.

## The problem, in one line

An idle worker holds a live ACP session, which pins a `claude` runtime at **215 MB**
measured on the agent box 2026-09-07 — so a hundred dormant workers would be ~21 GB
on a 32 GB box with no swap.

Khoi's framing is the right one: *"I open claude code application and I have a
hundred of different chats, I don't see my mac has any RAM problem."* A chat costs
nothing because an idle chat has **no live session** — it is a transcript on disk,
and a runtime is materialised only when opened. Our workers pin one open forever.

## What B is

Close a channel's ACP session when it goes idle; keep the bridge process alive.
Reopen the session on the next message.

| state | today | under B |
|---|---|---|
| idle worker | 215 MB (bridge + adapter + `claude`) | **~10 MB** (bridge only) |
| identity | online | online — unchanged |
| wake | process tree start, ~4 s | session load, no process start |

The bridge is the cheap part: measured **10 MB** of the 215. Keeping it means the
worker stays a live Buzz identity — online, addressable, its activity panel intact —
while the expensive part is dropped.

## Why this is NOT the change we reverted

Read [`S2-CHANGES.md` § *Session resume, and why we stopped*](https://github.com/system2tech/buzz/blob/s2/S2-CHANGES.md)
before touching this. That section is the most useful thing in the file and it
forbids re-litigating Buzz's architecture inside the fork.

It is not being re-litigated. The reverted change argued that re-seeding from the
relay was *wrong* and that `session/load` should replace it. That argument lost, and
should stay lost: the relay is the durable store and the agent is near-stateless.

B makes a different claim. Re-seeding stays the model for a **cold start**. B adds a
session lifecycle so an idle session can be dropped for memory and reopened without
the human seeing a seam. The motive is 215 MB × N, which the revert never weighed
because nothing was running many workers at the time.

One fact has also changed since August. The adapter is now
`@agentclientprotocol/claude-agent-acp` **0.75.1** (renamed from
`@zed-industries/claude-code-acp`, which stops at 0.16.2) and advertises
`sessionCapabilities: {close, resume, list, fork}` — a first-class close/resume pair
that did not exist when we tried this. S2-CHANGES anticipated exactly this: *"If
upstream starts resuming, nothing to do — we no longer fight it."*

## Two pieces

### B1 — close on idle (the memory win)

buzz-acp drops a channel's session after an idle period, keeping the bridge and the
pool. Needs a per-session idle timer and a close call.

Note `BUZZ_ACP_IDLE_TIMEOUT` is **not** this. Its own doc says *"max seconds of
silence before killing a turn"* — a stuck-turn watchdog, unrelated to session
lifecycle. Do not overload it.

### B2 — reopen without wrecking the transcript (the correctness requirement)

On the next message, load the stored session id instead of creating a new session.
This is what buys condition 3: genuine Claude Code continuation, not a re-seed.

**This is where it broke last time, and the mechanism is still live.** In the
installed 0.75.1, the load handler calls `replaySessionHistory` **unconditionally**:

```
const result = await this.getOrCreateSession(params, resumedSession);
timing.phase("session-ready");
await this.replaySessionHistory(params.sessionId, resumedSession.messages);
```

So loading a session re-emits its whole history as `session/update` notifications.
buzz-acp forwards updates to the relay as observer frames, paced at 1/sec — which is
precisely the reverted bug *"activity view minutes behind"*, and it now lands on the
activity panel Khoi spent 2026-09-06 getting to work. **B2 is not done until replay
is suppressed.**

The three reverted bugs, each with its handling:

| reverted bug | handling under B |
|---|---|
| replayed rows stamped with replay time | moot once replayed frames are never published |
| activity view minutes behind | suppress observer publication for the replay window; `isReplay` exists in the adapter (2 refs) — **verify it reaches the update payload**, else use an explicit flag around the load call |
| standing context delivered twice | buzz-acp already has the branch: *"Earlier conversation context was already delivered in this session"* ([`queue.rs:1450`](https://github.com/system2tech/buzz/blob/s2/crates/buzz-acp/src/queue.rs#L1450)). A resumed session must take it. |

## Acceptance — Khoi's three conditions, as tests

1. **Woken by a new message.** Message arrives at an idle worker → session loads →
   answered. No process start, no user-visible restart.
2. **History not messed up.** After a close/reopen cycle, ask about something
   **outside the last 100 messages**. This is the test that distinguishes real resume
   from the re-seed; the 2026-09-06 codeword check (killed mid-conversation, asked
   cold, answered `CINNABAR` correctly) only proved the re-seed, because the fact was
   inside the window.
3. **Indistinguishable from continuing a Claude Code session.** No re-introduction,
   no duplicated context block, and **the activity panel must not flood** — the panel
   is part of this test, not a separate concern.

Test 3 is the one that killed the previous attempt. It fails silently: the worker
answers correctly while the panel is minutes behind.

## Build and deploy

The change is in `buzz-acp` (Rust). Neither the agent box nor the relay box has a
toolchain. The agent box is **x86_64 with Docker 29.2.1** — same arch as the target —
so build in a `rust` container against a checkout of our fork, then replace
`/opt/buzz-bin/buzz-acp`. No new host, no cross-compilation (the Mac is arm64).

Keep the old binary next to the new one: B2 failing test 3 is the likely outcome of
a first attempt, and rollback should not need a rebuild.

## Effort and the honest unknowns

- **B1** — small. Idle timer plus a close call, with care around the pool's
  claim/return path.
- **B2** — medium, and the risk lives here. Suppressing replay is only easy if
  replayed updates are distinguishable at the point buzz-acp publishes them. **Spike
  this first**, before writing anything: load a session, log what the update stream
  looks like, confirm a replayed frame can be told from a live one. If it cannot,
  B2 needs a window flag in buzz-acp and the estimate grows.
- **Build pipeline** — unknown until tried once. `/opt/buzz` exists on the relay box;
  worth checking how the current binary was produced before assuming.

## What we do instead until then

Sleep/wake, built 2026-09-06 and running: the supervisor stops an idle worker
entirely and restarts it within ~4 s of a message, re-seeded from the channel. It
gets the memory profile now and costs a cold start and a prompt-cache miss per wake.
B replaces the cold start with a session load; it does not replace the supervisor,
which still handles crashes, memory pressure and orphaned sessions.

## The alternative, for the record

**Design A — one identity, many channels.** Native buzz-acp, no fork change: one
bridge with `BUZZ_ACP_AGENTS=2-3` subscribed to every task channel. buzz-acp is built
for this — `AgentPool` owns N runtimes and each holds a `channel_id → session_id` map
([`pool.rs:116`](https://github.com/system2tech/buzz/blob/s2/crates/buzz-acp/src/pool.rs#L116)).
Fixed ~500-700 MB regardless of task count.

Rejected because it costs the per-task identity: every task would post as one agent
and share one activity panel. That identity is the thing Khoi asked for on
2026-09-06 and watched working; B keeps it.
