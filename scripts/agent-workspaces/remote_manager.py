#!/usr/bin/env python3
"""Prepare, configure and operate independent personal Buzz managers."""
import argparse
import getpass
import fcntl
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import sys

from manager_common import (COORDINATION_CHANNEL_NAME, REGISTRY, HEX, account_name,
                            as_user, atomic, auth_tag, buzz, config_for, exact_channel_id,
                            load_json, mint_pair, paths, pubkey, relay_url, save_json,
                            secret_key, services)


def require_root():
    if os.geteuid() != 0:
        raise ValueError('Run this administrative command with sudo')


def unit_text(user, home, prefix, component):
    # Home and prefix paths must be safe as systemd words, including no % expansion.
    for path in (home, prefix):
        if not re.fullmatch(r'/[A-Za-z0-9_./-]+', str(path)):
            raise ValueError('Installation and home paths must be simple absolute paths')
    root = Path(home) / 'mrfix'
    worker = component == 'worker'
    argument = 'worker %i' if worker else component
    return (f'[Unit]\nDescription=Buzz {component} for {user}\n'
            'After=network-online.target\nWants=network-online.target\n'
            f'RequiresMountsFor={home}\n'
            f'ConditionPathExists={root}/.ready\nStartLimitIntervalSec=0\n\n'
            f'[Service]\nType=simple\nUser={user}\n'
            f'WorkingDirectory={root}\nEnvironment=HOME={home}\n'
            f'ExecStart={prefix}/bin/buzz-manager run {argument}\n'
            'Restart=always\nRestartSec=15\nKillMode=control-group\n'
            'TimeoutStopSec=30\nUMask=0077\nCPUAccounting=yes\n\n'
            '[Install]\nWantedBy=multi-user.target\n')


def install_units(user, home, prefix, unit_dir=Path('/etc/systemd/system')):
    mapping = {'manager': f'buzz-manager@{user}.service',
               'watch': f'buzz-manager-watch@{user}.service',
               'supervisor': f'buzz-worker-supervisor@{user}.service',
               'reporter': f'buzz-workspace-reporter@{user}.service',
               'worker': f'buzz-worker-{user}@.service'}
    for component, name in mapping.items():
        atomic(unit_dir / name, unit_text(user, home, prefix, component), 0o644)
    return list(mapping.values())


