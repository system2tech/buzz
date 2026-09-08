#!/usr/bin/env python3
"""Install root-owned shared tooling; preserve all existing manager services."""
import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess

from manager_common import REGISTRY, atomic, load_json, save_json

FILES = ('manager_common.py', 'remote_manager.py', 'remote_workers.py',
         'manager_identity.py', 'workspace_reporter.py', 'supervise-manager.sh')


def install_guides(source, prefix):
    """Install the operational Markdown snapshot beside the shared command."""
    docs = source.parent.parent / 'docs'
    if not (docs / 's2-personal-agents.md').is_file():
        raise ValueError('Install from the fork checkout including docs/s2-personal-agents.md')
    paths = list(docs.glob('s2-*.md'))
    paths += [docs / 'always-on-agents.md', docs / 'manager-task-sidebar.md']
    paths += list((docs / 'history/remote-agents').rglob('*.md'))
    for path in paths:
        relative = path.relative_to(docs)
        destination = prefix / 'docs' / relative
        for directory in [destination.parent, *destination.parent.parents]:
            if directory == prefix.parent:
                break
            if directory.is_symlink():
                raise ValueError('Refusing a symlinked documentation directory')
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        atomic(destination, path.read_text(), 0o644)
    changes = docs.parent / 'S2-CHANGES.md'
    if changes.is_file():
        atomic(prefix / 'S2-CHANGES.md', changes.read_text(), 0o644)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix', default='/opt/buzz-manager')
    for name in ('python', 'buzz', 'bridge', 'watcher', 'claude', 'adapter'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Install as root')
    prefix = Path(args.prefix)
    if not re.fullmatch(r'/[A-Za-z0-9_./-]+', str(prefix)):
        parser.error('Use a simple absolute installation path')
    tools = {name: str(Path(getattr(args, name))) for name in
             ('python', 'buzz', 'bridge', 'watcher', 'claude', 'adapter')}
    for name, path in tools.items():
        if not Path(path).is_absolute() or not os.access(path, os.X_OK):
            parser.error(f'{name} must name an installed executable')
    subprocess.run([tools['python'], '-c', 'import coincurve'], check=True)
    subprocess.run([tools['buzz'], 'agent-workspace', 'publish', '--help'],
                   stdout=subprocess.DEVNULL, check=True)
    source = Path(__file__).resolve().parent
    for name in FILES:
        if not (source / name).is_file():
            parser.error(f'Missing source file: {name}')
    if not (source.parent.parent / 'docs/s2-personal-agents.md').is_file():
        parser.error('Install from the fork checkout with its onboarding docs')
    desired = {'prefix': str(prefix), 'tools': tools}
    if (REGISTRY / 'installation.json').exists():
        if load_json(REGISTRY / 'installation.json') != desired:
            parser.error('Installed dependency paths differ; review an explicit migration first')
    for directory in (REGISTRY, prefix, prefix / 'lib', prefix / 'bin'):
        if directory.is_symlink():
            parser.error('Refusing a symlinked installation directory')
        directory.mkdir(parents=True, exist_ok=True, mode=0o755)
        os.chown(directory, 0, 0)
        directory.chmod(0o755)
    for name in FILES:
        target = prefix / 'lib' / name
        content = (source / name).read_text()
        if target.exists() and target.read_text() != content:
            backup = target.with_name(name + '.previous')
            shutil.copy2(target, backup)
        atomic(target, content, 0o644)
    launcher = prefix / 'bin' / 'buzz-manager'
    # The Python path is a JSON string literal, safely quoted for exec in Python.
    atomic(launcher, '#!/usr/bin/python3\nimport os\nos.execv(' + repr(tools['python']) +
           ', [' + repr(tools['python']) + ', ' + repr(str(prefix / 'lib' / 'remote_manager.py')) +
           '] + __import__("sys").argv[1:])\n', 0o755)
    alias = Path('/usr/local/bin/buzz-manager')
    if alias.exists() or alias.is_symlink():
        if not alias.is_symlink() or alias.resolve() != launcher.resolve():
            parser.error('Existing /usr/local/bin/buzz-manager belongs to another installation')
    else:
        alias.symlink_to(launcher)
    install_guides(source, prefix)
    save_json(REGISTRY / 'installation.json', desired, 0o644)
    print(f'Installed {launcher}; no existing services were restarted')


if __name__ == '__main__':
    main()
