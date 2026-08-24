"""
Architectural guards for the Branch framework.

Feature: tech-tree-branch-foundation, design section "Testing Strategy / Unit
tests / Architectural guards", plus the no-upkeep guard beside it. These are the
tests that hold the *shape* of the feature rather than its behaviour, so each one
exists to fail loudly the day a convention is quietly dropped:

* **No framework at module scope (R15.1).** ``branch_system`` is imported in a
  **subprocess** with ``evennia`` genuinely absent from ``sys.modules``, the
  whole read-only query surface is answered there, and the answers are compared
  to the same surface answered in-process (where ``conftest`` has installed the
  Evennia stubs). A source-level AST scan backs it up: no import that *executes*
  at import time names the framework.
* **No global registry (R15.4).** The same query surface is answered three times
  — with no process-wide ``DataRegistry`` installed, with a *conflicting* one
  installed, and with the injected one installed — and all three answers must be
  identical. Clearing the singleton alone would prove little: a query that reads
  ``DataRegistry.get_instance()`` and falls back to the injected registry would
  survive it, so the conflicting-singleton direction is what makes the guard bite.
* **One writer per attribute (R15.5).** Every non-test module under ``mygame`` is
  scanned (AST, not text) for a write to any of the five persisted attributes
  this feature introduces — ``branch_abandoned``, ``branch_reinstatement`` and
  ``vector_consent`` on a player, ``vector_cooldowns`` on the originating
  *building*, ``vector_escalation`` on the attacking player. ``branch_system.py``
  is the only module allowed to write them. ``tech_system`` deliberately calls
  ``BranchSystem.on_reinstatement_completed`` instead of assigning
  ``db.branch_reinstatement``; this scan is what catches a regression there.
* **Every knob stays hot (R15.7).** Each of the thirty-one Balance_Config fields
  this feature introduces is mutated on the injected registry *after* the system
  is constructed, and the next call must reflect it. The twelve fields no shipped
  code path consumes yet — the per-Operation_Kind cost and agent-XP fields the six
  vector specs will read — are asserted to be exactly that: bound by name in the
  Operation_Kind registry, and inert across the whole query surface. The gap is
  documented by a test rather than by a skip.
* **No recurring upkeep (R12.8).** A hundred ticks pass with a Branch_Estate
  standing and no operations in flight; not one resource counter moves.

**Why this module builds its own fakes.** Every other Branch test imports
``branch_strategies``, which installs Evennia stubs at import time — that is
exactly what the R15.1 guard must not have happen, because a stub *named*
``evennia`` in ``sys.modules`` is indistinguishable from the framework as far as
an import is concerned. So the fixtures below are deliberately self-contained and
framework-free, and this module imports nothing that reaches the framework
transitively (notably **not** ``agent_system``, which pulls in a typeclass and
therefore the real Evennia). The subprocess imports *this module* and answers the
same query table, which is what makes the comparison exact.

**Validates: Requirements 12.8, 15.1, 15.4, 15.5, 15.7**
"""

import ast
import json
import os
import subprocess
import sys
import unittest
from dataclasses import fields as dataclass_fields
from types import SimpleNamespace

from mygame.world.constants import (
    ATTR_BRANCH_ABANDONED,
    ATTR_BRANCH_REINSTATEMENT,
    ATTR_VECTOR_CONSENT,
    ATTR_VECTOR_COOLDOWNS,
    ATTR_VECTOR_ESCALATION,
    BRANCH_OPERATION_KIND,
    BRANCH_ROLE,
    BRANCHES,
    CONSENT_SUPPORT,
    CONSENT_TARGET_SHARING,
    OPERATION_KINDS,
    RESEARCH_LAB,
    RESOURCE_TYPES,
)
from mygame.world.data_registry import DataRegistry
from mygame.world.definitions import BalanceConfig, OperationKindDef
from mygame.world.event_bus import EventBus
from mygame.world.schema_validator import SchemaValidator
from mygame.world.systems.branch_system import BranchSystem
from mygame.world.systems.tech_system import TechLabSystem

# ------------------------------------------------------------------ #
#  Repository layout
# ------------------------------------------------------------------ #

#: ``mygame/world/systems/tests/`` -> ``mygame/``.
MYGAME_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

#: The repository root, the parent of ``mygame``.
REPO_ROOT = os.path.normpath(os.path.join(MYGAME_DIR, ".."))

#: The module this feature declares as the single writer of its player state.
BRANCH_SYSTEM_PATH = os.path.join(MYGAME_DIR, "world", "systems", "branch_system.py")


# ------------------------------------------------------------------ #
#  Framework-free fakes (see the module docstring for why they are local)
# ------------------------------------------------------------------ #

class FakeAttributes:
    """Evennia's Attribute handler, reduced to the four calls the code makes."""

    def __init__(self, data=None):
        self._data = dict(data or {})

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


class FakeDB:
    """Value-based ``db`` proxy: an unset key reads as ``None``, as Evennia's does."""

    def __init__(self, store):
        object.__setattr__(self, "_store", store)

    def __getattr__(self, key):
        if key.startswith("_"):
            raise AttributeError(key)
        return object.__getattribute__(self, "_store").get(key)

    def __setattr__(self, key, value):
        object.__getattribute__(self, "_store").add(key, value)


class FakePlayer:
    """The player surface the Branch queries touch: resources, buildings, ``db``."""

    def __init__(self, key, player_id, planet=None, level=1, resources=None, alliance=None):
        self.key = key
        self.id = player_id
        self.attributes = FakeAttributes()
        self.db = FakeDB(self.attributes)
        self.location = None
        self._resources = {resource: 0 for resource in RESOURCE_TYPES}
        self._resources.update(resources or {})
        self._buildings = []
        self.db.coord_planet = planet
        self.db.level = level
        self.db.researched_techs = set()
        self.db.tech_bonuses = {}
        if alliance is not None:
            self.db.player_alliance = alliance

    # -- resources -------------------------------------------------- #

    def get_resource(self, resource_type):
        return self._resources.get(resource_type, 0)

    def has_resources(self, costs):
        return all(self.get_resource(r) >= amount for r, amount in costs.items())

    def deduct_resources(self, costs):
        if not self.has_resources(costs):
            return False
        for resource, amount in costs.items():
            self._resources[resource] = self.get_resource(resource) - amount
        return True

    def add_resource(self, resource_type, amount):
        self._resources[resource_type] = self.get_resource(resource_type) + amount

    def resource_snapshot(self):
        return dict(self._resources)

    # -- buildings -------------------------------------------------- #

    def get_buildings(self):
        return list(self._buildings)

    def set_buildings(self, buildings):
        self._buildings = list(buildings)
        for building in self._buildings:
            building.attributes.add("owner", self)


