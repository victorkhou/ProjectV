"""
Unit tests for CombatEngine.

Tests queue_attack validation, resolve_tick processing, damage calculation,
player defeat, building destruction, turret auto-attack, and combat lockout.

Requirements: 6.1, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 6.9, 6.10, 6.11,
              6.12, 6.13, 6.14, 6.15, 6.16
"""

import sys
import types
import unittest

# -------------------------------------------------------------- #
#  Bootstrap: stub out Evennia modules
# -------------------------------------------------------------- #

def _ensure_evennia_stubs():
    """Insert lightweight stubs for Evennia modules into sys.modules."""
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
    _mod("evennia.commands")
    _mod("evennia.commands.cmdset")
    _mod("evennia.utils")
    _mod("evennia.utils.utils")
    _mod("evennia.utils.logger")

    for name, mod in stubs.items():
        sys.modules.setdefault(name, mod)

_ensure_evennia_stubs()

from mygame.world.systems.combat_engine import CombatEngine  # noqa: E402
from mygame.world.data_registry import DataRegistry  # noqa: E402
from mygame.world.definitions import BalanceConfig, BuildingDef  # noqa: E402
from mygame.world.event_bus import EventBus  # noqa: E402

# -------------------------------------------------------------- #
#  Helpers / Fakes
# -------------------------------------------------------------- #

RESOURCE_TYPES = (
    "Straw", "Clay", "Wood", "Stone", "Iron",
    "Energy", "Metals", "Circuits",
)

class FakeDB:
    """Simulates Evennia's db attribute handler."""
    def __init__(self, hp=100, hp_max=100, combat_xp=0, combat_lockout_tick=0):
        self.hp = hp
        self.hp_max = hp_max
        self.combat_xp = combat_xp
        self.combat_lockout_tick = combat_lockout_tick
        self.active_powerups = {}

class FakeEquipmentHandler:
    """Lightweight stand-in for EquipmentHandler."""
    def __init__(self):
        self._slots = {}

    def equip(self, item):
        slot = getattr(item, "slot", "weapon")
        self._slots[slot] = item
        return True, f"Equipped to {slot}."

    def get_equipped(self, slot):
        return self._slots.get(slot)

    def get_stat_total(self, stat_name):
        total = 0.0
        for item in self._slots.values():
            if hasattr(item, "get_stat"):
                total += item.get_stat(stat_name, 0)
            elif hasattr(item, "stat_modifiers"):
                total += float(item.stat_modifiers.get(stat_name, 0))
        return total

class FakeWeapon:
    """Lightweight stand-in for a weapon GameItem."""
    def __init__(self, damage=25, weapon_range=3, ammo_cost=None, key="assault_rifle"):
        self.key = key
        self.slot = "weapon"
        self.stat_modifiers = {"damage": damage, "range": weapon_range}
        self.ammo_cost = ammo_cost

    def get_stat(self, stat_name, default=0):
        return float(self.stat_modifiers.get(stat_name, default))

class FakeArmor:
    """Lightweight stand-in for an armor GameItem."""
    def __init__(self, damage_reduction=5, key="kevlar_vest"):
        self.key = key
        self.slot = "torso"
        self.category = "armor"
        self.stat_modifiers = {"damage_reduction": damage_reduction}
        self.ammo_cost = None

    def get_stat(self, stat_name, default=0):
        return float(self.stat_modifiers.get(stat_name, default))

class FakeTile:
    """Lightweight stand-in for a tile."""
    def __init__(self, xyz=(0, 0, "earth"), nearby_players=None):
        self.x = xyz[0]
        self.y = xyz[1]
        self.db = type("_Db", (), {
            "coord_x": xyz[0],
            "coord_y": xyz[1],
        })()
        self._nearby_players = nearby_players or []

    def get_nearby_players(self, x, y, radius):
        # 3-arg spatial-query signature matching PlanetRoom.get_nearby_players;
        # the fake returns its fixed roster regardless of the query center.
        return self._nearby_players

    @property
    def planet_name(self):
        return getattr(self, "_planet", "earth")

class FakePlayer:
    """Lightweight stand-in for CombatCharacter."""
    def __init__(self, name="TestPlayer", hp=100, hp_max=100, combat_xp=0,
                 resources=None, location=None, weapon=None, armor=None,
                 oid=None):
        self.key = name
        self.db = FakeDB(hp=hp, hp_max=hp_max, combat_xp=combat_xp)
        self._resources = {r: 0 for r in RESOURCE_TYPES}
        if resources:
            self._resources.update(resources)
        self.location = location or FakeTile()
        self.equipment = FakeEquipmentHandler()
        self._messages = []
        # Optional stable id for is_owner (.id) friend/foe comparisons.
        if oid is not None:
            self.id = oid
        if weapon:
            self.equipment.equip(weapon)
        if armor:
            self.equipment.equip(armor)

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

    def msg(self, text):
        self._messages.append(text)

