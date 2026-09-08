> Historical design/observations from September 2026, retained for rationale. Not current setup or recovery instructions. Use the [operations index](../../s2-operations.md). The [sidebar](../../manager-task-sidebar.md) is implemented; the manager uses [exact session identity](../../s2-manager-supervisor.md).

# Build our own Buzz? — a case file, deliberately kept open

**Status: open question, not a proposal.** Raised by Khoi 2026-09-07: *"I have an
idea at the back of my mind of building our own buzz but I need to justify it over
time."*

This file exists so the justification accumulates as evidence rather than arriving
as a mood. Two rules for using it:

1. **Append friction as it happens, with the date and the concrete cost.** A
   remembered impression is worth nothing here; a measured hour is worth a lot.
2. **The criteria below were written before the evidence.** Do not edit them to fit
   what accumulated — that turns a decision into a rationalisation. If they are
   wrong, say so explicitly and date the change.

## What we would be replacing

Not one thing — five, and they are separable. Any honest case has to say *which*.

| piece | what it does for us | rebuild cost |
|---|---|---|
| relay | durable store, auth, channels, membership | high, and it is the boring part |
| mobile app | Khoi's phone: notifications, reading, steering | very high |
| desktop app | agents tab, activity panel, channel UI | high |
| identity / NIP-OA | owner attestation, agent ownership | medium — the spec is small |
| `buzz-acp` bridge | channel ↔ agent-runtime adapter | **low** — this is the piece we already fight |

The asymmetry in that last column is the whole question. Most of our friction in
2026-09 was in the bridge, which is also the cheapest to own.

## What would justify it

State a threshold, then measure against it.

- **A.** Friction is concentrated in the bridge, not in the relay or the apps — so a
  rewrite is small and does not cost us the phone.
- **B.** Fork divergence becomes a standing tax: we are carrying patches upstream
  will not take, and rebasing costs real time per upstream release.
- **C.** A capability we need is architecturally refused, not merely absent — i.e.
  upstream's design forecloses it, so a patch is not a path.
- **D.** Time lost to diagnosing undocumented Buzz-side behaviour exceeds what
  building the equivalent would have cost. Measured, not felt.

## What would argue against, and currently does

- The relay is **already ours and already running** (`s2-fin-03`), so the durable
  half of the problem is solved without owning the code.
- The **phone** is the reason this whole line of work exists — *"tell it to do stuff
  for me 24/7 without my laptop online"*. A mobile client is the most expensive
  piece and the least interesting to build.
- Upstream keeps moving in our direction unprompted: mid-turn steering shipped in
  the Claude adapter after we built and reverted our own version, which
  [`S2-CHANGES.md`](https://github.com/system2tech/buzz/blob/s2/S2-CHANGES.md)
  had explicitly hoped for.
- Every hour spent on plumbing is an hour not spent on what S2 is actually for.

## Evidence log

### 2026-09-06/07 — one long session getting remote agents working

Recorded because it is the first substantial body of friction, and it lands almost
entirely in the bridge and the relay's undocumented edges. Cost: roughly a full day,
most of it diagnosis rather than building.

**Criterion A — concentrated in the bridge:** mostly yes.

- **A pre-admitted key silently disables the activity panel.** The relay's
  membership check returns on first hit, so a key that is already a member never has
  its owner attestation read — ownership goes unrecorded and observer frames are
  refused with `observer frame is not authorized for this agent owner`. The pool of
  pre-admitted identities we built was *preventing* the feature it existed to
  support. Nothing says this anywhere; found by reading relay source and the
  `users.agent_owner_pubkey` column.
- **Agent ownership is materialised from an HTTP header**, not from the event
  stream, and the column is write-once. Not documented.
- **The live panel needed four conditions at once** — bot-role membership, a
  self-authored directory record, an owner attestation on the profile, and the
  observer flag *with a resolved owner*. Three were invisible until measured. The
  bridge logs the fourth failure as an INFO line nobody reads:
  `relay observer requested but no agent owner was resolved at startup`.
- **`startup_watermark` is hardcoded to now−5s**, so an agent started in response to
  a message never sees the message that started it. This is a five-line fix in a
  file we cannot rebuild without a toolchain we do not have on the box.
- **A cancelled turn orphans its runtime.** Every interruption leaves a ~250 MB
  process alive; measured 3 processes / 4 cancels on one worker, and previously 16
  sessions in an afternoon.
- **No session lifecycle at all.** A live session pins a 215 MB runtime with no way
  to close it while keeping the identity online — which is the entire content of
  [idle-session proposal](idle-session-proposal.md).

**Criterion C — architecturally refused:** **no, and this is the strongest argument
against.** Everything above is a gap or an undocumented edge, not a refusal. The one
thing that *looked* architectural — that the relay is the durable store and the agent
near-stateless, so session resume is unwelcome — turned out to be right, and our
attempt to fight it was correctly reverted.

**Criterion B — divergence tax:** not yet. The fork is small and the one large patch
we carried was reverted, which *reduced* it by ~400 lines.

**Criterion D — diagnosis time:** the first real data point, and it is not small.
Most of the day went on the four-condition panel and the ownership short-circuit.
Log the next one before drawing a line through two points.

### Verdict as of 2026-09-07

**Not justified.** One day of friction, concentrated in the cheapest component, with
no architectural refusal and no divergence tax. The relay and the phone — the
expensive halves — are working for us.

**The interesting middle option, if this recurs:** own the bridge, keep the relay and
both apps. Replacing `buzz-acp` with our own channel↔runtime adapter would address
almost every item in the log above, costs least, and risks nothing we depend on. If
the log grows, that is the proposal to write — not a new Buzz.
