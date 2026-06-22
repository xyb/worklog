<sub><b>🌐 English</b> · <a href="DESIGN.zh.md">中文</a></sub>

# worklog design conventions (canonical)

> Every command must follow the unified conventions in this document. Read this before adding a new command or changing an old one, to keep things consistent.
> If conventions change, update this document, all affected commands, and the tests together.

## 0. Design goals (north star, override every specific convention)

These goals are foundational. Pass them before adding or changing any feature; when they conflict with a specific convention below, they win.

### G1 Structured-first (no string matching)

worklog's whole job is to **structure** the work log so information can be precisely queried / aggregated / analyzed. Any semantics that "must be searchable/countable later" has to live in a **structured field (its own column + type)** — never via string matching / prefix conventions on the body text. That is not structured; it can't be queried or counted reliably.

- For a new feature, ask first: "what is its structured carrier?" (which table, which column, what type) — not "which blob of text do I stuff it into".
- Anti-patterns (to be phased out): CLOCK events detected via `body LIKE 'CLOCK_%'`; habit completion inferred from "is there a log that day". Both are string conventions — fragile and unaggregatable.

### G2 Minimal & self-evident (usable without docs, by humans and AI alike)

The tool must be simple enough to **use without reading the manual**, for both humans and AI. AI faces a plain-text interface; complexity makes it err.

- Keep the **types of records hung under a node** restrained (fewer is better): each extra type is one more concept to remember and one more block to render — heavier, harder to grasp.
- Self-check any new design with three questions: (1) how many new concepts does it add? (2) does using it force AI/humans to make a choice ("A or B?")? (3) can you guess it right without docs? If any answer is poor, simplify further.
- When G1 and G2 conflict, find the "structured AND fewest-concepts" answer — don't pile on new tables / fields / commands just to be structured.

### G3 Few dependencies, simple logic, easy to maintain

worklog deliberately stays **dependency-light and low-abstraction**: runtime is **stdlib + `rich` only**, and new features prefer **zero new dependencies**. Logic stays **explicit and direct** — plain SQL over query-builder DSLs, small purpose-built helpers over frameworks/ORMs that hide what is happening. The bar: a maintainer (human or AI) can read any code path top-to-bottom without first learning a hidden layer.

- **Borrow the technique, not the library.** Prefer writing a tens-of-lines, zero-dependency helper over pulling in a package (e.g. a dict→`INSERT` helper in the spirit of sqlite-utils, *not* an ORM; a `field__op`→`WHERE` helper, *not* a query builder).
- **No ORM / no query builder.** Wrap only the uniform ~80 % (single-table CRUD + existence/count) in thin helpers that map transparently to one SQL statement; keep the complex ~20 % (JOIN / CTE / CASE / time-window) as explicit SQL. Don't reinvent SQL as a kwargs DSL.
- **Each table / module maintainable on its own.** Minimize forced coupling (hard FK cascades, cross-cutting magic, triggers that hide intent) so one part can change — or be migrated / synced — without rippling everywhere. Lean toward avoiding irreversible operations (prefer soft-delete / status over `DELETE`) where they create cross-table consistency burdens.
- When a "clever" abstraction conflicts with G3, choose the boringly-simple version. Fewer moving parts beats elegance.

## 1. Command style (todo.sh school)

- Verb first: `wl <verb> [args] [--flags]`
- Subcommand names: lowercase single words — `add` / `done` / `tree` / `projects` / `changes` / `summary` / `focus` …
- Commands operating on a single node take `id` (int) as the first positional arg: `wl show 42` / `wl done 42` / `wl log 42 "..."`
- Long flags `--xxx`; short flags only for the highest-frequency ones (`-p` priority / `-t` tag)
- Missing node: always `sys.exit(f"✗ ... #{id} not found")` with non-zero exit code

### 1.1 Command taxonomy (primitive CRUD / composite helper / view)

Every command falls into exactly one of three buckets. Knowing which keeps the
surface coherent and tells you where a new command belongs.

**A. Primitive entity CRUD** — directly create/read/update/delete one underlying
table row. The **`metric` subsystem is the template**: `metric add / ls / edit / rm`,
a complete, consistently-named CRUD set. Every entity should reach that completeness;
naming/behaviour stays uniform across entities. Current state:

| entity | create | read | update | delete |
|---|---|---|---|---|
| **metric** | `metric add` | `metric ls` | `metric edit` | `metric rm` |
| **node** | `node add` | `node ls` / `node show` | `node edit` / `reparent` | `node rm` |
| **prop** | `prop set` (= `set` w/ a non-goal/summary key) | `prop ls` / `show` | `prop set` | `prop rm` (= `unset`) |
| **goal** (reserved-tag logs) | `goal set` / bare `goal` (today) | `goal ls` / `show` | `goal set` (appends; latest = current) | `goal rm` (= `unset` w/ a goal/summary key) |
| **clock** | `start` / `spent` | `clock ls` / `active` | `clock edit` | `clock rm` |
| **link** | `link add` (= `link`) | `link ls` / `show` | — (atomic) | `link rm` (= `unlink`) |
| **log** | `log add` (= `log`) | `log ls` / `logs` / `show` | `log edit` (= `relog`) | `log rm` (= `unlog`) |
| **sched** | `sched add` (= `sched`) | `sched ls` / `show` | `sched add` / `defer` | `sched rm` (= `sched --clear`) |
| **tag** | `tag add` (= `tag +x`) | `tag ls` / `show` | — (atomic) | `tag rm` (= `tag -x`) |
| **date_meta** | `date set` (= `dateinfo`) | `date ls` / `dateinfo` | `date set` | `date rm` (= `dateinfo --clear`); `date import` |

Every entity now has a full metric-style `<entity> <verb>` group. Entities reshaped: **metric**
(template), **node**, **prop**, **goal** (reserved-tag logs), **clock**, **link**, **tag**, **log**, **sched**, **date** (the `date_meta` table). High-frequency verbs keep a top-level
shortcut onto the same handler (`wl add` == `wl node add`, `wl unlink` == `wl link rm`,
`wl relog` == `wl log edit`, `wl unlog` == `wl log rm`, `wl dateinfo` == polymorphic over
`wl date set/ls/rm/import`), args defined once via a shared adder so the two forms can't
drift. `clock` intervals are CREATED by the composite helpers (`start`/`stop`/`spent`).
Removal is soft-delete (reversible tombstone, § soft-delete).

**`wl set` / `wl unset` are *key-routed* shortcuts** (the same way `wl add` is `node add`):
the key `goal` or `summary` routes to `wl goal set` / `wl goal rm`
(history-preserving reserved-tag log); any other key routes to `wl prop set` / `wl prop rm`
(static single-value UDA prop). So one `set`/`unset` surface fronts two group-set verbs by
key — `prop` and the reserved-tag logs are distinct stores (prop = overwrite single-value;
goal/summary = append log, latest = current), but share the everyday `set`/`unset` entry. `wl
goal` / `wl recap` additionally auto-target today's day node for the goal / summary.

> Known limitation: the two reserved keys `goal`/`summary` route to the log store, so a *prop*
> literally named `goal`/`summary` can't be set through `wl set` (the key routes to the log).
> Acceptable for now (nobody needs a prop with those names); a future fix would be a
> `--prop`/`--meta` disambiguator flag on `set`, or `wl prop set` (which is unambiguous) as
> the escape hatch.

