"""
Integration tests for Agent Progression — owner-level-change wiring.

End-to-end tests that exercise the full wiring path: an EventBus with
``LEVEL_CHANGED`` published, a subscriber calling
``AgentSystem.on_owner_level_changed`` (mirroring ``game_init``'s wiring),
which re-evaluates owned agents' gated abilities.

Validates: Requirements 15.1, 15.2, 15.3, 15.4
"""

from mygame.conftest import _ensure_evennia_stubs
_ensure_evennia_stubs()

import unittest

from mygame.world.data_registry import DataRegistry
from mygame.world.definitions import RankDef, AbilityGateDef
from mygame.world.event_bus import EventBus, LEVEL_CHANGED
from mygame.world.constants import DeliveryState
from mygame.world.systems.agent_system import AgentSystem

# Reuse the established fakes from the AgentSystem unit-test module.
from mygame.world.systems.tests.test_agent_system import (
    NotifyingPlayer,
    CappedScriptedAgent,
)


class _SettableAgentRepo:
    """Test AgentRepository whose owner lookup delegates to a settable fn.

    Replaces the old ``system._get_agents_fallback = <closure>`` seam: assign
    ``repo.resolve = lambda player: [...]`` (or construct with it) and inject
    the repo into ``AgentSystem``. ``find_all_agents`` is derived from the
    resolver so per-tick sweeps see the same roster.
    """

    def __init__(self, resolve=None):
        self.resolve = resolve or (lambda player: [])

    def find_agents_for_owner(self, owner):
        return self.resolve(owner)

    def find_all_agents(self):
        # No single owner in scope; the progression flows drive per-owner
        # queries, so an empty sweep is correct for these tests.
        return []

    def find_all_enemies(self):
        return []

    def find_training_buildings(self):
        return []


# ------------------------------------------------------------------ #
#  Owner-level-change flow (Req 15.1, 15.2, 15.3, 15.4)
# ------------------------------------------------------------------ #

# Gate at level 21 (first level of rank 5), matching the unit-test fixtures.
DELIVERY_GATE_LEVEL = 21


