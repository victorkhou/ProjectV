"""
Role-table and no-special-case unit tests for the Branch agent roles.

Feature: tech-tree-branch-foundation, design section "Testing Strategy / Unit
tests" — the *fixed-cardinality and membership* assertions plus two of the
*no-special-case guards*. Four concrete claims, none of them generative (the
property modules own the input space):

* **R7.4** — the six Carrier_Agent roles exist in ``AGENT_ROLES``, each carries
  the Branch the requirements name (`spotter`/weapons, `sapper`/defense,
  `courier`/resource, `infiltrator`/cyber, `medic`/bio, `scout`/research), and
  the table agrees in both directions with ``world.constants.BRANCH_ROLE`` — the
  bijection the schema validator enforces, read here from the table's side so a
  hand edit to either source fails loudly.
* **R7.4, the decided asymmetry** — `scout` is assignable with *no*
  Branch_Commitment and under every one of the six, so a pre-feature player's
  patrols keep working. Driven through the real ``AgentSystem.assign_agent``
  with a real ``BranchSystem`` wired as the resolver, because the claim is about
  the shipped gate over shipped data rather than about a stand-in that answers a
  fixed string. The same class asserts the five *gated* roles are refused with no
  commitment, which is what stops the scout sweep from passing vacuously (a gate
  that never fires would pass it too).
* **R7.9** — the rank-derived agent cap is the rank table's and nothing else:
  identical under every commitment and under none, at every level band.
  Committing to a Branch grants access to new roles, never additional slots.
* **R12.7** — harvest yields, extractor output, and storage capacities are
  unchanged for a player holding any Branch_Commitment other than `resource`.
  Every claim here is a *parity* assertion: the same economy operation runs for a
  player who holds a commitment and for an otherwise identical player who holds
  none, and the two outcomes must match. Each parity test first asserts the pair
  really straddles the commitment boundary, so the pairing can never pass
  vacuously. `resource` (Logistics) is deliberately excluded from the sweep — it
  is the one Branch the requirement exempts, since its own vector spec owns the
  economy it changes.

Because the subject is the SHIPPED role table, the SHIPPED rank table, and the
SHIPPED subsystems, every registry here is the real ``mygame/data`` directory
loaded through ``DataRegistry.load_all`` (the pattern ``test_branch_catalog.py``
establishes) rather than the synthetic fixture catalog in ``branch_strategies``:
a fixture would only re-assert what the property tests already cover.

**Validates: Requirements 7.4, 7.9, 12.7**
"""

import os
import unittest

# Imported first: this module installs the Evennia stub block, so the project
# imports below resolve with ``evennia`` absent from ``sys.modules`` (R15.1). It
# also carries the shared framework-free fakes this module reuses.
from mygame.world.systems.tests.branch_strategies import (  # noqa: E402
    FakeAttributes,
    FakeBuilding,
    FakeDB,
    FakePlayer,
)

from mygame.typeclasses.agent_scripts import AGENT_ROLES  # noqa: E402
from mygame.world.constants import (  # noqa: E402
    BRANCH_ROLE,
    BRANCHES,
    RESOURCE_TYPES,
)
from mygame.world.data_registry import DataRegistry  # noqa: E402
from mygame.world.event_bus import EventBus  # noqa: E402
from mygame.world.systems import building_storage  # noqa: E402
from mygame.world.systems.agent_system import (  # noqa: E402
    ALL_ROLES,
    GATED_BRANCH_ROLES,
    GATED_ROLE_FOR_BRANCH,
    UNGATED_BRANCH_ROLES,
    VALID_ROLES,
    AgentSystem,
)
from mygame.world.systems.branch_system import BranchSystem  # noqa: E402
from mygame.world.systems.rank_system import rank_from_level  # noqa: E402
from mygame.world.systems.resource_system import ResourceSystem  # noqa: E402

# ------------------------------------------------------------------ #
#  The real data directory (mygame/data)
# ------------------------------------------------------------------ #
#  This file lives at mygame/world/systems/tests/ ; the shipped definitions live
#  at mygame/data/ — three directories up, then into ``data``.
_REAL_DATA_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "data")
)

#: The planet every fixture here stands on. Commitment is per-planet, so player,
#: agent, and buildings must agree on one.
HOME = "earth"

