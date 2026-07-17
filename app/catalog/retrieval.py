"""Lightweight lexical retrieval to keep LLM context small.

No embeddings service required (keeps the stack dependency-free and free of
per-request cost); a simple token-overlap scorer picks the top-K spreadsheets,
functions, and child workflows relevant to the user's prompt. Swap in
embeddings later behind the same interface if recall becomes a problem.
"""
from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall((text or "").lower()))


def _score(query_tokens: set[str], text: str) -> float:
    t = _tokens(text)
    if not t:
        return 0.0
    return len(query_tokens & t) / (len(t) ** 0.5)


def top_spreadsheets(prompt: str, catalog: list[dict], k: int = 5) -> list[dict]:
    q = _tokens(prompt)
    scored = []
    for ss in catalog:
        text = ss["name"] + " " + " ".join(c["name"] for c in ss["columns"])
        scored.append((_score(q, text), ss))
    scored.sort(key=lambda x: x[0], reverse=True)
    picked = [ss for score, ss in scored[:k] if score > 0]
    return picked or [ss for _, ss in scored[:k]]


def top_named(prompt: str, items: list[dict], k: int = 5) -> list[dict]:
    q = _tokens(prompt)
    scored = sorted(((_score(q, it["name"]), it) for it in items), key=lambda x: x[0], reverse=True)
    return [it for score, it in scored[:k] if score > 0]
