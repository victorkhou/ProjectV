"""
Unit tests for CombatCharacter typeclass.

Tests resource helpers, structured status, initialization,
coordinate tracking, discovery memory, and overworld spawn.

Requirements: 1.4, 2.4, 7.8, 8.2, 10.1, 10.4, 11.1, 11.7, 27.1
"""

import sys
import types
import unittest
from unittest.mock import MagicMock, patch


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

    class _AttrStore:
        def __init__(self):
            self._data = {}
        def get(self, key, default=None, **kw):
            return self._data.get(key, default)
        def add(self, key, value, **kw):
            self._data[key] = value
        def has(self, key):
            return key in self._data

    class _DbProxy:
        def __init__(self, store):
            object.__setattr__(self, "_store", store)
        def __getattr__(self, key):
            return object.__getattribute__(self, "_store").get(key)
        def __setattr__(self, key, value):
            object.__getattribute__(self, "_store").add(key, value)

    class DefaultCharacter:
        def __init__(self, **kwargs):
            self._attr_store = _AttrStore()
            self.attributes = self._attr_store
            self.db = _DbProxy(self._attr_store)
            self.key = kwargs.get("key", "TestChar")
        def at_object_creation(self):
            pass
        def at_post_login(self, session, **kwargs):
            pass
        def at_post_puppet(self, **kwargs):
            # The real Evennia DefaultCharacter.at_post_puppet emits
            # "You become X" and an at_look; the stub records that it ran.
            self._became = True
            if hasattr(self, "msg"):
                self.msg(f"You become {self.key}.")

    _mod("evennia")
    _mod("evennia.objects")
    _mod("evennia.objects.objects", {
        "DefaultCharacter": DefaultCharacter,
        "DefaultObject": type("DefaultObject", (), {}),
        "DefaultRoom": type("DefaultRoom", (), {}),
    })
    _mod("evennia.commands")
    _mod("evennia.commands.cmdset")
    _mod("evennia.utils")
    _mod("evennia.utils.utils")
    _mod("evennia.utils.logger")

    for name, mod in stubs.items():
        sys.modules.setdefault(name, mod)


_ensure_evennia_stubs()

from mygame.typeclasses.characters import (  # noqa: E402
    CombatCharacter, RESOURCE_TYPES, DEFAULT_HEALTH,
)


# -------------------------------------------------------------- #
#  Helpers
# -------------------------------------------------------------- #

def _make_char(name="TestChar") -> CombatCharacter:
    char = CombatCharacter(key=name)
    char.at_object_creation()
    return char


# -------------------------------------------------------------- #
#  Tests: at_object_creation
# -------------------------------------------------------------- #

class TestAtObjectCreation(unittest.TestCase):
    def test_hp_initialized(self):
        char = _make_char()
        self.assertEqual(char.db.hp, DEFAULT_HEALTH)
        self.assertEqual(char.db.hp_max, DEFAULT_HEALTH)

    def test_combat_xp_initialized(self):
        char = _make_char()
        self.assertEqual(char.db.combat_xp, 0)

    def test_rank_level_initialized(self):
        char = _make_char()
        self.assertEqual(char.db.rank_level, 1)

    def test_resources_initialized_with_starting_values(self):
        char = _make_char()
        self.assertEqual(char.get_resource("Wood"), 40)
        self.assertEqual(char.get_resource("Stone"), 25)
        self.assertEqual(char.get_resource("Iron"), 10)
        self.assertEqual(char.get_resource("Energy"), 0)
        self.assertEqual(char.get_resource("Circuits"), 0)
        self.assertEqual(char.get_resource("Nexium"), 0)

    def test_powerup_attributes_initialized(self):
        char = _make_char()
        self.assertEqual(char.db.active_powerups, {})
        self.assertEqual(char.db.powerup_cooldowns, {})
        self.assertEqual(char.db.researched_techs, set())
        self.assertEqual(char.db.combat_lockout_tick, 0)

    def test_coord_x_initialized(self):
        char = _make_char()
        self.assertEqual(char.db.coord_x, 0)

    def test_coord_y_initialized(self):
        char = _make_char()
        self.assertEqual(char.db.coord_y, 0)

    def test_coord_planet_initialized(self):
        char = _make_char()
        self.assertEqual(char.db.coord_planet, "")

    def test_discovery_memory_initialized(self):
        char = _make_char()
        self.assertEqual(char.db.discovery_memory, {})


# -------------------------------------------------------------- #
#  Tests: resource helpers
# -------------------------------------------------------------- #

