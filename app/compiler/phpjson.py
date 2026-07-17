"""PHP-compatible json_encode for full_objects.

PHP's json_encode (default flags) differs from Python's json.dumps:
  - escapes forward slashes:  / -> \\/
  - escapes non-ASCII as \\uXXXX  (ensure_ascii=True already does this)
  - no spaces after separators
"""
from __future__ import annotations

import json
from typing import Any


def php_json_encode(value: Any) -> str:
    s = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    return s.replace("/", "\\/")
