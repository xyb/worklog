"""worklog commands: bulk group."""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

from .. import render
from .. import db_table as _db
from .. import timeutil as _tu
from ..helpers import (
    _apply_top_limit,
    _fmt_dur,
    _is_brief,
    _log_full,
    _norm_sched,
    _resolve_at_ts,
    _resolve_concrete_date,
    _resolve_log_tail,
    _resolve_window,
    _sched_anchor,
    _sched_display,
    _sched_kind,
    _sched_sort_key,
    _status_marker,
    _term_width,
    _truncate_log_body,
    GENERIC_TAGS,
)
from ..queries import (
    _ancestors_chain,
    _check_ids_exist,
    _collect_descendants,
    soft_delete_node,
    _has_tag,
    _insert_log,
    _node_bucket,
    _node_clock_min,
    _node_exists,
    _node_plan,
    _node_project,
    _node_tags,
    _project_members,
    _sec_group,
    _status_filter_sql,
    _upsert_prop,
    _upsert_link,
    _delete_link,
)
from .metric import import_metric, _CARRIER_TYPE
from ..render import (
    _PRI_STYLE,
    _STATUS_STYLE,
    _RICH_AVAIL,
    _resolve_theme,
    THEMES,
    _c,
    _hl,
    _node_line,
    _print_truncation_hint,
    _snippet,
    out,
)
from ..xdg import _resolve_db_path, _resolve_aliases_path, _xdg_data_home, _xdg_config_home

# Lazy access to cli module (for DB wrappers + module state).
# Used at function call time (not at import) to avoid the cli ↔ commands
# import cycle.
from .. import cli as _cli  # noqa: E402


def cmd_import(args, con):
    """Batch add/update: JSON {add:[...], update:[...]}; single transaction; supports nested children + ref/parent_ref."""
    import json

    raw = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        sys.exit(f"✗ JSON parse error: {e}")
    if not isinstance(data, dict):
        sys.exit("✗ top level must be an object {add:[...], update:[...]}")

    ref_map = {}
    counters = {"add": 0, "update": 0}
    dry = args.dry_run
    try:
        for spec in data.get("add", []):
            _import_node(con, spec, None, ref_map, dry, counters)
        for spec in data.get("update", []):
            _import_update(con, spec, dry, counters)
    except (ValueError, KeyError) as e:
        con.rollback()
        sys.exit(f"✗ import failed (rolled back): {e}")

    if dry:
        out(_c("[dry-run]", "meta") + _c(f" would add {counters['add']} · update {counters['update']} (not written)"))
        if ref_map:
            out("  " + _c("ref: " + ", ".join(ref_map.keys()), "meta"))
    else:
        con.commit()
        out(_c("✓", "done") + _c(f" added {counters['add']} · updated {counters['update']}"))
        if ref_map:
            print("  ref->id: " + ", ".join(f"{k}=#{v}" for k, v in ref_map.items()))


# --- wl-diff format (apply) ---
# line format: <prefix><indent><node line>  prefix: '+' add, '~' update, '-' delete, ' ' context anchor
# node line: [marker] [#pri] #id [kind] title :tags:   (marker required, others optional)
# rich-field sub-lines: <indent>@log/@link/@prop <value>  (attached to the previous node)
_MARKER_STATUS = {" ": "TODO", "x": "DONE", "/": "DOING", ">": "LATER", "?": "WAIT", "-": "CANCELED"}

