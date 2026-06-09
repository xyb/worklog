#!/bin/sh
# Claude Code status-line segment — prints ` 📌WL#<id>` for the task this session is bound to
# (nothing if unbound). Portable: only `wl` + POSIX sh/sed (no jq, no sqlite CLI).
#
# Use it from your own status-line command by piping Claude Code's status-line JSON (which has
# `session_id`) into this script and appending the output:
#   input=$(cat)
#   printf '%s' "$input" | sh ~/.claude/statusline-wl.sh    # → ' 📌WL#42'
#
# Note: this spawns `wl` (Python) per refresh — simplest + dependency-light. For a faster segment,
# query the DB directly (see `wl help agent`). If `wl` isn't on PATH, set $WL_BIN.
WL="${WL_BIN:-wl}"
sid=$(cat | sed -n 's/.*"session_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
[ -z "$sid" ] && exit 0
line=$(WL_SESSION_ID="$sid" "$WL" agent context 2>/dev/null)
[ -z "$line" ] && exit 0
printf ' 📌WL#%s' "${line%%	*}"     # the <id> before the tab
