"""
Property-based tests for the in-combat movement lag terrain formula.

Property 9: In-combat movement lag formula with destination asymmetry —
for any in-combat player, equipment move_speed modifier, and pair of
(occupied, destination) tiles with independently generated terrain
movement modifiers, a permitted move schedules the next move at
``current_tick + max(0, int(COMBAT_MOVE_LAG_TICKS - move_speed -
destination_modifier))`` using the DESTINATION tile's modifier, not the
occupied tile's; and for any player not in combat, moves are always
permitted with no lag scheduled.

**Validates: Requirements 4.1, 4.2, 4.3, 2.7**
"""

import sys
import types
import unittest
from unittest.mock import patch

from hypothesis import given, settings
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

from mygame.commands.game_commands import CmdMove  # noqa: E402
from world import services  # noqa: E402
from world.constants import COMBAT_MOVE_LAG_TICKS  # noqa: E402


class _ServicesTestCase(unittest.TestCase):
    """TestCase giving each test a private, empty facade state.

    setUp runs once per test method (not per Hypothesis example), so the
    override dict is shared across examples; each FakeCaller installs the
    systems it needs, overwriting the previous example's entries.
    """

    def setUp(self):
        ctx = services.override({})
        ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)


def _install_systems(systems):
    """Register fake *systems* for the current test through the facade."""
    services.get_systems().update(systems)


# -------------------------------------------------------------- #
#  Fakes
# -------------------------------------------------------------- #


class FakeDB:
    """Simulates Evennia's db attribute handler."""

    def __init__(self, coord_x=5, coord_y=5, coord_planet="earth_planet"):
        self.coord_x = coord_x
        self.coord_y = coord_y
        self.coord_planet = coord_planet
        self.combat_xp = 100
        self.rank_level = 3
        self.hp = 80
        self.hp_max = 100
        self.resources = {"Iron": 10}
        self.researched_techs = set()
        self.active_powerups = {}
        self.combat_lockout_tick = 0
        self.equipment_slots = {}
        self.discovery_memory = {}


class FakeNDB:
    """Simulates Evennia's ndb attribute handler."""

    def __init__(self, systems=None):
        self.systems = systems or {}
        self.tile_lookup = None


class FakeLocation:
    """Simulates a tile/room (PlanetRoom-compatible)."""

    def __init__(self, x=5, y=5):
        self.x = x
        self.y = y
        self.building = None
        self.contents = []
        self._messages = []
        self._buildings_by_coord = {}

    def msg_contents(self, text, exclude=None, **kwargs):
        self._messages.append(text)

    def move_entity(self, obj, new_x, new_y):
        """Simulate PlanetRoom.move_entity — update coords on the object."""
        if hasattr(obj, "db"):
            obj.db.coord_x = new_x
            obj.db.coord_y = new_y

    def get_buildings_at(self, x, y):
        return list(self._buildings_by_coord.get((x, y), []))


class FakeCaller:
    """Simulates a player character (caller)."""

    def __init__(self, coord_x=5, coord_y=5, coord_planet="earth_planet",
                 systems=None, move_speed=0):
        self.key = "TestPlayer"
        self.db = FakeDB(coord_x, coord_y, coord_planet)
        self.ndb = FakeNDB()
        if systems:
            _install_systems(systems)
        self.location = FakeLocation(coord_x, coord_y)
        self._messages = []
        self._moved_to = None
        self._get_move_speed_modifier = lambda: move_speed

    def msg(self, text, **kwargs):
        self._messages.append(text)

    def move_to(self, target, **kwargs):
        self._moved_to = target

    def get_buildings(self):
        return []


class FakePlanetRegistry:
    """Simulates PlanetRegistry with a 100x100 grid."""

    def is_valid_coordinate(self, x, y, planet):
        return 0 <= x < 100 and 0 <= y < 100


class FakeTerrainResolver:
    """Coordinate-keyed terrain resolver returning per-tile movement modifiers.

    Records every ``resolve_for_player`` call so tests can prove exactly
    which tile the movement gate consulted (the destination asymmetry).
    """

    def __init__(self, movement_by_coord):
        self._movement_by_coord = dict(movement_by_coord)
        self.calls = []

    def resolve_for_player(self, player, planet, x, y):
        self.calls.append((planet, x, y))
        movement = self._movement_by_coord.get((x, y), 0.0)
        return types.SimpleNamespace(
            terrain_type="FakeTerrain", vision=0, movement=movement, defense=0.0,
        )


def _make_cmd(caller, args=""):
    """Create a CmdMove instance wired to a fake caller."""
    cmd = CmdMove()
    cmd.caller = caller
    cmd.args = args
    cmd.cmdstring = cmd.key
    return cmd


# -------------------------------------------------------------- #
#  Hypothesis strategies
# -------------------------------------------------------------- #

