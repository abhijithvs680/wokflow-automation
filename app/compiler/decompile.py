"""VizWorkflow document -> IR (for the natural-language edit flow).

Inverse of compile.py. Labels are taken from block_properties.label; blocks
with empty labels get a synthetic unique label ('<type>_<n>' from their id) so
the IR stays referenceable. Unknown/extra block_properties keys are preserved
in step.config so a recompile does not lose configuration.
"""
from __future__ import annotations

from typing import Any

from ..catalog import blocks as cat
from ..ir.schema import IRStep, IRTrigger, WorkflowIR

# keys the compiler regenerates; everything else round-trips through config
_COMMON_MANAGED = {"label", "description", "blockType", "dynamic_flag", "debug_mode"}
_TYPE_MANAGED = {
    "setvariable": {"variables"},
    "condition": {"message"},
    "genericpost": {"auth_required"},
    "livecloudfunction": {"objId", "test"},
    "executeworkflow": {"shortCode"},
}
_SS_MANAGED = {
    "filters", "filter_operators", "s_master_ssid", "ssid", "d_master_ssid",
    "ss_short_code", "sstabaction_source-1", "big_data", "row_count",
    "limit_offset", "limit_to", "distinct_column", "alias_column",
    "disable_realtime", "test",
}


def _managed_props(block_type: str) -> set[str]:
    managed = set(_COMMON_MANAGED) | _TYPE_MANAGED.get(block_type, set())
    if block_type in cat.SS_TYPES:
        managed |= _SS_MANAGED
    return managed


def _parse_variables(s: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in (s or "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k] = v
    return out


def _terminal(v: Any) -> bool:
    return v in (0, "0", "", None)


def decompile(doc: dict) -> tuple[WorkflowIR, dict[str, str]]:
    """Returns (ir, label_to_block_id) so edits can preserve existing ids."""
    w_objects: list[dict] = doc.get("w_objects", [])
    by_id = {o["id"]: o for o in w_objects}

    entry = next((o for o in w_objects if o.get("source") in (0, "0")), None)
    if entry is None:
        raise ValueError("workflow has no entry block (source == 0)")

    # unique labels
    labels: dict[str, str] = {}
    used: set[str] = set()
    for o in w_objects:
        lbl = (o.get("block_properties") or {}).get("label") or ""
        if not lbl or lbl in used:
            lbl = f"{o['type']}_{str(o['id']).rsplit('_', 1)[-1]}"
        used.add(lbl)
        labels[o["id"]] = lbl

    def lbl(bid: Any) -> str | None:
        return None if _terminal(bid) else labels.get(bid)

    trigger_type = entry["type"] if entry["type"] in cat.ENTRY_TYPES else "datatransfer"
    ep = entry.get("block_properties") or {}
    trigger = IRTrigger(
        type=trigger_type,
        label=labels[entry["id"]],
        auth_required=ep.get("auth_required") == "1",
        next=lbl(entry.get("target")),
    )

    steps: list[IRStep] = []
    for o in w_objects:
        if o is entry:
            continue
        steps.append(_decompile_block(o, labels, lbl))

    ir = WorkflowIR(
        name=doc.get("name", ""),
        description=doc.get("desc", ""),
        trigger=trigger,
        steps=steps,
    )
    label_to_id = {labels[o["id"]]: o["id"] for o in w_objects}
    return ir, label_to_id


def _decompile_block(o: dict, labels: dict, lbl) -> IRStep:
    p = o.get("block_properties") or {}
    t = o["type"]
    info = cat.block_info(t) or {}

    kw: dict[str, Any] = {
        "label": labels[o["id"]],
        "block": t,
        "description": p.get("description", ""),
    }

    if t == "condition":
        kw["expr"] = p.get("message", "")
        kw["yes"] = lbl(o.get("target_yes"))
        kw["no"] = lbl(o.get("target_no"))
    else:
        nxt = lbl(o.get("target"))
        kw["next"] = nxt
        kw["end"] = nxt is None

    if t == "setvariable":
        kw["set"] = _parse_variables(p.get("variables", ""))
    elif t in cat.SS_TYPES:
        id_key = info.get("spreadsheet", "ssid")
        kw["spreadsheet"] = p.get(id_key) or p.get("ssid") or p.get("s_master_ssid")
        if isinstance(p.get("filters"), dict):
            kw["filters"] = dict(p["filters"])
            kw["operators"] = dict(p.get("filter_operators") or {})
        fm = (o.get("properties") or {}).get("field-mapping")
        if fm:
            kw["fields"] = {m["insertcolumn"]: m["keyvalue"] for m in fm}
        kw.setdefault("config", {})["d_master_ssid"] = p.get("d_master_ssid", "")
    elif t == "livecloudfunction":
        kw["function"] = o.get("obj_id")
        fm = (o.get("properties") or {}).get("field-mapping")
        if fm:
            kw["fields"] = {m["insertcolumn"]: m["keyvalue"] for m in fm}
    elif t == "executeworkflow":
        kw["child_workflow"] = o.get("obj_id")
        kw.setdefault("config", {})["shortCode"] = p.get("shortCode", o.get("short_code", ""))

    # preserve unmanaged props
    cfg = kw.get("config", {})
    managed = _managed_props(t)
    for k, v in p.items():
        if k not in managed:
            cfg[k] = v
    if cfg:
        kw["config"] = cfg

    return IRStep(**kw)