class FakeBuilding:
    """A building the ownership scans, the Operational gate, and a ledger can read."""

    def __init__(self, building_type, planet=None, under_construction=False, offline=False):
        self.key = building_type
        self.attributes = FakeAttributes({
            "building_type": building_type,
            "owner": None,
            "building_level": 1,
            "hp": 500,
            "hp_max": 500,
            "offline": offline,
            "under_construction": under_construction,
            "coord_planet": planet,
            "coord_x": 0,
            "coord_y": 0,
        })
        self.db = FakeDB(self.attributes)
        self.location = None

    @property
    def owner(self):
        return self.attributes.get("owner")

    @property
    def is_offline(self):
        return bool(self.attributes.get("offline", False))


class FakeAgent:
    """The four eligibility flags a Carrier_Agent lookup reads, and nothing else."""

    def __init__(self, key, role, planet=None, hp=100, reserve=False, incapacitated=False):
        self.key = key
        self.attributes = FakeAttributes({
            "role": role,
            "coord_planet": planet,
            "hp": hp,
            "reserve": reserve,
            "incapacitated": incapacitated,
        })
        self.db = FakeDB(self.attributes)


class FakeAgentSystem:
    """The one call ``_owned_agents`` makes of the injected AgentSystem."""

    def __init__(self, agents=()):
        self._agents = list(agents)

    def get_agents(self, _player):
        return list(self._agents)


class FakeAllianceSystem:
    """The three reads the targeting and consent paths make of the AllianceSystem."""

    def __init__(self, pairs=(), members=()):
        self._pairs = [(first, second) for first, second in pairs]
        self._members = list(members)

    def are_allied(self, first, second):
        if first is second:
            return False
        return any(
            (first is a and second is b) or (first is b and second is a)
            for a, b in self._pairs
        )

    def alliance_summary(self, _alliance_id, **_kwargs):
        return {"name": "The Pact", "tag": "PCT"}

    def _live_members(self, _alliance_id):
        return list(self._members)


class FakeVector:
    """A Vector_System stand-in: one Operation_Kind and a tracked-record list."""

    def __init__(self, kind, records=()):
        self.operation_kind = kind
        self._tracked = list(records)
        self.advances = []

    def tracked_records(self):
        return list(self._tracked)

    def advance_all(self, tick):
        self.advances.append(tick)


class FakeClock:
    """The injected tick source, driven by the test rather than by a script."""

    def __init__(self, tick=0):
        self.tick = tick

    def __call__(self):
        return self.tick


# ------------------------------------------------------------------ #
#  The fixture catalog
# ------------------------------------------------------------------ #
#
# Self-contained rather than read from data/definitions/, for the same reason
# branch_strategies' fixture is: a guard has to be able to state the expected
# answer, which is only possible over a catalog the test controls. ``SX`` (not
# the design's ``SG``) hosts ``cyber`` — ``SG`` is the Shield Generator.

LAB_ABBR = {
    "weapons": "WX",
    "defense": "DF",
    "resource": "RX",
    "research": "LB",
    "bio": "BX",
    "cyber": "SX",
}

#: Branch -> one non-lab Branch_Building. Synthetic ``Z*`` codes, so a fixture
#: building can never be mistaken for a shipped one.
WORKS_ABBR = {
    "weapons": "ZW",
    "defense": "ZD",
    "resource": "ZR",
    "research": "ZE",
    "bio": "ZB",
    "cyber": "ZC",
}

NEUTRAL_ABBR = "HQ"

#: Branch -> its two technology keys.
TECH_KEYS = {branch: (f"{branch}_core", f"{branch}_adv") for branch in BRANCHES}

#: Every technology key the fixture defines, in Branch order.
ALL_TECH_KEYS = tuple(key for branch in BRANCHES for key in TECH_KEYS[branch])

#: Every building abbreviation the fixture defines.
ALL_ABBRS = (
    tuple(LAB_ABBR[branch] for branch in BRANCHES)
    + tuple(WORKS_ABBR[branch] for branch in BRANCHES)
    + (NEUTRAL_ABBR,)
)

#: The shipped Counter_Web cycle: one advantage and one disadvantage per Branch.
COUNTER_WEB = {
    "weapons": ["defense"],
    "defense": ["bio"],
    "bio": ["cyber"],
    "cyber": ["resource"],
    "resource": ["research"],
    "research": ["weapons"],
}