class FakeAttributes:
    """Simulates Evennia's Attribute handler."""
    def __init__(self, data=None):
        self._data = data or {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def add(self, key, value):
        self._data[key] = value

class FakeBuilding:
    """Lightweight stand-in for a Building object."""
    def __init__(self, building_type="VV", owner=None, hp=300, hp_max=300,
                 offline=False, location=None):
        self.key = building_type
        self.attributes = FakeAttributes({
            "building_type": building_type,
            "owner": owner,
            "hp": hp,
            "hp_max": hp_max,
            "offline": offline,
        })
        self.location = location
        self._deleted = False

    @property
    def owner(self):
        return self.attributes.get("owner")

    @property
    def is_offline(self):
        return bool(self.attributes.get("offline", default=False))

    def delete(self):
        self._deleted = True

class FakeAgent:
    """Lightweight stand-in for an NPC agent (CombatEntity NPC).

    Agents carry ``db.combat_xp`` (so ``_is_player`` recognizes them) plus
    ``db.npc_type == "agent"`` (so ``_is_agent`` recognizes them).
    """
    def __init__(self, name="Agent", hp=100, hp_max=100, combat_xp=0,
                 location=None, weapon=None):
        self.key = name
        self.db = FakeDB(hp=hp, hp_max=hp_max, combat_xp=combat_xp)
        self.db.npc_type = "agent"
        self.location = location or FakeTile()
        self.equipment = FakeEquipmentHandler()
        self._messages = []
        if weapon:
            self.equipment.equip(weapon)

    def msg(self, text):
        self._messages.append(text)


class FakeEnemyNPC:
    """Lightweight stand-in for an enemy NPC (an NPC-base guard).

    Enemies carry ``db.combat_xp`` (so ``_is_player`` recognizes them — which is
    precisely why the enemy check must run BEFORE the player branch) plus
    ``db.npc_type == "enemy"`` and a ``db.owner`` (the Sentinel). At 0 HP they
    are deleted, not respawned — ``delete()`` records that.
    """
    def __init__(self, name="Guard #1", hp=100, hp_max=100, combat_xp=0,
                 owner=None, location=None, oid=None):
        self.key = name
        self.db = FakeDB(hp=hp, hp_max=hp_max, combat_xp=combat_xp)
        self.db.npc_type = "enemy"
        self.db.owner = owner
        self.location = location or FakeTile()
        self.equipment = FakeEquipmentHandler()
        self._messages = []
        self.deleted = False
        if oid is not None:
            self.id = oid

    def delete(self):
        self.deleted = True

    def msg(self, text):
        self._messages.append(text)


class FakeAgentSystem:
    """Records award_agent_xp / apply_agent_death_loss calls for assertions."""
    def __init__(self):
        self.awarded = []          # list of (agent, source)
        self.death_losses = []     # list of agent

    def award_agent_xp(self, agent, source):
        self.awarded.append((agent, source))

    def apply_agent_death_loss(self, agent):
        self.death_losses.append(agent)


def _make_registry() -> DataRegistry:
    """Create a DataRegistry with default balance config.

    Registers a Turret (``TU``) building def carrying the ``turret`` capability
    so ``process_turrets`` (which now gates on the capability, not a hardcoded
    type) recognizes test turrets.
    """
    registry = DataRegistry()
    registry.balance = BalanceConfig()
    registry.buildings = {
        "TU": BuildingDef(
            name="Turret", abbreviation="TU", cost={"Stone": 20, "Iron": 15},
            max_health=300, requires_hq=True, required_terrain=None,
            category="defense", produces=None,
            capabilities=frozenset({"turret"}),
        ),
        # HQ so a turret owner can "have an active HQ" (deactivation gate).
        "HQ": BuildingDef(
            name="Headquarters", abbreviation="HQ", cost={"Wood": 10},
            max_health=500, requires_hq=False, required_terrain=None,
            category="headquarters", produces=None,
            capabilities=frozenset({"headquarters"}),
        ),
    }
    return registry


class _HqBuilding:
    """A minimal HQ-capability building for an owner's get_buildings()."""
    def __init__(self, planet="earth"):
        self.attributes = FakeAttributes({"building_type": "HQ"})
        self.location = type("_L", (), {"planet_name": planet})()
        self.db = type("_D", (), {"building_type": "HQ",
                                  "under_construction": False})()


def _hq_owner(name="Owner", planet="earth", oid=None):
    """A turret/base owner that has a completed HQ (passes owner_has_active_hq).

    Turret auto-fire is gated on the owner having an active HQ (the PvP
    'no HQ = base inert' rule), so a firing turret's owner must own one.
    """
    owner = FakePlayer(name=name)
    owner.get_buildings = lambda: [_HqBuilding(planet)]
    if oid is not None:
        owner.id = oid
    return owner

def _make_engine(registry=None, event_bus=None, current_tick=0):
    """Create a CombatEngine with test defaults."""
    if registry is None:
        registry = _make_registry()
    if event_bus is None:
        event_bus = EventBus()
    engine = CombatEngine(
        registry=registry,
        event_bus=event_bus,
        current_tick_func=lambda: current_tick,
    )
    # Attack notifications are now emitted as PLAYER_NOTIFICATION events;
    # attach the real presenter so tests capturing target._messages see the
    # rendered attack strings.
    from mygame.world.presenters.test_support import attach_presenter
    attach_presenter(event_bus)
    return engine, event_bus

# -------------------------------------------------------------- #
#  Queue Attack Tests
# -------------------------------------------------------------- #

class TestQueueAttackValidation(unittest.TestCase):
    """Test queue_attack validation chain."""

    def test_no_weapon_rejected(self):
        engine, _ = _make_engine()
        attacker = FakePlayer(name="Attacker")
        target = FakePlayer(name="Target")
        ok, msg = engine.queue_attack(attacker, target)
        self.assertFalse(ok)
        self.assertIn("No weapon", msg)

    def test_self_attack_rejected(self):
        weapon = FakeWeapon()
        engine, _ = _make_engine()
        player = FakePlayer(name="Player", weapon=weapon)
        ok, msg = engine.queue_attack(player, player)
        self.assertFalse(ok)
        self.assertIn("yourself", msg)

    def test_own_building_attack_allowed(self):
        """Friendly fire: attacking your own building is permitted."""
        weapon = FakeWeapon(damage=25, weapon_range=5)
        engine, _ = _make_engine()
        player = FakePlayer(name="Player", weapon=weapon,
                            location=FakeTile(xyz=(0, 0, "earth")))
        building = FakeBuilding(owner=player,
                                location=FakeTile(xyz=(1, 0, "earth")))
        ok, msg = engine.queue_attack(player, building)
        self.assertTrue(ok, msg)

    def test_out_of_range_rejected(self):
        weapon = FakeWeapon(damage=25, weapon_range=3)
        engine, _ = _make_engine()
        attacker = FakePlayer(name="Attacker", weapon=weapon,
                              location=FakeTile(xyz=(0, 0, "earth")))
        target = FakePlayer(name="Target",
                            location=FakeTile(xyz=(10, 10, "earth")))
        ok, msg = engine.queue_attack(attacker, target)
        self.assertFalse(ok)
        self.assertIn("out of range", msg)

    def test_in_range_succeeds(self):
        weapon = FakeWeapon(damage=25, weapon_range=5)
        engine, _ = _make_engine()
        attacker = FakePlayer(name="Attacker", weapon=weapon,
                              location=FakeTile(xyz=(0, 0, "earth")))
        target = FakePlayer(name="Target",
                            location=FakeTile(xyz=(2, 2, "earth")))
        ok, msg = engine.queue_attack(attacker, target)
        self.assertTrue(ok)
        self.assertEqual(len(engine.pending_actions), 1)

    def test_insufficient_ammo_rejected(self):
        weapon = FakeWeapon(damage=25, weapon_range=5, ammo_cost={"Iron": 1})
        engine, _ = _make_engine()
        attacker = FakePlayer(name="Attacker", weapon=weapon,
                              resources={"Iron": 0},
                              location=FakeTile(xyz=(0, 0, "earth")))
        target = FakePlayer(name="Target",
                            location=FakeTile(xyz=(1, 0, "earth")))
        ok, msg = engine.queue_attack(attacker, target)
        self.assertFalse(ok)
        self.assertIn("Insufficient ammo", msg)

    def test_ammo_deducted_on_queue(self):
        weapon = FakeWeapon(damage=25, weapon_range=5, ammo_cost={"Iron": 1})
        engine, _ = _make_engine()
        attacker = FakePlayer(name="Attacker", weapon=weapon,
                              resources={"Iron": 5},
                              location=FakeTile(xyz=(0, 0, "earth")))
        target = FakePlayer(name="Target",
                            location=FakeTile(xyz=(1, 0, "earth")))
        ok, msg = engine.queue_attack(attacker, target)
        self.assertTrue(ok)
        self.assertEqual(attacker.get_resource("Iron"), 4)

# -------------------------------------------------------------- #
#  Resolve Tick Tests
# -------------------------------------------------------------- #

class TestResolveTick(unittest.TestCase):
    """Test resolve_tick processing."""

    def test_damage_applied_to_target(self):
        weapon = FakeWeapon(damage=25, weapon_range=5)
        engine, _ = _make_engine()
        attacker = FakePlayer(name="Attacker", weapon=weapon,
                              location=FakeTile(xyz=(0, 0, "earth")))
        target = FakePlayer(name="Target", hp=100,
                            location=FakeTile(xyz=(1, 0, "earth")))
        engine.queue_attack(attacker, target)
        engine.resolve_tick()
        self.assertEqual(target.db.hp, 75)

    def test_armor_reduces_damage(self):
        weapon = FakeWeapon(damage=25, weapon_range=5)
        armor = FakeArmor(damage_reduction=10)
        engine, _ = _make_engine()
        attacker = FakePlayer(name="Attacker", weapon=weapon,
                              location=FakeTile(xyz=(0, 0, "earth")))
        target = FakePlayer(name="Target", hp=100, armor=armor,
                            location=FakeTile(xyz=(1, 0, "earth")))
        engine.queue_attack(attacker, target)
        engine.resolve_tick()
        # 25 - 10 = 15 damage
        self.assertEqual(target.db.hp, 85)

    def test_damage_minimum_zero(self):
        weapon = FakeWeapon(damage=5, weapon_range=5)
        armor = FakeArmor(damage_reduction=20)
        engine, _ = _make_engine()
        attacker = FakePlayer(name="Attacker", weapon=weapon,
                              location=FakeTile(xyz=(0, 0, "earth")))
        target = FakePlayer(name="Target", hp=100, armor=armor,
                            location=FakeTile(xyz=(1, 0, "earth")))
        engine.queue_attack(attacker, target)
        engine.resolve_tick()
        # 5 - 20 = -15, clamped to 0
        self.assertEqual(target.db.hp, 100)

    def test_combat_action_event_published(self):
        weapon = FakeWeapon(damage=25, weapon_range=5)
        events = []
        event_bus = EventBus()
        event_bus.subscribe("combat_action", lambda **kw: events.append(kw))
        engine, _ = _make_engine(event_bus=event_bus)
        attacker = FakePlayer(name="Attacker", weapon=weapon,
                              location=FakeTile(xyz=(0, 0, "earth")))
        target = FakePlayer(name="Target",
                            location=FakeTile(xyz=(1, 0, "earth")))
        engine.queue_attack(attacker, target)
        engine.resolve_tick()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["attacker"], attacker)
        self.assertEqual(events[0]["damage"], 25)

    def test_target_notified_of_attack(self):
        weapon = FakeWeapon(damage=25, weapon_range=5)
        engine, _ = _make_engine()
        attacker = FakePlayer(name="Attacker", weapon=weapon,
                              location=FakeTile(xyz=(0, 0, "earth")))
        target = FakePlayer(name="Target",
                            location=FakeTile(xyz=(1, 0, "earth")))
        engine.queue_attack(attacker, target)
        engine.resolve_tick()
        self.assertTrue(len(target._messages) > 0)
        self.assertIn("Attacker", target._messages[0])

    def test_combat_lockout_set(self):
        weapon = FakeWeapon(damage=25, weapon_range=5)
        engine, _ = _make_engine(current_tick=10)
        attacker = FakePlayer(name="Attacker", weapon=weapon,
                              location=FakeTile(xyz=(0, 0, "earth")))
        target = FakePlayer(name="Target",
                            location=FakeTile(xyz=(1, 0, "earth")))
        engine.queue_attack(attacker, target)
        engine.resolve_tick()
        # lockout = current_tick(10) + combat_lockout_ticks(5) = 15
        self.assertEqual(attacker.db.combat_lockout_tick, 15)
        self.assertEqual(target.db.combat_lockout_tick, 15)

    def test_fifo_ordering(self):
        """Attacks resolve in the order they were queued."""
        weapon = FakeWeapon(damage=10, weapon_range=5)
        engine, _ = _make_engine()
        tile = FakeTile(xyz=(0, 0, "earth"))
        target = FakePlayer(name="Target", hp=100, location=tile)

        a1 = FakePlayer(name="A1", weapon=weapon, location=tile)
        a2 = FakePlayer(name="A2", weapon=weapon, location=tile)

        engine.queue_attack(a1, target)
        engine.queue_attack(a2, target)
        engine.resolve_tick()

        # Both should have dealt 10 damage each = 80 HP remaining
        self.assertEqual(target.db.hp, 80)

    def test_pending_actions_cleared_after_resolve(self):
        weapon = FakeWeapon(damage=10, weapon_range=5)
        engine, _ = _make_engine()
        tile = FakeTile(xyz=(0, 0, "earth"))
        attacker = FakePlayer(name="Attacker", weapon=weapon, location=tile)
        target = FakePlayer(name="Target", location=tile)
        engine.queue_attack(attacker, target)
        engine.resolve_tick()
        self.assertEqual(len(engine.pending_actions), 0)

# -------------------------------------------------------------- #
#  Player Defeat Tests
# -------------------------------------------------------------- #

