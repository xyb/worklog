"""Thin, zero-dependency CRUD helpers over a sqlite3 connection (see DESIGN §0 G3).

NOT an ORM: no model classes, no query builder. These just remove the
hand-written column-list / placeholder / WHERE footguns for the uniform
single-table ~80% — the kind of query you can state in one line of kwargs.
Complex reads — JOIN / CTE / CASE / time-window — stay as explicit SQL.

None of these commit; the caller owns the transaction (matching the rest of
worklog's query helpers).

Read filters: a keyword `col=value` means `col = ?`; a `col__op=value` suffix
selects an operator (`ge`/`le`/`gt`/`lt`/`ne`/`like`/`is`/`in`). `col=None`
(and `col__ne=None`) become `IS NULL` / `IS NOT NULL` — never the always-false
`= NULL`.

    query(con, "node", parent_id=5, kind__in=["task", "habit"], order="id")
    exists(con, "tag", node_id=n, tag=t)
    get(con, "node", 42)

Safety: values are always parameter-bound (`?`); only table / column *names* are
interpolated into the SQL, and those must be plain identifiers — `_ident()`
enforces that, so a caller can't accidentally thread user input into a name.
`cols` / `order` are raw SQL expressions and so must be code-controlled, never
user input.
"""
from __future__ import annotations

import re

from . import timeutil as _tu

# a bare identifier, optionally qualified as `table.col`
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")

_OPS = {"eq": "=", "ne": "!=", "ge": ">=", "le": "<=", "gt": ">", "lt": "<",
        "like": "LIKE", "is": "IS"}


def _ident(name: str) -> str:
    """Validate a table/column identifier (defends against a caller threading
    user input into a name). Returns it unchanged, or raises ValueError."""
    if not isinstance(name, str) or not _IDENT.match(name):
        raise ValueError(f"unsafe SQL identifier: {name!r}")
    return name


def _clause(conds: dict):
    """The condition fragment LIST + params for a kwargs dict (no leading WHERE).
    Shared by `_where` and the public `clause()`."""
    frags, params = [], []
    for key, val in conds.items():
        # `col__op`: split on the first "__". This reserves "__" as the operator
        # separator, so a column name must not contain "__" (worklog's schema has
        # none — single underscores like created_at are fine). A typo'd / unknown
        # op is then caught below rather than silently treated as a column.
        col, _, op = key.partition("__")
        _ident(col)
        op = op or "eq"
        if op == "in":
            vals = list(val)
            if not vals:               # empty IN matches nothing (valid, deterministic)
                frags.append("0")
                continue
            frags.append(f"{col} IN ({', '.join('?' * len(vals))})")
            params.extend(vals)
        elif val is None and op in ("eq", "ne"):
            frags.append(f"{col} IS {'NOT ' if op == 'ne' else ''}NULL")
        elif op in _OPS:
            frags.append(f"{col} {_OPS[op]} ?")
            params.append(val)
        else:
            raise ValueError(f"unknown filter operator {op!r} (in {key!r})")
    return frags, params


def clause(**conds):
    """WHERE-condition fragments + bound params from kwargs (same `col__op` grammar
    as `query`). For composing into a hand-assembled query that ALSO needs complex
    fragments (subqueries, JOINs, expressions): build the simple equality/operator
    conditions here — safe, no manual col / ? / param three-way alignment — then
    AND your own fragments onto the returned list. Returns (list[str], list).

        frags, params = clause(kind="task", status__ne="DONE")
        frags.append("id IN (SELECT node_id FROM tag WHERE tag = ?)"); params.append(t)
        sql = "SELECT * FROM node" + (" WHERE " + " AND ".join(frags) if frags else "")
    """
    return _clause(conds)


def _where(conds: dict, *, alive: bool = True):
    """Build a `WHERE …` fragment + bound params from a kwargs dict. Key `col` →
    `col = ?`; `col__op` → operator; `col__in=[…]`; `col=None` / `col__ne=None` →
    `IS NULL` / `IS NOT NULL`. When `alive` (the default), a `deleted_at IS NULL`
    tombstone filter is ANDed on so reads skip soft-deleted rows."""
    frags, params = _clause(conds)
    if alive:
        frags = frags + ["deleted_at IS NULL"]
    if not frags:
        return "", []
    return " WHERE " + " AND ".join(frags), params


def insert(con, table, row: dict, *, or_=None) -> int:
    """INSERT one row from a dict; return the new rowid. No commit.
    `or_="ignore"` / `or_="replace"` adds an `INSERT OR IGNORE/REPLACE` conflict
    clause (for idempotent tag/link/prop writes and date_meta upserts)."""
    _ident(table)
    if not row:
        raise ValueError("insert: empty row")
    if or_ is None:
        conflict = ""
    elif or_ in ("ignore", "replace"):
        conflict = f" OR {or_.upper()}"
    else:
        raise ValueError(f"insert or_ must be 'ignore' / 'replace' / None, got {or_!r}")
    cols = [_ident(c) for c in row]
    ph = ", ".join("?" * len(cols))
    return con.execute(
        f"INSERT{conflict} INTO {table} ({', '.join(cols)}) VALUES ({ph})", list(row.values())
    ).lastrowid


