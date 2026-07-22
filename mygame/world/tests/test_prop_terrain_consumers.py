"""
Property-based tests for terrain-modifier consumers (terrain-strategy).

Property 7: Vision radius formula — for any base radius, sight bonus,
terrain vision modifier (integer or fractional), and configured minimum,
the computed vision circle radius equals
``max(min_vision_radius, int(base + sight_bonus + terrain_vision))`` —
truncation toward zero, then the minimum clamp — for player circles
(player-resolved modifier at the occupied tile) and for building circles
(base modifier at the building position, never affected by any player's
class or technology affinities).

**Validates: Requirements 3.1, 3.2, 3.3, 3.7**

Property 8: Narrowed vision never forgets discovery — any sequence of
discovery updates followed by any narrowing of the vision circle leaves
previously discovered tiles outside the new circle reporting ``"fog"``
(never ``"unexplored"``) and the discovery bitfield retaining every
previously discovered tile.

**Validates: Requirements 3.4**

Property 11: Physical damage formula with terrain DR, chip floor, and
zero floor — for any physical attack with positive raw output, damage
equals ``max(chip_floor, int(raw - max(0, other_DR + terrain_defense)), 0)``
with player-resolved terrain defense for player targets and base terrain
defense for buildings; damage never falls below
``ceil(raw * chip_fraction)`` and never exceeds ``raw``.

**Validates: Requirements 5.1, 5.2, 5.3, 5.4, 2.7**

Property 12: Non-physical damage ignores terrain — any attack whose
damage type is not physical computes identical damage whether terrain
defense modifiers, class affinities, and terrain technologies are
present or entirely absent.

**Validates: Requirements 5.5**

Uses plain fakes (fake balance, fake resolver, fake player/building) —
no Evennia database objects. The effective radius is inferred from the
returned tile set: a Chebyshev circle of radius r is exactly the
(2r+1)^2 tiles around its center.
"""

import math
import unittest

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from mygame.world.coordinate.fog_of_war import FogOfWarSystem
from mygame.world.data_registry import DataRegistry
from mygame.world.definitions import BalanceConfig
from mygame.world.event_bus import EventBus
from mygame.world.systems.combat_engine import CombatEngine
from world import services


# ------------------------------------------------------------------ #
#  Fakes
# ------------------------------------------------------------------ #

class _FakeDB:
    """Minimal attribute-bag mimicking Evennia's db handler."""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class _FakeBalance:
    """Minimal stand-in for BalanceConfig."""

    def __init__(self, pvr, bvr, min_vision_radius):
        self.player_vision_radius = pvr
        self.building_vision_radius = bvr
        self.min_vision_radius = min_vision_radius
        self.scout_vision_radius = 0


class _FakeEquipment:
    """Equipment handler fake exposing a fixed sight_range stat total."""

    def __init__(self, sight_bonus):
        self._sight_bonus = sight_bonus

    def get_stat_total(self, stat):
        if stat == "sight_range":
            return float(self._sight_bonus)
        return 0.0


class _FakePlayer:
    """Lightweight player stand-in (no tech bonuses: sight comes from gear)."""

    def __init__(self, x, y, planet="earth", sight_bonus=0):
        self.key = "Player1"
        self.db = _FakeDB(
            coord_x=x,
            coord_y=y,
            coord_planet=planet,
            discovery_memory={},
        )
        self.equipment = _FakeEquipment(sight_bonus)


class _FakeBuilding:
    """Building stand-in exposing db.coord_x / db.coord_y."""

    def __init__(self, x, y):
        self.db = _FakeDB(coord_x=x, coord_y=y)


class _FakeModifiers:
    """Stand-in for TerrainModifiers; only .vision is read by fog of war.

    The real resolver coerces .vision to int after clamping, but fog_of_war
    truncates the SUM with int() — a fake may return fractional vision values
    to exercise the truncation rule (Req 3.7).
    """

    def __init__(self, vision):
        self.vision = vision


