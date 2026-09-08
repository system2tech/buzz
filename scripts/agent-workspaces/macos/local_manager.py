#!/usr/bin/env python3
"""Personal macOS Buzz manager, using user LaunchAgents and retained identities."""
import argparse
import fcntl
import getpass
import json
import os
from pathlib import Path
import plistlib
import re
import shlex
import subprocess
import sys
import time

from manager_common import atomic, auth_tag, load_json, mint_pair, pubkey, save_json, secret_key, task_slug

LABELS = {'manager': 'com.mrfix.supervisor', 'watch': 'com.mrfix.buzz-watch',
          'supervisor': 'com.mrfix.worker-supervisor', 'reporter': 'com.mrfix.workspace-reporter'}


def environment(config, worker=None):
    root = Path(config['root'])
    env = os.environ.copy()
    for name in list(env):
        if name.startswith('BUZZ_') or name == 'CLAUDE_CONFIG_DIR':
            env.pop(name)
    directories = [str(root / 'bin'), *[str(Path(p).parent) for p in config['tools'].values()],
                   '/usr/bin', '/bin', '/usr/sbin', '/sbin']
    env.update(HOME=str(Path.home()), PATH=':'.join(dict.fromkeys(directories)),
               MRFIX_ROOT=str(root), MRFIX_MANAGER_CWD=str(root / 'manager'),
               BUZZ_RELAY_URL=config['relay'], BUZZ_CLI=config['tools']['buzz'])
    key = root / '.buzz-key' if worker is None else root / 'workers' / (worker['slug'] + '.key')
    env.update(BUZZ_PRIVATE_KEY=key.read_text().strip(),
               BUZZ_AUTH_TAG=json.dumps((worker or config).get('auth_tag', [])),
               BUZZ_CHANNEL=(worker or config).get('channel', ''))
    return env


def buzz(config, args, worker=None):
    result = subprocess.run([config['tools']['buzz'], *args], env=environment(config, worker),
                            capture_output=True, text=True, timeout=45)
    if result.returncode:
        raise RuntimeError(f'Buzz {args[0]} {args[1]} failed; inspect access without displaying credentials')
    value = json.loads(result.stdout)
    if isinstance(value, dict) and (value.get('error') or value.get('accepted') is False):
        raise RuntimeError('Buzz rejected the request')
    return value


def label(component, slug=None):
    return 'com.mrfix.worker.' + task_slug(slug) if component == 'worker' else LABELS[component]


def job(config, component, slug=None):
    return 'gui/' + str(os.getuid()) + '/' + label(component, slug)


def plist(config, component, slug=None):
    root = Path(config['root'])
    name = label(component, slug)
    return {'Label': name, 'ProgramArguments': [str(root / 'bin/buzz-local'), 'run', component]
            + ([slug] if slug else []), 'WorkingDirectory': str(root),
            'RunAtLoad': True, 'KeepAlive': True, 'ThrottleInterval': 15,
            'EnvironmentVariables': {'HOME': str(Path.home()), 'PATH': '/usr/bin:/bin:/usr/sbin:/sbin'},
            'StandardOutPath': str(root / 'logs' / (name + '.log')),
            'StandardErrorPath': str(root / 'logs' / (name + '.log'))}


def plist_file(config, component, slug=None):
    return Path(config['root']) / 'plists' / (label(component, slug) + '.plist')


def inspect_job(config, component, slug=None):
    result = subprocess.run(['launchctl', 'print', job(config, component, slug)],
                            capture_output=True, text=True)
    if result.returncode:
        if 'Could not find service' in result.stderr:
            return {'state': 'absent', 'pid': None}
        raise RuntimeError('Cannot inspect LaunchAgent; use a logged-in macOS GUI account')
    expected = str(Path(config['root']) / 'bin/buzz-local')
    program = re.search(r'^\s*program = (.+)$', result.stdout, re.M)
    arguments = re.search(r'^\s*arguments = \{\n(.*?)^\s*\}', result.stdout, re.M | re.S)
    actual = [line.strip().strip(chr(34)) for line in arguments[1].splitlines()] if arguments else []
    wanted = [expected, 'run', component] + ([slug] if slug else [])
    if not program or program[1].strip().strip(chr(34)) != expected or actual != wanted:
        return {'state': 'conflict', 'pid': None}
    pid = re.search(r'^\s*pid = (\d+)\s*$', result.stdout, re.M)
    return {'state': 'running' if pid else 'waiting', 'pid': int(pid[1]) if pid else None}


