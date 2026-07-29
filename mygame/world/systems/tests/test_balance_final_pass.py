"""
Final balance pass — item-loot-economy task 6.5.

Real-data verification of the feature's binding balance rules (design §9 +
the combat-rebalance steering principles), all against the SHIPPED data
files (items.yaml, affixes.yaml, buildings.yaml, technologies.yaml,
balance.yaml) and the REAL engine paths:

1. **Worst-case range stack ≤ cap** — a max-rolled sniper_rifle + "of
   Reach" affix + Extended Barrel insert + L5 Sniper Nest + Ballistics
   Optimization stacks past ``max_weapon_range`` and is clamped by the
   real ``CombatEngine._resolve_weapon_range``. The cap is also sanity-
   checked against the shipped planet sizes (never a whole-map sniper).

2. **God-roll set vs new player (the ~2× principle)** — using the real
   ``_calculate_damage`` math: the chip floor bounds armor at ~2×
   effective HP (a fresh player is never immunity-walled by a god-roll
   tank), same-class god-roll offense stays within the ~2× band of the
   crafted floor, and neither side one-shots the other.

3. **Rarity cadence** — the SHIPPED ``balance.rarity_table`` produces the
   design §9 cadence end-to-end through ``roll_item`` (guard kills mostly
   common; citadel epic ≈ 40% / legendary ≈ 15%, generous bands).

4. **Full loop integration** — craft → roll → guard-kill drop → salvage →
   reroll → insert, exercised through the real EquipmentSystem /
   BaseEliminationHandler / loot roller / CombatEngine range read.

Validates: Requirements 8.3, 3.2, 7.1, 6.1 and the steering principles
("never ~2× without counterplay", "always a counter, both ways").
"""

import math
import os
import random
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

from mygame.world.constants import MAX_LEVEL  # noqa: E402
from mygame.world.data_registry import DataRegistry  # noqa: E402
from mygame.world.event_bus import EventBus  # noqa: E402
from mygame.world.systems.combat_engine import CombatEngine  # noqa: E402
from mygame.world.systems.equipment_handler import EquipmentHandler  # noqa: E402
from mygame.world.systems.loot_roller import (  # noqa: E402
    RARITY_ORDER,
    roll_item,
)

# -------------------------------------------------------------- #
#  Shared real-data registry (loaded once per module)
# -------------------------------------------------------------- #

_REAL_REGISTRY = None


def _real_registry():
    """Load (once) and share the real data-file registry across classes."""
    global _REAL_REGISTRY
    if _REAL_REGISTRY is None:
        data_dir = os.path.normpath(os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..", "..", "..", "data",
        ))
        _REAL_REGISTRY = DataRegistry()
        _REAL_REGISTRY.load_all(data_dir)
    return _REAL_REGISTRY


# -------------------------------------------------------------- #
#  Fakes (GameItem / player / building doubles with real handlers)
# -------------------------------------------------------------- #

class _Bag:
    """A tiny attribute bag standing in for an Evennia ``.db`` proxy."""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class RolledItem:
    """A GameItem double whose ``get_stat`` mirrors the production read.

    Exactly the ``typeclasses.objects.GameItem.get_stat`` semantics: a
    per-instance rolled value (``db.rolled_stats``) takes precedence over
    the def base in ``stat_modifiers``, and affix magnitudes targeting the
    same stat axis add on top — so rolled/affixed values flow into
    ``_resolve_weapon_range`` / ``get_stat_total`` as in production.
    """

    def __init__(self, item_def):
        self.key = item_def.key
        self.name = item_def.name
        self.slot = getattr(item_def, "slot", "") or ""
        self.category = getattr(item_def, "category", None)
        self.weapon_type = getattr(item_def, "weapon_type", None)
        self.required_rank = getattr(item_def, "required_rank", None)
        self.stat_modifiers = dict(getattr(item_def, "stat_modifiers", None)
                                   or {})
        self.ammo_cost = None
        self.item_def = item_def
        self.db = _Bag()
        self._deleted = False
        self._container = None  # list to remove self from on delete()

    def get_stat(self, stat_name, default=0):
        rolled = getattr(self.db, "rolled_stats", None)
        base = None
        if rolled is not None and hasattr(rolled, "get"):
            value = rolled.get(stat_name)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                base = float(value)
        if base is None:
            value = self.stat_modifiers.get(stat_name, default)
            try:
                base = float(value)
            except (TypeError, ValueError):
                base = float(default)
        affix_total = 0.0
        for affix in (getattr(self.db, "affixes", None) or []):
            if hasattr(affix, "get") and affix.get("stat") == stat_name:
                magnitude = affix.get("magnitude")
                if isinstance(magnitude, (int, float)) and not isinstance(
                    magnitude, bool
                ):
                    affix_total += float(magnitude)
        return base + affix_total

    def delete(self):
        self._deleted = True
        if self._container is not None and self in self._container:
            self._container.remove(self)


