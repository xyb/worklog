"""worklog commands: semantic search — `wl reindex` (build vectors) + `wl query` (search).

The embedding backend is any OpenAI-compatible HTTP server (see worklog.config /
worklog.embedding); vectors live in an embedded LanceDB sidecar (worklog.vectorstore,
the optional 'semantic' extra). Both failure modes — backend unreachable, extra not
installed — are turned into a single clean `sys.exit` message here, never a traceback."""
from __future__ import annotations

import hashlib
import math
import os
import re
import sqlite3
import sys
from pathlib import Path

from .. import config as _config
from .. import embedding as _embedding
from .. import vectorstore as _vs
from .. import db_table as _db
from .. import render
from ..models import Node
from ..render import _c, _node_line, _hl_terms, _detail_line, die, out
from ..helpers import _truncate_log_body, _display_width
from ..xdg import _resolve_kw_db_path, _resolve_vec_db_path
from .output import output_format, TextRenderable, text_renderer

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
    Returns list of (node_id, title, status, priority, chunk_field, chunk_text)."""
    nodes = _db.query(con, "node", cols="id, title, body, status, priority", order="id")
    logs = {}
    for r in _db.query(con, "log", cols="node_id, body", order="id"):
        logs.setdefault(r["node_id"], []).append(r["body"] or "")
    tags = {}
    for r in _db.query(con, "tag", cols="node_id, tag"):
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


def _searchable_ids(con, include_canceled=False):
    """Node ids a query may return: every live node, minus canceled ones unless asked.

    Not `status != 'CANCELED'`: that is NULL, not true, for a NULL status, and every
    project / area / period / day node has one -- so the filter silently took about a
    tenth of the corpus out of keyword search, and those nodes were findable only by
    meaning."""
    ids = {r["id"] for r in _db.query(con, "node", cols="id")}
    if include_canceled:
        return ids
    return ids - {r["id"] for r in _db.query(con, "node", cols="id", status="CANCELED")}


def _keyword_hits(con, terms, include_canceled=False):
    """Per-node keyword match over title/body/log/tag with field weights. Returns
    `{node_id: (score, n_terms_matched)}` for alive, non-canceled nodes — `score` is the
    field-weighted sum (a term in the title counts more than one in a log), `n_terms_matched`
    is how many DISTINCT query terms appear anywhere on the node (== len(terms) ⇒ full coverage).
    The exact-match side of hybrid; full-coverage nodes get priority in cmd_query.

    Each term's contribution is scaled by its IDF. Without it every term weighed the same,
    so a word that half the nodes contain drowned out a name that appears on two -- and on
    a 41-question benchmark that one omission cost more than everything else in the ranking
    put together. The document frequency IDF needs is exactly the set of nodes each LIKE
    below already returns, so this stays a query against the live DB: no index to build,
    and a node added since the last reindex is still found by keyword."""
    alive = _searchable_ids(con, include_canceled)
    n_alive = len(alive) or 1
    score, covered = {}, {}

    for t in terms:
        like = f"%{t}%"
        field_w = {}                     # node_id -> summed field weight for THIS term

        def add(nid, w):
            field_w[nid] = field_w.get(nid, 0) + w

        for r in _db.query(con, "node", cols="id", title__like=like):
            add(r["id"], _KW_FIELD_WEIGHT["title"])
        for r in _db.query(con, "node", cols="id", body__like=like):
            add(r["id"], _KW_FIELD_WEIGHT["body"])
        for r in _db.query(con, "log", cols="DISTINCT node_id", body__like=like):
            add(r["node_id"], _KW_FIELD_WEIGHT["log"])
        for r in _db.query(con, "tag", cols="DISTINCT node_id", tag__like=like):
            add(r["node_id"], _KW_FIELD_WEIGHT["tag"])
        # drop canceled / missing nodes (a log/tag match can point at one) before counting df
        field_w = {nid: w for nid, w in field_w.items() if nid in alive}
        if not field_w:
            continue
        df = len(field_w)
        idf = math.log(1 + (n_alive - df + 0.5) / (df + 0.5))
        for nid, w in field_w.items():
            score[nid] = score.get(nid, 0.0) + w * idf
            covered.setdefault(nid, set()).add(t)
    return {nid: (score[nid], len(covered[nid])) for nid in score}


# --- BM25 keyword index (built by reindex) ----------------------------------------
#
# Every node is scored with BM25. `wl reindex` precomputes term counts into a sidecar
# index; a node whose text has changed since (or was never indexed) is re-tokenised at
# query time, so what you just wrote is searchable before any reindex -- which is what
# the live LIKE scan this replaced always did.
#
# Why BM25 and not just IDF: measured at node grain on a 41-question benchmark, hybrid
# recall with LIKE + field weights was .634, adding IDF .732, and BM25 .829 (keyword side
# alone: .268 / .610 / .902). IDF is a third of the gain; most of the rest is length
# normalisation -- a node carrying hundreds of logs contains nearly every common word, so
# without dividing by length it outranks the right node on almost any query -- and that
# needs term counts per node, which is what this index precomputes. A paired bootstrap
# over those 41 questions puts the LIKE -> BM25 step at +.146 [+.024, +.268].

_BM25_K1, _BM25_B = 1.2, 0.75
_KW_SCHEMA = 2           # bump when the layout or the indexed text changes
_KW_TITLE_W, _KW_TAG_W = 3, 2     # field weight by repetition (see _node_kw_text)


def _node_kw_text(con):
    """node_id -> the text BM25 indexes: title, tags, body and every log, with the title
    written 3 times and the tags twice.

    Repetition is the cheap stand-in for BM25F, and it is weaker than it looks: term
    frequency saturates (a title term at tf=3 scores about 1.6x, not 3x) and the copies
    count towards the document length. It buys the ordering the LIKE weights gave -- a
    title match ahead of a log match -- not their arithmetic.

    Not the embed chunks joined. Each log chunk carries a `title · tags` prefix so it can
    stand alone as a vector; concatenating them repeated the title once per log, so a
    node's title term frequency -- and its length -- grew with how much work it logged,
    and two nodes with the same title scored differently on it."""
    logs, tags = {}, {}
    for r in _db.query(con, "log", cols="node_id, body", order="id"):
        logs.setdefault(r["node_id"], []).append(r["body"] or "")
    for r in _db.query(con, "tag", cols="node_id, tag"):
        tags.setdefault(r["node_id"], []).append(r["tag"] or "")
    tags = {nid: sorted(ts) for nid, ts in tags.items()}   # stable order: a reshuffle is not an edit
    text = {}
    for n in _db.query(con, "node", cols="id, title, body", order="id"):
        nid = n["id"]
        parts = [n["title"] or ""] * _KW_TITLE_W
        tagstr = " ".join(tags.get(nid, ()))
        if tagstr:
            parts += [tagstr] * _KW_TAG_W
        if n["body"]:
            parts.append(n["body"])
        parts += logs.get(nid, ())
        text[nid] = "\n".join(parts)
    return text


def _tokenizer_id():
    """Which tokenizer built (or is querying) the index. Stored beside the schema, because
    a node's signature only says its text is unchanged -- upgrading jieba, or losing it and
    dropping to the `\\w+` fallback, re-cuts that same text into different tokens, and the
    postings would silently go on answering with the old ones."""
    try:
        import jieba
        return f"jieba-{getattr(jieba, '__version__', '?')}"
    except ImportError:
        return "re-w"


def _kw_terms(text):
    """Index and query tokens: segmented, then case-folded. SQLite LIKE ignores ASCII
    case, so the scan this replaced always did; matching the index on exact tokens did
    not, and a query for `lancedb` silently missed every node that writes `LanceDB`."""
    return [t.lower() for t in _segment(text)]


def _kw_sig(text):
    """Short content signature, to tell an index entry that is still current from one
    whose node has been edited since."""
    return hashlib.blake2b(text.encode("utf-8"), digest_size=8).hexdigest()


def _build_kw_index(con, path):
    """Rebuild the BM25 keyword index from scratch; returns how many nodes it holds.

    A plain inverted index in SQLite -- postings(term, node_id, tf), each node's length
    and a signature of the text it was built from -- scored in Python at query time.
    FTS5 was tried first and lost: it re-tokenises jieba's output with its own rules and
    normalises length its own way, and neither is adjustable. Owning the index keeps the
    ranking identical to the one that was benchmarked.

    Whole rebuild on every reindex, built beside the live file and swapped in with
    os.replace, so a concurrent query never reads a half-written index. The temporary
    name carries the pid, so a manual `wl reindex` and the background worker cannot
    delete or rename each other's half-built file."""
    path = Path(path)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.building")
    if tmp.exists():
        tmp.unlink()
    kw = sqlite3.connect(tmp)
    try:
        kw.executescript(
            "CREATE TABLE postings(term TEXT NOT NULL, node_id INTEGER NOT NULL, tf INTEGER NOT NULL);"
            "CREATE TABLE doclen(node_id INTEGER PRIMARY KEY, dl INTEGER NOT NULL, "
            "                    sig TEXT NOT NULL);"
            "CREATE TABLE meta(key TEXT PRIMARY KEY, value REAL NOT NULL);"
            "CREATE TABLE meta_text(key TEXT PRIMARY KEY, value TEXT NOT NULL);")
        n = 0
        for nid, text in _node_kw_text(con).items():
            counts = {}
            for t in _kw_terms(text):
                counts[t] = counts.get(t, 0) + 1
            dl = sum(counts.values())
            kw.executemany("INSERT INTO postings VALUES (?,?,?)",
                           ((t, nid, c) for t, c in counts.items()))
            kw.execute("INSERT INTO doclen VALUES (?,?,?)", (nid, dl, _kw_sig(text)))
            n += 1
        kw.executemany("INSERT INTO meta VALUES (?,?)", [("schema", _KW_SCHEMA)])
        kw.execute("INSERT INTO meta_text VALUES (?,?)", ("tokenizer", _tokenizer_id()))
        kw.execute("CREATE INDEX postings_term ON postings(term)")
        kw.commit()
    finally:
        kw.close()
    os.replace(tmp, path)
    return n


