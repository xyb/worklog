"""TOON (Token-Oriented Object Notation) encoder — a compact, LLM-friendly
JSON alternative (toonformat.dev, spec v3.3). Hand-rolled and zero-dependency
by principle G3 (borrow the technique, not the library).

Encodes the JSON data model: objects use indentation instead of braces; arrays
declare their length once; a uniform array of primitive-only objects becomes a
table — field names on the header, then one row of values per line — which is
where the ~40% token saving over JSON comes from. Output uses the comma
delimiter, 2-space indent, no key folding, no trailing newline (all spec
defaults). Lossless: decode(encode(x)) == x under the JSON data model.
"""
from __future__ import annotations

import math
import re
from dataclasses import is_dataclass, asdict

_IND = "  "  # 2 spaces per depth level (spec §12 default)
_DELIM = ","  # comma document delimiter (spec §11 default)
# unquoted key: spec §7.3 — ^[A-Za-z_][A-Za-z0-9_.]*$
_BARE_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
# numeric-like string that MUST be quoted so it doesn't read as a number (§7.2)
_NUMERIC_LIKE = re.compile(r"^-?\d+(?:\.\d+)?(?:e[+-]?\d+)?$", re.IGNORECASE)
# chars in a string value that force quoting (§7.2): colon, quote, backslash,
# brackets, braces, and the active delimiter (comma).
_FORCE_QUOTE_CHARS = set(':"\\[]{}' + _DELIM)


def encode(value) -> str:
    """A JSON value (dataclasses accepted) → a TOON document, no trailing newline."""
    value = _normalize(value)
    lines: list[str] = []
    if isinstance(value, dict):
        _emit_object(value, 0, lines)          # empty dict → no lines → ""
    elif isinstance(value, list):
        if not value:
            lines.append("[]")                  # empty root array (§9.1)
        else:
            _emit_array("", value, 0, lines)
    else:
        lines.append(_prim(value))              # root primitive (§5)
    return "\n".join(lines)


# --- normalization (§3) -----------------------------------------------------

def _normalize(v):
    if is_dataclass(v) and not isinstance(v, type):
        return _normalize(asdict(v))
    if isinstance(v, dict):
        return {str(k): _normalize(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_normalize(x) for x in v]
    return v


# --- primitives (§2, §7) ----------------------------------------------------

def _num(n) -> str:
    if isinstance(n, int):
        return str(n)
    if n != n or n in (math.inf, -math.inf):   # NaN / ±Inf → null (§3)
        return "null"
    if n == 0:                                  # also folds -0.0 → 0 (§2)
        return "0"
    if float(n).is_integer() and abs(n) < 1e21:
        return str(int(n))
    # ponytail: repr gives the shortest round-tripping decimal; wl's floats
    # (cosine scores, durations) never reach the 1e±21 exponent edge the spec
    # canonicalizes, so no exponent-expansion path is needed here.
    return repr(n)


def _escape(s: str) -> str:
    out = []
    for c in s:
        if c == "\\":
            out.append("\\\\")
        elif c == '"':
            out.append('\\"')
        elif c == "\n":
            out.append("\\n")
        elif c == "\r":
            out.append("\\r")
        elif c == "\t":
            out.append("\\t")
        elif ord(c) < 0x20:
            out.append(f"\\u{ord(c):04x}")
        else:
            out.append(c)
    return "".join(out)


def _needs_quote(s: str) -> bool:
    if s == "":
        return True
    if s != s.strip():                          # leading/trailing whitespace
        return True
    if s in ("true", "false", "null"):
        return True
    if _NUMERIC_LIKE.match(s):
        return True
    if s == "-" or s.startswith("-"):
        return True
    return any(c in _FORCE_QUOTE_CHARS or ord(c) < 0x20 for c in s)


def _prim(v) -> str:
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, (int, float)):
        return _num(v)
    s = v if isinstance(v, str) else str(v)
    return '"' + _escape(s) + '"' if _needs_quote(s) else s


def _key(k) -> str:
    k = str(k)
    return k if _BARE_KEY.match(k) else '"' + _escape(k) + '"'


def _is_primitive(v) -> bool:
    return v is None or isinstance(v, (bool, int, float, str))


def _tabular_ok(arr) -> bool:
    """§9.3: every element a non-empty object, same key set, all values primitive."""
    if not arr or not all(isinstance(x, dict) and x for x in arr):
        return False
    keys = set(arr[0])
    return all(set(x) == keys and all(_is_primitive(v) for v in x.values()) for x in arr)


# --- structure (§8–§10) -----------------------------------------------------

def _emit_object(obj, depth, lines):
    for k, v in obj.items():
        _emit_field(k, v, depth, lines)


def _emit_field(key, v, depth, lines):
    ind = _IND * depth
    kk = _key(key)
    if isinstance(v, dict):
        lines.append(f"{ind}{kk}:")             # nested/empty object (§8)
        if v:
            _emit_object(v, depth + 1, lines)
    elif isinstance(v, list):
        _emit_array(kk, v, depth, lines)
    else:
        lines.append(f"{ind}{kk}: {_prim(v)}")


def _emit_array(key, arr, depth, lines):
    """key is the already-encoded key prefix ("" for a root/hyphen-spliced array)."""
    ind = _IND * depth
    n = len(arr)
    if n == 0:
        lines.append(f"{ind}{key}: []")
        return
    if all(_is_primitive(x) for x in arr):      # inline primitive array (§9.1)
        lines.append(f"{ind}{key}[{n}]: " + _DELIM.join(_prim(x) for x in arr))
        return
    if _tabular_ok(arr):                         # tabular (§9.3)
        fields = list(arr[0].keys())
        header = _DELIM.join(_key(f) for f in fields)
        lines.append(f"{ind}{key}[{n}]{{{header}}}:")
        for obj in arr:
            lines.append(_IND * (depth + 1) + _DELIM.join(_prim(obj[f]) for f in fields))
        return
    lines.append(f"{ind}{key}[{n}]:")           # expanded list (§9.4)
    for item in arr:
        _emit_list_item(item, depth + 1, lines)


def _emit_list_item(item, depth, lines):
    ind = _IND * depth
    if isinstance(item, dict):
        if not item:
            lines.append(f"{ind}-")             # empty object list item (§10)
            return
        # Emit all fields at depth+1, then splice "- " onto the first line. The
        # replaced indent (depth+1)*2 == depth*2 + len("- "), so continuation
        # lines stay aligned — and a tabular first field lands its rows at
        # depth+2 with siblings at depth+1 exactly as §10 requires.
        buf: list[str] = []
        _emit_object(item, depth + 1, buf)
        buf[0] = ind + "- " + buf[0][len(_IND * (depth + 1)):]
        lines.extend(buf)
    elif isinstance(item, list):
        buf = []
        _emit_array("", item, depth + 1, buf)
        buf[0] = ind + "- " + buf[0][len(_IND * (depth + 1)):]
        lines.extend(buf)
    else:
        lines.append(f"{ind}- {_prim(item)}")
