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
    })

    for name, mod in stubs.items():
        sys.modules.setdefault(name, mod)

_ensure_evennia_stubs()

from mygame.typeclasses.agent_scripts import (  # noqa: E402
    HarvesterScript,
    EngineerScript,
    GuardScript,
    ScoutScript,
    SoldierScript,
    MedicScript,
    ROLE_SCRIPT_MAP,
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

    def _make_script(self, npc):
        script = HarvesterScript()
        script.obj = npc
        return script

    def test_produces_resources_into_extractor_inventory(self):
        """Harvester should add resources to the Extractor's inventory."""
        tile = FakeTile(resource_type="Wood")
        building = FakeBuilding(
            building_type="EX", building_level=1,
            location=tile,
        )
        npc = FakeNPC(role="harvester", role_target=building)
        script = self._make_script(npc)

        script.at_repeat()

        inv = building.db.resource_inventory
        self.assertIn("Wood", inv)
        self.assertGreater(inv["Wood"], 0)

    def test_production_scales_with_level(self):
        """Higher Extractor level should produce more resources."""
        tile = FakeTile(resource_type="Iron")
        building_l1 = FakeBuilding(
            building_type="EX", building_level=1, location=tile,
        )
        building_l3 = FakeBuilding(
            building_type="EX", building_level=3, location=tile,
        )

        npc1 = FakeNPC(role="harvester", role_target=building_l1)
        npc3 = FakeNPC(role="harvester", role_target=building_l3)

        self._make_script(npc1).at_repeat()
        self._make_script(npc3).at_repeat()

        prod_l1 = building_l1.db.resource_inventory.get("Iron", 0)
        prod_l3 = building_l3.db.resource_inventory.get("Iron", 0)
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

        script.at_repeat()

        inv = building.db.resource_inventory
        self.assertEqual(sum(inv.values()), 0)

    def test_skips_non_extractor_building(self):
        """Harvester assigned to a non-Extractor should not produce."""
        building = FakeBuilding(building_type="TU", building_level=1)
        npc = FakeNPC(role="harvester", role_target=building)
        script = self._make_script(npc)

        script.at_repeat()
        # No inventory change expected
        inv = getattr(building.db, "resource_inventory", {})
        self.assertEqual(sum(inv.values()), 0)

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

        script.at_repeat()

        inv = building.db.resource_inventory
        self.assertEqual(sum(inv.values()), 0)

    def test_reads_resource_type_from_building_attr(self):
        """If building has explicit resource_type, use it."""
        building = FakeBuilding(
            building_type="EX", building_level=1,
            resource_type="Energy", location=None,
        )
        npc = FakeNPC(role="harvester", role_target=building)
        script = self._make_script(npc)

        script.at_repeat()

        inv = building.db.resource_inventory
        self.assertIn("Energy", inv)

    def test_respects_storage_capacity(self):
        """Production should stop when Extractor is full."""
        tile = FakeTile(resource_type="Wood")
        building = FakeBuilding(
            building_type="EX", building_level=1, location=tile,
        )
        # Fill inventory to capacity (100 for level 1)
        building.db.resource_inventory = {"Wood": 100}
        npc = FakeNPC(role="harvester", role_target=building)
        script = self._make_script(npc)

        script.at_repeat()

        # Should still be 100 (no overflow)
        self.assertEqual(building.db.resource_inventory["Wood"], 100)


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

    def test_guard_script_runs(self):
        script = GuardScript()
        script.obj = FakeNPC(role="guard")
        script.at_repeat()  # should not raise

    def test_scout_script_runs(self):
        script = ScoutScript()
        script.obj = FakeNPC(role="scout")
        script.at_repeat()

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

    def test_map_values_are_classes(self):
        for role, cls in ROLE_SCRIPT_MAP.items():
            self.assertTrue(
                callable(cls),
                f"ROLE_SCRIPT_MAP['{role}'] should be a class",
            )


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

    def test_guard_key(self):
        self._check_key(GuardScript, "guard_script")

    def test_scout_key(self):
        self._check_key(ScoutScript, "scout_script")

    def test_soldier_key(self):
        self._check_key(SoldierScript, "soldier_script")

    def test_medic_key(self):
        self._check_key(MedicScript, "medic_script")


if __name__ == "__main__":
    unittest.main()
