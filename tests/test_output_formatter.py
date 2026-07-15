"""Unit tests for the output Formatter registry (output.py).

Covers: register_formatter / get_formatter round-trip, built-in registrations,
unknown-name fallback, and full lifecycle (setup → handler → teardown → emit).
"""
import json
import pytest

from worklog.commands.output import (
    Formatter,
    TextFormatter,
    JSONFormatter,
    JSONLFormatter,
    TextRenderable,
    _FORMATTERS,
    register_formatter,
    get_formatter,
    output_format,
)
import worklog.render as _render


# ---------------------------------------------------------------------------
# Registry basics
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_builtins_registered_via_register_formatter(self):
        assert _FORMATTERS["text"] is TextFormatter
        assert _FORMATTERS["json"] is JSONFormatter

    def test_get_formatter_returns_instance(self):
        assert isinstance(get_formatter("text"), TextFormatter)
        assert isinstance(get_formatter("json"), JSONFormatter)

    def test_unknown_name_falls_back_to_text(self):
        assert isinstance(get_formatter("nonexistent"), TextFormatter)

    def test_register_custom_formatter(self):
        class MyFormatter(Formatter):
            pass

        register_formatter("custom", MyFormatter)
        try:
            assert isinstance(get_formatter("custom"), MyFormatter)
        finally:
            del _FORMATTERS["custom"]

    def test_register_overwrites_existing(self):
        class AltJSON(Formatter):
            pass

        original = _FORMATTERS["json"]
        register_formatter("json", AltJSON)
        try:
            assert isinstance(get_formatter("json"), AltJSON)
        finally:
            _FORMATTERS["json"] = original


# ---------------------------------------------------------------------------
# Lifecycle: setup → teardown → emit called in order
# ---------------------------------------------------------------------------

class TestFormatterLifecycle:
    def test_custom_formatter_lifecycle_via_decorator(self, monkeypatch):
        calls = []

        class TraceFormatter(Formatter):
            def setup(self):
                calls.append("setup")

            def teardown(self):
                calls.append("teardown")

            def emit(self, data):
                calls.append(("emit", data))

        register_formatter("trace", TraceFormatter)
        try:
            import argparse
            args = argparse.Namespace(output="trace")

            @output_format
            def handler(args, con):
                calls.append("handler")
                return {"ok": True}

            handler(args, con=None)
            assert calls == ["setup", "handler", "teardown", ("emit", {"ok": True})]
        finally:
            del _FORMATTERS["trace"]

    def test_teardown_runs_even_on_exception(self, monkeypatch):
        teardown_called = []

        class GuardFormatter(Formatter):
            def teardown(self):
                teardown_called.append(True)

        register_formatter("guard", GuardFormatter)
        try:
            import argparse
            args = argparse.Namespace(output="guard")

            @output_format
            def bad_handler(args, con):
                raise RuntimeError("boom")

            with pytest.raises(RuntimeError):
                bad_handler(args, con=None)

            assert teardown_called == [True]
        finally:
            del _FORMATTERS["guard"]


# ---------------------------------------------------------------------------
# TextFormatter: calls TextRenderable.render(), ignores plain data
# ---------------------------------------------------------------------------

class TestTextFormatter:
    def test_setup_teardown_are_noops(self):
        fmt = TextFormatter()
        fmt.setup()
        fmt.teardown()

    def test_emit_ignores_plain_data(self, capsys):
        fmt = TextFormatter()
        fmt.emit({"key": "value"})
        assert capsys.readouterr().out == ""

    def test_emit_calls_render_on_text_renderable(self, capsys):
        rendered = []
        tr = TextRenderable({"id": 1}, lambda: rendered.append("called"))
        fmt = TextFormatter()
        fmt.emit(tr)
        assert rendered == ["called"]

    def test_emit_ignores_none(self, capsys):
        fmt = TextFormatter()
        fmt.emit(None)
        assert capsys.readouterr().out == ""


# ---------------------------------------------------------------------------
# TextRenderable: data/render separation
# ---------------------------------------------------------------------------

class TestTextRenderable:
    def test_data_accessible(self):
        tr = TextRenderable({"id": 42}, lambda: None)
        assert tr.data == {"id": 42}

    def test_render_calls_fn(self):
        called = []
        tr = TextRenderable({}, lambda: called.append(True))
        tr.render()
        assert called == [True]

    def test_json_formatter_uses_data_not_render(self, capsys):
        rendered = []
        tr = TextRenderable({"id": 1}, lambda: rendered.append("rendered"))
        fmt = JSONFormatter()
        fmt.emit(tr)
        out = capsys.readouterr().out
        assert json.loads(out) == {"id": 1}
        assert rendered == []  # render fn must NOT be called in JSON mode


