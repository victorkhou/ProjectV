"""
Unit tests for format_building_interior — specifically that it lists other
players standing on the building's tile, so entering a building reveals who is
inside (previously only an explicit `look` showed them).
"""

import unittest

from mygame.world.ui_formatters import format_building_interior


class _DB:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Tile:
    """PlanetRoom stand-in exposing get_players_at / get_objects_at."""

    def __init__(self, players_by_coord=None):
        self._players = players_by_coord or {}

    def get_players_at(self, x, y):
        return list(self._players.get((x, y), []))

    def get_objects_at(self, x, y, type_tag=None):
        return []


class _Player:
    def __init__(self, key):
        self.key = key
        self.db = _DB()


class _Building:
    """Minimal building at (5, 5) on a tile."""

    def __init__(self, tile):
        self.key = "Armory"
        self.location = tile
        self.db = _DB(
            building_type="AR", building_level=1, hp=100, hp_max=100,
            owner=None, coord_x=5, coord_y=5, closed_exits=None,
        )


class TestInteriorListsPlayers(unittest.TestCase):
    def test_other_player_on_tile_is_listed(self):
        looker = _Player("Looker")
        other = _Player("Rival")
        tile = _Tile({(5, 5): [looker, other]})
        building = _Building(tile)

        out = format_building_interior(looker, building, registry=None)

        self.assertIn("Players here:", out)
        self.assertIn("Rival", out)
        # The looker never lists themselves.
        self.assertNotIn("Looker", out.split("Players here:", 1)[1])

    def test_only_looker_present_lists_no_players(self):
        looker = _Player("Looker")
        tile = _Tile({(5, 5): [looker]})
        building = _Building(tile)

        out = format_building_interior(looker, building, registry=None)

        self.assertNotIn("Players here:", out)

    def test_no_players_section_when_tile_empty(self):
        looker = _Player("Looker")
        tile = _Tile({(5, 5): []})
        building = _Building(tile)

        out = format_building_interior(looker, building, registry=None)

        self.assertNotIn("Players here:", out)


if __name__ == "__main__":
    unittest.main()
