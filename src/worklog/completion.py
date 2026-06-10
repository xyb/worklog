"""Shell completion generation (fish / bash / zsh).

argparse is the source of truth: each generator walks `build_parser()`
and emits a completion script tailored to that shell. The scripts run
under init-load (`wl print-completion fish | source` in shell rc), so
new subcommands / flags propagate automatically.

Dynamic completion (node id / tag / date) uses SQLite directly via
helper functions inlined into each script — no Python startup, sub-50ms
Tab response. The helpers know the DB resolution order ($WORKLOG_DB,
then $XDG_DATA_HOME/worklog/worklog.db).
"""
from __future__ import annotations

import argparse
import re
import sys


_FISH_HELPERS = {
    # (sub_cmd, opt_name) → fish completion source
    # opt_name None = positional argument
    ("__any__", "--parent"): "(__wl_list_nodes)",
    ("__any__", "--root"): "(__wl_list_nodes)",
    ("__any__", "--id"): "(__wl_list_nodes)",
    ("__any__", "--node"): "(__wl_list_nodes)",
    ("__any__", "--ids"): "(__wl_list_nodes)",
    ("__any__", "--tag"): "(__wl_list_tags)",
    ("sched", "--recur"): "(__wl_recur_suggestions)",
    # time / date related -> date suggestions
    ("log", "--date"): "(__wl_date_suggestions)",
    ("logs", "--date"): "(__wl_date_suggestions)",
    ("unlog", "--date"): "(__wl_date_suggestions)",
    ("dateinfo", "date"): "(__wl_date_suggestions)",
    ("dateinfo", None): "(__wl_date_suggestions)",
    ("day", "date"): "(__wl_date_suggestions)",
    ("sched", "when"): "(__wl_date_suggestions)",   # sched takes a concrete on_date (someday rejected)
    ("defer", "date"): "(__wl_defer_suggestions)",  # defer also takes someday (fuzzy backlog)
}
# subcommands whose default positional argument takes a node id (when not explicitly specified)

_FISH_POSITIONAL_NODE = {"log", "done", "defer", "start", "stop", "wait", "reopen",
                        "cancel", "tick", "link", "set", "show", "focus", "ancestors",
                        "descendants", "spent", "unlog", "relog"}

_FISH_HELPER_FUNCTIONS = r"""# --- helper functions (dynamic queries against worklog.db; no Python startup, fast) ---
function __wl_db_path
    # $WORKLOG_DB env, else $XDG_DATA_HOME/worklog/worklog.db (default ~/.local/share/worklog/worklog.db)
    if set -q WORKLOG_DB
        echo $WORKLOG_DB
    else if set -q XDG_DATA_HOME
        echo $XDG_DATA_HOME/worklog/worklog.db
    else
        echo $HOME/.local/share/worklog/worklog.db
    end
end

function __wl_list_nodes
    set -l db (__wl_db_path)
    test -f $db; or return
    # SQLite char(9) = tab (fish completion uses \t to separate token + desc)
    sqlite3 $db "SELECT id || char(9) || title FROM node WHERE (status IS NULL OR status NOT IN ('DONE', 'CANCELED')) AND deleted_at IS NULL ORDER BY id DESC LIMIT 80" 2>/dev/null
end

function __wl_list_tags
    set -l db (__wl_db_path)
    test -f $db; or return
    sqlite3 $db "SELECT DISTINCT tag FROM tag WHERE deleted_at IS NULL ORDER BY tag" 2>/dev/null
end

function __wl_date_suggestions
    # concrete day — accepted by day/log/logs/dateinfo/sched. period words resolve to that
    # period's first day (next-week → next Monday). The fuzzy backlog token lives in defer only.
    printf 'today\ttoday\nyesterday\tyesterday\nday-before-yesterday\tday before yesterday\ntomorrow\ttomorrow\nday-after-tomorrow\tday after tomorrow\nnext-week\tnext Monday\nnext-month\t1st of next month\nnext-quarter\t1st of next quarter\n'
    set -l today (date +%Y-%m-%d)
    printf '%s\ttoday YYYY-MM-DD\n' $today
end

function __wl_defer_suggestions
    # defer also accepts someday (fuzzy backlog); next-week/-month/-quarter stay coarse buckets
    __wl_date_suggestions
    printf 'someday\tno specific time\n'
end

function __wl_recur_suggestions
    printf 'daily\tevery day\n'
    printf 'weekly:Mon,Wed,Fri\tMon/Wed/Fri\n'
    printf 'weekly:Sat,Sun\tweekends\n'
    printf 'weekly:-1\tevery Sunday (last day)\n'
    printf 'monthly:1\t1st of every month\n'
    printf 'monthly:15\t15th of every month\n'
    printf 'monthly:-1\tlast day of every month\n'
    printf 'quarterly:1-1\tfirst day of every quarter\n'
    printf 'quarterly:-1\tlast day of every quarter\n'
    printf 'yearly:01-01\tJan 1 every year\n'
    printf 'yearly:-1\tlast day of year (12-31)\n'
end
"""

