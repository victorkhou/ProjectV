"""
Terrain lookup port.

Systems that need to know a tile's terrain/resource (e.g. Extractor placement
validation, active-presence harvesting) depend on this abstraction instead of
reaching into the ``game_systems`` global for the per-planet
``TerrainGenerator`` dict. The Evennia/composition-root wiring lives in
``world.adapters.game_systems_terrain_provider``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class TerrainProvider(ABC):
    """Read-only terrain/resource lookup by planet + coordinates."""

    @abstractmethod
    def get_terrain_and_resource(
        self, planet: str, x: int, y: int
    ) -> tuple[Any, Any]:
        """Return ``(terrain_type, resource_type)`` for a tile.

        ``resource_type`` is falsy (``None``/empty) when the tile has no
        resource. Returns ``(None, None)`` when the planet/generator is
        unavailable.
        """