#: The Operation_Kind registry, derived from the constants so the fixture cannot
#: disagree with the shipped Branch -> kind -> role -> balance-field bindings.
OPERATION_KIND_DEFS = {
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

#: The Branch whose lab is deliberately dearer than the rest, so the
#: investment-parity tolerance has something to bite on (see the R15.7 probe).
DEAR_BRANCH = "weapons"


def _building_dicts():
    """Return the YAML-shaped building catalog: six labs, six works, one neutral."""
    entries = []
    for branch in BRANCHES:
        abbr = LAB_ABBR[branch]
        entries.append({
            "name": f"{branch.title()} Lab",
            "abbreviation": abbr,
            "cost": {"Iron": 100 if branch == DEAR_BRANCH else 60},
            "max_health": 400,
            "category": "research",
            "capabilities": [RESEARCH_LAB],
            "research_tree": branch,
            "branch": branch,
            "rank_requirement": 5,
            "map_symbol": abbr,
        })
        works = WORKS_ABBR[branch]
        entries.append({
            "name": f"{branch.title()} Works",
            "abbreviation": works,
            "cost": {"Iron": 40},
            "max_health": 250,
            "category": "utility",
            "capabilities": [],
            "branch": branch,
            "unlock_technology": TECH_KEYS[branch][0],
            "rank_requirement": 6,
            "map_symbol": works,
        })
    entries.append({
        "name": "Headquarters",
        "abbreviation": NEUTRAL_ABBR,
        "cost": {"Wood": 30, "Stone": 20},
        "max_health": 300,
        "requires_hq": False,
        "category": "utility",
        "capabilities": ["headquarters"],
        "rank_requirement": 1,
        "map_symbol": NEUTRAL_ABBR,
    })
    return entries


def _technology_dicts():
    """Return the YAML-shaped technology catalog: two per Branch."""
    return [
        {
            "name": key.replace("_", " ").title(),
            "key": key,
            "required_rank": "Recruit",
            "resource_cost": {"Circuits": 10},
            "research_ticks": 10,
            "effect_type": "bonus",
            "effect_value": {"damage": 1.0},
            "tree": branch,
        }
        for branch in BRANCHES
        for key in TECH_KEYS[branch]
    ]


def make_registry():
    """Build a ``DataRegistry`` in memory, through the real loader helpers.

    Never registered as the process-wide singleton, which is the point: every
    collaborator takes it by injection (R15.4). The balance is a fresh
    ``BalanceConfig`` at its declared defaults, so the R15.7 probes retune from a
    known baseline.
    """
    registry = DataRegistry()
    registry._populate_buildings(_building_dicts())
    registry._populate_technologies(_technology_dicts())
    registry.counter_web = {key: tuple(values) for key, values in COUNTER_WEB.items()}
    registry.operation_kinds = dict(OPERATION_KIND_DEFS)
    registry.balance = BalanceConfig()
    return registry


def rival_registry():
    """Return a catalog that shares not one answer with :func:`make_registry`.

    Installed as the process-wide singleton by the R15.4 guard: an accessor that
    reads ``DataRegistry.get_instance()`` answers about *this* catalog, whose
    Branch-to-lab map is rotated and whose technology keys are all its own, so the
    mismatch is unmissable.
    """
    rotated = {
        branch: LAB_ABBR[BRANCHES[(index + 1) % len(BRANCHES)]]
        for index, branch in enumerate(BRANCHES)
    }
    registry = DataRegistry()
    registry._populate_buildings([
        {
            "name": f"Rival {branch} Lab",
            "abbreviation": rotated[branch],
            "cost": {"Iron": 999},
            "max_health": 999,
            "category": "research",
            "capabilities": [RESEARCH_LAB],
            "research_tree": branch,
            "map_symbol": rotated[branch],
        }
        for branch in BRANCHES
    ])
    registry._populate_technologies([
        {
            "name": f"Rival {branch}",
            "key": f"rival_{branch}_technology",
            "required_rank": "Recruit",
            "tree": branch,
        }
        for branch in BRANCHES
    ])
    registry.counter_web = {
        branch: (BRANCHES[(index + 3) % len(BRANCHES)],)
        for index, branch in enumerate(BRANCHES)
    }
    registry.operation_kinds = {}
    registry.balance = BalanceConfig(
        minimum_response_window_ticks=99,
        counter_advantage_cap=9.0,
        new_player_vector_shield_level=1,
        escalation_cap=99,
        escalation_window_ticks=9,
    )
    return registry


# ------------------------------------------------------------------ #
#  The world the query surface is answered over
# ------------------------------------------------------------------ #

#: The tick the injected clock reads, and the cooldown horizon seeded on the
#: originating building. 100 ticks of headroom means a retuned cooldown length is
#: what the remaining figure clamps to, which is exactly the R15.7 claim.
NOW = 10
COOLDOWN_HORIZON = 110

#: The planet every fixture entity stands on, and one the owner has nothing on.
HOME = "earth"
AWAY = "mars"


def branch_world(register_vector=True):
    """Return the fully-wired fixture world the query table is answered over.

    Every value is fixed, so the answers are a pure function of the injected
    registry — which is what lets the same table be compared across processes and
    across singleton states.

    Args:
        register_vector: Leave it ``True`` for the query table, which needs a
            registered Vector_System for the in-flight count to be about
            something. ``False`` gives the state this feature actually SHIPS in —
            no vector registered, so the tick fan-out iterates nothing — which is
            what the no-upkeep guard runs its hundred ticks over.
    """
    registry = make_registry()
    bus = EventBus()
    clock = FakeClock(NOW)

    owner = FakePlayer("Owner", 1, planet=HOME, level=30,
                       resources={"Iron": 100, "Circuits": 3})
    victim = FakePlayer("Victim", 2, planet=HOME, level=3)
    ally = FakePlayer("Ally", 3, planet=HOME, level=30, alliance="pact")
    stranger = FakePlayer("Stranger", 4, planet=HOME, level=12)
    silent_ally = FakePlayer("SilentAlly", 5, planet=HOME, level=30, alliance="pact")

    lab = FakeBuilding(LAB_ABBR["weapons"], planet=HOME)
    works = FakeBuilding(WORKS_ABBR["weapons"], planet=HOME)
    dormant_works = FakeBuilding(WORKS_ABBR["defense"], planet=HOME, under_construction=True)
    neutral = FakeBuilding(NEUTRAL_ABBR, planet=HOME)
    owner.set_buildings([lab, works, dormant_works, neutral])

    owner.db.researched_techs = {
        TECH_KEYS["weapons"][0], TECH_KEYS["weapons"][1], TECH_KEYS["defense"][0],
    }
    # Seeded directly rather than through _seed_reinstatement: a test module is
    # not bound by the single-writer rule the R15.5 scan enforces on shipped
    # code, and one recorded-but-pending key makes the applied/pending split
    # visible in the snapshot.
    owner.db.branch_reinstatement = {"weapons": [TECH_KEYS["weapons"][0]]}
    owner.db.branch_abandoned = {"defense": True}
    # One cooldown per Operation_Kind, all with the same horizon, so each kind's
    # retuned length is the only thing its remaining figure can depend on.
    works.db.vector_cooldowns = {kind: COOLDOWN_HORIZON for kind in OPERATION_KINDS}
    # Three resolutions against the victim: the default cap is three, so the
    # ledger sits exactly at the limit and both escalation knobs are live.
    owner.db.vector_escalation = {victim.id: [NOW - 5, NOW - 4, NOW - 3]}

    agents = FakeAgentSystem([
        FakeAgent("Agent#1", BRANCH_ROLE["weapons"], planet=HOME),
        FakeAgent("Agent#2", BRANCH_ROLE["defense"], planet=HOME, reserve=True),
        FakeAgent("Agent#3", BRANCH_ROLE["bio"], planet=HOME, hp=0),
        FakeAgent("Agent#4", BRANCH_ROLE["cyber"], planet=AWAY),
    ])
    alliance = FakeAllianceSystem(
        pairs=[(owner, ally), (owner, silent_ally)],
        members=[owner, ally, silent_ally],
    )
    tech = TechLabSystem(registry, bus)
    system = BranchSystem(
        registry,
        bus,
        current_tick_func=clock,
        tech_system=tech,
        agent_system=agents,
        alliance_system=alliance,
    )
    tech.set_branch_resolver(system)

    kind = BRANCH_OPERATION_KIND["weapons"]
    vector = FakeVector(kind, records=[
        SimpleNamespace(kind=kind, owner_ref=owner.id, planet=HOME, state="pending"),
        SimpleNamespace(kind=kind, owner_ref=owner.id, planet=HOME, state="suspended"),
        SimpleNamespace(kind=kind, owner_ref=owner.id, planet=HOME, state="resolved"),
    ])
    if register_vector:
        system.register_vector(vector)
    # A consent is a decision, so it is granted through the system that owns the
    # write rather than seeded — and the silent ally deliberately grants none.
    system.grant_consent(ally, CONSENT_SUPPORT, owner)

    return SimpleNamespace(
        registry=registry, bus=bus, clock=clock, system=system, tech=tech,
        vector=vector, agents=agents, alliance=alliance,
        owner=owner, victim=victim, ally=ally, stranger=stranger,
        silent_ally=silent_ally,
        lab=lab, works=works, dormant_works=dormant_works, neutral=neutral,
    )


# ------------------------------------------------------------------ #
#  Normalization: every answer reduced to JSON-safe plain data
# ------------------------------------------------------------------ #

def _normalize(value):
    """Return *value* as plain, order-stable, JSON-safe data.

    Objects are reduced to a label rather than a ``repr``, because a ``repr``
    carries an address and the snapshot has to be comparable across two
    processes.
    """
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        # A BranchRefusal IS a str (the message key) carrying its structured
        # payload on ``data``. The payload is half the answer — it quotes the
        # required lab, the doctrine names, the blocking buildings — so it goes
        # into the snapshot too, and a query that resolved any of it through the
        # wrong registry shows up as a mismatch.
        payload = getattr(value, "data", None)
        if isinstance(payload, dict):
            return {"key": str(value), "data": _normalize(payload)}
        return str(value)
    if isinstance(value, dict):
        return {str(key): _normalize(item) for key, item in sorted(
            value.items(), key=lambda pair: str(pair[0])
        )}
    if isinstance(value, (frozenset, set)):
        return sorted(str(item) for item in value)
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    return _label(value)


def _label(entity):
    """Return the stable name of a fixture object, or ``None``."""
    if entity is None:
        return None
    return str(getattr(entity, "key", entity))


def query_answers(world):
    """Return every READ-ONLY Branch_System query, answered over *world*.

    One entry per public query, keyed by the method name — so
    :meth:`TestQuerySurfaceIsFullyCovered.test_every_public_method_is_classified`
    can prove the table is exhaustive rather than hand-picked, and so a new query
    cannot be added without being brought under the R15.1 and R15.4 guards.

    Nothing here writes: the same world answers the table identically however many
    times it is asked, which is what makes the three-way singleton comparison and
    the cross-process comparison meaningful.
    """
    system = world.system
    owner = world.owner
    gate_requests = (
        ("works_of_another_branch", WORKS_ABBR["defense"]),
        ("lab_of_another_branch", LAB_ABBR["defense"]),
    )
    return {
        # --- identity ------------------------------------------------ #
        "branch_of_building": {abbr: system.branch_of_building(abbr) for abbr in ALL_ABBRS},
        "branch_of_technology": {
            key: system.branch_of_technology(key) for key in ALL_TECH_KEYS
        },
        "lab_for_branch": {b: system.lab_for_branch(b) for b in BRANCHES},
        "branch_buildings": {b: system.branch_buildings(b) for b in BRANCHES},
        "role_for_branch": {b: system.role_for_branch(b) for b in BRANCHES},
        "branch_overview": _normalize(system.branch_overview()),
        # --- commitment, estate, dormancy ---------------------------- #
        "commitment": [system.commitment(owner), system.commitment(owner, AWAY)],
        "has_commitment": {b: system.has_commitment(owner, b) for b in BRANCHES},
        "estate": {b: _normalize(system.estate(owner, b)) for b in BRANCHES},
        "estate_count": {b: system.estate_count(owner, b) for b in BRANCHES},
        "conflicting_estates": {
            b: _normalize(system.conflicting_estates(owner, HOME, b)) for b in BRANCHES
        },
        "is_operational": [
            system.is_operational(world.lab),
            system.is_operational(world.works),
            system.is_operational(world.dormant_works),
            system.is_operational(world.neutral),
        ],
        "applied_technologies": _normalize(system.applied_technologies(owner)),
        "dormant_branches": _normalize(system.dormant_branches(owner)),
        "reinstatement_pending": {
            key: system.reinstatement_pending(owner, key) for key in ALL_TECH_KEYS
        },
        # --- construction gates -------------------------------------- #
        "construction_validators": {
            name: [_normalize(gate(owner, abbr, None)) for gate in system.construction_validators()]
            for name, abbr in gate_requests
        },
        # --- carriers, resources, targeting -------------------------- #
        "eligible_carrier": {
            role: _label(system.eligible_carrier(owner, role))
            for role in sorted(BRANCH_ROLE.values())
        },
        "resource_shortfall": _normalize(
            system.resource_shortfall(owner, {"Iron": 500, "Circuits": 1})
        ),
        "may_target": {
            "shielded": _normalize(system.may_target(owner, world.victim)),
            "stranger": _normalize(system.may_target(owner, world.stranger)),
            "allied": _normalize(system.may_target(owner, world.ally)),
            "own_building": _normalize(system.may_target(owner, world.lab)),
            "self": _normalize(system.may_target(owner, owner)),
            "supported_ally": _normalize(system.may_target(owner, world.ally, hostile=False)),
            "silent_ally": _normalize(
                system.may_target(owner, world.silent_ally, hostile=False)
            ),
        },
        "has_consent": {
            "granted": system.has_consent(world.ally, CONSENT_SUPPORT, owner),
            "other_kind": system.has_consent(world.ally, CONSENT_TARGET_SHARING, owner),
            "never_granted": system.has_consent(world.silent_ally, CONSENT_SUPPORT, owner),
        },
        # --- the vector registry, by value ---------------------------- #
        # The KINDS alone: the registered vector objects are collaborators, not
        # values, and the by-value copy this answers with is the whole claim —
        # reading it must expose nothing a caller could re-wire through.
        "registered_vectors": sorted(system.registered_vectors()),
        # --- the three limit ledgers --------------------------------- #
        "cooldown_remaining": {
            kind: system.cooldown_remaining(world.works, kind) for kind in OPERATION_KINDS
        },
        "in_flight_cap": {kind: system.in_flight_cap(kind) for kind in OPERATION_KINDS},
        "in_flight_count": {
            kind: system.in_flight_count(owner, kind) for kind in OPERATION_KINDS
        },
        "escalation_remaining": [
            system.escalation_remaining(owner, world.victim),
            system.escalation_remaining(owner, world.stranger),
        ],
        # --- Counter_Web and the response window --------------------- #
        "counter_multiplier": {
            f"{actor}->{target}": system.counter_multiplier(actor, target)
            for actor in BRANCHES for target in BRANCHES
        },
        "response_window": [
            system.response_window(1),
            system.response_window(40),
            system.response_window(40, 10),
        ],
    }


#: The public methods that CHANGE something, so they are deliberately outside the
#: read-only query table above. Listed by name rather than derived, because "does
#: this method write" is a design fact and a new writer must be classified by
#: hand.
NON_QUERY_METHODS = frozenset({
    "charge",
    "grant_consent",
    "note_cooldown",
    "note_escalation",
    "on_alliance_member_left",
    "on_building_demolished",
    "on_building_destroyed",
    "on_construction_completed",
    "on_player_moved",
    "on_reinstatement_completed",
    "process_tick",
    "refund",
    "register_vector",
    "revoke_alliance_consents",
    "revoke_consent",
})


def public_method_names():
    """Return every public method ``BranchSystem`` itself declares."""
    return frozenset(
        name for name, value in vars(BranchSystem).items()
        if not name.startswith("_") and callable(value)
    )


def json_snapshot(world=None):
    """Return the query table of *world* as a JSON string.

    Both sides of the cross-process comparison pass through this one serializer,
    so a tuple-versus-list difference can never masquerade as a disagreement
    about an answer.
    """
    return json.dumps(query_answers(world or branch_world()), sort_keys=True)


# ------------------------------------------------------------------ #
#  R15.1 — the module imports and answers with `evennia` absent
# ------------------------------------------------------------------ #

#: Run in a CHILD interpreter, because ``sys.modules`` has to be genuinely clean:
#: ``conftest`` installs the Evennia stubs for the whole test session, and every
#: other Branch test module installs them again at import, so the only honest
#: place to assert the framework is absent is a process that never had it.
_CHILD_PROGRAM = """\
import sys

sys.path[:0] = [%(repo)r, %(mygame)r]
if "evennia" in sys.modules:
    raise SystemExit("evennia was already present before the guard imported anything")

import importlib

module = importlib.import_module("world.systems.tests.test_branch_architecture")
snapshot = module.json_snapshot()
leaked = sorted(name for name in sys.modules if name.split(".")[0] == "evennia")
if leaked:
    raise SystemExit("importing the Branch system pulled in: " + ", ".join(leaked))
sys.stdout.write(snapshot)
"""


class TestBranchSystemNeedsNoFramework(unittest.TestCase):
    """``branch_system`` imports, and answers, with ``evennia`` absent (R15.1)."""

    def test_imports_and_answers_every_query_without_evennia(self):
        program = _CHILD_PROGRAM % {"repo": REPO_ROOT, "mygame": MYGAME_DIR}
        result = subprocess.run(
            [sys.executable, "-c", program],
            capture_output=True, text=True, cwd=REPO_ROOT, timeout=300,
        )
        self.assertEqual(
            result.returncode, 0,
            "the Branch system could not be imported and queried with evennia "
            "absent from sys.modules (R15.1) — a module-scope framework import "
            f"is the usual cause:\n{result.stdout}\n{result.stderr}",
        )
        self.assertEqual(
            json.loads(result.stdout), json.loads(json_snapshot()),
            "the Branch queries answered differently with the framework absent "
            "than with the Evennia stubs installed — an answer must never depend "
            "on the framework being importable (R15.1)",
        )


def _import_time_statements(node):
    """Yield every node under *node* that RUNS when the module is imported.

    Descends into module-level ``if`` / ``try`` / ``with`` bodies and into except
    handlers, because an import nested in one of those executes exactly like a
    top-level one, and skips function and class bodies, where this module's
    deliberate function-local imports of ``world.utils`` live. A ``TYPE_CHECKING``
    guard is skipped too: its body never executes, so an import inside it cannot
    make the module need the framework at run time.
    """
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(child, ast.If) and _is_type_checking_guard(child.test):
            continue
        yield child
        yield from _import_time_statements(child)


def _is_type_checking_guard(test):
    """Return True when *test* is the ``TYPE_CHECKING`` flag."""
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


def _imported_modules(node):
    """Yield the module names one import statement names."""
    if isinstance(node, ast.Import):
        for alias in node.names:
            yield alias.name
    elif isinstance(node, ast.ImportFrom) and node.module:
        yield node.module


class TestNoFrameworkImportInTheSource(unittest.TestCase):
    """The AST backs up the subprocess: no import-time framework import (R15.1)."""

    def test_branch_system_ast_holds_no_top_level_evennia_import(self):
        with open(BRANCH_SYSTEM_PATH, encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=BRANCH_SYSTEM_PATH)
        offenders = [
            f"line {node.lineno}: imports '{module}'"
            for node in _import_time_statements(tree)
            for module in _imported_modules(node)
            if module == "evennia" or module.startswith("evennia.")
        ]
        self.assertEqual(
            offenders, [],
            "branch_system.py imports the game framework at import time, so the "
            "module can no longer load without it (R15.1). Move the import into "
            "the function that needs it, as _write_player_attr does:\n  "
            + "\n  ".join(offenders),
        )

    def test_the_scan_would_catch_a_planted_import(self):
        """The scanner itself is exercised, so a green guard cannot be a blind one."""
        source = (
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    import evennia.objects.objects\n"
            "def later():\n"
            "    import evennia\n"
            "try:\n"
            "    import evennia.utils.logger\n"
            "except ImportError:\n"
            "    from evennia.utils import fallback\n"
        )
        found = [
            module
            for node in _import_time_statements(ast.parse(source))
            for module in _imported_modules(node)
        ]
        self.assertIn("evennia.utils.logger", found, "an import inside a module-level "
                      "try block executes at import time and must be reported")
        self.assertIn("evennia.utils", found, "an import inside an except handler "
                      "executes at import time and must be reported")
        self.assertNotIn("evennia", found, "a function-local import must not be reported")
        self.assertNotIn("evennia.objects.objects", found,
                         "a TYPE_CHECKING import never executes and must not be reported")


# ------------------------------------------------------------------ #
#  R15.4 — every query answers with no process-wide DataRegistry
# ------------------------------------------------------------------ #

class TestQuerySurfaceIsFullyCovered(unittest.TestCase):
    """The query table above is the whole read-only surface, not a selection."""

    def test_every_public_method_is_classified(self):
        classified = frozenset(query_answers(branch_world())) | NON_QUERY_METHODS
        surface = public_method_names()
        self.assertEqual(
            sorted(surface - classified), [],
            "a public BranchSystem method is neither in the read-only query "
            "table nor named as a writer, so it is covered by none of the "
            "architectural guards — add it to query_answers() (a query) or to "
            "NON_QUERY_METHODS (a writer)",
        )
        self.assertEqual(
            sorted(classified - surface), [],
            "the guard tables name a BranchSystem method that no longer exists",
        )


class TestQueriesNeedNoGlobalRegistry(unittest.TestCase):
    """Every query resolves through the INJECTED registry (R15.4).

    Three singleton states, one expected answer. The conflicting-singleton state
    is the load-bearing one: with the singleton merely cleared, a query that read
    ``DataRegistry.get_instance()`` and fell back to the injected registry would
    still pass.
    """

    def setUp(self):
        self._original = DataRegistry.get_instance()
        self.addCleanup(DataRegistry.set_instance, self._original)

    def test_answers_are_identical_in_every_singleton_state(self):
        world = branch_world()
        rival = rival_registry()
        DataRegistry.set_instance(None)
        expected = query_answers(world)
        for label, singleton in (
            ("cleared", None), ("conflicting", rival), ("injected", world.registry),
        ):
            with self.subTest(singleton=label):
                DataRegistry.set_instance(singleton)
                self.assertEqual(
                    DataRegistry.get_instance(), singleton,
                    "the singleton could not be installed for this state",
                )
                self.assertEqual(
                    query_answers(world), expected,
                    f"a Branch query answered differently with a {label} "
                    "process-wide DataRegistry — every definition lookup must "
                    "go through the injected registry (R15.4)",
                )

    def test_the_rival_catalog_really_would_change_the_answers(self):
        """The conflicting singleton is only a guard if it disagrees."""
        injected = query_answers(branch_world())
        rival = branch_world()
        rival.system.registry = rival_registry()
        self.assertNotEqual(
            query_answers(rival)["lab_for_branch"], injected["lab_for_branch"],
            "the rival catalog answers the same as the fixture one, so "
            "installing it as the singleton proves nothing — give it a "
            "Branch-to-lab map that cannot coincide",
        )


# ------------------------------------------------------------------ #
#  R15.5 — one writer for each persisted attribute this feature adds
# ------------------------------------------------------------------ #

#: attribute name -> the constant that names it, and where it lives. The task
#: text names the first two; the same single-writer rule covers all five, so the
#: scan covers all five.
SINGLE_WRITER_ATTRS = {
    ATTR_BRANCH_ABANDONED: ("ATTR_BRANCH_ABANDONED", "the owning player"),
    ATTR_BRANCH_REINSTATEMENT: ("ATTR_BRANCH_REINSTATEMENT", "the owning player"),
    ATTR_VECTOR_CONSENT: ("ATTR_VECTOR_CONSENT", "the consenting player"),
    ATTR_VECTOR_COOLDOWNS: ("ATTR_VECTOR_COOLDOWNS", "the ORIGINATING BUILDING"),
    ATTR_VECTOR_ESCALATION: ("ATTR_VECTOR_ESCALATION", "the attacking player"),
}

#: The helpers that persist an attribute by NAME: ``BranchSystem``'s own funnel,
#: the shared ``world.utils`` writer it delegates to, and the Evennia attribute
#: handler's own ``add``.
_WRITER_CALLS = frozenset({"_write_player_attr", "set_obj_attr", "add", "attr_add"})


def _write_targets(tree):
    """Yield ``(attribute_name_or_constant, lineno)`` for every write in *tree*.

    Two shapes, which is every way this codebase persists an attribute:

    * ``anything.branch_abandoned = value`` — an assignment to an attribute of
      that name, which covers the ``db`` proxy and a direct field write;
    * ``set_obj_attr(obj, ATTR_BRANCH_ABANDONED, value)`` /
      ``obj.attributes.add("branch_abandoned", value)`` — a writer helper naming
      the attribute in a string literal or through its constant.

    A *read* is deliberately not a write: every system may read this state, and
    several do.
    """
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Attribute):
                    yield target.attr, node.lineno
        elif isinstance(node, ast.Call):
            name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            if name not in _WRITER_CALLS:
                continue
            for argument in node.args:
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                    yield argument.value, node.lineno
                elif isinstance(argument, ast.Name):
                    yield argument.id, node.lineno


