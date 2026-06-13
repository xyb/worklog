"""worklog commands: semantic search — `wl reindex` (build vectors) + `wl query` (search).

The embedding backend is any OpenAI-compatible HTTP server (see worklog.config /
worklog.embedding); vectors live in an embedded LanceDB sidecar (worklog.vectorstore,
the optional 'semantic' extra). Both failure modes — backend unreachable, extra not
installed — are turned into a single clean `sys.exit` message here, never a traceback."""
from __future__ import annotations

import json
import os
import re
import sys

from .. import config as _config
from .. import embedding as _embedding
from .. import vectorstore as _vs
from .. import db_table as _db
from .. import render
from ..render import _c, _node_line, _hl_terms, out
from ..helpers import _truncate_log_body, _display_width
from ..xdg import _resolve_vec_db_path

# Batch bounds for embedding: a CHARACTER budget (≈ equal work / batch, the basis for an
# even progress bar) plus a node cap (keeps a single request's payload reasonable).
MAX_BATCH_CHARS = 16000
MAX_BATCH_NODES = 64


def _node_chunks(con):
    """Split each live node into CHUNKS for embedding (not one diluted whole-node
    vector): a `head` chunk (title + body + tags = the node's own identity) plus one
    `log` chunk per log entry, each prefixed with `title · tags` so a short log keeps
    enough context to stand on its own. Search max-pools chunks back to nodes, so a
    node surfaces on its single most relevant passage instead of an averaged blur.
    Returns list of (node_id, title, status, priority, chunk_kind, chunk_text)."""
    nodes = con.execute(
        "SELECT id, title, body, status, priority FROM node WHERE deleted_at IS NULL ORDER BY id"
    ).fetchall()
    logs = {}
    for r in con.execute("SELECT node_id, body FROM log WHERE deleted_at IS NULL ORDER BY id"):
        logs.setdefault(r["node_id"], []).append(r["body"] or "")
    tags = {}
    for r in con.execute("SELECT node_id, tag FROM tag WHERE deleted_at IS NULL"):
        tags.setdefault(r["node_id"], []).append(r["tag"] or "")
    chunks = []
    for n in nodes:
        title = n["title"] or ""
        tagstr = " ".join(tags.get(n["id"], []))
        prefix = title + (f" · {tagstr}" if tagstr else "")
        head = [title]
        if n["body"]:
            head.append(n["body"])
        if tagstr:
            head.append(tagstr)
        chunks.append((n["id"], title, n["status"], n["priority"], "head", "\n".join(head)))
        for body in logs.get(n["id"], []):
            if body.strip():
                chunks.append((n["id"], title, n["status"], n["priority"], "log", f"{prefix}\n{body}"))
    return chunks


def _weight(t):
    """Work weight of one doc ≈ its character count (a token proxy). Min 1 so empty
    nodes still advance the bar and the total is never zero."""
    return max(len(t), 1)


def _char_batches(texts, max_chars, max_nodes):
    """Group texts into batches bounded by a CHARACTER budget (so each batch is ~equal
    embedding work / wall-time, regardless of how log-heavy individual nodes are) and a
    node cap (to keep request payloads sane). A single oversized text becomes its own batch."""
    batch, chars = [], 0
    for t in texts:
        w = _weight(t)
        if batch and (chars + w > max_chars or len(batch) >= max_nodes):
            yield batch
            batch, chars = [], 0
        batch.append(t)
        chars += w
    if batch:
        yield batch


def _embed_batched(texts, input_type, cfg, on_progress=None, max_chars=MAX_BATCH_CHARS, max_nodes=MAX_BATCH_NODES):
    """Embed in character-budgeted batches (so each batch ≈ equal work / wall-time). One
    embedding HTTP call per batch; on_progress(calls_done, total_calls) fires after each —
    progress is measured in CALLS (the real unit of work dispatched), which is uniform
    precisely because the batches are char-budgeted to roughly equal size."""
    batches = list(_char_batches(texts, max_chars, max_nodes))
    vecs = []
    for i, batch in enumerate(batches, 1):
        vecs.extend(_embedding.embed(batch, input_type, cfg))
        if on_progress is not None:
            on_progress(i, len(batches))
    return vecs


