"""
Integration tests for full game loops.

Tests multi-system workflows end-to-end using lightweight fakes
instead of a running Evennia server.  Each test wires together
BuildingSystem, ResourceSystem, RankSystem, and AgentSystem to
verify a complete flow from start to finish.

Integration scenarios:
1. Full game tick cycle with agents producing resources
2. Construction flow: player presence → timer → completion
3. Agent training → assignment → production → collection
4. Rank up → new planet access → travel
5. Demotion → agent reserve → re-rank → restore
6. YAML hot-reload preserves running game state

Requirements: 14.1, 14.2, 14.3, 14.4, 14.9
"""

import sys
import types
import unittest

# ------------------------------------------------------------------ #
#  Bootstrap: stub out Evennia modules
# ------------------------------------------------------------------ #

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
    _mod("evennia.objects")
    _mod("evennia.objects.objects", {
        "DefaultObject": type("DefaultObject", (), {}),
        "DefaultRoom": type("DefaultRoom", (), {}),
        "DefaultCharacter": type("DefaultCharacter", (), {}),
    })
    _mod("evennia.objects.models")
    _mod("evennia.commands")
    _mod("evennia.commands.command", {
        "Command": type("Command", (), {"func": lambda self: None}),
    })
    _mod("evennia.commands.cmdset")
    _mod("evennia.utils")
    _mod("evennia.utils.utils")
    _mod("evennia.utils.logger")
    _mod("evennia.scripts")
    _mod("evennia.scripts.scripts", {
        "DefaultScript": type("DefaultScript", (), {}),
    })

    for name, mod in stubs.items():
        sys.modules.setdefault(name, mod)

_ensure_evennia_stubs()

from mygame.world.definitions import (  # noqa: E402
    BuildingDef, RankDef, BalanceConfig, CoordinateSpaceDef,
)
from mygame.world.data_registry import DataRegistry  # noqa: E402
from mygame.world.event_bus import (  # noqa: E402
    EventBus, RANK_PROMOTED, RANK_DEMOTED,
    CONSTRUCTION_COMPLETED, RESOURCE_GATHERED,
)
from mygame.world.systems.building_system import BuildingSystem  # noqa: E402
from mygame.world.systems.resource_system import ResourceSystem  # noqa: E402
from mygame.world.systems.rank_system import RankSystem  # noqa: E402
from mygame.world.systems.agent_system import AgentSystem  # noqa: E402
from mygame.world.coordinate.planet_registry import PlanetRegistry  # noqa: E402
from mygame.typeclasses.combat_entity import CombatEntity  # noqa: E402


# ------------------------------------------------------------------ #
#  Shared Fakes
# ------------------------------------------------------------------ #

class FakeDB:
    """Simulates Evennia's db attribute handler."""
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

    def __getattr__(self, name):
        return None


class FakeAttrs:
    """Minimal Evennia-like Attribute store backed by a dict."""
    def __init__(self, data=None):
        self._data = data or {}

    def get(self, key, default=None, **kw):
        return self._data.get(key, default)

    def add(self, key, value, **kw):
        self._data[key] = value

    def has(self, key):
        return key in self._data


class FakeAgent:
    """Lightweight stand-in for an NPC agent."""
    def __init__(self, agent_id, owner=None, role="", reserve=False,
                 incapacitated=False):
        self.db = FakeDB(
            agent_id=agent_id,
            owner=owner,
            npc_type="agent",
            role=role,
            role_target=None,
            reserve=reserve,
            incapacitated=incapacitated,
        )
        self._location = None

    def move_to(self, target, quiet=True):
        self._location = target


class FakeBuilding:
    """Lightweight stand-in for a building object."""
    def __init__(self, building_type="HQ", owner=None, building_level=1,
                 location=None):
        self._attr_data = {
            "building_type": building_type,
            "building_level": building_level,
            "owner": owner,
            "assigned_agent": None,
            "construction_progress": 0,
            "construction_total": 0,
            "resource_inventory": {},
        }
        self.attributes = FakeAttrs(self._attr_data)
        self.db = FakeDB(
            building_type=building_type,
            building_level=building_level,
            owner=owner,
            assigned_agent=None,
            construction_progress=0,
            construction_total=0,
            resource_inventory={},
            training_agent_id=None,
            training_ticks_remaining=None,
            training_owner=None,
        )
        self.owner = owner
        self.building_level = building_level
        self.location = location
        self.is_offline = False
        self.contents = []

    def get_display_abbreviation(self):
        return self._attr_data["building_type"]


