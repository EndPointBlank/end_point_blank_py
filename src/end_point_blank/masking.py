"""Client-side log masking.

A faithful port of ``end_point_blank_js/src/masking.py`` (parsePath/transform/
expand/regexReplaceAll/applyMasking) and the shared masking contract. Masking
NEVER raises: an uncompilable regex, a blank/malformed/unsupported path, a
non-JSON body, or a missing/null field all degrade to a no-op.
"""
from __future__ import annotations

import json
import re

# FIELD_MAP maps a masking target to the OUTGOING WIRE payload key (what the
# writers send and intake ingests). These MUST match intake's ingest keys:
# requests carry "headers"/"request"/"path", responses carry "body", errors
# "message". The log record type has nothing maskable.
FIELD_MAP = {
    "request": {"request_body": "request", "request_headers": "headers", "path": "path"},
    "response": {"response_body": "body"},
    "error": {"error_message": "message"},
    "log": {},
}

# Targets whose wire value is a JSON string body (decode/apply/re-encode).
JSON_TARGETS = ("request_body", "response_body")

# ---------------------------------------------------------------------------
# Constrained JSONPath subset — mirrors Intake.Masking.JsonPath exactly.
#
# Token tuples:
#   ("child", name)        — object child by key (case-sensitive)
#   ("index", n)           — 0-based array index
#   ("wildcard",)          — all object values / all array elements
#   ("descendant", name)   — recursive descent on key `name`
#
# parse_path returns a list of tokens, or None for blank/unsupported/garbled
# input (caller treats None as "matches nothing"). Never raises.
# ---------------------------------------------------------------------------

_NAME_RE = re.compile(r"[A-Za-z0-9_]+")
_INDEX_RE = re.compile(r"(\d+)\]")


def parse_path(string):
    if not isinstance(string, str):
        return None
    if not string or string[0] != "$":
        return None
    return _parse_tokens(string[1:], [])


def _parse_tokens(rest, acc):
    if rest == "":
        return acc

    # Recursive descent: ..name
    if rest.startswith(".."):
        m = _NAME_RE.match(rest[2:])
        if not m:
            return None
        return _parse_tokens(rest[2 + len(m.group(0)):], acc + [("descendant", m.group(0))])

    # Dot wildcard: .*
    if rest.startswith(".*"):
        return _parse_tokens(rest[2:], acc + [("wildcard",)])

    # Dot child: .name
    if rest.startswith("."):
        m = _NAME_RE.match(rest[1:])
        if not m:
            return None
        return _parse_tokens(rest[1 + len(m.group(0)):], acc + [("child", m.group(0))])

    # Bracket forms
    if rest.startswith("["):
        parsed = _parse_bracket(rest[1:])
        if not parsed:
            return None
        token, remaining = parsed
        return _parse_tokens(remaining, acc + [token])

    return None


def _parse_bracket(rest):
    # Wildcard inside brackets: [*]
    if rest.startswith("*]"):
        return (("wildcard",), rest[2:])
    # Quoted child names (single or double quotes); any chars between quotes.
    if rest.startswith("'"):
        return _parse_quoted(rest[1:], "'")
    if rest.startswith('"'):
        return _parse_quoted(rest[1:], '"')
    # Numeric index.
    m = _INDEX_RE.match(rest)
    if m:
        return (("index", int(m.group(1))), rest[len(m.group(0)):])
    return None


def _parse_quoted(rest, quote_char):
    closing = quote_char + "]"
    idx = rest.find(closing)
    if idx == -1:
        return None
    name = rest[:idx]
    return (("child", name), rest[idx + len(closing):])


def _is_plain_object(v):
    return isinstance(v, dict)


def transform(value, tokens, fn):
    """Walk ``value`` following ``tokens``, replacing each fully-matched
    location with ``fn(old_value)`` and rebuilding parents immutably.

    Never raises. ``tokens`` is None → return ``value`` unchanged.
    """
    if tokens is None:
        return value
    if len(tokens) == 0:
        return fn(value)

    token = tokens[0]
    rest = tokens[1:]
    kind = token[0]

    if kind == "child":
        name = token[1]
        if _is_plain_object(value) and name in value:
            out = dict(value)
            out[name] = transform(value[name], rest, fn)
            return out
        return value

    if kind == "index":
        idx = token[1]
        if isinstance(value, list) and 0 <= idx < len(value):
            out = list(value)
            out[idx] = transform(value[idx], rest, fn)
            return out
        return value

    if kind == "wildcard":
        if _is_plain_object(value):
            return {k: transform(v, rest, fn) for k, v in value.items()}
        if isinstance(value, list):
            return [transform(v, rest, fn) for v in value]
        return value

    if kind == "descendant":
        return _descend(value, token[1], rest, fn)

    return value


def _descend(value, key, rest, fn):
    if _is_plain_object(value):
        out = {}
        for k, v in value.items():
            descended = _descend(v, key, rest, fn)
            out[k] = transform(descended, rest, fn) if k == key else descended
        return out
    if isinstance(value, list):
        return [_descend(v, key, rest, fn) for v in value]
    return value


# ---------------------------------------------------------------------------
# Replacement backreferences (shared contract). Implemented explicitly (NOT via
# Python's native re replacement syntax): an out-of-range or non-participating
# group expands to "".
#
#   groups[0] = whole match, groups[n] = nth capture (missing → "").
#   $$           → literal "$"
#   $<digits>    → groups[N] or "" (the FULL consecutive digit run is one N)
#   $ + other    → literal "$" (also a trailing "$")
# ---------------------------------------------------------------------------


