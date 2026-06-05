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

    find(con, "node", parent_id=5, kind__in=["task", "habit"], order="id")
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


def _where(conds: dict):
    """Build a `WHERE …` fragment + bound params from kwargs. ("", []) when empty.
    Key `col` → `col = ?`; key `col__op` → operator; `col__in=[…]`; `col=None`
    / `col__ne=None` → `IS NULL` / `IS NOT NULL`."""
    if not conds:
        return "", []
    frags, params = [], []
    for key, val in conds.items():
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
    return " WHERE " + " AND ".join(frags), params


def insert(con, table, row: dict) -> int:
    """INSERT one row from a dict; return the new rowid. No commit."""
    _ident(table)
    if not row:
        raise ValueError("insert: empty row")
    cols = [_ident(c) for c in row]
    ph = ", ".join("?" * len(cols))
    return con.execute(
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({ph})", list(row.values())
    ).lastrowid


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
    """DELETE rows matching the kwargs filter; return rowcount. No commit. Refuses
    an unconditional whole-table delete. (worklog prefers soft-delete / status
    over DELETE for nodes — WL#501 — but removing a tag / link / prop row is a
    legitimate delete.)"""
    _ident(table)
    where, params = _where(conds)
    if not where:
        raise ValueError("delete: refusing to delete a whole table (give conditions)")
    return con.execute(f"DELETE FROM {table}{where}", params).rowcount


def find(con, table, *, cols="*", order=None, limit=None, **conds):
    """SELECT rows from one table matching the kwargs filter; return list[Row].
    `cols` / `order` are raw SQL expressions (code-controlled, e.g. "COUNT(*) AS n"
    / "priority NULLS LAST, id")."""
    _ident(table)
    where, params = _where(conds)
    sql = f"SELECT {cols} FROM {table}{where}"
    if order:
        sql += f" ORDER BY {order}"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return con.execute(sql, params).fetchall()


def find_one(con, table, **conds):
    """First matching row (LIMIT 1), or None."""
    rows = find(con, table, limit=1, **conds)
    return rows[0] if rows else None


def get(con, table, row_id):
    """The row with `id = row_id`, or None."""
    return find_one(con, table, id=row_id)


def exists(con, table, **conds) -> bool:
    """True iff any row matches the filter."""
    return find_one(con, table, cols="1", **conds) is not None


def count(con, table, **conds) -> int:
    """COUNT(*) of rows matching the filter."""
    return find(con, table, cols="COUNT(*) AS n", **conds)[0]["n"]