class _FakeResolver:
    """Terrain modifier resolver fake with distinct player/base vision values.

    Records every call so tests can assert which resolution path each vision
    circle used (player-resolved vs base, Req 3.1 / 3.3).
    """

    def __init__(self, player_vision, base_vision):
        self._player_vision = player_vision
        self._base_vision = base_vision
        self.player_calls = []
        self.base_calls = []

    def resolve_for_player(self, player, planet, x, y):
        self.player_calls.append((player, planet, x, y))
        return _FakeModifiers(self._player_vision)

    def resolve_base(self, planet, x, y):
        self.base_calls.append((planet, x, y))
        return _FakeModifiers(self._base_vision)

    def set_player_vision(self, vision):
        """Change the player-resolved vision between calls (drives narrowing, Prop 8)."""
        self._player_vision = vision


# ------------------------------------------------------------------ #
#  Helpers
# ------------------------------------------------------------------ #

def _chebyshev_circle(cx, cy, radius):
    """All (x, y) tiles within Chebyshev distance *radius* of (cx, cy)."""
    return {
        (cx + dx, cy + dy)
        for dx in range(-radius, radius + 1)
        for dy in range(-radius, radius + 1)
    }


def _expected_radius(base, sight_bonus, terrain_vision, min_vision_radius):
    """The Property 7 formula: truncate toward zero, then minimum clamp."""
    return max(min_vision_radius, int(base + sight_bonus + terrain_vision))


# ------------------------------------------------------------------ #
#  Hypothesis strategies
# ------------------------------------------------------------------ #

# Terrain vision modifiers: integers or exact quarter fractions (Req 3.7
# truncation must handle fractional values; quarters are exact in binary).
terrain_vision_strategy = st.one_of(
    st.integers(min_value=-5, max_value=5),
    st.integers(min_value=-20, max_value=20).map(lambda n: n / 4.0),
)

base_radius_strategy = st.integers(min_value=0, max_value=6)
sight_bonus_strategy = st.integers(min_value=-3, max_value=3)
min_radius_strategy = st.integers(min_value=0, max_value=3)
coord_strategy = st.integers(min_value=-100, max_value=100)


# ------------------------------------------------------------------ #
#  Property 7: Vision radius formula
#  # Feature: terrain-strategy, Property 7: Vision radius formula
#  **Validates: Requirements 3.1, 3.2, 3.3, 3.7**
# ------------------------------------------------------------------ #

