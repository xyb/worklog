"""Rich-based highlighting and node-line rendering for worklog.

Holds the mutable `_CONSOLE` state (set by `_init_console` from main()).
Coloring helpers (`_c`, `_hl`, `out`) read `_CONSOLE` at call time, so the
rest of the codebase doesn't pass a console object around. The trade-off is
that tests reading `_CONSOLE` must do so via `wl._CONSOLE` (a live
attribute lookup) rather than `wl._CONSOLE` (an import-time binding that
would not follow mutations).
"""
from __future__ import annotations

import os
import re
import sys
from typing import NoReturn

try:
    from rich.console import Console as _RichConsole
    from rich.theme import Theme as _RichTheme
    from rich.markup import escape as _rich_escape
    from rich.style import Style as _RichStyle
    _RICH_AVAIL = True
except ImportError:
    _RICH_AVAIL = False

# theme = semantic element -> rich style. No "default" theme; default is auto, probes terminal bg and resolves to dark/light/mono.
_THEME_KEYS = "done doing later wait todo canceled pri_a pri_b pri_c id type tag hit header meta planned clock body title italic underline derived".split()
THEMES = {
    # dark: dark background, use bright_* for contrast
    "dark": {
        "done": "bright_green", "doing": "bright_yellow", "later": "bright_cyan", "wait": "grey50",
        "todo": "default", "canceled": "strike grey50",
        "pri_a": "bold bright_red", "pri_b": "bright_yellow", "pri_c": "grey50",
        "id": "green", "type": "bright_cyan", "tag": "bright_magenta", "hit": "bold black on bright_yellow",
        "header": "bold bright_white", "meta": "grey50", "planned": "bright_blue", "clock": "bright_green",
        "body": "grey70",   # help prose: slightly grey so bold-white + colored refs stand out
        "title": "bold bright_white underline",   # wl help topic title (underline replaces the ─ rule)
        "italic": "italic", "underline": "underline",   # md *italic* / [links]; mono→default (plain)
        "derived": "italic grey50",   # machine-derived/computed rows (e.g. =backrels) — italic + dim, set apart from stored props
    },
    # light: light background, use deep saturated colors (avoid bright/white getting lost on white bg)
    "light": {
        "done": "green4", "doing": "dark_orange3", "later": "blue", "wait": "grey42",
        "todo": "default", "canceled": "strike grey42",
        "pri_a": "bold red3", "pri_b": "dark_orange3", "pri_c": "grey42",
        "id": "dark_green", "type": "dark_cyan", "tag": "purple", "hit": "bold black on yellow3",
        "header": "bold grey15", "meta": "grey42", "planned": "blue", "clock": "green4",
        "body": "grey30",   # help prose: slightly grey so bold + colored refs stand out
        "title": "bold grey15 underline",   # wl help topic title (underline replaces the ─ rule)
        "italic": "italic", "underline": "underline",   # md *italic* / [links]; mono→default (plain)
        "derived": "italic grey42",   # machine-derived/computed rows (=backrels) — italic + dim
    },
    # mono: no color (want rich layout but no color)
    "mono": {k: "default" for k in _THEME_KEYS},
}
THEMES["mono"]["derived"] = "italic"   # mono keeps italic (no color) so derived rows still read apart
_STATUS_STYLE = {"DONE": "done", "DOING": "doing", "LATER": "later", "WAIT": "wait",
                 "TODO": "todo", "DEFERRED": "later", "CANCELED": "canceled", None: "todo"}
_PRI_STYLE = {"A": "pri_a", "B": "pri_b", "C": "pri_c"}

_CONSOLE = None  # initialized by main() based on --color/--theme; None = plain text


def is_plain():
    """True when output is plain text (no rich console) — i.e. piped / NO_COLOR / --color never /
    rich missing. Read live (the global is set in main()). Callers use it to emit FULL, untruncated
    output in plain mode (a script/grep needs the whole value), and only abbreviate for the TTY."""
    return _CONSOLE is None


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


