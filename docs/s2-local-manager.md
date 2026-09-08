# Set up a personal local manager on macOS

This runs your own manager and task workers on your Mac, using your Claude login and Buzz identity. Your Mac must stay awake and your macOS user must be logged in. Use [remote setup](s2-personal-manager-setup.md) when the manager should keep working while your laptop is off.

For a fresh setup, follow steps 1–5. An existing manager stays on its current installation; use [existing installations](#existing-installations) below. The new installer defaults to `~/buzz-local-manager` and refuses to overwrite an unrelated runtime or take over another loaded manager's jobs.

## 1. Install your tools and check out the repositories

Use your own macOS account, without `sudo` for the manager. Install [Homebrew](https://brew.sh/) first if needed, following its displayed shell setup instructions. Then:

```bash
brew install python@3.12 node gh
```

Install Claude Code if it is not already available. This is the [official Homebrew installation](https://code.claude.com/docs/en/setup#install-claude-code):

```bash
brew install --cask claude-code
claude auth login
claude auth status
claude --help --verbose
claude agents --help
```

Complete your own Claude sign-in. This manager requires `--bg`, `--session-id`, and `claude agents --json`, plus a persistent Monitor tool in the running agent. The installer checks session-ID support in verbose help and JSON session inspection. Background mode may be absent from help even when supported; verify background launch and Monitor availability during the first authorized manager start. Do not probe with `claude --bg --help`: that can start a session. If those capabilities are absent, obtain the team's compatible Claude build before continuing. A version string alone does not prove compatibility.

Authenticate GitHub as yourself. If you already have working Git credentials, keep them; otherwise run `gh auth login` and `gh auth setup-git`, then complete your sign-in. Choose a checkout directory:

```bash
mkdir -p "$HOME/src"
cd "$HOME/src"
git clone --branch s2 https://github.com/system2tech/buzz.git
git clone https://github.com/system2tech/mr-fix.git
git clone https://github.com/system2tech/agent-harness.git
BUZZ_REPO="$HOME/src/buzz"
BRAIN_REPO="$HOME/src/mr-fix"
HARNESS_REPO="$HOME/src/agent-harness"
TOOLS="$HOME/.local/share/buzz-manager-tools"
```

Reuse existing personal checkouts instead of cloning over them; set those three variables to their actual directories. Use the fork revision containing this guide and `scripts/agent-workspaces/macos/install.py`. If they are missing, fetch the published fork changes first.

Build the fork's Buzz CLI and worker bridge, then install the watcher and the compatible adapter:

```bash
cd "$BUZZ_REPO"
. ./bin/activate-hermit
cargo build --release -p buzz-cli -p buzz-acp
mkdir -p "$TOOLS"
"$(brew --prefix python@3.12)/bin/python3.12" -m venv "$TOOLS/python"
"$TOOLS/python/bin/python" -m pip install --upgrade pip
"$TOOLS/python/bin/python" -m pip install --only-binary=:all: coincurve==21.0.0
"$TOOLS/python/bin/python" -m pip install -e "$HARNESS_REPO"
npm install --prefix "$TOOLS/adapter" --save-exact @zed-industries/claude-code-acp@0.16.2
"$TOOLS/python/bin/s2harness" buzz-watch --help
```

The watcher help must include `room`. Python 3.12 with the binary `coincurve` package above is the tested signing-library baseline; if no compatible wheel is available, stop and resolve the Python/architecture mismatch rather than silently building a different setup. Keep the old adapter package/version deliberately: the newer renamed adapter had a reply-delivery problem in this integration. See the [accepted interruption decision](always-on-agents.md#s2-decision-keep-interruption-for-now-2026-09-08) before changing it.

## 2. Prepare your local runtime

Get the current relay WebSocket URL from the deployment or the community you joined in Buzz. It normally uses `wss://` and the community hostname. Choose your display name and a personal channel name, distinct from your remote manager's channel:

```bash
printf 'Your display name: '
IFS= read -r PERSON_NAME
printf 'Local manager channel name, lowercase with hyphens: '
IFS= read -r CHANNEL_NAME
printf 'Current relay URL: '
IFS= read -r RELAY_URL
RUNTIME="$HOME/buzz-local-manager"
"$TOOLS/python/bin/python" "$BUZZ_REPO/scripts/agent-workspaces/macos/install.py" \
  --root "$RUNTIME" --brain "$BRAIN_REPO" --name "$PERSON_NAME" \
  --channel-name "$CHANNEL_NAME" --relay "$RELAY_URL" \
  --python "$TOOLS/python/bin/python" \
  --buzz "$BUZZ_REPO/target/release/buzz" --bridge "$BUZZ_REPO/target/release/buzz-acp" \
  --watcher "$TOOLS/python/bin/s2harness" --claude "$(command -v claude)" \
  --adapter "$TOOLS/adapter/node_modules/.bin/claude-code-acp" \
  --node "$(brew --prefix node)/bin/node"
"$RUNTIME/bin/buzz-local" status
```

Preparation creates your manager key, runtime scripts, instructions and four staged LaunchAgent files. It starts no jobs and publishes nothing to Buzz. Repeating it with identical settings preserves your manager identity; changing runtime paths or ownership is an explicit migration.

Review `manager/CLAUDE.md` inside the runtime. It imports your company-memory checkout and describes your own inbox, channel, workers and signature. The installer leaves your global Claude instructions untouched. Manager and workers share your existing Claude configuration and macOS Keychain login; an isolated `CLAUDE_CONFIG_DIR` must not be added.

## 3. Authorize your Buzz identity

Use your existing human Buzz identity. Follow [finding your public and private keys](s2-personal-manager-setup.md#buzz-ownership--your-own-identity); keep the private key out of agent chats and terminal command arguments.

In your own private terminal on the Mac:

```bash
printf 'Your existing Buzz public key in hex: '
IFS= read -r OWNER_PUBKEY
"$RUNTIME/bin/buzz-local" configure --owner-pubkey "$OWNER_PUBKEY"
"$RUNTIME/bin/buzz-local" status
```

Enter your private key only at the hidden prompt. Configuration verifies the matching identity, authorizes the separate manager, creates its private channel, adds you as an owner, and joins the manager to the single active open `#agent-managers` channel. Setup stops if that channel is missing or ambiguous; no channel ID is hard-coded. The key is used once and is not saved; the manager signs new worker authorizations with its own key. Existing `.owner-key` files are preserved. Remove one only after verifying worker creation with the updated tool and confirming no legacy tool still uses it. An existing installation that uses a remote signer keeps that arrangement unless you explicitly migrate it.

If configuration stops after an uncertain channel-creation response, inspect the saved `local.json` and your channels before retrying. The tool preserves the identity and records the uncertainty to avoid duplicate channels. A failed owner-membership step can be retried using the same key and channel.

## 4. Start and verify your manager and a worker

```bash
"$RUNTIME/bin/buzz-local" start
"$RUNTIME/bin/buzz-local" status
```

`start` checks prerequisites and relay access, installs your own login jobs and loads missing jobs. It leaves running jobs alone. A label already owned by another runtime blocks startup; do not remove or stop that installation just to bypass the check.

Verify the full flow:

1. Status shows the watcher, manager supervisor, worker supervisor and reporter running. Your saved full manager session ID must match the live session across two supervisor checks.
2. Confirm the manager belongs to both its personal channel and `#agent-managers`. It reads every coordination message but does not acknowledge routine updates.
3. Confirm the manager has a persistent inbox Monitor. Send it a short question in its new Buzz channel and confirm a reply and fresh sidebar status.
4. Give it a small task, or run `"$RUNTIME/bin/buzz-local" spawn setup-check "Reply in this task channel and remember the word maple for the resume test."` The worker must appear beneath your manager, publish a channel reply, and show activity.
5. In `local.json`, temporarily set `worker_idle_minutes` to `3`. Reload **only this new setup's worker supervisor** using the commands below. Leave the test worker quiet, confirm it sleeps, then message its task channel asking for the remembered word. Verify a reply and the same retained session. Restore the previous idle setting and reload the supervisor again.

For that deliberate settings change:

```bash
launchctl bootout "gui/$(id -u)/com.mrfix.worker-supervisor"
launchctl bootstrap "gui/$(id -u)" "$RUNTIME/plists/com.mrfix.worker-supervisor.plist"
```

The normal idle setting is three hours. The default limit of three awake workers sheds only quiet workers; it is not a hard cap on busy tasks. The supervisor checks process-tree CPU activity before sleeping a worker and records message IDs so arrivals during sleep transitions can wake it. Retire the test when finished:

```bash
"$RUNTIME/bin/buzz-local" retire setup-check
```

Retirement preserves its channel, identity, task files and saved session; it disables future automatic wakes.

## 5. Operate and recover it

Use `"$RUNTIME/bin/buzz-local" status` to inspect the installation and `start` to restore missing manager jobs. `stop` stops the manager, watcher, worker supervisor and reporter; it leaves active task workers alone. These are user login jobs, so stopped manager jobs return at the next login unless their installed LaunchAgents are removed as part of an explicit uninstall.

Read `watch.err`, `supervise.log`, `logs/`, and `workers/<slug>.out` under your runtime when a component fails. Keep `.session-id`, `.session-launched`, worker keys and `*.sessions.json`. New task workers are loaded from runtime plists rather than installed as login jobs, allowing intentionally sleeping workers to stay asleep across login. The worker supervisor restores retained active workers and wakes sleeping workers on messages.

After a dependency or code update, repeat the channel-reply and sleep/wake checks; startup logs alone do not prove the adapter publishes replies. No relay restart is needed for this local installation.

## Existing installations

Find their actual LaunchAgents rather than assuming the new runtime path:

```bash
launchctl list | grep 'com.mrfix'
ls "$HOME/Library/LaunchAgents"/com.mrfix*.plist
```

Inspect each plist with `plutil -p PATH` and use its runtime, executable and log paths. Existing local setups may have different manager instructions, signer helpers and worker launchers. Keep their keys, session IDs, pinned adapter and signer arrangement; the fresh installer does not migrate them. See [manager checks](s2-manager-supervisor.md) and [worker operations](s2-worker-operations.md).

## Give this to your agent

```text
Help set up my personal local Buzz manager using docs/s2-local-manager.md in the
Buzz fork. Inspect any existing local installation first and preserve it. Follow
the first-time dependency, identity, runtime and LaunchAgent steps for my Mac;
use my own Claude/GitHub/Buzz accounts and leave global Claude instructions alone.
Tell me when I need to sign in or enter my Buzz key in a private terminal, never
in this chat. Verify actual manager and worker channel replies, then sleep/wake
with the same saved conversation. Report what passed and what remains pending.
```

## Validation scope

The installer and runtime have isolated tests for retained configuration, loaded-job conflicts, personal authentication, failed owner membership, LaunchAgent arguments, CPU accounting and same-second wake messages. These tests do not publish Buzz messages or call a model. Full first-time account authorization and real channel reply/resume acceptance remain required on each new Mac.


Dated live checks, 2026-09-08: a fresh temporary runtime passed real key generation, repeated preparation without identity changes, and native plist validation. Two temporary launchd workers passed start/stop lifecycle checks using a local test executable; no model or relay was called. All fixture jobs/files were cleaned up and the existing local manager's jobs were left untouched. This verifies installation and process wiring, not full new-account Buzz replies or model-session resume.
