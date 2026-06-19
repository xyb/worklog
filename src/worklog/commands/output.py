"""Shared output helpers for all command handlers.

The @output_format decorator is the standard way to add -o json support,
following the AWS CLI pattern: handlers return data, the output layer decides
how to render it.

    @output_format
    def cmd_foo(args, con):
        # ... do work ...
        out("✓ human-readable line")   # auto-suppressed in JSON mode
        return {"id": ..., "key": ...} # auto-emitted as JSON in JSON mode

Text output via out() is the default formatter. JSON mode is selected with -o json.
"""
from __future__ import annotations

import json
from functools import wraps

from ..render import set_suppress_output, set_json_mode


def _emit_json(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def output_format(fn):
    """Decorator: output formatting layer for command handlers.

    In JSON mode (-o json): suppresses out() calls, emits the handler's return
    value as JSON (including null). In text mode: passthrough.
    """
    @wraps(fn)
    def wrapper(args, con):
        if getattr(args, "output", "text") == "json":
            set_json_mode(True)
            set_suppress_output(True)
            try:
                result = fn(args, con)
            finally:
                set_suppress_output(False)
                set_json_mode(False)
            _emit_json(result)
        else:
            fn(args, con)

    return wrapper
