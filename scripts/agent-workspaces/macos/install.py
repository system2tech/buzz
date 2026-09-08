#!/usr/bin/env python3
"""Prepare a fresh personal macOS manager without loading or restarting any job."""
import argparse
import os
from pathlib import Path
import plistlib
import re
import shlex
import subprocess
import sys

SOURCE = Path(__file__).resolve().parent
sys.path.insert(0, str(SOURCE.parent))
from manager_common import atomic, load_json, mint_pair, pubkey, relay_url, save_json
from local_manager import LABELS, plist, plist_file

SHARED = ('manager_common.py', 'manager_identity.py', 'workspace_reporter.py', 'supervise-manager.sh')


def install(args, shared_source=None):
    root = args.root.expanduser().resolve()
    brain = args.brain.expanduser().resolve()
    if args.root.expanduser().is_symlink():
        raise ValueError('Refusing a symlinked runtime directory')
    if root.exists() and any(root.iterdir()) and not (root / 'local.json').exists():
        raise ValueError('Runtime is not empty; preserve the existing installation and choose a fresh directory')
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', args.channel_name):
        raise ValueError('Use a simple lowercase channel name')
    tools = {name: str(Path(getattr(args, name)).expanduser().absolute())
             for name in ('python', 'buzz', 'bridge', 'watcher', 'claude', 'adapter', 'node')}
    for name, path in tools.items():
        if not Path(path).is_file() or not os.access(path, os.X_OK):
            raise ValueError(name + ' must name an installed executable')
    subprocess.run([tools['python'], '-c', 'import coincurve'], check=True, capture_output=True)
    subprocess.run([tools['buzz'], 'agent-workspace', 'publish', '--help'], check=True, capture_output=True)
    capability_checks = [(tools['watcher'], ['buzz-watch', '--help'], ['room']),
                         (tools['claude'], ['--help', '--verbose'], ['--session-id']),
                         (tools['claude'], ['agents', '--help'], ['--json'])]
    for executable, arguments, required in capability_checks:
        result = subprocess.run([executable, *arguments], check=True, capture_output=True, text=True)
        if not all(value in result.stdout for value in required):
            raise ValueError('Installed runtime is missing required capabilities: ' + ', '.join(required))
    config_path = root / 'local.json'
    expected = {'root': str(root), 'brain': str(brain), 'name': args.name,
                'relay': relay_url(args.relay), 'tools': tools, 'channel_name': args.channel_name}
    if config_path.exists():
        config = load_json(config_path)
        if any(config.get(k) != value for k, value in expected.items()):
            raise ValueError('Existing configuration differs; installer preserves identities and dependency paths')
    else:
        config = {**expected, 'worker_idle_minutes': 180, 'worker_max_awake': 3}
    for path in (root, *[root / name for name in ('lib', 'bin', 'workers', 'tasks', 'manager', 'logs', 'plists')]):
        if path.is_symlink():
            raise ValueError('Refusing a symlinked managed directory')
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = mint_pair(root, '.buzz')
    if config.get('manager_pubkey') and config['manager_pubkey'] != pubkey(key):
        raise ValueError('Saved manager identity differs')
    config['manager_pubkey'] = pubkey(key)
    save_json(config_path, config)
    origin = shared_source or SOURCE.parent
    for name, source in [('local_manager.py', SOURCE / 'local_manager.py'),
                         *[(name, origin / name) for name in SHARED]]:
        target = root / 'lib' / name
        if target.exists() and target.read_bytes() != source.read_bytes():
            atomic(target.with_suffix(target.suffix + '.previous'), target.read_text())
        atomic(target, source.read_text())
    launcher = '#!/bin/sh\nexec ' + shlex.join([tools['python'], str(root / 'lib/local_manager.py'),
                                              '--root', str(root)]) + ' "$@"\n'
    atomic(root / 'bin/buzz-local', launcher, 0o700)
    for name, target in [('buzz', tools['buzz']), ('claude', tools['claude']), ('python3', tools['python']), ('node', tools['node'])]:
        link = root / 'bin' / name
        if link.is_symlink() and str(link.readlink()) != target:
            raise ValueError('Existing tool symlink differs')
        if not link.exists():
            link.symlink_to(target)
    env = root / 'env.sh'
    # No secrets embedded in shell source; every process reads its own retained files.
    if not env.exists():
        atomic(env, '#!/bin/sh\nexport MRFIX_ROOT=' + shlex.quote(str(root)) + '\n'
               'export MRFIX_MANAGER_CWD="$MRFIX_ROOT/manager"\n'
               'export PATH="$MRFIX_ROOT/bin:$PATH"\n')
    instructions = root / 'manager/CLAUDE.md'
    if not instructions.exists():
        atomic(instructions, f'''# {args.name}'s local Mr. Fix manager

@{brain}/CLAUDE.md

Your personal runtime is {root}. Your channel and owner identity are in local.json.
The service already supplies BUZZ_CHANNEL, BUZZ_PRIVATE_KEY and BUZZ_AUTH_TAG.
Never print private keys. Use `buzz messages send --channel "$BUZZ_CHANNEL" --content -`
with text on stdin to reply in your manager channel. Use the actual event channel
when replying to another channel. Do not send startup announcements.

Before working, start a persistent inbox Monitor with a long timeout. Read the
saved {root}/inbox.cursor (or begin at line 1) and follow {root}/inbox.log from
that line using tail -F. Save the next unread line after processing messages, so
restarts replay missed messages without repeating completed replies. The separate
watcher receives messages even while your model session is stopped.

Delegate a task with `{root}/bin/buzz-local spawn <slug> "<task>"`.
Inspect existing workers before spawning duplicates. Workers have separate task
channels and saved sessions. `buzz-local retire <slug>` stops future wakes and
preserves the channel/history. Ordinary follow-ups may interrupt a task turn;
that behavior is accepted. Use status to inspect services, not to restart them.

Sign replies: — Mr. Fix c/o {args.name}'s Mac
''')
    for component in LABELS:
        atomic(plist_file(config, component), plistlib.dumps(plist(config, component)).decode())
        subprocess.run(['plutil', '-lint', str(plist_file(config, component))],
                       check=True, capture_output=True)
    print('Prepared ' + str(root) + '; no services started. Run ' + str(root / 'bin/buzz-local') + ' status')
    return config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.home() / 'buzz-local-manager')
    parser.add_argument('--brain', type=Path, required=True)
    parser.add_argument('--name', required=True)
    parser.add_argument('--channel-name', required=True)
    parser.add_argument('--relay', required=True)
    for name in ('python', 'buzz', 'bridge', 'watcher', 'claude', 'adapter', 'node'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    if sys.platform != 'darwin' or os.geteuid() == 0:
        parser.error('Use your logged-in macOS account, without sudo')
    try:
        install(args)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.error(str(error) if isinstance(error, ValueError) else type(error).__name__)


if __name__ == '__main__':
    main()
