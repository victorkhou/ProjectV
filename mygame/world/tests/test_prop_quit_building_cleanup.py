"""
Property-based tests for quit building cleanup.

Feature: quit-building-cleanup

Validates correctness properties from the design document for the
disconnect cleanup loop that destroys unprotected building contents.
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
    _mod("evennia.commands.cmdset")
    _mod("evennia.utils")
    _mod("evennia.utils.utils")
    _mod("evennia.utils.logger")

    for name, mod in stubs.items():
        sys.modules.setdefault(name, mod)

_ensure_evennia_stubs()

from mygame.typeclasses.characters import (  # noqa: E402
    _get_building_type,
    _clear_extractor_inventory,
    _delete_objects_at_building,
)
from mygame.world.constants import PROTECTED_BUILDING_TYPES  # noqa: E402

# -------------------------------------------------------------- #
#  Fake objects (mirror Evennia attribute access patterns)
# -------------------------------------------------------------- #

class FakeDB:
    """Simulates Evennia's db handler."""
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class FakeAttributes:
    """Simulates Evennia's attributes handler."""
    def __init__(self, data=None):
        self._data = dict(data or {})

    def get(self, key, default=None):
        return self._data.get(key, default)

    def add(self, key, value):
        self._data[key] = value


class FakeGameEntity:
    """Simulates a game object on a tile (item/resource drop)."""
    def __init__(self, coord_x, coord_y):
        self.db = FakeDB(coord_x=coord_x, coord_y=coord_y)
        self.tags = FakeTags()
        self.deleted = False

    def delete(self):
        self.deleted = True


class FakeTags:
    """Simulates Evennia's tag handler for object_type checks."""
    def __init__(self, tags=None):
        # tags: dict mapping (key, category) -> True
        self._tags = dict(tags or {})

    def get(self, key, category=None):
        return self._tags.get((key, category), None)

    def add(self, key, category=None):
        self._tags[(key, category)] = True


class FakeNPC:
    """Simulates an NPC (agent) on a tile — must NOT be deleted."""
    def __init__(self, coord_x, coord_y):
        self.db = FakeDB(coord_x=coord_x, coord_y=coord_y)
        self.tags = FakeTags({("npc", "object_type"): True})
        self.deleted = False

    def delete(self):
        self.deleted = True


class FakePlanetRoom:
    """Simulates a PlanetRoom with get_objects_at."""
    def __init__(self):
        self.contents = []

    def get_objects_at(self, x, y):
        return [
            obj for obj in self.contents
            if getattr(getattr(obj, "db", None), "coord_x", None) == x
            and getattr(getattr(obj, "db", None), "coord_y", None) == y
        ]


class FakeBuilding:
    """Simulates a Building object."""
    def __init__(self, building_type, coord_x, coord_y, room,
                 resource_inventory=None):
        self.key = f"Building-{building_type}"
        self.db = FakeDB(
            building_type=building_type,
            coord_x=coord_x,
            coord_y=coord_y,
        )
        self.attributes = FakeAttributes({"building_type": building_type})
        self.tags = FakeTags({("building", "object_type"): True})
        if resource_inventory is not None:
            self.attributes._data["resource_inventory"] = resource_inventory
            self.db.resource_inventory = resource_inventory
        self.location = room
        self.deleted = False

    def delete(self):
        self.deleted = True


# -------------------------------------------------------------- #
#  Cleanup helper (replicates at_pre_unpuppet loop for testing)
# -------------------------------------------------------------- #

def _run_cleanup(buildings):
    """Replicate the at_pre_unpuppet cleanup loop for testing."""
    for b in buildings:
        btype = _get_building_type(b)
        if btype in PROTECTED_BUILDING_TYPES:
            continue
        if btype == "EX":
            _clear_extractor_inventory(b)
        _delete_objects_at_building(b)


# -------------------------------------------------------------- #
#  Hypothesis strategies
# -------------------------------------------------------------- #

ALL_BUILDING_TYPES = [
    "HQ", "EX", "AC", "LB", "AR", "TU", "VT", "RD", "WL", "BK", "MB", "RL",
]

