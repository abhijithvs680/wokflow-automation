"""MongoDB access: VizWorkflow read/write, VizSpreadsheet / LiveCloud reads.

Replicates the platform's persistence contract (sys/controllers/workflow/Save.php
+ sys/core/aMongoObject.php):
  - insert sets _tid, created-by, created-at, updated-at, _flags
  - update $sets fields and bumps updated-at / updated-by
  - full_objects is always json_encode(w_objects) (PHP-style escaping)
  - short_code is generated once, then immutable
"""
from __future__ import annotations

import datetime
from functools import lru_cache
from typing import Any

from bson import ObjectId
from pymongo import MongoClient

from ..config import get_settings
from ..compiler.phpjson import php_json_encode

WORKFLOW_COLLECTION = "VizWorkflow"
SPREADSHEET_COLLECTION = "VizSpreadsheet"
LIVECLOUD_FUNCTIONS_COLLECTION = "VizLivecloudAppsFunctions"
LIVESPACE_ROLES_COLLECTION = "t-livespaces-roles"


@lru_cache(maxsize=1)
def _client() -> MongoClient:
    return MongoClient(get_settings().mongo_url, tz_aware=True)


def db():
    return _client()[get_settings().mongo_db]


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


# ---------------------------------------------------------------- workflows

def get_workflow(workflow_id: str, tid: int) -> dict | None:
    return db()[WORKFLOW_COLLECTION].find_one({"_id": ObjectId(workflow_id), "_tid": tid})


def find_workflow_by_shortcode(short_code: str, tid: int) -> dict | None:
    return db()[WORKFLOW_COLLECTION].find_one({"short_code": short_code, "_tid": tid})


def list_workflows(tid: int, limit: int = 100) -> list[dict]:
    cur = (
        db()[WORKFLOW_COLLECTION]
        .find({"_tid": tid}, {"name": 1, "short_code": 1, "updated-at": 1})
        .sort("updated-at", -1)
        .limit(limit)
    )
    return list(cur)


def insert_workflow(doc: dict, tid: int, uid: int) -> str:
    """Insert a compiled workflow document, mimicking aMongoObject::addNew."""
    now = _now()
    doc = dict(doc)
    doc["_tid"] = tid
    doc["created-by"] = uid
    doc["updated-by"] = uid
    doc["created-at"] = now
    doc["updated-at"] = now
    doc.setdefault("_flags", {"permanent": False})
    doc["full_objects"] = php_json_encode(doc["w_objects"])
    res = db()[WORKFLOW_COLLECTION].insert_one(doc)
    return str(res.inserted_id)


def update_workflow(workflow_id: str, tid: int, uid: int, fields: dict) -> None:
    """Update graph fields, mimicking aMongoObject::update. short_code immutable."""
    fields = dict(fields)
    fields.pop("short_code", None)
    fields.pop("_id", None)
    fields.pop("_tid", None)
    fields.pop("created-by", None)
    fields.pop("created-at", None)
    if "w_objects" in fields:
        fields["full_objects"] = php_json_encode(fields["w_objects"])
    fields["updated-by"] = uid
    fields["updated-at"] = _now()
    db()[WORKFLOW_COLLECTION].update_one(
        {"_id": ObjectId(workflow_id), "_tid": tid}, {"$set": fields}
    )


# -------------------------------------------------------------- spreadsheets

def get_spreadsheet_schemas(tid: int, ssids: list[str] | None = None) -> list[dict]:
    """Schema-only projection of VizSpreadsheet (never row data)."""
    q: dict[str, Any] = {"_tid": tid}
    if ssids:
        q["_id"] = {"$in": [ObjectId(s) for s in ssids]}
    cur = db()[SPREADSHEET_COLLECTION].find(
        q, {"title": 1, "short_code": 1, "header": 1, "column-settings": 1}
    )
    return list(cur)


# --------------------------------------------------- livecloud fns / children

def list_livecloud_functions(tid: int) -> list[dict]:
    cur = db()[LIVECLOUD_FUNCTIONS_COLLECTION].find(
        {"_tid": tid}, {"name": 1, "apps-id": 1}
    )
    return list(cur)


def list_livespace_roles(tid: int) -> list[dict]:
    """Livespace role names (usable as livespaceroleName in usermgmt blocks)."""
    cur = db()[LIVESPACE_ROLES_COLLECTION].find({"_tid": tid}, {"name": 1})
    return list(cur)


def list_reusable_workflows(tid: int) -> list[dict]:
    """Child workflows usable via executeworkflow (need _id + short_code)."""
    cur = db()[WORKFLOW_COLLECTION].find(
        {"_tid": tid, "short_code": {"$exists": True}},
        {"name": 1, "short_code": 1},
    )
    return list(cur)