class TestOwnerLevelChangeFlow(unittest.TestCase):
    """Drive gate re-evaluation end-to-end through the EventBus.

    Wires ``LEVEL_CHANGED`` → ``AgentSystem.on_owner_level_changed`` exactly as
    ``server/conf/game_init.py`` does, then publishes level changes and asserts
    the owner notifications and behavior-script attach/detach transitions.
    """

    def setUp(self):
        # Registry with a single delivery gate at level 21.
        self.registry = DataRegistry()
        self.registry.ranks = [
            RankDef(name="Recruit", level=1, xp_threshold=0, agent_cap=2),
        ]
        self.registry.ability_gates = {
            "delivery": AbilityGateDef(
                key="delivery", required_level=DELIVERY_GATE_LEVEL
            ),
        }

        self.event_bus = EventBus()
        self._repo = _SettableAgentRepo()
        self.system = AgentSystem(
            registry=self.registry,
            event_bus=self.event_bus,
            create_npc_func=lambda player, agent_id: None,
            agent_repository=self._repo,
        )
        # Ability notifications flow as PLAYER_NOTIFICATION events; attach the
        # real presenter so owner.messages captures the rendered strings.
        from mygame.world.presenters.test_support import attach_presenter
        attach_presenter(self.event_bus)

        # Owner with one owned agent. Raw level is well above the gate so the
        # owner cap (Effective_Level = min(raw, owner_level - 1)) is the only
        # thing keeping it below the gate.
        self.owner = NotifyingPlayer()
        self.owner.db.level = 5  # ceiling 4 → effective capped to 4 (< 21)
        self.agent = CappedScriptedAgent(
            42,
            owner=self.owner,
            raw_level=25,
            script_keys=["HarvesterScript"],  # harvester pre-attached
        )

        # Make get_agents resolve to our single owned agent.
        self._repo.resolve = lambda player: (
            [self.agent] if player is self.owner else []
        )

        # Wire the subscription exactly like game_init.py.
        self.event_bus.subscribe(
            LEVEL_CHANGED,
            lambda **kw: self.system.on_owner_level_changed(
                kw["player"], kw["old_level"], kw["new_level"]
            ),
        )

    def _script_keys(self):
        return [s.key for s in self.agent.scripts.all()]

    def _publish_level_change(self, old_level, new_level):
        self.owner.db.level = new_level
        self.event_bus.publish(
            LEVEL_CHANGED,
            player=self.owner,
            old_level=old_level,
            new_level=new_level,
        )

    def test_owner_level_change_flow_end_to_end(self):
        """Cross gate (available, not enabled) → enable+attach → drop → detach."""
        # Sanity: agent starts below the gate with only HarvesterScript.
        self.assertEqual(self._script_keys(), ["HarvesterScript"])
        self.assertNotIn("delivery_behavior", self._script_keys())

        # --- Step 1: raise owner level so the agent's effective level crosses
        # the gate while delivery is NOT enabled (Req 15.1, 15.2). ---
        self._publish_level_change(old_level=5, new_level=30)  # ceiling 29

        # Delivery is now available but must NOT auto-attach; the owner is told
        # how to enable it.
        self.assertNotIn("delivery_behavior", self._script_keys())
        self.assertIn("HarvesterScript", self._script_keys())
        available_msgs = [m for m in self.owner.messages if "available" in m]
        self.assertEqual(len(available_msgs), 1)
        self.assertIn("agent ability 42 delivery on", available_msgs[0])

        # --- Step 2: player enables delivery, then a re-evaluation (a fresh
        # LEVEL_CHANGED publication) attaches the script (Req 15.3). ---
        self.agent.db.enabled_abilities = ["delivery"]
        self.owner.messages.clear()
        self._publish_level_change(old_level=30, new_level=30)

        self.assertIn("delivery_behavior", self._script_keys())
        self.assertIn("HarvesterScript", self._script_keys())
        self.assertEqual(self.agent.db.delivery_state, DeliveryState.IDLE)
        self.assertTrue(any("now active" in m for m in self.owner.messages))

        # --- Step 3: drop owner level below the gate → detach delivery, keep
        # HarvesterScript, notify re-lock (Req 15.4). ---
        self.owner.messages.clear()
        self._publish_level_change(old_level=30, new_level=5)  # ceiling 4

        self.assertNotIn("delivery_behavior", self._script_keys())
        self.assertIn("HarvesterScript", self._script_keys())
        self.assertTrue(any("re-locked" in m for m in self.owner.messages))
        # Enabled flag is retained so the ability re-attaches if it re-qualifies.
        self.assertIn("delivery", self.agent.db.enabled_abilities)


if __name__ == "__main__":
    unittest.main()


# ================================================================== #
#  Task 15.1 — Integration tests for the wired flows.
#
#  These exercise the end-to-end wiring that ties owner-capped agent
#  progression, gated abilities, reserve/restore, reassignment, and the
#  RankSystem promotion event together. They reuse the established
#  Evennia-stub bootstrap and the fakes from the AgentSystem unit-test
#  module (RealAgent / FakeAgent / NotifyingPlayer / CappedScriptedAgent).
#
#  Covered flows:
#    1. Freeze-then-resume award across an owner level-up
#       (Req 5.9, 5.10, 14.8)
#    2. Reserve/restore preserves progression + enabled set
#       (Req 10.1, 10.2)
#    3. Reassign attaches delivery iff effective >= gate AND enabled
#       (Req 10.4)
#    4. RankSystem promotion still fires RANK_PROMOTED + reserve handling
#       (Req 4.3)
# ================================================================== #

