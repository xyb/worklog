---
title: reindex — build the semantic search index
category: command
see_also: query, config
---
`wl reindex` embeds every live node — split into chunks (its head, plus one per log) — via the
configured embedding server and stores the vectors in a sidecar index, which `wl query` then
searches (ranking each node by its best-matching chunk).

  wl reindex                            # build/refresh with the configured backend
  wl reindex --model qwen3-embedding    # override the model for this run
  wl reindex --endpoint http://host:11434/v1/embeddings   # a different server

Run it after adding or substantially editing nodes — the index is a snapshot, so `wl query`
won't see changes until you rebuild. It replaces the whole index each time (a clean rebuild,
not an incremental update).

Vector store: LanceDB with the optional `semantic` extra (`pip install 'pyworklog[semantic]'`,
the fast path on Python 3.9–3.14 / Linux / Apple-Silicon macOS), else a pure-Python SQLite
fallback where no LanceDB wheel exists — reindex prints a one-line note when it falls back.
The embedding backend resolves from defaults < config.ini `[embedding]` < `$WORKLOG_EMBED_*`
< flags — inspect the resolved values with `wl config`.
