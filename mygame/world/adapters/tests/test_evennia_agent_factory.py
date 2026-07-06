"""
Unit tests for EvenniaAgentFactory spawn-coordinate resolution.

Regression guard for the two-stage HQ→owner fallback that the port extraction
must preserve: an HQ found WITHOUT coordinates must still fall back to the
owner's own position (not spawn the agent unplaced).
"""

from mygame.world.adapters.evennia_agent_repository import EvenniaAgentFactory


class _Db:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Building:
    def __init__(self, building_type, coord_x=None, coord_y=None):
        self.db = _Db(building_type=building_type, coord_x=coord_x, coord_y=coord_y)


class _Owner:
    def __init__(self, buildings, coord_x=None, coord_y=None):
        self._buildings = buildings
        self.db = _Db(coord_x=coord_x, coord_y=coord_y)

    def get_buildings(self):
        return self._buildings


_resolve = EvenniaAgentFactory._resolve_spawn_coords


class TestResolveSpawnCoords:
    def test_hq_with_coords_used(self):
        owner = _Owner([_Building("HQ", 5, 7)], coord_x=1, coord_y=1)
        assert _resolve(owner) == (5, 7)

    def test_hq_without_coords_falls_back_to_owner(self):
        # The regression: HQ exists but has no coords; owner DOES have coords.
        owner = _Owner([_Building("HQ", None, None)], coord_x=3, coord_y=4)
        assert _resolve(owner) == (3, 4)

    def test_no_hq_falls_back_to_owner(self):
        owner = _Owner([_Building("EX", 9, 9)], coord_x=2, coord_y=2)
        assert _resolve(owner) == (2, 2)

    def test_no_hq_no_owner_coords_is_none(self):
        owner = _Owner([], coord_x=None, coord_y=None)
        assert _resolve(owner) == (None, None)

    def test_only_first_hq_considered(self):
        # Matches the original `break`: a second HQ with coords is not consulted
        # once the first HQ (without coords) is seen — falls back to owner.
        owner = _Owner(
            [_Building("HQ", None, None), _Building("HQ", 8, 8)],
            coord_x=6, coord_y=6,
        )
        assert _resolve(owner) == (6, 6)
