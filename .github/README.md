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

Check the `acp` subcommand is there before going further — it is what Buzz drives,
and a missing one is much easier to diagnose now than from inside the app:

```bash
.venv/bin/s2harness acp --help        # should print usage, not an error
```

That is the whole of Part 1. Buzz gets built by the command in Part 2, and the
harness is registered from inside the app — see [Registering the
harness](#registering-the-harness) once it is running.

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
. ./bin/activate-hermit
BUZZ_VITE_PORT=13914 BUZZ_INSTANCE_SLUG=team \
  BUZZ_RELAY_URL=wss://buzz.system2ai.com just desktop-standalone
```

**`BUZZ_VITE_PORT=13914` is not optional here.** The dev build serves its UI from a
local port, and that port is normally derived from a hash of *where you cloned the
repo* — so it differs on every machine. The relay only accepts requests from
origins on its allowlist, so an unpinned port gets your very first request blocked
by the browser: the UI says **"Load failed"** and nothing appears in the terminal,
because the request never leaves the webview. Pinning it to the one port the relay
allows avoids that. (A packaged App Store build has no dev server and no port, so
this does not apply to it.)

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

## Registering the harness

Once the app is running: **Settings → Agents**, then under **Agent runtimes** click
**Add runtimes** → **Custom harness** in the left panel. Fill in three fields:

| field | value |
|---|---|
| Name | `s2harness` — the **ID** beside it auto-derives, leave it |
| Command | `<absolute path to>/agent-harness/.venv/bin/s2harness` |
| Arguments | click **Add argument**, then a single argument: `acp` |

Leave **Env vars**, **Docs URL** and **Install hint** empty. Save.

`Command` must be the absolute path to the binary inside the virtualenv — not
`s2harness` on your `PATH`, which will not exist. And `acp` belongs in
**Arguments**, not appended to Command: the field is a command, not a shell line,
so `.../s2harness acp` there is one filename that does not exist.

It should then appear in the **Agent runtimes** list marked **Ready**, alongside
Claude Code and the others. If it says **`(not installed)`** or stays unready, use
the **Check again** button at the top right of that section — the status is cached,
and the button runs a live probe. It takes 20–60 seconds because it re-probes every
runtime, not just yours.

The registration is stored per app instance, under
`~/Library/Application Support/xyz.block.buzz.app.dev.<slug>/custom_harnesses/`.
The instance is derived from your **checked-out branch**, so a harness registered on
one branch is invisible on another. If the runtime disappears after a `git switch`,
that is why — register it again on the branch you are using.

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

## More

[`S2.md`](../S2.md) covers the tenancy model, why the relay's scheme is global,
and a diagnosis order for a silent agent.
[`S2-CHANGES.md`](../S2-CHANGES.md) lists every change this fork makes to
upstream, and what to re-check when merging a newer Buzz.
