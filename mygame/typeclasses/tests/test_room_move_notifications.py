"""
Unit tests for PlanetRoom tile-change notifications.

When an entity (player OR agent) leaves the tile you're standing on or arrives
on it, the other players on those tiles are told. The mover itself is never
notified. Uses the conftest Evennia stubs (no DB); the room is built via
__new__ with a real CoordinateIndex so get_players_at works.
"""

import unittest

from mygame.typeclasses.rooms import PlanetRoom
from mygame.world.coordinate.coordinate_index import CoordinateIndex


class _Tags:
    """Minimal tag handler: get(key=None, category=None) -> matching value/None."""

    def __init__(self, entries=None):
        # entries: list of (key, category)
        self._entries = list(entries or [])

    def get(self, key=None, category=None, **kw):
        for k, c in self._entries:
            if (key is None or k == key) and (category is None or c == category):
                return k
        return None


class _DB:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Player:
    """A player-like character (has_account True)."""

    def __init__(self, key, x, y):
        self.key = key
        self.has_account = True
        self.tags = _Tags()          # no npc_type / building tags
        self.db = _DB(coord_x=x, coord_y=y)
        self.messages = []

    def msg(self, text=None, **kw):
        if text is not None:
            self.messages.append(text)


class _Agent:
    """An NPC agent (npc_type tag, agent_id/role on db)."""

    def __init__(self, key, x, y, agent_id=1, role="harvester"):
        self.key = key
        self.has_account = False
        self.tags = _Tags([("agent", "npc_type")])
        self.db = _DB(coord_x=x, coord_y=y, agent_id=agent_id, role=role)


class _RoomWithIndex(PlanetRoom):
    """PlanetRoom whose coord_index is a plain settable attribute for tests."""

    def __init__(self, index):
        self._idx = index

    @property
    def coord_index(self):
        return self._idx


class TestTileChangeNotifications(unittest.TestCase):
    def _room(self):
        return _RoomWithIndex(CoordinateIndex())

    def test_other_player_notified_on_arrival_and_departure(self):
        room = self._room()
        observer = _Player("Observer", 5, 6)   # sits on the destination tile
        mover = _Player("Mover", 5, 5)
        room.coord_index.add(observer, 5, 6)
        room.coord_index.add(mover, 5, 5)

        # Mover steps north from (5,5) to (5,6) — onto the observer's tile.
        room.move_entity(mover, 5, 6)

        # Observer is told the mover arrived (from the south).
        self.assertTrue(
            any("Mover arrived" in m and "south" in m for m in observer.messages),
            observer.messages,
        )

    def test_departure_notifies_players_left_behind(self):
        room = self._room()
        stayer = _Player("Stayer", 5, 5)     # remains on the origin tile
        mover = _Player("Mover", 5, 5)
        room.coord_index.add(stayer, 5, 5)
        room.coord_index.add(mover, 5, 5)

        room.move_entity(mover, 6, 5)  # east

        self.assertTrue(
            any("Mover left" in m and "east" in m for m in stayer.messages),
            stayer.messages,
        )

    def test_mover_not_notified_about_itself(self):
        room = self._room()
        mover = _Player("Mover", 5, 5)
        room.coord_index.add(mover, 5, 5)

        room.move_entity(mover, 5, 6)

        # The mover gets its own "You move..." line from CmdMove, not an
        # arrived/left notification here.
        self.assertEqual(mover.messages, [])

    def test_agent_movement_still_notifies_players(self):
        room = self._room()
        observer = _Player("Observer", 5, 6)
        agent = _Agent("AgentObj", 5, 5, agent_id=3, role="scout")
        room.coord_index.add(observer, 5, 6)
        room.coord_index.add(agent, 5, 5)

        room.move_entity(agent, 5, 6)

        self.assertTrue(
            any("Agent #3" in m and "arrived" in m for m in observer.messages),
            observer.messages,
        )


if __name__ == "__main__":
    unittest.main()
