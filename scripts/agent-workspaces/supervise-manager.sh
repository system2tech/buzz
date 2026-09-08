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
        launch=(claude --bg --resume "$session_id")
        if [ "$action" = new ]; then
          launch=(claude --bg --session-id "$session_id" --permission-mode bypassPermissions
            "Follow the loaded CLAUDE.md instructions — start the inbox Monitor exactly as they say, persistent with a long timeout_ms, before anything else. Then check what you missed while you were down, answer it in the channel, and wait.")
        fi
        log "$action manager $session_id"
        # Resume without extra options: Claude otherwise forks a new session.
        if "${launch[@]}" >> "$LOG" 2>&1; then
          printf '%s\n' "$session_id" > "$ROOT/.session-launched.tmp"
          chmod 600 "$ROOT/.session-launched.tmp"
          mv "$ROOT/.session-launched.tmp" "$ROOT/.session-launched"
        else
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
