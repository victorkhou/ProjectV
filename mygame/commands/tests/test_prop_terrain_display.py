"""
Property-based tests for the terrain display surfaces.

Property 16: Inspection shows resolved values and unexplored tiles leak
nothing — for any discovered (visible or fog) tile, the map-header
inspection surface shows the terrain type and the three modifier values
produced by ``resolve_for_player`` (asserted on states where the resolved
values differ from the raw TerrainDef values, proving the display never
falls back to raw definition fields); and for any undiscovered tile, the
surface yields only an unexplored indication, revealing neither the
terrain type nor any modifier value.

**Validates: Requirements 8.1, 8.4**

Property 17: Score and placement surfaces render resolved values — any
resolved triple (including zeros) appears in full in the score display
for the player's current tile, and any placement attempt, accepted or
rejected, includes the target tile's resolved defense modifier in the
placement feedback.

**Validates: Requirements 8.2, 8.3**
"""

import sys
import types
import unittest

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

from mygame.commands.game_commands import CmdMap, CmdScore  # noqa: E402
from world import services  # noqa: E402
from world.data_registry import DataRegistry  # noqa: E402
from world.definitions import BuildingDef  # noqa: E402
from world.event_bus import EventBus  # noqa: E402
from world.systems.building_system import BuildingSystem  # noqa: E402


class _ServicesTestCase(unittest.TestCase):
    """TestCase giving each test a private, empty facade state.

    setUp runs once per test method (not per Hypothesis example), so the
    override dict is shared across examples; each example installs the
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

PLANET = "earth_planet"


class FakeDB:
    """Simulates Evennia's db attribute handler."""

    def __init__(self, coord_x=5, coord_y=5, coord_planet=PLANET):
        self.coord_x = coord_x
        self.coord_y = coord_y
        self.coord_planet = coord_planet
        self.discovery_memory = {}


class FakeNDB:
    """Simulates Evennia's ndb attribute handler."""

    def __init__(self):
        self.systems = {}
        self.tile_lookup = None


class FakeCaller:
    """Simulates a player character; captures every msg() text payload."""

    def __init__(self, coord_x=5, coord_y=5, systems=None):
        self.key = "TestPlayer"
        self.db = FakeDB(coord_x, coord_y)
        self.ndb = FakeNDB()
        if systems:
            _install_systems(systems)
        self._messages = []

    def msg(self, text=None, **kwargs):
        if text is None:
            text = kwargs.get("text")
        if isinstance(text, tuple):
            text = text[0]
        if text is not None:
            self._messages.append(str(text))

    def get_buildings(self):
        return []


class FakeRenderer:
    """Minimal map renderer (bracket-free body so header asserts stay clean)."""

    def render(self, player, buildings):
        return "## ## ##"


class FakeGen:
    """Terrain generator returning a fixed terrain type and resource."""

    def __init__(self, terrain_type, resource=None):
        self._terrain_type = terrain_type
        self._resource = resource

    def get_terrain(self, x, y):
        return self._terrain_type

    def get_terrain_and_resource(self, x, y):
        return self._terrain_type, self._resource


class FakeFog:
    """Fog system reporting a fixed visibility state for every tile."""

    def __init__(self, state):
        self._state = state

    def get_visible_tiles(self, player, buildings, **kwargs):
        return set()

    def get_tile_visibility(self, player, x, y, visible_tiles):
        return self._state

    def get_discovered_tile_set(self, player):
        return set()


class FakeResolver:
    """Terrain modifier resolver returning a fixed resolved triple.

    Stands in for TerrainModifierSystem.resolve_for_player — the clamped,
    affinity-adjusted values, generated to DIFFER from the raw TerrainDef
    triple so the test can prove the display shows resolver output.
    """

    def __init__(self, terrain_type, vision, movement, defense):
        self._mods = types.SimpleNamespace(
            terrain_type=terrain_type, vision=vision,
            movement=movement, defense=defense,
        )

    def resolve_for_player(self, player, planet, x, y):
        return self._mods


def _make_cmd(caller):
    """Create a CmdMap instance wired to a fake caller."""
    cmd = CmdMap()
    cmd.caller = caller
    cmd.args = ""
    cmd.cmdstring = cmd.key
    return cmd


