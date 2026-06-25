<sub><a href="DESIGN.md">🌐 English</a> · <b>中文</b></sub>

# worklog 设计约定（canonical）

> 所有命令必须遵守本文档的统一约定。加新命令 / 改老命令前先读这里，保持一致。
> 改了约定 → 同步更新本文档 + 所有受影响命令 + 测试。

## 0. 设计目标（北极星，凌驾于一切具体约定之上）

下面几条是地基。加任何功能、改任何设计前先过这几关；跟下文具体约定冲突时，以这几条为准。

### G1 结构化优先（不靠字符串匹配）

worklog 的整个职责就是把工作记录**结构化**，让信息能被精确查询 / 聚合 / 分析。任何"以后要能搜 / 能统计"的语义，都必须落在**结构化字段（独立的列 + 类型）**里，绝不靠对正文做字符串匹配 / 前缀约定。那不叫结构化，查不准也统计不了。

- 加新功能先问："它的结构化载体是什么？"（哪张表、哪个列、什么类型）——而不是"塞进哪段文本里"。
- 反模式（要逐步淘汰）：靠 `body LIKE 'CLOCK_%'` 检测 CLOCK 事件；靠"那天有没有 log"推断习惯完成。这两个都是字符串约定——脆弱且无法聚合。

### G2 极简自明（不读文档也能用，人和 AI 都一样）

工具必须简单到**不看手册就能用**，对人和 AI 都成立。AI 面对的是纯文本接口，复杂度会让它出错。

- **挂在一个节点下的记录种类**要克制（越少越好）：每多一种就多一个要记的概念、多一块要渲染的内容——更重、更难把握。
- 任何新设计自检三问：(1) 它新增了几个概念？(2) 用它要不要逼人 / AI 做选择（"用 A 还是 B？"）？(3) 不看文档能不能猜对用法？任一条答得不好，就再简化。
- G1 和 G2 冲突时，找"既结构化又最少概念"的答案——别为了结构化就堆新表 / 新字段 / 新命令。

### G3 依赖少、逻辑简单、易维护

worklog 刻意保持**少依赖、低抽象**：运行时只有 **stdlib + `rich`**，新功能优先**零新依赖**。逻辑保持**显式直白**——宁可裸 SQL 也不上 query-builder DSL，宁可写小而专的 helper 也不引入藏掉细节的框架 / ORM。底线:维护者(人或 AI)能把任一条代码路径从头读到尾,不必先学一层隐藏机制。

- **借技法,不借库**:宁可写几十行零依赖 helper,也别拉一个包(例如照 sqlite-utils 思路写 dict→`INSERT` helper,而**不是** ORM;写 `field__op`→`WHERE` helper,而**不是** query builder)。
- **不上 ORM / 不上 query builder**:只把均匀的 ~80%(单表 CRUD + 存在/计数)包成**透明映射到一条 SQL**的薄 helper;复杂的 ~20%(JOIN / CTE / CASE / 时间窗)留显式 SQL。别把 SQL 重新发明成一套 kwargs DSL。
- **每张表 / 每个模块能各自维护**:尽量减少强制耦合(硬 FK 级联、跨切面魔法、藏意图的 trigger),让一处能改动 / 迁移 / 同步而不牵连全局。在会造成跨表一致性负担的地方,倾向**避免不可逆操作**(用软删除 / 状态标记替代 `DELETE`)。
- "聪明"的抽象跟 G3 冲突时,选无聊但简单的版本。零件少胜过优雅。

## 1. 命令风格（todo.sh 派）

- 动词在前：`wl <verb> [args] [--flags]`
- 子命令名用全小写单词：`add` / `done` / `tree` / `projects` / `changes` / `summary` / `focus` …
- 操作单个节点的命令第一个位置参数是 `id`（int）：`wl show 42` / `wl done 42` / `wl log 42 "..."`
- 长 flag 用 `--xxx`，短 flag 仅给最高频的（`-p` priority / `-t` tag）
- 节点不存在一律 `sys.exit(f"✗ ... #{id} not found")`，退出码非 0

## 2. 数据模型（`src/worklog/migrations/`）

单 `node` 表承载一切，分类由正交的 `type.*` prop 命名空间承担（没有专门的列），`parent_id` 自引用建树。schema 以编号 SQL migrations 的形式发布,放在 `src/worklog/migrations/NNNN_*.sql`,`PRAGMA user_version` 记录最高已应用版本。`ensure_db()` 每条命令都自动 apply pending migrations,显式形式是 `wl migrate`。初始版本见 `src/worklog/migrations/0001_initial_schema.sql`,整体概览见 `README.md`。

> **migration 编写规则**：runner 把每个文件包进一个 `BEGIN/COMMIT`(中途失败整文件回滚),所以 migration 文件本身**不要**写 `BEGIN`/`COMMIT`。

- **分类取值**（正交的 `type.*` props，没有单列）：`type.para` = PARA 角色 `area / project / task`；`type.date` = 时间层级 `lifetime / decade / year / quarter / month / week / day`；`type.habit`；`type.meetlog`；自定义 `type.<x>`（可扩展，但加新 type 要想清楚它在 tree / projects / summary 里怎么归类）。一个 node 可同时持有多个；需要单个代表 token 时由 `node_type` / `node_type_from_props` 派生（优先级 para > date > habit > meetlog > custom > task）
- **status 只在 task / habit / meetlog 类用**；时间层级类（year/month/...）跟 project 类 status 留 NULL
- 表：`node / tag / log / metric / clock / prop / link / sched / date_meta` + 派生 view `v_node_path`
- **`node → log → metric` 主干**（log 为中心的核心）：一个 `node` 挂多条 `log`；一条 `log`(带 `tag`——`note`/`goal`/`summary`/`metric`(载体)/…，NULL = 普通笔记)下挂 0..N 条 `metric`。`metric` 是结构化数据点(`tag` = 它是什么，如 `glucose`/`pullups`/`checkin`，或 `goal` = 一条 goal log 按优先级排序的目标 node id；外加 `value_num`/`value_text`/`unit`/`note`/`at`)。**`tag` 是三处统一的分类字段**——node(多值标签集)、log(角色,单值)、metric(种类,单值);同一个词、不同范围、SQL 不混。metric **必须挂在一条 log 上**(`metric.log_id` NOT NULL)——所以每个数据点都有 log 载体；`CHECK` 要求有值(纯标记如打卡存 `value_num=1`)。`metric.node_id` 是反范式冗余(免 join 查某 node 的数据点，无 FK；trigger 保证它始终等于载体 log 的 node)。CRUD 入口：`wl metric add/ls/edit/rm`(`add` 不带 `--on-log` 时建载体 log；无值标记存 `value_num=1`);`wl log`/`wl add` 的 `--metric` 和 `wl import` 的 `metrics` 可内联挂数据点。habit「今天做没做」= 当天有没有 `tag=checkin` 的 metric(`wl tick`/`wl checkin` 写),不再是「那天有没有 log」。
- **保留 tag 的 log = 历史保留**：`goal`(前瞻,任意时间层级)和 `summary`(后顾日终)都是 `log.tag` 化的 log(最新一条=当前值,每次改追加一条),有自己的 `wl goal set/ls/rm <node>` 组(`--summary` 改写 summary),也由 bare `wl goal`/`wl recap`(今日自动)和 key-route 的 `wl set <node> goal|summary` 写。goal **每个层级同一个 `goal` tag**——node 的 type(day/week/month/year)就是层级;旧的 week `overview`、month `top5` 已并入 goal(迁移 0010)。一条 goal 可带结构化目标(写时显式给的 node id,顺序=优先级,存成 `goal` metric);`wl day`/`wl goal` 展示 goal + 编号带状态的目标列表 + `[done/total]`。(`prop` 回归只放真正静态的单值属性。)
- **`clock` = 结构化计时**:`clock(node_id, start_at, end_at, elapsed_sec)`,由 `wl start`/`stop`/`spent`/`wait` 读写——替代旧的 `CLOCK_IN`/`CLOCK_OUT` log-body 约定;时长从 `elapsed_sec` 求和,不再从文本解析。
- **两条并列树（都挂 lifetime 下）**（2026-05-29 起）：
  - **责任领域线**：`lifetime → area → project → task`（PARA：area 是跨时间的责任领域，project 归 area，task 归 project）
  - **时间线**：`lifetime → year → quarter → month → week → day`（承载时间骨架 + 保留 tag 的 log：每个层级的 `goal`、day 的 `summary` 日终）
    - 小结(`summary` log)自带 `logged_at` = 写入时间。`wl day` 显示「(写于 …)」,若当天小结后还有普通笔记 log(`tag IS NULL`)就提示「⚠ 小结后又有 N 条变更, 建议重写 recap」。(替代旧的 `summary_at` prop。)
  - **project 不再挂 month**（旧设计曾挂月，已迁到 area）。日/月/周视图靠 **log 的 logged_at**（时间维度）跟 **type/tag/祖先链**（领域维度）解耦：`wl day` 按 log 日期驱动、project 经祖先链解析、bucket 经 work/personal tag——所以 project 移到 area 下不影响任何按天/按项目视图