def _shipped_modules():
    """Yield every non-test Python module under ``mygame``."""
    for root, dirs, files in os.walk(MYGAME_DIR):
        dirs[:] = [name for name in dirs if name not in ("tests", "__pycache__")]
        for name in sorted(files):
            if name.endswith(".py") and not name.startswith("test_"):
                yield os.path.join(root, name)


class TestBranchStateHasOneWriter(unittest.TestCase):
    """``branch_system.py`` is the only module that writes Branch player state (R15.5)."""

    @classmethod
    def setUpClass(cls):
        cls.writes = {}
        for path in _shipped_modules():
            with open(path, encoding="utf-8") as handle:
                tree = ast.parse(handle.read(), filename=path)
            for name, lineno in _write_targets(tree):
                cls.writes.setdefault(name, []).append((path, lineno))

    def test_no_other_module_writes_the_new_attributes(self):
        for attribute, (constant, holder) in sorted(SINGLE_WRITER_ATTRS.items()):
            with self.subTest(attribute=attribute):
                offenders = sorted(
                    f"{os.path.relpath(path, MYGAME_DIR)}:{lineno}"
                    for name in (attribute, constant)
                    for path, lineno in self.writes.get(name, ())
                    if os.path.normcase(path) != os.path.normcase(BRANCH_SYSTEM_PATH)
                )
                self.assertEqual(
                    offenders, [],
                    f"'{attribute}' (on {holder}) is written outside "
                    "branch_system.py, which is its declared single writer "
                    "(R15.5). tech_system, for one, deliberately calls "
                    "BranchSystem.on_reinstatement_completed rather than "
                    "assigning the attribute:\n  " + "\n  ".join(offenders),
                )

    def test_the_scan_would_catch_a_planted_write(self):
        """The scanner itself is exercised, so a green guard cannot be a blind one."""
        source = (
            'player.db.branch_abandoned = {"weapons": True}\n'
            'player.attributes.add("vector_consent", {})\n'
            "set_obj_attr(building, ATTR_VECTOR_COOLDOWNS, ledger)\n"
            "recorded = player.db.branch_reinstatement\n"
            'count = len(player.attributes.get("vector_escalation") or {})\n'
        )
        found = {name for name, _lineno in _write_targets(ast.parse(source))}
        for planted in ("branch_abandoned", "vector_consent", "ATTR_VECTOR_COOLDOWNS"):
            self.assertIn(planted, found, f"the scan missed a write of '{planted}'")
        self.assertNotIn(
            "branch_reinstatement", found,
            "reading an attribute is not writing it — every system may read this "
            "state, and several do",
        )
        self.assertNotIn("vector_escalation", found, "an attributes.get() is a read")

    def test_branch_system_does_write_every_one_of_them(self):
        """The scan is only a guard while it can still see the legitimate writes."""
        for attribute, (constant, _holder) in sorted(SINGLE_WRITER_ATTRS.items()):
            with self.subTest(attribute=attribute):
                sites = [
                    path for name in (attribute, constant)
                    for path, _lineno in self.writes.get(name, ())
                    if os.path.normcase(path) == os.path.normcase(BRANCH_SYSTEM_PATH)
                ]
                self.assertTrue(
                    sites,
                    f"the scan found no write of '{attribute}' in "
                    "branch_system.py at all — either the single writer stopped "
                    "writing it, or the scanner no longer recognizes the write "
                    "shape and every other module is now unguarded",
                )


