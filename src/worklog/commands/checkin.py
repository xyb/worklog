"""worklog command: `wl checkin` — interactive multi-habit check-in (+ its TUI helpers)."""
from __future__ import annotations

from .. import render
from .. import timeutil as _tu
from .. import db_table as _db
from .metric import checkin_metric
from ..queries import _has_checkin, _insert_log, node_kind
from ..render import _c, out
from .views import _scheduled_node_ids


def cmd_checkin(args, con):
    """Interactive check-in for today's habits.
    Default: multi-select (up/down + space + Enter), pick all at once and check in.
    --per-item: per-item prompt mode (allows per-item note; also the fallback for non-TTY / piped input)."""
    rows, today, kinds = _checkin_collect(con, args)
    if not rows:
        out(_c(f"(no {'/'.join(kinds)} scheduled to check in for {today})", "meta"))
        return

    pending = [r for r in rows if not r["already"]]
    pre_done = len(rows) - len(pending)

    if not pending:
        out(_c(f"all {len(rows)}/{len(rows)} already checked in for {today} ✓", "done"))
        return

    if getattr(args, "per_item", False) or not _is_interactive_tty():
        _checkin_per_item(con, rows)
        return

    header = _c(f"{today} · pick habits done today (already checked in {pre_done}/{len(rows)})", "header")
    # default unselected for all (use space to toggle on what you did); intuitive: 'mark what I did' not 'unmark what I missed'
    options = [(f"#{r['id']} {r['title']}", False) for r in pending]
    chosen = _multi_select_tty(options, header)
    if chosen is None:
        out(_c("(canceled, no changes made)", "meta"))
        return

    for i in chosen:
        nid = pending[i]["id"]
        log_id = _insert_log(con, nid, "✓ done")
        checkin_metric(con, log_id, nid, today)
    con.commit()
    done_now = len(chosen)
    skipped = len(pending) - done_now
    out(_c(
        f"done {pre_done + done_now}/{len(rows)} · new this run {done_now}" +
        (f" · skipped {skipped}" if skipped else "") +
        " · for detailed notes use `wl tick <id> --note ...` or `wl checkin --per-item`",
        "header"))


def _checkin_collect(con, args):
    """Collect today's habits to check in. Returns [{id, title, priority, kind, already}]."""
    today = _tu.today()
    sched_ids = _scheduled_node_ids(con, today)
    kinds = {"habit"}
    if args.all_kinds:
        kinds = {"habit", "task", "meetlog"}

    rows = []
    for nid in sorted(sched_ids):
        n = _db.get(con, "node", nid)
        if not n:
            continue
        nk = node_kind(con, n)
        if nk not in kinds:
            continue
        if n["status"] == "CANCELED" and not getattr(args, "show_canceled", False):
            continue
        # "already done today" = structured check-in metric (not "any log that day")
        already = _has_checkin(con, nid, today)
        rows.append({
            "id": n["id"], "title": n["title"], "priority": n["priority"],
            "kind": nk, "already": bool(already),
        })
    return rows, today, kinds


def _is_interactive_tty():
    """Whether we can run a raw-mode TUI: both stdin and stdout are TTYs. Used by tests via monkeypatch."""
    import sys
    return sys.stdin.isatty() and sys.stdout.isatty()


