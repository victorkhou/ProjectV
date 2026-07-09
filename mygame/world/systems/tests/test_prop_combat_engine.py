"""
Property-based tests for CombatEngine.

Property 12: Attack damage application
Property 13: Turret targets nearest hostile
Property 14: Player defeat consequences
Property 15: Combat lockout prevents building
Property 16: Attack resolution ordering

Validates: Requirements 6.1, 6.3, 6.4, 6.5, 6.6, 6.9, 6.10, 6.11, 6.16
"""

import sys
import types
import unittest

from hypothesis import given, settings, assume
from hypothesis import strategies as st

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

RESOURCE_TYPES = [
    "Straw", "Clay", "Wood", "Stone", "Iron",
    "Energy", "Metals", "Circuits",
]

class FakeDB:
    """Simulates Evennia's db attribute handler."""
    def __init__(self, hp=100, hp_max=100, combat_xp=0,
                 combat_lockout_tick=0):
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
    def __init__(self, damage=25, weapon_range=3, ammo_cost=None,
                 key="test_weapon"):
        self.key = key
        self.slot = "weapon"
        self.stat_modifiers = {"damage": damage, "range": weapon_range}
        self.ammo_cost = ammo_cost

    def get_stat(self, stat_name, default=0):
        return float(self.stat_modifiers.get(stat_name, default))

class FakeArmor:
    """Lightweight stand-in for an armor GameItem."""
    def __init__(self, damage_reduction=5, key="test_armor"):
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
        # 3-arg spatial-query signature matching PlanetRoom.get_nearby_players.
        return self._nearby_players

    @property
    def planet_name(self):
        return "earth"

class FakePlayer:
    """Lightweight stand-in for CombatCharacter."""
    def __init__(self, name="TestPlayer", hp=100, hp_max=100,
                 combat_xp=0, resources=None, location=None,
                 weapon=None, armor=None):
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
        self._resources[resource_type] = (
            self._resources.get(resource_type, 0) + amount
        )

    def has_resources(self, costs):
        return all(
            self._resources.get(r, 0) >= amt for r, amt in costs.items()
        )

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
    def __init__(self, building_type="VV", owner=None, hp=300,
                 hp_max=300, offline=False, location=None):
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

def _make_registry(balance=None) -> DataRegistry:
    """Create a DataRegistry with default or custom balance config.

    Registers a Turret (``TU``) def with the ``turret`` capability so
    ``process_turrets`` (capability-gated) recognizes test turrets.
    """
    registry = DataRegistry()
    registry.balance = balance or BalanceConfig()
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


def _hq_owner(name="Owner", planet="earth"):
    """A turret owner that has a completed HQ (passes owner_has_active_hq),
    so its turret is active under the 'no HQ = base inert' gate."""
    owner = FakePlayer(name=name)
    _loc = type("_L", (), {"planet_name": planet})()
    _hq = type("_HQ", (), {})()
    _hq.attributes = FakeAttributes({"building_type": "HQ"})
    _hq.location = _loc
    _hq.db = type("_D", (), {"building_type": "HQ",
                             "under_construction": False})()
    owner.get_buildings = lambda: [_hq]
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
    return engine, event_bus

# -------------------------------------------------------------- #
#  Hypothesis strategies
# -------------------------------------------------------------- #

@st.composite
def weapon_strategy(draw):
    """Generate a random weapon with valid stats."""
    damage = draw(st.integers(min_value=1, max_value=200))
    weapon_range = draw(st.integers(min_value=1, max_value=20))
    has_ammo = draw(st.booleans())
    ammo_cost = None
    if has_ammo:
        resource = draw(st.sampled_from(RESOURCE_TYPES))
        amount = draw(st.integers(min_value=1, max_value=5))
        ammo_cost = {resource: amount}
    return FakeWeapon(damage=damage, weapon_range=weapon_range,
                      ammo_cost=ammo_cost)

@st.composite
def armor_strategy(draw):
    """Generate a random armor with valid stats."""
    reduction = draw(st.integers(min_value=0, max_value=100))
    return FakeArmor(damage_reduction=reduction)

@st.composite
def player_hp_strategy(draw):
    """Generate a valid player HP value."""
    return draw(st.integers(min_value=1, max_value=1000))

@st.composite
def combat_xp_strategy(draw):
    """Generate a valid combat XP value."""
    return draw(st.integers(min_value=0, max_value=10000))

