"""Declarative node JSON contract.

One ``NodeView`` dataclass is the single source of truth for every ``-o json`` node payload:
fields are declared once, tagged by view (``core`` / ``summary`` / ``full``) via
``field(metadata)``, and serialized by ``fields()`` projection — so adding a field is one edit and
a builder can't silently emit or drop a field (the guard against the "add a column, miss a builder,
`move` fails" bug class). Field declaration order IS the JSON key order.

Classification is the orthogonal ``type`` facet object (the ``type.*`` props with the ``type.``
prefix stripped), keeping every facet independently — a habit task is
``{"para":"task","habit":true}``, never a single lossy ``"task"`` that precedence-collapse would
produce.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields

from . import node_types as _nt
from .queries import node_props, _node_tags

CORE, SUMMARY, FULL = "core", "summary", "full"
#: a view includes its own fields plus all narrower views' fields
_INCLUDES = {CORE: {CORE}, SUMMARY: {CORE, SUMMARY}, FULL: {CORE, SUMMARY, FULL}}


def _f(view, **kw):
    """A dataclass field tagged with the narrowest view it belongs to."""
    return field(metadata={"view": view}, **kw)


def type_facet(props: dict) -> dict:
    """The node's orthogonal ``type.*`` facets as a dict, ``type.`` prefix stripped — a
    column-free, non-collapsing classification. Existence facets (habit/meetlog stored as
    the canonical ``"true"`` or ``""``) → ``True``; valued facets (``type.para=project``,
    ``type.date=day``, a sub-classified ``type.meetlog=dating``) keep the value. A bare task → ``{}``.
    ``date.*`` time-values are NOT facets (they live in the full payload's ``props``)."""
    out = {}
    for k, v in props.items():
        if k.startswith(_nt.TYPE_NS):
            out[k[len(_nt.TYPE_NS):]] = True if v in ("", _nt.EXISTENCE_TRUE) else v
    return out


@dataclass
class NodeView:
    """The serializable view of a node. Declaration order = JSON key order; ``metadata["view"]``
    decides which payloads a field appears in. Build via :func:`node_view`, emit via
    :meth:`to_dict`."""
    # ── core: present in every node payload (show / ls / tree / day / projects / summary) ──
    id: int       = _f(CORE, default=None)
    title: str    = _f(CORE, default=None)
    status: str   = _f(CORE, default=None)
    priority: str = _f(CORE, default=None)
    type: dict    = _f(CORE, default_factory=dict)   # orthogonal type.* facets, each kept independently
    # ── summary: the flat list views (ls / projects / summary / day) add identity + plan + tags ──
    parent_id: int      = _f(SUMMARY, default=None)
    scheduled_date: str = _f(SUMMARY, default=None)
    deadline_date: str  = _f(SUMMARY, default=None)
    created_at: str     = _f(SUMMARY, default=None)
    closed_at: str      = _f(SUMMARY, default=None)
    tags: list          = _f(SUMMARY, default_factory=list)
    # ── full: the whole node + its graph (`wl show -o json`). The caller populates these (their
    #    sub-queries live in commands/query.py); NodeView just declares the contract + order. ──
    body: str        = _f(FULL, default=None)
    ancestors: list  = _f(FULL, default_factory=list)
    props: dict      = _f(FULL, default_factory=dict)
    relations: dict  = _f(FULL, default_factory=dict)
    backrels: list   = _f(FULL, default_factory=list)
    links: list      = _f(FULL, default_factory=list)
    schedule: dict   = _f(FULL, default_factory=dict)
    children: list   = _f(FULL, default_factory=list)
    logs: list       = _f(FULL, default_factory=list)
    metrics: list    = _f(FULL, default_factory=list)
    clock: list      = _f(FULL, default_factory=list)

    def to_dict(self, view=SUMMARY) -> dict:
        """Project to a plain dict for the given view, in declaration order. Only fields tagged for
        ``view`` (or a view it includes) are emitted."""
        keep = _INCLUDES[view]
        return {f.name: getattr(self, f.name) for f in fields(self) if f.metadata["view"] in keep}


def node_view(con, n, view=SUMMARY) -> NodeView:
    """Build a :class:`NodeView` from a node row, populating the fields a ``view`` needs."""
    nv = NodeView(
        id=n["id"], title=n["title"], status=n["status"], priority=n["priority"],
        type=type_facet(node_props(con, n["id"])),
    )
    if view in (SUMMARY, FULL):
        nv.parent_id = n["parent_id"]
        nv.scheduled_date = n["scheduled_date"]
        nv.deadline_date = n["deadline_date"]
        nv.created_at = n["created_at"]
        nv.closed_at = n["closed_at"]
        nv.tags = _node_tags(con, n["id"])
    return nv
