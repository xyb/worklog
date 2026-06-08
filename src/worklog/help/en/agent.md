---
title: agent — bind this AI session to a task
category: command
see_also: prop, show, ls
---
`wl agent` ties the current Claude Code session to a node, so the AI knows which task it is
working on and the status line / hook context can surface it. It is stored as the
`agent_session.claude` prop on the node — no new table.

  wl agent 42        # bind this session to task #42 (default verb: set)
  wl agent           # show what this session is bound to
  wl agent ls        # list all session→task bindings
  wl agent rm        # unbind this session

The session id comes from `$WL_SESSION_ID` (preferred — a SessionStart hook can freeze the
official session_id under this stable name) or the undocumented `$CLAUDE_CODE_SESSION_ID`; if
neither is set, `wl agent` fails closed instead of guessing.

One session maps to one node: rebinding moves the prop off the old node. Binding a node that
another session already holds prints a conflict warning but still binds. The key prefix
`agent_session.` is shared across apps (a future `agent_session.cursor` etc.), so one query
finds all of a node's session bindings.