@st.composite
def coordinate_strategy(draw):
    """Generate a valid coordinate pair."""
    x = draw(st.integers(min_value=0, max_value=100))
    y = draw(st.integers(min_value=0, max_value=100))
    return (x, y)

# -------------------------------------------------------------- #
#  Property 12: Attack damage application
#  **Validates: Requirements 6.1, 6.3, 6.4, 6.11, 6.16**
# -------------------------------------------------------------- #

class TestProperty12AttackDamage(unittest.TestCase):
    """Property 12: Attack damage application.

    For any attack action where the attacker has a weapon-slot GameItem
    equipped and is within range of the target and has sufficient ammo,
    the target's HP SHALL decrease by weapon_damage - armor_reduction
    (min 0), and ammo SHALL be deducted before damage is applied.

    **Validates: Requirements 6.1, 6.3, 6.4, 6.11, 6.16**
    """

    @given(
        weapon_damage=st.integers(min_value=1, max_value=200),
        armor_reduction=st.integers(min_value=0, max_value=100),
        target_hp=st.integers(min_value=1, max_value=1000),
    )
    @settings(max_examples=100)
    def test_damage_equals_weapon_minus_armor_min_zero(
        self, weapon_damage, armor_reduction, target_hp
    ):
        """Net damage = weapon_damage - armor_reduction, min 0."""
        weapon = FakeWeapon(damage=weapon_damage, weapon_range=10)
        armor = FakeArmor(damage_reduction=armor_reduction)
        tile = FakeTile(xyz=(0, 0, "earth"))

        attacker = FakePlayer(name="Attacker", weapon=weapon, location=tile)
        target = FakePlayer(name="Target", hp=target_hp, hp_max=target_hp,
                            armor=armor, location=tile)

        engine, _ = _make_engine()
        engine.queue_attack(attacker, target)
        engine.resolve_tick()

        expected_damage = max(0, weapon_damage - armor_reduction)
        expected_hp = max(0, target_hp - expected_damage)
        # If target was defeated, HP is reset to max
        if target_hp - expected_damage <= 0:
            self.assertEqual(target.db.hp, target_hp)  # respawned
        else:
            self.assertEqual(target.db.hp, expected_hp)

    @given(
        weapon_damage=st.integers(min_value=1, max_value=200),
        ammo_resource=st.sampled_from(RESOURCE_TYPES),
        ammo_amount=st.integers(min_value=1, max_value=5),
        initial_ammo=st.integers(min_value=1, max_value=50),
    )
    @settings(max_examples=100)
    def test_ammo_deducted_on_queue(
        self, weapon_damage, ammo_resource, ammo_amount, initial_ammo
    ):
        """Ammo is deducted when attack is queued."""
        assume(initial_ammo >= ammo_amount)
        ammo_cost = {ammo_resource: ammo_amount}
        weapon = FakeWeapon(damage=weapon_damage, weapon_range=10,
                            ammo_cost=ammo_cost)
        tile = FakeTile(xyz=(0, 0, "earth"))

        attacker = FakePlayer(
            name="Attacker", weapon=weapon,
            resources={ammo_resource: initial_ammo}, location=tile,
        )
        target = FakePlayer(name="Target", location=tile)

        engine, _ = _make_engine()
        ok, _ = engine.queue_attack(attacker, target)

        self.assertTrue(ok)
        self.assertEqual(
            attacker.get_resource(ammo_resource),
            initial_ammo - ammo_amount,
        )

    @given(
        weapon_damage=st.integers(min_value=1, max_value=200),
        building_hp=st.integers(min_value=1, max_value=1000),
    )
    @settings(max_examples=100)
    def test_building_takes_full_damage_no_armor(
        self, weapon_damage, building_hp
    ):
        """Buildings have no armor, so they take full weapon damage."""
        weapon = FakeWeapon(damage=weapon_damage, weapon_range=10)
        tile = FakeTile(xyz=(0, 0, "earth"))

        attacker = FakePlayer(name="Attacker", weapon=weapon, location=tile)
        other = FakePlayer(name="Other")
        building = FakeBuilding(
            building_type="MM", owner=other,
            hp=building_hp, hp_max=building_hp, location=tile,
        )

        engine, _ = _make_engine()
        engine.queue_attack(attacker, building)
        engine.resolve_tick()

        expected_hp = max(0, building_hp - weapon_damage)
        if expected_hp <= 0:
            self.assertTrue(building._deleted)
        else:
            self.assertEqual(building.attributes.get("hp"), expected_hp)