def _apply_validate(con, ops):
    """Validate every wl-diff op (existence / field-ops / parent-cycle / markers) without writing.
    Returns the list of error strings (empty = all valid)."""
    errors = []
    for o in ops:
        pfx, ln = o["op"], o["lineno"]
        if pfx == "~":
            if not _node_exists(con, o["id"]):
                errors.append(f"line {ln}: #{o['id']} does not exist")
            if not o["fieldops"]:
                errors.append(f"line {ln}: ~ #{o['id']} has no field operations")
            for floln, (action, field, value) in o["fieldops"]:
                _validate_fieldop(con, floln, action, field, value, errors)
                # cycle guard: a parent set must not point at the node itself or a
                # descendant (FK is off → not DB-rejected; parity with cmd_node_reparent)
                if (field == "parent" and action == "set" and value.isdigit()
                        and _node_exists(con, o["id"])):
                    p = int(value)
                    if p == o["id"]:
                        errors.append(f"line {floln}: #{o['id']} cannot be its own parent")
                    elif _node_exists(con, p) and p in _collect_descendants(
                            con, o["id"], include_deleted=True):
                        errors.append(
                            f"line {floln}: parent #{p} is a descendant of #{o['id']} "
                            "— would make a cycle")
        else:
            f = o["fields"]
            has_id = "id" in f
            if pfx in ("-", " ") and not has_id:
                errors.append(f"line {ln}: '{pfx}' requires #id")
            if pfx == "+" and has_id:
                errors.append(f"line {ln}: '+' add should not carry #id")
            if has_id and pfx != "+" and not _node_exists(con, f["id"]):
                errors.append(f"line {ln}: #{f['id']} does not exist")
            if "marker" in f and f["marker"] not in _MARKER_STATUS:
                errors.append(f"line {ln}: unknown marker [{f['marker']}]")
    if errors:
        sys.exit("✗ validation failed (not written):\n  " + "\n  ".join(errors))
    return errors


def _apply_plan(ops):
    """One human-readable line per op (for --dry-run and the success summary)."""
    plan = []
    for o in ops:
        pfx = o["op"]
        if pfx == "~":
            ch = ", ".join(_fieldop_desc(a, fld, v) for _, (a, fld, v) in o["fieldops"])
            plan.append(f"~ #{o['id']}: {ch}")
        elif pfx == "+":
            f = o["fields"]
            sub = f" (+{len(o['subs'])} sub-items)" if o["subs"] else ""
            plan.append(f"+ {f['title']}" + (f" @depth{o['depth']}" if o["depth"] else "") + sub)
        elif pfx == "-":
            plan.append(f"- #{o['fields']['id']} (cascades subtree)")
    return plan


def _apply_execute(con, ops):
    """Execute every op in the caller's open transaction (caller commits on success); roll back
    and exit on any error. Returns counts {add, update, delete}."""
    stack = {}
    counts = {"add": 0, "update": 0, "delete": 0}
    try:
        for o in ops:
            pfx = o["op"]
            if pfx == "~":
                _exec_update(con, o)
                counts["update"] += 1
                continue
            f, depth = o["fields"], o["depth"]
            if pfx == " ":
                stack[depth] = f["id"]
                continue
            if pfx == "-":
                # recursive subtree soft-delete: tombstone the node + each
                # descendant (and, via soft_delete_node, their spoke rows) — reversible,
                # no cascade needed (FK is off). _collect_descendants returns live nodes.
                ids = [f["id"]] + _collect_descendants(con, f["id"], include_deleted=True)
                for did in ids:
                    soft_delete_node(con, did)
                counts["delete"] += len(ids)
                continue
            # pfx == "+": add new node
            parent_id = stack.get(depth - 1) if depth > 0 else None
            status = _MARKER_STATUS.get(f.get("marker", " "), "TODO")
            kind = f.get("kind", "task")
            now = _tu.utc_now()
            node_row = {
                "parent_id": parent_id, "title": f["title"], "kind": kind,
                "status": status, "priority": f.get("priority"), "created_at": now,
            }
            if status == "DONE":
                node_row["closed_at"] = now
            nid = _db.insert(con, "node", node_row)
            for t in f.get("tags", []):
                _db.upsert(con, "tag", {"node_id": nid, "tag": t}, key=("node_id", "tag"))
            for kind_, val in o["subs"]:
                _apply_sub(con, nid, kind_, val)
            counts["add"] += 1
            stack[depth] = nid
    except Exception as e:
        con.rollback()
        sys.exit(f"✗ apply failed (rolled back): {e}")
    return counts


