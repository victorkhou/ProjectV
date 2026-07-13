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

    def __init__(self, players_by_coord=None, objects_by_tag=None):
        self._players = players_by_coord or {}
        # {(x, y, type_tag): [objs]}
        self._objects = objects_by_tag or {}

    def get_players_at(self, x, y):
        return list(self._players.get((x, y), []))

    def get_objects_at(self, x, y, type_tag=None):
        return list(self._objects.get((x, y, type_tag), []))


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


class _Item:
    def __init__(self, key, count=None):
        self.key = key
        self.db = _DB(count=count)


class TestInteriorListsItems(unittest.TestCase):
    """The interior view lists dropped/produced items on the building's tile
    (e.g. gear an assigned engineer produced), so they're visible while inside."""

    def test_dropped_item_on_tile_is_listed(self):
        looker = _Player("Looker")
        tile = _Tile(objects_by_tag={(5, 5, "item"): [_Item("Combat Knife")]})
        building = _Building(tile)

        out = format_building_interior(looker, building, registry=None)

        self.assertIn("Items:", out)
        self.assertIn("Combat Knife", out)

    def test_supply_item_shows_count(self):
        looker = _Player("Looker")
        tile = _Tile(objects_by_tag={(5, 5, "item"): [_Item("Rifle Rounds", count=30)]})
        building = _Building(tile)

        out = format_building_interior(looker, building, registry=None)

        self.assertIn("Rifle Rounds x30", out)

    def test_no_items_section_when_tile_has_none(self):
        looker = _Player("Looker")
        tile = _Tile()  # no items
        building = _Building(tile)

        out = format_building_interior(looker, building, registry=None)

        self.assertNotIn("Items:", out)


class _Tags:
    """Minimal Evennia tag-handler stand-in: get(category=...) truthiness."""
    def __init__(self, npc_type=None):
        self._npc_type = npc_type

    def get(self, category=None):
        if category == "npc_type":
            return self._npc_type
        return None


class _Sentinel:
    """An NPC-base owner: is_sentinel True."""
    def __init__(self):
        self.key = "Sentinel"
        self.db = _DB(is_sentinel=True)


class _NPC:
    """An NPC on a tile: npc_type tag + db.owner + role."""
    def __init__(self, key, owner, role="guard"):
        self.key = key
        self.tags = _Tags(npc_type="enemy")
        self.db = _DB(owner=owner, role=role, agent_id=7)


class TestInteriorListsHostiles(unittest.TestCase):
    """The interior view lists hostile NPCs on the tile so a raider can see the
    guard attacking them from inside the same building (the reported bug: being
    hit with an empty occupant list)."""

    def test_enemy_guard_on_tile_is_listed_and_tagged(self):
        looker = _Player("Raider")
        guard = _NPC("Guard #1", owner=_Sentinel(), role="guard")
        tile = _Tile(objects_by_tag={(5, 5, None): [guard]})
        building = _Building(tile)

        out = format_building_interior(looker, building, registry=None)

        self.assertIn("Hostiles here:", out)
        self.assertIn("Guard #1", out)
        self.assertIn("[Enemy]", out)

    def test_own_agent_not_listed_as_hostile(self):
        looker = _Player("Owner")
        mine = _NPC("Agent", owner=looker, role="guard")
        tile = _Tile(objects_by_tag={(5, 5, None): [mine]})
        building = _Building(tile)

        out = format_building_interior(looker, building, registry=None)

        self.assertNotIn("Hostiles here:", out)
        self.assertIn("Agents here:", out)  # shown as the looker's own agent

    def test_no_hostiles_section_when_tile_clear(self):
        looker = _Player("Raider")
        tile = _Tile(objects_by_tag={(5, 5, None): []})
        building = _Building(tile)

        out = format_building_interior(looker, building, registry=None)

        self.assertNotIn("Hostiles here:", out)


if __name__ == "__main__":
    unittest.main()
