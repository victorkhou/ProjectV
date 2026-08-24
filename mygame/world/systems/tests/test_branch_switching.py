"""
Switching-cost and no-commitment unit tests for the Technology Branch feature.

Feature: tech-tree-branch-foundation, design section "Testing Strategy / Unit
tests" — the *no-special-case guards*. Three concrete claims, none of them
generative (the property modules own the input space):

* **R4.4** — the refund for demolishing a Branch_Building is the pre-existing
  ``balance.demolish_refund_rates[level]`` (``demolish_refund_default`` for a
  level outside that table) × ``BuildingSystem.get_building_investment``. Driven
  through the real ``CmdDemolish``, because the claim is that the *shipped*
  refund path applies unchanged to the buildings this feature introduced — a
  test that recomputed the formula against itself would prove nothing. The
  estate report task 5.3 added to that path (``report_demolish_estate``) is
  asserted to leave the refund alone.
* **R2.5** — a Neutral_Building is buildable under each of the six
  Branch_Commitments and under none, and constructing one gives the same answer
  and the same charge with the Branch gates unwired. Driven through the real
  ``BuildingSystem.construct`` chain with the gates spliced in via
  ``set_branch_validators(branch_system.construction_validators())``, so what is
  under test is the whole ordered chain rather than a gate in isolation.
* **R10.8** — holding no Branch_Commitment leaves melee combat, ranged combat,
  bombs, walls, turrets, and shields exactly as they were. Every claim here is a
  *parity* assertion: the same operation is run for a player who holds a
  commitment and for an otherwise identical player who holds none, and the two
  outcomes must match. Each parity test first asserts the two players really do
  differ in commitment, so the pairing can never pass vacuously.

Because all three claims are about SHIPPED content and the SHIPPED subsystems,
every registry here is the real ``mygame/data`` directory loaded through
``DataRegistry.load_all`` (the pattern ``test_branch_catalog.py`` establishes)
rather than the synthetic fixture catalog in ``branch_strategies``: a fixture
would only re-assert what the property tests already cover.

**Validates: Requirements 4.4, 10.8, 2.5**
"""

import os
import sys
import types
import unittest
from collections import namedtuple

# Imported first: this module installs the Evennia stub block, so the project
# imports below resolve with ``evennia`` absent from ``sys.modules``. It also
# carries the shared framework-free fakes this module reuses.
from mygame.world.systems.tests.branch_strategies import (  # noqa: E402
    FakeBuilding,
    FakePlayer,
)


def _ensure_command_stub():
    """Top up the stub block with the one module the command layer needs.

    ``branch_strategies`` stubs the typeclass modules; ``commands.game_commands``
    additionally imports ``evennia.commands.command.Command``. The repo-root
    conftest installs it, so this is only a standalone-run safety net — and it is
    skipped entirely when a real Evennia is importable, exactly as the shared
    stub block is.
    """
    real = sys.modules.get("evennia")
    if real is not None and getattr(real, "__file__", None):
        return
    if "evennia.commands.command" in sys.modules:
        return
    module = types.ModuleType("evennia.commands.command")
    module.Command = type("Command", (), {
        "func": lambda self: None,
        "at_pre_cmd": lambda self: False,
        "at_post_cmd": lambda self: None,
    })
    sys.modules["evennia.commands.command"] = module


_ensure_command_stub()

from mygame.world.constants import BRANCHES, RESOURCE_TYPES  # noqa: E402
from mygame.world.data_registry import DataRegistry  # noqa: E402
from mygame.world.event_bus import EventBus, PLAYER_NOTIFICATION  # noqa: E402
from mygame.world.systems.bomb_system import BombSystem  # noqa: E402
from mygame.world.systems.branch_system import BranchSystem  # noqa: E402
from mygame.world.systems.building_system import BuildingSystem  # noqa: E402
from mygame.world.systems.combat_engine import (  # noqa: E402
    CombatEngine,
    SyntheticWeapon,
)
from mygame.world.systems.shield_system import ShieldSystem  # noqa: E402

