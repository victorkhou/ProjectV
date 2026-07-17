"""
Unit tests for ``world.utils.are_allied`` — the single ally predicate.

Truth table: two distinct real players sharing a live alliance are allied;
everything else (same player, different/None alliance, non-real player, dead
alliance id, unavailable system) is NOT allied, failing toward "not allied" so a
lookup failure never suppresses legitimate hostile targeting.
"""

import types
import unittest

from world import utils


class _Db:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)
        # Defaults for the reads are_allied performs.
        for k in ("player_alliance", "npc_type"):
            if not hasattr(self, k):
                setattr(self, k, None)

    def __getattr__(self, _):  # unset attrs read as None (DbHolder gotcha)
        return None


class _NoTags:
    def get(self, *a, **k):
        return []


class _Player:
    _n = 500

    def __init__(self, alliance=None, has_account=True, npc_type=None, sentinel=False):
        _Player._n += 1
        self.id = _Player._n
        self.key = "P"
        self.has_account = has_account
        self.db = _Db(player_alliance=alliance, npc_type=npc_type)
        self.tags = _SentinelTags() if sentinel else _NoTags()


class _SentinelTags:
    def get(self, key=None, category=None):
        return [key] if key == "sentinel" else []


class _FakeSystem:
    """Stands in for the AllianceSystem: knows which ids are live."""

    def __init__(self, live_ids):
        self._live = set(live_ids)

    def alliance_exists(self, aid):
        return aid in self._live


class _AreAlliedBase(unittest.TestCase):
    def setUp(self):
        # Patch get_system so are_allied resolves our fake AllianceSystem.
        self._orig = utils.get_system
        self._live_ids = {1}
        utils.get_system = lambda caller, name: (
            _FakeSystem(self._live_ids) if name == "alliance_system" else None
        )

    def tearDown(self):
        utils.get_system = self._orig


class TestAreAllied(_AreAlliedBase):
    def test_two_distinct_real_players_same_live_alliance(self):
        a = _Player(alliance=1)
        b = _Player(alliance=1)
        self.assertTrue(utils.are_allied(a, b))

    def test_same_player_is_not_allied_to_itself(self):
        a = _Player(alliance=1)
        self.assertFalse(utils.are_allied(a, a))

    def test_same_id_instances_not_allied(self):
        a = _Player(alliance=1)
        b = _Player(alliance=1)
        b.id = a.id  # idmapper-flush look-alike
        self.assertFalse(utils.are_allied(a, b))

    def test_different_alliance_not_allied(self):
        self._live_ids = {1, 2}
        a = _Player(alliance=1)
        b = _Player(alliance=2)
        self.assertFalse(utils.are_allied(a, b))

    def test_none_alliance_not_allied(self):
        a = _Player(alliance=None)
        b = _Player(alliance=1)
        self.assertFalse(utils.are_allied(a, b))

    def test_dead_alliance_id_not_allied(self):
        # Both point at alliance 7, which is NOT live.
        a = _Player(alliance=7)
        b = _Player(alliance=7)
        self.assertFalse(utils.are_allied(a, b))

    def test_non_account_holder_not_allied(self):
        a = _Player(alliance=1)
        b = _Player(alliance=1, has_account=False)
        self.assertFalse(utils.are_allied(a, b))

    def test_npc_holder_not_allied(self):
        a = _Player(alliance=1)
        b = _Player(alliance=1, npc_type="enemy")
        self.assertFalse(utils.are_allied(a, b))

    def test_sentinel_holder_not_allied(self):
        a = _Player(alliance=1)
        b = _Player(alliance=1, sentinel=True)
        self.assertFalse(utils.are_allied(a, b))

    def test_system_unavailable_not_allied(self):
        utils.get_system = lambda caller, name: None
        a = _Player(alliance=1)
        b = _Player(alliance=1)
        self.assertFalse(utils.are_allied(a, b))

    def test_none_args(self):
        self.assertFalse(utils.are_allied(None, _Player(alliance=1)))
        self.assertFalse(utils.are_allied(_Player(alliance=1), None))


if __name__ == "__main__":
    unittest.main()