class FakePlayer:
    """A player double with a REAL EquipmentHandler + resource/salvage pool."""

    def __init__(self, name="TestPlayer", level=100, hp=100, hp_max=100,
                 resources=None, oid=None, location=None):
        self.key = name
        self.db = _Bag(
            level=level, hp=hp, hp_max=hp_max, combat_xp=0,
            resources=dict(resources or {}), tech_bonuses={},
            active_powerups={}, salvage=0,
        )
        self.equipment = EquipmentHandler(self)
        self.contents = []
        self.location = location
        self._messages = []
        if oid is not None:
            self.id = oid
        loc_db = getattr(location, "db", None)
        if loc_db is not None:
            self.db.coord_x = getattr(loc_db, "coord_x", None)
            self.db.coord_y = getattr(loc_db, "coord_y", None)

    def get_resource(self, resource):
        return int(self.db.resources.get(str(resource).title(), 0))

    def add_resource(self, resource, amount):
        key = str(resource).title()
        self.db.resources[key] = self.db.resources.get(key, 0) + int(amount)

    def has_resources(self, costs):
        return all(self.db.resources.get(str(r).title(), 0) >= amt
                   for r, amt in costs.items())

    def deduct_resources(self, costs):
        if not self.has_resources(costs):
            return False
        for r, amt in costs.items():
            key = str(r).title()
            self.db.resources[key] = self.db.resources.get(key, 0) - int(amt)
        return True

    # Salvage currency accessors (mirrors CombatCharacter).
    def get_salvage(self):
        return int(getattr(self.db, "salvage", 0) or 0)

    def add_salvage(self, amount):
        self.db.salvage = max(0, self.get_salvage() + int(amount))

    def spend_salvage(self, amount):
        amount = int(amount)
        if amount < 0:
            return False
        balance = self.get_salvage()
        if balance < amount:
            return False
        self.db.salvage = balance - amount
        return True

    def msg(self, text):
        self._messages.append(text)


