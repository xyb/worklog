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
    cli("add", "alpha task")     # 1
    cli("add", "beta task")       # 2
    cli("add", "gamma notes")     # 3
    return cli


class TestReindex:
    def test_reindex_reports_count(self, seeded):
        code, out, _ = seeded("reindex")
        assert code == 0
        assert "3" in out  # 3 nodes embedded

    def test_query_before_reindex_hints(self, cli, monkeypatch):
        monkeypatch.setattr(emb, "embed", _fake_embed)
        cli("add", "alpha task")
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
        cli("add", "weekly review")          # title has no 'alpha'
        cli("log", "1", "alpha specific finding")           # only this log matches
        cli("reindex")
        code, out, _ = cli("--color", "never", "query", "alpha")
        assert "↳ log:" in out                  # the matched chunk is a log, labelled
        assert "specific finding" in out        # its content is shown

    def test_matched_chunk_is_single_line(self, cli, monkeypatch):
        # the head chunk joins title+body with a newline; the display must flatten it onto
        # the `↳` line, not spill the body to column 0 (the alignment bug)
        monkeypatch.setattr(emb, "embed", _fake_embed)
        cli("add", "alpha proj")
        cli("node", "edit", "1", "--body", "extra context line")
        cli("reindex")
        code, out, _ = cli("--color", "never", "query", "alpha")
        chunk_lines = [l for l in out.splitlines() if "↳" in l]
        assert chunk_lines and "extra context line" in chunk_lines[0]
        assert "\nextra context line" not in out  # not spilled to its own col-0 line

    def test_shows_matched_head_chunk(self, cli, monkeypatch):
        monkeypatch.setattr(emb, "embed", _fake_embed)
        cli("add", "alpha project")           # title matches, no logs
        cli("reindex")
        code, out, _ = cli("--color", "never", "query", "alpha")
        assert "↳ head:" in out

    def test_highlights_literal_overlap(self, seeded):
        seeded("reindex")
        code, out, _ = seeded("--color", "never", "query", "alpha")
        # the literal query word, where it appears, is hit-marked (plain → *…*) like find
        assert "*alpha*" in out

    def test_multiword_query_highlights_each_term(self, cli, monkeypatch):
        monkeypatch.setattr(emb, "embed", _fake_embed)
        cli("add", "notes")
        cli("log", "1", "alpha and beta together")
        cli("reindex")
        code, out, _ = cli("--color", "never", "query", "alpha beta")
        # each query term highlighted separately in the matched chunk, not the whole phrase
        assert "*alpha*" in out and "*beta*" in out

    def test_matched_chunk_carries_tags(self, cli, monkeypatch):
        monkeypatch.setattr(emb, "embed", _fake_embed)
        cli("add", "alpha task")
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


class TestBackendFallback:
    """The 'semantic' extra (lancedb) loads lazily AND degrades gracefully: the core
    CLI works without it, and `query`/`reindex` fall back to the pure-stdlib SQLite
    vector store (no install prompt — they just work, only slower) when lancedb is absent."""

    def test_core_command_unaffected_without_extra(self, cli, monkeypatch):
        import sys
        monkeypatch.setitem(sys.modules, "lancedb", None)  # simulate not-installed
        assert cli("add", "ordinary task")[0] == 0
        code, out, _ = cli("ls")
        assert code == 0 and "ordinary task" in out

    def test_reindex_without_lancedb_uses_sqlite_fallback(self, cli, monkeypatch):
        import sys
        monkeypatch.setattr(emb, "embed", _fake_embed)
        monkeypatch.setitem(sys.modules, "lancedb", None)  # no fast backend
        cli("add", "alpha task")
        code, out, err = cli("reindex")
        assert code == 0                       # succeeds, no install prompt
        assert "1" in out                      # indexed the node
        assert "sqlite" in (out + err).lower() # notes which fallback backend it used

    def test_query_without_lancedb_uses_sqlite_fallback(self, cli, monkeypatch):
        import sys
        monkeypatch.setattr(emb, "embed", _fake_embed)
        monkeypatch.setitem(sys.modules, "lancedb", None)
        cli("add", "alpha task")
        cli("reindex")
        code, out, _ = cli("--color", "never", "query", "alpha")
        assert code == 0
        assert "#1 " in out                    # the node is found via the SQLite backend


