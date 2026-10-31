"""
Lightroom smart collections: decode their rules and work out which photos match.

Lightroom stores each smart collection as a Lua table in
AgLibraryCollectionContent.content (owningModule = "ag.library.smart_collection"):

    s = {
        { criteria = "rating",   operation = ">=",  value = 4 },
        { criteria = "keywords", operation = "any", value = "italy rome", value2 = "" },
        combine = "intersect",          -- all rules ("union" = any, "exclude" = none)
    }

A rule without `criteria` that holds its own numbered rules and `combine` is a
nested group. Rules we can't evaluate make the whole collection "rules only":
it's listed in the report but no photos are tagged, so nothing is tagged wrongly.

Photos are duck-typed: each needs .rating, .label, .pick, .keywords (list of
"A|B|C" paths), .collections (list of "Set|Name" paths), .file_format, .facts (dict).
"""
from __future__ import annotations

import datetime as _dt
import re

LABEL_NUMBERS = {1: "red", 2: "yellow", 3: "green", 4: "blue", 5: "purple"}
COMBINE_WORDS = {"intersect": "ALL", "union": "ANY", "exclude": "NONE"}
LR_EPOCH = _dt.datetime(2001, 1, 1)


class Unsupported(Exception):
    """A rule this tool can't evaluate."""


# ---------------------------------------------------------------------------
# Rule tree helpers
# ---------------------------------------------------------------------------

def rule_items(group: dict) -> list:
    """Numbered children of a rule group, in order."""
    if isinstance(group, list):
        return group
    return [group[k] for k in sorted(k for k in group if isinstance(k, int))]


def _is_group(item) -> bool:
    return isinstance(item, (dict, list)) and not (isinstance(item, dict) and "criteria" in item)


def describe(group, indent: int = 0) -> list[str]:
    """Readable text for a rule group, one line per rule."""
    pad = "  " * indent
    comb = group.get("combine", "intersect") if isinstance(group, dict) else "intersect"
    lines = [f"{pad}Match {COMBINE_WORDS.get(comb, comb)} of:"]
    for item in rule_items(group):
        if _is_group(item):
            lines += describe(item, indent + 1)
            continue
        val = item.get("value", "")
        extra = item.get("value2")
        units = item.get("_units") or item.get("value_units")
        text = f"{item.get('criteria')} {item.get('operation')} {val!r}"
        if extra not in (None, "", 0) and item.get("operation") == "in":
            text += f" and {extra!r}"
        if units:
            text += f" {units}"
        lines.append(f"{pad}  - {text}")
    return lines


# ---------------------------------------------------------------------------
# Text matching
# ---------------------------------------------------------------------------

def _terms(value) -> list[str]:
    return [t for t in str(value or "").lower().split() if t]


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"[\s/\\|,;:._\-()]+", text.lower()) if w]


def _term_hits(term: str, texts: list[str], op: str) -> bool:
    if op in ("words",):
        return any(term in _words(t) for t in texts)
    if op == "beginsWith":
        return any(t.startswith(term) or any(w.startswith(term) for w in _words(t)) for t in texts)
    if op == "endsWith":
        return any(t.endswith(term) or any(w.endswith(term) for w in _words(t)) for t in texts)
    return any(term in t for t in texts)


def match_text(texts: list[str], op: str, value) -> bool:
    """Lightroom text operators against one or more text values of a photo."""
    texts = [str(t).lower() for t in texts if t not in (None, "")]
    if op == "empty":
        return not texts
    if op == "notEmpty":
        return bool(texts)
    if op in ("==", "!="):
        hit = str(value or "").lower() in texts
        return hit if op == "==" else not hit
    terms = _terms(value)
    if not terms:
        return True
    if op in ("any",):
        return any(_term_hits(t, texts, op) for t in terms)
    if op in ("all", "words", "beginsWith", "endsWith"):
        return all(_term_hits(t, texts, op) for t in terms)
    if op == "noneOf":
        return not any(_term_hits(t, texts, "any") for t in terms)
    raise Unsupported(f"text operation {op!r}")


# ---------------------------------------------------------------------------
# Numbers and dates
# ---------------------------------------------------------------------------

def _num(v):
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def match_number(x, op: str, value, value2=None, null_as=None) -> bool:
    x = _num(x)
    if x is None:
        x = null_as
    if x is None:
        return op == "!="
    a, b = _num(value), _num(value2)
    if a is None:
        raise Unsupported(f"number value {value!r}")
    ops = {"==": x == a, "!=": x != a, ">": x > a, "<": x < a, ">=": x >= a, "<=": x <= a}
    if op in ops:
        return ops[op]
    if op == "in":
        lo, hi = (a, b) if b is not None else (a, a)
        return min(lo, hi) <= x <= max(lo, hi)
    raise Unsupported(f"number operation {op!r}")


