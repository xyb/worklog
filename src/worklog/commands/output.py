"""Shared output helpers for all command handlers.

The @output_format decorator applies the active Formatter to a command handler,
following the AWS CLI pattern: the handler returns structured data; the Formatter
decides how to render it.

    @output_format
    def cmd_foo(args, con):
        result = {"id": ..., "key": ...}
        def _render():
            out("✓ human-readable line")   # called by TextFormatter.emit()
        return TextRenderable(result, _render)

Add a new output format: subclass Formatter and register it in _FORMATTERS.
"""
from __future__ import annotations

import json
import sys
from functools import wraps

from ..render import set_suppress_output, set_active_error_formatter, get_active_error_formatter


class TextRenderable:
    """Carries structured data (for JSON) and a text renderer (for text mode).

    Return from @output_format handlers to separate data from presentation:
      - TextFormatter.emit() calls .render() to produce human-readable output
      - JSONFormatter.emit() serialises .data, never calls .render()

    The render function is typically a closure that captures handler-local
    context (computed labels, sibling rows, etc.) without polluting .data.
    """
    __slots__ = ("data", "_render")

    def __init__(self, data, render):
        self.data = data
        self._render = render

    def render(self):
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


class TextFormatter(Formatter):
    """Rich text mode: out() calls in the render function flow to the terminal.

    emit() calls TextRenderable.render() when the handler returned one;
    plain dicts/None are ignored (backward-compat for un-migrated handlers
    that still call out() inline during the handler body).
    """

    def emit(self, data) -> None:
        if isinstance(data, TextRenderable):
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
        print(json.dumps(payload, ensure_ascii=False, indent=2))


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
