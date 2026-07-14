# worklog Makefile
# Unified entry point for common dev / install / git actions.
# Dependency management goes through uv (https://docs.astral.sh/uv/).

UV          := uv
PYTHON      := $(UV) run python
PYTEST      := $(UV) run pytest
PROJ_DIR    := $(shell pwd)
GIT         := /usr/bin/git
# Dev interpreter, pinned on purpose: the golden --help byte comparison only runs on
# Python < 3.13 (argparse changed its layout), so developing on 3.13+ would silently skip it.
DEV_PY      := 3.9

# Resolve the DB path — matches worklog.cli._resolve_db_path:
# $WORKLOG_DB env, else $XDG_DATA_HOME/worklog/worklog.db (default ~/.local/share/worklog/worklog.db)
WORKLOG_DB_PATH  := $(shell \
  if [ -n "$$WORKLOG_DB" ]; then echo "$$WORKLOG_DB"; \
  else echo "$${XDG_DATA_HOME:-$$HOME/.local/share}/worklog/worklog.db"; fi)

.PHONY: help sync test test-v test-fast cov docker-test docker-test-image install uninstall reinstall push pull status commit demo clean reset setup

help:                ## show this help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# ── dev ──

# A uv-MANAGED (standalone) interpreter, not the system/framework one: a framework Python
# re-execs itself into Python.app and so cannot keep a custom process name — see
# scripts/run-tests.sh. Only affects the local venv; CI calls `uv run pytest` directly.
sync:                ## uv sync (runtime + dev deps from pyproject.toml + uv.lock)
	@UV_PYTHON_PREFERENCE=only-managed $(UV) sync --all-groups --python $(DEV_PY)
	@echo "✓ .venv synced from pyproject.toml + uv.lock"

test:                ## run pytest (parallel + cov + 95% gate; workers show as wl-pytest)
	@./scripts/run-tests.sh

test-v:              ## run pytest verbose (no parallel, useful for debug output)
	@$(PYTEST) -v -p no:xdist --no-cov

test-fast:           ## run pytest only (no cov, no gate; quick dev feedback)
	@./scripts/run-tests.sh --no-cov

cov:                 ## detailed coverage report (term-missing, includes 95% gate)
	@$(PYTEST)

# ── docker dev/test (physically isolated from the real DB — see Dockerfile.dev) ──

DEVTEST_IMG := worklog-devtest:latest

docker-test-image:   ## build the isolated dev/test image
	docker build -f Dockerfile.dev -t $(DEVTEST_IMG) .

docker-test: docker-test-image  ## run the FULL suite inside the container (never mounts the real data dir)
	docker run --rm -v "$(CURDIR)":/app -w /app $(DEVTEST_IMG)

# ── install / uninstall ──
# Safety rule: the daily `wl` must be a FROZEN snapshot from a CLEAN COMMITTED ref —
# never from the live working tree (.venv shares in-progress code + migrations).
# `make install` creates a temp git worktree at HEAD, runs `uv tool install` from it
# (isolated ~/.local/share/uv/tools/pyworklog/), then removes the worktree.

install:             ## install wl from a clean committed HEAD (isolated from dev .venv)
	@if ! $(GIT) diff --quiet || ! $(GIT) diff --cached --quiet; then \
	  echo "✗ working tree has uncommitted changes — commit first, then make install"; \
	  exit 1; \
	fi
	@WORKTREE=$$(mktemp -d /tmp/wl-install-XXXXXX) && \
	  $(GIT) worktree add --detach "$$WORKTREE" HEAD 2>/dev/null && \
	  $(UV) tool install --reinstall "$$WORKTREE" && \
	  $(GIT) worktree remove "$$WORKTREE"
	@echo "✓ wl installed from clean HEAD (isolated snapshot, not dev .venv)"
	@which wl
	@echo ""
	@echo "Shell completion:"
	@echo "  fish: add to ~/.config/fish/config.fish →  wl print-completion fish | source"
	@echo "  bash: add to ~/.bashrc                 →  eval \"\$$(wl print-completion bash)\""
	@echo "  zsh:  add to ~/.zshrc                  →  eval \"\$$(wl print-completion zsh)\""
	@echo "  aliases: ~/.config/worklog/aliases.ini  [aliases] d = day / c = checkin / ..."