class TestPlayerDefeat(unittest.TestCase):
    """Test player defeat handling."""

    def test_defeat_awards_xp_to_attacker(self):
        weapon = FakeWeapon(damage=200, weapon_range=5)  # Enough to kill
        engine, _ = _make_engine()
        attacker = FakePlayer(name="Attacker", weapon=weapon, combat_xp=50,
                              location=FakeTile(xyz=(0, 0, "earth")))
        target = FakePlayer(name="Target", hp=100, combat_xp=200,
                            location=FakeTile(xyz=(1, 0, "earth")))
        engine.queue_attack(attacker, target)
        engine.resolve_tick()
        # xp_kill = 100
        self.assertEqual(attacker.db.combat_xp, 150)

    def test_defeat_deducts_xp_from_victim(self):
        weapon = FakeWeapon(damage=200, weapon_range=5)
        engine, _ = _make_engine()
        attacker = FakePlayer(name="Attacker", weapon=weapon,
                              location=FakeTile(xyz=(0, 0, "earth")))
        target = FakePlayer(name="Target", hp=100, combat_xp=200,
                            location=FakeTile(xyz=(1, 0, "earth")))
        engine.queue_attack(attacker, target)
        engine.resolve_tick()
        # xp_death_loss = 50
        self.assertEqual(target.db.combat_xp, 150)

    def test_defeat_xp_not_below_zero(self):
        weapon = FakeWeapon(damage=200, weapon_range=5)
        engine, _ = _make_engine()
        attacker = FakePlayer(name="Attacker", weapon=weapon,
                              location=FakeTile(xyz=(0, 0, "earth")))
        target = FakePlayer(name="Target", hp=100, combat_xp=20,
                            location=FakeTile(xyz=(1, 0, "earth")))
        engine.queue_attack(attacker, target)
        engine.resolve_tick()
        self.assertEqual(target.db.combat_xp, 0)

    def test_defeat_respawns_victim(self):
        weapon = FakeWeapon(damage=200, weapon_range=5)
        engine, _ = _make_engine()
        attacker = FakePlayer(name="Attacker", weapon=weapon,
                              location=FakeTile(xyz=(0, 0, "earth")))
        target = FakePlayer(name="Target", hp=100, hp_max=100,
                            location=FakeTile(xyz=(1, 0, "earth")))
        engine.queue_attack(attacker, target)
        engine.resolve_tick()
        # HP should be restored to max
        self.assertEqual(target.db.hp, 100)

    def test_defeat_publishes_player_eliminated_event(self):
        weapon = FakeWeapon(damage=200, weapon_range=5)
        events = []
        event_bus = EventBus()
        event_bus.subscribe("player_eliminated", lambda **kw: events.append(kw))
        engine, _ = _make_engine(event_bus=event_bus)
        attacker = FakePlayer(name="Attacker", weapon=weapon,
                              location=FakeTile(xyz=(0, 0, "earth")))
        target = FakePlayer(name="Target", hp=100,
                            location=FakeTile(xyz=(1, 0, "earth")))
        engine.queue_attack(attacker, target)
        engine.resolve_tick()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["attacker"], attacker)
        self.assertEqual(events[0]["victim"], target)

# -------------------------------------------------------------- #
#  Building Destruction Tests
# -------------------------------------------------------------- #

class TestBuildingDestruction(unittest.TestCase):
    """Test building destruction handling."""

    def test_building_destroyed_on_zero_hp(self):
        weapon = FakeWeapon(damage=500, weapon_range=5)
        engine, _ = _make_engine()
        attacker = FakePlayer(name="Attacker", weapon=weapon,
                              location=FakeTile(xyz=(0, 0, "earth")))
        other_player = FakePlayer(name="Other")
        building = FakeBuilding(building_type="MM", owner=other_player,
                                hp=100, hp_max=100,
                                location=FakeTile(xyz=(1, 0, "earth")))
        engine.queue_attack(attacker, building)
        engine.resolve_tick()
        self.assertTrue(building._deleted)

    def test_building_destruction_awards_xp(self):
        weapon = FakeWeapon(damage=500, weapon_range=5)
        engine, _ = _make_engine()
        attacker = FakePlayer(name="Attacker", weapon=weapon, combat_xp=0,
                              location=FakeTile(xyz=(0, 0, "earth")))
        other_player = FakePlayer(name="Other")
        building = FakeBuilding(building_type="MM", owner=other_player,
                                hp=100, hp_max=100,
                                location=FakeTile(xyz=(1, 0, "earth")))
        engine.queue_attack(attacker, building)
        engine.resolve_tick()
        # xp_building_destroy = 50
        self.assertEqual(attacker.db.combat_xp, 50)

    def test_destroying_own_building_awards_no_xp(self):
        """Friendly fire on your own building grants no XP (unfarmable)."""
        weapon = FakeWeapon(damage=500, weapon_range=5)
        engine, _ = _make_engine()
        attacker = FakePlayer(name="Attacker", weapon=weapon, combat_xp=0,
                              location=FakeTile(xyz=(0, 0, "earth")))
        building = FakeBuilding(building_type="MM", owner=attacker,
                                hp=100, hp_max=100,
                                location=FakeTile(xyz=(1, 0, "earth")))
        engine.queue_attack(attacker, building)
        engine.resolve_tick()
        self.assertTrue(building._deleted)      # still destroyed
        self.assertEqual(attacker.db.combat_xp, 0)  # but no reward

    def test_building_destroyed_event_published(self):
        weapon = FakeWeapon(damage=500, weapon_range=5)
        events = []
        event_bus = EventBus()
        event_bus.subscribe("building_destroyed", lambda **kw: events.append(kw))
        engine, _ = _make_engine(event_bus=event_bus)
        attacker = FakePlayer(name="Attacker", weapon=weapon,
                              location=FakeTile(xyz=(0, 0, "earth")))
        other_player = FakePlayer(name="Other")
        building = FakeBuilding(building_type="MM", owner=other_player,
                                hp=100, hp_max=100,
                                location=FakeTile(xyz=(1, 0, "earth")))
        engine.queue_attack(attacker, building)
        engine.resolve_tick()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["building"], building)

# -------------------------------------------------------------- #
#  Turret Tests
# -------------------------------------------------------------- #

