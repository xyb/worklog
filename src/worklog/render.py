"""Rich-based highlighting and node-line rendering for worklog.

Holds the mutable `_CONSOLE` state (set by `_init_console` from main()).
Coloring helpers (`_c`, `_hl`, `out`) read `_CONSOLE` at call time, so the
rest of the codebase doesn't pass a console object around. The trade-off is
that tests reading `_CONSOLE` must do so via `wl.render._CONSOLE` (a live
attribute lookup) rather than `wl._CONSOLE` (an import-time binding that
would not follow mutations).
"""
from __future__ import annotations

import os
import sys

try:
    from rich.console import Console as _RichConsole
    from rich.theme import Theme as _RichTheme
    from rich.markup import escape as _rich_escape
    _RICH_AVAIL = True
except ImportError:
    _RICH_AVAIL = False

# theme = semantic element -> rich style. No "default" theme; default is auto, probes terminal bg and resolves to dark/light/mono.
_THEME_KEYS = "done doing later wait todo canceled pri_a pri_b pri_c id kind tag hit header meta planned clock".split()
THEMES = {
    # dark: dark background, use bright_* for contrast
    "dark": {
        "done": "bright_green", "doing": "bright_yellow", "later": "bright_cyan", "wait": "grey50",
        "todo": "default", "canceled": "strike grey50",
        "pri_a": "bold bright_red", "pri_b": "bright_yellow", "pri_c": "grey50",
        "id": "grey50", "kind": "bright_cyan", "tag": "bright_magenta", "hit": "bold black on bright_yellow",
        "header": "bold bright_white", "meta": "grey50", "planned": "bright_blue", "clock": "bright_green",
    },
    # light: light background, use deep saturated colors (avoid bright/white getting lost on white bg)
    "light": {
        "done": "green4", "doing": "dark_orange3", "later": "blue", "wait": "grey42",
        "todo": "default", "canceled": "strike grey42",
        "pri_a": "bold red3", "pri_b": "dark_orange3", "pri_c": "grey42",
        "id": "grey42", "kind": "dark_cyan", "tag": "purple", "hit": "bold black on yellow3",
        "header": "bold grey15", "meta": "grey42", "planned": "blue", "clock": "green4",
    },
    # mono: no color (want rich layout but no color)
    "mono": {k: "default" for k in _THEME_KEYS},
}
_STATUS_STYLE = {"DONE": "done", "DOING": "doing", "LATER": "later", "WAIT": "wait",
                 "TODO": "todo", "DEFERRED": "later", "CANCELED": "canceled", None: "todo"}
_PRI_STYLE = {"A": "pri_a", "B": "pri_b", "C": "pri_c"}

_CONSOLE = None  # initialized by main() based on --color/--theme; None = plain text


def _resolve_color(mode):
    if mode is None:
        mode = os.environ.get("WORKLOG_COLOR", "auto")
    if mode == "never":
        return False
    if mode == "always":
        return True
    return _RICH_AVAIL and sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def _detect_bg_is_dark():  # pragma: no cover -- TTY/escape-seq probe, not unit-tested at integration layer
    """Detect terminal bg: True=dark / False=light / None=unknown.
    First check $COLORFGBG (no I/O), then query OSC 11 (requires TTY, short timeout)."""
    fgbg = os.environ.get("COLORFGBG")
    if fgbg and ";" in fgbg:
        try:
            bg = int(fgbg.split(";")[-1])
            return bg not in (7, 15)  # 7/15 = light bg, others treated as dark
        except ValueError:
            pass
    if not (sys.stdout.isatty() and sys.stdin.isatty()):
        return None
    try:
        import termios, tty, select, re
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            sys.stdout.write("\033]11;?\033\\")
            sys.stdout.flush()
            resp = ""
            if select.select([fd], [], [], 0.15)[0]:
                resp = os.read(fd, 64).decode("latin-1", "ignore")
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        m = re.search(r"rgb:([0-9a-fA-F]+)/([0-9a-fA-F]+)/([0-9a-fA-F]+)", resp)
        if not m:
            return None
        r, g, b = (int(m.group(i)[:2], 16) for i in (1, 2, 3))  # take top 2 hex digits per channel
        return (0.299 * r + 0.587 * g + 0.114 * b) / 255 < 0.5  # perceived brightness < 0.5 = dark
    except (ValueError, AttributeError):
        # int(..., 16) parse failure / m.group out of range -> undetectable, treat as unknown
        return None


def _resolve_theme(name):
    """Resolve theme name to a real palette name. auto (default): probe bg -> dark/light, fallback dark if unknown."""
    if name in THEMES:
        return name  # explicit real theme
    # name is None / "auto" / unknown -> auto-detect
    dark = _detect_bg_is_dark()
    if dark is False:
        return "light"
    return "dark"  # dark or unknown -> use dark (most terminals have dark bg)


def _init_console(color_mode, theme_name):
    global _CONSOLE
    if not _resolve_color(color_mode) or not _RICH_AVAIL:
        _CONSOLE = None
        return
    name = _resolve_theme(theme_name or os.environ.get("WORKLOG_THEME"))
    force = True if color_mode == "always" else None
    _CONSOLE = _RichConsole(theme=_RichTheme(THEMES[name]), force_terminal=force, highlight=False, soft_wrap=True)
    # terminal without color support (TERM=dumb etc.) -> effectively mono, rich won't emit ANSI


def out(s):
    """Unified output: when highlighting is enabled, use rich (markup rendering); otherwise plain print."""
    if _CONSOLE is not None:
        _CONSOLE.print(s)
    else:
        print(s)


def _c(text, style=None):
    """Color a fragment: returns rich markup when enabled (content escaped to prevent injection), otherwise plain text."""
    t = str(text)
    if _CONSOLE is None:
        return t
    t = _rich_escape(t)
    return f"[{style}]{t}[/{style}]" if style else t


def _hl(text, q):
    """In a string, mark query matches (styled: hit style / plain: *…*). No match -> plain _c."""
    text = str(text)
    if not q:
        return _c(text)
    i = text.lower().find(q.lower())
    if i < 0:
        return _c(text)
    mid = text[i:i + len(q)]
    pre, post = text[:i], text[i + len(q):]
    if _CONSOLE is None:
        return pre + f"*{mid}*" + post
    return _c(pre) + _c(mid, "hit") + _c(post)
