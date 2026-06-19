"""Shared output helpers for all command handlers.

The @output_format decorator applies the active Formatter to a command handler,
following the AWS CLI pattern: the handler returns structured data; the Formatter
decides how to render it.

    @output_format
    def cmd_foo(args, con):
        out("✓ human-readable line")   # TextFormatter lets this through; JSONFormatter suppresses
        return {"id": ..., "key": ...} # JSONFormatter emits this; TextFormatter ignores it

Add a new output format: subclass Formatter and register it in _FORMATTERS.
"""
from __future__ import annotations

import json
import sys
from functools import wraps

from ..render import set_suppress_output, set_active_error_formatter


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
    """Rich text mode: out() calls flow to the terminal; return value is unused.

    All three lifecycle methods are no-ops — the handler does its own rendering
    inline via out(). TextFormatter is the extension point for future text-side
    post-processing (pagination, colour themes, default fallback rendering).
    """


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

    def setup(self) -> None:
        set_active_error_formatter(JSONFormatter.format_error)
        set_suppress_output(True)

    def teardown(self) -> None:
        set_suppress_output(False)
        set_active_error_formatter(None)

    def emit(self, data) -> None:
        print(json.dumps(data, ensure_ascii=False, indent=2))


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

    Text mode: out() calls flow normally; return value is ignored.
    JSON mode:  out() calls are suppressed; return value is emitted as JSON.
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


def set_json_error_mode(flag: bool) -> None:
    """For cli.py main(): activate JSON error formatting for group dispatchers.

    @output_format handlers manage this automatically via Formatter.setup/teardown.
    This covers the gap where die() is called before a decorated handler runs
    (e.g. group dispatchers like cmd_node that dispatch based on a sub-command arg).
    """
    set_active_error_formatter(JSONFormatter.format_error if flag else None)


# Register built-in formatters via the same public API used by external plugins.
register_formatter("text", TextFormatter)
register_formatter("json", JSONFormatter)