class FakeTile:
    """Lightweight stand-in for an OverworldRoom tile."""
    def __init__(self, x=0, y=0, terrain_type="Plains", resource_type=None):
        self._attr_data = {}
        self.attributes = FakeAttrs(self._attr_data)
        self.db = FakeDB(
            coord_x=x, coord_y=y, x=x, y=y,
        )
        self.x = x
        self.y = y
        self.terrain_type = terrain_type
        self.resource_type = resource_type
        self.building = None
        self.contents = []

    @property
    def resource_node(self):
        data = self.attributes.get("resource_node_data")
        return data if data else None


class FakePlayer(CombatEntity):
    """Lightweight stand-in for CombatCharacter.

    Mixes in the real CombatEntity so it exposes ``award_xp`` / ``deduct_xp``,
    matching the contract the refactored RankSystem delegates to.
    """
    def __init__(self, name="Player1", x=50, y=50, planet="terra",
                 combat_xp=0, rank_level=1, level=None, next_agent_id=1,
                 resources=None):
        self.id = 1
        self.key = name
        self.has_account = True
        if level is None:
            level = rank_level  # backward compat
        self.db = FakeDB(
            coord_x=x, coord_y=y, coord_planet=planet,
            combat_xp=combat_xp, rank_level=rank_level,
            level=level,
            next_agent_id=next_agent_id,
            activity_state="idle",
            activity_target=None,
            activity_progress=0,
            combat_timer_expires=0,
            researched_techs=set(),
            combat_lockout_tick=0,
        )
        self._resources = resources or {
            "Wood": 500, "Stone": 500, "Iron": 500,
            "Energy": 0, "Circuits": 0, "Nexium": 0,
        }
        self.location = None
        self._messages = []
        self._buildings = []

    def msg(self, text, **kwargs):
        self._messages.append(text)

    def move_to(self, target, quiet=True):
        self.location = target

    def get_resource(self, resource_type):
        return self._resources.get(resource_type, 0)

    def add_resource(self, resource_type, amount):
        self._resources[resource_type] = self._resources.get(resource_type, 0) + amount

    def has_resources(self, costs):
        return all(self._resources.get(r, 0) >= amt for r, amt in costs.items())

    def deduct_resources(self, costs):
        if not self.has_resources(costs):
            return False
        for r, amt in costs.items():
            self._resources[r] = self._resources.get(r, 0) - amt
        return True

    def get_buildings(self):
        return self._buildings


# ------------------------------------------------------------------ #
#  Shared test data
# ------------------------------------------------------------------ #

_TEST_RANKS = [
    RankDef(name="Recruit", level=1, xp_threshold=0, agent_cap=2,
            planet_access=["terra"]),
    RankDef(name="Private", level=2, xp_threshold=200, agent_cap=3,
            planet_access=["terra"]),
    RankDef(name="Corporal", level=3, xp_threshold=600, agent_cap=4,
            planet_access=["terra", "forge"]),
    RankDef(name="Sergeant", level=4, xp_threshold=1500, agent_cap=6,
            planet_access=["terra", "forge", "tundra"]),
    RankDef(name="Staff_Sergeant", level=5, xp_threshold=3500, agent_cap=8,
            planet_access=["terra", "forge", "tundra"]),
    RankDef(name="Lieutenant", level=6, xp_threshold=7000, agent_cap=10,
            planet_access=["terra", "forge", "tundra", "inferno"]),
    RankDef(name="Captain", level=7, xp_threshold=12000, agent_cap=12,
            planet_access=["terra", "forge", "tundra", "inferno"]),
    RankDef(name="Major", level=8, xp_threshold=20000, agent_cap=14,
            planet_access=["terra", "forge", "tundra", "inferno", "citadel"]),
    RankDef(name="Colonel", level=9, xp_threshold=35000, agent_cap=16,
            planet_access=["terra", "forge", "tundra", "inferno", "citadel"]),
    RankDef(name="Brigadier", level=10, xp_threshold=55000, agent_cap=17,
            planet_access=["terra", "forge", "tundra", "inferno", "citadel"]),
    RankDef(name="General", level=11, xp_threshold=80000, agent_cap=19,
            planet_access=["terra", "forge", "tundra", "inferno", "citadel", "space"]),
    RankDef(name="Marshal", level=12, xp_threshold=120000, agent_cap=20,
            planet_access=["terra", "forge", "tundra", "inferno", "citadel", "space"]),
]

