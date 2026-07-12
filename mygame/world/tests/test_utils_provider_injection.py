"""
Tests that the owner-agnostic helpers accept an injected DefinitionsProvider.

The payoff of routing world.utils / world.chat_system through the
DefinitionsProvider port: capability and rank-name lookups can be exercised
with an in-memory fake provider — no DataRegistry singleton, no set_instance,
no global-state leak between tests.
"""

from mygame.world.utils import building_has_capability, _get_rank_name


class _Cap:
    """A minimal BuildingDef-like object exposing has_capability."""

    def __init__(self, caps):
        self._caps = set(caps)

    def has_capability(self, cap):
        return cap in self._caps


class _Rank:
    def __init__(self, level, name):
        self.level = level
        self.name = name


class FakeProvider:
    """In-memory DefinitionsProvider stand-in."""

    def __init__(self, buildings=None, ranks=None):
        self._buildings = buildings or {}
        self._ranks = ranks or []

    @property
    def balance(self):
        return None

    @property
    def ranks(self):
        return self._ranks

    def resolve_building(self, building_type):
        return self._buildings.get(building_type)

    def get_ability_gates(self):
        return []


class _Db:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Building:
    def __init__(self, building_type):
        self.db = _Db(building_type=building_type)


class TestBuildingHasCapabilityInjection:
    def test_true_when_provider_reports_capability(self):
        provider = FakeProvider(buildings={"EX": _Cap({"harvestable"})})
        assert building_has_capability(_Building("EX"), "harvestable", provider=provider) is True

    def test_false_when_capability_absent(self):
        provider = FakeProvider(buildings={"EX": _Cap({"harvestable"})})
        assert building_has_capability(_Building("EX"), "storage", provider=provider) is False

    def test_false_when_type_unknown(self):
        provider = FakeProvider(buildings={})
        assert building_has_capability(_Building("ZZ"), "harvestable", provider=provider) is False

    def test_false_when_no_building_type(self):
        provider = FakeProvider(buildings={"EX": _Cap({"harvestable"})})
        assert building_has_capability(_Building(None), "harvestable", provider=provider) is False


class TestGetRankNameInjection:
    def _player(self, level):
        # No rank_name attr -> forces the provider-backed derivation path.
        p = type("P", (), {})()
        p.db = _Db(level=level)
        return p

    def test_resolves_rank_name_from_provider(self):
        # rank_from_level maps level 1 -> rank 1 (first rank). Provide that rank.
        from mygame.world.systems.rank_system import rank_from_level
        lvl = 1
        rank_num = rank_from_level(lvl)
        provider = FakeProvider(ranks=[_Rank(rank_num, "Field_Marshal")])
        assert _get_rank_name(self._player(lvl), provider=provider) == "Field Marshal"

    def test_falls_back_to_rank_number_when_not_in_provider(self):
        from mygame.world.systems.rank_system import rank_from_level
        lvl = 1
        rank_num = rank_from_level(lvl)
        provider = FakeProvider(ranks=[])  # rank not present
        assert _get_rank_name(self._player(lvl), provider=provider) == f"Rank {rank_num}"


class _Loc:
    """A PlanetRoom stand-in exposing planet_name."""

    def __init__(self, planet):
        self.planet_name = planet


class _OwnedBuilding:
    """A building with a building_type, a location (for planet), and optional
    under_construction flag — enough for owner_has_active_hq to inspect."""

    def __init__(self, building_type, planet="earth", under_construction=False):
        self.db = _Db(
            building_type=building_type,
            under_construction=under_construction,
        )
        self.location = _Loc(planet) if planet is not None else None


class _OwnerWithBuildings:
    """An owner exposing get_buildings() (like the game Character)."""

    def __init__(self, buildings, oid=1):
        self.id = oid
        self._buildings = list(buildings)

    def get_buildings(self):
        return list(self._buildings)


# HQ carries the headquarters capability; EX does not.
_HQ_PROVIDER = FakeProvider(buildings={
    "HQ": _Cap({"headquarters"}),
    "EX": _Cap({"harvestable"}),
})


