"""
Unit tests for the DirectiveSystem (early-game rebalance R9, R10).

Covers: directive advancement on matching events, condition filtering, one-time
rewards, the payload adapter (attacker → owner resolution, non-player discard,
D7), dismiss-all semantics (muted advance is silent + rewardless, D2), deed
awards from BASE_ELIMINATED (counted, D9), and the deed gate in
BuildingSystem._validate_construction.
"""

import types
import unittest

from world.event_bus import (
    BASE_ELIMINATED,
    CONSTRUCTION_COMPLETED,
    PATROL_SET,
    EventBus,
)
from world.systems.directive_system import DirectiveSystem


# -------------------------------------------------------------- #
#  Fakes
# -------------------------------------------------------------- #

class _Db(types.SimpleNamespace):
    def __getattr__(self, _):
        return None


class _Player:
    _n = 0

    def __init__(self):
        _Player._n += 1
        self.id = _Player._n
        self.key = f"P{self.id}"
        self.db = _Db(
            combat_xp=0, npc_type=None,
            deeds={}, directives_progress=0, directives_muted=False,
        )
        self.resources = {}
        self.messages = []

    def add_resource(self, resource, amount):
        self.resources[resource] = self.resources.get(resource, 0) + amount

    def msg(self, text, **kw):
        self.messages.append(text)


class _Npc:
    """An agent/turret-like actor with an owner."""

    def __init__(self, owner):
        self.id = 999
        self.db = _Db(npc_type="agent", owner=owner, combat_xp=0)


class _RankSystem:
    def __init__(self):
        self.awards = []

    def award_xp(self, player, amount, reason=""):
        self.awards.append((player, amount, reason))
        player.db.combat_xp = (player.db.combat_xp or 0) + amount


class _Registry:
    def __init__(self, directives, base_templates=None):
        self.directives = directives
        self._base_templates = base_templates or {}

    def get_base_template(self, tier):
        return self._base_templates.get(tier)


_CHAIN = [
    {"key": "build_hq", "description": "Build your Headquarters",
     "trigger_event": "construction_completed",
     "condition": {"building_type": "HQ"},
     "reward": {"xp": 15}},
    {"key": "guard_patrol", "description": "Set a guard patrol",
     "trigger_event": "patrol_set",
     "condition": {"role": "guard"},
     "reward": {"xp": 20, "Iron": 5}},
    {"key": "destroy_outpost", "description": "Destroy an NPC outpost",
     "trigger_event": "base_eliminated",
     "player_key": "attacker",
     "condition": {"base_kind": "outpost"},
     "reward": {"xp": 50}},
]


class _Building:
    def __init__(self, btype="HQ"):
        self.db = _Db(building_type=btype)


class DirectiveTestBase(unittest.TestCase):
    def setUp(self):
        from world import services

        self.bus = EventBus()
        self.registry = _Registry(list(_CHAIN))
        self.system = DirectiveSystem(self.registry, self.bus)
        self.rank_system = _RankSystem()
        # Install through the facade so _grant_reward's get_system finds them.
        ctx = services.override({
            "rank_system": self.rank_system,
            "directive_system": self.system,
        })
        ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)


# -------------------------------------------------------------- #
#  Directive advancement
# -------------------------------------------------------------- #

