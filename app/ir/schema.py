"""The Intermediate Representation (IR) the LLM produces.

The IR is intentionally small: labels, block types, semantic config, and edges
by label. Everything mechanical (ids, obj_id, icon_path, positions, connection[],
full_objects, boilerplate props) is added by the compiler.

Reference syntax inside IR values: "{Label.field}" exactly as the platform DSL
(we keep the platform syntax rather than inventing {{...}} — one less rewrite,
and the few-shot examples map 1:1 to real documents).
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class IRStep(BaseModel):
    """One workflow block."""

    label: str = Field(description="Unique short name; the block's variable namespace, e.g. 'user' -> {user.Email}")
    block: str = Field(description="Legacy block type string, e.g. setvariable, condition, ssdatafilter, sendmail")
    description: str = ""

    # routing (by label)
    next: Optional[str] = Field(default=None, description="Label of next step (non-condition blocks)")
    yes: Optional[str] = Field(default=None, description="Condition true branch label")
    no: Optional[str] = Field(default=None, description="Condition false branch label")
    end: bool = Field(default=False, description="True if the workflow terminates after this step")

    # semantic config (block-family specific; compiler maps to block_properties)
    set: Optional[dict[str, str]] = Field(default=None, description="setvariable: {var: value} pairs")
    expr: Optional[str] = Field(default=None, description="condition: expression like '{user.Email}==x' with ops == != < >")

    spreadsheet: Optional[str] = Field(default=None, description="Spreadsheet name or master_ssid for ss* blocks")
    filters: Optional[dict[str, str]] = Field(default=None, description="ss blocks: {ColumnName: value-or-{Ref}}")
    operators: Optional[dict[str, str]] = Field(default=None, description="ss blocks: {ColumnName: '=' | '!=' | '=i' | '<' | '>'}")
    fields: Optional[dict[str, str]] = Field(default=None, description="insert/update ss + livecloud: {TargetColumn: value-or-{Ref}}")

    function: Optional[str] = Field(default=None, description="livecloudfunction: function name or Mongo _id")
    child_workflow: Optional[str] = Field(default=None, description="executeworkflow: child workflow name, short_code or _id")

    config: dict[str, Any] = Field(default_factory=dict, description="Any additional raw block_properties keys")

    @model_validator(mode="after")
    def _routing_sanity(self) -> "IRStep":
        if self.block == "condition":
            if not (self.yes or self.no):
                raise ValueError(f"condition step '{self.label}' needs yes/no targets")
        return self


class IRTrigger(BaseModel):
    type: Literal["genericpost", "datatransfer"] = "datatransfer"
    label: str = "Entry"
    auth_required: bool = False
    next: Optional[str] = Field(default=None, description="Label of the first step; defaults to steps[0]")
    config: dict[str, Any] = Field(default_factory=dict)


class WorkflowIR(BaseModel):
    name: str
    description: str = ""
    trigger: IRTrigger = Field(default_factory=IRTrigger)
    steps: list[IRStep] = Field(default_factory=list)

    def step_map(self) -> dict[str, IRStep]:
        return {s.label: s for s in self.steps}


def ir_json_schema() -> dict:
    """JSON schema handed to the LLM for structured output."""
    return WorkflowIR.model_json_schema()