def cmd_apply(args, con):
    """Apply wl-diff: + add (node line) / ~ update (lock-line + field-ops, only declared fields) / - delete / anchor. Single transaction + dry-run validation."""
    raw = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
    try:
        ops = _parse_wld(raw)
    except ValueError as e:
        sys.exit(f"✗ parse failed: {e}")
    errors = _apply_validate(con, ops)
    if errors:
        sys.exit("✗ validation failed (not written):\n  " + "\n  ".join(errors))
    plan = _apply_plan(ops)
    if args.dry_run:
        out(_c("[dry-run]", "meta") + _c(f" {len(plan)} operations (not written):", "header"))
        for desc in plan:
            out("  " + _c(desc))
        return
    counts = _apply_execute(con, ops)
    con.commit()
    out(_c("✓", "done") + _c(f" added {counts['add']} · updated {counts['update']} · deleted {counts['delete']}"))

def _import_node(con, spec, parent_id, ref_map, dry, counters):
    """Recursively insert a node (with tags/props/links/logs/children). Returns new id (placeholder None in dry mode)."""
    title = spec.get("title")
    if not title:
        raise ValueError(f"node missing title: {spec}")
    kind = spec.get("kind", "task")
    status = spec.get("status")
    if not status and kind in ("task", "habit"):
        status = "TODO"
    sched = _norm_sched(spec.get("scheduled"))  # normalize + validate (raises in dry-run)
    # parent: explicit parent_id > parent_ref (same batch) > recursively-passed parent_id
    pid = spec.get("parent", parent_id)
    if spec.get("parent_ref"):
        if spec["parent_ref"] not in ref_map:
            raise ValueError(f"parent_ref '{spec['parent_ref']}' undefined (must appear before reference)")
        pid = ref_map[spec["parent_ref"]]
    if dry:
        nid = f"<ref:{spec.get('ref', '?')}>"
        counters["add"] += 1
    else:
        now = _tu.utc_now()
        node_row = {
            "parent_id": pid, "title": title, "kind": kind, "status": status,
            "priority": spec.get("priority"), "scheduled_date": sched,
            "deadline_date": spec.get("deadline"), "body": spec.get("body"),
            "created_at": now,
        }
        if status == "DONE":
            node_row["closed_at"] = now
        nid = _db.insert(con, "node", node_row)
        counters["add"] += 1
        for t in spec.get("tags", []):
            _db.upsert(con, "tag", {"node_id": nid, "tag": t}, key=("node_id", "tag"))
        for k, v in (spec.get("props") or {}).items():
            _upsert_prop(con, nid, k, str(v))
        for d in spec.get("links", []):
            _upsert_link(con, nid, d)
        for entry in spec.get("logs", []):
            log_id = _insert_log(con, nid, entry)
            # a log entry may carry structured datapoints: {"body":..., "metrics":[{tag,value,unit}]}
            if isinstance(entry, dict) and entry.get("metrics"):
                log_at = _db.get(con, "log", log_id)["logged_at"]
                for mspec in entry["metrics"]:
                    import_metric(con, log_id, nid, mspec, default_at=log_at)
        # node-level metrics → a dedicated carrier log (1 carrier → N datapoints, e.g. a CGM import)
        if spec.get("metrics"):
            log_id = _db.insert(con, "log", {
                "node_id": nid, "logged_at": _tu.utc_now(), "body": "", "tag": _CARRIER_TYPE,
            })
            for mspec in spec["metrics"]:
                import_metric(con, log_id, nid, mspec)
            counters["metric"] = counters.get("metric", 0) + len(spec["metrics"])

    if spec.get("ref"):
        ref_map[spec["ref"]] = nid
    for child in spec.get("children", []):
        _import_node(con, child, nid, ref_map, dry, counters)
    return nid