from mygame.commands.game_commands import CmdDemolish  # noqa: E402

# The command layer resolves its systems through the services facade, and every
# copy of ``world.utils`` reads THIS module (``from world import services``), so
# this unprefixed import is the facade the command under test will actually see.
from world import services  # noqa: E402

# ------------------------------------------------------------------ #
#  The real data directory (mygame/data)
# ------------------------------------------------------------------ #
#  This file lives at mygame/world/systems/tests/ ; the shipped definitions live
#  at mygame/data/ — three directories up, then into ``data``.
_REAL_DATA_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "data")
)

#: The planet every fixture in this module stands on. Commitment and estate are
#: per-planet, so player, tile, and buildings must agree on one.
HOME = "earth"

#: Shipped Neutral_Buildings the "buildable under every commitment" sweep uses.
#: ``WL`` and ``TU`` are the wall and the turret R10.8 names by capability;
#: ``VT`` is a third, plainer one so the sweep is not two defense buildings.
#: The HQ is deliberately absent: the pre-existing one-HQ-per-planet gate refuses
#: a second one, which would make the sweep fail for a reason unrelated to any
#: Branch (``TestNeutralBuildingsAreBuildableUnderEveryCommitment`` pins that
#: these three carry no Branch_Affiliation, so a content edit fails loudly).
NEUTRAL_ABBRS = ("WL", "VT", "TU")

#: The shipped `weapons` Branch_Building and its unlocking technology — the
#: contrast case proving the gates in the chain are live, not inert.
BRANCH_BUILDING_ABBR = "OW"
BRANCH_BUILDING_TECH = "field_marksmanship"

#: A level clearing every shipped ``rank_requirement`` these tests build at
#: (``TU`` 5, ``OW`` 12), so a refusal is never the level gate's doing.
BUILDER_LEVEL = 30

_REGISTRY: DataRegistry | None = None


def _real_registry() -> DataRegistry:
    """The shipped definitions, loaded once and shared by every test here."""
    global _REGISTRY
    if _REGISTRY is None:
        registry = DataRegistry()
        registry.load_all(_REAL_DATA_DIR)
        _REGISTRY = registry
    return _REGISTRY


def _branch_system(registry=None) -> BranchSystem:
    """A ``BranchSystem`` over the shipped catalog, on a bus of its own.

    The private bus matters: the systems under test publish construction and
    destruction events, and this system is only ever consulted here as a query
    object (identity, commitment, estate, gates) — never as a subscriber.
    """
    return BranchSystem(registry or _real_registry(), EventBus())


# ================================================================== #
#  Fakes
# ================================================================== #

class _Tile:
    """A placement tile: the terrain, coords, and planet the chain reads."""

    def __init__(self, x=5, y=5, planet=HOME, terrain_type="Plains"):
        self.x = x
        self.y = y
        self._terrain_type = terrain_type
        self._building = None
        self.db = types.SimpleNamespace(
            coord_x=x, coord_y=y, planet=planet, coord_planet=planet,
        )

    @property
    def terrain_type(self):
        return self._terrain_type

    @property
    def building(self):
        return self._building