from world import progression  # noqa: E402
from mygame.world.event_bus import RANK_PROMOTED, RANK_DEMOTED  # noqa: E402
from mygame.world.systems.rank_system import (  # noqa: E402
    RankSystem,
    rank_from_level,
)
from mygame.typeclasses.combat_entity import CombatEntity  # noqa: E402

# Additional fakes reused from the AgentSystem unit-test module.
from mygame.world.systems.tests.test_agent_system import (  # noqa: E402
    FakeAgent,
    FakePlayer,
    RealAgent,
)


#: A complete 12-rank curve so ``progression`` derives a well-defined level
#: for every level in ``1..MAX_LEVEL`` (a partial table leaves high levels at
#: threshold 0 and confuses ``level_for_xp``). Mirrors the tuning used by the
#: property-test registry.
_FULL_RANKS = [
    RankDef(name="Recruit", level=1, xp_threshold=0, agent_cap=2),
    RankDef(name="Private", level=2, xp_threshold=200, agent_cap=3),
    RankDef(name="Corporal", level=3, xp_threshold=600, agent_cap=4),
    RankDef(name="Sergeant", level=4, xp_threshold=1500, agent_cap=6),
    RankDef(name="Staff_Sergeant", level=5, xp_threshold=3500, agent_cap=8),
    RankDef(name="Lieutenant", level=6, xp_threshold=7000, agent_cap=10),
    RankDef(name="Captain", level=7, xp_threshold=12000, agent_cap=12),
    RankDef(name="Major", level=8, xp_threshold=20000, agent_cap=14),
    RankDef(name="Colonel", level=9, xp_threshold=35000, agent_cap=16),
    RankDef(name="Brigadier", level=10, xp_threshold=55000, agent_cap=17),
    RankDef(name="General", level=11, xp_threshold=80000, agent_cap=19),
    RankDef(name="Marshal", level=12, xp_threshold=120000, agent_cap=20),
]


def _make_full_registry():
    """DataRegistry with the full 12-rank curve and a delivery gate at 21."""
    registry = DataRegistry()
    registry.ranks = list(_FULL_RANKS)
    registry.ability_gates = {
        "delivery": AbilityGateDef(key="delivery", required_level=21),
    }
    return registry


def _make_system(registry, event_bus=None, agents=None):
    """Build an AgentSystem whose roster query resolves to *agents*."""
    bus = event_bus or EventBus()
    roster = agents if agents is not None else []
    repo = _SettableAgentRepo(
        resolve=lambda player: [
            a for a in roster
            if getattr(getattr(a, "db", None), "owner", None) is player
        ]
    )
    system = AgentSystem(
        registry=registry,
        event_bus=bus,
        create_npc_func=lambda player, agent_id: None,
        agent_repository=repo,
    )
    # Ability notifications are emitted as PLAYER_NOTIFICATION events; attach
    # the real presenter so tests capturing owner.messages see the strings.
    from mygame.world.presenters.test_support import attach_presenter
    attach_presenter(bus)
    return system, bus


# ------------------------------------------------------------------ #
#  Flow 1 — Freeze-then-resume award across an owner level-up.
#  Req 5.9 (frozen at ceiling), 5.10 / 14.8 (resume with no banked surplus)
# ------------------------------------------------------------------ #

