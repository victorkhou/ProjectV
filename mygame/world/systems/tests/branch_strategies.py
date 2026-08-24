"""
Shared Hypothesis strategies and fakes for the Technology Branch property tests.

Feature: tech-tree-branch-foundation (design section "Shared Hypothesis
strategies"). Every generator the Branch property modules draw from is defined
here exactly once, so the twenty-six properties speak one vocabulary instead of
each file growing its own near-copy. Consumed by
``test_prop_branch_catalog.py``, ``test_prop_branch_commitment.py``,
``test_prop_branch_reinstatement.py``, ``test_prop_operation_lifecycle.py``, and
``test_prop_operation_persistence.py``.

Two invariants this module exists to preserve:

- **No live framework (R15.1).** The Evennia stub block below (copied from
  ``test_prop_building_system.py``) runs before any project import, so importing
  this module with ``evennia`` absent from ``sys.modules`` succeeds.
- **No global registry (R15.4).** :func:`make_registry` builds a
  ``DataRegistry`` in memory from plain dicts *through the real loader helpers*,
  so every query under test resolves through an injected registry rather than
  ``DataRegistry.get_instance()``.

This module holds no tests of its own; ``pytest`` does not collect it.
"""

import copy
import string
import sys
import types
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from hypothesis import strategies as st
from hypothesis.strategies import SearchStrategy

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

from mygame.world.constants import (  # noqa: E402
    BRANCH_OPERATION_KIND,
    BRANCH_ROLE,
    BRANCHES,
    MAX_LEVEL,
    OPERATION_KINDS,
    RESEARCH_LAB,
    RESOURCE_TYPES,
)
from mygame.world.data_registry import DataRegistry  # noqa: E402
from mygame.world.definitions import BalanceConfig, OperationKindDef  # noqa: E402
from mygame.world.systems.operation_contract import (  # noqa: E402
    TERMINAL_STATES,
    OperationRecord,
    OperationState,
)

# -------------------------------------------------------------- #
#  Fixture catalog: a complete, valid six-Branch dataset
# -------------------------------------------------------------- #
#
# The fixture is deliberately self-contained rather than read from
# ``data/definitions/``: a property's reference computation has to be able to
# state the expected answer, which is only possible over a catalog the test
# controls. Content edits to the shipped YAML therefore cannot break these
# generators, and the shipped-data assertions live in the unit tests instead.

#: Branch -> the abbreviation of the lab hosting it. The real abbreviations, so
#: a failure message reads the way the game does. ``SX`` (not ``SG``) hosts
#: ``cyber``: ``SG`` is the Shield Generator.
FIXTURE_LAB_ABBR: dict[str, str] = {
    "weapons": "WX",
    "defense": "DF",
    "resource": "RX",
    "research": "LB",
    "bio": "BX",
    "cyber": "SX",
}

#: Branch -> the abbreviation of one non-lab Branch_Building of that Branch.
#: Synthetic two-letter codes (``Z*``) so a fixture building can never be
#: mistaken for a shipped one.
FIXTURE_BRANCH_BUILDING_ABBR: dict[str, str] = {
    "weapons": "ZW",
    "defense": "ZD",
    "resource": "ZR",
    "research": "ZE",
    "bio": "ZB",
    "cyber": "ZC",
}

#: Buildings with no Branch_Affiliation — every pre-feature building's shape.
FIXTURE_NEUTRAL_ABBRS: tuple[str, ...] = ("HQ", "WL", "TU")

#: Every abbreviation the fixture catalog defines.
FIXTURE_BUILDING_ABBRS: tuple[str, ...] = (
    tuple(FIXTURE_LAB_ABBR.values())
    + tuple(FIXTURE_BRANCH_BUILDING_ABBR.values())
    + FIXTURE_NEUTRAL_ABBRS
)

#: Branch -> its two fixture technology keys.
FIXTURE_TECH_KEYS_BY_BRANCH: dict[str, tuple[str, ...]] = {
    branch: (f"{branch}_core", f"{branch}_adv") for branch in BRANCHES
}

#: Every fixture technology key, spanning all six Branches (design: the pool
#: ``tech_key_st`` samples).
FIXTURE_TECH_KEYS: tuple[str, ...] = tuple(
    key for branch in BRANCHES for key in FIXTURE_TECH_KEYS_BY_BRANCH[branch]
)

#: The five payload keys ``TechLabSystem._apply_tech_effect`` understands.
#: ``production_multiplier`` composes multiplicatively; the rest are additive.
TECH_BONUS_KEYS: tuple[str, ...] = (
    "building_hp",
    "damage",
    "damage_reduction",
    "sight_range",
    "production_multiplier",
)

#: Rank names from ``ranks.yaml``, for a ``required_rank`` that resolves.
FIXTURE_RANK_NAMES: tuple[str, ...] = (
    "Recruit", "Private", "Corporal", "Sergeant", "Lieutenant", "Captain",
)

#: Categories a generated building definition may declare.
FIXTURE_CATEGORIES: tuple[str, ...] = (
    "research", "defense", "resource", "utility", "equipment",
)

#: Planets the ``owned_buildings_st`` roster spreads across. Commitment and
#: estate are per-planet, so two or three planets is the smallest set that can
#: show the scoping (R3.7).
FIXTURE_PLANETS: tuple[str, ...] = ("earth", "mars", "luna")

#: Agent roles ``agent_state_st`` draws: the six gated Branch roles plus two
#: ungated ones, so eligibility is exercised on both sides of the gate.
FIXTURE_AGENT_ROLES: tuple[str, ...] = tuple(BRANCH_ROLE.values()) + (
    "guard", "harvester",
)

#: Rank floor of a fixture lab, and of a fixture Branch_Building — the latter is
#: at or above the former, which is what validator rule 11 requires.
_FIXTURE_LAB_RANK = 5
_FIXTURE_BRANCH_BUILDING_RANK = 6

#: The shipped Counter_Web cycle (``branches.yaml``): each Branch holds exactly
#: one advantage and carries exactly one disadvantage.
CANONICAL_COUNTER_WEB: dict[str, list[str]] = {
    "weapons": ["defense"],
    "defense": ["bio"],
    "bio": ["cyber"],
    "cyber": ["resource"],
    "resource": ["research"],
    "research": ["weapons"],
}

#: role -> Branch, the inverse of ``BRANCH_ROLE``. Validator rule 9 requires
#: this to be a bijection, so the fixture is the passing case a mutation
#: strategy perturbs.
CANONICAL_ROLE_BRANCH: dict[str, str] = {
    role: branch for branch, role in BRANCH_ROLE.items()
}

#: Operation_Kind registry matching ``branches.yaml``, derived from the
#: constants so the fixture cannot disagree with the shipped bindings.
FIXTURE_OPERATION_KINDS: dict[str, OperationKindDef] = {
    kind: OperationKindDef(
        kind=kind,
        branch=branch,
        carrier_role=BRANCH_ROLE[branch],
        cost_field=f"{kind}_cost",
        cooldown_field=f"{kind}_cooldown_ticks",
        cap_field=f"{kind}_max_in_flight",
        agent_xp_field=f"agent_xp_{kind}",
    )
    for branch, kind in BRANCH_OPERATION_KIND.items()
}

