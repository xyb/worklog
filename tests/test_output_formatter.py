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
# TextFormatter: out() passthrough, emit is no-op
# ---------------------------------------------------------------------------

class TestTextFormatter:
    def test_setup_teardown_are_noops(self):
        fmt = TextFormatter()
        fmt.setup()
        fmt.teardown()

    def test_emit_ignores_data(self, capsys):
        fmt = TextFormatter()
        fmt.emit({"key": "value"})
        assert capsys.readouterr().out == ""


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

    def test_teardown_clears_error_formatter(self):
        fmt = JSONFormatter()
        fmt.setup()
        fmt.teardown()
        # After teardown, die() should emit plain text, not RFC 9457
        assert _render._active_error_formatter is None
