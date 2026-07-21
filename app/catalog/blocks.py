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
    """Compact catalog grouped by category for the LLM prompt."""
    pal = palette()
    by_cat: dict[str, list[tuple[str, dict]]] = {}
    for t, info in pal.items():
        if types and t not in types:
            continue
        cat = info.get("category", "Other")
        by_cat.setdefault(cat, []).append((t, info))

    lines = []
    for cat, items in by_cat.items():
        lines.append(f"\n### {cat}")
        for t, info in items:
            keys = config_keys(t)
            extra = f"  config: {', '.join(keys[:10])}" if keys else ""
            lines.append(f"- {t}: {info['description']}{extra}")
    return "\n".join(lines).strip()
