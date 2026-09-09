"""Select only the recorded manager; never adopt a session based on its folder."""
import json
from pathlib import Path
import re
import sys

#: What `claude --bg` prints as `backgrounded · <id>`, and the canonical form
#: it expands to. Either may be recorded; both are matched by prefix.
SHORT_ID = re.compile(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}|[0-9a-f]{8}")

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
    if not identity.exists():
        # **Do not invent an identity.** `claude --bg` manages the session id
        # itself and ignores `--session-id`, saying so in a warning, so a UUID
        # made up here could never match a real session: every later tick took
        # the resume branch against an id nothing had created, `--resume`
        # obligingly created it, and the supervisor then watched an idle
        # placeholder while the session doing the work went unsupervised —
        # with `exact_manager_running` reading true throughout. Measured on
        # agents-fin-03, 2026-09-09.
        #
        # So the caller launches without an id and records what `--bg` returns.
        return "new", ""
    wanted = identity.read_text().strip()
    if not SHORT_ID.fullmatch(wanted):
        # **Unparseable is absent, not fatal.** Raising here would exit non-zero,
        # the caller would log "cannot determine manager identity" and sleep, and
        # it would do that forever: the re-derive path is reached only when the
        # file is *missing*, so a file holding garbage has no exit at all. Every
        # shape rule added above is otherwise another route into a state the code
        # cannot leave. `ambiguous manager identity` below stays fatal, because
        # two live candidates is a real reason to keep hands off — but garbage is
        # not ambiguity, it is just garbage, and garbage is replaceable.
        # **Recover only when there is nothing to duplicate.** Raising here
        # wedges forever: the re-derive path is gated on the file being
        # *missing*, so a file holding garbage has no exit. But re-deriving
        # unconditionally is also wrong — with an unreadable identity we cannot
        # tell whether a session running in this folder is our manager, so
        # launching is a coin flip between duplicating it and orphaning it.
        # Refuse while anything is here; re-derive when nothing is.
        here = [s for s in sessions
                if s.get("kind") == "background" and s.get("cwd") == str(cwd)]
        if here:
            raise ValueError("unusable manager identity while a session runs here")
        print(f"discarding unusable manager identity {wanted!r}", file=sys.stderr)
        return "new", ""
    # **Prefix, because `--bg` prints the short form and only that.** Resolving it
    # to a canonical UUID would need a `claude agents --json` lookup, and that
    # lookup is not immediate: a launched session took over two minutes to become
    # visible there. A fix that had to expand the id before recording it would
    # find nothing and fall back to inventing one, which is the bug above.
    matches = [s for s in sessions
               if str(s.get("sessionId", s.get("id")) or "").startswith(wanted)]
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