def _completion_iter_actions(parser):
    """yield action; skip help / version / dest=cmd / subparsers"""
    for a in parser._actions:
        if isinstance(a, (argparse._HelpAction, argparse._VersionAction)):
            continue
        if isinstance(a, argparse._SubParsersAction):
            continue
        yield a


def _default_verb_leaf(name, sub):
    """For a default-verb collision group (link / tag / log / sched), return its
    default-verb leaf parser (the one reachable by omitting the verb, e.g. `sched add`).
    The group name is itself the everyday command (`wl sched <id> --recur`), so the leaf's
    args must complete under the bare group condition — but the leaf lives under a nested
    `_SubParsersAction` that the per-subcommand walk skips. Returns None for plain groups
    (node / prop / clock), whose common args are reached via their top-level shortcuts."""
    from .cli import _DEFAULT_VERB_ENTITIES  # deferred: cli imports completion
    spec = _DEFAULT_VERB_ENTITIES.get(name)
    if not spec:
        return None
    spa = next((x for x in sub._actions if isinstance(x, argparse._SubParsersAction)), None)
    return spa.choices.get(spec[0]) if spa else None

def _fish_escape(s):
    """fish string escape: wrap in single quotes; inner single quote becomes \\'"""
    if s is None:
        return ""
    return s.replace("\\", "\\\\").replace("'", "\\'")


def _fish_one_complete(prefix, action, sub_cmd=None):
    """Emit one fish complete line for a single action. prefix is the leading text of the complete line (with -c wl -n ...)."""
    lines = []
    descr = (action.help or "").split("\n")[0].strip()
    # short / long options
    short = []
    long_ = []
    for o in action.option_strings:
        (long_ if o.startswith("--") else short).append(o.lstrip("-"))
    opt_parts = []
    for s in short:
        opt_parts.append(f"-s {s}")
    for l in long_:
        opt_parts.append(f"-l {l}")
    opt_str = " ".join(opt_parts)
    if not opt_str:
        return []  # no short/long = positional; handled by caller

    # value-taking options disable filename completion (-x); store_true / store_false take no value
    takes_value = not isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction,
                                          argparse._StoreConstAction, argparse._CountAction))
    if takes_value:
        opt_str += " -x"

    line = f"{prefix} {opt_str}"
    if descr:
        line += f" -d '{_fish_escape(descr)}'"

    # value-completion source: choices > helper map > default (none)
    val_src = None
    if action.choices:
        val_src = " ".join(str(c) for c in action.choices)
    else:
        # find helper: first (sub_cmd, --long), then (__any__, --long)
        for opt in action.option_strings:
            for key in [(sub_cmd, opt), ("__any__", opt)]:
                if key in _FISH_HELPERS:
                    val_src = _FISH_HELPERS[key]
                    break
            if val_src:
                break
    if val_src:
        line += f' -a "{val_src}"'

    lines.append(line)
    return lines


def _help_topics():
    """Help topic ids, baked into the generated completion so `wl help <topic>` tab-completes.
    (Static at generation time — regenerate completion after adding topics.)"""
    try:
        from .commands.help import topic_names
        return topic_names()
    except Exception:
        return []