**`wl alias add/ls/rm`** manages `~/.config/worklog/aliases.ini` (maps a short name to a
command, e.g. `wl d` == `wl day`). A target may **carry arguments** — `w = day -t work` makes
`wl w` == `wl day -t work` — using the git-alias model: `_expand_user_alias` splices the
shlex-tokenized target onto argv at the subcommand position *before* parsing (so args you type
after the alias are appended), running only at the top level (prog `wl`) so an alias name reaching
a subparser as an argument isn't expanded; chains (`ww = w`) resolve with a depth/cycle guard.
The alias is also registered under its target's first word as an argparse alias, so `wl <alias>
-h` and shell completion know it. The target's first word must be a real command and an alias may
not shadow one. Aliases are wired in at startup, so a change takes effect on the next invocation.

**Default-verb dispatch (collision entities).** When the group name equals the old leaf
command (`link` / `tag` / `log` / `sched`), a custom parser (`_WlParser` /
`_expand_default_verb`) inserts the group's *default verb* when the token after the entity
isn't a known verb — so the legacy leaf keeps working: `wl link 42 doc` → `wl link add 42
doc`, `wl tag 42 +work` → `wl tag add 42 +work`, `wl log 42 "body"` → `wl log add 42
"body"`, `wl sched 42 2026-06-15` → `wl sched add 42 2026-06-15`, while `wl sched ls 42` /
`wl sched -h` are left alone. The leaf's first positional is always an int id, never a verb
word, so the test is unambiguous. For `tag` the `add` default verb keeps the full `+add` /
`-remove` / bare-add / empty-list grammar (`cmd_tag`); `tag ls` / `tag rm` are
single-purpose convenience verbs. For `log`, `log edit` / `log rm` reuse the `relog` /
`unlog` handlers (which keep their shortcuts), and `log ls` is a node-scoped lister (the
full filterable stream view stays at `wl logs`). For `sched`, the `add` default verb keeps
the full when / `--recur` / `--clear` / list-when-empty grammar (`cmd_sched`); `sched ls`
lists and `sched rm` clears, while `wl defer` (status=LATER + rough hint) stays its own
composite command.

A note for the completion generators: a default-verb group's leaf args (`sched`'s
`--recur` / `when`, `log`'s `--date`) live under the nested `add` subparser, so the
per-subcommand walk (which skips `_SubParsersAction`) must also descend into the
default-verb leaf (`_default_verb_leaf`) and emit those completions under the bare group
condition — otherwise `wl sched <id> --recur<TAB>` silently loses its suggestions.

The one **clean group** (no collision, so no default verb) is **`date`** (the `date_meta`
table): `date set / ls / rm / import`, with `wl dateinfo` as the polymorphic everyday
shortcut (sets when given a label, lists when not, `--clear` / `--import` variants).

The `<entity> <verb>` reshape is now **complete** for every entity.

**B. Composite helper** — a one-step shortcut that wraps one or more primitives for
the common path; never the *only* way to do something (the primitive stays). Status
verbs `done` / `cancel` / `reopen` / `wait` (set `node.status` + side effects),
`defer` (sched + LATER), `start` / `stop` (clock + DOING), `tick` (habit check-in
metric + done), and the compound `add --log/--done/--at/--link/--sched/--metric`
(add + several primitives in one transaction).

**C. View** — read-only cross-entity rendering, no single-row CRUD: `ls` / `tree` /
`day` / `agenda` / `projects` / `changes` / `summary` / `focus` / `ancestors` /
`descendants` / `find` / `logs` / `show`. These share the unified filter (§ shared
`--tag/--para/--status/--priority/--prop`), time-window (§8) and `--by` (§9) conventions. `--prop`
is repeatable (AND) and honors the namespace convention — exact `K=V` (matches a member of a
comma-joined value), key existence `K`, and `GROUP.` / `GROUP.*` prefix (see §2 / `wl help prop`).

System commands (`migrate` / `config` / `init` / `import` / `apply` / `themes` /
`print-completion`) sit outside the three buckets.

### 1.2 Help system (battery-included + shortcut ↔ canonical cross-reference)

Every command's `--help` is self-sufficient (§35 "battery-included"): a one-line intro,
a few scenario examples, and a "Differences from related commands" block. On top of that,
**wherever a shortcut and its canonical entity-verb both exist, both helps must name the
relationship — this is mandatory, never omitted**, so a reader landing on either form
learns the other:

- The **canonical entity-verb** help (e.g. `wl node add -h`) states it's also reachable as
  the shortcut (`also: wl add`).
- The **shortcut** help (e.g. `wl add -h`) states it's the shortcut for the canonical form
  (`canonical: wl node add`).
- The **entity group** help (e.g. `wl node -h`) carries a dedicated **Shortcuts** section
  mapping each shortcut-able verb to its top-level name (`add → wl add`, `ls → wl ls`, …).

This applies to every entity as the `<entity> <verb>` + shortcut rollout proceeds, so the
two ways to invoke the same operation never drift in discoverability, only in keystrokes.

## 2. Data model (`src/worklog/migrations/`)

A single `node` table carries everything; classification is the orthogonal `type.*` prop namespace (no dedicated column); `parent_id` self-reference builds the tree. The schema is delivered as numbered SQL migrations under `src/worklog/migrations/NNNN_*.sql`; `PRAGMA user_version` tracks the highest applied migration. `ensure_db()` auto-applies pending migrations on every command; `wl migrate` is the explicit form. Before applying, `run_migrations` snapshots an **existing** DB (`user_version` > 0) to a same-dir `<db>.pre-v<N>.bak` so a bad migration is recoverable (a fresh init at v0 has no data to protect, so it's skipped); a backup that can't be written aborts the migration rather than risking unrecoverable loss. See `src/worklog/migrations/0001_initial_schema.sql` for the initial layout and `README.md` for the high-level picture.

> **Migration-authoring rule**: the runner wraps each file in one `BEGIN/COMMIT` (so a mid-script failure rolls the whole file back). Migration files must therefore **not** contain their own `BEGIN`/`COMMIT`.

- **classification values** (orthogonal `type.*` props, no single column): `type.para` = PARA role `area / project / task`; `type.date` = time level `lifetime / decade / year / quarter / month / week / day`; `type.habit`; `type.meetlog`; custom `type.<x>` (extensible, but a new type should have a clear place in tree / projects / summary classification). A node may hold several at once; a single representative token is derived by `node_type` / `node_type_from_props` (precedence para > date > habit > meetlog > custom > task)
- **status only applies to task / habit / meetlog**; time-hierarchy / area / project nodes leave status NULL
- Tables: `node / tag / log / metric / clock / prop / link / sched / date_meta` + derived view `v_node_path`
- **`node → log → metric` spine** (the log-centric core): a `node` has many `log`s; a `log` (carrying a `tag` — `note`/`goal`/`summary`/`metric`(carrier)/… , NULL = plain note) has 0..N `metric`s. A `metric` is a structured datapoint (`tag` = what it is e.g. `glucose`/`pullups`/`checkin`, or `goal` = a goal log's priority-ordered target node ids; `value_num`/`value_text`/`unit`/`note`/`at`). **`tag` is the uniform classification field across all three** — node (a multi-value label set), log (its role, single-value), metric (its type, single-value); same word, different scopes, SQL-unambiguous. A metric **must hang off a log** (`metric.log_id` NOT NULL) — so every datapoint has a log carrier; a `CHECK` requires a value (pure markers store `value_num=1`). `metric.node_id` is denormalized for join-free per-node queries (no FK; triggers keep it equal to the carrier log's node). CRUD surface: `wl metric add/ls/edit/rm` (`add` without `--on-log` creates a carrier log; a value-less marker is stored as `value_num=1`); `--metric` on `wl log`/`wl add` and `metrics` in `wl import` attach datapoints inline. Habit "done today" = a `tag=checkin` metric that day (written by `wl tick`/`wl checkin`), not "any log exists".
- **Design rule — keep `log` thin; new per-log data goes on a `metric`, not a new log column.** The `log` table stays a small fixed shape (`node_id`/`logged_at`/`body`/`tag`). When some feature wants to attach structured data to a log event (a measurement, an id, a category, a flag), the answer is a `metric` hanging off that log — never a widened `log` row. A metric is already typed (`tag` + `value_num`/`value_text`/`unit`/`note`/`at`), queryable (`wl metric ls --tag …`), and append-only, so it carries arbitrary per-event fields without bloating every log. Example: `wl agent --record` records a bind as **two metrics on one carrier log** — `agent_session` (`value_text` = full sid) and `agent` (`value_text` = the runtime name — claude / cursor / codex, from `$WL_AGENT` / `--agent`) — not as `log.session_id` / `log.agent` columns. Each value is its own queryable datapoint (`wl metric ls <id> --tag agent_session` / `--tag agent`). This keeps the log body human-readable and the schema stable (G3). The live pointer is the `agent_session.<agent>` prop (the key suffix carries the agent too); session lookups match `key LIKE 'agent_session.%'` since the sid alone is unique. (The metric tag string `agent_session` mirrors the prop namespace; the Python constant is `_SESSION_METRIC_TAG`.)
- **Decision rule — where does a fact go: `prop` vs `metric` vs `log`?** Sort by *"will I filter / group / aggregate over it across nodes?"* and by *cardinality*:
  - **`prop`** — a **single-value query dimension** (`PRIMARY KEY (node_id, key)` → one value per key, overwrite-in-place). Use it for the few attributes you slice the whole tree by: `owner`, `project`, `linear-id`, or **the one identifying ref a task maps to** (the release it shipped in → `release=v0.7.0`, so "which tasks shipped in v0.7.0" is one `prop` query; the single PR a one-shot task *is*). Props are scarce and high-value — don't flood the key-space with process noise.
  - **`log`** — the **human process record**. A dev task's many intermediate commits are process narrative; they belong in log bodies, not as filterable attributes.
  - **`metric`** — when you want those process records **structured / queryable per node** (a series), hang them off the log: `tag=commit value=<hash>` queried with `wl metric ls <id> --tag commit`. Many-per-node, append-only, time-stamped — process-grade, **not** a cross-node filter dimension.
  - Worked example (commit / PR / release ↔ task): the **many** intermediate commits of a dev task → `log` (add a `commit` **metric** if you want them structured); a task that maps to **one** identifying PR/commit, or its **release** → `prop` (so it's filterable/countable). The dividing question is value-to-query, not "can it physically fit".
  - **Namespaced prop keys (`group.member`)** — a prop key may use a dot to group related single-value props under a shared prefix (`agent_session.claude` / `agent_session.cursor`; external ids per system `ext.linear` / `ext.github`). Each full key is still one single-value prop (`PRIMARY KEY (node_id, key)`); the namespace groups *sibling* keys — it does **not** make a key multi-valued. The convention's payoff is **prefix lookup**: one `key LIKE 'group.%'` finds every member across nodes, so prop-based features (filter / summary / stats) operate on a whole namespace, not just an exact key. **All prop query surfaces must honor it**: exact key (`k=v`), key existence (`k`), and namespace prefix (`group.` / `group.*`). Reserve it for a logical dimension with several named slots; keep flat keys (`owner`, `release`) flat. First user: `wl agent` (the live binding is the `agent_session.<agent>` prop; sid lookups match `key LIKE 'agent_session.%'`).
  - **Task↔task relations are `relation.*` props (`wl relation`)** — derivation / association *across* the tree (this task was split out of / into / relates to that one), distinct from **ancestors** (the parent/child hierarchy). Stored as namespaced props `relation.split_from` / `relation.split_into` / `relation.related`, each a **comma-separated id list** (a relation is naturally many-per-node, so the value is multi-valued — the one place a prop value holds a list rather than a single scalar). `split-from` ↔ `split-into` are inverses; `related` is symmetric. `wl relation` writes **both sides** (so either node's `wl show` displays it) and the resolved view *also* derives the reverse from other nodes' props, so a hand-set one-sided value still reads bidirectionally. `wl show` renders a dedicated `relations:` block (and `-o json` a resolved `relations` object); the raw `relation.*` props are hidden from the generic props block to avoid double display. Why a prop and not a `link`-style table: it rides the existing namespace + `wl ls --prop relation.` query machinery for free (G3) — no new table or join.
  - **A goal's structured targets are `goal` metrics on the goal log** — a `goal` log (goal/summary are `log` rows with a reserved `tag`) can carry, supplied **explicitly at write time** (`wl goal "ship X" 12 34` / `wl goal set <node> "..." 7 9 3`), the task/project node ids it aims to deliver — stored as one `goal` metric per id on the log (`metric.tag='goal'`, `value_num=node id`, **insertion order = priority**, any count). wl **never parses them from the prose** (text extraction is too fragile); omit them and the goal is plain free text. The metric tag is the bare word `goal` (same as the carrier log's tag) — no per-level suffix, because the carrier log's node type (day/week/month/year) already says the level. So the plan↔node link is **queryable** off the metric table (`tag='goal'`, `value_num=<id>` = which goals target a node; narrow by node type for the level) rather than buried in prose, and history-preserving (each goal edit appends a log with its own target snapshot; `_log_goals` reads the latest). Reuses the existing `metric` entity — no new table.
- **Deliberate design — `wl add` / `wl log` stamp the current local time in their success line** (`✓ #1 task '…'  @2026-06-09 20:09`). Intentional, primarily an **AI affordance**: an assistant's sense of "today" drifts to the date its session started and goes stale over a long session, so it mis-dates backfills and reasoning. Echoing the real wall-clock on every content-creating command re-anchors the caller's clock to *now*. (`local_now()`, minute precision.)
- **Reserved-tag logs are history-preserving**: `goal` (forward, any time level) and `summary` (backward recap) are `log.tag` logs (latest = current; each edit appends), with their own `wl goal set/ls/rm` CRUD group (`--summary` flag targets the summary); also written by bare `wl goal`/`wl recap` (today-auto) and the key-routed `wl set <node> <key>` shortcut. A goal is the **same `goal` tag at every level** — the node's type (day/week/month/year) is the level; the former week `overview` and month `top5` collapsed into it (migration 0010). (`prop` is back to truly-static single-value attributes — a separate store.) In the `wl day` header the day's goal + recap plus the ancestor week's & month's goal each render with a distinct marker (`_DAY_MARKERS`: 🎯 goal · 📝 recap · 📅 this week · ⭐ this month) through `_header_blockquote`, which prefixes only the FIRST line with `> ` and aligns every continuation (soft-wrap / embedded newline) with plain spaces under the `> ` content column (cleaner than repeating `> `), wrapping by display width like `_node_line`. Each shown goal then lists its structured target nodes below it — numbered, status-marked (`_emit_goal_targets`, shared by `wl day` / `wl goal` / `wl goal ls` so they render identically) — and a ` [done/total]` achievement tag (`_goal_progress`/`_goal_counts`: counts the structured targets' DONE/CANCELED, falling back to prose `#id`/`WL#id` refs when a goal has no structured targets; ✅/🟡/⬜).
- **`clock` is structured time tracking**: `clock(node_id, start_at, end_at, elapsed_sec)`, written by `wl start`/`stop`/`spent`/`wait` — replaces the old `CLOCK_IN`/`CLOCK_OUT` log-body convention. Durations are summed from `elapsed_sec`, not parsed from text.
- **Two parallel trees, both hung under lifetime**:
  - **Responsibility line**: `lifetime → area → project → task` (PARA model: area is a cross-time responsibility domain, projects belong to areas, tasks belong to projects)
  - **Time line**: `lifetime → year → quarter → month → week → day` (carries the time skeleton + reserved-tag logs: each level's `goal`, the day's `summary` recap)
    - The recap (`summary` log) carries its own `logged_at` = write time. `wl day` shows "(written ...)" and, if there are later plain-note logs that day (`tag IS NULL`), warns "⚠ N changes after recap, consider rewriting". (Replaces the old `summary_at` prop.)
  - **Projects no longer hang under months** (legacy design did; migrated to areas). Day/month/week views derive from **log's `logged_at`** (time dimension) and **type/tag/ancestor chain** (domain dimension) — so moving projects under areas does not affect any per-day or per-project view.

## 3. State machine (#+TODO style)

```
TODO / DOING / LATER / WAIT  (open)
DONE / DEFERRED / CANCELED   (closed)
```

- `wl done` → DONE + auto-writes `closed_at`
- `wl defer <id> <date>` → LATER + `scheduled_at`
- `wl start/stop` → DOING + CLOCK events (see §7)
- **Status display group order is fixed**: `In Progress (DOING) → To-do (TODO) → Later (LATER) → Waiting (WAIT)`. In Progress always first (most attention-worthy). Any place listing open items (summary etc.) follows this order.

## 4. Marker symbols (`_status_marker`)

| status | marker |
|---|---|
| None / TODO | `[ ]` |
| DOING | `[/]` |
| LATER / DEFERRED | `[>]` |
| WAIT | `[?]` |
| DONE | `[x]` (`✓` in summary completion lists) |
| CANCELED | `[-]` |

Single source `_status_marker(status)` — no hard-coding anywhere else.

## 5. Priority

- Three levels `A / B / C` (correspond to worklog `P0 / P1 / P2`)
- Render uniformly as `[#A]` / `[#B]` / `[#C]`; no priority shows `[ ]` (node line) or blank
- Sort key: `(priority or "Z", id)` — no-priority sorts last

## 6. Node line rendering (uniform format)

Anywhere "list a node" — use the uniform line format (`_fmt_node` / summary's `_line`):

```
<marker> [#<pri>] #<id> <title>[ ·planned][ ⏱<N>min]
```

- `·planned` tag-prefix: when the node has the `planned` tag
- `⏱<N>min`: CLOCK time accumulated on the node (`_node_clock_min`), shown when > 0
- Completion lists use `✓` in place of the marker
- **The only renderer is `_node_line`**, which threads through `_c` for coloring (see §19). Any place listing nodes reuses it and gets highlighting for free; do not hand-roll the string elsewhere.
- **The priority slot always shows a 4-column marker via `_pri_marker` (single source).** Set → `[#A]`/`[#B]`/`[#C]` (priority-colored); **unset → a muted `[# ]`**, never blank and never `[ ]`. Blank spaces (the old placeholder) were 3 columns — one short of `[#A]` — so unset rows mis-aligned; `[ ]` collides with the TODO status marker and reads as a checkbox. `[# ]` keeps every row's columns aligned and says "priority not set" unambiguously. Every list/header that shows priority (`ls`/`day`/`tree`/`projects`/`summary`/`focus`/`show`/running-clock/check-in) routes through `_pri_marker` — do not re-derive the blank/`[ ]` form anywhere.

## 7. Timeline / changes / CLOCK (`wl show`)

A node's "what happened" = created / scheduled / closed / individual logs, merged by time:

| event | marker |
|---|---|
| created_at | `● created` |
| scheduled_at | `◷ scheduled` |
| closed_at | `✓ <status>` |
| CLOCK_IN log | `⏱ clock-in` |
| CLOCK_OUT log | `⏱ clock-out (Nmin)` |
| ordinary log | `✎ log <body>` |

- `wl start` writes a log with `body=CLOCK_IN`; `wl stop` writes `CLOCK_OUT elapsed=Nmin (from ...)`
- CLOCK_* logs are not "progress logs" (filtered out by changes / summary)
- Time accumulation = sum of all `elapsed=Nmin` from CLOCK_OUT logs (`_node_clock_min`)

## 8. Time-window flags (unified, `_resolve_window`)

All "over a time range" commands (changes / summary, future `logs` etc.) share this set; **commands must not parse window flags themselves**:

| flag | meaning |
|---|---|
| `--since YYYY-MM-DD` | start (default: current Monday) |
| `--until YYYY-MM-DD` | end (default: today) |
| `--week YYYY-Www` | ISO week (overrides since/until) |
| `--month YYYY-MM` | full month (overrides since/until) |

Priority: `week > month > since/until > current-Monday~today`. Single source `_resolve_window(args)` returns `(since, until)` as two `YYYY-MM-DD` strings.

Range judgement is uniform: `since <= ts[:10] <= until` (date strings sort lexicographically = chronologically).

## 9. `--by` aggregation dimension (unified design language)

Both `tree` and `summary` support `--by`; future aggregation commands follow the same:

- `tree --by project/tag/direction`: flat 2-level regrouping, avoids time-dim's deep nesting
- `summary --by project/day`: default `project`
- **Semantics**: pick a dimension, regroup nodes; each group is a `▸` header + node list
- **Unbucketed**: when aggregating by project, nodes not in any project go to a tail `▸ (no project)` bucket

### 9.1 `wl tree` default behavior (anti-blast)

Fully expanding both trees would print thousands of lines. **Bare `wl tree` (no --root/--para/--depth/--by) is a dedicated overview view `_print_default_tree`**:

- **Time line expanded to today**: year → quarter → month → week → today (only the path to today, no other months/weeks/days), and today's day node lists **only tasks with a log today** (task/habit/meetlog, no log body). You see the current month yet stay focused on today.
- **Areas are one level only**: 7 areas show only area names, no project expansion.
- Typical ~30 lines (depending on today's task count).
- **Drill-down**: `wl tree --root <area>` (area's projects + tasks, default depth 3) / `wl tree --root <week/month>` (per-day activity) / `wl tree --root <day> --depth large` (every log of that day) / `wl tree --depth N` (full expand from lifetime, generic).

`_print_tree` rules:
- **Depth cap**: with `--root`, default 3; without root (`--depth` explicit), as given; `_print_tree(max_depth)` truncates beyond
- **Time nodes sort by date** (`_tree_children`): type ∈ time-levels sort by `title` (date) ascending; others by priority → id (otherwise W22 sorts before W18)
- **Day node expansion = day's activity** (`_print_day_activity`): after areas changeover, day has no real children — instead expand "tasks with logs that day + only that day's logs" (log-driven, same source as `wl day`). **Tasks should not hang under day** (hang under project); day content is derived from log dates.

## 10. Project ↔ task association (`_project_members`)

A task is "in" a project if any of:

1. **Structural child**: `task.parent_id == project.id`
2. **Shared semantic tag**: task and project share at least one non-generic tag

⚠️ Known caveat: shared-tag association is ambiguous (one `gaming` tag matches multiple gaming projects → tasks show up under multiple). **Future precision direction**: add `prop["project"]` for explicit single ownership; `_project_members` prefers prop, falls back to tag. Changing this helper affects `tree --by project` / `projects` / `changes` / `summary` — verify all four together.

## 11. Generic dimension tags (`GENERIC_TAGS`)

```
work personal planned unplanned P0 P1 P2 habit meeting followup
dev ai sync strategy reflection reading family health morning_check slack_scan
```

- These are "dimension/attribute" tags, not "project/topic" tags
- `--by tag` grouping, `focus --related`, `_project_members` shared-tag judgement all exclude them to avoid noise
- Add new generic dimension tags → extend the `GENERIC_TAGS` set

## 12. Shared helpers (do not re-implement)

| helper | purpose |
|---|---|
| `_status_marker(status)` | status → marker symbol |
| `_fmt_node(n, indent)` | node line render (tree --by etc.) |
| `_resolve_window(args)` | time window → (since, until) |
| `_project_members(con, pid)` | project's associated node id set |
| `_node_clock_min(con, nid)` | CLOCK time accumulated, minutes |
| `_has_tag(con, nid, tag)` | does the node have a given tag |
| `_ancestors_chain(con, nid)` | root → node ancestor chain |
| `_collect_descendants(con, rid)` | all descendant ids |

Reuse these when a new command needs the functionality; do not write another copy.

## 13. Vault association (`link` table)

- The execution system (wl DB) and the knowledge system (an Obsidian vault) are **decoupled**: the DB knows which vault docs a node references (the `link` table stores doc names, no `.md`), but doesn't reverse-sync vault content
- `wl link <id> <vault_doc>` adds an association
- Future `wl xref <doc>` reverse-queries "which nodes reference a given doc"

## 14. CLI output style

- Use `✓` prefix for success, `✗` for errors
- Group headers `▸`, sub-items indented 2 / 4 spaces
- Restrained emoji use: `📊` summary header / `📅` changes header / `⏱` time / `●◷✓✎` timeline events / `▸·` grouping
- Highlighting / colors / `--color` / `--theme` — see §19; outputs go through `out()`, not direct `print()`

## 15. Test conventions

- One `Test<Cmd>` class per command, in the matching `tests/test_<area>.py`
- `conftest.py`'s `cli` fixture: per-test isolated tempdir DB (`WORKLOG_DB` env + `tmp_path`), `run_cli` captures stdout/stderr/exit_code
- New commands must cover: happy path + edges (missing id / empty DB / filter no-match)
- If convention changes invalidate an assertion, update the assertion synchronously — do not leave it red

## 16. Engineering conventions

- **Runtime deps: just `rich` (optional enhancement)**: core logic uses only Python stdlib (sqlite3 + argparse); `rich` is only for highlighting (§19), and a `try import rich` at the top of `src/worklog/cli.py` falls back to plain text on failure (`_RICH_AVAIL=False`). `pytest` is test-time only.
- Single-package `src/worklog/`; `cli.py` holds the implementation, `migrations/NNNN_*.sql` the schema, `__init__.py` the version. Split `cli.py` into submodules only if it grows too large.
- Shell completion is **auto-generated** via `wl print-completion {fish,bash,zsh}` walking the argparse tree — no hand-maintained `completions/wl.fish` file. Adding a subcommand/flag → completion updates automatically. For dynamic completion (node id / tag), register the new positional/option in `_FISH_POSITIONAL_NODE` / `_FISH_HELPERS` / `_BASH_DYN_HELPERS`.
- Every command addition: implementation + tests + completion + this document (if convention is affected), all in one go
- Push via `make ship` (only pushes after tests pass)

## 17. Bulk `import` (the AI collaboration main entry)

`wl import [file|-] [--dry-run]` — let an AI load many nodes in one shot, replacing dozens of commands. JSON single document:

```json
{
  "add": [
    { "ref": "p1", "title": "...", "priority": "A",
      "tags": [...], "props": {"type.para": "project"}, "links": [...], "logs": [...],
      "children": [ {... nested child nodes ...} ] },
    { "title": "...", "parent_ref": "p1" }
  ],
  "update": [
    { "id": 45, "status": "DONE", "parent": 6, "add_tags": [...], "remove_tags": [...], "add_logs": [...], "add_links": [...] }
  ]
}
```

Conventions:

- **Classification = `props` with `type.*` keys**: `{"type.para":"project"}` (or `area`/`task`), `{"type.date":"day"}` (week/month/…), `{"type.habit":"true"}`, `{"type.meetlog":"true"}`; a bare node (no `type.*`) is a plain task. A `type.date` prop auto-completes its `date.period`/span from the title.
- **Two parent-child notations coexist**: `children` nesting (parent id auto-propagates to children — natural for time-hierarchy loads) + `ref`/`parent_ref` (in-batch temporary references — flat cross-section loads). `ref` must be defined before use.
- **`status=DONE` auto-writes `closed_at`** (in both add and update)
- **Update-able fields**: `status` / `priority` / `title` / `scheduled_at` / `deadline_at` / `body` / `parent` (move, validates existence) + `add_tags` / `remove_tags` / `add_logs` / `add_links`. ⚠️ Keys not in the whitelist are silently ignored — `parent` was once missing from the whitelist and caused moves to silently fail (later fixed); when adding fields, sync the whitelist.
- **Single transaction**: any node failure (missing title / undefined `parent_ref` / nonexistent update target) → full rollback, no partial state
- **`--dry-run`**: parse + summarize + list refs, write nothing
- **AI usage**: parse a worklog / one day's data into this JSON, `echo '...' | wl import -` or `wl import day.json`
- A future markdown importer = parse worklog markdown → produce this JSON → import

When adding import fields (new node attribute / update operation), keep `_import_node` / `_import_update` in sync with this section.

## 18. wl-diff format (`apply`, symmetric with wl output)

`wl apply [file|-] [--dry-run]` — input format = wl node line + diff prefix, sharing the same visual language as `tree` / `ls` / `day` output. "What you see is what you change."

**Prefixes**: `+` add / `~` update / `-` delete / ` ` (space) context anchor (locate existing node as parent, do not modify). Every 2-space indent = one tree level (parent-child, same as `tree`).

### 18.1 Add `+` / delete `-` / anchor ` `: the node line

`<prefix><indent>[marker] [#pri] #id title :tags:` (marker required; `+` has no `#id`; `-` / anchor must have `#id`).
Rich-field sub-lines: `@log <text>` / `@link <doc>` / `@prop k=v` (attached to the preceding `+` / anchor node). Classification is set via `@prop type.*` (e.g. `@prop type.para=project`), not a token on the node line.
marker → status: `[ ]`=TODO, `[x]`=DONE, `[/]`=DOING, `[>]`=LATER, `[?]`=WAIT, `[-]`=CANCELED.

```
  #2 [project] Aggregation        ← anchor: locate #2 as parent, don't modify
+   [x] [#A] login fix :gaming:P0:
+     @log root cause: configmap pointed to wrong bucket
- #44 glucose log                  ← delete #44 (recurses into subtree)
```

> ⚠️ **Delete `-` must recurse into the subtree**: node self-FK is `ON DELETE SET NULL` (to prevent accidental parent-cascade), so a bare parent delete only sets children's `parent_id` to NULL — leaving them **orphaned**, not deleted. `cmd_apply`'s `-` uses `_collect_descendants` to collect the whole subtree's ids and delete together (not relying on FK cascade). Without this, orphan tasks remained after rollbacks during a migration; covered by `test_apply_delete_cascades_subtree`.

### 18.2 Update `~`: lock line + explicit field operations (⚠️ safety first)

**Core principle: never touch a field unless it's explicitly declared.** Avoid using a node line for updates (risks unintended marker/title changes, risks wiping); instead use `~ #id` to lock + indented field-operation lines:

```
~ #14                  ← lock target (only #id); each line below is one explicit op
  status DONE          ← set
  priority A           ← set
  priority -           ← clear (value is -)
  title new title
  parent 6             ← move to under #6
  scheduled 2026-06-01
  +tag urgent          ← add tag
  -tag old             ← remove tag
  +log progress note   ← append log (only append)
  +link doc / -link doc
  prop owner=xyb     ← set prop
  -prop owner          ← remove prop
```

**Single-line shorthand** (for the three most common — status/priority/title, consistent with node list display):

```
~ [x] #14              ← only changes status=DONE (priority/title untouched)
~ [#A] #14             ← only changes priority (no marker = no status change)
~ #14 new title        ← only changes title
~ [x] [#A] #14 new t   ← all three
~ [x] #14              ← single-line + indented field ops can mix
  +tag urgent
```

Single-line shorthand semantics = **only the fields that appear in the line are changed** (marker → status / `[#X]` → priority / trailing text → title), same safety as standard field ops.

Conventions:

- **Only touch declared/appearing fields** — if priority isn't written under the lock line, do not touch priority; if a single-line shorthand has no marker, do not touch status; if no tag op, do not touch tags. This is the fundamental guard against "I gave id + name and other fields got wiped."
- **Settable fields** (status / priority / title / parent / scheduled / deadline): `field value` to set, `field -` to clear (title can't clear — NOT NULL)
- **Collection fields**: `+tag` / `-tag`, `+link` / `-link`, `+log` (append-only, no remove), `prop k=v` / `-prop k`
- **`status=DONE` auto-writes `closed_at`**
- `~ #id` requires at least one field op, or it errors

### 18.3 Shared conventions

- **Two phases**: validate everything first, abort on any error (write nothing) — `~` / `-` / anchor `#id` must exist; `+` has no `#id`; field op values are validated (priority∉ABC / illegal status / missing parent / unknown field name / empty title); `--dry-run` validates + lists plan then stops
- **Single transaction**: any failure during execution rolls back everything
- **Comments**: `#` followed by space or non-digit (`# explanation`); `#<digit>` is a node id and not a comment
- **Division of labor with `import` (JSON)**: `apply` for human/AI lightweight edits (state changes, add tasks, precise field ops); `import` for programmatic complex bulk loads (deep nesting, props)
- Future `tree --diff` / `day --diff` outputs editable format → edit → `wl apply` round-trip

When adding prefixes / field-op semantics, keep `_parse_wld` / `_parse_fieldop` / `_validate_fieldop` / `_exec_update` / `cmd_apply` in sync with this section.

## 19. Highlighting / theme (`rich`, optional)

Terminal highlighting goes through `rich`; can be turned off, can switch themes, default auto. The core is three things: global `_CONSOLE`, color helper `_c()`, and output function `out()`.

### 19.1 Switches and detection

- Global flags (before any subcommand): `--color {auto,always,never}` + `--theme {auto,dark,light,mono}` (theme choices = `["auto"] + list(THEMES)`; new palettes are auto-added to choices) + `--width {full,help,N}` / `$WORKLOG_WIDTH` (output width cap: `full` = fill terminal [default], `help` = the `--help` cap `HELP_MAX_WIDTH`, `N` = N columns). Resolved by `_resolve_width_cap` → `_set_width_cap`; `_term_width()` returns `min(terminal, cap)`, so all width-sensitive rendering (`_truncate_log_body`, title fits) respects it from one lever.
- `--title {wrap,clip}` / `$WORKLOG_TITLE` — how a node title too wide for the line renders in `_node_line`: `wrap` (default) folds onto multiple lines, continuation lines **hang-indented** to the title column (`prefix_cols` = display width of the plain `indent+marker+pri+id+type` prefix), so a long title never breaks the tree/list indentation; `clip` keeps one line, truncating with `…` (same budget as `_truncate_log_body`). Resolved by `_resolve_title_mode` → `_set_title_mode`; the wrap itself is `_wrap_display(text, cols)` — display-width-aware (East-Asian), breaks at spaces and hard-breaks a token (e.g. a spaceless CJK run) wider than the line. Anything but `clip` resolves to `wrap`, so the safe default always wins.
- `-o/--output {text,json}` — the **structured-output** surface, a shared `output_parent` parser on the commands that serialize: `show` / `ls` / `logs` / `projects` / `day` / `tree` / `summary` (a command without it rejects `-o` rather than silently printing text). `text` (default) is the rich rendering; `json` prints via `json.dumps(ensure_ascii=False, indent=2)` (plain `print`, never `out()` — machine output isn't styled/wrapped). `show` → `_node_to_dict` (full node + relations: tags / ancestors / props / links / schedule / children / logs / metrics / clock, no elision; single id → object, several → array). `ls` → array of `_node_summary_dict` (compact: identity + filter/sort fields + tags; filters apply, the display 20-cap is skipped, explicit `--limit`/`--top` honored). `logs` → array of log rows. `projects` → array of project summaries + `counts` (done/doing/pending/total) + `latest_activity`. `day` → `{date, weekday, nature, node_id, goal, goal_targets ([{id,title,status}], the goal's structured target nodes in priority order), goal_progress, summary, summary_at, week_goal, month_goal (the day node's reserved-tag logs, flattened in — only present keys), tasks[{…, logs, planned, clock_min}], clock_min_total}`. `tree` → the nested structural subtree (`--root` or top-level forest, each node + `children[]`, depth-bounded; ignores the text `--by`/filter sugar). `summary` → `{since, until, totals, by_direction, done[], pending[]}`. Empty result → `[]`. Field names mirror the DB columns (stable as an API contract); `*_at` are UTC instants verbatim, `*_date` local calendar days. Models the k8s `-o json` / `gh --json` pattern: let an AI/script pull an exact field instead of parsing the text view (G3).
- Env fallback: `$WORKLOG_COLOR` (same values as `--color`), `$WORKLOG_THEME`, `$NO_COLOR` (standard, any value disables color)
- `--color auto` (default) decides: `rich available AND stdout.isatty() AND no $NO_COLOR` → enabled. So pipes / redirects / test StringIO auto-downgrade to plain text, **tests need no special handling.**
- `_init_console(color, theme)` is called once in `main()` after parsing args, sets the global `_CONSOLE` (`None` = plain text / `rich.Console` = highlighting); `--color always` uses `force_terminal=True` to force ANSI even through pipes (for `less -R`).

### 19.2 Theme = semantic-name → style (no "default" palette)

**There is no palette named `default`** — `default` was once a fourth standalone palette and had confused semantics (neither dark nor light). Now there are three real palettes + one `auto` selector.

`THEMES` dict: each palette maps semantic names to rich styles. Semantic names (not color names), full set in `_THEME_KEYS`:
`done/doing/later/wait/todo/canceled` (status), `pri_a/pri_b/pri_c` (priority), `id/type/tag/hit/header/meta/planned/clock`.

- `dark`: dark background, `bright_*` for contrast
- `light`: light background, deep saturated colors (`green4` / `red3` / `dark_orange3` / `dark_cyan` / `purple` / `grey42` …), avoiding bright colors that wash out on white
- `mono`: all `default` (rich's `default` style = terminal default foreground, no color), for "want rich layout but no color"
- **`auto` (default)**: not a palette but a selector — `_resolve_theme` probes terminal background and resolves to `dark`/`light`:
  - `_detect_bg_is_dark()`: first read `$COLORFGBG` (no I/O; tail is bg color code, 7/15 = light, otherwise dark); if missing, send an **OSC 11 query** (`\033]11;?\033\\`, requires both stdin + stdout TTY, 0.15s timeout, parses `rgb:rr/gg/bb` and computes perceived luminance `0.299R+0.587G+0.114B < 0.5` for dark)
  - dark → `dark`, light → `light`, **undetectable → fallback `dark`** (most terminals are dark)
  - Terminal without color capability (`TERM=dumb` etc.) — rich won't emit ANSI anyway, equivalent to mono.
- `_STATUS_STYLE` / `_PRI_STYLE` map DB status/priority values to theme semantic names
- Adding a palette: add a key to `THEMES` (must cover all of `_THEME_KEYS` — `test_themes_have_same_keys` guards this); color names must be rich-valid (`test_every_theme_color_name_valid` guards against `cyan4`-class invalid names)
- **`wl themes`** lists dark/light/mono, each rendering a sample line in its own palette + marks the current (auto-resolved) palette (`cmd_themes` creates a separate `force_terminal` Console for each; `--color never` / no rich falls back to plain-text list)

### 19.3 Coloring helper `_c(text, style=None)` — the single coloring entry

```python
out(_c("✓", "done") + " " + _c(f"#{id}", "id") + " " + _c(title))
```

- `_CONSOLE is None` (plain text): `_c` returns `str(text)` as-is, zero overhead
- Styled: `_c` **first escapes content via `rich.markup.escape`** (so titles containing `[x]` / `[[doc]]` aren't eaten as markup), then wraps with `[style]…[/style]`; `style=None` only escapes, no color wrap
- **Iron rule: any fragment going into `out()` that may contain `[` `]` (marker `[x]` / priority `[#A]` / type `[day]` / wikilink `[[doc]]` / timestamp `[ts]`) must pass through `_c`.** Throwing a raw bracket-containing string into `out()` triggers `rich.MarkupError` or silent eating.

### 19.4 `out()` replaces `print()`

- Node / group / detail output uses `out()`, not `print()` — so flipping the console takes effect everywhere
- `out()`: with `_CONSOLE`, calls `console.print` (renders markup); else `print`
- `_node_line` (§6, the one node renderer) is already fully `_c`-colored, so `ls/tree/day/projects/show/focus/find/summary` node lines **automatically** get color; new commands need only reuse `_node_line` to get highlighting for free
- Search-hit highlighting in two places, both via `_hl(text, q)` (styled: `hit` style; plain text: half-width `*…*`; no hit returns plain `_c`):
  - **In-line title hit**: `_node_line(con, n, hl=q)` — find result title hits highlight in place, no separate expansion
  - **Out-of-line field hit**: `_snippet(text, q)` (body/log/tag/prop/link) slices a snippet around the query then `_hl`, indented expansion
- Plain text hit markers use half-width `*…*` (was full-width `「」`, but full-width brackets are wide glyphs and look spaced-out)

When adding output / changing colors, keep `_init_console` / `_detect_bg_is_dark` / `_resolve_theme` / `THEMES` (+ `_THEME_KEYS`) / `_c` / `_hl` / `out` / `_node_line` / `cmd_themes` in sync.

## 20. Scheduled time (precise + fuzzy)

`scheduled_at` stores both precise dates and **fuzzy granularity** — many things have a "roughly when" answer (next week / next month), not always a precise date.

- **No schema change**: `scheduled_at` remains a TEXT column with a normalized string value
- **Supported normalized values** (granularity fine → coarse): `YYYY-MM-DD` (day) / `YYYY-Www` (week) / `YYYY-MM` (month) / `YYYY-Qn` (quarter) / `YYYY` (year) / `someday`
- **Input entry unified through `_norm_sched(s)`** (add / defer / import / apply ~ all routes):
  - Canonical formats are validated directly (illegal date/month/week raises `ValueError`, caught at dry-run, no bad data lands)
  - Relative words normalize: `today` → date, `tomorrow` → date, `next-week` → `YYYY-Www`, `next-month` → `YYYY-MM`, `next-quarter` → `YYYY-Qn`, `someday` → `someday`
  - Unrecognized → `ValueError` with valid-format hint (no silent swallow)
- **Granularity `_sched_level(s)`** / **sort `_sched_sort_key(s)`**: sort key = `(anchor start date, granularity rank)`; fuzzy values anchor to start of their time slot (`2026-06` → `2026-06-01`, `2026-Q3` → `2026-07-01`); `someday` anchors far future (sorts last); same anchor → finer granularity first
- **Display `_sched_display(s)`**: precise date this year shows `MM-DD`; fuzzy shows as-is (`2026-06` / `2026-Q3` / `someday`); `_node_line(sched=True)` renders `@<display>` (planned style highlight; prefix `@` not emoji — 📅 is loud, `@` is light and separates well in plain-text mode); ls / day / summary turn `sched=True` on by default

When changing scheduled-time logic, keep `_norm_sched` / `_sched_level` / `_sched_anchor` / `_sched_sort_key` / `_sched_display` / `cmd_add` / `cmd_defer` / `_import_node` / `_validate_fieldop` / `_exec_update` / `_node_line` in sync.

## 21. Log historical dates (for migration)

When migrating a historical worklog, a log's `logged_at` must land on **the day the event happened**, not the import day — otherwise timelines stack at the import date and distort. Unified through `_insert_log(con, nid, entry)`:

- entry forms: `dict{date, body}` / string prefix `"YYYY-MM-DD content"` (auto-extracts date as `logged_at`, body strips prefix) / plain body (uses today)
- Entry points all covered: `wl log --date` / import `logs` & `add_logs` / apply `+log` & `@log` — all go through `_insert_log`
- Illegal date is caught by `date.fromisoformat`
- `wl show` timeline sorts by `logged_at`, so historical dates land correctly

## 22. Day reproduction view `wl day` + `wl logs --group` (markdown day structure)

Goal: reproduce the day-dimension view of "all projects + progress on a given day" that the markdown `YYYY-MM worklog` produced.

- **`wl day [date]` is log-date driven**, no longer requires a day node to exist (historical data lists by day too): query logs with `date(logged_at)=target` (excluding `CLOCK_*` accounting rows, restricted to task/habit/meetlog), render as **bucket → secondary group → task → indented logs**, with footer stats "N tasks made progress + status distribution + CLOCK"
- **Bucket = `work`/`personal` tag** → `Work` / `Personal` / `Other` (`_node_bucket`), order in `_BUCKET_ORDER`
- **Secondary group `--by`** (`_sec_group` / `_sec_sort_key`):
  - `plan` (**`wl day` default** — closest to markdown day structure): scheduled that day (or migration-era `planned` tag) → `Planned`; everything else → `Unplanned`. (The old separate `Unplanned (untagged)` bucket was merged into `Unplanned` — now that planned/unplanned derives from sched, the tag distinction has no value and the label misled.)
  - `project`: project ancestor (`_node_project` picks the project ancestor, `type.para=project`). `wl logs --group day` still defaults to `project`
  - `priority`: `A/B/C → P0/P1/P2`, no priority → `—` (defaults to unplanned but flagged unconfirmed). ⚠️ **Planned/unplanned is fundamentally per-day (per-log)** (a task may be planned today, unplanned tomorrow); hanging the mark on the task itself is an approximation after merging migration. Precise modeling would push the mark down to log rows (schema not yet done, decision pending).
- **`wl logs --group day [--by ...]`** reuses `_render_day_group`: groups by date header, each day has the same structure as `wl day`
- **`wl logs` default time window**: when no `--id` / `--date` / `--since`, only the last `--days` (default 7) — prevents flooding (works even when data grows); `--since` / `--until` / `--date` explicitly override

When changing day/logs rendering or grouping, keep `_node_bucket` / `_node_project` / `_node_plan` / `_sec_group` / `_sec_sort_key` / `_render_day_group` / `cmd_day` / `cmd_logs` in sync.

## 23. Forward planning sched (schedule vs. log separation; planned/unplanned derived)

The decision model: **schedule (forward planning, calendar-like) and log (retrospective record) are fully separated; planned/unplanned is derived from schedule, not stored in logs**.

- **`sched` table**: `(id, node_id, on_date, rrule, created_at)`. `on_date` and `rrule` are mutually exclusive; a task can have multiple rows (multiple days, or one-off + recurring together).
  - `on_date`: one-off scheduling to a specific `YYYY-MM-DD`
  - `rrule`: recurrence rule, currently supports `daily` / `weekly:Mon,Wed` (`_norm_rrule` validates, weekdays use `Mon..Sun`). Complex RRULE (interval / specific day + time slot) is future work.
- **`_sched_fires(on_date, rrule, target)`**: does this row fire on `target`. `_scheduled_node_ids(con, target)` collects nodes hit on a given day.
- **`wl sched <id> [when] [--recur R] [--clear]`**: schedule / recur / clear / no-arg = list. `when` goes through `_resolve_concrete_date` (YYYY-MM-DD / today / tomorrow / day-after-tomorrow).
- **`wl day` derivation (`_node_plan(con, nid, sched_ids)`)**:
  - Planned = `nid in sched_ids` (hit by schedule, scheduled in advance) **or** migration-period `planned` tag
  - Unplanned = not in sched_ids and no `planned` tag (a logged-but-unscheduled task is unplanned; the former `unplanned` tag and the no-tag case are now one bucket)
  - **Tasks scheduled but not yet logged are also listed** (`wl day` merges sched-only nodes into items, marked `«planned · not yet done»`), implementing "plan visible in advance, log added when actually doing"
- **Division of labor with `scheduled_at` (§20)**: `scheduled_at` = fuzzy todo time (someday / 2026-06, backlog hint); `sched` = specific calendar placement (drives planned). The two complement each other and do not auto-sync.
- **Migration transition**: legacy data uses `planned`/`unplanned` tags for planned/unplanned (no sched), and `_node_plan` falls back to tags; future real scheduling goes through sched, gradually dropping the tags.

When changing sched / day derivation, keep `_sched_fires` / `_scheduled_node_ids` / `_node_plan` / `_sec_group` / `_render_day_group` / `cmd_day` / `cmd_logs(--group)` / `cmd_sched` in sync.

## 25. Help topic system (`wl help` — info-style)

Beyond per-command `--help` (§1.2), `wl help` is a standalone, **info-style topic browser**:
a repo-managed set of docs — one per command / concept / common-param / guide — navigable by
"see also" links. This keeps `--help` short (summary + a pointer) while giving each topic room
for a full explanation, and makes the help content **i18n-able and reviewable as plain files**.

### Docs live in the repo (packaged, i18n-ready)
- Location: `src/worklog/help/<lang>/<topic>.md`, shipped in the wheel like `migrations/`.
  `en` is the source language and the fallback; other langs mirror the same topic ids.
- One Markdown file per topic; topic id = filename stem (`node.md` → `wl help node`).
- Minimal frontmatter (no YAML dep), `key: value` lines between `---` fences:
  ```
  ---
  title: node — the single unit of everything
  category: concept        # concept | command | param | guide
  see_also: log, tag, status, add
  ---
  <markdown body>
  ```
- `index.md` is the root (`wl help` with no topic): the overview + topic list grouped by category.

### `wl help` behavior
- `wl help` → render `index.md` + list topics by category.
- `wl help <topic>` → title, body, then a "See also: …" footer resolved from `see_also`.
- Unknown topic → closest-match suggestions (never a stack trace).
- Language: `--lang` > `$WORKLOG_LANG` > `$LANG` prefix > `en`; a missing translation falls back
  to `en` per-topic, never a hard error.
- Rendering: a **dependency-free renderer** (`commands/help.py`) handles a small fixed Markdown
  subset — ATX headings, ```fenced``` code blocks, and inline `**bold**` / `*italic*` /
  `` `code` `` / `[text](url)` / bare URLs (no `_italic_` — underscores collide with identifiers;
  no Markdown engine, no `rich.markdown`). It **escapes literal text** before injecting style
  markup, so bodies with `[ ]` / `[x]` / `[/]` / `#L42` render safely (a raw `[/]` previously
  crashed rich); with color off the markers are simply stripped. The exact subset + frontmatter
  spec for topic authors lives in **CONTRIBUTING.md** ("Writing `wl help` topics").

### Relationship to `--help` (the slimming policy)
- `--help` stays the *quick reference at the point of use*: usage, one-line intro, a couple of
  examples, and — when a fuller topic exists — a closing `More: wl help <topic>`.
- `wl help <topic>` is the *teaching* layer: concepts, rationale, cross-links, the planning
  rhythm, PARA, etc. Content that would bloat `--help` lives here, keeping `--help` scannable.
- The top-level `wl -h` Concepts glossary stays a one-line teaser per concept, pointing to
  `wl help <concept>` for the full entry.

### `--help` rendering: color, Markdown, wrapping (shared with `wl help`)
- **Color is post-processed, not native.** argparse has no color before Py 3.14 (and 3.14's uses
  its own palette), so `colorize_help` injects ANSI into argparse's *already-formatted* output.
  The ANSI is zero-width, so argparse's column math is untouched. Color/theme are re-resolved from
  `--color`/`--theme`/env at format time, because `--help` fires inside `parse_args`, before
  `main()` builds the console. It's the same 3-tier scheme as `wl help` (dim body / bright-cyan
  references / bold-white headings).
- **`--help` may use the topic Markdown subset — but only in `epilog`/`description`.** Markers
  render with color on and are stripped with color off. `help=` one-line summaries stay **plain
  text**: argparse line-wraps them, which would split a Markdown span across lines. The point of
  Markdown here is explicit styling where a heuristic can't help — a command name like `set` /
  `log` / `show` collides with the English word, so it's backticked to mark it as code.
- **Wrapping lives in the formatter, not the post-processor.** A `RawDescriptionHelpFormatter`
  subclass overrides `_fill_text` (the hook argparse calls *only* for epilog/description) to wrap
  the raw epilog with a hanging indent; option/choice help is still wrapped by argparse's own
  `_split_lines`. Re-wrapping argparse's whole formatted output as a post-process was the wrong
  layer — it re-wraps already-wrapped rows and misaligns them. The formatter caps the width on
  wide terminals (`HELP_MAX_WIDTH`) and shares one `render.help_width()` with the `wl help`
  topic-body wrapper, so every help surface lines up to a single width.

### Rollout (incremental)
Land the loader + `wl help` command + a handful of seed topics first; then author one topic per
command / concept / param and trim each `--help` to summary + pointer. Topics land piecemeal —
a missing topic simply isn't offered; nothing breaks.

## 26. Data access layer (model classes + query decomposition)

The data layer is three tiers: `models.py` (one dataclass per table + CRUD) → handlers (`commands/`) → view DTOs (`commands/dtos.py`). `db_table.py` is the low-level SQL helper underneath the models (parameter binding, the `ALIVE` soft-delete predicate, identifier validation).

- **Model classes are Active-Record-lite.** One `@dataclass` per table mirrors its columns; **field names match column names exactly**. CRUD lives on shared bases so a concrete class is just `_table` + fields: `_Model` (query / query_one / count / exists / insert / delete / purge + `__getitem__`), the `_IdPK` mixin (get / gets / update for integer-id tables), the `_Upsertable` mixin (upsert for natural-key tables), with the table name in a `_table` class attr. `from_row` is one reflective builder (`cls(**{f.name: row[f.name] …})`) — a field with no matching column raises at construction, which *enforces* the field==column rule instead of leaving it a comment. Never add a display-only field to a model; that is what the `commands/dtos.py` view DTOs are for.
- **Reads always `SELECT *`** (from_row needs every column). A column projection or an aliased / JOIN / aggregate read goes straight to `db_table` — the deliberate escape hatch the full-row models can't express. Reads hide tombstones (`include_deleted=False` default); writes (`update` by id, `upsert`) see through them — an `upsert` revives a soft-deleted row. `_table` / `_upsert_key` live only on the non-dataclass mixins (untyped class attrs); an annotated `_foo: T` on a concrete dataclass would become a required field.
- **`gets(ids)` is the batch read** — a single query returning one slot per id (None for misses), preserving input order. Use it instead of an N+1 `get` loop.
- **Renderers take `Node`, not `sqlite3.Row`.** `_node_line` / `node_view` / `node_type` and the other node renderers receive `Node` objects. Every raw full-row node query converts at its boundary via `Node.from_row` (centralized inside `nodes_with_tag` for the tag path, so a new full-row caller can't forget the wrap), so a Row never reaches a renderer. The `_Model.__getitem__` shim lets a Node be subscripted by column name like a Row (declared columns only, KeyError on miss) — kept deliberately so the renderers' existing `n["title"]` indexing works without a mass rewrite to `n.title`. Dropping the shim (and migrating every `n["x"]` to attribute access) is a deferred cleanup: hundreds of accesses, no behavior change, negative ROI for now.
- **Query decomposition — prefer simple reads + Python compose over complex SQL.** The standing direction, consistent with G3 and with "no foreign keys" + "the time model derives membership in Python from the date, not a parent chain": decompose a complex read into simple single-table SELECTs composed in Python — each wrapped as a small function, reading **on-demand in batches** (only the ids actually needed, in one `id__in` query — never per-row, never the whole table) — rather than a JOIN / EXISTS-subquery / aggregate in one SQL statement. **Why:** the DB is small (low thousands of rows, ~2 MB), so a candidate read + Python compose is sub-millisecond and measures *faster* than the subquery SQL it replaces (the `workitem_sql` filter — six EXISTS subqueries — is 1.90 ms/call; reading the nodes and classifying in Python via the pure `node_type_from_props` is 1.59 ms, identical result set). Complex SQL is also the part hardest to maintain, profile, and refactor (by a human or an AI), so minimizing it is a maintainability win, not a perf cost. The `db_table` "NOT an ORM — complex reads stay explicit SQL" stance narrows accordingly: keep raw SQL only where decomposition genuinely can't help (e.g. GROUP BY vocab stats, the `local_day` time-window JOINs) or for projections. Simple single-table reads that were still hand-written SQL (`semantic.py` keyword LIKE, count(*)) have been aligned to `_db.query` / `Node.count` — `cmd_find` was already the template.
- **Type classification is on-demand batch, not EXISTS subqueries.** A node's type derives from its `type.*` props via the pure `node_type_from_props(props)` (precedence para > date > habit > meetlog > custom > task). The EXISTS-subquery SQL forms — `workitem_sql` (deleted), `nodes_with_type`, `time_node_by_period` — are replaced by one primitive: **`classify_types(con, node_ids)`** reads *just those ids'* `type.*` props in ONE batched query (`node_id__in`) and classifies in Python; `filter_workitems` (node batches) and `workitem_ids` (rows carrying a node_id) sit on top. The batch is the caller's actual need — a project's children, a day's logged nodes, a status-filtered set — read in one `id__in` query, not per-node (N+1). The read tracks the request, not a speculative full-table index built ahead of need; a whole-tree `summary` / orphans request *is* every live node, so that path does read all type props (the `id__in` list is unchunked — fine at the current low-thousands scale, a known ceiling against SQLite's variable limit if the tree ever grows huge). **No cache** — each call reads what it needs. An object-level identity-map cache that auto-expires on write (so attribute access goes through the cached object, consistent across a long-running process) is the agreed *next* step, deliberately deferred. The one render-loop N+1 still open is `_node_line`'s mixed type/clock/tags/planned per-node reads — a separate render-layer batch-prefetch, not yet done.

## 24+. Additional sections (24–37)

Sections 24 onward cover finer-grained extensions: metadata at the top of `wl day` (goal / summary / week & month goal blockquote), date context (`date_meta` for holidays / makeups), `--by-task` per-task aggregation in `wl logs`, `wl active` (active CLOCKs with elapsed), the `compound flag` semantics on `add` / `done` / `cancel` (`--log` / `--at` / `--link` / `--sched` / `--done` in one shot), `unlog` / `relog` (log editing through `#L<id>` references), time backfill (`start --at` / `stop --at` / `spent`), the multi-habit interactive `wl checkin`, recurrence rules including `-1` for "last day of period", the `wl ls` multi-dimension query model, default-tail-N to prevent screen-blast, shell completion via `print-completion` init-load (matching the starship / direnv / zoxide pattern), and the **battery-included command help design philosophy** (every `--help` carries a one-line intro + scenario + diff-from-neighbors).

For full historical conventions and the design rationale of each section, refer to [`DESIGN.zh.md`](DESIGN.zh.md), which preserves the complete original (Chinese) text.

---

For the Chinese version of this design document, see [DESIGN.zh.md](DESIGN.zh.md).
