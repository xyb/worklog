"""Shared output helpers for all command handlers."""
from __future__ import annotations

import json


def _is_json(args) -> bool:
    return getattr(args, "output", "text") == "json"


def _emit_json(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))