# `--help` wrap width: the terminal width (minus argparse's 2-col margin), but capped so help
# stays readable on very wide terminals. Both the argparse HelpFormatter (via _WlHelpFormatter)
# and the epilog wrapper in colorize_help use this single value, so their wraps line up.
HELP_MAX_WIDTH = 100


def help_width():
    """The width `--help` wraps to: min(terminal - 2, HELP_MAX_WIDTH), floored at 11 (argparse's
    own floor). Matches argparse.HelpFormatter's `_width` derivation, then caps it."""
    import shutil
    return max(11, min(shutil.get_terminal_size().columns - 2, HELP_MAX_WIDTH))


def help_palette(color_mode=None, theme_name=None):
    """The theme dict to colorize `--help` output with, or None when color should be off.

    `wl --help` / `wl <cmd> --help` render *before* main() builds `_CONSOLE` (argparse fires
    its help action mid-parse), so the help colorizer can't read `_CONSOLE` — it resolves
    color + theme here the same way `_init_console` does (env / TTY / --color / --theme),
    then styles the returned string with raw ANSI via `style_ansi`."""
    if not _RICH_AVAIL or not _resolve_color(color_mode):
        return None
    name = _resolve_theme(theme_name or os.environ.get("WORKLOG_THEME"))
    return THEMES[name]


def style_ansi(text, style_str):
    """Wrap `text` in raw ANSI for a rich style string (e.g. "bold bright_white", "grey70").
    Used for help text returned as a string, independent of any live `_CONSOLE`. A falsy or
    "default" style (the mono theme) is a no-op, so mono help comes out as plain text."""
    if not _RICH_AVAIL or not style_str or style_str == "default" or not text:
        return text
    return _RichStyle.parse(style_str).render(text)


_SUPPRESS_DEPTH = 0  # ponytail: counter not bool, supports nested @output_format calls
_active_error_formatter = None  # (msg: str, status: int) -> None; set by Formatter.setup()


def set_suppress_output(flag: bool) -> None:
    """Enable/disable output suppression (used by @output_format in JSON mode)."""
    global _SUPPRESS_DEPTH
    _SUPPRESS_DEPTH += 1 if flag else -1


def get_active_error_formatter():
    """Return the current error-formatting callback (None = plain text)."""
    return _active_error_formatter


def set_active_error_formatter(fn) -> None:
    """Register the error-formatting callback for die().

    Formatters should save the previous value in setup() and restore it in
    teardown() so nested formatters (e.g. main() + @output_format) don't
    clobber each other's state.
    """
    global _active_error_formatter
    _active_error_formatter = fn


def die(msg: str, *, status: int = 400) -> NoReturn:
    """Exit with a user-facing error.

    Delegates formatting to the active Formatter's error handler (set via
    set_active_error_formatter). Defaults to plain '✗ <msg>' to stderr.
    """
    if _active_error_formatter is not None:
        _active_error_formatter(msg, status)
    else:
        print(f"✗ {msg}", file=sys.stderr)
    sys.exit(1 if status < 500 else 2)


def dispatch_group(args, con, attr, table, usage=None, default=None):
    """Route `wl <group> <sub>` to its handler — the single source for the entity-group
    dispatcher every group (tag / clock / prop / link / metric / goal / agent / …) repeats.
    `attr` is the argparse dest holding the chosen sub-verb; `table` maps sub-verb → handler.
    A bare group (sub is None) runs `default(args, con)` when given, else dies with `usage`.
    Sub-verbs are argparse `choices`, so an unknown sub can't reach here — `table[sub]` is total
    over real inputs. Returns the handler's result so `-o json` / TextRenderable propagate.

    A new group is a routing table + one call: `return dispatch_group(args, con, "x_sub", {...},
    usage="…")`. A default verb that aliases the bare form (e.g. goal's `today`) is just another
    table entry pointing at the same handler as `default=`."""
    sub = getattr(args, attr, None)
    if sub is None:
        if default is not None:
            return default(args, con)
        die(usage)
    return table[sub](args, con)