#: Branch -> (role, script key), the catalog the requirements' Branch Overview
#: table states. Spelled out literally rather than derived, because this is the
#: one place the intended content is asserted instead of cross-checked.
EXPECTED_ROLE_BY_BRANCH: dict[str, tuple[str, str]] = {
    "weapons": ("spotter", "spotter_script"),
    "defense": ("sapper", "sapper_script"),
    "resource": ("courier", "courier_script"),
    "research": ("scout", "patrol_behavior"),
    "bio": ("medic", "medic_script"),
    "cyber": ("infiltrator", "infiltrator_script"),
}

#: The five roles R7.6's commitment gate applies to — the six minus `scout`.
EXPECTED_GATED_ROLES = frozenset(
    role for role, _key in EXPECTED_ROLE_BY_BRANCH.values()
) - {"scout"}

#: Levels the cap sweep visits: the floor, a few interior bands, and the ceiling,
#: so the claim is about the rank-derived *function* and not about one value.
CAP_LEVELS: tuple[int, ...] = (1, 5, 12, 25, 50, 100)

#: A level clearing every shipped gate these tests care about, and high enough
#: that the agent cap is comfortably above the rosters built below.
OWNER_LEVEL = 30

_REGISTRY: DataRegistry | None = None


def _real_registry() -> DataRegistry:
    """The shipped definitions, loaded once and shared by every test here.

    Harvest crits are switched off on this private copy: a crit spawns an extra
    drop on the same tile, which would make the active-presence harvest yield
    random and turn the R12.7 parity assertions into coin flips. Zeroing the
    chance is the shipped way to disable the mechanic (``_try_harvest_crit``
    returns immediately), and it is done on a registry instance this module owns
    rather than on the process-wide singleton.
    """
    global _REGISTRY
    if _REGISTRY is None:
        registry = DataRegistry()
        registry.load_all(_REAL_DATA_DIR)
        registry.balance.harvest_crit_chance = 0.0
        _REGISTRY = registry
    return _REGISTRY


def _branch_system(registry=None) -> BranchSystem:
    """A ``BranchSystem`` over the shipped catalog, on a bus of its own.

    The private bus matters: this system is only ever consulted here as a query
    object (commitment, lab identity) and as the agent system's resolver — never
    as a subscriber to the systems under test.
    """
    return BranchSystem(registry or _real_registry(), EventBus())


def _lab_abbr(branch: str, registry=None) -> str:
    """The abbreviation of the shipped Branch_Lab hosting *branch*."""
    abbr = _branch_system(registry).lab_for_branch(branch)
    assert abbr, f"no shipped lab hosts {branch!r}"
    return abbr


def _owner(lab_abbr=None, planet=HOME, level=OWNER_LEVEL, key="Owner"):
    """A player holding an HQ and optionally one Branch_Lab on *planet*.

    Owning the lab IS the Branch_Commitment (R3.1), so ``lab_abbr=None`` is the
    no-commitment case and passing an abbreviation is the committed case — the
    commitment is never set directly, because there is nothing to set.
    """
    buildings = [FakeBuilding(building_type="HQ", planet=planet)]
    if lab_abbr:
        buildings.append(FakeBuilding(building_type=lab_abbr, planet=planet))
    return FakePlayer(
        key=key,
        resources={resource: 1000 for resource in RESOURCE_TYPES},
        buildings=buildings,
        planet=planet,
        x=5,
        y=5,
        level=level,
    )


class _CommitmentCases:
    """Builds owners across the commitment boundary and pins that they differ."""

    def _committed(self, branch, **kwargs):
        """An owner whose commitment on HOME is *branch*."""
        return _owner(lab_abbr=_lab_abbr(branch, self.registry), **kwargs)

    def _assert_commitment(self, player, expected):
        """Pin *player*'s derived commitment, so no sweep can run vacuously.

        Without this, a parity or invariance test could pass because NEITHER
        subject holds a commitment — which would make the whole class silent.
        """
        self.assertEqual(
            _branch_system(self.registry).commitment(player, HOME), expected
        )


# ================================================================== #
#  Requirement 7.4 — the six roles and the Branch each carries
# ================================================================== #