def _fish_positional_complete(parser, sub_cmd):
    """Positional argument completion for a subcommand (mostly node id / date)."""
    lines = []
    for a in parser._actions:
        if a.option_strings or isinstance(a, (argparse._SubParsersAction,
                                              argparse._HelpAction, argparse._VersionAction)):
            continue
        # positional. Look up dest -> helper
        prefix = f'complete -c wl -n "__fish_seen_subcommand_from {sub_cmd}"'
        val_src = None
        # explicit helper
        for key in [(sub_cmd, a.dest), (sub_cmd, None)]:
            if key in _FISH_HELPERS:
                val_src = _FISH_HELPERS[key]
                break
        # default: subcommand in node-id operation set -> __wl_list_nodes
        if val_src is None and sub_cmd in _FISH_POSITIONAL_NODE:
            val_src = "(__wl_list_nodes)"
        if val_src is None and sub_cmd == "help" and a.dest == "topic":
            tops = _help_topics()
            if tops:
                val_src = " ".join(tops)
        if val_src is None and a.choices:
            val_src = " ".join(str(c) for c in a.choices)
        if val_src:
            descr = (a.help or "").split("\n")[0].strip()
            line = f"{prefix} -f -a \"{val_src}\""
            if descr:
                line += f" -d '{_fish_escape(descr)}'"
            lines.append(line)
    return lines

def _generate_fish_completion(parser):
    """Walk build_parser() to produce full fish completion. argparse is the source of truth."""
    lines = [
        "# wl fish completion (auto-generated by `wl --print-completion fish`)",
        "# Load: add `wl --print-completion fish | source` to ~/.config/fish/config.fish",
        "",
        "complete -c wl -f   # disable filename completion by default",
        "",
        _FISH_HELPER_FUNCTIONS,
        "# --- global args ---",
    ]
    # global (top-level parser) actions
    for a in _completion_iter_actions(parser):
        lines += _fish_one_complete('complete -c wl', a, sub_cmd=None)

    # subcommands
    subparsers_action = next((x for x in parser._actions
                              if isinstance(x, argparse._SubParsersAction)), None)
    if subparsers_action is None:
        return "\n".join(lines) + "\n"

    # use _collect_sub_meta to get (name, help, sub, aliases)
    sub_metas = _collect_sub_meta(parser)
    lines.append("")
    lines.append("# --- subcommand names (+ aliases) ---")
    for name, descr, _sub, aliases in sub_metas:
        descr_part = f" -d '{_fish_escape(descr)}'" if descr else ""
        lines.append(f'complete -c wl -n "__fish_use_subcommand" -a "{name}"{descr_part}')
        for alias in aliases:
            alias_descr = f"{descr} (= {name})" if descr else f"alias of {name}"
            lines.append(f'complete -c wl -n "__fish_use_subcommand" -a "{alias}"'
                         f" -d '{_fish_escape(alias_descr)}'")

    # per-subcommand arguments -- condition includes the primary name + all aliases
    lines.append("")
    lines.append("# --- per-subcommand arguments ---")
    for name, _descr, sub, aliases in sub_metas:
        all_names = " ".join([name] + aliases)
        cond = f'__fish_seen_subcommand_from {all_names}'
        prefix = f'complete -c wl -n "{cond}"'
        section = [f"\n# {name}"]
        for a in _completion_iter_actions(sub):
            section += _fish_one_complete(prefix, a, sub_cmd=name)
        section += _fish_positional_complete(sub, name)
        # default-verb groups (sched/log/tag/link): surface the default leaf's args under
        # the bare group name, since `wl sched <id> --recur` omits the `add` verb
        leaf = _default_verb_leaf(name, sub)
        if leaf is not None:
            for a in _completion_iter_actions(leaf):
                section += _fish_one_complete(prefix, a, sub_cmd=name)
            section += _fish_positional_complete(leaf, name)
        if len(section) > 1:
            lines += section

    return "\n".join(lines) + "\n"

