"""LiveSpace (app) context for a specific lid: identity + roles + spreadsheets.

Gives the LLM the concrete facts it needs to configure app-scoped blocks:
  - lid / name / short_code       (viz_livespace, MySQL)
  - livespace role names          (t-livespaces-roles, Mongo) — usable as
    livespaceroleName in addusertolivespace
  - the app's spreadsheets        (via catalog.spreadsheets, lid-scoped)
"""
from __future__ import annotations

import time

from ..config import get_settings
from ..db import mongo, mysql

_cache: dict[tuple[int, int], tuple[float, dict | None]] = {}
_roles_cache: dict[int, tuple[float, list[str]]] = {}


def tenant_roles(tid: int) -> list[str]:
    """Livespace role names for the tenant (small, cached)."""
    ttl = get_settings().catalog_ttl_seconds
    now = time.time()
    if tid in _roles_cache and now - _roles_cache[tid][0] < ttl:
        return _roles_cache[tid][1]
    names = sorted({r.get("name", "") for r in mongo.list_livespace_roles(tid) if r.get("name")})
    _roles_cache[tid] = (now, names)
    return names


def livespace_context(tid: int, lid: int) -> dict | None:
    """{lid, name, short_code, roles[]} or None if the lid doesn't exist."""
    key = (tid, lid)
    ttl = get_settings().catalog_ttl_seconds
    now = time.time()
    if key in _cache and now - _cache[key][0] < ttl:
        info = _cache[key][1]
    else:
        info = mysql.livespace_info(tid, lid)
        _cache[key] = (now, info)
    if not info:
        return None
    return {
        "lid": info["lid"],
        "name": info.get("name") or "",
        "short_code": info.get("short_code") or "",
        "roles": tenant_roles(tid),
    }
