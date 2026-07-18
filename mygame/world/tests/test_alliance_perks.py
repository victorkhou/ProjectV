"""
Unit tests for alliance perk EFFECTS and the shallow-integration invariant.

Covers the perk-effect helpers that the combat/regen/harvest/fog seams call
(``perk_multiplier`` / ``perk_flat_bonus`` / ``has_shared_vision`` are exercised
in test_alliance_system.py; here we verify the shared-vision union's PLAYING-only
filter and the shallow-integration guard that no alliance module touches base
ownership).
"""

import os
import re
import types
import unittest

from world.event_bus import EventBus
from world.definitions import BalanceConfig
from world.systems.alliance_system import AllianceSystem


# -------------------------------------------------------------- #
#  Shared-vision union: PLAYING-only allies contribute
# -------------------------------------------------------------- #

class _FakeFog:
    """get_visible_tiles returns a single tile keyed off the player's id."""

    def get_visible_tiles(self, player, buildings):
        return {(player.id, 0)}


class _Db(types.SimpleNamespace):
    def __getattr__(self, _):
        return None


class _Player:
    _n = 0

    def __init__(self, alliance=None, state="playing", planet="terra"):
        _Player._n += 1
        self.id = _Player._n
        self.key = f"P{self.id}"
        self.has_account = True
        self.db = _Db(player_alliance=alliance, alliance_rank="member",
                      npc_type=None, player_state=state, combat_xp=0,
                      coord_planet=planet)
        self.tags = _NoTags()

    def get_buildings(self):
        return []

    def msg(self, *a, **k):
        pass


class _NoTags:
    def get(self, *a, **k):
        return []


class _Reg:
    def __init__(self):
        self._a = {}
        self._next = 1

    def get(self, aid):
        return self._a.get(aid)

    def all_alliances(self):
        return list(self._a.values())

    def by_tag(self, tag):
        return None

    def allocate_id(self):
        n = self._next
        self._next += 1
        return n

    def put(self, rec):
        self._a[rec["id"]] = rec

    def delete(self, aid):
        self._a.pop(aid, None)


class TestSharedVisionUnion(unittest.TestCase):
    def setUp(self):
        self.reg = _Reg()
        perks = {
            "shared_vision": {
                "category": "shared_vision", "effect_type": "boolean",
                "levels": {1: {"tier": 1, "effect": True, "cost": {}}},
            },
        }
        registry = types.SimpleNamespace(
            balance=BalanceConfig(), alliance_perks=perks,
            get_alliance_perk=lambda k: perks.get(k),
        )
        self.sys = AllianceSystem(registry, EventBus(),
                                  alliance_registry=self.reg,
                                  tick_provider=lambda: 0)
        self.people = {}
        self.sys._resolve_member = lambda cid: self.people.get(cid)
        self.sys._ensure_channel = lambda aid: None
        self.sys._subscribe = lambda p, aid: None
        self.fog = _FakeFog()

    def _mk(self, **kw):
        p = _Player(**kw)
        self.people[p.id] = p
        return p

    def _alliance_with(self, *members):
        aid = self.reg.allocate_id()
        rec = {
            "id": aid, "name": "A", "tag": "A", "leader_id": members[0].id,
            "officer_ids": [], "member_ids": [m.id for m in members[1:]],
            "treasury": {}, "active_perks": {"shared_vision": 1},
            "pending_invites": [], "pending_requests": [], "open_join": False,
            "withdraw_window": {}, "created_tick": 0, "renamed_tick": 0,
        }
        for m in members:
            m.db.player_alliance = aid
        self.reg.put(rec)
        return aid

    def test_playing_ally_contributes_vision(self):
        a = self._mk(state="playing")
        b = self._mk(state="playing")
        self._alliance_with(a, b)
        tiles = self.sys.shared_visible_tiles(a, [], self.fog)
        self.assertIn((a.id, 0), tiles)
        self.assertIn((b.id, 0), tiles)  # ally's tile unioned in

    def test_offline_ally_does_not_contribute(self):
        a = self._mk(state="playing")
        b = self._mk(state="lobby")  # not PLAYING -> no vision projected
        self._alliance_with(a, b)
        tiles = self.sys.shared_visible_tiles(a, [], self.fog)
        self.assertIn((a.id, 0), tiles)
        self.assertNotIn((b.id, 0), tiles)

    def test_no_perk_means_own_vision_only(self):
        a = self._mk(state="playing")
        b = self._mk(state="playing")
        aid = self._alliance_with(a, b)
        # Turn the perk off.
        self.reg.get(aid)["active_perks"] = {}
        tiles = self.sys.shared_visible_tiles(a, [], self.fog)
        self.assertEqual(tiles, {(a.id, 0)})

    def test_cross_planet_ally_does_not_contribute(self):
        # A PLAYING ally on a DIFFERENT planet must NOT leak vision — (x,y)
        # tuples carry no planet and planets share numeric ranges (Fix #3).
        a = self._mk(state="playing", planet="terra")
        b = self._mk(state="playing", planet="forge")
        self._alliance_with(a, b)
        tiles = self.sys.shared_visible_tiles(a, [], self.fog)
        self.assertIn((a.id, 0), tiles)
        self.assertNotIn((b.id, 0), tiles, "cross-planet ally vision must not leak")

    def test_same_planet_ally_still_contributes(self):
        a = self._mk(state="playing", planet="terra")
        b = self._mk(state="playing", planet="terra")
        self._alliance_with(a, b)
        tiles = self.sys.shared_visible_tiles(a, [], self.fog)
        self.assertIn((b.id, 0), tiles)


# -------------------------------------------------------------- #
#  Shallow-integration invariant (R16.1 / task 12.1)
# -------------------------------------------------------------- #

_ALLIANCE_MODULES = [
    "world/systems/alliance_system.py",
    "commands/alliance_commands.py",
]


class TestShallowIntegration(unittest.TestCase):
    """No alliance module may read or write base-ownership state."""

    def test_no_base_ownership_references(self):
        import io
        import tokenize

        here = os.path.dirname(__file__)
        root = os.path.abspath(os.path.join(here, "..", ".."))
        forbidden = {"owner_has_active_hq", "active_hq_owner_ids"}
        for rel in _ALLIANCE_MODULES:
            path = os.path.join(root, rel)
            with open(path) as f:
                src = f.read()
            # Only NAME tokens count as real code references — comments/docstrings
            # (which legitimately mention these in the "stays shallow" prose) are
            # excluded, so the guard flags a genuine call/read, not documentation.
            names = {
                tok.string
                for tok in tokenize.generate_tokens(io.StringIO(src).readline)
                if tok.type == tokenize.NAME
            }
            offending = forbidden & names
            self.assertFalse(
                offending,
                f"{rel} references base-ownership {offending} in CODE — alliances "
                f"must stay shallow (base ownership untouched).",
            )


if __name__ == "__main__":
    unittest.main()