UNPROTECTED_TYPES = [t for t in ALL_BUILDING_TYPES if t not in PROTECTED_BUILDING_TYPES]

building_type_st = st.sampled_from(ALL_BUILDING_TYPES)
unprotected_type_st = st.sampled_from(UNPROTECTED_TYPES)
coord_st = st.integers(min_value=0, max_value=100)
resource_inventory_st = st.dictionaries(
    st.sampled_from(["Wood", "Stone", "Iron"]),
    st.integers(min_value=0, max_value=1000),
)
num_objects_st = st.integers(min_value=0, max_value=5)


@st.composite
def building_with_objects_st(draw):
    """Generate a FakeBuilding with random objects at its tile.

    Returns (building, room, objects_at_tile, original_inventory).
    """
    btype = draw(building_type_st)
    cx = draw(coord_st)
    cy = draw(coord_st)
    inv = draw(st.one_of(st.none(), resource_inventory_st))
    n_objects = draw(num_objects_st)

    room = FakePlanetRoom()
    building = FakeBuilding(btype, cx, cy, room, resource_inventory=inv)
    room.contents.append(building)

    objects_at_tile = []
    for _ in range(n_objects):
        obj = FakeGameEntity(cx, cy)
        room.contents.append(obj)
        objects_at_tile.append(obj)

    original_inventory = dict(inv) if inv is not None else None
    return building, room, objects_at_tile, original_inventory


# -------------------------------------------------------------- #
#  Property 1: Protected building preservation
#  **Validates: Requirements 1.2, 3.1, 3.2, 3.3**
# -------------------------------------------------------------- #

class TestProperty1ProtectedBuildingPreservation(unittest.TestCase):
    """Property 1: Protected building preservation.

    For any set of buildings owned by a player where some buildings
    have a building_type in PROTECTED_BUILDING_TYPES, after running
    disconnect cleanup, all objects at protected building coordinates
    SHALL remain present and any resource_inventory on protected
    buildings SHALL be unchanged.

    **Validates: Requirements 1.2, 3.1, 3.2, 3.3**
    """

    @given(
        data=st.data(),
        num_buildings=st.integers(min_value=1, max_value=8),
    )
    @settings(max_examples=100)
    def test_protected_buildings_preserved(self, data, num_buildings):
        """Objects at protected tiles remain, inventory unchanged;
        objects at unprotected tiles are deleted (contrast)."""
        buildings = []
        protected_info = []  # (building, objects, original_inventory)
        unprotected_info = []

        for _ in range(num_buildings):
            b, room, objs, orig_inv = data.draw(building_with_objects_st())
            buildings.append(b)
            btype = _get_building_type(b)
            if btype in PROTECTED_BUILDING_TYPES:
                protected_info.append((b, objs, orig_inv))
            else:
                unprotected_info.append((b, objs, orig_inv))

        _run_cleanup(buildings)

        # Protected buildings: objects NOT deleted, inventory unchanged
        for b, objs, orig_inv in protected_info:
            for obj in objs:
                self.assertFalse(
                    obj.deleted,
                    f"Object at protected {b.key} tile should NOT be deleted",
                )
            if orig_inv is not None:
                current_inv = b.attributes.get("resource_inventory")
                self.assertEqual(
                    current_inv,
                    orig_inv,
                    f"Inventory on protected {b.key} should be unchanged",
                )

        # Contrast: unprotected buildings' objects ARE deleted
        for b, objs, orig_inv in unprotected_info:
            for obj in objs:
                self.assertTrue(
                    obj.deleted,
                    f"Object at unprotected {b.key} tile should be deleted",
                )


# -------------------------------------------------------------- #
#  Strategy: unprotected building with random objects at its tile
# -------------------------------------------------------------- #

@st.composite
def unprotected_building_with_objects_st(draw):
    """Generate an unprotected FakeBuilding with random objects at its tile.

    Returns (building, room, objects_at_tile).
    """
    btype = draw(unprotected_type_st)
    cx = draw(coord_st)
    cy = draw(coord_st)
    inv = draw(st.one_of(st.none(), resource_inventory_st))
    n_objects = draw(num_objects_st)

    room = FakePlanetRoom()
    building = FakeBuilding(btype, cx, cy, room, resource_inventory=inv)
    room.contents.append(building)

    objects_at_tile = []
    for _ in range(n_objects):
        obj = FakeGameEntity(cx, cy)
        room.contents.append(obj)
        objects_at_tile.append(obj)

    return building, room, objects_at_tile


