"""
Shared utility functions for the RTS Combat Overworld.
"""

from __future__ import annotations

from typing import Any


def get_system(caller: Any, system_name: str) -> Any | None:
    """Look up a game system by name.

    Checks caller.ndb.systems first (set during game init),
    then falls back to the module-level game_systems dict.
    """
    systems = getattr(getattr(caller, "ndb", None), "systems", None)
    if systems and isinstance(systems, dict):
        return systems.get(system_name)
    try:
        from server.conf.game_init import game_systems
        return game_systems.get(system_name)
    except (ImportError, AttributeError):
        return None


def get_coords(obj: Any) -> tuple[int, int] | None:
    """Extract (x, y) coordinates from an object.

    Checks coord_x/coord_y first (player in PlanetRoom),
    then x/y (OverworldRoom or building location).
    """
    if hasattr(obj, "db"):
        cx = getattr(obj.db, "coord_x", None)
        cy = getattr(obj.db, "coord_y", None)
        if cx is not None and cy is not None:
            return (int(cx), int(cy))
    x = getattr(obj, "x", None)
    y = getattr(obj, "y", None)
    if x is not None and y is not None:
        return (int(x), int(y))
    return None