def _embed_with_progress(texts, cfg):
    """Embed `texts` as documents, showing a stderr progress bar (calls done / total +
    percent + elapsed + ETA) when interactive. Disabled (no output) on non-TTY / piped /
    NO_COLOR, where the loop still runs."""
    from rich.console import Console
    from rich.progress import (Progress, BarColumn, TextColumn, TaskProgressColumn,
                               MofNCompleteColumn, TimeElapsedColumn, TimeRemainingColumn)
    interactive = render._RICH_AVAIL and sys.stderr.isatty() and not os.environ.get("NO_COLOR")
    n_calls = sum(1 for _ in _char_batches(texts, MAX_BATCH_CHARS, MAX_BATCH_NODES))
    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(), MofNCompleteColumn(), TaskProgressColumn(),
        TextColumn("·"), TimeElapsedColumn(), TextColumn("elapsed · ETA"), TimeRemainingColumn(),
        console=Console(stderr=True), transient=True, disable=not interactive,
    ) as prog:
        task = prog.add_task(f"embedding {len(texts)} node(s) in {n_calls} call(s)", total=n_calls)
        return _embed_batched(texts, "document", cfg,
                              on_progress=lambda done, total: prog.update(task, completed=done))


def _expand_synonyms(terms):
    """Expand each query term with its synonym group from config.ini [synonyms] (so an alias
    pulls in the whole group, e.g. NYC → New York/NY). Identity when no synonyms configured."""
    smap = _config.synonym_map()
    if not smap:
        return terms
    out, seen = [], set()
    for t in terms:
        for m in (smap.get(t.lower()) or {t}):
            if m.lower() not in seen:
                seen.add(m.lower())
                out.append(m)
    return out


_KW_FIELD_WEIGHT = {"title": 3, "tag": 2, "body": 1, "log": 1}


def _keyword_rank(con, terms):
    """Lexical ranking: node_ids ordered by a field-weighted term-match score — a term in the
    title counts more than one in a log (so a node *about* the term outranks one that merely
    mentions it once), and matching more distinct terms accumulates. The exact-match side of
    hybrid, catching names/ids/jargon that embeddings dilute. Ties broken by recency (id desc)."""
    score = {}

    def add(nid, w):
        score[nid] = score.get(nid, 0) + w

    for t in terms:
        like = f"%{t}%"
        for r in con.execute("SELECT id FROM node WHERE deleted_at IS NULL AND title LIKE ?", (like,)):
            add(r["id"], _KW_FIELD_WEIGHT["title"])
        for r in con.execute("SELECT id FROM node WHERE deleted_at IS NULL AND body LIKE ?", (like,)):
            add(r["id"], _KW_FIELD_WEIGHT["body"])
        for r in con.execute("SELECT DISTINCT node_id FROM log WHERE deleted_at IS NULL AND body LIKE ?", (like,)):
            add(r["node_id"], _KW_FIELD_WEIGHT["log"])
        for r in con.execute("SELECT DISTINCT node_id FROM tag WHERE deleted_at IS NULL AND tag LIKE ?", (like,)):
            add(r["node_id"], _KW_FIELD_WEIGHT["tag"])
    # drop canceled / missing nodes (a log/tag match can point at one)
    alive = {r["id"] for r in con.execute(
        "SELECT id FROM node WHERE deleted_at IS NULL AND status != 'CANCELED'")}
    ranked = [nid for nid in score if nid in alive]
    ranked.sort(key=lambda nid: (score[nid], nid), reverse=True)
    return ranked


def _rrf(ranked_lists, k=60):
    """Reciprocal Rank Fusion: merge several ranked id-lists into one order by
    Σ 1/(k + rank). Rank-based (not score-based), so it fuses the vector cosine ranking and
    the keyword ranking without needing their scores to be comparable. k=60 per Cormack 2009."""
    score = {}
    for ranked in ranked_lists:
        for rank, nid in enumerate(ranked, 1):
            score[nid] = score.get(nid, 0.0) + 1.0 / (k + rank)
    return sorted(score, key=lambda nid: (score[nid], nid), reverse=True)


def _open_store(args):
    """Open the sidecar store, mapping a missing 'semantic' extra to a clean exit."""
    try:
        return _vs.connect(_resolve_vec_db_path(args))
    except _vs.VectorStoreError as e:
        sys.exit(f"✗ {e}")


