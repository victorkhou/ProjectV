"""
Unit tests for the BuildingFactory / TerrainProvider / MovingEntityRepository
ports and their use by BuildingSystem / MovementSystem — all Evennia-free.
"""

from mygame.world.core.ports.entity_repository import (
    BuildingFactory,
    MovingEntityRepository,
)
from mygame.world.core.ports.terrain_provider import TerrainProvider


class FakeTerrainProvider(TerrainProvider):
    """Returns canned terrain/resource by (planet, x, y)."""

    def __init__(self, mapping=None):
        # mapping: {(planet, x, y): (terrain, resource)}
        self.mapping = mapping or {}

    def get_terrain_and_resource(self, planet, x, y):
        return self.mapping.get((planet, x, y), (None, None))


class FakeMovingRepo(MovingEntityRepository):
    def __init__(self, npcs=None):
        self.npcs = list(npcs or [])

    def find_moving_npcs(self):
        return list(self.npcs)


class TestTerrainProviderPort:
    def test_returns_mapped_resource(self):
        p = FakeTerrainProvider({("mars", 1, 2): ("rock", "iron")})
        assert p.get_terrain_and_resource("mars", 1, 2) == ("rock", "iron")

    def test_unknown_tile_is_none(self):
        assert FakeTerrainProvider().get_terrain_and_resource("mars", 0, 0) == (None, None)

    def test_abstract(self):
        for cls in (TerrainProvider, BuildingFactory, MovingEntityRepository):
            try:
                cls()
            except TypeError:
                continue
            raise AssertionError(f"{cls.__name__} should be abstract")


class TestMovementSystemUsesRepo:
    def test_ensure_initialized_pulls_from_repo(self):
        from mygame.world.systems.movement_system import MovementSystem

        class _Npc:
            def __init__(self):
                self.db = type("D", (), {"movement_queue": [(1, 1)]})()

        npcs = [_Npc(), _Npc()]
        ms = MovementSystem(moving_entity_repository=FakeMovingRepo(npcs))
        ms._ensure_initialized()
        assert len(ms._moving_npcs) == 2
