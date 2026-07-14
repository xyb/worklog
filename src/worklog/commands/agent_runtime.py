"""Agent runtime registry for `wl agent`.

This module centralizes runtime-specific behavior (session-id env, runtime detection marker,
and hook JSON shape) so adding a new tool is one registry entry instead of edits across handlers.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Callable, Dict, Optional

from ..render import out, _c


HookBuilder = Callable[[str, str, "AgentRuntime"], Optional[Dict]]


@dataclass(frozen=True)
class AgentRuntime:
    """One supported AI runtime (claude / cursor / …)."""
    name: str
    session_env: str
    marker_env: str = ""
    marker_value: str = ""
    hook_builder: HookBuilder | None = None

    def detected(self, environ: dict[str, str] | None = None) -> bool:
        """Whether this shell appears to be running inside this runtime."""
        if not self.marker_env:
            return False
        env = environ or os.environ
        marker = (env.get(self.marker_env) or "").strip()
        if not marker:
            return False
        return marker == self.marker_value if self.marker_value else True

    def hook_json(self, sid: str, binding_msg: str) -> str:
        """Ready-to-print hook JSON for `wl agent context --hook <runtime>`."""
        if not sid or not self.hook_builder:
            return ""
        payload = self.hook_builder(sid, binding_msg, self)
        return json.dumps(payload, ensure_ascii=False) if payload else ""


def _claude_hook_payload(sid: str, binding_msg: str, rt: AgentRuntime) -> dict | None:
    if not binding_msg:
        return None
    return {"hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit", "additionalContext": binding_msg}}


def _cursor_hook_payload(sid: str, binding_msg: str, rt: AgentRuntime) -> dict | None:
    payload = {"env": {"WL_SESSION_ID": sid, "WL_AGENT": rt.name}}
    if binding_msg:
        payload["additional_context"] = binding_msg
    return payload


DEFAULT_AGENT_RUNTIME = "claude"
AGENT_RUNTIMES = (
    # claude has no marker: it is the fallback default when no runtime marker matches.
    # Registry order also sets session-env fallback priority in `resolve_session_id`.
    AgentRuntime("claude", session_env="CLAUDE_CODE_SESSION_ID",
                 hook_builder=_claude_hook_payload),
    AgentRuntime("cursor", session_env="CURSOR_CONVERSATION_ID",
                 marker_env="CURSOR_AGENT", marker_value="1",
                 hook_builder=_cursor_hook_payload),
)
AGENT_HOOK_CHOICES = tuple(rt.name for rt in AGENT_RUNTIMES)


def runtime_by_name(name: str) -> AgentRuntime | None:
    for rt in AGENT_RUNTIMES:
        if rt.name == name:
            return rt
    return None


def resolve_runtime_name(environ: dict[str, str] | None = None) -> str:
    """Runtime recorded in bind history: WL_AGENT override -> detected marker -> default."""
    env = environ or os.environ
    explicit = (env.get("WL_AGENT") or "").strip().lower()
    if explicit:
        return explicit
    for rt in AGENT_RUNTIMES:
        if rt.detected(env):
            return rt.name
    return DEFAULT_AGENT_RUNTIME


def resolve_session_id(environ: dict[str, str] | None = None) -> str | None:
    """Session id for the current shell: WL_SESSION_ID first, then runtime envs by registry order."""
    env = environ or os.environ
    sid = env.get("WL_SESSION_ID")
    if sid:
        return sid
    for rt in AGENT_RUNTIMES:
        sid = env.get(rt.session_env)
        if sid:
            return sid
    return None


def session_env_hints() -> list[str]:
    """Human-readable env-variable hints for fail-closed errors."""
    return ["$WL_SESSION_ID"] + [f"${rt.session_env}" for rt in AGENT_RUNTIMES]


# --- auto-brief: overview commands default to -q inside an agent session -------------------
# Whitelisted by measured savings vs what brief throws away (a real ~1400-node DB):
#   summary 93% · active 53% · projects 30% · day 22% · ls 17%
# Excluded on purpose: `show` (brief drops the timeline — the reason you ran it), `logs`
# (drops the log body — likewise), `find` / `tree` / `focus` (brief is a no-op there anyway).
AUTO_BRIEF_CMDS = frozenset(("day", "ls", "projects", "active", "summary"))
AUTO_BRIEF_HINT = "(agent session: auto -q; --no-brief for the dropped detail)"


def apply_auto_brief(args, environ: dict[str, str] | None = None) -> bool:
    """Turn on brief output for an overview command running inside an agent session.

    Brief always drops *something* — that is how it saves tokens — so the flip is announced
    rather than silent: an agent that needs what was dropped can re-run with --no-brief. Returns
    whether the flip happened. Explicit -q or --no-brief both mean the caller already decided.
    """
    if getattr(args, "no_brief", False) or getattr(args, "brief", False):
        return False
    if getattr(args, "cmd", None) not in AUTO_BRIEF_CMDS:
        return False
    if not resolve_session_id(environ):
        return False
    args.brief = True
    out(_c(AUTO_BRIEF_HINT, "meta"))
    return True
