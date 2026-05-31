---
name: worklog-cli
description: 用 wl 命令读写 SQLite 执行体系库(`~/.worklog/wl.db`)—— 记录 / 查询 计划 + 项目 + 任务 + log + 时间分层,是 markdown worklog 的结构化替代。Use when 用户要: 加任务 / 项目 / 日条目、给任务追加 log、标完成 / 顺延、列树状 / 某天工作 / active projects、查时间段进展或写周报、批量灌一天数据、聚焦某节点上下游。Trigger 词: wl、worklog-cli、加 worklog、记 log、列树、列项目、看一天工作、写周报输入、批量导入任务、apply diff。
---

> English version: [SKILL.md](SKILL.md)

# worklog-cli (wl)

SQLite 执行体系工具,单 `node` 表承载 lifetime/year/quarter/month/week/day/area/project/task/meetlog/habit,`parent_id` 自引用建树。CLI 仿 todo.sh。命令全局名 `wl`(`~/bin/wl` → venv python + `~/projects/worklog-cli/wl.py`)。

**完整设计约定见仓内 `DESIGN.md`**(18 节,加命令 / 改格式前必读)。本 skill 只讲 AI 怎么用。

## 何时用 wl(场景 → 命令)

| 用户说 | 命令 |
|---|---|
| **今天的快捷三件**(daily 流) | `wl goal "今天交付 X"`(读=`wl goal`) / `wl recap "日终小结..."`(读=`wl recap`) / `wl tick <id> [--note "..."] [--done]` 打卡。**自动建当天 day 节点**(挂当前 ISO 周),不用手动 `wl add ... -k day`。`wl recap` 写时**自动盖编写时间戳 `summary_at`**,`wl day` 顶部显示「(写于 MM-DD HH:MM)」;小结写完后当天若再有非 CLOCK 变更,`wl day` 提示「⚠ 小结后又有 N 条变更, 建议重写 recap」 |
| 加一条任务 / 项目 / 打卡 | `wl add "..." -k task -p A -t work,P0 --parent N` |
| 加任务带计划时间(精确或模糊) | `wl add "..." --scheduled 2026-06-15` / `--scheduled 2026-06` / `下周` / `下月` / `someday` |
| 给任务记进展 | `wl log <id> "..."`(迁历史用 `--date 2026-05-06` 或 import logs 项写 `"2026-05-06 内容"` 让 log 落在当时那天,不是今天) |
| 标完成 / 顺延(可模糊时间)/ 开始计时 | `wl done <id>` / `wl defer <id> 下月`(也接受 `2026-Q3` / `someday` / 精确日)/ `wl start <id>` `wl stop <id>` |
| 前瞻计划: 排任务到某天 / 重复(决定计划内) | `wl sched <id> 2026-06-15`(也接受 `明天` / `后天`)/ `--clear`。`--recur` 支持周期初 / 末: `daily` / `weekly:Mon,Fri`(也接 1-7 / -1..-7)/ `monthly:1`(月初)·`monthly:-1`(月末)/ `quarterly:1-1`(季初)·`quarterly:-1`(季末)/ `yearly:01-01`(年初)·`yearly:-1`(年末),`-1` 一律=周期末。排到某天的任务即使没 log 也在 `wl day` 作「计划内·未做」出现 |
| 元信息(日终小结 / Top5 / 今日目标) | 存 day / week 节点 prop: `wl set <day_id> summary "..."` / `goal "..."` / `top5 "..."` / week 节点 `overview "..."`,`wl day` 顶部 blockquote 显示。日终小结优先用 `wl recap`(自动盖 `summary_at` 时间戳 + day 视图 stale 提示) |
| 日期上下文(节日 / 休假 / 调休) | `wl dateinfo 2026-05-01 劳动节假期` / `wl dateinfo --import holidays.json`(`{"YYYY-MM-DD":"标签"}`)/ `wl dateinfo <date> --clear`。周 X 自动算,`wl day` 头部显示「日期 周 X · 标签」 |
| 复现某天进展(像 markdown worklog 日结构) | `wl day [YYYY-MM-DD]`(基于 log 日期: 工作 / 个人分桶 → 次级分组 → 任务 → 缩进 log + 统计)。**默认 `--by plan`**(计划内 / 计划外 / 计划外(未标)—— 没打 `planned` / `unplanned` tag 的当计划外);换维度 `--by project` / `--by priority`(P0/P1/P2) |
| 列所有 active 项目 | `wl projects` |
| 看树状 | `wl tree`(**默认 = 概览视图: 时间线展开到今天 [年→季→月→周→今天 + 今天任务] + area 只列领域名一层,~30 行防刷屏**)/ `wl tree --root <area>`(看该领域项目 + 任务)/ `wl tree --root <week/month>`(看每天活动)/ `wl tree --depth N`(从 lifetime 全展开)/ `wl tree --by project/tag/direction`(换维度)。时间节点按日期排序;**day 节点展开 = 当天有 log 的任务 + 只含当天 log** |
| 查时间段进展(周报输入) | `wl changes --week 2026-W22` / `wl summary --week ... --by project/day` |
| 一个任务的全部信息 + 时间线 | `wl show <id>` |
| 聚焦某节点上下游 | `wl focus <id>` / `wl ancestors <id>` / `wl descendants <id>` |
| 关联 vault 文档 | `wl link <id> "文档名"`(无 .md) |
| 列 log 流水 | `wl logs`(**默认只列最近 7 天**,防全量刷屏);`--since/--until/--date` 指定时间段;`--group day [--by project/priority/plan]` 按天分组复现 |

