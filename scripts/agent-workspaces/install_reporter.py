#!/usr/bin/env python3
"""Install a reporter beside an existing Mr. Fix manager, without restarting agents.

Run as the manager account on macOS; as root with --user on Linux. Installs a
separate reporter service and local-only transition hooks in the supervisor.
Use --prepare-only to inspect generated files without starting the service.
"""

import argparse
import os
from pathlib import Path
import plistlib
import pwd
import re
import shlex
import shutil
import subprocess
import sys


def patch_hooks(root):
    reporter = shlex.quote(str(root / "workspace_reporter.py"))
    common = f"python3 {reporter} mark --root {shlex.quote(str(root))}"
    changes = []
    supervisor = root / "worker-supervisor.sh"
    spawn = root / "spawn-worker.sh"
    for path in (supervisor, spawn):
        source = path.read_text()
        expected = 2 if path == supervisor else 1
        if "# agent-workspace transition" in source:
            required = [f'{common} --worker "$slug" --state starting',
                        f'{common} --worker "$1" --state sleeping'] if path == supervisor else [
                            f'{common} --worker "$SLUG" --state starting']
            if source.count("# agent-workspace transition") != expected or not all(
                    source.count(hook) == 1 for hook in required):
                raise ValueError(f"{path.name}: partial or unfamiliar workspace hooks; no files changed")
            check = subprocess.run(["bash", "-n"], input=source, capture_output=True, text=True)
            if check.returncode:
                raise ValueError(f"{path.name}: invalid shell syntax; no files changed")
            continue
        lines = source.splitlines(keepends=True)
        output = []
        hits = 0
        for line in lines:
            # Record desired wake before starting; reporter independently proves readiness.
            if path == supervisor and ('systemctl start "$unit"' in line or
                                       'launchctl bootstrap "gui/$(id -u)" "$plist"' in line):
                output.append(f'      {common} --worker "$slug" --state starting >/dev/null 2>&1 || true # agent-workspace transition\n')
                hits += 1
            output.append(line)
            if path == supervisor and 'log "slept $1' in line:
                output.append(f'  {common} --worker "$1" --state sleeping >/dev/null 2>&1 || true # agent-workspace transition\n')
                hits += 1
            if path == spawn and 'printf \'%s\' "$CH" > "$W/$SLUG.channel"' in line:
                output.append(f'{common} --worker "$SLUG" --state starting >/dev/null 2>&1 || true # agent-workspace transition\n')
                hits += 1
        if hits != expected:
            raise ValueError(f"{path.name}: expected {expected} known hooks, found {hits}; no files changed")
        content = "".join(output)
        check = subprocess.run(["bash", "-n"], input=content, capture_output=True, text=True)
        if check.returncode:
            raise ValueError(f"{path.name}: invalid shell syntax; no files changed")
        changes.append((path, content))
    # Validate every patch before changing any file. Keep the exact previous versions.
    for path, content in changes:
        backup = path.with_name(path.name + ".before-workspace-reporter")
        if not backup.exists():
            shutil.copy2(path, backup)
        stat = path.stat()
        temporary = path.with_name(path.name + ".workspace-new")
        temporary.write_text(content)
        temporary.chmod(stat.st_mode)
        if os.getuid() == 0:
            os.chown(temporary, stat.st_uid, stat.st_gid)
        temporary.replace(path)


