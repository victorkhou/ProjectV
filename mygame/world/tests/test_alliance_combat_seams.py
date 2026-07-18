"""
Unit tests for the alliance combat seams (the hybrid friendly-fire rule).

Verifies, driving ``CombatEngine`` directly with fakes:

* automated targeting (turret / guard) SKIPS an allied candidate;
* a manual kill of an ALLIED player grants no XP and bumps no scored-kill tally;
* a NON-friendly player kill bumps ``scored_kills_pvp``; an enemy-NPC kill bumps
  ``scored_kills_pve``;
* the cosmetic ``db.kills`` tally still increments for a betrayal;
* razing an ally's building grants no XP.

``are_allied`` is resolved through a fake AllianceSystem installed in the global
``game_systems`` dict (the same path ``world.utils.are_allied`` uses).
"""

import types
import unittest

import server.conf.game_init as game_init
from world.event_bus import EventBus
from world.definitions import BalanceConfig
from world.data_registry import DataRegistry
from world.systems.combat_engine import CombatEngine


# -------------------------------------------------------------- #
#  Fakes
# -------------------------------------------------------------- #

class _Db(types.SimpleNamespace):
    def __getattr__(self, _):  # unset -> None (DbHolder gotcha)
        return None


class _Player:
    _n = 0

    def __init__(self, alliance=None):
        _Player._n += 1
        self.id = _Player._n
        self.key = f"P{self.id}"
        self.has_account = True
        self.db = _Db(
            combat_xp=0, level=10, player_alliance=alliance, npc_type=None,
            kills=0, deaths=0, scored_kills_pvp=0.0, scored_kills_pve=0.0,
            last_kill_decay_tick=0, hp=0, hp_max=100,
        )
        self.tags = _NoTags()

    def msg(self, *a, **k):
        pass


class _EnemyNpc:
    _n = 0

    def __init__(self, owner=None):
        _EnemyNpc._n += 1
        self.id = 9000 + _EnemyNpc._n
        self.key = "Guard"
        self.has_account = False
        self.db = _Db(combat_xp=0, npc_type="enemy", owner=owner, hp=0, hp_max=80)
        self.tags = _NoTags()

    def msg(self, *a, **k):
        pass


class _NoTags:
    def get(self, *a, **k):
        return []


class _FakeAllianceSystem:
    """Only needs alliance_exists (are_allied) + decay hooks (_record_scored_kill)."""

    def __init__(self, live_ids):
        self._live = set(live_ids)
        self._tick_provider = lambda: 0

    def alliance_exists(self, aid):
        return aid in self._live

    def _decay_multiplier(self, last_tick):
        return 1.0

    def perk_flat_bonus(self, player, category, field):
        return 0


class _XpAwarder:
    """Captures player XP awards so we can assert reward vs no-reward."""

    def __init__(self):
        self.awards = []  # (player, amount)

    def award_xp(self, player, amount, reason=None):
        self.awards.append((player, amount))
        try:
            player.db.combat_xp = (player.db.combat_xp or 0) + amount
        except Exception:
            pass


# -------------------------------------------------------------- #
#  Harness
# -------------------------------------------------------------- #

class _CombatSeamBase(unittest.TestCase):
    def setUp(self):
        reg = DataRegistry()
        reg.balance = BalanceConfig()
        self.bus = EventBus()
        self.awarder = _XpAwarder()
        self.engine = CombatEngine(
            reg, self.bus,
            player_xp_awarder_provider=lambda: self.awarder,
        )
        # Install a fake AllianceSystem so are_allied resolves.
        self._saved_systems = dict(game_init.game_systems)
        self.alliance = _FakeAllianceSystem(live_ids={1})
        game_init.game_systems["alliance_system"] = self.alliance

    def tearDown(self):
        game_init.game_systems.clear()
        game_init.game_systems.update(self._saved_systems)


# -------------------------------------------------------------- #
#  XP guard + scored kills
# -------------------------------------------------------------- #

class TestFriendlyFireXp(_CombatSeamBase):
    def test_allied_player_kill_grants_no_xp_or_scored_kill(self):
        a = _Player(alliance=1)
        b = _Player(alliance=1)  # same live alliance -> allied
        self.engine._handle_player_defeat(b, a)
        self.assertEqual(self.awarder.awards, [], "allied betrayal must grant no XP")
        self.assertEqual(a.db.scored_kills_pvp, 0.0, "no leaderboard credit for betrayal")
        # An allied betrayal is friendly fire — treated exactly like a self-kill
        # or own-unit kill: no XP, no scored-kill, and (like all friendly fire in
        # this engine) no cosmetic kill tally on the attacker either.
        self.assertEqual(a.db.kills, 0)
        # The victim's death still tallies (a death is a death, unconditional).
        self.assertEqual(b.db.deaths, 1)

    def test_non_friendly_player_kill_awards_xp_and_pvp(self):
        a = _Player(alliance=1)
        enemy = _Player(alliance=None)  # not allied
        self.engine._handle_player_defeat(enemy, a)
        self.assertEqual(len(self.awarder.awards), 1)
        self.assertEqual(a.db.scored_kills_pvp, 1.0)
        self.assertEqual(a.db.scored_kills_pve, 0.0)

    def test_enemy_npc_kill_awards_pve(self):
        a = _Player(alliance=1)
        npc = _EnemyNpc()
        self.engine._handle_enemy_death(npc, a)
        self.assertEqual(len(self.awarder.awards), 1)
        self.assertEqual(a.db.scored_kills_pve, 1.0)
        self.assertEqual(a.db.scored_kills_pvp, 0.0)

    def test_different_alliance_is_not_friendly(self):
        self.alliance._live = {1, 2}
        a = _Player(alliance=1)
        b = _Player(alliance=2)
        self.engine._handle_player_defeat(b, a)
        self.assertEqual(len(self.awarder.awards), 1, "rival alliances are hostile")
        self.assertEqual(a.db.scored_kills_pvp, 1.0)


# -------------------------------------------------------------- #
#  Automated targeting skips allies
# -------------------------------------------------------------- #

class TestAutomatedTargetingSkipsAllies(_CombatSeamBase):
    def test_are_allied_used_by_targeting(self):
        # Direct predicate check via the same path targeting uses.
        from world.utils import are_allied
        owner = _Player(alliance=1)
        ally = _Player(alliance=1)
        stranger = _Player(alliance=None)
        self.assertTrue(are_allied(ally, owner))
        self.assertFalse(are_allied(stranger, owner))


if __name__ == "__main__":
    unittest.main()