class TestProperty7VisionRadiusFormula(unittest.TestCase):
    """Property 7: Vision radius formula.

    The circle radius equals
    ``max(min_vision_radius, int(base + sight_bonus + terrain))`` for
    player circles (player-resolved modifier, occupied tile) and building
    circles (base modifier at the building position).

    **Validates: Requirements 3.1, 3.2, 3.3, 3.7**
    """

    @given(
        px=coord_strategy,
        py=coord_strategy,
        base=base_radius_strategy,
        sight_bonus=sight_bonus_strategy,
        terrain_vision=terrain_vision_strategy,
        min_vr=min_radius_strategy,
    )
    @settings(max_examples=150)
    def test_player_circle_radius_formula(
        self, px, py, base, sight_bonus, terrain_vision, min_vr
    ):
        """Player circle uses the player-resolved modifier at the occupied tile."""
        balance = _FakeBalance(pvr=base, bvr=0, min_vision_radius=min_vr)
        fow = FogOfWarSystem(balance)
        # Base vision differs from player vision so a building-path mixup
        # (resolve_base for the player circle) produces a detectable radius.
        resolver = _FakeResolver(
            player_vision=terrain_vision, base_vision=terrain_vision + 7,
        )
        fow.set_terrain_modifier_resolver(resolver)
        player = _FakePlayer(x=px, y=py, sight_bonus=sight_bonus)

        visible = fow.get_visible_tiles(player, [])

        expected_r = _expected_radius(base, sight_bonus, terrain_vision, min_vr)
        self.assertEqual(
            visible,
            _chebyshev_circle(px, py, expected_r),
            f"player circle radius != max({min_vr}, "
            f"int({base} + {sight_bonus} + {terrain_vision})) = {expected_r}",
        )
        # Player-resolved modifier at the OCCUPIED tile (Req 3.1).
        self.assertEqual(resolver.player_calls, [(player, "earth", px, py)])
        self.assertEqual(resolver.base_calls, [])

    @given(
        px=coord_strategy,
        py=coord_strategy,
        bx=coord_strategy,
        by=coord_strategy,
        player_base=base_radius_strategy,
        building_base=base_radius_strategy,
        sight_bonus=sight_bonus_strategy,
        player_terrain=terrain_vision_strategy,
        building_terrain=terrain_vision_strategy,
        min_vr=min_radius_strategy,
    )
    @settings(max_examples=150)
    def test_building_circle_radius_formula(
        self, px, py, bx, by, player_base, building_base,
        sight_bonus, player_terrain, building_terrain, min_vr,
    ):
        """Building circle uses the base modifier at the building position,
        never any player's class/technology affinities (Req 3.3)."""
        balance = _FakeBalance(
            pvr=player_base, bvr=building_base, min_vision_radius=min_vr,
        )
        fow = FogOfWarSystem(balance)
        # Player-resolved and base vision differ: if the building circle
        # wrongly used the player's affinity-adjusted value, the resulting
        # tile set would not match the base-modifier expectation.
        resolver = _FakeResolver(
            player_vision=player_terrain, base_vision=building_terrain,
        )
        fow.set_terrain_modifier_resolver(resolver)
        player = _FakePlayer(x=px, y=py, sight_bonus=sight_bonus)
        building = _FakeBuilding(x=bx, y=by)

        visible = fow.get_visible_tiles(player, [building])

        player_r = _expected_radius(player_base, sight_bonus, player_terrain, min_vr)
        # Building circle: no sight bonus, base terrain modifier only,
        # same truncate-then-minimum treatment (Req 3.2, 3.3, 3.7).
        building_r = _expected_radius(building_base, 0, building_terrain, min_vr)
        expected = (
            _chebyshev_circle(px, py, player_r)
            | _chebyshev_circle(bx, by, building_r)
        )
        self.assertEqual(
            visible,
            expected,
            f"union mismatch: player r={player_r} at ({px},{py}), "
            f"building r={building_r} at ({bx},{by})",
        )
        # Building resolution goes through resolve_base at the building
        # position; the player circle through resolve_for_player (Req 3.1/3.3).
        self.assertEqual(resolver.player_calls, [(player, "earth", px, py)])
        self.assertEqual(len(resolver.base_calls), 1)
        self.assertEqual(resolver.base_calls[0][1:], (bx, by))


# ------------------------------------------------------------------ #
#  Property 8: Narrowed vision never forgets discovery
#  # Feature: terrain-strategy, Property 8: Narrowed vision never forgets discovery
#  **Validates: Requirements 3.4**
# ------------------------------------------------------------------ #

# A discovery step: player position plus the terrain vision modifier in
# effect there. Positions stay small so successive circles overlap and
# diverge; modifiers span widening and narrowing terrain.
discovery_step_strategy = st.tuples(
    st.integers(min_value=-8, max_value=8),   # x
    st.integers(min_value=-8, max_value=8),   # y
    st.integers(min_value=-6, max_value=6),   # terrain vision modifier
)