def linux_service_names(user, service_name=None, supervisor_unit=None):
    """Default to separate per-user services; legacy names require explicit flags."""
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,30}", user):
        raise ValueError("invalid Linux account")
    service_name = service_name or f"buzz-workspace-reporter@{user}.service"
    supervisor_unit = supervisor_unit or f"buzz-worker-supervisor@{user}.service"
    for value in (service_name, supervisor_unit):
        if not re.fullmatch(r"[a-zA-Z0-9_@.-]+\.service", value) or value.startswith("-"):
            raise ValueError("invalid service name")
    return service_name, supervisor_unit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--manager-pubkey", required=True)
    parser.add_argument("--manager-cwd", required=True)
    parser.add_argument("--location", choices=["local", "remote"], required=True)
    parser.add_argument("--buzz", required=True, help="Absolute path to the CLI built with agent-workspace")
    parser.add_argument("--user", default=pwd.getpwuid(os.getuid()).pw_name)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--service-name", help="Explicit legacy reporter unit override on Linux")
    parser.add_argument("--supervisor-unit", help="Explicit legacy worker-supervisor unit on Linux")
    parser.add_argument("--worker-unit-template", default="buzz-worker@{slug}.service")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    account = pwd.getpwnam(args.user)
    if not (root / "env.sh").is_file():
        parser.error("manager env.sh is missing")
    # Check protocol support before touching an existing installation.
    subprocess.run([args.buzz, "agent-workspace", "publish", "--help"],
                   check=True, stdout=subprocess.DEVNULL)
    source = Path(__file__).with_name("workspace_reporter.py")
    target = root / "workspace_reporter.py"
    if source.resolve() != target.resolve():
        shutil.copy2(source, target)
    target.chmod(0o755)
    patch_hooks(root)
    command = ["python3", str(target), "run", "--root", str(root),
               "--manager-pubkey", args.manager_pubkey, "--manager-cwd", args.manager_cwd,
               "--location", args.location, "--buzz", str(Path(args.buzz).resolve()),
               "--worker-unit-template", args.worker_unit_template]
    launcher = root / "workspace-reporter.sh"
    launcher.write_text("#!/bin/bash\nset -euo pipefail\n. " + shlex.quote(str(root / "env.sh"))
                        + "\nexec " + shlex.join(command) + "\n")
    launcher.chmod(0o755)
    if sys.platform == "darwin":
        label = "com.mrfix.workspace-reporter"
        path = Path(account.pw_dir) / "Library/LaunchAgents" / f"{label}.plist"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(plistlib.dumps({"Label": label,
            "ProgramArguments": [str(launcher)], "RunAtLoad": True, "KeepAlive": True,
            "WorkingDirectory": str(root), "ThrottleInterval": 10,
            "EnvironmentVariables": {"PATH": f"{root}/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"},
            "StandardOutPath": str(root / "workspace-reporter.log"),
            "StandardErrorPath": str(root / "workspace-reporter.log")}))
        start = ["launchctl", "bootstrap", f"gui/{account.pw_uid}", str(path)]
        stop = ["launchctl", "bootout", f"gui/{account.pw_uid}/{label}"]
        reload_supervisor = ["launchctl", "kickstart", "-k",
                             f"gui/{account.pw_uid}/com.mrfix.worker-supervisor"]
    else:
        service_name, supervisor_unit = linux_service_names(
            args.user, args.service_name, args.supervisor_unit)
        path = Path("/etc/systemd/system") / service_name
        path.write_text("[Unit]\nDescription=Buzz manager/task relationships and lifecycle leases\n"
                        "After=network-online.target\nWants=network-online.target\n\n"
                        f"[Service]\nUser={args.user}\nWorkingDirectory={root}\nExecStart={launcher}\n"
                        "Restart=always\nRestartSec=10\n\n[Install]\nWantedBy=multi-user.target\n")
        start = ["systemctl", "enable", "--now", service_name]
        stop = ["systemctl", "stop", service_name]
        reload_supervisor = ["systemctl", "restart", supervisor_unit]
    if os.getuid() == 0:
        for file in (target, launcher):
            os.chown(file, account.pw_uid, account.pw_gid)
    print(f"Prepared {path}; supervisor/spawn backups are beside their originals.")
    print("Only the worker supervisor will reload its hooks; workers keep running.")
    if not args.prepare_only:
        subprocess.run(stop, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        if sys.platform != "darwin":
            subprocess.run(["systemctl", "daemon-reload"], check=True)
        # Hooks must be active before old transition logs are adopted by the reporter.
        subprocess.run(reload_supervisor, check=True)
        subprocess.run(start, check=True)


if __name__ == "__main__":
    main()
