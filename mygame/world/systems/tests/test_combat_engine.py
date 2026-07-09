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

    def test_agent_attacker_awarded_combat_xp_on_kill(self):
        """An agent attacker that kills a victim is awarded "combat" XP."""
        weapon = FakeWeapon(damage=200, weapon_range=5)
        engine, _ = self._make_engine_with_awarder()
        attacker = FakeAgent(name="AgentAttacker", weapon=weapon,
                             location=FakeTile(xyz=(0, 0, "earth")))
        target = FakePlayer(name="Target", hp=100, combat_xp=200,
                            location=FakeTile(xyz=(1, 0, "earth")))
        engine.queue_attack(attacker, target)
        engine.resolve_tick()

        self.assertEqual(self.agent_system.awarded, [(attacker, "combat")])
        # Agent attacker XP is NOT mutated via the player xp_kill path.
        self.assertEqual(attacker.db.combat_xp, 0)

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


if __name__ == "__main__":
    unittest.main()