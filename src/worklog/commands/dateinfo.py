"""worklog command: `wl dateinfo` (polymorphic) + the `wl date` set/ls/rm/import group.

Both front the `date_meta` table — per-date labels (holiday / vacation / working-day-swap /
solar term) shown in the `wl day` header. The weekday is auto-computed, not stored."""
from __future__ import annotations

import sys
from pathlib import Path

from ..models import DateMeta
from ..render import _c, dispatch_group, out
from .views import _cn_weekday, _date_label
from .output import output_format, text_renderer, TextRenderable
from .dtos import DateInfoClearResult, DateInfoImportResult, DateInfoSetResult, DateInfoShowResult


@text_renderer("dateinfo")
def _render_dateinfo(result):
    if isinstance(result, list):
        if not result:
            out(_c("(no date metadata)", "meta"))
        else:
            for r in result:
                out(_c(f"{r.date} {_cn_weekday(r.date)} · {r.label}", "meta"))
    elif isinstance(result, DateInfoImportResult):
        out(_c(f"✓ imported {result.imported} date metadata entries", "meta"))
    elif isinstance(result, DateInfoClearResult):
        if result.cleared:
            out(_c(f"✓ cleared metadata for {result.date}", "meta"))
        else:
            out(_c(f"({result.date} had no metadata)", "meta"))
    elif isinstance(result, DateInfoSetResult):
        out(_c(f"✓ {result.date} {result.weekday} · {result.label}", "meta"))
    elif isinstance(result, DateInfoShowResult):
        lbl = result.label
        out(_c(f"{result.date} {result.weekday}" + (f" · {lbl}" if lbl else " (no label)"), "meta"))


@text_renderer("date_set")
def _render_date_set(result):
    out(_c(f"✓ {result.date} {result.weekday} · {result.label}", "meta"))


@text_renderer("date_ls")
def _render_date_ls(result):
    if isinstance(result, DateInfoShowResult):
        lbl = result.label
        out(_c(f"{result.date} {result.weekday}" + (f" · {lbl}" if lbl else " (no label)"), "meta"))
    else:  # list[DateMeta]
        if not result:
            out(_c("(no date metadata)", "meta"))
        else:
            for r in result:
                out(_c(f"{r.date} {_cn_weekday(r.date)} · {r.label}", "meta"))


@text_renderer("date_rm")
def _render_date_rm(result):
    if result.cleared:
        out(_c(f"✓ cleared metadata for {result.date}", "meta"))
    else:
        out(_c(f"({result.date} had no metadata)", "meta"))


@text_renderer("date_import")
def _render_date_import(result):
    out(_c(f"✓ imported {result.imported} date metadata entries", "meta"))


@output_format
def cmd_dateinfo(args, con):
    """Date metadata: holiday / vacation / working-day-swap label. Set one / batch-import a yearly holiday table / list."""
    if args.import_file:
        import json
        raw = sys.stdin.read() if args.import_file == "-" else Path(args.import_file).read_text(encoding="utf-8")
        data = json.loads(raw)  # {"2026-05-01": "Labor Day", ...}
        n = 0
        for d, label in data.items():
            DateMeta.upsert(con, {"date": d, "label": label})
            n += 1
        con.commit()
        return TextRenderable(DateInfoImportResult(imported=n), cmd_name="dateinfo")
    if args.date and args.label:
        DateMeta.upsert(con, {"date": args.date, "label": args.label})
        con.commit()
        return TextRenderable(
            DateInfoSetResult(date=args.date, label=args.label, weekday=_cn_weekday(args.date)),
            cmd_name="dateinfo",
        )
    if args.date and args.clear:
        n = DateMeta.delete(con, date=args.date)
        con.commit()
        return TextRenderable(DateInfoClearResult(date=args.date, cleared=n), cmd_name="dateinfo")
    if args.date:
        lbl = _date_label(con, args.date)
        return TextRenderable(
            DateInfoShowResult(date=args.date, label=lbl, weekday=_cn_weekday(args.date)),
            cmd_name="dateinfo",
        )
    return TextRenderable(DateMeta.query(con, order="date"), cmd_name="dateinfo")


@output_format
def cmd_date_set(args, con):
    """Set/update a date's metadata label — the create/update verb of the date group
    (= `wl dateinfo <date> <label>`)."""
    DateMeta.upsert(con, {"date": args.date, "label": args.label})
    con.commit()
    return TextRenderable(
        DateInfoSetResult(date=args.date, label=args.label, weekday=_cn_weekday(args.date)),
        cmd_name="date_set",
    )


@output_format
def cmd_date_ls(args, con):
    """List date metadata, or show one date — the read verb of the date group
    (= bare `wl dateinfo` / `wl dateinfo <date>`)."""
    d = getattr(args, "date", None)
    if d:
        lbl = _date_label(con, d)
        return TextRenderable(
            DateInfoShowResult(date=d, label=lbl, weekday=_cn_weekday(d)),
            cmd_name="date_ls",
        )
    return TextRenderable(DateMeta.query(con, order="date"), cmd_name="date_ls")


@output_format
def cmd_date_rm(args, con):
    """Clear a date's metadata label — the delete verb of the date group
    (= `wl dateinfo <date> --clear`)."""
    n = DateMeta.delete(con, date=args.date)
    con.commit()
    return TextRenderable(DateInfoClearResult(date=args.date, cleared=n), cmd_name="date_rm")


@output_format
def cmd_date_import(args, con):
    """Batch-import a {"YYYY-MM-DD": "label"} JSON table (= `wl dateinfo --import`).
    `-` reads stdin."""
    import json
    raw = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
    data = json.loads(raw)
    n = 0
    for d, label in data.items():
        DateMeta.upsert(con, {"date": d, "label": label})
        n += 1
    con.commit()
    return TextRenderable(DateInfoImportResult(imported=n), cmd_name="date_import")


def cmd_date_group(args, con):
    """Dispatch `wl date <set|ls|rm|import>` (the metric-style entity group).
    A clean group (no default verb — `date` doesn't collide with any leaf). `wl dateinfo`
    is the polymorphic everyday shortcut over the same date_meta table."""
    return dispatch_group(args, con, "date_sub",
        {"set": cmd_date_set, "ls": cmd_date_ls, "rm": cmd_date_rm, "import": cmd_date_import},
        usage="usage: wl date <set|ls|rm|import> … (see `wl date --help`)")