# -------------------------------------------------------------- #
#  Property 2: Unprotected building tile cleanup
#  **Validates: Requirements 1.3, 5.1**
# -------------------------------------------------------------- #

class TestProperty2UnprotectedBuildingTileCleanup(unittest.TestCase):
    """Property 2: Unprotected building tile cleanup.

    For any unprotected building (building_type not in
    PROTECTED_BUILDING_TYPES) with any set of objects at its
    (coord_x, coord_y) tile, after running disconnect cleanup,
    all objects at that tile except the building itself SHALL be
    deleted.

    **Validates: Requirements 1.3, 5.1**
    """

    @given(
        data=st.data(),
        num_buildings=st.integers(min_value=1, max_value=8),
    )
    @settings(max_examples=100)
    def test_unprotected_tile_objects_deleted_building_survives(self, data, num_buildings):
        """All non-building objects at unprotected tiles are deleted;
        the building itself survives."""
        buildings = []
        info = []  # (building, objects_at_tile)

        for _ in range(num_buildings):
            b, room, objs = data.draw(unprotected_building_with_objects_st())
            buildings.append(b)
            info.append((b, objs))

        _run_cleanup(buildings)

        for b, objs in info:
            # Every non-building object at the tile must be deleted
            for obj in objs:
                self.assertTrue(
                    obj.deleted,
                    f"Object at unprotected {b.key} tile should be deleted",
                )
            # The building itself must survive
            self.assertFalse(
                b.deleted,
                f"Building {b.key} itself should NOT be deleted",
            )


# -------------------------------------------------------------- #
#  Strategy: Extractor with non-empty resource inventory
# -------------------------------------------------------------- #

nonempty_resource_inventory_st = st.dictionaries(
    st.sampled_from(["Wood", "Stone", "Iron"]),
    st.integers(min_value=1, max_value=1000),
    min_size=1,
)


@st.composite
def extractor_with_inventory_st(draw):
    """Generate an Extractor FakeBuilding with a non-empty resource_inventory.

    Returns a FakeBuilding with building_type="EX".
    """
    cx = draw(coord_st)
    cy = draw(coord_st)
    inv = draw(nonempty_resource_inventory_st)

    room = FakePlanetRoom()
    building = FakeBuilding("EX", cx, cy, room, resource_inventory=inv)
    room.contents.append(building)
    return building


# -------------------------------------------------------------- #
#  Property 3: Extractor inventory cleared
#  **Validates: Requirements 2.1**
# -------------------------------------------------------------- #

class TestProperty3ExtractorInventoryCleared(unittest.TestCase):
    """Property 3: Extractor inventory cleared.

    For any unprotected Extractor building (building_type "EX") with
    any resource_inventory dict, after running disconnect cleanup,
    the resource_inventory SHALL be an empty dictionary {}.

    **Validates: Requirements 2.1**
    """

    @given(
        data=st.data(),
        num_buildings=st.integers(min_value=1, max_value=8),
    )
    @settings(max_examples=100)
    def test_extractor_inventory_cleared_after_cleanup(self, data, num_buildings):
        """resource_inventory is {} for every Extractor after cleanup."""
        buildings = []
        for _ in range(num_buildings):
            b = data.draw(extractor_with_inventory_st())
            buildings.append(b)

        _run_cleanup(buildings)

        for b in buildings:
            self.assertEqual(
                b.attributes.get("resource_inventory"),
                {},
                f"Extractor {b.key} resource_inventory should be empty after cleanup",
            )


# -------------------------------------------------------------- #
#  Error-handling cleanup loop (replicates at_pre_unpuppet's
#  per-building try/except — NOT the bare _run_cleanup helper)
# -------------------------------------------------------------- #

