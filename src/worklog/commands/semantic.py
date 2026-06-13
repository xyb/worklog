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
from ..render import _c, _node_line, _snippet_terms, out
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
    """Semantic search: embed the query, return the nearest nodes by cosine similarity."""
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
    hits = _vs.search(db, qvec, k=limit, threshold=threshold)

    if getattr(args, "output", "text") == "json":
        print(json.dumps([
            {"id": h["node_id"], "title": h["title"], "status": h["status"],
             "priority": h["priority"], "score": round(h["score"], 4),
             "matched_kind": h["chunk_kind"], "matched_text": h["chunk_text"]}
            for h in hits
        ], ensure_ascii=False, indent=2))
        return

    if not hits:
        out(_c(f"(no semantic matches for '{q}')", "meta"))
        return
    terms = _query_terms(q)
    out(_c(f"'{q}' — {len(hits)} semantic hit(s):", "header"))
    for h in hits:
        n = _db.get(con, "node", h["node_id"])
        if not n:
            continue  # vector for a node since deleted — skip (reindex will prune it)
        out(_c(f"{h['score']:.3f}", "meta") + "  " + _node_line(con, n, hl=q))
        # show the single best-matching chunk — the exact passage that earned the score,
        # so a semantic hit is legible even when it shares no literal word with the query
        # (the `head` chunk repeats the title/body; a `log` chunk pins which log matched).
        # Highlight per query TERM, so a multi-word query lights up the parts that appear.
        if h["chunk_text"]:
            out("    " + _c(f"↳ {h['chunk_kind']}:", "meta") + " " + _snippet_terms(h["chunk_text"], terms))


def _query_terms(q):
    """Split a query into terms for highlighting: word runs (a CJK run stays one token —
    finer word-segmentation arrives with the jieba tokenizer when hybrid search lands)."""
    return re.findall(r"\w+", q, re.UNICODE)
