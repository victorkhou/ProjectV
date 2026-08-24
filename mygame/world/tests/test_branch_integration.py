"""Behavioural composition-root smoke for the Branch feature (R13.7, R15.9).

Three cheap guards already exist around this wiring, and this module deliberately
does **not** repeat them:

* ``test_composition_root_wiring.py`` reads ``game_init.py``'s SOURCE and asserts
  the injection lines are still written. It proves the calls EXIST; it cannot
  prove they fit together.
* ``test_tick_new_steps.py::TestVectorOperationsStep`` drives the tick step with
  a *fake* BranchSystem. It proves the step is emitted and ordered; it never
  touches the real system.
* ``test_directive_reachability.py`` walks the onboarding chain's XP. It proves
  no step strands a new player; it says nothing about the Branch step.

What is left — and what this module owns — is whether the pieces actually meet:
the REAL ``BranchSystem`` wired to the REAL ``BuildingSystem`` /
``TechLabSystem`` / ``AgentSystem`` over the REAL definition files, in the order
and at the positions the design documents. Every assertion here fails on a
signature drift, a chain-position drift, a renamed ``game_systems`` key, or a
directive step that wanders above the Branch_Lab gate — none of which a source
tripwire or a fake can see.

The graph is built the way ``game_init.initialize_game`` builds its Branch slice
(same construction order, same four setters), which is as close to the live root
as a test can get without booting Django/Evennia.

The second half of the module (``TestRestartRoundTrip``) is the same question
asked of the *operation* half of the feature: a conforming Signature_Vector
places operations against a fake world through the real ``BranchSystem``, the
systems are thrown away, a second graph is wired over the same world, and the
composition root's per-vector rebuild fan-out has to hand every non-terminal
operation back to the tick loop with the clock it was persisted with (R8.22,
R14.2, R14.3). The per-record unit and property tests for ``rebuild`` live in
``world/systems/tests``; what only this module can show is the whole path —
``request`` through the real nine-check chain, a real persistence write, a
restart, and the real ``vector_operations`` tick step — meeting end to end.
"""

from __future__ import annotations

import itertools
import pathlib
import re
import unittest

import yaml

from typeclasses.scripts import GameTickScript
from world import progression
from world.constants import (
    ATTR_VECTOR_OPERATIONS,
    BRANCH_OPERATION_KIND,
    RESEARCH_LAB,
    RESEARCH_TREE_WEAPONS,
)
from world.data_registry import DataRegistry
from world.event_bus import ALL_EVENTS, EventBus
from world.systems.agent_system import AgentSystem
from world.systems.alliance_system import AllianceSystem
from world.systems.base_system import BaseSystem
from world.systems.branch_system import BranchSystem
from world.systems.building_system import BuildingSystem
from world.systems.combat_engine import CombatEngine
from world.systems.operation_contract import (
    OperationDriver,
    OperationRecord,
    OperationState,
)
from world.systems.tech_system import TechLabSystem

_MYGAME = pathlib.Path(__file__).resolve().parents[2]
_DATA_DIR = str(_MYGAME / "data")
_GAME_INIT = _MYGAME / "server" / "conf" / "game_init.py"

#: ``BuildingSystem._validate_construction``'s chain, in the order the design's
#: "Construction gates and their position" section prints it: the three Branch
#: gates sit immediately after the lab gate they extend, before the rank gate,
#: and — the load-bearing part (R4.8, R13.4) — above ``resources``, so whatever
#: a gate reports precedes any charge. Names are the ``_validate_`` suffixes.
DOCUMENTED_CHAIN = (
    "hq_requirement",
    "one_hq_per_planet",
    "shield_generator_cap",
    "one_research_lab_per_planet",
    "branch_affiliation",             # NEW (R3.3, R3.4, R3.5)
    "branch_switch",                  # NEW (R4.1, R4.2, R4.8, R13.4)
    "unlock_technology",              # NEW (R6.2, R6.3)
    "rank_requirement",
    "deed_requirement",
    "terrain",
    "buildable",
    "extractor_terrain",
    "tile_empty",
    "build_range",
    "combat_lockout",
    "resources",
)

#: The three gates the Branch feature splices in, and where they must land.
BRANCH_GATES = ("branch_affiliation", "branch_switch", "unlock_technology")

#: The directive step introducing the Branch commitment decision (R13.7).
COMMIT_STEP_KEY = "commit_branch"

#: A Neutral_Building used to walk the chain: no ``branch``, no
#: ``unlock_technology``, so all three Branch gates must pass it through.
NEUTRAL_ABBR = "WL"


# ------------------------------------------------------------------ #
#  Fixtures
# ------------------------------------------------------------------ #

def _registry() -> DataRegistry:
    """The real definition files, loaded and validated."""
    registry = DataRegistry()
    registry.load_all(_DATA_DIR)
    return registry


def _raw_buildings() -> list[dict]:
    path = pathlib.Path(_DATA_DIR) / "definitions" / "buildings.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _game_init_source() -> str:
    """game_init.py with comment text stripped, so a commented line can't pass."""
    source = _GAME_INIT.read_text(encoding="utf-8")
    return "\n".join(re.sub(r"#.*$", "", line) for line in source.splitlines())


def _registered_branch_key() -> str | None:
    """The ``game_systems`` key the composition root binds BranchSystem to.

    Read from the root rather than hardcoded, so this module tests the *contract*
    between the producer (game_init) and the consumer
    (``_build_tick_steps``'s ``systems.get(...)``) instead of asserting the same
    literal twice on both sides of it.
    """
    match = re.search(
        r"[\"'](\w+)[\"']\s*:\s*branch_system\s*,", _game_init_source()
    )
    return match.group(1) if match else None