## 3. 状态机（#+TODO 风格）

```
TODO / DOING / LATER / WAIT  (未完成)
DONE / DEFERRED / CANCELED   (已了结)
```

- `wl done` → DONE + 自动写 `closed_at`
- `wl defer <id> <date>` → LATER + `scheduled_at`
- `wl start/stop` → DOING + CLOCK 事件（见 §7）
- **状态分组展示顺序固定**：`进行中(DOING) → 待办(TODO) → 顺延(LATER) → 等待他人(WAIT)`。进行中永远放最前（最该关注）。任何列未完成的地方（summary 等）都按这个顺序。

## 4. marker 符号约定（`_status_marker`）

| status | marker |
|---|---|
| None / TODO | `[ ]` |
| DOING | `[/]` |
| LATER / DEFERRED | `[>]` |
| WAIT | `[?]` |
| DONE | `[x]`（summary 完成清单里用 `✓`）|
| CANCELED | `[-]` |

唯一来源 `_status_marker(status)`，禁止各处硬编码。

## 5. 优先级

- 三档 `A / B / C`（对应 worklog 的 P0 / P1 / P2）
- 渲染统一 `[#A]` / `[#B]` / `[#C]`，无优先级显示 `[ ]`（节点行）或留空
- 排序 key 统一 `(priority or "Z", id)` —— 无优先级排最后

## 6. 节点行渲染（统一格式）

任何"列节点"的地方用统一行格式（`_fmt_node` / summary 的 `_line`）：

```
<marker> [#<pri>] #<id> <title>[ ·计划内][ ⏱<N>min]
```

- `·计划内` 标注：节点有 `planned` tag 时加
- `⏱<N>min`：该节点 CLOCK 工时累计（`_node_clock_min`）> 0 时加
- 完成清单用 `✓` 替代 marker
- **唯一渲染器是 `_node_line`**，全程经 `_c` 上色（见 §19）；任何列节点的地方复用它即免费获得高亮，禁止各处自己拼字符串

## 7. 时间线 / changes / CLOCK（`wl show`）

一个节点的"发生了什么" = created / scheduled / closed / 各 log 按时间合并：

| 事件 | 标记 |
|---|---|
| created_at | `● created` |
| scheduled_at | `◷ scheduled` |
| closed_at | `✓ <status>` |
| CLOCK_IN log | `⏱ clock-in` |
| CLOCK_OUT log | `⏱ clock-out (Nmin)` |
| 普通 log | `✎ log <body>` |

- `wl start` 写一条 body=`CLOCK_IN` 的 log；`wl stop` 写 `CLOCK_OUT elapsed=Nmin (from ...)` 的 log
- CLOCK_ 开头的 log 不算"进展 log"（changes / summary 里过滤掉）
- 工时统计 = 所有 CLOCK_OUT 的 `elapsed=Nmin` 累加（`_node_clock_min`）

## 8. 时间窗参数（统一，`_resolve_window`）

凡是"某时间段"的命令（changes / summary，未来的 logs 等）统一这套，**禁止各命令自己解析**：

| flag | 含义 |
|---|---|
| `--since YYYY-MM-DD` | 起（默认本周一）|
| `--until YYYY-MM-DD` | 止（默认今天）|
| `--week YYYY-Www` | ISO 周（覆盖 since/until）|
| `--month YYYY-MM` | 整月（覆盖 since/until）|

优先级：`week > month > since/until > 本周一~今天`。唯一来源 `_resolve_window(args)` → `(since, until)` 两个 `YYYY-MM-DD`。

窗口判定统一：`since <= ts[:10] <= until`（日期字符串字典序 = 时间序）。

## 9. `--by` 聚合维度（统一设计语言）

`tree` 跟 `summary` 都支持 `--by`，未来新增聚合命令也沿用：

- `tree --by project/tag/direction`：扁平 2 层重组，避开时间维度多层深
- `summary --by project/day`：默认 project
- **语义**：换一个维度把节点重新分组，每组一个 `▸` 头 + 组内节点列表
- **未归桶**：按 project 聚合时，没归到任何项目的节点进末尾 `▸ (未归项目)` 桶

### 9.1 `wl tree` 默认行为（2026-05-29 调整，防刷屏）

两条并列树全展开会上千行。**纯 `wl tree`（无 --root/--para/--depth/--by）= 专用概览视图 `_print_default_tree`**：

- **时间线展开到今天**：年→季→月→周→今天（只走到今天的那条路径，不列别的月/周/日），今天的 day 节点下**只列当天有 log 的任务**（task/habit/meetlog，不展开 log）。这样既看到当月、又只聚焦今天。
- **area 只列一层**：7 个 area 只出领域名，不展开项目。
- 典型 ~30 行（取决于今天任务数）。
- **钻取**：`wl tree --root <area>`（看该 area 的项目+任务，默认深 3）/ `wl tree --root <week/month>`（看每天活动）/ `wl tree --root <day> --depth 大`（看当天每条 log）/ `wl tree --depth N`（从 lifetime 全展开，泛用）。

底层 `_print_tree`（--root/--depth 路径）规则：
- **限深**：有 `--root` 默认 3，无 root（`--depth` 显式）按给的值；`_print_tree(max_depth)` 截断。
- **时间节点按日期排序**（`_tree_children`）：type ∈ 时间层级按 `title`(日期)升序，其他按 优先级→id（否则按 id 会 W22 排 W18 前）。
- **day 节点展开 = 当天活动**（`_print_day_activity`）：area 化后 day 无真子节点，改展开"当天有 log 的任务 + 只含当天的 log"（log 驱动，跟 `wl day` 同源）。**task 不要挂 day（挂 project）**，day 内容靠 log 日期推。

## 10. 项目↔任务关联（`_project_members`）

一个 task 算"属于"某 project，满足任一：

1. **结构子任务**：`task.parent_id == project.id`
2. **共享语义 tag**：task 跟 project 共享至少一个非通用 tag

⚠️ 已知特点：共享 tag 关联有歧义（一个 `gaming` tag 对应多个 gaming 项目 → task 出现在多个项目下）。**未来精确化方向**：加 `prop["project"]` 显式指定唯一归属，`_project_members` 优先用 prop，回退到 tag。改这个 helper 影响 tree --by project / projects / changes / summary 四处，要一起验。

## 11. 通用维度 tag（`GENERIC_TAGS`）

```
work personal planned unplanned P0 P1 P2 habit meeting followup
dev ai sync strategy reflection reading family health morning_check slack_scan
```

- 这些是"维度/属性"标签，不是"项目/主题"标签
- `--by tag` 分组、`focus --related`、`_project_members` 共享 tag 判定都排除它们，避免泛滥
- 加新的通用维度 tag → 加进 `GENERIC_TAGS` 集合

## 12. 共享 helper（禁止重复实现）

| helper | 用途 |
|---|---|
| `_status_marker(status)` | status → marker 符号 |
| `_fmt_node(n, indent)` | 节点行渲染（tree --by 等）|
| `_resolve_window(args)` | 时间窗 → (since, until) |
| `_project_members(con, pid)` | 项目关联节点 id 集合 |
| `_node_clock_min(con, nid)` | CLOCK 工时累计分钟 |
| `_has_tag(con, nid, tag)` | 节点是否有某 tag |
| `_ancestors_chain(con, nid)` | 根→节点祖先链 |
| `_collect_descendants(con, rid)` | 所有后代 id |

新命令需要这些功能时复用，不要再写一份。

## 13. vault 关联（link 表）

- 执行体系（wl DB）跟知识体系（Obsidian vault）**解耦**：DB 知道节点关联哪些 vault 文档（`link` 表存文档名，无 `.md`），但不反向同步 vault 内容
- `wl link <id> <vault_doc>` 加关联
- 未来 T-7 `wl xref <doc>` 反查"哪些节点关联了某文档"

## 14. CLI 输出风格

- 中文为主，简洁；不堆砌
- 成功操作 `✓` 前缀，错误 `✗` 前缀
- 分组头 `▸`，子项缩进 2/4 空格
- emoji 克制：`📊` summary 头 / `📅` changes 头 / `⏱` 工时 / `●◷✓✎` 时间线事件 / `▸·` 分组
- 高亮 / 配色 / `--color` / `--theme` 见 §19；输出走 `out()` 不直接 `print()`

## 15. 测试约定

- 每个命令一个 `Test<Cmd>` 类，放对应的 `tests/test_<area>.py`
- `conftest.py` 的 `cli` fixture：每测试独立临时 DB（`WORKLOG_DB` env + tmp_path），`run_cli` 抓 stdout/stderr/exit_code
- 新命令必须覆盖：正常路径 + 边界（不存在 id / 空库 / 过滤无命中）
- 改约定导致旧测试断言失效 → 同步更新断言，别留红

## 16. 工程约定

