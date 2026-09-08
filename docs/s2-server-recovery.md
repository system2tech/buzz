# Restart the S2 relay and shared agent server

Any team member with root SSH access can restore the relay and **all configured managers**, including other people's. Each manager still runs as its own Linux account with its own identity, credentials and saved conversation; its owner does not need to log in separately.

The sequence is: restore both machines and their volumes → start the relay → update DNS → start every person's agent services → verify each manager and its workers. Use current values from Verda and the retained configuration; no personal SSH aliases are needed.

This restores existing setups. If someone has no configured manager yet, use [personal remote-manager setup](s2-personal-manager-setup.md) first; starting a server does not create their account or manager.

## Validation scope

The restored relay and one existing manager have been tested live, including root-run service start/check commands, exact-session survival, a manager reply and reporter renewal. Shell examples and local links have also been checked. Personal-account preparation and isolated service operation were also tested; see [the dated onboarding validation](s2-personal-manager-setup.md#validation--2026-09-08). Full first-time onboarding through real model/channel replies, and recovery of multiple people's authenticated managers, have **not** been tested.

The personal-manager tooling supports separate per-user services and worker names. New accounts can remain **prepared, awaiting personal authorization**; do not count these as recovered running managers. Keep legacy installations in the inventory too: the new tool does not adopt them automatically. Use [personal setup](s2-personal-manager-setup.md) for missing accounts or login/ownership prerequisites, and check the installed tool's help before using it.

## [Human-only] Recreate the instances in Verda

1. Open the team's project in [Verda](https://verda.com/). Identify the relay and shared agent machine by their roles.
2. Reuse each machine's **existing OS volume and matching data volume**. Use the location compatible with those volumes. Do not substitute empty volumes. If the mapping is unclear, stop and confirm it.
3. Use the currently approved instance sizes and storage configuration. Select the team's authorized SSH keys. Keep the existing server role names and use no startup script unless the deployment explicitly requires one.
4. Copy the new public IP of each instance from the dashboard.

On your computer, enter the new addresses once:

```bash
printf 'Relay public IP from Verda: '
IFS= read -r RELAY_IP
printf 'Agent server public IP from Verda: '
IFS= read -r AGENTS_IP
```

## Start and check the relay

```bash
ssh "root@$RELAY_IP"
findmnt /mnt/data
```

Continue only if the retained relay data volume is mounted there.

```bash
systemctl start docker
cd /opt/buzz/deploy/compose
sed -n '/^BUZZ_DOMAIN=/p; /^BUZZ_IMAGE=/p' .env
docker compose -f compose.yml -f compose.caddy.yml up -d --no-recreate --pull never
docker compose -f compose.yml -f compose.caddy.yml ps
```

Relay, PostgreSQL, Redis and MinIO should be **healthy**; Caddy should be **Up**. The one-time `minio-init` job exiting with code 0 is normal.

Keep the retained `.env` and configured image. **Do not run `down -v`**: it deletes saved Docker volumes. The deployed Compose files and Docker configuration determine storage paths and service names if this installation has changed.

## [Human-only] Update DNS

1. In [Cloudflare](https://dash.cloudflare.com/), open the zone for the `BUZZ_DOMAIN` displayed above.
2. Set that hostname's A record to the relay's new public IP from Verda.

## Test the connection — human or agent

Once DNS is updated, a human or agent can run these checks, clear stale caches, and inspect logs. Use the hostname from `BUZZ_DOMAIN`; an agent can set `RELAY_DOMAIN` directly from the value already read.

Back on your computer (`exit` from SSH), enter that hostname and test:

```bash
printf 'Relay hostname (BUZZ_DOMAIN, without https://): '
IFS= read -r RELAY_DOMAIN
dig +short "$RELAY_DOMAIN"
curl -fsS "https://$RELAY_DOMAIN/health"
```

For a DNS-only record, the answer should match the current Verda relay IP. A proxied Cloudflare record returns Cloudflare addresses; check its origin target in the dashboard. Health should return `ok`.

If an old DNS answer is cached, allow it to expire, or clear caches with `dscacheutil -flushcache` on macOS and `ssh "root@$AGENTS_IP" 'resolvectl flush-caches'` on the agent server.

For relay logs:

```bash
ssh "root@$RELAY_IP" 'cd /opt/buzz/deploy/compose && docker compose -f compose.yml -f compose.caddy.yml logs --tail=50 relay caddy'
```

## Bring back every configured remote manager

First, check that the agent machine can reach the relay. Run this from your computer:

```bash
ssh "root@$AGENTS_IP" "curl -fsS https://$RELAY_DOMAIN/health"
```

Expected: `ok`. Then inspect the retained agent installation:

```bash
ssh "root@$AGENTS_IP"
findmnt /home
systemctl --failed
systemctl list-unit-files --type=service
systemctl list-units --all 'mrfix-agent*' 'buzz-*'
```

Continue only if the retained home volume is mounted. Build the expected manager list from the installed units and retained runtime configurations, including other unit names if this deployment uses them. **Do not use only the currently running processes as the inventory.** Repeat the following for every configured manager/account:

1. Inspect its unit definitions to identify its message watcher, manager supervisor, worker supervisor, reporter, runtime directory and Buzz channel. Shared services need checking only once.
2. Start required stopped services with `systemctl start UNIT`, replacing `UNIT` with the installed name. Check `systemctl status UNIT` and `journalctl -u UNIT --since '10 minutes ago' --no-pager` if one fails. Confirm required long-running services are enabled for boot. Keep the obsolete manager launch timer disabled where the persistent service replaces it.
3. Confirm the watcher has subscribed to that manager's channels and its logs show successful relay access, rather than repeated connection or authentication errors.
4. Follow [the exact-manager check](s2-manager-supervisor.md#check-the-manager): its saved UUID must match a live session and survive two scheduled supervisor checks. Another session in the same folder does not count.
5. Confirm the reporter publishes fresh status for that manager, then send a short check message in its configured Buzz channel and confirm the manager answers.

### Start accounts prepared by the personal-manager tool

As root, after checking the retained home volume and relay access:

```bash
/opt/buzz-manager/bin/buzz-manager status --all
/opt/buzz-manager/bin/buzz-manager start --all
/opt/buzz-manager/bin/buzz-manager status --all
```

Compare its account list with the team's expected managers. Resolve missing logins, project checkouts or Buzz authorization per account using [personal setup](s2-personal-manager-setup.md). A staged account is still pending until those steps and the channel/worker checks pass. The command covers accounts prepared by this tool; inspect legacy units separately below. Do not run the older fixed-name reporter installer for a new person.

### Run the startup steps for each person

Make a recovery list with one row per configured manager: person, Linux account, manager unit, watcher unit, worker-supervisor unit, reporter unit, runtime directory and channel. Read these from the retained service definitions and runtime configuration. Compare the list with the team's expected managers so an entirely missing service is noticed too.

For legacy installations or a targeted service repair, as root on the agent machine, repeat this for each row. Enter actual installed unit names, including any instance suffix. A shared service can appear in several rows; starting an already running service leaves it running.

```bash
printf 'Message-watcher unit: '
IFS= read -r WATCHER_UNIT
printf 'Worker-supervisor unit: '
IFS= read -r WORKER_SUPERVISOR_UNIT
printf 'Manager-supervisor unit: '
IFS= read -r MANAGER_UNIT
printf 'Sidebar-reporter unit: '
IFS= read -r REPORTER_UNIT
systemctl show "$MANAGER_UNIT" -p User -p ExecStart -p WorkingDirectory
systemctl start "$WATCHER_UNIT" "$WORKER_SUPERVISOR_UNIT" "$MANAGER_UNIT" "$REPORTER_UNIT"
systemctl is-active "$WATCHER_UNIT" "$WORKER_SUPERVISOR_UNIT" "$MANAGER_UNIT" "$REPORTER_UNIT"
```

Expected: all required services are active. Systemd uses the account declared by each unit; starting them as root does **not** make every agent run as root. Run session checks under the owning account, as shown in [the manager check](s2-manager-supervisor.md#check-the-manager).

Then complete checks 3–5 above for that person and record the result. If five managers are configured, completion means five verified manager sessions and five confirmed channel replies—not just one working account. Any missing configuration or expired login must be resolved for that account before marking it recovered.

## Check every retained task worker

For each manager, compare its retained worker registry with its worker services and Buzz task channels. Use [worker operations](s2-worker-operations.md) for the actual launcher and log paths.

- **Working workers:** confirm their service and relay subscription are restored and their status is fresh. Inspect progress without interrupting an active task just to test it.
- **Sleeping workers:** keep them asleep. Confirm their registry, launcher and saved session mapping survived and their supervisor is listening for wake messages. Exercise one sleeping worker per manager through its task channel; it should wake, answer, and continue its saved conversation.
- **Failed or missing workers:** investigate their service/launcher, credentials and supervisor logs. Do not delete their channel or session data as a recovery step.
- **Retired workers:** do not restart them. Flag stale registry entries separately rather than treating deleted channels as outages.

Every retained worker should be accounted for as working, intentionally sleeping, retired, or still requiring repair. Do not declare recovery complete with unexplained failures.

## Recovery is complete when

- [ ] Relay, database, Redis and storage are healthy; HTTPS works from both an operator's computer and the agent machine.
- [ ] Existing channels and message history are visible in Buzz.
- [ ] Every configured manager has its required services, exact session, fresh status and a confirmed channel reply.
- [ ] Every retained worker is accounted for; active work is restored and sleeping-worker wake/resume is verified per manager.
- [ ] No unexplained restart loops, connection failures or authentication failures remain.

“Recovered” means the relay and agents can do their jobs, not just that the instances accept SSH. Sleeping workers do not need to remain running.