def _import_update(con, spec, dry, counters):
    nid = spec.get("id")
    if not nid or not _node_exists(con, nid):
        raise ValueError(f"update target #{nid} does not exist")
    # Footgun guard: `tags`/`tag` is not an update field — it's silently ignored
    # here (and would silently create a shadow prop via wl set), so a writer thinks tags
    # were set when nothing happened. Tags go through add_tags / remove_tags.
    bad = {"tags", "tag"} & set(spec)
    if bad:
        raise ValueError(
            f"update #{nid}: '{'/'.join(sorted(bad))}' is not a field — use "
            f"\"add_tags\": [...] / \"remove_tags\": [...] to edit tags"
        )
    if dry:
        counters["update"] += 1
        return
    if "parent" in spec and spec["parent"] is not None:
        if not _node_exists(con, spec["parent"]):
            raise ValueError(f"update #{nid}: parent #{spec['parent']} does not exist")
        # cycle guard (parity with cmd_node_reparent): FK is off, so a bad
        # parent_id isn't DB-rejected — refuse making a node its own ancestor, which would
        # otherwise leave a cycle that hangs the ancestor/descendant walks.
        if spec["parent"] == nid:
            raise ValueError(f"update #{nid}: a node cannot be its own parent")
        if spec["parent"] in _collect_descendants(con, nid, include_deleted=True):
            raise ValueError(
                f"update #{nid}: parent #{spec['parent']} is a descendant of #{nid} "
                "— reparenting there would make a cycle")
    changes = {col: spec[col] for col in
               ("status", "priority", "title", "scheduled_date", "deadline_date", "body") if col in spec}
    if "parent" in spec:  # move; spec key 'parent' maps to the parent_id column
        changes["parent_id"] = spec["parent"]
    if spec.get("status") == "DONE" and "closed_at" not in spec:
        changes["closed_at"] = _tu.utc_now()
    if changes:
        _db.update(con, "node", nid, changes)
    for t in spec.get("add_tags", []):
        _db.upsert(con, "tag", {"node_id": nid, "tag": t}, key=("node_id", "tag"))
    for t in spec.get("remove_tags", []):
        _db.delete(con, "tag", node_id=nid, tag=t)
    for d in spec.get("add_links", []):
        _upsert_link(con, nid, d)
    for entry in spec.get("add_logs", []):
        _insert_log(con, nid, entry)
    counters["update"] += 1

def _parse_node_line(body):
    import re

    f = {}
    m = re.match(r"^\[([ x/>?\-])\]\s*", body)
    if m:
        f["marker"] = m.group(1)
        body = body[m.end():]
    m = re.match(r"^\[#([A-C])\]\s*", body)
    if m:
        f["priority"] = m.group(1)
        body = body[m.end():]
    m = re.match(r"^#(\d+)\s*", body)
    if m:
        f["id"] = int(m.group(1))
        body = body[m.end():]
    m = re.match(r"^\[([a-z_]+)\]\s*", body)
    if m:
        f["kind"] = m.group(1)
        body = body[m.end():]
    m = re.search(r"\s*:([\w:]+):\s*$", body)
    if m:
        f["tags"] = [t for t in m.group(1).split(":") if t]
        body = body[: m.start()]
    f["title"] = body.strip()
    return f


_SETTABLE = ("status", "priority", "title", "parent", "scheduled", "deadline")

def _parse_fieldop(s):
    """Parse a field-operation line under a ~ block. Returns (action, field, value) or None.

    set:   `status DONE` / `priority A` / `title x` / `parent 6` / `scheduled 2026-06-01`
    clear: `priority -` (value '-' clears)
    tag:   `+tag x` / `-tag x`
    log:   `+log text` (add only; log is append-only)
    link:  `+link doc` / `-link doc`
    prop:  `prop k=v` / `-prop k`
    """
    import re

    m = re.match(r"^([+-])(tag|link)\s+(.+)$", s)
    if m:
        return ("add" if m.group(1) == "+" else "remove", m.group(2), m.group(3).strip())
    m = re.match(r"^\+log\s+(.+)$", s)
    if m:
        return ("add", "log", m.group(1).strip())
    m = re.match(r"^-prop\s+(\S+)$", s)
    if m:
        return ("remove", "prop", m.group(1))
    m = re.match(r"^prop\s+(\S+?)=(.*)$", s)
    if m:
        return ("set", "prop", (m.group(1), m.group(2).strip()))
    m = re.match(r"^(" + "|".join(_SETTABLE) + r")\s+(.+)$", s)
    if m:
        val = m.group(2).strip()
        if val == "-":
            return ("clear", m.group(1), None)
        return ("set", m.group(1), val)
    return None

