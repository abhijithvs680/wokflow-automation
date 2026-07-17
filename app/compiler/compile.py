"""IR -> VizWorkflow document compiler.

Produces the full document the platform's Save.php would have written:
w_objects[] (authoritative graph), connection[] (UI edge list), full_objects
(PHP json_encode string, added at persistence time), short_code, metadata.
"""
from __future__ import annotations

import re
import time
from typing import Any

from ..catalog import blocks as cat
from ..ir.schema import IRStep, WorkflowIR
from .ids import IdAllocator
from .layout import assign_positions, bfs_layout


class CompileError(ValueError):
    pass


def php_uniqid() -> str:
    """PHP uniqid(): 13 lowercase hex chars derived from microtime."""
    now = time.time()
    sec = int(now)
    usec = int((now - sec) * 1_000_000)
    return f"{sec:08x}{usec:05x}"


def create_short_code(name: str) -> str:
    """StrUtils::createShortCode: strip non-alnum, lowercase, append uniqid()."""
    return re.sub(r"[^a-zA-Z0-9]+", "", name).lower() + php_uniqid()


def build_variables_string(pairs: dict[str, str]) -> str:
    """setvariable 'variables': 'k=v;k2=v2' (semicolon-separated)."""
    return ";".join(f"{k}={v}" for k, v in pairs.items())


def _base_props(block_type: str, step_label: str, description: str = "") -> dict:
    return {
        "label": step_label,
        "description": description,
        "blockType": block_type,
        "dynamic_flag": "false",
        "debug_mode": "0",
    }


def _field_mapping(fields: dict[str, str]) -> list[dict]:
    return [{"keyvalue": v, "insertcolumn": k} for k, v in fields.items()]