#: Branch -> the (first tech effect, second tech effect) payloads. The
#: ``resource`` pair is the multiplicative case, and ``research_adv`` carries no
#: effect at all so the "skip a payload-less tech" path is generated too.
_FIXTURE_TECH_EFFECTS: dict[str, tuple[dict | None, dict | None]] = {
    "weapons": ({"damage": 5.0}, {"damage": 3.0}),
    "defense": ({"damage_reduction": 2.0}, {"building_hp": 50.0}),
    "resource": ({"production_multiplier": 1.1}, {"production_multiplier": 1.25}),
    "research": ({"sight_range": 1.0}, None),
    "bio": ({"damage": 2.0}, {"damage_reduction": 1.0}),
    "cyber": ({"sight_range": 2.0}, {"damage": 1.0}),
}


def _lab_dict(branch: str) -> dict[str, Any]:
    """Return the YAML-shaped definition of *branch*'s hosting lab."""
    abbr = FIXTURE_LAB_ABBR[branch]
    return {
        "name": f"{branch.title()} Lab",
        "abbreviation": abbr,
        "cost": {"Iron": 60, "Circuits": 20},
        "max_health": 400,
        "requires_hq": True,
        "required_terrain": None,
        "category": "research",
        "produces": None,
        "capabilities": [RESEARCH_LAB],
        "research_tree": branch,
        # A lab may declare its own Branch, and when it does it must equal
        # research_tree (R2.4) — the fixture is the agreeing case.
        "branch": branch,
        "rank_requirement": _FIXTURE_LAB_RANK,
        "map_symbol": abbr,
    }


def _branch_building_dict(branch: str) -> dict[str, Any]:
    """Return the YAML-shaped definition of one non-lab Branch_Building."""
    abbr = FIXTURE_BRANCH_BUILDING_ABBR[branch]
    return {
        "name": f"{branch.title()} Works",
        "abbreviation": abbr,
        "cost": {"Iron": 40, "Circuits": 10},
        "max_health": 250,
        "requires_hq": True,
        "required_terrain": None,
        "category": "utility",
        "produces": None,
        "capabilities": [],
        "branch": branch,
        "unlock_technology": FIXTURE_TECH_KEYS_BY_BRANCH[branch][0],
        "rank_requirement": _FIXTURE_BRANCH_BUILDING_RANK,
        "map_symbol": abbr,
    }


def _neutral_dict(abbr: str, capabilities: list[str], requires_hq: bool) -> dict[str, Any]:
    """Return a Neutral_Building definition: no ``branch``, no research gate."""
    return {
        "name": f"Neutral {abbr}",
        "abbreviation": abbr,
        "cost": {"Wood": 30, "Stone": 20},
        "max_health": 300,
        "requires_hq": requires_hq,
        "required_terrain": None,
        "category": "utility",
        "produces": None,
        "capabilities": capabilities,
        "rank_requirement": 1,
        "map_symbol": abbr,
    }


def _tech_dict(branch: str, index: int) -> dict[str, Any]:
    """Return the YAML-shaped definition of fixture technology *index*."""
    key = FIXTURE_TECH_KEYS_BY_BRANCH[branch][index]
    return {
        "name": key.replace("_", " ").title(),
        "key": key,
        "required_rank": FIXTURE_RANK_NAMES[min(index + 2, len(FIXTURE_RANK_NAMES) - 1)],
        "resource_cost": {"Circuits": 20 + index * 10, "Energy": 10},
        "research_ticks": 10 + index * 5,
        "effect_type": "bonus",
        "effect_value": _FIXTURE_TECH_EFFECTS[branch][index],
        "tree": branch,
    }


#: The complete, valid building catalog: six labs, six Branch_Buildings, three
#: Neutral_Buildings. Every catalog-coverage rule (2.3 - 2.6) passes over it.
FIXTURE_BUILDING_DICTS: tuple[dict[str, Any], ...] = (
    tuple(_lab_dict(b) for b in BRANCHES)
    + tuple(_branch_building_dict(b) for b in BRANCHES)
    + (
        _neutral_dict("HQ", ["headquarters", "storage"], requires_hq=False),
        _neutral_dict("WL", [], requires_hq=True),
        _neutral_dict("TU", [], requires_hq=True),
    )
)

#: The complete, valid technology catalog: two per Branch.
FIXTURE_TECHNOLOGY_DICTS: tuple[dict[str, Any], ...] = tuple(
    _tech_dict(branch, index) for branch in BRANCHES for index in (0, 1)
)


# -------------------------------------------------------------- #
#  Registry construction (R15.4: no process-wide singleton needed)
# -------------------------------------------------------------- #

def make_registry(
    buildings: Any = (),
    technologies: Any = (),
    counter_web: Any = None,
    operation_kinds: Any = None,
    balance: BalanceConfig | None = None,
) -> DataRegistry:
    """Build a ``DataRegistry`` in memory from YAML-shaped dicts.

    Routes the building and technology dicts through the registry's own
    population helpers, so the definition round-trip under test is the real
    loader's and not a second implementation of it. The Counter_Web is
    normalized to the loaded shape (``{branch: (branch, ...)}``).

    The result is never registered as the process-wide singleton, which is the
    point: every collaborator takes it by injection (R15.4).
    """
    registry = DataRegistry()
    registry._populate_buildings([dict(entry) for entry in buildings])
    registry._populate_technologies([dict(entry) for entry in technologies])
    registry.counter_web = {
        key: tuple(values) for key, values in (counter_web or {}).items()
    }
    registry.operation_kinds = dict(operation_kinds or {})
    if balance is not None:
        registry.balance = balance
    return registry


def fixture_registry(balance: BalanceConfig | None = None) -> DataRegistry:
    """Return a fresh registry holding the complete, valid fixture catalog."""
    return make_registry(
        buildings=FIXTURE_BUILDING_DICTS,
        technologies=FIXTURE_TECHNOLOGY_DICTS,
        counter_web=CANONICAL_COUNTER_WEB,
        operation_kinds=FIXTURE_OPERATION_KINDS,
        balance=balance,
    )


# -------------------------------------------------------------- #
#  Fakes
# -------------------------------------------------------------- #

def _is_plain_data(value: Any) -> bool:
    """Return True when *value* is built only from primitives and containers."""
    if isinstance(value, (str, bytes, int, float, bool)) or value is None:
        return True
    if isinstance(value, (list, tuple, set, frozenset)):
        return all(_is_plain_data(item) for item in value)
    if isinstance(value, dict):
        return all(
            _is_plain_data(key) and _is_plain_data(item)
            for key, item in value.items()
        )
    return False


def _defensive_copy(value: Any) -> Any:
    """Return a copy of *value* that shares no mutable container with it.

    Deep-copies plain data (a persisted record list is plain data, so nested
    mutation is discarded too) and shallow-copies a container holding live
    objects, which keeps element identity intact while still discarding
    top-level mutation. Anything else is returned as-is.
    """
    if isinstance(value, (list, dict, set)):
        if _is_plain_data(value):
            return copy.deepcopy(value)
        return copy.copy(value)
    return value


