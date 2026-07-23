"""Provider-agnostic LLM client (any OpenAI-compatible chat completions API).

Set LLM_BASE_URL to use OpenRouter/Groq/Ollama/vLLM etc.; defaults to OpenAI.
Uses JSON mode + local Pydantic parsing (more portable than server-side
json_schema enforcement, which some providers don't support).
"""
from __future__ import annotations

import json

from openai import BadRequestError, OpenAI

from ..config import get_settings
from ..ir.schema import WorkflowIR


class LLMError(RuntimeError):
    pass


def _client() -> OpenAI:
    s = get_settings()
    if not s.llm_api_key:
        raise LLMError("LLM_API_KEY is not configured")
    return OpenAI(api_key=s.llm_api_key, base_url=s.llm_base_url)


def chat_json(messages: list[dict]) -> str:
    s = get_settings()
    kwargs = {
        "model": s.llm_model,
        "messages": messages,
        "temperature": s.llm_temperature,
        "response_format": {"type": "json_object"},
    }
    try:
        resp = _client().chat.completions.create(**kwargs)
    except BadRequestError as e:
        if "temperature" in str(e).lower():
            kwargs.pop("temperature", None)
            resp = _client().chat.completions.create(**kwargs)
        else:
            raise
            
    content = resp.choices[0].message.content or ""
    if not content.strip():
        raise LLMError("empty LLM response")
    return content


def parse_ir(raw: str) -> WorkflowIR:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise LLMError(f"LLM returned invalid JSON: {e}") from e
    return WorkflowIR.model_validate(data)
