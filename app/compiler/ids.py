"""Block id assignment: '{obj_id}_{globalCounter}'.

Mirrors the builder JS (html.js): a single monotonically increasing counter per
canvas; new blocks continue from max(existing)+1. First block starts at 2
(observed in real docs: entry blocks are *_2 or *_3).
"""
from __future__ import annotations


class IdAllocator:
    def __init__(self, start: int = 2):
        self._next = start

    @classmethod
    def continuing(cls, existing_ids: list[str]) -> "IdAllocator":
        mx = 1
        for bid in existing_ids:
            tail = bid.rsplit("_", 1)
            if len(tail) == 2 and tail[1].isdigit():
                mx = max(mx, int(tail[1]))
        return cls(start=mx + 1)

    def allocate(self, obj_id: str) -> str:
        bid = f"{obj_id}_{self._next}"
        self._next += 1
        return bid
