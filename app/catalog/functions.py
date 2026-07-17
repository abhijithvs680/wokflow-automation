"""LiveCloud functions + reusable child workflows catalogs (for dynamic blocks)."""
from __future__ import annotations

import time

from ..config import get_settings
from ..db import mongo

_fn_cache: dict[int, tuple[float, list[dict]]] = {}
_wf_cache: dict[int, tuple[float, list[dict]]] = {}


def tenant_functions(tid: int) -> list[dict]:
    ttl = get_settings().catalog_ttl_seconds
    now = time.time()
    if tid in _fn_cache and now - _fn_cache[tid][0] < ttl:
        return _fn_cache[tid][1]
    rows = mongo.list_livecloud_functions(tid)
    out = [{"id": str(r["_id"]), "name": r.get("name", "")} for r in rows]
    _fn_cache[tid] = (now, out)
    return out


def tenant_workflows(tid: int) -> list[dict]:
    ttl = get_settings().catalog_ttl_seconds
    now = time.time()
    if tid in _wf_cache and now - _wf_cache[tid][0] < ttl:
        return _wf_cache[tid][1]
    rows = mongo.list_reusable_workflows(tid)
    out = [
        {"id": str(r["_id"]), "name": r.get("name", ""), "short_code": r.get("short_code", "")}
        for r in rows
    ]
    _wf_cache[tid] = (now, out)
    return out


def _match(items: list[dict], ref: str) -> dict | None:
    ref_l = (ref or "").strip().lower()
    for it in items:
        if ref_l in (it["id"].lower(), it.get("short_code", "").lower(), it["name"].lower()):
            return it
    for it in items:
        if ref_l and ref_l in it["name"].lower():
            return it
    return None


def make_function_resolver(tid: int):
    items = tenant_functions(tid)
    return lambda ref: _match(items, ref)


def make_workflow_resolver(tid: int):
    items = tenant_workflows(tid)
    return lambda ref: _match(items, ref)