class _Graph:
    """The Branch slice of the composition root, built with the real systems.

    Mirrors ``game_init.initialize_game``: the collaborators first, then
    ``BranchSystem`` over them, then the four setters that are the whole of the
    coupling between the Branch feature and the systems it extends.
    """

    def __init__(
        self, registry: DataRegistry, agent_repository: object | None = None,
    ) -> None:
        self.registry = registry
        self.event_bus = EventBus()
        self.tick = 0
        clock = lambda: self.tick  # noqa: E731 - the shared tick clock
        self.building_system = BuildingSystem(
            registry, self.event_bus, current_tick_func=clock,
        )
        self.combat_engine = CombatEngine(
            registry, self.event_bus, current_tick_func=clock,
        )
        self.tech_system = TechLabSystem(registry, self.event_bus)
        # ``agent_repository`` is the port game_init injects the Evennia adapter
        # into; left ``None`` the system falls back to that adapter exactly as
        # before, and a fixture with a roster hands one in instead of a DB.
        self.agent_system = AgentSystem(
            registry, self.event_bus, agent_repository=agent_repository,
        )
        self.alliance_system = AllianceSystem(
            registry, self.event_bus, alliance_registry=None, tick_provider=clock,
        )
        self.branch_system = BranchSystem(
            registry,
            self.event_bus,
            current_tick_func=clock,
            building_system=self.building_system,
            tech_system=self.tech_system,
            agent_system=self.agent_system,
            alliance_system=self.alliance_system,
            combat_engine=self.combat_engine,
        )
        self.gates = self.branch_system.construction_validators()
        self.building_system.set_branch_validators(self.gates)
        self.building_system.set_branch_estate_provider(self.branch_system)
        self.tech_system.set_branch_resolver(self.branch_system)
        self.agent_system.set_branch_resolver(self.branch_system)

    def systems(self, branch_key: str) -> dict:
        """The ``game_systems``-shaped dict the tick script is handed."""
        return {
            "registry": self.registry,
            "event_bus": self.event_bus,
            "building_system": self.building_system,
            "combat_engine": self.combat_engine,
            "tech_system": self.tech_system,
            "agent_system": self.agent_system,
            branch_key: self.branch_system,
        }


def _tick_script() -> GameTickScript:
    """A GameTickScript with its world lookups stubbed (no DB in this suite)."""
    script = GameTickScript()
    script._get_online_players = lambda: []
    script._get_all_buildings = lambda: []
    script._get_all_agents = lambda agent_system: []
    script._get_all_enemies = lambda agent_system: []
    return script


class _FakeVector:
    """A registered Vector_System: records the ticks it is advanced on."""

    operation_kind = "smoke_operation"

    def __init__(self) -> None:
        self.advanced: list[int] = []

    def advance_all(self, tick: int) -> None:
        self.advanced.append(tick)


class _Sentinel:
    """A stand-in player/tile. The chain walk patches every real validator, so
    nothing reads anything off it — it only has to be an object."""