def _suffix(vision, movement, defense):
    """The exact modifier suffix the inspection surface renders."""
    return (
        f" [vision {vision:+d}, movement {movement:+g}, "
        f"defense {defense:+g}]"
    )


def _map_header(caller):
    """Drive CmdMap and return the captured map-header line."""
    _make_cmd(caller).func()
    headers = [m.split("\n", 1)[0] for m in caller._messages if "Map —" in m]
    return headers[0] if headers else None


# -------------------------------------------------------------- #
#  Hypothesis strategies
# -------------------------------------------------------------- #

# Names that never collide with fixed header words ('Map', 'unexplored',
# 'discovered', the planet name) so absence asserts are meaningful.
terrain_strategy = st.sampled_from(
    ["Forest", "Mountain", "Swamp", "Tundra", "Volcanic"])
resource_strategy = st.sampled_from([None, "Wood", "Iron", "Crystal"])
position_strategy = st.integers(min_value=0, max_value=99)
discovered_state_strategy = st.sampled_from(["visible", "fog"])

_half_steps = st.integers(min_value=-6, max_value=6).map(lambda n: n / 2)

# Raw TerrainDef modifier triple (vision int; movement/defense in half
# steps so ':+g' formatting is injective over the generated domain).
raw_triple_strategy = st.tuples(
    st.integers(min_value=-5, max_value=5), _half_steps, _half_steps)

# Non-zero adjustment applied by the resolver (class affinity / tech /
# clamp), guaranteeing resolved != raw in at least one modifier kind.
delta_triple_strategy = st.tuples(
    st.integers(min_value=-4, max_value=4), _half_steps, _half_steps,
).filter(lambda d: any(v != 0 for v in d))


def _build_caller(x, y, terrain_type, resource, fog_state, resolver):
    return FakeCaller(coord_x=x, coord_y=y, systems={
        "procedural_map_renderer": FakeRenderer(),
        "_terrain_generators": {PLANET: FakeGen(terrain_type, resource)},
        "fog_system": FakeFog(fog_state),
        "terrain_modifier_system": resolver,
    })


# Feature: terrain-strategy, Property 16: Inspection shows resolved values
# and unexplored tiles leak nothing
class TestProperty16InspectionOutput(_ServicesTestCase):
    """Property 16: Inspection shows resolved values and unexplored tiles
    leak nothing.

    **Validates: Requirements 8.1, 8.4**
    """

    @given(
        x=position_strategy,
        y=position_strategy,
        terrain_type=terrain_strategy,
        resource=resource_strategy,
        fog_state=discovered_state_strategy,
        raw=raw_triple_strategy,
        delta=delta_triple_strategy,
    )
    @settings(max_examples=150)
    def test_discovered_tile_shows_terrain_and_resolver_values(
            self, x, y, terrain_type, resource, fog_state, raw, delta):
        """Req 8.1: a discovered (visible or fog) tile shows the terrain
        type plus the three RESOLVER-produced values — never the raw
        TerrainDef values, which differ by construction."""
        raw_v, raw_m, raw_d = raw
        res_v, res_m, res_d = raw_v + delta[0], raw_m + delta[1], raw_d + delta[2]
        resolver = FakeResolver(terrain_type, res_v, res_m, res_d)
        caller = _build_caller(x, y, terrain_type, resource, fog_state, resolver)

        header = _map_header(caller)
        self.assertIsNotNone(
            header, f"CmdMap sent no map header; messages: {caller._messages}")

        # Terrain type (and its resource) are displayed (Req 8.1).
        self.assertIn(
            f"| {terrain_type}", header,
            f"Discovered ({fog_state}) tile must show its terrain type; "
            f"header: {header!r}")
        if resource:
            self.assertIn(f"({resource})", header)
        self.assertNotIn("unexplored", header)

        # The displayed numbers are the resolver's clamped, affinity-adjusted
        # values, not the raw TerrainDef fields.
        resolved_suffix = _suffix(res_v, res_m, res_d)
        raw_suffix = _suffix(raw_v, raw_m, raw_d)
        self.assertIn(
            resolved_suffix, header,
            f"Header must display the resolver-produced values "
            f"{resolved_suffix!r}; header: {header!r}")
        self.assertNotIn(
            raw_suffix, header,
            f"Header must not display the raw TerrainDef values "
            f"{raw_suffix!r}; header: {header!r}")

    @given(
        x=position_strategy,
        y=position_strategy,
        terrain_type=terrain_strategy,
        resource=resource_strategy,
        raw=raw_triple_strategy,
        delta=delta_triple_strategy,
    )
    @settings(max_examples=150)
    def test_unexplored_tile_leaks_nothing(
            self, x, y, terrain_type, resource, raw, delta):
        """Req 8.4: an undiscovered tile yields only an unexplored
        indication — no terrain type, no resource, no modifier values —
        even though generator and resolver data exist for the tile."""
        raw_v, raw_m, raw_d = raw
        resolver = FakeResolver(
            terrain_type, raw_v + delta[0], raw_m + delta[1], raw_d + delta[2])
        caller = _build_caller(
            x, y, terrain_type, resource, "unexplored", resolver)

        header = _map_header(caller)
        self.assertIsNotNone(
            header, f"CmdMap sent no map header; messages: {caller._messages}")

        self.assertIn(
            "unexplored", header,
            f"Undiscovered tile must be reported as unexplored; "
            f"header: {header!r}")
        self.assertNotIn(
            terrain_type, header,
            f"Unexplored tile must not reveal its terrain type; "
            f"header: {header!r}")
        if resource:
            self.assertNotIn(resource, header)
        # No modifier values leak: the bracketed suffix (and its labels)
        # must be entirely absent.
        for leaked in ("[", "vision", "movement", "defense"):
            self.assertNotIn(
                leaked, header,
                f"Unexplored tile must not leak modifier values; found "
                f"{leaked!r} in header: {header!r}")


