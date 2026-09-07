# Manager and task channels on desktop

The desktop sidebar groups each manager's task conversations beneath its home
conversation. Select the manager name to open its conversation; use its separate
chevron to expand or collapse tasks. Sleeping tasks remain selectable and retain
their unread indicators. A collapsed parent exposes unread activity below it.

This is a desktop feature. Other clients continue to see ordinary channels.

## Shared record

Kind **30180**, `KIND_AGENT_WORKSPACE`, is a channel-scoped, parameterized
replaceable event. Its `h` and `d` tags contain the same canonical channel UUID.
The manager signs it with its existing identity. Content:

```json
{
  "version": 1,
  "managerChannelId": "11111111-1111-4111-8111-111111111111",
  "agentPubkey": "<64 lowercase hex characters>",
  "location": "local",
  "state": "sleeping",
  "validUntil": 1788798000
}
```

For a manager root, `managerChannelId` equals this event's channel and
`agentPubkey` equals its author. For a task, they identify its manager's channel
and its worker. Relationships use IDs, so renames do not affect grouping.

The relay requires current channel authority and membership, and a task must
reference a root signed by the same manager in the same community. The task's
creator is its publishing manager. Membership remains the access boundary;
being grouped under a channel does not grant access to either conversation.

The relationship is durable. **Only state has a lease.** `validUntil` is between
the event's creation time and 600 seconds later. A zero-second lease records a
relationship with unknown status. The reporter normally renews 180-second leases
every 60 seconds, including sleeping workers. A dead reporter or disconnected
machine therefore becomes unknown, rather than appearing indefinitely awake or
being falsely described as sleeping. Presence retains its existing meaning of
conversational availability.

These records are excluded from message search and workflow triggers. They are
metadata, not chat messages or unread activity.

## Publishing

Build the CLI and deploy a relay that accepts this kind before enabling reporters.
Publish the root first, followed by workers:

```bash
# Existing manager identity/relay environment; no additional owner key required.
buzz agent-workspace publish \
  --channel "$BUZZ_CHANNEL" --manager-channel "$BUZZ_CHANNEL" \
  --agent-pubkey "$MANAGER_PUBKEY" --location local \
  --state awake --lease-seconds 180

buzz agent-workspace publish \
  --channel "$TASK_CHANNEL" --manager-channel "$BUZZ_CHANNEL" \
  --agent-pubkey "$WORKER_PUBKEY" --location local \
  --state sleeping --lease-seconds 180
```

## Existing Mr. Fix managers

[`workspace_reporter.py`](../scripts/agent-workspaces/workspace_reporter.py)
supports the macOS launchd and Linux systemd layouts from
[`always-on-agents.md`](always-on-agents.md). It reads worker `.busy`, `.channel`,
and `.pub` files, checks the actual service process, and requires a subscription
readiness line from that process's lifetime before reporting it awake. It never
reads a worker private key or starts an agent/LLM turn.

Sleep/wake intent is recorded locally by small hooks in the existing supervisor
and spawner. Publishing runs in its own daemon, so a slow network does not delay
the supervisor's two-second wake loop. Unexpected process loss is distinguished
from a supervisor-requested sleep. Existing workers are discovered automatically;
old supervisor logs supply the last transition until new hooks have run.

Inspect one cycle without writing records:

```bash
. "$HOME/mrfix/env.sh"
python3 scripts/agent-workspaces/workspace_reporter.py run \
  --root "$HOME/mrfix" --manager-cwd "$HOME/mrfix" \
  --manager-pubkey "$MANAGER_PUBKEY" --location local --once --dry-run
```

Install using the CLI built from this revision:

```bash
python3 scripts/agent-workspaces/install_reporter.py \
  --root "$HOME/mrfix" --manager-cwd "$HOME/mrfix" \
  --manager-pubkey "$MANAGER_PUBKEY" --location local \
  --buzz /absolute/path/to/buzz
```

On Linux run the installer as root with `--user <manager-account>`,
`--location remote`, and that manager's actual runtime directory and working
directory. `--prepare-only` writes reviewable files but does not start the reporter.
The installer keeps exact `.before-workspace-reporter` backups of the supervisor
and spawner, and refuses to patch unfamiliar script layouts. It reloads the
worker supervisor before starting the reporter; existing workers continue running.

The reporter service is `com.mrfix.workspace-reporter` on macOS and
`buzz-workspace-reporter.service` on Linux. A failed publish retries and never
counts as a successful refresh. Stop only this reporter to disable publishing;
cached status expires while channel relationships and conversations remain.

## Validation

- Contract/relay tests cover malformed coordinates, signer and membership
  authority, cross-community references, and bounded leases.
- Desktop tests cover snapshot/live ordering, reconnect repair, membership
  removal, community switching, orphan fallback, collapse state, unread status,
  and the distinction between sleeping and unknown.
- Reporter tests exercise process readiness, old log lines, intentional sleep,
  failed startup, publication retries, and exact manager-session selection.
- The runtime acceptance flow is spawn, discover under parent, sleep, wake from
  a message, and confirm the same conversation continues. Worker session-resume
  behavior belongs to the existing bridge and is not changed by the sidebar.

Run the Python tests with:

```bash
python3 -m unittest discover -s scripts/agent-workspaces -p 'test_*.py'
```
