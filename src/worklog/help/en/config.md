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
< `$WORKLOG_EMBED_*` < `--endpoint/--model/--dimensions/--api-key`** flags. Default is a local
Ollama (`ollama pull qwen3-embedding:0.6b`). Example `config.ini`:

    [embedding]
    endpoint = http://localhost:11434/v1/embeddings
    model = qwen3-embedding:0.6b
    # query_prompt: template applied to the QUERY only (not documents). {query} = the query text,
    # \n = a newline. The default (shown below — copy & tweak) is Qwen3-Embedding's retrieval
    # template; set it empty to disable for a server that adds the instruction itself (e.g. nmem):
    # query_prompt = Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery:{query}

A `[synonyms]` section (same file) defines equivalence groups for `wl query`'s keyword side —
`canonical = alias1, alias2`, so searching any member also matches the others (names/aliases):

    [synonyms]
    New York = NYC, NY

See `wl help admin`, `wl help query`.