class TestProperty8NarrowedVisionPreservesDiscovery(unittest.TestCase):
    """Property 8: Narrowed vision never forgets discovery.

    For any sequence of discovery updates followed by any narrowing of
    the vision circle, previously discovered tiles outside the new
    circle report ``"fog"`` — never ``"unexplored"`` — and the discovery
    bitfield retains every previously discovered tile.

    **Validates: Requirements 3.4**
    """

    @given(
        base=st.integers(min_value=1, max_value=5),
        steps=st.lists(discovery_step_strategy, min_size=1, max_size=6),
        final_x=st.integers(min_value=-8, max_value=8),
        final_y=st.integers(min_value=-8, max_value=8),
        narrow_vision=st.integers(min_value=-10, max_value=-1),
    )
    @settings(max_examples=100)
    def test_narrowed_vision_reports_fog_and_retains_bitfield(
        self, base, steps, final_x, final_y, narrow_vision
    ):
        """Discovered tiles outside the narrowed circle are fog, never lost."""
        balance = _FakeBalance(pvr=base, bvr=0, min_vision_radius=1)
        fow = FogOfWarSystem(balance)
        resolver = _FakeResolver(player_vision=0, base_vision=0)
        fow.set_terrain_modifier_resolver(resolver)
        player = _FakePlayer(x=0, y=0)

        # Any discovery-update sequence: move, resolve terrain, see, remember.
        discovered = set()
        for x, y, vision in steps:
            player.db.coord_x, player.db.coord_y = x, y
            resolver.set_player_vision(vision)
            visible = fow.get_visible_tiles(player, [])
            fow.update_discovery(player, visible)
            discovered |= visible

        # Any circle narrowing: negative terrain Vision_Modifier at the
        # (possibly new) occupied tile shrinks the circle toward the minimum.
        player.db.coord_x, player.db.coord_y = final_x, final_y
        resolver.set_player_vision(narrow_vision)
        new_visible = fow.get_visible_tiles(player, [])
        fow.update_discovery(player, new_visible)

        outside = discovered - new_visible
        assume(outside)  # only meaningful when narrowing left tiles behind

        # Previously discovered tiles outside the new circle report "fog",
        # never "unexplored" (Req 3.4).
        for x, y in outside:
            state = fow.get_tile_visibility(player, x, y, new_visible)
            self.assertEqual(
                state,
                "fog",
                f"discovered tile ({x}, {y}) outside the narrowed circle "
                f"reported {state!r} instead of 'fog'",
            )

        # The bitfield retains every previously discovered tile (Req 3.4).
        bitfield = fow.get_discovered_tile_set(player)
        for x, y in discovered:
            self.assertIn(
                (x, y),
                bitfield,
                f"tile ({x}, {y}) was dropped from the discovery bitfield "
                f"after the vision circle narrowed",
            )


# ------------------------------------------------------------------ #
#  Property 11: Physical damage formula with terrain DR, chip floor,
#  and zero floor
#  # Feature: terrain-strategy, Property 11: Physical damage formula with
#  # terrain DR, chip floor, and zero floor
#  **Validates: Requirements 5.1, 5.2, 5.3, 5.4, 2.7**
# ------------------------------------------------------------------ #

class _FakeDefenseModifiers:
    """Stand-in for TerrainModifiers; only .defense is read by combat."""

    def __init__(self, defense):
        self.defense = defense


class _FakeDefenseResolver:
    """Terrain resolver fake with distinct player/base defense values.

    Records every call so tests can assert which resolution path the
    damage calculation used (player-resolved vs base, Req 5.1 / 5.3).
    """

    def __init__(self, player_defense, base_defense):
        self._player_defense = player_defense
        self._base_defense = base_defense
        self.player_calls = []
        self.base_calls = []

    def resolve_for_player(self, player, planet, x, y):
        self.player_calls.append((player, planet, x, y))
        return _FakeDefenseModifiers(self._player_defense)

    def resolve_base(self, planet, x, y):
        self.base_calls.append((planet, x, y))
        return _FakeDefenseModifiers(self._base_defense)


class _FakeCombatEquipment:
    """Equipment handler fake exposing a fixed damage_reduction total."""

    def __init__(self, damage_reduction=0.0):
        self._dr = damage_reduction

    def get_stat_total(self, stat):
        if stat == "damage_reduction":
            return float(self._dr)
        return 0.0


class _FakeCombatPlayer:
    """Player-target stand-in: carries db.combat_xp (is_player) and coords."""

    def __init__(self, x=0, y=0, planet="earth", armor_dr=0.0, name="P"):
        self.key = name
        self.db = _FakeDB(
            combat_xp=0,
            hp=100,
            hp_max=100,
            active_powerups={},
            coord_x=x,
            coord_y=y,
            coord_planet=planet,
        )
        self.equipment = _FakeCombatEquipment(armor_dr)


