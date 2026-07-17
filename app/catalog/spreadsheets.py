"""Per-tenant spreadsheet catalog (compact, cached).

Two queries (never row data):
  1. MySQL viz_livespace_files -> name, short_code, master_ssid (dir_path), lid
  2. Mongo VizSpreadsheet -> header[] + column-settings (schema only)

header[] layout: index 0 row_id, 1 updated_by, 2 row_meta (system, skipped);
index 3+ = { "<colKeyHash>": "DisplayName" }.
"""
from __future__ import annotations

import time

from ..config import get_settings
from ..db import mongo, mysql

_cache: dict[int, tuple[float, list[dict]]] = {}


def _columns_from_schema(ss_doc: dict) -> list[dict]:
    cols: list[dict] = []
    settings = ss_doc.get("column-settings") or {}
    for idx, entry in enumerate(ss_doc.get("header") or []):
        if idx <= 2 or not isinstance(entry, dict):
            continue
        for col_key, display in entry.items():
            if not isinstance(display, str) or not display.strip():
                continue
            dtype = "string"
            cs = settings.get(col_key) or {}
            if isinstance(cs, dict):
                dtype = ((cs.get("datatype") or {}).get("type")) or "string"
            cols.append({"name": display, "key": col_key, "type": dtype})
    return cols


def tenant_spreadsheets(tid: int, force: bool = False) -> list[dict]:
    """[{name, short_code, master_ssid, lid, columns:[{name,key,type}]}]"""
    ttl = get_settings().catalog_ttl_seconds
    now = time.time()
    if not force and tid in _cache and now - _cache[tid][0] < ttl:
        return _cache[tid][1]

    index = mysql.spreadsheet_index(tid)
    ssids = [row["master_ssid"] for row in index if row.get("master_ssid")]
    schemas = {str(d["_id"]): d for d in mongo.get_spreadsheet_schemas(tid, ssids)}

    catalog: list[dict] = []
    for row in index:
        ssid = row.get("master_ssid") or ""
        doc = schemas.get(ssid)
        catalog.append({
            "name": row.get("name") or (doc or {}).get("title", ""),
            "short_code": row.get("short_code") or "",
            "master_ssid": ssid,
            "lid": row.get("lid"),
            "columns": _columns_from_schema(doc) if doc else [],
        })
    _cache[tid] = (now, catalog)
    return catalog


def make_resolver(tid: int):
    """Returns fn(name_or_id) -> {master_ssid, lid, short_code, columns} | None."""
    catalog = tenant_spreadsheets(tid)

    def resolve(ref: str) -> dict | None:
        ref_l = (ref or "").strip().lower()
        for ss in catalog:
            if ref_l in (ss["master_ssid"].lower(), ss["short_code"].lower(), ss["name"].lower()):
                return ss
        # loose name match
        for ss in catalog:
            if ref_l and ref_l in ss["name"].lower():
                return ss
        return None

    return resolve


def as_ss_catalog(tid: int) -> dict[str, dict]:
    """master_ssid -> {name, columns:[names]} for the validator."""
    return {
        ss["master_ssid"]: {"name": ss["name"], "columns": [c["name"] for c in ss["columns"]]}
        for ss in tenant_spreadsheets(tid)
    }
