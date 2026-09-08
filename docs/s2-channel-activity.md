# Channel member live activity

S2 extension: a task worker can share live activity with everyone currently in its
channel. It signs its own messages. Its manager creates workers using the
manager's key; the human's private key is used once when authorizing the manager
and is no longer saved by the setup tool.

## Access

- Relay admission accepts a direct member, a member's agent, or one worker
  generation signed by that agent. The human must still be a relay member in the
  same community. Workers cannot extend the chain again.
- Every channel viewer receives a separate encrypted copy of that channel's
  activity. The relay checks both the worker and recipient's current membership
  at publication and delivery, including delivery on another relay node.
- Channel membership grants observation only. Owner controls, configuration and
  management callbacks remain separate. New workers are owned by their manager;
  humans can send ordinary channel messages but do not gain owner control buttons
  merely by viewing activity.
- Joining allows future activity; this is not a shared historical transcript.
  Leaving blocks new delivery. Copies already received can remain in the viewer's
  local history, like other previously received content.

## Wire format

Existing ephemeral kind 24200 and recipient `p` routing are retained. Shared
telemetry adds `observer_channel` with one channel UUID, alongside `agent` and
`frame=telemetry`. The payload remains NIP-44 encrypted for its recipient. There
is no `h` routing tag and the relay does not store these frames.

The decrypted envelope and every batch item must name the same channel. Sharing
includes turn/session status, agent output, thoughts, tool activity and plans.
Prompts, global events, configuration, control results and management requests are
excluded. A shared frame cannot dispatch desktop management callbacks. Existing
untagged owner streams remain compatible.

## Enable and roll out

**Deployed 2026-09-08.** Relay (`buzz-channel-activity:latest`), bridge binaries
(agents + Mac), and desktop sidecar are updated. Source changes alone do not update
running services. Deploy order: relay first, then desktop, then worker bridge and
provisioning tools. Preserve existing keys, saved conversation IDs and worker
authorizations during updates.

The new personal-worker runtime sets `BUZZ_ACP_OBSERVER_CHANNEL_MEMBERS=true`.
The bridge also needs `BUZZ_ACP_RELAY_OBSERVER=true`. Other bridges keep owner-only
observation unless explicitly enabled. Existing worker authorizations remain
unchanged; new workers use manager-signed authorization.

Older setup tools saved a human `.owner-key` file. The new tools neither need nor
delete it. Remove that file only after confirming no legacy worker-spawn script
still uses it; retain the human's own secure identity backup.

Roster requests have a two-second timeout and one-MiB response limit. Currently,
sharing supports up to 64 additional recipients beyond the existing owner stream;
larger rosters fail closed with a warning. Activity batches retain their original
one-second cadence, and recipient copies are paced at at most 20 per second.

## Live acceptance, 2026-09-08

Protocol acceptance passed against the deployed relay with one real Claude worker
on the Mac and one on the agent server:

- Both workers were admitted using their manager's signed authorization and
  published replies under their own identities. No human key was used for creation.
- Two separate temporary channel-member identities received and NIP-44 decrypted
  live activity from each worker. A third, authenticated nonmember received none.
- Removing one viewer stopped its delivery while the other kept receiving frames.
- Direct worker-signed WebSocket publication to a current member succeeded;
  publication to a removed viewer and a nonmember was explicitly rejected.
- Restarting each test worker retained its session mapping and original remembered
  marker. Existing production workers were not restarted.

The two-viewer checks used independent protocol clients. Khoi subsequently
confirmed that activity renders correctly in the desktop after visual inspection.
Together these checks cover live multi-recipient delivery, access rejection and
the human desktop display. The development desktop was already running with the
updated sidecar; it was not restarted again.

The rollout initially missed installed launchers. The installed multi-user
`remote_manager.py` and `remote_workers.py` now match the committed source. Khoi's
legacy local and remote `spawn-worker.sh` launchers now sign with the manager key,
set the manager as immediate worker owner, and enable both observer flags. They
retain the human as a channel owner. Existing worker launch files and legacy
human-key files were preserved.

Legacy macOS signing uses [sign-worker.mjs](../scripts/agent-workspaces/macos/sign-worker.mjs):
pass the runtime directory, the Buzz desktop `package.json` path, and the worker
public key. It uses the desktop's installed crypto dependencies, validates the
manager's saved public key, and accepts hex or nsec manager keys. It does not use
the remote human signer. Fresh local installations continue using `local_manager.py`.

Launcher backups have suffix `.before-channel-sharing`; installed shared-tool
backups use `.before-activity-acceptance`. Restoring a launcher affects future
workers only. It does not change running workers or their saved sessions.

Validation for this follow-up: 107 shared/remote setup tests, 13 macOS setup tests,
and the legacy signer regression test passed. The signer test checks cryptographic
verification, both key encodings, malformed input, self-delegation and public/private
identity mismatch. Run it from the fork root with:

```bash
node --test scripts/agent-workspaces/macos/sign-worker.test.mjs
```

Publication checks for this follow-up: Rust unit suites, desktop/Tauri checks,
desktop type checking, formatting and file-size checks passed. The full desktop
suite passed 5,764 of 5,765 tests; the existing
`test_exhausted_channel_A_switch_to_B_hook_reads_B_from_null_cursor` failed again.
The desktop Git tree is identical to the pre-follow-up `s2` tree. This existing
archived-history regression remains open; live acceptance does not resolve it.

## Automated coverage and desktop acceptance

Automated tests cover recipient encryption, mixed-channel rejection, current
membership and removal, community isolation, bounded manager delegation, desktop
read-only callbacks, and paced delivery to five viewers. The focused suite passes: 847 bridge tests, 44 desktop tests, 120 setup tests,
and real-database authorization tests. The broader suite has a tool-cancellation
timeout also reproduced on the unchanged setup commit. The full relay suite also
has a mesh-demo timeout that passes in isolation. For future rollout acceptance, use two signed-in team members in the same task channel: both should
see live activity; a nonmember must not. Remove one viewer and check that new
activity stops for them. Confirm the worker still resumes its saved conversation.

Implementation: [bridge](../crates/buzz-acp/src/channel_observer.rs),
[shared payload validation](../crates/buzz-core/src/observer.rs),
[relay access](../crates/buzz-relay/src/handlers/event.rs),
[desktop policy](../desktop/src/features/agents/channelObserverPolicy.ts), and
[personal setup](s2-personal-agents.md).
