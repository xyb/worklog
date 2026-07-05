"""Graph operations on the node graph — the SINGLE SOURCE for everything that walks an edge.

THE GRAPH (worklog has no foreign keys; the graph is three edge kinds over one `node` table):
  - **tree**      — `parent_id` self-reference (area→project→task, year→…→day).
  - **relation**  — `relation.*` props hold comma-id lists (split-from/into, related); a
                    task↔task association graph, distinct from the parent/child tree.
  - **spokes**    — log/tag/link/prop/sched/clock/metric all carry `node_id`; deleting a node
                    must tombstone them too (the app-level stand-in for `ON DELETE CASCADE`,
                    since FK enforcement is off).

WHAT LIVES HERE: every function that *traverses or mutates an edge* —
  - tree traversal      : `_ancestors_chain` (up), `_collect_descendants` (down)
  - cascade delete      : `soft_delete_node` (+ spokes), `soft_delete_log` (+ metrics)
  - structural membership: `_project_members` (a project's tasks via parent + shared tags)
  - relation graph      : `relation_view` (read, bidirectional), `_apply_relation` (write both
                          sides), `_backrels` (text-derived inbound `#id` references)

LAYERING RULE (follow this — it's what keeps the import graph acyclic):

    db_table / models                         (no deps)
        ↑
    queries.py   — attribute/classification primitives + the relation DATA primitives
        ↑          (classify_types, nodes_with_tag, node_type, _node_exists, _parse_id_list,
        |           _add_/_remove_id_from_prop_list, _RELATION_* constants, make_node_filter).
    graph.py     — THIS module: graph operations, importing those primitives ONE-WAY.
        ↑
    commands/    — handlers + view-layer grouping helpers (_node_project / _sec_group, which
                   walk the tree, live up here and call graph._ancestors_chain).

  • graph.py imports FROM queries; queries MUST NOT import graph (would cycle). If a graph op
    needs a new attribute primitive, add it to queries and import it here — don't reach sideways.
  • An operation belongs here if it walks an edge — with ONE deliberate exception: the `--by`
    grouping derivations (`_node_project` / `_node_bucket` / `_node_plan` / `_sec_group`) live here
    as a cohesive unit even though only `_node_project` walks the tree (the other two just read
    tags), because `_sec_group` ties them together and every command needs them from one shared
    source. Outside that group the rule is strict: a pure attribute filter that walks no edge
    (`make_node_filter` — filters one node by its tags/status/props) stays in queries; don't pull
    edgeless code in just because it's node-related, or this module becomes a junk drawer.
  • Free functions, not a class — the node graph is one table with cycle-safe walks; an ORM/Graph
    class would add ceremony for zero benefit (DESIGN G3). This IS the repository layer already.

INVARIANTS — no foreign key enforces any of these; app code holds them on the write paths and
`check_integrity` (`wl doctor`) audits them on demand:
  1. every `parent_id` points at a LIVE node (no dangling), and the parent chain is acyclic;
  2. every live spoke row's `node_id` is a live node (`soft_delete_node` cascades the spokes);
  3. every `relation.*` ref is a live node, and relation edges are symmetric
     (A.split_into ∋ B ⟺ B.split_from ∋ A; related is mutual; _apply_relation writes both sides), and no relation is self-referential.
  4. every log's `logged_at` is a full UTC instant (`YYYY-MM-DD HH:MM:SS`), never a bare date —
     a date loses intra-day ordering and renders no `@HH:MM`.
Because nothing in the DB enforces them, the everyday walks ALSO stay defensive (cycle-safe
`seen` sets, `_node_exists` guards) so legacy/corrupt data degrades gracefully instead of hanging
or crashing; `wl doctor` is the explicit "find + fix the dirt" signal.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import db_table as _db
from .models import Log, Metric, Node, Prop, Tag
from .helpers import GENERIC_TAGS
from .queries import (
    filter_workitems,
    nodes_with_tag,
    node_type,
    _node_exists,
    _parse_id_list,
    _add_id_to_prop_list,
    _remove_id_from_prop_list,
    _RELATION_TYPES,
    _RELATION_KEY_LABEL,
    _RELATION_REVERSE_LABEL,
)


# ── tree traversal (parent_id self-reference) ──────────────────────────────────

def _ancestors_chain(con, node_id):
    """The path list[Node] from the top-level root down to `node` (inclusive). Cycle-safe:
    FK enforcement is off so `parent_id` integrity isn't DB-guaranteed and a bad/legacy graph
    could contain a cycle — a visited set stops the walk re-entering it rather than looping."""
    chain = []
    cur = Node.get(con, node_id)
    if not cur:
        return chain
    chain.append(cur)
    seen = {node_id}
    while cur["parent_id"] and cur["parent_id"] not in seen:
        seen.add(cur["parent_id"])
        cur = Node.get(con, cur["parent_id"])
        if not cur:
            break
        chain.append(cur)
    return list(reversed(chain))


def _collect_descendants(con, root_id, *, include_deleted=False):
    """Recursively collect all descendant ids of a node (excluding self). By default only
    live nodes; `include_deleted=True` walks through tombstoned nodes too, so a structural
    cascade (soft-delete subtree / cycle check) reaches live nodes hanging under an already-
    tombstoned intermediate."""
    acc = []
    stack = [root_id]
    seen = {root_id}  # cycle-safe: FK is off, so a bad parent_id graph could loop
    while stack:
        pid = stack.pop()
        children = _db.query(con, "node", cols="id", parent_id=pid, include_deleted=include_deleted)
        for c in children:
            if c["id"] in seen:
                continue
            seen.add(c["id"])
            acc.append(c["id"])
            stack.append(c["id"])
    return acc


# ── cascade soft-delete (spoke tables keyed by node_id / log_id) ───────────────

# every spoke table references node_id; soft-deleting a node tombstones these too —
# the app-level stand-in for the old FK ON DELETE CASCADE (foreign_keys is now OFF).
_NODE_SPOKES = ("log", "tag", "link", "prop", "sched", "clock", "metric")


def soft_delete_node(con, nid):
    """Soft-delete a node and everything hanging off it (log / tag / link / prop /
    sched / clock / metric, all keyed by node_id) — the app-level replacement for the
    old `ON DELETE CASCADE` now that FK enforcement is off. Tombstones, never removes;
    reversible by clearing `deleted_at`. No commit. Returns the node rowcount."""
    n = Node.delete(con, id=nid)
    for spoke in _NODE_SPOKES:   # _NODE_SPOKES is the single source — add a spoke table here only
        _db.delete(con, spoke, node_id=nid)
    return n


def soft_delete_log(con, log_id):
    """Soft-delete a log and its metrics (the old `metric.log_id` CASCADE, now
    app-level). Tombstones, never removes. No commit. Returns the log rowcount."""
    n = Log.delete(con, id=log_id)
    Metric.delete(con, log_id=log_id)
    return n


# ── structural membership ──────────────────────────────────────────────────────

def _project_members(con, proj_id):
    """Set of task/meetlog/habit ids linked to a project: structural children (parent) + shared semantic tags"""
    ids = set()
    proj_tags = {r.tag for r in Tag.query(con, node_id=proj_id)} - GENERIC_TAGS
    for n in filter_workitems(con, Node.query(con, parent_id=proj_id)):
        ids.add(n["id"])
    if proj_tags:
        for r in nodes_with_tag(con, proj_tags, types=("task", "meetlog", "habit"), cols="id"):
            ids.add(r["id"])
    return ids


# ── relation graph (relation.* props; A.split_into ∋ B ⇔ B.split_from ∋ A) ─────

def relation_view(con, nid):
    """Resolved bidirectional relations for a node: an ordered dict
    {'split-from': [ids], 'split-into': [ids], 'related': [ids]}. Unions the node's own
    relation.* props with the reverse derived from every other node's props
    (A.split_into ∋ nid ⇒ nid.split-from ∋ A), so one-sided data still shows both ways.
    Each list is order-preserving + deduped, excludes nid itself, and only includes live
    nodes (a relation to a soft-deleted node is dropped from the view)."""
    merged = {t: [] for t in _RELATION_TYPES}
    seen = {t: set() for t in _RELATION_TYPES}

    def _add(label, i):
        if i == nid or i in seen[label] or not _node_exists(con, i):
            return
        seen[label].add(i)
        merged[label].append(i)

    # own props
    for r in Prop.query(con, node_id=nid):
        lbl = _RELATION_KEY_LABEL.get(r.key)
        if lbl:
            for i in _parse_id_list(r.value):
                _add(lbl, i)
    # reverse: any other node whose relation.* list points at nid
    for r in Prop.query(con, key__like="relation.%"):
        lbl = _RELATION_REVERSE_LABEL.get(r.key)
        if lbl and nid in _parse_id_list(r.value):
            _add(lbl, r.node_id)
    return merged


def _apply_relation(con, nid, rtype, others, *, rm=False):
    """Add (or with rm=True, remove) a relation between `nid` and each id in `others`,
    writing BOTH sides (split-from ↔ split-into; related is symmetric). Self-relations are
    skipped. No commit (caller owns the transaction). Returns the applied other-ids.
    The shared core behind `wl relation` and `wl add --relation`."""
    key, inv = _RELATION_TYPES[rtype]
    done = []
    for o in others:
        if o == nid:
            continue
        if rm:
            _remove_id_from_prop_list(con, nid, key, o)
            _remove_id_from_prop_list(con, o, inv, nid)
        else:
            _add_id_to_prop_list(con, nid, key, o)
            _add_id_to_prop_list(con, o, inv, nid)
        done.append(o)
    return done


def _backrels(con, nid):
    """Back-relations ('what links here' / 维基百科链入): other nodes whose TEXT mentions this
    node's id — a `#<nid>` or `WL#<nid>` reference in a log body or a node body. Returns sorted
    distinct node ids, excluding self. A bare `#` or a `WL#` prefix counts; a letter run like
    `PR#`/`LUM-` does NOT (so a GitHub PR / Linear ref isn't mistaken for a node ref). Unlike the
    stored relation.* props, these are MACHINE-DERIVED (computed by scanning text), so the show
    view marks the row with a leading `=` + italic to set it apart from the real relations."""
    import re
    # candidates via a cheap LIKE, then a word-boundary regex confirms it's a real node ref
    pat = re.compile(rf"(?<![A-Za-z0-9])(?:WL)?#0*{nid}(?!\d)")
    found = set()
    like = f"%#{nid}%"
    # skip goal/summary logs: a day's 今日目标 / 日终小结 (incl. `wl recap`, which rewrites
    # the summary) lists a bunch of in-progress #task ids in passing — that roll-call isn't a
    # substantive reference, so it shouldn't backrel every listed task to the day node.
    for src_id, body, tag in _db.query(con, "log", cols="DISTINCT node_id, body, tag", body__like=like):
        if src_id != nid and tag not in ("goal", "summary") and pat.search(body or ""):
            found.add(src_id)
    for src_id, body in _db.query(con, "node", cols="id, body", body__like=like):
        if src_id != nid and pat.search(body or ""):
            found.add(src_id)
    return sorted(found)


# ── `--by` grouping derivations ────────────────────────────────────────────────
# Bucket/derive a node for the `wl tree --by project/priority/plan` secondary grouping. They live
# here (not in a command module) because `_node_project` walks the tree and all four must be
# reachable from any command via the same single source; `cli` re-exports them for tests. Tag-based
# bucketing (work/personal, planned) rides along since it's part of the same grouping concept.

def _node_bucket(con, nid):
    """Bucket a node into work / personal / other by work/personal tag."""
    tags = {r.tag for r in Tag.query(con, node_id=nid)}
    if "work" in tags:
        return "work"
    if "personal" in tags:
        return "personal"
    return "other"


def _node_project(con, nid):
    """Return the project ancestor (id, title) of a node, or (None, '(unassigned)') if none."""
    for p in _ancestors_chain(con, nid):
        if node_type(con, p) == "project":
            return p["id"], p["title"]
    return None, "(unassigned)"


def _node_plan(con, nid, sched_ids):
    """Derive planned vs unplanned: scheduled that day (or carrying the transitional
    'planned' tag) = planned; everything else = unplanned. The old separate
    'unplanned (untagged)' bucket was a migration-era distinction — now that
    planned/unplanned is derived from sched, anything not scheduled is just unplanned."""
    if nid in sched_ids:
        return "planned"
    tags = {r.tag for r in Tag.query(con, node_id=nid)}
    if "planned" in tags:
        return "planned"
    return "unplanned"


def _sec_group(con, nid, n, by, sched_ids):
    """(key, display title) for the secondary group. by in project/priority/plan."""
    if by == "priority":
        label = {"A": "P0", "B": "P1", "C": "P2"}.get(n["priority"], "—")
        return label, label
    if by == "plan":
        label = _node_plan(con, nid, sched_ids)
        return label, label
    pid, ptitle = _node_project(con, nid)
    return (pid if pid is not None else ptitle), ptitle


# ── integrity check (the cost of no foreign keys) ──────────────────────────────
# FK enforcement is OFF, so nothing in the DB stops the graph from going inconsistent: a
# parent_id pointing at a deleted node, a parent cycle, a spoke row whose node was deleted out from
# under it, a relation.* ref to a dead node, or a one-sided relation. The everyday read/write paths
# stay defensive (cycle-safe walks, soft_delete cascade, wl relation writes both sides), but legacy
# data, a manual SQL edit, or a half-applied bulk op can still leave dirt. `wl doctor` scans for it.

@dataclass
class GraphIssue:
    """One graph inconsistency found by check_integrity. `node_id` is the offending node (for a
    spoke orphan, the spoke row's dangling node_id). `kind` is one of: dangling_parent / cycle /
    orphan_spoke / dead_relation / asymmetric_relation / self_relation / bare_timestamp."""
    kind: str
    node_id: int
    detail: str


def check_integrity(con):
    """Scan the live graph for the inconsistencies no foreign key prevents, plus the data invariant that a log's logged_at is a full instant (not a bare date). Pure read, never
    mutates. Returns list[GraphIssue]. On-demand batch: reads node + each spoke + relation props
    ONCE and checks in Python (no per-node query) — O(N + spoke rows + relation props)."""
    issues = []
    # one batched read: every live node's id + parent
    parent_of = {r["id"]: r["parent_id"] for r in _db.query(con, "node", cols="id, parent_id")}
    live = set(parent_of)

    # 1. dangling parent_id — parent set but missing or soft-deleted
    for nid, pid in parent_of.items():
        if pid is not None and pid not in live:
            issues.append(GraphIssue("dangling_parent", nid, f"parent #{pid} is missing or deleted"))

    # 2. parent cycle — iterative DFS coloring; each node visited once (O(N)).
    #    white=unseen, "gray"=on the current path, "black"=proven to reach a root/dangling/known-safe.
    color = {}
    for start in parent_of:
        if color.get(start):
            continue
        path, cur = [], start
        while cur is not None and cur in live and color.get(cur) is None:
            color[cur] = "gray"
            path.append(cur)
            cur = parent_of[cur]
        if cur is not None and color.get(cur) == "gray":   # walked back into the current path → cycle
            loop = path[path.index(cur):]
            chain = "->".join(f"#{x}" for x in loop)
            for n in loop:
                issues.append(GraphIssue("cycle", n, f"on a parent_id cycle: {chain}"))
        for n in path:
            color[n] = "black"

    # 3. orphan spoke — a live spoke row whose node_id isn't a live node (cascade missed it, or dirt)
    for spoke in _NODE_SPOKES:
        for r in _db.query(con, spoke, cols="DISTINCT node_id"):
            sid = r["node_id"]
            if sid not in live:
                issues.append(GraphIssue("orphan_spoke", sid, f"live {spoke} row(s) on missing/deleted node #{sid}"))

    # 4 & 5. relation.* — dead refs + one-sided edges. Read every relation prop once.
    rel = {}   # node_id -> {key: set(ref ids)}
    for r in _db.query(con, "prop", cols="node_id, key, value", key__like="relation.%"):
        rel.setdefault(r["node_id"], {})[r["key"]] = set(_parse_id_list(r["value"]))
    inverse_key = {own: inv for own, inv in _RELATION_TYPES.values()}   # own relation key -> inverse key
    for nid, keys in rel.items():
        if nid not in live:
            continue   # owner node is dead — orphan_spoke already reports this prop; auditing its
            #            relations on top would spuriously demand a back-edge to a dead node.
        for key, refs in keys.items():
            inv = inverse_key.get(key)
            for ref in refs:
                if ref == nid:   # self-referential edge — write path skips it, view drops it; surface the dirt
                    issues.append(GraphIssue("self_relation", nid, f"relation {key} points at itself"))
                elif ref not in live:
                    issues.append(GraphIssue("dead_relation", nid, f"relation {key} points at missing/deleted node #{ref}"))
                elif inv and nid not in rel.get(ref, {}).get(inv, set()):
                    issues.append(GraphIssue("asymmetric_relation", nid,
                                             f"{key} #{ref}, but #{ref} has no matching {inv} back to #{nid}"))

    # 6. bare-timestamp logs — logged_at must be a full instant, not a date-only string. A bare date
    #    (legacy `wl log --date`, or a manual SQL edit) loses intra-day ordering and renders no
    #    @HH:MM. One batched read of the log table; "YYYY-MM-DD" is 10 chars, a full instant is 19.
    for r in _db.query(con, "log", cols="id, node_id, logged_at"):
        ts = r["logged_at"]
        if ts and len(ts) < 19:
            issues.append(GraphIssue("bare_timestamp", r["node_id"],
                                     f"log #L{r['id']} has a date-only logged_at '{ts}' (no time)"))
    return issues
