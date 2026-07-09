"""Tests for `wl relation claim` / `wl relation unclaim` — ticket-level
exclusive claim, orthogonal to `block`/`ready` (claim = who's working it, not whether it
can be worked at all). Stored as plain UDA props `claimed_by` (free-string identity) /
`claimed_at` (UTC ISO timestamp) — not `relation.*` (a claim isn't a task↔task edge).

Identity defaults to `<agent>:<session id>`, the same derivation `wl agent` uses (reads
$WL_SESSION_ID / a runtime's own env var, and $WL_AGENT / the registry's env markers);
`--as IDENTITY` overrides with a free string (a human name, etc). A claim older than
`_STALE_CLAIM_HOURS` (24h) is treated as abandoned and can be taken over / released by
anyone without `--force`.
"""
import json
from datetime import datetime, timedelta

import pytest


def _mk(cli, n):
    for i in range(n):
        cli("add", f"task {i + 1}")


def _sess(monkeypatch, sid="sess-aaa"):
    monkeypatch.delenv("CURSOR_AGENT", raising=False)
    monkeypatch.delenv("CURSOR_CONVERSATION_ID", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", sid)
    monkeypatch.delenv("WL_SESSION_ID", raising=False)
    monkeypatch.delenv("WL_AGENT", raising=False)


def _no_session(monkeypatch):
    for k in ("CLAUDE_CODE_SESSION_ID", "CURSOR_CONVERSATION_ID", "CURSOR_AGENT",
              "WL_SESSION_ID", "WL_AGENT"):
        monkeypatch.delenv(k, raising=False)


def _prop(tmp_db, nid, key):
    con = tmp_db.db_connect()
    r = con.execute(
        "SELECT value FROM prop WHERE node_id=? AND key=? AND deleted_at IS NULL", (nid, key)
    ).fetchone()
    return r["value"] if r else None


def _backdate_claim(tmp_db, nid, hours_ago):
    """Directly rewrite claimed_at to simulate a stale claim, bypassing the CLI (there's
    no `--at` on `claim` — staleness is judged off the real clock). Matches
    timeutil.FMT ("%Y-%m-%d %H:%M:%S", UTC, naive) — the storage format for every `*_at`
    column."""
    con = tmp_db.db_connect()
    ts = (datetime.utcnow() - timedelta(hours=hours_ago)).strftime("%Y-%m-%d %H:%M:%S")
    con.execute("UPDATE prop SET value=? WHERE node_id=? AND key='claimed_at' AND deleted_at IS NULL",
                (ts, nid))
    con.commit()


class TestClaim:
    def test_claim_unclaimed_ticket(self, cli, tmp_db, monkeypatch):
        _sess(monkeypatch)
        _mk(cli, 1)
        code, out, _ = cli("relation", "claim", "1")
        assert code == 0
        assert "claimed" in out and "#1" in out
        assert _prop(tmp_db, 1, "claimed_by") == "claude:sess-aaa"
        assert _prop(tmp_db, 1, "claimed_at") is not None

    def test_claim_json_shape(self, cli, monkeypatch):
        _sess(monkeypatch)
        _mk(cli, 1)
        code, j, _ = cli("relation", "claim", "1", "-o", "json")
        assert code == 0
        d = json.loads(j)
        assert d["node_id"] == 1
        assert d["claimed_by"] == "claude:sess-aaa"
        assert "claimed_at" in d

    def test_claim_with_explicit_as_needs_no_session(self, cli, tmp_db, monkeypatch):
        _no_session(monkeypatch)
        _mk(cli, 1)
        code, out, _ = cli("relation", "claim", "1", "--as", "alice")
        assert code == 0
        assert _prop(tmp_db, 1, "claimed_by") == "alice"

    def test_claim_without_session_or_as_dies(self, cli, monkeypatch):
        _no_session(monkeypatch)
        _mk(cli, 1)
        code, _, err = cli("relation", "claim", "1")
        assert code != 0
        assert "--as" in err

    def test_reclaim_same_identity_is_a_refresh_not_an_error(self, cli, tmp_db, monkeypatch):
        _sess(monkeypatch)
        _mk(cli, 1)
        cli("relation", "claim", "1")
        code, out, _ = cli("relation", "claim", "1")
        assert code == 0
        assert "already claimed by you" in out

    def test_claim_by_another_fresh_identity_is_rejected(self, cli, monkeypatch):
        _sess(monkeypatch, "sess-aaa")
        _mk(cli, 1)
        cli("relation", "claim", "1")
        _sess(monkeypatch, "sess-bbb")
        code, _, err = cli("relation", "claim", "1")
        assert code != 0
        assert "already claimed by" in err
        assert "claude:sess-aaa" in err

    def test_claim_by_another_identity_after_going_stale_succeeds(self, cli, tmp_db, monkeypatch):
        _sess(monkeypatch, "sess-aaa")
        _mk(cli, 1)
        cli("relation", "claim", "1")
        _backdate_claim(tmp_db, 1, 25)   # > _STALE_CLAIM_HOURS (24)
        _sess(monkeypatch, "sess-bbb")
        code, out, _ = cli("relation", "claim", "1")
        assert code == 0
        assert "stale" in out
        assert _prop(tmp_db, 1, "claimed_by") == "claude:sess-bbb"

    def test_claim_requires_node_to_exist(self, cli, monkeypatch):
        _sess(monkeypatch)
        code, _, err = cli("relation", "claim", "999")
        assert code != 0


class TestUnclaim:
    def test_unclaim_releases_own_claim(self, cli, tmp_db, monkeypatch):
        _sess(monkeypatch)
        _mk(cli, 1)
        cli("relation", "claim", "1")
        code, out, _ = cli("relation", "unclaim", "1")
        assert code == 0
        assert "unclaimed" in out
        assert _prop(tmp_db, 1, "claimed_by") is None
        assert _prop(tmp_db, 1, "claimed_at") is None

    def test_unclaim_never_claimed_is_a_friendly_noop(self, cli, monkeypatch):
        _sess(monkeypatch)
        _mk(cli, 1)
        code, out, _ = cli("relation", "unclaim", "1")
        assert code == 0
        assert "wasn't claimed" in out

    def test_unclaim_someone_elses_fresh_claim_rejected(self, cli, monkeypatch):
        _sess(monkeypatch, "sess-aaa")
        _mk(cli, 1)
        cli("relation", "claim", "1")
        _sess(monkeypatch, "sess-bbb")
        code, _, err = cli("relation", "unclaim", "1")
        assert code != 0
        assert "claude:sess-aaa" in err
        assert "--force" in err

    def test_unclaim_with_force_overrides_someone_elses_claim(self, cli, tmp_db, monkeypatch):
        _sess(monkeypatch, "sess-aaa")
        _mk(cli, 1)
        cli("relation", "claim", "1")
        _sess(monkeypatch, "sess-bbb")
        code, out, _ = cli("relation", "unclaim", "1", "--force")
        assert code == 0
        assert _prop(tmp_db, 1, "claimed_by") is None

    def test_unclaim_stale_claim_needs_no_force_and_no_identity(self, cli, tmp_db, monkeypatch):
        _sess(monkeypatch, "sess-aaa")
        _mk(cli, 1)
        cli("relation", "claim", "1")
        _backdate_claim(tmp_db, 1, 25)
        _no_session(monkeypatch)   # no identity available at all — must not be needed
        code, out, _ = cli("relation", "unclaim", "1")
        assert code == 0
        assert _prop(tmp_db, 1, "claimed_by") is None

    def test_unclaim_with_explicit_as_matching_owner_succeeds(self, cli, tmp_db, monkeypatch):
        _no_session(monkeypatch)
        _mk(cli, 1)
        cli("relation", "claim", "1", "--as", "alice")
        code, out, _ = cli("relation", "unclaim", "1", "--as", "alice")
        assert code == 0
        assert _prop(tmp_db, 1, "claimed_by") is None


class TestClaimConcurrency:
    def test_two_racing_claims_serialize_exactly_one_wins(self, cli, tmp_db):
        """The exclusive-claim guarantee under real contention: two connections race the same
        read-check-write on one ticket. `_immediate_txn` holds the write lock across it, so exactly
        one wins and the loser reads the committed claim and backs off. Without the lock both SELECTs
        read "unclaimed" and both write — a silent double-claim (the bug this fixes). Threads because
        the race is inherently concurrent; each thread owns its own connection."""
        import threading
        from worklog.queries import _immediate_txn, _upsert_prop
        from worklog.models import Prop

        _mk(cli, 1)                      # node #1
        barrier = threading.Barrier(2)   # line both racers up so they actually contend
        outcomes = {}

        def racer(who):
            con = tmp_db.db_connect()
            try:
                barrier.wait()
                with _immediate_txn(con):
                    cur = Prop.query_one(con, node_id=1, key="claimed_by")
                    if cur and cur.value:
                        outcomes[who] = "backed_off"
                        return
                    _upsert_prop(con, 1, "claimed_by", who)
                outcomes[who] = "won"
            finally:
                con.close()

        threads = [threading.Thread(target=racer, args=(w,)) for w in ("a", "b")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert sorted(outcomes.values()) == ["backed_off", "won"], outcomes
        con = tmp_db.db_connect()
        rows = con.execute(
            "SELECT value FROM prop WHERE node_id=1 AND key='claimed_by' AND deleted_at IS NULL"
        ).fetchall()
        con.close()
        assert len(rows) == 1   # exactly one claimant persisted, not a clobbered pair