class _FakeCombatBuilding:
    """Building-target stand-in: db.building_type (is_building), no combat_xp."""

    def __init__(self, x=0, y=0, planet="earth"):
        self.key = "VV"
        self.db = _FakeDB(
            building_type="VV",
            coord_x=x,
            coord_y=y,
            coord_planet=planet,
        )


class _FakeDamageWeapon:
    """Weapon stand-in with a flat physical damage stat (no damage_type)."""

    def __init__(self, damage):
        self.key = "test_weapon"
        self.stat_modifiers = {"damage": damage}
        self.ammo_cost = None

    def get_stat(self, stat_name, default=0):
        return float(self.stat_modifiers.get(stat_name, default))


def _make_combat_engine(chip_fraction):
    """A CombatEngine over a minimal registry with the given chip fraction."""
    registry = DataRegistry()
    registry.balance = BalanceConfig()
    registry.balance.chip_damage_min_fraction = chip_fraction
    return CombatEngine(
        registry=registry,
        event_bus=EventBus(),
        current_tick_func=lambda: 0,
    )


def _expected_damage(raw, other_dr, terrain_defense, chip_fraction):
    """The Property 11 formula, mirroring the spec text exactly:

    ``max(chip_floor, int(raw - max(0, other_DR + terrain_defense)), 0)``
    where ``chip_floor = ceil(raw * chip_fraction)`` for positive raw.
    """
    dr_total = max(0.0, other_dr + terrain_defense)
    net = int(raw - dr_total)
    frac = min(1.0, max(0.0, float(chip_fraction or 0.0)))
    chip_floor = int(math.ceil(raw * frac)) if frac > 0 and raw > 0 else 0
    return max(chip_floor, net, 0)


# Raw weapon damage: always positive ("any physical attack with positive
# raw output"). Attacker fakes carry no gear/class/tech bonus, so raw ==
# the weapon's damage stat.
raw_damage_strategy = st.integers(min_value=1, max_value=100)

# Non-terrain DR (armor gear): non-negative, ranging past raw so the chip
# floor binds (Req 5.2). Quarters exercise fractional DR exactly.
armor_dr_strategy = st.one_of(
    st.integers(min_value=0, max_value=120),
    st.integers(min_value=0, max_value=480).map(lambda n: n / 4.0),
)

# Terrain Defense_Modifier: signed, so negative values exercise the
# zero-floored DR total (Req 5.4); quarters for fractional values.
terrain_defense_strategy = st.one_of(
    st.integers(min_value=-60, max_value=60),
    st.integers(min_value=-240, max_value=240).map(lambda n: n / 4.0),
)

# Chip fraction spans disabled (0) through full (1) in exact quarters.
chip_fraction_strategy = st.integers(min_value=0, max_value=4).map(lambda n: n / 4.0)


