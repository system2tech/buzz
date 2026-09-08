"""Select only the recorded manager; never adopt a session based on its folder."""
import json
from pathlib import Path
import sys
import uuid

ACTIVE = {"busy", "idle", "running", "waiting", "starting"}
STOPPED = {"stopped", "failed"}


def write_value(path, value):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(value + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def decision(root, cwd, sessions):
    if not isinstance(sessions, list) or any(not isinstance(s, dict) for s in sessions):
        raise ValueError("expected a list of session objects")
    identity = root / ".session-id"
    launched = root / ".session-launched"
    if identity.exists():
        wanted = identity.read_text().strip()
        if str(uuid.UUID(wanted)) != wanted:
            raise ValueError("manager identity must be a full canonical UUID")
    else:
        wanted = str(uuid.uuid4())
        write_value(identity, wanted)
    matches = [s for s in sessions if s.get("sessionId", s.get("id")) == wanted]
    if len(matches) > 1:
        raise ValueError("ambiguous manager identity")
    if matches:
        manager = matches[0]
        if manager.get("kind") != "background" or manager.get("cwd") != str(cwd):
            raise ValueError("recorded manager has unexpected kind or working directory")
        status = manager.get("status")
        if status in ACTIVE:
            write_value(launched, wanted)
            return "alive", wanted
        # Claude retains stopped sessions without status or PID after daemon shutdown.
        offline = status is None and manager.get("pid") is None
        if status not in STOPPED and not offline:
            raise ValueError("unrecognized manager status")
        write_value(launched, wanted)
    mode = "resume" if launched.exists() and launched.read_text().strip() == wanted else "new"
    return mode, wanted


if __name__ == "__main__":
    try:
        action, session_id = decision(Path(sys.argv[1]), Path(sys.argv[2]), json.load(sys.stdin))
        print(action, session_id)
    except (ValueError, OSError) as error:
        print(f"Cannot determine manager identity: {error}", file=sys.stderr)
        sys.exit(1)