_BASH_HELPER_FUNCTIONS = r"""# helper functions (local SQLite query against worklog.db; no Python startup)
__wl_db_path_bash() {
    # $WORKLOG_DB env, else $XDG_DATA_HOME/worklog/worklog.db (default ~/.local/share/worklog/worklog.db)
    if [ -n "$WORKLOG_DB" ]; then
        echo "$WORKLOG_DB"
    else
        echo "${XDG_DATA_HOME:-$HOME/.local/share}/worklog/worklog.db"
    fi
}

__wl_list_nodes_bash() {
    local db=$(__wl_db_path_bash)
    [ -f "$db" ] || return
    sqlite3 "$db" "SELECT id FROM node WHERE (status IS NULL OR status NOT IN ('DONE', 'CANCELED')) AND deleted_at IS NULL ORDER BY id DESC LIMIT 80" 2>/dev/null
}

__wl_list_tags_bash() {
    local db=$(__wl_db_path_bash)
    [ -f "$db" ] || return
    sqlite3 "$db" "SELECT DISTINCT tag FROM tag WHERE deleted_at IS NULL ORDER BY tag" 2>/dev/null
}

__wl_date_suggestions_bash() {
    echo "today yesterday day-before-yesterday tomorrow day-after-tomorrow $(date +%Y-%m-%d)"
}

__wl_recur_suggestions_bash() {
    echo "daily weekly:Mon,Wed,Fri weekly:Sat,Sun weekly:-1 monthly:1 monthly:15 monthly:-1 quarterly:1-1 quarterly:-1 yearly:01-01 yearly:-1"
}
"""

# subcommand / argument -> bash helper function name (outputs token list, consumed by compgen -W)

_BASH_DYN_HELPERS = {
    ("__any__", "--parent"): "__wl_list_nodes_bash",
    ("__any__", "--root"): "__wl_list_nodes_bash",
    ("__any__", "--id"): "__wl_list_nodes_bash",
    ("__any__", "--node"): "__wl_list_nodes_bash",
    ("__any__", "--ids"): "__wl_list_nodes_bash",
    ("__any__", "--tag"): "__wl_list_tags_bash",
    ("sched", "--recur"): "__wl_recur_suggestions_bash",
    ("log", "--date"): "__wl_date_suggestions_bash",
    ("logs", "--date"): "__wl_date_suggestions_bash",
    ("unlog", "--date"): "__wl_date_suggestions_bash",
}

def _collect_sub_meta(parser):
    """Return [(sub_name, sub_help, sub_parser, [aliases])].
    aliases are all alias names of the sub (excluding the primary name); the primary sub matches via choices key against _choices_actions; aliases point to the same parser object."""
    subparsers_action = next((x for x in parser._actions
                              if isinstance(x, argparse._SubParsersAction)), None)
    if not subparsers_action:
        return []
    # reverse map: parser obj id -> list of names
    parser_to_names = {}
    for name, sub_p in subparsers_action.choices.items():
        parser_to_names.setdefault(id(sub_p), []).append(name)
    # primary names: those in _choices_actions with help text
    primary_names = set()
    if subparsers_action._choices_actions:
        for c in subparsers_action._choices_actions:
            primary_names.add(c.dest)
    result = []
    seen = set()
    for name, sub in subparsers_action.choices.items():
        if id(sub) in seen:
            continue
        if name not in primary_names:
            # this is an alias; skip for now, collected together when the primary name appears
            continue
        seen.add(id(sub))
        help_text = ""
        if subparsers_action._choices_actions:
            for c in subparsers_action._choices_actions:
                if c.dest == name:
                    help_text = c.help or ""
                    break
        aliases = [n for n in parser_to_names[id(sub)] if n != name]
        result.append((name, (help_text or "").split("\n")[0].strip(), sub, aliases))
    return result


def _sub_options(sub_parser):
    """List of all --long / -short options for a subcommand."""
    opts = []
    for a in _completion_iter_actions(sub_parser):
        for o in a.option_strings:
            opts.append(o)
    return opts