def _open_kw_index(args):
    """The keyword index read-only, or None when it has not been built yet, was built by
    an older version, or is not a keyword index at all.

    The check is a read, not a guess: anything that fails to answer for its schema is
    treated as absent, so `wl query` falls back to LIKE and asks for a reindex instead of
    raising out of a SELECT against a table shaped differently."""
    p = _resolve_kw_db_path(args)
    if not p.exists():
        return None
    try:
        kw = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        meta = dict(kw.execute("SELECT key, value FROM meta"))
        tok = dict(kw.execute("SELECT key, value FROM meta_text")).get("tokenizer")
        if meta.get("schema") != _KW_SCHEMA or tok != _tokenizer_id():
            kw.close()
            return None
        return kw
    except sqlite3.Error:
        return None


def _bm25_hits(con, kw, terms, alive):
    """BM25 over `alive`: `{node_id: (score, n_terms_matched)}`, same shape as
    `_keyword_hits`.

    Two things this does not do, both of which it used to. It does not decide by
    membership: a node that was edited after the last reindex is still in the index, so
    scoring it from its postings answered out of text that no longer exists and missed
    the text that does. Every entry carries a signature of the text it was built from,
    and a node whose signature moved is re-tokenised here, which is what the live LIKE
    scan this replaced always did. And it does not take its statistics from the index:
    df, n_docs and avgdl are computed over `alive`, so canceled nodes and nodes deleted
    since the last reindex cannot tilt the ranking of the ones actually shown.

    Cost is one pass over the corpus text to sign it (tens of ms on a few thousand
    nodes), plus tokenising only the nodes that moved."""
    texts = _node_kw_text(con)
    indexed = {r[0]: (r[1], r[2]) for r in kw.execute("SELECT node_id, dl, sig FROM doclen")}
    dls, moved = {}, {}
    for nid in alive:
        text = texts.get(nid)
        if text is None:
            continue
        row = indexed.get(nid)
        if row is not None and row[1] == _kw_sig(text):
            dls[nid] = row[0]                      # index still holds this node's text
        else:
            counts = {}
            for tok in _kw_terms(text):
                counts[tok] = counts.get(tok, 0) + 1
            moved[nid] = counts
            dls[nid] = sum(counts.values())
    if not dls:
        return {}
    n_docs = len(dls)
    avgdl = (sum(dls.values()) / n_docs) or 1.0
    score, covered = {}, {}
    for term in dict.fromkeys(t for t in terms if t.strip()):
        # a term can be several tokens -- `[synonyms]` expands an alias into whole phrases
        # ("NYC" -> "New York"), and the index holds those cut, so looking the phrase up
        # whole matched nothing. Coverage still counts the term once, however it was cut.
        for tok in dict.fromkeys(_kw_terms(term) or [term.lower()]):
            postings = [(nid, tf) for nid, tf in
                        kw.execute("SELECT node_id, tf FROM postings WHERE term = ?", (tok,))
                        if nid in dls and nid not in moved]
            postings += [(nid, c[tok]) for nid, c in moved.items() if c.get(tok)]
            if not postings:
                continue
            idf = math.log(1 + (n_docs - len(postings) + 0.5) / (len(postings) + 0.5))
            for nid, tf in postings:
                norm = 1 - _BM25_B + _BM25_B * dls[nid] / avgdl
                score[nid] = score.get(nid, 0.0) + idf * tf * (_BM25_K1 + 1) / (tf + _BM25_K1 * norm)
                covered.setdefault(nid, set()).add(term)
    return {nid: (score[nid], len(covered[nid])) for nid in score}


