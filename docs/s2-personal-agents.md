# Set up your personal Buzz agents

Use this page when asked to set up a personal manager, locally, remotely, or both. These are persistent managers with task workers. They are separate from adding an ordinary agent through Buzz's Agents tab.

These guides use Claude Code with the person's own login. The message watcher uses the agent-harness package, but watching messages does not require configuring a model API key or profile. The ordinary s2harness-agent instructions elsewhere in the README describe a different workflow.

## What gets installed

The manager's reasoning and conversation are one Claude Code background session. The installation around it makes that session persistent and connects it to Buzz:

| Component | Purpose |
|---|---|
| Manager supervisor | A long-running shell script kept alive by systemd on Linux or launchd on macOS. It checks the full Claude session UUID saved in `.session-id`; if that exact session stops, it resumes the same conversation. |
| Message watcher | Receives Buzz messages independently of Claude and appends them to `inbox.log`, including while the manager session is down. |
| Manager inbox Monitor | Runs inside the Claude session, follows `inbox.log` from the saved cursor, and gives messages to the manager. It must be re-armed after a session restart. |
| Worker supervisor | Manages task workers, including idle sleep and message-triggered wake. This is separate from the manager supervisor. |
| Workspace reporter | Publishes manager and worker state for the Buzz desktop sidebar. It does not keep agents alive. |

The manager supervisor is not another agent or model. It is [`supervise-manager.sh`](../scripts/agent-workspaces/supervise-manager.sh), installed as an operating-system service alongside an identity helper. The operating system restarts the script if the script exits; the script starts or resumes only the recorded Claude manager session. It must never decide that any session in the same directory is “the manager.” See [manager supervision](s2-manager-supervisor.md) for the exact identity and restart rules.

Onboarding is incomplete until the manager supervisor is active, `.session-id` contains a full canonical UUID, that UUID identifies a background Claude session in the manager working directory, and the same UUID remains correct across supervisor checks. A healthy watcher or worker does not prove the manager is alive.

## Choose the location

| Request | Follow |
|---|---|
| Manager on your Mac | [Local macOS setup](s2-local-manager.md) |
| Manager on the shared Linux server | [Personal remote setup](s2-personal-manager-setup.md) |
| Both | Complete both guides, keeping their agent keys, channels, runtimes and saved sessions distinct |
| An existing installation stopped working | [Operations](s2-operations.md) and [server recovery](s2-server-recovery.md) |

A local manager requires its Mac to be awake and logged in. A remote manager runs on the shared server. Both can use the person's existing human Buzz identity, with separate authorized agent identities.

## Instructions for the setup agent

1. Read the selected guide before acting. Inspect existing runtimes, services and logins; preserve working installations and conversation state.
2. Confirm only missing choices: local/remote/both, the person's account, and the intended Buzz community. Discover current addresses and executable paths from the deployment or existing configuration.
3. Do the executable setup steps. For a manual step, tell the human exactly which app/terminal to use, what action to take, and what success looks like. Resume checks after they finish.
4. Provider login and secret entry belong to the person. Never ask for a private key in chat or copy another person's credentials. Root access does not grant their external account authorization.
5. Verify the manager supervisor against the exact saved UUID, the independent watcher and inbox Monitor, a manager reply, and a task worker before calling onboarding complete.
6. Report **prepared**, **running and verified**, or **waiting for a named prerequisite**, separately for each location.

## Give this to your agent

```text
Use the system2tech/buzz fork to set up my personal local and remote managers.
Start at docs/s2-personal-agents.md, then follow the linked guides. Inspect
what is already installed and preserve it. Handle the setup and checks;
guide me through each personal login or private-terminal authorization.
Report each location's verified result and any remaining blocker.
```

Use a checkout containing these files. The fork's operational branch is currently `s2`; the links are relative so they also work if the files later move to `main`. A branch name alone does not guarantee that a checkout contains the setup tooling. Read its README/AGENTS instructions and check that the linked files exist. Do not use upstream `block/buzz` as a substitute for this fork's setup.