class TestProcessTurrets(unittest.TestCase):
    """Test turret auto-attack processing."""

    def test_turret_targets_nearest_hostile(self):
        engine, _ = _make_engine()
        owner = _hq_owner()  # owner has an HQ, so its turret is active
        near_player = FakePlayer(name="Near",
                                 location=FakeTile(xyz=(2, 0, "earth")))
        far_player = FakePlayer(name="Far",
                                location=FakeTile(xyz=(8, 0, "earth")))

        turret_tile = FakeTile(xyz=(0, 0, "earth"),
                               nearby_players=[near_player, far_player])
        turret = FakeBuilding(building_type="TU", owner=owner,
                              hp=300, hp_max=300, location=turret_tile)

        engine.process_turrets([turret])
        self.assertEqual(len(engine.pending_actions), 1)
        self.assertEqual(engine.pending_actions[0]["target"], near_player)

    def test_turret_gated_by_active_owner_ids_set(self):
        """When the precomputed active-owner-id set is supplied, the turret uses
        it (no per-turret get_buildings query). An owner absent from the set is
        inert even with an in-range hostile."""
        engine, _ = _make_engine()
        owner = FakePlayer(name="Owner", oid=55)  # no get_buildings
        hostile = FakePlayer(name="Hostile",
                             location=FakeTile(xyz=(1, 0, "earth")))
        turret_tile = FakeTile(xyz=(0, 0, "earth"), nearby_players=[hostile])
        turret = FakeBuilding(building_type="TU", owner=owner,
                              hp=300, hp_max=300, location=turret_tile)

        # Owner in the active set -> fires.
        engine.process_turrets([turret], active_owner_ids={55})
        self.assertEqual(len(engine.pending_actions), 1)

        # Owner absent -> inert (no fallback DB query attempted).
        engine.pending_actions.clear()
        engine.process_turrets([turret], active_owner_ids=set())
        self.assertEqual(len(engine.pending_actions), 0)

    def test_turret_does_not_fire_through_wall(self):
        """LOS: a Wall between turret and target blocks the shot."""
        engine, _ = _make_engine()
        owner = _hq_owner()
        hostile = FakePlayer(name="Hostile",
                             location=FakeTile(xyz=(3, 0, "earth")))
        turret_tile = FakeTile(xyz=(0, 0, "earth"), nearby_players=[hostile])
        turret = FakeBuilding(building_type="TU", owner=owner,
                              hp=300, hp_max=300, location=turret_tile)
        engine.set_sight_blocked_func(
            lambda loc, x1, y1, x2, y2: (x2, y2) == (3, 0)
        )
        engine.process_turrets([turret])
        self.assertEqual(len(engine.pending_actions), 0)

        # Clear LOS -> fires.
        engine.set_sight_blocked_func(lambda *a: False)
        engine.process_turrets([turret])
        self.assertEqual(len(engine.pending_actions), 1)

    def test_turret_does_not_fire_when_owner_has_no_hq(self):
        """The deactivation rule in production form: an owner with no HQ (a
        plain FakePlayer whose get_buildings returns nothing) has an inert
        turret, even with a hostile in range."""
        engine, _ = _make_engine()
        owner = FakePlayer(name="Owner")  # no get_buildings -> no HQ
        hostile = FakePlayer(name="Hostile",
                             location=FakeTile(xyz=(1, 0, "earth")))
        turret_tile = FakeTile(xyz=(0, 0, "earth"), nearby_players=[hostile])
        turret = FakeBuilding(building_type="TU", owner=owner,
                              hp=300, hp_max=300, location=turret_tile)

        engine.process_turrets([turret])
        self.assertEqual(len(engine.pending_actions), 0)

    def test_turret_ignores_owner(self):
        engine, _ = _make_engine()
        owner = FakePlayer(name="Owner",
                           location=FakeTile(xyz=(1, 0, "earth")))
        turret_tile = FakeTile(xyz=(0, 0, "earth"),
                               nearby_players=[owner])
        turret = FakeBuilding(building_type="TU", owner=owner,
                              hp=300, hp_max=300, location=turret_tile)

        engine.process_turrets([turret])
        self.assertEqual(len(engine.pending_actions), 0)

    def test_turret_no_targets_in_range(self):
        engine, _ = _make_engine()
        owner = FakePlayer(name="Owner")
        far_player = FakePlayer(name="Far",
                                location=FakeTile(xyz=(50, 50, "earth")))
        turret_tile = FakeTile(xyz=(0, 0, "earth"),
                               nearby_players=[far_player])
        turret = FakeBuilding(building_type="TU", owner=owner,
                              hp=300, hp_max=300, location=turret_tile)

        engine.process_turrets([turret])
        self.assertEqual(len(engine.pending_actions), 0)

    def test_turret_skips_offline(self):
        engine, _ = _make_engine()
        owner = FakePlayer(name="Owner")
        hostile = FakePlayer(name="Hostile",
                             location=FakeTile(xyz=(1, 0, "earth")))
        turret_tile = FakeTile(xyz=(0, 0, "earth"),
                               nearby_players=[hostile])
        turret = FakeBuilding(building_type="TU", owner=owner,
                              hp=300, hp_max=300, offline=True,
                              location=turret_tile)

        engine.process_turrets([turret])
        self.assertEqual(len(engine.pending_actions), 0)

    def test_turret_skips_non_turret_buildings(self):
        engine, _ = _make_engine()
        owner = FakePlayer(name="Owner")
        hostile = FakePlayer(name="Hostile",
                             location=FakeTile(xyz=(1, 0, "earth")))
        tile = FakeTile(xyz=(0, 0, "earth"), nearby_players=[hostile])
        building = FakeBuilding(building_type="MM", owner=owner,
                                hp=200, hp_max=200, location=tile)

        engine.process_turrets([building])
        self.assertEqual(len(engine.pending_actions), 0)

    def test_turret_does_not_fire_for_registered_non_turret_building(self):
        """A building that IS registered but lacks the turret capability must
        not fire — proves the fix discriminates on the capability, not merely
        on the type being resolvable. (Guards against a regression that treats
        every known building as a turret.)"""
        registry = _make_registry()
        registry.buildings["WA"] = BuildingDef(
            name="Wall", abbreviation="WA", cost={"Stone": 5},
            max_health=600, requires_hq=True, required_terrain=None,
            category="defense", produces=None,
            capabilities=frozenset(),  # NO turret capability
        )
        engine, _ = _make_engine(registry=registry)
        owner = FakePlayer(name="Owner")
        hostile = FakePlayer(name="Hostile",
                             location=FakeTile(xyz=(1, 0, "earth")))
        tile = FakeTile(xyz=(0, 0, "earth"), nearby_players=[hostile])
        wall = FakeBuilding(building_type="WA", owner=owner,
                            hp=600, hp_max=600, location=tile)

        engine.process_turrets([wall])
        self.assertEqual(len(engine.pending_actions), 0)

    def test_turret_skips_owner_by_id_not_identity(self):
        """The owner-skip compares by .id, not object identity: a distinct
        player object sharing the owner's .id (e.g. a re-fetched proxy after a
        reload) is treated as the owner and not fired upon."""
        engine, _ = _make_engine()
        owner = _hq_owner(oid=7)  # has an HQ, so the turret is active
        # A DISTINCT object with the SAME id as the owner (reload/proxy).
        owner_proxy = FakePlayer(name="OwnerProxy",
                                 location=FakeTile(xyz=(1, 0, "earth")))
        owner_proxy.id = 7
        turret_tile = FakeTile(xyz=(0, 0, "earth"),
                               nearby_players=[owner_proxy])
        turret = FakeBuilding(building_type="TU", owner=owner,
                              hp=300, hp_max=300, location=turret_tile)

        engine.process_turrets([turret])
        self.assertEqual(len(engine.pending_actions), 0)  # skipped by .id

    def test_turret_fires_on_distinct_player_with_different_id(self):
        """Conversely, a hostile whose .id differs from the owner's IS fired
        upon (the id comparison classifies non-owners as hostile)."""
        engine, _ = _make_engine()
        owner = _hq_owner(oid=7)  # has an HQ, so the turret is active
        hostile = FakePlayer(name="Hostile",
                             location=FakeTile(xyz=(1, 0, "earth")))
        hostile.id = 99  # different id -> hostile
        turret_tile = FakeTile(xyz=(0, 0, "earth"),
                               nearby_players=[hostile])
        turret = FakeBuilding(building_type="TU", owner=owner,
                              hp=300, hp_max=300, location=turret_tile)

        engine.process_turrets([turret])
        self.assertEqual(len(engine.pending_actions), 1)
        self.assertEqual(engine.pending_actions[0]["target"], hostile)

    def test_turret_does_not_fire_when_owner_hq_inactive(self):
        """The deactivation gate: when owner_has_active_hq returns False, an
        in-range hostile is NOT fired upon. Locks in the gate's wiring in
        process_turrets before Phase 2 replaces the always-True stub."""
        engine, _ = _make_engine()
        # Owner HAS an HQ (would normally fire), so a False from the predicate
        # can only come from the gate consulting it — not from a missing HQ.
        owner = _hq_owner()
        hostile = FakePlayer(name="Hostile",
                             location=FakeTile(xyz=(1, 0, "earth")))
        turret_tile = FakeTile(xyz=(0, 0, "earth"),
                               nearby_players=[hostile])
        turret = FakeBuilding(building_type="TU", owner=owner,
                              hp=300, hp_max=300, location=turret_tile)

        # process_turrets does `from world.utils import ... owner_has_active_hq`,
        # so patch it on the world.utils module (the import source).
        import world.utils as wu
        original = wu.owner_has_active_hq
        wu.owner_has_active_hq = lambda owner, planet=None, provider=None: False
        try:
            engine.process_turrets([turret])
        finally:
            wu.owner_has_active_hq = original
        self.assertEqual(len(engine.pending_actions), 0)

    def test_turret_end_to_end_with_real_planetroom_query(self):
        """End-to-end: drive process_turrets against a REAL
        PlanetRoom.get_nearby_players (not FakeTile), so the 3-arg spatial-query
        contract is exercised, not just asserted by convention. This is the
        exact seam whose mismatch (a 1-arg get_nearby_players) hid the original
        turret bug."""
        from mygame.typeclasses.rooms import PlanetRoom
        from mygame.world.coordinate.coordinate_index import CoordinateIndex

        class _RealRoom(PlanetRoom):
            def __init__(self, index):
                self._idx = index

            @property
            def coord_index(self):
                return self._idx

            @property
            def planet_name(self):
                return "earth"

        class _RoomDb:
            def __init__(self, x, y):
                self.coord_x = x
                self.coord_y = y

        class _RoomPlayer:
            """A player-like object the real coordinate index accepts."""
            def __init__(self, name, x, y):
                self.key = name
                self.has_account = True
                self.pk = 1
                self.db = _RoomDb(x, y)
                self.location = None
                self._messages = []

            def msg(self, text):
                self._messages.append(text)

        index = CoordinateIndex()
        room = _RealRoom(index)
        near = _RoomPlayer("Near", 2, 0)   # dist 2 from turret at (0,0)
        far = _RoomPlayer("Far", 40, 0)    # far out of radius
        near.location = room
        far.location = room
        index.add(near, 2, 0)
        index.add(far, 40, 0)

        engine, _ = _make_engine()
        # Turret owner is a separate object so near/far are hostile; it has an
        # HQ so the turret is active (deactivation gate).
        owner = _hq_owner()
        turret = FakeBuilding(building_type="TU", owner=owner,
                              hp=300, hp_max=300, location=room)
        # A real Building carries its own coords (db.coord_x/y); the room has
        # none. Give the turret a coord-bearing db so process_turrets resolves
        # its firing position from the building, mirroring live objects.
        turret.db = _RoomDb(0, 0)

        engine.process_turrets([turret])
        self.assertEqual(len(engine.pending_actions), 1)
        self.assertEqual(engine.pending_actions[0]["target"], near)

# -------------------------------------------------------------- #
#  Building Damage Tests
# -------------------------------------------------------------- #

class TestBuildingDamage(unittest.TestCase):
    """Test damage to buildings (no armor reduction)."""

    def test_building_takes_full_weapon_damage(self):
        weapon = FakeWeapon(damage=25, weapon_range=5)
        engine, _ = _make_engine()
        attacker = FakePlayer(name="Attacker", weapon=weapon,
                              location=FakeTile(xyz=(0, 0, "earth")))
        other_player = FakePlayer(name="Other")
        building = FakeBuilding(building_type="MM", owner=other_player,
                                hp=200, hp_max=200,
                                location=FakeTile(xyz=(1, 0, "earth")))
        engine.queue_attack(attacker, building)
        engine.resolve_tick()
        # Buildings have no armor, so full 25 damage
        self.assertEqual(building.attributes.get("hp"), 175)

    def test_building_owner_notified(self):
        weapon = FakeWeapon(damage=25, weapon_range=5)
        engine, _ = _make_engine()
        attacker = FakePlayer(name="Attacker", weapon=weapon,
                              location=FakeTile(xyz=(0, 0, "earth")))
        other_player = FakePlayer(name="Owner")
        building = FakeBuilding(building_type="MM", owner=other_player,
                                hp=200, hp_max=200,
                                location=FakeTile(xyz=(1, 0, "earth")))
        engine.queue_attack(attacker, building)
        engine.resolve_tick()
        self.assertTrue(len(other_player._messages) > 0)
        self.assertIn("Attacker", other_player._messages[0])

    def test_agent_target_owner_notified_of_hit(self):
        """When B attacks A's AGENT, A (the owner) is told its agent was hit —
        the agent has no session of its own, so the notice must go to A."""
        weapon = FakeWeapon(damage=10, weapon_range=5)
        engine, _ = _make_engine()
        owner_a = FakePlayer(name="OwnerA", oid=7,
                             location=FakeTile(xyz=(5, 5, "earth")))
        attacker_b = FakePlayer(name="Bandit", weapon=weapon, oid=9,
                                location=FakeTile(xyz=(0, 0, "earth")))
        agent = FakeAgent(name="MyGuard", hp=100,
                          location=FakeTile(xyz=(1, 0, "earth")))
        agent.db.owner = owner_a
        engine.queue_attack(attacker_b, agent)
        engine.resolve_tick()
        self.assertTrue(
            any("Bandit" in m and "MyGuard" in m for m in owner_a._messages),
            owner_a._messages,
        )

    def test_turret_attacker_owner_notified_of_its_shot(self):
        """When A's TURRET fires on B, A is told its turret struck B (offensive
        notice), and B is told it was attacked (defensive notice)."""
        engine, _ = _make_engine()
        owner_a = FakePlayer(name="OwnerA", oid=7,
                             location=FakeTile(xyz=(5, 5, "earth")))
        turret = FakeBuilding(building_type="TU", owner=owner_a,
                              location=FakeTile(xyz=(0, 0, "earth")))
        target_b = FakePlayer(name="Raider", hp=100, oid=9,
                              location=FakeTile(xyz=(1, 0, "earth")))
        # Drive the turret hit through the shared finalize path. The synthetic
        # weapon just needs a name/damage; the attacker being a TU building is
        # what makes _unit_kind resolve to "turret".
        engine.apply_direct_hit(turret, target_b,
                                FakeWeapon(damage=15, weapon_range=20),
                                current_tick=0)
        # A hears the offensive notice about its turret.
        self.assertTrue(
            any("Turret" in m and "Raider" in m for m in owner_a._messages),
            owner_a._messages,
        )
        # B hears the defensive "attacked" notice.
        self.assertTrue(len(target_b._messages) > 0, target_b._messages)

    def test_hq_destruction_notifies_owner_base_deactivated(self):
        """Destroying a player's HQ fires the base_deactivated alert to the
        owner (the PvP 'no HQ = base inert' consequence)."""
        weapon = FakeWeapon(damage=999, weapon_range=5)
        engine, _ = _make_engine()
        attacker = FakePlayer(name="Raider", weapon=weapon,
                              location=FakeTile(xyz=(0, 0, "earth")))
        victim = FakePlayer(name="Victim")  # player -> _is_player(owner) True
        hq = FakeBuilding(building_type="HQ", owner=victim,
                          hp=1, hp_max=500,
                          location=FakeTile(xyz=(1, 0, "earth")))
        engine.queue_attack(attacker, hq)
        engine.resolve_tick()
        self.assertTrue(
            any("deactivated" in m.lower() for m in victim._messages),
            victim._messages,
        )

    def test_non_hq_destruction_does_not_deactivate(self):
        """Destroying a non-HQ building does NOT fire base_deactivated."""
        weapon = FakeWeapon(damage=999, weapon_range=5)
        engine, _ = _make_engine()
        attacker = FakePlayer(name="Raider", weapon=weapon,
                              location=FakeTile(xyz=(0, 0, "earth")))
        victim = FakePlayer(name="Victim")
        building = FakeBuilding(building_type="MM", owner=victim,
                                hp=1, hp_max=200,
                                location=FakeTile(xyz=(1, 0, "earth")))
        engine.queue_attack(attacker, building)
        engine.resolve_tick()
        self.assertFalse(
            any("deactivated" in m.lower() for m in victim._messages),
            victim._messages,
        )