class Compiler:
    """Compiles a WorkflowIR into a VizWorkflow document (sans _id/_tid/dates).

    spreadsheet_resolver(name_or_id) -> {master_ssid, lid, short_code, columns[]}
    function_resolver(name_or_id)    -> {id}
    workflow_resolver(name_or_id)    -> {id, short_code}
    Resolvers may be None for offline compilation (ids passed through as-is).
    """

    def __init__(self, spreadsheet_resolver=None, function_resolver=None, workflow_resolver=None):
        self.resolve_ss = spreadsheet_resolver
        self.resolve_fn = function_resolver
        self.resolve_wf = workflow_resolver

    # ------------------------------------------------------------------ main
    def compile(self, ir: WorkflowIR, existing_ids: list[str] | None = None) -> dict:
        steps = ir.step_map()
        if not ir.steps:
            raise CompileError("IR has no steps")

        alloc = IdAllocator.continuing(existing_ids or [])

        # 1. allocate ids (trigger first, then steps in declared order)
        trigger_info = cat.block_info(ir.trigger.type)
        if not trigger_info:
            raise CompileError(f"Unknown trigger type '{ir.trigger.type}'")
        label_to_id: dict[str, str] = {}
        obj_ids: dict[str, str] = {}

        trigger_id = alloc.allocate(trigger_info["obj_id"])
        label_to_id[ir.trigger.label] = trigger_id
        obj_ids[ir.trigger.label] = trigger_info["obj_id"]

        step_dynamic: dict[str, dict] = {}  # label -> resolved dynamic target
        for step in ir.steps:
            if step.label in label_to_id:
                raise CompileError(f"Duplicate label '{step.label}'")
            info = cat.block_info(step.block)
            if not info:
                raise CompileError(f"Unknown block type '{step.block}' (step '{step.label}')")
            obj_id = info["obj_id"]
            if step.block == "livecloudfunction":
                target = self._resolve_dynamic(self.resolve_fn, step.function, step)
                obj_id = target["id"]
                step_dynamic[step.label] = target
            elif step.block == "executeworkflow":
                target = self._resolve_dynamic(self.resolve_wf, step.child_workflow, step)
                obj_id = target["id"]
                step_dynamic[step.label] = target
            obj_ids[step.label] = obj_id
            label_to_id[step.label] = alloc.allocate(obj_id)

        # 2. wire edges (labels -> ids); entry -> trigger.next or first step
        first_label = ir.trigger.next or ir.steps[0].label
        if first_label not in label_to_id:
            raise CompileError(f"trigger.next: unknown label '{first_label}'")

        def _id_of(label: str | None, ctx: str) -> str | int:
            if label is None:
                return 0
            if label not in label_to_id:
                raise CompileError(f"{ctx}: unknown target label '{label}'")
            return label_to_id[label]

        # 3. build w_objects
        w_objects: list[dict] = []

        trig_props = _base_props(ir.trigger.type, ir.trigger.label)
        if ir.trigger.type == "genericpost":
            trig_props["auth_required"] = "1" if ir.trigger.auth_required else "0"
        trig_props.update({k: str(v) for k, v in ir.trigger.config.items()})
        w_objects.append({
            "id": trigger_id,
            "type": ir.trigger.type,
            "obj_id": trigger_info["obj_id"],
            "position": {"x": "0", "y": "0"},  # filled by layout below
            "icon_path": trigger_info["icon_path"],
            "source": 0,
            "target": label_to_id[first_label],
            "block_properties": trig_props,
        })

        for step in ir.steps:
            w_objects.append(self._compile_step(step, steps, label_to_id, obj_ids, step_dynamic, _id_of))

        # 4. layout
        edges: dict[str, list[tuple[str, int]]] = {}
        for obj in w_objects:
            kids: list[tuple[str, int]] = []
            if obj.get("type") == "condition":
                if obj.get("target_yes"):
                    kids.append((obj["target_yes"], 0))
                if obj.get("target_no"):
                    kids.append((obj["target_no"], 1))
            elif obj.get("target") not in (0, "0", "", None):
                kids.append((obj["target"], 0))
            edges[obj["id"]] = kids
        order, depth, lane = bfs_layout(trigger_id, edges)
        positions = assign_positions(order, depth, lane)
        for obj in w_objects:
            if obj["id"] in positions:
                obj["position"] = positions[obj["id"]]

        # 5. connection[] (UI edge list; engine ignores it)
        connection = self._build_connections(w_objects)

        return {
            "name": ir.name,
            "desc": "Workflow desc",
            "w_objects": w_objects,
            "connection": connection,
            "short_code": create_short_code(ir.name),
            "enable_log": "0",
            "category-type": None,
            "zeos_flag": False,
        }

    # ------------------------------------------------------------- per-step
    def _compile_step(self, step: IRStep, steps: dict[str, IRStep],
                      label_to_id: dict, obj_ids: dict, dynamic: dict, _id_of) -> dict:
        info = cat.block_info(step.block)
        bid = label_to_id[step.label]
        props = _base_props(step.block, step.label, step.description)
        top_properties: dict | None = None
        extra_top: dict = {}

        if step.block == "setvariable":
            props["variables"] = build_variables_string(step.set or {})
        elif step.block == "condition":
            props["message"] = step.expr or ""
        elif step.block in cat.SS_TYPES:
            ss = self._resolve_spreadsheet(step)
            id_key = info.get("spreadsheet", "ssid")
            props[id_key] = ss["master_ssid"]
            props["d_master_ssid"] = str(ss["lid"])
            props["ss_short_code"] = ""
            props["sstabaction_source-1"] = ["shortcode"]
            if step.block in cat.SS_FILTER_TYPES:
                props.update({
                    "big_data": "0", "row_count": "0", "limit_offset": "",
                    "limit_to": "", "distinct_column": "", "alias_column": "",
                })
            else:
                props["disable_realtime"] = "false"
                props["test"] = ""
            if step.filters:
                props["filters"] = step.filters
                props["filter_operators"] = {
                    k: (step.operators or {}).get(k, "=") for k in step.filters
                }
            if step.fields and step.block in cat.SS_WRITE_TYPES:
                top_properties = {"action": "READ", "field-mapping": _field_mapping(step.fields)}
        elif step.block == "livecloudfunction":
            props["objId"] = obj_ids[step.label]
            props["test"] = ""
            top_properties = {"action": "READ", "field-mapping": _field_mapping(step.fields or {})}
        elif step.block == "executeworkflow":
            target = dynamic[step.label]
            props["shortCode"] = target.get("short_code", "")
            extra_top["short_code"] = target.get("short_code", "")
        # everything else: raw config only

        for k, v in (step.config or {}).items():
            props[k] = v

        obj: dict[str, Any] = {
            "id": bid,
            "type": step.block,
            "obj_id": obj_ids[step.label],
            "position": {"x": "0", "y": "0"},
            "icon_path": info["icon_path"],
            "source": 0,   # filled below by _wire_sources
            "target": 0,
            "block_properties": props,
        }
        obj.update(extra_top)

        if step.block == "condition":
            obj["target"] = 0
            obj["target_yes"] = _id_of(step.yes, f"step '{step.label}' yes") if step.yes else 0
            obj["target_no"] = _id_of(step.no, f"step '{step.label}' no") if step.no else 0
        elif step.end or step.next is None:
            obj["target"] = 0
        else:
            obj["target"] = _id_of(step.next, f"step '{step.label}' next")

        if top_properties is not None:
            obj["properties"] = top_properties

        # source wiring happens once all targets are known — see compile();
        # here we defer by marking and fixing up in _build_connections pass.
        return obj

    # ---------------------------------------------------------- connections
    @staticmethod
    def _build_connections(w_objects: list[dict]) -> list[dict]:
        """Derive connection[] and back-fill each block's `source` field."""
        by_id = {o["id"]: o for o in w_objects}
        conns: list[dict] = []

        def link(src: dict, tgt_id: str, branch: str | None):
            tgt = by_id.get(tgt_id)
            if tgt is None:
                return
            if tgt.get("source") in (0, "0", None) and src["id"] != tgt_id:
                # entry keeps source 0; others get their (last) inbound edge
                if not (tgt.get("_is_entry")):
                    tgt["source"] = src["id"]
            conns.append({
                "id": f"{src['id']}-{tgt_id}",
                "source": src["id"],
                "target": tgt_id,
                "target_no": tgt_id if branch == "no" else "",
                "target_yes": tgt_id if branch in (None, "yes") else "",
            })

        # mark entry so its source stays 0
        for o in w_objects:
            if o.get("source") == 0 and o is w_objects[0]:
                o["_is_entry"] = True

        for o in w_objects:
            if o.get("type") == "condition":
                if o.get("target_yes"):
                    link(o, o["target_yes"], "yes")
                if o.get("target_no"):
                    link(o, o["target_no"], "no")
            else:
                t = o.get("target")
                if t not in (0, "0", "", None):
                    link(o, t, None)

        for o in w_objects:
            o.pop("_is_entry", None)
        return conns

    # ------------------------------------------------------------ resolvers
    def _resolve_spreadsheet(self, step: IRStep) -> dict:
        ref = step.spreadsheet
        if not ref:
            raise CompileError(f"step '{step.label}' ({step.block}) needs a spreadsheet")
        if self.resolve_ss is None:
            # offline mode: assume ref is already a master_ssid, lid unknown
            return {"master_ssid": ref, "lid": step.config.get("d_master_ssid", "")}
        ss = self.resolve_ss(ref)
        if not ss:
            raise CompileError(f"step '{step.label}': spreadsheet '{ref}' not found for tenant")
        return ss

    @staticmethod
    def _resolve_dynamic(resolver, ref: str | None, step: IRStep) -> dict:
        if not ref:
            raise CompileError(f"step '{step.label}' ({step.block}) needs a target (function/child_workflow)")
        if resolver is None:
            if re.fullmatch(r"[0-9a-f]{24}", ref or ""):
                return {"id": ref, "short_code": step.config.get("shortCode", "")}
            raise CompileError(f"step '{step.label}': cannot resolve '{ref}' without a catalog resolver")
        target = resolver(ref)
        if not target:
            raise CompileError(f"step '{step.label}': '{ref}' not found for tenant")
        return target