# ------------------------------------------------------------------ #
#  R15.7 — every new balance field is hot after construction
# ------------------------------------------------------------------ #

def _parity_errors(world):
    """Return how many investment-parity errors the catalog reports.

    The consumer of ``branch_cost_parity_tolerance`` is the schema validator's
    parity rule rather than a system service, so this is the reachable path the
    knob changes.
    """
    errors = SchemaValidator().cross_validate(world.registry)
    return sum(1 for error in errors if "branch_cost_parity_tolerance" in error)


def _cooldown_probe(kind):
    return lambda world: world.system.cooldown_remaining(world.works, kind)


def _cap_probe(kind):
    return lambda world: world.system.in_flight_cap(kind)


#: field -> (value A, value B, probe). Every probe is a PUBLIC call, and the two
#: values are chosen so the answer must differ if — and only if — the field is
#: read on the call rather than cached at construction.
HOT_FIELDS = {
    "branch_reinstatement_cost_fraction": (
        0.25, 0.75,
        lambda world: world.tech.report_technology_view(world.owner)["reinstatement_fraction"],
    ),
    "minimum_response_window_ticks": (5, 40, lambda world: world.system.response_window(1)),
    "counter_advantage_cap": (
        1.1, 1.5,
        lambda world: world.system.counter_multiplier("weapons", "defense"),
    ),
    "branch_cost_parity_tolerance": (0.01, 0.9, _parity_errors),
    "new_player_vector_shield_level": (
        10, 20,
        lambda world: _normalize(world.system.may_target(world.owner, world.stranger)),
    ),
    "escalation_window_ticks": (
        600, 100,
        lambda world: world.system.escalation_remaining(world.owner, world.victim),
    ),
    "escalation_cap": (
        3, 9,
        lambda world: world.system.escalation_remaining(world.owner, world.victim),
    ),
}
HOT_FIELDS.update({
    f"{kind}_cooldown_ticks": (5, 60, _cooldown_probe(kind)) for kind in OPERATION_KINDS
})
HOT_FIELDS.update({
    f"{kind}_max_in_flight": (1, 7, _cap_probe(kind)) for kind in OPERATION_KINDS
})

