"""worklog command handlers and their internal helpers.

Split by responsibility: state mutations, queries / views, tree / day rendering,
bulk import-apply, and one file per command (group): admin (init/config/migrate/themes),
dateinfo, goal (+ recap), alias, checkin, sched, plus the shared timenodes helpers.
This __init__ re-exports everything so cli.py (and tests) can import from
`worklog.commands` without knowing the internal split.
"""
from .state import (
    cmd_add,
    cmd_log,
    cmd_done,
    cmd_defer,
    cmd_start,
    cmd_stop,
    cmd_spent,
    cmd_link,
    cmd_unlink,
    cmd_relation,
    cmd_relation_set,
    cmd_relation_ready,
    cmd_relation_deps,
    cmd_relation_unclaimed,
    cmd_relation_claim,
    cmd_relation_unclaim,
    cmd_set,
    cmd_tag,
    cmd_tick,
    cmd_wait,
    cmd_reopen,
    cmd_cancel,
    cmd_unlog,
    cmd_relog,
    cmd_retag,
    cmd_active,
    cmd_node_edit,
    cmd_node_rm,
    cmd_node_reparent,
    cmd_agent,
    AGENT_HOOK_CHOICES,
    cmd_prop,
    cmd_prop_ls,
    cmd_prop_rm,
    cmd_clock,
    cmd_clock_ls,
    cmd_clock_edit,
    cmd_clock_rm,
    cmd_link_ls,
    cmd_link_group,
    cmd_tag_ls,
    cmd_tag_rm,
    cmd_tag_group,
    cmd_log_ls,
    cmd_log_group,
    _ids_list,
    _bulk_status_change,
    _edit_in_editor,
)
from .metric import (
    cmd_metric,
    _metric_id_arg,
)
from .query import (
    cmd_show,
    cmd_ls,
    cmd_find,
    cmd_focus,
    cmd_ancestors,
    cmd_descendants,
    cmd_agenda,
    cmd_projects,
    cmd_types,
    cmd_tags,
    cmd_props,
    cmd_metrics,
    cmd_changes,
    cmd_summary,
    cmd_logs,
    _show_one,
)
from .views import (
    cmd_tree,
    cmd_day,
    _tree_by,
    _tree_children,
    _print_tree,
    _print_day_activity,
    _print_default_tree,
    _render_day_group,
    _sec_sort_key,
    _sched_fires,
    _scheduled_node_ids,
    _date_label,
    _cn_weekday,
)
from .bulk import (
    cmd_import,
    cmd_apply,
    _import_node,
    _import_update,
    _parse_node_line,
    _parse_fieldop,
    _parse_wld,
    _validate_fieldop,
    _exec_update,
    _fieldop_desc,
    _apply_sub,
)
# meta.py was split into per-command modules (2026-06-13): admin / dateinfo / goal / alias /
# checkin / sched + the shared timenodes helpers.
from .admin import cmd_init, cmd_demo, cmd_config, cmd_migrate, cmd_themes, cmd_doctor
from .dateinfo import (
    cmd_dateinfo,
    cmd_date_set,
    cmd_date_ls,
    cmd_date_rm,
    cmd_date_import,
    cmd_date_group,
)
from .goal import (
    cmd_goal_set,
    cmd_goal_ls,
    cmd_goal_rm,
    cmd_goal_group,
    cmd_goal,
    cmd_summary_prop,
)
from .alias import cmd_alias_add, cmd_alias_ls, cmd_alias_rm, cmd_alias
from .checkin import (
    cmd_checkin,
    _checkin_collect,
    _is_interactive_tty,
    _multi_select_tty,
    _checkin_per_item,
)
from .sched import cmd_sched, cmd_sched_ls, cmd_sched_rm, cmd_sched_group, _norm_rrule
from .timenodes import _ensure_today_day
from .help import cmd_help, colorize_help, topic_exists, topic_names
from .semantic import cmd_query, cmd_reindex