- **运行时依赖只有 `rich`（可选增强）**：核心逻辑只用 Python stdlib（sqlite3 + argparse）；`rich` 仅供高亮（见 §19），不装也能跑——`src/worklog/cli.py` 顶部 `try import rich` 失败就 `_RICH_AVAIL=False`，全程降级纯文本。pytest 仅测试期。
- 单包 `src/worklog/`;`cli.py` 主实现,`migrations/NNNN_*.sql` 是 schema,`__init__.py` 出 `__version__`。超了再考虑拆 module。
- Shell completion **自动生成**: `wl print-completion {fish,bash,zsh}` 走 argparse 树自动产出对应 shell 补全脚本,**不维护 `completions/wl.fish` 文件**。加子命令 / flag 后补全自动跟。动态补全(node id / tag)需要 register 进 `_FISH_POSITIONAL_NODE` / `_FISH_HELPERS` / `_BASH_DYN_HELPERS`。
- 每次加命令：实现 + 测试 + completion + 本文档（若涉及约定）四处一起改
- push 走 `make ship`（test 通过才推）

## 17. 批量 import（AI 协作主入口）

`wl import [file|-] [--dry-run]` — AI 一次灌多节点，替代几十条命令。JSON 单文档：

```json
{
  "add": [
    { "ref": "p1", "title": "...", "priority": "A",
      "tags": [...], "props": {"type.para": "project"}, "links": [...], "logs": [...],
      "children": [ {...嵌套子节点...} ] },
    { "title": "...", "parent_ref": "p1" }
  ],
  "update": [
    { "id": 45, "status": "DONE", "parent": 6, "add_tags": [...], "remove_tags": [...], "add_logs": [...], "add_links": [...] }
  ]
}
```

约定：

- **分类 = `props` 里的 `type.*` 键**：`{"type.para":"project"}`（或 `area`/`task`）、`{"type.date":"day"}`（week/month/…）、`{"type.habit":"true"}`、`{"type.meetlog":"true"}`；无 `type.*` 的裸节点就是普通 task。`type.date` 会按 title 自动补全 `date.period`/区间
- **父子两种表达并存**：`children` 嵌套（父 id 自动传子，灌时间分层天然用）+ `ref`/`parent_ref`（同批次临时引用，扁平交叉用）。`ref` 必须先定义后引用
- **status=DONE 自动写 closed_at**（add 跟 update 都是）
- **update 可改字段**：`status`/`priority`/`title`/`scheduled_at`/`deadline_at`/`body`/`parent`(move, 校验存在) + `add_tags`/`remove_tags`/`add_logs`/`add_links`。⚠️ 没列在白名单的 key 被静默忽略——`parent` 曾漏在白名单导致 move 静默失败（2026-05-29 修），加字段务必同步白名单
- **单事务**：任一节点失败（缺 title / parent_ref 未定义 / update 目标不存在）→ 全部 rollback，不留半截
- **`--dry-run`**：只解析报数 + 列 ref，不写库
- **AI 用法**：把 worklog / 一天数据解析成此 JSON，`echo '...' | wl import -` 或 `wl import day.json`
- T-4 markdown 导入器 = parse worklog markdown → 生成此 JSON → import

加 import 字段（新 node 属性 / update 操作）时，`_import_node` / `_import_update` 跟本节同步。

## 18. wl-diff 格式（apply，跟 wl 输出对称）

`wl apply [file|-] [--dry-run]` — 输入格式 = wl 节点行 + diff 前缀，跟 `tree`/`ls`/`day` 输出同一视觉语言，可"所见即所改"。

**前缀**：`+`新增 / `~`改 / `-`删 / ` `(空格)上下文锚（定位已有节点作父，不改它）。缩进每 2 空格 = 一层（父子，跟 tree 一致）。

### 18.1 新增 `+` / 删除 `-` / 锚 ` `：节点行

`<前缀><缩进>[marker] [#pri] #id title :tags:`（marker 必有；`+` 无 #id，`-`/锚 必有 #id）。
富字段子行 `@log <文本>` / `@link <doc>` / `@prop k=v`（附到上一个 `+`/锚 节点）。分类用 `@prop type.*` 设置（如 `@prop type.para=project`），节点行上不再有分类 token。
marker → status：`[ ]`TODO `[x]`DONE `[/]`DOING `[>]`LATER `[?]`WAIT `[-]`CANCELED。

```
  #2 [project] 业务聚合          ← 锚: 定位 #2 作父, 不改
+   [x] [#A] 登录修复 :gaming:P0:
+     @log 根因 configmap 错指源桶
- #44 血糖记录                    ← 删 #44 (含子树级联)
```

> ⚠️ **删除 `-` 必须递归删子树**：node 自引外键是 `ON DELETE SET NULL`（防误删父连带、防悬空），所以单删父节点只会把子节点 `parent_id` 置 NULL 变**游离**，不会删子。`cmd_apply` 的 `-` 用 `_collect_descendants` 收集整棵子树 id 一起删（不靠外键级联）。2026-05-29 修复——此前误迁移回退时留下 11 个游离 task 即此 bug；有 `test_apply_delete_cascades_subtree` 守。

### 18.2 更新 `~`：锁定行 + 显式字段操作（⚠️ 安全第一，T-11）

**核心原则：没有显式声明要改的字段，绝对不动。** 不用节点行（怕 marker/title 误改、怕抹除）——改用 `~ #id` 锁定 + 缩进的字段操作行：

```
~ #14                  ← 锁定目标(只 #id), 下面每行一个显式操作
  status DONE          ← set
  priority A           ← set
  priority -           ← clear(清空, 值为 -)
  title 新标题
  parent 6             ← move 到 #6 下
  scheduled 2026-06-01
  +tag urgent          ← add tag
  -tag old             ← remove tag
  +log 进展             ← append log(只能加)
  +link doc / -link doc
  prop owner=xyb       ← set prop
  -prop owner          ← remove prop
```

**单行简写**（改 status/priority/title 这三个最常见的，跟 node list 一致，方便）：

```
~ [x] #14              ← 只改 status=DONE（没写 priority/title 就不碰）
~ [#A] #14             ← 只改 priority（没写 marker = 不碰 status）
~ #14 新标题            ← 只改 title
~ [x] [#A] #14 新标题   ← 三个都改
~ [x] #14              ← 单行 + 后续字段操作可混用
  +tag urgent
```

单行简写 = 行内**出现哪个字段就只改哪个**（marker→status / `[#X]`→priority / 尾部文字→title），跟标准字段操作同一安全语义。tag/prop/link/parent/scheduled 等单行表达不了的，写成下面的缩进字段操作。

约定：

- **只动声明/出现的字段**——锁定行下没写 priority 就绝不碰 priority；单行简写没写 marker 就不碰 status；没写 tag 就不动 tags。这是防"只给 id+名字把别的抹掉"的根本保证
- **可设字段**（status/priority/title/parent/scheduled/deadline）：`字段 值` set，`字段 -` clear（title 不可 clear，NOT NULL）
- **集合字段**：`+tag`/`-tag`、`+link`/`-link`、`+log`（append-only 无 remove）、`prop k=v`/`-prop k`
- **status=DONE 自动写 closed_at**
- `~ #id` 必须至少一个字段操作，否则报错

### 18.3 共同约定

- **两阶段**：先全部校验有错即停**不写**——`~`/`-`/锚 的 #id 必须存在；`+` 不带 #id；字段操作非法值拦截（priority∉ABC / status 非法 / parent 不存在 / 未知字段名 / title 清空）；`--dry-run` 校验 + 列 plan 后停
- **单事务**：执行期任一失败全 rollback
- **注释**：`#` 后跟空格或非数字（`# 说明`）；`#<数字>` 是节点 id 不算注释
- **跟 import(JSON) 分工**：apply 给人/AI 轻量编辑（改状态、加任务、精确字段操作），import 给程序复杂批量（深嵌套、props）
- 未来 `tree --diff` / `day --diff` 输出可编辑格式 → 改 → `wl apply` 往返

加前缀/字段操作语义时，`_parse_wld` / `_parse_fieldop` / `_validate_fieldop` / `_exec_update` / `cmd_apply` 跟本节同步。

## 19. 高亮渲染 / theme（rich，可选）

终端高亮统一走 `rich`，可关、可换主题，默认自动检测。核心是三个东西：全局 `_CONSOLE`、上色 helper `_c()`、输出函数 `out()`。

### 19.1 开关与检测

- 全局参数（任意子命令前）：`--color {auto,always,never}` + `--theme {auto,dark,light,mono}`（theme choices = `["auto"] + list(THEMES)`，加调色板自动进 choices）
- 环境变量兜底：`$WORKLOG_COLOR`（同 `--color` 值）、`$WORKLOG_THEME`、`$NO_COLOR`（标准约定，置任意值即关色）
- `--color auto`（默认）判定：`rich 可用 且 stdout.isatty() 且 无 $NO_COLOR` → 启用。所以管道 / 重定向 / 测试的 StringIO 自动降级纯文本，**测试无需特殊处理**
- `_init_console(color, theme)` 在 `main()` 解析参数后调用一次，设全局 `_CONSOLE`（`None`=纯文本 / `rich.Console`=高亮）；`--color always` 用 `force_terminal=True` 强制出 ANSI（管道也出色，给 `less -R` 用）

