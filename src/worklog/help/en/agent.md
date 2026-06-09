---
title: agent — bind this AI session to a task
category: command
see_also: prop, show, ls
---
`wl agent` ties the current Claude Code session to a node, so the AI knows which task it is
working on and the status line / hook context can surface it. It is stored as the
`agent_session.claude` prop on the node — no new table.

  wl agent 42          # bind this session to task #42 (default verb: set)
  wl agent 42 --record # bind + leave a permanent history mark on the node
  wl agent             # show what this session is bound to
  wl agent ls          # list all session→task bindings
  wl agent rm          # unbind this session

The session id comes from `$WL_SESSION_ID` (preferred — a SessionStart hook can freeze the
official session_id under this stable name) or the undocumented `$CLAUDE_CODE_SESSION_ID`; if
neither is set, `wl agent` fails closed instead of guessing.

One session maps to one node: rebinding moves the prop off the old node. Binding a node that
another session already holds prints a conflict warning but still binds. The key prefix
`agent_session.` is shared across apps (a future `agent_session.cursor` etc.), so one query
finds all of a node's session bindings.

## Live pointer vs. history: `--record`

The binding splits into two stores with different jobs:

- The `agent_session.claude` **prop is the live pointer** — exactly one session → one node, and
  it *moves* on rebind. It answers "what is this session on right now?" and powers the status
  line / hook. Plain `wl agent <id>` only touches this — no log, cheap.
- `wl agent <id> --record` additionally writes the **history trail**: one log + an
  `agent_session` metric carrying the *full* session id, which stays on the node forever. It
  answers "which sessions has this node ever been worked under?" Recover it with:

      wl metric ls <id> --tag agent_session --all   # every session that bound this node
      wl show <id>                                   # the bind events appear in the timeline

Only the bind *event* is recorded — later logs don't each carry the session id, so the cost is
one row per association, not a stamp on every write. That's the deliberate light design: the
prop never accumulates, the metric is the append-only ledger.

**Recommended usage**: bind without `--record` for routine single-session work (the live pointer
is all you need). Add `--record` when a node's session lineage matters — a long-lived task that
several agent sessions pass through, or anything you may later want to forensically trace back to
the conversation that touched it.

## Wiring it up: status line + context hook (optional)

`wl agent` only stores the binding. To make it *visible* and *known to the agent*, wire two
small pieces that read the prop straight from the DB with `sqlite3` (fast — no `wl` spawn per
refresh). The reverse lookup (session id → task) is one query, where `$DB` is `$WORKLOG_DB` or
`~/.local/share/worklog/worklog.db`:

  sqlite3 "$DB" "SELECT node_id FROM prop WHERE key='agent_session.claude'
                 AND value='<session-id>' AND deleted_at IS NULL LIMIT 1;"

**1. Status line — show `📌WL#<id>`.** Claude Code passes its status-line command a JSON blob
with `session_id` on stdin; add a segment:

  sid=$(echo "$input" | jq -r '.session_id // empty')
  bound=$(sqlite3 "$DB" "SELECT node_id FROM prop WHERE key='agent_session.claude' AND value='$sid' AND deleted_at IS NULL LIMIT 1;")
  [ -n "$bound" ] && printf ' 📌WL#%s' "$bound"

**2. Context hook — keep the agent anchored.** A `UserPromptSubmit` hook that injects the bound
task every turn. The hook reads `session_id` from stdin, joins prop→node for the title, and prints:

  {"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"bound to WL#<id>: <title>"}}

Register the hook script in `~/.claude/settings.json` under `hooks.UserPromptSubmit`. Both pieces
read the same `agent_session.claude` prop, so `wl agent <id>` / `wl agent rm` update them with no
extra wiring — bind once, the status line and the agent's context both follow.
