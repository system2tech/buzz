# Set up your personal remote manager

Any team member with root access can prepare accounts and start everyone's configured managers. Each manager runs as its own Linux user, uses that person's Claude login and project access, and keeps its own Buzz relay identity. Everyone has the same server administration privileges.

For local setup or both locations, start with [personal agents](s2-personal-agents.md). This page configures the shared Linux server.

There are two stages: **prepared** means the account and runtime exist; **running and verified** means personal login and relay membership are complete and the manager can answer and run task workers. Preparing an account alone does not complete onboarding.

## 1. Prepare the account — a root operator or their agent

Get the current agent-server address from Verda and the relay WebSocket URL from the deployed configuration. Use the installed `/opt/buzz-manager/bin/buzz-manager`; check its `--help` before proceeding. If it is missing, an operator installs it using [the shared-tooling step](#install-the-shared-tooling-once) below.

As root on the agent server, first run `buzz-manager status --all` when the tool is installed. Reuse the person's existing prepared account; do not create a second account just because its username differs from their display name.

As root on the agent server:

```bash
printf 'Personal Linux account: '
IFS= read -r AGENT_USER
printf 'Person display name: '
IFS= read -r AGENT_NAME
printf 'Relay WebSocket URL from deployment: '
IFS= read -r RELAY_URL
/opt/buzz-manager/bin/buzz-manager prepare --user "$AGENT_USER" --name "$AGENT_NAME" --relay "$RELAY_URL"
/opt/buzz-manager/bin/buzz-manager status --user "$AGENT_USER"
```

Repeat for each requested person. `prepare` creates the account, gives it passwordless sudo, and prepares a separate `~/mrfix` runtime and agent key. No Linux password is needed: SSH uses a public key. It does not copy anyone else's login or private keys, and it refuses to adopt an existing legacy runtime. Keep existing managers on their installed services until an explicit migration.

Add the person's **SSH public key** when available. Save their `.pub` contents in a file on the server and repeat `prepare` with the same account, display name and relay, adding:

```bash
printf "File containing this person's SSH public key: "
IFS= read -r PUBLIC_KEY_FILE
/opt/buzz-manager/bin/buzz-manager prepare --user "$AGENT_USER" --name "$AGENT_NAME" --relay "$RELAY_URL" --ssh-public-key-file "$PUBLIC_KEY_FILE"
```

Only a public key goes in this file. Keep the SSH private key on its owner's computer. Existing SSH keys and manager state must be preserved when preparing an account again.

## 2. Connect as yourself — each person

**Agent — on the person's computer:** inspect their existing SSH configuration and choose a working personal key. If they need a new one, run `ssh-keygen -t ed25519` interactively, choosing an unused filename and letting the human choose its passphrase. Keep the private key on this computer.

**Already have root access?** First inspect the existing SSH config. Reuse a working root SSH alias, or add `-i /absolute/path/to/root-access-key` to **both** `scp` and `ssh` below when that key is not a default identity. The root-access key can differ from the public key being installed for your personal account. You can install your own public key; you do not need Khoi or another operator. On your computer:

```bash
printf 'Your prepared Linux account: '
IFS= read -r AGENT_USER
printf 'Current agent-server IP from Verda: '
IFS= read -r AGENTS_IP
printf 'Absolute path to your SSH public-key file (.pub): '
IFS= read -r SSH_PUBLIC_KEY_FILE
ssh-keygen -lf "$SSH_PUBLIC_KEY_FILE"
scp "$SSH_PUBLIC_KEY_FILE" "root@$AGENTS_IP:/root/buzz-onboarding-$AGENT_USER.pub"
ssh "root@$AGENTS_IP"
```

If SSH asks to trust an unfamiliar server, verify its fingerprint from the Verda console (`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`) before accepting. A changed fingerprint needs the same check; do not blindly delete the known-host entry. If root access does not work, another root operator must do the public-key installation in step 1. Sudo access inside a new account cannot establish its initial SSH access.

**Agent — now in the root shell on the server:** for an already prepared account, reuse its retained name and relay:

```bash
printf 'Your prepared Linux account: '
IFS= read -r AGENT_USER
python3 - "$AGENT_USER" <<'PYSETUP'
import json, pathlib, pwd, subprocess, sys
user = sys.argv[1]
home = pathlib.Path(pwd.getpwnam(user).pw_dir)
config = json.loads((home / 'mrfix/manager.json').read_text())
subprocess.run(['/opt/buzz-manager/bin/buzz-manager', 'prepare',
    '--user', user, '--name', config['name'], '--relay', config['relay'],
    '--ssh-public-key-file', f'/root/buzz-onboarding-{user}.pub'], check=True)
PYSETUP
exit
```

If there is no prepared account, complete step 1 first. The public-key file may be removed from `/root` after successful installation. If using a non-default SSH key, add `-i /absolute/path/to/private-key` to the personal SSH command below, or configure `IdentityFile` in that person's existing SSH config without replacing it.

Connect using your account and the current address from Verda:

```bash
printf 'Your Linux account: '
IFS= read -r AGENT_USER
printf 'Current agent-server IP from Verda: '
IFS= read -r AGENTS_IP
ssh "$AGENT_USER@$AGENTS_IP"
whoami
sudo -n true
/opt/buzz-manager/bin/buzz-manager status
```

`whoami` should show your account; `sudo -n true` should succeed. If you already have root access, you or your agent can prepare your own account first. Use your personal account for the following logins, even when root is doing the rest of the setup.

## 3. Complete personal authorization

Your agent can run checks and finish configuration. You complete interactive provider sign-in and obtain an unused Buzz invite from a workspace owner or admin. Root access to the server does not provide access to your Claude subscription or GitHub account.

### Claude login — human sign-in, agent verification

On the agent server, as your own account:

```bash
"$HOME/mrfix/bin/claude" auth login
"$HOME/mrfix/bin/claude" auth status
"$HOME/mrfix/bin/claude" stop --help
```

Open the login URL printed by the command in a browser on your own computer, sign in to your Claude account, and approve the requested access. If the CLI asks for a completion code, paste it back into that same personal-account terminal. `auth status` should then report that you are logged in. `stop --help` must say that it keeps the conversation; the manager restart command depends on that behavior. An operator may open your shell with `sudo -iu ACCOUNT`, but the login must belong to you. Do not copy another user's Claude configuration or credentials.

### Project access — your own GitHub authorization

Use a separate key for **this server → GitHub**. Your laptop → server SSH key from step 2 only gets you onto the machine; it does not give the manager GitHub access. The server-side key below is generated by you or your agent during onboarding, not by `prepare`.

If you already have working personal GitHub credentials, keep them and verify the manager can use them. Otherwise, as your personal account on the agent server, create a dedicated key if it does not already exist:

```bash
mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
GITHUB_KEY="$HOME/.ssh/buzz_github_ed25519"
if [ ! -e "$GITHUB_KEY" ]; then
  ssh-keygen -t ed25519 -N '' -f "$GITHUB_KEY" -C "Buzz remote manager GitHub access"
fi
cat "$GITHUB_KEY.pub"
```

**Human step:** add that public key to your own [GitHub SSH keys](https://github.com/settings/keys), and authorize it for the team's organization if GitHub asks. Never copy a laptop's private key to the server or use another person's GitHub credentials. This dedicated server key has no passphrase so the background manager can use it; its private file stays protected in your account. If reusing an encrypted key, configure and verify persistent SSH-agent access separately; the personal-manager tool does not provide it.

For the dedicated key created above, your agent can verify access and clone the company-memory repository. If reusing another credential method, use that method instead of setting `core.sshCommand` to a key you did not create:

```bash
GITHUB_KEY="$HOME/.ssh/buzz_github_ed25519"
GIT_SSH_COMMAND="ssh -i \"$GITHUB_KEY\" -o IdentitiesOnly=yes" \
  git ls-remote git@github.com:system2tech/mr-fix.git HEAD
if [ ! -e "$HOME/mr-fix" ]; then
  GIT_SSH_COMMAND="ssh -i \"$GITHUB_KEY\" -o IdentitiesOnly=yes" \
    git clone git@github.com:system2tech/mr-fix.git "$HOME/mr-fix"
fi
git -C "$HOME/mr-fix" config core.sshCommand "ssh -i \"$GITHUB_KEY\" -o IdentitiesOnly=yes"
```

Verify GitHub's host fingerprint using its [official fingerprint list](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints) when SSH first asks to trust the host. If you already use your own working GitHub credential manager, keep it; `gh` is optional and is not installed on every agent server.

Keep an existing checkout and its changes. The manager runs from `~/mrfix/manager`, with per-person standing instructions that import the shared `~/mr-fix/CLAUDE.md`. Inspect them before starting: they must identify you, your account and your channel. The setup leaves global Claude identity instructions untouched. A shared company-memory checkout does not authorize copying another person's local identity files or tokens.

### Buzz membership and your public identity

The manager uses its own retained key and joins the relay directly through a normal invite. It publishes no human-owner attestation, so Buzz treats it as a searchable relay user. Your public key is used only to add you to the private manager and worker channels.

**Human — in your Buzz desktop app:**

1. Select the team's community and check that you are using your usual personal identity.
2. Open **Settings → Profile → Identity → Identity details**.
3. Beside **Public key**, use the copy button. It copies the full public key in hex; use it for the public-key prompt below.
4. Do not reveal or copy the private key. Manager setup does not need it.

**Agent:** use the shared [workspace onboarding invite](https://buzz.system2ai.com/invite/v2.2USjGtVn9uNdOiEPLIgJu-T0KOmQbow86hxRITozIco). It may be reused until it expires on 2026-10-09 at 08:09 UTC. If Buzz rejects it as expired or invalid, ask an owner or admin to replace the link in both manager onboarding guides. The manager configuration does not retain the invite.

The public-key label above was checked against this fork's [profile controls](../desktop/src/features/settings/ui/ProfileSettingsCard.tsx).

As the personal Linux account, in that private terminal:

```bash
printf 'Your Buzz public key (hex): '
IFS= read -r HUMAN_PUBKEY
/opt/buzz-manager/bin/buzz-manager configure --human-pubkey "$HUMAN_PUBKEY"
/opt/buzz-manager/bin/buzz-manager status
```

`configure` asks for the workspace onboarding invite, claims it with the manager's own key, creates the personal channel, adds your public identity as an owner, and joins the manager to the single active open `#agent-managers` channel. Setup stops if that channel is missing or ambiguous. No channel ID is hard-coded. Do not consider onboarding complete until `status` confirms that coordination-channel membership. For unattended setup, put only the invite link in a temporary file and add `--invite-file "$INVITE_FILE"`; remove that file after a successful claim.

The manager is a direct relay member and remains discoverable to other humans. New workers are authorized with the manager's own key; your private key is never read or saved. Existing owned-manager installations remain supported and are not changed automatically. Migrate one only after preserving its key, channel, session ID and worker registry, then verify a real manager reply and worker spawn.

## 4. Start and verify — you or any root operator

As your personal account:

```bash
sudo /opt/buzz-manager/bin/buzz-manager start --user "$(id -un)"
/opt/buzz-manager/bin/buzz-manager status
```

Or, as root, inspect and start all prepared accounts:

```bash
/opt/buzz-manager/bin/buzz-manager status --all
/opt/buzz-manager/bin/buzz-manager start --all
/opt/buzz-manager/bin/buzz-manager status --all
```

Resolve each missing prerequisite shown by `status`. An account waiting for login or relay invitation remains pending, even if other people are ready. Check legacy managers separately using [server recovery](s2-server-recovery.md); they are not automatically migrated into this tool's inventory.

For each ready account, verify:

1. Its watcher, manager supervisor, worker supervisor and reporter are active; logs show successful relay access. Status and the relay check confirm membership in both its personal channel and `#agent-managers`.
2. Its exact saved manager session remains alive across two supervisor checks. Another Claude session in the same folder does not count.
3. A message you send in its personal Buzz channel receives a reply from the correct manager and the sidebar status is fresh.
4. A small authorized task creates a worker owned by their manager. Channel members can view its live activity; viewing does not grant runtime control. The worker replies, sleeps, and wakes in the same saved conversation.

New services use `buzz-manager@USER`, `buzz-manager-watch@USER`, `buzz-worker-supervisor@USER` and `buzz-workspace-reporter@USER`. Worker services use `buzz-worker-USER@SLUG`, so two people can use the same task slug. Inspect actual unit definitions for runtime and log paths. See [manager checks](s2-manager-supervisor.md) and [worker operations](s2-worker-operations.md).

Later, any root operator can repeat `start --all` after an outage. Personal sign-in is only needed again if authorization expires or is revoked. Use [server recovery](s2-server-recovery.md) when instances or volumes also need restoring. Ordinary worker follow-ups retain the accepted [interruption behavior](always-on-agents.md#s2-decision-keep-interruption-for-now-2026-09-08).

To reload one manager's startup instructions or runtime without stopping its watcher or workers, that manager runs `buzz-manager restart` as its own account after saving current work. Its supervisor resumes the same retained conversation. Re-arm the inbox Monitor after it returns. See [manager supervision](s2-manager-supervisor.md#restart-the-manager-itself).

## Install the shared tooling once

Skip this when `/opt/buzz-manager/bin/buzz-manager --help` works. An operator installs from an updated checkout of this fork on the agent server. Discover the currently deployed Python, Buzz CLI, worker bridge, watcher, Claude and adapter executable paths from the existing service definitions and launchers. Preserve that tested dependency set; this setup does not upgrade the adapter or relay.

As root, from the Buzz checkout containing `scripts/agent-workspaces/install_remote_managers.py`:

```bash
printf 'Python executable: '
IFS= read -r PYTHON_BIN
printf 'Buzz CLI executable: '
IFS= read -r BUZZ_BIN
printf 'Worker bridge executable: '
IFS= read -r BRIDGE_BIN
printf 'Watcher executable: '
IFS= read -r WATCHER_BIN
printf 'Claude executable: '
IFS= read -r CLAUDE_BIN
printf 'Claude adapter executable: '
IFS= read -r ADAPTER_BIN
python3 scripts/agent-workspaces/install_remote_managers.py \
  --python "$PYTHON_BIN" --buzz "$BUZZ_BIN" --bridge "$BRIDGE_BIN" \
  --watcher "$WATCHER_BIN" --claude "$CLAUDE_BIN" --adapter "$ADAPTER_BIN"
/opt/buzz-manager/bin/buzz-manager --help
```

The installer also copies the operational guides to `/opt/buzz-manager/docs` (or `docs` under a custom installation prefix). These are an installed snapshot; source-code links require the fork checkout. Reinstall from the updated checkout to refresh the snapshot.

The shared command is root-owned and also available as `/usr/local/bin/buzz-manager`. It prepares new per-user installations; keep existing legacy services and saved sessions in place. Finish with the personal setup above, then verify each account. Installing the shared tool alone does not establish personal logins or create working managers.

## Give this to your agent

```text
Help finish my personal remote-manager setup using the Buzz fork's
docs/s2-personal-manager-setup.md. On the agent server, an installed copy is
at /opt/buzz-manager/docs/s2-personal-manager-setup.md. Inspect the installed
buzz-manager --help and my account's status first. Get current server addresses and relay settings
from the deployment, preserving existing files and managers. Complete the
server and runtime steps I have authorized. Use my account and identity;
never copy another person's credentials. Tell me when I need to complete
Claude/GitHub sign-in or supply Buzz authorization in a private terminal;
never ask me to paste a private key into this conversation. Start only when
prerequisites pass, verify my manager and a task worker, and report separately
what is prepared, verified, or still waiting for me.
```

## Validation — 2026-09-08

- Shared tooling was installed, and Harri, Alex, Mathias and Çağlar's accounts were prepared. Repeating preparation preserved each manager configuration and key. Passwordless sudo and private-key file permissions passed checks. Generated public keys matched their private keys, and a temporary ownership signature verified with the installed signing library.
- Real generated worker services for two accounts ran a temporary local test executable with the same task slug. They used distinct accounts, processes and home directories; stopping/restarting one left the other running. Test files and services were cleaned up and configurations restored. This exercised systemd isolation without calling a model or relay.
- `start --all` correctly left the four incomplete setups inactive and reported missing SSH public keys, brain checkouts, Buzz authorization and Claude login. Khoi's existing manager remained running without a restart.
- The focused automated suite passed 102 tests, including preparation/service contracts, exact-session checks, reporter behavior and sleep/wake message races.

Personal sign-in and authorization are still required. Full first-time onboarding through real manager replies and task-worker sleep/wake has **not** been validated for these new accounts. Use current `status` output to track completion; this dated record is not a live roster.
