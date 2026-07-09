<sub><a href="README.md">🌐 English</a> · <b>中文</b></sub>

# worklog

[![PyPI version](https://img.shields.io/pypi/v/pyworklog.svg)](https://pypi.org/project/pyworklog/)
[![Python versions](https://img.shields.io/pypi/pyversions/pyworklog.svg)](https://pypi.org/project/pyworklog/)
[![Test](https://github.com/xyb/worklog/actions/workflows/test.yml/badge.svg)](https://github.com/xyb/worklog/actions/workflows/test.yml)
[![codecov](https://codecov.io/gh/xyb/worklog/branch/main/graph/badge.svg)](https://codecov.io/gh/xyb/worklog)
[![License: MIT](https://img.shields.io/pypi/l/pyworklog.svg)](https://github.com/xyb/worklog/blob/main/LICENSE)

**worklog(`wl`)是一个 AI-first、local-first 的执行体系 CLI** —— 用来替代 Markdown 工作记录。完整执行体系层级建模在单个 SQLite `node` 表里 —— lifetime / decade / year / quarter / month / week / day / project / task / habit / signal / meetlog —— 共享同一个 id 空间,通过 `parent_id` 自引用形成树,命令风格沿用 `todo.sh`。

<p align="center">
  <img src="https://github.com/user-attachments/assets/c472b531-ec09-4488-ad73-eb03ac413383" alt="wl 演示 —— 终端里的一天:规划、记录、复盘" width="720">
</p>

## 为什么用 worklog?

起源:AI 帮我记的 Markdown 工作记录涨了大约 50 倍后撑不住——并发写互相覆盖、wikilink 重命名就断、总结要重读大文件。于是把结构化的部分挪进一个为 AI 驱动而设计的数据库。

**AI-first** —— AI 才是真正的使用者,人只在终端看一眼确认:

- 命令单行、无交互问答 —— shell 里调用最不易出错。
- `-q` brief 模式 + 按宽度截行 → 输出省 token。
- 输出是 AI 能直接读的纯文本;自带 [Claude Code skill](skills/worklog-cli/SKILL.md)。

**local-first** —— 一个 SQLite 文件、schema 透明、无常驻进程 / GUI / 锁定:

- 人和 AI 读写同一个文件 —— 同一份事实。
- 并发写安全 → 多个 AI session 并行记录不互相覆盖(Markdown 做不到)。
- 通过 `wl link` 跟 vault 配合 —— 结构化执行进 `wl`,长文笔记留在 Obsidian。

**设计约定见 [DESIGN.md](DESIGN.md)** —— 加命令前必读,保持各处一致。
**AI 协作见 [skills/worklog-cli/SKILL.md](skills/worklog-cli/SKILL.md)** —— Claude Code skill(何时 / 如何用 `wl` + 批量 import / apply)。
背景: 在调研了 12 个候选产品(Logseq / Tana / TaskWarrior / org-mode / Anytype / Capacities / Linear 等)后没找到能同时覆盖三个维度(时间层级 / 项目层级 / vault wikilink)又无折中的现成方案,所以自建。

## 特性

- **一张 `node` 表装下一切** —— 时间线(year → day)+ 项目线(area → task)+ 习惯 / 会议记录,连成树。
- **Log** —— 任意 node 上带时间戳的进展,保留历史。
- **Metric** —— 结构化数据点(次数、血糖、打卡),能做趋势。
- **习惯 & 循环** —— 打卡 + `--recur`(daily / weekly / monthly / …)。
- **排期** —— 把任务排到某天;模糊时间词(`tomorrow` / `next-week` / `+3w`)。
- **状态机** —— TODO / DOING / LATER / WAIT / DONE / DEFERRED / CANCELED。
- **日 / 周 / 月视图** —— `wl day` / `tree` / `summary` 重建画面 + 统计。
- **全文搜索** —— `wl find`,命中高亮。
- **语义 + 混合检索** —— `wl query`:按语义(embedding)排序,再和关键词匹配(RRF)融合,改写过的说法和精确的名字都能浮出来;走任意 OpenAI 兼容的 embedding 服务。向量存在 LanceDB(可选 `semantic` extra),没有 LanceDB wheel 的平台自动降级到纯 Python 的 SQLite 后端 —— 见[语义检索后端](#语义检索后端)。
- **任务关系** —— `wl relation` 给任务建关系(`block` 依赖 / `split` / `related`),独立于父子树;`block` 加边时查环,拒绝成环。
- **机器可读输出** —— `show` / `ls` / `logs` / `day` / `tree` / `summary` / `projects` 上加 `-o json`,给脚本和 AI 用。
- **Agent session 绑定** —— `wl agent` 把一个 AI session 绑到任务上(status line / hook 可以显示出来)。
- **Vault 关联** —— `wl link` 到 Obsidian 文档(`[[wikilink]]`)。
- **批量 import / apply** —— 一个 JSON 或 wl-diff 导入一整天。
- **AI 友好输出** —— `-q` brief、捕获时纯文本、TTY 上配色、shell 补全。

## 安装

需要 Python ≥ 3.9(在 3.9–3.14 上测过)。先装 [uv](https://docs.astral.sh/uv/)(`brew install uv` 或 `pipx install uv`)。

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

## 快速开始

头 30 秒 —— 加任务、记进展、完成、复现当天:

```fish
wl init                                   # 建库(一次)
wl add "写 README" -p A                   # → 打印新 id,如 #1
wl log 1 "起草了 Features 段"             # 追加进展
wl done 1                                 # 完成
wl day                                    # 今天的工作, 重新分组 + 统计
```

下面是同一个 demo 的真实输出(与顶部 gif 同一份数据):

```
#9 2026-07-10 Fri · workday
  > 🎯 Send out the monthly report [1/1] ✅
     1. [x] #3 Write the report summary
  > 📝 Recap: Summary sent; started the AI tutorial; ran 3km (written at 07-10 00:37)
  work
    ▸ planned
      [x] #3 [# ] Write the report summary
        · Draft written, sent to the team
  personal
    ▸ planned
      [ ] #4 [# ] Do the AI-agents tutorial

  ── 2026-07-10: 1/1 tasks with progress · DONE 1 · planned·not-done 1
```

`wl tree --by project` 按项目而不是按时间对同一份数据分组:

```
▸ #1 [#A] Ship the monthly report  (1)
[x] [# ] #3 Write the report summary
▸ #2 [#A] Learn how AI agents work  (2)
[ ] [# ] #4 Do the AI-agents tutorial
[ ] [# ] #10 Build a tiny agent myself
```

## 命令

更全的命令面 —— 每条命令也有 `wl <cmd> --help`,`wl help` 浏览主题文档:

```fish
wl add "调研 X" -p A -t work,P0 --proj dev_tooling --parent 42
wl add "Dev tooling" --para project -p A --parent 4   # 项目挂在月份下
wl log 42 "今天看了 A 资料, 发现..."
wl done 42
wl defer 42 2026-06-01
wl start 42 ; wl stop 42                            # CLOCK in/out
wl link 42 "Dev tooling"                           # vault wikilink
wl set 42 owner xyb                               # 自定义 prop
wl show 42                                          # 详情 + log + tags + links
wl ls                                               # 默认列出未完成项
wl ls --para project --tag work,P0
wl tree                                             # 全树
wl tree --prop type.date=year --depth 3
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

## 语义检索后端

`wl query` / `wl reindex` 通过任意 OpenAI 兼容服务做 embedding(标准库 HTTP,无依赖),向量存在一个旁路索引里。两个后端可互换、自动选择 —— 分词和存储都能优雅降级,所以语义检索在**每个**支持的 Python 上、**零**必装 extra 就能用:

| 组件 | 最佳(装了 `semantic` extra) | 降级(无 extra / 无 wheel) |
| --- | --- | --- |
| 向量存储 | **LanceDB** —— 内存映射,不管多大都 ~1ms 打开 | **SQLite** —— 纯 Python cosine、线性扫描;worklog 规模够用,很大时偏慢 |
| 分词 | **jieba** —— 多粒度,中文召回好 | **`\w+`** —— 一段 CJK 当一个 token(中文召回更粗) |

```fish
pip install 'pyworklog[semantic]'   # 快路径:LanceDB + jieba
```

**最佳体验:** 在任意 **Python 3.9–3.14**、**Linux** 或 **Apple-Silicon macOS** 上装 `semantic` extra —— LanceDB 在这些组合上发的是前向兼容(abi3)wheel,覆盖全部版本;jieba 是纯 Python,哪儿都能装。

**什么时候走降级:** LanceDB 不发源码包,所以没有预编译 wheel 的平台 —— **Intel macOS**、**musl/Alpine**、**\*BSD**、32-bit —— `wl query`/`reindex` 自动改用 SQLite 后端(结果一样,只是慢);`wl reindex` 这时会打一行提示。其它什么都不变,核心 CLI 也从不需要任一 extra。

## Schema

六个表;一切都是 `node`。

```
node (id, parent_id→node, title, status, priority,
      created_at, scheduled_at, deadline_at, closed_at, body)
tag  (node_id→node, tag)                    # 多对多
log  (id, node_id→node, logged_at, body)    # 一个 node 多次 log entry
prop (node_id→node, key, value)             # UDA
link (node_id→node, vault_doc)              # vault wikilink 双链
v_node_path                                  # 递归 CTE view, 树状路径
```

一个 `node` 表装下所有执行体系实体;分类由正交的 `type.*` prop 命名空间承担(没有专门的列)。级联删除会传播到 `tag/log/prop/link`;`parent_id` 用 `ON DELETE SET NULL`,删父不会孤儿杀子。

## 状态机

`TODO / DOING / LATER / WAIT / DONE / DEFERRED / CANCELED` —— 是 markdown `[ ]/[x]/[/]/[>]` 四态的超集,加了 `LATER` / `WAIT` 区分(推到将来 vs 等他人)。

## 贡献

开发环境、TDD / DRY 约定、Makefile 本地 override、发版流程都在 [CONTRIBUTING.md](CONTRIBUTING.md)。AI agent 操作规则见 [AGENTS.md](AGENTS.md);设计约定见 [DESIGN.md](DESIGN.md)。