# -------------------------------------------------------------- #
#  Agent Combat XP / Death Loss Tests (Req 5.4, 6.1)
# -------------------------------------------------------------- #

class TestAgentDefeatXP(unittest.TestCase):
    """Test agent combat XP award and agent death-loss routing."""

    def setUp(self):
        self.agent_system = FakeAgentSystem()

    def _make_engine_with_awarder(self):
        """Build an engine and inject the fake agent XP-awarder."""
        engine, extra = _make_engine()
        engine.set_agent_xp_awarder(lambda: self.agent_system)
        return engine, extra

    def test_agent_kill_credits_owning_player_not_agent(self):
        """A kill by A's agent is credited to A (owning player), NOT banked on
        the agent — the single-owner model. The agent's own combat_xp is
        untouched, and no agent-XP award is routed through the AgentSystem."""
        weapon = FakeWeapon(damage=200, weapon_range=5)
        engine, _ = self._make_engine_with_awarder()
        owner = FakePlayer(name="OwnerA", combat_xp=0, oid=7,
                           location=FakeTile(xyz=(0, 0, "earth")))
        attacker = FakeAgent(name="AgentAttacker", weapon=weapon,
                             location=FakeTile(xyz=(0, 0, "earth")))
        attacker.db.owner = owner
        target = FakePlayer(name="Target", hp=100, combat_xp=200, oid=9,
                            location=FakeTile(xyz=(1, 0, "earth")))
        engine.queue_attack(attacker, target)
        engine.resolve_tick()

        # Kill XP goes to the OWNER (via the player progression fallback), not
        # the agent, and not through the agent-XP path.
        self.assertEqual(self.agent_system.awarded, [])
        self.assertEqual(attacker.db.combat_xp, 0)
        self.assertEqual(owner.db.combat_xp, engine.registry.balance.xp_kill)

    def test_killing_own_agent_awards_no_xp(self):
        """Friendly fire on your own agent grants the attacker no kill XP."""
        weapon = FakeWeapon(damage=200, weapon_range=5)
        engine, _ = self._make_engine_with_awarder()
        attacker = FakePlayer(name="Owner", weapon=weapon, combat_xp=0,
                              location=FakeTile(xyz=(0, 0, "earth")))
        own_agent = FakeAgent(name="MyAgent", hp=100,
                              location=FakeTile(xyz=(1, 0, "earth")))
        own_agent.db.owner = attacker
        engine.queue_attack(attacker, own_agent)
        engine.resolve_tick()

        # No player kill XP for downing your own agent.
        self.assertEqual(attacker.db.combat_xp, 0)

    def test_killing_own_agent_still_applies_death_loss(self):
        """Friendly fire is 'allowed but purely costly': the attacker gains no
        XP, yet the victim agent still takes its death loss. This asserts both
        halves in one scenario (the reward guard doesn't skip the victim path).
        """
        weapon = FakeWeapon(damage=200, weapon_range=5)
        engine, _ = self._make_engine_with_awarder()
        attacker = FakePlayer(name="Owner", weapon=weapon, combat_xp=0,
                              location=FakeTile(xyz=(0, 0, "earth")))
        own_agent = FakeAgent(name="MyAgent", hp=100, combat_xp=200,
                              location=FakeTile(xyz=(1, 0, "earth")))
        own_agent.db.owner = attacker
        engine.queue_attack(attacker, own_agent)
        engine.resolve_tick()

        # No reward to the attacker...
        self.assertEqual(attacker.db.combat_xp, 0)
        self.assertEqual(self.agent_system.awarded, [])
        # ...but the victim's death loss is still applied (purely costly).
        self.assertEqual(self.agent_system.death_losses, [own_agent])

    def test_agent_victim_gets_death_loss_applied(self):
        """A defeated agent victim has agent death loss applied via AgentSystem."""
        weapon = FakeWeapon(damage=200, weapon_range=5)
        engine, _ = self._make_engine_with_awarder()
        attacker = FakePlayer(name="Attacker", weapon=weapon, combat_xp=0,
                              location=FakeTile(xyz=(0, 0, "earth")))
        victim = FakeAgent(name="AgentVictim", hp=100, combat_xp=200,
                           location=FakeTile(xyz=(1, 0, "earth")))
        engine.queue_attack(attacker, victim)
        engine.resolve_tick()

        self.assertEqual(self.agent_system.death_losses, [victim])
        # Agent victim XP is NOT deducted via the player xp_death_loss path;
        # the agent death-loss balance (applied by AgentSystem) governs it.
        self.assertEqual(victim.db.combat_xp, 200)

    def test_agent_victim_not_double_deducted(self):
        """Agent victim defeat does not also run the player xp_death_loss path."""
        weapon = FakeWeapon(damage=200, weapon_range=5)
        engine, _ = self._make_engine_with_awarder()
        attacker = FakePlayer(name="Attacker", weapon=weapon,
                              location=FakeTile(xyz=(0, 0, "earth")))
        victim = FakeAgent(name="AgentVictim", hp=100, combat_xp=200,
                           location=FakeTile(xyz=(1, 0, "earth")))
        engine.queue_attack(attacker, victim)
        engine.resolve_tick()

        # combat_xp untouched by the engine (only AgentSystem would change it).
        self.assertEqual(victim.db.combat_xp, 200)
        self.assertEqual(len(self.agent_system.death_losses), 1)

    def test_player_attacker_still_uses_player_xp_path(self):
        """A non-agent player attacker still earns xp_kill, not agent XP."""
        weapon = FakeWeapon(damage=200, weapon_range=5)
        engine, _ = self._make_engine_with_awarder()
        attacker = FakePlayer(name="Attacker", weapon=weapon, combat_xp=50,
                              location=FakeTile(xyz=(0, 0, "earth")))
        target = FakePlayer(name="Target", hp=100, combat_xp=200,
                            location=FakeTile(xyz=(1, 0, "earth")))
        engine.queue_attack(attacker, target)
        engine.resolve_tick()

        # xp_kill = 100 (player path), no agent XP awarded.
        self.assertEqual(attacker.db.combat_xp, 150)
        self.assertEqual(self.agent_system.awarded, [])

    def test_agent_victim_respawned(self):
        """An agent victim's HP is restored to max after defeat."""
        weapon = FakeWeapon(damage=200, weapon_range=5)
        engine, _ = self._make_engine_with_awarder()
        attacker = FakePlayer(name="Attacker", weapon=weapon,
                              location=FakeTile(xyz=(0, 0, "earth")))
        victim = FakeAgent(name="AgentVictim", hp=100, hp_max=100,
                           location=FakeTile(xyz=(1, 0, "earth")))
        engine.queue_attack(attacker, victim)
        engine.resolve_tick()
        self.assertEqual(victim.db.hp, 100)


