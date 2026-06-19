---
title: query — hybrid (meaning + keyword) search
category: command
see_also: find, reindex, config, output
---
`wl query "<text>"` is **hybrid** search: it fuses a *semantic* ranking (meaning, even when no
words are shared — the paraphrase matches `wl find` misses) with a *keyword* ranking (exact
names / ids / jargon that embeddings dilute), so both kinds of match surface.

  wl query "how to avoid duplicate work"   # concept, not substring
  wl query "open-source release" --limit 5 # top 5
  wl query "performance" --threshold 0.4    # drop weak semantic matches (keyword still applies)
  wl query "vector search" -o json          # machine-readable {id,title,status,score,matched_*}

How it works: each node is split into chunks (its head — title+body+tags — plus one per log)
and embedded by an OpenAI-compatible server; your query is embedded the same way and nodes are
ranked by their **best-matching chunk** (cosine, so a node surfaces on its most relevant passage,
not a diluted whole-node average). In parallel the query is **jieba-segmented** into terms
(expanded via `config.ini [synonyms]`, so e.g. an alias finds its canonical) and matched
literally against title/body/log/tag. The two rankings are merged with **Reciprocal Rank
Fusion**. Each hit shows its matching chunk (`↳ …`), query terms highlighted.

`--threshold` filters the *semantic* side; keyword matches still surface regardless.

Run `wl reindex` to build or update the index — `wl query` reads what it builds. The first
`wl reindex` does a full build; subsequent runs are incremental (only new/changed nodes).
Use `wl reindex --full` to force a complete rebuild, e.g. after switching embedding models.

Backends degrade gracefully, so this works with **no** required extras: the vector store is
LanceDB when the optional `semantic` extra is installed (`pip install 'pyworklog[semantic]'`,
the fast path on Python 3.9–3.14 / Linux / Apple-Silicon macOS), else a pure-Python SQLite
fallback (same results, slower) where no LanceDB wheel exists; segmentation is jieba with the
extra, else a `\w+` fallback. Configure the embedding backend in `wl config` — see `wl help config`.

`wl find` (exact keyword/substring) and `wl query` (meaning) are complementary: `find` for
when you remember a word, `query` for when you only remember the idea.