def out(s):
    """Unified output: when highlighting is enabled, use rich (markup rendering); otherwise plain print."""
    if _SUPPRESS_DEPTH > 0:
        return
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


def _hl_terms(text, terms):
    """Mark every occurrence of any term (case-insensitive) — styled hit / plain `*…*`.
    Unlike `_hl` (one contiguous substring), this highlights each query term separately,
    so a multi-word / non-contiguous query (`web server config`, `spawn-tab skill`) still lights up
    the parts that do appear. Longest terms win at a shared position."""
    text = str(text)
    terms = [t for t in terms if t]
    if not terms:
        return _c(text)
    pat = re.compile("|".join(re.escape(t) for t in sorted(set(terms), key=len, reverse=True)),
                     re.IGNORECASE)
    mark = (lambda s: f"*{s}*") if _CONSOLE is None else (lambda s: _c(s, "hit"))
    parts, last = [], 0
    for m in pat.finditer(text):
        if m.start() > last:
            parts.append(_c(text[last:m.start()]))
        parts.append(mark(m.group(0)))
        last = m.end()
    if last < len(text):
        parts.append(_c(text[last:]))
    return "".join(parts)


def _detail_line(label, content, *, indent="    "):
    """One indented detail line shown UNDER a node line: ``<indent><label> <content>`` with the
    label dimmed (the "meta" style). The single building block for the match/snippet rows that
    `wl find` (``body:`` / ``log:`` / ``tag:`` …) and `wl query` (``↳ <field>:``) print, so every
    command renders its sub-lines identically instead of re-inlining the indent+label format.
    ``content`` is passed through already-rendered (e.g. via `_snippet` / `_hl` / `_hl_terms`)."""
    return indent + _c(label, "meta") + " " + content


# --- node-line rendering (extracted from cli.py) ---
from .helpers import (
    _status_marker, _sched_display, _fmt_dur,
    _term_width, _wrap_display, _title_mode, _truncate_log_body, _display_width,
)
from .queries import _has_tag, _node_clock_min, _node_tags, node_type


def _pri_marker(priority):
    """The 4-col priority marker — `[#A]`/`[#B]`/`[#C]` styled by priority, or a muted `[# ]`
    when unset. The single source for priority display (DESIGN §6/§19): never blank (blanks
    mis-aligned by one column against `[#A]`) and never `[ ]` (that's the TODO status marker).
    Every list/header that shows a node's priority routes through this — do not roll your own."""
    if priority:
        return _c(f"[#{priority}]", _PRI_STYLE.get(priority))
    return _c("[# ]", "meta")


def _pri_plain(priority):
    """Plain-text twin of `_pri_marker` (no styling): ``[#A]`` / ``[# ]`` when unset. Used for
    hang-indent WIDTH math, where only the column count matters, not the color — the single
    source for that string so the unset marker can't drift between renderers."""
    return f"[#{priority}]" if priority else "[# ]"


def _node_activity_prefix(n, nid, indent, *, done=False):
    """The ``<indent><marker> #<id> <pri> `` prefix the day / activity renderers print before a
    node's title, then feed to `_hang_wrap`. NOTE this is the activity layout (id BEFORE priority,
    no type tag) — distinct from `_node_line`'s list layout (priority before id) — shared by
    `_print_day_activity` and `_render_day_group` so the two can't drift. ``nid`` is passed
    explicitly (the callers key on it; their row ``n`` may not carry an ``id`` column). ``done=True``
    forces the ``[x]`` check-marker (a habit checked-in that day). Returns ``(styled_prefix,
    prefix_cols)``, where prefix_cols is the plain display width for the hang indent."""
    if done:
        mk_txt, mk = "[x]", _c("[x]", "done")
    else:
        mk_txt = _status_marker(n["status"])
        mk = _c(mk_txt, _STATUS_STYLE.get(n["status"], "todo"))
    prefix = indent + mk + " " + _c(f"#{nid}", "id") + " " + _pri_marker(n["priority"]) + " "
    prefix_cols = _display_width(f"{indent}{mk_txt} #{nid} {_pri_plain(n['priority'])} ")
    return prefix, prefix_cols


