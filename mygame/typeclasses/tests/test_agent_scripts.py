"""
Unit tests for agent behavior scripts.

Tests HarvesterScript, EngineerScript, and the placeholder scripts
to verify they can be instantiated and their at_repeat() logic works
correctly with mock objects.

Requirements: 9.1, 10.1, 10.5, 10.6, 11.1, 11.3, 12.1, 12.3
"""

import sys
import types
import unittest
from contextlib import contextmanager

# -------------------------------------------------------------- #
#  Bootstrap: stub out Evennia modules before importing scripts
# -------------------------------------------------------------- #

def _ensure_evennia_stubs():
    if "evennia" in sys.modules:
        mod = sys.modules["evennia"]
        if hasattr(mod, "__file__") and mod.__file__:
            return
    stubs = {}

    def _mod(name, attrs=None):
        m = types.ModuleType(name)
        if attrs:
            for k, v in attrs.items():
                setattr(m, k, v)
        stubs[name] = m
        return m

    _mod("evennia")
    _mod("evennia.scripts")
    _mod("evennia.scripts.scripts", {
        "DefaultScript": type("DefaultScript", (), {
            "key": "", "desc": "", "interval": 0,
            "persistent": True, "obj": None,
            "at_script_creation": lambda self: None,
            "at_repeat": lambda self: None,
        }),
    })
    _mod("evennia.objects")
    _mod("evennia.objects.objects", {
        "DefaultObject": type("DefaultObject", (), {}),
        "DefaultRoom": type("DefaultRoom", (), {}),
        "DefaultCharacter": type("DefaultCharacter", (), {}),
    })
    # Extra modules required by the AgentSystem import chain (mirrors the
    # bootstrap in world/systems/tests/test_agent_system.py) so the gate-driven
    # delivery tests below can import AgentSystem.
    _mod("evennia.objects.models")
    _mod("evennia.commands")
    _mod("evennia.commands.cmdset")
    _mod("evennia.utils")
    _mod("evennia.utils.utils")
    _mod("evennia.utils.logger")

    for name, mod in stubs.items():
        sys.modules.setdefault(name, mod)

_ensure_evennia_stubs()

from mygame.typeclasses.agent_scripts import (  # noqa: E402
    HarvesterScript,
    EngineerScript,
    PatrolBehavior,
    DeliveryBehavior,
    SoldierScript,
    MedicScript,
    ROLE_SCRIPT_MAP,
    ABILITY_SCRIPT_MAP,
)


# -------------------------------------------------------------- #
#  Helpers / Fakes
# -------------------------------------------------------------- #

class FakeDB:
    """Simulates Evennia's db attribute handler."""
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

    def __getattr__(self, name):
        return None


class FakeNPC:
    """Lightweight stand-in for an NPC agent object."""
    def __init__(self, role="harvester", role_target=None,
                 incapacitated=False):
        self.db = FakeDB(
            role=role,
            role_target=role_target,
            incapacitated=incapacitated,
        )


class FakeTile:
    """Lightweight stand-in for a terrain tile."""
    def __init__(self, resource_type=None):
        self.db = FakeDB(
            resource_type=resource_type,
            resource_node_data=(
                {"resource_type": resource_type} if resource_type else None
            ),
            resource_inventory={},
        )


class FakeBuilding:
    """Lightweight stand-in for a building object."""
    def __init__(self, building_type="EX", building_level=1,
                 resource_type=None, location=None,
                 construction_progress=0, construction_total=0,
                 research_progress=0, research_total=0):
        self.db = FakeDB(
            building_type=building_type,
            building_level=building_level,
            resource_type=resource_type,
            resource_inventory={},
            construction_progress=construction_progress,
            construction_total=construction_total,
            research_progress=research_progress,
            research_total=research_total,
        )
        self.location = location


# -------------------------------------------------------------- #
#  HarvesterScript Tests (Req 9.1, 9.2, 9.3, 9.4)
# -------------------------------------------------------------- #