def _generate_bash_completion(parser):
    """argparse → bash _wl() function + complete -F _wl wl."""
    sub_metas = _collect_sub_meta(parser)
    # subcmds list includes primary names + aliases
    all_sub_names = []
    for name, _, _, aliases in sub_metas:
        all_sub_names.append(name)
        all_sub_names.extend(aliases)
    sub_names = " ".join(all_sub_names)

    # global flags (top-level parser)
    global_opts = []
    for a in _completion_iter_actions(parser):
        global_opts.extend(a.option_strings)
    global_opts_str = " ".join(global_opts)

    lines = [
        "# wl bash completion (auto-generated by `wl print-completion bash`)",
        "# Load: add `eval \"$(wl print-completion bash)\"` to ~/.bashrc",
        "",
        _BASH_HELPER_FUNCTIONS,
        "_wl() {",
        '    local cur="${COMP_WORDS[COMP_CWORD]}"',
        '    local prev="${COMP_WORDS[COMP_CWORD-1]}"',
        "",
        "    # find current sub: first word not starting with -",
        '    local sub=""',
        "    local i",
        "    for ((i=1; i<COMP_CWORD; i++)); do",
        '        case "${COMP_WORDS[i]}" in',
        "            -*) ;;",
        '            *) sub="${COMP_WORDS[i]}"; break ;;',
        "        esac",
        "    done",
        "",
        f'    local global_opts="{global_opts_str}"',
        f'    local subcmds="{sub_names}"',
        "",
        '    if [ -z "$sub" ]; then',
        '        if [[ "$cur" == -* ]]; then',
        '            COMPREPLY=( $(compgen -W "$global_opts" -- "$cur") )',
        "        else",
        '            COMPREPLY=( $(compgen -W "$subcmds" -- "$cur") )',
        "        fi",
        "        return",
        "    fi",
        "",
        '    case "$sub" in',
    ]

    for name, _, sub, aliases in sub_metas:
        # default-verb groups (sched/log/tag/link): fold the default leaf's args in, since
        # `wl sched <id> --recur` omits the `add` verb (the leaf lives under nested subparsers)
        leaf = _default_verb_leaf(name, sub)
        arg_parsers = [sub] + ([leaf] if leaf is not None else [])
        opts = [o for p in arg_parsers for o in _sub_options(p)]
        opts_str = " ".join(opts)
        # bash case pattern: name|alias1|alias2)
        case_pattern = "|".join([name] + aliases)
        case_lines = [f'        {case_pattern})']
        # when prev is a long option, look up its helper / choices
        prev_cases = []
        for a in (act for p in arg_parsers for act in _completion_iter_actions(p)):
            if not a.option_strings:
                continue
            long_opts = [o for o in a.option_strings if o.startswith("--")]
            if not long_opts:
                continue
            for opt in long_opts:
                src = None
                if a.choices:
                    src = " ".join(str(c) for c in a.choices)
                else:
                    for key in [(name, opt), ("__any__", opt)]:
                        if key in _BASH_DYN_HELPERS:
                            src = f'$({_BASH_DYN_HELPERS[key]})'
                            break
                if src:
                    prev_cases.append((opt, src))
        if prev_cases:
            case_lines.append('            case "$prev" in')
            for opt, src in prev_cases:
                if src.startswith("$("):
                    case_lines.append(f'                {opt}) COMPREPLY=( $(compgen -W "{src}" -- "$cur") ); return ;;')
                else:
                    case_lines.append(f'                {opt}) COMPREPLY=( $(compgen -W "{src}" -- "$cur") ); return ;;')
            case_lines.append('            esac')

        case_lines.append('            if [[ "$cur" == -* ]]; then')
        case_lines.append(f'                COMPREPLY=( $(compgen -W "{opts_str} $global_opts" -- "$cur") )')
        case_lines.append('            else')
        # positional: when the subcommand operates on node ids -> __wl_list_nodes_bash
        if name in _FISH_POSITIONAL_NODE:
            case_lines.append(f'                COMPREPLY=( $(compgen -W "$(__wl_list_nodes_bash)" -- "$cur") )')
        elif name == "help":
            case_lines.append(f'                COMPREPLY=( $(compgen -W "{" ".join(_help_topics())}" -- "$cur") )')
        else:
            case_lines.append('                :')
        case_lines.append('            fi')
        case_lines.append('            ;;')
        lines.extend(case_lines)

    lines.append('    esac')
    lines.append('}')
    lines.append('complete -F _wl wl')
    return "\n".join(lines) + "\n"

