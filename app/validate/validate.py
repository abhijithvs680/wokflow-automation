"""Structural validation of a compiled VizWorkflow document (local, free).

Checks:
  graph    - exactly one entry (source==0), unique ids, targets resolve,
             all nodes reachable, terminates (no target-less cycles)
  labels   - unique non-empty labels for blocks that are referenced
  refs     - every {Label.field} template references an upstream label,
             a system variable, or a set variable
  types    - block types exist in the palette
  ss       - spreadsheet columns used in filters/field-mapping exist (when a
             schema catalog is provided)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..catalog import blocks as cat

_TERMINAL = (0, "0", "", None)
_REF_RE = re.compile(r"\{([A-Za-z0-9_ .-]+?)\}")
_HELPER_RE = re.compile(r"\{%.*?%\}", re.S)


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _is_terminal(v) -> bool:
    return v in _TERMINAL


def _iter_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _iter_strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _iter_strings(v)


def validate_workflow(doc: dict, ss_catalog: dict[str, dict] | None = None) -> ValidationResult:
    """ss_catalog: master_ssid -> {name, columns: [names]} (optional)."""
    res = ValidationResult()
    w_objects = doc.get("w_objects") or []
    if not w_objects:
        res.errors.append("w_objects is empty")
        return res

    by_id: dict[str, dict] = {}
    for o in w_objects:
        bid = o.get("id")
        if not bid:
            res.errors.append("block without id")
            continue
        if bid in by_id:
            res.errors.append(f"duplicate block id '{bid}'")
        by_id[bid] = o

    # entry
    entries = [o for o in w_objects if o.get("source") in (0, "0")]
    if len(entries) != 1:
        ids = [o.get("id") for o in entries]
        res.errors.append(f"expected exactly 1 entry block (source==0), found {len(entries)}: {ids}")
    entry = entries[0] if entries else w_objects[0]

    # types + targets
    for o in w_objects:
        t = o.get("type")
        if t not in cat.palette():
            res.errors.append(f"block '{o.get('id')}': unknown type '{t}'")
        for key in ("target", "target_yes", "target_no"):
            v = o.get(key)
            if v is not None and not _is_terminal(v) and v not in by_id:
                res.errors.append(f"block '{o.get('id')}': {key} -> missing block '{v}'")
        if t == "condition":
            if _is_terminal(o.get("target_yes")) and _is_terminal(o.get("target_no")):
                res.errors.append(f"condition '{o.get('id')}' has neither target_yes nor target_no")

    # reachability
    seen: set[str] = set()
    stack = [entry.get("id")]
    while stack:
        cur = stack.pop()
        if cur in seen or cur not in by_id:
            continue
        seen.add(cur)
        o = by_id[cur]
        for key in ("target", "target_yes", "target_no"):
            v = o.get(key)
            if not _is_terminal(v):
                stack.append(v)
    unreachable = set(by_id) - seen
    for bid in sorted(unreachable):
        res.warnings.append(f"block '{bid}' is unreachable from the entry block")

    # termination: at least one reachable terminal
    def _has_terminal(o: dict) -> bool:
        if o.get("type") == "condition":
            return _is_terminal(o.get("target_yes")) or _is_terminal(o.get("target_no"))
        return _is_terminal(o.get("target"))

    if not any(_has_terminal(by_id[b]) for b in seen if b in by_id):
        res.errors.append("no reachable terminal block (target==0); workflow cannot finish")

    # labels + upstream reference check
    labels: dict[str, str] = {}
    for o in w_objects:
        lbl = (o.get("block_properties") or {}).get("label") or ""
        if lbl:
            if lbl in labels.values():
                res.warnings.append(f"duplicate label '{lbl}' — {{{lbl}.x}} references are ambiguous")
            labels[o["id"]] = lbl

    known_names = set(labels.values()) | cat.SYSTEM_VARS
    for o in w_objects:
        texts = list(_iter_strings(o.get("block_properties"))) + list(_iter_strings(o.get("properties")))
        for text in texts:
            cleaned = _HELPER_RE.sub("", text)
            for m in _REF_RE.finditer(cleaned):
                name = m.group(1).split(".")[0].strip()
                if not name or name in known_names:
                    continue
                if name.replace("_", "").replace("-", "").isdigit():
                    continue
                res.warnings.append(
                    f"block '{o['id']}': reference '{{{m.group(1)}}}' does not match any block label or system var"
                )

    # spreadsheet column checks
    if ss_catalog:
        for o in w_objects:
            if o.get("type") not in cat.SS_TYPES:
                continue
            p = o.get("block_properties") or {}
            ssid = p.get("s_master_ssid") or p.get("ssid") or ""
            ss = ss_catalog.get(ssid)
            if not ss:
                res.errors.append(f"block '{o['id']}': spreadsheet '{ssid}' not found in tenant catalog")
                continue
            cols = set(ss.get("columns") or [])
            for col in (p.get("filters") or {}):
                if col not in cols and col != "rowID":
                    res.errors.append(
                        f"block '{o['id']}': filter column '{col}' not in spreadsheet '{ss.get('name', ssid)}' "
                        f"(columns: {sorted(cols)})"
                    )
            for m in ((o.get("properties") or {}).get("field-mapping") or []):
                col = m.get("insertcolumn", "")
                if col and col not in cols:
                    res.errors.append(
                        f"block '{o['id']}': field-mapping column '{col}' not in spreadsheet '{ss.get('name', ssid)}'"
                    )

    return res