class TestProperty11PhysicalDamageFormula(unittest.TestCase):
    """Property 11: Physical damage formula with terrain DR, chip floor,
    and zero floor.

    For any physical attack with positive raw output, damage equals
    ``max(chip_floor, int(raw - max(0, other_DR + terrain_defense)), 0)``
    with player-resolved defense for player targets (Req 5.1) and base
    defense for buildings (Req 5.3); damage never falls below
    ``ceil(raw * chip_fraction)`` (Req 5.2) and never exceeds ``raw``
    (Req 5.4).

    **Validates: Requirements 5.1, 5.2, 5.3, 5.4, 2.7**
    """

    def _damage(self, resolver, engine, weapon, target):
        with services.override({"terrain_modifier_system": resolver}):
            return engine._calculate_damage(
                attacker=_FakeCombatPlayer(name="A"),
                target=target,
                weapon_item=weapon,
            )

    @given(
        raw=raw_damage_strategy,
        armor_dr=armor_dr_strategy,
        terrain_defense=terrain_defense_strategy,
        chip_fraction=chip_fraction_strategy,
        tx=coord_strategy,
        ty=coord_strategy,
    )
    @settings(max_examples=150)
    def test_player_target_damage_formula(
        self, raw, armor_dr, terrain_defense, chip_fraction, tx, ty
    ):
        """Player targets: player-resolved defense at the occupied tile
        (Req 5.1, occupied-tile side of Req 2.7), chip floor after the
        terrain-adjusted DR (Req 5.2), DR total floored at zero (Req 5.4)."""
        engine = _make_combat_engine(chip_fraction)
        # Base defense differs from the player-resolved value so a wrong
        # resolution path (resolve_base for a player) changes the damage.
        resolver = _FakeDefenseResolver(
            player_defense=terrain_defense, base_defense=terrain_defense + 7.0,
        )
        target = _FakeCombatPlayer(x=tx, y=ty, armor_dr=armor_dr, name="T")

        dealt = self._damage(resolver, engine, _FakeDamageWeapon(raw), target)

        expected = _expected_damage(raw, armor_dr, terrain_defense, chip_fraction)
        self.assertEqual(
            dealt,
            expected,
            f"player-target damage != max(chip_floor, int({raw} - "
            f"max(0, {armor_dr} + {terrain_defense})), 0) = {expected}",
        )
        # Damage never falls below the chip floor and never exceeds raw.
        self.assertGreaterEqual(dealt, int(math.ceil(raw * chip_fraction)))
        self.assertLessEqual(dealt, raw)
        # Player-resolved defense at the target's occupied tile (Req 5.1, 2.7).
        self.assertEqual(resolver.player_calls, [(target, "earth", tx, ty)])
        self.assertEqual(resolver.base_calls, [])

    @given(
        raw=raw_damage_strategy,
        terrain_defense=terrain_defense_strategy,
        chip_fraction=chip_fraction_strategy,
        bx=coord_strategy,
        by=coord_strategy,
    )
    @settings(max_examples=150)
    def test_building_target_damage_formula(
        self, raw, terrain_defense, chip_fraction, bx, by
    ):
        """Building targets: base terrain defense only (Req 5.3), never any
        player's class/technology affinities; buildings carry no armor DR,
        so other_DR is zero."""
        engine = _make_combat_engine(chip_fraction)
        # Player-resolved and base defense differ: if the building path
        # wrongly used resolve_for_player, the damage would not match.
        resolver = _FakeDefenseResolver(
            player_defense=terrain_defense + 7.0, base_defense=terrain_defense,
        )
        building = _FakeCombatBuilding(x=bx, y=by)

        dealt = self._damage(resolver, engine, _FakeDamageWeapon(raw), building)

        expected = _expected_damage(raw, 0.0, terrain_defense, chip_fraction)
        self.assertEqual(
            dealt,
            expected,
            f"building-target damage != max(chip_floor, int({raw} - "
            f"max(0, 0 + {terrain_defense})), 0) = {expected}",
        )
        # Damage never falls below the chip floor and never exceeds raw.
        self.assertGreaterEqual(dealt, int(math.ceil(raw * chip_fraction)))
        self.assertLessEqual(dealt, raw)
        # Base resolution at the building's position, never player-resolved
        # (Req 5.3, occupied-tile side of Req 2.7).
        self.assertEqual(resolver.base_calls, [("earth", bx, by)])
        self.assertEqual(resolver.player_calls, [])


# ------------------------------------------------------------------ #
#  Property 12: Non-physical damage ignores terrain
#  # Feature: terrain-strategy, Property 12: Non-physical damage ignores terrain
#  **Validates: Requirements 5.5**
# ------------------------------------------------------------------ #

class _FakeTypedWeapon:
    """Weapon stand-in with a flat damage stat and a non-physical damage_type."""

    def __init__(self, damage, damage_type):
        self.key = "typed_weapon"
        self.damage_type = damage_type
        self.stat_modifiers = {"damage": damage}
        self.ammo_cost = None

    def get_stat(self, stat_name, default=0):
        return float(self.stat_modifiers.get(stat_name, default))


# Non-physical damage types recognised by the engine's typed-resist model
# (any non-"physical" string takes the typed branch; these are the shipped
# axes: fire, psychic, blast).
non_physical_type_strategy = st.sampled_from(["fire", "psychic", "blast"])