_ZSH_HELPER_FUNCTIONS = r"""# helper functions (local SQLite query against worklog.db; no Python startup)
__wl_db_path_zsh() {
    # $WORKLOG_DB env, else $XDG_DATA_HOME/worklog/worklog.db (default ~/.local/share/worklog/worklog.db)
    if [ -n "$WORKLOG_DB" ]; then
        echo "$WORKLOG_DB"
    else
        echo "${XDG_DATA_HOME:-$HOME/.local/share}/worklog/worklog.db"
    fi
}

__wl_list_nodes_zsh() {
    local db=$(__wl_db_path_zsh)
    [ -f "$db" ] || return
    local -a nodes
    nodes=( "${(@f)$(sqlite3 "$db" "SELECT id || ':' || replace(title, ':', '\\:') FROM node WHERE (status IS NULL OR status NOT IN ('DONE', 'CANCELED')) AND deleted_at IS NULL ORDER BY id DESC LIMIT 80" 2>/dev/null)}" )
    _describe 'node' nodes
}

__wl_list_tags_zsh() {
    local db=$(__wl_db_path_zsh)
    [ -f "$db" ] || return
    local -a tags
    tags=( "${(@f)$(sqlite3 "$db" "SELECT DISTINCT tag FROM tag WHERE deleted_at IS NULL ORDER BY tag" 2>/dev/null)}" )
    _values 'tag' $tags
}

__wl_date_suggestions_zsh() {
    local today=$(date +%Y-%m-%d)
    _describe 'date' \
        "today:today" "yesterday:yesterday" "day-before-yesterday:day before yesterday" "tomorrow:tomorrow" "day-after-tomorrow:day after tomorrow" \
        "$today:today YYYY-MM-DD"
}

__wl_recur_suggestions_zsh() {
    _describe 'recur' \
        "daily:every day" \
        "weekly\\:Mon,Wed,Fri:Mon/Wed/Fri" \
        "weekly\\:Sat,Sun:weekends" \
        "weekly\\:-1:every Sunday (last day)" \
        "monthly\\:1:1st of every month" \
        "monthly\\:15:15th of every month" \
        "monthly\\:-1:last day of every month" \
        "quarterly\\:1-1:first day of every quarter" \
        "quarterly\\:-1:last day of every quarter" \
        "yearly\\:01-01:Jan 1 every year" \
        "yearly\\:-1:last day of year (12-31)"
}
"""

def _zsh_escape(s):
    """zsh string escape: backticks / square brackets / single + double quotes"""
    if s is None:
        return ""
    return s.replace("\\", "\\\\").replace("'", "''").replace("[", "\\[").replace("]", "\\]").replace(":", "\\:")



_ZSH_DYN_HELPERS = {
    ("__any__", "--parent"): "__wl_list_nodes_zsh",
    ("__any__", "--root"): "__wl_list_nodes_zsh",
    ("__any__", "--id"): "__wl_list_nodes_zsh",
    ("__any__", "--node"): "__wl_list_nodes_zsh",
    ("__any__", "--ids"): "__wl_list_nodes_zsh",
    ("__any__", "--tag"): "__wl_list_tags_zsh",
    ("sched", "--recur"): "__wl_recur_suggestions_zsh",
    ("log", "--date"): "__wl_date_suggestions_zsh",
    ("logs", "--date"): "__wl_date_suggestions_zsh",
    ("unlog", "--date"): "__wl_date_suggestions_zsh",
}

def _zsh_arg_spec(action, sub_cmd):
    """For a single action, produce the _arguments spec string. None means positional (handled separately)."""
    if not action.option_strings:
        return None  # positional
    descr = (action.help or "").split("\n")[0].strip()
    descr_part = f"[{_zsh_escape(descr)}]" if descr else ""

    takes_value = not isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction,
                                          argparse._StoreConstAction, argparse._CountAction))

    val_part = ""
    if takes_value:
        # value-completion source
        val_src = None
        if action.choices:
            val_src = "(" + " ".join(str(c) for c in action.choices) + ")"
        else:
            for opt in action.option_strings:
                for key in [(sub_cmd, opt), ("__any__", opt)]:
                    if key in _ZSH_DYN_HELPERS:
                        val_src = _ZSH_DYN_HELPERS[key]
                        break
                if val_src:
                    break
        if val_src:
            val_part = f": :{val_src}" if val_src.startswith("__") else f": :{val_src}"
        else:
            val_part = ": :"

    # multiple option strings (e.g. -q --brief): zsh uses {-q,--brief} form
    opts = action.option_strings
    if len(opts) == 1:
        return f"'{opts[0]}{descr_part}{val_part}'"
    elif len(opts) == 2:
        return "'(" + " ".join(opts) + ")'{" + ",".join(opts) + "}'" + descr_part + val_part + "'"
    else:
        # > 2 options: one entry per option
        return " ".join(f"'{o}{descr_part}{val_part}'" for o in opts)