class TestBranchRoleTable(unittest.TestCase):
    """``AGENT_ROLES`` supports the six Carrier_Agent roles R7.4 names.

    The role table is the single source of truth every derived lookup is
    computed from, so these are assertions about the table itself: the six
    entries exist, each carries the right Branch, and the table agrees with
    ``BRANCH_ROLE`` in both directions.
    """

    def test_each_of_the_six_roles_exists_and_carries_its_branch(self):
        for branch, (role, _key) in EXPECTED_ROLE_BY_BRANCH.items():
            with self.subTest(branch=branch, role=role):
                self.assertIn(role, AGENT_ROLES)
                self.assertEqual(AGENT_ROLES[role].branch, branch)

    def test_the_table_agrees_with_the_branch_role_constant_both_ways(self):
        # Branch -> role, the constant's direction.
        self.assertEqual(
            BRANCH_ROLE,
            {branch: role for branch, (role, _k) in EXPECTED_ROLE_BY_BRANCH.items()},
        )
        # role -> Branch, the table's direction. Equal sets in both directions is
        # the bijection R7.11 states; a drift on either side breaks one of them.
        self.assertEqual(
            {spec.name: spec.branch
             for spec in AGENT_ROLES.values() if spec.branch is not None},
            {role: branch for branch, (role, _k) in EXPECTED_ROLE_BY_BRANCH.items()},
        )

    def test_exactly_six_roles_carry_a_branch_one_per_branch(self):
        branched = [spec for spec in AGENT_ROLES.values() if spec.branch is not None]
        self.assertEqual(len(branched), 6)
        self.assertEqual({spec.branch for spec in branched}, set(BRANCHES))

    def test_no_other_role_claims_a_branch(self):
        # The pre-feature roles stay Branch-free, so the commitment gate can
        # never reach a harvester, engineer, guard, or the hidden soldier.
        for name in ("harvester", "engineer", "guard", "soldier"):
            with self.subTest(role=name):
                self.assertIsNone(AGENT_ROLES[name].branch)

    def test_all_six_are_player_assignable_army_roles(self):
        # "THE AgentSystem SHALL support the role X" means a player may assign
        # it: in VALID_ROLES (so not hidden — `medic` lost that flag for this
        # feature) and an army role, so a Carrier_Agent needs no target building.
        for branch, (role, _key) in EXPECTED_ROLE_BY_BRANCH.items():
            with self.subTest(branch=branch, role=role):
                spec = AGENT_ROLES[role]
                self.assertIn(role, ALL_ROLES)
                self.assertIn(role, VALID_ROLES)
                self.assertFalse(spec.hidden)
                self.assertTrue(spec.army)
                self.assertEqual(spec.buildings, ())

    def test_each_role_binds_the_expected_behaviour_script_key(self):
        # `scout` and `medic` keep the scripts they already shipped with
        # (PatrolBehavior / MedicScript); the other four bind the new shells.
        for branch, (role, script_key) in EXPECTED_ROLE_BY_BRANCH.items():
            with self.subTest(branch=branch, role=role):
                self.assertEqual(AGENT_ROLES[role].script_key, script_key)
                self.assertIsNotNone(AGENT_ROLES[role].script)

    def test_the_gate_tables_derive_from_the_role_table(self):
        # The three derived lookups AgentSystem gates on. `scout` is the one
        # exemption (see UNGATED_BRANCH_ROLES), so the gated set is the six minus
        # it and the Branch->role inverse covers five Branches.
        self.assertEqual(UNGATED_BRANCH_ROLES, frozenset({"scout"}))
        self.assertEqual(set(GATED_BRANCH_ROLES), EXPECTED_GATED_ROLES)
        self.assertEqual(
            GATED_BRANCH_ROLES,
            {role: AGENT_ROLES[role].branch for role in EXPECTED_GATED_ROLES},
        )
        self.assertEqual(
            GATED_ROLE_FOR_BRANCH,
            {branch: role for role, branch in GATED_BRANCH_ROLES.items()},
        )
        self.assertEqual(len(GATED_ROLE_FOR_BRANCH), 5)
        self.assertNotIn("research", GATED_ROLE_FOR_BRANCH)


# ================================================================== #
#  Fakes for the roster paths
# ================================================================== #