### 19.2 theme = 语义元素 → 风格（无"default"调色板，默认 auto）

**没有名叫 `default` 的调色板**——`default` 曾是第 4 个独立配色，语义混乱（既不是 dark 也不是 light）。现在只有三个真实调色板 + 一个 `auto` 选择器：

`THEMES` 字典：每个调色板把语义名映射到 rich style。语义名（不是颜色名），全集见 `_THEME_KEYS`：
`done/doing/later/wait/todo/canceled`（状态）、`pri_a/pri_b/pri_c`（优先级）、`id/type/tag/hit/header/meta/planned/clock`。
- `dark`：深色背景，`bright_*` 提对比度
- `light`：浅色背景，深色饱和色（`green4`/`red3`/`dark_orange3`/`dark_cyan`/`purple`/`grey42`…），避免亮色/白色在白底糊掉
- `mono`：全 `default`（rich 的 default 风格=终端默认前景，无色），给"想要 rich 布局但不要颜色"的场景
- **`auto`（默认）**：不是调色板，是选择器——`_resolve_theme` 探测终端底色解析成 `dark`/`light`：
  - `_detect_bg_is_dark()`：先读 `$COLORFGBG`（无 I/O，末段是 bg 色号，7/15=浅色其余深色）；不行再发 **OSC 11 查询**（`\033]11;?\033\\`，需 stdin+stdout 都是 TTY，0.15s 超时，解析 `rgb:rr/gg/bb` 算感知亮度 `0.299R+0.587G+0.114B < 0.5` 即深色）
  - 深色 → `dark`，浅色 → `light`，**测不出 fallback `dark`**（多数终端深色背景）
  - 终端无色彩能力（`TERM=dumb` 等）→ rich 本就不发 ANSI，等效 mono
- `_STATUS_STYLE` / `_PRI_STYLE` 把 DB 里的 status/priority 值映射到 theme 语义名
- 加调色板：往 `THEMES` 加 key（**必须覆盖 `_THEME_KEYS` 全部**，`test_themes_have_same_keys` 守）；色名必须是 rich 合法名（`test_every_theme_color_name_valid` 守，防 `cyan4` 这类无效名）
- **`wl themes`** 命令列出 dark/light/mono，各按自身配色渲染样例行 + 标出当前（auto 解析后的）调色板（`cmd_themes` 给每个建独立 `force_terminal` Console；`--color never`/无 rich 时退回纯文本列名）

### 19.3 上色 helper `_c(text, style=None)` —— 唯一上色入口

```python
out(_c("✓", "done") + " " + _c(f"#{id}", "id") + " " + _c(title))
```

- `_CONSOLE is None`（纯文本）：`_c` 原样返回 `str(text)`，零开销
- styled：`_c` **先 `rich.markup.escape` 内容**（防 title 里的 `[x]`/`[[doc]]` 被当 markup 吃掉），再包 `[style]…[/style]`；`style=None` 只 escape 不包色
- **铁律：任何要进 `out()` 的片段，凡可能含 `[` `]`（marker `[x]` / 优先级 `[#A]` / type `[day]` / wikilink `[[doc]]` / 时间戳 `[ts]`），必须经 `_c` 包一层**。直接把含方括号的裸串塞进 `out()` 会触发 rich MarkupError 或被静默吞掉

### 19.4 `out()` 取代 `print()`

- 渲染节点/分组/详情的输出一律走 `out()`，不直接 `print()`——这样一处切换 console 全局生效
- `out()`：`_CONSOLE` 有则 `console.print`（渲染 markup），否则 `print`
- `_node_line`（§6 唯一节点渲染器）已全程 `_c` 上色，所以 ls/tree/day/projects/show/focus/find/summary 的节点行**自动**带色，新命令只要复用 `_node_line` 就免费获得高亮
- 搜索命中高亮两处，都走 `_hl(text, q)`（styled: `hit` 风格 / 纯文本: 半角 `*…*`，无命中返回原样 `_c`）：
  - **行内 title 命中**：`_node_line(con, n, hl=q)` —— find 结果里标题命中处直接高亮，不再单独展开
  - **行外字段命中**：`_snippet(text, q)`（body/log/tag/prop/link）截 query 周围片段再 `_hl`，缩进展开
- 纯文本命中标记用半角 `*…*`（曾用全角 `「」`，但全角角括号是宽字形、看着像有空格易误解，2026-05-29 换掉）；styled 模式用背景色不用任何标记字符

加输出 / 改配色时，`_init_console` / `_detect_bg_is_dark` / `_resolve_theme` / `THEMES`(+`_THEME_KEYS`) / `_c` / `_hl` / `out` / `_node_line` / `cmd_themes` 跟本节同步；新增带方括号的输出务必走 `_c`。

## 21. log 历史日期（迁移用）

迁移历史 worklog 时，log 的 `logged_at` 要落在**当时发生那天**，不是导入今天，否则时间线全堆在导入日、失真。统一走 `_insert_log(con, nid, entry)`：

- entry 形态：`dict{date,body}` / 字符串前缀 `"YYYY-MM-DD 内容"`（自动提取日期作 `logged_at`，body 去前缀）/ 纯 body（用今天）
- 入口全覆盖：`wl log --date` / import `logs` & `add_logs` / apply `+log` & `@log` —— 都经 `_insert_log`
- 非法日期 `date.fromisoformat` 校验抛错
- `wl show` 时间线按 `logged_at` 排序，历史日期正确归位

## 20. 计划时间 scheduled（精确 + 模糊）

`scheduled_at` 列既存精确日期也存**模糊粒度**——很多事"大概什么时间做"，往后排可能是下周 / 下个月，时间不一定精确（设计决策 2026-05-29）。

- **不改 schema**：`scheduled_at` 仍是 TEXT 列，存归一化字符串
- **支持的归一化值**（粒度从细到粗）：`YYYY-MM-DD`(day) / `YYYY-Www`(week) / `YYYY-MM`(month) / `YYYY-Qn`(quarter) / `YYYY`(year) / `someday`
- **输入入口统一走 `_norm_sched(s)`**（add / defer / import / apply ~ 全部）：
  - 规范格式直接校验（非法日期/月份/周号抛 `ValueError`，在 dry-run 即报，不写脏数据）
  - 相对词归一化：`今天/today`→日期、`明天/tomorrow`→日期、`下周/next-week`→`YYYY-Www`、`下月/next-month`→`YYYY-MM`、`下季/next-quarter`→`YYYY-Qn`、`以后/someday`→`someday`
  - 无法识别 → `ValueError` + 提示合法格式（不静默吞）
- **粒度 `_sched_level(s)`** / **排序 `_sched_sort_key(s)`**：排序键 = `(起始日 anchor, 粒度 rank)`；模糊值 anchor 到其时间段起点（`2026-06`→`2026-06-01`、`2026-Q3`→`2026-07-01`），`someday` anchor 远未来排最后；同 anchor 时粒度细的排前
- **显示 `_sched_display(s)`**：精确日去年只显 `MM-DD`，模糊值显原样（`2026-06`/`2026-Q3`/`someday`）；`_node_line(sched=True)` 用 `@<display>`（planned 风格高亮；前缀 `@` 而非 emoji——📅 太扎眼，`@` 轻量且在 plain 无色模式下跟前文分隔），ls / day / summary 默认开 `sched=True`

加 / 改计划时间相关时，`_norm_sched` / `_sched_level` / `_sched_anchor` / `_sched_sort_key` / `_sched_display` / `cmd_add` / `cmd_defer` / `_import_node` / `_validate_fieldop` / `_exec_update` / `_node_line` 跟本节同步。

## 22. 当天复现视图 `wl day` + `wl logs --group`（markdown 日结构）

目标：复现 markdown `YYYY-MM 工作记录` 里「某一天所有项目 + 进展」的日维度视图（设计决策 2026-05-29）。

