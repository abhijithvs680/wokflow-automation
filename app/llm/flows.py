"""End-to-end generate / edit pipelines.

generate: retrieve context -> LLM -> IR -> compile -> validate -> (repair loop)
edit:     load doc -> decompile -> LLM edit -> recompile -> validate -> (repair)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from ..catalog import functions as fn_cat
from ..catalog import livespace as ls_cat
from ..catalog import retrieval
from ..catalog import spreadsheets as ss_cat
from ..compiler.compile import CompileError, Compiler
from ..compiler.decompile import decompile
from ..config import get_settings
from ..ir.schema import WorkflowIR
from ..validate.validate import validate_workflow
from . import client, prompts


@dataclass
class PipelineResult:
    ok: bool
    ir: dict | None = None
    document: dict | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    attempts: int = 0


def _tenant_context(prompt_text: str, tid: int, lid: int | None = None) -> str:
    livespace = None
    all_ss = ss_cat.tenant_spreadsheets(tid, lid=lid)
    if lid is not None:
        # explicit app scope: give the FULL app context (identity, roles, every
        # spreadsheet in the app) instead of lexical guessing
        livespace = ls_cat.livespace_context(tid, lid)
        ss = all_ss if len(all_ss) <= 15 else retrieval.top_spreadsheets(prompt_text, all_ss, k=15)
    else:
        ss = retrieval.top_spreadsheets(prompt_text, all_ss, k=5)
    fns = retrieval.top_named(prompt_text, fn_cat.tenant_functions(tid), k=5)
    wfs = retrieval.top_named(prompt_text, fn_cat.tenant_workflows(tid), k=5)
    return prompts.build_context(None, ss, fns, wfs, livespace=livespace)


def _compiler(tid: int, lid: int | None = None) -> Compiler:
    return Compiler(
        spreadsheet_resolver=ss_cat.make_resolver(tid, lid=lid),
        function_resolver=fn_cat.make_function_resolver(tid),
        workflow_resolver=fn_cat.make_workflow_resolver(tid),
    )


def _plan_coverage_errors(ir) -> list[str]:
    """The plan is the model's own requirement decomposition; a block type named
    in the plan but absent from the steps means the workflow is incomplete."""
    from ..catalog import blocks as blk_cat

    if not getattr(ir, "plan", None):
        return []
    used = {s.block for s in ir.steps} | {ir.trigger.type}
    errors = []
    known = set(blk_cat.palette().keys())
    for item in ir.plan:
        for token in re.findall(r"[a-z]+", item.lower()):
            if token in known and token not in used:
                errors.append(
                    f"plan item '{item}' names block '{token}' but no step uses it — "
                    "add the step or correct the plan"
                )
    return errors


def _try_build(raw_ir: str, tid: int, lid: int | None = None, existing_ids: list[str] | None = None) -> tuple[dict | None, dict | None, list[str], list[str]]:
    """raw LLM json -> (ir_dict, doc, errors, warnings)."""
    errors: list[str] = []
    try:
        ir = client.parse_ir(raw_ir)
    except (client.LLMError, ValidationError) as e:
        return None, None, [f"IR parse error: {e}"], []

    plan_errors = _plan_coverage_errors(ir)
    if plan_errors:
        return ir.model_dump(exclude_none=True), None, plan_errors, []

    try:
        doc = _compiler(tid, lid=lid).compile(ir, existing_ids=existing_ids)
    except CompileError as e:
        return ir.model_dump(exclude_none=True), None, [f"compile error: {e}"], []

    result = validate_workflow(doc, ss_catalog=ss_cat.as_ss_catalog(tid, lid=lid))
    errors.extend(result.errors)
    return ir.model_dump(exclude_none=True), doc, errors, result.warnings


def _run_with_repair(messages: list[dict], tid: int, lid: int | None = None, existing_ids: list[str] | None = None) -> PipelineResult:
    max_attempts = 1 + get_settings().llm_max_repair_attempts
    res = PipelineResult(ok=False)
    raw = ""
    for attempt in range(1, max_attempts + 1):
        res.attempts = attempt
        try:
            raw = client.chat_json(messages)
        except client.LLMError as e:
            res.errors = [str(e)]
            return res
        ir_dict, doc, errors, warnings = _try_build(raw, tid, lid=lid, existing_ids=existing_ids)
        res.ir, res.document, res.errors, res.warnings = ir_dict, doc, errors, warnings
        if doc is not None and not errors:
            res.ok = True
            return res
        messages = prompts.repair_messages(messages, raw, errors)
    return res


def generate_workflow(prompt_text: str, tid: int, lid: int | None = None) -> PipelineResult:
    context = _tenant_context(prompt_text, tid, lid=lid)
    messages = prompts.generation_messages(context, prompt_text)
    return _run_with_repair(messages, tid, lid=lid)


def edit_workflow(doc: dict, instruction: str, tid: int, lid: int | None = None) -> PipelineResult:
    ir, _label_to_id = decompile(doc)
    existing_ids = [o["id"] for o in doc.get("w_objects", [])]
    context = _tenant_context(instruction + " " + doc.get("name", ""), tid, lid=lid)
    messages = prompts.edit_messages(context, ir.model_dump(exclude_none=True), instruction)
    result = _run_with_repair(messages, tid, lid=lid, existing_ids=existing_ids)
    if result.ok and result.document is not None:
        # keep original identity fields
        result.document["name"] = result.document.get("name") or doc.get("name")
        result.document.pop("short_code", None)  # immutable, handled by update
    return result


def compile_ir_dict(ir_data: dict[str, Any], tid: int | None, lid: int | None = None) -> PipelineResult:
    """Compile a caller-supplied IR without any LLM call (testing / manual use)."""
    res = PipelineResult(ok=False, attempts=0)
    try:
        ir = WorkflowIR.model_validate(ir_data)
    except ValidationError as e:
        res.errors = [f"IR parse error: {e}"]
        return res
    try:
        compiler = _compiler(tid, lid=lid) if tid is not None else Compiler()
        doc = compiler.compile(ir)
    except CompileError as e:
        res.ir = ir.model_dump(exclude_none=True)
        res.errors = [f"compile error: {e}"]
        return res
    ss_catalog = ss_cat.as_ss_catalog(tid, lid=lid) if tid is not None else None
    v = validate_workflow(doc, ss_catalog=ss_catalog)
    res.ir = ir.model_dump(exclude_none=True)
    res.document = doc
    res.errors = v.errors
    res.warnings = v.warnings
    res.ok = not v.errors
    return res