# -------------------------------------------------------------- #
#  Property 17 fakes: score command + building placement
# -------------------------------------------------------------- #


def _score_output(caller):
    """Drive CmdScore for *caller* and return the captured sheet text."""
    cmd = CmdScore()
    cmd.caller = caller
    cmd.args = ""
    cmd.cmdstring = cmd.key
    cmd.func()
    return "\n".join(caller._messages)


class _BuilderDB:
    """db handler carrying the attributes BuildingSystem reads."""

    def __init__(self):
        self.combat_lockout_tick = 0
        self.rank_level = 1
        self.level = 1
        self.activity_state = "idle"
        self.activity_target = None
        self.activity_progress = 0


class FakeBuilder:
    """Minimal player for placement attempts (resource + building API)."""

    def __init__(self, resources=None):
        self.key = "Builder"
        self.db = _BuilderDB()
        self._resources = dict(resources or {})
        self.location = None

    def get_resource(self, resource_type):
        return self._resources.get(resource_type, 0)

    def has_resources(self, costs):
        return all(self._resources.get(r, 0) >= amt for r, amt in costs.items())

    def deduct_resources(self, costs):
        if not self.has_resources(costs):
            return False
        for r, amt in costs.items():
            self._resources[r] = self._resources.get(r, 0) - amt
        return True

    def get_buildings(self):
        return []