class TestEnemyNPCDeath(unittest.TestCase):
    """Enemy NPCs (npc_type='enemy') die permanently at 0 HP (Phase 4)."""

    def _sentinel(self, oid=1):
        """A base owner (Sentinel) with a distinct id for is_owner checks."""
        owner = FakePlayer(name="Sentinel")
        owner.id = oid
        return owner

    def test_enemy_deleted_at_zero_hp(self):
        weapon = FakeWeapon(damage=200, weapon_range=5)
        engine, _ = _make_engine()
        attacker = FakePlayer(name="Raider", weapon=weapon, oid=2,
                              location=FakeTile(xyz=(0, 0, "earth")))
        enemy = FakeEnemyNPC(name="Guard #1", hp=100, owner=self._sentinel(1),
                             location=FakeTile(xyz=(1, 0, "earth")), oid=3)
        engine.queue_attack(attacker, enemy)
        engine.resolve_tick()
        self.assertTrue(enemy.deleted)

    def test_enemy_not_respawned(self):
        """Unlike a player agent, the enemy's HP is NOT reset to max — it dies."""
        weapon = FakeWeapon(damage=200, weapon_range=5)
        engine, _ = _make_engine()
        attacker = FakePlayer(name="Raider", weapon=weapon, oid=2,
                              location=FakeTile(xyz=(0, 0, "earth")))
        enemy = FakeEnemyNPC(name="Guard #1", hp=100, hp_max=100,
                             owner=self._sentinel(1),
                             location=FakeTile(xyz=(1, 0, "earth")), oid=3)
        engine.queue_attack(attacker, enemy)
        engine.resolve_tick()
        self.assertEqual(enemy.db.hp, 0)  # stayed dead, not restored to 100

    def test_kill_awards_xp_kill_to_player(self):
        weapon = FakeWeapon(damage=200, weapon_range=5)
        engine, _ = _make_engine()
        attacker = FakePlayer(name="Raider", weapon=weapon, combat_xp=50, oid=2,
                              location=FakeTile(xyz=(0, 0, "earth")))
        enemy = FakeEnemyNPC(name="Guard #1", hp=100, owner=self._sentinel(1),
                             location=FakeTile(xyz=(1, 0, "earth")), oid=3)
        engine.queue_attack(attacker, enemy)
        engine.resolve_tick()
        # xp_kill = 100 (same as a player kill)
        self.assertEqual(attacker.db.combat_xp, 150)

    def test_kill_publishes_npc_eliminated_event(self):
        weapon = FakeWeapon(damage=200, weapon_range=5)
        events = []
        event_bus = EventBus()
        event_bus.subscribe("npc_eliminated", lambda **kw: events.append(kw))
        engine, _ = _make_engine(event_bus=event_bus)
        attacker = FakePlayer(name="Raider", weapon=weapon, oid=2,
                              location=FakeTile(xyz=(0, 0, "earth")))
        enemy = FakeEnemyNPC(name="Guard #1", hp=100, owner=self._sentinel(1),
                             location=FakeTile(xyz=(1, 0, "earth")), oid=3)
        engine.queue_attack(attacker, enemy)
        engine.resolve_tick()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["attacker"], attacker)
        self.assertEqual(events[0]["victim"], enemy)

    def test_npc_eliminated_published_before_delete(self):
        """Subscribers can still read the victim (not-yet-deleted) at publish."""
        weapon = FakeWeapon(damage=200, weapon_range=5)
        seen_deleted = []
        event_bus = EventBus()
        event_bus.subscribe(
            "npc_eliminated",
            lambda **kw: seen_deleted.append(kw["victim"].deleted),
        )
        engine, _ = _make_engine(event_bus=event_bus)
        attacker = FakePlayer(name="Raider", weapon=weapon, oid=2,
                              location=FakeTile(xyz=(0, 0, "earth")))
        enemy = FakeEnemyNPC(name="Guard #1", hp=100, owner=self._sentinel(1),
                             location=FakeTile(xyz=(1, 0, "earth")), oid=3)
        engine.queue_attack(attacker, enemy)
        engine.resolve_tick()
        self.assertEqual(seen_deleted, [False])  # not deleted yet at publish
        self.assertTrue(enemy.deleted)           # deleted afterward

    def test_killing_own_enemy_awards_no_xp(self):
        """Anti-farm: destroying an enemy NPC you own grants no XP (by .id)."""
        weapon = FakeWeapon(damage=200, weapon_range=5)
        engine, _ = _make_engine()
        owner = self._sentinel(oid=7)
        attacker = FakePlayer(name="Owner", weapon=weapon, combat_xp=50, oid=7,
                              location=FakeTile(xyz=(0, 0, "earth")))
        enemy = FakeEnemyNPC(name="Guard #1", hp=100, owner=owner,
                             location=FakeTile(xyz=(1, 0, "earth")), oid=3)
        engine.queue_attack(attacker, enemy)
        engine.resolve_tick()
        self.assertEqual(attacker.db.combat_xp, 50)  # unchanged
        self.assertTrue(enemy.deleted)               # still dies

    def test_kill_notifies_player(self):
        weapon = FakeWeapon(damage=200, weapon_range=5)
        engine, _ = _make_engine()
        attacker = FakePlayer(name="Raider", weapon=weapon, oid=2,
                              location=FakeTile(xyz=(0, 0, "earth")))
        enemy = FakeEnemyNPC(name="Guard #2", hp=100, owner=self._sentinel(1),
                             location=FakeTile(xyz=(1, 0, "earth")), oid=3)
        engine.queue_attack(attacker, enemy)
        engine.resolve_tick()
        killed = [m for m in attacker._messages if "Guard #2" in m]
        self.assertTrue(killed, attacker._messages)

    def test_agent_kill_of_enemy_credits_owning_player(self):
        """An agent killing an enemy credits its OWNER's kill XP (single-owner
        model), not the agent's own progression (mirrors _handle_player_defeat)."""
        weapon = FakeWeapon(damage=200, weapon_range=5)
        agent_system = FakeAgentSystem()
        engine, _ = _make_engine()
        engine.set_agent_xp_awarder(lambda: agent_system)
        owner = FakePlayer(name="OwnerA", combat_xp=0, oid=7,
                           location=FakeTile(xyz=(0, 0, "earth")))
        attacker = FakeAgent(name="MyGuard", weapon=weapon,
                             location=FakeTile(xyz=(0, 0, "earth")))
        attacker.id = 5
        attacker.db.owner = owner
        enemy = FakeEnemyNPC(name="Guard #1", hp=100, owner=self._sentinel(1),
                             location=FakeTile(xyz=(1, 0, "earth")), oid=3)
        engine.queue_attack(attacker, enemy)
        engine.resolve_tick()
        # Owner credited via the player progression fallback; agent-XP path unused.
        self.assertEqual(agent_system.awarded, [])
        self.assertEqual(owner.db.combat_xp, engine.registry.balance.xp_kill)
        self.assertTrue(enemy.deleted)

    def test_player_agent_still_respawns_regression(self):
        """Regression: a player agent (npc_type='agent') still respawns and is
        NOT deleted — only npc_type='enemy' dies permanently."""
        weapon = FakeWeapon(damage=200, weapon_range=5)
        agent_system = FakeAgentSystem()
        engine, _ = _make_engine()
        engine.set_agent_xp_awarder(lambda: agent_system)
        attacker = FakePlayer(name="Attacker", weapon=weapon, oid=2,
                              location=FakeTile(xyz=(0, 0, "earth")))
        agent = FakeAgent(name="PlayerAgent", hp=100, hp_max=100,
                          location=FakeTile(xyz=(1, 0, "earth")))
        engine.queue_attack(attacker, agent)
        engine.resolve_tick()
        self.assertEqual(agent.db.hp, 100)              # respawned to full
        self.assertFalse(hasattr(agent, "deleted") and agent.deleted)


# -------------------------------------------------------------- #
#  Typed-weapon / gear fakes for the equipment combat touches
#  (tasks 4.1-4.3). These extend the plain FakeWeapon above with the
#  weapon typing and magazine fields the combat engine now reads.
# -------------------------------------------------------------- #

class _WeaponDB:
    """A tiny ``.db`` bag exposing a mutable ``loaded`` count."""

    def __init__(self, loaded=None):
        self.loaded = loaded


class FakeTypedWeapon:
    """Weapon GameItem stand-in carrying weapon_type + a magazine.

    Mirrors the fields the combat engine reads off a live GameItem:
    ``weapon_type``/``ammo_type``/``ammo_per_shot`` (via ``_get_weapon_attr``),
    ``db.loaded`` (via ``_get_loaded``/``_set_loaded``), and ``range``/``damage``
    stats (via ``get_stat``).
    """

    def __init__(self, weapon_type="ranged", damage=25, weapon_range=1,
                 ammo_type=None, ammo_per_shot=1, magazine_size=None,
                 loaded=None, ammo_cost=None, key="typed_weapon"):
        self.key = key
        self.slot = "weapon"
        self.weapon_type = weapon_type
        self.ammo_type = ammo_type
        self.ammo_per_shot = ammo_per_shot
        self.magazine_size = magazine_size
        self.ammo_cost = ammo_cost
        self.stat_modifiers = {"damage": damage, "range": weapon_range}
        self.db = _WeaponDB(loaded=loaded)

    def get_stat(self, stat_name, default=0):
        return float(self.stat_modifiers.get(stat_name, default))


class FakeGear:
    """Non-weapon gear GameItem contributing a single aggregated stat."""

    def __init__(self, slot, stat_name, value, key=None):
        self.key = key or f"{slot}_gear"
        self.slot = slot
        self.stat_modifiers = {stat_name: value}
        self.ammo_cost = None

    def get_stat(self, stat_name, default=0):
        return float(self.stat_modifiers.get(stat_name, default))


def _notification_sink(event_bus):
    """Subscribe to PLAYER_NOTIFICATION and collect (player, kind, data)."""
    from mygame.world.event_bus import PLAYER_NOTIFICATION
    received = []
    event_bus.subscribe(
        PLAYER_NOTIFICATION,
        lambda player, kind, data, **_kw: received.append((player, kind, data)),
    )
    return received


# -------------------------------------------------------------- #
#  Magazine draw (task 4.2, Req 5.3-5.6)
# -------------------------------------------------------------- #

class TestMagazineDraw(unittest.TestCase):
    """Ranged magazine gating and per-shot draw."""

    def _adjacent(self):
        return (FakeTile(xyz=(0, 0, "earth")), FakeTile(xyz=(1, 0, "earth")))

    def test_ranged_shot_decrements_loaded_by_ammo_per_shot(self):
        atk_tile, tgt_tile = self._adjacent()
        weapon = FakeTypedWeapon(
            weapon_type="ranged", damage=25, weapon_range=5,
            ammo_type="rifle_rounds", ammo_per_shot=3,
            magazine_size=30, loaded=10,
        )
        engine, _ = _make_engine()
        attacker = FakePlayer(name="Attacker", weapon=weapon, location=atk_tile)
        target = FakePlayer(name="Target", location=tgt_tile)

        ok, _ = engine.queue_attack(attacker, target)
        self.assertTrue(ok)
        # A single shot draws exactly ammo_per_shot from the magazine.
        self.assertEqual(weapon.db.loaded, 7)

    def test_empty_magazine_rejects_and_does_not_mutate_loaded(self):
        atk_tile, tgt_tile = self._adjacent()
        weapon = FakeTypedWeapon(
            weapon_type="ranged", damage=25, weapon_range=5,
            ammo_type="rifle_rounds", ammo_per_shot=3,
            magazine_size=30, loaded=2,  # < ammo_per_shot
        )
        engine, event_bus = _make_engine()
        received = _notification_sink(event_bus)
        attacker = FakePlayer(name="Attacker", weapon=weapon, location=atk_tile)
        target = FakePlayer(name="Target", location=tgt_tile)

        ok, msg = engine.queue_attack(attacker, target)
        self.assertFalse(ok)
        # The empty-magazine feedback is delivered via the out_of_ammo
        # notification (below), so the returned message is empty — the command
        # layer suppresses it to avoid a duplicate line.
        self.assertEqual(msg, "")
        # Loaded is untouched on a rejected attack.
        self.assertEqual(weapon.db.loaded, 2)
        # Nothing is queued.
        self.assertEqual(len(engine.pending_actions), 0)
        # Attacker is notified to reload (out_of_ammo).
        kinds = [kind for (_p, kind, _d) in received]
        self.assertIn("out_of_ammo", kinds)

    def test_exact_magazine_allows_a_final_shot(self):
        atk_tile, tgt_tile = self._adjacent()
        weapon = FakeTypedWeapon(
            weapon_type="ranged", damage=25, weapon_range=5,
            ammo_type="rifle_rounds", ammo_per_shot=5,
            magazine_size=30, loaded=5,  # exactly ammo_per_shot
        )
        engine, _ = _make_engine()
        attacker = FakePlayer(name="Attacker", weapon=weapon, location=atk_tile)
        target = FakePlayer(name="Target", location=tgt_tile)

        ok, _ = engine.queue_attack(attacker, target)
        self.assertTrue(ok)
        self.assertEqual(weapon.db.loaded, 0)


