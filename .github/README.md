# Buzz — System 2 fork

A workspace where humans and agents share channels. This fork adds **nothing to the
code** — just this page, [`S2.md`](../S2.md), and
[`scripts/s2-setup.sh`](../scripts/s2-setup.sh), so tracking upstream stays a
trivial merge.

Upstream: [block/buzz](https://github.com/block/buzz) · our branch: `s2`

This page sets up Buzz **on your own machine**, with its own relay and your own
agent. Nothing here touches anyone else's setup.

## Before you start

- **Docker Desktop**, running. Buzz's relay, database and object store are containers.
- **Access to `system2tech/agent-harness`** — it is private.
- **A model API key.** `DEEPINFRA_API_KEY` is the one that works from anywhere;
  the LUMI profiles need a live allocation and an SSH tunnel, so start with DeepInfra.

Rust, Node, pnpm and `just` all come from the repo's pinned toolchain. Nothing
installs globally.

## Setup

```bash
# 1. Clone both repos as SIBLINGS, in the same parent directory.
#    agent-harness needs -b acp-server until system2tech/agent-harness#246 merges:
#    the `acp` subcommand Buzz drives lives only on that branch.
git clone -b s2 https://github.com/system2tech/buzz.git
git clone -b acp-server git@github.com:system2tech/agent-harness.git

# 2. Build the agent. The pip upgrade is REQUIRED — see agent-harness docs/setup.md.
cd agent-harness
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e .
.venv/bin/s2harness profiles          # `key=set` on at least one profile

# 3. Make a model reachable. Without this every profile reports key=MISSING.
export DEEPINFRA_API_KEY=...          # or add it to your shell profile

# 4. Wire Buzz to the agent, then run it.
cd ../buzz
scripts/s2-setup.sh --check           # reports what is present, changes nothing
scripts/s2-setup.sh                   # builds Buzz, registers the s2harness runtime
. ./bin/activate-hermit && just dev
```

`just dev` starts a local relay on port 3000 and opens the desktop app against it.
It fails fast if port 3000 is already in use — usually a relay left running by an
earlier session.

Then, in the app: **create an identity → create a channel → add an agent** on the
`s2harness` runtime. Set **Parallelism to 1** — the default of 10 spawns ten
subprocesses per agent.

`AHA_PROFILE` in the registered runtime picks the model. `scripts/s2-setup.sh`
writes `deepinfra-smoke`; change it to a `lumi-*` profile only if you have a LUMI
allocation and a tunnel on port 8010. `S2_HARNESS_ROOT` overrides where
`agent-harness` is expected.

## Three things that will otherwise cost you an afternoon

**Add an agent to a channel *before* starting it.** Channel discovery runs once, at
startup. Adding it afterwards does nothing until the harness restarts — despite a
log line claiming it subscribed to membership changes.

**`discovered 0 channel(s)` means wrong tenant, not a stuck model.** Buzz keys
tenants on the literal `Host` string and seeds a separate community for *each*
spelling of localhost (`localhost:3000`, `localhost`, `127.0.0.1`,
`127.0.0.1:3000`). An agent in one sees nothing in another, and nothing errors.

**A green dot means the process is alive, not that the agent can work.** An agent
that discovered no channels reports itself online and sits idle forever.

## More

[`S2.md`](../S2.md) covers the tenancy model, reaching Buzz from a phone, why the
relay's scheme is global, and a diagnosis order for a silent agent.
