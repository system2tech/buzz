# Buzz — System 2 fork

A workspace where humans and agents share channels. This fork keeps its code
changes small and documented — see [`S2-CHANGES.md`](../S2-CHANGES.md) for
exactly what differs from upstream and why, so tracking
[block/buzz](https://github.com/block/buzz) stays cheap.

Our branch: `s2`.

There are **two ways to run this**, and they differ only in which relay you talk
to. The agent setup is identical for both, so do Part 1 either way, then pick a
path in Part 2.

- **Path A — your own relay, on your machine.** Nothing shared. Best for trying
  the thing out and breaking it freely.
- **Path B — the team relay.** Shared channels, shared agents, always on. Your
  agent still runs on your laptop.

## Before you start

- **Docker Desktop**, running. Path A's relay, database and object store are
  containers. Path B needs Docker too, for the build.
- **Access to `system2tech/agent-harness`** — it is private.
- **A model API key.** `DEEPINFRA_API_KEY` works from anywhere. The `lumi-*`
  profiles need a live LUMI allocation and an SSH tunnel, so start with
  DeepInfra.

Rust, Node, pnpm, `just` and Flutter all come from the repo's pinned toolchain
via Hermit. Nothing installs globally.

---

## Part 1 — the agent (both paths)

```bash
# Clone both repos as SIBLINGS, in the same parent directory.
# Buzz needs its `s2` branch; agent-harness needs only its default branch —
# the `acp` subcommand Buzz drives is on `main` as of 2026-09-01.
git clone -b s2 https://github.com/system2tech/buzz.git
git clone git@github.com:system2tech/agent-harness.git

# Build the agent. The pip upgrade is REQUIRED — see agent-harness docs/setup.md.
cd agent-harness
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e .

# Make a model reachable, then check it.
export DEEPINFRA_API_KEY=...          # add to your shell profile to persist
.venv/bin/s2harness profiles          # want `key=set` on at least one line
```

Then wire it into Buzz. The script builds Buzz and registers the `s2harness`
runtime so it appears in the app's harness dropdown:

```bash
cd ../buzz
scripts/s2-setup.sh --check           # reports what is present, changes nothing
scripts/s2-setup.sh                   # builds Buzz, registers the runtime
```

`--check` is worth reading before the real run. It tells you whether the harness
binary exists, whether it speaks `acp`, whether Docker is up, and whether the
runtime is already registered.

> **If you would rather register the harness by hand**, or the script's location
> is wrong for your setup, see [Registering the harness
> manually](#registering-the-harness-manually) below.

---

## Part 2A — your own relay

```bash
. ./bin/activate-hermit && just dev
```

`just dev` starts a relay on port 3000 and opens the desktop app against it. It
refuses to launch if port 3000 is already in use, which usually means a relay
left running by an earlier session.

In the app: **create an identity → create a channel → add an agent** on the
`s2harness` runtime.

## Part 2B — the team relay

```bash
. ./bin/activate-hermit && just desktop-standalone
```

**`desktop-standalone`, not `dev`.** `dev` starts its own local relay and would
ignore the team one entirely.

You need two things from whoever runs the relay:

1. **An invite link**, of the form `https://<relay>/invite/<code>`. It carries
   the relay address, so it is the only thing you need to paste. Treat it like a
   password — anyone holding it can join.
2. Nothing else. Your model key and harness are already set up from Part 1.

In the app: **create an identity**, then at *Join or create a community* choose
**"I already have a community" → "I'm a member or admin"** and paste the invite
link.

> Not "I own the community" — despite being the truthful-sounding option, it
> routes to Block's hosted signup, which is a different product. And not "Join a
> community", which wants an invite *code* rather than a link.

Then **add an agent** on the `s2harness` runtime, exactly as in Path A.

---

## Registering the harness manually

`scripts/s2-setup.sh` writes this for you. Do it by hand if you moved things, or
if you want a second runtime alongside it.

In the app: **Settings → Harnesses → Add custom harness**, then:

| field | value |
|---|---|
| id | `s2harness` |
| label | `s2harness` |
| command | `<absolute path to>/agent-harness/.venv/bin/s2harness` |
| args | `acp` |
| env | *optional* — `AHA_PROFILE=deepinfra-smoke` |

`command` must be the absolute path to the binary inside the virtualenv — not
`s2harness` on your `PATH`, which will not exist.

**`AHA_PROFILE` is optional.** You pick an agent's model in Buzz when you create
it, and that choice is applied as soon as the session opens — so the harness's own
launch profile is a placeholder that gets overridden before you ever talk to the
agent. Set this only if you want to pin what the harness starts on; any name from
`s2harness profiles` works.


If the harness shows as **`(not installed)` and greyed out**, the app has a
cached answer rather than a missing binary: hit **refresh** on that settings
panel, which runs a live probe. It takes 20–60 seconds because it probes every
known runtime, not just yours.

The registration is stored per app instance, under
`~/Library/Application Support/xyz.block.buzz.app.dev.<branch>/custom_harnesses/`.
The instance is derived from your **checked-out branch**, so a harness registered
on one branch is invisible on another. That is a real trap; `s2-setup.sh` derives
the same slug the app does.

---

## Mobile

**Install Buzz from the App Store.** There is no build step — the published client
is the one to use.

Then pair it to your desktop identity: **desktop → Settings → Mobile**, and scan
the QR code with the phone. Pairing carries your existing identity across, so the
phone is *you* — same agents, same permissions — rather than a second person in the
workspace.

**This works on Path B and not on Path A**, and the reason is iOS rather than
anything in Buzz. App Transport Security forces an App Store app onto `wss`, and a
relay serves `ws` clients **or** `wss` clients, never both
([`S2.md`](../S2.md) § *Why the relay scheme is global, and what it costs*). So:

- **Path B (team relay):** works as-is. `wss://buzz.system2ai.com` has a real
  certificate, which is most of why a hosted relay on a real domain is the cleaner
  end state.
- **Path A (your own relay):** the phone cannot reach `ws://localhost:3000` at all.
  Getting there means putting the local relay behind `wss` — Tailscale plus a
  certificate — at which point *every* client has to be `wss` too, including your
  desktop. `S2.md` covers that trade; it is not worth doing just to try the phone.

> **Building the mobile client from source** (`just mobile-install`,
> `just mobile-dev`) is for working *on* the mobile app, not for using it. It is
> also the only way to pair against a local relay, and needs
> `BUZZ_PAIRING_RELAY_URL=wss://pairing.buzz.xyz` in `.env` — a closed relay cannot
> pair a device against itself, because the handshake uses temporary throwaway keys
> that are not members, so the relay rejects them (`404 Not Found`, or
> `relay closed waiting for EOSE`).

## Five things that will otherwise cost you an afternoon

**Set Parallelism to 1** when adding an agent. The default of 10 spawns ten
subprocesses per agent.

**Add an agent to a channel *before* starting it.** Channel discovery runs once,
at startup. Adding it afterwards does nothing until the harness restarts —
despite a log line claiming it subscribed to membership changes.

**`discovered 0 channel(s)` means wrong tenant, not a stuck model.** Buzz keys
tenants on the literal `Host` string and seeds a separate community for *each*
spelling of localhost (`localhost:3000`, `localhost`, `127.0.0.1`,
`127.0.0.1:3000`). An agent in one sees nothing in another, and nothing errors.

**A green dot means the process is alive, not that the agent can work.** An agent
that discovered no channels reports itself online and sits idle forever.

**Relay media is auth-gated, so an agent cannot `curl` an image you send it.**
Attachments arrive as `![image](https://<relay>/media/<sha256>.png)`, and an
unauthenticated fetch returns **HTTP 401** — verified 2026-09-01. Blossom wants a
signed get-auth. The CLI signs it for you:

```bash
buzz media get <url-or-sha256.ext> -o /tmp/img.png
```

Worth telling your agents, because the failure is opaque: they see an auth error on
a URL you can open in your own browser, and the natural conclusion is that the
image is broken rather than that they need a different fetch.

## More

[`S2.md`](../S2.md) covers the tenancy model, why the relay's scheme is global,
and a diagnosis order for a silent agent.
[`S2-CHANGES.md`](../S2-CHANGES.md) lists every change this fork makes to
upstream, and what to re-check when merging a newer Buzz.
