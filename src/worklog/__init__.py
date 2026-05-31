"""worklog: SQLite-backed worklog tool with a todo.sh-style CLI.

The CLI entry point is `worklog.cli:main`, exposed as the `wl` console
script (see [project.scripts] in pyproject.toml).
"""
from .cli import __version__

__all__ = ["__version__"]
