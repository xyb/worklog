"""Shared output helpers for all command handlers.

The @output_format decorator applies the active Formatter to a command handler,
following the AWS CLI pattern: the handler returns structured data; the Formatter
decides how to render it.

    @output_format
    def cmd_foo(args, con):
        result = {"id": ..., "key": ...}
        return TextRenderable(result, cmd_name="foo")

    @text_renderer("foo")
    def _render_foo(result):
        out("✓ " + result["id"])   # called by TextFormatter.emit()

Add a new output format: subclass Formatter and register it in _FORMATTERS.
Override a command's text render: assign to _TEXT_RENDERERS["foo"] directly.
"""
from __future__ import annotations

import json
import sys
from dataclasses import is_dataclass, asdict
from functools import wraps


def _dc_default(obj):
    """json.dumps default: serialize dataclasses; raise TypeError for everything else."""
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

from ..render import set_suppress_output, set_active_error_formatter, get_active_error_formatter


class TextRenderable:
    """Carries structured data (for JSON) and a text renderer (for text mode).

    Return from @output_format handlers to separate data from presentation:
      - TextFormatter.emit() dispatches to a registered renderer by cmd_name,
        falling back to the inline _render closure for un-migrated handlers
      - JSONFormatter.emit() serialises .data, never calls any renderer

    Preferred form (registry-based, data/render fully decoupled):
        return TextRenderable(result, cmd_name="foo")   # renderer in _TEXT_RENDERERS

    Legacy form (inline closure, still supported for backward compat):
        return TextRenderable(result, _render)
    """
    __slots__ = ("data", "_render", "cmd_name")

    def __init__(self, data, render=None, *, cmd_name=None):
        self.data = data
        self._render = render
        self.cmd_name = cmd_name

    def render(self):
        if self._render:
            self._render()


class Formatter:
    """Output formatter protocol.

    Three-phase lifecycle, matching the AWS CLI formatter contract:
      setup()    — called before the handler; configure render state
      teardown() — called in finally; always runs, even on exception
      emit(data) — called with the handler's return value after teardown
    """

    def setup(self) -> None:
        pass

    def teardown(self) -> None:
        pass

    def emit(self, data) -> None:
        pass


_TEXT_RENDERERS: dict[str, object] = {}


def text_renderer(name: str):
    """Register a text render function for a command.

    The decorated function receives the handler's result dict and produces
    human-readable terminal output.  TextFormatter.emit() dispatches here
    by cmd_name, so handlers and render logic live in separate scopes.

    Example::

        @text_renderer("foo_ls")
        def _render_foo_ls(result):
            for item in result["items"]:
                out(_c(item["name"], "id") + " = " + item["value"])

    Override a built-in renderer at runtime::

        from worklog.commands.output import _TEXT_RENDERERS
        _TEXT_RENDERERS["foo_ls"] = my_custom_renderer
    """
    def decorator(fn):
        _TEXT_RENDERERS[name] = fn
        return fn
    return decorator


class TextFormatter(Formatter):
    """Rich text mode: dispatches to _TEXT_RENDERERS by cmd_name, then falls
    back to the inline _render closure for backward compat.

    emit() is a no-op for plain dicts/None (un-migrated handlers that still
    call out() inline during the handler body work unchanged).
    """

    def emit(self, data) -> None:
        if not isinstance(data, TextRenderable):
            return
        if data.cmd_name and data.cmd_name in _TEXT_RENDERERS:
            _TEXT_RENDERERS[data.cmd_name](data.data)
        elif data._render:
            data.render()


class JSONFormatter(Formatter):
    """JSON mode: suppress out() calls; emit the handler's return value as JSON.

    Mirrors the AWS CLI --output json formatter: structured data only,
    no human-readable decoration. Also owns RFC 9457 error formatting so that
    die() emits structured errors when this formatter is active.
    """

    @staticmethod
    def format_error(msg: str, status: int) -> None:
        """Write an RFC 9457 Problem Details object to stderr."""
        print(json.dumps({
            "type": "about:blank",
            "title": "Error",
            "status": status,
            "detail": str(msg),
        }, ensure_ascii=False), file=sys.stderr)

    def __init__(self) -> None:
        self._saved_error_formatter = None

    def setup(self) -> None:
        self._saved_error_formatter = get_active_error_formatter()
        set_active_error_formatter(JSONFormatter.format_error)
        set_suppress_output(True)

    def teardown(self) -> None:
        set_suppress_output(False)
        set_active_error_formatter(self._saved_error_formatter)

    def emit(self, data) -> None:
        payload = data.data if isinstance(data, TextRenderable) else data
        if is_dataclass(payload) and not isinstance(payload, type):
            payload = asdict(payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=_dc_default))


class JSONLFormatter(JSONFormatter):
    """JSON Lines mode: a top-level array is streamed as one compact JSON object
    per line; any other payload is emitted as a single compact line.

    Same data as -o json, reshaped for line-oriented pipelines (`jq -c`, `grep`,
    a streaming analyzer). Inherits out() suppression + RFC 9457 error formatting
    from JSONFormatter — only the emit() serialisation differs.
    """

    def emit(self, data) -> None:
        payload = data.data if isinstance(data, TextRenderable) else data
        if isinstance(payload, list):
            for item in payload:
                print(json.dumps(item, ensure_ascii=False, default=_dc_default))
        else:
            if is_dataclass(payload) and not isinstance(payload, type):
                payload = asdict(payload)
            print(json.dumps(payload, ensure_ascii=False, default=_dc_default))


class TOONFormatter(JSONFormatter):
    """TOON mode: emit the payload as Token-Oriented Object Notation — a compact,
    LLM-friendly JSON alternative (~40% fewer tokens on uniform arrays). Inherits
    out() suppression + RFC 9457 error formatting from JSONFormatter; only the
    serialisation differs. Encoder lives in worklog.toon (zero-dependency, G3).
    """

    def emit(self, data) -> None:
        from ..toon import encode
        payload = data.data if isinstance(data, TextRenderable) else data
        print(encode(payload))


_FORMATTERS: dict[str, type[Formatter]] = {}


def register_formatter(name: str, cls: type[Formatter]) -> None:
    """Register a custom output formatter for use with -o <name>.

    Example::

        from worklog.commands.output import Formatter, register_formatter

        class TableFormatter(Formatter):
            def emit(self, data) -> None:
                ...  # render data as a table

        register_formatter("table", TableFormatter)

    Call this before the CLI parses arguments (e.g. in a plugin's entry point).
    The name becomes a valid -o value for all @output_format-decorated commands.
    """
    _FORMATTERS[name] = cls


def get_formatter(name: str) -> Formatter:
    """Return a Formatter instance for the given output format name.

    Falls back to TextFormatter for unknown names so new CLI flags never crash.
    """
    return _FORMATTERS.get(name, TextFormatter)()


def output_format(fn):
    """Decorator: apply the active output Formatter to a command handler.

    Text mode: TextRenderable.render() is called by TextFormatter.emit().
    JSON mode: out() calls are suppressed; TextRenderable.data (or the raw
               return value) is emitted as JSON.
    """
    @wraps(fn)
    def wrapper(args, con):
        fmt = get_formatter(getattr(args, "output", "text"))
        fmt.setup()
        try:
            result = fn(args, con)
        finally:
            fmt.teardown()
        fmt.emit(result)

    return wrapper


# Register built-in formatters via the same public API used by external plugins.
register_formatter("text", TextFormatter)
register_formatter("json", JSONFormatter)
register_formatter("jsonl", JSONLFormatter)
register_formatter("toon", TOONFormatter)