class BranchGraphTestBase(unittest.TestCase):
    """One registry + one wired graph per class (the YAML load is the slow part)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = _registry()

    def setUp(self) -> None:
        self.graph = _Graph(self.registry)
        self.branch_system = self.graph.branch_system


# ------------------------------------------------------------------ #
#  branch_system is installed, and installed under the key the tick
#  dispatch looks up
# ------------------------------------------------------------------ #

class TestBranchSystemInstalled(BranchGraphTestBase):
    """The system the root builds is the system the tick dispatch finds."""

    def test_the_key_game_init_registers_is_the_key_the_tick_step_reads(self):
        """A renamed key silently stops every Vector_Operation from advancing.

        ``_build_tick_steps`` registers ``vector_operations`` only when
        ``systems.get("branch_system")`` resolves; the root spells that key out
        in its ``game_systems`` dict. Both sides are read here — the key comes
        from game_init, the lookup from the real tick script — so a rename on
        either side fails rather than quietly disabling the whole feature.
        """
        key = _registered_branch_key()
        self.assertIsNotNone(
            key,
            "game_init.py registers no game_systems entry bound to "
            "branch_system — the tick dispatch will never find it.",
        )
        steps = dict(
            _tick_script()._build_tick_steps(self.graph.systems(key), tick_number=3)
        )
        self.assertIn(
            "vector_operations", steps,
            f"the root registers BranchSystem under {key!r}, which "
            "_build_tick_steps does not look up.",
        )

    def test_the_four_root_setters_land_on_the_real_systems(self):
        """The injections hold the live BranchSystem, not a stale default.

        The source tripwire proves the four calls are written. This proves they
        took: each consumer holds *this* BranchSystem, so none of them is still
        running its pre-feature default.
        """
        bs = self.graph.building_system
        self.assertEqual(
            [gate.__func__.__name__ for gate in bs._branch_validators],
            ["_validate_" + name for name in BRANCH_GATES],
            "BuildingSystem's chain does not hold the three documented gates.",
        )
        for gate in bs._branch_validators:
            self.assertIs(gate.__self__, self.branch_system)
        self.assertIs(bs._branch_estate_provider, self.branch_system)
        self.assertIs(self.graph.tech_system._branch, self.branch_system)
        self.assertIs(self.graph.agent_system._branch, self.branch_system)


# ------------------------------------------------------------------ #
#  The three gates sit in the chain at the documented positions
# ------------------------------------------------------------------ #

class TestBranchGatesInTheValidationChain(BranchGraphTestBase):
    """Position is the requirement, not mere membership (R4.8, R13.4).

    The chain is first-failure-wins, so *where* a gate sits decides which error
    a player reads and — for the three Branch gates — whether the report about
    what they must tear down precedes the resource charge.
    """

    def _walk_the_chain(self) -> tuple[list[str], str | None]:
        """Run ``_validate_construction`` and return (order observed, error).

        Every one of ``BuildingSystem``'s own validators is replaced by a
        recorder that passes (the chain stops at the first failure, so a real
        HQ/terrain check would end the walk at step one). The three Branch gates
        are the REAL ones, only wrapped to record — so this also proves they
        accept the chain's call signature and pass a Neutral_Building through.
        """
        bs = self.graph.building_system
        observed: list[str] = []

        def recorder(name):
            def _record(*args, **kwargs):
                observed.append(name)
                return None
            return _record

        def spy(name, gate):
            def _spy(*args, **kwargs):
                observed.append(name)
                return gate(*args, **kwargs)
            return _spy

        for attr in dir(bs):
            if not attr.startswith("_validate_") or attr == "_validate_construction":
                continue
            setattr(bs, attr, recorder(attr[len("_validate_"):]))
        bs.set_branch_validators([
            spy(gate.__func__.__name__[len("_validate_"):], gate)
            for gate in self.graph.gates
        ])

        _bdef, err = bs._validate_construction(
            _Sentinel(), _Sentinel(), NEUTRAL_ABBR
        )
        return observed, err

    def test_chain_runs_in_the_documented_order(self):
        observed, err = self._walk_the_chain()
        self.assertIsNone(
            err,
            "a Branch gate refused a Neutral_Building — every building shipped "
            "before this feature must pass all three untouched (R2.5).",
        )
        self.assertEqual(
            observed, list(DOCUMENTED_CHAIN),
            "the construction chain no longer matches the order the design "
            "documents. If a validator was added, moved, or renamed, update "
            "DOCUMENTED_CHAIN and the design section together — the three "
            "Branch gates' positions are load-bearing.",
        )

    def test_gates_sit_after_the_lab_gate_and_before_the_rank_gate(self):
        observed, _err = self._walk_the_chain()
        for offset, name in enumerate(BRANCH_GATES):
            with self.subTest(gate=name):
                self.assertEqual(
                    observed.index(name),
                    observed.index("one_research_lab_per_planet") + 1 + offset,
                    f"{name} must sit immediately after the lab gate it "
                    "extends, in affiliation/switch/unlock order.",
                )
        self.assertLess(
            observed.index(BRANCH_GATES[-1]), observed.index("rank_requirement"),
            "a wrong-Branch attempt must read as a Branch error, not as a "
            "misleading rank error.",
        )

    def test_every_gate_reports_before_any_resource_is_charged(self):
        """R4.8 / R13.4: the report precedes the charge, structurally."""
        observed, _err = self._walk_the_chain()
        charge = observed.index("resources")
        for name in BRANCH_GATES:
            with self.subTest(gate=name):
                self.assertLess(observed.index(name), charge)


# ------------------------------------------------------------------ #
#  _build_tick_steps emits vector_operations, and it drives the real system
# ------------------------------------------------------------------ #

class TestVectorOperationsStepDrivesTheRealSystem(BranchGraphTestBase):
    """The tick surface of the whole Branch feature is this one step (R15.9)."""

    def _steps(self, tick_number: int) -> dict:
        key = _registered_branch_key() or "branch_system"
        return dict(
            _tick_script()._build_tick_steps(
                self.graph.systems(key), tick_number=tick_number
            )
        )

    def test_step_is_emitted_and_advances_every_registered_vector(self):
        first, second = _FakeVector(), _FakeVector()
        second.operation_kind = "other_smoke_operation"
        self.branch_system.register_vector(first)
        self.branch_system.register_vector(second)

        steps = self._steps(11)
        self.assertIn("vector_operations", steps)
        steps["vector_operations"]()

        self.assertEqual(first.advanced, [11])
        self.assertEqual(second.advanced, [11],
                         "the fan-out must reach every registered vector.")

    def test_step_is_a_no_op_in_the_shipped_state(self):
        """No vector is registered yet, so the step must run and do nothing."""
        steps = self._steps(1)
        self.assertIn("vector_operations", steps)
        steps["vector_operations"]()  # must not raise into the tick script

    def test_step_advances_after_combat_and_before_effects(self):
        key = _registered_branch_key() or "branch_system"
        names = [
            name for name, _step in _tick_script()._build_tick_steps(
                self.graph.systems(key), tick_number=1
            )
        ]
        self.assertLess(
            names.index("combat_resolution"), names.index("vector_operations")
        )
        self.assertLess(
            names.index("vector_operations"), names.index("effect_ticks")
        )


# ------------------------------------------------------------------ #
#  The Branch-commitment directive step sits at or after the lab gate
# ------------------------------------------------------------------ #

class TestBranchCommitmentDirective(unittest.TestCase):
    """R13.7: one directive step introduces the commitment decision, positioned
    at or after the Branch_Lab level and deed gate."""

    @classmethod
    def setUpClass(cls) -> None:
        registry = _registry()
        cls.chain = list(registry.directives)
        cls.keys = [step["key"] for step in cls.chain]
        cls.labs = [
            b for b in _raw_buildings()
            if "research_lab" in (b.get("capabilities") or [])
        ]

    def _step(self) -> dict:
        self.assertIn(
            COMMIT_STEP_KEY, self.keys,
            "no directive step introduces the Branch commitment decision — a "
            "player would meet the doctrine choice only by reading the code.",
        )
        return self.chain[self.keys.index(COMMIT_STEP_KEY)]

    def test_the_step_loads_and_triggers_on_an_event_the_bus_publishes(self):
        step = self._step()
        self.assertIn(
            step["trigger_event"], ALL_EVENTS,
            "the commitment step triggers on an event nothing publishes, so it "
            "would load clean and park the chain forever.",
        )

    def test_the_step_sits_at_or_after_the_lab_level_and_deed_gate(self):
        """The tail is the only position at or after that gate.

        Every Branch_Lab is gated on a level AND on a deed the onboarding chain
        never awards (it excludes the outpost kill on purpose). The generous
        upper bound below — every directive's XP plus the largest per-action
        award for every step — stays under the cheapest lab's level gate, so no
        earlier slot in the chain clears it and the step must come last.
        """
        progression.build_thresholds()
        gate_level = min(
            int(lab.get("rank_requirement", 1) or 1) for lab in self.labs
        )
        gate_xp = progression.xp_for_level(gate_level)
        best_action_xp = 40  # xp_agent_trained, the largest of the three
        upper_bound = best_action_xp * len(self.chain) + sum(
            int((step.get("reward") or {}).get("xp", 0) or 0) for step in self.chain
        )
        self.assertLess(
            upper_bound, gate_xp,
            f"the chain can now reach the level-{gate_level} lab gate "
            f"({gate_xp} XP); re-derive where the commitment step belongs.",
        )
        self.assertEqual(
            self.keys[-1], COMMIT_STEP_KEY,
            "the commitment step must be positioned at or after the lab level "
            "and deed gate; with that gate above everything the chain awards, "
            "the tail is the only position that satisfies it.",
        )
        for lab in self.labs:
            with self.subTest(lab=lab.get("abbreviation")):
                self.assertTrue(
                    lab.get("unlock_deed"),
                    "a lab lost its deed gate — the reasoning above (and the "
                    "step's position) assumes the labs sit behind one.",
                )

    def test_the_step_neither_strands_the_chain_nor_retunes_it(self):
        """It names no gated building and grants no XP, both deliberately.

        Naming a lab would make this the one step gated above the XP the player
        holds when it becomes current; granting XP would move the tuned
        level-6/7 onboarding target. Either regression is silent, so both are
        asserted here rather than left to the YAML comment.
        """
        step = self._step()
        self.assertIsNone(step.get("requires_building"))
        self.assertIsNone((step.get("condition") or {}).get("building_type"))
        self.assertEqual(int((step.get("reward") or {}).get("xp", 0) or 0), 0)


# ------------------------------------------------------------------ #
#  The restart round trip (R8.22, R14.2, R14.3)
# ------------------------------------------------------------------ #
#
# A restart replaces every system and empties every in-memory list. What
# survives is the world: the durable owners a vector nominated, and the
# Operation_Records persisted on them. So the fixture below is split along that
# line — ``_RestartWorld`` holds only objects and ``_RestartWorld.boot`` wires a
# fresh system graph over them, which makes "restart" literally "boot twice".
#
# Everything the operations are measured through is real: the nine-check
# validation chain over the real ``BranchSystem``, the real definition files
# (so the Branch_Commitment, the Active_HQ_Rule, the unlock gate, the carrier
# role, the cost, and the in-flight cap are the shipped ones), the driver's own
# persistence pair, the rebuild fan-out as ``game_init`` writes it, and the real
# ``vector_operations`` tick step. Only the world objects are fakes, because
# there is no Evennia database in this suite.

#: The planet the restart fixture lives on.
RESTART_PLANET = "earth"

#: The Operation_Kind and Branch the fixture vector owns, from the shipped
#: bindings rather than spelled again here.
RESTART_BRANCH = RESEARCH_TREE_WEAPONS
RESTART_KIND = BRANCH_OPERATION_KIND[RESTART_BRANCH]

#: Levels either side of ``new_player_vector_shield_level`` (10): the defender
#: has to be *above* it or the new-player shield (R10.4) refuses every request
#: and the fixture would never place anything.
RESTART_ATTACKER_LEVEL = 20
RESTART_DEFENDER_LEVEL = 20

#: Generous enough that two operations' costs never reach the ``resources``
#: check — this module is not measuring the charge.
RESTART_PURSE = {"Iron": 500, "Circuits": 500, "Energy": 500, "Stone": 500}

#: The two clocks the fixture places, deliberately different and both **above**
#: ``minimum_response_window_ticks`` (5), so a rebuilt clock can be told from
#: the floor and from its neighbour's.
RESTART_TICKS = (6, 9)

#: The Operation_Record fields R14.2 names, minus the four references — those
#: come back as live OBJECTS after a rebuild, not as the values that were
#: written, so they are asserted separately.
ROUND_TRIP_VALUES = (
    "kind",
    "planet",
    "target_x",
    "target_y",
    "ticks_remaining",
    "magnitude",
    "radius",
    "state",
    "charged",
)

#: Two coordinates no tile query in this fixture matches, for an object whose
#: own coordinates cannot be read.
_NOWHERE = (10 ** 9, 10 ** 9)

#: Fresh identities, so the rebuild's id-to-object index can never see a
#: collision between two fixture objects (its first claimant keeps the id).
_NEXT_ID = itertools.count(1)


def _weapons_slice(registry: DataRegistry) -> tuple[str, str, str, str]:
    """Return the shipped ``weapons`` slice: ``(lab, origin, unlock, role)``.

    Read off the loaded definitions rather than written out here, so the fixture
    follows a data rename instead of failing on one — this module measures
    persistence, not which two letters a lab is abbreviated to. That is not
    hypothetical: the Signals Lab already ships as ``SX`` rather than the
    design's ``SG``, because ``SG`` was taken.
    """
    lab = next(
        abbr for abbr, bdef in registry.buildings.items()
        if bdef.has_capability(RESEARCH_LAB)
        and getattr(bdef, "research_tree", None) == RESTART_BRANCH
    )
    origin_abbr, origin = next(
        (abbr, bdef) for abbr, bdef in registry.buildings.items()
        if getattr(bdef, "branch", None) == RESTART_BRANCH
        and not bdef.has_capability(RESEARCH_LAB)
    )
    role = getattr(registry.operation_kinds[RESTART_KIND], "carrier_role", None)
    return lab, origin_abbr, origin.unlock_technology, role


# ------------------------------------------------------------------ #
#  The fake world
# ------------------------------------------------------------------ #

class _AttrStore:
    """Evennia's attribute handler, reduced to the calls this fixture makes.

    ``get`` takes ``default`` by keyword because that is how
    ``world.utils.get_obj_attr`` and the driver's persistence pair both call it.
    """

    def __init__(self, data: dict | None = None) -> None:
        self._data: dict = dict(data or {})

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


class _DbProxy:
    """``obj.db`` over the **same** store ``obj.attributes`` reads.

    One store, deliberately: Evennia backs both handlers with a single set of
    attributes, and a fake with two bags lets a write through one of them vanish
    from the other — which is exactly how a persistence test can pass while the
    real thing loses every record.
    """

    def __init__(self, store: _AttrStore) -> None:
        object.__setattr__(self, "_store", store)

    def __getattr__(self, key):
        if key.startswith("_"):
            raise AttributeError(key)
        return object.__getattribute__(self, "_store").get(key)

    def __setattr__(self, key, value):
        object.__getattribute__(self, "_store").add(key, value)


class _WorldObject:
    """What every fixture object below shares: an identity and one attr store.

    No ``pk``: ``OperationDriver._is_deleted`` reads ``getattr(obj, "pk", True)``
    and calls ``None`` deleted, so an object that declares none is simply alive.
    """

    def __init__(self, key: str = "", **attrs) -> None:
        self.id = next(_NEXT_ID)
        self.key = key
        self.attributes = _AttrStore(attrs)
        self.db = _DbProxy(self.attributes)
        self.location = None

    def __repr__(self):  # pragma: no cover - diagnostics only
        return f"{type(self).__name__}({self.key!r}, id={self.id})"


class _FakePlayer(_WorldObject):
    """A CombatCharacter stand-in: a building roster, a level, and a purse.

    The four resource methods are the pair ``BranchSystem.charge`` /
    ``refund`` delegate to, and ``deduct_resources`` is whole-or-none for the
    same reason the real one is.
    """

    def __init__(self, key, planet, x=0, y=0, level=1, resources=None) -> None:
        super().__init__(
            key,
            coord_planet=planet, coord_x=x, coord_y=y, level=level,
            researched_techs=set(),
        )
        self.buildings: list = []
        self._resources: dict = dict(resources or {})

    def get_buildings(self):
        return list(self.buildings)

    def get_resource(self, resource):
        return int(self._resources.get(resource, 0))

    def add_resource(self, resource, amount):
        self._resources[resource] = self.get_resource(resource) + int(amount)

    def has_resources(self, costs):
        return all(self.get_resource(r) >= n for r, n in (costs or {}).items())

    def deduct_resources(self, costs):
        if not self.has_resources(costs):
            return False
        for resource, amount in (costs or {}).items():
            self._resources[resource] = self.get_resource(resource) - int(amount)
        return True


class _FakeBuilding(_WorldObject):
    """A Building stand-in, Operational and completed unless a test says otherwise."""

    def __init__(self, abbr, owner, room, x, y) -> None:
        super().__init__(
            abbr,
            building_type=abbr, owner=owner, building_level=1,
            hp=500, hp_max=500, offline=False, under_construction=False,
            coord_planet=room.planet_name, coord_x=x, coord_y=y,
        )
        self.location = room


class _FakeAgent(_WorldObject):
    """A Carrier_Agent: alive, on duty, and holding the role its kind requires."""

    def __init__(self, role, owner, room, x, y) -> None:
        super().__init__(
            f"{role}-1",
            role=role, owner=owner, hp=30, hp_max=30,
            reserve=False, incapacitated=False,
            coord_planet=room.planet_name, coord_x=x, coord_y=y,
        )
        self.location = room

    def is_alive(self):
        """The existing CombatEntity predicate both eligibility reads prefer."""
        return int(self.attributes.get("hp", default=0) or 0) > 0


def _tile(entity) -> tuple[int, int]:
    """Return *entity*'s tile, or a coordinate no query in this fixture matches."""
    store = getattr(entity, "attributes", None)
    if store is None:
        return _NOWHERE
    x, y = store.get("coord_x"), store.get("coord_y")
    if x is None or y is None:
        return _NOWHERE
    return (int(x), int(y))