# -------------------------------------------------------------- #
#  Melee gating (task 4.2, Req 4.2, 4.4)
# -------------------------------------------------------------- #

class TestMeleeGating(unittest.TestCase):
    """A melee weapon's effective range is always 1 and never uses ammo."""

    def test_melee_ignores_range_stat_out_of_range(self):
        weapon = FakeTypedWeapon(
            weapon_type="melee", damage=25, weapon_range=10,  # big stat
        )
        engine, _ = _make_engine()
        attacker = FakePlayer(name="Attacker", weapon=weapon,
                              location=FakeTile(xyz=(0, 0, "earth")))
        # Distance 3 > effective melee range 1 -> rejected despite range=10.
        target = FakePlayer(name="Target",
                            location=FakeTile(xyz=(3, 0, "earth")))
        ok, msg = engine.queue_attack(attacker, target)
        self.assertFalse(ok)
        self.assertIn("out of range", msg)
        self.assertIn("max 1", msg)

    def test_melee_hits_adjacent_target(self):
        weapon = FakeTypedWeapon(
            weapon_type="melee", damage=25, weapon_range=10,
        )
        engine, _ = _make_engine()
        attacker = FakePlayer(name="Attacker", weapon=weapon,
                              location=FakeTile(xyz=(0, 0, "earth")))
        target = FakePlayer(name="Target",
                            location=FakeTile(xyz=(1, 0, "earth")))
        ok, _ = engine.queue_attack(attacker, target)
        self.assertTrue(ok)

    def test_melee_never_touches_ammo(self):
        # Even with a magazine and ammo_type present, melee skips all ammo
        # handling: db.loaded is never read or decremented.
        weapon = FakeTypedWeapon(
            weapon_type="melee", damage=25, weapon_range=10,
            ammo_type="rifle_rounds", ammo_per_shot=3,
            magazine_size=30, loaded=5,
        )
        engine, _ = _make_engine()
        attacker = FakePlayer(name="Attacker", weapon=weapon,
                              location=FakeTile(xyz=(0, 0, "earth")))
        target = FakePlayer(name="Target",
                            location=FakeTile(xyz=(1, 0, "earth")))
        ok, _ = engine.queue_attack(attacker, target)
        self.assertTrue(ok)
        self.assertEqual(weapon.db.loaded, 5)  # untouched


# -------------------------------------------------------------- #
#  Fresh ranged weapon starts full (task 4.3, Req 5.2, 11.7)
# -------------------------------------------------------------- #

class TestFreshRangedWeaponFull(unittest.TestCase):
    """A freshly created ranged weapon arrives with a full magazine."""

    def test_factory_initializes_loaded_to_magazine_size(self):
        from mygame.world.systems.equipment_system import EquipmentSystem
        from mygame.world.definitions import ItemDef

        item_def = ItemDef(
            key="rifle", name="Rifle", slot="weapon", category="weapon",
            weapon_type="ranged", ammo_type="rifle_rounds",
            ammo_per_shot=1, magazine_size=30,
        )
        owner = FakePlayer(name="Owner")
        item = EquipmentSystem._default_create_item(item_def, owner)
        self.assertEqual(item["loaded"], 30)

    def test_factory_does_not_load_melee_weapon(self):
        from mygame.world.systems.equipment_system import EquipmentSystem
        from mygame.world.definitions import ItemDef

        item_def = ItemDef(
            key="blade", name="Blade", slot="weapon", category="weapon",
            weapon_type="melee",
        )
        owner = FakePlayer(name="Owner")
        item = EquipmentSystem._default_create_item(item_def, owner)
        self.assertNotIn("loaded", item)


# -------------------------------------------------------------- #
#  Damage-bonus aggregation (task 4.1, Req 2.3)
# -------------------------------------------------------------- #

class TestDamageBonusAggregation(unittest.TestCase):
    """Attacker damage includes the sum of gear damage_bonus (+ powerups)."""

    def test_gear_damage_bonus_added_to_damage(self):
        weapon = FakeTypedWeapon(weapon_type="ranged", damage=20,
                                 weapon_range=5, key="rifle")
        weapon.db.loaded = 100  # plenty; but no ammo_type -> no gating anyway
        weapon.ammo_type = None
        engine, _ = _make_engine()
        attacker = FakePlayer(name="Attacker", weapon=weapon,
                              location=FakeTile(xyz=(0, 0, "earth")))
        # Two damage_bonus gear pieces in distinct slots.
        attacker.equipment.equip(FakeGear("gloves", "damage_bonus", 5))
        attacker.equipment.equip(FakeGear("accessory", "damage_bonus", 3))
        target = FakePlayer(name="Target", hp=100,
                            location=FakeTile(xyz=(1, 0, "earth")))
        engine.queue_attack(attacker, target)
        engine.resolve_tick()
        # 20 base + (5 + 3) gear bonus = 28 damage.
        self.assertEqual(target.db.hp, 72)

    def test_gear_bonus_and_powerup_stack(self):
        weapon = FakeTypedWeapon(weapon_type="ranged", damage=20,
                                 weapon_range=5, ammo_type=None, key="rifle")
        engine, _ = _make_engine()
        attacker = FakePlayer(name="Attacker", weapon=weapon,
                              location=FakeTile(xyz=(0, 0, "earth")))
        attacker.equipment.equip(FakeGear("gloves", "damage_bonus", 4))
        attacker.db.active_powerups = {
            "rage": {"effect": {"effect_type": "damage_bonus",
                                "effect_value": 6}},
        }
        target = FakePlayer(name="Target", hp=100,
                            location=FakeTile(xyz=(1, 0, "earth")))
        engine.queue_attack(attacker, target)
        engine.resolve_tick()
        # 20 base + 4 gear + 6 powerup = 30 damage.
        self.assertEqual(target.db.hp, 70)


# -------------------------------------------------------------- #
#  Armor aggregation invariance (task 4.1, Req 2.2, 14.1)
# -------------------------------------------------------------- #

class TestArmorAggregation(unittest.TestCase):
    """Target damage_reduction sums across all equipped gear."""

    def test_multiple_armor_pieces_sum_reduction(self):
        weapon = FakeTypedWeapon(weapon_type="ranged", damage=40,
                                 weapon_range=5, ammo_type=None, key="rifle")
        engine, _ = _make_engine()
        attacker = FakePlayer(name="Attacker", weapon=weapon,
                              location=FakeTile(xyz=(0, 0, "earth")))
        target = FakePlayer(name="Target", hp=100,
                            location=FakeTile(xyz=(1, 0, "earth")))
        target.equipment.equip(FakeGear("torso", "damage_reduction", 6))
        target.equipment.equip(FakeGear("legs", "damage_reduction", 4))
        engine.queue_attack(attacker, target)
        engine.resolve_tick()
        # 40 - (6 + 4) = 30 damage.
        self.assertEqual(target.db.hp, 70)


class TestFinalizeHit(unittest.TestCase):
    """The shared post-damage helper used by both resolve_tick and throw AoE.

    Regression guard: the throwable AoE path calls ``_apply_damage`` +
    ``_finalize_hit`` directly (bypassing ``resolve_tick``). Before the fix a
    lethal bomb left a target at 0 HP but never defeated/destroyed. These tests
    pin that ``_finalize_hit`` performs defeat/destruction, notification, and
    the combat event so the throw path resolves a kill identically.
    """

    def _synthetic_weapon(self, damage):
        # Mirrors the SyntheticWeapon shape used by throw AoE.
        w = FakeTypedWeapon(weapon_type="ranged", damage=damage,
                            weapon_range=3, ammo_type=None, key="frag grenade")
        return w

    def test_finalize_hit_defeats_zero_hp_player(self):
        engine, event_bus = _make_engine()
        received = _notification_sink(event_bus)
        attacker = FakePlayer(name="Bomber", combat_xp=0,
                              location=FakeTile(xyz=(0, 0, "earth")))
        victim = FakePlayer(name="Victim", hp=10, hp_max=100, combat_xp=500,
                            location=FakeTile(xyz=(1, 0, "earth")))
        weapon = self._synthetic_weapon(40)

        # Emulate the throw path: apply lethal damage, then finalize.
        engine._apply_damage(victim, 40, attacker)
        self.assertEqual(victim.db.hp, 0)
        engine._finalize_hit(attacker, victim, weapon, 40, 0)

        # Defeat handling ran: victim respawned to full HP, attacker got xp_kill.
        self.assertEqual(victim.db.hp, victim.db.hp_max)
        self.assertEqual(attacker.db.combat_xp,
                         engine.registry.balance.xp_kill)
        # Victim was notified they were attacked (weapon name = the bomb's).
        kinds = [k for (_p, k, _d) in received]
        self.assertIn("attacked", kinds)

    def test_finalize_hit_destroys_zero_hp_building(self):
        from world.event_bus import BUILDING_DESTROYED
        engine, event_bus = _make_engine()
        destroyed = []
        event_bus.subscribe(BUILDING_DESTROYED, lambda **kw: destroyed.append(kw))
        owner = FakePlayer(name="Owner", location=FakeTile(xyz=(9, 9, "earth")))
        attacker = FakePlayer(name="Bomber",
                              location=FakeTile(xyz=(0, 0, "earth")))
        building = FakeBuilding(building_type="EX", owner=owner, hp=20,
                                hp_max=300, location=FakeTile(xyz=(1, 0, "earth")))
        weapon = self._synthetic_weapon(40)

        engine._apply_damage(building, 40, attacker)
        self.assertEqual(engine._get_hp(building), 0)
        engine._finalize_hit(attacker, building, weapon, 40, 0)

        # Destruction handling ran: building deleted and the event published.
        self.assertTrue(building._deleted,
                        "building at 0 HP should be destroyed via _finalize_hit")
        self.assertTrue(destroyed,
                        "BUILDING_DESTROYED should be published via _finalize_hit")

    def test_finalize_hit_nonlethal_does_not_defeat(self):
        engine, _ = _make_engine()
        attacker = FakePlayer(name="Bomber",
                              location=FakeTile(xyz=(0, 0, "earth")))
        victim = FakePlayer(name="Victim", hp=100, hp_max=100, combat_xp=500,
                            location=FakeTile(xyz=(1, 0, "earth")))
        weapon = self._synthetic_weapon(30)

        engine._apply_damage(victim, 30, attacker)
        engine._finalize_hit(attacker, victim, weapon, 30, 0)

        # Survived: HP reduced, not respawned; attacker earns no kill XP.
        self.assertEqual(victim.db.hp, 70)
        self.assertEqual(attacker.db.combat_xp, 0)


