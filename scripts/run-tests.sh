#!/bin/sh
# Run the suite so its processes are identifiable in macOS Activity Monitor, and so they leave
# the machine usable while they run. Eight anonymous "Python" processes pinning every core look
# exactly like something has gone wrong — which is the whole problem being fixed here.
#
# HOW THE RENAME WORKS (and why the obvious approaches don't):
# Activity Monitor shows a process under the kernel's `p_comm`, filled at exec time from the
# FILENAME of the binary that was exec'd. It is not argv[0]. So `exec -a` and any argv rewrite
# change what `ps` prints and nothing that Activity Monitor reads. The only way to get a name is
# to exec a binary whose filename already IS that name — hence a hardlink to the interpreter,
# called `wl-pytest`:
#
#   * a HARDLINK (or copy), never a symlink — the kernel resolves a symlink and then reports the
#     TARGET's name, so a renamed symlink buys nothing;
#   * living in .venv/bin, so `sys.prefix` still resolves to the venv and the project's
#     dependencies are importable;
#   * plus libpython linked into .venv/lib — see below;
#   * and the interpreter must NOT be a framework build (python.org / Homebrew /
#     CommandLineTools): a framework Python re-execs itself into Python.app/…/Python, and that
#     second exec overwrites the name. That is exactly why the workers show up as a generic
#     "Python" today, so `make sync` pins the venv to a uv-managed standalone build.
#
# pytest is then launched THROUGH that hardlink, which makes it `sys.executable` — so every
# xdist worker inherits the name for free. No `--tx` spec to keep in sync with pytest.ini
# (whose `-n auto` in addopts would override a --tx anyway).
#
# `setproctitle` is the other route, and it does work — but on macOS it reaches Activity Monitor
# by checking the process in to LaunchServices through a private API, which registers each worker
# as an "app" and bounces a Dock icon for it. Eight bouncing icons is worse than the problem.
# This needs no dependency and no private API.
#
# CI never runs this script — it calls `uv run pytest` directly and reads pytest.ini — so CI
# behavior (plain `-n auto`) is untouched.
set -eu

NAME=wl-pytest                 # ≤16 chars: p_comm is truncated at MAXCOMLEN
VENV=.venv

# Leave headroom. Saturating every core is half of what makes a test run look like a runaway.
WORKERS=${WORKERS:-$(( $(sysctl -n hw.ncpu) - 2 ))}
[ "$WORKERS" -lt 1 ] && WORKERS=1

REAL=$(uv run python -c 'import os, sys; print(os.path.realpath(sys.executable))')

case "$REAL" in
    *Python3.framework*|*Python.app*|*/Frameworks/*)
        echo "note: $REAL is a framework build. It re-execs itself into Python.app, which"
        echo "      overwrites the process name, so the workers can't be named. Running as"
        echo "      usual — to get named workers, rebuild the venv on a managed Python:"
        echo "        make sync"
        exec uv run pytest -n "$WORKERS" "$@"
        ;;
esac

rm -f "$VENV/bin/$NAME"
ln "$REAL" "$VENV/bin/$NAME" 2>/dev/null || cp "$REAL" "$VENV/bin/$NAME"

# The interpreter finds libpython through `@executable_path/../lib`. The venv's own bin/python is
# a symlink, so that resolves against the base install and just works — but our hardlink is a real
# file in .venv/bin, so it resolves against .venv/lib, which holds no libpython. Without this the
# workers die in dyld and xdist quietly degrades to running the whole suite in one process.
BASE_LIB=$(dirname "$(dirname "$REAL")")/lib
for dylib in "$BASE_LIB"/libpython*.dylib; do
    [ -e "$dylib" ] && ln -sf "$dylib" "$VENV/lib/$(basename "$dylib")"
done

exec "$VENV/bin/$NAME" -m pytest -n "$WORKERS" "$@"
