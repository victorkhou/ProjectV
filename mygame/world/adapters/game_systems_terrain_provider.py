"""
TerrainProvider backed by the per-planet TerrainGenerator dict.

Wraps the ``{planet: TerrainGenerator}`` mapping built at the composition root
so systems can look up terrain/resource by coordinates without importing the
``game_systems`` global. Holds a direct reference to the generators dict,
injected at ``game_init``.
"""

from __future__ import annotations

import logging
from typing import Any

from world.core.ports.terrain_provider import TerrainProvider

logger = logging.getLogger("evennia.world.adapters.terrain")


class GameSystemsTerrainProvider(TerrainProvider):
    """Looks up terrain/resource via the injected per-planet generators."""

    def __init__(self, terrain_generators: dict[str, Any] | None = None) -> None:
        self._generators = terrain_generators or {}

    def get_terrain_and_resource(self, planet: str, x: int, y: int) -> tuple[Any, Any]:
        gen = self._generators.get(planet)
        if gen is None:
            return None, None
        try:
            return gen.get_terrain_and_resource(int(x), int(y))
        except Exception:
            logger.exception(
                "get_terrain_and_resource failed for planet=%s (%s,%s)", planet, x, y
            )
            return None, None