def start_job(config, component, slug=None):
    state = inspect_job(config, component, slug)['state']
    if state == 'conflict':
        raise ValueError('Loaded job belongs to another runtime; leave it running and resolve the label conflict')
    if state != 'absent':
        return
    path = plist_file(config, component, slug)
    subprocess.run(['launchctl', 'bootstrap', 'gui/' + str(os.getuid()), str(path)], check=True,
                   capture_output=True, text=True)


def stop_job(config, component, slug=None):
    state = inspect_job(config, component, slug)['state']
    if state == 'conflict':
        raise ValueError('Loaded job belongs to another runtime; refusing to stop it')
    if state == 'absent':
        return
    subprocess.run(['launchctl', 'bootout', job(config, component, slug)], check=True,
                   capture_output=True, text=True)


def checked_channel_create(config, record, target, name, description):
    if record.get('channel'):
        return
    if record.get('channel_pending'):
        raise ValueError('Channel creation outcome unknown; inspect channels and record the existing ID before retrying')
    record['channel_pending'] = True
    save_json(target, record)
    created = buzz(config, ['channels', 'create', '--name', name, '--description', description,
                            '--type', 'stream', '--visibility', 'private'])
    record['channel'] = created.get('channel_id') or created.get('id')
    if not record['channel']:
        raise RuntimeError('No channel ID returned; inspect the retained pending record')
    record.pop('channel_pending', None)
    save_json(target, record)


def configure(config, args):
    root = Path(config['root'])
    with (root / '.configure.lock').open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        config = load_json(root / 'local.json')
        if config.get('owner_pubkey') and config['owner_pubkey'] != args.owner_pubkey:
            raise ValueError('Existing owner differs; refusing an identity replacement')
        raw = Path(args.owner_key_file).read_text() if args.owner_key_file else getpass.getpass(
            'Your Buzz private key (hidden; saved locally to authorize workers): ')
        key = secret_key(raw)
        if pubkey(key) != args.owner_pubkey:
            raise ValueError('Private key does not match the supplied public key')
        config.update(owner_pubkey=args.owner_pubkey, auth_tag=auth_tag(key, config['manager_pubkey']))
        buzz(config, ['users', 'set-profile', '--name', config['name'] + ' Mr. Fix',
                      '--about', 'Personal local manager'])
        atomic(root / '.owner-key', key.secret.hex() + '\n')
        save_json(root / 'local.json', config)
        checked_channel_create(config, config, root / 'local.json', config['channel_name'],
                               config['name'] + "'s local manager")
        buzz(config, ['channels', 'add-member', '--channel', config['channel'],
                      '--pubkey', args.owner_pubkey, '--role', 'owner'])
        config['configured'] = True
        save_json(root / 'local.json', config)
    print('Configured channel ' + config['channel'] + '; run buzz-local status before starting')


def status(config):
    root = Path(config['root'])
    auth = subprocess.run([config['tools']['claude'], 'auth', 'status', '--json'],
                          env=environment(config), capture_output=True, text=True)
    try:
        logged_in = auth.returncode == 0 and json.loads(auth.stdout).get('loggedIn') is True
    except ValueError:
        logged_in = False
    checks = {'claude_login': logged_in, 'brain': (Path(config['brain']) / 'CLAUDE.md').is_file(),
              'buzz_identity': bool(config.get('configured') and config.get('channel')
                                    and (root / '.owner-key').is_file())}
    states = {name: inspect_job(config, name) for name in LABELS}
    exact = False
    if (root / '.session-id').exists():
        wanted = (root / '.session-id').read_text().strip()
        result = subprocess.run([config['tools']['claude'], 'agents', '--json'],
                                env=environment(config), capture_output=True, text=True)
        try:
            rows = json.loads(result.stdout)
            exact = result.returncode == 0 and any(
                (s.get('sessionId') or s.get('id')) == wanted
                and s.get('cwd') == str(root / 'manager') and s.get('kind') == 'background'
                and s.get('status') in ('running', 'busy', 'idle', 'waiting') for s in rows)
        except (ValueError, TypeError, AttributeError):
            pass
    return {'root': str(root), 'channel': config.get('channel'), 'checks': checks,
            'missing': [name for name, ok in checks.items() if not ok],
            'jobs': states, 'exact_manager_running': exact}


def install_login_plists(config):
    directory = Path.home() / 'Library/LaunchAgents'
    directory.mkdir(parents=True, exist_ok=True)
    # Validate every destination before changing any existing job definition.
    for component in LABELS:
        if inspect_job(config, component)['state'] == 'conflict':
            raise ValueError('A loaded manager job belongs to another runtime; migration is separate')
        source = plist_file(config, component)
        target = directory / source.name
        if target.exists() and target.read_bytes() != source.read_bytes():
            raise ValueError('Existing ' + target.name + ' belongs to another setup; preserve it and plan migration')
    for component in LABELS:
        target = directory / plist_file(config, component).name
        if not target.exists():
            atomic(target, plist_file(config, component).read_text())


