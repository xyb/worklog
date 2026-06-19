---
title: reindex — build/update the semantic search index
category: command
see_also: query, config
---
`wl reindex` embeds live nodes — split into chunks (its head, plus one per log) — via the
configured embedding server and stores the vectors in a sidecar index, which `wl query` then
searches (ranking each node by its best-matching chunk).

  wl reindex                              # incremental top-up (full on first run)
  wl reindex --full                       # full rebuild — use after switching models
  wl reindex --model qwen3-embedding      # override the model for this run
  wl reindex --endpoint http://host:11434/v1/embeddings --model text-embedding-3-small

**Incremental by default**: on the first run (no index yet) it builds the full index; on
subsequent runs it embeds only new/changed nodes and evicts deleted ones. Run it after adding
or editing nodes — `wl query` won't see changes until the next reindex. (The optional
`auto_reindex` setting in `config.ini [index]` kicks off an incremental pass automatically
after any write.)

Use `--full` to rebuild the whole index from scratch — necessary when you switch embedding
models, since vectors from different models are not comparable.

Vector store: LanceDB with the optional `semantic` extra (`pip install 'pyworklog[semantic]'`,
the fast path on Python 3.9–3.14 / Linux / Apple-Silicon macOS), else a pure-Python SQLite
fallback where no LanceDB wheel exists — reindex prints a one-line note when it falls back.
The embedding backend resolves from defaults < config.ini `[embedding]` < `$WORKLOG_EMBED_*`
< flags — inspect the resolved values with `wl config`.