class FakeAttributes:
    """Simulates Evennia's Attribute handler (for building doubles)."""

    def __init__(self, data=None):
        self._data = data or {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def add(self, key, value):
        self._data[key] = value


class FakeBuilding:
    """A building double readable by the real capability/operational utils."""

    def __init__(self, building_type, owner=None, level=1, offline=False,
                 under_construction=False):
        self.key = building_type
        self.attributes = FakeAttributes({
            "building_type": building_type, "owner": owner,
            "building_level": level, "offline": offline,
            "under_construction": under_construction,
            "hp": 300, "hp_max": 300,
        })
        # A db view for readers that go through get_obj_attr/db.
        self.db = _Bag(building_type=building_type, owner=owner,
                       building_level=level, offline=offline,
                       under_construction=under_construction)

    @property
    def owner(self):
        return self.attributes.get("owner")

    @property
    def is_offline(self):
        return bool(self.attributes.get("offline", default=False))


class NestRoom:
    """A room double answering get_buildings_at (the on-tile aura read)."""

    def __init__(self, planet="earth"):
        self._planet = planet
        self._buildings_at = {}
        self.db = _Bag(coord_x=0, coord_y=0)

    def place(self, x, y, building):
        self._buildings_at.setdefault((x, y), []).append(building)

    def get_buildings_at(self, x, y):
        return list(self._buildings_at.get((x, y), []))

    @property
    def planet_name(self):
        return self._planet


def _player_on_tile(room, x, y, oid=1, name="P"):
    """A player standing at (x, y) of *room* (coords on db, as live players)."""
    player = FakePlayer(name=name, oid=oid, location=room)
    player.db.coord_x = x
    player.db.coord_y = y
    return player


def _engine(registry):
    return CombatEngine(registry, EventBus(), current_tick_func=lambda: 0)


def _affix_band_max(registry, pool, key):
    """The magnitude band max of the *key* affix in *pool* (real data)."""
    for entry in registry.affixes.get(pool, []):
        if entry.get("key") == key:
            return float(entry["max"])
    raise AssertionError(f"affix {key!r} missing from the {pool!r} pool")


# -------------------------------------------------------------- #
#  1. Worst-case range stack ≤ cap (design §9, R8.3)
# -------------------------------------------------------------- #

class TestWorstCaseRangeStack(unittest.TestCase):
    """The worst legal range stack clamps to ``max_weapon_range`` (§9).

    Every term comes from the SHIPPED data files: the sniper_rifle loot
    band max (13), the "of Reach" weapon-affix band max (+3), the
    Extended Barrel insert (+2 into rolled_stats, exactly what
    ``apply_insert`` writes), a level-5 Sniper Nest on the attacker's own
    tile (+3 via the real ``_tile_range_bonus``), and Ballistics
    Optimization researched through the real TechLabSystem (+1). The raw
    sum exceeds the cap and the REAL ``_resolve_weapon_range`` clamps it.

    Validates: Requirements 8.1, 8.3
    """

    @classmethod
    def setUpClass(cls):
        cls.registry = _real_registry()

    def _god_stack(self):
        """Build (player-on-nest, weapon) carrying the full worst-case stack."""
        registry = self.registry
        sniper_def = registry.items["sniper_rifle"]
        band_max = float(sniper_def.roll_spec["stats"]["range"]["max"])
        reach_max = _affix_band_max(registry, "weapon", "long")
        insert_val = float(
            registry.items["extended_barrel"].insert_effect["value"])

        weapon = RolledItem(sniper_def)
        # Max roll + Extended Barrel: apply_insert bumps rolled_stats["range"].
        weapon.db.rolled_stats = {"range": band_max + insert_val,
                                  "damage": 60.0}
        weapon.db.affixes = [{"key": "long", "name": "of Reach",
                              "stat": "range", "magnitude": reach_max,
                              "value": 14.0}]
        weapon.db.inserts = [{"key": "extended_barrel",
                              "effect": {"type": "range",
                                         "value": insert_val}}]

        # Player on their own operational L5 Sniper Nest tile (real SN def).
        self.assertIn("SN", registry.buildings,
                      "Sniper Nest def missing from buildings.yaml")
        self.assertIn("range_aura", registry.buildings["SN"].capabilities)
        room = NestRoom()
        player = _player_on_tile(room, 2, 3, oid=1)
        room.place(2, 3, FakeBuilding("SN", owner=player, level=5))
        player.equipment.equip(weapon)

        # Ballistics Optimization researched end-to-end (real tech def).
        from mygame.world.systems.tech_system import TechLabSystem
        tech = TechLabSystem(registry, EventBus())
        for res, amt in registry.technologies[
                "ballistics_optimization"].resource_cost.items():
            player.add_resource(res, amt)
        ok, msg = tech.start_research(player, "ballistics_optimization")
        self.assertTrue(ok, f"start_research failed: {msg}")
        for _ in range(registry.technologies[
                "ballistics_optimization"].research_ticks):
            tech.process_tick()
        self.assertEqual(player.db.tech_bonuses.get("weapon_range"), 1.0)

        raw = band_max + insert_val + reach_max + 3 + 1  # nest L5 = +3
        return player, weapon, raw

    def test_stack_exceeds_cap_and_is_clamped(self):
        """13 (roll) + 2 (insert) + 3 (affix) + 3 (L5 nest) + 1 (tech) = 22
        raw → clamped to max_weapon_range (16) by the real resolver."""
        player, weapon, raw = self._god_stack()
        engine = _engine(self.registry)

        cap = int(self.registry.balance.max_weapon_range)
        self.assertGreater(cap, 0)
        # The stack genuinely overflows the cap (else this test is vacuous).
        self.assertGreater(raw, cap,
                           f"worst-case stack {raw} no longer exceeds the "
                           f"cap {cap} — the clamp is untested")
        # Nest term sanity: L5 grants exactly +3 through the real read.
        self.assertEqual(engine._tile_range_bonus(player), 3)
        # The single R8 resolver clamps the whole stack.
        self.assertEqual(engine._resolve_weapon_range(player, weapon), cap)

    def test_stack_without_positional_terms_stays_under_cap(self):
        """Off the nest, the PORTABLE stack (roll + insert + affix + tech =
        19 → still clamped; without the affix, 16 = exactly at cap): the
        big range numbers are positional, per design §7."""
        player, weapon, _raw = self._god_stack()
        player.db.coord_x, player.db.coord_y = 9, 9  # step off the nest
        engine = _engine(self.registry)
        cap = int(self.registry.balance.max_weapon_range)
        self.assertEqual(engine._tile_range_bonus(player), 0)
        self.assertLessEqual(engine._resolve_weapon_range(player, weapon), cap)

    def test_cap_is_sane_against_planet_sizes(self):
        """max_weapon_range must never approach a whole-map (or whole-
        viewport) sniper: ≤ 1/8 of the smallest shipped planet edge and
        under the ~30-tile viewport (planets.yaml sizing comment).

        Finding (documented): cap 16 vs smallest planet 200×200 (8% of an
        edge) and roughly half the ~30-tile viewport — a positional
        strongpoint, not a global gun. No tuning needed.
        """
        from mygame.world.coordinate.planet_registry import PlanetRegistry

        cap = int(self.registry.balance.max_weapon_range)
        planets_path = os.path.normpath(os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..", "..", "..", "data", "definitions", "planets.yaml",
        ))
        planet_registry = PlanetRegistry()
        planet_registry.load_from_yaml(planets_path)
        keys = planet_registry.list_planets()
        self.assertTrue(keys, "no planets loaded from planets.yaml")
        min_edge = min(
            min(int(planet_registry.get_space(k).width),
                int(planet_registry.get_space(k).height))
            for k in keys)
        self.assertGreaterEqual(min_edge, 8 * cap,
                                f"cap {cap} is more than 1/8 of the "
                                f"smallest planet edge {min_edge}")
        self.assertLess(cap, 30, "cap reaches across the whole viewport")