class _Agent:
    """A framework-free agent NPC: an ``attributes``/``db`` pair and nothing else.

    No ``scripts`` handler and no ``location`` on purpose — an army-role
    assignment needs neither, and the attach/detach helpers already no-op
    without them, so this fake stays the smallest thing ``assign_agent`` accepts.
    """

    def __init__(self, agent_id, owner, planet=HOME, role=""):
        self.key = f"Agent #{agent_id}"
        self.attributes = FakeAttributes({
            "agent_id": agent_id,
            "owner": owner,
            "npc_type": "agent",
            "role": role,
            "role_target": None,
            "coord_planet": planet,
        })
        self.db = FakeDB(self.attributes)

    def __repr__(self):  # pragma: no cover - diagnostics only
        return f"_Agent(#{self.db.agent_id}, role={self.db.role!r})"


class _Roster:
    """The ``AgentRepository`` port over an in-memory list.

    Injected rather than defaulted, so no test here needs an Evennia database.
    Ownership is compared by identity, matching the real adapter's scoping.
    """

    def __init__(self, agents=()):
        self.agents = list(agents)

    def find_agents_for_owner(self, owner):
        return [agent for agent in self.agents if agent.db.owner is owner]

    def find_all_agents(self):
        return list(self.agents)

    def find_all_enemies(self):
        return []

    def find_training_buildings(self):
        return []


def _agent_system(registry, resolver=None, roster=None):
    """An ``AgentSystem`` over an in-memory roster, with no framework at all."""
    system = AgentSystem(
        registry=registry,
        event_bus=EventBus(),
        agent_repository=roster if roster is not None else _Roster(),
    )
    system.set_branch_resolver(resolver)
    return system


# ================================================================== #
#  Requirement 7.4 — `scout` is assignable with no commitment
# ================================================================== #

class TestScoutIsAssignableWithoutACommitment(unittest.TestCase, _CommitmentCases):
    """The decided asymmetry: `scout` carries a Branch but is never gated.

    `scout` ships today as a free army role any player may assign, so gating it
    on a `research` commitment would break existing players' patrols. It still
    carries ``branch="research"`` for the R7.11 bijection and for Carrier_Agent
    lookup; the Recon Branch is gated where it counts, inside the
    Detection_Sweep operation's own commitment check.

    The resolver wired here is a real ``BranchSystem`` over the shipped catalog,
    so what is under test is the whole derivation — owned lab to commitment to
    gate decision — rather than a stand-in answering a fixed string.
    """

    def setUp(self):
        self.registry = _real_registry()

    def _assign(self, player, role):
        """Assign one fresh agent on HOME through the real gate."""
        roster = _Roster()
        system = _agent_system(
            self.registry, resolver=_branch_system(self.registry), roster=roster
        )
        agent = _Agent(1, player, planet=HOME)
        roster.agents.append(agent)
        ok, msg = system.assign_agent(player, 1, role)
        return agent, ok, msg

    def test_scout_is_assignable_with_no_commitment_at_all(self):
        player = _owner()
        self._assert_commitment(player, None)

        agent, ok, msg = self._assign(player, "scout")

        self.assertTrue(ok, msg)
        self.assertEqual(agent.db.role, "scout")

    def test_scout_is_assignable_under_every_one_of_the_six_commitments(self):
        for branch in BRANCHES:
            with self.subTest(commitment=branch):
                player = self._committed(branch)
                self._assert_commitment(player, branch)

                agent, ok, msg = self._assign(player, "scout")

                self.assertTrue(ok, msg)
                self.assertEqual(agent.db.role, "scout")

    def test_the_five_gated_roles_are_refused_with_no_commitment(self):
        # The guard that stops the two sweeps above from passing vacuously: with
        # the same wiring and the same no-commitment owner, every role the gate
        # DOES cover is refused, and the refusal names the Branch it requires.
        player = _owner()
        self._assert_commitment(player, None)

        for role in sorted(EXPECTED_GATED_ROLES):
            with self.subTest(role=role):
                agent, ok, msg = self._assign(player, role)

                self.assertFalse(ok)
                self.assertIn(GATED_BRANCH_ROLES[role], msg)
                self.assertEqual(agent.db.role, "")

    def test_a_gated_role_is_permitted_once_its_own_lab_is_owned(self):
        # The other half of the same guard: the gate is a gate, not a wall — the
        # real BranchSystem opens it for the Branch whose lab the player owns.
        for role, branch in sorted(GATED_BRANCH_ROLES.items()):
            with self.subTest(role=role, commitment=branch):
                player = self._committed(branch)
                self._assert_commitment(player, branch)

                agent, ok, msg = self._assign(player, role)

                self.assertTrue(ok, msg)
                self.assertEqual(agent.db.role, role)


