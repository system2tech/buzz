#!/usr/bin/env python3
"""Publish manager/task relationships and leased process status to Buzz.

Run beside the existing supervisor, with the manager's env.sh already sourced.
This reads public worker coordinates and process state, never worker private keys.
Network calls run here, outside the supervisor's latency-sensitive wake loop.
"""

import argparse
import datetime as dt
import fcntl
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time


SLUG = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}\Z")
ANSI = re.compile(r"\x1b\[[0-9;]*m")
PUBLISH_RETRY_SECONDS = 60


def read_text(path):
    try:
        return path.read_text().strip()
    except (OSError, UnicodeError):
        return ""


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value) + "\n")
    tmp.replace(path)


def valid_intent(value):
    return (isinstance(value, dict) and value.get("state") in ("sleeping", "starting")
            and type(value.get("at")) in (int, float) and math.isfinite(value["at"]))


def record_intent(root, slug, intent):
    """Keep the newest transition, including concurrent supervisor observations.

    The Linux supervisor runs as root while the reporter runs as the manager.
    Give the lock to the workers-directory owner so both can use the same lock.
    A lock lives at a stable path; atomically replaced intent files do not.
    """
    workers = root / "workers"
    workers.mkdir(parents=True, exist_ok=True)
    lock_path = workers / f"{slug}.workspace-intent.lock"
    with lock_path.open("a") as lock:
        if os.getuid() == 0:
            owner = workers.stat()
            os.fchown(lock.fileno(), owner.st_uid, owner.st_gid)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        path = workers / f"{slug}.workspace-intent.json"
        existing = read_json(path)
        if valid_intent(existing) and existing["at"] >= intent["at"]:
            return existing
        atomic_json(path, intent)
        return intent


def command(args, timeout=10):
    """Capture output; callers log categories, never credential-bearing stderr."""
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None


def publish_failure_category(result):
    """Classify CLI failures without reflecting credentials or server text."""
    if result is None:
        return "unavailable"
    try:
        error = json.loads(result.stderr)
    except (ValueError, TypeError):
        return "rejected"
    if not isinstance(error, dict):
        return "rejected"
    message = str(error.get("message", "")).lower()
    if "channel not found" in message or "channel has been deleted" in message:
        return "channel-unavailable"
    if error.get("error") == "auth_error" or any(
            term in message for term in ("permission denied", "not a channel member")):
        return "permission-denied"
    return "rejected"


def process_state(slug, platform, run=command):
    """Return (service state, PID); a registered launchd job need not be running."""
    if platform == "darwin":
        r = run(["launchctl", "print", f"gui/{os.getuid()}/com.mrfix.worker.{slug}"])
        if r is None:
            return "unknown", 0
        if r.returncode:
            # launchctl's absent-service result differs from permission/IPC failure.
            if "Could not find service" in r.stderr:
                return "stopped", 0
            return "unknown", 0
        state = re.search(r"^\s*state = (.+)$", r.stdout, re.M)
        pid = re.search(r"^\s*pid = (\d+)$", r.stdout, re.M)
        if state and state[1] == "running" and pid:
            return "running", int(pid[1])
        return "starting", 0
    r = run(["systemctl", "show", f"buzz-worker@{slug}.service",
             "--property=ActiveState,MainPID,LoadState"])
    if r is None or r.returncode:
        return "unknown", 0
    fields = dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)
    if fields.get("LoadState") == "not-found":
        return "stopped", 0
    active = fields.get("ActiveState")
    try:
        pid = int(fields.get("MainPID", "0"))
    except ValueError:
        return "unknown", 0
    if active == "active" and pid:
        return "running", pid
    if active in ("activating", "reloading", "deactivating"):
        return "starting", pid
    if active == "failed":
        return "failed", 0
    return ("stopped", 0) if active == "inactive" else ("unknown", 0)


def process_started(pid, run=command):
    r = run(["ps", "-o", "lstart=", "-p", str(pid)])
    if r is None or r.returncode:
        return None
    try:
        # lstart is in this machine's local time. Force a predictable locale.
        return dt.datetime.strptime(r.stdout.strip(), "%a %b %d %H:%M:%S %Y").timestamp()
    except ValueError:
        return None