class TestSegment:
    def test_jieba_segments_cjk(self):
        from worklog.commands import semantic
        terms = semantic._segment("向量检索")
        assert "向量" in terms and "检索" in terms   # jieba splits the CJK run

    def test_segments_latin_words(self):
        from worklog.commands import semantic
        assert semantic._segment("wl skill") == ["wl", "skill"] or \
               set(["wl", "skill"]).issubset(set(semantic._segment("wl skill")))

    def test_fallback_without_jieba_keeps_cjk_run(self, monkeypatch):
        import sys
        from worklog.commands import semantic
        monkeypatch.setitem(sys.modules, "jieba", None)   # simulate extra not installed
        assert semantic._segment("向量检索") == ["向量检索"]   # \w+ fallback: CJK run as one token
        assert semantic._segment("wl skill") == ["wl", "skill"]

    def test_query_terms_dedups(self):
        from worklog.commands import semantic
        assert semantic._query_terms("skill SKILL skill") == ["skill"]


class TestRRF:
    def test_fuses_by_reciprocal_rank(self):
        from worklog.commands import semantic
        # 1 is high in both lists → top; 3 and 2 trade places by combined reciprocal rank
        assert semantic._rrf([[1, 2, 3], [3, 1, 2]]) == [1, 3, 2]

    def test_union_of_ids(self):
        from worklog.commands import semantic
        assert set(semantic._rrf([[1, 2], [3]])) == {1, 2, 3}


class TestHybrid:
    def test_keyword_only_node_surfaces(self, cli, monkeypatch):
        # 'xyzzy' is not in the fake embedder's vocab (neutral vector), but appears literally
        # in node 2's log — hybrid's keyword side must surface it.
        monkeypatch.setattr(emb, "embed", _fake_embed)
        cli("add", "alpha task")              # 1
        cli("add", "ordinary")                # 2
        cli("log", "2", "the xyzzy marker is here")
        cli("reindex")
        code, out, _ = cli("--color", "never", "query", "xyzzy")
        assert code == 0 and "#2 " in out                   # surfaced by keyword match

    def test_synonym_query_finds_canonical(self, cli, monkeypatch):
        from worklog.xdg import _resolve_config_path
        p = _resolve_config_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("[synonyms]\nnyc = new york\n", encoding="utf-8")
        monkeypatch.setattr(emb, "embed", _fake_embed)
        cli("add", "trip to new york")        # 1: contains "new york", not "nyc"
        cli("add", "other")
        cli("reindex")
        code, out, _ = cli("--color", "never", "query", "nyc")
        assert "#1 " in out                                 # nyc → new york via synonyms


class TestKeywordRank:
    def test_ranks_by_distinct_terms_matched(self, cli, tmp_db):
        cli("add", "alpha beta gamma")   # 1: matches both terms
        cli("add", "alpha only")          # 2: matches one
        cli("add", "unrelated")           # 3: matches none
        from worklog.commands import semantic
        con = tmp_db.db_connect()
        try:
            ranked = semantic._keyword_rank(con, ["alpha", "beta"])
        finally:
            con.close()
        assert ranked[0] == 1          # most distinct terms first
        assert 2 in ranked and 3 not in ranked

    def test_matches_log_and_tag(self, cli, tmp_db):
        cli("add", "plain")               # 1
        cli("log", "1", "needle in the log")
        cli("add", "tagged")              # 2
        cli("tag", "2", "needle")
        from worklog.commands import semantic
        con = tmp_db.db_connect()
        try:
            ranked = semantic._keyword_rank(con, ["needle"])
        finally:
            con.close()
        assert set(ranked) == {1, 2}    # matched via log and via tag


