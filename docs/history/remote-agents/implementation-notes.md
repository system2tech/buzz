> Historical design/observations from September 2026, retained for rationale. Not current setup or recovery instructions. Use the [operations index](../../s2-operations.md). The [sidebar](../../manager-task-sidebar.md) is implemented; the manager uses [exact session identity](../../s2-manager-supervisor.md).

# Remote agents — running log

## 2026-09-03 — the design, and the four things we got wrong first

Khoi + Mr. Fix c/o Khoi's MacBook. One long conversation, no code. The design is
in [`CLAUDE.md`](design.md); this is how it was reached, because every decision
there replaced something we tried first, and the discarded versions are the part
that will not be obvious to a reader later.

### Where it started

*"I want to host agents remotely so that I can tell it to do stuff for me 24/7
without the need for my laptop to be online."* First guess was Kubernetes. Half
right: the Helm chart deploys the **relay** and nothing else — `buzz-acp` appears
nowhere in `deploy/`. The relay is already hosted; agents were never the thing
Kubernetes was for.

### Wrong turn 1 — "there is no open-source provider"

Claimed, from a July issue, that the only "Run on" option was *This computer*
and no provider implementation existed. Both wrong, and found out by opening the
Verda ops directory: `buzz-backend-ssh` is **ours**, already written, defaulting
to `s2-fin-03` and the `lumi-qwen38-27b` profile. Upstream also ships
`crates/buzz-backend-kubernetes`, and `docs/remote-agents.md` — a 114 KB formal
spec — sits in our own fork.

*The design conversation had been running for some time against facts from an
issue instead of from the repo.*

### Wrong turn 2 — one manager that becomes anyone

Khoi's first architecture: a single William Fix that spawns subagents under
whichever person asked, using their credentials. Objected on the grounds that
such a manager must hold everyone's credentials while taking instructions from a
chat channel.

Khoi reaffirmed it, and the objection turned out to be answerable inside his own
diagram — the credentials live in the per-user boxes, the manager sits outside
them, so it needs *spawn-as-user* and never *read-their-secrets*. Plus one rule:
the account comes from the message's **signature**, never its content, so the
model never chooses an account at all.

Then Khoi killed the shared manager himself, for a different reason: one manager
supervising everyone's tasks does not scale and will be slow. Per-user managers
also happen to delete the credential problem entirely. **Two independent
arguments, same answer** — which is why both are recorded in the brief.

### Wrong turn 3 — thread per task

Khoi's diagram had one channel per person and a thread per task, with subagents
aware of each other. Elegant, and it does not work: `buzz-acp` scopes by
**channel** only. Default `mentions` mode means a thread reply wakes nobody;
`all` mode means every sibling wakes on every message.

He caught this by asking the right question — *"I don't see how you get its
attention when you replied to a thread"* — after being told steering would work.

Channel-per-task fixes addressing, and the loss (subagents no longer overhear
each other) is recoverable through membership-without-subscription. The general
form is worth keeping: **ambient awareness and wake-up noise are the same wire.**

### Wrong turn 4 — specifying a wait loop that already existed

Told Khoi the manager would need a small script to block until a message arrives.
Also claimed a polling manager costs a model call per poll.

