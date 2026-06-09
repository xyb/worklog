#!/bin/sh
# Claude Code UserPromptSubmit hook — inject the wl task this session is bound to (set via
# `wl agent <id>`), so the agent stays anchored to it.
#
# Portable: needs only `wl` + POSIX sh/sed — no jq, no sqlite CLI. Caches per session and injects
# ONLY when the binding changes (re-injecting an unchanged binding every prompt just wastes
# tokens): `wl agent set/rm` invalidate the cache, so the next prompt re-fetches and re-injects.
# On the common (cached) path it spawns nothing.
#
# Install: copy to ~/.claude/hooks/, `chmod +x`, and register under hooks.UserPromptSubmit in
# ~/.claude/settings.json. Full steps: `wl help agent`. If `wl` isn't on the hook's PATH, point
# $WL_BIN at it (e.g. WL_BIN=$HOME/.local/bin/wl).
WL="${WL_BIN:-wl}"
DIR="${XDG_STATE_HOME:-$HOME/.local/state}/worklog/agent"

input=$(cat)
sid=$(printf '%s' "$input" | sed -n 's/.*"session_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
[ -z "$sid" ] && exit 0

cache="$DIR/$sid"
[ -f "$cache" ] && exit 0          # binding unchanged since last prompt → silent

mkdir -p "$DIR"
json=$(WL_SESSION_ID="$sid" "$WL" agent context --hook 2>/dev/null)
printf '%s' "$json" > "$cache"     # mark handled (cache an empty result too → no re-run until rebind)
[ -n "$json" ] && printf '%s\n' "$json"
exit 0
