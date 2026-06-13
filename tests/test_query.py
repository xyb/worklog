"""Tests for semantic search: `wl reindex` + `wl query`.

The embedding backend (HTTP) is replaced with a deterministic fake — these test
the command wiring, ranking, threshold, JSON output, and error handling, NOT the
real model. LanceDB (the 'semantic' extra) runs for real against a tmp store."""
import json
import pytest

from worklog import embedding as emb


def _fake_embed(texts, input_type, cfg):
    """4-dim toy embedding: dims = [has 'alpha', 'beta', 'gamma'] + a constant
    so no vector is all-zero (keeps cosine well-defined)."""
    def vec(t):
        return [float("alpha" in t), float("beta" in t), float("gamma" in t), 0.1]
    return [vec(t) for t in texts]


@pytest.fixture
def seeded(cli, monkeypatch):
    monkeypatch.setattr(emb, "embed", _fake_embed)
    cli("add", "alpha task", "-k", "task")     # 1
    cli("add", "beta task", "-k", "task")       # 2
    cli("add", "gamma notes", "-k", "task")     # 3
    return cli


class TestReindex:
    def test_reindex_reports_count(self, seeded):
        code, out, _ = seeded("reindex")
        assert code == 0
        assert "3" in out  # 3 nodes embedded

    def test_query_before_reindex_hints(self, cli, monkeypatch):
        monkeypatch.setattr(emb, "embed", _fake_embed)
        cli("add", "alpha task", "-k", "task")
        code, out, err = cli("query", "alpha")
        assert "reindex" in (out + err).lower()


class TestQueryRanking:
    def test_top_hit_is_semantic_match(self, seeded):
        seeded("reindex")
        code, out, _ = seeded("--color", "never", "query", "alpha")
        assert code == 0
        # the alpha node (#1) ranks first; assert by id (title is hit-highlighted)
        hit_lines = [l for l in out.splitlines() if "#" in l and l.lstrip()[0].isdigit()]
        assert "#1 " in hit_lines[0]

    def test_threshold_filters_unrelated(self, seeded):
        seeded("reindex")
        # high threshold: only the near-identical 'alpha' node (#1) should survive
        code, out, _ = seeded("--color", "never", "query", "alpha", "--threshold", "0.9")
        assert "#1 " in out
        assert "#2 " not in out and "#3 " not in out  # beta/gamma cut by threshold
        assert "beta task" not in out and "gamma notes" not in out


class TestHitExplanation:
    """A semantic hit shows the single best-matching CHUNK as the reason — the exact
    passage (a specific log, or the head) that earned the score — not just the title."""

    def test_shows_matched_log_chunk(self, cli, monkeypatch):
        monkeypatch.setattr(emb, "embed", _fake_embed)
        cli("add", "weekly review", "-k", "task")          # title has no 'alpha'
        cli("log", "1", "alpha specific finding")           # only this log matches
        cli("reindex")
        code, out, _ = cli("--color", "never", "query", "alpha")
        assert "↳ log:" in out                  # the matched chunk is a log, labelled
        assert "specific finding" in out        # its content is shown

    def test_shows_matched_head_chunk(self, cli, monkeypatch):
        monkeypatch.setattr(emb, "embed", _fake_embed)
        cli("add", "alpha project", "-k", "task")           # title matches, no logs
        cli("reindex")
        code, out, _ = cli("--color", "never", "query", "alpha")
        assert "↳ head:" in out

    def test_highlights_literal_overlap(self, seeded):
        seeded("reindex")
        code, out, _ = seeded("--color", "never", "query", "alpha")
        # the literal query word, where it appears, is hit-marked (plain → *…*) like find
        assert "*alpha*" in out

    def test_matched_chunk_carries_tags(self, cli, monkeypatch):
        monkeypatch.setattr(emb, "embed", _fake_embed)
        cli("add", "alpha task", "-k", "task")
        cli("tag", "1", "gamma")
        cli("reindex")
        code, out, _ = cli("--color", "never", "query", "alpha")
        assert "gamma" in out  # tags are part of the head chunk text shown as the reason


class TestJsonOutput:
    def test_query_json_structure(self, seeded):
        seeded("reindex")
        code, out, _ = seeded("query", "alpha", "-o", "json")
        assert code == 0
        data = json.loads(out)
        assert isinstance(data, list) and data
        top = data[0]
        assert top["id"] == 1 and top["title"] == "alpha task"
        assert "score" in top and 0.0 <= top["score"] <= 1.0

    def test_query_json_empty_when_below_threshold(self, seeded):
        seeded("reindex")
        code, out, _ = seeded("query", "alpha", "--threshold", "0.999", "-o", "json")
        # only the alpha node clears 0.999; beta/gamma excluded — still valid JSON list
        data = json.loads(out)
        assert [d["id"] for d in data] == [1]