class TestOwnerHasActiveHq:
    """The real 'no HQ = base inert' predicate (Phase 2)."""

    def _call(self, owner, planet=None):
        from mygame.world.utils import owner_has_active_hq
        return owner_has_active_hq(owner, planet, provider=_HQ_PROVIDER)

    def test_true_when_owner_has_a_completed_hq(self):
        owner = _OwnerWithBuildings([_OwnedBuilding("HQ", planet="earth")])
        assert self._call(owner, "earth") is True

    def test_false_when_owner_has_no_hq(self):
        owner = _OwnerWithBuildings([_OwnedBuilding("EX", planet="earth")])
        assert self._call(owner, "earth") is False

    def test_false_when_owner_has_no_buildings(self):
        assert self._call(_OwnerWithBuildings([]), "earth") is False

    def test_false_when_hq_still_under_construction(self):
        owner = _OwnerWithBuildings([
            _OwnedBuilding("HQ", planet="earth", under_construction=True),
        ])
        assert self._call(owner, "earth") is False

    def test_planet_scoping_excludes_hq_on_another_planet(self):
        owner = _OwnerWithBuildings([_OwnedBuilding("HQ", planet="mars")])
        assert self._call(owner, "earth") is False
        assert self._call(owner, "mars") is True

    def test_planet_none_matches_any_planet(self):
        owner = _OwnerWithBuildings([_OwnedBuilding("HQ", planet="mars")])
        assert self._call(owner, None) is True

    def test_hq_with_undeterminable_planet_counts_for_any_query(self):
        # A building whose location yields no planet is not excluded by scoping
        # (planet None on the building -> matches any queried planet).
        owner = _OwnerWithBuildings([_OwnedBuilding("HQ", planet=None)])
        assert self._call(owner, "earth") is True

    def test_owner_without_get_buildings_is_false(self):
        class _Bare:
            id = 9
        assert self._call(_Bare(), "earth") is False

    def test_none_owner_is_false(self):
        assert self._call(None, "earth") is False


# ------------------------------------------------------------------ #
#  Tile item-capacity caps (room carry capacity)
# ------------------------------------------------------------------ #

from mygame.world.utils import (  # noqa: E402
    tile_item_capacity, tile_object_count, tile_has_room,
)
from mygame.world.definitions import BalanceConfig  # noqa: E402


class _LevelBuilding:
    """A building on a tile with a building_type + level (for capability lookup)."""
    def __init__(self, building_type, level=1):
        self.db = _Db(building_type=building_type, building_level=level)


class _CapTile:
    """A tile stand-in: buildings + loose objects addressable by coordinate."""
    def __init__(self):
        self._buildings = {}   # (x, y) -> [building]
        self._objects = {}     # (x, y) -> [obj]

    def place_building(self, b, x, y):
        self._buildings.setdefault((x, y), []).append(b)

    def add_objects(self, x, y, n):
        self._objects.setdefault((x, y), []).extend(object() for _ in range(n))

    def get_buildings_at(self, x, y):
        return list(self._buildings.get((x, y), []))

    def get_objects_at(self, x, y, type_tag=None):
        # The helper sums over ("item", "resource_drop"); return the same loose
        # list for the "item" tag and nothing for others (count once).
        if type_tag in (None, "item"):
            return list(self._objects.get((x, y), []))
        return []