def upsert(con, table, row, *, key) -> bool:
    """Tombstone-safe insert-or-revive by a natural key — the replacement for
    `INSERT OR IGNORE` / `INSERT OR REPLACE` under soft-delete. Updates the row
    matching `key` (a tuple of the natural-key columns), reviving it if it was tombstoned
    and writing the non-key columns, or INSERTs a fresh row if none exists. So re-adding a
    removed tag / re-setting a prop revives its tombstone instead of being swallowed by it
    (the OR IGNORE bug) or hard-replacing it (OR REPLACE drops the tombstone). No commit.
    Returns True if an existing row was revived/updated, False if a new row was inserted."""
    _ident(table)
    keyconds = {k: row[k] for k in key}
    sets = {c: v for c, v in row.items() if c not in key and c != "id"}  # never UPDATE the rowid
    sets["deleted_at"] = None  # clear any tombstone on the matched row
    where, params = _where(keyconds, alive=False)  # match the key regardless of tombstone state
    set_sql = ", ".join(f"{_ident(c)} = ?" for c in sets)
    rc = con.execute(f"UPDATE {table} SET {set_sql}{where}", [*sets.values(), *params]).rowcount
    if rc:
        return True
    insert(con, table, row)
    return False


def update(con, table, row_id, changes: dict) -> int:
    """UPDATE the row with `id = row_id` from a dict of changes; return rowcount.
    No-op (returns 0) on an empty change set. No commit."""
    _ident(table)
    if not changes:
        return 0
    sets = ", ".join(f"{_ident(c)} = ?" for c in changes)
    return con.execute(
        f"UPDATE {table} SET {sets} WHERE id = ?", [*changes.values(), row_id]
    ).rowcount


def delete(con, table, **conds) -> int:
    """Soft-delete: stamp `deleted_at` on the live rows matching the kwargs
    filter, instead of removing them; return the number tombstoned. Idempotent — a
    row already tombstoned isn't re-stamped. Reads (query/get/exists/count) skip
    tombstoned rows, so this looks like a delete but is reversible (clear `deleted_at`
    via `update`). No commit. Refuses an unconditional whole-table soft-delete.
    For a genuine, irreversible removal use `purge()`."""
    _ident(table)
    if not conds:
        raise ValueError("delete: refusing to soft-delete a whole table (give conditions)")
    where, params = _where(conds, alive=True)  # only live rows; never re-stamp a tombstone
    return con.execute(f"UPDATE {table} SET deleted_at = ?{where}", [_tu.utc_now(), *params]).rowcount


def purge(con, table, **conds) -> int:
    """Hard `DELETE` that bypasses the tombstone — for migrations / tests / a genuine
    irreversible purge. Normal removal goes through `delete()` (soft). Return rowcount;
    refuses an unconditional whole-table delete. No commit."""
    _ident(table)
    if not conds:
        raise ValueError("purge: refusing to delete a whole table (give conditions)")
    where, params = _where(conds, alive=False)
    return con.execute(f"DELETE FROM {table}{where}", params).rowcount


def query(con, table, *, cols="*", order=None, limit=None, include_deleted=False, **conds):
    """SELECT rows from one table matching the kwargs filter; return list[Row].
    Skips soft-deleted rows unless `include_deleted=True`. `cols` / `order`
    are raw SQL expressions (code-controlled, e.g. "COUNT(*) AS n" / "priority NULLS LAST, id")."""
    _ident(table)
    where, params = _where(conds, alive=not include_deleted)
    sql = f"SELECT {cols} FROM {table}{where}"
    if order:
        sql += f" ORDER BY {order}"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return con.execute(sql, params).fetchall()


def query_one(con, table, *, include_deleted=False, **conds):
    """First matching row (LIMIT 1), or None."""
    rows = query(con, table, limit=1, include_deleted=include_deleted, **conds)
    return rows[0] if rows else None


def get(con, table, row_id, *, include_deleted=False):
    """The row with `id = row_id`, or None (None too if it's soft-deleted, unless
    `include_deleted`)."""
    return query_one(con, table, id=row_id, include_deleted=include_deleted)


def exists(con, table, *, include_deleted=False, **conds) -> bool:
    """True iff any (live, unless include_deleted) row matches the filter."""
    return query_one(con, table, cols="1", include_deleted=include_deleted, **conds) is not None


def count(con, table, *, include_deleted=False, **conds) -> int:
    """COUNT(*) of (live, unless include_deleted) rows matching the filter."""
    return query(con, table, cols="COUNT(*) AS n", include_deleted=include_deleted, **conds)[0]["n"]