def _multi_select_tty(options, header):  # pragma: no cover -- TTY interactive, needs termios+os.read, manual smoke only
    """Terminal multi-select widget (rich.Live render, no misalignment): up/down moves cursor; space toggles; Enter confirms; q/Esc cancels.
    options: [(label, default_selected)]
    Returns: list of selected indices, or None (canceled).
    Requires rich available + both stdin/stdout are TTYs; otherwise returns None so caller can fall back."""
    import sys
    if not render._RICH_AVAIL or not _is_interactive_tty():
        return None
    import os, termios, tty, select
    from rich.console import Console as _LiveConsole
    from rich.live import Live
    from rich.text import Text

    selected = [d for _, d in options]
    cursor = 0
    n = len(options)

    def make_view():
        # header may contain [style]..[/style] markup; from_markup parses; rich output handles \r\n
        t = Text.from_markup(header)
        t.append("\n")
        t.append("(up/down or j/k to move · space to toggle · Enter to confirm · q/Esc to cancel)\n\n",
                 style="dim")
        for i, (label, _) in enumerate(options):
            mark = "[x] " if selected[i] else "[ ] "
            pointer = "▸ " if i == cursor else "  "
            line = f"  {pointer}{mark}{label}\n"
            t.append(line, style="bold reverse" if i == cursor else None)
        return t

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    canceled = False
    # use a separate Console to avoid collision with wl's global render._CONSOLE theme/highlight
    live_console = _LiveConsole(file=sys.stderr, force_terminal=True)
    try:
        # cbreak (not raw): disable echo + line buffer but keep ONLCR (\n auto-adds \r);
        # otherwise rich's \n won't return to col 0 and each line drifts right
        tty.setcbreak(fd)
        # important: use os.read(fd, ...) to bypass Python's sys.stdin buffer.
        # sys.stdin.read(1) would swallow the entire ESC[A 3-byte sequence; select would then
        # see no more data and misinterpret ESC as a single keypress -> exit (root cause of prior bug)
        def read_byte():
            return os.read(fd, 1).decode("utf-8", errors="replace")

        def peek_more(timeout):
            # check whether fd has more bytes ready (terminals emit ESC[A as 3 bytes nearly instantly)
            return bool(select.select([fd], [], [], timeout)[0])

        with Live(make_view(), console=live_console, refresh_per_second=30,
                  screen=False, transient=True) as live:
            while True:
                ch = read_byte()
                if ch == "\x1b":  # ESC or arrow sequence
                    if peek_more(0.05):  # more bytes already there = escape sequence
                        seq = os.read(fd, 2).decode("utf-8", errors="replace")
                        if seq == "[A":
                            cursor = (cursor - 1) % n
                        elif seq == "[B":
                            cursor = (cursor + 1) % n
                        # other arrows / Home / End: ignore
                    else:
                        canceled = True
                        break
                elif ch == " ":
                    selected[cursor] = not selected[cursor]
                elif ch in ("\r", "\n"):
                    break
                elif ch in ("q", "Q", "\x03", "\x04"):  # q / Ctrl-C / Ctrl-D
                    canceled = True
                    break
                elif ch in ("j", "J"):
                    cursor = (cursor + 1) % n
                elif ch in ("k", "K"):
                    cursor = (cursor - 1) % n
                live.update(make_view())
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    if canceled:
        return None
    return [i for i, s in enumerate(selected) if s]


def _checkin_per_item(con, rows):
    """Per-item prompt fallback mode: y/n/note/q (works on non-TTY / piped input; also supports per-item note)."""
    pre_done = sum(1 for r in rows if r["already"])
    out(_c(f"{len(rows)} items to check in, {pre_done} already done:", "header"))
    out(_c("Input: [Enter]/y = check in · n = skip · q = quit · any other text = check in with that as note", "meta"))
    print()

    done_now = skipped = 0
    for r in rows:
        nid = r["id"]
        pri = f"[#{r['priority']}]" if r["priority"] else "[# ]"   # unset priority marker (aligned)
        head = f"#{nid} {pri} {r['title']}".strip()
        if r["already"]:
            out(_c(f"  ✓ {head} (already done today)", "done"))
            continue
        try:
            ans = input(_c(f"  ▸ {head}\n    [y/n/note/q] > ", "header")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            out(_c("(interrupted; remaining tasks skipped)", "meta"))
            break
        if ans in ("q", "Q", "exit", "quit"):
            out(_c("(quit)", "meta"))
            break
        if ans in ("n", "N", "no", "skip"):
            skipped += 1
            out(_c(f"    ⏭ #{nid} skipped", "meta"))
            continue
        body = "✓ done" if ans in ("", "y", "Y", "yes") else ans
        log_id = _insert_log(con, nid, body)
        checkin_metric(con, log_id, nid, _tu.today())
        con.commit()
        done_now += 1
        marker = _c("    ✓", "done")
        if body == "✓ done":
            out(f"{marker} #{nid} checked in")
        else:
            out(f"{marker} #{nid} checked in: {_c(body, 'meta')}")
    print()
    out(_c(
        f"done {pre_done + done_now}/{len(rows)} · new this run {done_now}" +
        (f" · skipped {skipped}" if skipped else ""),
        "header"))