class _FakePlanetRoom:
    """A PlanetRoom stand-in: the sweep surface plus the two audience queries.

    ``contents`` is what the rebuild's id-to-object index and a vector's
    ``discover_records`` sweep both walk — the same surface
    ``BombSystem.rebuild_from_world`` falls back to — and
    ``get_objects_in_area`` / ``get_players_at`` are the two coordinate queries
    the driver resolves a notification audience through.
    """

    def __init__(self, planet: str) -> None:
        self.planet_name = planet
        self.contents: list = []

    def get_objects_in_area(self, x1, y1, x2, y2):
        return [
            entity for entity in self.contents
            if x1 <= _tile(entity)[0] <= x2 and y1 <= _tile(entity)[1] <= y2
        ]

    def get_players_at(self, x, y):
        return [
            entity for entity in self.contents
            if isinstance(entity, _FakePlayer)
            and _tile(entity) == (int(x), int(y))
        ]


class _FakeAgentRoster:
    """The ``AgentRepository`` port, reduced to the three queries it is asked.

    Injected where ``game_init`` injects the Evennia adapter, so the carrier
    check reads a roster through the real ``AgentSystem`` rather than a stubbed
    method on it.
    """

    def __init__(self, agents=()) -> None:
        self.agents = list(agents)

    def find_agents_for_owner(self, owner):
        return [a for a in self.agents if a.attributes.get("owner") is owner]

    def find_all_agents(self):
        return list(self.agents)

    def find_all_enemies(self):
        return []


