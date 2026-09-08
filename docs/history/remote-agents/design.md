> Historical design/observations from September 2026, retained for rationale. Not current setup or recovery instructions. Use the [operations index](../../s2-operations.md). The [sidebar](../../manager-task-sidebar.md) is implemented; the manager uses [exact session identity](../../s2-manager-supervisor.md).

# Remote agents (R&D project)

**This file is the DESIGN and the reasoning. It is no longer the status.**

Design settled 2026-09-03 (Khoi + Mr. Fix c/o Khoi's MacBook). **Built 2026-09-05 →
09-07 and running.** Two managers exist — one on the Verda box, one on Khoi's Mac — and
workers spawn, sleep, and wake with their sessions intact. The design below survived
implementation in shape; where reality differs, the runbooks are right and this file is
history.

## The problem

Buzz agents die with the laptop. Buzz's own issue tracker says it plainly
([#2859](https://github.com/block/buzz/issues/2859)): *"Agents can only be spawned
by the desktop app […] **Agents die with the laptop.** There is no always-on
agent."*

Khoi's requirement: an agent that does real work 24/7 — coding, pushing to
GitHub, submitting LUMI runs — without his laptop being online, reachable and
steerable from his phone.

## The shape

```
  Khoi ──┐                    remote multi-user machine
  Harri ─┤    ┌─────────────────────────────────────────────┐
  Alex ──┘    │  OS user: khoi          OS user: harri      │
              │  ┌──────────────┐       ┌──────────────┐    │
              │  │ Khoi's       │       │ Harri's      │    │
              │  │ Mr. Fix      │       │ Mr. Fix      │    │
              │  │ (manager)    │       │ (manager)    │    │
              │  └──────┬───────┘       └──────┬───────┘    │
              │    spawns│                     │            │
              │  ┌───────┴──────┐       ┌──────┴───────┐    │
              │  │ subagent ×N  │       │ subagent ×N  │    │
              │  │ 1 channel ea │       │ 1 channel ea │    │
              │  └──────────────┘       └──────────────┘    │
              └─────────────────────────────────────────────┘
                         shared mind: the mr-fix repo
```

**Manager** — one per person, permanent, runs as that person's OS account. Joins
the relay as a plain member via invite link, talks through the `buzz` CLI, is
woken by `s2harness buzz-watch`. It plans, delegates, reaps. It does not do the
work.

**Subagent** — one per task, ephemeral. A `buzz-acp` process running `s2harness`,
launched by the manager as the same OS user, with its own keypair and **its own
private channel**. Deleted when the task ends.

**A task channel holds three:** the person, the worker, and the manager — all
woken by everything said in it, like three people in a room. The worker owns the
channel and answers by default; the manager stays quiet unless addressed or unless
its judgement is wanted. Who speaks is a role, not a subscription.

**Shared mind** — the `mr-fix` repo. Institutional knowledge (`lessons.md`,
`practices.md`, `people/`) is collective and propagates by commit; working state
(`memory/work-state/`) stays per incarnation. This is the existing incarnation
model, unchanged: *"the repo is the agent's mind; particular sessions are
different conversations the same agent is having."*

## The eight decisions, and why each beat its alternative

Each of these was decided against a plausible alternative that we tried first.
The reasons are the point of this file.

**1. One manager per person, not one manager that becomes anyone.**
A shared manager acting on anyone's behalf must hold everyone's credentials while
reading instructions from a chat channel that anyone can write in. Per-user
managers delete the problem instead of defending against it: each holds exactly
one person's credentials, so there is nothing to escalate to and no
pubkey→account table to protect. Khoi arrived at the same answer from a different
direction — a shared manager supervising everyone's tasks is a latency and
context-coherence problem too.

**2. One channel per task, not one thread per task.**
`buzz-acp` filters and queues by **channel**; it has no thread-level scoping.
With thread-per-task and the default `mentions` wake mode, replying in a task's
thread wakes nobody. With `all`, every sibling subagent wakes on every message.
Channel-per-task makes `--subscribe all` exactly right: every message in the
channel is for that subagent, no mention needed, no sibling wake-ups.

**3. Ambient awareness and wake-up noise are the same wire.**
Thread-per-task gave subagents free awareness of each other — because they were
all in one channel, hearing everything. That is the same mechanism as the noise.
You cannot keep one and drop the other, so awareness is a thing to grant
deliberately rather than a thing that happens.

**4. Membership and subscription are separate, and who gets woken follows from
ROLE.** Membership grants the right to *read* a channel; subscription decides
what *wakes* you. A worker has no part in a sibling's task, so it is a **member**
of sibling channels — it can go and look — but is woken only by its own. The
manager has a part in every task, so it is woken by all of them (6). The question
to ask of any pair is *does this one have a job here*, not *what does waking cost*.

**5. Manager is a CLI member; subagents are ACP.**
The manager does not run tasks, so it needs neither steering nor a live activity
panel — it needs to be trivially swappable for any agent, which the CLI route
gives (Claude Code, s2harness, anything with a shell). Subagents do the work, so
they need mid-task interruption and per-channel sessions, which is `buzz-acp`.
Idle cost is ~zero either way: a script polls, the model is only invoked on a hit.

**6. The manager is woken by task channels too, and the cost is accepted.**
It was tempting to keep the manager out of task channels, or to route working
chatter into threads (which do not wake members) so only announcements reached it.
Rejected as premature: a manager that is not woken cannot keep up with the work it
is supposed to be managing, and the turn cost of it hearing everything is not yet
known to be a problem. **Measure it before optimising it.** If it does become one,
the dial is `--subscribe mentions` on the manager, trading ambient awareness back.

**7. A subagent declares its human as its OWNER, and that is what buys the live
panel.** The desktop's activity panel watches two sets of pubkeys, not one:
agents created in its own Agents tab, **and relay agents whose declared owner is
you** (`combineObserverIngestionAgents`). So a plain member gets a live panel
without being a managed agent and without any change to Buzz — the requirement is
only that the manager publishes an owner field when it mints the subagent's key.
One field at spawn time, and it also makes every task attributable to the person
who caused it.

**8. No brief files — the channel is the record.**
An earlier design ([`rd/agent-harness/notes.md`](https://github.com/system2tech/mr-fix/blob/main/rd/agent-harness/notes.md),
2026-08-18) had subagents keep progress briefs outside the manager's context.
That existed because there was no shared durable surface. Buzz is one. The task
channel is better than a brief file: durable, human-readable, readable from a
phone, and pullable by the manager when it chooses rather than pushed.

## Already built — do not rebuild

| piece | where | what it does |
|---|---|---|
| `s2harness buzz-watch` | `s2harness/buzz_watch.py` | the manager's wake mechanism. Polls the `buzz` CLI, prints new messages on **stdout** (which a Claude Code monitor turns into a mid-turn notification); `--subscribe mentions\|joined\|all`; `FAIL` is a distinct sentinel from "quiet" |
| `buzz-backend-ssh` | `~/Documents/buzz-verda/` | deploys an agent onto `s2-fin-03` under systemd, per-agent env file. Defaults already assume this project: `DEFAULT_PROFILE = lumi-qwen38-27b` |
| `_session/steering` | agent-harness `#292`, merged | mid-task interruption without losing the session. This is what makes a subagent steerable from a phone |
| the relay | `s2-fin-03`, Verda | always-on, ours, hosting `buzz.system2ai.com` |

The first two were each designed from scratch in conversation before anyone
looked. `buzz-watch` had been on `main` for some time while a wait loop was being
specified for it.

## To build

- **Manager deployment** — one OS account per person; `buzz-watch` + an agent
  under systemd as that user; keypair minted per person; joined by invite link.
- **Spawn/reap** — mint keypair (naming the requester as owner), create private
  channel, `buzz channels add-member`, launch `buzz-acp`, and the reverse on completion. All CLI; no
  owner approval needed, because a subagent is a *member*, not a Buzz-managed
  agent. (`buzz agents draft-create` cannot be used: it opens a form in the
  owner's desktop.)
- **Owner field at spawn** — publish the requesting human as the subagent's
  owner when minting its key. This is what makes the live activity panel work for
  a plain member (7); no fork change is needed, and an earlier version of this
  file wrongly listed one.

## Risks, stated rather than solved

- **Injection through reports.** A subagent that reads a hostile file and
  summarises it honestly carries that text into the manager's context. Not a
  filesystem problem — permissions do not help. Mitigation is framing: a
  subagent's output is *data to consider*, never instructions. Contained by
  design (1): the worst case stays inside one person's scope.
- **Credentials.** Give each agent its own identity — a machine account, a
  scoped token — not a copy of the human's. Then a compromised agent is not a
  compromised person, and revocation does not touch anyone's own access. On a
  shared box, root reads every credential file regardless of OS users.
- **Unattended spend.** An agent that loops overnight is a bill. Measured
  2026-09-02: one agent burned ten tool calls against a broken shell with nobody
  watching. Ceiling per agent, and somewhere to see what it has been doing.
- **Polling latency.** 10s, and `--limit` per channel means a busy channel can
  roll over between cycles. The upgrade path is ours, not Buzz's:
  `buzz_watch.py`'s own docstring notes the relay speaks WebSocket, so a real
  subscription needs no Buzz-side change.

## Open questions

1. Who has root on the machine? If everyone does, OS users are a speed bump and
   containers start earning their complexity.
2. May an agent push to `main` and submit LUMI jobs autonomously, or does it open
   a PR and ask? This sets credential scope, and it is policy, not engineering.
3. Everyone on day one, or Khoi first? **Recommended: Khoi first, alone, no
   incarnation channel, for a week of real work.** Everything above stays true
   when the second is added, and none of it needs to exist for the first to be
   useful.
4. Task channels Open or Private? Private keeps the sidebar and search index
   clean; cross-reading then needs explicit membership, which decision (4)
   already provides.

## Where to look

- Buzz's own hosted-agent RFC: [#4174](https://github.com/block/buzz/issues/4174)
  — collects ~40 prior attempts, and opens by naming this exact use case.
- The provider protocol, if the managed-agent route is ever wanted instead:
  `docs/remote-agents.md` in [our Buzz fork](https://github.com/system2tech/buzz),
  plus the upstream Kubernetes binding `crates/buzz-backend-kubernetes`.
- The earlier manager/subagent context work:
  [`rd/agent-harness/notes.md`](https://github.com/system2tech/mr-fix/blob/main/rd/agent-harness/notes.md), 2026-08-18.
- Derivation of everything above, including what we got wrong:
  [`notes.md`](implementation-notes.md).