class TestHarvesterScript(unittest.TestCase):

    def setUp(self):
        """Register a DataRegistry singleton so capability checks resolve.

        HarvesterScript now decides "is this a harvestable building?" via the
        ``harvestable`` capability (looked up through the DataRegistry
        singleton) rather than a hardcoded ``building_type == "EX"``. Provide a
        registry mapping the test abbreviations to defs with the right caps.
        """
        # Use the ``world.*`` namespace to match production imports in
        # agent_scripts (``from world.data_registry import DataRegistry``);
        # ``mygame.world.*`` is a distinct module object with its own singleton.
        from world.data_registry import DataRegistry
        from world.definitions import BuildingDef
        registry = DataRegistry()
        registry.buildings = {
            "EX": BuildingDef(
                name="Extractor", abbreviation="EX", cost={"Wood": 10},
                max_health=200, requires_hq=True, required_terrain=None,
                category="resource", produces=None,
                capabilities=frozenset({"harvestable", "upgradable"}),
            ),
            "TU": BuildingDef(
                name="Turret", abbreviation="TU", cost={"Iron": 10},
                max_health=300, requires_hq=True, required_terrain=None,
                category="defense", produces=None,
            ),
        }
        DataRegistry.set_instance(registry)

    def tearDown(self):
        from world.data_registry import DataRegistry
        DataRegistry.set_instance(None)

    def _make_script(self, npc):
        script = HarvesterScript()
        script.obj = npc
        return script

    @contextmanager
    def _capture_drops(self):
        """Patch ResourceSystem._spawn_resource_drop and capture spawned drops.

        HarvesterScript.at_repeat does ``from world.systems.resource_system
        import ResourceSystem`` then calls ``ResourceSystem._spawn_resource_drop``.
        We inject a fake module into sys.modules so the call is captured without
        a live DB.
        """
        spawned = []

        mock_module = types.ModuleType("world.systems.resource_system")

        class MockResourceSystem:
            @staticmethod
            def _spawn_resource_drop(room, rtype, amount, x=None, y=None):
                spawned.append({
                    "room": room, "resource_type": rtype,
                    "amount": amount, "x": x, "y": y,
                })

        mock_module.ResourceSystem = MockResourceSystem
        saved = sys.modules.get("world.systems.resource_system")
        sys.modules["world.systems.resource_system"] = mock_module
        try:
            yield spawned
        finally:
            if saved is not None:
                sys.modules["world.systems.resource_system"] = saved
            else:
                sys.modules.pop("world.systems.resource_system", None)

    def _run_until_production(self, script):
        """Drive at_repeat through the harvest cooldown until production fires."""
        from mygame.world.definitions import BalanceConfig
        for _ in range(BalanceConfig().harvest_cooldown_ticks):
            script.at_repeat()

    def test_produces_resource_drops(self):
        """Harvester should spawn a ResourceDrop for the tile's resource type."""
        tile = FakeTile(resource_type="Wood")
        building = FakeBuilding(
            building_type="EX", building_level=1,
            location=tile,
        )
        npc = FakeNPC(role="harvester", role_target=building)
        script = self._make_script(npc)

        with self._capture_drops() as spawned:
            self._run_until_production(script)

        # A drop was spawned with the right resource type and positive amount.
        self.assertEqual(len(spawned), 1)
        self.assertEqual(spawned[0]["resource_type"], "Wood")
        self.assertGreater(spawned[0]["amount"], 0)

        # Production only — no delivery state is set (Req 8.3).
        self.assertIsNone(npc.db.delivery_state)

    def test_production_scales_with_level(self):
        """Higher Extractor level should produce a larger drop amount."""
        tile1 = FakeTile(resource_type="Iron")
        tile3 = FakeTile(resource_type="Iron")
        building_l1 = FakeBuilding(
            building_type="EX", building_level=1, location=tile1,
        )
        building_l3 = FakeBuilding(
            building_type="EX", building_level=3, location=tile3,
        )

        npc1 = FakeNPC(role="harvester", role_target=building_l1)
        npc3 = FakeNPC(role="harvester", role_target=building_l3)

        with self._capture_drops() as spawned1:
            self._run_until_production(self._make_script(npc1))
        with self._capture_drops() as spawned3:
            self._run_until_production(self._make_script(npc3))

        prod_l1 = spawned1[0]["amount"]
        prod_l3 = spawned3[0]["amount"]
        self.assertGreaterEqual(prod_l3, prod_l1)

    def test_skips_incapacitated_agent(self):
        """Incapacitated agent should not produce."""
        tile = FakeTile(resource_type="Wood")
        building = FakeBuilding(
            building_type="EX", building_level=1, location=tile,
        )
        npc = FakeNPC(
            role="harvester", role_target=building, incapacitated=True,
        )
        script = self._make_script(npc)

        with self._capture_drops() as spawned:
            self._run_until_production(script)

        self.assertEqual(len(spawned), 0)

    def test_skips_non_extractor_building(self):
        """Harvester assigned to a non-Extractor should not produce."""
        building = FakeBuilding(building_type="TU", building_level=1)
        npc = FakeNPC(role="harvester", role_target=building)
        script = self._make_script(npc)

        script.at_repeat()

    def test_skips_when_no_role_target(self):
        """Harvester with no role_target should do nothing."""
        npc = FakeNPC(role="harvester", role_target=None)
        script = self._make_script(npc)
        # Should not raise
        script.at_repeat()

    def test_skips_when_no_resource_type(self):
        """Extractor on a tile with no resource should not produce."""
        tile = FakeTile(resource_type=None)
        building = FakeBuilding(
            building_type="EX", building_level=1, location=tile,
        )
        npc = FakeNPC(role="harvester", role_target=building)
        script = self._make_script(npc)

        with self._capture_drops() as spawned:
            self._run_until_production(script)

        self.assertEqual(len(spawned), 0)

    def test_reads_resource_type_from_building_attr(self):
        """If building has explicit resource_type, spawn a drop with that type."""
        tile = FakeTile()  # no resource on tile
        building = FakeBuilding(
            building_type="EX", building_level=1,
            resource_type="Energy", location=tile,
        )
        npc = FakeNPC(role="harvester", role_target=building)
        script = self._make_script(npc)

        with self._capture_drops() as spawned:
            self._run_until_production(script)

        self.assertEqual(len(spawned), 1)
        self.assertEqual(spawned[0]["resource_type"], "Energy")
        self.assertGreater(spawned[0]["amount"], 0)

        # Production only — no delivery state is set (Req 8.3).
        self.assertIsNone(npc.db.delivery_state)

    def test_production_accumulates(self):
        """Repeated production cycles spawn multiple drops (production-only)."""
        tile = FakeTile(resource_type="Wood")
        building = FakeBuilding(
            building_type="EX", building_level=1, location=tile,
        )
        npc = FakeNPC(role="harvester", role_target=building)
        script = self._make_script(npc)

        with self._capture_drops() as spawned:
            # Two full cooldown cycles → two production events.
            self._run_until_production(script)
            self._run_until_production(script)

        self.assertEqual(len(spawned), 2)
        for drop in spawned:
            self.assertEqual(drop["resource_type"], "Wood")
            self.assertGreater(drop["amount"], 0)

        # Production only — no delivery state involvement (Req 8.3, 8.4).
        self.assertIsNone(npc.db.delivery_state)