class _Room:
    """A PlanetRoom stand-in: a coordinate index over buildings and players.

    The demolish path finds its target through ``get_buildings_at``; the bomb
    path fans a blast through ``get_objects_in_area`` and broadcasts through
    ``get_players_at``. One room serves both.
    """

    def __init__(self, planet=HOME):
        self.planet_name = planet
        self.contents: list = []
        self._buildings: dict = {}
        self._objects: dict = {}
        self._players: dict = {}

    # -- placement ------------------------------------------------- #

    def place_building(self, building, x, y):
        self._buildings.setdefault((int(x), int(y)), []).append(building)
        self._objects.setdefault((int(x), int(y)), []).append(building)
        building.location = self

    def place_object(self, obj, x, y):
        self._objects.setdefault((int(x), int(y)), []).append(obj)

    def place_player(self, player, x, y):
        self._players.setdefault((int(x), int(y)), []).append(player)
        self.contents.append(player)
        player.location = self

    # -- queries --------------------------------------------------- #

    def get_buildings_at(self, x, y):
        return list(self._buildings.get((int(x), int(y)), []))

    def get_objects_at(self, x, y, type_tag=None):
        return list(self._objects.get((int(x), int(y)), []))

    def get_players_at(self, x, y):
        return list(self._players.get((int(x), int(y)), []))

    def get_objects_in_area(self, x1, y1, x2, y2):
        found: list = []
        for (ox, oy), objects in self._objects.items():
            if x1 <= ox <= x2 and y1 <= oy <= y2:
                found.extend(objects)
        for (px, py), players in self._players.items():
            if x1 <= px <= x2 and y1 <= py <= y2:
                found.extend(players)
        return found


