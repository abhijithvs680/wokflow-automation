"""Compiler + validator tests on hand-written IRs (no DB, no LLM)."""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from app.compiler.compile import CompileError, Compiler
from app.ir.schema import WorkflowIR
from app.validate.validate import validate_workflow

SS = {"master_ssid": "693fe7ff8ed9d9d1310dddf6", "lid": 1795, "short_code": "doctors",
      "columns": [{"name": "PhoneNumber", "key": "k1", "type": "string"},
                  {"name": "OTP", "key": "k2", "type": "string"},
                  {"name": "Status", "key": "k3", "type": "string"}]}


def _resolver(ref):
    return SS if ref.lower() in ("doctors", SS["master_ssid"]) else None


IR = {
    "name": "OTP Flow",
    "trigger": {"type": "genericpost", "label": "Entry"},
    "steps": [
        {"label": "user", "block": "ssdatafilter", "spreadsheet": "Doctors",
         "filters": {"PhoneNumber": "{Entry.phone}"}, "next": "found"},
        {"label": "found", "block": "condition", "expr": "{filter-count}>0",
         "yes": "store", "no": "fail"},
        {"label": "store", "block": "updatessdata", "spreadsheet": "Doctors",
         "filters": {"PhoneNumber": "{user.PhoneNumber}"},
         "fields": {"OTP": "{viz-uuid}", "Status": "Created"}, "next": "ok"},
        {"label": "ok", "block": "setvariable", "set": {"error": "False"}, "end": True},
        {"label": "fail", "block": "setvariable",
         "set": {"error": "True", "message": "not found"}, "end": True},
    ],
}


def _compile():
    ir = WorkflowIR.model_validate(IR)
    return Compiler(spreadsheet_resolver=_resolver).compile(ir)


def test_compiles_valid_document():
    doc = _compile()
    res = validate_workflow(doc, ss_catalog={SS["master_ssid"]: {"name": "Doctors",
                            "columns": [c["name"] for c in SS["columns"]]}})
    assert res.ok, res.errors


def test_entry_and_terminals():
    doc = _compile()
    entries = [o for o in doc["w_objects"] if o["source"] in (0, "0")]
    assert len(entries) == 1
    assert entries[0]["type"] == "genericpost"
    terminals = [o for o in doc["w_objects"]
                 if o.get("type") != "condition" and o.get("target") in (0, "0")]
    assert len(terminals) == 2


def test_ids_and_palette():
    doc = _compile()
    by_type = {o["type"]: o for o in doc["w_objects"]}
    assert by_type["ssdatafilter"]["obj_id"] == "4101"
    assert by_type["updatessdata"]["obj_id"] == "2000"
    assert by_type["condition"]["obj_id"] == "5001"
    for o in doc["w_objects"]:
        assert o["id"].startswith(o["obj_id"] + "_")


def test_ss_ids_resolved():
    doc = _compile()
    flt = next(o for o in doc["w_objects"] if o["type"] == "ssdatafilter")
    upd = next(o for o in doc["w_objects"] if o["type"] == "updatessdata")
    assert flt["block_properties"]["s_master_ssid"] == SS["master_ssid"]
    assert flt["block_properties"]["d_master_ssid"] == "1795"
    assert upd["block_properties"]["ssid"] == SS["master_ssid"]
    fm = upd["properties"]["field-mapping"]
    assert {"keyvalue": "Created", "insertcolumn": "Status"} in fm


def test_variables_string():
    doc = _compile()
    fail = next(o for o in doc["w_objects"]
                if o["type"] == "setvariable" and "message" in o["block_properties"].get("variables", ""))
    assert fail["block_properties"]["variables"] == "error=True;message=not found"


def test_connection_list_matches_edges():
    doc = _compile()
    conn_pairs = {(c["source"], c["target"]) for c in doc["connection"]}
    for o in doc["w_objects"]:
        if o["type"] == "condition":
            for k in ("target_yes", "target_no"):
                if o.get(k) not in (0, "0", "", None):
                    assert (o["id"], o[k]) in conn_pairs
        elif o.get("target") not in (0, "0", "", None):
            assert (o["id"], o["target"]) in conn_pairs


def test_unknown_spreadsheet_fails():
    bad = dict(IR, steps=[dict(IR["steps"][0], spreadsheet="Nope")] + IR["steps"][1:])
    with pytest.raises(CompileError):
        Compiler(spreadsheet_resolver=_resolver).compile(WorkflowIR.model_validate(bad))


def test_unknown_target_label_fails():
    bad = dict(IR, steps=[dict(IR["steps"][0], next="ghost")] + IR["steps"][1:])
    with pytest.raises(CompileError):
        Compiler(spreadsheet_resolver=_resolver).compile(WorkflowIR.model_validate(bad))


def test_validator_catches_missing_target():
    doc = _compile()
    doc["w_objects"][1]["target"] = "9999_99"
    res = validate_workflow(doc)
    assert not res.ok


def test_validator_catches_bad_column():
    doc = _compile()
    res = validate_workflow(doc, ss_catalog={SS["master_ssid"]: {"name": "Doctors", "columns": ["Other"]}})
    assert not res.ok
    assert any("PhoneNumber" in e for e in res.errors)
