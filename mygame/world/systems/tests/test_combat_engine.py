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
from mygame.world.definitions import BalanceConfig  # noqa: E402
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
        self.slot = "armor"
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

    def get_nearby_players(self, radius):
        return self._nearby_players

class FakePlayer:
    """Lightweight stand-in for CombatCharacter."""
    def __init__(self, name="TestPlayer", hp=100, hp_max=100, combat_xp=0,
                 resources=None, location=None, weapon=None, armor=None):
        self.key = name
        self.db = FakeDB(hp=hp, hp_max=hp_max, combat_xp=combat_xp)
        self._resources = {r: 0 for r in RESOURCE_TYPES}
        if resources:
            self._resources.update(resources)
        self.location = location or FakeTile()
        self.equipment = FakeEquipmentHandler()
        self._messages = []
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

def _make_registry() -> DataRegistry:
    """Create a DataRegistry with default balance config."""
    registry = DataRegistry()
    registry.balance = BalanceConfig()
    return registry

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

    def test_own_building_attack_rejected(self):
        weapon = FakeWeapon()
        engine, _ = _make_engine()
        player = FakePlayer(name="Player", weapon=weapon)
        building = FakeBuilding(owner=player, location=player.location)
        ok, msg = engine.queue_attack(player, building)
        self.assertFalse(ok)
        self.assertIn("own buildings", msg)

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
        owner = FakePlayer(name="Owner")
        near_player = FakePlayer(name="Near",
                                 location=FakeTile(xyz=(2, 0, "earth")))
        far_player = FakePlayer(name="Far",
                                location=FakeTile(xyz=(8, 0, "earth")))

        turret_tile = FakeTile(xyz=(0, 0, "earth"),
                               nearby_players=[near_player, far_player])
        turret = FakeBuilding(building_type="VV", owner=owner,
                              hp=300, hp_max=300, location=turret_tile)

        engine.process_turrets([turret])
        self.assertEqual(len(engine.pending_actions), 1)
        self.assertEqual(engine.pending_actions[0]["target"], near_player)

    def test_turret_ignores_owner(self):
        engine, _ = _make_engine()
        owner = FakePlayer(name="Owner",
                           location=FakeTile(xyz=(1, 0, "earth")))
        turret_tile = FakeTile(xyz=(0, 0, "earth"),
                               nearby_players=[owner])
        turret = FakeBuilding(building_type="VV", owner=owner,
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
        turret = FakeBuilding(building_type="VV", owner=owner,
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
        turret = FakeBuilding(building_type="VV", owner=owner,
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

if __name__ == "__main__":
    unittest.main()