class TestResourceHelpers(unittest.TestCase):
    def test_get_resource_default_zero(self):
        char = _make_char()
        self.assertEqual(char.get_resource("unobtanium"), 0)

    def test_add_resource(self):
        char = _make_char()
        char.add_resource("wood", 50)
        self.assertEqual(char.get_resource("wood"), 90)  # 40 starting + 50

    def test_add_resource_accumulates(self):
        char = _make_char()
        char.add_resource("iron", 10)
        char.add_resource("iron", 20)
        self.assertEqual(char.get_resource("iron"), 40)  # 10 starting + 10 + 20

    def test_has_resources_true(self):
        char = _make_char()
        char.add_resource("stone", 100)
        self.assertTrue(char.has_resources({"stone": 50}))

    def test_has_resources_false(self):
        char = _make_char()
        self.assertFalse(char.has_resources({"Nexium": 1}))

    def test_has_resources_exact_amount(self):
        char = _make_char()
        char.add_resource("energy", 10)
        self.assertTrue(char.has_resources({"energy": 10}))

    def test_deduct_resources_success(self):
        char = _make_char()
        char.add_resource("energy", 50)
        char.add_resource("circuits", 30)
        result = char.deduct_resources({"energy": 20, "circuits": 10})
        self.assertTrue(result)
        self.assertEqual(char.get_resource("energy"), 30)
        self.assertEqual(char.get_resource("circuits"), 20)

    def test_deduct_resources_failure_no_change(self):
        char = _make_char()
        char.add_resource("nexium", 5)
        result = char.deduct_resources({"nexium": 10})
        self.assertFalse(result)
        self.assertEqual(char.get_resource("nexium"), 5)

    def test_deduct_multi_resource_failure_no_partial(self):
        """If one resource is insufficient, none are deducted."""
        char = _make_char()
        char.add_resource("energy", 100)
        # Nexium starts at 0, add only 1
        char.add_resource("nexium", 1)
        result = char.deduct_resources({"energy": 50, "nexium": 10})
        self.assertFalse(result)
        self.assertEqual(char.get_resource("energy"), 100)
        self.assertEqual(char.get_resource("nexium"), 1)

    def test_get_resource_unknown_type_returns_zero(self):
        char = _make_char()
        self.assertEqual(char.get_resource("unobtanium"), 0)


# -------------------------------------------------------------- #
#  Tests: get_structured_status
# -------------------------------------------------------------- #

class TestGetStructuredStatus(unittest.TestCase):
    def test_returns_dict(self):
        char = _make_char("Alice")
        status = char.get_structured_status()
        self.assertIsInstance(status, dict)

    def test_contains_expected_keys(self):
        char = _make_char("Alice")
        status = char.get_structured_status()
        for key in ("name", "hp", "hp_max", "combat_xp", "rank_level",
                     "resources", "active_powerups", "researched_techs",
                     "combat_lockout_tick"):
            self.assertIn(key, status)

    def test_name_matches(self):
        char = _make_char("Bob")
        self.assertEqual(char.get_structured_status()["name"], "Bob")

    def test_resources_reflect_additions(self):
        char = _make_char()
        char.add_resource("iron", 42)
        status = char.get_structured_status()
        self.assertEqual(status["resources"]["Iron"], 52)  # 10 starting + 42


# -------------------------------------------------------------- #
#  Tests: equipment property
# -------------------------------------------------------------- #

class TestEquipmentProperty(unittest.TestCase):
    def test_equipment_handler_exists(self):
        char = _make_char()
        handler = char.equipment
        self.assertIsNotNone(handler)

    def test_equipment_handler_cached(self):
        char = _make_char()
        h1 = char.equipment
        h2 = char.equipment
        self.assertIs(h1, h2)


# -------------------------------------------------------------- #
#  Tests: get_buildings
# -------------------------------------------------------------- #

class TestGetBuildings(unittest.TestCase):
    def test_returns_empty_list(self):
        char = _make_char()
        self.assertEqual(char.get_buildings(), [])


# -------------------------------------------------------------- #
#  Tests: _ensure_overworld_position
# -------------------------------------------------------------- #