def _refresh_kw_index(con, args, *, force):
    """Rebuild the keyword index when reindex did work (`force`), or when the one on disk
    cannot be used -- missing, or built by a version whose tokens or layout differ.

    The test is `_open_kw_index`, the same one the query path applies, not `path.exists()`:
    an index this version refuses to read is exactly what a reindex has to replace, and
    checking only for the file left an upgraded install answering from LIKE forever, since
    `wl reindex` finds the vectors already current and would do nothing.

    Returns the node count built, or None."""
    kw = _open_kw_index(args)
    if kw is not None:
        kw.close()
        if not force:
            return None
    return _build_kw_index(con, _resolve_kw_db_path(args))


def _keyword_rank(con, terms):
    """node_ids ordered by field-weighted keyword score (ties → recency, id desc). The keyword
    leg of hybrid; see `_keyword_hits` for the per-node score + term coverage it's derived from."""
    hits = _keyword_hits(con, terms)
    return sorted(hits, key=lambda nid: (hits[nid][0], nid), reverse=True)


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
        die(f"{e}")


def _chunk_rows(chunks, vecs, cfg, dim):
    """Build vector-store rows from (chunk tuple, vector) pairs — shared by full + incremental."""
    return [{"node_id": nid, "title": title, "status": st, "priority": pr,
             "model": cfg["model"], "dim": dim, "vector": v, "chunk_text": text, "chunk_field": field}
            for (nid, title, st, pr, field, text), v in zip(chunks, vecs)]


