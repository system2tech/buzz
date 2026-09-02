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

## The agent (both paths)

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

## Path A — your own relay

```bash
. ./bin/activate-hermit && just dev
```

`just dev` starts a relay on port 3000 and opens the desktop app against it. It
refuses to launch if port 3000 is already in use, which usually means a relay
left running by an earlier session.

In the app: **create an identity → create a channel → add an agent** on the
`s2harness` runtime.

## Path B — the team relay

```bash
. ./bin/activate-hermit
BUZZ_VITE_PORT=13914 BUZZ_INSTANCE_SLUG=team \
  BUZZ_RELAY_URL=wss://buzz.system2ai.com just desktop-standalone
```

In the app: **create an identity**, then at *Join or create a community* choose
**"I already have a community" → "I'm a member or admin"** and paste the invite
link, of the form `https://<relay>/invite/<code>`.

> Not "Join a community", which wants an invite *code* rather than a link.

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

Note: `Command` must be the absolute path to the binary inside the virtualenv — not
`s2harness` on your `PATH`, which will not exist.

## Create your own agent

**Agents → "+"**, then fill in the fields to create your agent.

## Mobile

**Team relay only.** Install Buzz from the App Store — there is no build step.

Then pair it: **Settings → Mobile**, and scan the QR code with the
phone. Pairing carries your identity across, so the phone is *you* — same agents,
same permissions — rather than a second person in the workspace.

## More

[`S2.md`](../S2.md) covers the tenancy model, why the relay's scheme is global,
and a diagnosis order for a silent agent.
[`S2-CHANGES.md`](../S2-CHANGES.md) lists every change this fork makes to
upstream, and what to re-check when merging a newer Buzz.