class TestFreezeResumeAcrossOwnerLevelUp(unittest.TestCase):
    """An agent frozen at its cap ceiling earns nothing until the owner
    levels up, at which point ``award_agent_xp`` resumes adding exactly the
    configured amount with no banked surplus."""

    def setUp(self):
        self.registry = _make_full_registry()
        self.system, self.event_bus = _make_system(self.registry)
        # Force this registry's level->XP curve active (the table is a
        # process-global shared with CombatEntity).
        progression.build_thresholds(self.registry.ranks)

    def test_freeze_then_resume(self):
        # Owner level 2 → cap ceiling = max(1, 2) = 2 (R3.1: cap == owner level).
        owner = FakePlayer()
        owner.db.level = 2
        agent = RealAgent(owner=owner)

        # Seed the agent's raw level to exactly the ceiling (level 2) via the
        # real CombatEntity curve so the freeze precondition holds.
        seed_xp = progression.xp_for_level(2)
        agent.award_xp(seed_xp)
        self.assertEqual(agent.db.level, 2)
        self.assertEqual(self.system.get_cap_ceiling(agent), 2)

        # --- Frozen at the ceiling: award_agent_xp is a complete no-op. ---
        pre_xp = agent.db.combat_xp
        pre_level = agent.db.level
        pre_rank = agent.db.rank_level
        self.system.award_agent_xp(agent, "harvest")
        self.assertEqual(agent.db.combat_xp, pre_xp)
        self.assertEqual(agent.db.level, pre_level)
        self.assertEqual(agent.db.rank_level, pre_rank)

        # --- Owner levels up → ceiling rises well above the agent. ---
        owner.db.level = 30  # ceiling = 30 (R3.1)
        self.assertEqual(self.system.get_cap_ceiling(agent), 30)

        # The next award resumes and adds EXACTLY the configured harvest
        # amount — no banked surplus from the frozen period.
        harvest = self.registry.balance.agent_xp_harvest
        self.assertGreater(harvest, 0)
        self.system.award_agent_xp(agent, "harvest")
        self.assertEqual(agent.db.combat_xp, pre_xp + harvest)

        # A second award adds exactly the amount again (linear, no catch-up).
        self.system.award_agent_xp(agent, "harvest")
        self.assertEqual(agent.db.combat_xp, pre_xp + 2 * harvest)


# ------------------------------------------------------------------ #
#  Flow 2 — Reserve/restore preserves progression + enabled set.
#  Req 10.1, 10.2
# ------------------------------------------------------------------ #

class TestReserveRestorePreservesProgression(unittest.TestCase):
    """Toggling reserve (via demotion/promotion handling) must never mutate
    an agent's combat_xp / level / rank_level or its enabled-ability set."""

    def setUp(self):
        self.registry = _make_full_registry()
        self.owner = FakePlayer()
        self.owner.db.level = 30
        # One owned agent with a fixed progression snapshot + enabled set.
        self.agent = FakeAgent(agent_id=1, owner=self.owner)
        self.agent.db.combat_xp = 500
        self.agent.db.level = 10
        self.agent.db.rank_level = 2
        self.agent.db.enabled_abilities = ["delivery"]
        self.system, self.event_bus = _make_system(
            self.registry, agents=[self.agent]
        )

    def test_reserve_then_restore_is_lossless(self):
        before = {
            "combat_xp": self.agent.db.combat_xp,
            "level": self.agent.db.level,
            "rank_level": self.agent.db.rank_level,
            "enabled": self.system.get_enabled_abilities(self.agent),
        }
        self.assertEqual(before["enabled"], {"delivery"})

        # Reserve the agent: demote to a cap that leaves it no active slot.
        self.system.handle_demotion(self.owner, new_agent_cap=1)
        self.assertTrue(self.agent.db.reserve)
        # Progression + enabled set untouched while reserved.
        self.assertEqual(self.agent.db.combat_xp, before["combat_xp"])
        self.assertEqual(self.agent.db.level, before["level"])
        self.assertEqual(self.agent.db.rank_level, before["rank_level"])
        self.assertEqual(
            self.system.get_enabled_abilities(self.agent), before["enabled"]
        )

        # Restore the agent: promote so the slot reopens.
        self.system.handle_promotion(self.owner, new_agent_cap=3)
        self.assertFalse(self.agent.db.reserve)
        # Still untouched after the round trip.
        self.assertEqual(self.agent.db.combat_xp, before["combat_xp"])
        self.assertEqual(self.agent.db.level, before["level"])
        self.assertEqual(self.agent.db.rank_level, before["rank_level"])
        self.assertEqual(
            self.system.get_enabled_abilities(self.agent), before["enabled"]
        )


