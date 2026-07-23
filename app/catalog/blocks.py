"""Block catalog: palette (obj_id/icon) + config-key schemas + prompt summaries.

Sources:
  - palette.json  (harvested from sys/controllers/workflow/Leftblock.php)
  - platform-metadata/workflows/*.json JSON Schemas (componentType -> config keys),
    vendored into app/catalog/metadata/ at build time when available.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache

_HERE = os.path.dirname(__file__)

# legacy type -> metadata componentType (PascalCase class names)
TYPE_TO_COMPONENT = {
    "livecloudfunction": "Livecloud",
    "dataset": "Datasetblock",
    "sendmail": "Sendmailblock",
    "notify": "Notify",
    "condition": "Condition",
    "uniquevalidator": "Uniquevalidator",
    "executeworkflow": "executeWorkflow",
    "setvariable": "setVariable",
    "customoutput": "customOutput",
    "ssdatafilter": "SpreadsheetOperationsBlock",
    "ssadvdatafilter": "SpreadsheetOperationsBlock",
    "insertssdata": "SpreadsheetOperationsBlock",
    "bulkinsertssdata": "SpreadsheetOperationsBlock",
    "insertorupdatessdata": "SpreadsheetOperationsBlock",
    "updatessdata": "SpreadsheetOperationsBlock",
    "ssdeleterow": "SpreadsheetOperationsBlock",
    "ssautoincrementcol": "SpreadsheetOperationsBlock",
    "arrayextract": "arrayExtractBlock",
    "datatransfer": "Datatransferblock",
    "retarusfax": "retarusFaxBlock",
    "retarussms": "retarusSMSBlock",
    "genericpost": "postBlock",
    "genericget": "getBlock",
    "twilio": "TwilioBlock",
    "roverai": "RoveraiBlock",
    "roveragent": "RoveragentBlock",
    "getfiles": "FileOperationsBlock",
    "deletefile": "FileOperationsBlock",
    "movefile": "FileOperationsBlock",
    "copyfile": "FileOperationsBlock",
    "getfiledetails": "FileOperationsBlock",
    "createfile": "FileOperationsBlock",
    "processfile": "processFileBlock",
    "clearoutput": "ClearOutputBlock",
    "return": "ReturnBlock",
    "chatfileupload": "chatFileUpload",
    "realtimepush": "realTimePush",
    "ruleengine": "ruleEngine",
    "formrule": "formRule",
    "date": "Dateblock",
    "math": "Mathblock",
    "string": "Stringblock",
    "backgroundworkflow": "backgroundWorkflow",
    "downloadasfile": "downloadAsFile",
    "adduser": "userManagementBlocks",
    "getuser": "userManagementBlocks",
    "deactivateuser": "userManagementBlocks",
    "addusertolivespace": "userManagementBlocks",
    "removeuserfromlivespace": "userManagementBlocks",
    "getuserlivespaces": "userManagementBlocks",
    "getlivespacemembers": "userManagementBlocks",
    "setlanding": "userManagementBlocks",
}

SS_FILTER_TYPES = {"ssdatafilter", "ssadvdatafilter", "ssdeleterow", "ssautoincrementcol"}
SS_WRITE_TYPES = {"insertssdata", "bulkinsertssdata", "insertorupdatessdata", "updatessdata"}
SS_TYPES = SS_FILTER_TYPES | SS_WRITE_TYPES
ENTRY_TYPES = {"datatransfer", "genericpost"}
DYNAMIC_TYPES = {"livecloudfunction", "executeworkflow"}

# System variables / runtime flags valid inside {...} references
SYSTEM_VARS = {"viz-uuid", "viz-timestamp", "viz-domain", "tenantid", "httpcode", "filter-count"}


@lru_cache(maxsize=1)
def palette() -> dict:
    with open(os.path.join(_HERE, "palette.json"), "r", encoding="utf-8") as f:
        return json.load(f)["blocks"]


@lru_cache(maxsize=1)
def block_docs() -> dict:
    """Detailed per-block docs (usage, required/optional config keys, outputs)
    harvested from the PHP block classes. Overlays palette()."""
    path = os.path.join(_HERE, "block_docs.json")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)["blocks"]


@lru_cache(maxsize=1)
def metadata_schemas() -> dict:
    """componentType -> JSON schema (if the metadata folder was vendored)."""
    out: dict[str, dict] = {}
    meta_dir = os.path.join(_HERE, "metadata")
    if not os.path.isdir(meta_dir):
        return out
    for fn in os.listdir(meta_dir):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(meta_dir, fn), "r", encoding="utf-8") as f:
            schema = json.load(f)
        out[schema.get("title", fn[:-5])] = schema
    return out


def block_info(block_type: str) -> dict | None:
    return palette().get(block_type)


def config_keys(block_type: str) -> list[str]:
    comp = TYPE_TO_COMPONENT.get(block_type)
    schema = metadata_schemas().get(comp or "")
    if not schema:
        return []
    props = schema.get("properties", {}).get("configuration", {}).get("properties", {})
    return sorted(props.keys())


def prompt_catalog(types: list[str] | None = None) -> str:
    """Full block catalog for the LLM prompt, grouped by category.

    Every block type is always included (the whole catalog is small); blocks
    with harvested docs additionally list their real config keys and outputs so
    the model can configure them without examples.
    """
    docs = block_docs()
    by_category: dict[str, list[str]] = {}
    for t, info in palette().items():
        if types and t not in types:
            continue
        doc = docs.get(t)
        if doc:
            line = f"- {t}: {doc['usage']}"
            req = doc.get("required") or {}
            opt = doc.get("optional") or {}
            if req:
                line += "\n  required config: " + "; ".join(f"{k} ({v})" for k, v in req.items())
            if opt:
                line += "\n  optional config: " + "; ".join(f"{k} ({v})" for k, v in opt.items())
            if doc.get("outputs"):
                line += "\n  outputs: " + ", ".join("{Label.%s}" % o for o in doc["outputs"])
        else:
            keys = config_keys(t)
            extra = f" Config keys: {', '.join(keys[:12])}." if keys else ""
            line = f"- {t}: {info['description']}{extra}"
        by_category.setdefault(info.get("category", "Other"), []).append(line)

    parts = []
    for category, lines in by_category.items():
        parts.append(f"### {category}")
        parts.extend(lines)
    return "\n".join(parts)