DIRECTION_DELTAS = {
    "north": (0, 1),
    "south": (0, -1),
    "east": (1, 0),
    "west": (-1, 0),
}

# Interior positions so every cardinal move stays in bounds.
position_strategy = st.integers(min_value=1, max_value=98)
direction_strategy = st.sampled_from(sorted(DIRECTION_DELTAS))
move_speed_strategy = st.integers(min_value=-3, max_value=5)
# Resolved movement modifiers (integer or fractional) as consumers see them.
modifier_strategy = st.one_of(
    st.integers(min_value=-3, max_value=3),
    st.floats(min_value=-3.0, max_value=3.0,
              allow_nan=False, allow_infinity=False),
)
tick_strategy = st.integers(min_value=0, max_value=100_000)


# Feature: terrain-strategy, Property 9: In-combat movement lag formula with
# destination asymmetry
class TestProperty9InCombatMovementLag(_ServicesTestCase):
    """Property 9: In-combat movement lag formula with destination asymmetry.

    **Validates: Requirements 4.1, 4.2, 4.3, 2.7**
    """

    def _run_move(self, x, y, direction, move_speed, occ_mod, dest_mod,
                  current_tick, in_combat):
        """Drive one CmdMove through the gate; return (caller, resolver, tx, ty)."""
        dx, dy = DIRECTION_DELTAS[direction]
        tx, ty = x + dx, y + dy
        resolver = FakeTerrainResolver({(x, y): occ_mod, (tx, ty): dest_mod})
        caller = FakeCaller(
            coord_x=x, coord_y=y,
            systems={
                "planet_registry": FakePlanetRegistry(),
                "terrain_modifier_system": resolver,
            },
            move_speed=move_speed,
        )
        caller.db.combat_timer_expires = current_tick + 1000 if in_combat else 0
        with patch("world.combat_timer._get_current_tick",
                   return_value=current_tick):
            _make_cmd(caller, f" {direction}").func()
        return caller, resolver, tx, ty

    @given(
        x=position_strategy,
        y=position_strategy,
        direction=direction_strategy,
        move_speed=move_speed_strategy,
        occ_mod=modifier_strategy,
        dest_mod=modifier_strategy,
        current_tick=tick_strategy,
    )
    @settings(max_examples=150)
    def test_in_combat_move_schedules_destination_lag(
            self, x, y, direction, move_speed, occ_mod, dest_mod, current_tick):
        """A permitted in-combat move schedules the zero-floored lag from the
        DESTINATION tile's modifier (Req 4.1, 4.2, 2.7)."""
        caller, resolver, tx, ty = self._run_move(
            x, y, direction, move_speed, occ_mod, dest_mod,
            current_tick, in_combat=True)

        # The move was permitted (no pending lag existed).
        self.assertEqual(
            (caller.db.coord_x, caller.db.coord_y), (tx, ty),
            f"In-combat move with no pending lag must proceed. "
            f"Messages: {caller._messages}",
        )

        # Scheduled lag uses the destination tile's modifier (Req 4.1, 4.2).
        expected = current_tick + max(
            0, int(COMBAT_MOVE_LAG_TICKS - move_speed - dest_mod))
        self.assertEqual(
            caller.db.next_move_tick, expected,
            f"next_move_tick must be current_tick + "
            f"max(0, int({COMBAT_MOVE_LAG_TICKS} - {move_speed} - {dest_mod})) "
            f"= {expected}, got {caller.db.next_move_tick} "
            f"(occupied modifier was {occ_mod})",
        )

        # Destination asymmetry (Req 2.7): the gate consulted exactly the
        # destination tile — never the occupied tile.
        self.assertEqual(
            resolver.calls, [("earth_planet", tx, ty)],
            f"Movement gate must resolve only the destination tile "
            f"({tx}, {ty}), not the occupied tile ({x}, {y}); "
            f"resolver saw {resolver.calls}",
        )

    @given(
        x=position_strategy,
        y=position_strategy,
        direction=direction_strategy,
        move_speed=move_speed_strategy,
        occ_mod=modifier_strategy,
        dest_mod=modifier_strategy,
        current_tick=tick_strategy,
        stale_lag=st.integers(min_value=0, max_value=200_000),
    )
    @settings(max_examples=150)
    def test_out_of_combat_moves_always_permitted_no_lag(
            self, x, y, direction, move_speed, occ_mod, dest_mod,
            current_tick, stale_lag):
        """Out of combat, moves always succeed and no lag is scheduled;
        any stale pending lag is cleared (Req 4.3)."""
        dx, dy = DIRECTION_DELTAS[direction]
        tx, ty = x + dx, y + dy
        resolver = FakeTerrainResolver({(x, y): occ_mod, (tx, ty): dest_mod})
        caller = FakeCaller(
            coord_x=x, coord_y=y,
            systems={
                "planet_registry": FakePlanetRegistry(),
                "terrain_modifier_system": resolver,
            },
            move_speed=move_speed,
        )
        caller.db.combat_timer_expires = 0  # not in combat
        caller.db.next_move_tick = stale_lag  # possibly stale pending lag
        with patch("world.combat_timer._get_current_tick",
                   return_value=current_tick):
            _make_cmd(caller, f" {direction}").func()

        self.assertEqual(
            (caller.db.coord_x, caller.db.coord_y), (tx, ty),
            f"Out-of-combat move must always be permitted. "
            f"Messages: {caller._messages}",
        )
        self.assertEqual(
            getattr(caller.db, "next_move_tick", 0) or 0, 0,
            "Out of combat, no movement lag may be scheduled and stale "
            f"lag must be cleared; next_move_tick={caller.db.next_move_tick}",
        )

    @given(
        x=position_strategy,
        y=position_strategy,
        direction=direction_strategy,
        move_speed=move_speed_strategy,
        occ_mod=modifier_strategy,
        dest_mod=modifier_strategy,
        current_tick=tick_strategy,
    )
    @settings(max_examples=150)
    def test_expired_combat_timer_means_no_lag(
            self, x, y, direction, move_speed, occ_mod, dest_mod, current_tick):
        """A combat timer expiring at or before the current tick counts as
        out of combat: the move is permitted with no lag (Req 4.3)."""
        dx, dy = DIRECTION_DELTAS[direction]
        tx, ty = x + dx, y + dy
        resolver = FakeTerrainResolver({(x, y): occ_mod, (tx, ty): dest_mod})
        caller = FakeCaller(
            coord_x=x, coord_y=y,
            systems={
                "planet_registry": FakePlanetRegistry(),
                "terrain_modifier_system": resolver,
            },
            move_speed=move_speed,
        )
        # Expiry in the past (or now) — player_in_combat requires expiry
        # strictly in the future.
        caller.db.combat_timer_expires = current_tick
        with patch("world.combat_timer._get_current_tick",
                   return_value=current_tick):
            _make_cmd(caller, f" {direction}").func()

        self.assertEqual((caller.db.coord_x, caller.db.coord_y), (tx, ty))
        self.assertEqual(getattr(caller.db, "next_move_tick", 0) or 0, 0)