# ================================================================== #
#  Requirement 7.9 — the rank-derived agent cap is untouched
# ================================================================== #

class TestAgentCapIsUnchangedByCommitment(unittest.TestCase, _CommitmentCases):
    """R7.9: committing to a Branch grants new roles, never new agent slots.

    The cap is derived from the player's level through the shipped rank table
    and from nothing else, so it must read identically for an owner holding each
    of the six commitments and for one holding none.
    """

    def setUp(self):
        self.registry = _real_registry()
        self.system = _agent_system(
            self.registry, resolver=_branch_system(self.registry)
        )

    def _expected_cap(self, level):
        """The cap recomputed from the shipped rank table alone.

        ``agent_cap`` in ranks.yaml includes the commander slot, so the usable
        agent-only cap is one less. Named here so the assertion below points at
        the rank table as the *only* input, rather than comparing the system to
        itself.
        """
        rank_num = rank_from_level(level)
        rank_def = self.registry.get_rank_by_level(rank_num)
        if rank_def is None:
            candidates = [r for r in self.registry.ranks if r.level <= rank_num]
            rank_def = (max(candidates, key=lambda r: r.level)
                        if candidates else self.registry.ranks[0])
        return rank_def.agent_cap - 1

    def test_the_cap_is_identical_under_every_commitment_and_under_none(self):
        for level in CAP_LEVELS:
            bare = _owner(level=level)
            self._assert_commitment(bare, None)
            baseline = self.system.get_max_agents(bare)

            for branch in BRANCHES:
                with self.subTest(level=level, commitment=branch):
                    player = self._committed(branch, level=level)
                    self._assert_commitment(player, branch)
                    self.assertEqual(self.system.get_max_agents(player), baseline)

    def test_the_cap_is_the_shipped_rank_tables_cap_minus_the_commander(self):
        # Non-vacuous on two counts: the caps really are rank-derived numbers,
        # and they really do rise with level — so the parity sweep above is not
        # comparing one constant to itself.
        caps = []
        for level in CAP_LEVELS:
            with self.subTest(level=level):
                player = self._committed("weapons", level=level)
                cap = self.system.get_max_agents(player)
                self.assertEqual(cap, self._expected_cap(level))
                self.assertGreaterEqual(cap, 1)
                caps.append(cap)
        self.assertGreater(caps[-1], caps[0])

    def test_training_refuses_at_the_same_cap_under_every_commitment(self):
        # The cap's real consumer. A roster already AT the cap must be refused
        # identically whatever the commitment, and the refusal must come before
        # any resource is charged.
        outcomes = set()
        for branch in (None, *BRANCHES):
            with self.subTest(commitment=branch):
                player = (_owner() if branch is None
                          else self._committed(branch))
                self._assert_commitment(player, branch)
                roster = _Roster()
                system = _agent_system(
                    self.registry,
                    resolver=_branch_system(self.registry),
                    roster=roster,
                )
                cap = system.get_max_agents(player)
                roster.agents.extend(
                    _Agent(i, player) for i in range(1, cap + 1)
                )
                before = player.resource_snapshot()

                ok, msg = system.train_agent(player, None)

                self.assertFalse(ok)
                self.assertEqual(player.resource_snapshot(), before)
                outcomes.add((ok, msg))
        self.assertEqual(len(outcomes), 1)


# ================================================================== #
#  Requirement 12.7 — the economy is unchanged under a combat Branch
# ================================================================== #

#: The five Branches R12.7 covers. `resource` is the exemption the requirement
#: itself names — Logistics is the one Branch allowed to change the economy, and
#: its vector spec owns that change.
NON_RESOURCE_BRANCHES: tuple[str, ...] = tuple(
    branch for branch in BRANCHES if branch != "resource"
)


class _Tile:
    """A legacy harvest tile: terrain, coords, a resource node, and a ground pile.

    Carries no ``is_node_depleted`` and no ``get_buildings_at``, so the resource
    system takes its legacy OverworldRoom path — the one that reads
    ``resource_node_data`` and drops yields into the tile's inventory.
    """

    def __init__(self, x=5, y=5, planet=HOME, terrain_type="Forest",
                 resource_type="Wood", building=None):
        self.x = x
        self.y = y
        self._terrain_type = terrain_type
        self._building = building
        self.attributes = FakeAttributes({
            "coord_x": x,
            "coord_y": y,
            "coord_planet": planet,
            "resource_node_data": {
                "resource_type": resource_type,
                "depleted": False,
                "respawn_counter": 0,
            },
        })
        self.db = FakeDB(self.attributes)

    @property
    def terrain_type(self):
        return self._terrain_type

    @property
    def building(self):
        return self._building


