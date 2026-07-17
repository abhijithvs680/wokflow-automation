"""Auto-layout for canvas positions.

Simple layered layout matching how real workflows look: the main chain flows
downward (y += ~150), branches are offset to the right (x += ~210). Positions
are stored as strings (platform convention).
"""
from __future__ import annotations

X_START = 100.0
Y_START = 60.0
Y_STEP = 150.0
X_STEP = 210.0


def assign_positions(order: list[str], depth: dict[str, int], lane: dict[str, int]) -> dict[str, dict]:
    """order: block ids in BFS order; depth: id->row; lane: id->column."""
    pos: dict[str, dict] = {}
    for bid in order:
        x = X_START + lane.get(bid, 0) * X_STEP
        y = Y_START + depth.get(bid, 0) * Y_STEP
        pos[bid] = {"x": str(x), "y": str(y)}
    return pos


def bfs_layout(entry: str, edges: dict[str, list[tuple[str, int]]]) -> tuple[list[str], dict[str, int], dict[str, int]]:
    """edges: id -> [(child_id, lane_offset)]; lane_offset 0 = main, 1 = branch.

    Returns (order, depth, lane).
    """
    order: list[str] = []
    depth: dict[str, int] = {entry: 0}
    lane: dict[str, int] = {entry: 0}
    seen = {entry}
    queue = [entry]
    while queue:
        cur = queue.pop(0)
        order.append(cur)
        for child, offset in edges.get(cur, []):
            if child in seen or not child:
                continue
            seen.add(child)
            depth[child] = depth[cur] + 1
            lane[child] = lane[cur] + offset
            queue.append(child)
    return order, depth, lane