if __name__ == "__main__":
    unittest.main()


# Feature: terrain-strategy, Property 10: Blocked moves change nothing
class TestProperty10BlockedMovesChangeNothing(_ServicesTestCase):
    """Property 10: Blocked moves change nothing.

    For any in-combat player whose ``next_move_tick`` lies in the future,
    the attempted move is rejected, and the player's coordinates and
    pending lag are exactly their pre-attempt values.

    **Validates: Requirements 4.4**
    """

    @given(
        x=position_strategy,
        y=position_strategy,
        direction=direction_strategy,
        move_speed=move_speed_strategy,
        occ_mod=modifier_strategy,
        dest_mod=modifier_strategy,
        current_tick=tick_strategy,
        remaining_wait=st.integers(min_value=1, max_value=10_000),
    )
    @settings(max_examples=150)
    def test_blocked_move_leaves_position_and_lag_unchanged(
            self, x, y, direction, move_speed, occ_mod, dest_mod,
            current_tick, remaining_wait):
        """An in-combat move attempted before the pending lag expires is
        blocked; coordinates and pending lag stay exactly as they were
        (Req 4.4)."""
        dx, dy = DIRECTION_DELTAS[direction]
        tx, ty = x + dx, y + dy
        resolver = FakeTerrainResolver({(x, y): occ_mod, (tx, ty): dest_mod})
        caller = FakeCaller(
            coord_x=x, coord_y=y,
            systems={
                "planet_registry": FakePlanetRegistry(),
                "terrain_modifier_system": resolver,
            },
            move_speed=move_speed,
        )
        # In combat, with a pending lag strictly in the future.
        caller.db.combat_timer_expires = current_tick + remaining_wait + 1000
        pending_tick = current_tick + remaining_wait
        caller.db.next_move_tick = pending_tick

        with patch("world.combat_timer._get_current_tick",
                   return_value=current_tick):
            _make_cmd(caller, f" {direction}").func()

        # The move was rejected: coordinates are the pre-attempt values.
        self.assertEqual(
            (caller.db.coord_x, caller.db.coord_y), (x, y),
            f"A blocked in-combat move must not change position; player "
            f"moved to ({caller.db.coord_x}, {caller.db.coord_y}). "
            f"Messages: {caller._messages}",
        )
        self.assertIsNone(
            caller._moved_to,
            "A blocked move must not relocate the caller object",
        )

        # Pending lag is exactly the pre-attempt value: not cleared, not
        # rescheduled from the destination tile's modifier.
        self.assertEqual(
            caller.db.next_move_tick, pending_tick,
            f"A blocked move must leave pending lag unchanged; expected "
            f"next_move_tick={pending_tick}, got {caller.db.next_move_tick}",
        )