# -------------------------------------------------------------- #
#  2. God-roll gear set vs new player (the ~2× principle)
# -------------------------------------------------------------- #

class TestGodRollVsNewPlayer(unittest.TestCase):
    """A maxed player must never be unbeatable by a fresh one (steering).

    **Metrics (documented per task 6.5):** per-hit damage through the real
    ``_calculate_damage`` (mitigation + chip floor included) and effective
    HP (hits-to-kill at 100 HP). The binding bounds:

    - *Defense axis:* the chip floor (``chip_damage_min_fraction`` 0.5)
      guarantees any landed hit deals ≥ 50% of its raw output, so a
      god-roll armor stack (max power_armor + "of the Bulwark" + the +6
      DR tech cap) multiplies effective HP by AT MOST 2× — the fresh
      player is never immunity-walled (Requirements/steering "skill beats
      progression").
    - *Offense axis:* a god-roll weapon of the SAME class as the fresh
      player's crafted-floor weapon (loot band max + "of Power" + the +6
      damage tech cap) stays within a generous ~2× band (≤ 2.5×) of the
      crafted floor's raw output.
    - *Counterplay floor:* neither side one-shots the other; the fresh
      player kills the full god-roll set in a bounded number of hits.

    The remaining asymmetry (weapon TIER, e.g. sniper vs assault rifle)
    is rank-gated progression whose counter is the death drop: the whole
    god-roll set is loseable (R1.6) — the steering doc's counterplay.

    Validates: steering "never ~2× without counterplay"; Requirements 6.2
    """

    HP = 100

    @classmethod
    def setUpClass(cls):
        cls.registry = _real_registry()

    # ---------------- builds ---------------- #

    def _god_player(self):
        """Max-rolled sniper_rifle + power_armor, strong affixes, +6 techs."""
        r = self.registry
        player = FakePlayer(name="God", oid=1)

        sniper = RolledItem(r.items["sniper_rifle"])
        dmg_max = float(
            r.items["sniper_rifle"].roll_spec["stats"]["damage"]["max"])
        rng_max = float(
            r.items["sniper_rifle"].roll_spec["stats"]["range"]["max"])
        sniper.db.rolled_stats = {"damage": dmg_max, "range": rng_max}
        sniper.db.affixes = [
            {"key": "keen", "name": "of Power", "stat": "damage_bonus",
             "magnitude": _affix_band_max(r, "weapon", "keen"), "value": 10.0},
        ]

        armor = RolledItem(r.items["power_armor"])
        dr_max = float(
            r.items["power_armor"].roll_spec["stats"]["damage_reduction"]
            ["max"])
        armor.db.rolled_stats = {"damage_reduction": dr_max}
        armor.db.affixes = [
            {"key": "sturdy", "name": "of the Bulwark",
             "stat": "damage_reduction",
             "magnitude": _affix_band_max(r, "armor", "sturdy"),
             "value": 10.0},
        ]

        player.equipment.equip(sniper)
        player.equipment.equip(armor)
        # Permanent bonuses AT the +6 caps (perm_bonus_cap_damage/dr).
        player.db.tech_bonuses = {
            "damage": float(r.balance.perm_bonus_cap_damage),
            "damage_reduction": float(r.balance.perm_bonus_cap_dr),
        }
        return player, sniper

    def _fresh_player(self):
        """Starter gear at the crafted floor: assault_rifle + kevlar_vest."""
        r = self.registry
        player = FakePlayer(name="Fresh", oid=2)

        rifle = RolledItem(r.items["assault_rifle"])
        craft = r.items["assault_rifle"].roll_spec["craft"]
        rifle.db.rolled_stats = {
            "damage": float(craft["damage"]["min"]),
            "range": float(craft["range"]["min"]),
        }
        vest = RolledItem(r.items["kevlar_vest"])
        vest.db.rolled_stats = {"damage_reduction": float(
            r.items["kevlar_vest"].roll_spec["craft"]["damage_reduction"]
            ["min"])}

        player.equipment.equip(rifle)
        player.equipment.equip(vest)
        return player, rifle

    @staticmethod
    def _hits_to_kill(damage_per_hit, hp):
        return math.inf if damage_per_hit <= 0 else math.ceil(
            hp / damage_per_hit)

    # ---------------- the bounds ---------------- #

    def test_chip_floor_keeps_god_roll_tank_beatable(self):
        """Defense ≤ ~2×: the fresh player's hit into the FULL god armor
        stack always lands ≥ 50% of raw, so the god set's effective HP is
        at most 2× an unarmored target's — never an immunity wall."""
        engine = _engine(self.registry)
        god, _sniper = self._god_player()
        fresh, rifle = self._fresh_player()

        raw = rifle.get_stat("damage")  # crafted floor, no bonuses
        dealt = engine._calculate_damage(fresh, god, rifle)
        chip = float(self.registry.balance.chip_damage_min_fraction)
        self.assertGreater(chip, 0.0, "chip floor disabled — immunity walls "
                                      "are possible again")
        # The god stack's DR exceeds the fresh raw — only the chip floor
        # keeps damage flowing (i.e. this scenario genuinely binds).
        self.assertGreater(engine._get_target_armor_reduction(god), raw)
        self.assertEqual(dealt, math.ceil(raw * chip))

        naked = FakePlayer(name="Naked", oid=3)
        dealt_naked = engine._calculate_damage(fresh, naked, rifle)
        hits_god = self._hits_to_kill(dealt, self.HP)
        hits_naked = self._hits_to_kill(dealt_naked, self.HP)
        self.assertLessEqual(
            hits_god, 2 * hits_naked,
            f"god armor multiplies effective HP {hits_god}/{hits_naked} — "
            f"past the 2× chip-floor bound")

    def test_same_class_god_weapon_within_two_x_band(self):
        """Offense ≤ ~2×: a god-roll assault_rifle (loot max + max "of
        Power" + the +6 damage tech cap) vs the crafted-floor assault
        rifle, measured into a common unarmored target: within 2.5×
        (measured ≈ 2.1× at the shipped numbers — the design's band)."""
        r = self.registry
        engine = _engine(r)
        target = FakePlayer(name="Dummy", oid=9)

        god = FakePlayer(name="GodAR", oid=4)
        god_rifle = RolledItem(r.items["assault_rifle"])
        god_rifle.db.rolled_stats = {"damage": float(
            r.items["assault_rifle"].roll_spec["stats"]["damage"]["max"])}
        god_rifle.db.affixes = [
            {"key": "keen", "name": "of Power", "stat": "damage_bonus",
             "magnitude": _affix_band_max(r, "weapon", "keen"),
             "value": 10.0}]
        god.equipment.equip(god_rifle)
        god.db.tech_bonuses = {
            "damage": float(r.balance.perm_bonus_cap_damage)}

        fresh, rifle = self._fresh_player()

        god_hit = engine._calculate_damage(god, target, god_rifle)
        fresh_hit = engine._calculate_damage(fresh, target, rifle)
        self.assertGreater(fresh_hit, 0)
        ratio = god_hit / fresh_hit
        self.assertGreater(ratio, 1.0)  # progression still matters
        self.assertLessEqual(
            ratio, 2.5,
            f"same-class god-roll offense is {ratio:.2f}× the crafted "
            f"floor — outside the ~2× band (design §9: tune the bands)")

    def test_no_one_shot_and_bounded_fight_both_ways(self):
        """Counterplay floor: the maxed sniper build cannot one-shot the
        fresh player, and the fresh player kills the full god set within
        a bounded hit count (2× the unarmored count — the chip bound)."""
        engine = _engine(self.registry)
        god, sniper = self._god_player()
        fresh, rifle = self._fresh_player()

        god_hit = engine._calculate_damage(god, fresh, sniper)
        fresh_hit = engine._calculate_damage(fresh, god, rifle)

        self.assertLess(god_hit, self.HP, "the god build one-shots a fresh "
                                          "player — no counterplay window")
        self.assertGreater(fresh_hit, 0, "the fresh player cannot damage "
                                         "the god build at all")
        hits = self._hits_to_kill(fresh_hit, self.HP)
        self.assertLessEqual(hits, 2 * self._hits_to_kill(
            rifle.get_stat("damage"), self.HP))


