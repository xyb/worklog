# worklog Makefile
# Unified entry point for common dev / install / git actions.
# Dependency management goes through uv (https://docs.astral.sh/uv/).

UV          := uv
PYTHON      := $(UV) run python
PYTEST      := $(UV) run pytest
WL_BIN      := $(HOME)/bin/wl
PROJ_DIR    := $(shell pwd)
GIT         := /usr/bin/git

# Resolve the DB path — matches wl.py _resolve_db_path:
# $WORKLOG_DB env, else $XDG_DATA_HOME/worklog/worklog.db (default ~/.local/share/worklog/worklog.db)
WORKLOG_DB_PATH  := $(shell \
  if [ -n "$$WORKLOG_DB" ]; then echo "$$WORKLOG_DB"; \
  else echo "$${XDG_DATA_HOME:-$$HOME/.local/share}/worklog/worklog.db"; fi)

.PHONY: help sync test test-v test-fast cov install uninstall reinstall push pull status commit demo clean reset setup

help:                ## show this help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# ── dev ──

sync:                ## uv sync (runtime + dev deps from pyproject.toml + uv.lock)
	@$(UV) sync --all-groups
	@echo "✓ .venv synced from pyproject.toml + uv.lock"

test:                ## run pytest (parallel + cov + 95% gate, reads pytest.ini)
	@$(PYTEST)

test-v:              ## run pytest verbose (no parallel, useful for debug output)
	@$(PYTEST) -v -p no:xdist --no-cov

test-fast:           ## run pytest only (no cov, no gate; quick dev feedback)
	@$(PYTEST) --no-cov -n auto

cov:                 ## detailed coverage report (term-missing, includes 95% gate)
	@$(PYTEST)

# ── install / uninstall ──

install: sync        ## install ~/bin/wl wrapper pointing into the project .venv
	@printf '#!/usr/bin/env bash\nexec %s/.venv/bin/python %s/wl.py "$$@"\n' "$(PROJ_DIR)" "$(PROJ_DIR)" > $(WL_BIN)
	@chmod +x $(WL_BIN)
	@echo "✓ $(WL_BIN) installed"
	@which wl
	@echo ""
	@echo "Shell completion (init-load mode; new shells auto-pick-up code changes):"
	@echo "  fish: add to ~/.config/fish/config.fish →  wl print-completion fish | source"
	@echo "  bash: add to ~/.bashrc                 →  eval \"\$$(wl print-completion bash)\""
	@echo "  zsh:  add to ~/.zshrc                  →  eval \"\$$(wl print-completion zsh)\""
	@echo "  aliases: ~/.config/worklog/aliases.ini  [aliases] d = day / c = checkin / ..."

uninstall:           ## remove wrapper (keeps .venv + DB; manually clean the wl print-completion line in your shell rc)
	@rm -f $(WL_BIN)
	@echo "✓ wl wrapper removed (.venv + DB $(WORKLOG_DB_PATH) kept)"
	@echo "  (if ~/.config/fish/config.fish has 'wl print-completion fish | source', remove it manually)"

reinstall: uninstall install  ## clean reinstall

setup: sync install  ## first-time setup: uv sync + install wrapper

# ── demo / sample data ──

demo:                ## populate DB with sample tree (idempotent reset DB)
	@rm -f $(WORKLOG_DB_PATH)
	@wl init
	@wl add "Lifetime" -k lifetime
	@wl add "2026" -k year --parent 1
	@wl add "2026-Q2" -k quarter --parent 2
	@wl add "2026-05" -k month --parent 3
	@wl add "2026-W21" -k week --parent 4
	@wl add "2026-05-18 Mon" -k day --parent 5
	@wl add "Dev tooling" -k project -p A -t work --parent 4
	@wl add "Dev tooling — strategy pivot" -k task -p A -t work,unplanned,P0,dev_tooling --parent 6
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
