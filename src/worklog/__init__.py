"""worklog: SQLite-backed worklog tool with a todo.sh-style CLI.

The CLI entry point is `worklog.cli:main`, exposed as the `wl` console
script (see [project.scripts] in pyproject.toml).
"""
from .cli import __version__
from .commands.output import (
    Formatter,
    TextFormatter,
    JSONFormatter,
    register_formatter,
    set_json_error_mode,
)

__all__ = [
    "__version__",
    "Formatter",
    "TextFormatter",
    "JSONFormatter",
    "register_formatter",
    "set_json_error_mode",
]
