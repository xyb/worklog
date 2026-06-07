"""`wl help` — an info-style topic browser over repo-managed Markdown docs (DESIGN §25).

Topic docs live at `src/worklog/help/<lang>/<topic>.md` (shipped with the package, like
`migrations/`). Each has minimal frontmatter (title / category / see_also) and a Markdown
body. `wl help` renders the index; `wl help <topic>` renders one topic + a "See also" footer.
Language resolves from --lang / $WORKLOG_LANG / $LANG, always falling back to `en` per-topic.

Kept dependency-free: a tiny frontmatter parser and a light terminal renderer (the body is
already human-readable Markdown), so no YAML / Markdown engine is pulled in.
"""
from __future__ import annotations

import os
import sys
from difflib import get_close_matches
from pathlib import Path

from ..render import _c, out

HELP_DIR = Path(__file__).resolve().parent.parent / "help"
FALLBACK_LANG = "en"
_CATEGORY_ORDER = ["guide", "concept", "command", "param"]
_CATEGORY_TITLE = {
    "guide": "Guides", "concept": "Concepts", "command": "Commands", "param": "Common parameters",
}


def _resolve_lang(args):
    """--lang > $WORKLOG_LANG > $LANG prefix > en. Only returns a lang that has a dir;
    otherwise en (so a bogus value never blanks out help)."""
    cand = (getattr(args, "lang", None) or os.environ.get("WORKLOG_LANG")
            or os.environ.get("LANG", "").split(".")[0].split("_")[0] or FALLBACK_LANG)
    cand = cand.strip().lower()
    if cand and (HELP_DIR / cand).is_dir():
        return cand
    return FALLBACK_LANG


def _topic_path(topic, lang):
    """Path to a topic doc in `lang`, falling back to `en`; None if neither exists."""
    for lg in (lang, FALLBACK_LANG):
        p = HELP_DIR / lg / f"{topic}.md"
        if p.is_file():
            return p
    return None


def _parse_doc(text):
    """Split a topic doc into (meta dict, body). Frontmatter is `key: value` lines between
    two `---` fences at the top; `see_also` is parsed into a list. No fence → all body."""
    meta, body = {}, text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            _, front, body = parts
            for line in front.strip().splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    meta[k.strip()] = v.strip()
            body = body.lstrip("\n")
    if "see_also" in meta:
        meta["see_also"] = [s.strip() for s in meta["see_also"].replace(",", " ").split() if s.strip()]
    return meta, body


def _list_topics(lang):
    """All topic ids visible in `lang` (its files + en fallbacks), as
    {topic: (title, category)}, sorted by id."""
    found = {}
    for lg in (FALLBACK_LANG, lang):  # lang overrides en for title/category
        d = HELP_DIR / lg
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.md")):
            topic = p.stem
            if topic == "index":
                continue
            meta, _ = _parse_doc(p.read_text(encoding="utf-8"))
            found[topic] = (meta.get("title", topic), meta.get("category", "concept"))
    return dict(sorted(found.items()))


def _render_body(body):
    """Light terminal styling: color ATX headings and `See also:` lines; print the rest
    verbatim (the body is already readable Markdown)."""
    for line in body.rstrip().splitlines():
        stripped = line.lstrip("#").strip()
        if line.startswith("#") and stripped:
            out(_c(stripped, "header"))
        else:
            out(line)


def _render_topic(topic, meta, body, lang):
    title = meta.get("title", topic)
    out(_c(title, "header"))
    out(_c("─" * min(len(title), 60), "meta"))
    out("")
    _render_body(body)
    see = meta.get("see_also") or []
    if see:
        out("")
        out(_c("See also: ", "meta") + _c(" · ".join(see), "id")
            + _c("   (wl help <topic>)", "meta"))


def _render_index(lang):
    p = _topic_path("index", lang)
    if p:
        meta, body = _parse_doc(p.read_text(encoding="utf-8"))
        _render_topic("index", meta, body, lang)
        out("")
    out(_c("All topics", "header") + _c("  (wl help <topic>)", "meta"))
    topics = _list_topics(lang)
    by_cat = {}
    for topic, (title, cat) in topics.items():
        by_cat.setdefault(cat, []).append((topic, title))
    ordered = _CATEGORY_ORDER + [c for c in by_cat if c not in _CATEGORY_ORDER]
    for cat in ordered:
        items = by_cat.get(cat)
        if not items:
            continue
        out("")
        out("  " + _c(_CATEGORY_TITLE.get(cat, cat.title()), "planned"))
        for topic, title in items:
            # titles read "<name> — <description>"; show the description (or the whole
            # title if there's no dash), so the list isn't "node   node".
            desc = title.split("—", 1)[1].strip() if "—" in title else title
            out("    " + _c(f"{topic:<12}", "id") + _c(desc, "meta"))


def cmd_help(args, con=None):
    """`wl help [topic]` — render the index, or one topic + its see-also links (DESIGN §25)."""
    lang = _resolve_lang(args)
    topic = getattr(args, "topic", None)
    if not topic:
        _render_index(lang)
        return
    topic = topic.strip().lower()
    p = _topic_path(topic, lang)
    if p is None:
        known = list(_list_topics(lang))
        near = get_close_matches(topic, known, n=3, cutoff=0.5)
        msg = f"✗ no help topic '{topic}'"
        if near:
            msg += " — did you mean: " + ", ".join(near) + "?"
        msg += "\n  run `wl help` for the topic list"
        sys.exit(msg)
    meta, body = _parse_doc(p.read_text(encoding="utf-8"))
    _render_topic(topic, meta, body, lang)