def _hang_wrap(prefix, prefix_cols, title, *, hl=None, style=None, tail="", tail_cols=0):
    """Render `prefix` + a node `title` that wraps per the title mode (the single wrap utility,
    shared by `_node_line` and the day/tree custom renderers so they all behave the same).
    `prefix` is the styled left part of the line; `prefix_cols` is the display width of its PLAIN
    text. `wrap` (default): fold the title, continuation lines hang-indented to `prefix_cols`;
    `clip`: one line truncated with `…`. `style` themes the title text (e.g. `meta` to dim a
    relation's title as auxiliary info); default = normal.

    `tail` is the styled trailing suffix the caller appends (e.g. `«planned·not-done»` / clock /
    `(this month N/M)`); `tail_cols` is its PLAIN display width. The tail rides the last title
    line, but if it wouldn't fit it gets its own hang-indented continuation line — so it never
    spills to column 0 (the bug when callers blindly appended suffixes ignoring the wrap width)."""
    # hl: a list/tuple of terms → per-term highlight (`_hl_terms`, lights non-contiguous matches);
    # a plain string → one contiguous substring (`_hl`); falsy → no highlight.
    if isinstance(hl, (list, tuple)):
        render = lambda t: _hl_terms(t, hl)
    elif hl:
        render = lambda t: _hl(t, hl)
    else:
        render = lambda t: _c(t, style)
    avail = _term_width() - prefix_cols
    if _title_mode() == "clip":
        return prefix + render(_truncate_log_body(title, indent_cols=prefix_cols + tail_cols)) + tail
    wlines = _wrap_display(title, avail)
    cont = " " * prefix_cols
    if tail and tail_cols and _display_width(wlines[-1]) + tail_cols > avail:
        wlines.append("")   # tail doesn't fit the last title line → give it its own hung line
    s = prefix + render(wlines[0])
    for ln in wlines[1:]:
        s += "\n" + cont + render(ln)
    return s + tail


def _node_line(con, n, *, indent="", done=False, show_type=True, tags=False, planned=False, clock=True, sched=False, hl=None):
    """Unified node-line rendering (sole source per DESIGN.md §6).

    Format: <indent><marker> [#pri] #<id> [type] <title>[ ·planned][ @sched][ [Xh Ym]][ :tags:]
    Everywhere that "lists tasks" goes through this; do not roll your own. hl=query highlights matches in title (used by find).
    clock defaults True: shows total duration [Xh Ym] when there's a CLOCK or log span; 0 hides it.
    """
    mk = "✓" if done else _status_marker(n["status"])
    marker = _c(mk, "done" if done else _STATUS_STYLE.get(n["status"], "todo"))
    pri = _pri_marker(n["priority"])
    pri_plain = _pri_plain(n["priority"])
    ntype = node_type(con, n)   # single representative type token, derived from type.* props
    type_plain = f"[{ntype}] " if (show_type and ntype != "task") else ""
    type_str = (_c(type_plain.rstrip(), "type") + " ") if type_plain else ""
    nid = _c(f"#{n['id']}", "id")
    prefix = f"{indent}{marker} {pri} {nid} {type_str}"
    # hanging-indent column = display width of the plain prefix (everything left of the title).
    # Continuation lines (wrap mode) align here so a long title doesn't break tree indentation.
    prefix_cols = _display_width(f"{indent}{mk} {pri_plain} #{n['id']} {type_plain}")
    # accumulate trailing suffixes as one tail (styled + its plain width) so _hang_wrap can keep
    # the whole line within the terminal — a long title no longer pushes the suffix off the edge
    tail, tail_plain = "", ""
    if planned and _has_tag(con, n["id"], "planned"):
        tail += " " + _c("·planned", "planned"); tail_plain += " ·planned"
    if sched and n["scheduled_date"]:
        sd = "@" + _sched_display(n["scheduled_date"])
        tail += " " + _c(sd, "planned"); tail_plain += " " + sd
    if clock:
        d = _fmt_dur(_node_clock_min(con, n["id"]))
        if d:
            tail += " " + _c(d, "clock"); tail_plain += " " + d
    if tags:
        tl = _node_tags(con, n["id"])
        if tl:
            t = f":{':'.join(tl)}:"; tail += "  " + _c(t, "tag"); tail_plain += "  " + t
    return _hang_wrap(prefix, prefix_cols, n["title"], hl=hl, tail=tail, tail_cols=_display_width(tail_plain))