def _parse_wld(text):
    """Parse wl-diff -> ops list.

    +/-/anchor op: {op,depth,fields,subs,lineno}
    ~  op:         {op:'~',id,fieldops:[(lineno,(action,field,value))],lineno}
    raises ValueError
    """
    import re

    ops = []
    cur_update = None  # most recent ~ op; collects subsequent indented field-op lines
    for lineno, raw in enumerate(text.splitlines(), 1):
        s = raw.lstrip()
        # blank / comment ('#' followed by space or non-digit) -> skip; but #<digit> is a node id, not a comment
        if not s or (s.startswith("#") and (len(s) == 1 or not s[1].isdigit())):
            continue
        indented = raw[:1] in (" ", "\t")
        # indented line under a ~ context: try field-op first (+tag/-tag/-prop start with +/-, so first-char heuristic isn't enough)
        if cur_update is not None and indented:
            fop = _parse_fieldop(s)
            if fop is not None:
                cur_update["fieldops"].append((lineno, fop))
                continue
            # indented but not a valid field-op: if it looks like a node line (has marker), drop through as new node (ending ~); else error
            if not re.match(r"^[+\- ]?\s*\[", s):
                raise ValueError(f"line {lineno}: unparseable field-op '{s}' under '~' (allowed: status/priority/title/parent/scheduled/deadline/±tag/+log/±link/prop/-prop)")
        # @ sub-line (rich fields of a +/-/anchor node)
        m = re.match(r"^[+\- ]?\s*@(log|link|prop)\s+(.*)$", raw)
        if m:
            if not ops or ops[-1]["op"] == "~":
                raise ValueError(f"line {lineno}: @{m.group(1)} has no preceding +/anchor node to attach to")
            ops[-1]["subs"].append((m.group(1), m.group(2).strip()))
            continue
        m = re.match(r"^([+~\- ])(\s*)(.*)$", raw)
        if not m:
            # A flush-left line that parses as a field op right after a `~ #id` is
            # almost always a forgotten indent — field ops must be indented
            # under the lock line (DESIGN §18.2). Give an actionable hint instead of
            # the opaque "cannot parse".
            if cur_update is not None and _parse_fieldop(s) is not None:
                raise ValueError(
                    f"line {lineno}: field op '{s}' must be indented under "
                    f"'~ #{cur_update['id']}' (indent each op by 2 spaces)"
                )
            raise ValueError(f"line {lineno}: cannot parse '{raw}'")
        prefix, spaces, body = m.group(1), m.group(2), m.group(3)
        if not body.strip():
            continue
        if prefix == "~":
            idm = re.search(r"#(\d+)", body)
            if not idm:
                raise ValueError(f"line {lineno}: '~' requires #id (e.g. '~ #14' or single-line '~ [x] #14')")
            nid = int(idm.group(1))
            # single-line shorthand: parse marker/priority/title if present -> a set op for each (untouched if absent)
            f = _parse_node_line(body)
            inline = []
            if "marker" in f:
                inline.append((lineno, ("set", "status", _MARKER_STATUS.get(f["marker"], "TODO"))))
            if "priority" in f:
                inline.append((lineno, ("set", "priority", f["priority"])))
            if f.get("title"):
                inline.append((lineno, ("set", "title", f["title"])))
            op = {"op": "~", "id": nid, "fieldops": inline, "lineno": lineno}
            ops.append(op)
            cur_update = op  # may still accept subsequent indented field-ops (mix of single-line shorthand + complex ops)
            continue
        cur_update = None  # +/-/anchor line ends ~ context
        depth = len(spaces) // 2
        fields = _parse_node_line(body)
        if not fields["title"] and prefix != "-":
            raise ValueError(f"line {lineno}: missing title")
        ops.append({"op": prefix, "depth": depth, "fields": fields, "subs": [], "lineno": lineno})
    return ops


