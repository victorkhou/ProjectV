"""
Unit tests for the line-of-sight adapter (combat targeting through Walls).

A shot is blocked when a ``combat_barrier`` building sits on a tile strictly
between shooter and target; endpoints never block.
"""

import types
import unittest

from mygame.world.adapters.line_of_sight import make_sight_blocked, _line_cells


class _Bldg:
    def __init__(self, capabilities):
        self._caps = frozenset(capabilities)
        self.db = types.SimpleNamespace(building_type="WL")
        self.attributes = types.SimpleNamespace(
            get=lambda key, default=None: (
                "WL" if key == "building_type" else default
            )
        )


class _Registry:
    """Resolves a building_type to a def exposing has_capability."""
    def __init__(self):
        self._defs = {
            "WL": types.SimpleNamespace(
                has_capability=lambda cap: cap == "combat_barrier"
            ),
            "TU": types.SimpleNamespace(has_capability=lambda cap: cap == "turret"),
        }

    def resolve_building(self, btype):
        return self._defs.get(btype)


class _Room:
    def __init__(self, walls=None):
        # walls: set of (x, y) that hold a combat_barrier building
        self._walls = set(walls or [])

    def get_buildings_at(self, x, y):
        return [_Bldg({"combat_barrier"})] if (x, y) in self._walls else []


class TestLineCells(unittest.TestCase):
    def test_excludes_endpoints(self):
        cells = _line_cells(0, 0, 3, 0)
        self.assertNotIn((0, 0), cells)
        self.assertNotIn((3, 0), cells)
        self.assertEqual(cells, [(1, 0), (2, 0)])

    def test_adjacent_has_no_between_cells(self):
        self.assertEqual(_line_cells(0, 0, 1, 0), [])


class TestSightBlocked(unittest.TestCase):
    def setUp(self):
        # building_has_capability resolves via provider.resolve_building; the
        # adapter passes our registry as provider, so no live registry needed.
        self.registry = _Registry()
        self.sight = make_sight_blocked(self.registry)

    def test_wall_between_blocks(self):
        room = _Room(walls={(2, 0)})
        self.assertTrue(self.sight(room, 0, 0, 4, 0))

    def test_clear_line_not_blocked(self):
        room = _Room(walls=set())
        self.assertFalse(self.sight(room, 0, 0, 4, 0))

    def test_wall_on_endpoint_does_not_block(self):
        # A wall on the target tile itself must not block (you're hitting it).
        room = _Room(walls={(4, 0)})
        self.assertFalse(self.sight(room, 0, 0, 4, 0))

    def test_adjacent_never_blocked(self):
        room = _Room(walls={(0, 0), (1, 0)})
        self.assertFalse(self.sight(room, 0, 0, 1, 0))

    def test_missing_query_method_is_not_blocked(self):
        self.assertFalse(self.sight(object(), 0, 0, 5, 0))

    def test_none_location_not_blocked(self):
        self.assertFalse(self.sight(None, 0, 0, 5, 0))


if __name__ == "__main__":
    unittest.main()
