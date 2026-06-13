---
title: query — hybrid (meaning + keyword) search
category: command
see_also: find, reindex, config
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

First run `wl reindex` to build the index — `wl query` reads what it builds, and won't see
nodes added since the last reindex until you rebuild.

Needs the optional `semantic` extra: `pip install 'pyworklog[semantic]'` (adds LanceDB).
Configure the embedding backend in `wl config` — see `wl help config`.

`wl find` (exact keyword/substring) and `wl query` (meaning) are complementary: `find` for
when you remember a word, `query` for when you only remember the idea.
