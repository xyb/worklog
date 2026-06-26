"""Contract of `render.dispatch_group` — the single-source entity-group dispatcher that every
`wl <group> <sub>` routes through. Pure function (no DB), so it's tested directly with a fake args
namespace and stub handlers: route by sub-verb, propagate the handler's return value, run `default`
on a bare group, and die with `usage` when bare and no default."""
from types import SimpleNamespace

import pytest

from worklog.render import dispatch_group

CON = object()  # opaque — dispatch_group only forwards it to the handler


def _handler(tag):
    """A stub handler that records its call and returns a marker so propagation is observable."""
    calls = []
    def fn(args, con):
        calls.append((args, con))
        return f"ran:{tag}"
    fn.calls = calls
    return fn


def test_routes_to_handler_by_subverb_and_propagates_result():
    add, ls = _handler("add"), _handler("ls")
    args = SimpleNamespace(x_sub="ls")
    out = dispatch_group(args, CON, "x_sub", {"add": add, "ls": ls}, usage="u")
    assert out == "ran:ls"          # returned the handler result (so -o json / TextRenderable flow)
    assert ls.calls == [(args, CON)]  # forwarded (args, con) verbatim
    assert add.calls == []          # only the chosen verb ran


def test_bare_group_runs_default():
    default = _handler("show")
    args = SimpleNamespace(x_sub=None)
    out = dispatch_group(args, CON, "x_sub", {"set": _handler("set")}, default=default)
    assert out == "ran:show"
    assert default.calls == [(args, CON)]


def test_default_verb_aliasing_bare_form_routes_via_table():
    # goal's `today`: the explicit verb and the bare form land on the SAME handler — one table
    # entry pointing at it, plus default= pointing at it.
    today = _handler("today")
    table = {"today": today, "set": _handler("set")}
    assert dispatch_group(SimpleNamespace(x_sub="today"), CON, "x_sub", table, default=today) == "ran:today"
    assert dispatch_group(SimpleNamespace(x_sub=None), CON, "x_sub", table, default=today) == "ran:today"
    assert len(today.calls) == 2


def test_bare_group_without_default_dies_with_usage():
    with pytest.raises(SystemExit):
        dispatch_group(SimpleNamespace(x_sub=None), CON, "x_sub", {"set": _handler("set")},
                       usage="usage: wl x <set> …")


def test_missing_attr_is_treated_as_bare():
    # a group whose argparse dest was never set behaves like a bare invocation
    default = _handler("show")
    assert dispatch_group(SimpleNamespace(), CON, "x_sub", {}, default=default) == "ran:show"
