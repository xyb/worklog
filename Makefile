# worklog Makefile
# Unified entry point for common dev / install / git actions.

PYTHON      := $(HOME)/.virtualenvs/worklog/bin/python
PIP         := $(HOME)/.virtualenvs/worklog/bin/pip
VENV        := $(HOME)/.virtualenvs/worklog
WL_BIN      := $(HOME)/bin/wl
FISH_COMP   := $(HOME)/.config/fish/completions/wl.fish
PROJ_DIR    := $(shell pwd)
GIT         := /usr/bin/git

# Resolve the DB path — matches wl.py _resolve_db_path priority:
# $WL_DB > legacy ~/.worklog/wl.db (if it exists) > $XDG_DATA_HOME/wl/wl.db
WL_DB_PATH  := $(shell \
  if [ -n "$$WL_DB" ]; then echo "$$WL_DB"; \
  elif [ -e "$$HOME/.worklog/wl.db" ]; then echo "$$HOME/.worklog/wl.db"; \
  else echo "$${XDG_DATA_HOME:-$$HOME/.local/share}/wl/wl.db"; fi)

.PHONY: help test test-v install uninstall reinstall push pull status commit demo clean reset venv setup

help:                ## show this help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# ── dev ──

venv:                ## (re)create venv + install runtime + dev deps
	@test -d $(VENV) || python3 -m venv $(VENV)
	@$(PIP) install -q -r requirements.txt -r requirements-dev.txt
	@echo "✓ venv: $(VENV) (runtime + dev deps from requirements*.txt)"

test:                ## run pytest (parallel + cov + 95% gate, reads pytest.ini)
	@$(PYTHON) -m pytest

test-v:              ## run pytest verbose (no parallel, useful for debug output)
	@$(PYTHON) -m pytest -v -p no:xdist --no-cov

test-fast:           ## run pytest only (no cov, no gate; quick dev feedback)
	@$(PYTHON) -m pytest --no-cov -n auto

cov:                 ## detailed coverage report (term-missing, includes 95% gate)
	@$(PYTHON) -m pytest

# ── install / uninstall ──

install: venv        ## install ~/bin/wl wrapper; completions go via init-load (see below)
	@printf '#!/usr/bin/env bash\nexec %s %s/wl.py "$$@"\n' "$(PYTHON)" "$(PROJ_DIR)" > $(WL_BIN)
	@chmod +x $(WL_BIN)
	@echo "✓ $(WL_BIN) installed"
	@which wl
	@echo ""
	@echo "Shell completion (init-load mode; new shells auto-pick-up code changes):"
	@echo "  fish: add to ~/.config/fish/config.fish →  wl print-completion fish | source"
	@echo "  bash: add to ~/.bashrc                 →  eval \"\$$(wl print-completion bash)\""
	@echo "  zsh:  add to ~/.zshrc                  →  eval \"\$$(wl print-completion zsh)\""
	@echo "  aliases: ~/.config/wl/aliases.ini  [aliases] d = day / c = checkin / ..."

uninstall:           ## remove wrapper (keeps venv + DB; manually clean the wl print-completion line in your shell rc)
	@rm -f $(WL_BIN) $(FISH_COMP)
	@echo "✓ wl wrapper removed (venv $(VENV) + DB $(WL_DB_PATH) kept)"
	@echo "  (if ~/.config/fish/config.fish has 'wl print-completion fish | source', remove it manually)"

reinstall: uninstall install  ## clean reinstall

setup: venv install  ## first-time setup: venv + install

# ── demo / sample data ──

demo:                ## populate DB with sample tree (idempotent reset DB)
	@rm -f $(WL_DB_PATH)
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
	@read -p "⚠️ rm $(WL_DB_PATH) ? (y/N) " ans; \
	[ "$$ans" = "y" ] && rm -f $(WL_DB_PATH) && wl init || echo "abort"

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
# Example: local/private.mk can override variables (PYTHON, GIT) or add
# extra targets (push-mirror, deploy-staging, etc.).
-include local/*.mk
