# S2 task-worker operations

The [always-on agents guide](always-on-agents.md) owns identity, channel roles, adapter compatibility and sleep/resume behavior. This page is the operator checklist for an existing deployment.

## Locate the worker

On Linux, list `systemctl list-units --all 'buzz-worker@*' 'buzz-worker-*@*'` and inspect the selected unit with `systemctl cat UNIT`. On macOS, inspect its installed worker LaunchAgent. Use the account, runtime directory, launcher and logs those definitions name.

New [personal-manager installations](s2-personal-manager-setup.md) name workers `buzz-worker-USER@SLUG`; legacy installations may still use `buzz-worker@SLUG`. Use the owning account as well as the task slug to identify the service.

The runtime's worker registry maps each task slug to its channel, agent public key and launcher. Read limits and idle thresholds from its installed supervisor. Configured awake limits are an idle-shedding policy, not a guaranteed limit on simultaneous busy build jobs.

## Diagnose

| Symptom | Check |
|---|---|
| Manager hears nothing | Its message-watcher service and error log; confirm subscriptions recover and failures clear. |
| Worker never answers | Its launcher, adapter authentication and subscription log. The task must arrive after subscription is ready. |
| No live activity | Owner attestation, bot membership, and `relay observer enabled` in its log. |
| Worker appears idle | Compare its service CPU usage across checks; buffered logs and channel silence can hide active work. |
| Memory grows after interruptions | Inspect processes in that worker's service group for orphaned runtimes. |
| Sidebar says Unknown or Failed | [Reporter and lease checks](manager-task-sidebar.md), then the actual worker/manager service. |
| Deleted channel remains in the registry | Reconcile that stale entry through the supported retirement process; do not recreate the channel just to clear a reporter denial. |

## Sleep, wake and retire

Sleep is temporary: retain the worker identity, channel and saved session map. A message wakes the worker through its supervisor. Retiring a worker and deleting its channel/history are separate actions; use the installed `reap-worker.sh` only when retirement is intended.

Where supported by the deployed bridge, `BUZZ_ACP_SESSION_MAP` preserves the channel-to-session mapping and waking loads the real adapter session. Keep the configured bridge binary and session files during recovery. Find the executable through its launcher/symlink; do not replace it with an unmodified upstream build or an old binary name from a document.

Before changing a pinned adapter, review the dated compatibility findings in [always-on agents](always-on-agents.md#23-adapter-choice-and-the-trade-it-forces) and retest actual channel replies and observer replay. Logs can show a completed model turn even when no reply reaches Buzz.

Worker wake acceptance: retain a fact only in the worker's session, let the worker sleep, wake it through its channel, verify the fact and an undelayed activity panel. Read-only channel history alone cannot prove session resumption.

Historical implementation findings and remaining proposals are in [the history index](s2-operations.md#design-history); they are not current machine settings.
