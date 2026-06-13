"""Embedding-backend configuration for `wl query` / `wl reindex`.

Resolution layers, lowest to highest precedence:

    1. built-in defaults (EMBED_DEFAULTS)               — a local HTTP server
    2. ``[embedding]`` in $XDG_CONFIG_HOME/worklog/config.ini
    3. ``$WORKLOG_EMBED_*`` environment variables
    4. per-invocation CLI flags (--endpoint / --model / --dimensions / --api-key)

The returned dict also carries a ``source`` sub-dict naming where each value
came from ("default" / "config" / "env" / "flag"), so `wl config` can show it
and users can see why a backend is being used.

The backend is any OpenAI-compatible ``/v1/embeddings`` HTTP server running
locally; the default points at one on 127.0.0.1. Switching backend (a different
local server, a remote one) is a config change, not a code change."""
from __future__ import annotations

import configparser
import os

from .xdg import _resolve_config_path

EMBED_DEFAULTS = {
    "endpoint": "http://127.0.0.1:14242/v1/embeddings",
    "model": "mlx-community/Qwen3-Embedding-0.6B-4bit-DWQ",
    "dimensions": None,   # None = the model's native dimensionality (no truncation)
    "api_key": None,      # most local backends need none
}

# field -> ($WORKLOG_EMBED_* env var name)
_ENV = {
    "endpoint": "WORKLOG_EMBED_ENDPOINT",
    "model": "WORKLOG_EMBED_MODEL",
    "dimensions": "WORKLOG_EMBED_DIMENSIONS",
    "api_key": "WORKLOG_EMBED_API_KEY",
}

_INT_FIELDS = {"dimensions"}


def _coerce(field, value):
    """Turn a raw string (from ini/env/flag) into the field's typed value.
    Empty string -> None (treated as 'unset', falls through to the lower layer's value)."""
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if value == "":
            return None
    if field in _INT_FIELDS and value is not None:
        return int(value)
    return value


def _read_ini():
    """The ``[embedding]`` section as a plain dict (empty if file/section absent)."""
    p = _resolve_config_path()
    if not p.exists():
        return {}
    cfg = configparser.ConfigParser()
    cfg.read(p, encoding="utf-8")
    if "embedding" not in cfg:
        return {}
    return dict(cfg["embedding"])


def resolve_embedding_config(args=None):
    """Resolve the embedding backend config across the four layers. Returns a dict of
    the resolved values plus a ``source`` dict naming each value's origin."""
    ini = _read_ini()
    out = {}
    source = {}
    for field, default in EMBED_DEFAULTS.items():
        flag_val = _coerce(field, getattr(args, field, None)) if args is not None else None
        env_val = _coerce(field, os.environ.get(_ENV[field]))
        ini_val = _coerce(field, ini.get(field))
        if flag_val is not None:
            out[field], source[field] = flag_val, "flag"
        elif env_val is not None:
            out[field], source[field] = env_val, "env"
        elif ini_val is not None:
            out[field], source[field] = ini_val, "config"
        else:
            out[field], source[field] = default, "default"
    out["source"] = source
    return out
