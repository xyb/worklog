"""View DTOs for all worklog command groups.

All @text_renderer handlers and JSONFormatter payloads use dataclasses from
this module. Keeping them central makes the JSON contract visible without
reading individual command handlers.

Naming: <Verb><Entity>Result for write operations; <Entity><Operation>Result
for reads. The one exception is RecapDiffEntry, which is a sub-record inside
RecapDiffResult (not a top-level command result).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..node_schema import NodeSummaryView


# ── log / relog / unlog ──────────────────────────────────────────────────────

@dataclass
class LogWriteResult:
    id: int
    node_id: int
    body: str
    logged_at: str
    status_changed: bool
    metrics_added: int
    time_defaulted: bool = False   # --date given without --time → the time was filled with "now"


@dataclass
class LogShowResult:
    id: int
    node_id: int
    tag: str | None
    body: str
    logged_at: str
    title: str
    label: str


@dataclass
class RelogResult:
    id: int
    node_id: int
    tag: str | None
    body: str
    logged_at: str
    canceled: bool = False


@dataclass
class UnlogResult:
    deleted: list        # list[int]
    node_id: int | None
    metrics_deleted: int
    messages: list       # list[str]


# ── clock ─────────────────────────────────────────────────────────────────────

@dataclass
class SpentResult:
    id: int
    node_id: int
    start_at: str
    end_at: str
    elapsed_sec: int
    mins: int


@dataclass
class ClockEditResult:
    id: int
    node_id: int
    start_at: str
    end_at: str | None
    elapsed_sec: int | None
    clock_id: int    # display alias for id; kept for renderer
    summary: str     # human-readable change description; kept for renderer


@dataclass
class ClockRmResult:
    deleted: list    # list[int]
    count: int


# ── tick ──────────────────────────────────────────────────────────────────────

@dataclass
class TickResult:
    node_id: int
    log_id: int
    done: bool


# ── prop ──────────────────────────────────────────────────────────────────────

@dataclass
class PropRmResult:
    key: str
    node_id: int
    removed: int
    from_log: bool


# ── node write operations ─────────────────────────────────────────────────────

@dataclass
class SetResult:
    node_id: int
    key: str
    value: str


@dataclass
class SetLogResult:
    node_id: int
    key: str
    body: str
    logged_at: str
    value: str


@dataclass
class RetagResult:
    log_id: int
    tag: str | None


@dataclass
class NodeRmResult:
    deleted: list    # list[int]
    count: int


@dataclass
class NodeReparentResult:
    node_id: int
    parent_id: int | None
    where: str       # "the top level" or "#<parent_id>"


@dataclass
class NodeEditResult:
    id: int
    title: str
    status: str | None
    priority: str | None
    scheduled_date: str | None
    deadline_date: str | None
    summary: str         # comma-joined list of changed field=value pairs
    conflicts: list      # list[str] — stale type.* props alongside new type.para
    para: str | None


# ── goal / recap ──────────────────────────────────────────────────────────────

@dataclass
class RecapDiffEntry:
    log_id: int
    node_id: int
    title: str
    logged_at: str
    body: str


@dataclass
class RecapDiffResult:
    recap_at: str | None
    changes: list        # list[RecapDiffEntry]


@dataclass
class GoalSetTargetsResult:
    node_id: int
    targets: list        # list[int]


# ── projects (query group) ────────────────────────────────────────────────────

@dataclass
class ProjectCounts:
    done: int
    doing: int
    pending: int
    total: int


@dataclass
class ProjectResult:
    project: NodeSummaryView
    counts: ProjectCounts
    latest_activity: str | None


# ── dateinfo / date group ─────────────────────────────────────────────────────

@dataclass
class DateInfoImportResult:
    imported: int


@dataclass
class DateInfoSetResult:
    date: str
    label: str
    weekday: str


@dataclass
class DateInfoClearResult:
    date: str
    cleared: int


@dataclass
class DateInfoShowResult:
    date: str
    label: str | None
    weekday: str