class FakeAttributes:
    """Simulates Evennia's Attribute handler.

    ``get`` accepts ``default`` positionally or by keyword because
    ``world.utils.get_obj_attr`` calls it as ``get(key, default=None)``.
    """

    def __init__(self, data: dict | None = None):
        self._data: dict[str, Any] = dict(data or {})

    def get(self, key, default=None, **_kwargs):
        return self._data.get(key, default)

    def add(self, key, value, **_kwargs):
        self._data[key] = value

    def remove(self, key, **_kwargs):
        self._data.pop(key, None)

    def has(self, key):
        return key in self._data

    def all(self):
        return dict(self._data)


class HostileFakeAttributes(FakeAttributes):
    """A ``FakeAttributes`` that discards in-place mutation of a read value.

    Deliberately hostile, so the read-copy-write persistence discipline (R14.7)
    is *tested* rather than assumed: a caller that does
    ``attributes.get("records").append(rec)`` and never writes back loses the
    append here, exactly as it would against a real Evennia Attribute holding a
    serialized container. Reads hand out a copy and writes take a copy, so
    neither side can reach the stored object.
    """

    def get(self, key, default=None, **_kwargs):
        if key not in self._data:
            return default
        return _defensive_copy(self._data[key])

    def add(self, key, value, **_kwargs):
        self._data[key] = _defensive_copy(value)

    def all(self):
        return _defensive_copy(dict(self._data))


class FakeDB:
    """Value-based ``db`` proxy over a :class:`FakeAttributes` store.

    Reading an unset key yields ``None`` (Evennia's own behavior), so the
    "absent attribute reads as the documented default" contract (R14.8) can be
    exercised without pre-seeding anything. Underscored and dunder names raise
    ``AttributeError`` so ``copy``/``pickle`` protocol probing still works.
    """

    def __init__(self, store: FakeAttributes):
        object.__setattr__(self, "_store", store)

    def __getattr__(self, key):
        if key.startswith("_"):
            raise AttributeError(key)
        return object.__getattribute__(self, "_store").get(key)

    def __setattr__(self, key, value):
        object.__getattribute__(self, "_store").add(key, value)


class FakePlayer:
    """Lightweight stand-in for a CombatCharacter.

    Carries the four resource methods the charge/refund path uses, the
    ``get_buildings`` enumeration every Branch query walks, and an
    ``attributes``/``db`` pair so persisted Branch state is reachable.
    """

    def __init__(
        self,
        key: str = "TestPlayer",
        resources: dict[str, int] | None = None,
        buildings: Any = None,
        planet: str | None = None,
        x: int = 0,
        y: int = 0,
        level: int = 1,
        player_id: int | None = None,
        hostile: bool = False,
    ):
        self.key = key
        attributes_cls = HostileFakeAttributes if hostile else FakeAttributes
        self.attributes = attributes_cls()
        self.db = FakeDB(self.attributes)
        self.id = player_id if player_id is not None else id(self)
        self.location = None
        self._resources = {resource: 0 for resource in RESOURCE_TYPES}
        if resources:
            self._resources.update(resources)
        self._buildings: list[Any] = []
        self.db.coord_x = x
        self.db.coord_y = y
        self.db.coord_planet = planet
        self.db.level = level
        self.db.researched_techs = set()
        self.db.tech_bonuses = {}
        self.set_buildings(buildings or [])

    # -- resources ------------------------------------------------ #

    def get_resource(self, resource_type: str) -> int:
        return self._resources.get(resource_type, 0)

    def add_resource(self, resource_type: str, amount: int) -> None:
        self._resources[resource_type] = self.get_resource(resource_type) + amount

    def has_resources(self, costs: dict[str, int]) -> bool:
        return all(self.get_resource(r) >= amount for r, amount in costs.items())

    def deduct_resources(self, costs: dict[str, int]) -> bool:
        """Whole-or-none deduction, matching the real all-or-nothing charge."""
        if not self.has_resources(costs):
            return False
        for resource, amount in costs.items():
            self._resources[resource] = self.get_resource(resource) - amount
        return True

    def resource_snapshot(self) -> dict[str, int]:
        """Return a copy of every resource counter, for conservation checks."""
        return dict(self._resources)

    # -- buildings ------------------------------------------------ #

    def get_buildings(self) -> list:
        return list(self._buildings)

    def set_buildings(self, buildings: Any) -> None:
        """Adopt *buildings*, stamping this player as each one's owner."""
        self._buildings = list(buildings or [])
        for building in self._buildings:
            building.attributes.add("owner", self)


class FakeBuilding:
    """Lightweight stand-in for a Building object.

    ``planet`` is written to ``coord_planet`` because a real building derives
    its planet from its room and falls back to that attribute
    (``world.utils._building_planet``), so a fake needs no room to be
    planet-scoped.

    The three state flags are kept independent on purpose:

    - ``under_construction`` is the flag ``building_is_operational`` and
      ``owner_research_lab`` both read, so it decides whether a lab confers a
      Branch_Commitment at all.
    - ``offline`` is the other half of the Operational gate.
    - ``upgrading`` writes only the ``upgrade_target_level`` marker. In the real
      system an upgrade *also* sets ``under_construction``; keeping the marker
      separate lets a test build all four combinations, including the
      "mid-upgrade" pair, and assert which flags an answer does and does not
      depend on (R3.9, R5.10).
    """

    def __init__(
        self,
        building_type: str = "HQ",
        owner: Any = None,
        level: int = 1,
        hp: int = 500,
        hp_max: int = 500,
        offline: bool = False,
        under_construction: bool = False,
        upgrading: bool = False,
        planet: str | None = None,
        x: int = 0,
        y: int = 0,
        location: Any = None,
        hostile: bool = False,
    ):
        self.key = building_type
        attributes_cls = HostileFakeAttributes if hostile else FakeAttributes
        self.attributes = attributes_cls({
            "building_type": building_type,
            "owner": owner,
            "building_level": level,
            "hp": hp,
            "hp_max": hp_max,
            "offline": offline,
            "under_construction": under_construction,
            "coord_x": x,
            "coord_y": y,
            "coord_planet": planet,
        })
        if upgrading:
            self.attributes.add("upgrade_target_level", level + 1)
        self.db = FakeDB(self.attributes)
        self.location = location
        self._deleted = False

    @property
    def owner(self):
        return self.attributes.get("owner")

    @property
    def building_level(self):
        return self.attributes.get("building_level", default=1)

    @property
    def is_offline(self):
        return bool(self.attributes.get("offline", default=False))

    def set_offline(self, state: bool):
        self.attributes.add("offline", state)

    @property
    def deleted(self) -> bool:
        return self._deleted

    def delete(self):
        self._deleted = True

    def __repr__(self):  # pragma: no cover - diagnostics only
        return (
            f"FakeBuilding({self.attributes.get('building_type')!r}, "
            f"planet={self.attributes.get('coord_planet')!r}, "
            f"under_construction={self.attributes.get('under_construction')!r})"
        )


# -------------------------------------------------------------- #
#  Optional-field helper
# -------------------------------------------------------------- #

