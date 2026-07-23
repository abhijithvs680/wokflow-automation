"""Tests for full block awareness: catalog completeness in the prompt,
plan-coverage enforcement, and livespace context rendering."""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))

from app.catalog import blocks as cat
from app.ir.schema import WorkflowIR
from app.llm import prompts
from app.llm.flows import _plan_coverage_errors


def test_prompt_catalog_contains_every_block_type():
    text = cat.prompt_catalog(None)
    for block_type in cat.palette():
        assert f"- {block_type}:" in text, f"block '{block_type}' missing from prompt catalog"


def test_prompt_catalog_has_usermanagement_config_keys():
    """The onboarding blocks must expose their real config contract."""
    text = cat.prompt_catalog(None)
    assert "systemRoleName" in text          # adduser alternative role key
    assert "livespaceroleName" in text       # addusertolivespace role-by-name
    assert "livespace_shortcode" in text     # lid alternative
    assert "{Label.uid}" in text             # adduser outputs documented


def test_system_prompt_teaches_capability_layers():
    p = prompts.SYSTEM_PROMPT
    assert "PLATFORM layer" in p and "APP layer" in p and "DATA layer" in p
    assert "adduser" in p and "addusertolivespace" in p
    # the critical anti-confusion rule
    assert "NOT a user account" in p


def test_few_shot_ir_is_valid_and_has_plan():
    ir = WorkflowIR.model_validate(prompts.FEW_SHOT_IR)
    assert ir.plan, "few-shot must demonstrate the plan field"


def test_plan_coverage_flags_missing_block():
    ir = WorkflowIR.model_validate({
        "plan": ["PLATFORM: create account -> adduser",
                 "DATA: store profile row -> insertssdata"],
        "name": "x",
        "trigger": {"type": "genericpost", "label": "Entry"},
        "steps": [
            {"label": "row", "block": "insertssdata", "spreadsheet": "S",
             "fields": {"A": "1"}, "end": True},
        ],
    })
    errors = _plan_coverage_errors(ir)
    assert any("adduser" in e for e in errors)
    assert not any("insertssdata" in e for e in errors)


def test_plan_coverage_ok_when_all_used():
    ir = WorkflowIR.model_validate({
        "plan": ["PLATFORM: create account -> adduser"],
        "name": "x",
        "trigger": {"type": "genericpost", "label": "Entry"},
        "steps": [
            {"label": "acct", "block": "adduser",
             "config": {"email": "{Entry.email}", "name": "{Entry.name}",
                        "systemRoleName": "Member", "sendmail": "on"},
             "end": True},
        ],
    })
    assert _plan_coverage_errors(ir) == []


def test_build_context_renders_livespace():
    ls = {"lid": 1795, "name": "HMS", "short_code": "hmsapp123", "roles": ["Member", "Admin"]}
    ss = [{"name": "Doctors", "columns": [{"name": "PhoneNumber", "type": "string"}]}]
    ctx = prompts.build_context(None, ss, [], [], livespace=ls)
    assert "lid: 1795" in ctx
    assert "hmsapp123" in ctx
    assert '"Member", "Admin"' in ctx
    assert "Spreadsheets in this app" in ctx
    assert '"Doctors"' in ctx


def test_adduser_step_compiles_with_config():
    """usermanagement steps must compile with config keys passed through."""
    from app.compiler.compile import Compiler
    from app.validate.validate import validate_workflow

    ir = WorkflowIR.model_validate({
        "name": "Onboard",
        "trigger": {"type": "genericpost", "label": "Entry"},
        "steps": [
            {"label": "exists", "block": "getuser",
             "config": {"email": "{Entry.email}"}, "next": "check"},
            {"label": "check", "block": "condition", "expr": "{exists.uid}!=",
             "yes": "member", "no": "acct"},
            {"label": "acct", "block": "adduser",
             "config": {"email": "{Entry.email}", "name": "{Entry.name}",
                        "systemRoleName": "Member", "sendmail": "on"},
             "next": "member"},
            {"label": "member", "block": "addusertolivespace",
             "config": {"email": "{Entry.email}", "lid": "1795",
                        "livespaceroleName": "Member"}, "next": "done"},
            {"label": "done", "block": "setvariable",
             "set": {"error": "False", "message": "onboarded"}, "end": True},
        ],
    })
    doc = Compiler().compile(ir)
    by_type = {o["type"]: o for o in doc["w_objects"]}
    assert by_type["adduser"]["obj_id"] == "7000"
    assert by_type["adduser"]["block_properties"]["systemRoleName"] == "Member"
    assert by_type["addusertolivespace"]["obj_id"] == "7005"
    assert by_type["addusertolivespace"]["block_properties"]["livespaceroleName"] == "Member"
    res = validate_workflow(doc)
    assert res.ok, res.errors