# -------------------------------------------------------------- #
#  Property 13: Turret targets nearest hostile
#  **Validates: Requirements 6.5**
# -------------------------------------------------------------- #

class TestProperty13TurretTargeting(unittest.TestCase):
    """Property 13: Turret targets nearest hostile.

    For any active turret and set of hostile players within turret_radius,
    the turret SHALL target the player with the minimum tile distance.
    If no hostile player is within range, the turret SHALL not attack.

    **Validates: Requirements 6.5**
    """

    @given(
        num_players=st.integers(min_value=1, max_value=5),
        turret_x=st.integers(min_value=0, max_value=50),
        turret_y=st.integers(min_value=0, max_value=50),
        data=st.data(),
    )
    @settings(max_examples=100)
    def test_turret_targets_nearest(
        self, num_players, turret_x, turret_y, data
    ):
        """Turret always targets the nearest hostile within radius."""
        turret_radius = 10
        balance = BalanceConfig(turret_radius=turret_radius, turret_damage=15)
        engine, _ = _make_engine(registry=_make_registry(balance))

        owner = _hq_owner()  # owner has an HQ, so its turret is active

        # Generate players at various distances
        players = []
        for i in range(num_players):
            px = data.draw(st.integers(min_value=0, max_value=100))
            py = data.draw(st.integers(min_value=0, max_value=100))
            p = FakePlayer(
                name=f"Player_{i}",
                location=FakeTile(xyz=(px, py, "earth")),
            )
            players.append(p)

        turret_tile = FakeTile(
            xyz=(turret_x, turret_y, "earth"),
            nearby_players=players,
        )
        turret = FakeBuilding(
            building_type="TU", owner=owner,
            hp=300, hp_max=300, location=turret_tile,
        )

        engine.process_turrets([turret])

        # Find expected nearest within radius
        in_range = []
        for p in players:
            if p is owner:
                continue
            px, py = p.location.x, p.location.y
            dist = abs(turret_x - px) + abs(turret_y - py)
            if dist <= turret_radius:
                in_range.append((dist, p))

        if not in_range:
            self.assertEqual(len(engine.pending_actions), 0)
        else:
            self.assertEqual(len(engine.pending_actions), 1)
            in_range.sort(key=lambda x: x[0])
            expected_target = in_range[0][1]
            self.assertIs(
                engine.pending_actions[0]["target"], expected_target,
            )

    @given(
        player_dist=st.integers(min_value=11, max_value=100),
    )
    @settings(max_examples=100)
    def test_turret_no_attack_when_out_of_range(self, player_dist):
        """Turret does not attack when no hostile is within radius."""
        balance = BalanceConfig(turret_radius=10, turret_damage=15)
        engine, _ = _make_engine(registry=_make_registry(balance))

        owner = FakePlayer(name="Owner")
        far_player = FakePlayer(
            name="Far",
            location=FakeTile(xyz=(player_dist, 0, "earth")),
        )

        turret_tile = FakeTile(
            xyz=(0, 0, "earth"),
            nearby_players=[far_player],
        )
        turret = FakeBuilding(
            building_type="TU", owner=owner,
            hp=300, hp_max=300, location=turret_tile,
        )

        engine.process_turrets([turret])
        self.assertEqual(len(engine.pending_actions), 0)

# -------------------------------------------------------------- #
#  Property 14: Player defeat consequences
#  **Validates: Requirements 6.6**
# -------------------------------------------------------------- #

