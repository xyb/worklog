# worklog Makefile
# 常用动作统一入口, 避免每次手打长命令

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

# ── 开发 ──

venv:                ## (re)create venv + install runtime + dev 依赖
	@test -d $(VENV) || python3 -m venv $(VENV)
	@$(PIP) install -q -r requirements.txt -r requirements-dev.txt
	@echo "✓ venv: $(VENV) (runtime + dev deps from requirements*.txt)"

test:                ## run pytest (parallel + cov + 95% gate, 读 pytest.ini)
	@$(PYTHON) -m pytest

test-v:              ## run pytest verbose (无并行, 调试看输出)
	@$(PYTHON) -m pytest -v -p no:xdist --no-cov

test-fast:           ## run pytest 仅跑测, 不算 cov, 不门槛 (开发期快速反馈)
	@$(PYTHON) -m pytest --no-cov -n auto

cov:                 ## 详细 coverage 报告 (term-missing, 含 95% gate)
	@$(PYTHON) -m pytest

# ── 安装 / 卸载 ──

install: venv        ## install ~/bin/wl wrapper; 补全走 init load (见下)
	@printf '#!/usr/bin/env bash\nexec %s %s/wl.py "$$@"\n' "$(PYTHON)" "$(PROJ_DIR)" > $(WL_BIN)
	@chmod +x $(WL_BIN)
	@echo "✓ $(WL_BIN) installed"
	@which wl
	@echo ""
	@echo "Shell 补全 (init load 模式, 开新 shell 自动跟代码改动):"
	@echo "  fish: 在 ~/.config/fish/config.fish 加 →  wl print-completion fish | source"
	@echo "  bash: 在 ~/.bashrc            加 →  eval \"\$$(wl print-completion bash)\""
	@echo "  zsh:  在 ~/.zshrc             加 →  eval \"\$$(wl print-completion zsh)\""
	@echo "  别名: ~/.config/wl/aliases.ini  [aliases] d = day / c = checkin / ..."

uninstall:           ## remove wrapper (keep venv + DB; 用户需手清 config.fish 里的 wl print-completion 行)
	@rm -f $(WL_BIN) $(FISH_COMP)
	@echo "✓ wl wrapper removed (venv $(VENV) + DB $(WL_DB_PATH) kept)"
	@echo "  (如有 ~/.config/fish/config.fish 内 'wl print-completion fish | source' 行需手清)"

reinstall: uninstall install  ## clean reinstall

setup: venv install  ## first-time setup: venv + install

# ── 演示 / 数据 ──

demo:                ## populate DB with 5/18 sample (idempotent reset DB)
	@rm -f $(WL_DB_PATH)
	@wl init
	@wl add "Lifetime" -k lifetime
	@wl add "2026 年" -k year --parent 1
	@wl add "2026-Q2" -k quarter --parent 2
	@wl add "2026-05" -k month --parent 3
	@wl add "2026-W21" -k week --parent 4
	@wl add "2026-05-18 周一" -k day --parent 5
	@wl add "Infra 智能化" -k project -p A -t work --parent 4
	@wl add "Infra 智能化 项目战略转向" -k task -p A -t work,unplanned,P0,infra_intel --parent 6
	@wl log 8 "5/18 17:18 拍板战略转向 #76"
	@wl log 8 "5/19 09:42 拆需求 export_for_ai"
	@wl log 8 "5/20 14:55 B 路径端到端打通 owner 6/6→7/7"
	@wl log 8 "5/21 11:08 复盘 87% 成本下降"
	@wl link 8 "Infra 智能化"
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

# ── 一键 ──

ship: test push      ## test → push (CI 跑通才推)

all: setup test demo ## first-time: setup + test + demo

# 默认 target
.DEFAULT_GOAL := help

# ── local overrides (gitignored; safe if absent) ──
# Drop site-specific variables, private remotes, or custom targets into
# any *.mk file under local/. The directory is gitignored and missing
# is fine — the `-` prefix on -include silences the no-match case.
# Example: local/private.mk can override variables (PYTHON, GIT) or add
# extra targets (push-mirror, deploy-staging, etc.).
-include local/*.mk