def _run_cleanup_with_error_handling(buildings):
    """Replicate at_pre_unpuppet with per-building error handling.

    The real ``at_pre_unpuppet`` wraps each building's cleanup in its
    own ``try/except Exception`` so that a failure on one building does
    not prevent cleanup of subsequent buildings.  ``_run_cleanup`` above
    does NOT have this guard, so we need a separate function to test
    the error-isolation property.
    """
    for b in buildings:
        try:
            btype = _get_building_type(b)
            if btype in PROTECTED_BUILDING_TYPES:
                continue
            if btype == "EX":
                _clear_extractor_inventory(b)
            _delete_objects_at_building(b)
        except Exception:
            pass  # Error isolated, continue to next building


class FakePoisonedRoom:
    """A room whose ``get_objects_at`` always raises."""

    def get_objects_at(self, x, y):
        raise RuntimeError("Injected failure")


# -------------------------------------------------------------- #
#  Property 4: Error isolation across buildings
#  **Validates: Requirements 4.1**
# -------------------------------------------------------------- #

class TestProperty4ErrorIsolationAcrossBuildings(unittest.TestCase):
    """Property 4: Error isolation across buildings.

    For any ordered list of unprotected buildings where one building
    raises an exception during cleanup, all buildings processed after
    the failing building SHALL still have their cleanup applied
    (objects deleted, inventory cleared as applicable).

    **Validates: Requirements 4.1**
    """

    @given(
        data=st.data(),
        num_buildings=st.integers(min_value=2, max_value=8),
    )
    @settings(max_examples=100)
    def test_error_in_one_building_does_not_block_others(self, data, num_buildings):
        """Buildings before and after a poisoned building are still cleaned."""
        # Build a list of unprotected buildings, each with its own room
        buildings = []
        # Track (building, objects_at_tile) for non-poisoned buildings
        clean_info = []

        for _ in range(num_buildings):
            b, room, objs = data.draw(unprotected_building_with_objects_st())
            buildings.append(b)
            clean_info.append((b, objs))

        # Pick a random index to poison
        poison_idx = data.draw(
            st.integers(min_value=0, max_value=num_buildings - 1)
        )

        # Replace the poisoned building's room with FakePoisonedRoom
        poisoned_building = buildings[poison_idx]
        poisoned_building.location = FakePoisonedRoom()

        # Run cleanup WITH error handling (mirrors at_pre_unpuppet)
        _run_cleanup_with_error_handling(buildings)

        # Assert: buildings BEFORE the poisoned one are cleaned
        for i, (b, objs) in enumerate(clean_info):
            if i == poison_idx:
                continue  # Don't assert on the poisoned building
            for obj in objs:
                self.assertTrue(
                    obj.deleted,
                    f"Object at building index {i} (type={b.key}) should be "
                    f"deleted even though building at index {poison_idx} failed",
                )


# -------------------------------------------------------------- #
#  Property 5: Logout event always fires
#  **Validates: Requirements 4.2, 4.3**
# -------------------------------------------------------------- #

from unittest.mock import MagicMock, patch  # noqa: E402

from mygame.typeclasses.characters import CombatCharacter  # noqa: E402


class _FakeSelf:
    """Minimal stand-in for CombatCharacter used as ``self`` when calling
    the real ``at_pre_unpuppet`` unbound method.

    Configurable to simulate normal cleanup, per-building errors, or
    total ``get_buildings`` failure.
    """

    def __init__(self, buildings=None, fail_get_buildings=False):
        self.key = "TestPlayer"
        self._buildings = buildings or []
        self._fail_get_buildings = fail_get_buildings

    def get_buildings(self):
        if self._fail_get_buildings:
            raise RuntimeError("Injected get_buildings failure")
        return list(self._buildings)