class TestProperty14PlayerDefeat(unittest.TestCase):
    """Property 14: Player defeat consequences.

    For any player whose HP reaches zero from combat, the attacker SHALL
    receive xp_kill Combat XP, the defeated player SHALL lose
    xp_death_loss Combat XP (not below 0), and the defeated player
    SHALL respawn.

    **Validates: Requirements 6.6**
    """

    @given(
        attacker_xp=combat_xp_strategy(),
        victim_xp=combat_xp_strategy(),
        victim_hp=st.integers(min_value=1, max_value=200),
        xp_kill=st.integers(min_value=1, max_value=500),
        xp_death_loss=st.integers(min_value=1, max_value=500),
    )
    @settings(max_examples=100)
    def test_defeat_xp_consequences(
        self, attacker_xp, victim_xp, victim_hp, xp_kill, xp_death_loss
    ):
        """On defeat: attacker gains xp_kill, victim loses xp_death_loss."""
        balance = BalanceConfig(xp_kill=xp_kill, xp_death_loss=xp_death_loss)
        engine, _ = _make_engine(registry=_make_registry(balance))

        # Weapon damage must exceed victim HP to trigger defeat
        weapon = FakeWeapon(damage=victim_hp + 100, weapon_range=10)
        tile = FakeTile(xyz=(0, 0, "earth"))

        attacker = FakePlayer(
            name="Attacker", weapon=weapon,
            combat_xp=attacker_xp, location=tile,
        )
        victim = FakePlayer(
            name="Victim", hp=victim_hp, hp_max=victim_hp,
            combat_xp=victim_xp, location=tile,
        )

        engine.queue_attack(attacker, victim)
        engine.resolve_tick()

        # Attacker gains xp_kill
        self.assertEqual(attacker.db.combat_xp, attacker_xp + xp_kill)

        # Victim loses xp_death_loss (min 0)
        expected_victim_xp = max(0, victim_xp - xp_death_loss)
        self.assertEqual(victim.db.combat_xp, expected_victim_xp)

        # Victim respawned (HP restored)
        self.assertEqual(victim.db.hp, victim_hp)

    @given(
        victim_xp=st.integers(min_value=0, max_value=10),
        xp_death_loss=st.integers(min_value=11, max_value=500),
    )
    @settings(max_examples=100)
    def test_victim_xp_never_below_zero(
        self, victim_xp, xp_death_loss
    ):
        """Victim XP never goes below zero after defeat."""
        balance = BalanceConfig(
            xp_kill=100, xp_death_loss=xp_death_loss,
        )
        engine, _ = _make_engine(registry=_make_registry(balance))

        weapon = FakeWeapon(damage=500, weapon_range=10)
        tile = FakeTile(xyz=(0, 0, "earth"))

        attacker = FakePlayer(
            name="Attacker", weapon=weapon, location=tile,
        )
        victim = FakePlayer(
            name="Victim", hp=100, hp_max=100,
            combat_xp=victim_xp, location=tile,
        )

        engine.queue_attack(attacker, victim)
        engine.resolve_tick()

        self.assertGreaterEqual(victim.db.combat_xp, 0)

# -------------------------------------------------------------- #
#  Property 15: Combat lockout prevents building
#  **Validates: Requirements 6.10**
# -------------------------------------------------------------- #

class TestProperty15CombatLockout(unittest.TestCase):
    """Property 15: Combat lockout prevents building.

    After combat, player cannot build for lockout_ticks.

    **Validates: Requirements 6.10**
    """

    @given(
        current_tick=st.integers(min_value=0, max_value=1000),
        lockout_ticks=st.integers(min_value=1, max_value=20),
    )
    @settings(max_examples=100)
    def test_lockout_set_after_combat(
        self, current_tick, lockout_ticks
    ):
        """After an attack resolves, both attacker and target get lockout."""
        balance = BalanceConfig(combat_lockout_ticks=lockout_ticks)
        engine, _ = _make_engine(
            registry=_make_registry(balance),
            current_tick=current_tick,
        )

        weapon = FakeWeapon(damage=10, weapon_range=10)
        tile = FakeTile(xyz=(0, 0, "earth"))

        attacker = FakePlayer(
            name="Attacker", weapon=weapon, location=tile,
        )
        target = FakePlayer(name="Target", hp=100, location=tile)

        engine.queue_attack(attacker, target)
        engine.resolve_tick()

        expected_lockout = current_tick + lockout_ticks
        self.assertEqual(attacker.db.combat_lockout_tick, expected_lockout)
        self.assertEqual(target.db.combat_lockout_tick, expected_lockout)

    @given(
        current_tick=st.integers(min_value=0, max_value=1000),
        lockout_ticks=st.integers(min_value=1, max_value=20),
    )
    @settings(max_examples=100)
    def test_lockout_prevents_building(
        self, current_tick, lockout_ticks
    ):
        """A player with active lockout cannot build."""
        from mygame.world.systems.building_system import BuildingSystem
        from mygame.world.definitions import BuildingDef

        balance = BalanceConfig(combat_lockout_ticks=lockout_ticks)
        registry = _make_registry(balance)
        registry.buildings = {
            "HQ": BuildingDef(
                name="Headquarters", abbreviation="HQ",
                cost={"Straw": 50, "Wood": 50, "Stone": 30},
                max_health=500, requires_hq=False,
                required_terrain=None, category="headquarters",
                produces=None, unlocks=[], map_symbol="HQ",
            ),
        }

        # Player has lockout set in the future
        lockout_until = current_tick + lockout_ticks
        player = FakePlayer(
            name="Player",
            resources={r: 10000 for r in RESOURCE_TYPES},
        )
        player.db.combat_lockout_tick = lockout_until

        tile = FakeTile(xyz=(0, 0, "earth"))
        player.location = tile

        # Building system checks lockout at current_tick
        # If lockout_until > check_tick, build is rejected
        check_tick = current_tick  # Same tick as combat
        building_system = BuildingSystem(
            registry=registry,
            event_bus=EventBus(),
            create_building_func=lambda d, t, o: None,
            build_range=1000,
            current_tick_func=lambda: check_tick,
        )

        ok, msg = building_system.construct(player, tile, "HQ")
        self.assertFalse(ok)
        self.assertIn("combat", msg.lower())