@text_renderer("reindex")
def _render_reindex(result):
    """Text renderer for cmd_reindex, registered in _TEXT_RENDERERS."""
    status = result.get("status", "")
    backend = result.get("backend", "")
    if status == "up_to_date":
        out(_c("✓ index already up to date", "done"))
        if result.get("kw_nodes") is not None:
            out(_c(f"✓ BM25 keyword index built ({result['kw_nodes']} node(s)) — it was missing",
                   "done"))
    elif status == "indexed":
        mode = result.get("mode", "")
        if mode == "full":
            out(_c(f"✓ indexed {result['nodes']} node(s) ({result['chunks']} chunks) "
                   f"— model {result['model']}, dim {result['dim']}", "done"))
        elif mode == "incremental":
            out(_c(f"✓ incremental: +{result['new']} new, ~{result['changed']} changed, "
                   f"-{result['removed']} removed ({result['chunks_embedded']} chunks embedded)", "done"))
        elif mode == "incremental-full":
            out(_c(f"✓ indexed {result['nodes']} node(s) ({result['chunks']} chunks)", "done"))
        if result.get("kw_nodes") is not None:
            out(_c(f"✓ BM25 keyword index rebuilt ({result['kw_nodes']} node(s))", "done"))
        if backend == "sqlite":
            out(_c("  (sqlite fallback backend — install the 'semantic' extra "
                   "[pip install 'pyworklog[semantic]'] for the faster LanceDB store)", "meta"))