class TestSynonyms:
    def test_expand_pulls_in_group(self, tmp_db, monkeypatch, tmp_path):
        from worklog.commands import semantic
        from worklog.xdg import _resolve_config_path
        p = _resolve_config_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("[synonyms]\nnyc = new york, ny\n", encoding="utf-8")
        out = semantic._expand_synonyms(["new york"])
        assert set(out) >= {"nyc", "new york", "ny"}   # one alias pulls in the whole group

    def test_no_config_is_identity(self, tmp_db):
        from worklog.commands import semantic
        assert semantic._expand_synonyms(["foo", "bar"]) == ["foo", "bar"]


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
        cli("add", "plain title")           # 1
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
        # a term in no node + a threshold above cosine max → neither side matches
        code, out, _ = seeded("query", "zzz-nonexistent", "--threshold", "1.5")
        assert code == 0
        assert "no matches" in out.lower()


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
        cli("add", "alpha task")

        def boom(*a, **k):
            raise emb.EmbeddingError("backend unreachable at http://x")
        monkeypatch.setattr(emb, "embed", boom)
        code, out, err = cli("reindex")
        assert code != 0
        assert "unreachable" in err.lower()


class TestSemanticInternals:
    """Edge paths in the reindex/query plumbing."""

    def test_node_chunks_skips_whitespace_only_log(self, cli, tmp_db):
        cli("add", "task one")               # node 1
        con = tmp_db.db_connect()
        try:
            con.execute("INSERT INTO log (node_id, body) VALUES (1, '   ')")  # blank-body log
            con.commit()
            from worklog.commands import semantic
            chunks = semantic._node_chunks(con)
        finally:
            con.close()
        fields = {(c[0], c[4]) for c in chunks}
        assert (1, "head") in fields          # head chunk still produced
        assert (1, "log") not in fields        # the whitespace-only log produced no chunk

    def test_embed_batched_without_progress_callback(self, monkeypatch):
        from worklog.commands import semantic
        monkeypatch.setattr(semantic._embedding, "embed",
                            lambda texts, t, cfg: [[1.0] for _ in texts])
        out = semantic._embed_batched(["a", "b"], "document", {}, on_progress=None)
        assert len(out) == 2                  # runs fine with no progress callback

    def test_open_store_error_exits_cleanly(self, cli, monkeypatch):
        # A VectorStoreError from connect() (e.g. a forced backend that can't load) becomes a
        # clean `✗ …` exit, never a traceback.
        from worklog.commands import semantic
        def boom(path):
            raise semantic._vs.VectorStoreError("backend boom")
        monkeypatch.setattr(semantic._vs, "connect", boom)
        code, out, err = cli("query", "anything")
        assert code != 0
        assert "boom" in (out + err)


class TestQueryKeywordPriority:
    """#759: a node matching ALL query terms surfaces top even if it's missing from (or weak
    in) the vector index — exact full-term matches must not be lost to semantic dilution."""

    def test_full_term_match_not_in_index_surfaces_first(self, seeded):
        seeded("reindex")                                  # indexes #1-#3 only
        seeded("add", "alpha beta combo")    # #4, created AFTER reindex -> not in vector index
        code, out, _ = seeded("--color", "never", "query", "alpha beta")
        assert code == 0
        hit_lines = [l for l in out.splitlines() if "#" in l and l.lstrip()[0].isdigit()]
        assert "#4 " in hit_lines[0]   # both terms in its title -> full coverage -> ranks first

    def test_single_term_still_ranks_match_first(self, seeded):
        seeded("reindex")
        code, out, _ = seeded("--color", "never", "query", "alpha")
        hit_lines = [l for l in out.splitlines() if "#" in l and l.lstrip()[0].isdigit()]
        assert "#1 " in hit_lines[0]   # unchanged: the alpha node still leads


class TestQueryHighlightTerms:
    """#809: title highlight uses the segmented terms (each word), so a non-contiguous
    match (terms separated by other words) still lights up the matched parts."""

    def test_separated_terms_highlight(self, seeded):
        seeded("reindex")
        seeded("add", "alpha gap beta")   # both terms, separated by 'gap'
        # plain (no color) marks matches as *term*; the raw-substring highlighter would miss
        # 'alpha beta' (not contiguous) — per-term highlight lights 'alpha' and 'beta'
        code, out, _ = seeded("query", "alpha beta")
        line = next(l for l in out.splitlines() if "#4" in l)
        assert "*alpha*" in line and "*beta*" in line