# -------------------------------------------------------------- #
#  Property 16: Attack resolution ordering
#  **Validates: Requirements 6.9**
# -------------------------------------------------------------- #

class TestProperty16FIFOOrdering(unittest.TestCase):
    """Property 16: Attack resolution ordering.

    For any set of attack actions queued during a game tick, the combat
    engine SHALL resolve them in the order they were received (FIFO).

    **Validates: Requirements 6.9**
    """

    @given(
        num_attackers=st.integers(min_value=2, max_value=10),
        weapon_damage=st.integers(min_value=1, max_value=20),
    )
    @settings(max_examples=100)
    def test_fifo_order_preserved(self, num_attackers, weapon_damage):
        """Attacks resolve in FIFO order — damage accumulates sequentially."""
        engine, _ = _make_engine()
        tile = FakeTile(xyz=(0, 0, "earth"))
        target_hp = num_attackers * weapon_damage + 100  # Enough to survive
        target = FakePlayer(
            name="Target", hp=target_hp, hp_max=target_hp, location=tile,
        )

        resolve_order = []
        original_resolve = engine.resolve_tick

        # Track the order events are published
        events = []
        engine.event_bus.subscribe(
            "combat_action",
            lambda **kw: events.append(kw["attacker"].key),
        )

        attackers = []
        for i in range(num_attackers):
            weapon = FakeWeapon(damage=weapon_damage, weapon_range=10)
            a = FakePlayer(
                name=f"Attacker_{i}", weapon=weapon, location=tile,
            )
            attackers.append(a)
            engine.queue_attack(a, target)

        engine.resolve_tick()

        # Events should be in the same order as queue order
        expected_order = [f"Attacker_{i}" for i in range(num_attackers)]
        self.assertEqual(events, expected_order)

        # Total damage should be num_attackers * weapon_damage
        expected_hp = target_hp - (num_attackers * weapon_damage)
        self.assertEqual(target.db.hp, expected_hp)

    @given(
        num_actions=st.integers(min_value=1, max_value=8),
        data=st.data(),
    )
    @settings(max_examples=100)
    def test_all_queued_actions_resolved(self, num_actions, data):
        """All queued actions are resolved in a single tick."""
        engine, _ = _make_engine()
        tile = FakeTile(xyz=(0, 0, "earth"))

        events = []
        engine.event_bus.subscribe(
            "combat_action", lambda **kw: events.append(kw),
        )

        for i in range(num_actions):
            damage = data.draw(st.integers(min_value=1, max_value=10))
            weapon = FakeWeapon(damage=damage, weapon_range=10)
            attacker = FakePlayer(
                name=f"A_{i}", weapon=weapon, location=tile,
            )
            target = FakePlayer(
                name=f"T_{i}", hp=1000, hp_max=1000, location=tile,
            )
            engine.queue_attack(attacker, target)

        engine.resolve_tick()

        # All actions should have been resolved
        self.assertEqual(len(events), num_actions)
        # Pending actions should be empty
        self.assertEqual(len(engine.pending_actions), 0)