_STATUSES = {"TODO", "DOING", "LATER", "WAIT", "DONE", "DEFERRED", "CANCELED"}
_SET_COL = {"status": "status", "priority": "priority", "title": "title",
            "parent": "parent_id", "scheduled": "scheduled_date", "deadline": "deadline_date"}

def _validate_fieldop(con, lineno, action, field, value, errs):
    if field == "status" and action == "set" and value not in _STATUSES:
        errs.append(f"line {lineno}: invalid status '{value}' (valid: {'/'.join(sorted(_STATUSES))})")
    elif field == "priority" and action == "set" and value not in ("A", "B", "C"):
        errs.append(f"line {lineno}: invalid priority '{value}' (A/B/C)")
    elif field == "title" and action == "clear":
        errs.append(f"line {lineno}: title cannot be cleared")
    elif field == "parent" and action == "set":
        if not value.isdigit() or not _node_exists(con, int(value)):
            errs.append(f"line {lineno}: parent #{value} does not exist")
    elif field == "scheduled" and action == "set":
        try:
            _norm_sched(value)
        except ValueError as e:
            errs.append(f"line {lineno}: {e}")

def _exec_update(con, o):
    """Execute ~ field operations: only touches explicitly-declared fields; never touches anything not declared."""
    nid = o["id"]
    for _, (action, field, value) in o["fieldops"]:
        if field in _SET_COL:
            col = _SET_COL[field]
            if action == "clear":
                _db.update(con, "node", nid, {col: None})
            else:
                if field == "parent":
                    v = int(value)
                elif field == "scheduled":
                    v = _norm_sched(value)
                else:
                    v = value
                _db.update(con, "node", nid, {col: v})
                if field == "status" and value == "DONE":
                    con.execute("UPDATE node SET closed_at = ? WHERE id = ? AND closed_at IS NULL", (_tu.utc_now(), nid))
        elif field == "tag":
            if action == "add":
                _db.upsert(con, "tag", {"node_id": nid, "tag": value}, key=("node_id", "tag"))
            else:
                _db.delete(con, "tag", node_id=nid, tag=value)
        elif field == "log":
            _insert_log(con, nid, value)
        elif field == "link":
            if action == "add":
                _upsert_link(con, nid, value)
            else:
                _delete_link(con, nid, value)
        elif field == "prop":
            if action == "set":
                k, v = value
                _upsert_prop(con, nid, k, v)
            else:
                _db.delete(con, "prop", node_id=nid, key=value)

def _fieldop_desc(action, field, value):
    if action == "clear":
        return f"{field}=cleared"
    if action == "add":
        return f"+{field} {value}"
    if action == "remove":
        return f"-{field} {value}"
    if field == "prop":
        return f"prop {value[0]}={value[1]}"
    return f"{field}->{value}"

def _apply_sub(con, nid, kind, val):
    if kind == "log":
        _insert_log(con, nid, val)
    elif kind == "link":
        _upsert_link(con, nid, val)
    elif kind == "prop":
        if "=" in val:
            k, v = val.split("=", 1)
            _upsert_prop(con, nid, k.strip(), v.strip())



_VALID_FIND_FIELDS = {"title", "body", "log", "tag", "prop", "link"}
_VALID_KINDS = {"task", "project", "area", "year", "quarter", "month", "week", "day",
                "lifetime", "decade", "habit", "signal", "meetlog"}