class TestEconomyIsUnchangedUnderANonResourceCommitment(
    unittest.TestCase, _CommitmentCases
):
    """R12.7: committing to a combat Branch costs no economic output.

    Harvest yields, extractor output, and storage capacities are run twice —
    once for an owner holding each non-`resource` commitment, once for an
    otherwise identical owner holding none — and the outcomes must match. The
    resource system consults no Branch at all, and these are the tests that say
    so: a commitment-sensitive multiplier sneaking into any of the three paths
    breaks the pairing.
    """

    def setUp(self):
        self.registry = _real_registry()

    def _resource_system(self):
        return ResourceSystem(self.registry, EventBus())

    # -- harvest yields ------------------------------------------------ #

    def _manual_harvest(self, player):
        """One manual harvest; return the credit, the node, and the answer.

        The credit is a *delta*, not a balance, so the comparison is about the
        yield rather than about the starting resources the fixture happens to
        hand out.
        """
        tile = _Tile()
        before = player.get_resource("Wood")
        system = self._resource_system()
        ok, msg = system.harvest(player, tile)
        node = tile.attributes.get("resource_node_data")
        return (
            ok,
            msg,
            player.get_resource("Wood") - before,
            node["depleted"],
            node["respawn_counter"],
        )

    def test_manual_harvest_yields_the_same_under_every_commitment(self):
        bare = _owner()
        self._assert_commitment(bare, None)
        baseline = self._manual_harvest(bare)

        for branch in NON_RESOURCE_BRANCHES:
            with self.subTest(commitment=branch):
                player = self._committed(branch)
                self._assert_commitment(player, branch)
                self.assertEqual(self._manual_harvest(player), baseline)

        # Non-vacuous: the harvest really credited something and really
        # depleted the node.
        self.assertTrue(baseline[0], baseline[1])
        self.assertGreater(baseline[2], 0)
        self.assertTrue(baseline[3])

    def _harvest_ticks(self, player, ticks):
        """Harvest actively for *ticks* ticks; return what landed on the ground."""
        tile = _Tile()
        player.location = tile
        system = self._resource_system()
        ok, msg = system.start_harvest(player, tile)
        yields = [system.process_harvest_tick(player) for _ in range(ticks)]
        return ok, msg, tuple(yields), ResourceSystem.get_tile_inventory(tile)

    def test_active_presence_harvest_yields_the_same_under_every_commitment(self):
        # Two full cooldown cycles, so both the yielding ticks and the silent
        # ones are compared — a commitment-sensitive cooldown would show up in
        # the per-tick pattern even if the total happened to match.
        cycles = 2
        ticks = self.registry.balance.harvest_cooldown_ticks * cycles
        bare = _owner()
        self._assert_commitment(bare, None)
        baseline = self._harvest_ticks(bare, ticks)

        for branch in NON_RESOURCE_BRANCHES:
            with self.subTest(commitment=branch):
                player = self._committed(branch)
                self._assert_commitment(player, branch)
                self.assertEqual(self._harvest_ticks(player, ticks), baseline)

        self.assertTrue(baseline[0], baseline[1])
        self.assertEqual(sum(baseline[2]), cycles)
        self.assertGreater(baseline[3].get("Wood", 0), 0)

    # -- extractor output ---------------------------------------------- #

    def _extractor_production(self, player, level=3, ticks=3):
        """Run *ticks* production ticks on one staffed Extractor.

        Returns the resources dropped on the extractor's tile plus every
        ``resource_gathered`` amount published, so both the stored effect and the
        announced one are compared.
        """
        extractor = FakeBuilding(building_type="EX", owner=player, level=level,
                                 planet=HOME, x=5, y=5)
        extractor.attributes.add("resource_type", "Wood")
        agent = _Agent(1, player)
        agent.db.role = "harvester"
        extractor.attributes.add("assigned_agent", agent)
        player.set_buildings([*player.get_buildings(), extractor])

        published: list = []
        event_bus = EventBus()
        event_bus.subscribe(
            "resource_gathered",
            lambda amount=None, **_kw: published.append(amount),
        )
        system = ResourceSystem(self.registry, event_bus)
        for _ in range(ticks):
            system.process_extractor_production([extractor])
        return ResourceSystem.get_extractor_inventory(extractor), tuple(published)

    def test_extractor_output_is_the_same_under_every_commitment(self):
        bare = _owner()
        self._assert_commitment(bare, None)
        baseline = self._extractor_production(bare)

        for branch in NON_RESOURCE_BRANCHES:
            with self.subTest(commitment=branch):
                player = self._committed(branch)
                self._assert_commitment(player, branch)
                self.assertEqual(self._extractor_production(player), baseline)
                # Pinned again after the run: the extractor joined the roster
                # mid-test, and the commitment must still be the lab's.
                self._assert_commitment(player, branch)

        # Non-vacuous: production really ran, three ticks' worth.
        _inventory, amounts = baseline
        self.assertEqual(len(amounts), 3)
        self.assertTrue(all(amount > 0 for amount in amounts))

    def test_extractor_output_scales_with_level_the_same_way(self):
        # The level scaling is the extractor's whole output curve, so a
        # commitment that changed it while leaving level 1 alone would slip past
        # a single-level comparison.
        outputs = []
        for level in (1, 3, 5):
            bare = _owner()
            self._assert_commitment(bare, None)
            baseline = self._extractor_production(bare, level=level, ticks=1)
            outputs.append(baseline[1])
            for branch in NON_RESOURCE_BRANCHES:
                with self.subTest(level=level, commitment=branch):
                    player = self._committed(branch)
                    self._assert_commitment(player, branch)
                    self.assertEqual(
                        self._extractor_production(player, level=level, ticks=1),
                        baseline,
                    )
        # Non-vacuous: the curve really rises, so the sweep is comparing three
        # different numbers rather than one repeated constant.
        self.assertGreater(outputs[-1], outputs[0])

    # -- storage capacities -------------------------------------------- #

    def _capacities(self, player):
        """Every storage capacity R12.7 covers, read for *player*.

        Three surfaces, because "storage capacity" has three shipped
        implementations: the Extractor's level-scaled inventory bound, the
        Vault's, and the Storage_Building resource pool bounded by the
        definition's ``storage_capacity``.
        """
        vault = FakeBuilding(building_type="VT", owner=player, planet=HOME)
        extractor = FakeBuilding(building_type="EX", owner=player, planet=HOME)
        # A capacity is only meaningful if it BINDS, so each pool is filled past
        # its bound and the accepted amount is what gets compared.
        stored = building_storage.deposit_to_building(
            vault, "Wood", 10 ** 6, provider=self.registry
        )
        extractor_stored = ResourceSystem.add_to_extractor_inventory(
            extractor, "Wood", 10 ** 6, 2
        )
        return (
            tuple(ResourceSystem.get_extractor_capacity(lvl) for lvl in (1, 3, 5)),
            tuple(ResourceSystem.get_vault_capacity(lvl) for lvl in (1, 3, 5)),
            building_storage.get_storage_capacity(vault, provider=self.registry),
            stored,
            building_storage.get_remaining_capacity(vault, provider=self.registry),
            extractor_stored,
        )

    def test_storage_capacities_are_the_same_under_every_commitment(self):
        bare = _owner()
        self._assert_commitment(bare, None)
        baseline = self._capacities(bare)

        for branch in NON_RESOURCE_BRANCHES:
            with self.subTest(commitment=branch):
                player = self._committed(branch)
                self._assert_commitment(player, branch)
                self.assertEqual(self._capacities(player), baseline)

        (extractor_caps, vault_caps, vault_capacity, stored, remaining,
         extractor_stored) = baseline
        # Non-vacuous: the capacities are real, they grow with level, and every
        # bound above actually clamped an oversized deposit.
        self.assertGreater(extractor_caps[0], 0)
        self.assertGreater(extractor_caps[-1], extractor_caps[0])
        self.assertGreater(vault_caps[0], 0)
        self.assertGreater(vault_caps[-1], vault_caps[0])
        self.assertEqual(stored, vault_capacity)
        self.assertEqual(remaining, 0)
        self.assertEqual(
            extractor_stored, ResourceSystem.get_extractor_capacity(2)
        )


if __name__ == "__main__":  # pragma: no cover - manual runs
    unittest.main()