# ---------------------------------------------------------------------------
# JSONFormatter: suppresses out(), emits JSON
# ---------------------------------------------------------------------------

class TestJSONFormatter:
    def test_emit_writes_json_to_stdout(self, capsys):
        fmt = JSONFormatter()
        fmt.emit({"id": 1, "title": "task"})
        out = capsys.readouterr().out
        assert json.loads(out) == {"id": 1, "title": "task"}

    def test_emit_null(self, capsys):
        fmt = JSONFormatter()
        fmt.emit(None)
        assert json.loads(capsys.readouterr().out) is None

    def test_teardown_restores_previous_error_formatter(self):
        # Without an outer context, teardown restores to None.
        fmt = JSONFormatter()
        fmt.setup()
        fmt.teardown()
        assert _render._active_error_formatter is None

    def test_nested_setup_teardown_restores_outer(self):
        # Inner teardown must not clobber outer formatter (main() + @output_format nesting).
        outer = JSONFormatter()
        outer.setup()
        inner = JSONFormatter()
        inner.setup()
        inner.teardown()
        # After inner teardown, outer's format_error is back.
        assert _render._active_error_formatter is JSONFormatter.format_error
        outer.teardown()
        assert _render._active_error_formatter is None


class TestJSONLFormatter:
    """JSONL mode: a top-level list streams one compact object per line; any
    other payload is a single compact line. Subclasses JSONFormatter, so it
    inherits out() suppression + RFC 9457 error formatting."""

    def test_registered_as_jsonl(self):
        assert _FORMATTERS["jsonl"] is JSONLFormatter
        assert isinstance(get_formatter("jsonl"), JSONLFormatter)

    def test_subclasses_json_formatter(self):
        assert issubclass(JSONLFormatter, JSONFormatter)

    def test_list_streams_one_object_per_line(self, capsys):
        JSONLFormatter().emit([{"id": 1}, {"id": 2}, {"id": 3}])
        lines = capsys.readouterr().out.splitlines()
        assert [json.loads(x) for x in lines] == [{"id": 1}, {"id": 2}, {"id": 3}]

    def test_lines_are_compact_not_indented(self, capsys):
        JSONLFormatter().emit([{"id": 1, "title": "a"}])
        out = capsys.readouterr().out
        assert out == '{"id": 1, "title": "a"}\n'  # no indent, no array brackets

    def test_empty_list_emits_nothing(self, capsys):
        JSONLFormatter().emit([])
        assert capsys.readouterr().out == ""

    def test_dict_payload_is_single_line(self, capsys):
        JSONLFormatter().emit({"a": 1, "b": [2, 3]})
        out = capsys.readouterr().out
        assert out.count("\n") == 1 and json.loads(out) == {"a": 1, "b": [2, 3]}

    def test_none_is_single_line(self, capsys):
        JSONLFormatter().emit(None)
        assert json.loads(capsys.readouterr().out) is None

    def test_uses_text_renderable_data(self, capsys):
        rendered = []
        tr = TextRenderable([{"id": 7}], lambda: rendered.append("x"))
        JSONLFormatter().emit(tr)
        assert [json.loads(x) for x in capsys.readouterr().out.splitlines()] == [{"id": 7}]
        assert rendered == []

    def test_dataclass_items_serialized(self, capsys):
        from dataclasses import dataclass
        @dataclass
        class P:
            a: int
        JSONLFormatter().emit([P(1), P(2)])
        assert [json.loads(x) for x in capsys.readouterr().out.splitlines()] == [{"a": 1}, {"a": 2}]

    def test_non_ascii_not_escaped(self, capsys):
        JSONLFormatter().emit([{"t": "复盘"}])
        assert "复盘" in capsys.readouterr().out

    def test_inherits_error_formatter_setup(self):
        fmt = JSONLFormatter()
        fmt.setup()
        try:
            assert _render._active_error_formatter is JSONFormatter.format_error
        finally:
            fmt.teardown()
        assert _render._active_error_formatter is None


class TestJsonDefault:
    def test_dc_default_serializes_dataclass(self):
        from dataclasses import dataclass
        from worklog.commands.output import _dc_default
        @dataclass
        class P:
            a: int
        assert _dc_default(P(1)) == {"a": 1}

    def test_dc_default_rejects_non_dataclass(self):
        from worklog.commands.output import _dc_default
        with pytest.raises(TypeError):
            _dc_default(object())