def subscribed_since(logfile, channel, started):
    """Require a readiness line from this process lifetime, not the prior wake."""
    try:
        with logfile.open(errors="replace") as stream:
            for line in stream:
                if f"subscribed to channel {channel}" not in line:
                    continue
                match = re.search(r"\d{4}-\d\d-\d\dT[\d:.]+(?:Z|[+-]\d\d:\d\d)", ANSI.sub("", line))
                if not match:
                    continue
                try:
                    stamp = dt.datetime.fromisoformat(match[0].replace("Z", "+00:00")).timestamp()
                    if stamp >= started:
                        return True
                except ValueError:
                    continue
    except OSError:
        pass
    return False


def last_intent(root, slug):
    intent = read_json(root / "workers" / f"{slug}.workspace-intent.json")
    if valid_intent(intent):
        return intent
    # Backfill existing workers from the supervisor's recorded transition history.
    latest = None
    try:
        with (root / "workers" / "supervisor.log").open(errors="replace") as stream:
            for line in stream:
                for verb, state in (("slept", "sleeping"), ("woke", "starting")):
                    if re.search(rf" {verb} {re.escape(slug)}(?: |$)", line):
                        try:
                            when = dt.datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S").timestamp()
                            latest = {"state": state, "at": when}
                        except ValueError:
                            pass
    except OSError:
        pass
    return latest


def classify_worker(service, ready, intent, now, started=None):
    if service == "unknown":
        return "unknown"
    if service == "failed":
        return "failed"
    if service == "running":
        if ready:
            return "awake"
        if started is None:
            return "unknown"
        return "failed" if started is not None and now - started > 120 else "starting"
    if service == "starting":
        age = now - intent.get("at", now) if intent else 0
        return "failed" if age > 120 else "starting"
    if intent and intent.get("state") == "sleeping":
        return "sleeping"
    if intent and intent.get("state") == "starting":
        return "starting" if now - intent.get("at", now) < 120 else "failed"
    return "unknown"


def manager_state(root, cwd, run=command):
    r = run(["claude", "agents", "--json"])
    if r is None or r.returncode:
        return "unknown"
    try:
        sessions = json.loads(r.stdout)
        if not isinstance(sessions, list):
            return "unknown"
    except ValueError:
        return "unknown"
    wanted = read_text(root / ".session-id")
    matches = []
    for session in sessions:
        if not isinstance(session, dict) or session.get("kind") != "background":
            continue
        if session.get("cwd") != str(cwd):
            continue
        if wanted:
            session_id = session.get("id", "")
            if not isinstance(session_id, str):
                continue
            if len(wanted) == 8 and re.fullmatch(r"[0-9a-f]{8}", wanted):
                if not session_id.startswith(wanted):
                    continue
            elif session_id != wanted:
                continue
        matches.append(session)
    if not matches:
        return "failed"
    if len(matches) != 1:
        return "unknown"
    status = matches[0].get("status")
    if status in ("busy", "idle", "running", "waiting"):
        return "awake"
    if status in ("stopped", "failed"):
        return "failed"
    return "starting" if status == "starting" else "unknown"