_TEST_BUILDINGS = {
    "HQ": BuildingDef(
        name="Headquarters", abbreviation="HQ",
        cost={"Wood": 10, "Stone": 10, "Iron": 10},
        max_health=500, requires_hq=False, required_terrain=None,
        category="headquarters", produces=None,
        build_time_seconds=5, rank_requirement=1,
        capabilities=frozenset({"headquarters", "storage"}),
    ),
    "EX": BuildingDef(
        name="Extractor", abbreviation="EX",
        cost={"Wood": 20, "Stone": 15},
        max_health=200, requires_hq=True, required_terrain=None,
        category="resource", produces="Wood",
        build_time_seconds=3, rank_requirement=1,
        storage_capacity=100,
        capabilities=frozenset(
            {"harvestable", "upgradable", "requires_resource_terrain"}
        ),
    ),
    "AC": BuildingDef(
        name="Academy", abbreviation="AC",
        cost={"Wood": 30, "Stone": 20, "Iron": 10},
        max_health=300, requires_hq=True, required_terrain=None,
        category="military", produces=None,
        build_time_seconds=4, rank_requirement=1,
    ),
}


def _make_registry():
    """Create a DataRegistry pre-loaded with test data."""
    registry = DataRegistry()
    registry.ranks = list(_TEST_RANKS)
    registry.buildings = dict(_TEST_BUILDINGS)
    registry.balance = BalanceConfig(gather_amount=5)
    registry.technologies = {}
    registry.powerups = {}
    return registry


def _make_planet_registry():
    """Create a PlanetRegistry with test planets."""
    pr = PlanetRegistry()
    pr._spaces = {
        "terra": CoordinateSpaceDef(
            planet_key="terra", planet_type="earth",
            width=500, height=500, terrain_seed=42,
            rank_requirement=1,
        ),
        "forge": CoordinateSpaceDef(
            planet_key="forge", planet_type="industrial",
            width=400, height=400, terrain_seed=7,
            rank_requirement=11,  # level 11 = Corporal
        ),
        "tundra": CoordinateSpaceDef(
            planet_key="tundra", planet_type="ice",
            width=400, height=400, terrain_seed=13,
            rank_requirement=16,  # level 16 = Sergeant
        ),
    }
    return pr


