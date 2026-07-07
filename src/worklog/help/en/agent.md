---
title: agent — bind this AI session to a task
category: command
see_also: prop, show, ls
---
`wl agent` ties the current AI session to a node, so the AI knows which task it is working on
and the status line / hook context can surface it. It is stored as an `agent_session.<agent>`
prop on the node — no new table.

  wl agent 42              # bind this session to task #42 (default verb: set)
  wl agent 42 --record     # bind + leave a permanent history mark on the node
  wl agent 42 --agent codex # record a non-default runtime (else $WL_AGENT, else auto-detect)
  wl agent                 # show what this session is bound to
  wl agent ls              # list all session→task bindings
  wl agent rm              # unbind this session

## Supported runtimes (the AgentRuntime registry)

Runtime-specific knowledge lives in ONE registry (`src/worklog/commands/agent_runtime.py`) —
each supported tool declares its name, its session-id env var, the env marker identifying its
shell, and its hook JSON shape. Currently registered:

| runtime | session id from            | detected by        | hook payload (`--hook <name>`) |
|---------|----------------------------|--------------------|--------------------------------|
| claude  | `$CLAUDE_CODE_SESSION_ID`  | (the default)      | `UserPromptSubmit` additionalContext |
| cursor  | `$CURSOR_CONVERSATION_ID`  | `$CURSOR_AGENT=1`  | `sessionStart` env + additional_context |

**Session id**: `$WL_SESSION_ID` wins (a session-start hook can freeze the runtime's official
session id under this stable name), else each registry runtime's own env var in order; if none
is set, `wl agent` fails closed instead of guessing.

**Which agent** is recorded too, so the history shows *what* worked the node, not just an opaque
sid: `--agent NAME` wins, else `$WL_AGENT`, else the first registry runtime whose env marker
matches (Cursor agent shells export `$CURSOR_AGENT=1`), else `claude` (the runtime this CLI
ships for). The name is the prop key suffix and the metric value.

One session maps to one node: rebinding moves the prop off the old node (under any agent key).
Binding a node that another session already holds prints a conflict warning but still binds. The
key prefix `agent_session.` is shared across apps (`agent_session.codex`, `agent_session.cursor`,
…), so one query finds all of a node's session bindings.

## Live pointer vs. history (records by default)

The binding splits into two stores with different jobs:

- The `agent_session.<agent>` **prop is the live pointer** — exactly one session → one node, and
  it *moves* on rebind. It answers "what is this session on right now?" and powers the status
  line / hook.
- A **history trail** — one log carrying *two* metrics: `agent_session` (the *full* session id)
  and `agent` (the runtime name — claude / cursor / …), which stays on the node forever. It
  answers "which sessions, run by which agent, has this node ever been worked under?"
  **`wl agent <id>` writes it by default** (so auto-binds capture the lineage without anyone
  remembering a flag); `wl agent <id> --no-record` skips it for a pointer-only bind. Recover it:

      wl metric ls <id> --tag agent_session --all   # every session that bound this node
      wl metric ls <id> --tag agent --all           # which agent runtime worked it
      wl show <id>                                   # the bind events appear in the timeline

Only the bind *event* is recorded — later logs don't each carry the session id, so the cost is
one row per *binding*, not a stamp on every write. Rebinding the same session to the same node
again doesn't duplicate it.

**Recommended usage**: leave it on (the default) so a node's session lineage is always traceable;
reach for `--no-record` only for a throwaway pointer-only bind where the history would be noise.

## Wiring it up: status line + context hook (optional)

`wl agent` only stores the binding. Small integrations make it *visible* (status bar
`📌WL#<id>`) and *known to the agent* (injected via the runtime's hook). All ship with the
worklog-cli skill under `integrations/` and depend only on `wl` + POSIX `sh`/`sed` — **no `jq`,
no `sqlite3` CLI**. They all read one query: `wl agent context` — the current session's binding
as a machine line `<id>\t<title>` (empty if unbound), or with `--hook <runtime>` the ready-to-emit
hook JSON in that runtime's shape (so the hook needs no `jq`; `wl` does the escaping).

- `wl-session-context.sh` — Claude Code `UserPromptSubmit` hook (`--hook claude`).
- `wl-cursor-session-start.sh` — Cursor `sessionStart` hook (`--hook cursor`).
- `statusline-wl.sh` — a status-line segment printing ` 📌WL#<id>` (runtime-agnostic).

**Claude Code — context hook.** Injects the binding on every prompt (re-injects only when it
changes):

  mkdir -p ~/.claude/hooks
  cp <skill>/integrations/wl-session-context.sh ~/.claude/hooks/ && chmod +x ~/.claude/hooks/wl-session-context.sh

then register it in `~/.claude/settings.json` under `hooks.UserPromptSubmit` (a `command` hook
running `$HOME/.claude/hooks/wl-session-context.sh`). It caches the binding per session under
`$XDG_STATE_HOME/worklog/agent/<sid>` and injects only when it *changes*; `wl agent set` / `rm`
drop that cache on every bind / rebind / unbind. On the common (cached) path it spawns nothing.

**Cursor — sessionStart hook.** Freezes `$WL_SESSION_ID` / `$WL_AGENT=cursor` for the session and
injects any existing binding as initial context:

  mkdir -p ~/.cursor/hooks
  cp <skill>/integrations/wl-cursor-session-start.sh ~/.cursor/hooks/ && chmod +x ~/.cursor/hooks/wl-cursor-session-start.sh

then register it in `~/.cursor/hooks.json` under `hooks.sessionStart` (a `command` hook running
`./hooks/wl-cursor-session-start.sh`). Inside a Cursor agent shell `wl agent <id>` already works
with no env setup — the hook just makes an *existing* binding known to a fresh session.

If `wl` isn't on a hook's PATH, set `WL_BIN` to the binary.

**Status line.** Pipe your status-line command's stdin JSON through `statusline-wl.sh`; it appends
` 📌WL#<id>` by calling `wl agent context` (simple, dependency-light — no direct DB access). Works
for any runtime whose status-line JSON carries a `session_id` (Claude Code, Cursor CLI).
