"""Graph integrity checks (`check_integrity` / `wl doctor`). FK enforcement is OFF, so nothing
in the DB prevents the inconsistencies a foreign key would: dangling parent_id, parent cycles,
orphaned spoke rows, and relation.* refs to dead nodes. These tests build each broken shape via
raw SQL (bypassing every write guard, simulating legacy/corrupt data) and assert the checker
reports it with the right kind + node. (One-sided relation.* edges are NOT checked — every
relation type is single-write by design, the reverse is derived, never stored.)"""
import pytest

from worklog.graph import check_integrity


def _con(tmp_db):
    tmp_db.ensure_db()
    return tmp_db.db_connect()


def _node(con, nid, parent=None, title=None):
    con.execute("INSERT INTO node (id, title, parent_id, created_at) VALUES (?,?,?,?)",
                (nid, title or f"n{nid}", parent, "2026-06-06 00:00:00"))


def _kinds(issues):
    return {i.kind for i in issues}


def _of_kind(issues, kind):
    return [i for i in issues if i.kind == kind]


class TestCleanGraph:
    def test_consistent_graph_has_no_issues(self, tmp_db):
        con = _con(tmp_db)
        _node(con, 1)            # root
        _node(con, 2, parent=1)  # child of 1
        # a valid spoke (log on a live node)
        con.execute("INSERT INTO log (node_id, logged_at, body) VALUES (?,?,?)",
                    (1, "2026-06-06 00:00:00", "note"))
        # a one-sided relation edge — the norm now, not dirt (single-write by design)
        con.execute("INSERT INTO prop (node_id, key, value) VALUES (?,?,?)", (1, "relation.split", "2"))
        con.commit()
        assert check_integrity(con) == []


class TestDanglingParent:
    def test_parent_points_at_missing_node(self, tmp_db):
        con = _con(tmp_db)
        _node(con, 1)
        _node(con, 2, parent=999)   # parent 999 doesn't exist
        con.commit()
        issues = check_integrity(con)
        assert "dangling_parent" in _kinds(issues)
        assert any(i.node_id == 2 for i in _of_kind(issues, "dangling_parent"))

    def test_parent_is_soft_deleted(self, tmp_db):
        con = _con(tmp_db)
        _node(con, 1)
        _node(con, 2, parent=1)
        con.execute("UPDATE node SET deleted_at = ? WHERE id = 1", ("2026-06-06 01:00:00",))  # delete parent, keep child
        con.commit()
        issues = check_integrity(con)
        assert any(i.node_id == 2 for i in _of_kind(issues, "dangling_parent"))


class TestCycle:
    def test_parent_cycle_is_reported(self, tmp_db):
        con = _con(tmp_db)
        _node(con, 1)
        _node(con, 2, parent=1)
        _node(con, 3, parent=2)
        con.execute("UPDATE node SET parent_id = 3 WHERE id = 1")  # 1->3->2->1
        con.commit()
        issues = check_integrity(con)
        assert "cycle" in _kinds(issues)
        cyc_nodes = {i.node_id for i in _of_kind(issues, "cycle")}
        assert cyc_nodes & {1, 2, 3}   # at least one node on the loop reported


class TestOrphanSpoke:
    def test_live_log_on_missing_node(self, tmp_db):
        con = _con(tmp_db)
        _node(con, 1)
        con.execute("INSERT INTO log (node_id, logged_at, body) VALUES (?,?,?)",
                    (999, "2026-06-06 00:00:00", "orphan log"))   # node 999 doesn't exist
        con.commit()
        issues = check_integrity(con)
        assert "orphan_spoke" in _kinds(issues)
        assert any(i.node_id == 999 for i in _of_kind(issues, "orphan_spoke"))

    def test_live_tag_on_soft_deleted_node(self, tmp_db):
        con = _con(tmp_db)
        _node(con, 1)
        con.execute("INSERT INTO tag (node_id, tag) VALUES (?,?)", (1, "work"))
        con.execute("UPDATE node SET deleted_at = ? WHERE id = 1", ("2026-06-06 01:00:00",))  # node gone, tag left live
        con.commit()
        issues = check_integrity(con)
        assert any(i.node_id == 1 for i in _of_kind(issues, "orphan_spoke"))


