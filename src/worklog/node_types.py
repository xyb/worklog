"""The `type.*` reserved-property namespace — single source of truth for node
classification.

A node's category is a set of independent, namespaced reserved properties
(see DESIGN §"node types"):

- ``type.para``  — responsibility-line role: ``area`` / ``project`` / ``task``
- ``type.date``  — which time level a node is: ``lifetime`` … ``day``
- ``type.habit`` / ``type.meetlog`` — soft subtypes, *existence-based*
  (the key being present is the signal; the value is an optional user
  sub-classification, e.g. ``type.meetlog=dating``)

Because these are separate ``(node_id, key)`` prop rows, orthogonal concepts
coexist — a recurring habit task is ``type.para=task`` + ``type.habit`` at once,
a combination a single mutually-exclusive classification could never express.

Time values live in the sibling ``date.*`` namespace: ``date.period`` is the
canonical "which period" value (``2026-06-14`` / ``2026-06`` / ``2026-W24``);
``date.start`` / ``date.end`` pin an explicit interval for levels whose
boundaries are ambiguous (week / quarter / decade / arbitrary spans).

This module is pure (no DB, no I/O) so it can be unit-tested in isolation and
imported anywhere. The accessors take a plain ``{key: value}`` props dict; a
thin DB-backed wrapper lives where the prop table is read.

Reserved-key value domains are enforced by :func:`validate_prop`, which the
single prop-write API funnels every write through (DESIGN hard rule: no reserved
key may be written by a path that bypasses this validator)."""
from __future__ import annotations

import calendar
import datetime as _dt
import re

# ── canonical key names ──────────────────────────────────────────────────────
TYPE_NS = "type."     # the classification namespace prefix
DATE_NS = "date."     # the time-value namespace prefix
K_PARA = "type.para"
K_DATE = "type.date"
K_HABIT = "type.habit"
K_MEETLOG = "type.meetlog"
K_PERIOD = "date.period"
K_START = "date.start"
K_END = "date.end"

#: every reserved key (writes to these are validated + may not bypass the write API)
RESERVED_KEYS = frozenset({K_PARA, K_DATE, K_HABIT, K_MEETLOG, K_PERIOD, K_START, K_END})

# ── value domains (in display order) ─────────────────────────────────────────
#: responsibility-line roles, coarse → fine
PARA_ROLES = ("area", "project", "task")
#: time levels, coarse → fine (matches the old ``_TIME_LEVELS`` set)
DATE_LEVELS = ("lifetime", "decade", "year", "quarter", "month", "week", "day")
#: existence-based soft subtypes
SOFT_TYPES = ("habit", "meetlog")

#: every legacy kind the type.* model preserves losslessly (a migrated node of one of these
#: must round-trip exactly). ``signal`` and any user-custom kind are intentionally NOT here —
#: they collapse to a bare node (``signal`` is retired by design), which is a deliberate
#: transformation, not data loss.
KNOWN_KINDS = frozenset(PARA_ROLES) | frozenset(DATE_LEVELS) | frozenset(SOFT_TYPES)

#: the value the system writes for an existence-only prop with no sub-class (DESIGN ⑤:
#: canonical is ``"true"`` — meaningful + satisfies prop.value NOT NULL; reads don't care)
EXISTENCE_TRUE = "true"

#: time levels whose interval is unambiguous from ``date.period`` alone (no stored span)
SELF_DESCRIBING_LEVELS = ("year", "month", "day")
#: time levels whose boundaries are ambiguous → ``date.start`` / ``date.end`` stored at creation
EXPLICIT_SPAN_LEVELS = ("decade", "quarter", "week")

_PARA_RANK = {r: i for i, r in enumerate(PARA_ROLES)}
_DATE_RANK = {lv: i for i, lv in enumerate(DATE_LEVELS)}

# ── period format, per level ─────────────────────────────────────────────────
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_PERIOD_RE = {
    "day": re.compile(r"^\d{4}-\d{2}-\d{2}$"),
    "month": re.compile(r"^\d{4}-\d{2}$"),
    "year": re.compile(r"^\d{4}$"),
    "week": re.compile(r"^\d{4}-W\d{2}$"),
    "quarter": re.compile(r"^\d{4}-Q[1-4]$"),
    "decade": re.compile(r"^\d{4}s$"),
}


def is_reserved_key(key: str) -> bool:
    """True iff ``key`` is a system-reserved prop key with a controlled value domain."""
    return key in RESERVED_KEYS


def validate_prop(key, value):
    """Validate + normalize a ``(key, value)`` about to be written to the prop table.

    Returns the (possibly normalized) value. Raises ``ValueError`` with a clear,
    user-facing message when a *reserved* key carries an out-of-domain value.
    Non-reserved keys pass through unchanged (user props have free values).

    - ``type.para`` → must be one of :data:`PARA_ROLES`
    - ``type.date`` → must be one of :data:`DATE_LEVELS`
    - ``type.habit`` / ``type.meetlog`` → existence-based: empty/None → ``"true"``,
      any other value kept verbatim (user sub-classification)
    - ``date.start`` / ``date.end`` → ``YYYY-MM-DD``
    - ``date.period`` → one of the canonical period forms
    """
    if key == K_PARA:
        if value not in PARA_ROLES:
            raise ValueError(
                f"invalid {K_PARA}: {value!r} (valid: {', '.join(PARA_ROLES)})")
        return value
    if key == K_DATE:
        if value not in DATE_LEVELS:
            raise ValueError(
                f"invalid {K_DATE}: {value!r} (valid: {', '.join(DATE_LEVELS)})")
        return value
    if key in (K_HABIT, K_MEETLOG):
        return EXISTENCE_TRUE if value in (None, "") else value
    if key in (K_START, K_END):
        if not (isinstance(value, str) and _DATE_RE.fullmatch(value)):
            raise ValueError(f"invalid {key}: {value!r} (expected YYYY-MM-DD)")
        return value
    if key == K_PERIOD:
        level = level_of_period(value)
        if level is None or not valid_period(level, value):   # real-date check, not just shape
            raise ValueError(
                f"invalid {K_PERIOD}: {value!r} "
                "(expected a real YYYY / YYYY-MM / YYYY-MM-DD / YYYY-Www / YYYY-Qn / YYYYs)")
        return value
    return value


