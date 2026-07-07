#!/bin/sh
# Cursor sessionStart hook — freeze this chat's session id as $WL_SESSION_ID / $WL_AGENT=cursor
# and inject any existing wl binding as initial additional_context.
#
# Portable: needs only `wl` + POSIX sh/sed — no jq, no sqlite CLI.
#
# Install (user hook):
#   mkdir -p ~/.cursor/hooks
#   cp <skill>/integrations/wl-cursor-session-start.sh ~/.cursor/hooks/
#   chmod +x ~/.cursor/hooks/wl-cursor-session-start.sh
# then register in ~/.cursor/hooks.json:
#   { "version": 1, "hooks": { "sessionStart": [{ "command": "./hooks/wl-cursor-session-start.sh" }] } }
WL="${WL_BIN:-wl}"
input=$(cat)
sid=$(printf '%s' "$input" | sed -n 's/.*"session_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
[ -z "$sid" ] && sid=$(printf '%s' "$input" | sed -n 's/.*"conversation_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
[ -z "$sid" ] && exit 0

json=$(WL_SESSION_ID="$sid" WL_AGENT=cursor "$WL" agent context --hook cursor 2>/dev/null)
[ -n "$json" ] && printf '%s\n' "$json"
exit 0