@output_format
def cmd_reindex(args, con):
    """(Re)build the semantic index. Default = incremental (embed only new/changed nodes, drop
    deleted; falls back to a full pass when no index exists yet). `--full` = always full
    rebuild (use after a model change or to repair a corrupt index)."""
    if getattr(args, "auto", False):     # background single-flight worker (spawned after a write)
        _reindex_auto(args, con)
        return TextRenderable({"mode": "auto"}, lambda: None)
    cfg = _config.resolve_embedding_config(args)
    chunks = _node_chunks(con)
    if not chunks:
        return TextRenderable(
            {"indexed": 0, "chunks": 0},
            lambda: out(_c("(nothing to index — no nodes yet)", "meta")),
        )
    db = _open_store(args)
    backend_name = _vs.backend_name(db)
    if not getattr(args, "full", False):
        # Default: incremental; _reindex_incremental falls back to full when store is empty/new
        result = _reindex_incremental(con, db, cfg, chunks)
        if result is None:
            kw_nodes = _refresh_kw_index(con, args, force=False)
            return TextRenderable({"status": "up_to_date", "backend": backend_name,
                                   "kw_nodes": kw_nodes}, cmd_name="reindex")
        if result is False:
            return TextRenderable({"status": "error", "backend": backend_name}, cmd_name="reindex")
        result["status"] = "indexed"
        result["backend"] = backend_name
        result["kw_nodes"] = _refresh_kw_index(con, args, force=True)
        return TextRenderable(result, cmd_name="reindex")
    try:
        vecs = _embed_with_progress([c[5] for c in chunks], cfg)
    except _embedding.EmbeddingError as e:
        die(f"{e}")
    dim = len(vecs[0]) if vecs else 0
    rows = _chunk_rows(chunks, vecs, cfg, dim)
    _vs.clear(db)
    _vs.upsert(db, rows)
    n_nodes = len({c[0] for c in chunks})
    result = {"status": "indexed", "mode": "full", "nodes": n_nodes, "chunks": len(rows),
              "model": cfg["model"], "dim": dim, "backend": backend_name,
              "kw_nodes": _refresh_kw_index(con, args, force=True)}
    return TextRenderable(result, cmd_name="reindex")


