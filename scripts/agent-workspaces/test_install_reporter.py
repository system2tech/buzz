"""Test installation patches against disposable macOS and Linux shell fixtures."""

import importlib.util
import os
from pathlib import Path
import shlex
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "install_reporter", Path(__file__).with_name("install_reporter.py")
)
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)
RUN = subprocess.run
MARKER = "# agent-workspace transition"

MAC_SUPERVISOR = '''#!/bin/bash
set -u
hibernate() {
  launchctl bootout "gui/$(id -u)/com.mrfix.worker.$1" 2>/dev/null
  log "slept $1 — $2"
}
wake() {
  slug=$1
  plist="$W/$slug.plist"
  if [ -f "$plist" ]; then
      launchctl bootstrap "gui/$(id -u)" "$plist" 2>/dev/null
      log "woke $slug"
  fi
}
'''

LINUX_SUPERVISOR = '''#!/bin/bash
set -u
hibernate() {
  systemctl stop "buzz-worker@$1.service"
  log "slept $1 — $2"
}
wake() {
  slug=$1
  unit="buzz-worker@$slug.service"
  if [ -n "$unit" ]; then
      systemctl start "$unit"
      log "woke $slug"
  fi
}
'''

SPAWN = '''#!/bin/bash
set -u
SLUG=$1
W="$ROOT/workers"
CH="channel-created-earlier"
date -u "+%Y-%m-%dT%H:%M:%SZ" > "$W/$SLUG.busy"
printf '%s' "$CH" > "$W/$SLUG.channel"
printf '%s slug=%s channel=%s\\n' "$(date '+%F %T')" "$SLUG" "$CH"
'''


class PatchHooksTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.guard = patch.object(installer.subprocess, "run", side_effect=self.syntax_check_only)
        self.guard.start()
        self.addCleanup(self.guard.stop)

    def syntax_check_only(self, args, *positional, **kwargs):
        # The only permitted process is the shell parser. It cannot execute scripts.
        self.assertIsInstance(args, (list, tuple))
        self.assertIn(Path(args[0]).name, ("bash", "sh"))
        self.assertIn("-n", args)
        self.assertNotIn("-c", args)
        self.assertFalse(kwargs.get("shell", False))
        return RUN(args, *positional, **kwargs)

    def fixture(self, platform="darwin", root=None):
        root = root or self.root
        root.mkdir(parents=True, exist_ok=True)
        supervisor = root / "worker-supervisor.sh"
        spawn = root / "spawn-worker.sh"
        supervisor.write_text(MAC_SUPERVISOR if platform == "darwin" else LINUX_SUPERVISOR)
        spawn.write_text(SPAWN)
        supervisor.chmod(0o751)
        spawn.chmod(0o750)
        return supervisor, spawn

    def snapshot(self, root=None):
        root = root or self.root
        return {
            path.name: (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
            for path in root.iterdir() if path.is_file()
        }

    def assert_shell_valid(self, path):
        result = subprocess.run(["/bin/bash", "-n", str(path)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_both_platforms_insert_wake_before_start_and_sleep_after_success_log(self):
        for platform, start in (
            ("darwin", 'launchctl bootstrap "gui/$(id -u)" "$plist"'),
            ("linux", 'systemctl start "$unit"'),
        ):
            with self.subTest(platform=platform):
                root = self.root / platform
                supervisor, spawn = self.fixture(platform, root)
                installer.patch_hooks(root)
                lines = supervisor.read_text().splitlines()
                starting = next(i for i, line in enumerate(lines) if "--state starting" in line)
                service = next(i for i, line in enumerate(lines) if start in line)
                slept = next(i for i, line in enumerate(lines) if 'log "slept $1' in line)
                sleeping = next(i for i, line in enumerate(lines) if "--state sleeping" in line)
                self.assertEqual(starting + 1, service)
                self.assertEqual(slept + 1, sleeping)
                self.assertIn('--worker "$slug"', lines[starting])
                self.assertIn('--worker "$1"', lines[sleeping])
                self.assertEqual(supervisor.read_text().count(MARKER), 2)
                spawn_lines = spawn.read_text().splitlines()
                channel = next(i for i, line in enumerate(spawn_lines) if '"$W/$SLUG.channel"' in line)
                self.assertIn('--worker "$SLUG" --state starting', spawn_lines[channel + 1])
                self.assertEqual(spawn.read_text().count(MARKER), 1)
                for path in (supervisor, spawn):
                    self.assert_shell_valid(path)

    def test_original_backups_are_byte_exact_and_modes_are_preserved(self):
        paths = self.fixture()
        originals = {path: (path.read_bytes(), path.stat()) for path in paths}
        installer.patch_hooks(self.root)
        for path, (content, metadata) in originals.items():
            with self.subTest(path=path.name):
                backup = path.with_name(path.name + ".before-workspace-reporter")
                self.assertEqual(backup.read_bytes(), content)
                self.assertEqual(stat.S_IMODE(backup.stat().st_mode), stat.S_IMODE(metadata.st_mode))
                self.assertEqual(backup.stat().st_mtime_ns, metadata.st_mtime_ns)
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), stat.S_IMODE(metadata.st_mode))

    def test_second_install_does_not_rewrite_files_or_backups(self):
        self.fixture()
        installer.patch_hooks(self.root)
        before = self.snapshot()
        metadata = {path.name: path.stat().st_mtime_ns for path in self.root.iterdir()}
        installer.patch_hooks(self.root)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual({path.name: path.stat().st_mtime_ns for path in self.root.iterdir()}, metadata)

    def test_existing_backups_are_never_overwritten(self):
        paths = self.fixture()
        for path in paths:
            backup = path.with_name(path.name + ".before-workspace-reporter")
            backup.write_bytes(b"previously saved exact original\n")
            backup.chmod(0o640)
        installer.patch_hooks(self.root)
        for path in paths:
            backup = path.with_name(path.name + ".before-workspace-reporter")
            self.assertEqual(backup.read_bytes(), b"previously saved exact original\n")
            self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o640)

    def test_unfamiliar_spawn_layout_leaves_both_files_and_directory_unchanged(self):
        _, spawn = self.fixture()
        spawn.write_text(SPAWN.replace('printf \'%s\' "$CH" > "$W/$SLUG.channel"', 'save_channel "$CH"'))
        before = self.snapshot()
        with self.assertRaises(ValueError):
            installer.patch_hooks(self.root)
        self.assertEqual(self.snapshot(), before)

    def test_unfamiliar_supervisor_layout_leaves_spawn_unchanged(self):
        supervisor, _ = self.fixture()
        supervisor.write_text(MAC_SUPERVISOR.replace('log "slept $1 — $2"', 'log "sleep complete"'))
        before = self.snapshot()
        with self.assertRaises(ValueError):
            installer.patch_hooks(self.root)
        self.assertEqual(self.snapshot(), before)

    def test_duplicate_wake_anchors_refuse_patch_without_changes(self):
        supervisor, _ = self.fixture("linux")
        supervisor.write_text(LINUX_SUPERVISOR.replace(
            '      systemctl start "$unit"', '      systemctl start "$unit"\n      systemctl start "$unit"'
        ))
        before = self.snapshot()
        with self.assertRaises(ValueError):
            installer.patch_hooks(self.root)
        self.assertEqual(self.snapshot(), before)

    def test_partial_prior_patch_is_not_silently_accepted(self):
        supervisor, _ = self.fixture()
        supervisor.write_text(MAC_SUPERVISOR.replace(
            '  log "slept $1 — $2"',
            '  log "slept $1 — $2"\n  true # agent-workspace transition'
        ))
        before = self.snapshot()
        with self.assertRaises(ValueError):
            installer.patch_hooks(self.root)
        self.assertEqual(self.snapshot(), before)

    def test_malformed_shell_with_recognized_hooks_leaves_every_file_unchanged(self):
        _, spawn = self.fixture()
        spawn.write_text(SPAWN + "if then\n")
        before = self.snapshot()
        with self.assertRaises((ValueError, subprocess.CalledProcessError)):
            installer.patch_hooks(self.root)
        self.assertEqual(self.snapshot(), before)

    def test_missing_second_file_does_not_patch_first_file(self):
        supervisor = self.root / "worker-supervisor.sh"
        supervisor.write_text(MAC_SUPERVISOR)
        before = self.snapshot()
        with self.assertRaises((ValueError, FileNotFoundError)):
            installer.patch_hooks(self.root)
        self.assertEqual(self.snapshot(), before)

    def test_root_with_spaces_apostrophes_and_shell_metacharacters_is_quoted(self):
        root = self.root / "manager's home $(ignored)"
        supervisor, spawn = self.fixture(root=root)
        installer.patch_hooks(root)
        for path in (supervisor, spawn):
            self.assert_shell_valid(path)
            for line in path.read_text().splitlines():
                if MARKER not in line:
                    continue
                tokens = shlex.split(line)
                self.assertEqual(tokens[0], "python3")
                self.assertEqual(tokens[1], str(root / "workspace_reporter.py"))
                self.assertEqual(tokens[tokens.index("--root") + 1], str(root))
        self.assertFalse((root / "ignored").exists())


if __name__ == "__main__":
    unittest.main()
