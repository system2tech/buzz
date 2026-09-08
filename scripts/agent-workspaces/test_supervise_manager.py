"""Exercise the actual supervisor loop with a fake Claude CLI."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).with_name("supervise-manager.sh")
MANAGER = "cfc9c514-7134-4748-84d5-4596b53f0b44"
OTHER = "11111111-1111-4111-8111-111111111111"


def session(identity, status="busy"):
    return dict(id=identity[:8], sessionId=identity, kind="background", cwd="MANAGER", status=status)


class SupervisorTests(unittest.TestCase):
    def check_case(self, sessions, expected_launches=0, query_exit=0,
                   identity=MANAGER, expected_flag="--resume", launch_exit=0):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "bin").mkdir()
            cwd = root / "repo"
            cwd.mkdir()
            if identity is not None:
                (root / ".session-id").write_text(identity + "\n")
                (root / ".session-launched").write_text(identity + "\n")
            for item in sessions if isinstance(sessions, list) else []:
                if isinstance(item, dict) and item.get("cwd") == "MANAGER":
                    item["cwd"] = str(cwd)
            (root / "sessions").write_text(
                json.dumps(sessions) if not isinstance(sessions, str) else sessions)
            (root / "env.sh").write_text(
                "export PATH=" + shlex.quote(str(root / "bin")) + ":$PATH\n")
            commands = {
                "claude": '''#!/usr/bin/env python3
import json, os, pathlib, sys
root = pathlib.Path(os.environ["MRFIX_ROOT"])
if sys.argv[1] == "agents":
    print((root / "sessions").read_text())
    sys.exit(int(os.environ["QUERY_EXIT"]))
with (root / "launches").open("a") as stream:
    stream.write(json.dumps(sys.argv[1:]) + "\\n")
if int(os.environ["LAUNCH_EXIT"]):
    sys.exit(int(os.environ["LAUNCH_EXIT"]))
flag = "--resume" if "--resume" in sys.argv else "--session-id"
identity = sys.argv[sys.argv.index(flag) + 1]
assert (root / ".session-id").read_text().strip() == identity
sessions = json.loads((root / "sessions").read_text())
sessions = [s for s in sessions if s.get("sessionId") != identity]
sessions.append(dict(id=identity[:8], sessionId=identity, kind="background",
                     cwd=os.environ["MRFIX_MANAGER_CWD"], status="busy"))
(root / "sessions").write_text(json.dumps(sessions))
''',
                "sleep": '''#!/bin/bash
if [ -e "$MRFIX_ROOT/checked" ]; then kill -TERM "$PPID"; fi
touch "$MRFIX_ROOT/checked"
''',
            }
            for name, body in commands.items():
                path = root / "bin" / name
                path.write_text(body)
                path.chmod(0o755)
            env = dict(os.environ, MRFIX_ROOT=str(root), MRFIX_MANAGER_CWD=str(cwd),
                       QUERY_EXIT=str(query_exit), LAUNCH_EXIT=str(launch_exit))
            result = subprocess.run(["bash", str(SCRIPT)], env=env, timeout=10,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, -15, result.stderr)
            path = root / "launches"
            launches = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
            self.assertEqual(len(launches), expected_launches)
            saved = (root / ".session-id").read_text().strip() if (root / ".session-id").exists() else None
            for launch in launches:
                self.assertIn(expected_flag, launch)
                if expected_flag == "--resume":
                    self.assertEqual(launch, ["--bg", "--resume", saved])
                self.assertEqual(launch[launch.index(expected_flag) + 1], saved)
            if identity is not None:
                self.assertEqual(saved, identity)

    def test_exact_manager_kept_with_same_folder_sibling(self):
        self.check_case([session(MANAGER), session(OTHER)])

    def test_same_folder_sibling_cannot_mask_missing_manager(self):
        self.check_case([session(OTHER)], expected_launches=1)

    def test_same_folder_sibling_cannot_mask_stopped_manager(self):
        self.check_case([session(MANAGER, "stopped"), session(OTHER)], expected_launches=1)

    def test_new_install_records_own_identity_without_adopting_sibling(self):
        self.check_case([session(OTHER)], identity=None, expected_launches=1, expected_flag="--session-id")

    def test_failed_first_launch_retries_same_identity(self):
        self.check_case([], identity=None, expected_launches=2, expected_flag="--session-id", launch_exit=1)

    def test_offline_cached_manager_is_resumed(self):
        item = session(MANAGER)
        del item["status"]
        self.check_case([item, session(OTHER)], expected_launches=1)

    def test_missing_status_with_live_pid_does_not_create_duplicate(self):
        item = session(MANAGER)
        del item["status"]
        item["pid"] = 123
        self.check_case([item])

    def test_failed_query_does_not_create_duplicate(self):
        self.check_case([], query_exit=1)

    def test_invalid_json_does_not_create_duplicate(self):
        self.check_case("invalid")

    def test_invalid_schema_does_not_create_duplicate(self):
        self.check_case({})

    def test_invalid_identity_does_not_adopt_sibling(self):
        self.check_case([session(OTHER)], identity="corrupt")

    def test_unrecognized_manager_status_does_not_create_duplicate(self):
        self.check_case([session(MANAGER, "unrecognized")])

    def test_recorded_manager_in_wrong_folder_does_not_create_duplicate(self):
        item = session(MANAGER)
        item["cwd"] = "/other"
        self.check_case([item])


if __name__ == "__main__":
    unittest.main()