## UX 升级 v2(深度迭代到收敛)

新顺手命令(`done/start/stop/link/wait/reopen` 都接受**多 id**):

```fish
wl add "新活" -k task -p A --parent 7 --sched 今天  # 加 + 直通 sched 一条搞定
wl done 18 19 20                                     # 批量完成
wl start 18 19; wl stop 18 19                        # 批量 clock
wl active                                            # 看现在哪些 task 在 CLOCK(含时长)
wl wait 18 --note "等 review"                         # WAIT 状态 + 自动关 CLOCK
wl reopen 18                                          # 撤销 DONE 回 TODO
wl log 44 "早饭" --time 11:09 --date 昨天              # 精确时刻 + 短日期
wl day 昨天 / wl day 前天                              # 日期短写
wl find skill --limit 5 / --all                       # 搜索默认 20 防爆屏
```

⚠️ `--sched <day>`(精确,直通 sched 表,`wl day <那天>` 看得到)vs `--scheduled <fuzzy>`(粗 hint,只写 `node.scheduled_at`)。**日常用 `--sched`**。两者同时给会报冲突。

新增命令 `wl active` / `wl wait` / `wl reopen`、`wl logs today/yesterday/week/recent` 预设、`wl show 多 id`、`wl find --limit`、`wl --version`。所有空字符串输入(title / body / vault_doc / prop key / find query)统一拒绝;非法字段名(`--in bogus` / `--kind bogus`)拒绝;非法时间(`--time 25:99`)拒绝。

## UX 升级 v3(复合参数 / log 编辑 / 时间补录 / 查询精准化)

### 一步完成: `add` / `done` / `cancel` 复合参数

```fish
# 建 + log + done + closed_at + link + sched 一步(事后回顾型)
wl add "做完一件事" -k task -p B --log "结果: PR#42 修 3 bug" \
  --done --at 14:30 --link "vault doc 名" --sched today

# 已有 task: 关 + log 一步
wl done <id> --log "结果说明" --at HH:MM
wl cancel <id> --log "放弃: 优先级降低"
# -m 是 --log 短写(跟 git commit 习惯一致)
```

### log 编辑: `unlog` / `relog`

`wl show` / `wl logs` 时间线把 log id 显示成 `#L<id>`:

```fish
wl unlog #L282                       # 删 log
wl unlog --node 39 --date 昨天        # 按 node + 日期删最近 1 条
wl relog #L282 "改正后内容"           # 改 body
wl relog #L282 --at 14:30             # 只改时间
wl relog #L282                        # 不传 body/--at 走 $EDITOR
# CLOCK_IN/OUT log 禁动(改时间用 wl stop --at)
```

### 时间补录: `start --at` / `stop --at` / `spent`

```fish
wl start <id> --at 09:00              # 倒填 CLOCK_IN
wl stop <id> --at 11:30               # 倒填 CLOCK_OUT(须晚于 IN)
wl spent <id> 90m                     # 给时长直接落 CLOCK pair
wl spent <id> 1h30m --at 14:00        # 14:00 当结束, 倒推 12:30 当起始
```

### 多 habit 交互式打卡: `wl checkin`

```fish
wl checkin                            # 默认多选框(↑↓ 空格 Enter)
wl checkin --linear                   # 备选 逐条 prompt y/n/note/q
wl checkin --all-kinds                # 不限 habit
```

### 循环规则扩展(`--recur`), 每种都支持 `-1` 表"周期最后一天"

```fish
wl sched <id> --recur weekly:Mon,Wed,Fri    # 或 weekly:1,3,5 / weekly:-1=Sun
wl sched <id> --recur monthly:5,15,-1       # 每月 5/15/末
wl sched <id> --recur quarterly:1-15        # 季首月 15 → 1/15, 4/15, 7/15, 10/15
wl sched <id> --recur quarterly:-1          # 季度末日(3/31, 6/30, 9/30, 12/31)
wl sched <id> --recur yearly:03-21          # 每年某日
wl sched <id> --recur yearly:-1             # 年末 12-31
```