def _generate_zsh_completion(parser):
    """argparse → zsh _wl() function + compdef _wl wl."""
    sub_metas = _collect_sub_meta(parser)

    lines = [
        "#compdef wl",
        "# wl zsh completion (auto-generated by `wl print-completion zsh`)",
        "# Load: add `eval \"$(wl print-completion zsh)\"` to ~/.zshrc",
        "",
        _ZSH_HELPER_FUNCTIONS,
        "_wl() {",
        "    local context state line",
        "    typeset -A opt_args",
        "",
        "    _arguments -C \\",
    ]

    # global args
    global_specs = []
    for a in _completion_iter_actions(parser):
        spec = _zsh_arg_spec(a, sub_cmd=None)
        if spec:
            global_specs.append(spec)
    for spec in global_specs:
        lines.append(f"        {spec} \\")
    lines.append("        '1: :->cmds' \\")
    lines.append("        '*::arg:->args'")
    lines.append("")
    lines.append('    case "$state" in')
    lines.append('        cmds)')
    lines.append('            local -a subcmds')
    lines.append('            subcmds=(')
    for name, descr, _, aliases in sub_metas:
        descr_safe = _zsh_escape(descr)
        lines.append(f"                '{name}:{descr_safe}'")
        for alias in aliases:
            alias_descr = _zsh_escape(f"{descr} (= {name})" if descr else f"alias of {name}")
            lines.append(f"                '{alias}:{alias_descr}'")
    lines.append('            )')
    lines.append("            _describe 'subcommand' subcmds")
    lines.append('            ;;')
    lines.append('        args)')
    lines.append('            case $line[1] in')

    for name, _, sub, aliases in sub_metas:
        # zsh case pattern: name|alias1|alias2)
        case_pattern = "|".join([name] + aliases)
        lines.append(f'                {case_pattern})')
        lines.append('                    _arguments \\')
        # default-verb groups (sched/log/tag/link): fold the default leaf's args in
        leaf = _default_verb_leaf(name, sub)
        arg_parsers = [sub] + ([leaf] if leaf is not None else [])
        sub_specs = []
        for a in (act for p in arg_parsers for act in _completion_iter_actions(p)):
            spec = _zsh_arg_spec(a, sub_cmd=name)
            if spec:
                sub_specs.append(spec)
        # positional (single positional taking a node id, or help's topic list)
        positional_helper = None
        if name in _FISH_POSITIONAL_NODE:
            positional_helper = "__wl_list_nodes_zsh"
        elif name == "help":
            positional_helper = "(" + " ".join(_help_topics()) + ")"
        for i, spec in enumerate(sub_specs):
            suffix = " \\" if (i < len(sub_specs) - 1 or positional_helper) else ""
            lines.append(f"                        {spec}{suffix}")
        if positional_helper:
            lines.append(f"                        '*: :{positional_helper}'")
        lines.append('                    ;;')

    lines.append('            esac')
    lines.append('            ;;')
    lines.append('    esac')
    lines.append('}')
    lines.append('compdef _wl wl')
    return "\n".join(lines) + "\n"

def cmd_print_completion(args, con=None):
    """Dump shell completion script. See per-shell header for how to load.

    fish: add `wl print-completion fish | source` to ~/.config/fish/config.fish
    bash: add `eval "$(wl print-completion bash)"` to ~/.bashrc
    zsh:  add `eval "$(wl print-completion zsh)"`  to ~/.zshrc
    """
    from .cli import build_parser  # lazy import — cli imports completion, so this side must be deferred
    shell = args.shell
    parser = build_parser()
    if shell == "fish":
        sys.stdout.write(_generate_fish_completion(parser))
    elif shell == "bash":
        sys.stdout.write(_generate_bash_completion(parser))
    elif shell == "zsh":
        sys.stdout.write(_generate_zsh_completion(parser))
    else:
        sys.exit(f"✗ shell '{shell}' not supported (fish / bash / zsh)")

