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

## How to think (mandatory)
Before writing steps, fill the `plan` array: decompose the request into EVERY
distinct requirement and name the block type for each. Work top-down through
the platform's capability layers — a request usually spans several:

1. PLATFORM layer — user accounts and identity. Creating/inviting a person =
   `adduser` (upserts the account, can send the welcome email). Checking
   existence = `getuser`. Disabling = `deactivateuser`.
2. APP layer — membership and permissions. Giving a user access to an app
   (livespace) with a role = `addusertolivespace`. Removing = `removeuserfromlivespace`.
   Listing = `getuserlivespaces` / `getlivespacemembers`.
3. DATA layer — the app's spreadsheets. Reading = `ssdatafilter`; writing =
   `insertssdata` / `updatessdata` / `insertorupdatessdata`. A spreadsheet row is
   NOT a user account: writing a "Users" spreadsheet row does not create a login,
   and creating a login does not write app data. Complete flows usually need both.
4. INTEGRATION layer — `livecloudfunction` (external endpoints), `executeworkflow` /
   `backgroundworkflow` (child workflows), `genericget`/`genericpost` HTTP.
5. COMMUNICATION layer — `sendmail`, `notify`, `realtimepush`, `twilio`.
6. CONTROL layer — `condition` checks, `setvariable` outputs, `clearoutput`,
   utility blocks (`date`, `math`, `string`, `arrayextract`).

Selection rules:
- Cover ALL layers the request touches. "Onboard a user to an app" = platform
  account (adduser) + app membership (addusertolivespace) + any app data rows
  (insertssdata) + duplicate checks (getuser / ssdatafilter+condition) + response.
- Choose blocks by capability from the catalog below — never substitute a
  spreadsheet write for a platform/app action or vice versa.
- Add the checks a careful builder would: does the entity already exist? did the
  action succeed ({httpcode}, {filter-count})? Always give failure branches a
  terminal step with a clear error message.
- Every plan item must map to at least one step. Do not leave requirements
  uncovered.

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

## Block-specific IR fields
- setvariable: `set` = {var: value}. Commonly used to build the final output payload
  (e.g. set {error, message} or the response fields) before ending.
- condition: `expr` like "{user.Email}==user.Email" or "{httpcode}==200" or
  "{%strlen OTP.value%}<32". Operators: == != < >
- ssdatafilter / ssdeleterow / ssautoincrementcol: `spreadsheet` (exact name from
  the provided catalog), `filters` {Column: value-or-{Ref}}, `operators`
  {Column: "=" | "!=" | "=i" (case-insensitive) | "<" | ">"}.
  After a filter, row fields are {Label.Column} and count is {filter-count}.
- insertssdata / updatessdata / insertorupdatessdata: `spreadsheet`, `filters`
  (for update matching), `fields` = {Column: value-or-{Ref}} to write.
- livecloudfunction: `function` = function name from catalog; `fields` = inputs
  (HTTP headers/params as shown in the catalog). Response fields: {Label.field};
  check {httpcode} in a following condition.
- executeworkflow: `child_workflow` = child workflow name from catalog.
- customoutput: `config` with outputDataType (usually "json").
- clearoutput: insert between a condition branch and the next data-producing block
  to clear accumulated output (common platform pattern; no config needed).
- ALL other blocks (adduser, addusertolivespace, sendmail, notify, date, ...):
  put their config keys in `config`, using the exact key names listed in the
  block catalog (e.g. adduser -> config: {email, name, systemRoleName, sendmail};
  addusertolivespace -> config: {email, lid, livespaceroleName}). Values may use
  {Ref} templates. Use the LiveSpace context section (lid, short_code, role
  names) for app-scoped config values when provided.

## Conventions observed in production workflows
- API flows: genericpost Entry -> lookups/filters -> conditions -> actions ->
  a final setvariable named "Output" (or customoutput) holding the response.
- Always handle the failure branch of important conditions (not-found, expired,
  non-200 http) with a terminal setvariable like {error=True;message=...}.
"""

# Gold few-shot: compact version of the real HMS_GenerateDoctorOTP flow.
# NOTE: this demonstrates the IR FORMAT only — block choice must always come
# from the capability layers + catalog, not from this example.
FEW_SHOT_USER = (
    "Create a workflow: user posts a phone number; look up the user in the "
    "'Doctors' spreadsheet by PhoneNumber; if not found return an error; if "
    "found generate a 4-digit OTP, store it on the user's row (Status=Created), "
    "and return success. (FORMAT EXAMPLE — your block selection must follow the "
    "capability layers, not copy this example.)"
)

FEW_SHOT_IR = {
    "plan": [
        "DATA: look up doctor by phone in 'Doctors' spreadsheet -> ssdatafilter",
        "CONTROL: branch on found/not-found -> condition on {filter-count}",
        "CONTROL: generate 4-digit OTP -> setvariable with helper",
        "DATA: store OTP on the user's row -> updatessdata",
        "CONTROL: success response -> setvariable (terminal)",
        "CONTROL: not-found error response -> setvariable (terminal)",
    ],
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
                  child_workflows: list[dict],
                  livespace: dict | None = None) -> str:
    parts = ["## Available blocks (the complete catalog — choose by capability)",
             cat.prompt_catalog(block_types)]
    if livespace:
        parts.append("\n## LiveSpace (app) context — the target app for this workflow")
        parts.append(f"- name: \"{livespace['name']}\", lid: {livespace['lid']}, "
                     f"short_code: \"{livespace['short_code']}\"")
        parts.append("- use this lid / short_code for app-scoped config "
                     "(addusertolivespace.lid, livespace_shortcode, getlivespacemembers.lid, ...)")
        if livespace.get("roles"):
            parts.append("- livespace role names (for livespaceroleName): "
                         + ", ".join(f'"{r}"' for r in livespace["roles"]))
    if spreadsheets:
        header = ("\n## Spreadsheets in this app (use exact names & column names)"
                  if livespace else
                  "\n## Tenant spreadsheets (use exact names & column names)")
        parts.append(header)
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
