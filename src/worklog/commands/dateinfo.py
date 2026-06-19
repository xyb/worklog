"""worklog command: `wl dateinfo` (polymorphic) + the `wl date` set/ls/rm/import group.

Both front the `date_meta` table — per-date labels (holiday / vacation / working-day-swap /
solar term) shown in the `wl day` header. The weekday is auto-computed, not stored."""
from __future__ import annotations

import sys
from pathlib import Path

from .. import db_table as _db
from ..render import _c, die, out
from .views import _cn_weekday, _date_label
from .output import output_format, TextRenderable


@output_format
def cmd_dateinfo(args, con):
    """Date metadata: holiday / vacation / working-day-swap label. Set one / batch-import a yearly holiday table / list."""
    if args.import_file:
        import json
        raw = sys.stdin.read() if args.import_file == "-" else Path(args.import_file).read_text(encoding="utf-8")
        data = json.loads(raw)  # {"2026-05-01": "Labor Day", ...}
        n = 0
        for d, label in data.items():
            _db.upsert(con, "date_meta", {"date": d, "label": label}, key=("date",))
            n += 1
        con.commit()
        return TextRenderable({"imported": n}, lambda: out(_c(f"✓ imported {n} date metadata entries", "meta")))
    if args.date and args.label:
        _db.upsert(con, "date_meta", {"date": args.date, "label": args.label}, key=("date",))
        con.commit()
        _d, _l = args.date, args.label
        _wd = _cn_weekday(args.date)
        return TextRenderable({"date": _d, "label": _l}, lambda: out(_c(f"✓ {_d} {_wd} · {_l}", "meta")))
    if args.date and args.clear:
        n = _db.delete(con, "date_meta", date=args.date)
        con.commit()
        _d = args.date
        _msg = _c(f"✓ cleared metadata for {_d}", "meta") if n else _c(f"({_d} had no metadata)", "meta")
        return TextRenderable({"date": _d, "cleared": n}, lambda: out(_msg))
    # no args / only date: list
    if args.date:
        lbl = _date_label(con, args.date)
        _d, _wd = args.date, _cn_weekday(args.date)
        _msg = _c(f"{_d} {_wd}" + (f" · {lbl}" if lbl else " (no label)"), "meta")
        return TextRenderable({"date": _d, "label": lbl}, lambda: out(_msg))
    rows = _db.query(con, "date_meta", cols="date, label", order="date")
    result = [{"date": r["date"], "label": r["label"]} for r in rows]

    def _render():
        if not rows:
            out(_c("(no date metadata)", "meta"))
        else:
            for r in rows:
                out(_c(f"{r['date']} {_cn_weekday(r['date'])} · {r['label']}", "meta"))

    return TextRenderable(result, _render)


@output_format
def cmd_date_set(args, con):
    """Set/update a date's metadata label — the create/update verb of the date group
    (= `wl dateinfo <date> <label>`)."""
    _db.upsert(con, "date_meta", {"date": args.date, "label": args.label}, key=("date",))
    con.commit()
    _d, _l, _wd = args.date, args.label, _cn_weekday(args.date)
    return TextRenderable({"date": _d, "label": _l}, lambda: out(_c(f"✓ {_d} {_wd} · {_l}", "meta")))


@output_format
def cmd_date_ls(args, con):
    """List date metadata, or show one date — the read verb of the date group
    (= bare `wl dateinfo` / `wl dateinfo <date>`)."""
    d = getattr(args, "date", None)
    if d:
        lbl = _date_label(con, d)
        _wd = _cn_weekday(d)
        _msg = _c(f"{d} {_wd}" + (f" · {lbl}" if lbl else " (no label)"), "meta")
        return TextRenderable({"date": d, "label": lbl}, lambda: out(_msg))
    rows = _db.query(con, "date_meta", cols="date, label", order="date")
    result = [{"date": r["date"], "label": r["label"]} for r in rows]

    def _render():
        if not rows:
            out(_c("(no date metadata)", "meta"))
        else:
            for r in rows:
                out(_c(f"{r['date']} {_cn_weekday(r['date'])} · {r['label']}", "meta"))

    return TextRenderable(result, _render)


@output_format
def cmd_date_rm(args, con):
    """Clear a date's metadata label — the delete verb of the date group
    (= `wl dateinfo <date> --clear`)."""
    n = _db.delete(con, "date_meta", date=args.date)
    con.commit()
    _d = args.date
    _msg = _c(f"✓ cleared metadata for {_d}", "meta") if n else _c(f"({_d} had no metadata)", "meta")
    return TextRenderable({"date": _d, "cleared": n}, lambda: out(_msg))


@output_format
def cmd_date_import(args, con):
    """Batch-import a {"YYYY-MM-DD": "label"} JSON table (= `wl dateinfo --import`).
    `-` reads stdin."""
    import json
    raw = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
    data = json.loads(raw)
    n = 0
    for d, label in data.items():
        _db.upsert(con, "date_meta", {"date": d, "label": label}, key=("date",))
        n += 1
    con.commit()
    return TextRenderable({"imported": n}, lambda: out(_c(f"✓ imported {n} date metadata entries", "meta")))


def cmd_date_group(args, con):
    """Dispatch `wl date <set|ls|rm|import>` (the metric-style entity group).
    A clean group (no default verb — `date` doesn't collide with any leaf). `wl dateinfo`
    is the polymorphic everyday shortcut over the same date_meta table."""
    sub = getattr(args, "date_sub", None)
    if sub is None:
        die("usage: wl date <set|ls|rm|import> … (see `wl date --help`)")
    {"set": cmd_date_set, "ls": cmd_date_ls, "rm": cmd_date_rm,
     "import": cmd_date_import}[sub](args, con)