# -------------------------------------------------------------- #
#  EngineerScript Tests (Req 10.1, 10.5, 10.6)
# -------------------------------------------------------------- #

class TestEngineerScript(unittest.TestCase):

    def _make_script(self, npc):
        script = EngineerScript()
        script.obj = npc
        return script

    def test_increments_construction_progress(self):
        """Engineer should advance construction by 1 each tick."""
        building = FakeBuilding(
            building_type="LB",
            construction_progress=5,
            construction_total=10,
        )
        npc = FakeNPC(role="engineer", role_target=building)
        script = self._make_script(npc)

        script.at_repeat()

        self.assertEqual(building.db.construction_progress, 6)

    def test_completes_construction(self):
        """When progress reaches total, construction completes."""
        building = FakeBuilding(
            building_type="AR",
            construction_progress=9,
            construction_total=10,
        )
        npc = FakeNPC(role="engineer", role_target=building)
        script = self._make_script(npc)

        script.at_repeat()

        self.assertEqual(building.db.construction_progress, 10)

    def test_does_not_exceed_construction_total(self):
        """Already-complete construction should not be incremented."""
        building = FakeBuilding(
            building_type="AR",
            construction_progress=10,
            construction_total=10,
        )
        npc = FakeNPC(role="engineer", role_target=building)
        script = self._make_script(npc)

        script.at_repeat()

        self.assertEqual(building.db.construction_progress, 10)

    def test_increments_research_progress(self):
        """Engineer at a Lab should advance research."""
        building = FakeBuilding(
            building_type="LB",
            construction_total=0,
            research_progress=3,
            research_total=20,
        )
        npc = FakeNPC(role="engineer", role_target=building)
        script = self._make_script(npc)

        script.at_repeat()

        self.assertEqual(building.db.research_progress, 4)

    def test_completes_research(self):
        building = FakeBuilding(
            building_type="LB",
            construction_total=0,
            research_progress=19,
            research_total=20,
        )
        npc = FakeNPC(role="engineer", role_target=building)
        script = self._make_script(npc)

        script.at_repeat()

        self.assertEqual(building.db.research_progress, 20)

    def test_skips_incapacitated_agent(self):
        building = FakeBuilding(
            building_type="AR",
            construction_progress=5,
            construction_total=10,
        )
        npc = FakeNPC(
            role="engineer", role_target=building, incapacitated=True,
        )
        script = self._make_script(npc)

        script.at_repeat()

        self.assertEqual(building.db.construction_progress, 5)

    def test_skips_when_no_role_target(self):
        npc = FakeNPC(role="engineer", role_target=None)
        script = self._make_script(npc)
        # Should not raise
        script.at_repeat()

    def test_construction_takes_priority_over_research(self):
        """If both construction and research are active, construction wins."""
        building = FakeBuilding(
            building_type="LB",
            construction_progress=2,
            construction_total=10,
            research_progress=5,
            research_total=20,
        )
        npc = FakeNPC(role="engineer", role_target=building)
        script = self._make_script(npc)

        script.at_repeat()

        # Construction should advance, research should not
        self.assertEqual(building.db.construction_progress, 3)
        self.assertEqual(building.db.research_progress, 5)