def _reindex_incremental(con, db, cfg, chunks, *, quiet=False):
    """Embed only the nodes whose chunk set changed since the last index, and evict deleted ones.
    Dirty detection is a cheap text diff (no embedding): a node is dirty if its current set of
    chunk texts differs from what's indexed; deleted = indexed node no longer live. Embeds just
    the dirty nodes' chunks. Falls back to a full pass when the store is empty.

    Returns a result dict (mode/counts) if work was done, None if up-to-date, False on error.
    `quiet` (the background --auto worker) suppresses output and swallows embedding errors."""
    def _embed(texts):
        try:
            return _embed_with_progress(texts, cfg)
        except _embedding.EmbeddingError as e:
            if quiet:
                return None              # give up this pass quietly; a later write retries
            die(f"{e}")

    im = _vs.index_model(db)
    if im is None:                       # empty store → nothing to diff against, do a full build
        vecs = _embed([c[5] for c in chunks])
        if vecs is None:
            return False
        n_chunks = len(chunks)
        _vs.upsert(db, _chunk_rows(chunks, vecs, cfg, len(vecs[0]) if vecs else 0))
        n_nodes = len({c[0] for c in chunks})
        return {"mode": "incremental-full", "nodes": n_nodes, "chunks": n_chunks}
    if im[0] != cfg["model"]:
        if quiet:
            return False                 # model changed → needs a full rebuild; don't churn here
        die(f"index was built with model '{im[0]}' but config is '{cfg['model']}' — "
                 f"run `wl reindex --full` to rebuild with the new model")
    by_node, cur_text = {}, {}
    for c in chunks:
        by_node.setdefault(c[0], []).append(c)
        cur_text.setdefault(c[0], set()).add(c[5])
    idx_text = {}
    for r in _vs.load(db):
        idx_text.setdefault(r["node_id"], set()).add(r["chunk_text"])
    live, indexed = set(by_node), set(idx_text)
    new = live - indexed
    removed = indexed - live
    changed = {nid for nid in (live & indexed) if cur_text[nid] != idx_text[nid]}
    dirty = new | changed
    if not dirty and not removed:
        return None  # up to date; caller renders
    dirty_chunks = [c for nid in dirty for c in by_node[nid]]
    rows = []
    if dirty_chunks:
        vecs = _embed([c[5] for c in dirty_chunks])
        if vecs is None:
            return False
        rows = _chunk_rows(dirty_chunks, vecs, cfg, im[1])
    _vs.delete_nodes(db, changed | removed)   # drop changed nodes' stale chunks + deleted nodes
    if rows:
        _vs.upsert(db, rows)
    return {"mode": "incremental", "new": len(new), "changed": len(changed),
            "removed": len(removed), "chunks_embedded": len(dirty_chunks)}


def _reindex_lock_path(args):
    """The single-flight lock for the background auto-reindex worker — a sibling of the vector
    store, so one is held per store (per DB)."""
    return Path(_resolve_vec_db_path(args)).parent / ".reindex.lock"