_RELATION_LABEL_W = 12  # widest label is "=blocked-by:" / "=split-from:" (12 cols); keeps the type column aligned
# own (stored) relation types, plain label, no `=` — rendered first, in this order
_OWN_RELATION_TYPES = ("block", "split", "related")
# derived (computed, never stored) reverse views — `=` prefix + italic/dim, rendered after
# the stored ones. No entry for `related`'s reverse: it folds into `=backrels` instead.
_DERIVED_RELATION_LABELS = {"blocked_by": "=blocked-by", "split_from": "=split-from"}


def _relations_lines(con, rel, backrels=None, indent=2, ready_view=None):
    """Render a node's connections under a `relation:` sub-block (named for the `relation.*`
    prop namespace they're stored in — in `wl show` this nests under `props:`, since relations
    ARE props, just with their own richer display). First the STORED relation.* props (block /
    split / related), each node-per-line with title (width-aware via `_hang_wrap`). Then the
    DERIVED reverse rows (`=blocked-by` / `=split-from`) and `=backrels` (text mentions +
    one-sided `related` edges) — none of these are a stored prop, so each is marked with a
    leading `=` + italic/dim to set it apart from the real, stored relations above. Finally, if
    `ready_view` is given (a `(ready, waiting)` pair from `graph.node_ready_view`),
    two more computed rows: `=ready` (bool) and `=waiting` (the direct blockers still open —
    empty when ready). `ready_view=None` (the node has no block edge at all) omits both rows
    entirely, same as any other empty section. `indent` = column of the `relation:` header (rows
    sit at indent+2). Returns [] when there's nothing to show. Shared by `wl relation` /
    `wl show` (only `wl show` passes `ready_view` — see its call site)."""
    from .models import Node
    backrels = backrels or []
    if not any(rel.values()) and not backrels and ready_view is None:
        return []
    pad, rowpad = " " * indent, " " * (indent + 2)
    all_rel_ids = [i for t in _OWN_RELATION_TYPES for i in (rel.get(t) or [])]
    all_rel_ids += [i for t in _DERIVED_RELATION_LABELS for i in (rel.get(t) or [])]
    node_cache = {n.id: n for n in Node.gets(con, all_rel_ids) if n}
    lines = [pad + _c("relation:", "meta")]
    for t in _OWN_RELATION_TYPES:
        for k, i in enumerate(rel.get(t) or []):
            n = node_cache.get(i)
            title = n["title"] if n else "?"
            label = f"{t + ':':{_RELATION_LABEL_W}}" if k == 0 else " " * _RELATION_LABEL_W
            nid = f"#{i}"
            prefix = rowpad + _c(label, "meta") + " " + _c(nid, "id") + " "
            prefix_cols = _display_width(rowpad + label + " " + nid + " ")
            # title is auxiliary (the #id is the reference) — dim it grey
            lines.append(_hang_wrap(prefix, prefix_cols, title, style="meta"))
    for t, derived_label in _DERIVED_RELATION_LABELS.items():
        for k, i in enumerate(rel.get(t) or []):
            n = node_cache.get(i)
            title = n["title"] if n else "?"
            label = f"{derived_label + ':':{_RELATION_LABEL_W}}" if k == 0 else " " * _RELATION_LABEL_W
            nid = f"#{i}"
            prefix = rowpad + _c(label, "derived") + " " + _c(nid, "id") + " "
            prefix_cols = _display_width(rowpad + label + " " + nid + " ")
            lines.append(_hang_wrap(prefix, prefix_cols, title, style="derived"))
    if backrels:
        # leading '=' + italic/dim marks this as a derived (computed) row, not a stored prop
        label = f"{'=backrels':{_RELATION_LABEL_W}}"
        lines.append(rowpad + _c(label, "derived") + " "
                     + " ".join(_c(f"#{i}", "id") for i in backrels))
    if ready_view is not None:
        ready, waiting = ready_view
        label = f"{'=ready:':{_RELATION_LABEL_W}}"
        lines.append(rowpad + _c(label, "derived") + " " + _c(str(ready).lower(), "derived"))
        label = f"{'=waiting:':{_RELATION_LABEL_W}}"
        tail = " ".join(_c(f"#{i}", "id") for i in waiting) if waiting else _c("(none)", "derived")
        lines.append(rowpad + _c(label, "derived") + " " + tail)
    return lines


