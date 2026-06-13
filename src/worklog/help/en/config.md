---
title: config — show resolved paths & settings
category: command
see_also: admin, init, query
---
`wl config` prints where things live and the current settings: the DB path (and how it was
resolved), the aliases file, XDG dirs, relevant env vars, and the **embedding backend** for
`wl query`/`wl reindex` (endpoint / model / dimensions / api_key, each tagged with where it
resolved from — default / config / env / flag — plus whether the LanceDB `semantic` extra is
installed). Read-only — it doesn't create anything.

The embedding backend resolves across **defaults < `~/.config/worklog/config.ini` `[embedding]`
< `$WORKLOG_EMBED_*` < `--endpoint/--model/--dimensions/--api-key`** flags. Example `config.ini`:

    [embedding]
    endpoint = http://127.0.0.1:11434/v1/embeddings
    model = qwen3-embedding

See `wl help admin`, `wl help query`.