class _Absent:
    """Sentinel for "this YAML key is not present at all"."""

    def __repr__(self):  # pragma: no cover - diagnostics only
        return "ABSENT"


#: Drawn in place of a value when the generated definition omits the key.
#: Distinct from ``None``, which is the key *present and null* case — the two
#: must both load to the documented default (R2.2, R6.1).
ABSENT = _Absent()


def optional_st(value_st: Any) -> Any:
    """Return a strategy over "key present", "key null", and "key absent"."""
    return st.one_of(st.just(ABSENT), st.none(), value_st)


def put_optional(target: dict, key: str, drawn: Any) -> None:
    """Assign ``target[key] = drawn`` unless *drawn* is :data:`ABSENT`."""
    if drawn is not ABSENT:
        target[key] = drawn


# -------------------------------------------------------------- #
#  Vocabulary strategies
# -------------------------------------------------------------- #

#: One of the six Branches.
branch_st = st.sampled_from(BRANCHES)

#: A Branch or ``None`` — the Neutral_Building / no-commitment case.
maybe_branch_st = st.one_of(st.none(), branch_st)

#: In- and out-of-vocabulary Branch values, for the validator rules that must
#: reject a typo. Text is length-capped purely to keep generation cheap.
noisy_branch_st = st.one_of(branch_st, st.text(max_size=8))

#: A two-letter building abbreviation.
abbr_st = st.text(alphabet=string.ascii_uppercase, min_size=2, max_size=2)

#: A resource -> positive amount map, the shape of every cost field.
cost_map_st = st.dictionaries(
    st.sampled_from(RESOURCE_TYPES),
    st.integers(min_value=1, max_value=500),
    max_size=4,
)

#: A key of the six-Branch fixture technology set.
tech_key_st = st.sampled_from(FIXTURE_TECH_KEYS)

#: A researched-technology set spanning all six Branches.
researched_set_st = st.sets(tech_key_st)

#: The Reinstatement exclusion set — recorded technologies whose effects stay
#: withheld until their reduced-cost job completes.
pending_set_st = st.sets(tech_key_st)

#: A game tick.
tick_st = st.integers(min_value=0, max_value=100_000)

#: An ``unlock_technology`` value: a resolvable key, an unknown key, or the
#: empty string that rule 3 rejects.
unlock_technology_st = st.one_of(
    tech_key_st,
    st.just(""),
    st.text(alphabet=string.ascii_lowercase + "_", max_size=8),
)

#: A technology effect payload over the five keys the tech system understands.
effect_value_st = st.dictionaries(
    st.sampled_from(TECH_BONUS_KEYS),
    st.floats(min_value=0.5, max_value=5.0, allow_nan=False, allow_infinity=False),
    max_size=3,
)

#: A Counter_Web graph, in- and out-of-vocabulary, including self-edges and
#: duplicate targets — everything Property 18's "never compounds" claim must
#: survive.
counter_web_st = st.dictionaries(
    noisy_branch_st,
    st.lists(noisy_branch_st, max_size=3),
    max_size=8,
)


class AgentState(NamedTuple):
    """The four flags carrier eligibility is the conjunction of (R7.1, R7.5)."""

    alive: bool
    role: str | None
    in_reserve: bool
    incapacitated: bool


#: The four eligibility flags, drawn independently so all sixteen truth
#: combinations are reachable.
agent_state_st = st.builds(
    AgentState,
    st.booleans(),
    st.one_of(st.none(), st.sampled_from(FIXTURE_AGENT_ROLES)),
    st.booleans(),
    st.booleans(),
)


# -------------------------------------------------------------- #
#  Definition-dict strategies
# -------------------------------------------------------------- #

@st.composite
def _building_def_dict(draw) -> dict[str, Any]:
    """Draw a building YAML dict exercising every Branch-field combination.

    ``branch`` and ``unlock_technology`` are each present, null, or absent, and
    the ``research_lab`` capability is drawn independently of ``research_tree``
    so a non-lab that wrongly declares a tree — and a lab whose ``branch``
    disagrees with its tree — are both generated.
    """
    abbr = draw(abbr_st)
    is_lab = draw(st.booleans())
    entry: dict[str, Any] = {
        "name": f"Generated {abbr}",
        "abbreviation": abbr,
        "cost": draw(cost_map_st),
        "max_health": draw(st.integers(min_value=50, max_value=1000)),
        "requires_hq": True,
        "required_terrain": None,
        "category": draw(st.sampled_from(FIXTURE_CATEGORIES)),
        "produces": None,
        "capabilities": [RESEARCH_LAB] if is_lab else [],
        "rank_requirement": draw(st.integers(min_value=1, max_value=12)),
        "map_symbol": abbr,
    }
    put_optional(entry, "research_tree", draw(optional_st(noisy_branch_st)))
    put_optional(entry, "branch", draw(optional_st(noisy_branch_st)))
    put_optional(entry, "unlock_technology", draw(optional_st(unlock_technology_st)))
    return entry


#: A building YAML dict (see :func:`_building_def_dict`).
building_def_dict_st = _building_def_dict()


@st.composite
def _tech_def_dict(draw) -> dict[str, Any]:
    """Draw a technology YAML dict whose ``tree`` may be out of vocabulary."""
    key = draw(st.text(alphabet=string.ascii_lowercase + "_", min_size=3, max_size=12))
    entry: dict[str, Any] = {
        "name": key.replace("_", " ").title() or "Unnamed",
        "key": key,
        "required_rank": draw(st.sampled_from(FIXTURE_RANK_NAMES)),
    }
    put_optional(entry, "tree", draw(optional_st(noisy_branch_st)))
    put_optional(entry, "resource_cost", draw(optional_st(cost_map_st)))
    put_optional(
        entry, "research_ticks",
        draw(optional_st(st.integers(min_value=1, max_value=200))),
    )
    put_optional(entry, "effect_type", draw(optional_st(st.just("bonus"))))
    put_optional(entry, "effect_value", draw(optional_st(effect_value_st)))
    return entry


#: A technology YAML dict (see :func:`_tech_def_dict`).
tech_def_dict_st = _tech_def_dict()


# -------------------------------------------------------------- #
#  Whole-dataset strategy
# -------------------------------------------------------------- #

#: Building key -> the pool a canonical-dataset mutation retargets it from.
#: Each pool stays type-plausible for its key: the Branch catalog rules are
#: about *values* (an unknown Branch, a dangling unlock, a rank below the lab's),
#: and a cost field holding a string would only test the loader's typing, which
#: the existing schema tests already own.
_BUILDING_MUTATION_POOLS: dict[str, Any] = {
    "branch": st.one_of(noisy_branch_st, st.none()),
    "unlock_technology": st.one_of(unlock_technology_st, st.none()),
    "research_tree": st.one_of(noisy_branch_st, st.none()),
    "rank_requirement": st.integers(min_value=1, max_value=30),
    "cost": cost_map_st,
    "capabilities": st.lists(st.just(RESEARCH_LAB), max_size=1),
}

#: Technology key -> its mutation pool.
_TECH_MUTATION_POOLS: dict[str, Any] = {
    "tree": st.one_of(noisy_branch_st, st.none()),
    "resource_cost": cost_map_st,
    "required_rank": st.sampled_from(FIXTURE_RANK_NAMES),
}

