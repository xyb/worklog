---
title: query — semantic (meaning-based) search
category: command
see_also: find, reindex, config
---
`wl query "<text>"` finds the nodes whose *meaning* is closest to your text, even when they
share no words with it — the paraphrase matches that keyword `wl find` misses.

  wl query "how to avoid duplicate work"   # concept, not substring
  wl query "开源发布" --limit 5             # top 5
  wl query "性能" --threshold 0.4           # drop weak matches (cosine score < 0.4)
  wl query "vector search" -o json          # machine-readable {id,title,status,score}

How it works: every node (title + body + logs + tags) is embedded into a vector by an
OpenAI-compatible embedding server; your query is embedded the same way and the nearest
vectors come back ranked by cosine similarity.

First run `wl reindex` to build the index — `wl query` reads what it builds, and won't see
nodes added since the last reindex until you rebuild.

Needs the optional `semantic` extra: `pip install 'pyworklog[semantic]'` (adds LanceDB).
Configure the embedding backend in `wl config` — see `wl help config`.

`wl find` (exact keyword/substring) and `wl query` (meaning) are complementary: `find` for
when you remember a word, `query` for when you only remember the idea.