# ------------------------------------------------------------------ #
#  A conforming Signature_Vector
# ------------------------------------------------------------------ #

class _RoundTripVector(OperationDriver, BaseSystem):
    """A conforming vector for the ``weapons`` Branch — the composed shape §4.10.

    The five required hooks and nothing else, so every gate, clock, refusal,
    notification, persistence write, and the rebuild exercised below are the
    driver's own rather than this fixture's.

    ``on_resolve`` records an ``op_id`` instead of dealing damage on purpose: the
    damage path is task 11.11's guard, and what this module measures is whether
    an operation survives a restart still holding its clock.
    """

    operation_kind = RESTART_KIND
    branch = RESTART_BRANCH

    def __init__(self, registry, event_bus, *, branch_system, world=None) -> None:
        # Set before the MRO call: the driver's ``__init__`` subscribes the
        # lifecycle events, and a handler must never find a half-built vector.
        self.world: dict = dict(world or {})
        self.resolved: list[str] = []
        super().__init__(registry, event_bus, branch_system=branch_system)

    # -- the five required hooks ------------------------------------- #

    def validate_target(self, ctx):
        """Anything the shared protection gates allow is a strike target here."""
        return None

    def build_record(self, ctx):
        """Every field a value or a resolvable reference — never a live object.

        That is the discipline the whole restart rests on (design §7): a record
        holding objects would round-trip through this fixture's dicts and fail
        the moment a real Evennia attribute serialized it.
        """
        return OperationRecord(
            kind=self.operation_kind,
            owner_ref=ctx.player.id,
            building_ref=ctx.building.id,
            carrier_ref=ctx.carrier.id,
            planet=ctx.planet,
            target_x=ctx.target_x,
            target_y=ctx.target_y,
            target_ref=getattr(ctx.target, "id", None),
            ticks_remaining=int(ctx.param("ticks", RESTART_TICKS[-1])),
            magnitude=12.5,
            radius=1,
        )

    def on_resolve(self, record):
        self.resolved.append(record.op_id)

    def persistence_owner(self, record):
        """The originating building — the world object the strike is fired from."""
        return self._live(getattr(record, "building_ref", None))

    def discover_records(self, planet_rooms):
        """Every object in the world: an owner holding no records costs a read."""
        return [
            entity
            for room in (planet_rooms or {}).values()
            for entity in room.contents
        ]

    # -- the id -> object bridge a real vector gets from an object search -- #

    def _live(self, ref):
        """Return the object *ref* names; a live one (or ``None``) passes through."""
        if ref is None or isinstance(ref, bool) or not isinstance(ref, int):
            return ref
        for room in self.world.values():
            for entity in room.contents:
                if getattr(entity, "id", None) == ref:
                    return entity
        return None