# ------------------------------------------------------------------ #
#  Flow 3 — Reassign attaches delivery iff effective >= gate AND enabled.
#  Req 10.4 (covers the reserve-restore-then-reassign path)
# ------------------------------------------------------------------ #

class TestReassignAttachesDeliveryGated(unittest.TestCase):
    """``_attach_behavior_script(agent, 'harvester')`` always attaches the
    harvester base script and attaches ``delivery_behavior`` if and only if
    the agent's effective level meets the gate AND delivery is enabled —
    including after a reserve/restore round trip."""

    def setUp(self):
        self.registry = _make_full_registry()
        self.system, self.event_bus = _make_system(self.registry)

    def _make_agent(self, owner_level, raw_level, enabled):
        owner = NotifyingPlayer()
        owner.db.level = owner_level
        agent = CappedScriptedAgent(
            agent_id=1, owner=owner, raw_level=raw_level, script_keys=[]
        )
        if enabled:
            agent.db.enabled_abilities = ["delivery"]
        return owner, agent

    def _keys(self, agent):
        return [s.key for s in agent.scripts.all()]

    def test_attaches_when_effective_at_gate_and_enabled(self):
        # owner 30 → ceiling 29; raw 25 → effective 25 >= 21. enabled.
        owner, agent = self._make_agent(30, 25, enabled=True)

        self.system._attach_behavior_script(agent, "harvester")

        keys = self._keys(agent)
        self.assertIn("delivery_behavior", keys)
        self.assertEqual(agent.db.delivery_state, DeliveryState.IDLE)

    def test_no_attach_when_at_gate_but_not_enabled(self):
        # effective 25 >= 21 but delivery NOT enabled → production only.
        owner, agent = self._make_agent(30, 25, enabled=False)

        self.system._attach_behavior_script(agent, "harvester")

        self.assertNotIn("delivery_behavior", self._keys(agent))
        # The owner is told the ability is available.
        self.assertTrue(any("available" in m for m in owner.messages))

    def test_no_attach_when_below_gate_even_if_enabled(self):
        # owner 10 → ceiling 9; raw 25 → effective capped to 9 < 21.
        owner, agent = self._make_agent(10, 25, enabled=True)

        self.system._attach_behavior_script(agent, "harvester")

        self.assertNotIn("delivery_behavior", self._keys(agent))

    def test_reserve_restore_then_reassign_attaches_delivery(self):
        """Full reserve → restore → reassign path attaches delivery when it
        qualifies (effective >= gate AND enabled)."""
        owner, agent = self._make_agent(30, 25, enabled=True)
        # Wire the roster so demotion/promotion can find the agent.
        self.system._repo.resolve = lambda p: (
            [agent] if p is owner else []
        )

        # Reserve then restore — progression/enabled survive (Flow 2 covers
        # the assertions; here we only need the agent active again).
        self.system.handle_demotion(owner, new_agent_cap=1)
        self.assertTrue(agent.db.reserve)
        self.system.handle_promotion(owner, new_agent_cap=3)
        self.assertFalse(agent.db.reserve)

        # Reassign as harvester → delivery attaches because it qualifies.
        self.system._attach_behavior_script(agent, "harvester")
        self.assertIn("delivery_behavior", self._keys(agent))


# ------------------------------------------------------------------ #
#  Flow 4 — RankSystem promotion fires RANK_PROMOTED + reserve handling.
#  Req 4.3
# ------------------------------------------------------------------ #

class _RankPlayerDB:
    """Minimal db handler for a CombatEntity-backed commander."""

    def __init__(self, combat_xp=0, level=1, rank_level=1):
        self.combat_xp = combat_xp
        self.level = level
        self.rank_level = rank_level
        self.researched_techs = set()

    def __getattr__(self, name):
        # Sensible default for any unset attribute the systems may probe.
        return None