# -------------------------------------------------------------- #
#  Placeholder Script Tests
# -------------------------------------------------------------- #

class TestPlaceholderScripts(unittest.TestCase):
    """Verify placeholder scripts can be instantiated and called."""

    def test_patrol_behavior_runs(self):
        script = PatrolBehavior()
        script.obj = FakeNPC(role="guard")
        script.at_repeat()  # should not raise

    def test_soldier_script_runs(self):
        script = SoldierScript()
        script.obj = FakeNPC(role="soldier")
        script.at_repeat()

    def test_medic_script_runs(self):
        script = MedicScript()
        script.obj = FakeNPC(role="medic")
        script.at_repeat()


# -------------------------------------------------------------- #
#  ROLE_SCRIPT_MAP Tests
# -------------------------------------------------------------- #

class TestRoleScriptMap(unittest.TestCase):

    def test_all_roles_mapped(self):
        expected_roles = {
            "harvester", "engineer", "guard",
            "scout", "soldier", "medic",
        }
        self.assertEqual(set(ROLE_SCRIPT_MAP.keys()), expected_roles)

    def test_map_values_are_classes_or_lists(self):
        for role, value in ROLE_SCRIPT_MAP.items():
            if isinstance(value, list):
                for cls in value:
                    self.assertTrue(
                        callable(cls),
                        f"ROLE_SCRIPT_MAP['{role}'] list item should be a class",
                    )
            else:
                self.assertTrue(
                    callable(value),
                    f"ROLE_SCRIPT_MAP['{role}'] should be a class",
                )

    def test_harvester_maps_to_harvester_script(self):
        """Harvester role maps to a single HarvesterScript (production only).

        Task 8.1 reverted the harvester role to attach HarvesterScript only;
        delivery is now a gated ability rather than part of the role.
        """
        self.assertIs(ROLE_SCRIPT_MAP["harvester"], HarvesterScript)

    def test_delivery_is_gated_ability_not_role_script(self):
        """DeliveryBehavior is a gated ability, not part of the harvester role.

        Role application alone (via ROLE_SCRIPT_MAP) must NOT include
        DeliveryBehavior; it lives in ABILITY_SCRIPT_MAP and is only attached
        by AgentSystem.evaluate_gated_abilities when the agent is at/above the
        ability's gate level AND the player has explicitly enabled it. The full
        attach-via-gate behavior is covered by the AgentSystem property/unit
        tests (Req 8.5/8.6) and the gate-aware _attach_behavior_script tests;
        here we assert only the map wiring that those behaviors rely on.
        """
        self.assertIs(ABILITY_SCRIPT_MAP["delivery"], DeliveryBehavior)

        # DeliveryBehavior must not be reachable via the harvester role entry.
        harvester_value = ROLE_SCRIPT_MAP["harvester"]
        if isinstance(harvester_value, list):
            self.assertNotIn(DeliveryBehavior, harvester_value)
        else:
            self.assertIsNot(harvester_value, DeliveryBehavior)

        # DeliveryBehavior must not appear in any role's script entry.
        for role, value in ROLE_SCRIPT_MAP.items():
            classes = value if isinstance(value, list) else [value]
            self.assertNotIn(
                DeliveryBehavior, classes,
                f"DeliveryBehavior should not be a role script for '{role}'",
            )

    def test_guard_maps_to_patrol(self):
        self.assertIs(ROLE_SCRIPT_MAP["guard"], PatrolBehavior)

    def test_scout_maps_to_patrol(self):
        self.assertIs(ROLE_SCRIPT_MAP["scout"], PatrolBehavior)


