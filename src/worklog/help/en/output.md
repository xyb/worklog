---
title: output — -o json / jsonl / toon for machine-readable output
category: param
see_also: show, ls, query, bulk
---
`-o json` (or `--output json`) switches any read command to machine-readable JSON instead of
the human text view. It is a global flag accepted both before and after the verb:

  wl -o json ls --parent 45          # prefix form (before the verb)
  wl ls --parent 45 -o json          # suffix form (after the verb)
  wl -o json show 42
  wl find "deploy" -o json

All read commands that produce a list return a **JSON array**; commands that return one item
return a **JSON object**; commands with nothing to return emit `null` or `[]`. `*_at` fields
are UTC instants (ISO-8601); `*_date` fields are local calendar days (YYYY-MM-DD).

`-o jsonl` (JSON Lines) is the same data, line-oriented: a top-level array streams **one compact
JSON object per line** (empty array → no output); any other payload is a single line. Use it to
pipe into `jq -c`, `grep`, or a streaming analyzer without loading the whole array:

  wl logs --since 2026-07-01 --until 2026-07-07 -o jsonl   # one log event per line, streamable
  wl ls --all -o jsonl | jq -c 'select(.status=="DOING")'
  wl find "deploy" -o jsonl | grep -c .                     # count matches by line

`-o toon` emits [TOON](https://github.com/toon-format/spec) (Token-Oriented Object Notation) — the
same data as `-o json`, losslessly, in a compact indentation-based format that costs an LLM ~40%
fewer tokens. A uniform array of flat objects becomes a table: field names once, then one row of
values per line. Use it when piping wl data straight into an LLM prompt (context is the budget):

  wl ls --all -o toon
  # [3]{id,title,status,priority,tags}:
  #   1,ship it,DOING,A,work
  #   ...
  wl agent ls -o toon                # bind table, minimal punctuation
  wl show 42 -o toon                 # nested object form (indentation, no braces)

Common uses:

  wl show 42 -o json                 # full node: tags, props, links, timeline, metrics, clock
  wl ls --parent 45 -o json          # array of node summaries (filters apply, no 20-cap)
  wl ls --all -o json | jq '.[].id'  # extract all ids
  wl find "deploy" -o json           # search results as [{id,title,status,score,…}]
  wl query "vector search" -o json   # same shape, ranked by meaning + keyword
  wl active -o json                  # [{node_id,title,status,start_at,elapsed_min}] or []
  wl goal -o json                    # {body,logged_at} or null
  wl recap -o json                   # {body,logged_at} or null
  wl relation 42 -o json             # {block:[…],split:[…],related:[…],blocked_by:[…],split_from:[…]}
  wl log show 282 -o json            # {id,node_id,tag,body,logged_at}
  wl clock ls 42 -o json             # [{id,start_at,end_at,elapsed_sec}]
  wl agent ls -o json                # [{id,agent,sid,title,act,bound,stale}] (act = last real
                                     #   work on the node; the bind itself doesn't count)
  wl agent gaps -o json              # [{id,title,priority,status,reason}] reason: DOING|goal
  wl date ls -o json                 # [{date,label}]
  wl sched ls -o json                # [{node_id,title,scheduled_date,recur}]
  wl changes --month 2026-06 -o json # per-project change lists

Write commands (`add`, `done`, `start`, `defer`, `wait`, …) also accept `-o json` and return
the affected node(s) as a summary array instead of printing confirmation text — useful when
a script needs the id or status right after a mutation.

  wl -o json add "ship it"           # returns [{id,title,status,…}]
  wl done 42 -o json                 # returns [{id,title,status,…}]
  wl start 42 -o json                # returns [{id,title,status,start_at,…}]

JSON output goes to stdout; progress/warning lines (if any) still go to stderr. Pipe freely:

  wl ls -o json | jq '.[] | select(.status == "DOING")'
  wl show 42 -o json | jq '.props'