# -------------------------------------------------------------- #
#  2b. Agent-level yield — the SHIPPED tunables vs the ~2× line
# -------------------------------------------------------------- #

class TestShippedAgentYieldTunables(unittest.TestCase):
    """The agent-level yield bonus, as the running game loads it.

    The unit tests in ``test_agent_system`` exercise this multiplier against a
    bare ``DataRegistry()`` — i.e. the ``BalanceConfig`` dataclass DEFAULTS.
    The shipped ``balance.yaml`` overrides those defaults at runtime
    (``DataRegistry._build_balance`` prefers the yaml value whenever the key is
    present), so without this class the number the game actually uses is
    asserted by nothing: raising ``agent_level_yield_cap`` in balance.yaml past
    the design's ~2× line stayed green across the whole suite.
    """

    def setUp(self):
        self.balance = _real_registry().balance

    def _multiplier(self, effective_level):
        """The real formula from ``get_level_yield_multiplier``, on shipped values."""
        levels = max(0, effective_level - 1)
        return 1.0 + min(
            self.balance.agent_level_yield_bonus * levels,
            self.balance.agent_level_yield_cap,
        )

    def test_shipped_veteran_stays_under_the_two_x_line(self):
        """A max-level agent's economic edge respects the binding principle."""
        veteran = self._multiplier(MAX_LEVEL)

        self.assertLess(
            veteran, 2.0,
            f"a max-level agent produces {veteran:.2f}× a fresh one from the "
            f"SHIPPED balance.yaml — progression bonuses must stay under the "
            f"~2× line (design: 'never ~2× without counterplay')")

    def test_shipped_bonus_is_a_slow_sweetener(self):
        """The per-level rate stays well below the chosen-investment rate.

        Agent level accrues passively, whereas an Extractor upgrade is a
        resource investment the player elects to make — so the agent's share of
        the yield must remain the smaller term of the two.
        """
        self.assertGreater(self.balance.agent_level_yield_bonus, 0.0)
        self.assertLess(
            self.balance.agent_level_yield_bonus,
            self.balance.extractor_level_bonus,
            "agent level must not out-earn a deliberate Extractor upgrade")

    def test_shipped_cap_actually_binds(self):
        """The cap is reachable — it is a real ceiling, not dead config.

        A cap above ``bonus × (MAX_LEVEL - 1)`` would never engage, leaving the
        2× line unguarded no matter what the cap says.
        """
        uncapped = self.balance.agent_level_yield_bonus * (MAX_LEVEL - 1)

        self.assertLess(
            self.balance.agent_level_yield_cap, uncapped,
            "agent_level_yield_cap can never be reached, so it guards nothing")