# -------------------------------------------------------------- #
#  Typed-weapon / gear fakes for the equipment combat touches
#  (tasks 4.1-4.2): weapon typing + magazine + aggregated gear stats.
# -------------------------------------------------------------- #

class _WeaponDB:
    """A tiny ``.db`` bag exposing a mutable ``loaded`` count."""

    def __init__(self, loaded=None):
        self.loaded = loaded


class FakeTypedWeapon:
    """Weapon GameItem stand-in carrying weapon_type + a magazine."""

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


# Distinct non-weapon slots to spread aggregated gear across (one item/slot).
_GEAR_SLOTS = [
    "head", "eyes", "torso", "legs", "boots",
    "gloves", "accessory", "back", "shoulders", "hands",
]

# -------------------------------------------------------------- #
#  Property 6: Melee range
#  **Validates: Requirements 4.2, 4.4**
# -------------------------------------------------------------- #

class TestProperty6MeleeRange(unittest.TestCase):
    """Property 6: Melee range.

    A melee weapon's effective attack range is always 1, regardless of any
    ``range`` stat on the item, and a melee attack never consumes ammo.

    **Validates: Requirements 4.2, 4.4**
    """

    @given(
        range_stat=st.integers(min_value=1, max_value=20),
        dx=st.integers(min_value=0, max_value=10),
        dy=st.integers(min_value=0, max_value=10),
        loaded=st.integers(min_value=0, max_value=30),
    )
    @settings(max_examples=200)
    def test_melee_effective_range_is_one(self, range_stat, dx, dy, loaded):
        assume(dx + dy > 0)  # distinct tiles so attacker is not the target
        weapon = FakeTypedWeapon(
            weapon_type="melee", damage=25, weapon_range=range_stat,
            ammo_type="rifle_rounds", ammo_per_shot=3,
            magazine_size=30, loaded=loaded,
        )
        engine, _ = _make_engine()
        attacker = FakePlayer(name="Attacker", weapon=weapon,
                              location=FakeTile(xyz=(0, 0, "earth")))
        target = FakePlayer(name="Target",
                            location=FakeTile(xyz=(dx, dy, "earth")))

        ok, _ = engine.queue_attack(attacker, target)

        # Success iff within an effective range of 1, ignoring range_stat.
        self.assertEqual(ok, (dx + dy) <= 1)
        # Melee never touches the magazine, whether it hit or missed.
        self.assertEqual(weapon.db.loaded, loaded)


# -------------------------------------------------------------- #
#  Property 7: Magazine draw conservation
#  **Validates: Requirements 5.3, 5.4, 5.5, 5.6**
# -------------------------------------------------------------- #

class TestProperty7MagazineDraw(unittest.TestCase):
    """Property 7: Magazine draw conservation.

    A ranged shot decrements ``db.loaded`` by exactly ``ammo_per_shot``;
    rounds are conserved (loaded_before == loaded_after + rounds_fired). An
    empty magazine (loaded < ammo_per_shot) rejects the attack and does NOT
    mutate ``db.loaded``.

    **Validates: Requirements 5.3, 5.4, 5.5, 5.6**
    """

    @given(
        magazine_size=st.integers(min_value=1, max_value=60),
        loaded=st.integers(min_value=0, max_value=60),
        ammo_per_shot=st.integers(min_value=1, max_value=10),
    )
    @settings(max_examples=200)
    def test_shot_draws_exactly_ammo_per_shot_or_rejects(
        self, magazine_size, loaded, ammo_per_shot
    ):
        assume(loaded <= magazine_size)
        weapon = FakeTypedWeapon(
            weapon_type="ranged", damage=25, weapon_range=5,
            ammo_type="rifle_rounds", ammo_per_shot=ammo_per_shot,
            magazine_size=magazine_size, loaded=loaded,
        )
        engine, event_bus = _make_engine()
        received = []
        from mygame.world.event_bus import PLAYER_NOTIFICATION
        event_bus.subscribe(
            PLAYER_NOTIFICATION,
            lambda player, kind, data, **_kw: received.append(kind),
        )
        attacker = FakePlayer(name="Attacker", weapon=weapon,
                              location=FakeTile(xyz=(0, 0, "earth")))
        target = FakePlayer(name="Target",
                            location=FakeTile(xyz=(1, 0, "earth")))

        ok, _ = engine.queue_attack(attacker, target)

        if loaded >= ammo_per_shot:
            self.assertTrue(ok)
            # Exactly ammo_per_shot drawn; rounds conserved.
            self.assertEqual(weapon.db.loaded, loaded - ammo_per_shot)
            self.assertEqual(weapon.db.loaded + ammo_per_shot, loaded)
        else:
            self.assertFalse(ok)
            # Rejected attack never mutates the magazine.
            self.assertEqual(weapon.db.loaded, loaded)
            self.assertEqual(len(engine.pending_actions), 0)
            self.assertIn("out_of_ammo", received)