- **`wl day [date]` 基于 log 日期驱动**，不再依赖 day 节点存在（历史数据也能按天罗列）：查 `date(logged_at)=target` 的 log（排除 `CLOCK_*` 记账行 + 限 task/habit/meetlog），按 **桶 → 次级分组 → 任务 → 缩进 log** 渲染，底部统计「N 任务有进展 + 状态分布 + CLOCK」
- **桶（bucket）= `work`/`personal` tag** → `工作`/`个人`/`其他`（`_node_bucket`），顺序 `_BUCKET_ORDER`
- **次级分组 `--by`**（`_sec_group` / `_sec_sort_key`）：
  - `plan`（**`wl day` 默认**，设计决策 2026-05-29——最贴近 markdown 日结构）：当天有排期（或迁移期 `planned` tag）→ `计划内`；其余 → `计划外`。（原来单独的 `计划外（未标）` 一档已并入 `计划外`——现在计划内外靠 sched 推导，那个 tag 区分没价值且 `（未标）` 文案误导，会被当成"没打 work/personal tag"。）
  - `project`：项目祖先（`_node_project` 取项目祖先，`type.para=project`）。`wl logs --group day` 仍默认 `project`
  - `priority`：`A/B/C → P0/P1/P2`，无优先级 `—`（默认当计划外，但标注未显式确认，设计决策 2026-05-29）。⚠️ **计划内/计划外本质是 per-day（per-log）属性**（同一任务今天计划内、明天可能计划外），归并迁移后挂在任务上只是近似；要精确需把标记下沉到 log 行（schema 未做，留待决策）
- **`wl logs --group day [--by ...]`** 复用 `_render_day_group`：先按日期 header 分组，每天内同 `wl day` 结构
- **`wl logs` 默认时间窗**：无 `--id`/`--date`/`--since` 时只列**最近 `--days`（默认 7）天**，防全量刷屏（数据涨上来还能用）；`--since`/`--until`/`--date` 显式覆盖

改 day/logs 渲染或分组维度时，`_node_bucket` / `_node_project` / `_node_plan` / `_sec_group` / `_sec_sort_key` / `_render_day_group` / `cmd_day` / `cmd_logs` 同步。

## 23. 前瞻计划 sched（schedule 与 log 分离，计划内/外推导）

2026-05-29 拍板的模型（详见 [[migration plan]] §八）：**schedule（前瞻计划，类日历）与 log（事后实录）彻底分离；计划内/计划外由 schedule 推导，不存进 log**。

- **`sched` 表**：`(id, node_id, on_date, rrule, created_at)`。`on_date` 与 `rrule` 二选一；一个任务可有多条（多天 / 一次性+重复并存）。
  - `on_date`：一次性排到具体某天 `YYYY-MM-DD`。
  - `rrule`：重复规则，当前支持 `daily` / `weekly:Mon,Wed`（`_norm_rrule` 校验，星期用 Mon..Sun）。复杂 RRULE（间隔/某天+时段）后做。
- **`_sched_fires(on_date, rrule, target)`**：该行是否在 target 当天触发。`_scheduled_node_ids(con, target)` 收集当天被命中的 node。
- **`wl sched <id> [when] [--recur R] [--clear]`**：排期 / 重复 / 清除 / 无参列出。`when` 走 `_resolve_concrete_date`（YYYY-MM-DD / 今天 / 明天 / 后天）。
- **`wl day` 推导（`_node_plan(con, nid, sched_ids)`）**：
  - 计划内 = `nid in sched_ids`（被 schedule 命中，事先排的）**或** 迁移期 `planned` tag。
  - 计划外 = 没命中 sched 且无 `planned` tag（有 log 但没排期的就是计划外；原 `unplanned` tag 和"没打标"两种情况现在合并成一档）。
  - **被 schedule 命中但当天还没 log 的任务也列出**（`wl day` 把 sched-only node 并进 items，标 `«计划·未做»`），实现"计划提前可见，真正做时再加 log"。
- **跟 `scheduled_at`（§20）的分工**：`scheduled_at` = 模糊待办时间（someday / 2026-06，backlog 提示）；`sched` = 具体日历落位（驱动计划内）。两者互补，不互相同步。
- **迁移期过渡**：历史数据用 `planned`/`unplanned` tag 表达计划内外（无 sched），`_node_plan` 把 tag 作 fallback；将来真实排期走 sched 后逐步去 tag。

改 sched / day 推导时，`_sched_fires` / `_scheduled_node_ids` / `_node_plan` / `_sec_group` / `_render_day_group` / `cmd_day` / `cmd_logs`(--group) / `cmd_sched` 同步。

## 24. 目标 / 总结 + 日期上下文（wl day 顶部）

2026-05-29 拍板（vault §八 4/5）：

- **wl day 顶部的 goal / summary**（2026-05-29 起为 prop，后演进，**以下为现状**）：`goal`（前瞻，任意层级）和 `summary`（日终）是节点上保留 tag 的 **log**（历史保留，非 prop）——day 写 goal/summary，week/month 写 goal（同一个 `goal` tag，靠 node type 区分层级；旧的 `overview`/`top5` 已并入 goal）。`wl goal`/`wl recap`/`wl goal set <node>` 写，goal 可带结构化目标 node id（`goal` metric，顺序=优先级）。`wl day` 顶部按 `> 🎯 / > 📝 Recap / > 📅 This week / > ⭐ This month` blockquote 渲染 + 编号带状态的目标列表 + `[done/total]`。
- **日期上下文 → 专门 `date_meta` 表**：`(date PRIMARY KEY, label)`。`label` = 节日/休假/调休/节气（劳动节假期 / 调休上班 / 小满 / 年假 …）。**不依赖 day 节点**，可全年预导入假期表 + 自定义。
  - **周X 自动算**（`_cn_weekday`，不存储），`date_meta` 只存非自动信息。
  - `wl day` 头部 = `<date> <周X>[ · <label>]`（`_date_label` 查表）。day 节点标题统一存**纯日期** `YYYY-MM-DD`（周X/节日不进标题，避免与自动算/查表重复）。
  - `wl dateinfo <date> <label>` 设单个 / `--import <json>`（`{"YYYY-MM-DD":"标签"}`，- 读 stdin）批量导入 / `<date> --clear` 清除 / 无参列出。

改 day 顶部或日期上下文时，`_cn_weekday` / `_date_label` / `cmd_day`(header) / `cmd_dateinfo` 同步。

## 25. 快捷命令: `wl goal` / `wl recap` / `wl tick` + 自动建 day

为日常 daily 流程减少敲键(2026-05-30 加,为 worklog→wl 切换准备):

- **`_ensure_today_day(con)`**: 找今天 day 节点;不在就建,挂当前 ISO 周(`2026-Www`),周不在就 unparented(`parent_id NULL`)
- **`wl goal [text]`**: 读/写今天 day 节点的 `goal` prop。`text` 缺省=读
- **`wl recap [text]`**: 读/写今天 `summary` prop(命名避开已有 `wl summary --week/--month` 报表命令)
- **`wl tick <id> [--note "..."] [--done]`**: 给 node 加今天一条 log(默认 body `✓ done`),`--done` 同时把 node status 改 DONE。habit 打卡 + 一次性任务标完成都用它
- 共享底层 `_set_prop`/`_get_prop`/`_insert_log`,避免重复

## 26. 时长汇总自动计算(2026-05-30)

拍板:**不引入 `wl log --duration` 显式字段, 暂用自动计算**。理由:

- 显式字段会因不及时更新+级联(task → project → area 上层)产生污染
- 自动计算容易看出偏差(数据一摆出来误差就明显), 反而好发现和修 bug
- 等未来有强需求(精确日报/工时报销)再加 `--duration`

**`_node_clock_min(con, nid)` 综合算法**:

```
total = max(clock_total, log_span)
```

- `clock_total`: 所有 `CLOCK_OUT elapsed=Nmin` 累计(精确, 来自 `wl start/stop`)
- `log_span`: 普通 log(非 CLOCK_*) 的 `(max(logged_at) - min(logged_at))` 分钟数
- **去重 timestamp**: `SELECT DISTINCT logged_at` 让同时刻批量补录的多条 log 算"一刻", 不污染 span
- 两个口径取较大值——CLOCK 没有时 log span 兜底, 有 CLOCK 时不被压低

**显示格式 `_fmt_dur(minutes)`**:

- ≥1h 且有分钟: `[2h30m]`
- ≥1h 整: `[2h]`
- <1h: `[45m]`
- 0: 不显示
- **ASCII 安全**: 不用 emoji(`⏱` 等 wide char 跟下个字符在某些终端重叠), 不用花式 unicode
- 参考 org-mode 的 `[2:30]` 但用 `Xh Ym` 更符合 wl 已有 `wl day` 底部统计的 `Xh Ym` 口径

**显示位置**(`_node_line` 默认 `clock=True`):

- `wl day` task 行末尾
- `wl ls` 默认带(brief 模式不带, 暂用 tags 参数控制)
- `wl projects` task 行
- `wl show` 单 node 元信息段
- `_render_day_group` 也加显示(wl day 主视图)

未来扩展:

- log span > clock_total 时 hint 标 `*`(估算)
- project / area 层汇总(SUM 子 task 时长)

## 27. 自动状态推进 + `--keep-status`(2026-05-30)

拍板: **`wl log <id>` 自动 `TODO → DOING`**(无 --date 时), 因为"我 log 一条 = 我在做"是直觉表达。

**精确规则**:

