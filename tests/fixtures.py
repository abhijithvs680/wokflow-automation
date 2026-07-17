"""Load real VizWorkflow documents from ../referance.json (Mongo shell dump).

referance.json is a Node/Mongo-shell style dump: single quotes, unquoted keys,
ObjectId("..."), ISODate("..."), and multiple top-level arrays concatenated.
We normalise the Mongo constructors with regex, then parse with pyjson5.
"""
from __future__ import annotations

import os
import re

import pyjson5

_HERE = os.path.dirname(__file__)
REFERENCE_PATH = os.path.normpath(os.path.join(_HERE, "..", "..", "referance.json"))

_CONSTRUCTOR_RE = re.compile(r'(?:ObjectId|ISODate)\("([^"]*)"\)')


def load_reference_docs(path: str = REFERENCE_PATH) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    text = _CONSTRUCTOR_RE.sub(r'"\1"', text)

    # split concatenated top-level arrays: "]\n[" boundaries
    chunks = re.split(r"\]\s*\n\s*\[", text)
    docs: list[dict] = []
    for i, chunk in enumerate(chunks):
        if not chunk.strip():
            continue
        if not chunk.lstrip().startswith("["):
            chunk = "[" + chunk
        if not chunk.rstrip().endswith("]"):
            chunk = chunk + "]"
        parsed = pyjson5.decode(chunk)
        if isinstance(parsed, list):
            docs.extend(d for d in parsed if isinstance(d, dict))
    return docs
