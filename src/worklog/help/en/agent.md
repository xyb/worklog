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
  wl agent gaps            # important work that has NO session on it
  wl agent rm              # unbind this session

## Is everything important actually being pushed?

Once each real task has a session bound to it, wl can answer a question no task list can:
**is every important thing actually being worked, and is it still moving?** Two views, forward
and reverse.

### `wl agent ls` — what is bound, and is it moving?

    $ wl agent ls
    2026-07-14 (today)
      #42 ← claude:d93867f4-… · write the Q3 report  13m
      #57 ← claude:534ca427-… · redesign the settings page  2h
      #57 ← cursor:9a76d245-… · redesign the settings page  2h
    2026-07-09
      #31 ← claude:ac5dd474-… · migrate the old exports  4d 💤
      #19 ← claude:1691636b-… · rename the legacy fields  4d 💤

The trailing column is **how long since the node was last worked** — its newest log, coarse
(`40m` / `5h` / `12d`). A 💤 lands once that passes `--stale-days` (default 3): the session is
bound, but nothing has moved in days. That's the signal — a tab you opened and forgot.

The **bind itself doesn't count as work**. Binding writes a history log, and if that counted,
every freshly-bound node would look active forever — exactly the claim the view exists to test.
So a session bound to a task you then never touched still reads stale, which is the honest answer.
(The row still sorts and groups under today, so the session you just bound never falls off the
bottom of the list.)

    wl agent ls                  # default: by day, most-recently-worked first
    wl agent ls --stale-days 1   # stricter: 💤 anything untouched since yesterday
    wl agent ls --no-activity    # drop the age column
    wl agent ls --all            # every binding (default elides older ones)
    wl agent ls --by bound       # sort by bind time, not by last work
    wl agent ls --flat           # no per-day grouping
    wl agent ls -o json          # [{id,agent,sid,title,act,bound,stale}]

### `wl agent gaps` — what has NOBODY on it?

The listing above can only ever show what IS bound, so by construction it cannot surface the
task nobody is on. That's the reverse view, and it's the one that catches what you'd miss:

    $ wl agent gaps
    ⚠ important but no session pushing it (3):
      [/] [#A] #12 rebuild the ingest pipeline [40h12m]  (DOING)
      [/] [#A] #63 investigate the queue backlog  (DOING)
      [ ] [#B] #71 today's third deliverable  (goal)

Two things qualify, and the `(reason)` says which:

- **`goal`** — a still-open target of **today's goal**: what you said this morning you'd deliver
  today. Settled targets drop out, including a recurring one you already ticked (see `wl help goal`).
- **`DOING`** — a **P0 you declared you're working on**. Claiming to be on it while nothing is
  pushing it is the actual risk signal.

Highest priority first, so the in-flight P0s are what you read at the top.

Three exclusions, each learned the hard way from a real database:

- **A merely-open P0 is NOT a gap.** Priority is a standing ranking, not a claim that a thing is
  in flight today. Counting every open P0 sweeps in nearly all of them at once — and an alert
  that fires every day is one you learn to ignore.
- **Recurring items are excluded** from the DOING side. A habit never leaves DOING, because
  `wl tick` doesn't move the status (`wl done` would retire the whole recurrence). It's kept up
  by checking in, not by a session — flagging it would warn you every single day, forever.
- **Containers (project / area) are excluded.** A project stays DOING for as long as anything
  under it is alive, and it's pushed through its children — a session binds to a task, not to the
  bucket the task sits in.

### Using it day to day

Morning, after `wl goal` sets today's targets — ask what's uncovered, and open a session for
each thing that matters:

    wl agent gaps            # → today's targets with nobody on them
    wl agent 71              # (in the new session/tab) claim one

End of day, or any time the tabs have piled up — find the ones that stalled:

    wl agent ls --stale-days 1   # 💤 = bound but untouched — close it, or push it

The pair is the whole loop: `gaps` says what needs a session, `ls` says which sessions stopped
delivering. Neither needs you to remember which tab was doing what.

## Supported runtimes (the AgentRuntime registry)

Runtime-specific knowledge lives in ONE registry (`src/worklog/commands/agent_runtime.py`) —
each supported tool declares its name, its session-id env var, the env marker identifying its
shell, and its hook JSON shape. Currently registered:

- **claude** — session id from `$CLAUDE_CODE_SESSION_ID`; the default runtime when nothing else
  matches; `--hook claude` emits a `UserPromptSubmit` additionalContext payload.
- **cursor** — session id from `$CURSOR_CONVERSATION_ID`; detected by `$CURSOR_AGENT=1`;
  `--hook cursor` emits a `sessionStart` env + additional_context payload.

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