class TestProperty5LogoutEventAlwaysFires(unittest.TestCase):
    """Property 5: Logout event always fires.

    For any disconnect cleanup execution — whether cleanup succeeds
    fully, partially fails, or fails entirely — the PLAYER_LOGOUT
    event SHALL be published on the EventBus.

    **Validates: Requirements 4.2, 4.3**
    """

    @given(scenario=st.sampled_from(["normal", "per_building_error", "total_failure"]),
           data=st.data())
    @settings(max_examples=100)
    def test_logout_event_published_in_all_scenarios(self, scenario, data):
        """PLAYER_LOGOUT is published regardless of cleanup outcome."""
        if scenario == "normal":
            # Normal: 0-5 healthy unprotected buildings
            n = data.draw(st.integers(min_value=0, max_value=5))
            buildings = []
            for _ in range(n):
                b, _room, _objs = data.draw(
                    unprotected_building_with_objects_st()
                )
                buildings.append(b)
            fake = _FakeSelf(buildings=buildings)

        elif scenario == "per_building_error":
            # Per-building error: at least one building has a poisoned room
            n = data.draw(st.integers(min_value=1, max_value=5))
            buildings = []
            for _ in range(n):
                b, _room, _objs = data.draw(
                    unprotected_building_with_objects_st()
                )
                buildings.append(b)
            poison_idx = data.draw(
                st.integers(min_value=0, max_value=n - 1)
            )
            buildings[poison_idx].location = FakePoisonedRoom()
            fake = _FakeSelf(buildings=buildings)

        else:  # total_failure
            # get_buildings() itself raises
            fake = _FakeSelf(fail_get_buildings=True)

        mock_bus = MagicMock()
        with patch("world.event_bus.event_bus", mock_bus):
            CombatCharacter.at_pre_unpuppet(fake)

        # Assert PLAYER_LOGOUT was published exactly once
        calls = [
            c for c in mock_bus.publish.call_args_list
            if c.args[0] == "player_logout" or (
                c.kwargs.get("event_name") == "player_logout"
            )
        ]
        self.assertEqual(
            len(calls), 1,
            f"Expected exactly 1 PLAYER_LOGOUT publish in '{scenario}' "
            f"scenario, got {len(calls)}",
        )
        # Verify the player kwarg is our fake self
        call = calls[0]
        self.assertIs(
            call.kwargs.get("player", call.args[1] if len(call.args) > 1 else None),
            fake,
            "PLAYER_LOGOUT event should carry the disconnecting player",
        )


# -------------------------------------------------------------- #
#  Unit tests for helpers and constant
#  **Validates: Requirements 1.4, 2.2, 2.3, 5.3, 6.2**
# -------------------------------------------------------------- #

class FakeBasicRoom:
    """Room without get_objects_at — forces fallback path."""
    def __init__(self):
        self.contents = []


