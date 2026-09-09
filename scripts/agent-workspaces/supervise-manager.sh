#!/bin/bash
# Persistent service: track one recorded UUID, never any session in a folder.
set -uo pipefail
ROOT="${MRFIX_ROOT:-$HOME/mrfix}"
CWD="${MRFIX_MANAGER_CWD:-$HOME/mr-fix}"
IDENTITY_HELPER="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/manager_identity.py"
. "$ROOT/env.sh" || exit 1
cd "$CWD" || exit 1
LOG="$ROOT/supervise.log"
log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG"; }
while true; do
  if sessions=$(claude agents --json 2>> "$LOG"); then
    if result=$(printf '%s' "$sessions" | python3 "$IDENTITY_HELPER" "$ROOT" "$CWD" 2>> "$LOG"); then
      read -r action session_id <<< "$result"
      if [ "$action" = alive ]; then
        log "ok — manager $session_id"
      else
        # **Never pass --session-id.** `claude --bg` manages the id itself and
        # ignores it with a warning, so dictating one produced an identity that
        # could never match a live session: the resume branch then created a
        # phantom, and the supervisor watched that while the real manager ran
        # unsupervised with the liveness check reading green.
        launch=(env NO_COLOR=1 claude --bg --resume "$session_id")
        if [ "$action" = new ]; then
          # NO_COLOR because the id has to be parsed out of this: run by hand
          # from a terminal, `claude` colours the `backgrounded` line and the
          # capture silently returns nothing.
          launch=(env NO_COLOR=1 claude --bg --permission-mode bypassPermissions
            "Follow the loaded CLAUDE.md instructions — start the inbox Monitor exactly as they say, persistent with a long timeout_ms, before anything else. Then check what you missed while you were down, answer it in the channel, and wait.")
        fi
        log "${action} manager ${session_id:-<unrecorded>}"
        # Resume without extra options: Claude otherwise forks a new session.
        if output=$("${launch[@]}" 2>&1); then
          printf '%s\n' "$output" >> "$LOG"
          if [ "$action" = new ]; then
            # `backgrounded · <id>` is the only place the real id appears. Record
            # it before anything else uses it; do NOT look it up in `claude
            # agents --json` first, which lagged a launch by over two minutes.
            # **Anchor to the `backgrounded` line.** An unanchored hex match
            # takes the first eight-hex run in the whole output, so a token in an
            # earlier line — `deadbeef` in a warning — gets recorded as the
            # session id. Matching hex within the line is deliberately tolerant
            # of colour escapes around it, which is what a `sed` capture of the
            # whole line is not: run by hand from a terminal `claude` colours
            # this line, and a capture that returns empty records no identity and
            # relaunches every tick forever. NO_COLOR above prevents that at
            # source; tolerating it here means one guard rather than two.
            session_id=$(printf '%s' "$output" \
              | grep -F 'backgrounded' | head -1 \
              | grep -oE '[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}|[0-9a-f]{8}' | head -1)
            if [ -z "$session_id" ]; then
              log "launched but could not read the session id; not recording an identity"
              sleep 120
              continue
            fi
            printf '%s\n' "$session_id" > "$ROOT/.session-id.tmp"
            chmod 600 "$ROOT/.session-id.tmp"
            mv "$ROOT/.session-id.tmp" "$ROOT/.session-id"
            log "recorded manager $session_id"
          fi
          printf '%s\n' "$session_id" > "$ROOT/.session-launched.tmp"
          chmod 600 "$ROOT/.session-launched.tmp"
          mv "$ROOT/.session-launched.tmp" "$ROOT/.session-launched"
        else
          printf '%s\n' "$output" >> "$LOG"
          log "manager launch failed; will retry the same identity"
        fi
      fi
    else
      log "cannot determine manager identity; leaving existing sessions alone"
    fi
  else
    log "cannot query session status; leaving existing sessions alone"
  fi
  sleep 120
done