class TestEnsureOverworldPosition(unittest.TestCase):
    """Test the _ensure_overworld_position login hook."""

    def _make_limbo_char(self):
        """Create a char whose location looks like Limbo (id=2)."""
        char = _make_char()
        limbo = MagicMock()
        limbo.id = 2
        char.location = limbo
        char.move_to = MagicMock()
        char.msg = MagicMock()
        return char

    def test_moves_to_spawn_from_limbo(self):
        char = self._make_limbo_char()

        from world.definitions import CoordinateSpaceDef
        space = CoordinateSpaceDef(
            planet_key="earth",
            planet_type="earth",
            width=100, height=100,
            terrain_seed=42,
            terrain_weights={"Plains": 1.0},
            spawn_x=50, spawn_y=50,
            default_planet=True,
        )

        mock_registry = MagicMock()
        mock_registry.list_planets.return_value = ["earth"]
        mock_registry.get_space.return_value = space

        mock_room = MagicMock()

        mock_systems = {
            "planet_registry": mock_registry,
            "planet_rooms": {"earth": mock_room},
        }

        from world import services
        with services.override(mock_systems):
            char._ensure_overworld_position()

        char.move_to.assert_called_once_with(mock_room, quiet=True)
        self.assertEqual(char.db.coord_x, 50)
        self.assertEqual(char.db.coord_y, 50)
        self.assertEqual(char.db.coord_planet, "earth")

    def test_no_move_when_not_in_limbo(self):
        """If the character is not in Limbo, nothing happens."""
        char = _make_char()
        loc = MagicMock()
        loc.id = 999  # Not Limbo
        char.location = loc
        char.move_to = MagicMock()
        char._ensure_overworld_position()
        char.move_to.assert_not_called()

    def test_no_crash_on_import_failure(self):
        """If PlanetRegistry can't be imported, method silently handles it."""
        char = self._make_limbo_char()
        # The method catches all exceptions, so even if imports fail
        # it should not raise
        with patch.dict(sys.modules, {"world.coordinate.planet_registry": None}):
            char._ensure_overworld_position()
        # No exception raised — that's the test


class TestAtPostPuppetLifecycle(unittest.TestCase):
    """at_post_puppet is the REAL login hook (Characters have no at_post_login).

    Regression: the lifecycle routing + welcome nudge + login event used to live
    on at_post_login, which Evennia never calls on a Character — so on a real
    server the wizard prompt never appeared. It now lives on at_post_puppet.
    """

    def _make_char(self):
        char = _make_char()
        char.msg = MagicMock()
        char._ensure_overworld_position = MagicMock()
        char.ensure_attributes = MagicMock()
        return char

    def test_flow_disabled_defers_to_super_puppet(self):
        # Flow off -> normal puppet: parent runs ("You become X"), no wizard.
        char = self._make_char()
        with patch("world.lobby_flow.lobby_flow_enabled", return_value=False):
            char.at_post_puppet()
        self.assertTrue(getattr(char, "_became", False),
                        "flow-off login must run the default puppet (become+look)")

    def test_spawning_shows_wizard_and_suppresses_become(self):
        char = self._make_char()
        char.db.player_state = None  # fresh -> routes to SPAWNING
        char.stow_from_world = MagicMock()
        with patch("world.lobby_flow.lobby_flow_enabled", return_value=True):
            char.at_post_puppet()
        # A staging login shows the numbered wizard (no classes are defined in
        # this stub env, so it opens at the spawn-point step) and does NOT run
        # the default "You become X" map-look puppet.
        msgs = " ".join(str(c.args[0]) for c in char.msg.call_args_list if c.args)
        self.assertIn("choose your", msgs.lower())      # a wizard step is shown
        self.assertIn("type the number", msgs.lower())  # it's the numbered menu
        self.assertFalse(getattr(char, "_became", False),
                         "a staging login must NOT run the map-look puppet")

    def test_playing_resume_defers_to_super_puppet(self):
        char = self._make_char()
        char.db.player_state = "playing"  # crash-resume stays PLAYING
        with patch("world.lobby_flow.lobby_flow_enabled", return_value=True):
            char.at_post_puppet()
        self.assertTrue(getattr(char, "_became", False),
                        "resuming into PLAYING is a normal puppet (become+look)")


class _FakeTargeting:
    """Records clear_lock calls for the move-breaks-lock test."""
    def __init__(self, target=None):
        self._target = target
        self.cleared = []

    def get_target(self, player):
        return self._target

    def clear_lock(self, player, reason=None):
        self._target = None
        self.cleared.append(reason)


class TestAtCoordChangeBreaksLock(unittest.TestCase):
    """Moving (in any direction) breaks the mover's ranged lock immediately."""

    def test_move_clears_active_lock(self):
        from world import services

        char = _make_char()
        tg = _FakeTargeting(target=object())
        with services.override({"targeting_system": tg}):
            char.at_coord_change(1, 1, 1, 2)
        self.assertEqual(tg.cleared, ["moved"])

    def test_move_without_lock_is_noop(self):
        from world import services

        char = _make_char()
        tg = _FakeTargeting(target=None)  # no active lock
        with services.override({"targeting_system": tg}):
            char.at_coord_change(1, 1, 2, 1)
        self.assertEqual(tg.cleared, [])

    def test_move_never_raises_without_targeting_system(self):
        from world import services

        char = _make_char()
        with services.override({}):
            char.at_coord_change(1, 1, 2, 2)  # must not raise