# ------------------------------------------------------------------ #
#  The world, and booting a graph over it
# ------------------------------------------------------------------ #

class _RestartWorld:
    """The half of a restart that SURVIVES it: the world objects.

    A restart replaces the systems and empties every in-memory list; the durable
    owners and the Operation_Records on them are what is left. So this holds only
    objects, and :meth:`boot` wires a fresh system graph over them — which makes
    a restart literally "boot twice".
    """

    def __init__(self, registry: DataRegistry, planet: str = RESTART_PLANET) -> None:
        lab_abbr, origin_abbr, unlock_tech, role = _weapons_slice(registry)
        self.planet = planet
        self.registry = registry
        self.room = _FakePlanetRoom(planet)
        self.attacker = _FakePlayer(
            "Vex", planet, x=2, y=0,
            level=RESTART_ATTACKER_LEVEL, resources=dict(RESTART_PURSE),
        )
        # Standing on their own turret's tile, so the resolution audience is
        # reached through BOTH of R8.12's readings rather than only ownership.
        self.defender = _FakePlayer(
            "Mira", planet, x=9, y=9, level=RESTART_DEFENDER_LEVEL,
        )
        self.attacker.db.researched_techs = {unlock_tech}
        self.hq = _FakeBuilding("HQ", self.attacker, self.room, 0, 0)
        self.lab = _FakeBuilding(lab_abbr, self.attacker, self.room, 1, 0)
        # Two origins, because the cooldown is per building per Operation_Kind
        # (R8.19): a second request from the same one would be refused.
        self.origins = [
            _FakeBuilding(origin_abbr, self.attacker, self.room, 2, 0),
            _FakeBuilding(origin_abbr, self.attacker, self.room, 3, 0),
        ]
        self.carrier = _FakeAgent(role, self.attacker, self.room, 2, 0)
        self.defender_hq = _FakeBuilding("HQ", self.defender, self.room, 8, 8)
        self.target = _FakeBuilding("TU", self.defender, self.room, 9, 9)
        self.attacker.buildings = [self.hq, self.lab, *self.origins]
        self.defender.buildings = [self.defender_hq, self.target]
        self.room.contents = [
            self.attacker, self.defender, self.hq, self.lab, *self.origins,
            self.carrier, self.defender_hq, self.target,
        ]
        self.planet_rooms = {planet: self.room}
        self.roster = _FakeAgentRoster([self.carrier])

    def boot(self, registry: DataRegistry) -> _Graph:
        """Wire a fresh system graph over this world — one server start."""
        graph = _Graph(registry, agent_repository=self.roster)
        graph.vector = _RoundTripVector(
            registry, graph.event_bus,
            branch_system=graph.branch_system, world=self.planet_rooms,
        )
        graph.branch_system.register_vector(graph.vector)
        return graph

    def place(self, graph: _Graph, origin, ticks: int):
        """Request one operation from *origin* against the defender's turret."""
        return graph.vector.request(
            self.attacker,
            building=origin, target=self.target, x=9, y=9, ticks=ticks,
        )

    def stored(self) -> dict:
        """Return ``{op_id: payload}`` across every durable owner in the world.

        Read straight off the attribute the persistence pair writes, so this is
        what a restart would actually find rather than what a driver remembers.
        """
        payloads: dict = {}
        for entity in self.room.contents:
            for entry in entity.attributes.get(ATTR_VECTOR_OPERATIONS) or ():
                payloads[entry["op_id"]] = dict(entry)
        return payloads


