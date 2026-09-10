#!/usr/bin/env python3
"""Install root-owned shared tooling; preserve all existing manager services."""
import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess

from manager_common import (REGISTRY, atomic, load_json, save_json,
                            verify_installation)

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


def installed_digests(prefix):
    """Digest every file this installer placed, so an exposed account can check.

    `installed-source.json` records which commit was installed, which answers
    "is this current". It cannot answer "is what landed actually that commit" --
    the copy could be partial, or a file could have been edited in place
    afterwards, and neither shows in the commit field or in mtimes.

    Verifying that needs a clone of the repo to diff against, and the accounts
    most exposed to a bad deploy are the ones least likely to have one: on
    2026-09-09 the box running the managers could not read the deployer's
    checkout, so the strongest check was available only to the machines with no
    exposure. Recording the digests here moves that check to where the risk is
    -- anyone can hash `lib/*` and compare, with no clone and no need to trust
    another party's conclusion.

    Digests the file as it now sits on disk rather than the source content, so
    a truncated or failed write is visible rather than assumed away.

    **Covers the installed payload (`FILES`) and nothing else.** The installer
    also writes `.previous` backups beside them, and those are deliberately
    absent: their digests can never match the merged tree, so including them
    would give a verifier permanent mismatches to learn to ignore. "Written" and
    "installed" are not the same set here, and the difference reads as
    equivalent right up until someone implements against it.

    **Verify manifest-first, not directory-first.** `lib/` holds backups and
    `__pycache__` as well, so a directory glob compares nine unrelated entries
    against six and emits an error line for the directory. The manifest is the
    authority on what should be there:

        python3 - <<'EOF'
        import hashlib, json, pathlib
        m = json.load(open('/etc/buzz-managers/installed-source.json'))['files']
        lib = pathlib.Path('/opt/buzz-manager/lib')
        for name, want in m.items():
            got = hashlib.sha256((lib / name).read_bytes()).hexdigest()
            print(('MATCH' if got == want else 'DIFFERS'), name)
        EOF

    That direction finds missing or altered files. The opposite direction --
    listing `lib/` and flagging anything the manifest does not name, other than
    `.previous` backups and `__pycache__` -- finds files that should not be
    there at all, such as a hand edit or a backup mistaken for live code. The
    two catch different faults and neither is complete alone.
    """
    digests = {}
    for name in FILES:
        target = prefix / 'lib' / name
        try:
            digests[name] = hashlib.sha256(target.read_bytes()).hexdigest()
        except OSError as error:
            digests[name] = f'unreadable: {error}'
    return digests


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
    parser.add_argument('--verify', action='store_true',
                        help='check the installed files against the recorded manifest '
                             'and exit: 0 verified, 1 failed, 2 unverifiable')
    tool_names = ('python', 'buzz', 'bridge', 'watcher', 'claude', 'adapter')
    for name in tool_names:
        parser.add_argument('--' + name)
    args = parser.parse_args()
    # Verifying is read-only and needs no toolchain, so the install arguments are
    # required for installing rather than for running the command at all.
    if args.verify:
        raise SystemExit(verify_installation(Path(args.prefix)))
    absent = [name for name in tool_names if getattr(args, name) is None]
    if absent:
        parser.error('the following arguments are required: '
                     + ', '.join('--' + name for name in absent))
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
    watcher_help = subprocess.run([tools['watcher'], 'buzz-watch', '--help'],
                                  capture_output=True, text=True, check=True).stdout
    required_watcher_flags = ('room', '--state-file', '--receipt-channel', '--receipt-dms')
    if not all(flag in watcher_help for flag in required_watcher_flags):
        parser.error('Watcher is missing manager receipt-reaction support')
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
    provenance['files'] = installed_digests(prefix)
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