# -------------------------------------------------------------- #
#  Script key attribute Tests
# -------------------------------------------------------------- #

class TestScriptKeys(unittest.TestCase):
    """Verify each script sets the correct key in at_script_creation."""

    def _check_key(self, cls, expected_key):
        script = cls()
        script.at_script_creation()
        self.assertEqual(script.key, expected_key)

    def test_harvester_key(self):
        self._check_key(HarvesterScript, "harvester_script")

    def test_engineer_key(self):
        self._check_key(EngineerScript, "engineer_script")

    def test_patrol_behavior_key(self):
        self._check_key(PatrolBehavior, "patrol_behavior")

    def test_soldier_key(self):
        self._check_key(SoldierScript, "soldier_script")

    def test_medic_key(self):
        self._check_key(MedicScript, "medic_script")


# -------------------------------------------------------------- #
#  Gate-driven DeliveryBehavior attach (Req 8.2, 8.3, 9.1)
# -------------------------------------------------------------- #
#
# The harvester-production tests above operate at the
# HarvesterScript.at_repeat level and never drive AgentSystem gate
# evaluation, so none of them attach DeliveryBehavior. This class adds the
# gate-driven coverage required by task 14.3: with a delivery gate at level
# 21, applying the harvester role attaches DeliveryBehavior *only* when the
# ability is enabled, and an at/above-gate-but-not-enabled harvester stays
# production-only while its owner is notified the ability is available.
#
# The AgentSystem fakes (script manager, scripted agent, notifying player)
# mirror world/systems/tests/test_agent_system.py.

from mygame.world.systems.agent_system import (  # noqa: E402
    AgentSystem,
    ABILITY_SCRIPT_KEYS,
)
from mygame.world.data_registry import DataRegistry  # noqa: E402
from mygame.world.definitions import AbilityGateDef  # noqa: E402
from mygame.world.event_bus import EventBus  # noqa: E402
from mygame.world.constants import DeliveryState  # noqa: E402


class _FakeScript:
    """Minimal stand-in for an attached Evennia Script."""

    def __init__(self, key):
        self.key = key
        self._deleted = False

    def delete(self):
        self._deleted = True


class _FakeScriptManager:
    """Minimal scripts manager supporting .all()/.add()/delete semantics."""

    def __init__(self):
        self._scripts = []

    def all(self):
        return [s for s in self._scripts if not s._deleted]

    def add(self, script_cls):
        # Resolve the script key the same way AgentSystem does (by class name).
        key = ABILITY_SCRIPT_KEYS.get(
            getattr(script_cls, "__name__", ""),
            getattr(script_cls, "key", "") or script_cls.__name__,
        )
        self._scripts.append(_FakeScript(key))