def _reindex_auto(args, con):
    """Background single-flight incremental reindex (spawned detached after a write). Holds an
    exclusive lock — if another worker already holds it, exit immediately (single-flight) — then
    loops incremental passes until nothing is dirty, so writes that land mid-run are still picked
    up. Quiet; never raises (a detached worker must not surface errors)."""
    import fcntl
    lock_path = _reindex_lock_path(args)
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lf = open(lock_path, "w")
    except OSError:
        return
    try:
        try:
            fcntl.flock(lf.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return                       # another worker is running — single-flight, don't pile up
        cfg = _config.resolve_embedding_config(args)
        did_work = False
        for _ in range(1000):            # bounded guard; each pass only embeds what's still dirty
            chunks = _node_chunks(con)
            if not chunks:
                break                    # empty db — nothing to index
            db = _open_store(args)
            if not _reindex_incremental(con, db, cfg, chunks, quiet=True):
                break                    # clean → done (loop again only if a write dirtied it)
            did_work = True
        _refresh_kw_index(con, args, force=did_work)
    except Exception:
        pass                             # detached worker: swallow everything, never crash loudly
    finally:
        try:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        lf.close()


@output_format
def cmd_query(args, con):
    """Hybrid search: fuse semantic (best-chunk cosine) and keyword (term match) rankings via
    RRF — so paraphrases (vector) and exact names/jargon (keyword) both surface, and a literal
    hit isn't lost to semantic dilution. Query terms are jieba-segmented + synonym-expanded."""
    q = (args.query or "").strip()
    if not q:
        die("search term cannot be empty")
    cfg = _config.resolve_embedding_config(args)
    db = _open_store(args)
    # A silent downgrade is worse than a slow one: when lancedb is not importable
    # connect() falls back to the pure-Python cosine scan and says nothing, and a
    # leftover worklog.lancedb/ directory keeps it looking like the fast path is
    # still in use. Warn before anything else can exit, on stderr so JSON output
    # stays clean.
    if _vs.backend_name(db) == "sqlite":
        print("⚠ vector search is on the SQLite fallback (pure-Python cosine scan), "  # noqa: stderr, see file= below
              "not LanceDB — `pip install 'pyworklog[semantic]'` for the fast store",
              file=sys.stderr)
    if _vs.index_model(db) is None:
        die("no semantic index yet — run `wl reindex` first to build it")
    try:
        qvec = _embedding.embed([q], "query", cfg)[0]
    except _embedding.EmbeddingError as e:
        die(f"{e}")
    limit = getattr(args, "limit", None) or 10
    threshold = getattr(args, "threshold", None)
    terms = _expand_synonyms(_query_terms(q))
    # vector side: rank every node by its best chunk; keyword side: lexical term match. Fuse w/ RRF.
    vec_hits = _vs.search(db, qvec, k=10 ** 9)   # all nodes ranked; threshold applied below, not here
    vec_map = {h["node_id"]: h for h in vec_hits}
    # canceled nodes are hidden on both sides unless --show-canceled; the vector side used
    # to keep them while the keyword side dropped them, so they surfaced only by meaning
    include_canceled = getattr(args, "show_canceled", False)
    searchable = _searchable_ids(con, include_canceled)
    vec_ids_all = [h["node_id"] for h in vec_hits if h["node_id"] in searchable
                   and (threshold is None or h["score"] >= threshold)]
    # B: cap the vector list — its long noisy tail (every node gets a rank) otherwise
    # dilutes precise keyword hits through RRF, esp. for short Chinese queries on a small model.
    vec_ids = vec_ids_all[: max(50, limit * 5)]
    # Keyword side: one BM25 ranking over every searchable node. Nodes the index has not
    # seen, and nodes whose text has changed since it was built, are scored from the live
    # DB inside _bm25_hits on the same scale -- so there is one keyword list, not a tier
    # per index-coverage state, and a fresh node is neither hidden nor over-credited.
    kw_db = _open_kw_index(args)
    if kw_db is not None:
        bm = _bm25_hits(con, kw_db, terms, searchable)
        kw_db.close()
        kw_lists = [sorted(bm, key=lambda nid: (bm[nid][0], nid), reverse=True)] if bm else []
        tiers = (bm,)
    else:
        print("⚠ BM25 keyword index missing or built by an older version — keyword matching "  # noqa: stderr
              "falls back to LIKE + IDF, which ranks noticeably worse. Run `wl reindex`.",
              file=sys.stderr)
        live = _keyword_hits(con, terms, include_canceled)
        kw_lists = [sorted(live, key=lambda nid: (live[nid][0], nid), reverse=True)]
        tiers = (live,)
    # C: nodes covering EVERY query term (an exact full match) lead, ordered by keyword
    # score -- so a literal hit (e.g. a node titled "微信全量导出" for "微信导出") isn't
    # buried under noisy semantic neighbors.
    nterms = len(terms)
    full = [nid for h in tiers
            for nid in sorted((n for n in h if nterms and h[n][1] == nterms),
                              key=lambda n, h=h: (h[n][0], n), reverse=True)]
    fused_rest = _rrf([vec_ids] + kw_lists)
    seen = set(full)
    fused = (full + [n for n in fused_rest if n not in seen])[:limit]

    # warn (on stderr, so stdout/JSON stays clean) when the index is out of date vs the
    # live DB — a node added/changed since the last `wl reindex` is absent from the vector side
    # (scores 0.000, only the keyword leg carries it), which is otherwise silent and confusing.
    total = Node.count(con)
    n_unindexed = total - len(vec_map)
    if n_unindexed > 0:
        pct = 100.0 * n_unindexed / total if total else 0.0
        # Graded, because "3 nodes behind" and "a third of the corpus missing" call
        # for different reactions, and the flat wording read the same for both. Past
        # 10% the vector leg is only covering part of the corpus, so a query is
        # closer to keyword search than to the hybrid it claims to be -- loud enough
        # that a person or an agent reading stderr knows to reindex before trusting
        # the result.
        if pct >= 10:
            # keep the phrase "not indexed": the milder line has always said it, and
            # anything that greps stderr to decide whether to reindex keys on it
            print(f"⚠⚠ semantic index is STALE: {n_unindexed}/{total} node(s) "  # noqa: stderr, see file= below
                  f"({pct:.0f}%) not indexed, so those match by keyword only and "
                  f"score 0.000. Results are NOT the full hybrid search. "
                  f"Run `wl reindex` before relying on this.", file=sys.stderr)
        else:
            print(f"⚠ {n_unindexed}/{total} node(s) ({pct:.1f}%) not indexed — "  # noqa: stderr, see file= below
                  f"run `wl reindex` (they match by keyword only, not meaning)",
                  file=sys.stderr)

    # fetch every fused node in one query; both the JSON and the render path share it
    node_pairs = [(nid, n) for nid, n in zip(fused, Node.gets(con, fused)) if n]

    # build JSON result list (used in JSON mode; computed before text output for DRY)
    json_rows = []
    for nid, n in node_pairs:
        h = vec_map.get(nid, {})
        json_rows.append({"id": nid, "title": n.title, "status": n.status, "priority": n.priority,
                         "score": round(h.get("score", 0.0), 4),
                         "matched_field": h.get("chunk_field", ""), "matched_text": h.get("chunk_text", "")})

    # build node objects for rendering (avoid re-querying in _render)
    fused_nodes = [(nid, n, vec_map.get(nid, {})) for nid, n in node_pairs]

    def _render():
        if not fused_nodes:
            out(_c(f"(no matches for '{q}')", "meta"))
            return
        out(_c(f"'{q}' — {len(fused)} hit(s) (semantic + keyword):", "header"))
        for nid, n, h in fused_nodes:
            out(_node_line(con, n, indent=f"{h.get('score', 0.0):.3f}  ", hl=terms))
            chunk = h.get("chunk_text", "")
            if chunk:
                flat = " ".join(chunk.split())
                label = f"↳ {h.get('chunk_field', '')}:"
                body = _truncate_log_body(flat, indent_cols=_display_width("    " + label + " "))
                out(_detail_line(label, _hl_terms(body, terms)))

    if not fused_nodes:
        return TextRenderable(json_rows, lambda: out(_c(f"(no matches for '{q}')", "meta")))
    return TextRenderable(json_rows, _render)


def _segment(text):
    """Segment text into terms — jieba (cut_for_search, multi-granularity, good Chinese recall)
    when the `semantic` extra is installed, else a `\\w+` fallback (a CJK run stays one token,
    latin/space split) so highlighting still works and the core CLI never needs jieba."""
    try:
        import warnings
        import logging
        with warnings.catch_warnings():
            # jieba 0.42.1 ships invalid escape sequences ("\.", "\s") that Python 3.12+ flags as
            # SyntaxWarning when it first compiles the module — jieba's bug, harmless, and it can't
            # be filtered after the fact (it fires at compile time), so swallow it around the import.
            warnings.simplefilter("ignore", SyntaxWarning)
            import jieba
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
