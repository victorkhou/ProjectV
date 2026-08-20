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


class TestBuildingAndSurveyDirectives(unittest.TestCase):
    """The Munitions Plant (construction_completed + MP) and Survey Array
    (outpost_surveyed) steps advance and reward exactly like the shipped
    building/command directives they mirror."""

    _CHAIN = [
        {"key": "build_mp", "description": "Build a Munitions Plant",
         "trigger_event": "construction_completed",
         "condition": {"building_type": "MP"},
         "reward": {"xp": 20, "Iron": 10}},
        {"key": "survey_outpost", "description": "Locate a base",
         "trigger_event": "outpost_surveyed",
         "reward": {"xp": 30, "Circuits": 10}},
    ]

    def setUp(self):
        from world import services
        self.bus = EventBus()
        self.system = DirectiveSystem(_Registry(list(self._CHAIN)), self.bus)
        self.rank_system = _RankSystem()
        ctx = services.override({
            "rank_system": self.rank_system,
            "directive_system": self.system,
        })
        ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)

    def test_building_mp_advances_and_rewards(self):
        p = _Player()
        self.bus.publish(CONSTRUCTION_COMPLETED, player=p,
                         building=_Building("MP"))
        self.assertEqual(p.db.directives_progress, 1)
        self.assertEqual(self.rank_system.awards[0][1], 20)
        self.assertEqual(p.resources.get("Iron"), 10)

    def test_building_the_wrong_plant_does_not_advance(self):
        p = _Player()
        self.bus.publish(CONSTRUCTION_COMPLETED, player=p,
                         building=_Building("LB"))  # Lab, not MP
        self.assertEqual(p.db.directives_progress, 0)

    def test_survey_step_advances_on_a_pinpoint_event(self):
        p = _Player()
        p.db.directives_progress = 1  # on the survey step
        self.bus.publish("outpost_surveyed", player=p, base_name="Outpost",
                         planet="terra", x=5, y=6)
        self.assertEqual(p.db.directives_progress, 2)
        self.assertEqual(self.rank_system.awards[0][1], 30)
        self.assertEqual(p.resources.get("Circuits"), 10)

    def test_survey_event_ignored_while_on_an_earlier_step(self):
        """A find before the survey step is reached must not skip ahead."""
        p = _Player()
        self.bus.publish("outpost_surveyed", player=p, base_name="Outpost",
                         planet="terra", x=5, y=6)
        self.assertEqual(p.db.directives_progress, 0)


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


# -------------------------------------------------------------- #
#  Shipped directives.yaml — real-data guards
# -------------------------------------------------------------- #

class TestShippedDirectiveChain(unittest.TestCase):
    """Guards over the REAL directives.yaml.

    The loader does NO validation beyond key/trigger_event presence, so a
    typo'd trigger_event or a condition naming a building that doesn't exist
    would load clean and simply never fire. These assertions are the only thing
    that catches that for the two new steps.
    """

    @classmethod
    def setUpClass(cls):
        import os
        from world.data_registry import DataRegistry
        data_dir = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "..", "data")
        )
        reg = DataRegistry()
        reg.load_all(data_dir)
        cls.registry = reg
        cls.directives = list(reg.directives)
        cls.by_key = {d["key"]: d for d in cls.directives}

    def test_new_directives_present(self):
        self.assertIn("build_munitions_plant", self.by_key)
        self.assertIn("survey_outpost", self.by_key)

    def test_munitions_plant_directive_targets_the_real_building(self):
        d = self.by_key["build_munitions_plant"]
        self.assertEqual(d["trigger_event"], "construction_completed")
        abbr = d["condition"]["building_type"]
        self.assertIsNotNone(
            self.registry.resolve_building(abbr),
            f"directive targets building {abbr!r} which does not exist",
        )

    def test_survey_directive_uses_a_real_event(self):
        from world.event_bus import ALL_EVENTS
        d = self.by_key["survey_outpost"]
        self.assertIn(
            d["trigger_event"], ALL_EVENTS,
            "survey directive triggers on an event the bus never publishes",
        )

    def test_every_directive_triggers_on_a_known_event(self):
        from world.event_bus import ALL_EVENTS
        for d in self.directives:
            self.assertIn(
                d["trigger_event"], ALL_EVENTS,
                f"directive {d['key']!r} triggers on unknown event "
                f"{d['trigger_event']!r}",
            )

    def test_survey_step_follows_the_munitions_plant_step(self):
        keys = [d["key"] for d in self.directives]
        self.assertLess(
            keys.index("build_munitions_plant"), keys.index("survey_outpost"),
            "build the bomb works before the find-a-base step",
        )

    def test_chain_does_not_force_an_outpost_kill(self):
        """A first outpost kill alone awards xp_hq_destroy (300) — enough to
        blow past the level-6 target — so onboarding must not require it."""
        self.assertNotIn(
            "base_eliminated",
            [d["trigger_event"] for d in self.directives],
            "onboarding must not gate completion behind destroying a base",
        )

    def test_finishing_the_chain_lands_around_level_six(self):
        """The whole point of the retune: a player who completes every
        directive — collecting each step's directive XP AND the economy/combat
        XP the underlying action grants — should be about level 6.

        This models the guided path against the REAL balance XP values and the
        REAL level curve, so a future edit to either a directive reward or an
        economy-XP knob that pushed a completer out of the level-6 band fails
        here instead of silently shipping.
        """
        from world import progression
        from world.adapters.registry_definitions_provider import default_balance

        progression.build_thresholds(self.registry.ranks)
        bal = self.registry.balance

        # Action XP the guided path necessarily earns, keyed by directive.
        # Building a type fires xp_build_complete; upgrading fires
        # xp_upgrade_complete; training an agent fires xp_agent_trained. The
        # remaining steps (assign / equip / patrol / survey) grant no economy XP.
        build = int(getattr(bal, "xp_build_complete", 0))
        upgrade = int(getattr(bal, "xp_upgrade_complete", 0))
        agent = int(getattr(bal, "xp_agent_trained", 0))
        action_by_key = {
            "build_hq": build, "build_extractor": build, "build_wall": build,
            "build_munitions_plant": build,
            "train_agent": agent, "upgrade_hq": upgrade,
        }

        total = 0
        for d in self.directives:
            total += int((d.get("reward") or {}).get("xp", 0))
            total += action_by_key.get(d["key"], 0)

        level = progression.level_for_xp(total)
        self.assertEqual(
            level, 6,
            f"guided path totals {total} XP -> level {level}; "
            f"expected ~level 6 (band "
            f"{progression.xp_for_level(6)}-{progression.xp_for_level(7) - 1})",
        )


if __name__ == "__main__":
    unittest.main()
