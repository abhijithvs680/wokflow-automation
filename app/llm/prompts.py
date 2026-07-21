"""Prompt construction: static cached prefix + dynamic tail.

The static prefix (system message) holds the DSL cheatsheet and a gold few-shot
example modeled on a real production workflow — providers cache this across
requests. The dynamic tail carries only the retrieved tenant context and the
user's request.
"""
from __future__ import annotations

import json

from ..catalog import blocks as cat

SYSTEM_PROMPT = """You are a workflow architect for the Vizru low-code platform. You convert a user's
natural-language use case into a JSON workflow IR (intermediate representation).
Output ONLY valid JSON matching the provided schema. No prose.

## IR rules
- `trigger`: how the workflow starts. Use "genericpost" for API-triggered flows
  (external callers POST JSON; fields readable as {Entry.field}), or
  "datatransfer" for internal/parameter entry. Label it "Entry" unless told otherwise.
- `steps`: ordered blocks. Every step needs a unique, meaningful `label` —
  labels are the VARIABLE NAMESPACE: later steps reference outputs as {Label.field}.
- Routing: non-condition steps use `next` (label) or `end: true`.
  Condition steps use `yes` / `no` (labels). A missing branch may be omitted only
  if that path should terminate.
- Multiple terminal steps are fine (success path, error paths).

## Template DSL (used inside values)
- {Label.field} — output of an earlier step (e.g. {Entry.phone}, {user.Email}).
- Nested paths allowed: {Res.Body.token}
- System vars: {viz-uuid} {viz-timestamp} {viz-domain} {tenantid}
- Runtime flags: {httpcode} (after livecloudfunction/genericpost/get),
  {filter-count} (after ssdatafilter)
- Helpers: {%strlen X%} {%math EXPR%} {%implode "sep" arr.value%} {%regexE "/pat/" X "1.0"%}

## How to design a workflow
1. Identify what the use case NEEDS: user accounts? data storage? emails? files? external APIs?
2. For each need, pick the DEDICATED block from the capability domains below.
   - NEVER use spreadsheet insert/filter as a substitute when a dedicated block exists.
     Example: to create a user, use `adduser`, NOT insertssdata into a Users spreadsheet.
     Example: to grant app access, use `addusertolivespace`, NOT updatessdata.
3. Add conditions to handle failures (user not found, HTTP errors, empty results).
4. End with a setvariable or customoutput that returns the result.

## IR Syntax for Special Blocks
- setvariable: `set` = {var: value}.
- condition: `expr` like "{user.Email}==user.Email" or "{httpcode}==200". Operators: == != < >
- ssdatafilter / ssdeleterow / ssautoincrementcol: `spreadsheet`, `filters` {Col: val}, `operators` {Col: "="}.
  After a filter, row fields are {Label.Column} and count is {filter-count}.
- insertssdata / updatessdata / insertorupdatessdata: `spreadsheet`, `filters` (for updates), `fields` {Col: val}.
- livecloudfunction: `function` = function name; `fields` = inputs. Response fields: {Label.field}.
- executeworkflow: `child_workflow` = child workflow name.
- sendmail: put mail_to, mail_subject, mail_content in `config`.
- customoutput: put outputDataType in `config`.
- clearoutput: insert between a condition and the next data-producing block. (No config).
- All other blocks: put all their properties inside `config`.

## Conventions observed in production workflows
- API flows: genericpost Entry -> lookups/filters -> conditions -> actions ->
  a final setvariable named "Output" holding the response.
- Always handle the failure branch of important conditions (not-found, expired,
  non-200 http) with a terminal setvariable like {error=True;message=...}.
"""

# Gold few-shot: compact version of the real HMS_GenerateDoctorOTP flow.
FEW_SHOT_USER = (
    "Create a workflow: user posts a phone number; look up the user in the "
    "'Doctors' spreadsheet by PhoneNumber; if not found return an error; if "
    "found generate a 4-digit OTP, store it on the user's row (Status=Created), "
    "and return success."
)

FEW_SHOT_IR = {
    "name": "GenerateDoctorOTP",
    "description": "Send OTP to a doctor by phone",
    "trigger": {"type": "genericpost", "label": "Entry", "auth_required": False},
    "steps": [
        {"label": "user", "block": "ssdatafilter", "spreadsheet": "Doctors",
         "filters": {"PhoneNumber": "{Entry.phone}", "UserType": "Doctor"},
         "operators": {"PhoneNumber": "=", "UserType": "=i"}, "next": "found"},
        {"label": "found", "block": "condition", "expr": "{filter-count}>0",
         "yes": "OTP", "no": "notfound"},
        {"label": "OTP", "block": "setvariable",
         "set": {"value": "{%regexE \"/(\\d{4})/\" {viz-uuid} \"1.0\"%}"}, "next": "store"},
        {"label": "store", "block": "updatessdata", "spreadsheet": "Doctors",
         "filters": {"PhoneNumber": "{user.PhoneNumber}"},
         "fields": {"OTP": "{OTP.value}", "Status": "Created",
                    "CreatedTimeStamp": "{viz-timestamp}"}, "next": "Output"},
        {"label": "Output", "block": "setvariable",
         "set": {"error": "False", "message": "OTP Sent"}, "end": True},
        {"label": "notfound", "block": "setvariable",
         "set": {"error": "True", "message": "Account not found. Sign up to get started."},
         "end": True},
    ],
}

EDIT_SYSTEM_PROMPT = SYSTEM_PROMPT + """

## Edit mode
You are given the CURRENT workflow as IR plus an edit instruction. Return the
FULL updated IR (not a diff). Keep every step you are not asked to change —
same labels, same config — so unchanged blocks keep their identity.
"""


def build_context(block_types: list[str] | None,
                  spreadsheets: list[dict],
                  functions: list[dict],
                  child_workflows: list[dict]) -> str:
    parts = ["## Available blocks", cat.prompt_catalog(block_types)]
    if spreadsheets:
        parts.append("\n## Tenant spreadsheets (use exact names & column names)")
        for ss in spreadsheets:
            cols = ", ".join(f"{c['name']}({c['type']})" for c in ss["columns"])
            parts.append(f"- \"{ss['name']}\": columns [{cols}]")
    if functions:
        parts.append("\n## LiveCloud functions (for livecloudfunction blocks)")
        parts.extend(f"- \"{f['name']}\"" for f in functions)
    if child_workflows:
        parts.append("\n## Child workflows (for executeworkflow blocks)")
        parts.extend(f"- \"{w['name']}\"" for w in child_workflows)
    return "\n".join(parts)


def generation_messages(context: str, user_prompt: str) -> list[dict]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": FEW_SHOT_USER},
        {"role": "assistant", "content": json.dumps(FEW_SHOT_IR)},
        {"role": "user", "content": f"{context}\n\n## Request\n{user_prompt}"},
    ]


def edit_messages(context: str, current_ir: dict, instruction: str) -> list[dict]:
    return [
        {"role": "system", "content": EDIT_SYSTEM_PROMPT},
        {"role": "user", "content": (
            f"{context}\n\n## Current workflow IR\n{json.dumps(current_ir)}\n\n"
            f"## Edit instruction\n{instruction}"
        )},
    ]


def repair_messages(previous: list[dict], bad_ir: str, errors: list[str]) -> list[dict]:
    return previous + [
        {"role": "assistant", "content": bad_ir},
        {"role": "user", "content": (
            "That IR failed validation with these errors:\n- "
            + "\n- ".join(errors)
            + "\nReturn the corrected FULL IR JSON only."
        )},
    ]