#: Technology keys a mutation may DELETE. ``required_rank`` is excluded because
#: the technology loader hard-requires it — dropping it raises out of the load,
#: which is a pre-existing contract and not a Branch rule under test.
_TECH_REMOVABLE_KEYS: tuple[str, ...] = ("tree", "resource_cost")


@st.composite
def _mutated_entries(draw, base, pools, removable=None):
    """Draw *base* with up to two entries dropped, retargeted, or stripped.

    Near misses are where the validator rules actually live: a dataset one
    ``branch`` away from valid exercises far more rules than a random one, which
    almost always fails everything at once.

    Args:
        base: The canonical entry tuple to perturb.
        pools: ``{key: strategy}`` — the keys an ``override`` may retarget and
            the pool each draws its replacement from.
        removable: Keys a ``remove_key`` may delete; defaults to every key in
            *pools*. Pass a narrower tuple to protect a loader-required key.
    """
    keys = tuple(pools)
    removable = keys if removable is None else removable
    entries = [dict(entry) for entry in base]
    for _ in range(draw(st.integers(min_value=0, max_value=2))):
        if not entries:
            break
        index = draw(st.integers(min_value=0, max_value=len(entries) - 1))
        operation = draw(st.sampled_from(("drop", "override", "remove_key")))
        if operation == "drop":
            entries.pop(index)
        elif operation == "override":
            key = draw(st.sampled_from(keys))
            entries[index][key] = draw(pools[key])
        else:
            entries[index].pop(draw(st.sampled_from(removable)), None)
    return tuple(entries)


@st.composite
def _role_branch_map(draw) -> dict[str, str]:
    """Draw a role -> Branch map: the canonical bijection, or a near miss."""
    mapping = dict(CANONICAL_ROLE_BRANCH)
    for _ in range(draw(st.integers(min_value=0, max_value=2))):
        operation = draw(st.sampled_from(("drop", "retarget", "add")))
        if operation == "add" or not mapping:
            role = draw(
                st.text(alphabet=string.ascii_lowercase, min_size=3, max_size=8)
            )
            mapping[role] = draw(noisy_branch_st)
        elif operation == "drop":
            del mapping[draw(st.sampled_from(sorted(mapping)))]
        else:
            mapping[draw(st.sampled_from(sorted(mapping)))] = draw(noisy_branch_st)
    return mapping


#: A role -> Branch map (see :func:`_role_branch_map`).
role_branch_st = _role_branch_map()


@dataclass
class BranchDataset:
    """One whole load input: the four things the Branch catalog rules read."""

    buildings: tuple[dict[str, Any], ...] = ()
    technologies: tuple[dict[str, Any], ...] = ()
    role_branch: dict[str, str] = field(default_factory=dict)
    counter_web: dict[str, list[str]] = field(default_factory=dict)

    def registry(self, balance: BalanceConfig | None = None) -> DataRegistry:
        """Load this dataset into a fresh, unregistered ``DataRegistry``."""
        return make_registry(
            buildings=self.buildings,
            technologies=self.technologies,
            counter_web=self.counter_web,
            operation_kinds=FIXTURE_OPERATION_KINDS,
            balance=balance,
        )


@st.composite
def _dataset(draw) -> BranchDataset:
    """Draw a whole load input, weighted toward near-valid catalogs.

    Two thirds of the draws start from the complete fixture catalog and mutate
    it; the rest are wholly random lists. Abbreviations and technology keys are
    unique within a draw, so the registry's abbreviation-keyed maps hold every
    generated entry and a reference scan can enumerate them.
    """
    if draw(st.sampled_from(("canonical", "canonical", "random"))) == "canonical":
        buildings = draw(_mutated_entries(
            FIXTURE_BUILDING_DICTS, _BUILDING_MUTATION_POOLS,
        ))
        technologies = draw(_mutated_entries(
            FIXTURE_TECHNOLOGY_DICTS, _TECH_MUTATION_POOLS,
            removable=_TECH_REMOVABLE_KEYS,
        ))
    else:
        buildings = tuple(draw(st.lists(
            building_def_dict_st, max_size=8,
            unique_by=lambda entry: entry["abbreviation"],
        )))
        technologies = tuple(draw(st.lists(
            tech_def_dict_st, max_size=8, unique_by=lambda entry: entry["key"],
        )))
    return BranchDataset(
        buildings=buildings,
        technologies=technologies,
        role_branch=draw(role_branch_st),
        counter_web=draw(st.one_of(
            st.just(dict(CANONICAL_COUNTER_WEB)), counter_web_st,
        )),
    )


#: A whole load input (see :func:`_dataset`).
dataset_st = _dataset()


# -------------------------------------------------------------- #
#  Owned-building roster
# -------------------------------------------------------------- #

@st.composite
def owned_buildings(draw, owner: Any = None, min_size: int = 0, max_size: int = 6):
    """Draw a roster of :class:`FakeBuilding` spread across two or three planets.

    Every building draws its own ``under_construction`` / ``offline`` /
    ``upgrading`` flags, which is what makes the commitment and estate
    properties able to assert *independence* from the last two. Coordinates are
    distinct so a refusal that reports them can be checked member by member.
    """
    planets = draw(st.sampled_from((FIXTURE_PLANETS[:2], FIXTURE_PLANETS)))
    specs = draw(st.lists(
        st.tuples(
            st.sampled_from(FIXTURE_BUILDING_ABBRS),
            st.sampled_from(planets),
            st.booleans(),
            st.booleans(),
            st.booleans(),
        ),
        min_size=min_size,
        max_size=max_size,
    ))
    buildings = []
    for index, (btype, planet, under_construction, offline, upgrading) in enumerate(specs):
        buildings.append(FakeBuilding(
            building_type=btype,
            owner=owner,
            planet=planet,
            under_construction=under_construction,
            offline=offline,
            upgrading=upgrading,
            x=index,
            y=index * 2,
        ))
    return buildings


#: A roster of owned buildings (see :func:`owned_buildings`). Bind the owner
#: afterwards with ``FakePlayer.set_buildings``, which stamps each building.
owned_buildings_st = owned_buildings()


# -------------------------------------------------------------- #
#  Balance-field value pools
# -------------------------------------------------------------- #

#: The seven cross-cutting Branch balance fields, each with the range its
#: validator rule enforces. The pools below mirror these bounds so a draw lands
#: on both sides of every boundary.
BRANCH_RANGE_FIELDS: tuple[str, ...] = (
    "branch_reinstatement_cost_fraction",
    "minimum_response_window_ticks",
    "counter_advantage_cap",
    "branch_cost_parity_tolerance",
    "new_player_vector_shield_level",
    "escalation_window_ticks",
    "escalation_cap",
)

#: The per-Operation_Kind cost maps — one per kind, validated as resource maps.
BRANCH_COST_FIELDS: tuple[str, ...] = tuple(f"{kind}_cost" for kind in OPERATION_KINDS)

