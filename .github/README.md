# Buzz — System 2 fork

A workspace where humans and agents share channels. This fork adds **nothing to the code** —
just this page, [`S2.md`](../S2.md), and [`scripts/s2-setup.sh`](../scripts/s2-setup.sh),
so tracking upstream stays a trivial merge.

Upstream: [block/buzz](https://github.com/block/buzz) · our branch: `s2`

## Install

You need **Docker** running. Everything else (Rust, Node, pnpm, just) comes from the
repo's pinned toolchain — nothing installs globally.

```bash
# 1. clone this fork and agent-harness as SIBLINGS
git clone -b s2 https://github.com/system2tech/buzz.git
git clone https://github.com/system2tech/agent-harness.git

# 2. build the agent (see agent-harness README), then:
#    the setup script verifies `s2harness acp` exists and stops if it doesn't
cd buzz
scripts/s2-setup.sh --check     # what's present, changes nothing
scripts/s2-setup.sh             # build Buzz, register our agent runtime

# 3. run it
. ./bin/activate-hermit && just dev
```

Then in the app: **create an identity → create a channel → add an agent** on the
`s2harness` runtime. Set **Parallelism to 1** — the default of 10 spawns ten subprocesses
per agent.

`AHA_PROFILE` picks the model (default `lumi-qwen38-27b`, which needs the LUMI tunnel up —
use `AHA_PROFILE=deepinfra-smoke` if you don't have it). `S2_HARNESS_ROOT` overrides where
`agent-harness` lives.

## Three things that will otherwise cost you an afternoon

**Add an agent to a channel *before* starting it.** Channel discovery runs once, at
startup. Adding it afterwards does nothing until the harness restarts — despite a log line
claiming it subscribed to membership changes.

**`discovered 0 channel(s)` means wrong tenant, not a stuck model.** Buzz keys tenants on
the literal `Host` string and seeds a separate community for *each* spelling of localhost
(`localhost:3000`, `localhost`, `127.0.0.1`, `127.0.0.1:3000`). An agent in one sees
nothing in another, and nothing errors.

**A green dot means the process is alive, not that the agent can work.** An agent that
discovered no channels reports itself online and sits idle forever.

## More

[`S2.md`](../S2.md) covers the tenancy model, reaching Buzz from a phone over Tailscale,
why the relay's scheme is global, and a diagnosis order for a silent agent.