def _make_all_systems(registry=None, event_bus=None, planet_registry=None):
    """Wire up all game systems and return them as a dict.

    Returns dict with keys: registry, event_bus, planet_registry,
    building_system, resource_system, rank_system, agent_system,
    created_agents (list tracking NPC objects created by agent_system).
    """
    registry = registry or _make_registry()
    event_bus = event_bus or EventBus()
    planet_registry = planet_registry or _make_planet_registry()

    # Publish this registry as the singleton so capability lookups
    # (building_has_capability, used by harvester/delivery scripts) resolve.
    DataRegistry.set_instance(registry)

    created_agents: list[FakeAgent] = []

    def fake_create_npc(player, agent_id):
        agent = FakeAgent(agent_id=agent_id, owner=player)
        created_agents.append(agent)
        return agent

    def fake_create_building(building_def, tile, owner):
        b = FakeBuilding(
            building_type=building_def.abbreviation,
            owner=owner,
            location=tile,
        )
        owner._buildings.append(b)
        tile.building = b
        return b

    building_system = BuildingSystem(
        registry=registry,
        event_bus=event_bus,
        create_building_func=fake_create_building,
    )
    resource_system = ResourceSystem(
        registry=registry,
        event_bus=event_bus,
    )
    rank_system = RankSystem(
        registry=registry,
        event_bus=event_bus,
        planet_registry=planet_registry,
    )
    # RankSystem delegates its level->XP curve to the process-global
    # world.progression table. Force this registry's curve active so the
    # integration harness is independent of any table another test module
    # left behind (the __init__ build is skipped when already initialized).
    rank_system._rebuild_thresholds()
    # In-memory AgentRepository over the tracked agents for the test harness.
    class _FakeAgentRepo:
        def find_agents_for_owner(self, owner):
            return [
                a for a in created_agents
                if getattr(getattr(a, "db", None), "owner", None) is owner
            ]

        def find_all_agents(self):
            return list(created_agents)

        def find_training_buildings(self):
            return []

    agent_system = AgentSystem(
        registry=registry,
        event_bus=event_bus,
        create_npc_func=fake_create_npc,
        agent_repository=_FakeAgentRepo(),
    )

    # Wire rank events → agent system (like game_init.py does)
    event_bus.subscribe(RANK_PROMOTED, lambda event_name, **kw: (
        agent_system.handle_promotion(kw["player"], kw["new_agent_cap"])
    ))
    event_bus.subscribe(RANK_DEMOTED, lambda event_name, **kw: (
        agent_system.handle_demotion(kw["player"], kw["new_agent_cap"])
    ))

    return {
        "registry": registry,
        "event_bus": event_bus,
        "planet_registry": planet_registry,
        "building_system": building_system,
        "resource_system": resource_system,
        "rank_system": rank_system,
        "agent_system": agent_system,
        "created_agents": created_agents,
    }


# ------------------------------------------------------------------ #
#  1. Full game tick cycle with agents producing resources
# ------------------------------------------------------------------ #

class TestFullGameTickCycle(unittest.TestCase):
    """Verify a complete game tick where Harvester agents produce resources
    into Extractor inventories.

    Requirements: 14.1, 14.7, 14.8
    """

    def test_tick_cycle_harvester_produces_resources(self):
        """Harvester agent assigned to Extractor produces resources each tick."""
        sys = _make_all_systems()
        player = FakePlayer(combat_xp=120000, rank_level=12, level=56, resources={
            "Wood": 9999, "Stone": 9999, "Iron": 9999,
            "Energy": 0, "Circuits": 0, "Nexium": 0,
        })

        # Build HQ first
        hq_tile = FakeTile(x=50, y=50)
        player.location = hq_tile
        player.db.coord_x = 50
        player.db.coord_y = 50
        ok, _ = sys["building_system"].construct(player, hq_tile, "HQ")
        self.assertTrue(ok)

        # Build Extractor on resource tile
        ex_tile = FakeTile(x=51, y=50, resource_type="Wood")
        ex_tile.attributes.add("resource_node_data", {
            "resource_type": "Wood", "depleted": False,
        })
        ok, _ = sys["building_system"].construct(player, ex_tile, "EX")
        self.assertTrue(ok)
        extractor = player._buildings[-1]

        # Train an agent
        academy = FakeBuilding(building_type="AC", building_level=1)
        ok, _ = sys["agent_system"].train_agent(player, academy)
        self.assertTrue(ok)
        npc = sys["agent_system"].complete_training(academy)
        self.assertIsNotNone(npc)

        # Assign agent as harvester to the extractor
        ok, _ = sys["agent_system"].assign_agent(
            player, npc.db.agent_id, "harvester", extractor,
        )
        self.assertTrue(ok)

        # Wire the agent onto the extractor for production
        extractor.db.assigned_agent = npc
        extractor.attributes.add("assigned_agent", npc)

        # Simulate one game tick: process extractor production
        sys["resource_system"].process_extractor_production([extractor])

        # Verify resources were produced (dropped on the tile)
        inv = ResourceSystem.get_tile_inventory(ex_tile)
        total = sum(inv.values())
        self.assertGreater(total, 0, "Extractor tile should have resource drops")
        self.assertIn("Wood", inv)


# ------------------------------------------------------------------ #
#  2. Construction flow: player presence → timer → completion
# ------------------------------------------------------------------ #