class _Caller(FakePlayer):
    """A ``FakePlayer`` that also answers ``msg`` — what a command needs extra."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.messages: list = []

    def msg(self, text=None, **_kwargs):
        if text is not None:
            self.messages.append(text)


class _SupplyBag:
    """The two supply methods the bomb deploy path reads off ``player.equipment``."""

    def __init__(self, supplies=None):
        self._supplies = dict(supplies or {})

    def get_supply(self, item_key):
        return self._supplies.get(item_key, 0)

    def get_supplies(self):
        return dict(self._supplies)

    def remove_supply(self, item_key, count):
        held = self._supplies.get(item_key, 0)
        if held < count:
            return False
        self._supplies[item_key] = held - count
        return True


class _LiveBomb:
    """A placed, ticking bomb — the surface the fuse countdown and blast read."""

    def __init__(self, room, x, y, owner, item_def, bomb_type, fuse, amount, radius):
        self.key = item_def.name
        self.location = room
        self.pk = 1
        self.db = types.SimpleNamespace(
            coord_x=x, coord_y=y, coord_planet=getattr(room, "planet_name", None),
            owner=owner, amount=amount, radius=radius, fuse_remaining=fuse,
            item_key=item_def.key, bomb_type=bomb_type, disarm_ticks_remaining=0,
        )

    def delete(self):
        self.pk = None


class _RecordingEngine:
    """Records the blast fan-out as plain data, so two runs can be compared.

    Deliberately not a real ``CombatEngine``: the claim under test is that the
    bomb path resolves the same targets, damage, and attribution regardless of
    the placer's commitment — not what the damage pipeline then does with them
    (that is the melee/ranged parity test's subject, which uses the real engine).
    """

    def __init__(self, owner=None):
        self.owner = owner
        self.hits: list = []

    def apply_direct_hit(self, attacker, target, weapon, include_attacker_bonus=True):
        self.hits.append((
            getattr(target, "key", None),
            weapon.get_stat("damage"),
            weapon.get_stat("range"),
            attacker is self.owner,
            include_attacker_bonus,
        ))
        return 10


# ================================================================== #
#  Shared builders
# ================================================================== #

def _building_system(registry=None, event_bus=None):
    """A ``BuildingSystem`` over *registry* with a recording building factory."""
    registry = registry or _real_registry()
    created: list = []

    def _create(building_def, tile, owner, x=None, y=None):
        building = FakeBuilding(
            building_type=building_def.abbreviation,
            owner=owner,
            hp=building_def.max_health,
            hp_max=building_def.max_health,
            planet=getattr(getattr(tile, "db", None), "planet", None),
        )
        created.append(building)
        tile._building = building
        building.location = tile
        return building

    system = BuildingSystem(
        registry=registry,
        event_bus=event_bus or EventBus(),
        create_building_func=_create,
        build_range=10,
        current_tick_func=lambda: 0,
    )
    return system, created


def _builder(lab_abbr=None, techs=(), planet=HOME, level=BUILDER_LEVEL):
    """A player holding an HQ, ample resources, and optionally one Branch_Lab.

    Owning the lab IS the Branch_Commitment (R3.1), so ``lab_abbr=None`` is the
    no-commitment case and passing a lab abbreviation is the committed case.
    """
    buildings = [FakeBuilding(building_type="HQ", planet=planet)]
    if lab_abbr:
        buildings.append(FakeBuilding(building_type=lab_abbr, planet=planet))
    player = FakePlayer(
        resources={resource: 1000 for resource in RESOURCE_TYPES},
        buildings=buildings,
        planet=planet,
        x=5,
        y=5,
        level=level,
    )
    player.db.researched_techs = set(techs)
    return player


def _combatant(x=5, y=5, planet=HOME, buildings=(), hp=200, key="Fighter"):
    """A combat-ready player: HP, combat XP, and a resolvable tile position."""
    player = FakePlayer(
        key=key, buildings=list(buildings), planet=planet, x=x, y=y, level=10,
    )
    player.db.hp = hp
    player.db.hp_max = hp
    player.db.combat_xp = 0
    return player


def _melee_weapon(damage=40):
    """A synthetic melee weapon: no ammo, effective range forced to 1."""
    weapon = SyntheticWeapon(damage, 1, name="a melee strike")
    weapon.weapon_type = "melee"
    weapon.ammo_type = None
    weapon.ammo_per_shot = 0
    return weapon


def _ranged_weapon(damage=40, weapon_range=5):
    """A synthetic ranged weapon: no magazine and no resource ammo cost."""
    weapon = SyntheticWeapon(damage, weapon_range, name="a rifle")
    weapon.weapon_type = "ranged"
    weapon.ammo_type = None
    weapon.ammo_per_shot = 0
    return weapon


class _CommitmentPairMixin:
    """Builds the (no-commitment, committed) pair every R10.8 parity test needs."""

    BRANCH = "weapons"

    def _lab_abbr(self, registry=None):
        return _branch_system(registry).lab_for_branch(self.BRANCH)

    def _assert_pair_differs(self, uncommitted, committed, registry=None):
        """Pin that the pair really straddles the commitment boundary.

        Without this the parity assertions could pass because NEITHER player
        holds a commitment, which would make every test in the class vacuous.
        """
        system = _branch_system(registry)
        self.assertIsNone(system.commitment(uncommitted, HOME))
        self.assertEqual(system.commitment(committed, HOME), self.BRANCH)


# ================================================================== #
#  Requirement 4.4 — the demolish refund on a Branch_Building
# ================================================================== #

#: What one scripted demolish produced: the resources actually credited, the
#: rate × investment the requirement says they must equal, and the notifications
#: the run published.
_Demolition = namedtuple(
    "_Demolition", "refund expected investment rate notifications"
)


class TestDemolishRefundOnABranchBuilding(unittest.TestCase):
    """R4.4: the existing partial-refund arithmetic covers Branch_Buildings.

    ``CmdDemolish`` prices a refund as ``demolish_refund_rates[level]`` × the
    owner-discounted cumulative investment. R4.4 asks for exactly that on a
    Branch_Building — no Branch-specific rate, no Branch-specific basis — and
    for the result to be a PARTIAL refund, so abandoning a Branch always returns
    less than was sunk into it.
    """

    @classmethod
    def setUpClass(cls):
        cls.registry = _real_registry()
        cls.branch_buildings = {
            branch: _branch_system(cls.registry).branch_buildings(branch)
            for branch in BRANCHES
        }

    def _install(self, **systems):
        """Install *systems* in the services facade for this test only."""
        manager = services.override(dict(systems))
        manager.__enter__()
        self.addCleanup(manager.__exit__, None, None, None)

    def _demolish(self, abbr, level, estate_provider=None, extra_buildings=()):
        """Demolish a level-*level* *abbr* through the real command and report.

        The expected refund is computed here, once and visibly, from the two
        things R4.4 names: the shipped rate table (with its documented default)
        and ``get_building_investment`` priced for the owner.
        """
        system, _created = _building_system(self.registry)
        published: list = []
        system.event_bus.subscribe(
            PLAYER_NOTIFICATION,
            lambda player=None, kind=None, data=None, **_kw: published.append(kind),
        )
        if estate_provider is not None:
            system.set_branch_estate_provider(estate_provider)
        self._install(registry=self.registry, building_system=system)

        caller = _Caller(planet=HOME, x=5, y=5)
        target = FakeBuilding(
            building_type=abbr, owner=caller, level=level, planet=HOME, x=5, y=5,
        )
        caller.set_buildings([target, *extra_buildings])
        room = _Room()
        room.place_building(target, 5, 5)
        caller.location = room

        balance = self.registry.balance
        rate = balance.demolish_refund_rates.get(
            level, balance.demolish_refund_default
        )
        investment = system.get_building_investment(
            self.registry.get_building(abbr), level, owner=caller
        )
        expected = {
            resource: int(amount * rate)
            for resource, amount in investment.items()
            if int(amount * rate) > 0
        }

        command = CmdDemolish()
        command.caller = caller
        command.args = ""
        command.cmdstring = command.key
        command.func()

        refund = {
            resource: amount
            for resource, amount in caller.resource_snapshot().items()
            if amount
        }
        return _Demolition(refund, expected, investment, rate, published)

    def test_every_branch_has_at_least_one_branch_building_to_demolish(self):
        # Guards the sweeps below against silently iterating nothing.
        for branch in BRANCHES:
            with self.subTest(branch=branch):
                self.assertTrue(self.branch_buildings[branch])

    def test_refund_is_the_rate_times_the_investment_at_every_level(self):
        for branch in BRANCHES:
            for abbr in self.branch_buildings[branch]:
                for level in range(1, 6):
                    with self.subTest(branch=branch, abbr=abbr, level=level):
                        result = self._demolish(abbr, level)
                        self.assertEqual(result.refund, result.expected)
                        self.assertEqual(
                            result.rate,
                            self.registry.balance.demolish_refund_rates[level],
                        )

    def test_the_refund_returns_less_than_was_invested(self):
        # R4.4's purpose clause: a PARTIAL refund, so abandoning a Branch costs
        # real resources. Every shipped rate is below 1.0, so this holds per
        # resource line as well as in total.
        for branch in BRANCHES:
            abbr = self.branch_buildings[branch][0]
            for level in range(1, 6):
                with self.subTest(abbr=abbr, level=level):
                    result = self._demolish(abbr, level)
                    self.assertLess(
                        sum(result.refund.values()),
                        sum(result.investment.values()),
                    )
                    for resource, paid in result.investment.items():
                        self.assertLess(result.refund.get(resource, 0), paid)

    def test_a_level_outside_the_rate_table_falls_back_to_the_default(self):
        # The table covers levels 1-5; the documented fallback is what a raised
        # ``max_level`` would meet, and it must be the DEFAULT, not zero refund.
        abbr = self.branch_buildings["weapons"][0]
        result = self._demolish(abbr, 6)
        self.assertEqual(result.rate, self.registry.balance.demolish_refund_default)
        self.assertEqual(result.refund, result.expected)
        self.assertTrue(result.refund)

    def test_the_estate_progress_report_leaves_the_refund_untouched(self):
        # Task 5.3 added ``report_demolish_estate`` to this path. It reads the
        # estate and publishes a count; it must not move a single resource.
        abbr = self.branch_buildings["weapons"][0]
        provider = _branch_system(self.registry)
        standing = FakeBuilding(building_type=abbr, planet=HOME, x=6, y=5)

        unwired = self._demolish(abbr, 3)
        wired = self._demolish(
            abbr, 3, estate_provider=provider, extra_buildings=[standing],
        )

        self.assertEqual(wired.refund, unwired.refund)
        self.assertEqual(wired.refund, wired.expected)
        # And the report DID fire, so the equality above is not the report
        # silently no-opping.
        self.assertIn("branch_estate_progress", wired.notifications)
        self.assertNotIn("branch_estate_progress", unwired.notifications)

    def test_the_same_rate_table_governs_a_neutral_building(self):
        # The rate is keyed on LEVEL alone: a Neutral_Building of the same level
        # is priced by the same entry, which is what "the existing refund rates"
        # means. Pinned alongside the Branch case so a Branch-specific rate
        # introduced later fails here rather than passing unnoticed.
        for level in range(1, 6):
            with self.subTest(level=level):
                result = self._demolish("VT", level)
                self.assertEqual(result.refund, result.expected)
                self.assertEqual(
                    result.rate,
                    self.registry.balance.demolish_refund_rates[level],
                )


# ================================================================== #
#  Requirement 2.5 (and R10.8's walls and turrets) — Neutral_Buildings
# ================================================================== #

class TestNeutralBuildingsAreBuildableUnderEveryCommitment(unittest.TestCase):
    """R2.5: a Neutral_Building is buildable under every commitment and none.

    Driven through the real ``BuildingSystem.construct`` chain with the three
    Branch gates spliced in, so the subject is the whole ordered chain. The
    sweep includes the shipped wall and turret, which is also R10.8's "walls,
    turrets ... unchanged" for a player holding no commitment.
    """

    @classmethod
    def setUpClass(cls):
        cls.registry = _real_registry()
        cls.labs = {
            branch: _branch_system(cls.registry).lab_for_branch(branch)
            for branch in BRANCHES
        }

    def _wired(self):
        """A ``BuildingSystem`` with the Branch gates in its validation chain."""
        system, created = _building_system(self.registry)
        branch_system = _branch_system(self.registry)
        system.set_branch_validators(branch_system.construction_validators())
        return system, created

    def test_the_swept_buildings_declare_no_branch_affiliation(self):
        # The sweep below is only meaningful while these stay Neutral_Buildings.
        branch_system = _branch_system(self.registry)
        for abbr in NEUTRAL_ABBRS:
            with self.subTest(abbr=abbr):
                self.assertIsNone(branch_system.branch_of_building(abbr))

    def test_every_commitment_and_none_permits_every_neutral_building(self):
        for branch in (None, *BRANCHES):
            lab = self.labs[branch] if branch else None
            for abbr in NEUTRAL_ABBRS:
                with self.subTest(commitment=branch, abbr=abbr):
                    system, created = self._wired()
                    player = _builder(lab_abbr=lab)
                    ok, msg = system.construct(player, _Tile(), abbr)
                    self.assertTrue(ok, msg)
                    self.assertEqual(len(created), 1)

    def test_a_neutral_building_builds_identically_with_the_gates_unwired(self):
        # The migration claim in its strongest form: for a Neutral_Building the
        # spliced gates are not merely permissive, they are inert — same answer,
        # same charge, with and without them.
        for abbr in NEUTRAL_ABBRS:
            with self.subTest(abbr=abbr):
                wired, wired_created = self._wired()
                plain, plain_created = _building_system(self.registry)
                wired_player, plain_player = _builder(), _builder()

                wired_result = wired.construct(wired_player, _Tile(), abbr)
                plain_result = plain.construct(plain_player, _Tile(), abbr)

                self.assertEqual(wired_result, plain_result)
                self.assertEqual(len(wired_created), len(plain_created))
                self.assertEqual(
                    wired_player.resource_snapshot(),
                    plain_player.resource_snapshot(),
                )

    def test_a_branch_building_is_still_gated_in_the_same_chain(self):
        # The "for the right reason" guard: the sweep above passes because the
        # buildings are Neutral, not because the gates are unwired or inert. The
        # same chain refuses a Branch_Building under all seven commitment states
        # and permits it only under the matching commitment with its unlocking
        # technology researched.
        for branch in (None, *BRANCHES):
            lab = self.labs[branch] if branch else None
            with self.subTest(commitment=branch):
                system, created = self._wired()
                player = _builder(lab_abbr=lab)
                ok, _msg = system.construct(player, _Tile(), BRANCH_BUILDING_ABBR)
                self.assertFalse(ok)
                self.assertEqual(created, [])

        system, created = self._wired()
        player = _builder(lab_abbr=self.labs["weapons"], techs=[BRANCH_BUILDING_TECH])
        ok, msg = system.construct(player, _Tile(), BRANCH_BUILDING_ABBR)
        self.assertTrue(ok, msg)
        self.assertEqual(len(created), 1)


# ================================================================== #
#  Requirement 10.8 — no commitment leaves the existing surfaces alone
# ================================================================== #

class TestNoCommitmentLeavesCombatUnchanged(unittest.TestCase, _CommitmentPairMixin):
    """R10.8: melee and ranged combat are indifferent to Branch_Commitment.

    The real ``CombatEngine`` resolves the same attack twice — once for an
    attacker who owns a Branch_Lab, once for one who owns none — and the damage
    dealt, the accept/refuse answer, and the target's remaining HP must match.
    """

    def setUp(self):
        self.registry = _real_registry()
        self.lab = self._lab_abbr(self.registry)

    def _resolve(self, weapon, attacker_buildings=(), same_tile=True):
        """Resolve one attack; return ``(ok, message, damage dealt)``."""
        engine = CombatEngine(
            registry=self.registry,
            event_bus=EventBus(),
            current_tick_func=lambda: 0,
        )
        attacker = _combatant(
            x=5, y=5, buildings=attacker_buildings, key="Attacker",
        )
        target = _combatant(
            x=5 if same_tile else 7, y=5, key="Target",
        )
        before = target.db.hp
        ok, msg = engine.resolve_now(attacker, target, weapon=weapon)
        return attacker, ok, msg, before - target.db.hp

    def _pair(self, weapon_factory, same_tile):
        """Run the attack with no commitment and with one; return both outcomes."""
        bare, bare_ok, bare_msg, bare_damage = self._resolve(
            weapon_factory(), same_tile=same_tile,
        )
        lab = FakeBuilding(building_type=self.lab, planet=HOME)
        committed, ok, msg, damage = self._resolve(
            weapon_factory(), attacker_buildings=[lab], same_tile=same_tile,
        )
        self._assert_pair_differs(bare, committed, self.registry)
        return (bare_ok, bare_msg, bare_damage), (ok, msg, damage)

    def test_melee_resolves_identically_with_and_without_a_commitment(self):
        bare, committed = self._pair(_melee_weapon, same_tile=True)
        self.assertEqual(bare, committed)
        self.assertTrue(bare[0], bare[1])
        self.assertGreater(bare[2], 0)

    def test_ranged_resolves_identically_with_and_without_a_commitment(self):
        bare, committed = self._pair(_ranged_weapon, same_tile=False)
        self.assertEqual(bare, committed)
        self.assertTrue(bare[0], bare[1])
        self.assertGreater(bare[2], 0)

    def test_an_uncommitted_player_is_a_valid_target_of_both(self):
        # The other half of "access is unchanged": no commitment does not make a
        # player unhittable either, so the no-commitment state stays playable
        # rather than becoming a shelter.
        for factory, same_tile in ((_melee_weapon, True), (_ranged_weapon, False)):
            with self.subTest(weapon=factory.__name__):
                _attacker, ok, msg, damage = self._resolve(
                    factory(), same_tile=same_tile,
                )
                self.assertTrue(ok, msg)
                self.assertGreater(damage, 0)


class TestNoCommitmentLeavesBombsUnchanged(unittest.TestCase, _CommitmentPairMixin):
    """R10.8: arming and detonating a bomb is indifferent to Branch_Commitment.

    The shipped ``land_mine`` is fused, armed, and ticked down to its blast for
    both halves of the pair; the placement, the countdown, and the blast
    fan-out (targets, damage, radius, attribution to the placer) must match.
    """

    ITEM_KEY = "land_mine"
    FUSE = 2

    def setUp(self):
        self.registry = _real_registry()
        self.lab = self._lab_abbr(self.registry)

    def _run(self, owner_buildings=()):
        """Fuse, arm, and detonate one mine; return the placement and the blast."""
        room = _Room()
        placed: list = []
        owner = _combatant(x=5, y=5, buildings=owner_buildings, key="Placer")
        owner.equipment = _SupplyBag({self.ITEM_KEY: 1})
        engine = _RecordingEngine(owner=owner)

        def _spawn(location, item_def, x, y, placer, bomb_type, fuse, amount, radius):
            placed.append((x, y, bomb_type, fuse, amount, radius))
            bomb = _LiveBomb(
                location, x, y, placer, item_def, bomb_type, fuse, amount, radius,
            )
            location.place_object(bomb, x, y)
            return bomb

        system = BombSystem(
            self.registry,
            EventBus(),
            spawn_bomb_func=_spawn,
            area_damage_applier=lambda: engine,
        )
        victim = _combatant(x=5, y=6, key="Victim")
        room.place_player(owner, 5, 5)
        room.place_player(victim, 5, 6)

        armed = system.set_fuse(owner, self.ITEM_KEY, self.FUSE) and system.arm_mine(
            owner, self.ITEM_KEY
        )
        ticks = 0
        while system._live_bombs and ticks < self.FUSE + 2:
            system.process_tick()
            ticks += 1
        return owner, (armed, tuple(placed), ticks, sorted(engine.hits))

    def test_a_mine_arms_and_detonates_identically_without_a_commitment(self):
        bare_owner, bare = self._run()
        lab = FakeBuilding(building_type=self.lab, planet=HOME)
        committed_owner, committed = self._run(owner_buildings=[lab])
        self._assert_pair_differs(bare_owner, committed_owner, self.registry)

        self.assertEqual(bare, committed)
        armed, placed, ticks, hits = bare
        self.assertTrue(armed)
        self.assertEqual(len(placed), 1)
        self.assertEqual(ticks, self.FUSE)
        # The blast caught the placer and the neighbour, credited to the placer.
        self.assertEqual(len(hits), 2)
        self.assertTrue(all(hit[3] for hit in hits))


class TestNoCommitmentLeavesShieldsUnchanged(unittest.TestCase, _CommitmentPairMixin):
    """R10.8: a Shield Generator projects the same shield without a commitment.

    The real ``ShieldSystem`` refreshes an identical base twice — once for an
    owner who holds a Branch_Commitment, once for one who holds none — and the
    covered building's capacity and charge must match.
    """

    def setUp(self):
        self.registry = _real_registry()
        self.lab = self._lab_abbr(self.registry)

    def _refresh(self, owner_lab=None):
        """Refresh a generator + covered vault base; return the vault's shield."""
        buildings = [
            FakeBuilding(building_type="HQ", planet=HOME, x=5, y=4),
            FakeBuilding(building_type="SG", planet=HOME, x=5, y=5, hp=200, hp_max=200),
            FakeBuilding(building_type="VT", planet=HOME, x=6, y=5, hp=400, hp_max=400),
        ]
        if owner_lab:
            # Placed outside the generator's radius, so it cannot alter coverage.
            buildings.append(FakeBuilding(building_type=owner_lab, planet=HOME, x=0, y=0))
        owner = FakePlayer(buildings=buildings, planet=HOME)
        ShieldSystem(self.registry, EventBus()).refresh(owner.get_buildings())
        vault = buildings[2]
        return owner, (vault.db.shield_max, vault.db.shield)

    def test_a_generator_shields_the_same_with_and_without_a_commitment(self):
        bare_owner, bare = self._refresh()
        committed_owner, committed = self._refresh(owner_lab=self.lab)
        self._assert_pair_differs(bare_owner, committed_owner, self.registry)

        self.assertEqual(bare, committed)
        # Non-vacuous: the generator really did project something.
        self.assertGreater(bare[0], 0)
        self.assertEqual(bare[1], bare[0])


if __name__ == "__main__":  # pragma: no cover - manual runs
    unittest.main()
