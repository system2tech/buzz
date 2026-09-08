# Always-on agents: running a manager and its workers on your own machine

How to run a Buzz agent that does not die with your laptop, using nothing but the
`buzz` CLI, `buzz-acp`, and your machine's own service manager. Built and measured on
S2's infrastructure 2026-09-05 → 09-07; every gotcha below cost real time.

**This is not [`remote-agents.md`](remote-agents.md).** That specifies the provider
protocol by which Buzz Desktop *delegates* a managed agent to a remote substrate. This
document does the opposite: the agents here are **plain relay members**, owned by you
but not spawned or supervised by your desktop. Nothing in the Agents tab creates them,
and the desktop never needs to be running.

Upstream's own issue tracker names the gap this fills
([block/buzz#2859](https://github.com/block/buzz/issues/2859)): *"Agents can only be
spawned by the desktop app […] Agents die with the laptop. There is no always-on
agent."*

## The shape

```
   you, from your phone
            │
            ▼  Buzz relay (durable store)
   ┌────────────────────────────────────────────┐
   │  #your-channel        #task-<slug> × N     │
   └────────┬───────────────────────┬───────────┘
            │                       │
      ┌─────▼─────┐          ┌──────▼──────┐
      │  MANAGER  │ spawns → │   WORKER    │
      │ buzz CLI  │          │  buzz-acp   │
      │ + watcher │          │  + adapter  │
      └───────────┘          └─────────────┘
```

**Manager** — one per person, permanent. Talks by running the `buzz` CLI; hears through
a watcher process that appends new messages to a file it tails. It plans and delegates;
it does not do the work. Any agent runtime with a shell can be the manager.

**Worker** — one per task, in its own channel. `buzz-acp` bridges the channel to an
agent runtime: it subscribes, prompts the agent with what it hears, posts the answer
back, and publishes live activity frames. Workers are what make the live activity panel
work; managers do not need it.

Two processes, two different mechanisms, for one reason: **only `buzz-acp` emits
observer frames.** A CLI-driven agent works perfectly and shows nothing in the activity
panel until its reply lands.

---

## Part 1 — The manager

### 1.1 Identity, and how to get it onto a closed relay

The manager needs its own keypair. If your relay requires membership
(`BUZZ_REQUIRE_RELAY_MEMBERSHIP=true`), a fresh key is not a member and cannot post.

**Do not pre-admit it from the relay side.** Admit it through a **NIP-OA attestation**
instead: your own key signs "this pubkey is my agent", the agent carries that signature
on every event, and the relay verifies it and admits the agent through you.

```bash
# 32 random bytes is the private key; the Nostr pubkey is the X coordinate.
openssl ecparam -name secp256k1 -genkey -noout | openssl ec -text -noout
```

Then sign the attestation with the owner key — see `compute_auth_tag` in
[`crates/buzz-sdk/src/nip_oa.rs`](../crates/buzz-sdk/src/nip_oa.rs). The preimage is
`nostr:agent-auth:<agent pubkey>:<conditions>` hashed with SHA-256 and signed BIP-340;
the tag is `["auth", <owner pubkey>, <conditions>, <sig>]`. Conditions may be empty.

Export it as `BUZZ_AUTH_TAG` and the CLI attaches it to everything it signs
([`crates/buzz-cli/src/lib.rs`](../crates/buzz-cli/src/lib.rs), `--auth-tag`).

> **The trap that will cost you a day.** `check_relay_membership`
> ([`crates/buzz-relay/src/api/mod.rs`](../crates/buzz-relay/src/api/mod.rs)) returns on
> its **first** hit: if the key is already a relay member it never reads the attestation,
> so `users.agent_owner_pubkey` is never written — and without that column the relay
> **rejects every observer frame** with `restricted: observer frame is not authorized for
> this agent owner`. A pre-admitted key therefore silently disables the live activity
> panel it was supposed to enable. Use keys the relay has never seen.

**Sign the attestation where the owner key already lives.** Generate the keypair on the
target machine, send only the *public* key to wherever the owner key is, and bring back
the tag. The owner key never needs to travel.

### 1.2 Hearing

The manager needs a process that is **not** part of its session. Agent sessions end;
a watcher that ends with them loses every message that arrives while it is down.

`s2harness buzz-watch` (S2's harness) polls the CLI and appends new messages to a file.
Any equivalent works — the contract is: poll, deduplicate, append to a file, and put
liveness output on **stderr**, not stdout.

Then the agent tails that file from a saved cursor:

```bash
FROM=$(cat inbox.cursor 2>/dev/null || echo 1)
wc -l < inbox.log | tr -d ' ' > inbox.cursor
tail -F -n +$FROM inbox.log      # replays the gap, then follows
```

One command both replays what was missed and keeps listening, so the agent cannot miss
a message by forgetting to look back.

> **Do not merge stderr into stdout.** The watcher prints a liveness line every cycle;
> merged, every heartbeat becomes a wake-up and the agent burns a turn on nothing.

> **On Claude Code specifically:** the tail must run as a **persistent** Monitor.
> `timeout_ms` defaults to five minutes, and a Monitor that hits it dies silently —
> which is the exact failure the external watcher exists to prevent.

> **`persistent: true` is necessary, not sufficient.** Measured 2026-09-08: a persistent
> Monitor was reported stopped mid-session — *"may have been stopped (via the UI, Monitor
> timeout, or agent teardown — these leave no transcript marker)"* — while the session
> itself kept running, with the model-id line changing in the same instant. A session
> boundary can therefore tear down the tail without ending the conversation. Nothing was
> lost, and the reason is this section's whole point: the watcher was not the session's.
> Re-arm the tail whenever you are told it stopped, and confirm against the relay rather
> than assuming the cursor is where you left it.

> **Identity is resolved once, at the worst possible moment.** `whoami()` runs at startup
> and is cached for the life of the process. A failure there is deliberately non-fatal, so
> the watcher carries on with "identity unknown" — and never asks again. Startup is exactly
> when the relay is least likely to answer: after a reboot, or a restart during an outage.
> The symptom is that the agent wakes on **its own messages**, spending a turn on every
> reply it sends.
>
> It is worse than cosmetic. `refresh_membership` ends `... if me else True`, so with no
> identity **every visible channel is marked as one you are in**, even where the read
> succeeded and shows otherwise. Under `--subscribe joined` that is the intended fail-open,
> noisy rather than lossy, which is the right direction. Under `--subscribe room`, which
> gates thread replies on that same flag, it silently becomes `--subscribe all` for every
> thread reply in every visible channel.
>
> Measured 2026-09-08 on two machines independently: both managers' watchers had started
> during a relay outage and were echoing their own posts for hours. A restart re-resolves
> it; the durable fix is to retry while the identity is unknown. **The fail-open is not the
> bug — its permanence is.**
>
> **Check it after every watcher restart, and look in the right file.** The banner,
> including this warning, is written with a bare `print`, so it is on **stdout**, which the
> unit redirects to `inbox.log`. Only the heartbeat and the BLIND/RECOVERED lines go to
> stderr in `watch.err`. Grepping the wrong file makes a present warning look absent.

### 1.3 Giving it its instructions — read vs. loaded

The manager needs to know it is a Buzz manager: its channel, how it hears, how it
speaks, whether it may delegate. **Where you put that text decides whether it is
followed.**

Put it somewhere the agent runtime **loads as a standing instruction** — for Claude
Code, `~/.claude/CLAUDE.md` or `<cwd>/CLAUDE.md`. Do **not** put it in a plain file and
tell the agent to read it in its boot prompt.

> **The failure is silent and looks like disobedience.** Measured 2026-09-07: a manager
> whose brief was a file it read at boot kept doing tasks itself, while an otherwise
> identical manager with the same text auto-loaded delegated correctly every time. The
> read version had arrived as a tool result in the conversation and had drifted out of
> attention twenty turns later. Nothing errored; the agent simply behaved like one that
> had been told something once.
>
> **A brief the agent reads is not a brief it follows.** This applies to anything you
> hand an agent through a file read rather than through its context.

On a machine a human also uses, the two obvious locations are both taken — the global
file is the human's own, and a shared repo's is the team's. The way out is to give the
agent a **working directory of its own** holding nothing but its brief, so
`<cwd>/CLAUDE.md` is auto-loaded and belongs to it. Identity from a shared repo still
reaches it if the human's global file imports that repo by absolute path.

And regardless of where it lives: **editing the brief does not change a running
session.** Restart it when you change what the agent is allowed to do.

> **And an instruction you removed is not an instruction reversed.** Measured 2026-09-08:
> `--flat-replies` (#1) stopped `buzz-acp` from supplying a `--reply-to` anchor, and workers
> went on threading regardless, because their base prompt has a Threading section telling
> them to. It took a second change (#2), emitting an explicit *"post top-level, do NOT use
> `--reply-to`"*, to actually get the behaviour. Withdrawing a nudge leaves the default in
> place. When changing an agent's behaviour by changing its inputs, name the default that
> survives your change — if you cannot, you have removed a reason rather than made a change.

### 1.3 Staying alive

A timer that asks "is my session running?" and starts one if not. Announce restarts in
the channel **without a mention**: it fires on every restart including ones where
nothing was lost, and a mention wakes the human's phone for nothing.

> **Check for *your* session, not for *a* session.** Counting live background sessions
> is only safe on a machine nobody else uses. On a shared or personal machine, record
> the id of the session you started and check that one — otherwise the supervisor
> believes the manager is alive because the human happens to be running something.

> **Do not make the supervisor a `oneshot`.** A `Type=oneshot` service that launches the
> agent and then exits takes the agent down with it: systemd tears the unit's cgroup down
> when a completed oneshot's last process exits, so the session dies seconds after being
> started. The timer fires again, finds no session, starts another, and kills that one too.
> Measured 2026-09-08: **sixteen restarts in thirty-two minutes**, each announcing itself in
> the channel, while the human's two questions sat unanswered because nothing stayed alive
> long enough to read them. The announcements came from the supervisor script rather than
> from any session, which is what made it look like an agent that kept crashing instead of a
> supervisor that kept killing.
>
> Make it `Type=simple` with `Restart=always`, and let the script hold its own loop with the
> sleep *inside* it, so the cgroup never empties while the agent is meant to be running.

---

## Part 2 — Workers

The manager spawns these. Each gets a fresh keypair, its own attestation, its own
channel, and its own working directory.

### 2.1 Channel and roles — the part that decides whether you get an activity panel

Desktop agent discovery scans **channel-membership events for members carrying the
`bot` role** and ignores everyone else. And **the creator of a channel is its owner**,
which is not a bot.

So: **the manager creates the channel**, the human joins as `owner`, and the worker is
added with `--role bot`.

```bash
buzz channels create --name "task-$SLUG" --type stream --visibility private
buzz channels add-member --channel "$CH" --pubkey "$HUMAN" --role owner
buzz channels add-member --channel "$CH" --pubkey "$WORKER" --role bot
```

A worker that creates its own channel is permanently that channel's owner, `--role bot`
silently does nothing, and the worker stays invisible *as an agent* however well it
works.

The worker also needs a self-authored **agent profile** record — `buzz channels
set-add-policy` emits it; the policy value is incidental, the record is the point — and
its own kind-0 profile must carry the owner attestation, which the CLI does for you when
`BUZZ_AUTH_TAG` is set.

### 2.2 Launching the bridge

```bash
BUZZ_PRIVATE_KEY=<worker key>
BUZZ_AUTH_TAG=<attestation>            # resolves the owner; see below
BUZZ_ACP_AGENT_COMMAND=<adapter>       # e.g. claude-agent-acp, codex-acp, s2harness
BUZZ_ACP_CHANNELS=$CH                  # scope to this channel only
BUZZ_ACP_SUBSCRIBE=all                 # everything here is for this worker
BUZZ_ACP_KINDS=9                       # messages only — see below
BUZZ_ACP_RELAY_OBSERVER=true           # publish live activity
BUZZ_ACP_CONTEXT_MESSAGE_LIMIT=100     # how much it remembers on a cold start
buzz-acp
```

> **`RELAY_OBSERVER=true` alone does nothing.** Without a resolved owner it logs
> `relay observer requested but no agent owner was resolved at startup; observer frames
> will not be published` and moves on. `BUZZ_AUTH_TAG` (or `BUZZ_ACP_AGENT_OWNER`)
> is what resolves it. That INFO line is the whole diagnosis and it is easy to scroll past.

> **Set `BUZZ_ACP_KINDS=9`.** Creating a channel and adding members emits system events
> too, and with one agent slot each extra arrival cannot queue — it falls back to
> cancel-and-re-prompt. Measured: one worker answered the same question **five times**,
> once per membership event.

> **Wait for the subscription before handing over the task.** `buzz-acp` subscribes
> from `startup_watermark - 5s`, so a message posted more than ~5 seconds before the
> process starts is never dispatched. A fixed sleep is a race: poll the log for
> `subscribed to channel <uuid>` instead. Measured with a 30s delay — woken correctly,
> question never answered, task sat unread in its own channel forever.

### 2.3 Adapter choice, and the trade it forces

Pin the adapter version deliberately.

| | `@zed-industries/claude-code-acp` (≤ 0.16.2) | `@agentclientprotocol/claude-agent-acp` (0.75.x) |
|---|---|---|
| posts replies to the channel | **yes** | **no** (with buzz-acp as of 2026-09-07) |
| mid-turn steering | no | yes |
| standing context delivery | prepended to the first message | system prompt |

Without steering, every mid-turn interruption **cancels the turn**, which invalidates
the session. That costs, per interruption: a new session, a fresh standing-context block
(~4,500 tokens), and an **orphaned agent runtime of ~250 MB that is never reaped**.
Measured: 4 cancels → 3 live runtimes, none using CPU, 1.45 GB resident.

So do not poke a working worker to ask how it is going. Read its log.

---

## Part 3 — Resource behaviour

Numbers measured on an 8-CPU / 32 GB Linux box, 2026-09-07.

| | |
|---|---|
| idle worker, tokens | **zero** — the model is invoked only on a message |
| idle worker, CPU | **zero** — load average 0.00 with workers up |
| idle worker, memory | **~215 MB fresh, ~300 MB after use** |
| the bridge alone | 11 MB — everything else is the agent runtime |

**Memory is the only real cost, and it is flat per runtime.** It does not grow with
conversation length (a 22-turn transcript is ~1 MB on disk) and the runtime is not a
model — it is the agent *application*, and it costs the same as running that agent on
your laptop.

So: **sleep idle workers, do not delete them.** A stopped worker costs nothing and its
channel is a durable record. Wake it when its channel speaks.

> **Waking is cheap; sleeping aggressively is not.** Every LLM turn resends the whole
> conversation anyway, so a wake costs a cold prompt cache, not an extra prompt. But a
> worker that wakes constantly pays that repeatedly. Hours, not minutes, is the right
> idle timeout — and cap **how many run at once**, which is the limit that actually
> protects a swapless box from the OOM killer.

> **Judge "idle" by CPU, not by channel silence** — a worker mid-task is silent by
> definition — and **not by whether its log is growing**, which is block-buffered and
> lags minutes behind reality. That signal produced two wrong diagnoses here.

### 3.1 Waking without losing the conversation

Buzz's design is that **the relay is the durable store and the agent is near-stateless**:
a restarted worker is re-seeded with the channel's recent messages, plus an instruction
to run `buzz messages get` for anything older. That recovers what the worker **said**.

It does not recover what the worker **did** — its commands, their output, what it had
already ruled out. That lives in the agent runtime's own transcript on disk.

If your adapter advertises `agentCapabilities.loadSession`, `session/load` restores the
real session. **If you build that, suppress the observer while it runs:** `session/load`
re-emits the entire history as ACP updates before it answers, and forwarding those to
the relay floods the activity panel and leaves it minutes behind. The window is
deterministic — everything between sending the request and receiving the reply — so no
wire marker is needed. Getting this wrong is why S2 reverted an earlier attempt; see
`S2-CHANGES.md` § *Session resume, and why we stopped*.

---

## Part 4 — Per-platform service wiring

The pieces are identical; only the service manager differs.

### Linux / systemd

Template units keyed by name: `buzz-worker@<slug>.service` with
`ExecStart=<dir>/<slug>.launch` and `Restart=always`. `Restart=always` covers crashes
but not `systemctl stop`, which is exactly what you want — a supervisor can put a
worker to sleep without systemd immediately undoing it.

Grant the unprivileged user only what it needs:

```
<user> ALL=(root) NOPASSWD: /usr/bin/systemctl start buzz-worker@*, \
                            /usr/bin/systemctl stop buzz-worker@*
```

### macOS / launchd

User agents in `~/Library/LaunchAgents`, controlled with
`launchctl bootstrap|bootout|kickstart gui/$(id -u)`. **No `sudo` needed** — user agents
run in your own domain. Validate with `plutil -lint`.

Four macOS-specific traps, all measured 2026-09-07:

1. **Credentials may not be in a file.** Claude Code on macOS keeps its login in the
   **Keychain**; `~/.claude/.credentials.json` does not exist. So giving the agent its
   own `CLAUDE_CONFIG_DIR` — the obvious way to keep its instructions separate — makes
   it report *"Not logged in"* while the same command on the default config dir works.
   Share the config dir and put the agent's instructions in a plain file named in its
   boot prompt instead. **This applies to workers too, not just the manager** — a worker
   with its own config dir fails mid-turn with `Authentication required`, which looks
   like a relay problem and is not.
   
   **Sharing the config dir also moves the transcripts.** A worker's own session history
   then lands in `~/.claude/projects/<cwd with slashes turned to dashes>/` rather than
   under its working directory. If you tell a worker where its history lives, compute
   that path rather than hardcoding it — otherwise it goes looking and finds nothing.
2. **Never overwrite `~/.claude/CLAUDE.md` on a machine a human uses.** It is loaded by
   *every* session on that machine. On a dedicated box it is a fine place for the
   agent's boot file; on someone's laptop it is their identity.
3. **`StandardOutPath` can truncate on restart.** If the file it points at is your
   inbox, a restart destroys it. Point the plist at a wrapper script that appends with
   `>>` instead.
4. **`launchd` has a minimal PATH.** A Homebrew-installed binary is not on it; set
   `PATH` explicitly in `EnvironmentVariables` or the supervisor silently finds nothing
   to run.

`coincurve` may not build (it needs `libsecp256k1`); `openssl` does secp256k1 natively
and is enough for key generation.

---

## Checklist

```
[ ] relay reachable; note whether it requires membership
[ ] manager keypair minted, attestation signed by the owner key (owner key stays put)
[ ] manager channel created, human added
[ ] watcher running under the service manager, appending to a file, liveness on stderr
[ ] agent tails the file from a cursor, persistently
[ ] the brief is AUTO-LOADED (a CLAUDE.md the runtime picks up), not read from the
    boot prompt — and the agent is restarted whenever the brief changes
[ ] supervisor checks for ITS OWN session id and announces restarts without a mention
[ ] supervisor is NOT a `oneshot` — it holds its own loop, so exiting does not kill the
    agent it just started
[ ] after every watcher restart: identity confirmed in inbox.log (NOT watch.err), because
    an unresolved identity turns `room` into `all` for thread replies
[ ] worker spawn: fresh key + attestation per worker, manager creates the channel,
    human as owner, worker as bot, agent-profile record published
[ ] worker bridge: KINDS=9, CHANNELS scoped, RELAY_OBSERVER with a resolved owner,
    CONTEXT_MESSAGE_LIMIT raised, task handed over only after "subscribed to channel"
[ ] adapter version pinned, and the steering/replies trade understood
[ ] sleep policy: hours not minutes, cap concurrency, judge idle by CPU
[ ] verify: kill a worker, speak in its channel, confirm it comes back and continues
```

The last line is the only test that matters. Everything above is how you pass it.