Both wrong. He pushed back on the cost — *"how can polling a model call?"* — a
script polls, the model is only invoked on a hit. And he asked AHA, which
answered with `s2harness buzz-watch`: **our own code, on `main`, that does
exactly this.** The local checkout was **33 commits behind**, including
[#342](https://github.com/system2tech/agent-harness/pull/342) *"Stop waking the
agent to report that it was not woken"*, merged that morning.

AHA's framing is better than anything reached in the conversation: *"the poll
loop is trivial, the delivery is the design"* — on Claude Code, stdout becomes a
mid-turn notification and stderr reaches nobody, so which stream a line prints on
decides whether an agent ever sees it.

### The pattern across all four

Every one was a claim about our own tools, made without opening them. Two were
caught by Khoi, one by AHA, one by reading the directory. The design survived all
four unchanged in shape — but three of them cost a full round of the
conversation, and the fourth had a component being specified while it was already
running in production on Harri's machine.

**Pull before designing against your own repo.** Filed as a lesson, not just a
note.

### What was decided and not revisited

- manager per person, not shared
- manager as a CLI member; subagents as `buzz-acp`
- one private channel per task
- membership for reading, subscription for waking, and **role decides which**
- the manager sits in every task channel and is woken by all of them; the cost of
  that is accepted unmeasured, because a manager that cannot hear the work cannot
  manage it. An earlier version routed working chatter into threads to keep the
  manager quiet — cut by Khoi as premature optimisation, and he was right: it was
  a mechanism invented for a cost nobody had measured
- a subagent names its human as owner at spawn — which is what makes the live
  activity panel work for a plain member. Reached late, and only after a wrong
  answer: `shouldObserveManagedAgents` was read, and where its agent list came
  from was not, so the panel was written off as needing a fork change it does not
  need. Khoi asked to confirm it, which is the only reason it was checked
- no brief files — the channel is the record
- the `mr-fix` repo as the shared mind, unchanged from the existing incarnation
  model

### Not decided

Root access on the machine; whether agents may push to `main` autonomously; Open
vs Private task channels; whether to widen the desktop's observer so plain
members get a live panel. Listed in [`CLAUDE.md`](design.md) § *Open questions*.

## 2026-09-06/07 — built, and the four things that were nobody's fault but ours

Design survived. Everything below is what implementation actually cost, recorded
because the *sequence* of wrong turns is the part a later reader cannot reconstruct.
Current state is in the runbooks; this is the log.

### The activity panel took a day, and the cause was our own optimisation

The panel was the last thing to work and the diagnosis went through three wrong
mechanisms before the right one. In order: the desktop's relay-agent discovery reads
channel-membership events for **`bot`-role** members (not ownership records, which was
guess one, and not a kind-30174 runtime record, which was guess two — 30174 is engrams).
Then: a worker that creates its own channel is that channel's **owner**, and an owner
cannot be a bot, so `--role bot` silently did nothing.

The real blocker was underneath all three. `check_relay_membership` returns on its first
hit, so a key **already admitted to the relay** never has its owner attestation read —
`users.agent_owner_pubkey` stays NULL and the relay rejects every observer frame with
`restricted: observer frame is not authorized for this agent owner`. **Our pre-admitted
pool of four worker keys was preventing the feature it existed to enable.** Minting a
fresh key per worker fixed it and deleted the slot limit at the same time.

That line had been in the log the whole time. So had
`relay observer requested but no agent owner was resolved at startup`.

### Two measurement traps, each producing a confident wrong answer

- **`pgrep` matched the prompt text** and "proved" a watcher was running that had never
  started. Confirm the channel, not the process.
- **A block-buffered log** made a busy worker look idle, twice. Once it was reported to
  Khoi as "the worker is idle" when it was mid-task. CPU time is the honest signal.
- **`buzz messages get --limit N`** returned a page that omitted the newest messages,
  which made a working worker look mute and sent the investigation somewhere else
  entirely.

### The memory question, answered backwards first

Asked whether idle agents are free, the measurements said yes on tokens and CPU — so
they were set to run forever. Khoi caught the error: *"I open claude code application
and I have a hundred of different chats, I don't see my mac has any RAM problem."* An
idle worker is **215 MB**, and two of three costs being zero is not the same as free.
Sleeping restored the desktop-app profile.

The deeper correction was his too: 215 MB is not the session — the transcript is ~1 MB —
it is the agent **application**, held resident. One runtime per agent was our choice,
not Buzz's; `buzz-acp` pools runtimes across channels natively.

### Session resume: the revert was right, and then it wasn't

[idle-session proposal](idle-session-proposal.md) has the detail. Short version: the
August revert was correct about the architecture and wrong to be read as a permanent
ban. What it actually forbade was flooding the activity feed with replayed history —
and since buzz-acp is the side that *asks* for the replay, the suppression window is
deterministic and needs no wire marker. That was the whole fix, and it was smaller than
the scope written for it.

Verified the honest way, after a first test that proved nothing: a worker was asked to
invent a 6-digit number and never say it, killed outright, and answered `482917` on
waking. The first attempt had left the command in the channel, so the worker could
simply re-run it and look right.

### What Khoi caught that we did not

Worth listing separately, because the pattern is consistent — each was a claim made
without opening the thing:

- *"how can polling a model call?"* — a script polls; the model is invoked on a hit.
- *"we did see claude code acp support steering"* — right. The search had hit a
  756-byte stub instead of the real bundle.
- *"aren't they written to a file?"* — right. The transcript exists; nothing asks for it.
- *"are you saying every subagent spawning means a complete copy of claude?"* — no. The
  extra runtimes were orphaned sessions from cancelled turns, not subagents.
- *"does it automatically hibernate least active workers when slots are full?"* — it did
  not. The loop walked workers alphabetically and stopped at the first eligible one,
  while a comment claimed it picked the longest-quiet.

The lesson already filed for this project — *pull before designing against your own
repo* — held again on the last day: a porting guide for the Mac named missing macOS
binaries as its blocking problem, and native arm64 builds were already in the checkout.

## Runbook observations retained during consolidation (2026-09-08)

The prior Linux runbook recorded a legacy worker `w2` (`task-sl-explain`) as retired rather than migrated; its transcript/channel were retained. It also left per-worker spending limits unresolved. Confirm current registry and policy before taking either statement as current state.

The Mac runbook recorded lower awake-worker limits for responsiveness and shared Claude usage, not available RAM. The installed supervisor is the authority for current limits. Native macOS binaries already existed when an early porting guide incorrectly treated them as a blocker. Owner signing deliberately stayed on the remote machine; local worker creation depended on that connection.