#: The per-Operation_Kind integer tunables: cooldown, in-flight cap, agent XP.
BRANCH_KIND_INT_FIELDS: tuple[str, ...] = tuple(
    name
    for kind in OPERATION_KINDS
    for name in (
        f"{kind}_cooldown_ticks", f"{kind}_max_in_flight", f"agent_xp_{kind}",
    )
)

#: Every balance field this feature introduces.
BRANCH_BALANCE_FIELDS: tuple[str, ...] = (
    BRANCH_RANGE_FIELDS + BRANCH_COST_FIELDS + BRANCH_KIND_INT_FIELDS
)

#: field -> (in-range pool, out-of-range pool).
_RANGE_POOLS: dict[str, tuple[Any, Any]] = {
    "branch_reinstatement_cost_fraction": (
        st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
        st.one_of(
            st.floats(min_value=-10.0, max_value=-0.001, allow_infinity=False),
            st.floats(min_value=1.001, max_value=10.0, allow_infinity=False),
        ),
    ),
    "minimum_response_window_ticks": (
        st.integers(min_value=1, max_value=100),
        st.integers(min_value=-50, max_value=0),
    ),
    "counter_advantage_cap": (
        st.floats(min_value=1.0, max_value=5.0, allow_nan=False, allow_infinity=False),
        st.floats(min_value=-5.0, max_value=0.999, allow_infinity=False),
    ),
    "branch_cost_parity_tolerance": (
        st.floats(
            min_value=0.01, max_value=1.0, allow_nan=False, allow_infinity=False,
        ),
        st.one_of(
            st.just(0.0),
            st.floats(min_value=-5.0, max_value=-0.001, allow_infinity=False),
            st.floats(min_value=1.001, max_value=5.0, allow_infinity=False),
        ),
    ),
    "new_player_vector_shield_level": (
        st.integers(min_value=1, max_value=MAX_LEVEL),
        st.one_of(
            st.integers(min_value=-10, max_value=0),
            st.integers(min_value=MAX_LEVEL + 1, max_value=MAX_LEVEL + 50),
        ),
    ),
    "escalation_window_ticks": (
        st.integers(min_value=1, max_value=5_000),
        st.integers(min_value=-100, max_value=0),
    ),
    "escalation_cap": (
        st.integers(min_value=1, max_value=10),
        st.integers(min_value=-10, max_value=0),
    ),
}

#: Values that are the wrong type for any numeric field. ``booleans`` is in the
#: pool deliberately: ``bool`` is a subclass of ``int``, so it is the one wrong
#: type an int field's check does not catch, and the reference computation has
#: to agree with that.
_WRONG_TYPE_ST = st.one_of(
    st.text(max_size=6),
    st.booleans(),
    st.lists(st.integers(), max_size=2),
)

#: Non-finite floats. They pass every ``isinstance`` check, so only an explicit
#: finiteness test rejects them.
_NON_FINITE_ST = st.sampled_from((float("nan"), float("inf"), float("-inf")))

_VALID_COST_MAP_ST = st.dictionaries(
    st.sampled_from(RESOURCE_TYPES),
    st.integers(min_value=1, max_value=200),
    min_size=1,
    max_size=3,
)

_INVALID_COST_MAP_ST = st.one_of(
    # An unknown resource name: silent at load, permanently unpayable at runtime.
    st.dictionaries(
        st.text(alphabet=string.ascii_letters, min_size=1, max_size=6),
        st.integers(min_value=1, max_value=200),
        min_size=1,
        max_size=2,
    ),
    # A non-positive or non-integer amount.
    st.dictionaries(
        st.sampled_from(RESOURCE_TYPES),
        st.one_of(
            st.integers(min_value=-50, max_value=0),
            st.floats(min_value=0.1, max_value=5.0, allow_nan=False,
                      allow_infinity=False),
            st.just(True),
        ),
        min_size=1,
        max_size=2,
    ),
)


def balance_value_st(field_name: str) -> Any:
    """Return the value pool for one Branch balance field.

    Per-field rather than one global pool, because "invalid" means something
    different for each: a cost map fails on an unknown resource or a
    non-positive amount, while a scalar fails on its own documented range.
    Every pool also carries ``None`` (the absent case, which the validator
    skips), wrong types, and — for the scalars — non-finite floats.

    Args:
        field_name: A member of :data:`BRANCH_BALANCE_FIELDS`.

    Raises:
        KeyError: If *field_name* is not a Branch balance field, so a renamed
            field fails the test rather than silently generating nothing.
    """
    if field_name not in BRANCH_BALANCE_FIELDS:
        raise KeyError(f"not a Branch balance field: {field_name!r}")
    if field_name in BRANCH_COST_FIELDS:
        return st.one_of(
            st.none(), _VALID_COST_MAP_ST, _INVALID_COST_MAP_ST,
            st.text(max_size=4), st.integers(),
        )
    if field_name in BRANCH_KIND_INT_FIELDS:
        # Type-checked as an int, with no range rule of its own.
        return st.one_of(
            st.none(), st.integers(min_value=0, max_value=500), _WRONG_TYPE_ST,
            _NON_FINITE_ST,
        )
    valid_st, invalid_st = _RANGE_POOLS[field_name]
    return st.one_of(st.none(), valid_st, invalid_st, _WRONG_TYPE_ST, _NON_FINITE_ST)


def branch_balance_dict_st(fields: Any = None) -> Any:
    """Return a strategy over ``{field: value}`` across the Branch balance fields.

    The single-call form Property 26 needs: one dict carrying a value for every
    field, so the validator's "collect every violation in one pass" behavior is
    what gets measured rather than one field at a time.
    """
    names = tuple(fields) if fields is not None else BRANCH_BALANCE_FIELDS
    return st.fixed_dictionaries({name: balance_value_st(name) for name in names})

# -------------------------------------------------------------- #
#  Operation lifecycle: records, events, checks, and fault points
# -------------------------------------------------------------- #
#
# The generators the *operation* half of the feature draws from — Properties 13
# through 25 — as distinct from the catalog and commitment generators above.
#
# One warning about vocabulary, because two lifecycles live in this feature and
# they are NOT the same machine:
#
# - The **lab** lifecycle (``commit`` / ``demolish`` / ``destroy`` / ``rebuild``)
#   is Property 9's, and stays local to ``test_prop_branch_reinstatement.py``
#   where it belongs — it is about a building coming and going, and about which
#   loss owes Reinstatement.
# - The **operation** lifecycle below is about one Vector_Operation moving
#   through the six states of the Operation Contract (design §4.1).
#
# Nothing here imports ``OperationDriver``: the driver is a collaborator the
# properties inject, while these are the values they inject it *with*.


def _pinned(value: Any, default: Any) -> Any:
    """Return the strategy a caller-pinnable field should draw from.

    Lets one composite serve both "draw this field from the shared pool" and
    "hold this field fixed at a value I care about", which is what keeps the
    lifecycle properties from each growing their own near-copy of
    :func:`operation_records`.

    Args:
        value: ``None`` to draw from *default*; a strategy to draw from instead;
            or any other value, which pins the field to exactly that value.
        default: The shared pool for this field.

    Returns:
        A strategy.
    """
    if value is None:
        return default
    if isinstance(value, SearchStrategy):
        return value
    return st.just(value)