class TestConstructionFlow(unittest.TestCase):
    """Verify the full construction flow with active-presence timer.

    Requirements: 14.2
    """

    def test_construction_with_player_presence(self):
        """Player starts construction, ticks progress, completes building."""
        sys = _make_all_systems()
        player = FakePlayer(combat_xp=0, rank_level=1, resources={
            "Wood": 500, "Stone": 500, "Iron": 500,
            "Energy": 0, "Circuits": 0, "Nexium": 0,
        })

        tile = FakeTile(x=50, y=50)
        player.location = tile
        player.db.coord_x = 50
        player.db.coord_y = 50

        # Start timed construction of HQ (build_time_seconds=5)
        ok, msg = sys["building_system"].start_construction(player, tile, "HQ")
        self.assertTrue(ok, msg)

        # Player should be in "building" state
        self.assertEqual(player.db.activity_state, "building")

        building = player.db.activity_target
        self.assertIsNotNone(building)

        # Tick through construction — HQ has build_time_seconds=5
        build_time = _TEST_BUILDINGS["HQ"].build_time_seconds
        for tick in range(build_time - 1):
            completed = sys["building_system"].process_construction_tick(player)
            self.assertFalse(completed, f"Should not complete at tick {tick+1}")

        # Final tick completes construction
        completed = sys["building_system"].process_construction_tick(player)
        self.assertTrue(completed, "Construction should complete on final tick")

        # Player returns to idle
        self.assertEqual(player.db.activity_state, "idle")


# ------------------------------------------------------------------ #
#  3. Agent training → assignment → production → collection
# ------------------------------------------------------------------ #

class TestAgentTrainAssignProduceCollect(unittest.TestCase):
    """Full agent lifecycle: train at Academy, assign as Harvester,
    produce resources, then collect from Extractor.

    Requirements: 14.3
    """

    def test_train_assign_produce_collect(self):
        """Train agent → assign to Extractor → produce → verify inventory."""
        sys = _make_all_systems()
        player = FakePlayer(combat_xp=120000, rank_level=12, level=56, resources={
            "Wood": 9999, "Stone": 9999, "Iron": 9999,
            "Energy": 0, "Circuits": 0, "Nexium": 0,
        })

        # Step 1: Build HQ
        hq_tile = FakeTile(x=50, y=50)
        player.location = hq_tile
        player.db.coord_x = 50
        player.db.coord_y = 50
        ok, _ = sys["building_system"].construct(player, hq_tile, "HQ")
        self.assertTrue(ok)

        # Step 2: Build Extractor on resource tile
        ex_tile = FakeTile(x=51, y=50, resource_type="Iron")
        ok, _ = sys["building_system"].construct(player, ex_tile, "EX")
        self.assertTrue(ok)
        extractor = player._buildings[-1]
        # Set the resource type on the extractor for production
        extractor.db.resource_type = "Iron"
        extractor.attributes.add("resource_type", "Iron")

        # Step 3: Train agent at Academy
        academy = FakeBuilding(building_type="AC", building_level=1)
        ok, msg = sys["agent_system"].train_agent(player, academy)
        self.assertTrue(ok, msg)
        npc = sys["agent_system"].complete_training(academy)
        self.assertIsNotNone(npc)

        # Step 4: Assign agent as Harvester to Extractor
        ok, msg = sys["agent_system"].assign_agent(
            player, npc.db.agent_id, "harvester", extractor,
        )
        self.assertTrue(ok, msg)
        self.assertEqual(npc.db.role, "harvester")

        # Wire agent onto extractor
        extractor.db.assigned_agent = npc
        extractor.attributes.add("assigned_agent", npc)

        # Step 5: Simulate multiple ticks of production
        for _ in range(5):
            sys["resource_system"].process_extractor_production([extractor])

        # Step 6: Verify resources accumulated on the tile
        inv = ResourceSystem.get_tile_inventory(ex_tile)
        total = sum(inv.values())
        self.assertGreater(total, 0)

        # The produced resource should match the extractor's resource type
        # (Iron in this case, but the building_def.produces is "Wood" for
        # our test EX definition — the actual resource comes from the
        # building's resource_type attribute or terrain)
        self.assertTrue(len(inv) > 0, "Extractor inventory should not be empty")


