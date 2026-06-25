"""ALIVE soft-delete lint — the code-level guard that keeps the tombstone
convention enforced by a test instead of by review. `db_table` auto-ANDs `ALIVE`
(`deleted_at IS NULL`) onto model reads and the `_where`/`clause` helpers, but a raw
JOIN/CTE/aggregate SELECT is the deliberate escape hatch that bypasses that — and forgetting
the predicate there leaks tombstoned rows into `wl day` / search. Two static (no-DB) asserts,
parsing source with `ast` so multi-line f-strings collapse to one statement:

  1. the predicate string `deleted_at IS NULL` is hand-written nowhere but the `ALIVE` constant
     definition — every other read must reference the constant (`_db.ALIVE`), so the predicate has
     a single source;
  2. every raw SQL read of a soft-deletable table carries ALIVE — either in the statement text or
     assembled in its enclosing function (the `where.append(_db.ALIVE)` pattern).

Out of scope (skipped): already-applied `migrations/` (operate on raw/legacy rows) and the
generated shell-completion templates in `completion.py` (raw sqlite3 in zsh/bash, not Python).
"""
import ast
import pathlib

SRC = pathlib.Path(__file__).parent.parent / "src" / "worklog"

# Every real table is soft-deletable (carries a deleted_at column); a read FROM one should hide
# tombstones. Add new soft-deletable tables here — a missing one just isn't linted (fails open).
SOFT_TABLES = {"node", "log", "metric", "tag", "prop", "clock", "sched", "link", "date_meta"}

# File-level skips: out of scope for the Python-read soft-delete convention.
SKIP_FILES = {"completion.py"}        # generated shell-completion templates (raw sqlite3 in shell)
SKIP_DIRS = {"migrations"}            # already-applied migrations run against raw/legacy rows

_FROM_RE = __import__("re").compile(r"\bFROM\s+(" + "|".join(SOFT_TABLES) + r")\b", __import__("re").I)


def _py_files():
    for f in sorted(SRC.rglob("*.py")):
        rel = f.relative_to(SRC)
        if f.name in SKIP_FILES or SKIP_DIRS & set(rel.parts):
            continue
        yield f, rel


def _flat(node):
    """Reconstruct one string expression's full text (placeholders kept as `{expr}`), following
    f-strings and `+`/implicit concatenation — so a raw read split across physical lines is seen
    as a single statement. Returns None for non-string nodes."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            v.value if isinstance(v, ast.Constant) else "{" + ast.unparse(v.value) + "}"
            for v in node.values
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return (_flat(node.left) or "") + " " + (_flat(node.right) or "")
    return None


def _docstring_ids(tree):
    """ids of bare-expression string nodes (module/func/class docstrings) — documentation, not SQL."""
    return {id(n.value) for n in ast.walk(tree)
            if isinstance(n, ast.Expr) and isinstance(n.value, (ast.Constant, ast.JoinedStr))}


def _enclosing_refs_alive(src, funcs, lineno):
    """True if the innermost function around `lineno` references ALIVE/deleted_at anywhere — covers
    reads that assemble the predicate into a `where` list (`where.append(_db.ALIVE)`) rather than
    inline. Ceiling: a function with two reads, only one filtered, would pass; acceptable for a lint."""
    inner = None
    for fn in funcs:
        if fn.lineno <= lineno <= (fn.end_lineno or fn.lineno):
            if inner is None or fn.lineno > inner.lineno:
                inner = fn
    if inner is None:
        return False
    seg = ast.get_source_segment(src, inner) or ""
    return "ALIVE" in seg or "deleted_at" in seg


def _read_statements(src, tree):
    """Yield (lineno, sql_text) for each SQL read (SELECT ... FROM <soft table>), one per
    statement, skipping docstrings and not descending into a matched read's inner fragments."""
    docs = _docstring_ids(tree)
    out = []

    class V(ast.NodeVisitor):
        def visit(self, node):
            if isinstance(node, (ast.JoinedStr, ast.Constant, ast.BinOp)) and id(node) not in docs:
                s = _flat(node)
                if s and "SELECT" in s.upper() and _FROM_RE.search(s):
                    out.append((node.lineno, s))
                    return  # a matched read owns its subtree; don't re-flag inner subqueries
            self.generic_visit(node)

    V().visit(tree)
    return out


def test_no_handwritten_alive_predicate():
    """`deleted_at IS NULL` is written by hand nowhere but the ALIVE constant — everything else
    routes through `_db.ALIVE`, so the soft-delete predicate has exactly one source of truth."""
    offenders = []
    for f, rel in _py_files():
        tree = ast.parse(f.read_text())
        docs = _docstring_ids(tree)
        for n in ast.walk(tree):
            if not isinstance(n, (ast.Constant, ast.JoinedStr)) or id(n) in docs:
                continue
            s = _flat(n)
            if s and "deleted_at IS NULL" in s:
                # the one allowed site: the `ALIVE = "deleted_at IS NULL"` definition in db_table.py
                if rel.name == "db_table.py":
                    continue
                offenders.append(f"{rel}:{n.lineno}")
    assert not offenders, (
        "hand-written `deleted_at IS NULL` — use the `_db.ALIVE` constant instead:\n  "
        + "\n  ".join(offenders))


def test_raw_reads_carry_alive():
    """Every raw read of a soft-deletable table hides tombstones — ALIVE is in the statement text
    or assembled in its enclosing function."""
    leaks = []
    for f, rel in _py_files():
        src = f.read_text()
        tree = ast.parse(src)
        funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for lineno, sql in _read_statements(src, tree):
            if "ALIVE" in sql or "deleted_at" in sql:
                continue
            if _enclosing_refs_alive(src, funcs, lineno):
                continue
            leaks.append(f"{rel}:{lineno}  {' '.join(sql.split())[:90]}")
    assert not leaks, (
        "raw SQL read of a soft-deletable table with no ALIVE filter (leaks tombstoned rows):\n  "
        + "\n  ".join(leaks))