# -------------------------------------------------------------- #
#  3. Rarity cadence — the SHIPPED balance.rarity_table (design §9)
# -------------------------------------------------------------- #

class TestRarityCadenceRealData(unittest.TestCase):
    """End-to-end cadence check on the REAL ``balance.rarity_table``.

    Task 2.2 covered the pure-module DEFAULT_RARITY_TABLE statistically;
    this is the real-data pass: seeded rolls through ``roll_item`` with
    the shipped table, the shipped sniper_rifle roll_spec, and the shipped
    affix pools. Design §9 cadence, generous bands: guard kills mostly
    common (never legendary); citadel epic ≈ 40%, legendary ≈ 15%.

    Validates: Requirements 3.2
    """

    N = 4000
    SEED = 20260724

    @classmethod
    def setUpClass(cls):
        cls.registry = _real_registry()

    def _rarity_counts(self, source_weight):
        r = self.registry
        rng = random.Random(self.SEED)
        item_def = r.items["sniper_rifle"]
        counts = {name: 0 for name in RARITY_ORDER}
        for _ in range(self.N):
            result = roll_item(
                item_def, source_rarity_weight=source_weight, rng=rng,
                default_skew=float(r.balance.loot_roll_skew),
                rarity_table=r.balance.rarity_table,
                affix_pools=r.affixes,
            )
            self.assertIsNotNone(result)
            self.assertIn(result.rarity, RARITY_ORDER)
            counts[result.rarity] += 1
        return counts

    def test_guard_kills_mostly_common(self):
        """Guard bucket (weight 0): > 60% common, ≤ 10% rare, 0 epic+."""
        counts = self._rarity_counts(0.0)
        self.assertGreater(counts["common"] / self.N, 0.60)
        self.assertLess(counts["rare"] / self.N, 0.10)
        self.assertEqual(counts["epic"], 0)
        self.assertEqual(counts["legendary"], 0)

    def test_citadel_cadence_matches_design(self):
        """Citadel bucket (weight 4): epic ≈ 40% (band 30–50%), legendary
        ≈ 15% (band 8–22%) — the §9 raid cadence, generous bands."""
        counts = self._rarity_counts(4.0)
        epic = counts["epic"] / self.N
        legendary = counts["legendary"] / self.N
        self.assertTrue(0.30 <= epic <= 0.50,
                        f"citadel epic rate {epic:.3f} outside [0.30, 0.50]")
        self.assertTrue(0.08 <= legendary <= 0.22,
                        f"citadel legendary rate {legendary:.3f} outside "
                        f"[0.08, 0.22]")

    def test_mean_rarity_rank_monotone_across_real_buckets(self):
        """guard < outpost < stronghold < fortress < citadel (real table)."""
        rank = {name: i for i, name in enumerate(RARITY_ORDER)}

        def mean_rank(counts):
            return sum(rank[r] * n for r, n in counts.items()) / self.N

        means = [mean_rank(self._rarity_counts(w))
                 for w in (0.0, 1.0, 2.0, 3.0, 4.0)]
        for lower, higher in zip(means, means[1:]):
            self.assertLess(lower, higher,
                            f"rarity means not monotone: {means}")