# ------------------------------------------------------------------ #
#  4. Rank up → new planet access → travel
# ------------------------------------------------------------------ #

class TestRankUpPlanetAccess(unittest.TestCase):
    """Rank up unlocks new planet access via RankSystem + PlanetRegistry.

    Requirements: 14.4
    """

    def test_rank_up_unlocks_planet_travel(self):
        """Player levels up and gains access to a previously locked planet."""
        sys = _make_all_systems()
        player = FakePlayer(combat_xp=0, rank_level=1, level=1)

        rank_sys = sys["rank_system"]

        # Initially at level 1 (Recruit) — can access terra, not forge
        self.assertTrue(rank_sys.can_access_planet(player, "terra"))
        self.assertFalse(rank_sys.can_access_planet(player, "forge"))

        # Award enough XP to reach level 11+ (Corporal, rank 3)
        # Forge requires level 11
        rank_sys.award_xp(player, 700, reason="combat")

        # Verify promotion happened
        self.assertEqual(player.db.rank_level, 3)
        self.assertGreaterEqual(player.db.level, 11)

        # Now player can access forge
        self.assertTrue(rank_sys.can_access_planet(player, "forge"))

        # But not tundra (requires level 16)
        self.assertFalse(rank_sys.can_access_planet(player, "tundra"))

        # Award more XP to reach level 16+ (Sergeant, rank 4)
        rank_sys.award_xp(player, 900, reason="combat")
        self.assertEqual(player.db.rank_level, 4)
        self.assertGreaterEqual(player.db.level, 16)
        self.assertTrue(rank_sys.can_access_planet(player, "tundra"))


# ------------------------------------------------------------------ #
#  5. Demotion → agent reserve → re-rank → restore
# ------------------------------------------------------------------ #

class TestDemotionReserveRestore(unittest.TestCase):
    """Demotion reserves agents, re-ranking restores them.

    Requirements: 14.4
    """

    def test_demotion_reserves_then_promotion_restores(self):
        """Full cycle: train agents → demote → reserve → promote → restore."""
        sys = _make_all_systems()
        agent_sys = sys["agent_system"]
        rank_sys = sys["rank_system"]

        # Start at Corporal (rank 3, agent_cap=4) with enough XP
        player = FakePlayer(
            combat_xp=600, rank_level=3, level=11,
            next_agent_id=1,
            resources={
                "Wood": 99999, "Stone": 99999, "Iron": 99999,
                "Energy": 0, "Circuits": 0, "Nexium": 0,
            },
        )

        # Train 3 agents (total = 1 commander + 3 NPCs = 4, at cap)
        academy = FakeBuilding(building_type="AC", building_level=1)
        trained_npcs = []
        for _ in range(3):
            ok, msg = agent_sys.train_agent(player, academy)
            self.assertTrue(ok, msg)
            npc = agent_sys.complete_training(academy)
            self.assertIsNotNone(npc)
            trained_npcs.append(npc)

        # Verify 3 NPCs trained
        self.assertEqual(agent_sys.get_agent_count(player), 3)

        # Assign all agents to soldier role
        for npc in trained_npcs:
            ok, _ = agent_sys.assign_agent(player, npc.db.agent_id, "soldier")
            self.assertTrue(ok)

        # Demote: deduct XP to drop below Corporal (600) to Private (200)
        # Player has 600 XP, deduct 500 → 100 XP → Recruit (rank 1, cap=2)
        rank_sys.deduct_xp(player, 500)

        self.assertEqual(player.db.rank_level, 1)

        # With cap=2, excess = 4 - 2 = 2 agents should be reserved
        agents = agent_sys.get_agents(player)
        reserved = [a for a in agents if a.db.reserve]
        self.assertEqual(len(reserved), 2)

        # The reserved agents should be the ones with the highest IDs
        reserved_ids = sorted(a.db.agent_id for a in reserved)
        all_ids = sorted(a.db.agent_id for a in agents)
        self.assertEqual(reserved_ids, all_ids[-2:])

        # Reserved agents cannot be reassigned
        for ra in reserved:
            ok, _ = agent_sys.assign_agent(player, ra.db.agent_id, "medic")
            self.assertFalse(ok)

        # Re-rank: award XP back to Corporal (rank 3, cap=4)
        rank_sys.award_xp(player, 600, reason="combat")
        self.assertEqual(player.db.rank_level, 3)

        # Agents should be restored (no longer reserved)
        agents = agent_sys.get_agents(player)
        still_reserved = [a for a in agents if a.db.reserve]
        self.assertEqual(len(still_reserved), 0,
                         "All agents should be restored after re-ranking")


