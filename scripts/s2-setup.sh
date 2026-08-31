#!/usr/bin/env bash
# Register System 2's agent with this Buzz checkout.
#
#   scripts/s2-setup.sh            build Buzz, register the s2harness runtime
#   scripts/s2-setup.sh --check    report what is present, change nothing
#
# Buzz spawns any ACP-speaking binary over stdio, so joining is a registration,
# not a patch — this branch carries no code changes against upstream. See S2.md
# for why each step exists; every one is a failure we hit and diagnosed.
set -uo pipefail

BUZZ_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# agent-harness is a sibling checkout. It knows nothing about Buzz — it is a
# generic ACP server, and Buzz is one of several clients that can spawn it.
HARNESS_ROOT="${S2_HARNESS_ROOT:-$(dirname "$BUZZ_ROOT")/agent-harness}"
# The desktop's app-data directory is per-instance, and the instance slug comes
# from the CHECKOUT'S BRANCH (scripts/instance-env.sh) — not a fixed "main". A
# harness registered into the wrong directory is invisible to the running app,
# which shows up as an empty runtime dropdown and no error anywhere.
SLUG="$(git -C "$BUZZ_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null \
        | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/-/g; s/--*/-/g; s/^-//; s/-$//')"
APP_STATE="$HOME/Library/Application Support/xyz.block.buzz.app.dev.${SLUG:-main}"
# deepinfra-smoke by default: it works from any machine with a key. The lumi-*
# profiles need a live LUMI allocation and an SSH tunnel on 8010, so they are a
# deliberate choice, not a default a new setup should silently inherit.
PROFILE="${AHA_PROFILE:-deepinfra-smoke}"

say()  { printf '  %s\n' "$*"; }
ok()   { printf '  \033[0;32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[1;33m!\033[0m %s\n' "$*"; }

check() {
  say "buzz         : $BUZZ_ROOT ($(git -C "$BUZZ_ROOT" rev-parse --short HEAD 2>/dev/null))"
  say "agent-harness: $HARNESS_ROOT"
  [ -x "$HARNESS_ROOT/.venv/bin/s2harness" ] && ok "s2harness binary" \
      || warn "no .venv/bin/s2harness — build agent-harness first"
  "$HARNESS_ROOT/.venv/bin/s2harness" acp --help >/dev/null 2>&1 \
      && ok "speaks acp" || warn "no 'acp' subcommand — needs the acp-server branch (see below)"
  command -v docker >/dev/null && ok "docker installed" || warn "docker missing"
  docker info >/dev/null 2>&1 && ok "docker running" || warn "docker not running"
  [ -f "$APP_STATE/custom_harnesses/s2harness.json" ] && ok "s2harness registered" \
      || warn "s2harness not registered yet"
}

[ "${1:-}" = "--check" ] && { check; exit 0; }

# ── preconditions ────────────────────────────────────────────────────────────
if [ ! -x "$HARNESS_ROOT/.venv/bin/s2harness" ]; then
  warn "no $HARNESS_ROOT/.venv/bin/s2harness"
  say  "Clone agent-harness beside this repo and build its venv, then re-run."
  say  "Override the location with S2_HARNESS_ROOT=/path/to/agent-harness"
  exit 1
fi
if ! "$HARNESS_ROOT/.venv/bin/s2harness" acp --help >/dev/null 2>&1; then
  # Pulling does NOT fix this: `acp` lives only on the acp-server branch until
  # system2tech/agent-harness#246 merges. Saying "pull" sends people in circles.
  warn "s2harness has no 'acp' subcommand."
  say  "It lives on the acp-server branch until agent-harness#246 merges:"
  say  "  git -C $HARNESS_ROOT checkout acp-server && $HARNESS_ROOT/.venv/bin/pip install -e $HARNESS_ROOT"
  exit 1
fi
docker info >/dev/null 2>&1 || { warn "Start Docker Desktop, then re-run."; exit 1; }

# ── build (hermit supplies rust/node/pnpm/just; nothing installs globally) ────
say "building — first run takes several minutes"
( cd "$BUZZ_ROOT" && export PATH="$PWD/bin:$PATH" \
  && just setup >/dev/null 2>&1 && just build >/dev/null 2>&1 ) \
  && ok "buzz built" \
  || { warn "build failed — run 'just setup && just build' here to see why"; exit 1; }

# ── register the runtime ─────────────────────────────────────────────────────
# No --profile flag: buzz-acp splits --agent-args on commas without
# allow_hyphen_values, so "--profile x" cannot pass through. The env var is the
# only channel — see s2harness/cli.py, which documents this deliberately.
mkdir -p "$APP_STATE/custom_harnesses" "$HARNESS_ROOT/datasets/traces-acp"
cat > "$APP_STATE/custom_harnesses/s2harness.json" <<JSON
{
  "id": "s2harness",
  "label": "s2harness",
  "command": "$HARNESS_ROOT/.venv/bin/s2harness",
  "args": ["acp", "--traces", "$HARNESS_ROOT/datasets/traces-acp"],
  "env": { "AHA_PROFILE": "$PROFILE" },
  "installInstructionsUrl": "https://github.com/system2tech/agent-harness",
  "installHint": ""
}
JSON
ok "registered s2harness (profile: $PROFILE)"

cat <<EOF

  Next:

    . ./bin/activate-hermit && just dev

  In the app: create an identity, create a channel, add an agent on the
  "s2harness" runtime. Set Parallelism to 1 — the default of 10 spawns ten
  subprocesses per agent.

  Three gotchas that otherwise cost an afternoon (S2.md has the rest):

    - Agents join channels as role=bot, and channel discovery runs ONCE at
      startup. Add the agent to the channel FIRST, then start it.
    - "discovered 0 channel(s)" means wrong tenant, not a stuck model. Buzz keys
      tenants on the literal Host string and seeds one community per spelling
      of localhost.
    - A green dot means the process is alive, not that the agent can work.

EOF
