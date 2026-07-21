"""workflow-ai: natural-language workflow generation service for Vizru.

Runs inside the vizru-network Docker network; writes directly to MongoDB
VizWorkflow and reads MySQL/Mongo for tenant catalogs.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .catalog import blocks as blk_cat
from .catalog import functions as fn_cat
from .catalog import spreadsheets as ss_cat
from .compiler.decompile import decompile
from .db import mongo
from .llm import flows
from .validate.validate import validate_workflow

app = FastAPI(
    title="Vizru Workflow AI",
    description="Natural-language workflow generation/editing for the Vizru platform",
    version="0.1.0",
)


# ------------------------------------------------------------------ schemas

class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=3, description="Natural-language use case")
    tid: int = Field(description="Tenant id (_tid)")
    lid: int | None = Field(default=None, description="LiveSpace id — scopes spreadsheet context")
    uid: int = Field(default=0, description="User id for created-by/updated-by")
    save: bool = Field(default=False, description="Persist to Mongo when valid")


class EditRequest(BaseModel):
    instruction: str = Field(min_length=3)
    tid: int
    lid: int | None = Field(default=None, description="LiveSpace id — scopes spreadsheet context")
    uid: int = 0
    save: bool = Field(default=False)


class CompileRequest(BaseModel):
    ir: dict[str, Any]
    tid: int | None = Field(default=None, description="Tenant for resolvers/validation; omit for offline compile")
    lid: int | None = Field(default=None, description="LiveSpace id — scopes spreadsheet resolution")


class SaveRequest(BaseModel):
    document: dict[str, Any]
    tid: int
    lid: int | None = Field(default=None, description="LiveSpace id — scopes spreadsheet validation")
    uid: int = 0


class ValidateRequest(BaseModel):
    document: dict[str, Any]
    tid: int | None = None
    lid: int | None = None


def _pipeline_response(res: flows.PipelineResult, saved_id: str | None = None) -> dict:
    return {
        "ok": res.ok,
        "workflow_id": saved_id,
        "ir": res.ir,
        "document": res.document,
        "errors": res.errors,
        "warnings": res.warnings,
        "llm_attempts": res.attempts,
    }


# ---------------------------------------------------------------- endpoints

@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.post("/workflows/generate")
def generate(req: GenerateRequest) -> dict:
    res = flows.generate_workflow(req.prompt, req.tid, lid=req.lid)
    saved_id = None
    if req.save:
        if not res.ok or res.document is None:
            raise HTTPException(status_code=422, detail=_pipeline_response(res))
        saved_id = mongo.insert_workflow(res.document, req.tid, req.uid)
    return _pipeline_response(res, saved_id)


@app.post("/workflows/{workflow_id}/edit")
def edit(workflow_id: str, req: EditRequest) -> dict:
    doc = mongo.get_workflow(workflow_id, req.tid)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"workflow {workflow_id} not found for tid {req.tid}")
    res = flows.edit_workflow(doc, req.instruction, req.tid, lid=req.lid)
    saved_id = None
    if req.save:
        if not res.ok or res.document is None:
            raise HTTPException(status_code=422, detail=_pipeline_response(res))
        mongo.update_workflow(workflow_id, req.tid, req.uid, {
            "name": res.document["name"],
            "w_objects": res.document["w_objects"],
            "connection": res.document["connection"],
        })
        saved_id = workflow_id
    return _pipeline_response(res, saved_id)


@app.post("/workflows/compile")
def compile_ir(req: CompileRequest) -> dict:
    """Compile a hand-written IR (no LLM). Useful for testing and integrations."""
    res = flows.compile_ir_dict(req.ir, req.tid, lid=req.lid)
    return _pipeline_response(res)


@app.post("/workflows/save")
def save(req: SaveRequest) -> dict:
    v = validate_workflow(req.document, ss_catalog=ss_cat.as_ss_catalog(req.tid, lid=req.lid))
    if not v.ok:
        raise HTTPException(status_code=422, detail={"errors": v.errors, "warnings": v.warnings})
    wid = mongo.insert_workflow(req.document, req.tid, req.uid)
    return {"ok": True, "workflow_id": wid, "short_code": req.document.get("short_code"), "warnings": v.warnings}


@app.post("/workflows/validate")
def validate(req: ValidateRequest) -> dict:
    ss_catalog = ss_cat.as_ss_catalog(req.tid, lid=req.lid) if req.tid is not None else None
    v = validate_workflow(req.document, ss_catalog=ss_catalog)
    return {"ok": v.ok, "errors": v.errors, "warnings": v.warnings}


@app.get("/workflows/{workflow_id}/ir")
def workflow_ir(workflow_id: str, tid: int) -> dict:
    doc = mongo.get_workflow(workflow_id, tid)
    if doc is None:
        raise HTTPException(status_code=404, detail="not found")
    ir, label_to_id = decompile(doc)
    return {"ir": ir.model_dump(exclude_none=True), "label_to_id": label_to_id}


@app.get("/workflows")
def workflows(tid: int, lid: int | None = None, limit: int = 50) -> dict:
    rows = mongo.list_workflows(tid, limit)
    return {"workflows": [
        {"id": str(r["_id"]), "name": r.get("name"), "short_code": r.get("short_code")}
        for r in rows
    ]}


# ------------------------------------------------------------------ catalog

@app.get("/catalog/blocks")
def catalog_blocks() -> dict:
    return {"blocks": blk_cat.palette()}


@app.get("/catalog/spreadsheets")
def catalog_spreadsheets(tid: int, lid: int | None = None, refresh: bool = False) -> dict:
    return {"spreadsheets": ss_cat.tenant_spreadsheets(tid, lid=lid, force=refresh)}


@app.get("/catalog/functions")
def catalog_functions(tid: int) -> dict:
    return {"functions": fn_cat.tenant_functions(tid), "child_workflows": fn_cat.tenant_workflows(tid)}