class TestDeadRelation:
    def test_relation_points_at_missing_node(self, tmp_db):
        con = _con(tmp_db)
        _node(con, 1)
        con.execute("INSERT INTO prop (node_id, key, value) VALUES (?,?,?)",
                    (1, "relation.split", "999"))   # 999 doesn't exist
        con.commit()
        issues = check_integrity(con)
        assert "dead_relation" in _kinds(issues)
        assert any(i.node_id == 1 for i in _of_kind(issues, "dead_relation"))


class TestOneSidedRelationIsClean:
    """Every relation type is single-write by design (the reverse is derived, never stored),
    so a one-sided edge is the norm, not dirt — there's no asymmetric_relation check."""

    def test_one_sided_split_reports_nothing(self, tmp_db):
        con = _con(tmp_db)
        _node(con, 1)
        _node(con, 2)
        con.execute("INSERT INTO prop (node_id, key, value) VALUES (?,?,?)", (1, "relation.split", "2"))
        con.commit()
        assert check_integrity(con) == []

    def test_dead_owner_relation_prop_is_orphan_spoke(self, tmp_db):
        """A relation prop whose OWNER node is missing/deleted (an orphan prop the cascade
        missed) is reported by orphan_spoke."""
        con = _con(tmp_db)
        _node(con, 1)   # live; owner node #2 never existed
        con.execute("INSERT INTO prop (node_id, key, value) VALUES (2, 'relation.related', '1')")
        con.commit()
        assert "orphan_spoke" in _kinds(check_integrity(con))


class TestSelfRelation:
    def test_self_referential_relation_is_flagged(self, tmp_db):
        """A node relating to itself (relation.* listing its own id) is dirt the write path avoids
        and relation_view silently drops — so it'd be invisible. doctor must surface it."""
        con = _con(tmp_db)
        _node(con, 1)
        con.execute("INSERT INTO prop (node_id, key, value) VALUES (1, 'relation.related', '1')")  # relates to self
        con.commit()
        kinds = _kinds(check_integrity(con))
        assert "self_relation" in kinds
        assert "dead_relation" not in kinds  # a self-ref must not be misclassified as dead (it's live)


class TestBareTimestamp:
    """logged_at must be a full UTC instant; a date-only value (legacy `wl log --date` behavior, or
    a manual SQL edit) loses intra-day ordering. doctor surfaces it so it can be fixed."""

    def test_date_only_logged_at_is_flagged(self, tmp_db):
        con = _con(tmp_db)
        _node(con, 1)
        con.execute("INSERT INTO log (node_id, logged_at, body) VALUES (1, '2026-06-20', 'bare-date log')")
        con.commit()
        issues = check_integrity(con)
        assert "bare_timestamp" in _kinds(issues)
        assert any(i.node_id == 1 for i in _of_kind(issues, "bare_timestamp"))

    def test_full_instant_logged_at_is_clean(self, tmp_db):
        con = _con(tmp_db)
        _node(con, 1)
        con.execute("INSERT INTO log (node_id, logged_at, body) VALUES (1, '2026-06-20 14:30:00', 'ok')")
        con.commit()
        assert "bare_timestamp" not in _kinds(check_integrity(con))


class TestDoctorCommand:
    """End-to-end: `wl doctor` reports issues (text + JSON), read-only."""

    def test_clean_graph_reports_consistent(self, cli):
        cli("add", "a task")
        _, out, _ = cli("doctor")
        assert "consistent" in out

    def test_reports_dangling_parent(self, cli, tmp_db):
        cli("add", "root")                       # #1
        con = tmp_db.db_connect()
        con.execute("INSERT INTO node (id, title, parent_id, created_at) VALUES (2,'orphan',999,'2026-06-06 00:00:00')")
        con.commit()
        _, out, _ = cli("doctor")
        assert "dangling_parent" in out and "#2" in out

    def test_json_output(self, cli, tmp_db):
        import json
        cli("add", "root")
        con = tmp_db.db_connect()
        con.execute("INSERT INTO node (id, title, parent_id, created_at) VALUES (2,'orphan',999,'2026-06-06 00:00:00')")
        con.commit()
        _, out, _ = cli("doctor", "-o", "json")
        data = json.loads(out)
        assert data["issue_count"] >= 1
        assert any(i["kind"] == "dangling_parent" and i["node_id"] == 2 for i in data["issues"])