#: The fields no **Branch_System** answer depends on: the per-Operation_Kind
#: resource cost and Carrier_Agent XP award. Each is asserted to be exactly that
#: — present, bound by name in the Operation_Kind registry, and inert against
#: every query in :func:`query_answers` — so the gap is documented here instead
#: of being silently skipped.
#:
#: ``agent_xp_<kind>`` still has no consumer anywhere; the six vector specs bring
#: it. ``<kind>_cost`` now has one, but it is the **Operation driver** rather than
#: this module's subject: ``OperationDriver._resource_cost`` reads the bound field
#: on every request and the acceptance half charges it (R12.1, R12.2). It stays
#: here rather than moving into :data:`HOT_FIELDS` because a probe for it has to
#: be a public driver call, and ``request`` only reaches the cost after eight
#: earlier checks pass — which no kind can do over this fixture: the owner is
#: committed to one Branch, and that Branch's unlock technology is deliberately
#: pending reinstatement. The R15.7 guard for the cost binding therefore lives
#: where the consumer does, over a real ``request``, in
#: ``test_operation_contract.TestResourcesCheck``:
#: ``test_the_cost_is_read_from_the_bound_balance_field_on_every_request`` and
#: ``test_the_convention_names_the_field_when_the_binding_is_absent``.
PENDING_CONSUMER_FIELDS = {
    **{f"{kind}_cost": ("cost_field", dict) for kind in OPERATION_KINDS},
    **{f"agent_xp_{kind}": ("agent_xp_field", int) for kind in OPERATION_KINDS},
}

