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

# Default backend = a local Ollama (`ollama pull qwen3-embedding:0.6b`), the most common
# zero-config local embedding server. query_prompt is a FULL TEMPLATE applied to the QUERY only
# (documents never get it): the literal `{query}` placeholder is replaced with the query text, so
# the whole format — instruction, separator, where the query goes — is visible and editable, not
# hidden in code (`\n` in a config value is read as a newline). Default = Qwen3-Embedding's
# official retrieval template. Set it to "" for a server that already applies the instruction
# itself (e.g. nmem via input_type), to avoid doing it twice.
EMBED_DEFAULTS = {
    "endpoint": "http://localhost:11434/v1/embeddings",
    "model": "qwen3-embedding:0.6b",
    "dimensions": None,   # None = the model's native dimensionality (no truncation)
    "api_key": None,      # most local backends need none
    "query_prompt": "Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery:{query}",
}

# field -> ($WORKLOG_EMBED_* env var name)
_ENV = {
    "endpoint": "WORKLOG_EMBED_ENDPOINT",
    "model": "WORKLOG_EMBED_MODEL",
    "dimensions": "WORKLOG_EMBED_DIMENSIONS",
    "api_key": "WORKLOG_EMBED_API_KEY",
    "query_prompt": "WORKLOG_EMBED_QUERY_PROMPT",
}

_INT_FIELDS = {"dimensions"}
# Fields where an explicit empty string is a REAL value (means "off"), NOT "unset → use default":
# query_prompt = "" must mean "don't wrap the query" (server already does), distinct from absent.
_PRESERVE_EMPTY = {"query_prompt"}


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


def synonym_map():
    """The ``[synonyms]`` section of config.ini as a member→group lookup: each line
    ``canonical = alias1, alias2`` defines a group {canonical, alias1, alias2}, and every
    member (lowercased) maps to that whole set. So a query term hitting any member can be
    expanded to the whole group (e.g. ``New York = NYC, NY`` → searching "NYC" also
    matches "New York"/"NY"). Empty if no file/section."""
    p = _resolve_config_path()
    if not p.exists():
        return {}
    cfg = configparser.ConfigParser()
    cfg.read(p, encoding="utf-8")
    if "synonyms" not in cfg:
        return {}
    out = {}
    for canon, aliases in cfg["synonyms"].items():
        group = {canon} | {a.strip() for a in aliases.split(",") if a.strip()}
        for m in group:
            out[m.lower()] = group
    return out


def auto_reindex_enabled():
    """Whether a write should kick a background incremental reindex. Default ON; turn off with
    `$WORKLOG_AUTO_REINDEX=0` or `[index] auto_reindex = false` in config.ini. The env wins."""
    env = os.environ.get("WORKLOG_AUTO_REINDEX")
    if env is not None:
        return env.strip().lower() not in ("0", "false", "no", "off", "")
    p = _resolve_config_path()
    if not p.exists():
        return True
    cfg = configparser.ConfigParser()
    try:
        cfg.read(p, encoding="utf-8")
    except configparser.Error:
        return True
    if "index" in cfg and "auto_reindex" in cfg["index"]:
        return cfg["index"].getboolean("auto_reindex", True)
    return True


def resolve_embedding_config(args=None):
    """Resolve the embedding backend config across the four layers. Returns a dict of
    the resolved values plus a ``source`` dict naming each value's origin."""
    ini = _read_ini()
    out = {}
    source = {}
    for field, default in EMBED_DEFAULTS.items():
        if field in _PRESERVE_EMPTY:
            # presence-based: an explicit "" (set-but-empty) is a real value and wins;
            # only an ABSENT key (None) falls through to the next layer / default.
            flag_val = getattr(args, field, None) if args is not None else None
            env_val = os.environ.get(_ENV[field])
            ini_val = ini.get(field)
        else:
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
