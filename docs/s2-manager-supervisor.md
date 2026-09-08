# S2 manager supervisor

The persistent supervisor checks the manager's full saved UUID, not whether any Claude session exists in the same directory. It resumes that conversation if it stops. It writes startup attempts to its log and sends no automatic restart announcements.

Source: [supervisor](../scripts/agent-workspaces/supervise-manager.sh), [identity helper](../scripts/agent-workspaces/manager_identity.py), [reporter](../scripts/agent-workspaces/workspace_reporter.py), and [S2 service override](../scripts/agent-workspaces/mrfix-agent-persistent.conf).

## Check the manager

For accounts prepared by the [personal-manager tool](s2-personal-manager-setup.md), begin with `buzz-manager status --user ACCOUNT` as root or `buzz-manager status` as that person. SSH to the current agent server using [server recovery](s2-server-recovery.md). Find the manager's installed service, then enter its name:

```bash
systemctl list-unit-files 'mrfix-agent*.service' 'buzz-manager@*.service'
printf 'Manager service name: '
IFS= read -r MANAGER_UNIT
systemctl show "$MANAGER_UNIT" -p User -p WorkingDirectory -p ExecStart
```

Use the unit's `User` and the runtime `root` reported by `buzz-manager status`. For a legacy installation, use the runtime directory containing `supervise.sh` shown by the unit:

```bash
printf 'Agent Linux account (User above): '
IFS= read -r AGENT_USER
printf 'Manager runtime directory: '
IFS= read -r AGENT_RUNTIME
cat "$AGENT_RUNTIME/.session-id"
tail -n 10 "$AGENT_RUNTIME/supervise.log"
printf 'Absolute Claude executable from the runtime bin/claude or configured launcher: '
IFS= read -r CLAUDE_BIN
sudo -iu "$AGENT_USER" "$CLAUDE_BIN" agents --json
```

The saved UUID must match the manager's full `sessionId`. Verify that exact session remains running across two scheduled supervisor checks. Other sessions do not count. Check the watcher and the manager's reply in its configured Buzz channel too.

## Restart the manager itself

Restart the manager after changing instructions or runtime files that are loaded only at session start. Finish and save the current work first. As the final action in the manager's current turn, run one command as that manager's normal OS account:

```bash
# Personal manager on the shared Linux server
buzz-manager restart

# Personal manager on a Mac; use the runtime path from its status output
"$RUNTIME/bin/buzz-local" restart
```

The command reads the full UUID from that manager's `.session-id` and asks Claude to stop only that session. Claude keeps the conversation. The persistent supervisor sees the stopped UUID and resumes it, normally within two minutes. The watcher, reporter and task workers keep running, so messages and worker work continue while the manager is briefly unavailable.

When the manager returns, it must re-arm its persistent inbox Monitor and replay from `inbox.cursor`. It should then check its own status and process any messages collected during the restart. This does not require a relay restart.

Do not substitute a supervisor service restart: an already-running background manager may outlive the wrapper. Do not stop a session selected only by directory or by a short ID. If the command reports a missing or malformed saved UUID, repair the retained identity and supervisor first instead of choosing another session.

For a legacy installation without `buzz-manager restart` or `buzz-local restart`, first verify that its persistent supervisor resumes the exact saved UUID as described above. Then the equivalent command, run as the manager's own OS account, is `claude stop "$(cat "$AGENT_RUNTIME/.session-id")"`. Upgrade legacy setups to the managed command rather than keeping this manual step.

## Recover it

Keep `.session-id` and `.session-launched` in the retained runtime directory. Start a stopped manager service with `systemctl start "$MANAGER_UNIT"`. A service restart interrupts its processes; use it only when needed.

A missing identity file creates a new, explicitly identified manager. Before migrating an older installation without that file, identify the actual manager from its conversation and record its full `sessionId` as the agent user; also save that value in `.session-launched`. Never adopt an arbitrary session by directory. A malformed identity or unreadable status response blocks launching and is logged.

The supervisor resumes with `claude --bg --resume <UUID>` and **no extra options or prompt**. Claude can create a copy when extra options are supplied. A cached session with no status and no PID is offline. The sidebar reporter uses the same saved UUID.

## Deploy a supervisor update

For a personal-manager installation, update the shared tooling through the [shared installer](s2-personal-manager-setup.md#install-the-shared-tooling-once), then restart and check only the affected services. The manual steps below apply to legacy runtime copies.

Use the paths and account from the installed unit; do not copy another person's home directory or service name. Back up the installed files before replacement.

1. Copy the supervisor and identity helper from this checkout to the agent's runtime directory. Keep their names `supervise.sh` and `manager_identity.py`; make the supervisor executable and preserve ownership.
2. If updating the reporter, replace its configured runtime copy too; see [reporter installation](manager-task-sidebar.md).
3. Inspect the unit and its overrides with `systemctl cat "$MANAGER_UNIT"`. The [included override](../scripts/agent-workspaces/mrfix-agent-persistent.conf) is an installation example: adapt its account paths and mount dependency to the actual unit.
4. Keep the supervisor persistent (`Type=simple`, automatic restart), with the obsolete launch timer disabled. Run `systemctl daemon-reload`, validate the unit with `systemd-analyze verify "$MANAGER_UNIT"`, and restart the service when ready.
5. Repeat the exact-manager check above. Restore the backed-up files and service configuration if verification fails; do not restore the known-broken oneshot/timer arrangement.

The original oneshot launched Claude inside its control group and killed it when the startup check finished. The persistent-service fix avoids that cleanup; exact UUID matching avoids confusing another agent with the manager. Neither change needs a relay restart.

Dated validation: [recovery evidence](history/remote-agents/supervisor-recovery-2026-09-08.md). Current process IDs, session IDs, addresses and installed versions must come from the running installation.