class Reporter:
    def __init__(self, args):
        self.args = args
        self.root = Path(args.root).resolve()
        self.previous = {}
        self.retry_after = {}
        cached = read_json(self.root / ".workspace-readiness.json")
        self.ready = cached if isinstance(cached, dict) else {}
        self.last_manager_probe = 0
        self.manager = "unknown"
        self.pending = {}

    def publish(self, channel, pubkey, state, now):
        key = (channel, pubkey)
        if now < self.retry_after.get(key, 0):
            return False
        old_state, last_sent = self.previous.get(key, (None, 0))
        if old_state == state and now - last_sent < 60:
            return True
        args = [self.args.buzz, "agent-workspace", "publish", "--channel", channel,
                "--manager-channel", self.args.manager_channel, "--agent-pubkey", pubkey,
                "--location", self.args.location, "--state", "starting" if state == "unknown" else state,
                "--lease-seconds", "0" if state == "unknown" else "180"]
        if self.args.dry_run:
            print(json.dumps({"channel": channel, "agentPubkey": pubkey, "state": state}))
        else:
            r = command(args, timeout=15)
            if r is None or r.returncode:
                self.retry_after[key] = now + PUBLISH_RETRY_SECONDS
                category = publish_failure_category(r)
                print(f"workspace publish failed: channel={channel} category={category} "
                      f"retry_seconds={PUBLISH_RETRY_SECONDS}", file=sys.stderr)
                return False
            try:
                accepted = json.loads(r.stdout).get("accepted") is True
            except (ValueError, AttributeError):
                accepted = False
            if not accepted:
                self.retry_after[key] = now + PUBLISH_RETRY_SECONDS
                print(f"workspace publish refused: channel={channel} "
                      f"retry_seconds={PUBLISH_RETRY_SECONDS}", file=sys.stderr)
                return False
        self.retry_after.pop(key, None)
        self.previous[key] = state, now
        return True

    def tick(self):
        now = time.time()
        if now - self.last_manager_probe >= 30:
            self.manager = manager_state(self.root, Path(self.args.manager_cwd))
            self.last_manager_probe = now
        # Task authorization depends on its manager root already existing.
        if not self.publish(self.args.manager_channel, self.args.manager_pubkey, self.manager, now):
            return
        for busy in sorted((self.root / "workers").glob("*.busy")):
            slug = busy.stem
            if not SLUG.fullmatch(slug):
                continue
            channel = read_text(busy.with_suffix(".channel"))
            pubkey = read_text(busy.with_suffix(".pub"))
            if not channel or not re.fullmatch(r"[a-f0-9]{64}", pubkey):
                continue
            service, pid = process_state(slug, sys.platform)
            started = process_started(pid) if pid else None
            intent = last_intent(self.root, slug)
            intent_file = busy.with_suffix(".workspace-intent.json")
            # An observed start supersedes an older sleep even when somebody
            # starts the service outside the hooked supervisor. Persist this
            # before readiness, so a later crash or reporter restart cannot
            # resurrect the earlier sleeping state.
            observed_start = (service == "running" and started is not None
                              and (intent is None or
                                   (intent["state"] == "sleeping" and intent["at"] < started)))
            if observed_start:
                intent = {"state": "starting", "at": started}
            if intent and (observed_start or not intent_file.exists()) and not self.args.dry_run:
                try:
                    intent = record_intent(self.root, slug, intent)
                except OSError:
                    # Do not claim a reliable state if the observation could
                    # not be persisted. Retry on the next independent tick.
                    print(f"workspace intent update failed: worker={slug}", file=sys.stderr)
                    self.publish(channel, pubkey, "unknown", now)
                    continue
            if service == "starting":
                self.pending.setdefault(slug, now)
                if not intent or intent.get("state") != "starting":
                    intent = {"state": "starting", "at": self.pending[slug]}
            else:
                self.pending.pop(slug, None)
            identity = f"{pid}:{started}"
            ready = started is not None and self.ready.get(slug) == identity
            if service == "running" and started is not None and not ready:
                ready = subscribed_since(busy.with_suffix(".out"), channel, started)
                if ready:
                    self.ready[slug] = identity
                    if not self.args.dry_run:
                        atomic_json(self.root / ".workspace-readiness.json", self.ready)
            state = classify_worker(service, ready, intent, now, started)
            self.publish(channel, pubkey, state, now)


def main():
    os.environ["LC_ALL"] = "C"
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    mark = sub.add_parser("mark", help="Record a supervisor transition locally; no network")
    mark.add_argument("--root", required=True)
    mark.add_argument("--worker", required=True)
    mark.add_argument("--state", choices=["starting", "sleeping"], required=True)
    serve = sub.add_parser("run")
    serve.add_argument("--root", required=True)
    serve.add_argument("--manager-channel", default=os.environ.get("BUZZ_CHANNEL"), required=not os.environ.get("BUZZ_CHANNEL"))
    serve.add_argument("--manager-pubkey", required=True)
    serve.add_argument("--manager-cwd", required=True)
    serve.add_argument("--location", choices=["local", "remote"], required=True)
    serve.add_argument("--buzz", default="buzz")
    serve.add_argument("--once", action="store_true")
    serve.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.command == "mark":
        if not SLUG.fullmatch(args.worker):
            parser.error("invalid worker slug")
        record_intent(Path(args.root), args.worker,
                      {"state": args.state, "at": time.time()})
        return
    reporter = Reporter(args)
    while True:
        reporter.tick()
        if args.once:
            return
        time.sleep(2)


if __name__ == "__main__":
    main()