### `wl ls` 多维度查询(参考 shell ls -t/-S/-r)

默认 limit 20 + truncation hint,`wl ls --help` 含 10 条示例:

```fish
wl ls --kind project                  只列项目
wl ls --parent 45                     看 #45 下子任务
wl ls --tag work,dev                多 tag AND
wl ls --unscheduled --kind task       待安排清单
wl ls --sort created -r --limit 5     最近建的 5 条(类 ls -tr -5)
wl ls --sort updated --limit 10       最近有 log 的 10 条(类 ls -t)
wl ls --recent 7                      最近 7 天有变动
wl ls --ids 39 41 270                 直接看几个固定 id(类 ls f1 f2)
wl ls --all                           解除 limit + 含 DONE/CANCELED
```

### log/timeline 默认收尾 N 条(防长任务撑屏)

| 命令 | 默认 tail | 关闭 | 全展开 | 自定 |
|---|---|---|---|---|
| `wl day` | 每 task 3 条 | `--no-logs` | `--all-logs` | `--log-tail N` |
| `wl logs --by-task` | 3 条 | `--no-body` | `--all-logs` | `--tail N` |
| `wl logs --id N` | 全部 | `-q` | (默认全) | `--tail N` 切末尾 |
| `wl show <id>` | 时间线 5 条 | `--no-timeline` | `--all-timelines` | `--timeline-tail N` |
| `wl tree`(day 活动) | 每 task 3 条 | `--no-logs` | `--all-logs` | `--log-tail N` |

### 查询精准化设计原则

核心: **命令应让"目标 → 命令 → 输出"三层都精准**, 不靠裸 ls 全量再眼睛搜。反模式: AI 用 `wl ls` 列 100 条找 1 条 / `wl logs --id N` 列 17 条找最近。正解: 用专用入口(`wl find` / `wl active` / `wl day` / `wl ls --recent/--ids/--sort` / 任意子命令 `--help` 看示例)或加专用入口。

## ⭐ 批量操作(AI 灌数据主路径,别一条条 add)

灌一天 worklog / 多节点时,**用批量入口,不要跑几十条 `wl add`**:

### `wl import <file|->`(JSON,复杂批量 / 深嵌套)

```fish
echo '{
  "add": [
    {"ref":"p","title":"data-viz","kind":"project","priority":"A","tags":["work","viz"],
     "children":[{"title":"登录修复","kind":"task","priority":"A","status":"DONE","tags":["P0"],"logs":["根因..."]}]},
    {"title":"消化系统","kind":"task","parent_ref":"p","tags":["viz"]}
  ],
  "update": [{"id":14,"status":"DONE","parent":6,"add_tags":["urgent"],"remove_tags":["old"]}]
}' | wl import -
```

- `children` 嵌套(父 id 自动传)+ `ref`/`parent_ref`(同批次引用)
- `--dry-run` 先预览

### `wl apply <file|->`(wl-diff, 跟 wl 输出同格式, 人 / AI 轻量编辑)

```
  #6 [day] 2026-05-29 周五       ← 锚: 定位已存在节点作父, 不改它
+   [x] [#A] 晨检 :planned:P0:    ← 新增(缩进=子), [x]=DONE
+     @log 巡检要点
~ [x] #14                        ← 改 #14 状态(单行简写)
~ #20                            ← 复杂更新: 锁定 + 字段操作
  priority A
  +tag urgent
  -tag old
```

前缀: `+` 新增 / `~` 改 / `-` 删 / ` ` 锚。`--dry-run` 校验 + 预览。

### ⚠️ 更新(~)安全铁律

**只改出现 / 声明的字段,没出现的绝不动。** 这是防"只给 id+名字把别的抹掉"。

- 单行简写: `~ [x] #14` 只改 status;`~ [#A] #14` 只改优先级(没写 marker 不碰 status);`~ #14 新名` 只改 title
- 字段操作: `status DONE` / `priority -`(清空)/ `parent 6`(move)/ `+tag` / `-tag` / `prop k=v` / `-prop k` / `+log`
- 非法值(priority∉ABC、status 非法、parent 不存在)会被校验拦截,**不会改坏数据**

## ⭐ Brief / token 节省模式(AI 调用必看)

所有命令支持顶层 **`-q` / `--brief`**: 跳过 log body、时间线、详情段, 保留结构化关键信息。AI 抓输出时默认带上 `-q`, 详细看具体那条用 `wl show <id>` 单独点。

