"""`wl demo` — seed a sample "a day with wl" dataset, EMPTY database only.

The command drives the real handlers (add / goal / log / done / recap), so these tests assert the
seeded shape (projects, a done focus task, logs) rather than exact wording, and — the safety
contract — that it refuses the moment the database holds any node."""


def _titles(tmp_db):
    con = tmp_db.db_connect()
    return {r["title"] for r in con.execute("SELECT title FROM node WHERE deleted_at IS NULL")}


class TestDemo:
    def test_seeds_a_populated_day_on_empty_db(self, cli, tmp_db):
        code, out, _ = cli("demo")
        assert code == 0
        assert "seeded" in out
        titles = _titles(tmp_db)
        # the two projects + the two of today's tasks the story is built around
        assert {"Ship the monthly report", "Learn how AI agents work",
                "Write the report summary", "Do the AI-agents tutorial"} <= titles

    def test_focus_task_is_done_and_logged(self, cli, tmp_db):
        cli("demo")
        con = tmp_db.db_connect()
        st = con.execute(
            "SELECT status FROM node WHERE title='Write the report summary' AND deleted_at IS NULL"
        ).fetchone()["status"]
        assert st == "DONE"
        # at least the task log + the day's goal + the recap were written
        nlog = con.execute("SELECT COUNT(*) FROM log WHERE deleted_at IS NULL").fetchone()[0]
        assert nlog >= 3

    def test_sets_todays_goal(self, cli, tmp_db):
        cli("demo")
        con = tmp_db.db_connect()
        ngoal = con.execute(
            "SELECT COUNT(*) FROM log WHERE tag='goal' AND deleted_at IS NULL"
        ).fetchone()[0]
        assert ngoal >= 1

    def test_refuses_on_a_non_empty_db(self, cli):
        cli("add", "a real task")
        code, _, err = cli("demo")
        assert code != 0
        assert "empty" in err.lower()

    def test_refuses_to_run_twice(self, cli):
        # the guard is the whole safety story: a second run must not scribble more sample data
        assert cli("demo")[0] == 0
        code, _, err = cli("demo")
        assert code != 0
        assert "empty" in err.lower()