def cmd_reindex(args, con):
    """(Re)build the semantic index: embed every node's chunks and replace the vector store."""
    cfg = _config.resolve_embedding_config(args)
    chunks = _node_chunks(con)
    if not chunks:
        out(_c("(nothing to index — no nodes yet)", "meta"))
        return
    db = _open_store(args)
    try:
        vecs = _embed_with_progress([c[5] for c in chunks], cfg)
    except _embedding.EmbeddingError as e:
        sys.exit(f"✗ {e}")
    dim = len(vecs[0]) if vecs else 0
    rows = [{"node_id": nid, "title": title, "status": st, "priority": pr,
             "model": cfg["model"], "dim": dim, "vector": v, "chunk_text": text, "chunk_kind": kind}
            for (nid, title, st, pr, kind, text), v in zip(chunks, vecs)]
    _vs.clear(db)
    _vs.upsert(db, rows)
    n_nodes = len({c[0] for c in chunks})
    out(_c(f"✓ indexed {n_nodes} node(s) ({len(rows)} chunks) — model {cfg['model']}, dim {dim}", "done"))


def cmd_query(args, con):
    """Hybrid search: fuse semantic (best-chunk cosine) and keyword (term match) rankings via
    RRF — so paraphrases (vector) and exact names/jargon (keyword) both surface, and a literal
    hit isn't lost to semantic dilution. Query terms are jieba-segmented + synonym-expanded."""
    q = (args.query or "").strip()
    if not q:
        sys.exit("✗ search term cannot be empty")
    cfg = _config.resolve_embedding_config(args)
    db = _open_store(args)
    if _vs.index_model(db) is None:
        sys.exit("✗ no semantic index yet — run `wl reindex` first to build it")
    try:
        qvec = _embedding.embed([q], "query", cfg)[0]
    except _embedding.EmbeddingError as e:
        sys.exit(f"✗ {e}")
    limit = getattr(args, "limit", None) or 10
    threshold = getattr(args, "threshold", None)
    terms = _expand_synonyms(_query_terms(q))
    # vector side: rank every node by its best chunk; keyword side: lexical term match. Fuse w/ RRF.
    vec_hits = _vs.search(db, qvec, k=10 ** 9)   # all nodes ranked; threshold applied below, not here
    vec_map = {h["node_id"]: h for h in vec_hits}
    vec_ids = [h["node_id"] for h in vec_hits if threshold is None or h["score"] >= threshold]
    fused = _rrf([vec_ids, _keyword_rank(con, terms)])[:limit]

    if getattr(args, "output", "text") == "json":
        rows = []
        for nid in fused:
            n = _db.get(con, "node", nid)
            if not n:
                continue
            h = vec_map.get(nid, {})
            rows.append({"id": nid, "title": n["title"], "status": n["status"], "priority": n["priority"],
                         "score": round(h.get("score", 0.0), 4),
                         "matched_kind": h.get("chunk_kind", ""), "matched_text": h.get("chunk_text", "")})
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return

    if not fused:
        out(_c(f"(no matches for '{q}')", "meta"))
        return
    out(_c(f"'{q}' — {len(fused)} hit(s) (semantic + keyword):", "header"))
    for nid in fused:
        n = _db.get(con, "node", nid)
        if not n:
            continue  # vector/keyword pointed at a since-deleted node — skip
        h = vec_map.get(nid, {})
        # Pass the score as the node-line's `indent` so its hang-wrap counts the score width:
        # a wrapped title's continuation lines then align under the title, not column 0.
        out(_node_line(con, n, indent=f"{h.get('score', 0.0):.3f}  ", hl=q))
        # show the best-matching chunk as the reason (the head chunk repeats title/body; a log
        # chunk pins which log), clipped to one line via the same _truncate_log_body `wl day`
        # uses, then highlighted per query term.
        chunk = h.get("chunk_text", "")
        if chunk:
            flat = " ".join(chunk.split())
            label = f"↳ {h.get('chunk_kind', '')}: "
            body = _truncate_log_body(flat, indent_cols=_display_width("    " + label))
            out("    " + _c(f"↳ {h.get('chunk_kind', '')}:", "meta") + " " + _hl_terms(body, terms))


def _segment(text):
    """Segment text into terms — jieba (cut_for_search, multi-granularity, good Chinese recall)
    when the `semantic` extra is installed, else a `\\w+` fallback (a CJK run stays one token,
    latin/space split) so highlighting still works and the core CLI never needs jieba."""
    try:
        import jieba
        import logging
        jieba.setLogLevel(logging.WARNING)   # silence the one-time "Building prefix dict…" on stderr
    except ImportError:
        return re.findall(r"\w+", text, re.UNICODE)
    return [t for t in jieba.cut_for_search(text) if t.strip()]


def _query_terms(q):
    """Segmented query terms for highlight + keyword matching: deduped (case-insensitive),
    original case kept, empties dropped."""
    terms, seen = [], set()
    for t in _segment(q):
        tl = t.lower()
        if t.strip() and tl not in seen:
            seen.add(tl)
            terms.append(t)
    return terms