uninstall:           ## remove the installed wl tool (keeps dev .venv + DB)
	@$(UV) tool uninstall pyworklog 2>/dev/null || true
	@echo "✓ pyworklog tool removed (dev .venv + DB $(WORKLOG_DB_PATH) kept)"

reinstall: uninstall install  ## clean reinstall from current committed HEAD

setup: sync install  ## first-time setup: sync dev deps + install isolated wl

# ── demo / sample data ──

demo:                ## populate a fresh demo DB with a sample tree (REFUSES to touch an existing DB)
	@if [ -e "$(WORKLOG_DB_PATH)" ]; then \
	  echo "✗ refusing to overwrite an existing DB: $(WORKLOG_DB_PATH)"; \
	  echo "  'make demo' must NEVER delete a real worklog. Point it at a throwaway demo DB:"; \
	  echo "    WORKLOG_DB=/tmp/wl-demo.db make demo"; \
	  echo "    WORKLOG_DB=/tmp/wl-demo.db wl tree      # then browse it"; \
	  exit 1; \
	fi
	@wl init
	@wl add "Lifetime" --prop type.date=lifetime
	@wl add "2026" --prop type.date=year --parent 1
	@wl add "2026-Q2" --prop type.date=quarter --parent 2
	@wl add "2026-05" --prop type.date=month --parent 3
	@wl add "2026-W21" --prop type.date=week --parent 4
	@wl add "2026-05-18" --prop type.date=day --parent 5
	@wl add "Dev tooling" --para project -p A -t work --parent 4
	@wl add "Dev tooling — strategy pivot" -p A -t work,unplanned,P0,dev_tooling --parent 6
	@wl log 8 "2026-05-18 17:18 strategy pivot decided"
	@wl log 8 "2026-05-19 09:42 break down requirements: export_for_ai"
	@wl log 8 "2026-05-20 14:55 path B working end-to-end, owners 6/6 -> 7/7"
	@wl log 8 "2026-05-21 11:08 retro: 87% cost reduction"
	@wl link 8 "Dev tooling"
	@wl done 8
	@echo
	@echo "=== demo tree ==="
	@wl tree

clean:               ## clean test cache + pycache
	@find . -type d \( -name __pycache__ -o -name .pytest_cache \) -exec rm -rf {} + 2>/dev/null || true
	@echo "✓ cache cleaned"

reset:               ## ⚠️ DROP DB + recreate empty (ask first)
	@read -p "⚠️ rm $(WORKLOG_DB_PATH) ? (y/N) " ans; \
	[ "$$ans" = "y" ] && rm -f $(WORKLOG_DB_PATH) && wl init || echo "abort"

# ── git ──

status:              ## git status
	@$(GIT) status --short

pull:                ## git pull --rebase
	@$(GIT) pull --rebase

push:                ## git push (current branch)
	@$(GIT) -c commit.gpgsign=false push -u origin $$($(GIT) branch --show-current)

commit:              ## git add -A + commit with $msg (default 'wip')
	@$(GIT) add -A
	@$(GIT) -c commit.gpgsign=false commit -m "$${msg:-wip}"

# ── one-shot ──

ship: test push      ## test then push (only push if tests pass)

all: setup test demo ## first-time: setup + test + demo

# default target
.DEFAULT_GOAL := help

# ── local overrides (gitignored; safe if absent) ──
# Drop site-specific variables, private remotes, or custom targets into
# any *.mk file under local/. The directory is gitignored and missing
# is fine — the `-` prefix on -include silences the no-match case.
-include local/*.mk
