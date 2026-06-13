"""worklog command: `wl alias` group — manage command aliases in ~/.config/worklog/aliases.ini.

Aliases are loaded into the argparse subparser `aliases=` at startup, so edits take effect on
the NEXT wl invocation."""
from __future__ import annotations

import sys

from ..render import _c, out
from ..xdg import _resolve_aliases_path

# Lazy access to the cli module (for HANDLERS) — at call time, to avoid the cli ↔ commands cycle.
from .. import cli as _cli  # noqa: E402


def _read_aliases_cfg():
    """(ConfigParser, Path) for the aliases file; case-preserving (optionxform=str) so an
    alias name keeps its exact spelling. Ensures an [aliases] section exists."""
    import configparser
    cfg = configparser.ConfigParser()
    cfg.optionxform = str
    p = _resolve_aliases_path()
    if p.exists():
        cfg.read(p, encoding="utf-8")
    if "aliases" not in cfg:
        cfg["aliases"] = {}
    return cfg, p


def _write_aliases_cfg(cfg, p):
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        cfg.write(f)


def cmd_alias_ls(args, con):
    """List configured command aliases (name → target)."""
    cfg, p = _read_aliases_cfg()
    items = sorted(cfg["aliases"].items())
    if not items:
        out(_c(f"(no aliases configured — file: {p})", "meta"))
        return
    for name, target in items:
        out("  " + _c(name, "id") + _c(" → ", "meta") + _c(target))


def cmd_alias_add(args, con):
    """Add/update a command alias. The target may carry arguments — `wl alias add w "day -t work"`
    makes `wl w` == `wl day -t work`; `wl alias add d day` makes `wl d` == `wl day`. The target's
    first word must be a real wl command, and an alias can't shadow an existing command. Takes
    effect on the next wl invocation (aliases are wired into the parser at startup)."""
    name, target = args.name.strip(), args.target.strip()
    if not name or not target:
        sys.exit("✗ alias name and target are both required")
    valid = set(_cli.HANDLERS)
    cmd = target.split()[0]   # the target may carry args; only its first word is the command
    if cmd not in valid:
        sys.exit(f"✗ unknown command '{cmd}' — an alias target must start with a wl command")
    if name in valid:
        sys.exit(f"✗ '{name}' is already a wl command — an alias can't shadow it")
    cfg, p = _read_aliases_cfg()
    cfg["aliases"][name] = target
    _write_aliases_cfg(cfg, p)
    out(_c(f"✓ alias '{name}' → '{target}' (takes effect on the next wl run)", "meta"))


def cmd_alias_rm(args, con):
    """Remove a command alias."""
    name = args.name.strip()
    cfg, p = _read_aliases_cfg()
    if name not in cfg["aliases"]:
        out(_c(f"(no alias '{name}')", "meta"))
        return
    del cfg["aliases"][name]
    _write_aliases_cfg(cfg, p)
    out(_c(f"✓ alias '{name}' removed (takes effect on the next wl run)", "meta"))


def cmd_alias(args, con):
    """Dispatch `wl alias <add|ls|rm>` — manage command aliases in aliases.ini."""
    sub = getattr(args, "alias_sub", None)
    if sub is None:
        sys.exit("✗ usage: wl alias <add|ls|rm> … (see `wl alias --help`)")
    {"add": cmd_alias_add, "ls": cmd_alias_ls, "rm": cmd_alias_rm}[sub](args, con)
