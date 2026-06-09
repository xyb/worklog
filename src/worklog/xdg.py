"""XDG Base Directory resolution for worklog's DB and config files.

Spec: https://specifications.freedesktop.org/basedir-spec/

All four helpers re-read environment variables on every call so test
fixtures that monkeypatch `$HOME` / `$XDG_*` see the change without
needing a module reload.
"""
from __future__ import annotations

import os
from pathlib import Path


def _xdg_data_home() -> Path:
    """`$XDG_DATA_HOME` (default `~/.local/share`)."""
    return Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))


def _xdg_config_home() -> Path:
    """`$XDG_CONFIG_HOME` (default `~/.config`)."""
    return Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))


def _xdg_state_home() -> Path:
    """`$XDG_STATE_HOME` (default `~/.local/state`) — transient state, e.g. the per-session
    agent-binding pointer files that let hooks / the status line read the binding without a DB query."""
    return Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state"))


def _resolve_db_path(args=None) -> Path:
    """Resolve the SQLite DB path. Priority:

    1. ``--db`` flag (per-invocation override, top priority)
    2. ``$WORKLOG_DB`` env
    3. ``$XDG_DATA_HOME/worklog/worklog.db`` (default ``~/.local/share/worklog/worklog.db``)
    """
    if args is not None and getattr(args, "db", None):
        return Path(args.db).resolve()
    env = os.environ.get("WORKLOG_DB")
    if env:
        return Path(env).resolve()
    return (_xdg_data_home() / "worklog" / "worklog.db").resolve()


def _resolve_aliases_path() -> Path:
    """``$XDG_CONFIG_HOME/worklog/aliases.ini`` (default ``~/.config/worklog/aliases.ini``)."""
    return _xdg_config_home() / "worklog" / "aliases.ini"
