"""Unit tests for TerrainModifierSystem fallback paths (terrain-strategy task 3.8).

Covers the resolver's fail-soft contract with plain fakes (no Evennia database
objects): missing generator and missing TerrainDef return ``ZERO_MODIFIERS``
(Req 2.3, 2.5), and an affinity read that raises degrades to base modifiers
without propagating, logged once per system instance (Req 6.4). Property tests
for resolution live in test_prop_terrain_modifiers.py.
"""

import logging

from mygame.world.definitions import TerrainAffinity, TerrainDef
from mygame.world.systems.terrain_modifiers import (
    ZERO_MODIFIERS,
    TerrainModifierSystem,
)

LOGGER_NAME = "mygame.terrain_modifiers"


# ------------------------------------------------------------------ #
#  Fakes
# ------------------------------------------------------------------ #

class FakeBalance:
    """Balance bounds wide enough that no test value is clamped."""

    terrain_vision_bound = 5
    terrain_movement_bound = 3.0
    terrain_defense_bound = 6.0


class FakeRegistry:
    """Registry fake exposing .terrain, .balance, and .get_class."""

    def __init__(self, terrain=None, classes=None):
        self.terrain = terrain or {}
        self.balance = FakeBalance()
        self._classes = classes or {}

    def get_class(self, key):
        return self._classes.get(key)


class RaisingClassRegistry(FakeRegistry):
    """Registry whose class lookup raises (broken affinity read)."""

    def get_class(self, key):
        raise RuntimeError("class lookup exploded")


class FakeGenerator:
    """Terrain generator fake returning a fixed terrain type."""

    def __init__(self, terrain_type):
        self._terrain_type = terrain_type

    def get_terrain(self, x, y):
        return self._terrain_type


class FakeDb:
    """Plain attribute object standing in for Evennia's .db handler."""

    def __init__(self, player_class="ranger", tech_bonuses=None):
        self.player_class = player_class
        self.tech_bonuses = tech_bonuses if tech_bonuses is not None else {}


class RaisingTechDb:
    """A .db whose tech_bonuses read raises (broken tech affinity read)."""

    player_class = "ranger"

    @property
    def tech_bonuses(self):
        raise RuntimeError("tech_bonuses read exploded")


class FakePlayer:
    def __init__(self, db):
        self.db = db


_FOREST = TerrainDef(
    terrain_type="Forest", map_symbol="FF",
    vision_modifier=-2, movement_modifier=-1.0, defense_modifier=3.0,
)

_RANGER_CLASS = type("Cls", (), {"terrain_affinities": [
    TerrainAffinity(terrain_type="Forest", kind="movement", adjustment=1.0),
]})()


def _system(registry=None, generators=None):
    registry = registry if registry is not None else FakeRegistry(
        terrain={"Forest": _FOREST},
    )
    generators = generators if generators is not None else {
        "terra": FakeGenerator("Forest"),
    }
    return TerrainModifierSystem(registry, generators)


# ------------------------------------------------------------------ #
#  Missing generator → ZERO_MODIFIERS (Req 2.3)
# ------------------------------------------------------------------ #

class TestNoGeneratorForPlanet:
    def test_resolve_base_returns_zero_modifiers(self):
        system = _system(generators={})
        assert system.resolve_base("terra", 3, 4) is ZERO_MODIFIERS

    def test_resolve_for_player_returns_zero_modifiers(self):
        system = _system(generators={"terra": FakeGenerator("Forest")})
        player = FakePlayer(FakeDb())
        assert system.resolve_for_player(player, "luna", 3, 4) is ZERO_MODIFIERS


# ------------------------------------------------------------------ #
#  Terrain type without TerrainDef → ZERO_MODIFIERS (Req 2.5)
# ------------------------------------------------------------------ #

class TestTerrainTypeWithoutTerrainDef:
    def test_resolve_base_returns_zero_modifiers(self):
        system = _system(generators={"terra": FakeGenerator("Swamp")})
        assert system.resolve_base("terra", 0, 0) is ZERO_MODIFIERS

    def test_resolve_for_player_returns_zero_modifiers(self):
        system = _system(generators={"terra": FakeGenerator("Swamp")})
        player = FakePlayer(FakeDb())
        assert system.resolve_for_player(player, "terra", 0, 0) is ZERO_MODIFIERS


# ------------------------------------------------------------------ #
#  Affinity read raising → base modifiers, logged once (Req 6.4)
# ------------------------------------------------------------------ #

class TestAffinityReadFailureDegradesToBase:
    def _assert_base(self, result):
        assert result.terrain_type == "Forest"
        assert result.vision == _FOREST.vision_modifier
        assert result.movement == _FOREST.movement_modifier
        assert result.defense == _FOREST.defense_modifier

    def test_class_read_raising_degrades_to_base(self, caplog):
        registry = RaisingClassRegistry(terrain={"Forest": _FOREST})
        system = _system(registry=registry)
        player = FakePlayer(FakeDb())
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            result = system.resolve_for_player(player, "terra", 1, 1)
        self._assert_base(result)

    def test_tech_read_raising_degrades_to_base(self, caplog):
        system = _system()
        player = FakePlayer(RaisingTechDb())
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            result = system.resolve_for_player(player, "terra", 1, 1)
        self._assert_base(result)

    def test_failure_result_matches_resolve_base(self):
        registry = RaisingClassRegistry(terrain={"Forest": _FOREST})
        system = _system(registry=registry)
        player = FakePlayer(FakeDb())
        assert (
            system.resolve_for_player(player, "terra", 1, 1)
            == system.resolve_base("terra", 1, 1)
        )

    def test_failure_logged_once_per_system_instance(self, caplog):
        registry = RaisingClassRegistry(terrain={"Forest": _FOREST})
        system = _system(registry=registry)
        player = FakePlayer(FakeDb())
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            system.resolve_for_player(player, "terra", 1, 1)
            system.resolve_for_player(player, "terra", 2, 2)
        warnings = [
            r for r in caplog.records
            if r.name == LOGGER_NAME and r.levelno >= logging.WARNING
        ]
        assert len(warnings) == 1

    def test_working_affinities_still_apply(self):
        """Sanity check: the degrade path is failure-only, not universal."""
        registry = FakeRegistry(
            terrain={"Forest": _FOREST},
            classes={"ranger": _RANGER_CLASS},
        )
        system = _system(registry=registry)
        player = FakePlayer(FakeDb(player_class="ranger"))
        result = system.resolve_for_player(player, "terra", 1, 1)
        assert result.movement == _FOREST.movement_modifier + 1.0