def _to_date(v) -> _dt.date | None:
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return None
    s = str(v).strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return _dt.date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    m = re.match(r"(\d{4})-(\d{2})$", s)
    if m:
        return _dt.date(int(m[1]), int(m[2]), 1)
    m = re.match(r"(\d{4})$", s)
    if m:
        return _dt.date(int(m[1]), 1, 1)
    return None


def _shift(today: _dt.date, n: int, units: str) -> _dt.date:
    units = (units or "days").lower().rstrip("s")
    if units == "day":
        return today - _dt.timedelta(days=n)
    if units == "week":
        return today - _dt.timedelta(weeks=n)
    if units in ("month", "year"):
        months = n * (12 if units == "year" else 1)
        y, m = divmod(today.year * 12 + today.month - 1 - months, 12)
        d = min(today.day, 28)
        return _dt.date(y, m + 1, d)
    if units == "hour":
        return today
    raise Unsupported(f"date units {units!r}")


def match_date(d: _dt.date | None, rule: dict, today: _dt.date) -> bool:
    op = rule.get("operation")
    if d is None:
        return op == "!="
    if op in ("today",):
        return d == today
    if op == "yesterday":
        return d == today - _dt.timedelta(days=1)
    if op == "thisWeek":
        return d >= today - _dt.timedelta(days=6)
    if op == "thisMonth":
        return (d.year, d.month) == (today.year, today.month)
    if op == "thisYear":
        return d.year == today.year
    if op in ("inLast", "notInLast"):
        n = int(_num(rule.get("value")) or 0)
        start = _shift(today, n, rule.get("_units") or rule.get("value_units") or "days")
        hit = d >= start
        return hit if op == "inLast" else not hit
    a = _to_date(rule.get("value"))
    if a is None:
        raise Unsupported(f"date value {rule.get('value')!r}")
    if op == "in":
        b = _to_date(rule.get("value2")) or a
        lo, hi = min(a, b), max(a, b)
        return lo <= d <= hi
    ops = {"==": d == a, "!=": d != a, ">": d > a, "<": d < a, ">=": d >= a, "<=": d <= a}
    if op in ops:
        return ops[op]
    raise Unsupported(f"date operation {op!r}")


def _bool_rule(actual: bool, rule: dict) -> bool:
    op = rule.get("operation")
    if op == "isTrue":
        want = True
    elif op == "isFalse":
        want = False
    else:
        want = bool(rule.get("value"))
        if op == "!=":
            want = not want
    return bool(actual) == want


# ---------------------------------------------------------------------------
# Criteria
# ---------------------------------------------------------------------------

def _keyword_names(p) -> list[str]:
    """Every keyword on the photo plus its parents (choosing 'Italy' includes 'Italy|Rome')."""
    out = []
    for path in p.keywords:
        for part in path.split("|"):
            if part and part not in out:
                out.append(part)
    return out


def _collection_names(p) -> list[str]:
    out = []
    for path in p.collections:
        for part in path.split("|"):
            if part and part not in out:
                out.append(part)
    return out


TEXT_FACTS = {
    "filename": "filename", "folder": "folder", "copyname": "copy_name", "copyName": "copy_name",
    "camera": "camera", "lens": "lens", "creator": "creator", "title": "title",
    "caption": "caption", "headline": "title", "cameraSerialNumber": "camera_serial",
}
NUMBER_FACTS = {"isoSpeedRating": "iso", "focalLength": "focal_length", "aperture": "aperture"}


def eval_rule(rule: dict, p, today: _dt.date) -> bool:
    c, op, v = rule.get("criteria"), rule.get("operation"), rule.get("value")
    f = p.facts
    if c == "rating":
        return match_number(p.rating, op, v, rule.get("value2"), null_as=0)
    if c == "pick":
        return match_number(p.pick, op, v, null_as=0)
    if c in ("labelColor", "labelText"):
        want = LABEL_NUMBERS.get(v, v) if isinstance(v, int) else v
        want = "" if str(want).lower() == "none" else str(want or "")
        have = (p.label or "").lower()
        if op == "==":
            return have == want.lower()
        if op == "!=":
            return have != want.lower()
        return match_text([p.label], op, want)
    if c == "keywords":
        return match_text(_keyword_names(p), op, v)
    if c == "collection":
        return match_text(_collection_names(p), op, v)
    if c in TEXT_FACTS:
        return match_text([f.get(TEXT_FACTS[c])], op, v)
    if c in NUMBER_FACTS:
        return match_number(f.get(NUMBER_FACTS[c]), op, v, rule.get("value2"))
    if c == "shutterSpeed":
        # Stored as log2 of 1/seconds, so "longer than" (>) means a smaller number.
        flip = {">": "<", "<": ">", ">=": "<=", "<=": ">="}
        return match_number(f.get("shutter"), flip.get(op, op), v, rule.get("value2"))
    if c == "fileFormat":
        hit = (p.file_format or "").upper() == str(v or "").upper()
        if op == "==":
            return hit
        if op == "!=":
            return not hit
        raise Unsupported(f"fileFormat {op!r}")
    if c == "captureTime":
        return match_date(_to_date(f.get("capture_time")), rule, today)
    if c == "touchTime":
        t = _num(f.get("touch_time"))
        d = (LR_EPOCH + _dt.timedelta(seconds=t)).date() if t else None
        return match_date(d, rule, today)
    if c == "hasGPSData":
        return _bool_rule(bool(f.get("has_gps")), rule)
    if c == "flashFired":
        return _bool_rule(bool(f.get("flash")), rule)
    if c == "hasAdjustments":
        has = bool(f.get("has_adjustments"))
        return (not has) if op == "isFalse" else has
    if c == "treatment":
        if str(v).lower() != "grayscale":
            raise Unsupported(f"treatment {v!r}")
        gray = bool(f.get("grayscale"))
        return (not gray) if op == "!=" else gray
    if c == "aspectRatio":
        r = _num(f.get("aspect"))
        if r is None:
            return op == "!="
        shape = "square" if abs(r - 1) < 0.01 else ("portrait" if r < 1 else "landscape")
        return (shape == v) if op == "==" else (shape != v)
    if c in ("all", "metadata", "anyText", "allText"):
        texts = (_keyword_names(p) + _collection_names(p) +
                 [f.get(k) for k in ("filename", "folder", "title", "caption", "creator",
                                     "camera", "lens", "copy_name")])
        return match_text(texts, op, v)
    raise Unsupported(f"criteria {c!r}")


