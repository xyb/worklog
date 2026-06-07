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
import re
import sys
from difflib import get_close_matches
from pathlib import Path

from .. import render as _render
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


def topic_exists(name, lang=FALLBACK_LANG):
    """True if a help topic doc exists for `name` (in `lang` or the en fallback). Used by the
    parser to auto-link a command's --help to `wl help <name>` only when the topic is there."""
    return _topic_path(name, lang) is not None


def topic_names(lang=FALLBACK_LANG):
    """All topic ids (for shell completion of `wl help <topic>`)."""
    return list(_list_topics(lang))


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


# --- the restricted Markdown subset wl help renders (see CONTRIBUTING.md + DESIGN §25) ---
# Dependency-free: one inline tokenizer + line-based block handling. Intentionally small;
# bodies are written to read fine as plain text too. The order in the alternation matters
# (code span and **bold** before *italic*). `_italic_` is deliberately NOT supported — bare
# underscores are too common in identifiers (node_id, closed_at, $WORKLOG_LANG).
_MD_INLINE = re.compile(
    r"(`[^`\n]+`"                       # `inline code`
    r"|\*\*[^*\n]+\*\*"                 # **bold**
    r"|\*[^*\n]+\*"                     # *italic*
    r"|\[[^\]\n]+\]\([^)\n]+\)"         # [text](url)
    r"|https?://[^\s)]+)"               # bare URL
)
# `code` → "kind" (bright cyan, theme-aware) so it's brighter than the dim body; **bold** →
# the strong "header" style (plain [bold]=ESC[1m is too faint); *italic* → italic.
_MD_STYLE = {"`": "kind", "**": "header", "*": "italic"}


def _color_on():
    return _render._CONSOLE is not None


def _strip_md(text):
    """Plain-text fallback: drop the inline markers so no `**`/`` ` ``/`[..](..)` leaks."""
    text = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*\n]+)\*", r"\1", text)
    text = re.sub(r"`([^`\n]+)`", r"\1", text)
    text = re.sub(r"\[([^\]\n]+)\]\(([^)\n]+)\)", r"\1 (\2)", text)
    return text


def _md_inline(text):
    """Render one line's inline Markdown to an output-ready string: escaped literals + rich
    markup for styled spans when color is on; markers stripped to plain text when off. The
    escaping is essential — bodies contain literal `[ ]` / `[x]` / `[#A]` that rich markup
    would otherwise mis-parse (and crash on a stray `[/]`)."""
    if not _color_on():
        return _strip_md(text)
    # prose runs render in the dim "body" style so bold-white + colored refs stand out
    # against them (3 tiers: body < refs < bold), Claude-Code-style.
    out_parts, pos = [], 0
    for m in _MD_INLINE.finditer(text):
        out_parts.append(_c(text[pos:m.start()], "body"))
        tok = m.group(0)
        if tok.startswith(("`", "*")):
            mark = "**" if tok.startswith("**") else tok[0]
            out_parts.append(_c(tok[len(mark):-len(mark)], _MD_STYLE[mark]))
        elif tok.startswith("["):
            lm = re.match(r"\[([^\]]+)\]\(([^)]+)\)", tok)
            out_parts.append(_c(lm.group(1), "underline") + " " + _c(lm.group(2), "kind"))
        else:  # bare URL
            out_parts.append(_c(tok, "underline"))
        pos = m.end()
    out_parts.append(_c(text[pos:], "body"))
    return "".join(out_parts)


def _render_body(body):
    """Render a topic body with the restricted Markdown subset: ATX headings, fenced code
    blocks (``` …), and inline **bold** / *italic* / `code` / [text](url) / bare URLs.
    Everything else is preserved verbatim (bodies are pre-formatted with indentation)."""
    in_code = False
    for line in body.rstrip("\n").splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code          # drop the fence line itself
            continue
        if in_code:
            out(_c(line, "meta"))          # code block: dim, no inline parsing
            continue
        heading = line.lstrip("#").strip()
        if line.startswith("#") and heading:
            out(_c(heading, "header"))
        else:
            out(_md_inline(line))


def _render_topic(topic, meta, body, lang):
    title = meta.get("title", topic)
    out(_c(title, "header"))
    out(_c("─" * min(len(title), 60), "meta"))
    out("")
    _render_body(body)
    see = meta.get("see_also") or []
    if see:
        out("")
        # each see-also is a runnable `wl help <topic>` — color it like inline references
        # (cyan), not the dim "id" grey, so the links stand out.
        links = _c(" · ", "meta").join(_c(t, "kind") for t in see)
        out(_c("See also: ", "meta") + links + _c("   (wl help <topic>)", "meta"))


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