#: Every Balance_Config field this feature introduces: the seven cross-cutting
#: knobs plus four per Operation_Kind.
NEW_BALANCE_FIELDS = frozenset(HOT_FIELDS) | frozenset(PENDING_CONSUMER_FIELDS)


class TestNewBalanceFieldsAreAccountedFor(unittest.TestCase):
    """The field tables match ``BalanceConfig``, so neither can drift alone."""

    def test_every_named_field_is_a_real_balance_field(self):
        declared = {field.name for field in dataclass_fields(BalanceConfig)}
        self.assertEqual(
            sorted(NEW_BALANCE_FIELDS - declared), [],
            "a field the Branch guards tune is no longer declared on "
            "BalanceConfig — a rename here silently un-tunes the game",
        )

    def test_the_count_is_the_documented_thirty_one(self):
        expected = 7 + 4 * len(OPERATION_KINDS)
        self.assertEqual(
            len(NEW_BALANCE_FIELDS), expected,
            "the feature introduces seven cross-cutting fields plus four per "
            f"Operation_Kind ({expected} in all); the guard tables cover "
            f"{len(NEW_BALANCE_FIELDS)}",
        )


class TestBalanceKnobsStayHot(unittest.TestCase):
    """Retuning a knob after construction changes the next call (R15.7)."""

    def test_each_consumed_field_is_read_on_the_call(self):
        for field, (low, high, probe) in sorted(HOT_FIELDS.items()):
            with self.subTest(field=field):
                answers = []
                for value in (low, high):
                    world = branch_world()
                    # AFTER construction, exactly as an @reload retunes a live
                    # game: the system must read the new value on the next call.
                    setattr(world.registry.balance, field, value)
                    answers.append(probe(world))
                self.assertNotEqual(
                    answers[0], answers[1],
                    f"'{field}' retuned from {low!r} to {high!r} changed nothing "
                    f"(both calls answered {answers[0]!r}) — the value must be "
                    "read from the injected registry on every call, never cached "
                    "at construction (R15.7)",
                )

    def test_a_retune_between_two_calls_on_ONE_system_is_seen(self):
        """The same instance, retuned mid-life — no rebuild, no reconstruction."""
        world = branch_world()
        world.registry.balance.minimum_response_window_ticks = 5
        self.assertEqual(world.system.response_window(1), 5)
        world.registry.balance.minimum_response_window_ticks = 42
        self.assertEqual(
            world.system.response_window(1), 42,
            "the response-window floor was cached: one construction must serve "
            "every retune (R15.7)",
        )

    def test_the_fields_no_branch_query_reads_are_bound_and_inert(self):
        """Each is named by its Operation_Kind and changes no Branch answer.

        See :data:`PENDING_CONSUMER_FIELDS` for which of the two families has
        grown a consumer since, and where that consumer's hot-reload guard lives.
        """
        for field, (binding, kind_type) in sorted(PENDING_CONSUMER_FIELDS.items()):
            with self.subTest(field=field):
                world = branch_world()
                bindings = {
                    getattr(kind_def, binding)
                    for kind_def in world.registry.operation_kinds.values()
                }
                self.assertIn(
                    field, bindings,
                    f"'{field}' is named by no Operation_Kind's {binding}, so no "
                    "vector can ever find it — the naming contract is the only "
                    "thing holding this field to its kind until a consumer lands",
                )
                self.assertIsInstance(
                    getattr(world.registry.balance, field), kind_type,
                    f"'{field}' no longer holds a {kind_type.__name__}",
                )
                before = query_answers(world)
                setattr(
                    world.registry.balance, field,
                    {"Iron": 999} if kind_type is dict else 999,
                )
                self.assertEqual(
                    query_answers(world), before,
                    f"'{field}' now changes a Branch_System answer, so its "
                    "consumer is reachable from this fixture — move it into "
                    "HOT_FIELDS with the probe that reads it, so the hot-reload "
                    "guard covers it",
                )


