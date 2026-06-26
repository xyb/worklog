"""Contract of the shared text-render helpers `_group_header` / `_log_body_row` — the single
shape behind every `▸ ` group section and every `· ` log line. Asserted in plain mode (console
off), so the exact emitted bytes are pinned: glyph, spacing, optional #id / priority / suffix,
and the `indent_cols = len(indent) + 2` relationship the body row centralizes."""
import pytest

from worklog.render import _group_header, _log_body_row


@pytest.fixture
def plain(tmp_db, capsys):
    """Console off → `out`/`_c` emit plain text; return a reader for the captured line."""
    tmp_db._init_console("never", None)
    capsys.readouterr()  # drop any fixture noise

    def read():
        return capsys.readouterr().out
    return read


def test_group_header_title_only_default_lead(plain):
    _group_header("By day")
    assert plain() == "\n▸ By day\n"


def test_group_header_pri_and_suffix(plain):
    # query.py `--by project`: priority marker + caller-worded count suffix, blank-line lead
    _group_header("Website revamp", pri="A", suffix="(done 3 / pending 2)")
    assert plain() == "\n▸ [#A] Website revamp  (done 3 / pending 2)\n"


def test_group_header_id_pri_suffix_flush_lead(plain):
    # views.py `projects`: #id + priority + (count), flush (no leading blank line)
    _group_header("Proj", node_id=42, pri="B", suffix="(5)", lead="")
    assert plain() == "▸ #42 [#B] Proj  (5)\n"


def test_group_header_indented_lead_and_style(plain):
    # views.py `changes`: indented, type-styled title, no suffix
    _group_header("some-bucket", style="type", lead="    ")
    assert plain() == "    ▸ some-bucket\n"


def test_group_header_unset_priority_still_4col_marker(plain):
    # _pri_marker never blanks: an unset priority renders the muted [# ], keeping alignment
    _group_header("x", pri="")
    assert plain() == "\n▸ [# ] x\n"


def test_log_body_row_indent_and_glyph(plain):
    _log_body_row("did the thing", "      ")  # 6-space indent
    assert plain() == "      · did the thing\n"


def test_log_body_row_truncates_to_one_line(plain):
    # a long body collapses to a single line ending with the … ellipsis (no wrap to column 0)
    _log_body_row("x" * 4000, "        ")
    line = plain()
    assert line.count("\n") == 1          # exactly one line
    assert line.startswith("        · ")
    assert line.rstrip().endswith("…")