def _snippet(text, q, ctx=30):
    """Extract a snippet around the query, with the match highlighted (styled) / *…* marked (plain)."""
    i = text.lower().find(q.lower())
    if i < 0:
        return _c(text[:80] + ("…" if len(text) > 80 else ""))
    a, b = max(0, i - ctx), min(len(text), i + len(q) + ctx)
    mid = text[i:i + len(q)]
    pre = ("…" if a > 0 else "") + text[a:i]
    post = text[i + len(q):b] + ("…" if b < len(text) else "")
    if _CONSOLE is None:
        return pre + f"*{mid}*" + post
    return _c(pre) + _c(mid, "hit") + _c(post)



def _print_truncation_hint(shown, total, extra=""):
    """Print `(showing N/total[, extra])` hint when truncated; print nothing otherwise."""
    if shown < total:
        msg = f"(showing {shown}/{total}"
        if extra:
            msg += f", {extra}"
        msg += ")"
        out(_c(msg, "meta"))


def _group_header(title, *, style="header", pri=None, node_id=None, suffix=None, lead="\n"):
    """Emit one `▸ ` group-section header — the single shape behind every grouped view
    (`wl day` sections, `--by project/direction`, `changes`, `summary`, `projects`). An optional
    `#id` and the 4-col priority marker prefix the styled title; `suffix` (caller-worded, e.g.
    `"(done 3 / pending 2)"` or `"(5)"`) trails dimmed. `lead` precedes the glyph — a blank line by
    default, `""` or an indent for a nested/flush header. Callers own the suffix WORDING; this owns
    the glyph, spacing, and `id/pri/title/suffix` styling so the six call sites can't drift apart."""
    parts = []
    if node_id is not None:
        parts.append(_c(f"#{node_id}", "id"))
    if pri is not None:
        parts.append(_pri_marker(pri))
    parts.append(_c(title, style))
    line = lead + "▸ " + " ".join(parts)
    if suffix:
        line += "  " + _c(suffix, "meta")
    out(line)


def _log_body_row(body, indent, *, full=False):
    """Emit one dimmed log line under a node — `<indent>· <body>`, the body truncated to one
    line. `indent_cols` is `len(indent) + 2` for the `· ` glyph (the relationship the two
    day/activity call sites used to hand-compute, once as a hard-coded `10` for an 8-space
    indent)."""
    shown = _truncate_log_body(body, indent_cols=len(indent) + 2, full=full)
    out(indent + _c("· " + shown, "meta"))