#: The small pool of integer ids every generated reference is spelled from.
#: Small on purpose: a *list* of records then reliably contains several records
#: sharing an owner, which is what an in-flight count restricted to one player
#: (R8.20) has to be measured over.
_REF_IDS: tuple[int, ...] = (1, 2, 3, 4, 5)

#: One persisted reference — an owner, an originating building, a Carrier_Agent,
#: or a target. A **value, never a live object** (design §7: "every field is a
#: value or a resolvable reference, never a live object graph"), in the three
#: shapes ``BranchSystem._owner_matches`` already resolves: an integer id, a
#: ``#dbref`` string spelling the same id, and ``None`` — the absent reference a
#: rebuild must Discard rather than track (R14.4).
#:
#: A property that needs a reference to resolve to a *particular* fake pins the
#: field (``operation_records(...)`` for a kind or a state,
#: ``dataclasses.replace`` for the rest) rather than fishing for a match here.
ref_st = st.one_of(
    st.none(),
    st.sampled_from(_REF_IDS),
    st.sampled_from(tuple(f"#{ref_id}" for ref_id in _REF_IDS)),
)

#: Three fixed identities, mixed into :data:`op_id_st` alongside fresh unique
#: ones so a drawn list of records reliably contains two records sharing one
#: ``op_id``. That collision is the whole point of keying the rebuild's tracked
#: map by identity (R14.3), and a pool of only-unique ids would never show it.
FIXTURE_OP_IDS: tuple[str, ...] = ("aa" * 16, "bb" * 16, "cc" * 16)

#: An Operation_Record identity: a fresh uuid4 hex (the shape
#: ``operation_contract.new_op_id`` mints), or one of the colliding fixtures.
op_id_st = st.one_of(
    st.uuids().map(lambda value: value.hex),
    st.sampled_from(FIXTURE_OP_IDS),
)

#: One Operation_Kind, from the six the shipped registry declares. The pool is
#: the constant rather than a literal list, so a renamed kind reaches every
#: property that samples it.
operation_kind_st = st.sampled_from(OPERATION_KINDS)

#: The six lifecycle states as the plain strings persistence holds (R8.1).
OPERATION_STATE_VALUES: tuple[str, ...] = tuple(
    str(state) for state in OperationState
)

#: The four states R8.2 declares terminal, by value.
TERMINAL_STATE_VALUES: tuple[str, ...] = tuple(
    str(state) for state in OperationState if state in TERMINAL_STATES
)

#: The two an operation can still be moved out of: Pending and Suspended.
LIVE_STATE_VALUES: tuple[str, ...] = tuple(
    str(state) for state in OperationState if state not in TERMINAL_STATES
)

#: One lifecycle state, drawn BOTH as an :class:`OperationState` member and as
#: the plain string a persisted record holds. The two are interchangeable —
#: ``OperationState`` is a ``StrEnum``, so a member and its value compare and
#: hash alike — and drawing both is what stops a property from quietly asserting
#: on the *type* of a state instead of on the state.
operation_state_st = st.one_of(
    st.sampled_from(tuple(OperationState)),
    st.sampled_from(OPERATION_STATE_VALUES),
)

#: A target coordinate pair: both set, or neither. A vector targets a tile or an
#: entity (design §7: the coordinate is ``None`` "where the vector targets an
#: entity rather than a tile"), so a record with exactly one of the two set is
#: not a state the game reaches. The half-set combinations are still exercised —
#: through Property 23, which strips keys out of a persisted payload and asserts
#: the read back never raises.
_target_coord_st = st.one_of(
    st.just((None, None)),
    st.tuples(
        st.integers(min_value=-20, max_value=20),
        st.integers(min_value=-20, max_value=20),
    ),
)


@st.composite
def operation_records(draw, kind: Any = None, state: Any = None) -> OperationRecord:
    """Draw an :class:`OperationRecord` over every persisted field (design §7).

    Every one of the sixteen fields is drawn, including each field's ``None``
    case where it has one, because the properties this feeds are about exactly
    those cases: the round-trip (Property 21) has to move an absent lifetime and
    an absent coordinate through storage unchanged, the rebuild (Property 22) has
    to Discard a record whose reference is absent, and the partial read (Property
    23) starts from a full record and takes keys away.

    The fields are drawn **independently**, so a combination like "Resolved with
    ticks left" or "never suspended but holding a suspension snapshot" is
    reachable. That is deliberate: a record read back out of storage was written
    by code the test does not control, and the contract's claims — a terminal
    state is final, a partial read never raises — are unconditional, so a
    generator that only produced tidy records would test a smaller contract than
    the one that shipped. The one coherence the generator does keep is the
    coordinate pair (see :data:`_target_coord_st`), because a half-set pair
    misstates what the vector was aiming at rather than merely being untidy.

    Args:
        kind: ``None`` to draw from :data:`operation_kind_st`; a strategy or a
            fixed Operation_Kind to pin it (a count restricted to one kind, or a
            cooldown keyed to one, wants it pinned).
        state: ``None`` to draw from :data:`operation_state_st`; a strategy or a
            fixed state to pin it — ``operation_records(state=
            OperationState.PENDING)`` is the in-flight record the tick-advance
            and timing properties start from.

    Returns:
        A strategy over one record. ``dataclasses.replace`` pins any other field.
    """
    target_x, target_y = draw(_target_coord_st)
    return OperationRecord(
        op_id=draw(op_id_st),
        kind=draw(_pinned(kind, operation_kind_st)),
        owner_ref=draw(ref_st),
        building_ref=draw(ref_st),
        carrier_ref=draw(ref_st),
        planet=draw(st.one_of(st.none(), st.sampled_from(FIXTURE_PLANETS))),
        target_x=target_x,
        target_y=target_y,
        target_ref=draw(ref_st),
        ticks_remaining=draw(st.integers(min_value=0, max_value=200)),
        lifetime_remaining=draw(
            st.one_of(st.none(), st.integers(min_value=0, max_value=200))
        ),
        magnitude=draw(st.floats(
            min_value=0.0, max_value=500.0, allow_nan=False, allow_infinity=False,
        )),
        radius=draw(st.integers(min_value=0, max_value=8)),
        state=draw(_pinned(state, operation_state_st)),
        suspended_ticks=draw(
            st.one_of(st.none(), st.integers(min_value=0, max_value=200))
        ),
        charged=draw(cost_map_st),
    )


#: One Operation_Record (see :func:`operation_records`). The design's
#: ``st.builds(OperationRecord, ...)`` "over every persisted field including the
#: ``None`` cases", as a composite so the coordinate pair can stay coherent.
record_st = operation_records()