# ------------------------------------------------------------------ #
#  R12.8 — a Branch_Estate charges no recurring upkeep
# ------------------------------------------------------------------ #

class TestBranchEstateChargesNoUpkeep(unittest.TestCase):
    """A hundred idle ticks over a standing estate cost nothing (R12.8)."""

    TICKS = 100

    def _run_ticks(self, world):
        for tick in range(NOW, NOW + self.TICKS):
            world.clock.tick = tick
            world.system.process_tick(tick)

    def test_one_hundred_ticks_move_no_resource(self):
        # No Vector_System registered: the state this feature ships in, and the
        # literal "no operations" of the requirement.
        world = branch_world(register_vector=False)
        estate = world.system.estate_count(world.owner, "weapons")
        self.assertGreater(
            estate, 1, "the guard is meaningless without a standing Branch_Estate"
        )
        before = world.owner.resource_snapshot()
        self._run_ticks(world)
        self.assertEqual(
            world.owner.resource_snapshot(), before,
            "owning a Branch_Estate charged the owner over 100 idle ticks — a "
            "Branch_Building costs its owner nothing beyond the existing repair "
            "cost (R12.8)",
        )
        self.assertEqual(
            world.system.estate_count(world.owner, "weapons"), estate,
            "the estate itself changed over a hundred ticks in which nothing "
            "happened to it",
        )
        self.assertEqual(
            world.vector.advances, [],
            "an unregistered vector was advanced anyway",
        )

    def test_the_ticks_really_reach_a_registered_vector(self):
        """Otherwise a tick step that silently does nothing would pass the guard."""
        world = branch_world()
        before = world.owner.resource_snapshot()
        self._run_ticks(world)
        self.assertEqual(
            world.vector.advances, list(range(NOW, NOW + self.TICKS)),
            "the fan-out did not advance the registered vector once per tick, so "
            "the no-upkeep guard above ran over a dead tick step",
        )
        self.assertEqual(
            world.owner.resource_snapshot(), before,
            "a hundred advances of a vector running no operation charged the owner",
        )


if __name__ == "__main__":
    unittest.main()
