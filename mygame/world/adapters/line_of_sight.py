"""
Line-of-sight for combat targeting.

Turrets and NPC guards acquire targets by Manhattan distance. Without a
line-of-sight test they fire *through* their own Walls, which trivializes the
"breach the walls to reach the core" raid design. This adapter builds a cheap
LOS predicate: a shot is blocked when a ``combat_barrier`` building (a Wall)
sits on any tile between the shooter and the target.

It stays out of ``world/systems`` (which is framework-free) — the systems take
an injected ``sight_blocked_func`` and this module supplies the concrete
implementation, resolving building occupancy via the PlanetRoom's
``get_buildings_at`` and the ``combat_barrier`` capability via the registry.
"""

from __future__ import annotations

from typing import Any, Callable


def _line_cells(x0: int, y0: int, x1: int, y1: int) -> list[tuple[int, int]]:
    """Bresenham cells strictly between (x0, y0) and (x1, y1) (endpoints excluded)."""
    cells: list[tuple[int, int]] = []
    dx = abs(x1 - x0)
    dy = abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx - dy
    x, y = x0, y0
    # Bound the walk defensively so a degenerate input can never loop forever.
    for _ in range(dx + dy + 2):
        if x == x1 and y == y1:
            break
        e2 = 2 * err
        if e2 > -dy:
            err -= dy
            x += sx
        if e2 < dx:
            err += dx
            y += sy
        if (x, y) != (x1, y1):
            cells.append((x, y))
    return cells


def make_sight_blocked(registry: Any = None) -> Callable[[Any, int, int, int, int], bool]:
    """Return a ``sight_blocked(location, x1, y1, x2, y2) -> bool`` predicate.

    ``True`` when a ``combat_barrier`` building lies on a tile strictly between
    the two points, i.e. the shooter cannot see/hit the target through a Wall.
    Only walls block — impassable terrain does not, to avoid over-nerfing
    ranged fire across e.g. water. Never raises: any lookup error resolves to
    "not blocked" so an LOS glitch can never silently disable all combat.
    """
    from world.constants import COMBAT_BARRIER
    from world.utils import building_has_capability

    def sight_blocked(location: Any, x1: int, y1: int, x2: int, y2: int) -> bool:
        if location is None or not hasattr(location, "get_buildings_at"):
            return False
        try:
            for cx, cy in _line_cells(int(x1), int(y1), int(x2), int(y2)):
                for b in location.get_buildings_at(cx, cy) or ():
                    if building_has_capability(b, COMBAT_BARRIER, provider=registry):
                        return True
            return False
        except Exception:  # noqa: BLE001 - LOS errors must never block combat
            return False

    return sight_blocked