def add_ssh_key(account, keyfile):
    if keyfile is None:
        return
    source = Path(keyfile)
    # ssh-keygen parses and verifies the public-key input; private keys are rejected.
    text = source.read_text().strip()
    if len(text.splitlines()) != 1 or not text.startswith(('ssh-ed25519 ', 'ssh-rsa ',
                                                          'ecdsa-sha2-', 'sk-ssh-', 'sk-ecdsa-')):
        raise ValueError('Supply one SSH public key, never a private key')
    subprocess.run(['ssh-keygen', '-l', '-f', str(source)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    directory = Path(account.pw_dir) / '.ssh'
    if directory.is_symlink():
        raise ValueError('Refusing a symlinked SSH directory')
    directory.mkdir(mode=0o700, exist_ok=True)
    target = directory / 'authorized_keys'
    if target.is_symlink():
        raise ValueError('Refusing a symlinked authorized_keys')
    old = target.read_text() if target.exists() else ''
    identity = text.split()[:2]
    if not any(line.split()[:2] == identity for line in old.splitlines()):
        atomic(target, old.rstrip('\n') + ('\n' if old else '') + text + '\n')
    directory.chmod(0o700)
    target.chmod(0o600)
    for path in (directory, target):
        os.chown(path, account.pw_uid, account.pw_gid)


def prepare(args):
    require_root()
    user = account_name(args.user)
    relay = relay_url(args.relay)
    install = load_json(REGISTRY / 'installation.json')
    try:
        account = pwd.getpwnam(user)
    except KeyError:
        subprocess.run(['useradd', '--create-home', '--shell', '/bin/bash', user], check=True)
        account = pwd.getpwnam(user)
    root = Path(account.pw_dir) / 'mrfix'
    if root.is_symlink():
        raise ValueError('Refusing a symlinked runtime directory')
    if (root.exists() and any(root.iterdir()) and not (root / 'manager.json').exists()
            and not (root / '.provisioning').is_file()):
        raise ValueError('Existing legacy runtime: preserve it; migration is a separate operation')
    registration = REGISTRY / f'{user}.json'
    if registration.exists():
        previous = load_json(registration)
        if previous.get('home') != account.pw_dir:
            raise ValueError('Registered home changed; inspect the retained data before continuing')
    # Team members explicitly have equal root privileges. Processes still run as users.
    sudo_rule = f'{user} ALL=(ALL:ALL) NOPASSWD: ALL\n'
    check = subprocess.run(['visudo', '-cf', '-'], input=sudo_rule, text=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if check.returncode:
        raise ValueError('Invalid sudo policy')
    atomic(Path('/etc/sudoers.d') / f'buzz-manager-{user}', sudo_rule, 0o440)
    for directory in (root, root / 'workers', root / 'bin', root / 'manager',
                      Path(account.pw_dir) / 'work'):
        if directory.is_symlink():
            raise ValueError('Refusing a symlinked managed directory')
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chown(directory, account.pw_uid, account.pw_gid)
    if not (root / 'manager.json').exists():
        atomic(root / '.provisioning', 'Personal manager preparation in progress\n')
    add_ssh_key(account, args.ssh_public_key_file)
    # Initialize user-owned runtime data as the user, never execute their files as root.
    command = [install['prefix'] + '/bin/buzz-manager', 'initialize', '--name', args.name,
               '--relay', relay]
    result = as_user(user, command)
    if result.returncode:
        raise RuntimeError('User runtime initialization failed; inspect configuration before retrying')
    (root / '.provisioning').unlink(missing_ok=True)
    units = install_units(user, account.pw_dir, install['prefix'])
    save_json(registration, {'user': user, 'name': args.name, 'home': account.pw_dir,
                              'services': services(user)}, 0o644)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    subprocess.run(['systemd-analyze', 'verify', *units], check=True,
                   stdout=subprocess.DEVNULL)
    print(f'Prepared {user}; personal credentials remain separate. Run buzz-manager status --user {user}.')


def initialize(args):
    if os.geteuid() == 0:
        raise ValueError('Initialize must run as the personal account')
    user = pwd.getpwuid(os.geteuid()).pw_name
    _, root = paths(user)
    install = load_json(REGISTRY / 'installation.json')
    path = root / 'manager.json'
    if path.exists():
        config = load_json(path)
        if config['user'] != user or config['relay'] != args.relay:
            raise ValueError('Existing manager differs; prepare never changes identity or relay')
    else:
        key = mint_pair(root, '.buzz')
        config = {'user': user, 'name': args.name, 'root': str(root), 'relay': args.relay,
                  'manager_pubkey': pubkey(key), 'tools': install['tools'],
                  'prefix': install['prefix'], 'worker_idle_minutes': 180,
                  'worker_max_awake': 3, 'worker_memory_floor_mb': 6000}
        save_json(path, config)
    for name, target in [('buzz', install['tools']['buzz']),
                         ('claude', install['tools']['claude'])]:
        link = root / 'bin' / name
        if not link.exists() and not link.is_symlink():
            link.symlink_to(target)
    # These wrappers carry no credentials. All runtime reads happen as the owner.
    for name, operation in [('spawn-worker.sh', 'spawn'), ('reap-worker.sh', 'retire')]:
        target = root / name
        if not target.exists():
            atomic(target, '#!/bin/sh\nexec ' + install['prefix'] +
                   '/bin/buzz-manager ' + operation + ' "$@"\n', 0o700)
    env = root / 'env.sh'
    if not env.exists():
        atomic(env, '#!/bin/sh\nexport PATH="$HOME/mrfix/bin:/usr/local/bin:/usr/bin:/bin"\n'
               'export MRFIX_ROOT="$HOME/mrfix"\nexport MRFIX_MANAGER_CWD="$MRFIX_ROOT/manager"\n'
               'export BUZZ_PRIVATE_KEY="$(cat "$MRFIX_ROOT/.buzz-key")"\n'
               'export BUZZ_RELAY_URL="$(python3 -c \'import json,os; print(json.load(open(os.path.expanduser("~/mrfix/manager.json")))["relay"])\')"\n'
               'export BUZZ_AUTH_TAG="$(python3 -c \'import json,os; print(json.dumps(json.load(open(os.path.expanduser("~/mrfix/manager.json"))).get("auth_tag",[])))\')"\n'
               'export BUZZ_CHANNEL="$(python3 -c \'import json,os; print(json.load(open(os.path.expanduser("~/mrfix/manager.json"))).get("channel",""))\')"\n')
    instructions = root / 'manager' / 'CLAUDE.md'
    if not instructions.exists():
        home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
        atomic(instructions, f'''# Personal remote manager for {args.name}

@{home}/mr-fix/CLAUDE.md

You are this person's Mr. Fix manager on the shared agent server. Your own runtime
is {root}; your manager channel and owner identity are in manager.json there.
Source {root}/env.sh for Buzz commands. Never use another account's identity.

Your shared coordination channel is `#agent-managers`, recorded as
`coordination_channel` in manager.json. Read every message there. Do not
acknowledge routine updates. Reply when a message asks you directly, assigns or
hands off work, reports a relevant conflict, or needs information only you have.

Before other work, start a persistent inbox Monitor using the available Monitor
tool with a long timeout and command:
`tail -F -n +1 {root}/inbox.log`
On later starts, read your saved inbox.cursor and replay from that line instead;
save the processed line after handling messages so restarting does not repeat replies.
The separate watcher keeps receiving messages while your session is stopped.

Reply with `buzz messages send --channel "$BUZZ_CHANNEL" --content -`, sending
the text on stdin. Read the actual event channel in the inbox when responding to
a task channel. Do not post startup announcements or repeat old replies.

Delegate substantive tasks through `{root}/spawn-worker.sh <slug> "<task>"`.
Each worker has its own task channel. `{root}/reap-worker.sh <slug>` retires one
while preserving its conversation. Quiet workers sleep and wake automatically.
Inspect {root}/workers before creating a duplicate task.

Shared services are operated through `buzz-manager status` and `sudo buzz-manager
start --user {user}`. Agent processes run as {user}; teammates retain root access.
Sign replies: — Mr. Fix c/o {args.name}'s remote manager
''')
    print('Runtime initialized without copying another account\'s credentials')


def configure(args):
    user = pwd.getpwuid(os.geteuid()).pw_name
    _, root = paths(user)
    with (root / ".configure.lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        configure_locked(args)


def configure_locked(args):
    if os.geteuid() == 0:
        raise ValueError('Run configure in the personal account, not as root')
    user = pwd.getpwuid(os.geteuid()).pw_name
    config = config_for(user)
    root = Path(config['root'])
    if not HEX.fullmatch(args.owner_pubkey):
        raise ValueError('Owner public key must be 64 lowercase hex characters')
    if config.get('owner_pubkey') and config['owner_pubkey'] != args.owner_pubkey:
        raise ValueError('Owner identity is already configured; do not replace it through onboarding')
    raw = (Path(args.owner_key_file).read_text() if args.owner_key_file
           else getpass.getpass('Your Buzz secret key (hidden; used once to authorize your manager): '))
    owner = secret_key(raw)
    if pubkey(owner) != args.owner_pubkey:
        raise ValueError('Secret key does not match the specified owner public key')
    tag = auth_tag(owner, config['manager_pubkey'])
    config.update(owner_pubkey=args.owner_pubkey, auth_tag=tag)
    # Authorize the fresh manager before channel creation so ownership is recorded.
    buzz(config, ['users', 'set-profile', '--name', f"{config['name']} Mr. Fix",
                  '--about', f"Personal remote manager for {config['name']}"])
    save_json(root / 'manager.json', config)
    if not config.get('channel'):
        if config.get('channel_pending'):
            raise ValueError('Channel creation outcome unknown: inspect channels and record the existing channel ID before retrying')
        config['channel_pending'] = True
        save_json(root / 'manager.json', config)
        channel = buzz(config, ['channels', 'create', '--name', f'{user}-remote',
                               '--description', f"{config['name']}'s personal manager",
                               '--type', 'stream', '--visibility', 'private'])
        config['channel'] = channel.get('channel_id') or channel.get('id')
        if not config['channel']:
            raise RuntimeError('No channel ID returned; inspect channels before retrying')
        config.pop('channel_pending', None)
        # Save immediately so an add-member failure cannot create a duplicate on retry.
        save_json(root / 'manager.json', config)
    buzz(config, ['channels', 'add-member', '--channel', config['channel'],
                  '--pubkey', args.owner_pubkey, '--role', 'owner'])
    coordination = exact_channel_id(
        buzz(config, ['channels', 'search', '--query', COORDINATION_CHANNEL_NAME, '--exact']))
    buzz(config, ['channels', 'join', '--channel', coordination])
    config['coordination_channel'] = coordination
    config["configured"] = True
    save_json(root / "manager.json", config)
    print(f"Configured {user}: manager channel {config['channel']}. Run buzz-manager status.")


def status_one(user):
    account, root = paths(user)
    if not (root / 'manager.json').exists():
        return {'user': user, 'layout': 'legacy', 'detail': 'Inspect retained service definitions'}
    config = config_for(user)
    ssh_keys = Path(account.pw_dir) / '.ssh/authorized_keys'
    checks = {'ssh_public_key': ssh_keys.is_file() and ssh_keys.stat().st_size > 0,
              'brain_checkout': (Path(account.pw_dir) / 'mr-fix/CLAUDE.md').is_file(),
              'buzz_identity': bool(config.get('configured') and config.get('owner_pubkey')
                                    and config.get('channel') and config.get('auth_tag')
                                    and (root / '.buzz-key').is_file()),
              'coordination_channel': bool(config.get('coordination_channel')),
              'claude_login': False}
    try:
        result = as_user(user, [config['tools']['claude'], 'auth', 'status', '--json'])
        checks['claude_login'] = result.returncode == 0 and json.loads(result.stdout).get('loggedIn') is True
    except (ValueError, subprocess.TimeoutExpired):
        pass
    unit_states = {}
    for unit in services(user):
        result = subprocess.run(['systemctl', 'is-active', unit], capture_output=True, text=True)
        unit_states[unit] = result.stdout.strip() or 'unknown'
    exact = False
    identity = root / '.session-id'
    if identity.exists():
        try:
            result = as_user(user, [config['tools']['claude'], 'agents', '--json'])
            sessions = json.loads(result.stdout)
            wanted = identity.read_text().strip()
            exact = any(s.get('sessionId', s.get('id')) == wanted
                        and s.get('cwd') == str(root / 'manager')
                        and s.get('kind') == 'background'
                        and s.get('status') in ('running', 'busy', 'idle', 'waiting')
                        for s in sessions) if isinstance(sessions, list) else False
        except (ValueError, subprocess.TimeoutExpired):
            pass
    return {'user': user, 'layout': 'personal', 'checks': checks,
            'missing': [name for name, ok in checks.items() if not ok],
            'services': unit_states, 'exact_manager_running': exact,
            'channel': config.get('channel'),
            'coordination_channel': config.get('coordination_channel'), 'root': str(root)}


def selected_users(args):
    if args.all:
        require_root()
        return [path.stem for path in sorted(REGISTRY.glob('*.json'))
                if path.name != 'installation.json']
    return [args.user or pwd.getpwuid(os.geteuid()).pw_name]


def operate(args):
    require_root()
    results = []
    failed = False
    for user in selected_users(args):
        try:
            state = status_one(user)
            if state['layout'] != 'personal':
                results.append(state)
                failed = True
                continue
            root = Path(state['root'])
            if args.command == 'start':
                if state['missing']:
                    results.append(state)
                    failed = True
                    continue
                # Verify the person's relay access before enabling unattended operation.
                install = load_json(REGISTRY / 'installation.json')
                result = as_user(user, [install['prefix'] + '/bin/buzz-manager', 'check-relay'])
                if result.returncode:
                    state['missing'].append('relay_access')
                    results.append(state)
                    failed = True
                    continue
                account, _ = paths(user)
                atomic(root / '.ready', 'Configured by buzz-manager start\n')
                os.chown(root / '.ready', account.pw_uid, account.pw_gid)
                subprocess.run(['systemctl', 'enable', '--now', *services(user)], check=True)
            else:
                # Stopping managers leaves task workers alone; retirement is explicit.
                subprocess.run(['systemctl', 'stop', *services(user)], check=True)
            results.append(status_one(user))
        except (ValueError, KeyError, RuntimeError, OSError, subprocess.SubprocessError) as error:
            failed = True
            # Exceptions may contain credentials in argv: report only their class.
            results.append({'user': user, 'error': type(error).__name__,
                            'detail': 'Account operation failed; inspect its configuration and service status'})
    print(json.dumps(results, indent=2))
    return 1 if failed else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    prep = sub.add_parser('prepare', help='Root: prepare one account without starting a manager')
    prep.add_argument('--user', required=True)
    prep.add_argument('--name', required=True)
    prep.add_argument('--relay', required=True)
    prep.add_argument('--ssh-public-key-file')
    init = sub.add_parser('initialize', help=argparse.SUPPRESS)
    init.add_argument('--name', required=True)
    init.add_argument('--relay', required=True)
    conf = sub.add_parser('configure', help='Personal account: authorize its Buzz identity and channel')
    conf.add_argument('--owner-pubkey', required=True)
    conf.add_argument('--owner-key-file')
    for command in ('status', 'start', 'stop'):
        item = sub.add_parser(command)
        group = item.add_mutually_exclusive_group()
        group.add_argument('--user')
        group.add_argument('--all', action='store_true')
    sub.add_parser('check-relay', help=argparse.SUPPRESS)
    run = sub.add_parser('run', help=argparse.SUPPRESS)
    run.add_argument('component', choices=['manager', 'watch', 'supervisor', 'reporter', 'worker'])
    run.add_argument('slug', nargs='?')
    spawn = sub.add_parser('spawn', help='Create one worker and task channel')
    spawn.add_argument('slug')
    spawn.add_argument('task')
    retire = sub.add_parser('retire', help='Retire a worker, retaining its files and channel')
    retire.add_argument('slug')
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            prepare(args)
        elif args.command == 'initialize':
            initialize(args)
        elif args.command == 'configure':
            configure(args)
        elif args.command == 'status':
            states = [status_one(user) for user in selected_users(args)]
            print(json.dumps(states, indent=2))
        elif args.command in ('start', 'stop'):
            return operate(args)
        else:
            if os.geteuid() == 0:
                raise ValueError('Runtime commands must run as the personal account')
            config = config_for(pwd.getpwuid(os.geteuid()).pw_name)
            if args.command == 'check-relay':
                channels = buzz(config, ['channels', 'list', '--member'])
                rows = channels.get('channels', []) if isinstance(channels, dict) else channels
                visible = {(c.get('channel_id') or c.get('id')) for c in rows}
                required = {config.get('channel'), config.get('coordination_channel')}
                if None in required or not required.issubset(visible):
                    raise ValueError('Personal or #agent-managers channel membership is missing')
            else:
                import remote_workers
                if args.command == 'run':
                    remote_workers.run_component(config, args.component, args.slug)
                elif args.command == 'spawn':
                    remote_workers.spawn(config, args.slug, args.task)
                else:
                    remote_workers.retire(config, args.slug)
        return 0
    except (ValueError, KeyError, RuntimeError, OSError, subprocess.SubprocessError) as error:
        # CalledProcessError can contain sensitive argv: never render it verbatim.
        message = str(error) if isinstance(error, (ValueError, RuntimeError)) else type(error).__name__
        print(f'Cannot complete setup: {message}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