| 命令 | `-q` 行为 | 行数对比(实测) | 专用参数 |
|---|---|---|---|
| `wl day` | task 行 + `(N log)` hint, 不展开 body | 34→18 (-47%) | `--no-logs` / `--log-tail N` |
| `wl summary --week` | 每 project 一行 `完成 N / 未完 M`, 不展开 task | 329→36 (-89%) | `--projects-only` / `--top N` / `--no-dedup` |
| `wl logs --since` | 只列 `[date] #id title`, 不带 body | 字符大降 | `--no-body` / `--by-task --tail N` |
| `wl show <id>` | 跳过时间线, 只列元信息 | 19→6 (-68%) | `--no-timeline` / `--timeline-tail N` |
| `wl projects` | 不显示"最近 YYYY-MM-DD"列 | 字符省 18/行 | `--since DATE` 只列那天后活跃 |

**summary 默认去重**: 同一 task 跨多个 project(parent + 共享 tag)现在只在主 project 下列一次, 旧行为(每 project 都列)用 `--no-dedup` 保留。

**时间窗口全局**: `--since` / `--until` / `--week` / `--month` 已提到 parent parser, `changes` / `summary` / `logs` 都直接用, 参数命名 + 行为完全一致。

典型 AI 调用模式:

```fish
wl -q day                          # 看今天有哪些 task 待办/进行/完成
wl -q summary --week 2026-W22      # 看本周每项目完成数(周报骨架)
wl summary --week 2026-W22 --top 5 --projects-only  # Top 5 主线一眼
wl -q logs --since 2026-05-25 --by-task --tail 1     # 每个 task 最近 1 条进展
wl -q show 356                     # 看 #356 元信息不要时间线
wl projects --since 2026-05-25     # 本周真正活跃的项目
```

非 AI(人用 TTY)保持原 verbose 输出, 无需特意切换。

## 跟 vault 的关系(知识 ⇄ 执行 解耦)

- vault markdown(`YYYY-MM 工作记录`)= 人写人看的归档;wl DB = 机器写机器查的执行体系真相
- `wl link <id> "Dev tooling"` 把任务关联 vault 文档(存文档名, 无 .md), 跟 worklog 里 `[[...]]` 同名
- 处理新任务: 优先进 wl(结构化), 涉及 vault 文档的挂上 link

## 典型 workflow: AI 处理一天 worklog

1. 解析当天讨论 / 记录 → 拼一个 `wl apply` diff 或 `wl import` JSON
2. 先 `--dry-run` 预览, 确认无误
3. apply/import 一次灌库(替代几十条命令)
4. `wl day` 复看当天全貌 + 统计
5. 写周报时 `wl changes` / `wl summary --week` 出素材

## 不该做的

- **不擅自批量删 / 改** node —— 删改前 `wl show <id>` 给用户确认
- **不绕开 wl 直接 `sqlite3` 改库** —— `~/.worklog/wl.db` 是 source of truth, schema 约定在 DESIGN.md
- **不跑 `wl reset`**(drop DB)除非用户明确要
- 批量写库前**一律先 `--dry-run`**, 尤其 update/delete
- 改 wl.py 加命令时: 实现 + 测试 + completion + DESIGN.md(涉约定)+ 本 SKILL.md(涉用法)一起改, `make ship`(test 通过才推)

## 高亮 / 配色

终端里 `wl` 默认带颜色(rich): 状态绿 / 黄、优先级 A 红、搜索命中(含标题命中)背景色高亮。全局开关(任意子命令前): `wl --color {auto,always,never}`、`wl --theme {auto,dark,light,mono}`, 也读 `$WL_COLOR` / `$WL_THEME` / `$NO_COLOR`。主题默认 **auto**: 探测终端底色自动选 dark/light(测不出回退 dark);`wl themes` 列出并预览。`--color auto`(默认)只在 TTY 开色 —— **AI 抓 stdout 时自动是纯文本**, 无需特意关;想给人看带色输出(如 `| less -R`)用 `--color always`。细节见 DESIGN §19。

## 安装(新机器)

```fish
git clone <your-git-host>:<user>/worklog-cli.git ~/projects/worklog-cli
cd ~/projects/worklog-cli && make setup    # venv + ~/bin/wl
ln -sf ~/projects/worklog-cli/skills/worklog-cli/SKILL.md ~/.claude/skills/worklog-cli/SKILL.md
wl init
# 见下 "Shell 补全" 加 init load 行到对应 shell rc
```

## Shell 补全

```fish
# fish: ~/.config/fish/config.fish 加
wl print-completion fish | source
```

```bash
# bash: ~/.bashrc 加
eval "$(wl print-completion bash)"
```

```zsh
# zsh: ~/.zshrc 加
eval "$(wl print-completion zsh)"
```

加载模式跟 starship/direnv/zoxide 一致, 开新 shell 自动跟 wl.py 改动。详 DESIGN.md §34。

**用户别名**:

```ini
# ~/.config/wl/aliases.ini(可选)
[aliases]
d = day
c = checkin
ll = ls
```

跨 shell 一致 —— `wl d` 在 fish/bash/zsh 都识别为 `wl day`, 改 ini 开新 shell 即应用。
