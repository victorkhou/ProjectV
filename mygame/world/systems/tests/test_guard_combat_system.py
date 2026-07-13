"""
Unit tests for GuardCombatSystem (PvE NPC bases — Phase 3).

Covers target acquisition (nearest non-owner within aggro radius), owner-skip
by .id, the deactivation gate (owner without an active HQ), melee vs ranged
weapon selection and range, and the skip rules (wrong role, reserved,
incapacitated, 0-HP, ownerless). Guards queue attacks through a real
CombatEngine so the queue_attack range/self validation is exercised end-to-end.

Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 12.6
"""

import sys
import types
import unittest


# -------------------------------------------------------------- #
#  Bootstrap: stub out Evennia modules
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

from mygame.world.systems.guard_combat_system import GuardCombatSystem  # noqa: E402
from mygame.world.systems.combat_engine import CombatEngine  # noqa: E402
from mygame.world.data_registry import DataRegistry  # noqa: E402
from mygame.world.definitions import BalanceConfig, BuildingDef  # noqa: E402
from mygame.world.event_bus import EventBus  # noqa: E402


# -------------------------------------------------------------- #
#  Fakes
# -------------------------------------------------------------- #

class FakeDB:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class FakeAttributes:
    def __init__(self, data=None):
        self._data = data or {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def add(self, key, value):
        self._data[key] = value


class FakeTile:
    """Stand-in for a PlanetRoom tile exposing the 3-arg spatial query."""
    def __init__(self, planet="earth", nearby_players=None):
        self._planet = planet
        self._nearby_players = nearby_players or []

    def get_nearby_players(self, x, y, radius):
        return self._nearby_players

    @property
    def planet_name(self):
        return self._planet


class FakePlayer:
    """A player-like target/owner: has db.combat_xp (so is_player is True)."""
    def __init__(self, name="Player", x=0, y=0, oid=None, location=None,
                 combat_xp=0, hp=100, hp_max=100):
        self.key = name
        self.db = FakeDB(coord_x=x, coord_y=y, combat_xp=combat_xp,
                         hp=hp, hp_max=hp_max, combat_lockout_tick=0,
                         active_powerups={})
        self.location = location
        self._messages = []
        if oid is not None:
            self.id = oid

    def msg(self, text):
        self._messages.append(text)


class _HqBuilding:
    """Minimal HQ-capability building for an owner's get_buildings()."""
    def __init__(self, planet="earth", under_construction=False):
        self.attributes = FakeAttributes({"building_type": "HQ"})
        self.location = type("_L", (), {"planet_name": planet})()
        self.db = FakeDB(building_type="HQ",
                         under_construction=under_construction)


def _hq_owner(name="Sentinel", planet="earth", oid=None, under_construction=False):
    """A base owner that has a (completed) HQ, so its guards are active."""
    owner = FakePlayer(name=name, oid=oid)
    owner.get_buildings = lambda: [_HqBuilding(planet, under_construction)]
    return owner


class FakeGuard:
    """A guard NPC: role guard/soldier, npc_type enemy, coords + owner."""
    def __init__(self, role="guard", owner=None, x=0, y=0, location=None,
                 hp=100, reserve=False, incapacitated=False, oid=None):
        self.key = f"{role.title()} #1"
        self.db = FakeDB(role=role, owner=owner, npc_type="enemy",
                         coord_x=x, coord_y=y, hp=hp, hp_max=hp,
                         reserve=reserve, incapacitated=incapacitated,
                         combat_xp=0, combat_lockout_tick=0,
                         active_powerups={})
        # A guard fights with a synthetic weapon, so it needs no equipment
        # handler; queue_attack uses the weapon we pass explicitly.
        self.location = location
        self._messages = []
        if oid is not None:
            self.id = oid

    def msg(self, text):
        self._messages.append(text)


def _make_registry(**balance_overrides) -> DataRegistry:
    registry = DataRegistry()
    registry.balance = BalanceConfig(**balance_overrides)
    registry.buildings = {
        "HQ": BuildingDef(
            name="Headquarters", abbreviation="HQ", cost={"Wood": 10},
            max_health=500, requires_hq=False, required_terrain=None,
            category="headquarters", produces=None,
            capabilities=frozenset({"headquarters"}),
        ),
    }
    return registry


def _make_system(registry=None, engine=None, **balance_overrides):
    if registry is None:
        registry = _make_registry(**balance_overrides)
    event_bus = EventBus()
    if engine is None:
        engine = CombatEngine(registry, event_bus, current_tick_func=lambda: 0)
    system = GuardCombatSystem(registry, event_bus, combat_engine=engine)
    return system, engine


# -------------------------------------------------------------- #
#  Tests
# -------------------------------------------------------------- #

class TestGuardTargetAcquisition(unittest.TestCase):

    def test_guard_targets_nearest_non_owner(self):
        system, engine = _make_system()
        owner = _hq_owner()
        near = FakePlayer(name="Near", x=1, y=0, oid=2)
        far = FakePlayer(name="Far", x=3, y=0, oid=3)
        tile = FakeTile(nearby_players=[far, near])
        guard = FakeGuard(role="guard", owner=owner, x=0, y=0, location=tile)

        system.process_tick(1, [guard])

        self.assertEqual(len(engine.pending_actions), 1)
        self.assertEqual(engine.pending_actions[0]["target"], near)

    def test_guard_ignores_owner_by_id(self):
        """A player sharing the owner's .id is treated as the owner (not fired)."""
        system, engine = _make_system()
        owner = _hq_owner(oid=7)
        owner_proxy = FakePlayer(name="OwnerProxy", x=1, y=0, oid=7)
        tile = FakeTile(nearby_players=[owner_proxy])
        guard = FakeGuard(role="guard", owner=owner, x=0, y=0, location=tile)

        system.process_tick(1, [guard])
        self.assertEqual(len(engine.pending_actions), 0)

    def test_guard_fires_on_distinct_id(self):
        system, engine = _make_system()
        owner = _hq_owner(oid=7)
        hostile = FakePlayer(name="Hostile", x=1, y=0, oid=99)
        tile = FakeTile(nearby_players=[hostile])
        guard = FakeGuard(role="guard", owner=owner, x=0, y=0, location=tile)

        system.process_tick(1, [guard])
        self.assertEqual(len(engine.pending_actions), 1)
        self.assertEqual(engine.pending_actions[0]["target"], hostile)

    def test_no_target_in_aggro_radius(self):
        system, engine = _make_system(guard_aggro_radius=5)
        owner = _hq_owner()
        # 10 tiles away — outside the aggro radius but returned by the fake tile.
        far = FakePlayer(name="Far", x=10, y=0, oid=2)
        tile = FakeTile(nearby_players=[far])
        guard = FakeGuard(role="guard", owner=owner, x=0, y=0, location=tile)

        system.process_tick(1, [guard])
        self.assertEqual(len(engine.pending_actions), 0)

    @staticmethod
    def _sheltered_player(name, x, y, oid):
        """A player standing INSIDE a closed building on their own tile."""
        class _ShelterTile:
            planet_name = "earth"
            def get_buildings_at(self, bx, by):
                b = type("_B", (), {})()
                b.attributes = FakeAttributes({"building_type": "MM",
                                               "open": False})
                b.db = FakeDB(building_type="MM", open=False)
                return [b]
        p = FakePlayer(name=name, x=x, y=y, oid=oid, location=_ShelterTile())
        p.db.inside_building = True
        return p

    def test_ranged_guard_does_not_acquire_sheltered_player(self):
        """A ranged guard (soldier) skips a player sheltered in a closed
        building — its shot can't reach an occupant under cover, so it falls
        through to the next-nearest real target (here: none)."""
        system, engine = _make_system(guard_aggro_radius=5)
        owner = _hq_owner()
        sheltered = self._sheltered_player("Hider", x=1, y=0, oid=2)
        tile = FakeTile(nearby_players=[sheltered])
        soldier = FakeGuard(role="soldier", owner=owner, x=0, y=0, location=tile)

        system.process_tick(1, [soldier])
        self.assertEqual(len(engine.pending_actions), 0,
                         "a ranged guard must not lock onto a sheltered player")

    def test_melee_guard_does_not_hit_inside_player_from_adjacent_tile(self):
        """Buildings are rooms for melee: a melee guard adjacent to a player who
        is INSIDE a building does NOT attack across the boundary — it must close
        onto the same tile first (chases if it can). So no attack is queued from
        an adjacent tile."""
        system, engine = _make_system(guard_aggro_radius=5)
        owner = _hq_owner()
        inside = self._sheltered_player("Hider", x=1, y=0, oid=2)
        tile = FakeTile(nearby_players=[inside])
        guard = FakeGuard(role="guard", owner=owner, x=0, y=0, location=tile)

        system.process_tick(1, [guard])
        self.assertEqual(len(engine.pending_actions), 0)

    def test_melee_guard_hits_inside_player_on_same_tile(self):
        """When the melee guard shares the tile with the inside player (it has
        chased onto the building tile), the melee room gate is satisfied and the
        attack is queued."""
        system, engine = _make_system(guard_aggro_radius=5)
        owner = _hq_owner()
        inside = self._sheltered_player("Hider", x=0, y=0, oid=2)
        tile = FakeTile(nearby_players=[inside])
        guard = FakeGuard(role="guard", owner=owner, x=0, y=0, location=tile)

        system.process_tick(1, [guard])
        self.assertEqual(len(engine.pending_actions), 1)
        self.assertEqual(engine.pending_actions[0]["target"], inside)


class TestGuardChebyshevDistance(unittest.TestCase):
    """Guard distance math uses Chebyshev (a diagonal = distance 1), matching the
    combat/vision metric. Every OTHER guard test is axis-aligned (y=0), where
    Chebyshev and Manhattan agree — these diagonal cases are the ones that catch
    a silent revert to Manhattan (which would read a diagonal as distance 2)."""

    def test_melee_guard_reaches_diagonal_target(self):
        """A melee guard (range 1) hits a target one tile DIAGONALLY away —
        Chebyshev(1,1)=1 (in reach). Under Manhattan that tile is distance 2 and
        the shot would be wrongly rejected."""
        system, engine = _make_system(guard_aggro_radius=5)
        owner = _hq_owner()
        diag = FakePlayer(name="Diag", x=1, y=1, oid=2)  # Cheb 1, Manhattan 2
        tile = FakeTile(nearby_players=[diag])
        guard = FakeGuard(role="guard", owner=owner, x=0, y=0, location=tile)

        system.process_tick(1, [guard])
        self.assertEqual(len(engine.pending_actions), 1,
                         "melee guard must reach a diagonal-adjacent target")
        self.assertEqual(engine.pending_actions[0]["target"], diag)

    def test_ranged_soldier_reaches_diagonal_within_chebyshev_range(self):
        """A soldier with range 4 hits a target at (3,3): Chebyshev 3 (in range).
        Manhattan would read 6 and reject it."""
        system, engine = _make_system(guard_aggro_radius=10, guard_ranged_range=4)
        owner = _hq_owner()
        diag = FakePlayer(name="Diag", x=3, y=3, oid=2)  # Cheb 3, Manhattan 6
        tile = FakeTile(nearby_players=[diag])
        soldier = FakeGuard(role="soldier", owner=owner, x=0, y=0, location=tile)

        system.process_tick(1, [soldier])
        self.assertEqual(len(engine.pending_actions), 1,
                         "ranged guard must reach a diagonal target within range")

    def test_aggro_uses_chebyshev_for_diagonal(self):
        """Aggro acquisition is Chebyshev: a target at (4,4) is Chebyshev-4 (in a
        5-radius aggro) though Manhattan-8 (which would exclude it)."""
        system, engine = _make_system(guard_aggro_radius=5, guard_ranged_range=8)
        owner = _hq_owner()
        diag = FakePlayer(name="Diag", x=4, y=4, oid=2)  # Cheb 4, Manhattan 8
        tile = FakeTile(nearby_players=[diag])
        soldier = FakeGuard(role="soldier", owner=owner, x=0, y=0, location=tile)

        system.process_tick(1, [soldier])
        self.assertEqual(len(engine.pending_actions), 1,
                         "diagonal target within Chebyshev aggro must be acquired")

    def test_nearest_target_by_chebyshev_not_manhattan(self):
        """Target selection picks the Chebyshev-nearest. A diagonal foe at (2,2)
        (Cheb 2) is chosen over an axis foe at (3,0) (Cheb 3) — even though
        Manhattan would rank them 4 vs 3 and pick the axis foe instead."""
        system, engine = _make_system(guard_aggro_radius=8, guard_ranged_range=8)
        owner = _hq_owner()
        diag = FakePlayer(name="Diag", x=2, y=2, oid=2)   # Cheb 2, Manhattan 4
        axis = FakePlayer(name="Axis", x=3, y=0, oid=3)   # Cheb 3, Manhattan 3
        tile = FakeTile(nearby_players=[axis, diag])
        soldier = FakeGuard(role="soldier", owner=owner, x=0, y=0, location=tile)

        system.process_tick(1, [soldier])
        self.assertEqual(len(engine.pending_actions), 1)
        self.assertEqual(engine.pending_actions[0]["target"], diag,
                         "Chebyshev-nearest (the diagonal foe) must be chosen")


class TestGuardWeaponSelection(unittest.TestCase):

    def test_melee_guard_range_one_blocks_distant_target(self):
        """A melee guard's effective range is 1: a target 3 tiles away is inside
        aggro but out of weapon range, so queue_attack rejects it."""
        system, engine = _make_system(guard_aggro_radius=5)
        owner = _hq_owner()
        target = FakePlayer(name="T", x=3, y=0, oid=2)
        tile = FakeTile(nearby_players=[target])
        guard = FakeGuard(role="guard", owner=owner, x=0, y=0, location=tile)

        system.process_tick(1, [guard])
        self.assertEqual(len(engine.pending_actions), 0)

    def test_melee_guard_fires_at_range_one(self):
        system, engine = _make_system()
        owner = _hq_owner()
        target = FakePlayer(name="T", x=1, y=0, oid=2)
        tile = FakeTile(nearby_players=[target])
        guard = FakeGuard(role="guard", owner=owner, x=0, y=0, location=tile)

        system.process_tick(1, [guard])
        self.assertEqual(len(engine.pending_actions), 1)
        weapon = engine.pending_actions[0]["weapon_item"]
        # Duck-typed check: the runtime creates the weapon via the ``world.*``
        # import path, distinct from this test's ``mygame.world.*`` class object.
        self.assertEqual(weapon.weapon_type, "melee")
        self.assertEqual(weapon.get_stat("damage"),
                         system.registry.balance.guard_melee_damage)

    def test_ranged_soldier_fires_at_distance(self):
        system, engine = _make_system(guard_ranged_range=4)
        owner = _hq_owner()
        target = FakePlayer(name="T", x=3, y=0, oid=2)
        tile = FakeTile(nearby_players=[target])
        soldier = FakeGuard(role="soldier", owner=owner, x=0, y=0, location=tile)

        system.process_tick(1, [soldier])
        self.assertEqual(len(engine.pending_actions), 1)
        weapon = engine.pending_actions[0]["weapon_item"]
        self.assertEqual(weapon.weapon_type, "ranged")
        self.assertEqual(weapon.get_stat("damage"),
                         system.registry.balance.guard_ranged_damage)
        self.assertEqual(weapon.get_stat("range"),
                         system.registry.balance.guard_ranged_range)

    def test_ranged_soldier_out_of_weapon_range(self):
        system, engine = _make_system(guard_aggro_radius=10, guard_ranged_range=4)
        owner = _hq_owner()
        target = FakePlayer(name="T", x=8, y=0, oid=2)  # inside aggro, past range
        tile = FakeTile(nearby_players=[target])
        soldier = FakeGuard(role="soldier", owner=owner, x=0, y=0, location=tile)

        system.process_tick(1, [soldier])
        self.assertEqual(len(engine.pending_actions), 0)


class TestGuardGates(unittest.TestCase):

    def test_deactivated_base_guard_inert(self):
        """A guard whose owner has no active HQ does not attack (deactivation)."""
        system, engine = _make_system()
        owner = FakePlayer(name="OwnerNoHQ")  # no get_buildings -> no HQ
        hostile = FakePlayer(name="Hostile", x=1, y=0, oid=2)
        tile = FakeTile(nearby_players=[hostile])
        guard = FakeGuard(role="guard", owner=owner, x=0, y=0, location=tile)

        system.process_tick(1, [guard])
        self.assertEqual(len(engine.pending_actions), 0)

    def test_under_construction_hq_does_not_activate(self):
        system, engine = _make_system()
        owner = _hq_owner(under_construction=True)
        hostile = FakePlayer(name="Hostile", x=1, y=0, oid=2)
        tile = FakeTile(nearby_players=[hostile])
        guard = FakeGuard(role="guard", owner=owner, x=0, y=0, location=tile)

        system.process_tick(1, [guard])
        self.assertEqual(len(engine.pending_actions), 0)

    def test_ownerless_guard_inert(self):
        system, engine = _make_system()
        hostile = FakePlayer(name="Hostile", x=1, y=0, oid=2)
        tile = FakeTile(nearby_players=[hostile])
        guard = FakeGuard(role="guard", owner=None, x=0, y=0, location=tile)

        system.process_tick(1, [guard])
        self.assertEqual(len(engine.pending_actions), 0)

    def test_non_guard_role_skipped(self):
        system, engine = _make_system()
        owner = _hq_owner()
        hostile = FakePlayer(name="Hostile", x=1, y=0, oid=2)
        tile = FakeTile(nearby_players=[hostile])
        harvester = FakeGuard(role="harvester", owner=owner, x=0, y=0,
                              location=tile)

        system.process_tick(1, [harvester])
        self.assertEqual(len(engine.pending_actions), 0)

    def test_incapacitated_guard_skipped(self):
        system, engine = _make_system()
        owner = _hq_owner()
        hostile = FakePlayer(name="Hostile", x=1, y=0, oid=2)
        tile = FakeTile(nearby_players=[hostile])
        guard = FakeGuard(role="guard", owner=owner, x=0, y=0, location=tile,
                          incapacitated=True)

        system.process_tick(1, [guard])
        self.assertEqual(len(engine.pending_actions), 0)

    def test_reserved_guard_skipped(self):
        system, engine = _make_system()
        owner = _hq_owner()
        hostile = FakePlayer(name="Hostile", x=1, y=0, oid=2)
        tile = FakeTile(nearby_players=[hostile])
        guard = FakeGuard(role="guard", owner=owner, x=0, y=0, location=tile,
                          reserve=True)

        system.process_tick(1, [guard])
        self.assertEqual(len(engine.pending_actions), 0)

    def test_dead_guard_skipped(self):
        system, engine = _make_system()
        owner = _hq_owner()
        hostile = FakePlayer(name="Hostile", x=1, y=0, oid=2)
        tile = FakeTile(nearby_players=[hostile])
        guard = FakeGuard(role="guard", owner=owner, x=0, y=0, location=tile,
                          hp=0)

        system.process_tick(1, [guard])
        self.assertEqual(len(engine.pending_actions), 0)


class TestGuardActiveOwnerIdsGate(unittest.TestCase):
    """The per-tick precomputed owner-id set replaces the per-guard HQ DB query."""

    def test_active_owner_ids_allows_listed_owner(self):
        system, engine = _make_system()
        # Owner has NO get_buildings (would fail the fallback live query), but is
        # present in the precomputed active set — so the guard still fires.
        owner = FakePlayer(name="Sentinel", oid=42)
        hostile = FakePlayer(name="Hostile", x=1, y=0, oid=2)
        tile = FakeTile(nearby_players=[hostile])
        guard = FakeGuard(role="guard", owner=owner, x=0, y=0, location=tile)

        system.process_tick(1, [guard], active_owner_ids={42})
        self.assertEqual(len(engine.pending_actions), 1)

    def test_active_owner_ids_excludes_unlisted_owner(self):
        system, engine = _make_system()
        owner = _hq_owner(oid=42)  # has an HQ, but NOT in the active set
        hostile = FakePlayer(name="Hostile", x=1, y=0, oid=2)
        tile = FakeTile(nearby_players=[hostile])
        guard = FakeGuard(role="guard", owner=owner, x=0, y=0, location=tile)

        system.process_tick(1, [guard], active_owner_ids=set())
        self.assertEqual(len(engine.pending_actions), 0)


class TestGuardLineOfSight(unittest.TestCase):
    """A guard/soldier does not shoot through a Wall (combat_barrier)."""

    def _sight_blocked_between(self, blocked_pairs):
        def f(location, x1, y1, x2, y2):
            return (x1, y1, x2, y2) in blocked_pairs
        return f

    def test_soldier_blocked_by_wall(self):
        system, engine = _make_system(guard_aggro_radius=10, guard_ranged_range=6)
        system.set_sight_blocked_func(
            self._sight_blocked_between({(0, 0, 4, 0)})
        )
        owner = _hq_owner()
        target = FakePlayer(name="T", x=4, y=0, oid=2)
        tile = FakeTile(nearby_players=[target])
        soldier = FakeGuard(role="soldier", owner=owner, x=0, y=0, location=tile)

        system.process_tick(1, [soldier])
        self.assertEqual(len(engine.pending_actions), 0)

    def test_soldier_fires_with_clear_los(self):
        system, engine = _make_system(guard_aggro_radius=10, guard_ranged_range=6)
        system.set_sight_blocked_func(lambda *a: False)
        owner = _hq_owner()
        target = FakePlayer(name="T", x=4, y=0, oid=2)
        tile = FakeTile(nearby_players=[target])
        soldier = FakeGuard(role="soldier", owner=owner, x=0, y=0, location=tile)

        system.process_tick(1, [soldier])
        self.assertEqual(len(engine.pending_actions), 1)


class _ChaseGuard(FakeGuard):
    """A guard that also supports movement so chase can be exercised."""
    def __init__(self, **kw):
        super().__init__(**kw)
        self.db.movement_queue = []
        self.db.home_x = kw.get("x", 0)
        self.db.home_y = kw.get("y", 0)
        self.queued_path = None

    def set_movement_queue(self, path):
        self.queued_path = list(path)


class TestGuardChase(unittest.TestCase):
    """A melee guard steps toward an out-of-weapon-range raider (bounded)."""

    def test_melee_guard_chases_out_of_range_target(self):
        system, engine = _make_system(guard_aggro_radius=5)
        owner = _hq_owner()
        target = FakePlayer(name="T", x=3, y=0, oid=2)  # aggro yes, melee no
        tile = FakeTile(nearby_players=[target])
        guard = _ChaseGuard(role="guard", owner=owner, x=0, y=0, location=tile)

        system.process_tick(1, [guard])
        # No attack (out of range) but a one-tile step toward the target.
        self.assertEqual(len(engine.pending_actions), 0)
        self.assertEqual(guard.queued_path, [(1, 0)])

    def test_chase_is_leashed_to_home(self):
        # aggro_radius 5, but the guard is already 5 tiles from home, so a step
        # to 6 would exceed the leash — no chase.
        system, engine = _make_system(guard_aggro_radius=5)
        owner = _hq_owner()
        target = FakePlayer(name="T", x=9, y=0, oid=2)
        tile = FakeTile(nearby_players=[target])
        guard = _ChaseGuard(role="guard", owner=owner, x=5, y=0, location=tile)
        guard.db.home_x = 0
        guard.db.home_y = 0

        system.process_tick(1, [guard])
        self.assertIsNone(guard.queued_path)

    def test_no_chase_when_already_moving(self):
        system, engine = _make_system(guard_aggro_radius=5)
        owner = _hq_owner()
        target = FakePlayer(name="T", x=3, y=0, oid=2)
        tile = FakeTile(nearby_players=[target])
        guard = _ChaseGuard(role="guard", owner=owner, x=0, y=0, location=tile)
        guard.db.movement_queue = [(2, 0)]  # already en route

        system.process_tick(1, [guard])
        self.assertIsNone(guard.queued_path)

    def test_homeless_agent_does_not_chase(self):
        """A guard/soldier with no home anchor (e.g. a player-assigned agent)
        must NOT chase — otherwise the leash resets every tick and it follows a
        raider across the map."""
        system, engine = _make_system(guard_aggro_radius=5)
        owner = _hq_owner()
        target = FakePlayer(name="T", x=3, y=0, oid=2)
        tile = FakeTile(nearby_players=[target])
        guard = _ChaseGuard(role="guard", owner=owner, x=0, y=0, location=tile)
        # Strip the home anchor to model a player-assigned agent.
        guard.db.home_x = None
        guard.db.home_y = None

        system.process_tick(1, [guard])
        self.assertIsNone(guard.queued_path)

    def test_guard_returns_home_when_no_target(self):
        """A base guard that drifted off its post steps back when no raider is
        in range, so the garrison doesn't scatter over successive raids."""
        system, engine = _make_system(guard_aggro_radius=5)
        owner = _hq_owner()
        tile = FakeTile(nearby_players=[])  # no hostiles
        guard = _ChaseGuard(role="guard", owner=owner, x=3, y=0, location=tile)
        guard.db.home_x = 0
        guard.db.home_y = 0

        system.process_tick(1, [guard])
        self.assertEqual(guard.queued_path, [(2, 0)])  # one tile toward home

    def test_guard_at_home_does_not_move(self):
        system, engine = _make_system(guard_aggro_radius=5)
        owner = _hq_owner()
        tile = FakeTile(nearby_players=[])
        guard = _ChaseGuard(role="guard", owner=owner, x=0, y=0, location=tile)
        guard.db.home_x = 0
        guard.db.home_y = 0

        system.process_tick(1, [guard])
        self.assertIsNone(guard.queued_path)


class TestGuardRobustness(unittest.TestCase):

    def test_empty_roster_no_error(self):
        system, engine = _make_system()
        system.process_tick(1, [])
        self.assertEqual(len(engine.pending_actions), 0)

    def test_no_combat_engine_is_noop(self):
        registry = _make_registry()
        system = GuardCombatSystem(registry, EventBus(), combat_engine=None)
        owner = _hq_owner()
        tile = FakeTile(nearby_players=[FakePlayer(x=1, oid=2)])
        guard = FakeGuard(role="guard", owner=owner, x=0, y=0, location=tile)
        # Should not raise.
        system.process_tick(1, [guard])

    def test_bad_guard_does_not_halt_others(self):
        """A guard that raises during processing is isolated; others still fire."""
        system, engine = _make_system()
        owner = _hq_owner()
        hostile = FakePlayer(name="Hostile", x=1, y=0, oid=2)
        tile = FakeTile(nearby_players=[hostile])
        good = FakeGuard(role="guard", owner=owner, x=0, y=0, location=tile)

        class _BoomGuard:
            """A guard whose db access raises, to exercise per-guard isolation."""
            key = "Boom"

            @property
            def db(self):
                raise RuntimeError("boom")

        # Put the exploding guard FIRST so, without isolation, the good guard
        # would never be reached.
        system.process_tick(1, [_BoomGuard(), good])
        # The good guard still queued its attack.
        self.assertEqual(len(engine.pending_actions), 1)
        self.assertEqual(engine.pending_actions[0]["target"], hostile)


class TestGuardWeaponName(unittest.TestCase):
    """A guard's synthetic weapon reports a sensible name so the combat notice
    reads '...with a melee strike' / '...with a rifle', not '...with Guard'."""

    def test_melee_guard_weapon_named_melee_strike(self):
        from world.systems.combat_engine import _GuardWeapon
        w = _GuardWeapon(10, 1, weapon_type="melee")
        self.assertEqual(w.key, "a melee strike")

    def test_ranged_guard_weapon_named_rifle(self):
        from world.systems.combat_engine import _GuardWeapon
        w = _GuardWeapon(15, 4, weapon_type="ranged")
        self.assertEqual(w.key, "a rifle")


if __name__ == "__main__":
    unittest.main()