def eval_group(group, p, today: _dt.date) -> bool:
    comb = group.get("combine", "intersect") if isinstance(group, dict) else "intersect"
    results = (eval_group(it, p, today) if _is_group(it) else eval_rule(it, p, today)
               for it in rule_items(group))
    if comb == "intersect":
        return all(results)
    if comb == "union":
        return any(results)
    if comb == "exclude":
        return not any(results)
    raise Unsupported(f"combine {comb!r}")


def check_supported(group, sample) -> None:
    """Raise Unsupported if any rule can't be evaluated (tested on one photo)."""
    for it in rule_items(group):
        if _is_group(it):
            check_supported(it, sample)
        else:
            eval_rule(it, sample, _dt.date.today())
    comb = group.get("combine", "intersect") if isinstance(group, dict) else "intersect"
    if comb not in COMBINE_WORDS:
        raise Unsupported(f"combine {comb!r}")


class _Blank:
    rating = 0
    label = ""
    pick = 0
    keywords: list = []
    collections: list = []
    file_format = ""
    facts: dict = {}


def evaluate(defs: list[dict], photos: list, today: _dt.date | None = None) -> list[dict]:
    """defs: [{"path": "Set|Name", "rules": {...} or None, "error": str}]
    Returns one result per smart collection and adds matching paths to
    each photo's .smart_collections."""
    today = today or _dt.date.today()
    results = []
    for d in defs:
        res = {"path": d["path"], "count": 0, "status": "", "rules": [], "reason": ""}
        rules = d.get("rules")
        if rules is None:
            res.update(status="unreadable", reason=d.get("error", "could not read rules"))
            results.append(res)
            continue
        res["rules"] = describe(rules)
        try:
            check_supported(rules, photos[0] if photos else _Blank())
            hits = [p for p in photos if eval_group(rules, p, today)]
        except Unsupported as e:
            res.update(status="rules_only", reason=f"can't evaluate {e}")
            results.append(res)
            continue
        for p in hits:
            p.smart_collections.append(d["path"])
        res.update(status="tagged", count=len(hits))
        if _uses_relative_dates(rules):
            res["reason"] = "uses dates relative to today, so it was evaluated as of today"
        results.append(res)
    return results


def _uses_relative_dates(group) -> bool:
    rel = {"inLast", "notInLast", "today", "yesterday", "thisWeek", "thisMonth", "thisYear"}
    for it in rule_items(group):
        if _is_group(it):
            if _uses_relative_dates(it):
                return True
        elif it.get("operation") in rel:
            return True
    return False


def write_listing(results: list[dict], path: str) -> None:
    """Human-readable list of every smart collection and its rules."""
    lines = ["Lightroom smart collections",
             "===========================",
             "",
             "Tagged ones became 'Lightroom|smart collections|<name>' tags (a snapshot of",
             "the photos that matched). The rules are listed so you can recreate them as",
             "saved searches in your new app.",
             ""]
    for r in results:
        name = r["path"].replace("|", " > ")
        if r["status"] == "tagged":
            head = f"{name}  [tagged {r['count']} photo{'' if r['count'] == 1 else 's'}]"
        elif r["status"] == "rules_only":
            head = f"{name}  [not tagged: {r['reason']}]"
        else:
            head = f"{name}  [not read: {r['reason']}]"
        lines.append(head)
        lines += ["  " + ln for ln in r["rules"]]
        if r["status"] == "tagged" and r["reason"]:
            lines.append(f"  Note: {r['reason']}")
        lines.append("")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