| 条件 | 自动转 | 原因 |
|---|---|---|
| `wl log <id> "body"` (无 --date) + status=TODO | TODO → DOING | 默认行为 |
| `wl log <id> "body" --date <历史>` | 不动 | 补录历史不该改当前 status |
| `wl log <id> "body" --keep-status` | 不动 | 显式禁用(给 DONE 后再补 log 不想回退场景) |
| status 已是 DOING/LATER/WAIT/DONE/CANCELED/DEFERRED | 不动 | 不打扰显式终态 |
| `wl tick <id>` | 不动 | 习惯打卡是离散事件, 每次重置 DOING 不合理 |

输出 `✓ log added to #N (status: TODO → DOING)` 让用户看到状态变化。

## 28. 过滤 / 合并 / 排序 / 范围参数统一规则(2026-05-30, **设计中, 待 review**)

随着命令增多(ls/tree/projects/day/summary/find/logs/changes),**过滤/合并/排序/范围**参数会激增, 容易各处不一致。需要先在文档落规则, 各命令统一实现。

### 28.1 参数分类

| 类别 | 参数(顶层全局 OR 子命令本地) | 描述 |
|---|---|---|
| **状态过滤** | `--show-canceled`(顶层全局, 默认隐) / `--all`(本地, ls/projects: 包括 DONE+CANCELED) / `--status STATUS`(本地, ls 已有) | 默认隐 DONE 和/或 CANCELED |
| **类型过滤** | `--para {area,project,task}`(本地, ls/tree/find/day) 走 type.para 精确匹配; 其它分类用 `--prop type.<x>`(如 type.meetlog / type.date=day) | 分类由 type.* 派生 |
| **优先级过滤** | `--priority A/B/C`(待加, ls/find/day) | 限 P0/P1/P2 |
| **标签过滤** | `--tag TAG`(本地, ls 已有, AND filter) | 多 tag 逗号分隔 AND |
| **时间范围** | `--since/--until/--week/--month/--date`(parent parser, changes/summary/logs/projects 已用) | 时间窗口 |
| **父节点过滤** | `--parent ID`(本地, ls 已有) / `--root ID`(本地, tree) | 限定子树 |
| **数量限制** | `--limit N`(本地, find 已有, 默认 20) / `--top N`(本地, summary 已有) / `--all`(配合 --limit, find 已有) | 防爆屏 |
| **次级分组** | `--by KEY`(本地, day/summary/logs/tree 已有但语义微妙不同) | 二级分组维度 |
| **合并/去重** | `--no-dedup`(本地, summary 已有) | 同 task 跨多 project 是否重复列 |
| **排序** | (暂无显式 `--sort`, 各命令内置) | 隐式按 priority → id / 按时间 / 按完成数 |

### 28.2 命名约定

- **隐藏/显示**: 默认隐的, 显式打开用 `--show-X`(顶层全局 OR 本地); 默认显的, 隐藏用 `--no-X`(本地)
- **包含/排除**: 单类型用 `--X VALUE`; 默认所有, 排除子集用 `--exclude-X`(暂未引入)
- **数量**: `--limit N` 截断后 N 条; `--top N` 排序后取前 N; `--all` 取消默认限制
- **范围**: `--since/--until` 范围; `--week/--month/--date` 单维度快捷, 覆盖 since/until
- **复用 parent parser**: 时间窗口 `--since/--until/--week/--month` 已抽 `window` parent parser; 状态过滤考虑也抽

### 28.3 顶层全局 vs 本地子命令

| 参数 | 位置 | 理由 |
|---|---|---|
| `-q/--brief` | 顶层全局 | 跨所有命令的输出紧凑模式 |
| `--show-canceled` | 顶层全局 | 跨所有 list/tree/find 命令的状态过滤 |
| `--color/--theme/--version` | 顶层全局 | 渲染配置 |
| `--since/--until/--week/--month` | 子命令本地(经 window parent parser) | 只 changes/summary/logs/projects 用 |
| `--limit/--top/--by/--para/--tag/--parent/--all` | 子命令本地 | 不一定每个命令都适用 |

**冲突处理规则**:

- `--show-canceled` 跟 `--all`: `--all` 包含 CANCELED + DONE, `--show-canceled` 只加 CANCELED, 不加 DONE
- `--top N` 跟 `--limit N`: `--top` 先排序取前 N, `--limit` 截断显示, 可叠加
- `--week` 跟 `--since`: `--week` 覆盖 `--since/--until`(已实现)
- 互斥参数显式 `sys.exit("✗ X 跟 Y 二选一")` 报错(参考 `--sched + --scheduled`)

### 28.4 实施清单(分批, 不一次推完)

**Batch A: 状态过滤统一**(已完成)

- [x] 顶层 `--show-canceled` flag
- [x] `_tree_children` / `_print_default_tree` / `_print_tree` / `_print_day_activity` 接受 `include_canceled` 参数
- [x] `cmd_tree` 选 roots 时也过滤 CANCELED
- [x] `cmd_ls` 应用(默认隐 DONE+CANCELED, --all 都显, --show-canceled 只加 CANCELED)
- [x] `cmd_projects` 应用
- [x] `cmd_find` 应用
- [x] `cmd_day` 应用(默认隐 CANCELED 任务的 log)
- [x] `cmd_summary` 应用
- [x] 新增 `wl cancel <id>` 命令(多 id),跟 `done` 平行,改 node.status='CANCELED' + closed_at

**Batch B: 范围参数全统一**(已完成)

- [x] window parent parser(`--since/--until/--week/--month`)已抽
- [x] `projects` 改用 window parent parser, `--since/--week/--month` 都接受(经 `_resolve_window` 解析成 since cutoff)
- [x] `day` 接受短日期 ✓ 已实现

**Batch C: 数量限制统一**(已完成)

- [x] `--limit N` 加到 `ls/projects/logs`(默认无限, 显式按需; `wl find` 已有,默认 20)
- [x] `--top N` 加到 `ls/projects`(按 priority+id 排序后取前 N; `wl summary` 已有同名)
- [x] 截断时输出 `(显示 N/total)` hint 让用户知道

**Batch D: 排序**(待 review)

- [ ] 暂无明确需求, 等 use case 出现再加 `--sort PRI/DATE/...`

### 28.5 决策记录

- **不一次全实施**: §28.4 Batch A 部分实施(tree 链路), 其他等 review 后扩到 ls/projects/find/day, 避免多处不一致
- **`set <id> status X` 不改 node.status**: 历史设计, `set` 是写 prop 表的通用接口; 改 node.status 应用 `wl done/start/wait/reopen/cancel` 等专用命令。`wl cancel <id>` 已加(2026-05-30), CANCELED 有专用入口
- **CANCELED 显示**: 已有 marker `[-]` + 主题 `canceled`(颜色), 默认隐藏即可, 显式 `--show-canceled` 时再渲染

## 29. log 展示统一规则

所有"列 log 给人看"的命令共用一组规则,防 wl day / wl logs / wl tree 被长 log 撑爆屏幕:

| 命令 | 默认 tail | 关闭 (一条不展) | 全展开 | 自定 N 条 |
|---|---|---|---|---|
| `wl day` | 3 (每 task 末尾) | `--no-logs` / `-q` | `--all-logs` | `--log-tail N` |
| `wl tree` (day 活动) | 3 (每 task 末尾) | `--no-logs` / `-q` | `--all-logs` | `--log-tail N` |
| `wl logs --by-task` | 3 (每 task 末尾) | `--no-body` / `-q` 只列日期 | `--all-logs` | `--tail N` |
| `wl logs --group day` | 3 (桶内每 task 末尾) | `--no-body` / `-q` | `--all-logs` | (复用上一行) |
| `wl show` (时间线) | 5 (整条 timeline 末尾) | `--no-timeline` / `-q` | `--all-timelines` | `--timeline-tail N` |

实现统一走 `_resolve_log_tail(args, brief, default_tail=N)`,优先级 brief → all-flag → 显式 tail → 默认。中间省略时多印一行 `… (X 条更早 log 省略)`,让用户知道有多少被吃掉。

设计意图: `wl day` 是高频复现命令, 过去全展开导致一条任务的十几条 log 把屏幕撑爆;改成默认 3 后, 看末尾进展+总条数提示已够;真要看历史进 `wl show <id>` 或 `wl logs --by-task <id> --all-logs`。AI 侧调用同样受益(token 大幅降)。

## 30. log 编辑接口 (unlog / relog)

log 写错有两条出路:

- `wl unlog #L<id>` — 删除 (CLOCK_* 禁删, 用 `wl stop --at` 修计时)
- `wl relog #L<id> [新 body] [--at ts]` — 改写 body 和/或时间戳;不传任何参数走 `$EDITOR`

不允许跨 node 迁移 log (那是先 unlog 再 log 的事, 保持职责单一)。`wl relog --at` 接受 `HH:MM` / `YYYY-MM-DD` / `YYYY-MM-DD HH:MM[:SS]`, 用 `datetime.strptime` 校验范围(拒 `25:00` / `2026-13-01`)。body 不接受 `CLOCK_IN`/`CLOCK_OUT` 开头, 防伪造计时事件。

