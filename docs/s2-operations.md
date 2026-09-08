# S2 Buzz operations

This fork owns Buzz setup, operation, troubleshooting, and design history. Start here; keep operational facts in the deployed configuration or Verda/Cloudflare, rather than copying them between runbooks.

For first-time personal setup, start at [personal agents](s2-personal-agents.md).

| Task | Guide |
|---|---|
| Recover the relay and shared agent machine | [Server recovery](s2-server-recovery.md) |
| Recover or inspect an exact manager session | [Manager supervisor](s2-manager-supervisor.md) |
| Run a manager on a Mac | [Local manager](s2-local-manager.md) |
| Diagnose task workers | [Worker operations](s2-worker-operations.md) |
| Understand accepted interruption behavior and deferred steering | [Steering decision](always-on-agents.md#s2-decision-keep-interruption-for-now-2026-09-08) |
| Prepare everyone's remote managers, or finish your own setup | [Personal remote-manager setup](s2-personal-manager-setup.md) |
| Set up an always-on manager and workers | [Always-on agents](always-on-agents.md) |
| Understand the manager/task sidebar and reporter | [Sidebar protocol and setup](manager-task-sidebar.md) |
| Review our differences from upstream | [Fork changes](../S2-CHANGES.md) |

## Where current values come from

- **IPs, instance sizes, location, OS/data volumes, SSH keys:** the team's Verda project and each instance's attached volumes. Record server roles in Verda's names/descriptions so detached volumes remain identifiable.
- **Public relay hostname and DNS:** the deployed Compose `.env` (`BUZZ_DOMAIN`) and the matching Cloudflare zone.
- **Relay image:** the retained Compose `.env` (`BUZZ_IMAGE`) and running container image. Preserve it during recovery; an upgrade is a separate task.
- **Agent account, paths and unit names:** systemd unit definitions; on macOS, the installed LaunchAgent definitions.
- **Manager identity:** `.session-id` in that manager's runtime directory. Treat it as persistent state.
- **Worker limits, adapter and binaries:** the installed supervisor, worker launchers and their configured executable paths.

## Design history

These explain decisions and failed approaches; they are not current operational instructions:

- [Original remote-agent design](history/remote-agents/design.md)
- [ACP integration findings](history/remote-agents/acp-integration-findings.md)
- [Implementation findings](history/remote-agents/implementation-notes.md)
- [Idle-session proposal](history/remote-agents/idle-session-proposal.md)
- [Replacement assessment](history/remote-agents/replacement-assessment.md)
- [Supervisor recovery verification](history/remote-agents/supervisor-recovery-2026-09-08.md)

Mr. Fix keeps only [its identity/context notes](https://github.com/system2tech/mr-fix/blob/main/rd/remote-agents/host-context.md) and a link here. General agent-harness integration history and cross-project lessons remain in Mr. Fix.
