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
