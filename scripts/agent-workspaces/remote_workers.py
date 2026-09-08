"""Per-account task registry, service launches, and sleep/wake supervision."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from manager_common import (atomic, auth_tag, buzz, http_relay, load_json,
                            pubkey, runtime_env, save_json, secret_key, task_slug,
                            worker_unit)


def service(action, unit, check=True):
    argv = ['systemctl', action, unit]
    if action in ('start', 'stop', 'restart') and os.geteuid() != 0:
        argv = ['sudo', '-n', *argv]
    return subprocess.run(argv, capture_output=True, text=True, timeout=45, check=check)


def fields(unit):
    result = subprocess.run(['systemctl', 'show', unit,
                             '--property=ActiveState,MainPID,CPUUsageNSec'],
                            capture_output=True, text=True, timeout=10, check=True)
    return dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)


def mark(config, slug, state):
    from workspace_reporter import record_intent
    record_intent(Path(config['root']), slug, {'state': state, 'at': time.time()})


def spawn(config, slug, task):
    task_slug(slug)
    root = Path(config['root'])
    workers = root / 'workers'
    if not (root / '.ready').is_file():
        raise ValueError('Complete manager setup and start it before spawning workers')
    with (workers / '.registry.lock').open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        metadata = workers / f'{slug}.json'
        import coincurve
        owner = secret_key((root / '.owner-key').read_text())
        if pubkey(owner) != config['owner_pubkey']:
            raise ValueError('Stored owner key differs from configured owner')
        if metadata.exists():
            worker = load_json(metadata)
            if worker.get('state') not in ('preparing', 'starting'):
                raise ValueError('Task slug already exists; inspect it and reuse its channel')
            key = secret_key((workers / f'{slug}.key').read_text())
            if (worker.get('task') != task or worker.get('slug') != slug
                    or worker.get('pubkey') != pubkey(key)):
                raise ValueError('Partial task identity differs; refusing to replace it')
            if worker.get('channel_pending') and not worker.get('channel'):
                raise ValueError('Channel creation outcome unknown: inspect channels and record '
                                 'the existing channel in the task JSON before retrying')
        else:
            key = coincurve.PrivateKey()
            worker = {'slug': slug, 'pubkey': pubkey(key), 'auth_tag': auth_tag(owner, pubkey(key)),
                      'channel': '', 'task': task, 'state': 'preparing'}
            atomic(workers / f'{slug}.key', key.secret.hex() + '\n')
            atomic(workers / f'{slug}.pub', worker['pubkey'] + '\n')
            save_json(metadata, worker)
        # Admit the fresh key through its owner's attestation before membership writes.
        buzz(config, ['users', 'set-profile', '--name', f'task-{slug}',
                      '--about', f"Task worker for {config['name']}"], worker)
        if not worker['channel']:
            worker['channel_pending'] = True
            save_json(metadata, worker)
            channel = buzz(config, ['channels', 'create', '--name', f'task-{slug}',
                                   '--description', task, '--type', 'stream',
                                   '--visibility', 'private'])
            worker['channel'] = channel.get('channel_id') or channel.get('id')
            if not worker['channel']:
                raise RuntimeError('Channel creation returned no ID; inspect partial task registry')
            worker.pop('channel_pending', None)
            save_json(metadata, worker)
        buzz(config, ['channels', 'add-member', '--channel', worker['channel'],
                      '--pubkey', config['owner_pubkey'], '--role', 'owner'])
        buzz(config, ['channels', 'add-member', '--channel', worker['channel'],
                      '--pubkey', worker['pubkey'], '--role', 'bot'])
        buzz(config, ['channels', 'set-add-policy', '--policy', 'anyone'], worker)
        workdir = Path(root).parent / 'work' / slug
        if workdir.exists() and not worker.get('directory_prepared'):
            raise ValueError('Task directory exists; inspect it instead of overwriting')
        workdir.mkdir(mode=0o700, exist_ok=True)
        if not (workdir / 'TASK.md').exists():
            atomic(workdir / 'TASK.md', task + '\n')
        # Share only this user's Claude login. Manager role lives in a sibling cwd,
        # not global ~/.claude instructions, so workers do not inherit that role.
        if not (workdir / 'CLAUDE.md').exists():
            atomic(workdir / 'CLAUDE.md', f'''# Task worker: {slug}

Work on TASK.md in this directory. Your owner is {config['name']}; your manager
and owner are members of the task channel {worker['channel']}.

Follow Buzz's loaded instructions for publishing replies with the Buzz CLI.
Publish useful results and blockers to your task channel; text in the activity
panel alone is not a channel reply. Preserve existing files and inspect the saved
conversation before repeating work. Ordinary follow-ups may interrupt your turn;
continue the task with the new direction.

The shared company knowledge is at {root.parent}/mr-fix. Read it when relevant.
Your personal Claude credentials remain under your own home directory.
''')
        worker['directory_prepared'] = True
        atomic(workers / f'{slug}.channel', worker['channel'] + '\n')
        atomic(workers / f'{slug}.busy', str(time.time()) + '\n')
        worker['state'] = 'starting'
        save_json(metadata, worker)
        mark(config, slug, 'starting')
        unit = worker_unit(config['user'], slug)
        logfile = workers / f'{slug}.out'
        service('start', unit)
        # Match the current PID lifetime, including retries of a partial start.
        from workspace_reporter import process_started, subscribed_since
        deadline = time.monotonic() + 90
        ready = False
        while time.monotonic() < deadline:
            state = fields(unit)
            pid = int(state.get('MainPID', 0))
            started = process_started(pid) if pid else None
            if state.get('ActiveState') == 'active' and started is not None:
                ready = subscribed_since(logfile, worker['channel'], started)
            if ready or state.get('ActiveState') == 'failed':
                break
            time.sleep(1)
        if not ready:
            raise RuntimeError('Worker did not subscribe; task is retained and has not been sent')
        if worker.get('task_send_pending'):
            raise ValueError('Task send outcome unknown: check channel history before retrying')
        worker['task_send_pending'] = True
        save_json(metadata, worker)
        buzz(config, ['messages', 'send', '--channel', worker['channel'],
                      '--content', task, '--mention', worker['pubkey']])
        worker.pop('task_send_pending', None)
        worker['state'] = 'active'
        save_json(metadata, worker)
        print(json.dumps({'slug': slug, 'channel': worker['channel'], 'unit': unit,
                          'workdir': str(workdir)}))


def retire(config, slug):
    task_slug(slug)
    workers = Path(config['root']) / 'workers'
    with (workers / '.registry.lock').open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        metadata = workers / f'{slug}.json'
        worker = load_json(metadata)
        service('stop', worker_unit(config['user'], slug))
        worker['state'] = 'retired'
        save_json(metadata, worker)
        (workers / f'{slug}.busy').unlink(missing_ok=True)
        mark(config, slug, 'sleeping')
        print('Retired worker; saved files, identity, session and channel remain')


def newest(config, channel):
    """Snapshot recent messages, retaining IDs to distinguish same-second arrivals."""
    rows = buzz(config, ['messages', 'get', '--channel', channel, '--limit', '100'])
    if isinstance(rows, dict):
        rows = rows.get('messages', rows.get('events'))
    if not isinstance(rows, list):
        raise ValueError('Expected a message list when checking worker activity')
    for event in rows:
        if (not isinstance(event, dict) or not isinstance(event.get('id'), str)
                or not event['id'] or type(event.get('created_at')) is not int
                or event['created_at'] < 0):
            raise ValueError('Message activity requires event IDs and timestamps')
    return {'created_at': max((event['created_at'] for event in rows), default=0),
            'ids': sorted({event['id'] for event in rows})}


def supervise(config):
    workers = Path(config['root']) / 'workers'
    prior_cpu = {}
    ticks = 0

    def changed(current, previous):
        if previous is None:
            # No sleep record means a stopped registered worker needs recovery.
            return True
        if isinstance(previous, (int, float)):
            # Older installations recorded wall-clock stop times. Include the whole
            # boundary second so a message arriving just after stop is not lost.
            return bool(current['ids']) and current['created_at'] >= int(previous)
        if (not isinstance(previous, dict) or type(previous.get('created_at')) is not int
                or not isinstance(previous.get('ids'), list)
                or any(not isinstance(value, str) for value in previous['ids'])):
            raise ValueError('Invalid retained worker sleep watermark')
        return (current['created_at'] > previous['created_at']
                or bool(set(current['ids']) - set(previous['ids'])))

    def wake(slug, unit):
        # Serialize with spawn/retire, then discard any stale glob/message snapshot.
        with (workers / '.registry.lock').open('a') as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            if not (workers / f'{slug}.busy').exists():
                return
            record = load_json(workers / f'{slug}.json')
            if record.get('slug') != slug or record.get('state') == 'retired':
                return
            mark(config, slug, 'starting')
            service('start', unit)
            prior_cpu.pop(slug, None)

    while True:
        ticks += 1
        candidates = []
        awake = 0
        for busy in sorted(workers.glob('*.busy')):
            slug = task_slug(busy.stem)
            try:
                unit = worker_unit(config['user'], slug)
                state = fields(unit)
                channel = (workers / f'{slug}.channel').read_text().strip()
                if state.get('ActiveState') != 'active':
                    # Retain the pre-stop message watermark across supervisor restarts.
                    slept_file = workers / f'{slug}.slept'
                    slept = load_json(slept_file) if slept_file.exists() else None
                    if changed(newest(config, channel), slept):
                        wake(slug, unit)
                    continue
                awake += 1
                if ticks % 30:
                    continue
                cpu = state.get('CPUUsageNSec')
                previous = prior_cpu.get(slug)
                prior_cpu[slug] = cpu
                if cpu in (None, '[not set]') or cpu != previous:
                    continue
                baseline = newest(config, channel)
                last = baseline['created_at']
                quiet = (time.time() - last) / 60 if last else 0
                if quiet >= 3:
                    candidates.append((quiet, slug, channel, baseline))
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                print(f'Cannot inspect worker {slug}; leaving it alone', file=sys.stderr, flush=True)
        if ticks % 30 == 0:
            mem = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
            pressure = int(mem['MemAvailable'].split()[0]) // 1024 < config['worker_memory_floor_mb']
            over = max(0, awake - config['worker_max_awake'])
            for quiet, slug, channel, baseline in sorted(candidates, reverse=True):
                if quiet < config['worker_idle_minutes'] and not over and not pressure:
                    continue
                unit = worker_unit(config['user'], slug)
                slept_file = workers / f'{slug}.slept'
                try:
                    # Messages can arrive while other candidates are inspected. Keep
                    # working if anything changed since this worker's idle check.
                    if changed(newest(config, channel), baseline):
                        continue
                    previous_sleep = slept_file.read_text() if slept_file.exists() else None
                    # Persist before stop so even a supervisor crash during stopping
                    # leaves enough information for the next process to notice input.
                    save_json(slept_file, baseline)
                    try:
                        service('stop', unit)
                    except subprocess.SubprocessError:
                        if previous_sleep is None:
                            slept_file.unlink(missing_ok=True)
                        else:
                            atomic(slept_file, previous_sleep)
                        raise
                    mark(config, slug, 'sleeping')
                    prior_cpu.pop(slug, None)
                    # Recheck after stop: never replace the baseline with stop time
                    # or the new message would be treated as already consumed.
                    if changed(newest(config, channel), baseline):
                        wake(slug, unit)
                        continue
                    over = max(0, over - 1)
                    pressure = False
                    print(f'Slept {slug} after {quiet:.0f} quiet minutes', flush=True)
                except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                    # A failed post-stop probe leaves the baseline durable; the next
                    # ordinary poll will retry rather than discard the wake message.
                    print(f'Could not complete sleep check for {slug}; will retry', file=sys.stderr, flush=True)
        time.sleep(2)


def run_component(config, component, slug=None):
    root = Path(config['root'])
    env = runtime_env(config)
    tools = config['tools']
    prefix = Path(config['prefix']) / 'lib'
    if component == 'manager':
        os.chdir(root / 'manager')
        os.execve('/bin/bash', ['bash', str(prefix / 'supervise-manager.sh')], env)
    if component == 'watch':
        argv = [tools['watcher'], 'buzz-watch', '--keyfile', str(root / '.buzz-key'),
                '--relay', http_relay(config['relay']), '--binary', tools['buzz'],
                '--subscribe', 'joined']
        with (root / 'inbox.log').open('a') as out, (root / 'watch.err').open('a') as err:
            os.dup2(out.fileno(), 1)
            os.dup2(err.fileno(), 2)
        os.execve(argv[0], argv, env)
    if component == 'reporter':
        argv = [tools['python'], str(prefix / 'workspace_reporter.py'), 'run',
                '--root', str(root), '--manager-channel', config['channel'],
                '--manager-pubkey', config['manager_pubkey'],
                '--manager-cwd', str(root / 'manager'), '--location', 'remote',
                '--buzz', tools['buzz'], '--worker-unit-template',
                f"buzz-worker-{config['user']}@{{slug}}.service"]
        os.execve(argv[0], argv, env)
    if component == 'supervisor':
        supervise(config)
        return
    task_slug(slug or '')
    worker = load_json(root / 'workers' / f'{slug}.json')
    if worker.get('state') == 'retired' or not (root / 'workers' / f'{slug}.busy').exists():
        raise ValueError('Worker is retired or not registered')
    if worker.get('slug') != slug:
        raise ValueError('Worker registry identity mismatch')
    env = runtime_env(config, worker)
    env.update({'BUZZ_ACP_AGENT_COMMAND': tools['adapter'],
                'BUZZ_ACP_AGENT_OWNER': config['owner_pubkey'],
                'BUZZ_ACP_CHANNELS': worker['channel'], 'BUZZ_ACP_SUBSCRIBE': 'all',
                'BUZZ_ACP_KINDS': '9', 'BUZZ_ACP_CONTEXT_MESSAGE_LIMIT': '100',
                'BUZZ_ACP_SESSION_MAP': str(root / 'workers' / f'{slug}.sessions.json'),
                'BUZZ_ACP_RESPOND_TO': 'anyone', 'BUZZ_ACP_AGENTS': '1',
                'BUZZ_ACP_RELAY_OBSERVER': 'true', 'BUZZ_ACP_MULTIPLE_EVENT_HANDLING': 'steer'})
    os.chdir(root.parent / 'work' / slug)
    with (root / 'workers' / f'{slug}.out').open('a') as out:
        os.dup2(out.fileno(), 1)
        os.dup2(out.fileno(), 2)
    os.execve(tools['bridge'], [tools['bridge']], env)
