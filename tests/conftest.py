"""pytest fixtures: 每个测试一个独立 SQLite DB（tmp_path）"""
import os
import sys
from pathlib import Path
import pytest

# 让 tests/ 能 import wl 主模块
PROJ_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJ_ROOT))


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """每个测试一个临时 DB, 测完自动清"""
    db_file = tmp_path / "wl-test.db"
    monkeypatch.setenv("WL_DB", str(db_file))
    # reload wl module 让 DB_PATH 重新读 env
    import importlib
    import wl
    importlib.reload(wl)
    return wl


def run_cli(wl, *args):
    """模拟命令行 argv 跑 main(), 返回 (exit_code, stdout, stderr) — 抓 print 输出"""
    import io
    import contextlib

    parser = wl.build_parser()
    parsed = parser.parse_args(list(args))
    wl._init_console(parsed.color, parsed.theme)

    wl.ensure_db()
    con = wl.db_connect()
    buf_out = io.StringIO()
    buf_err = io.StringIO()
    exit_code = 0
    try:
        with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
            try:
                wl.HANDLERS[parsed.cmd](parsed, con)
            except SystemExit as e:
                # sys.exit("msg") 把 msg 存在 e.code, 解释器默认会 print 到 stderr; 我们 catch 后要自己写
                if isinstance(e.code, int):
                    exit_code = e.code
                elif e.code is None:
                    exit_code = 0
                else:
                    exit_code = 1
                    print(e.code, file=sys.stderr)
    finally:
        con.close()
    return exit_code, buf_out.getvalue(), buf_err.getvalue()


@pytest.fixture
def cli(tmp_db):
    """返回 run_cli 的偏函数, 自动绑定 wl module"""
    def _run(*args):
        return run_cli(tmp_db, *args)
    return _run