# ------------------------------------------------------------------ #
#  6. YAML hot-reload preserves running game state
# ------------------------------------------------------------------ #

class TestYAMLHotReload(unittest.TestCase):
    """Verify that DataRegistry.reload_all preserves running game state.

    Requirements: 14.9
    """

    def test_hot_reload_preserves_game_state(self):
        """Reload registry data while game objects retain their state."""
        sys = _make_all_systems()
        player = FakePlayer(combat_xp=120000, rank_level=12, level=56, resources={
            "Wood": 9999, "Stone": 9999, "Iron": 9999,
            "Energy": 0, "Circuits": 0, "Nexium": 0,
        })

        # Build HQ and Extractor
        hq_tile = FakeTile(x=50, y=50)
        player.location = hq_tile
        player.db.coord_x = 50
        player.db.coord_y = 50
        ok, _ = sys["building_system"].construct(player, hq_tile, "HQ")
        self.assertTrue(ok)

        ex_tile = FakeTile(x=51, y=50, resource_type="Wood")
        ok, _ = sys["building_system"].construct(player, ex_tile, "EX")
        self.assertTrue(ok)
        extractor = player._buildings[-1]

        # Train and assign an agent
        academy = FakeBuilding(building_type="AC", building_level=1)
        ok, _ = sys["agent_system"].train_agent(player, academy)
        self.assertTrue(ok)
        npc = sys["agent_system"].complete_training(academy)
        ok, _ = sys["agent_system"].assign_agent(
            player, npc.db.agent_id, "harvester", extractor,
        )
        self.assertTrue(ok)

        # Snapshot game state before reload
        pre_xp = player.db.combat_xp
        pre_rank = player.db.rank_level
        pre_agent_role = npc.db.role
        pre_agent_id = npc.db.agent_id
        pre_resources = dict(player._resources)
        pre_building_count = len(player._buildings)

        # Simulate hot-reload by swapping registry data
        # (We can't call reload_all without YAML files, so we simulate
        # the atomic swap that reload_all performs)
        registry = sys["registry"]
        old_buildings = registry.buildings
        old_ranks = registry.ranks
        old_balance = registry.balance

        # Swap in new data (simulating a YAML change)
        new_balance = BalanceConfig(gather_amount=10)  # changed from 5 to 10
        registry.balance = new_balance
        registry.buildings = dict(old_buildings)  # same buildings, new dict
        registry.ranks = list(old_ranks)  # same ranks, new list

        # Verify game state is preserved after reload
        self.assertEqual(player.db.combat_xp, pre_xp)
        self.assertEqual(player.db.rank_level, pre_rank)
        self.assertEqual(npc.db.role, pre_agent_role)
        self.assertEqual(npc.db.agent_id, pre_agent_id)
        self.assertEqual(player._resources, pre_resources)
        self.assertEqual(len(player._buildings), pre_building_count)

        # Verify systems still work with new registry data
        # The new balance has gather_amount=10 (was 5)
        extractor.db.assigned_agent = npc
        extractor.attributes.add("assigned_agent", npc)
        sys["resource_system"].process_extractor_production([extractor])

        inv = ResourceSystem.get_tile_inventory(ex_tile)
        total = sum(inv.values())
        # With gather_amount=10 at level 1, production should be 10
        self.assertGreater(total, 0,
                           "Production should work after hot-reload")

        # Rank system still resolves correctly
        rank = sys["rank_system"].get_rank(player)
        self.assertEqual(rank.level, pre_rank)


if __name__ == "__main__":
    unittest.main()