class TestDirectiveAdvance(DirectiveTestBase):
    def test_matching_event_advances_and_rewards(self):
        p = _Player()
        self.bus.publish(CONSTRUCTION_COMPLETED, player=p,
                         building=_Building("HQ"))
        self.assertEqual(p.db.directives_progress, 1)
        self.assertEqual(self.rank_system.awards[0][1], 15)

    def test_condition_mismatch_does_not_advance(self):
        p = _Player()
        self.bus.publish(CONSTRUCTION_COMPLETED, player=p,
                         building=_Building("EX"))  # not HQ
        self.assertEqual(p.db.directives_progress, 0)
        self.assertEqual(self.rank_system.awards, [])

    def test_wrong_step_event_does_not_advance(self):
        """An event matching step 2 while the player is on step 1 is ignored."""
        p = _Player()
        self.bus.publish(PATROL_SET, player=p, agent_id=1, role="guard")
        self.assertEqual(p.db.directives_progress, 0)

    def test_full_chain_in_order(self):
        p = _Player()
        self.bus.publish(CONSTRUCTION_COMPLETED, player=p,
                         building=_Building("HQ"))
        self.bus.publish(PATROL_SET, player=p, agent_id=1, role="guard")
        self.bus.publish(BASE_ELIMINATED, attacker=p, tier="outpost",
                         sentinel=None, planet="terra", x=0, y=0)
        self.assertEqual(p.db.directives_progress, 3)
        # XP: 15 + 20 + 50; Iron: 5 from step 2.
        self.assertEqual(sum(a[1] for a in self.rank_system.awards), 85)
        self.assertEqual(p.resources.get("Iron"), 5)

    def test_reward_is_one_time(self):
        p = _Player()
        self.bus.publish(CONSTRUCTION_COMPLETED, player=p,
                         building=_Building("HQ"))
        self.bus.publish(CONSTRUCTION_COMPLETED, player=p,
                         building=_Building("HQ"))  # repeat
        self.assertEqual(p.db.directives_progress, 1)
        self.assertEqual(len(self.rank_system.awards), 1)


# -------------------------------------------------------------- #
#  Payload adapter (D7)
# -------------------------------------------------------------- #

class TestPayloadAdapter(DirectiveTestBase):
    def test_npc_actor_resolves_to_owner(self):
        """An agent/turret actor credits its owning player."""
        p = _Player()
        p.db.directives_progress = 2  # on destroy_outpost
        npc = _Npc(owner=p)
        self.bus.publish(BASE_ELIMINATED, attacker=npc, tier="outpost",
                         sentinel=None, planet="terra", x=0, y=0)
        self.assertEqual(p.db.directives_progress, 3)

    def test_non_player_actor_discarded(self):
        """An ownerless NPC actor is discarded without side effects."""
        npc = _Npc(owner=None)
        # Must not raise; no directive holder exists.
        self.bus.publish(BASE_ELIMINATED, attacker=npc, tier="outpost",
                         sentinel=None, planet="terra", x=0, y=0)
        self.assertEqual(self.rank_system.awards, [])

    def test_missing_payload_key_discarded(self):
        self.bus.publish(BASE_ELIMINATED, tier="outpost",
                         sentinel=None, planet="terra", x=0, y=0)
        self.assertEqual(self.rank_system.awards, [])


# -------------------------------------------------------------- #
#  Dismiss-all (D2)
# -------------------------------------------------------------- #

class TestDismissAll(DirectiveTestBase):
    def test_muted_advances_silently_without_reward(self):
        p = _Player()
        DirectiveSystem.set_muted(p, True)
        self.bus.publish(CONSTRUCTION_COMPLETED, player=p,
                         building=_Building("HQ"))
        self.assertEqual(p.db.directives_progress, 1)  # advanced
        self.assertEqual(self.rank_system.awards, [])  # no reward

    def test_unmute_resumes_without_back_pay(self):
        p = _Player()
        DirectiveSystem.set_muted(p, True)
        self.bus.publish(CONSTRUCTION_COMPLETED, player=p,
                         building=_Building("HQ"))  # step 1 muted, forfeited
        DirectiveSystem.set_muted(p, False)
        self.bus.publish(PATROL_SET, player=p, agent_id=1, role="guard")
        self.assertEqual(p.db.directives_progress, 2)
        # Only step 2's reward was paid.
        self.assertEqual(len(self.rank_system.awards), 1)
        self.assertEqual(self.rank_system.awards[0][1], 20)


# -------------------------------------------------------------- #
#  Deeds (R9/D9)
# -------------------------------------------------------------- #