class TestQueryStaleIndex:
    """#807: warn (on stderr) when the live node count != the indexed count, so a new node
    that isn't in the vector index yet is explained instead of silently scoring 0.000."""

    def test_warns_when_node_added_after_reindex(self, seeded):
        seeded("reindex")                              # indexes #1-#3
        seeded("add", "delta task")      # #4 — not in the index
        code, out, err = seeded("query", "alpha")
        assert code == 0
        assert "reindex" in err.lower()                # hint points at the fix
        assert "#1 " in out                            # results still print on stdout (warning is stderr-only)

    def test_no_warning_when_index_fresh(self, seeded):
        seeded("reindex")
        code, out, err = seeded("query", "alpha")
        assert "reindex" not in err.lower()            # fresh index → no nag


class TestReindexIncremental:
    """reindex is incremental by default (embeds only new/changed nodes + drops deleted).
    First run (empty index) falls back to full automatically. --full rebuilds everything."""

    def test_first_run_does_full_build(self, seeded):
        # empty store → incremental falls back to full
        code, out, _ = seeded("reindex")
        assert code == 0 and "indexed 3" in out

    def test_adds_new_node(self, seeded):
        seeded("reindex")                              # first run: full (indexes #1-#3)
        seeded("add", "delta task")      # #4
        code, out, _ = seeded("reindex")               # second run: incremental
        assert code == 0 and "+1 new" in out
        code, out2, err = seeded("query", "delta")
        assert "#4 " in out2 and "not indexed" not in err.lower()

    def test_up_to_date_noop(self, seeded):
        seeded("reindex")
        code, out, _ = seeded("reindex")
        assert "up to date" in out.lower()

    def test_changed_node_reembedded(self, seeded):
        seeded("reindex")
        seeded("log", "1", "a brand new log on node one", "--keep-status")  # #1 chunk set changes
        code, out, _ = seeded("reindex")
        assert "~1 changed" in out

    def test_removed_node_evicted(self, seeded):
        seeded("reindex")
        seeded("node", "rm", "3")                       # soft-delete #3
        code, out, _ = seeded("reindex")
        assert "-1 removed" in out

    def test_embeds_only_dirty(self, seeded, monkeypatch):
        seeded("reindex")
        seeded("add", "delta task")       # only #4 dirty
        n = []
        monkeypatch.setattr(emb, "embed", lambda texts, t, cfg: (n.append(len(texts)), _fake_embed(texts, t, cfg))[1])
        seeded("reindex")
        assert sum(n) <= 2   # just #4's chunk(s), not all four nodes

    def test_force_rebuilds_all(self, seeded):
        seeded("reindex")                              # first run
        code, out, _ = seeded("reindex", "--full")   # full rebuild despite existing index
        assert code == 0 and "indexed 3" in out


class TestReindexAuto:
    """#806 Phase 2: `wl reindex --auto` = single-flight background worker (lock + loop-until-clean)."""

    def test_auto_indexes_new_nodes(self, seeded):
        seeded("reindex")
        seeded("add", "delta task")
        code, _, _ = seeded("reindex", "--auto")
        assert code == 0
        _, out2, err = seeded("query", "delta")
        assert "#4 " in out2 and "not indexed" not in err.lower()   # #4 now indexed

    def test_auto_single_flight_skips_when_locked(self, seeded, tmp_path, monkeypatch):
        import fcntl
        from worklog.commands import semantic as s
        lock = tmp_path / "reindex.lock"
        monkeypatch.setattr(s, "_reindex_lock_path", lambda args: lock)
        seeded("reindex")
        seeded("add", "delta task")
        held = open(lock, "w")
        fcntl.flock(held.fileno(), fcntl.LOCK_EX)               # simulate another worker holding it
        try:
            seeded("reindex", "--auto")                         # must skip (single-flight)
        finally:
            fcntl.flock(held.fileno(), fcntl.LOCK_UN); held.close()
        _, _, err = seeded("query", "delta")
        assert "not indexed" in err.lower()                     # skipped → #4 still unindexed


class TestAutoReindexConfig:
    def test_env_disables(self, monkeypatch):
        from worklog import config
        monkeypatch.setenv("WORKLOG_AUTO_REINDEX", "0")
        assert config.auto_reindex_enabled() is False

    def test_env_enables(self, monkeypatch):
        from worklog import config
        monkeypatch.setenv("WORKLOG_AUTO_REINDEX", "1")
        assert config.auto_reindex_enabled() is True