## 31. 时间投入补录 (start --at / stop --at / spent)

工作干完才想起没开计时 是常态。三条补录入口:

- `wl start <id> --at HH:MM` (或完整 ts) — 倒填 CLOCK_IN, 不影响 `wl active` 配对
- `wl stop <id> --at HH:MM` — 倒填 CLOCK_OUT (必须晚于对应 CLOCK_IN, 否则拒)
- `wl spent <id> <duration> [--at end]` — 直接给一段时长 (90 / 90m / 1h30m / 2h), 内部写一对 CLOCK_IN/OUT (起始 = end - duration; end 默认 NOW)

`spent` 适合"已经结束、忘了开 clock"的回顾;`start --at` + `stop --at` 适合"开始时间记得但当时没敲"。三种都共用 `_resolve_at_ts(at)` helper (跟 `wl relog --at` / `wl log --time` 一致语法)。

## 32. 循环/定期任务 RRULE

`wl sched <id> --recur <rule>` 接受五种重复规则,每种都支持 `-1` 表"周期最后一天":

| 规则 | 含义 | 例子 |
|---|---|---|
| `daily` | 每天 | `--recur daily` |
| `weekly:Mon,Wed,Fri` 或数字 `1-7`/`-1..-7` | 指定星期 (1=Mon..7=Sun, -1=Sun..-7=Mon, 写入时归一化为 Mon..Sun) | `weekly:Sat,Sun` / `weekly:1,3,5` / `weekly:-1`(周日) |
| `monthly:N` | 每月第 N 号; -N 从月末倒数 | `monthly:5` / `monthly:-1`(月末) / `monthly:1,15,-1`(月初/中/末) |
| `quarterly:M-D` 或 `-1` | 季度内月偏移 M (1=季首/2=季中/3=季末) + 第 D 号; `-1` = 季度末日 | `quarterly:1-15` (1/15, 4/15, 7/15, 10/15) / `quarterly:-1` (3/31, 6/30, 9/30, 12/31) |
| `yearly:MM-DD` 或 `-1` | 每年某日; `-1` = 12-31 | `yearly:03-21` / `yearly:01-01,12-25` / `yearly:-1` |

`-1` 在所有 rrule 里语义统一:**当前周期的最后一天**。短月份自动处理(`monthly:31` 2 月不触发; `quarterly:3-31` 6 月不触发因为没 31)。

实现走 `_sched_fires(on_date, rrule, target)`(target 日期返回 True/False),`_norm_rrule` 在写入时校验范围 + 数字 → Mon..Sun 归一化。`_scheduled_node_ids(con, target)` 遍历 sched 表算 target 当天命中的 node id, 给 `wl day` / `wl checkin` 用。

未来扩展(暂未做): `monthly:1st-Mon`(每月第 N 个周 X), interval(每 2 周), end-date(规则到 X 日后失效)。

## 33. 查询精准化设计原则

理念落 vault [[wl 查询精准化设计原则]] — 命令应让"目标 → 命令 → 输出"三层都精准, 不靠列全量再眼睛搜.

核心反模式: AI 用 `wl ls` / `wl logs --id N` 列全量找 1-2 内容; 浪费 token + 反复试.

落地动作:
- `wl ls` 无任何过滤参数时顶部加一行 hint 引导用 `wl find` / `--parent` / `--para` / `wl day` 等精准入口 (仍列出, 不阻断)
- `wl logs --id N --tail K` 单 task 模式 tail 也生效 (之前仅 `--by-task` 配合)
- vault 文档维护"已识别浪费场景" Audit 清单, 撞到一个反模式就修一个, 持续迭代

`--tail` 跟 brief / `--all-logs` 共用 `_resolve_log_tail` (§29).

## 34. shell completion 自生成器

argparse 是 source of truth. `wl print-completion {fish,bash,zsh}` 遍历 `build_parser()` 自动出对应 shell 补全脚本; 用户走 init load 模式加载 (跟 starship/direnv/zoxide 同), 开新 shell 自动应用 `cli.py` 改动。

```fish
# fish: ~/.config/fish/config.fish
wl print-completion fish | source
```

```bash
# bash: ~/.bashrc
eval "$(wl print-completion bash)"
```

```zsh
# zsh: ~/.zshrc
eval "$(wl print-completion zsh)"
```

动态补全 (node id / tag / date / recur 建议) 用 4 个 shell helper function 本地查 SQLite `worklog.db`, **不启 Python**, Tab 体验 <50ms。

### 用户别名 (跨 shell 统一)

```ini
# ~/.config/worklog/aliases.ini
[aliases]
d = day
c = checkin
ll = ls
```

`build_parser()` 启动时读这个文件用 `configparser` (stdlib), 注入 `argparse aliases=[...]` 到对应 subparser。三个 shell backend 自动 emit 别名 (fish OR `__fish_seen_subcommand_from day d`, bash/zsh `day|d)` case pattern)。`wl d` 在所有 shell 都识别为 `wl day`, 跟 shell 无关。

设计调研详 vault [[Python argparse 自动生成 fish completion 调研]].

## 35. battery-included 命令设计哲学

每个 `wl <cmd> --help` 都要让用户**立刻就懂**这个命令是什么、什么场景用、跟相似命令的区别, 不用读 DESIGN/SKILL 才会用。

### 三层信息每个命令都得有

| 层 | 内容 | 实现 |
|---|---|---|
| **1. one-liner** | `wl --help` 显示的一句话: 干啥 + 适合谁 | `add_parser(name, help="...")` 的 `help` |
| **2. 使用场景** | `wl <cmd> --help` 顶部: 什么情况用 (2-4 个典型场景) | 子 parser `formatter_class=argparse.RawDescriptionHelpFormatter` + epilog 开头 |
| **3. 跟相邻命令的区别** | epilog 内"跟 X 的区别"段落: 防止混用 | epilog 中段 |

可选 4: 常用示例 (`wl ls --help` 已有 10 条 epilog 示例)。

### 反例 (违反 battery-included)

- ❌ `help="active"` (无信息量)
- ❌ `help="列在跑的 task"` (单层一句, 没场景说明)
- ❌ 跟 `wl day` 信息有重叠但没说差异 → 用户不知道两条命令选哪个

### 正例

`wl active --help`:

```
help     = "此刻在跑的 task (CLOCK_IN 未关) + 今日累计 + 最近 log"
epilog   = 使用场景 (3 条) + 跟 wl day 的区别 + 紧凑模式 / full mode 提示
```

`wl ls --help`:

```
help     = "list nodes (默认限 20 条, 参考 shell ls -t / -S / -r 各种维度)"
epilog   = 10 条常用示例 (--parent / --para / --tag / --unscheduled / --sort / --recent / --ids / --status / --all)
```

### 信息密度 vs 简洁的权衡

- 不要堆字。每条信息要么是**操作判定锚点**(场景/差异/默认值/极常用例),要么不写
- 字数控制: epilog 总长 ≤ 25 行 (一屏内容)
- 例子靠前,理论靠后
- 用列表 / 表格 / 缩进让结构清晰

### Audit 节奏

每加一个新命令必须按上面三层来写。已有命令逐步补 (按使用频率排序):

- 高频(必补 epilog): `day` / `add` / `done` / `log` / `find` / `summary` / `projects` / `changes` / `ls` / `tree` / `show` / `active`
- 中频: `start` / `stop` / `spent` / `relog` / `unlog` / `checkin` / `sched` / `defer` / `tick` / `link` / `set`
- 低频/工具: `dateinfo` / `import` / `apply` / `themes` / `print-completion` / `init` / `goal` / `recap` / `wait` / `reopen` / `cancel` / `focus` / `ancestors` / `descendants`

每次改某个 cmd 时顺手按 battery-included 三层 audit, 不达标补全 epilog (类似 §29-§33 的演进节奏)。

### ⭐ help 是 source of truth, skill 二级派生

**重要架构原则**: 所有有用的例子 / 用法提示 / 场景说明 / 跟相邻命令的区别都**先写进 `wl <cmd> --help` 的 epilog**, 不直接进 skill。skill (worklog / log / worklog-cli 等) 从 help 抽取或引用, 不维护独立副本。

为什么:
- **唯一来源** — 改 help 自动跟新, 不会 help / skill 漂移 (旧痛点)
- **离线可查** — 用户 `wl <cmd> --help` 看完整文档, 不需查 vault / skill 文件
- **AI 抽取** — `wl print-completion fish` 之类未来可以从 epilog 派生提示;skill 写 `wl <cmd>` 时让 AI `Bash wl <cmd> --help` 拿权威说明
- **跟 fish completion 解耦** — completion 已经从 argparse 自动生成 (§34), help epilog 跟它共用 source

skill 里应该写什么:
- ✅ **决策框架** (用户说 X → 调 Y 命令) — 跨命令的导航
- ✅ **跨命令最佳实践** (多 cmd 配合的 workflow: e.g. add → log → done 一条链)
- ✅ **规范 / 反模式** (e.g. log body 先概括后细节, 不混桶等)
- ❌ **某条命令的参数细节** → 该进 help epilog, skill 提一句"详 `wl <cmd> --help`"
- ❌ **重复 help 已写的示例** → 删, 让 skill 简短