# Terrain defenses the resolver hands out when terrain IS present: large and
# never zero, so any accidental terrain read would visibly change the damage.
nonzero_defense_strategy = st.one_of(
    st.integers(min_value=1, max_value=200),
    st.integers(min_value=-200, max_value=-1),
).map(float)


class TestProperty12NonPhysicalIgnoresTerrain(unittest.TestCase):
    """Property 12: Non-physical damage ignores terrain.

    For any attack whose damage type is not physical, the computed damage
    is identical whether terrain defense modifiers, class affinities, and
    terrain technologies are present or entirely absent (Req 5.5).

    **Validates: Requirements 5.5**
    """

    def _damage(self, engine, weapon, target, systems):
        with services.override(systems):
            return engine._calculate_damage(
                attacker=_FakeCombatPlayer(name="A"),
                target=target,
                weapon_item=weapon,
            )

    @given(
        raw=raw_damage_strategy,
        armor_dr=armor_dr_strategy,
        player_defense=nonzero_defense_strategy,
        base_defense=nonzero_defense_strategy,
        chip_fraction=chip_fraction_strategy,
        damage_type=non_physical_type_strategy,
        tx=coord_strategy,
        ty=coord_strategy,
    )
    @settings(max_examples=150)
    def test_player_target_ignores_terrain(
        self, raw, armor_dr, player_defense, base_defense,
        chip_fraction, damage_type, tx, ty,
    ):
        """Player targets: terrain presence (resolver returning large non-zero
        defenses, standing in for modifiers + affinities + technologies) never
        changes a non-physical attack's damage, and terrain is never consulted."""
        engine = _make_combat_engine(chip_fraction)
        weapon = _FakeTypedWeapon(raw, damage_type)
        target = _FakeCombatPlayer(x=tx, y=ty, armor_dr=armor_dr, name="T")
        resolver = _FakeDefenseResolver(
            player_defense=player_defense, base_defense=base_defense,
        )

        with_terrain = self._damage(
            engine, weapon, target, {"terrain_modifier_system": resolver},
        )
        without_terrain = self._damage(engine, weapon, target, {})

        self.assertEqual(
            with_terrain,
            without_terrain,
            f"non-physical ({damage_type}) damage changed with terrain present: "
            f"{with_terrain} (terrain) != {without_terrain} (no terrain)",
        )
        # The typed branch never consults terrain resolution at all (Req 5.5).
        self.assertEqual(resolver.player_calls, [])
        self.assertEqual(resolver.base_calls, [])

    @given(
        raw=raw_damage_strategy,
        player_defense=nonzero_defense_strategy,
        base_defense=nonzero_defense_strategy,
        chip_fraction=chip_fraction_strategy,
        damage_type=non_physical_type_strategy,
        bx=coord_strategy,
        by=coord_strategy,
    )
    @settings(max_examples=150)
    def test_building_target_ignores_terrain(
        self, raw, player_defense, base_defense, chip_fraction, damage_type, bx, by,
    ):
        """Building targets: a non-physical attack deals identical damage with
        or without terrain data present, and never resolves terrain."""
        engine = _make_combat_engine(chip_fraction)
        weapon = _FakeTypedWeapon(raw, damage_type)
        building = _FakeCombatBuilding(x=bx, y=by)
        resolver = _FakeDefenseResolver(
            player_defense=player_defense, base_defense=base_defense,
        )

        with_terrain = self._damage(
            engine, weapon, building, {"terrain_modifier_system": resolver},
        )
        without_terrain = self._damage(engine, weapon, building, {})

        self.assertEqual(
            with_terrain,
            without_terrain,
            f"non-physical ({damage_type}) building damage changed with terrain "
            f"present: {with_terrain} (terrain) != {without_terrain} (no terrain)",
        )
        # Terrain resolution is never consulted on the typed branch (Req 5.5).
        self.assertEqual(resolver.player_calls, [])
        self.assertEqual(resolver.base_calls, [])


if __name__ == "__main__":
    unittest.main()