def operate(config, action):
    if action == 'start':
        state = status(config)
        if state['missing']:
            raise ValueError('Complete prerequisites: ' + ', '.join(state['missing']))
        rows = buzz(config, ['channels', 'list'])
        if isinstance(rows, dict):
            rows = rows.get('channels', [])
        if not any((r.get('channel_id') or r.get('id')) == config['channel'] for r in rows):
            raise ValueError('Manager channel is not accessible')
        install_login_plists(config)
        atomic(Path(config['root']) / '.ready', 'Configured\n')
        for component in ('watch', 'supervisor', 'manager', 'reporter'):
            start_job(config, component)
    else:
        for component in reversed(list(LABELS)):
            stop_job(config, component)
    print(json.dumps(status(config), indent=2))


def mark(config, slug, state):
    from workspace_reporter import record_intent
    record_intent(Path(config['root']), slug, {'state': state, 'at': time.time()})


def spawn(config, slug, task):
    task_slug(slug)
    root = Path(config['root'])
    registry = root / 'workers'
    if not (root / '.ready').exists():
        raise ValueError('Configure and start the manager before spawning a worker')
    with (registry / '.lock').open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        target = registry / (slug + '.json')
        owner = secret_key((root / '.owner-key').read_text())
        if pubkey(owner) != config['owner_pubkey']:
            raise ValueError('Stored owner signing identity differs')
        if target.exists():
            record = load_json(target)
            if record['state'] not in ('preparing', 'starting') or record['task'] != task:
                raise ValueError('Worker already exists; use its channel instead')
            if pubkey(secret_key((registry / (slug + '.key')).read_text())) != record['pubkey']:
                raise ValueError('Stored worker identity differs')
        else:
            import coincurve
            key = coincurve.PrivateKey()
            record = {'slug': slug, 'task': task, 'state': 'preparing', 'pubkey': pubkey(key),
                      'auth_tag': auth_tag(owner, pubkey(key))}
            atomic(registry / (slug + '.key'), key.secret.hex() + '\n')
            atomic(registry / (slug + '.pub'), record['pubkey'] + '\n')
            save_json(target, record)
        buzz(config, ['users', 'set-profile', '--name', 'task-' + slug], record)
        checked_channel_create(config, record, target, 'task-' + slug, task)
        for identity, role in [(config['owner_pubkey'], 'owner'), (record['pubkey'], 'bot')]:
            buzz(config, ['channels', 'add-member', '--channel', record['channel'],
                          '--pubkey', identity, '--role', role])
        buzz(config, ['channels', 'set-add-policy', '--policy', 'anyone'], record)
        cwd = root / 'tasks' / slug
        ownership = cwd / '.buzz-worker.json'
        expected = {'root': str(root), 'pubkey': record['pubkey']}
        if cwd.exists():
            if not ownership.exists() or load_json(ownership) != expected:
                raise ValueError('Task directory belongs to another setup; refusing to adopt it')
        else:
            cwd.mkdir()
            save_json(ownership, expected)
        if not (cwd / 'TASK.md').exists():
            atomic(cwd / 'TASK.md', task + '\n')
            atomic(cwd / 'CLAUDE.md', 'Work on TASK.md. Publish results with the Buzz CLI to your task channel. '
                   'Your owner is ' + config['name'] + '. Preserve your saved conversation on wake.\n')
        atomic(registry / (slug + '.channel'), record['channel'] + '\n')
        atomic(registry / (slug + '.busy'), 'registered\n')
        atomic(plist_file(config, 'worker', slug), plistlib.dumps(plist(config, 'worker', slug)).decode())
        record['state'] = 'starting'
        save_json(target, record)
        mark(config, slug, 'starting')
        from workspace_reporter import process_started, subscribed_since
        start_job(config, 'worker', slug)
        ready = False
        for _ in range(90):
            state = inspect_job(config, 'worker', slug)
            started = process_started(state['pid']) if state['pid'] else None
            if started and subscribed_since(registry / (slug + '.out'), record['channel'], started):
                ready = True
                break
            time.sleep(1)
        if not ready:
            raise RuntimeError('Worker has not subscribed; retry the same slug/task after fixing its launch')
        if record.get('task_send_pending'):
            raise ValueError('Task send outcome unknown; inspect channel before retrying to avoid duplicates')
        record['task_send_pending'] = True
        save_json(target, record)
        buzz(config, ['messages', 'send', '--channel', record['channel'], '--content', task,
                      '--mention', record['pubkey']])
        record.pop('task_send_pending', None)
        record['state'] = 'active'
        save_json(target, record)
        print(json.dumps({'channel': record['channel'], 'slug': slug}))


