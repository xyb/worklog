<sub><a href="README.md">🌐 English</a> · <b>中文</b></sub>

# worklog

[![Test](https://github.com/xyb/worklog/actions/workflows/test.yml/badge.svg)](https://github.com/xyb/worklog/actions/workflows/test.yml)
[![codecov](https://codecov.io/gh/xyb/worklog/branch/main/graph/badge.svg)](https://codecov.io/gh/xyb/worklog)

SQLite 后端的 worklog 工具,`todo.sh` 风格 CLI。完整执行体系层级建模在单个 `node` 表里 —— lifetime / decade / year / quarter / month / week / day / project / task / habit / signal / meetlog —— 共享同一个 id 空间,通过 `parent_id` 自引用形成树状结构。

**设计约定见 [DESIGN.md](DESIGN.md)** —— 加命令前必读,保持各处一致。
**AI 协作见 [skills/worklog-cli/SKILL.md](skills/worklog-cli/SKILL.md)** —— Claude Code skill(何时 / 如何用 `wl` + 批量 import / apply)。
背景: 结构化 worklog 工具,在调研了 12 个候选产品(Logseq / Tana / TaskWarrior / org-mode / Anytype / Capacities / Linear 等)后没找到能同时覆盖三个维度(时间层级 / 项目层级 / vault wikilink)又无折中的现成方案,所以自建。

## 安装

需要先装 [uv](https://docs.astral.sh/uv/)(`brew install uv` 或 `pipx install uv`)。

```fish
git clone https://github.com/xyb/worklog.git ~/projects/worklog
cd ~/projects/worklog
make setup       # uv sync + 装 ~/bin/wl wrapper

# shell 补全 (init load 模式, 任选 shell)
# fish: ~/.config/fish/config.fish 加
echo 'wl print-completion fish | source' >> ~/.config/fish/config.fish
# bash: ~/.bashrc       加  eval "$(wl print-completion bash)"
# zsh:  ~/.zshrc        加  eval "$(wl print-completion zsh)"

wl init
```

`make setup` 内部跑 `uv sync` 根据 `pyproject.toml` + `uv.lock` 建 `.venv/`,然后装 `~/bin/wl` wrapper 指向那个 `.venv`。

数据库位置遵循 [XDG Base Directory 规范](https://specifications.freedesktop.org/basedir-spec/): 默认 `$XDG_DATA_HOME/worklog/worklog.db`(即 `~/.local/share/worklog/worklog.db`)。可以用 `wl --db PATH ...` 单次覆盖,也可以用 `$WORKLOG_DB` 环境变量全局覆盖。用户配置(aliases.ini)走 `$XDG_CONFIG_HOME/worklog/aliases.ini`(默认 `~/.config/worklog/aliases.ini`)。

## 命令

```fish
wl add "调研 X" -k task -p A -t work,P0 --proj dev_tooling --parent 42
wl add "Dev tooling" -k project -p A --parent 4   # 项目挂在月份下
wl log 42 "今天看了 A 资料, 发现..."
wl done 42
wl defer 42 2026-06-01
wl start 42 ; wl stop 42                            # CLOCK in/out
wl link 42 "Dev tooling"                           # vault wikilink
wl set 42 owner xyb                               # 自定义 prop
wl show 42                                          # 详情 + log + tags + links
wl ls                                               # 默认列出未完成项
wl ls --kind project --tag work,P0
wl tree                                             # 全树
wl tree --kind year --depth 3
wl logs --since 2026-05-18                          # 跨任务 log 时间段查询
wl find needle                                      # 全文搜索, 命中高亮 + 缩进展开
```

### 高亮 / 配色

终端默认带颜色(rich);全局参数放在子命令前:

```fish
wl themes                            # 列出 dark/light/mono + 各自配色预览 + 标出当前
wl --color always tree | less -R     # 强制出色(管道也保留 ANSI)
wl --color never ls                  # 关色(纯文本)
wl --theme light summary --week ...  # 手动指定浅色背景主题
```

- `--color {auto,always,never}`,默认 `auto`: TTY + rich 可用才上色;管道 / 重定向 / 无 rich 自动降级纯文本
- `--theme {auto,dark,light,mono}`,默认 **auto**: 探测终端底色自动选 dark(深色背景) / light(浅色背景);测不出回退 dark。dark/light/mono 也可手动指定
  - 底色探测: 先看 `$COLORFGBG`,再发 OSC 11 查询(需交互终端,短超时,不支持就回退)
- 搜索命中(含标题里的命中)高亮: styled 用背景色,纯文本用半角 `*…*` 标出
- env 兜底: `$WORKLOG_COLOR` / `$WORKLOG_THEME` / `$NO_COLOR`
- rich 是可选依赖,没装也能跑(纯文本)

## Schema

六个表;一切都是 `node`。

```
node (id, parent_id→node, title, kind, status, priority,
      created_at, scheduled_at, deadline_at, closed_at, body)
tag  (node_id→node, tag)                    # 多对多
log  (id, node_id→node, logged_at, body)    # 一个 node 多次 log entry
prop (node_id→node, key, value)             # UDA
link (node_id→node, vault_doc)              # vault wikilink 双链
v_node_path                                  # 递归 CTE view, 树状路径
```

`kind` 字段让一个表能装任意执行体系实体。级联删除会传播到 `tag/log/prop/link`;`parent_id` 用 `ON DELETE SET NULL`,删父不会孤儿杀子。

## 状态机

`TODO / DOING / LATER / WAIT / DONE / DEFERRED / CANCELED` —— 是 markdown `[ ]/[x]/[/]/[>]` 四态的超集,加了 `LATER` / `WAIT` 区分(推到将来 vs 等他人)。

## 贡献

开发环境、TDD / DRY 约定、Makefile 本地 override、发版流程都在 [CONTRIBUTING.md](CONTRIBUTING.md)。AI agent 操作规则见 [AGENTS.md](AGENTS.md);设计约定见 [DESIGN.md](DESIGN.md)。