class TestTileItemCapacity:
    """Per-building tile capacity tiers + the has-room predicate."""

    def _prov(self):
        return FakeProvider(buildings={
            "VT": _Cap({"storage"}),
            "EX": _Cap({"harvestable"}),
            "HQ": _Cap({"headquarters"}),
        })

    def _bal(self):
        return BalanceConfig()  # empty=1, building=10, per_storage_level=20

    def test_empty_tile_capacity_is_one(self):
        tile = _CapTile()
        assert tile_item_capacity(tile, 1, 1, provider=self._prov(),
                                  balance=self._bal()) == 1

    def test_non_storage_building_capacity_is_ten(self):
        tile = _CapTile()
        tile.place_building(_LevelBuilding("HQ"), 1, 1)
        assert tile_item_capacity(tile, 1, 1, provider=self._prov(),
                                  balance=self._bal()) == 10

    def test_vault_capacity_scales_with_level(self):
        tile = _CapTile()
        tile.place_building(_LevelBuilding("VT", level=3), 1, 1)
        # 20 x level 3 = 60.
        assert tile_item_capacity(tile, 1, 1, provider=self._prov(),
                                  balance=self._bal()) == 60

    def test_extractor_capacity_scales_with_level(self):
        tile = _CapTile()
        tile.place_building(_LevelBuilding("EX", level=2), 1, 1)
        assert tile_item_capacity(tile, 1, 1, provider=self._prov(),
                                  balance=self._bal()) == 40

    def test_object_count_and_has_room(self):
        tile = _CapTile()
        # Empty tile: cap 1, count 0 -> has room.
        assert tile_object_count(tile, 1, 1) == 0
        assert tile_has_room(tile, 1, 1, provider=self._prov(), balance=self._bal())
        # Fill it: count 1, cap 1 -> no room.
        tile.add_objects(1, 1, 1)
        assert tile_object_count(tile, 1, 1) == 1
        assert not tile_has_room(tile, 1, 1, provider=self._prov(), balance=self._bal())

    def test_object_count_zero_when_tile_not_queryable(self):
        assert tile_object_count(object(), 1, 1) == 0


class TestRestingActivityStatus:
    """The single derived authority for an agent's resting status.

    Precedence (highest first): incapacitated > reserve > (role at a building)
    > (army role, no building) > idle. This is what the movement engine and the
    assign/unassign paths consult instead of guessing, so no two writers can
    disagree — the fix for the "engineer stuck at Idle" regression class.
    """

    @staticmethod
    def _agent(**kw):
        from mygame.world.utils import resting_activity_status  # noqa: F401
        a = type("A", (), {})()
        a.db = _Db(**kw)
        return a

    def _status(self, **kw):
        from mygame.world.utils import resting_activity_status
        return resting_activity_status(self._agent(**kw))

    def test_no_role_is_idle(self):
        assert self._status(role="", role_target=None) == "Idle"

    def test_role_at_building_is_working(self):
        # An engineer/harvester/guard assigned to a building.
        assert self._status(role="engineer", role_target=object()) == "Working"

    def test_army_role_without_building_is_ready(self):
        # A soldier has no target building → on standby, "Ready" (not "Idle").
        assert self._status(role="soldier", role_target=None) == "Ready"

    def test_reserve_outranks_role(self):
        # Benched by a demotion — "Reserve" even though a role is still set.
        assert self._status(role="engineer", role_target=object(),
                            reserve=True) == "Reserve"

    def test_incapacitated_outranks_everything(self):
        assert self._status(role="engineer", role_target=object(),
                            reserve=True, incapacitated=True) == "Incapacitated"

    def test_role_without_target_and_not_army_is_idle(self):
        # A building role whose target was lost, and not an army role → Idle.
        assert self._status(role="engineer", role_target=None) == "Idle"

    def test_none_db_is_idle(self):
        from mygame.world.utils import resting_activity_status
        assert resting_activity_status(object()) == "Idle"


class TestBuildingIsOpen:
    """building_is_open reads the ``open`` attr, defaulting to open when unset."""

    def _building(self, **kw):
        b = _Building("MM")
        for k, v in kw.items():
            setattr(b.db, k, v)
        return b

    def test_open_true_when_explicitly_open(self):
        from mygame.world.utils import building_is_open
        assert building_is_open(self._building(open=True)) is True

    def test_closed_when_explicitly_false(self):
        from mygame.world.utils import building_is_open
        assert building_is_open(self._building(open=False)) is False

    def test_defaults_to_open_when_unset(self):
        """A legacy building with no 'open' attribute reads as open."""
        from mygame.world.utils import building_is_open
        assert building_is_open(self._building()) is True