def level_of_period(period):
    """Infer the time level a canonical ``date.period`` string denotes, or None if
    it matches no known form. (Self-identifying: ``2026-06-14`` → day, ``2026-06`` →
    month, ``2026`` → year, ``2026-W24`` → week, ``2026-Q2`` → quarter, ``2020s`` →
    decade.) ``lifetime`` has no period and is never inferred here."""
    if not isinstance(period, str):
        return None
    for level in ("day", "month", "year", "week", "quarter", "decade"):
        if _PERIOD_RE[level].fullmatch(period):
            return level
    return None


def valid_period(level: str, period: str) -> bool:
    """Whether ``period`` is a well-formed AND real value for time ``level``. ``lifetime``
    takes no period (empty string is valid). Every other level must match its canonical regex
    *and* denote a real calendar period — ``2026-W99`` / ``2026-13`` match the shape but aren't
    real, so they're rejected here (``span_of`` is the real-date oracle). This keeps it the single
    truthful gate every writer relies on before calling ``span_of``."""
    if level == "lifetime":
        return period in ("", None)
    rx = _PERIOD_RE.get(level)
    if not (rx and isinstance(period, str) and rx.fullmatch(period)):
        return False
    try:
        span_of(level, period)   # raises on a shape-valid but unreal period (week 99, month 13, …)
        return True
    except ValueError:
        return False


def span_of(level, period):
    """The ``(start, end)`` calendar dates (``YYYY-MM-DD``) a time node covers,
    computed from its level + period. ``lifetime`` → ``(None, None)`` (a global
    singleton, exempt from date arithmetic). Used to seed ``date.start`` /
    ``date.end`` for explicit-span levels and to frame range rollups."""
    if level == "lifetime":
        return (None, None)
    if level == "day":
        _dt.date.fromisoformat(period)        # validate it's a real calendar date (raises otherwise)
        return (period, period)
    if level == "month":
        y, m = int(period[:4]), int(period[5:7])
        last = calendar.monthrange(y, m)[1]
        return (f"{y:04d}-{m:02d}-01", f"{y:04d}-{m:02d}-{last:02d}")
    if level == "year":
        y = int(period)
        return (f"{y:04d}-01-01", f"{y:04d}-12-31")
    if level == "quarter":
        y, q = int(period[:4]), int(period[6])
        first_m = (q - 1) * 3 + 1
        last_m = first_m + 2
        last_d = calendar.monthrange(y, last_m)[1]
        return (f"{y:04d}-{first_m:02d}-01", f"{y:04d}-{last_m:02d}-{last_d:02d}")
    if level == "week":
        y, w = int(period[:4]), int(period[6:8])
        mon = _dt.date.fromisocalendar(y, w, 1)
        sun = _dt.date.fromisocalendar(y, w, 7)
        return (mon.isoformat(), sun.isoformat())
    if level == "decade":
        start = int(period[:4])
        return (f"{start:04d}-01-01", f"{start + 9:04d}-12-31")
    raise ValueError(f"cannot compute span for level {level!r} / period {period!r}")


def date_props_for(level, period) -> dict:
    """The ``date.*`` props a time node of ``level`` with canonical ``period`` should carry:
    ``{date.period}`` plus ``{date.start, date.end}`` for the explicit-span levels
    (week / quarter / decade). Returns ``{}`` for ``lifetime``, a missing/empty period, or a
    period that isn't valid for the level (so a non-canonical title yields no date.* — the node
    keeps only its level, exactly as before).

    This is the SINGLE definition of the period→span mapping. Every path that completes a time
    node — create_node, write_kind_type_props (import/apply), write_time_props (find-or-create /
    recap), the kind→type.* backfill, and ``wl set type.date`` — derives its date.* props here so
    they can never diverge (a span tweak or a new explicit-span level is a one-line change)."""
    if not level or level == "lifetime" or not period or not valid_period(level, period):
        return {}
    props = {K_PERIOD: period}
    if level in EXPLICIT_SPAN_LEVELS:
        start, end = span_of(level, period)
        props[K_START] = start
        props[K_END] = end
    return props


# ── accessors over a {key: value} props dict ─────────────────────────────────
def para_of(props):
    """The node's ``type.para`` role, or None."""
    return props.get(K_PARA)


def date_level_of(props):
    """The node's ``type.date`` level, or None."""
    return props.get(K_DATE)


def is_habit(props) -> bool:
    """Existence-based: the node is a habit iff ``type.habit`` is present (value ignored)."""
    return K_HABIT in props


def is_meetlog(props) -> bool:
    """Existence-based: the node is a meetlog iff ``type.meetlog`` is present (value ignored)."""
    return K_MEETLOG in props


def type_props(props) -> dict:
    """The ``type.*`` subset of a props dict (drops ``date.*`` and user props)."""
    return {k: v for k, v in props.items() if k.startswith("type.")}


def para_rank(role) -> int:
    """Display-order rank of a ``type.para`` role (unknown sorts last)."""
    return _PARA_RANK.get(role, len(PARA_ROLES))


def date_rank(level) -> int:
    """Display-order rank of a ``type.date`` level (unknown sorts last)."""
    return _DATE_RANK.get(level, len(DATE_LEVELS))
