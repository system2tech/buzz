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


def source_provenance(source):
    """Identify the tree being installed, so a stale install is visible later.

    This installer copies a working directory, so it can install anything and
    leave no evidence but file mtimes. That is how the box ran an eight-hour-old
    `remote_manager.py` on 2026-09-09 while its source had already dropped the
    private-key prompt: the binary and the doc looked no different from current
    ones.

    **Kept out of the dependency-path record on purpose.** That dict is compared
    for equality against the previous install and refuses a mismatch, so a commit
    inside it would make every upgrade fail as though the dependencies had changed.
    Provenance is written separately, and is descriptive rather than enforced.
    """
    def git(*args):
        """Query the tree, and say why if we cannot.

        **`safe.directory` is set for these subprocesses only.** This installer
        runs as root against a checkout owned by an ordinary user, which is
        git's "dubious ownership" case — it refuses, and without this the
        provenance silently reported a clean branch as "not a git checkout",
        failing in precisely the root-runs-the-install case that is the normal
        one. Scoped with `-c` so nothing is written to any git config; these are
        read-only queries and the operator has already chosen this tree by
        running the installer out of it.

        A refusal is returned with git's own reason rather than folded into
        "not a repository", because those need different fixes and guessing
        between them is how the first version of this misreported an install.
        """
        try:
            done = subprocess.run(['git', '-c', 'safe.directory=*', '-C', str(source), *args],
                                  capture_output=True, text=True)
        except OSError as error:
            return None, f'git could not be run: {error}'
        if done.returncode != 0:
            reason = done.stderr.strip().splitlines()
            return None, reason[0] if reason else f'git exited {done.returncode}'
        return done.stdout.strip(), None

    commit, why = git('rev-parse', 'HEAD')
    if commit is None:
        return {'commit': None, 'branch': None, 'dirty': None,
                'note': f'nothing identifies what was installed: {why}'}
    branch, _ = git('rev-parse', '--abbrev-ref', 'HEAD')
    changes, _ = git('status', '--porcelain', '--', '.')
    return {'commit': commit, 'branch': branch, 'dirty': changes != ''}


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
    provenance = source_provenance(source)
    save_json(REGISTRY / 'installed-source.json', provenance, 0o644)
    print(f'Installed {launcher}; no existing services were restarted')
    if provenance['commit'] is None:
        print('WARNING installed from a non-git tree; what landed cannot be identified later')
    else:
        print(f"Installed from {provenance['branch']} at {provenance['commit'][:9]}"
              + (' with UNCOMMITTED changes' if provenance['dirty'] else ''))
        if provenance['dirty']:
            print('WARNING the tree had uncommitted changes; the commit above does not '
                  'describe what was installed')


if __name__ == '__main__':
    main()