def _restart_rebuild(graph: _Graph, planet_rooms) -> dict:
    """Run the composition root's per-vector rebuild fan-out.

    Written the way ``game_init`` writes it: the registered vectors are read off
    ``branch_system._vectors`` — the same defensive read the root makes, because
    ``register_vector`` files each vector there and ``BranchSystem`` publishes no
    accessor for that mapping — so this exercises the producer/consumer pair
    rather than calling one driver directly.

    The root's per-vector try/except is deliberately **not** copied: it exists so
    one broken vector cannot stop a server start, and swallowing a failure here
    would hide it instead. ``rebuild`` answers rather than raising anyway (R15.3).

    Returns:
        ``{operation_kind: operations now tracked}``.
    """
    registered = getattr(graph.branch_system, "_vectors", None) or {}
    return {
        kind: vector.rebuild(planet_rooms)
        for kind, vector in tuple(registered.items())
    }


def _run_vector_tick(graph: _Graph, tick_number: int) -> None:
    """Drive the real ``vector_operations`` tick step once for *graph*.

    Through ``_build_tick_steps`` and the ``game_systems`` key the root
    registers, so the fan-out a rebuilt operation resumes on is the shipped one.
    """
    key = _registered_branch_key() or "branch_system"
    steps = dict(
        _tick_script()._build_tick_steps(
            graph.systems(key), tick_number=tick_number
        )
    )
    steps["vector_operations"]()