class TestLazyDependency:
    """The 'semantic' extra (lancedb) must load lazily: users without it keep
    full use of the core CLI, and only `query`/`reindex` prompt to install it."""

    def test_core_command_unaffected_without_extra(self, cli, monkeypatch):
        import sys
        monkeypatch.setitem(sys.modules, "lancedb", None)  # simulate not-installed
        assert cli("add", "ordinary task", "-k", "task")[0] == 0
        code, out, _ = cli("ls")
        assert code == 0 and "ordinary task" in out

    def test_query_without_extra_hints_install(self, cli, monkeypatch):
        import sys
        monkeypatch.setattr(emb, "embed", _fake_embed)
        monkeypatch.setitem(sys.modules, "lancedb", None)
        code, out, err = cli("query", "alpha")
        assert code != 0
        assert "pip install" in err and "semantic" in err

    def test_reindex_without_extra_hints_install(self, cli, monkeypatch):
        import sys
        monkeypatch.setattr(emb, "embed", _fake_embed)
        monkeypatch.setitem(sys.modules, "lancedb", None)
        cli("add", "alpha task", "-k", "task")
        code, out, err = cli("reindex")
        assert code != 0
        assert "pip install" in err and "semantic" in err


class TestProgress:
    def test_char_batches_respect_char_budget(self):
        from worklog.commands import semantic
        # texts of 100 chars each, budget 250 -> 2 per batch
        texts = ["x" * 100 for _ in range(5)]
        batches = list(semantic._char_batches(texts, max_chars=250, max_nodes=999))
        assert [len(b) for b in batches] == [2, 2, 1]

    def test_char_batches_respect_node_cap(self):
        from worklog.commands import semantic
        # tiny texts, huge char budget -> node cap is what splits
        batches = list(semantic._char_batches(["x"] * 10, max_chars=10 ** 9, max_nodes=4))
        assert [len(b) for b in batches] == [4, 4, 2]

    def test_oversized_single_text_is_its_own_batch(self):
        from worklog.commands import semantic
        texts = ["a" * 10, "b" * 1000, "c" * 10]
        batches = list(semantic._char_batches(texts, max_chars=500, max_nodes=999))
        assert [len(b) for b in batches] == [1, 1, 1]

    def test_embed_batched_progress_counts_calls(self, monkeypatch):
        from worklog.commands import semantic
        calls = []
        monkeypatch.setattr(semantic._embedding, "embed",
                            lambda texts, it, cfg: calls.append(len(texts)) or [[0.0] for _ in texts])
        seen = []
        # 5 texts of 100 chars, budget 250 -> 3 batches => 3 embedding calls
        texts = ["x" * 100 for _ in range(5)]
        out = semantic._embed_batched(texts, "document", {}, on_progress=lambda d, t: seen.append((d, t)),
                                      max_chars=250, max_nodes=999)
        assert len(out) == 5
        assert len(calls) == 3                          # one HTTP call per batch
        assert all(t == 3 for _, t in seen)             # total = number of calls
        assert [d for d, _ in seen] == [1, 2, 3]        # one tick per completed call

    def test_reindex_still_summarizes_with_progress_path(self, seeded):
        # non-TTY: the progress bar is disabled, but the loop + summary still run
        code, out, _ = seeded("reindex")
        assert code == 0 and "indexed" in out


class TestDocBuilding:
    def test_indexes_body_logs_tags(self, cli, monkeypatch):
        # a node carrying body + a log + a tag exercises the full doc concatenation
        monkeypatch.setattr(emb, "embed", _fake_embed)
        cli("add", "plain title", "-k", "task")           # 1
        cli("node", "edit", "1", "--body", "alpha body")
        cli("log", "1", "beta progress")
        cli("tag", "1", "gamma")
        code, out, _ = cli("reindex")
        assert code == 0 and "1" in out


class TestEmptyAndNoHits:
    def test_reindex_empty_db(self, cli, monkeypatch):
        monkeypatch.setattr(emb, "embed", _fake_embed)
        code, out, _ = cli("reindex")
        assert code == 0
        assert "nothing to index" in out.lower()

    def test_query_no_hits_message(self, seeded):
        seeded("reindex")
        # threshold above the cosine max (1.0) → nothing clears it (text path, not json)
        code, out, _ = seeded("query", "alpha", "--threshold", "1.5")
        assert code == 0
        assert "no semantic matches" in out.lower()


class TestErrors:
    def test_empty_query_errors(self, seeded):
        code, out, err = seeded("query", "  ")
        assert code != 0
        assert "empty" in err.lower()

    def test_query_backend_error_after_index(self, seeded, monkeypatch):
        seeded("reindex")  # builds the index with the fake embedder

        def boom(*a, **k):
            raise emb.EmbeddingError("backend unreachable at http://x")
        monkeypatch.setattr(emb, "embed", boom)
        code, out, err = seeded("query", "alpha")
        assert code != 0 and "unreachable" in err.lower()

    def test_backend_unreachable_surfaced(self, cli, monkeypatch):
        cli("add", "alpha task", "-k", "task")

        def boom(*a, **k):
            raise emb.EmbeddingError("backend unreachable at http://x")
        monkeypatch.setattr(emb, "embed", boom)
        code, out, err = cli("reindex")
        assert code != 0
        assert "unreachable" in err.lower()