# -------------------------------------------------------------- #
#  Property 3: Damage-bonus aggregation
#  **Validates: Requirements 2.3**
# -------------------------------------------------------------- #

class TestProperty3DamageBonusAggregation(unittest.TestCase):
    """Property 3: Damage-bonus aggregation.

    Attacker damage includes the sum of ``damage_bonus`` across all equipped
    gear (plus active powerups).

    **Validates: Requirements 2.3**
    """

    @given(
        weapon_damage=st.integers(min_value=1, max_value=200),
        bonuses=st.lists(st.integers(min_value=0, max_value=20),
                         min_size=0, max_size=len(_GEAR_SLOTS)),
        powerup_bonus=st.integers(min_value=0, max_value=30),
    )
    @settings(max_examples=200)
    def test_damage_includes_sum_of_gear_damage_bonus(
        self, weapon_damage, bonuses, powerup_bonus
    ):
        weapon = FakeTypedWeapon(
            weapon_type="ranged", damage=weapon_damage, weapon_range=10,
            ammo_type=None, key="rifle",
        )
        engine, _ = _make_engine()
        attacker = FakePlayer(name="Attacker", weapon=weapon,
                              location=FakeTile(xyz=(0, 0, "earth")))
        for slot, value in zip(_GEAR_SLOTS, bonuses):
            attacker.equipment.equip(FakeGear(slot, "damage_bonus", value))
        if powerup_bonus:
            attacker.db.active_powerups = {
                "buff": {"effect": {"effect_type": "damage_bonus",
                                    "effect_value": powerup_bonus}},
            }

        target_hp = 1_000_000  # never defeated, so no respawn masking
        target = FakePlayer(name="Target", hp=target_hp, hp_max=target_hp,
                            location=FakeTile(xyz=(1, 0, "earth")))

        engine.queue_attack(attacker, target)
        engine.resolve_tick()

        expected_damage = max(
            0, weapon_damage + sum(bonuses) + powerup_bonus
        )
        self.assertEqual(target.db.hp, target_hp - expected_damage)


# -------------------------------------------------------------- #
#  Property 2: Armor aggregation invariance
#  **Validates: Requirements 2.2, 14.1**
# -------------------------------------------------------------- #

class TestProperty2ArmorAggregation(unittest.TestCase):
    """Property 2: Armor aggregation invariance.

    Target ``damage_reduction`` is the sum over all equipped gear, and the
    damage formula (weapon_damage - reduction, min 0) is unchanged.

    **Validates: Requirements 2.2, 14.1**
    """

    @given(
        weapon_damage=st.integers(min_value=1, max_value=200),
        reductions=st.lists(st.integers(min_value=0, max_value=20),
                            min_size=0, max_size=len(_GEAR_SLOTS)),
    )
    @settings(max_examples=200)
    def test_reduction_is_sum_over_all_gear(self, weapon_damage, reductions):
        weapon = FakeTypedWeapon(
            weapon_type="ranged", damage=weapon_damage, weapon_range=10,
            ammo_type=None, key="rifle",
        )
        engine, _ = _make_engine()
        attacker = FakePlayer(name="Attacker", weapon=weapon,
                              location=FakeTile(xyz=(0, 0, "earth")))

        target_hp = 1_000_000
        target = FakePlayer(name="Target", hp=target_hp, hp_max=target_hp,
                            location=FakeTile(xyz=(1, 0, "earth")))
        for slot, value in zip(_GEAR_SLOTS, reductions):
            target.equipment.equip(FakeGear(slot, "damage_reduction", value))

        engine.queue_attack(attacker, target)
        engine.resolve_tick()

        expected_damage = max(0, weapon_damage - sum(reductions))
        self.assertEqual(target.db.hp, target_hp - expected_damage)


if __name__ == "__main__":
    unittest.main()