class TestRestartRoundTrip(unittest.TestCase):
    """R8.22, R14.2, R14.3: an operation survives a restart and keeps advancing.

    Two operations are placed through the real validation chain, the systems are
    thrown away, a second graph is wired over the same world, and the root's
    rebuild fan-out has to hand both back to the tick loop with the clocks they
    were persisted with.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = _registry()

    def setUp(self) -> None:
        self.world = _RestartWorld(self.registry)
        self.before = self.world.boot(self.registry)
        self.outcomes = [
            self.world.place(self.before, origin, ticks=ticks)
            for origin, ticks in zip(self.world.origins, RESTART_TICKS)
        ]
        for outcome in self.outcomes:
            self.assertTrue(
                outcome.ok,
                "the fixture could not place an operation through the real "
                f"chain: refused at {outcome.check!r} with {outcome.detail!r}.",
            )

    # -- before the restart ----------------------------------------- #

    def test_a_placed_operation_is_persisted_on_its_durable_owner(self):
        """R14.1: the record reaches storage, or there is nothing to rebuild.

        Every assertion below this one is meaningless if the placement never
        wrote, so this is asserted first and separately.
        """
        stored = self.world.stored()
        self.assertEqual(len(stored), len(RESTART_TICKS))
        self.assertEqual(
            {entry["state"] for entry in stored.values()},
            {str(OperationState.PENDING)},
        )
        self.assertEqual(
            sorted(entry["ticks_remaining"] for entry in stored.values()),
            sorted(RESTART_TICKS),
            "the persisted clock must be the one the request placed — above the "
            "Response_Window floor, so a floored clock is distinguishable.",
        )
        for origin in self.world.origins:
            with self.subTest(origin=origin.id):
                self.assertEqual(
                    len(origin.attributes.get(ATTR_VECTOR_OPERATIONS) or []), 1,
                    "each originating building holds its own operation's record.",
                )

    def test_the_root_finds_the_registered_vector_where_it_looks_for_it(self):
        """The rebuild fan-out reads a mapping ``BranchSystem`` never publishes.

        ``register_vector`` files a vector under ``_vectors`` and the composition
        root reads that private mapping back to call ``rebuild`` on each one. Both
        sides are read here, so renaming the mapping — or filing a vector
        elsewhere — fails rather than silently leaving every operation inert
        after a restart.
        """
        registered = getattr(self.before.branch_system, "_vectors", None) or {}
        self.assertIn(RESTART_KIND, registered)
        self.assertIs(registered[RESTART_KIND], self.before.vector)
        self.assertTrue(
            callable(getattr(registered[RESTART_KIND], "rebuild", None)),
            "the root calls rebuild() on whatever it finds in _vectors.",
        )

    # -- the restart ------------------------------------------------- #

    def test_a_restart_re_tracks_every_non_terminal_operation(self):
        """R8.22 and R14.2: same operations, same values, from persistence alone."""
        written = self.world.stored()
        after = self.world.boot(self.registry)
        self.assertEqual(
            after.vector.tracked_records(), [],
            "a fresh graph must start from an empty tracked list — the records "
            "on the durable owners are the only state a restart leaves.",
        )

        self.assertEqual(
            _restart_rebuild(after, self.world.planet_rooms),
            {RESTART_KIND: len(RESTART_TICKS)},
        )

        rebuilt = {r.op_id: r for r in after.vector.tracked_records()}
        self.assertEqual(set(rebuilt), set(written))
        for op_id, record in rebuilt.items():
            for name in ROUND_TRIP_VALUES:
                with self.subTest(op_id=op_id, field=name):
                    self.assertEqual(getattr(record, name), written[op_id][name])

    def test_the_rebuilt_references_are_the_live_objects_again(self):
        """R14.4's other half: resolving is what carries the triggers across.

        Every condition that ends or pauses an operation gates on a live object —
        a dbref is not a corpse and not a demolished building — so an operation
        rebuilt with its references still spelled as ids would be judged by its
        clock alone, and R8.16, R8.17, and R8.18 would all be dead for it.
        """
        written = self.world.stored()
        after = self.world.boot(self.registry)
        _restart_rebuild(after, self.world.planet_rooms)

        for record in after.vector.tracked_records():
            payload = written[record.op_id]
            with self.subTest(op_id=record.op_id):
                self.assertIs(record.owner_ref, self.world.attacker)
                self.assertIs(record.carrier_ref, self.world.carrier)
                self.assertIs(record.target_ref, self.world.target)
                self.assertIn(record.building_ref, self.world.origins)
                self.assertEqual(record.building_ref.id, payload["building_ref"])

    def test_a_repeated_rebuild_duplicates_nothing(self):
        """R14.3: rebuilding twice tracks exactly what rebuilding once tracked."""
        after = self.world.boot(self.registry)

        once = _restart_rebuild(after, self.world.planet_rooms)
        tracked_once = sorted(r.op_id for r in after.vector.tracked_records())
        twice = _restart_rebuild(after, self.world.planet_rooms)

        self.assertEqual(once, twice)
        self.assertEqual(
            sorted(r.op_id for r in after.vector.tracked_records()), tracked_once
        )
        self.assertEqual(len(tracked_once), len(RESTART_TICKS))

    def test_every_rebuilt_operation_advances_on_the_next_tick(self):
        """R8.22's own words: each rebuilt operation RESUMES advancing.

        Driven through the real ``vector_operations`` step rather than by calling
        ``advance_all``, so the whole path a recovered operation depends on —
        the ``game_systems`` key, the tick step, ``BranchSystem``'s fan-out, the
        driver's per-record isolation — is what is measured.
        """
        written = self.world.stored()
        after = self.world.boot(self.registry)
        _restart_rebuild(after, self.world.planet_rooms)

        _run_vector_tick(after, tick_number=1)

        tracked = after.vector.tracked_records()
        self.assertEqual(len(tracked), len(RESTART_TICKS))
        for record in tracked:
            with self.subTest(op_id=record.op_id):
                self.assertEqual(
                    record.ticks_remaining,
                    int(written[record.op_id]["ticks_remaining"]) - 1,
                    "a rebuilt operation advances by exactly one tick, from the "
                    "clock it was persisted with rather than from a fresh one.",
                )
        self.assertEqual(
            {op: entry["ticks_remaining"] for op, entry in self.world.stored().items()},
            {record.op_id: record.ticks_remaining for record in tracked},
            "the advanced clock must reach storage, or the next restart would "
            "hand every operation its old clock back.",
        )

    def test_a_resolved_operation_is_not_resurrected_by_a_restart(self):
        """R8.2 across a restart: only the non-terminal operations come back.

        The shorter of the two operations is run to its effect before the
        restart. It is terminal, so the persist that settled it swept it out of
        its owner's container — and the rebuild has to bring back the survivor
        with the clock that survivor actually reached, not the one it launched
        with.
        """
        short, long = RESTART_TICKS
        for tick in range(1, short + 1):
            self.before.tick = tick
            _run_vector_tick(self.before, tick_number=tick)

        self.assertEqual(
            len(self.before.vector.resolved), 1,
            "exactly one operation's effect should have applied by now.",
        )
        survivors = self.world.stored()
        self.assertEqual(len(survivors), 1)

        after = self.world.boot(self.registry)
        self.assertEqual(
            _restart_rebuild(after, self.world.planet_rooms), {RESTART_KIND: 1}
        )
        tracked = after.vector.tracked_records()
        self.assertEqual([r.op_id for r in tracked], list(survivors))
        self.assertEqual(tracked[0].ticks_remaining, long - short)

    def test_a_cancellation_trigger_still_fires_after_a_restart(self):
        """R8.16 across a restart, which is what resolving the references buys.

        The carrier is killed *after* the rebuild, so nothing about the
        cancellation was decided before the restart: the rebuilt record has to be
        holding the live agent for the tick to notice.
        """
        after = self.world.boot(self.registry)
        _restart_rebuild(after, self.world.planet_rooms)

        self.world.carrier.db.hp = 0
        _run_vector_tick(after, tick_number=1)

        self.assertEqual(after.vector.tracked_records(), [])
        self.assertEqual(
            self.world.stored(), {},
            "Cancelled is terminal, so the persist that settled each record "
            "swept it out of its owner's container.",
        )


if __name__ == "__main__":
    unittest.main()