新加 / 改 cmd 流程:
1. 改 `src/worklog/cli.py`: 加 / 改 add_parser 的 description + epilog (含使用场景 + 例子 + 跟相邻命令区别)
2. 跑测试 + 自查 `wl <cmd> --help` 输出
3. 看相关 skill 有没有冗余副本, 删 / 改成引用
4. commit

skill 维护成本下降, help 一改全跟。

## 36. 数据访问层（model 类 + 查询拆解）

数据层三层: `models.py`（每表一个 dataclass + 增删改查）→ handlers（`commands/`）→ 视图 DTO（`commands/dtos.py`）。`db_table.py` 是 model 之下的底层 SQL 封装（参数绑定、`ALIVE` 软删除谓词、标识符校验）。

- **软删除约定靠 lint 强制，不靠 review。** `ALIVE = "deleted_at IS NULL"` 是谓词的单一来源；model 读取和 `_where`/`clause` 助手都会自动 AND 上它，但 raw JOIN/CTE/聚合 SELECT 是绕过它的逃生通道——在那儿漏掉就会把 tombstone 行泄漏进 `wl day`/搜索。`tests/test_alive_lint.py` 用 `ast` 解析每个源文件（f-string 折叠成一条语句），断言: (1) `deleted_at IS NULL` 除了 `ALIVE` 定义处别处不许手写; (2) 每个对软删表的 raw 读取都带 ALIVE——内联写或在所在函数里拼装（`where.append(_db.ALIVE)`）皆可。逃生通道照留, lint 只是把"漏掉"从静默泄漏变成红色测试。（跳过已应用的 `migrations/` 和生成的 shell 补全模板。）

- **model 类是 Active-Record-lite。** 每表一个 `@dataclass` 镜像表的列，**字段名与列名严格一致**。增删改查放在共享基类上，具体类只剩 `_table` + 字段: `_Model`（query / query_one / count / exists / insert / delete / purge + `__getitem__`）、`_IdPK` mixin（整数 id 表的 get / gets / update）、`_Upsertable` mixin（自然键表的 upsert），表名放在 `_table` 类属性里。`from_row` 是一个反射式构造器（`cls(**{f.name: row[f.name] …})`）——字段在行里找不到对应列就在构造时报错，把"字段名==列名"这条规则从注释变成**强制**。绝不给 model 加纯展示字段，那是 `commands/dtos.py` 视图 DTO 的职责。
- **读取一律 `SELECT *`**（from_row 需要每一列）。列投影、别名 / JOIN / 聚合读取直接走 `db_table`——这是全行 model 无法表达的、刻意保留的逃生通道。读取隐藏 tombstone（默认 `include_deleted=False`）；写入（按 id 的 `update`、`upsert`）穿透它——`upsert` 会复活一个被软删的行。`_table` / `_upsert_key` 只声明在非 dataclass 的 mixin 上（无类型标注的类属性）；在具体 dataclass 上写带标注的 `_foo: T` 会变成必填字段。
- **`gets(ids)` 是批量读取**——一次查询返回与输入等长、缺失为 None、保持顺序的列表。用它替代 N+1 的 `get` 循环。
- **渲染器接收 `Node`，不是 `sqlite3.Row`。** `_node_line` / `node_view` / `node_type` 等节点渲染器收到的是 `Node` 对象。每个 raw 全行 node 查询在边界用 `Node.from_row` 转换（tag 路径集中在 `nodes_with_tag` 内部转，新的全行调用点不会漏 wrap），所以 Row 永远到不了渲染器。`_Model.__getitem__` shim 让 Node 能像 Row 一样按列名下标访问（只认声明列，miss 抛 KeyError）——刻意保留，好让渲染器现有的 `n["title"]` 索引不必大改成 `n.title`。删掉 shim（把每个 `n["x"]` 迁成属性访问）是个推迟的清理: 几百处访问、零行为变化、当下 ROI 为负。
- **查询拆解——简单读 + Python 组合，优先于复杂 SQL。** 既定方向，和 G3、以及"不用外键" + "时间模型用 Python 从 date 推导归属、而非 parent 链"一脉相承: 把一个复杂读拆成若干简单单表 SELECT、在 Python 里组合——每个封装成小函数，**按需批量读取**（只取实际需要的那批 id，一次 `id__in` 查询——不逐行、不全表）——而不是把 JOIN / EXISTS 子查询 / 聚合塞进一条 SQL。**原因:** 库很小（几千行量级、~2 MB），候选读 + Python 组合是亚毫秒级，实测比它替代的子查询 SQL **更快**（`workitem_sql` 过滤——6 个 EXISTS 子查询——1.90 ms/次；读 node 再用纯函数 `node_type_from_props` 在 Python 里分类是 1.59 ms，结果集完全一致）。复杂 SQL 也是最难维护、最难做性能剖析、最难（被人或 AI）重构的部分，所以减少它是可维护性的净收益，不是性能代价。`db_table` 那句"NOT an ORM——复杂读保留显式 SQL"相应收窄: 只在拆解确实帮不上的地方（如 GROUP BY 词表统计、`local_day` 时间窗 JOIN）或投影场景保留 raw SQL。仍在手写 SQL 的简单单表读（`semantic.py` 的关键词 LIKE、count(*)）已对齐到 `_db.query` / `Node.count`——`cmd_find` 本就是模板。
- **type 分类是按需批量，不是 EXISTS 子查询。** 节点的 type 由它的 `type.*` props 经纯函数 `node_type_from_props(props)` 推导（优先级 para > date > habit > meetlog > custom > task）。原来重新编码成 EXISTS 子查询的 SQL 形式（`workitem_sql`（已删）、`nodes_with_type`、`time_node_by_period`）已被一个原语取代: **`classify_types(con, node_ids)`** 只读这批 id 的 `type.*` props（一次 `node_id__in` 批量查询），在 Python 里分类；`filter_workitems`（过滤 node 批次）和 `workitem_ids`（过滤只带 node_id 的 JOIN 行）建在它上面。这批是调用方的实际需要——某 project 的子节点、某天有 log 的 node、status 过滤后的集合——一次 `id__in` 读出、不逐个（非 N+1）。读取跟着请求走，而不是预先建一个投机的全表索引；不过整树 `summary` / orphans 的请求*就是*全部存活 node，所以那条路径确实读了全部 type prop（`id__in` 列表未分块——当前几千行规模没问题，若整树涨到极大则是撞 SQLite 变量上限的已知天花板）。**不加缓存**——每次只读所需。对象级、写入时自动过期的 identity-map 缓存（让属性访问走缓存对象、长期运行进程内保持一致）是约定的*下一步*，刻意推迟。还没解决的渲染循环 N+1 只剩 `_node_line` 里 type/clock/tags/planned 混在一起的逐节点读取——那是单独的渲染层批量预取，尚未做。
- **图操作集中在一个模块（`graph.py`）。** 凡是沿 node 图的边遍历/变更的都在这里，作为 single source: 树（`parent_id`——`_ancestors_chain` 向上、`_collect_descendants` 向下）、`relation.*` 关系图（`relation_view` / `_apply_relation` / `_backrels`）、spoke 表级联软删（`soft_delete_node` / `soft_delete_log`）、结构成员（`_project_members`）、以及 `--by` 分组派生（`_node_project` / `_sec_group` …）。**单向依赖**（`queries ← graph ← commands`）: `graph` 从 `queries` import 属性/分类原语（`classify_types`、`node_type`、`_node_exists`、`_parse_id_list`、`relation.*` 常量），**绝不反向**——这是保持 import 图无环的关键；图操作需要新原语就加到 `queries` 再 import，别横向伸手。一个操作进这里的标准是**必须沿边遍历**: 纯属性过滤（`make_node_filter`——按 tag/status/prop 过滤单个节点、不遍历）留在 `queries`；别把"和 node 有关但不沿边"的塞进来，否则这模块就不再是"图"而变成杂物抽屉。自由函数、不包类——一张表 + cycle-safe 遍历本身就是 repository 层（G3）；`cli` re-export 这些函数,让测试经统一的 `wl.X` 入口访问。这个模块还拥有 **`check_integrity`（`wl doctor`）**——对无 FK 不变量的按需审计（无 dangling `parent_id`、无父链环、每个存活 spoke + relation 引用都指向存活节点、relation 对称、不自指）；只读、每表一次批量读、O(N)。

## 引用

- `README.md` — 安装 + 命令速查 + schema
- `TODO.md` — 功能路线 T-1~T-10
- `src/worklog/migrations/NNNN_*.sql` — 表结构(编号 migrations,`PRAGMA user_version` 追踪)
- vault `[[结构化 worklog 项目需求 v1]]` — 项目 PRD + v1.1 + D8 自建决策