class RankPlayer(CombatEntity):
    """Commander that mixes in the real CombatEntity so ``award_xp`` drives
    the shared progression curve, exactly as a live player does."""

    def __init__(self, combat_xp=0, level=1):
        self.id = 99
        self.key = "Commander"
        self.db = _RankPlayerDB(
            combat_xp=combat_xp, level=level, rank_level=rank_from_level(level)
        )
        self.messages: list[str] = []

    def msg(self, text, **kwargs):
        self.messages.append(text)


class TestRankPromotionFiresEventAndReserveHandling(unittest.TestCase):
    """Crossing a rank boundary publishes ``RANK_PROMOTED`` (with the new
    agent cap) and the wired ``handle_promotion`` subscriber restores a
    reserved agent when the cap increases — mirroring ``game_init``."""

    def setUp(self):
        # Rank curve with increasing agent caps so a promotion widens the cap.
        self.registry = DataRegistry()
        self.registry.ranks = [
            RankDef(name="Recruit", level=1, xp_threshold=0, agent_cap=2),
            RankDef(name="Private", level=2, xp_threshold=100, agent_cap=3),
            RankDef(name="Corporal", level=3, xp_threshold=300, agent_cap=4),
            RankDef(name="Sergeant", level=4, xp_threshold=600, agent_cap=6),
            RankDef(name="Captain", level=5, xp_threshold=1000, agent_cap=8),
        ]
        self.registry.technologies = {}
        self.registry.powerups = {}

        self.event_bus = EventBus()
        self.rank_system = RankSystem(
            registry=self.registry, event_bus=self.event_bus
        )
        # Force this registry's curve active (the threshold table is global).
        self.rank_system._rebuild_thresholds()

        self.player = RankPlayer(combat_xp=0, level=1)
        # Two owned agents (IDs 1, 2).
        self.agents = [
            FakeAgent(agent_id=1, owner=self.player),
            FakeAgent(agent_id=2, owner=self.player),
        ]
        self.agent_system = AgentSystem(
            registry=self.registry,
            event_bus=self.event_bus,
            create_npc_func=lambda player, agent_id: None,
            agent_repository=_SettableAgentRepo(
                resolve=lambda p: list(self.agents) if p is self.player else []
            ),
        )

        # Wire reserve/restore subscriptions exactly like game_init.py.
        self.event_bus.subscribe(
            RANK_DEMOTED,
            lambda **kw: self.agent_system.handle_demotion(
                kw.get("player"), kw.get("new_agent_cap", 2)
            ),
        )
        self.event_bus.subscribe(
            RANK_PROMOTED,
            lambda **kw: self.agent_system.handle_promotion(
                kw.get("player"), kw.get("new_agent_cap", 2)
            ),
        )

        # Capture the promotion events for assertions.
        self.promotions: list[dict] = []
        self.event_bus.subscribe(
            RANK_PROMOTED, lambda **kw: self.promotions.append(kw)
        )

    def test_promotion_event_and_reserve_restore(self):
        # Start with agent #2 reserved (Recruit cap=2 leaves a single slot).
        self.agent_system.handle_demotion(self.player, new_agent_cap=2)
        reserved = [a for a in self.agents if a.db.reserve]
        self.assertEqual([a.db.agent_id for a in reserved], [2])

        # Award enough XP to cross from Recruit into Private (level 1 → 6).
        self.rank_system.award_xp(self.player, 100, "kill")

        # The player actually promoted a rank.
        self.assertEqual(self.player.db.rank_level, 2)  # Private

        # RANK_PROMOTED fired exactly once with the new (wider) agent cap.
        self.assertEqual(len(self.promotions), 1)
        self.assertEqual(self.promotions[0]["new_agent_cap"], 3)
        self.assertEqual(self.promotions[0]["new_rank"].name, "Private")

        # Reserve handling ran: the reserved agent was restored when the cap
        # increased (cap 3 → 2 agent slots, both agents now active).
        self.assertFalse(
            any(a.db.reserve for a in self.agents),
            "reserved agent should have been restored on promotion",
        )