class TestKillCounter(unittest.TestCase):
    """Cosmetic kill/death tallies (db.kills / db.deaths) — stats, never
    progression inputs.

    A player or agent tracks its OWN kills; a turret (no score sheet) tallies
    on its owning player. Friendly fire records no kill. The victim's death
    tallies on the victim, friendly fire included.
    """

    def test_player_kill_increments_own_counter_and_victim_death(self):
        weapon = FakeWeapon(damage=999, weapon_range=5)
        engine, _ = _make_engine()
        attacker = FakePlayer(name="Ace", weapon=weapon, combat_xp=0, oid=1,
                              location=FakeTile(xyz=(0, 0, "earth")))
        victim = FakePlayer(name="Vic", hp=1, combat_xp=500, oid=2,
                            location=FakeTile(xyz=(1, 0, "earth")))
        engine.queue_attack(attacker, victim)
        engine.resolve_tick()
        self.assertEqual(attacker.db.kills, 1)
        # The victim's death is tallied on the victim (mirror of the kill).
        self.assertEqual(victim.db.deaths, 1)
        self.assertEqual(getattr(attacker.db, "deaths", 0) or 0, 0)

    def test_agent_victim_death_tallied_on_agent(self):
        """A defeated agent tallies its own death (before respawn)."""
        weapon = FakeWeapon(damage=999, weapon_range=5)
        engine, _ = _make_engine()
        attacker = FakePlayer(name="Ace", weapon=weapon, combat_xp=0, oid=1,
                              location=FakeTile(xyz=(0, 0, "earth")))
        victim_owner = FakePlayer(name="OwnerB", oid=3)
        agent = FakeAgent(name="TheirGuard", hp=1, combat_xp=500,
                          location=FakeTile(xyz=(1, 0, "earth")))
        agent.db.owner = victim_owner
        engine.queue_attack(attacker, agent)
        engine.resolve_tick()
        self.assertEqual(agent.db.deaths, 1)

    def test_friendly_fire_death_still_tallied(self):
        """A death counts even from friendly fire (no kill credit, but the
        victim still died)."""
        weapon = FakeWeapon(damage=999, weapon_range=5)
        engine, _ = _make_engine()
        owner = FakePlayer(name="Owner", weapon=weapon, combat_xp=0, oid=7,
                           location=FakeTile(xyz=(0, 0, "earth")))
        own_agent = FakeAgent(name="MyAgent", hp=1, combat_xp=500,
                              location=FakeTile(xyz=(1, 0, "earth")))
        own_agent.db.owner = owner
        engine.queue_attack(owner, own_agent)
        engine.resolve_tick()
        # No kill credited (friendly fire), but the death is tallied.
        self.assertEqual(getattr(owner.db, "kills", 0) or 0, 0)
        self.assertEqual(own_agent.db.deaths, 1)

    def test_agent_kill_increments_agent_counter_not_owner(self):
        """An agent's kill tallies on the AGENT (its acknowledgment), while its
        OWNER gets the XP — the two ledgers are separate."""
        weapon = FakeWeapon(damage=999, weapon_range=5)
        engine, _ = _make_engine()
        owner = FakePlayer(name="OwnerA", combat_xp=0, oid=7,
                           location=FakeTile(xyz=(0, 0, "earth")))
        agent = FakeAgent(name="Guard", weapon=weapon,
                          location=FakeTile(xyz=(0, 0, "earth")))
        agent.db.owner = owner
        victim = FakePlayer(name="Vic", hp=1, combat_xp=500, oid=2,
                            location=FakeTile(xyz=(1, 0, "earth")))
        engine.queue_attack(agent, victim)
        engine.resolve_tick()
        # Agent tallies its own kill; owner's tally is untouched (owner gets XP).
        self.assertEqual(agent.db.kills, 1)
        self.assertEqual(getattr(owner.db, "kills", 0) or 0, 0)
        self.assertEqual(owner.db.combat_xp, engine.registry.balance.xp_kill)

    def test_turret_kill_tallies_on_owner(self):
        """A turret has no score sheet, so its kill tallies on the owner."""
        engine, _ = _make_engine()
        owner = FakePlayer(name="OwnerA", combat_xp=0, oid=7,
                           location=FakeTile(xyz=(5, 5, "earth")))
        turret = FakeBuilding(building_type="TU", owner=owner,
                              location=FakeTile(xyz=(0, 0, "earth")))
        victim = FakePlayer(name="Vic", hp=1, combat_xp=500, oid=2,
                            location=FakeTile(xyz=(1, 0, "earth")))
        engine.apply_direct_hit(turret, victim,
                                FakeWeapon(damage=999, weapon_range=20),
                                current_tick=0)
        self.assertEqual(owner.db.kills, 1)

    def test_friendly_fire_records_no_kill(self):
        """Downing your own agent grants no XP and records no kill."""
        weapon = FakeWeapon(damage=999, weapon_range=5)
        engine, _ = _make_engine()
        owner = FakePlayer(name="Owner", weapon=weapon, combat_xp=0, oid=7,
                           location=FakeTile(xyz=(0, 0, "earth")))
        own_agent = FakeAgent(name="MyAgent", hp=1, combat_xp=500,
                              location=FakeTile(xyz=(1, 0, "earth")))
        own_agent.db.owner = owner
        engine.queue_attack(owner, own_agent)
        engine.resolve_tick()
        self.assertEqual(getattr(owner.db, "kills", 0) or 0, 0)


class TestClosedBuildingRangedImmunity(unittest.TestCase):
    """A CLOSED building (db.open False) is immune to ranged attacks — only
    melee (adjacent) player/agent attacks reach it. An OPEN building (default)
    takes ranged fire as before.
    """

    @staticmethod
    def _melee_weapon():
        w = FakeWeapon(damage=25, weapon_range=1, key="combat_knife")
        w.weapon_type = "melee"  # effective range 1, immune-bypassing
        return w

    def test_ranged_attack_on_closed_building_rejected(self):
        engine, _ = _make_engine()
        attacker = FakePlayer(name="Sniper",
                              weapon=FakeWeapon(damage=25, weapon_range=8),
                              location=FakeTile(xyz=(0, 0, "earth")))
        building = FakeBuilding(building_type="MM", owner=FakePlayer(name="B"),
                                hp=200, hp_max=200,
                                location=FakeTile(xyz=(3, 0, "earth")))
        building.attributes.add("open", False)  # closed
        ok, msg = engine.queue_attack(attacker, building)
        self.assertFalse(ok)
        self.assertIn("closed", msg.lower())
        # No action queued -> resolving does no damage.
        engine.resolve_tick()
        self.assertEqual(building.attributes.get("hp"), 200)

    def test_melee_attack_on_closed_building_allowed(self):
        engine, _ = _make_engine()
        attacker = FakePlayer(name="Bruiser", weapon=self._melee_weapon(),
                              location=FakeTile(xyz=(0, 0, "earth")))
        building = FakeBuilding(building_type="MM", owner=FakePlayer(name="B"),
                                hp=200, hp_max=200,
                                location=FakeTile(xyz=(1, 0, "earth")))
        building.attributes.add("open", False)  # closed, but attacker is adjacent
        ok, _ = engine.queue_attack(attacker, building)
        self.assertTrue(ok)
        engine.resolve_tick()
        self.assertEqual(building.attributes.get("hp"), 175)

    def test_ranged_attack_on_open_building_allowed(self):
        engine, _ = _make_engine()
        attacker = FakePlayer(name="Sniper",
                              weapon=FakeWeapon(damage=25, weapon_range=8),
                              location=FakeTile(xyz=(0, 0, "earth")))
        building = FakeBuilding(building_type="MM", owner=FakePlayer(name="B"),
                                hp=200, hp_max=200,
                                location=FakeTile(xyz=(3, 0, "earth")))
        building.attributes.add("open", True)  # open
        ok, _ = engine.queue_attack(attacker, building)
        self.assertTrue(ok)
        engine.resolve_tick()
        self.assertEqual(building.attributes.get("hp"), 175)

    def test_unset_open_defaults_to_open(self):
        """A building with no 'open' attribute (legacy) reads as open — ranged
        attacks still land, preserving prior behavior."""
        engine, _ = _make_engine()
        attacker = FakePlayer(name="Sniper",
                              weapon=FakeWeapon(damage=25, weapon_range=8),
                              location=FakeTile(xyz=(0, 0, "earth")))
        building = FakeBuilding(building_type="MM", owner=FakePlayer(name="B"),
                                hp=200, hp_max=200,
                                location=FakeTile(xyz=(3, 0, "earth")))
        # Note: no attributes.add("open", ...) — legacy building.
        ok, _ = engine.queue_attack(attacker, building)
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()