class TestDeedAwards(DirectiveTestBase):
    def test_outpost_deed_incremented(self):
        p = _Player()
        for _ in range(3):
            self.bus.publish(BASE_ELIMINATED, attacker=p, tier="outpost",
                             sentinel=None, planet="terra", x=0, y=0)
        self.assertEqual(p.db.deeds.get("outpost_cleared"), 3)

    def test_fortress_deed_recorded(self):
        p = _Player()
        self.bus.publish(BASE_ELIMINATED, attacker=p, tier="fortress",
                         sentinel=None, planet="terra", x=0, y=0)
        self.assertEqual(p.db.deeds.get("fortress_cleared"), 1)

    def test_npc_kill_credits_owner_deed(self):
        p = _Player()
        npc = _Npc(owner=p)
        self.bus.publish(BASE_ELIMINATED, attacker=npc, tier="outpost",
                         sentinel=None, planet="terra", x=0, y=0)
        self.assertEqual(p.db.deeds.get("outpost_cleared"), 1)

    def test_new_tier_deed_maps_by_difficulty_class(self):
        """A difficulty tier awards the deed for its CLASS, not its tier key: a
        'stronghold' (outpost-class) → outpost_cleared, a 'citadel'
        (fortress-class) → fortress_cleared. Resolved via the template's
        difficulty_class in the registry."""
        import types as _t
        self.registry._base_templates = {
            "stronghold": _t.SimpleNamespace(difficulty_class="outpost"),
            "citadel": _t.SimpleNamespace(difficulty_class="fortress"),
        }
        p = _Player()
        self.bus.publish(BASE_ELIMINATED, attacker=p, tier="stronghold",
                         sentinel=None, planet="terra", x=0, y=0)
        self.bus.publish(BASE_ELIMINATED, attacker=p, tier="citadel",
                         sentinel=None, planet="terra", x=0, y=0)
        self.assertEqual(p.db.deeds.get("outpost_cleared"), 1)
        self.assertEqual(p.db.deeds.get("fortress_cleared"), 1)


# -------------------------------------------------------------- #
#  Deed gate in BuildingSystem (R9.2, R9.4)
# -------------------------------------------------------------- #

class TestDeedGate(unittest.TestCase):
    def _system(self):
        from world.systems.building_system import BuildingSystem
        from world.data_registry import DataRegistry
        registry = DataRegistry()
        return BuildingSystem(registry, EventBus())

    def _bdef(self, deed=None, count=1):
        return types.SimpleNamespace(
            name="Lab", unlock_deed=deed, unlock_deed_count=count,
        )

    def test_no_deed_gate_passes(self):
        sys_ = self._system()
        p = _Player()
        self.assertIsNone(
            sys_._validate_deed_requirement(p, self._bdef(deed=None)))

    def test_missing_deed_refused_with_requires_message(self):
        sys_ = self._system()
        p = _Player()
        err = sys_._validate_deed_requirement(
            p, self._bdef(deed="outpost_cleared"))
        self.assertIsNotNone(err)
        self.assertIn("Requires", err)

    def test_counted_gate_below_count_refused(self):
        sys_ = self._system()
        p = _Player()
        p.db.deeds = {"outpost_cleared": 2}
        err = sys_._validate_deed_requirement(
            p, self._bdef(deed="outpost_cleared", count=3))
        self.assertIsNotNone(err)
        self.assertIn("2/3", err)

    def test_counted_gate_at_count_passes(self):
        sys_ = self._system()
        p = _Player()
        p.db.deeds = {"outpost_cleared": 3}
        self.assertIsNone(sys_._validate_deed_requirement(
            p, self._bdef(deed="outpost_cleared", count=3)))

    def test_boolean_gate_at_one_passes(self):
        sys_ = self._system()
        p = _Player()
        p.db.deeds = {"outpost_cleared": 1}
        self.assertIsNone(sys_._validate_deed_requirement(
            p, self._bdef(deed="outpost_cleared", count=1)))


if __name__ == "__main__":
    unittest.main()