def retire(config, slug):
    task_slug(slug)
    root = Path(config['root'])
    with (root / 'workers/.lock').open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        record = load_json(root / 'workers' / (slug + '.json'))
        stop_job(config, 'worker', slug)
        record['state'] = 'retired'
        save_json(root / 'workers' / (slug + '.json'), record)
        (root / 'workers' / (slug + '.busy')).unlink(missing_ok=True)
        mark(config, slug, 'sleeping')
    print('Retired; channel, key, task files and saved session retained')


def messages(config, channel):
    rows = buzz(config, ['messages', 'get', '--channel', channel, '--limit', '100'])
    if isinstance(rows, dict):
        rows = rows.get('messages', rows.get('events'))
    if not isinstance(rows, list) or any(not isinstance(e, dict) or not isinstance(e.get('id'), str)
                                      or type(e.get('created_at')) is not int for e in rows):
        raise ValueError('Cannot determine channel message watermark')
    return {'created_at': max((e['created_at'] for e in rows), default=0),
            'ids': sorted({e['id'] for e in rows})}


def changed(current, previous):
    return previous is None or current['created_at'] > previous['created_at'] or bool(
        set(current['ids']) - set(previous['ids']))


def tree_cpu(pid):
    """Advisory cumulative CPU seconds; uncertainty prevents sleeping a worker."""
    result = subprocess.run(['ps', '-axo', 'pid=,ppid=,time='], capture_output=True, text=True, check=True)
    rows = {}
    for line in result.stdout.splitlines():
        values = line.split()
        if len(values) == 3:
            process, parent = map(int, values[:2])
            rows[process] = (parent, values[2])
    if pid not in rows:
        return None
    descendants = {pid}
    while True:
        added = {p for p, (parent, _) in rows.items() if parent in descendants} - descendants
        if not added:
            break
        descendants.update(added)
    total = 0.0
    for process in descendants:
        raw = rows[process][1]
        days, clock = raw.split('-', 1) if '-' in raw else ('0', raw)
        parts = clock.split(':')
        if (not days.isdigit() or not 2 <= len(parts) <= 3
                or not all(re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', part) for part in parts)):
            return None
        total += int(days) * 86400 + sum(float(part) * (60 ** i)
                                       for i, part in enumerate(reversed(parts)))
    return total


def guarded_wake(config, slug):
    """Serialize with spawn/retire and recheck the registry before launching."""
    root = Path(config['root'])
    with (root / 'workers/.lock').open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if not (root / 'workers' / (slug + '.busy')).exists():
            return False
        record = load_json(root / 'workers' / (slug + '.json'))
        if record.get('slug') != slug or record.get('state') == 'retired':
            return False
        mark(config, slug, 'starting')
        start_job(config, 'worker', slug)
        return True


def supervise(config):
    root = Path(config['root'])
    prior_cpu = {}
    ticks = 0
    while True:
        ticks += 1
        candidates = []
        awake = 0
        for busy in sorted((root / 'workers').glob('*.busy')):
            slug = task_slug(busy.stem)
            try:
                record = load_json(busy.with_suffix('.json'))
                state = inspect_job(config, 'worker', slug)
                slept = busy.with_suffix('.slept')
                if state['state'] == 'absent':
                    baseline = load_json(slept) if slept.exists() else None
                    if changed(messages(config, record['channel']), baseline):
                        guarded_wake(config, slug)
                    continue
                awake += 1
                if ticks % 30 or not state['pid']:
                    continue
                cpu = tree_cpu(state['pid'])
                before = prior_cpu.get(slug)
                prior_cpu[slug] = cpu
                if cpu is None or cpu != before:
                    continue
                baseline = messages(config, record['channel'])
                quiet = (time.time() - baseline['created_at']) / 60 if baseline['ids'] else 0
                if quiet >= 3:
                    candidates.append((quiet, slug, record['channel'], baseline))
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                print('Cannot inspect worker ' + slug, file=sys.stderr, flush=True)
        over = max(0, awake - config['worker_max_awake'])
        for quiet, slug, channel, baseline in sorted(candidates, reverse=True):
            if quiet < config['worker_idle_minutes'] and not over:
                continue
            slept = root / 'workers' / (slug + '.slept')
            try:
                if changed(messages(config, channel), baseline):
                    continue
                previous = slept.read_text() if slept.exists() else None
                save_json(slept, baseline)
                try:
                    stop_job(config, 'worker', slug)
                except subprocess.SubprocessError:
                    if previous is None:
                        slept.unlink(missing_ok=True)
                    else:
                        atomic(slept, previous)
                    raise
                mark(config, slug, 'sleeping')
                prior_cpu.pop(slug, None)
                if changed(messages(config, channel), baseline):
                    if (root / 'workers' / (slug + '.busy')).exists():
                        guarded_wake(config, slug)
                else:
                    over = max(0, over - 1)
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                print('Cannot complete sleep check for ' + slug, file=sys.stderr, flush=True)
        time.sleep(2)


def run(config, component, slug=None):
    root = Path(config['root'])
    tools = config['tools']
    env = environment(config)
    if component == 'manager':
        os.chdir(root / 'manager')
        os.execve('/bin/bash', ['bash', str(root / 'lib/supervise-manager.sh')], env)
    if component == 'watch':
        from manager_common import http_relay
        argv = [tools['watcher'], 'buzz-watch', '--keyfile', str(root / '.buzz-key'),
                '--relay', http_relay(config['relay']), '--binary', tools['buzz'], '--subscribe', 'room']
        with (root / 'inbox.log').open('a') as out, (root / 'watch.err').open('a') as err:
            os.dup2(out.fileno(), 1)
            os.dup2(err.fileno(), 2)
        os.execve(argv[0], argv, env)
    if component == 'reporter':
        argv = [tools['python'], str(root / 'lib/workspace_reporter.py'), 'run', '--root', str(root),
                '--manager-cwd', str(root / 'manager'), '--manager-pubkey', config['manager_pubkey'],
                '--manager-channel', config['channel'], '--location', 'local', '--buzz', tools['buzz']]
        os.execve(argv[0], argv, env)
    if component == 'supervisor':
        return supervise(config)
    task_slug(slug or '')
    record = load_json(root / 'workers' / (slug + '.json'))
    if record['state'] == 'retired' or not (root / 'workers' / (slug + '.busy')).exists():
        raise ValueError('Worker is retired')
    env = environment(config, record)
    env.update(BUZZ_ACP_AGENT_COMMAND=tools['adapter'], BUZZ_ACP_AGENT_OWNER=config['owner_pubkey'],
               BUZZ_ACP_CHANNELS=record['channel'], BUZZ_ACP_SUBSCRIBE='all', BUZZ_ACP_KINDS='9',
               BUZZ_ACP_CONTEXT_MESSAGE_LIMIT='100', BUZZ_ACP_SESSION_MAP=str(root / 'workers' / (slug + '.sessions.json')),
               BUZZ_ACP_RESPOND_TO='anyone', BUZZ_ACP_AGENTS='1', BUZZ_ACP_RELAY_OBSERVER='true',
               BUZZ_ACP_MULTIPLE_EVENT_HANDLING='steer')
    os.chdir(root / 'tasks' / slug)
    with (root / 'workers' / (slug + '.out')).open('a') as out:
        os.dup2(out.fileno(), 1)
        os.dup2(out.fileno(), 2)
    os.execve(tools['bridge'], [tools['bridge']], env)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.home() / 'buzz-local-manager')
    sub = parser.add_subparsers(dest='command', required=True)
    conf = sub.add_parser('configure')
    conf.add_argument('--owner-pubkey', required=True)
    conf.add_argument('--owner-key-file')
    for name in ('status', 'start', 'stop'):
        sub.add_parser(name)
    item = sub.add_parser('spawn')
    item.add_argument('slug')
    item.add_argument('task')
    item = sub.add_parser('retire')
    item.add_argument('slug')
    item = sub.add_parser('run')
    item.add_argument('component', choices=[*LABELS, 'worker'])
    item.add_argument('slug', nargs='?')
    args = parser.parse_args()
    try:
        if os.geteuid() == 0:
            raise ValueError('Run as your logged-in macOS user, without sudo')
        config = load_json(args.root.expanduser().resolve() / 'local.json')
        if args.command == 'configure':
            configure(config, args)
        elif args.command == 'status':
            print(json.dumps(status(config), indent=2))
        elif args.command in ('start', 'stop'):
            operate(config, args.command)
        elif args.command == 'spawn':
            spawn(config, args.slug, args.task)
        elif args.command == 'retire':
            retire(config, args.slug)
        else:
            run(config, args.component, args.slug)
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError) as error:
        print(str(error) if isinstance(error, (ValueError, RuntimeError)) else type(error).__name__, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