# -------------------------------------------------------------- #
#  4. Full-loop integration: craft → drop → salvage → reroll → insert
# -------------------------------------------------------------- #

class TestLootLoopIntegration(unittest.TestCase):
    """The whole economy loop against the real registry + real systems.

    craft (EquipmentSystem.craft, real AR catalog + craft band) → a
    guard-kill gear drop (BaseEliminationHandler._roll_guard_gear_drop →
    the real loot roller) → salvage the crafted piece (Blacksmith yield
    formula) → reroll the drop (Salvage + resource charge, loot band) →
    insert an Extended Barrel (weapon mutation read by the real
    ``_resolve_weapon_range``).

    Validates: Requirements 1.4, 3.6, 7.1, 4.5, 5.1, 8.1
    """

    SEED = 424242

    @classmethod
    def setUpClass(cls):
        cls.registry = _real_registry()

    def setUp(self):
        from mygame.world.systems.equipment_system import EquipmentSystem
        self.registry = _real_registry()
        self.created = []

        def factory(item_def, owner):
            item = RolledItem(item_def)
            item._container = owner.contents
            owner.contents.append(item)
            self.created.append(item)
            return item

        self.system = EquipmentSystem(self.registry, EventBus(),
                                      create_item_func=factory)
        self.system._rng = random.Random(self.SEED)
        self.player = FakePlayer(
            name="Looper", oid=1,
            resources={"Iron": 500, "Wood": 200, "Stone": 200,
                       "Energy": 200, "Circuits": 200})

    def _blacksmith(self, level=3):
        return FakeBuilding("BS", owner=self.player, level=level)

    def _craft_rifle(self):
        armory = FakeBuilding("AR", owner=self.player)
        ok = self.system.craft(self.player, "assault_rifle", armory)
        self.assertTrue(ok, "craft failed")
        rifle = self.created[-1]
        self.assertIn(rifle, self.player.contents)
        return rifle

    def _guard_drop(self, item_key="sniper_rifle"):
        """One forced guard-kill gear drop through the real handler."""
        from mygame.world.systems.base_elimination import (
            BaseEliminationHandler,
        )
        # base_elimination does `from typeclasses.objects import
        # spawn_gear_drop` at call time — patch THAT module instance.
        import typeclasses.objects as objects_mod

        handler = BaseEliminationHandler(self.registry, None)
        template = types.SimpleNamespace(
            guard_gear_drop_chance=1.0, gear_pool=[item_key])
        drops = []

        def _spawn_stub(location, item_def, x=None, y=None):
            drop = RolledItem(item_def)
            drops.append(drop)
            return drop

        original = getattr(objects_mod, "spawn_gear_drop", None)
        objects_mod.spawn_gear_drop = _spawn_stub
        # base_elimination rolls through the module-level `random`; seed it
        # so the drop (rarity/affixes) is stable across suite orderings.
        random.seed(self.SEED)
        try:
            handler._roll_guard_gear_drop(template, NestRoom(), 4, 6)
        finally:
            if original is not None:
                objects_mod.spawn_gear_drop = original
            else:
                del objects_mod.spawn_gear_drop
        self.assertEqual(len(drops), 1, "the forced guard drop never spawned")
        drop = drops[0]
        # The player picks it up (`get`).
        drop._container = self.player.contents
        self.player.contents.append(drop)
        return drop

    def test_full_loop(self):
        r = self.registry
        # --- 1. Craft: tight band, IQS stamped, NO affixes. Crafted-rarity
        # change (deviation from R6.1, per user request): the L1 armory's
        # craft_rarity_table row may assign common/uncommon (never rare at
        # L1, never epic+ at any level); affixes stay loot-only.
        rifle = self._craft_rifle()
        craft_band = r.items["assault_rifle"].roll_spec["craft"]
        rolled = rifle.db.rolled_stats
        for stat, band in craft_band.items():
            self.assertGreaterEqual(rolled[stat], float(band["min"]))
            self.assertLessEqual(rolled[stat], float(band["max"]))
        self.assertIsInstance(rifle.db.iqs, int)
        l1_tiers = set(r.balance.craft_rarity_table[1])  # {common, uncommon}
        self.assertIn(getattr(rifle.db, "rarity", None), l1_tiers | {None})
        self.assertIsNone(getattr(rifle.db, "affixes", None))

        # --- 2. Guard-kill drop: rolled in the loot band, rarity from the
        # lowest (guard_kill) bucket of the REAL table (R3.6).
        drop = self._guard_drop("sniper_rifle")
        loot_band = r.items["sniper_rifle"].roll_spec["stats"]
        for stat in ("damage", "range"):
            self.assertGreaterEqual(drop.db.rolled_stats[stat],
                                    float(loot_band[stat]["min"]))
            self.assertLessEqual(drop.db.rolled_stats[stat],
                                 float(loot_band[stat]["max"]))
        guard_tiers = set(r.balance.rarity_table["guard_kill"]["weights"])
        self.assertIn(drop.db.rarity, guard_tiers)
        self.assertIsInstance(drop.db.iqs, int)

        # --- 3. Salvage the crafted rifle at a L3 Blacksmith (R7.1/R7.2).
        bs = self._blacksmith(level=3)
        iqs = int(rifle.db.iqs)
        self.assertTrue(self.system.salvage(self.player, "assault_rifle", bs))
        self.assertTrue(rifle._deleted)
        self.assertNotIn(rifle, self.player.contents)
        base = float(r.balance.base_salvage)
        per_iqs = float(r.balance.salvage_per_iqs)
        level_mult = 1.0 + float(r.balance.salvage_level_bonus) * 2  # L3
        expected_yield = round((base + iqs * per_iqs) * level_mult)
        self.assertEqual(self.player.get_salvage(), expected_yield)

        # --- 4. Reroll the drop: Salvage + resources charged, fresh rolls
        # in the loot band, rarity/affixes untouched (R4.5).
        self.player.add_salvage(200)  # fund the bench
        salvage_before = self.player.get_salvage()
        iron_before = self.player.get_resource("Iron")
        rarity_before = drop.db.rarity
        affixes_before = list(getattr(drop.db, "affixes", None) or [])
        self.assertTrue(self.system.reroll(self.player, "sniper_rifle", bs))
        for stat in ("damage", "range"):
            self.assertGreaterEqual(drop.db.rolled_stats[stat],
                                    float(loot_band[stat]["min"]))
            self.assertLessEqual(drop.db.rolled_stats[stat],
                                 float(loot_band[stat]["max"]))
        self.assertEqual(drop.db.rarity, rarity_before)
        self.assertEqual(list(getattr(drop.db, "affixes", None) or []),
                         affixes_before)
        self.assertIsInstance(drop.db.iqs, int)
        self.assertEqual(
            salvage_before - self.player.get_salvage(),
            int(r.balance.reroll_salvage_cost))
        self.assertEqual(iron_before - self.player.get_resource("Iron"),
                         int(r.balance.reroll_resource_cost.get("Iron", 0)))

        # --- 5. Insert an Extended Barrel into the equipped drop (R5) and
        # read the result through the REAL range resolver (R8.1).
        self.player.equipment.equip(drop)
        self.player.equipment.add_supply("extended_barrel", 1, max_stack=10)
        range_before = float(drop.db.rolled_stats["range"])
        self.assertTrue(
            self.system.apply_insert(self.player, "extended_barrel", bs))
        insert_val = float(r.items["extended_barrel"].insert_effect["value"])
        self.assertEqual(drop.db.rolled_stats["range"],
                         range_before + insert_val)
        self.assertEqual(len(drop.db.inserts), 1)
        self.assertEqual(self.player.equipment.get_supply("extended_barrel"),
                         0)

        engine = _engine(r)
        cap = int(r.balance.max_weapon_range)
        # The weapon-instance read includes any range affix the drop rolled
        # (GameItem.get_stat adds affix magnitudes on the same axis).
        affix_range = sum(
            float(a.get("magnitude", 0))
            for a in (getattr(drop.db, "affixes", None) or [])
            if a.get("stat") == "range")
        expected = min(int(range_before + insert_val + affix_range), cap)
        self.assertEqual(engine._resolve_weapon_range(self.player, drop),
                         expected)


if __name__ == "__main__":
    unittest.main()