class _BuildingAttributes:
    """Simulates Evennia's Attribute handler for a created building."""

    def __init__(self):
        self._data = {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def add(self, key, value):
        self._data[key] = value

    def has(self, key):
        return key in self._data


class BuildTile:
    """Target tile with coordinates and planet for placement attempts."""

    def __init__(self, x, y, planet=PLANET, terrain_type="Plains"):
        self._terrain_type = terrain_type
        self._building = None
        self.x = x
        self.y = y
        self.db = types.SimpleNamespace(coord_x=x, coord_y=y, planet=planet)

    @property
    def terrain_type(self):
        return self._terrain_type

    @property
    def building(self):
        return self._building


class FakeBaseResolver:
    """resolve_base stand-in returning a fixed base triple; records calls.

    Stands in for TerrainModifierSystem.resolve_base — the clamped BASE
    modifiers (no player affinities) placement feedback must render.
    """

    def __init__(self, terrain_type, defense):
        self._mods = types.SimpleNamespace(
            terrain_type=terrain_type, vision=0, movement=0.0,
            defense=defense,
        )
        self.calls = []

    def resolve_base(self, planet, x, y):
        self.calls.append((planet, x, y))
        return self._mods


_HQ_COST = {"Straw": 50, "Wood": 50, "Stone": 30}


def _make_placement_system(resolver):
    """BuildingSystem over a fake registry/factory with *resolver* injected."""
    registry = DataRegistry()
    registry.buildings = {
        "HQ": BuildingDef(
            name="Headquarters", abbreviation="HQ",
            cost=dict(_HQ_COST),
            max_health=500, requires_hq=False, required_terrain=None,
            category="headquarters", produces=None,
            unlocks=[], map_symbol="HQ",
            build_time_seconds=180, rank_requirement=1,
            capabilities=frozenset({"headquarters", "storage"}),
        ),
    }

    def fake_create(building_def, tile, owner):
        building = types.SimpleNamespace(attributes=_BuildingAttributes())
        tile._building = building
        return building

    system = BuildingSystem(
        registry=registry,
        event_bus=EventBus(),
        create_building_func=fake_create,
        current_tick_func=lambda: 0,
    )
    system.set_terrain_modifier_resolver(resolver)
    return system


# Base defense modifier in half steps so ':+g' formatting is injective
# over the generated domain; includes zero.
defense_strategy = st.integers(min_value=-12, max_value=12).map(lambda n: n / 2)
vision_strategy = st.integers(min_value=-5, max_value=5)
placement_method_strategy = st.sampled_from(["construct", "start_construction"])


# Feature: terrain-strategy, Property 17: Score and placement surfaces
# render resolved values
class TestProperty17ScoreAndPlacementSurfaces(_ServicesTestCase):
    """Property 17: Score and placement surfaces render resolved values.

    **Validates: Requirements 8.2, 8.3**
    """

    @given(
        x=position_strategy,
        y=position_strategy,
        terrain_type=terrain_strategy,
        vision=vision_strategy,
        movement=_half_steps,
        defense=_half_steps,
        all_zero=st.booleans(),
    )
    @settings(max_examples=150)
    def test_score_shows_full_resolved_triple(
            self, x, y, terrain_type, vision, movement, defense, all_zero):
        """Req 8.2: the score display renders all three resolver-produced
        values for the current tile, zeros included (the ``all_zero`` flag
        forces the all-zeros triple so it is always exercised)."""
        if all_zero:
            vision, movement, defense = 0, 0.0, 0.0
        resolver = FakeResolver(terrain_type, vision, movement, defense)
        caller = FakeCaller(coord_x=x, coord_y=y, systems={
            "terrain_modifier_system": resolver,
        })

        output = _score_output(caller)

        self.assertIn(
            "Terrain:", output,
            f"Score must include a Terrain section; output: {output!r}")
        for row in (
            f"Vision - {vision:+d}",
            f"Movement - {movement:+g}",
            f"Defense - {defense:+g}",
        ):
            self.assertIn(
                row, output,
                f"Score must render the resolved value row {row!r} "
                f"(all three print, zeros included); output: {output!r}")

    @given(
        x=position_strategy,
        y=position_strategy,
        terrain_type=terrain_strategy,
        defense=defense_strategy,
        method=placement_method_strategy,
        accepted=st.booleans(),
    )
    @settings(max_examples=150)
    def test_placement_feedback_includes_resolved_defense(
            self, x, y, terrain_type, defense, method, accepted):
        """Req 8.3: every placement attempt — accepted or rejected — carries
        the target tile's resolved defense modifier in its feedback."""
        resolver = FakeBaseResolver(terrain_type, defense)
        system = _make_placement_system(resolver)
        resources = dict(_HQ_COST) if accepted else {}
        player = FakeBuilder(resources=resources)
        tile = BuildTile(x, y)

        ok, msg = getattr(system, method)(player, tile, "HQ")

        self.assertEqual(
            ok, accepted,
            f"{method} acceptance mismatch (expected accepted={accepted}); "
            f"message: {msg!r}")
        note = f" [terrain defense {defense:+g}]"
        self.assertIn(
            note, msg,
            f"{'Accepted' if accepted else 'Rejected'} {method} feedback "
            f"must include the resolved defense note {note!r}; "
            f"message: {msg!r}")
        # The value shown is resolved for the TARGET tile's coordinates.
        self.assertIn(
            (PLANET, x, y), resolver.calls,
            f"Resolver must be queried for the target tile ({PLANET}, {x}, "
            f"{y}); calls: {resolver.calls}")


if __name__ == "__main__":
    unittest.main()