#: The lifecycle-event vocabulary: every input that moves a Vector_Operation
#: between the six states, which is the design's §4.1 state machine read as its
#: edges. Sampled by :data:`lifecycle_event_st`, sequenced by
#: :data:`event_sequence_st`, and applied one at a time by Property 15.
#:
#: What each event is, and the transition the contract declares for it:
#:
#: ==================  =========================================================
#: ``tick``            One advance (R8.9). Decrements the bounded lifetime and
#:                     then the effect clock: Pending -> Resolved when the
#:                     effect clock runs out (R8.11), Pending -> Expired when
#:                     the lifetime runs out first (R8.13). A Suspended
#:                     operation does not move and its clocks do not run
#:                     (R8.14).
#: ``suspend``         The pause, entered directly. Snapshots the remaining
#:                     ticks (R8.15).
#: ``resume``          The carrier is eligible again: Suspended -> Pending with
#:                     the snapshotted ticks, so a suspension delays rather
#:                     than restarts (R8.15).
#: ``resolve``         The effect applies: -> Resolved (R8.11).
#: ``expire``          The bounded lifetime elapses: -> Expired, and each
#:                     entity the operation suspended is restored (R8.13).
#: ``cancel``          -> Cancelled, requested directly.
#: ``discard``         -> Discarded, the state a rebuild puts a record in when a
#:                     reference no longer exists (R14.4).
#: ``carrier_killed``  The Carrier_Agent dies: -> Cancelled (R8.16).
#: ``building_lost``   The originating building goes non-Operational or is
#:                     destroyed: -> Cancelled (R8.17).
#: ``commitment_lost`` The owner loses the Branch_Commitment the operation
#:                     requires: -> Suspended, so a dormant Branch resolves
#:                     nothing (R8.18).
#: ``base_eliminated`` A base elimination removes the originating building: ->
#:                     Cancelled, for exactly the operations whose building was
#:                     removed (R11.4).
#: ==================  =========================================================
#:
#: The four terminal events sit in the pool beside the conditions that cause
#: them on purpose: a sequence can reach a terminal state directly and then keep
#: delivering events, which is how "a terminal state is final" (R8.2) gets
#: exercised without waiting for a clock to run down first.
#:
#: NOT the lab lifecycle. ``commit`` / ``demolish`` / ``destroy`` / ``rebuild``
#: (Property 9) are a different machine over a different subject, and the two
#: vocabularies must not be crossed.
LIFECYCLE_EVENTS: tuple[str, ...] = (
    "tick",
    "suspend",
    "resume",
    "resolve",
    "expire",
    "cancel",
    "discard",
    "carrier_killed",
    "building_lost",
    "commitment_lost",
    "base_eliminated",
)

#: One lifecycle event (see :data:`LIFECYCLE_EVENTS`).
lifecycle_event_st = st.sampled_from(LIFECYCLE_EVENTS)

#: A sequence of lifecycle events. At least one, because an empty sequence
#: asserts nothing; capped at twenty-five, which is long enough for a record to
#: reach a terminal state early and still be handed a dozen further events. The
#: interesting failures here live in the *ordering*, which is why the properties
#: that draw this raise ``max_examples`` to 200.
event_sequence_st = st.lists(lifecycle_event_st, min_size=1, max_size=25)


#: The nine checks a Vector_Operation request runs, **in the order R8.3 fixes**
#: (design §4.2). The order is the point: a request refuses at the first failing
#: check, so the earliest name present in a forced-failure subset is the one
#: reason the refusal may carry.
#:
#: What each check answers, and why it sits where it does — cheap identity
#: questions first, then refusals a player can act on immediately, then the ones
#: that depend on timing, and resource sufficiency last so a structurally
#: blocked request hears the structural reason rather than "not enough Iron":
#:
#: 1. ``collaborators``  — every declared collaborator is wired (R15.2).
#: 2. ``commitment``     — the owner's Branch_Commitment matches this vector's
#:                         Branch.
#: 3. ``origin``         — the originating building is owned, Operational, and
#:                         under an active HQ.
#: 4. ``unlock``         — that building's unlocking technology is researched.
#: 5. ``carrier``        — an eligible Carrier_Agent of the required role
#:                         (R7.3).
#: 6. ``target``         — the vector's own target validity, plus the new-player
#:                         shield, the allied-target refusal, and the escalation
#:                         cap (R10.4, R10.6, R11.9).
#: 7. ``cooldown``       — this building's cooldown for this kind has elapsed
#:                         (R8.19).
#: 8. ``in_flight``      — the simultaneous-operation cap (R8.20).
#: 9. ``resources``      — sufficiency; the charge happens after (R12.3).
#:
#: **Authority.** ``OperationDriver._CHECK_ORDER`` (task 11.2) is the authority
#: on this tuple; the copy here is written from the design because the
#: generators land before the driver does. The two must stay identical, so a
#: later task should assert their equality once in the driver's unit tests —
#: a one-line cross-check, in the same spirit as the ``TERMINAL_STATES`` /
#: ``BranchSystem._TERMINAL_STATE_NAMES`` cross-check that already exists.
OPERATION_CHECK_ORDER: tuple[str, ...] = (
    "collaborators",
    "commitment",
    "origin",
    "unlock",
    "carrier",
    "target",
    "cooldown",
    "in_flight",
    "resources",
)

#: A subset of the nine checks to force failing — the forced-failure lattice
#: Property 13 walks. The empty set is included and is the interesting bottom of
#: the lattice: nothing forced to fail must yield an accepted operation, which is
#: what stops the property from passing on a driver that refuses everything.
check_subset_st = st.sets(st.sampled_from(OPERATION_CHECK_ORDER))


#: The points a fault can be injected at along one request, in request order.
#: The pool the resource-conservation property (Property 14) draws from, and the
#: reason it can state R8.6 — "no Vector_Operation both charges and fails" — over
#: the whole path rather than over one hand-picked failure:
#:
#: - ``none``          — no fault. The clean path, and the only one that may end
#:                       with the player's resources reduced: accepted, Pending,
#:                       charged exactly the Operation_Kind's cost (R8.5).
#: - ``check``         — a check refuses, before any charge. Nothing is charged
#:                       and nothing else changes either (R8.4).
#: - ``charge``        — the whole-or-none charge itself fails, as it does when
#:                       the player cannot afford the cost. Refused with the
#:                       have-and-need breakdown, and no partial deduction
#:                       (R12.2, R12.3).
#: - ``build_record``  — the vector's ``build_record`` hook raises **after** the
#:                       charge.
#: - ``track``         — tracking the new record raises after the charge.
#: - ``persist``       — the first persist of the new record raises after the
#:                       charge.
#:
#: The last three are the R8.6 window — charged, but never reached Pending — and
#: each must end in a full refund and a ``failed`` outcome. They are enumerated
#: separately rather than as one "raise somewhere" case because they fail at
#: different points in the same ``try``, and a refund written after only one of
#: them would pass a single-point test.
#:
#: The pool stops at Pending entry, which is where R8.6's obligation stops: what
#: happens after an operation is accepted (its notifications, its cooldown note)
#: cannot un-charge a request that succeeded.
FAULT_POINTS: tuple[str, ...] = (
    "none",
    "check",
    "charge",
    "build_record",
    "track",
    "persist",
)

#: One fault point (see :data:`FAULT_POINTS`).
fault_point_st = st.sampled_from(FAULT_POINTS)

#: The fault points that land AFTER the cost is charged — the ones a refund has
#: to answer for (R8.6). Named so a property can assert the refund path
#: specifically instead of only asserting conservation over the whole pool.
POST_CHARGE_FAULT_POINTS: tuple[str, ...] = ("build_record", "track", "persist")