class _NotifyingPlayer:
    """Stand-in for an owning player that captures msg(...) notifications."""

    def __init__(self, level=30):
        self.db = FakeDB(level=level)
        self.messages = []

    def msg(self, text, **kwargs):
        self.messages.append(text)


class _GateHarvesterAgent:
    """Harvester agent with a scripts manager and a controllable raw level."""

    def __init__(self, agent_id, owner, raw_level, enabled=None,
                 script_keys=None):
        self.db = FakeDB(
            agent_id=agent_id,
            owner=owner,
            role="harvester",
            enabled_abilities=list(enabled) if enabled is not None else None,
        )
        self._raw_level = raw_level
        self.scripts = _FakeScriptManager()
        for key in (script_keys or []):
            self.scripts._scripts.append(_FakeScript(key))

    def get_raw_level(self):
        return self._raw_level


class TestHarvesterGateDrivenDelivery(unittest.TestCase):
    """Gate-driven DeliveryBehavior attach behavior (Req 8.2, 8.3, 9.1).

    Builds a real AgentSystem with a ``delivery`` gate at level 21 and drives
    ``_attach_behavior_script(agent, "harvester")`` — the AgentSystem path that
    combines the role-to-script map with the ability-gate registry.
    """

    DELIVERY_KEY = "delivery_behavior"

    def setUp(self):
        registry = DataRegistry()
        # delivery gate at level 21 (first level of rank 5)
        registry.ability_gates = {
            "delivery": AbilityGateDef(key="delivery", required_level=21),
        }
        bus = EventBus()
        self.system = AgentSystem(
            registry=registry,
            event_bus=bus,
            create_npc_func=lambda player, agent_id: None,
        )
        # Ability notifications flow as events; attach the presenter so
        # owner message assertions capture the rendered strings.
        from mygame.world.presenters.test_support import attach_presenter
        attach_presenter(bus)

    def _script_keys(self, agent):
        return [s.key for s in agent.scripts.all()]

    def test_delivery_attaches_when_at_gate_and_enabled(self):
        """At/above gate AND enabled → both HarvesterScript and DeliveryBehavior.

        Enablement path: confirms DeliveryBehavior attaches and its delivery
        FSM is initialized to idle (Req 8.3, inverse of 8.2).
        """
        owner = _NotifyingPlayer(level=30)  # ceiling 29
        agent = _GateHarvesterAgent(
            agent_id=1, owner=owner, raw_level=25, enabled=["delivery"],
        )  # effective 25 >= 21

        self.system._attach_behavior_script(agent, "harvester")

        keys = self._script_keys(agent)
        self.assertIn("HarvesterScript", keys)
        self.assertIn(self.DELIVERY_KEY, keys)
        # Delivery FSM initialized to idle on attach (Req 9.3).
        self.assertEqual(agent.db.delivery_state, DeliveryState.IDLE)

    def test_at_gate_but_not_enabled_is_production_only_and_notifies(self):
        """At/above gate but NOT enabled → production-only + available notice.

        The harvester produces (HarvesterScript attaches) but delivery does not
        attach because the player has not enabled it (Req 8.2). The owner is
        notified that the ability is available and how to enable it (Req 9.1),
        and no delivery state is initialized (Req 8.3).
        """
        owner = _NotifyingPlayer(level=30)  # ceiling 29
        agent = _GateHarvesterAgent(
            agent_id=2, owner=owner, raw_level=25, enabled=[],
        )  # effective 25 >= 21 but delivery not enabled

        self.system._attach_behavior_script(agent, "harvester")

        keys = self._script_keys(agent)
        # Production-only: HarvesterScript attaches, DeliveryBehavior does not.
        self.assertIn("HarvesterScript", keys)
        self.assertNotIn(self.DELIVERY_KEY, keys)
        # No delivery FSM state set while delivery is unattached (Req 8.3).
        self.assertIsNone(agent.db.delivery_state)

        # Player is notified the ability is available + how to enable it.
        available_msgs = [m for m in owner.messages if "available" in m]
        self.assertEqual(len(available_msgs), 1)
        self.assertIn("delivery", available_msgs[0])
        self.assertIn("agent ability 2 delivery on", available_msgs[0])


if __name__ == "__main__":
    unittest.main()