# -------------------------------------------------------------- #
#  Base-health migration (100 -> 500) + admin HP bump
# -------------------------------------------------------------- #

class TestBaseHealthMigration(unittest.TestCase):
    """_migrate_base_health lifts a legacy 100-base character to DEFAULT_HEALTH."""

    def test_new_char_creates_at_default_health(self):
        char = _make_char()
        self.assertEqual(char.db.hp_max, DEFAULT_HEALTH)
        self.assertEqual(char.db.hp, DEFAULT_HEALTH)

    def test_legacy_full_char_rebased_and_topped_up(self):
        char = _make_char()
        char.db.hp_max = 100
        char.db.hp = 100
        char.db.equipment_hp_bonus = 0
        char._migrate_base_health()
        self.assertEqual(char.db.hp_max, DEFAULT_HEALTH)
        self.assertEqual(char.db.hp, DEFAULT_HEALTH)  # full unit topped up

    def test_legacy_wounded_char_keeps_current_hp(self):
        char = _make_char()
        char.db.hp_max = 100
        char.db.hp = 40
        char.db.equipment_hp_bonus = 0
        char._migrate_base_health()
        self.assertEqual(char.db.hp_max, DEFAULT_HEALTH)
        self.assertEqual(char.db.hp, 40)  # headroom only, no free heal

    def test_legacy_base_preserves_equipment_bonus(self):
        char = _make_char()
        # 100 base + 30 gear = 130 ceiling
        char.db.hp_max = 130
        char.db.hp = 130
        char.db.equipment_hp_bonus = 30
        char._migrate_base_health()
        self.assertEqual(char.db.hp_max, DEFAULT_HEALTH + 30)

    def test_already_rebased_char_untouched(self):
        char = _make_char()  # hp_max already DEFAULT_HEALTH
        char.db.hp = 250
        char._migrate_base_health()
        self.assertEqual(char.db.hp_max, DEFAULT_HEALTH)
        self.assertEqual(char.db.hp, 250)  # not touched

    def test_custom_ceiling_not_treated_as_legacy(self):
        char = _make_char()
        char.db.hp_max = 777
        char.db.hp = 777
        char.db.equipment_hp_bonus = 0
        char._migrate_base_health()
        self.assertEqual(char.db.hp_max, 777)  # non-legacy base left alone


class TestAdminHealthBump(unittest.TestCase):
    """_ensure_admin_health raises a staff character's ceiling to ADMIN_BASE_HEALTH."""

    def _staff_char(self, name="Staff"):
        char = _make_char(name)
        char.check_permstring = lambda perm: True  # Builder+
        return char

    def test_non_staff_untouched(self):
        char = _make_char()  # stub has no check_permstring -> not staff
        char.db.hp_max = DEFAULT_HEALTH
        char._ensure_admin_health()
        self.assertEqual(char.db.hp_max, DEFAULT_HEALTH)

    def test_staff_ceiling_raised_and_topped_up(self):
        from world.constants import ADMIN_BASE_HEALTH

        char = self._staff_char()
        char.db.hp_max = DEFAULT_HEALTH
        char.db.hp = DEFAULT_HEALTH
        char._ensure_admin_health()
        self.assertEqual(char.db.hp_max, ADMIN_BASE_HEALTH)
        self.assertEqual(char.db.hp, ADMIN_BASE_HEALTH)

    def test_staff_ceiling_not_lowered(self):
        from world.constants import ADMIN_BASE_HEALTH

        char = self._staff_char()
        char.db.hp_max = ADMIN_BASE_HEALTH + 500  # built past the admin base
        char.db.hp = ADMIN_BASE_HEALTH + 500
        char._ensure_admin_health()
        self.assertEqual(char.db.hp_max, ADMIN_BASE_HEALTH + 500)  # not lowered

    def test_staff_wounded_not_force_healed(self):
        from world.constants import ADMIN_BASE_HEALTH

        char = self._staff_char()
        char.db.hp_max = DEFAULT_HEALTH
        char.db.hp = 10
        char._ensure_admin_health()
        self.assertEqual(char.db.hp_max, ADMIN_BASE_HEALTH)
        self.assertEqual(char.db.hp, 10)  # ceiling raised, HP left as-is

    def test_staff_migration_skips_base_rebase(self):
        # A staff char on the legacy 100 base must NOT be rebased by
        # _migrate_base_health (admin path owns its ceiling).
        char = self._staff_char()
        char.db.hp_max = 100
        char.db.hp = 100
        char._migrate_base_health()
        self.assertEqual(char.db.hp_max, 100)  # left for _ensure_admin_health


if __name__ == "__main__":
    unittest.main()