def expand(template, groups):
    out = []
    i = 0
    length = len(template)
    while i < length:
        ch = template[i]
        if ch != "$":
            out.append(ch)
            i += 1
            continue
        nxt = template[i + 1] if i + 1 < length else ""
        if nxt == "$":
            out.append("$")
            i += 2
            continue
        if nxt.isdigit():
            j = i + 1
            while j < length and template[j].isdigit():
                j += 1
            n = int(template[i + 1:j])
            g = groups[n] if n < len(groups) else None
            out.append("" if g is None else g)
            i = j
            continue
        # Lone "$" (followed by a non-digit/non-$, or end of string): literal.
        out.append("$")
        i += 1
    return "".join(out)


def regex_replace_all(pattern, string, template):
    """Global regex replacement using the explicit expander. Walks every
    non-overlapping match, expands the template per match, and copies gaps
    verbatim. Guards zero-width matches against an infinite loop.
    """
    out = []
    last = 0
    pos = 0
    length = len(string)
    while pos <= length:
        m = pattern.search(string, pos)
        if m is None:
            break
        start, end = m.start(), m.end()
        out.append(string[last:start])
        groups = [m.group(0)] + [g if g is not None else "" for g in m.groups()]
        out.append(expand(template, groups))
        last = end
        if end == start:
            # Zero-width match: copy one char and advance so we don't loop forever.
            if start < length:
                out.append(string[start])
            last = start + 1
            pos = start + 1
        else:
            pos = end
    out.append(string[last:])
    return "".join(out)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


def apply(payload, record_type, rules, hook):
    """Apply masking rules to a wire payload for the given record type.

    Each rule is a plain dict with keys ``target``/``path``/``regex``/
    ``replacement_value`` and an optional ``enabled`` flag. Never raises;
    any compile/parse failure or type mismatch degrades to a no-op. If
    ``hook`` is callable it runs last and its return value is returned.
    """
def apply(payload, record_type, rules, hook):
    """Apply masking rules to a wire payload for the given record type.

    Each rule is a plain dict with keys ``target``/``path``/``regex``/
    ``replacement_value`` and an optional ``enabled`` flag. Never raises;
    any compile/parse failure or type mismatch degrades to a no-op. If
    ``hook`` is callable it runs last and its return value is returned.
    """
    masked = payload
    for rule in rules or []:
        if not isinstance(rule, dict):
            continue
        if not rule.get("enabled", True):
            continue
        masked = _apply_rule(masked, record_type, rule)
    if callable(hook):
        return hook(masked, record_type)
    return masked
        return hook(masked, record_type)
    return masked


def _apply_rule(payload, record_type, rule):
    field_map = FIELD_MAP.get(record_type, {})
    key = field_map.get(rule.get("target"))
    if not key or key not in payload or payload[key] is None:
        return payload
    masked = _mask_field(payload[key], rule)
    if masked is payload[key]:
        return payload
    out = dict(payload)
    out[key] = masked
    return out


def _compiled(rule):
    """Returns a precomputed (tokens, regexp, repl) context for the rule."""
    rv = rule.get("replacement_value")
    repl = rv if isinstance(rv, str) and rv != "" else "..."

    path = rule.get("path")
    has_path = isinstance(path, str) and path != ""
    tokens = parse_path(path) if has_path else None
    path_invalid = has_path and tokens is None

    regexp = None
    regex = rule.get("regex")
    if not path_invalid and isinstance(regex, str) and regex != "":
        try:
            regexp = re.compile(regex)
        except (re.error, TypeError):
            regexp = None

    return tokens, regexp, repl


def _mask_field(value, rule):
    tokens, regexp, repl = _compiled(rule)

    if isinstance(value, str):
        if rule.get("target") in JSON_TARGETS:
            try:
                decoded = json.loads(value)
            except (ValueError, TypeError):
                return _apply_to_raw_string(value, regexp, repl)
            return json.dumps(_apply_to_value(decoded, tokens, regexp, repl))
        # path / error_message: plain strings — path no-ops, only regex applies.
        return _apply_to_raw_string(value, regexp, repl)

    # request_headers: a map. Path applies to the map; regex to string leaves.
    if _is_plain_object(value):
        return _apply_to_value(value, tokens, regexp, repl)

    return value


def _apply_to_raw_string(value, regexp, repl):
    if regexp is not None:
        return regex_replace_all(regexp, value, repl)
    return value


def _apply_to_value(value, tokens, regexp, repl):
    # path + regex: select nodes, apply regex to leaves within each.
    if tokens is not None and regexp is not None:
        return transform(value, tokens, lambda old: regex_replace_leaves(old, regexp, repl))
    # path only: replace each selected node entirely.
    if tokens is not None:
        return transform(value, tokens, lambda _old: repl)
    # regex only: substitute across every string leaf.
    if regexp is not None:
        return regex_replace_leaves(value, regexp, repl)
    # no usable path or regex: no-op.
    return value


def regex_replace_leaves(node, regexp, repl):
    """Recurse over containers; substitute on every string leaf."""
    if isinstance(node, str):
        return regex_replace_all(regexp, node, repl)
    if isinstance(node, list):
        return [regex_replace_leaves(v, regexp, repl) for v in node]
    if _is_plain_object(node):
        return {k: regex_replace_leaves(v, regexp, repl) for k, v in node.items()}
    return node