class TestUnitHelpersAndConstant(unittest.TestCase):
    """Unit tests for PROTECTED_BUILDING_TYPES, _get_building_type,
    _clear_extractor_inventory, and _delete_objects_at_building.

    **Validates: Requirements 1.4, 2.2, 2.3, 5.3, 6.2**
    """

    # -- PROTECTED_BUILDING_TYPES constant --

    def test_protected_building_types_constant(self):
        """PROTECTED_BUILDING_TYPES equals {"VT"}."""
        self.assertEqual(PROTECTED_BUILDING_TYPES, {"VT"})

    # -- _get_building_type --

    def test_get_building_type_returns_type(self):
        """Returns building_type from attributes."""
        room = FakePlanetRoom()
        b = FakeBuilding("HQ", 0, 0, room)
        self.assertEqual(_get_building_type(b), "HQ")

    def test_get_building_type_missing_attribute(self):
        """Returns None when object has no attributes and no db."""
        obj = object()
        self.assertIsNone(_get_building_type(obj))

    def test_get_building_type_db_fallback(self):
        """Falls back to db.building_type when attributes is absent."""
        class _Obj:
            pass
        obj = _Obj()
        obj.db = FakeDB(building_type="EX")
        self.assertEqual(_get_building_type(obj), "EX")

    # -- _clear_extractor_inventory --

    def test_clear_extractor_inventory_resets(self):
        """Resets resource_inventory to {} on an Extractor."""
        room = FakePlanetRoom()
        b = FakeBuilding("EX", 0, 0, room, resource_inventory={"Wood": 50})
        _clear_extractor_inventory(b)
        self.assertEqual(b.attributes.get("resource_inventory"), {})

    def test_clear_extractor_inventory_no_inventory(self):
        """No error when building has no resource_inventory at all."""
        room = FakePlanetRoom()
        b = FakeBuilding("EX", 0, 0, room)
        # resource_inventory was never set — should be a no-op
        _clear_extractor_inventory(b)  # must not raise

    # -- _delete_objects_at_building --

    def test_delete_objects_at_building_deletes_all_except_building(self):
        """Deletes all objects at building coords except the building."""
        room = FakePlanetRoom()
        b = FakeBuilding("HQ", 5, 5, room)
        room.contents.append(b)
        objs = [FakeGameEntity(5, 5) for _ in range(3)]
        room.contents.extend(objs)

        _delete_objects_at_building(b)

        for obj in objs:
            self.assertTrue(obj.deleted, "Tile object should be deleted")
        self.assertFalse(b.deleted, "Building itself should NOT be deleted")

    def test_delete_objects_at_building_no_coords(self):
        """Building with None coordinates is skipped without error."""
        room = FakePlanetRoom()
        b = FakeBuilding("HQ", 5, 5, room)
        b.db.coord_x = None  # simulate missing coordinate
        room.contents.append(b)

        _delete_objects_at_building(b)  # must not raise

    def test_delete_objects_at_building_fallback_no_get_objects_at(self):
        """Falls back to room.contents iteration when get_objects_at is absent."""
        room = FakeBasicRoom()
        b = FakeBuilding("HQ", 3, 3, room)
        b.location = room
        room.contents.append(b)
        objs = [FakeGameEntity(3, 3) for _ in range(3)]
        room.contents.extend(objs)

        _delete_objects_at_building(b)

        for obj in objs:
            self.assertTrue(obj.deleted, "Tile object should be deleted via fallback")
        self.assertFalse(b.deleted, "Building itself should NOT be deleted")

    def test_delete_objects_at_building_preserves_npcs(self):
        """NPCs (agents) at building tile are NOT deleted."""
        room = FakePlanetRoom()
        b = FakeBuilding("HQ", 5, 5, room)
        room.contents.append(b)

        npc = FakeNPC(5, 5)
        room.contents.append(npc)

        item = FakeGameEntity(5, 5)
        room.contents.append(item)

        _delete_objects_at_building(b)

        self.assertFalse(npc.deleted, "NPC at building tile should NOT be deleted")
        self.assertTrue(item.deleted, "Item at building tile should be deleted")
        self.assertFalse(b.deleted, "Building itself should NOT be deleted")

    def test_delete_objects_at_building_preserves_npcs_fallback(self):
        """NPCs preserved even via fallback path (no get_objects_at)."""
        room = FakeBasicRoom()
        b = FakeBuilding("HQ", 5, 5, room)
        b.location = room
        room.contents.append(b)

        npc = FakeNPC(5, 5)
        room.contents.append(npc)

        item = FakeGameEntity(5, 5)
        room.contents.append(item)

        _delete_objects_at_building(b)

        self.assertFalse(npc.deleted, "NPC should NOT be deleted via fallback")
        self.assertTrue(item.deleted, "Item should be deleted via fallback")

    def test_delete_objects_at_building_preserves_characters(self):
        """Player characters at building tile are NOT deleted."""
        from evennia.objects.objects import DefaultCharacter

        room = FakePlanetRoom()
        b = FakeBuilding("EX", 5, 5, room)
        room.contents.append(b)

        # Simulate a player character (isinstance DefaultCharacter)
        char = FakeGameEntity(5, 5)
        char.__class__ = type("FakeChar", (DefaultCharacter,), {
            "__init__": lambda self: None,
        })
        # Patch to avoid Evennia DB access
        char.sessions = type("S", (), {"count": lambda self: 0})()
        room.contents.append(char)

        item = FakeGameEntity(5, 5)
        room.contents.append(item)

        _delete_objects_at_building(b)

        self.assertFalse(char.deleted, "Player character should NOT be deleted")
        self.assertTrue(item.deleted, "Item at building tile should be deleted")
        self.assertFalse(b.deleted, "Building itself should NOT be deleted")


if __name__ == "__main__":
    unittest.main()
