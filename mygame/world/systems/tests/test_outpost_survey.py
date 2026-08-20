"""
Unit tests for OutpostSurveySystem — the Survey Array triangulation search.

Covers the bench gates (capability / ownership / operational), the deduct-first
resource charge, target selection scoped to the player's planet and filtered by
what they have already discovered, search-box geometry (contains the target,
clamped to planet bounds, level-scaled opening size), narrowing, probe bearings
and distance bands, the two pinpoint routes (probe close enough, or narrow to a
single tile), fog-of-war marking on a pinpoint, and contract persistence.
"""

import random
import sys
import types
import unittest


def _ensure_evennia_stubs():
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

from mygame.world.constants import OUTPOST_SURVEY  # noqa: E402
from mygame.world.definitions import BalanceConfig, BuildingDef  # noqa: E402
from mygame.world.event_bus import EventBus, PLAYER_NOTIFICATION  # noqa: E402
from mygame.world.systems.outpost_survey import (  # noqa: E402
    DISTANCE_BAND_FAINT, DISTANCE_BANDS, SURVEY_ATTR, OutpostSurveySystem,
)


# -------------------------------------------------------------- #
#  Fakes
# -------------------------------------------------------------- #

class FakeRegistry:
    """Minimal DefinitionsProvider + balance holder."""

    def __init__(self, balance=None, survey_capable=True):
        self.balance = balance or BalanceConfig()
        self._defs = {
            "SA": BuildingDef(
                name="Survey Array", abbreviation="SA", cost={}, max_health=200,
                requires_hq=True, required_terrain=None,
                category="intelligence", produces=None,
                capabilities=frozenset({OUTPOST_SURVEY} if survey_capable else set()),
            ),
            "HQ": BuildingDef(
                name="Headquarters", abbreviation="HQ", cost={}, max_health=500,
                requires_hq=False, required_terrain=None,
                category="headquarters", produces=None,
            ),
        }

    def resolve_building(self, token):
        return self._defs.get(str(token).upper())


class FakeBuilding:
    """A Survey Array stand-in owned by a player."""

    def __init__(self, owner, btype="SA", level=1, offline=False,
                 under_construction=False):
        self.owner = owner
        self.is_offline = offline
        self.db = types.SimpleNamespace(
            building_type=btype, building_level=level,
            under_construction=under_construction, owner=owner,
        )


class FakeSpace:
    def __init__(self, width=100, height=100):
        self.width = width
        self.height = height


def _bounds_provider(spaces):
    """``(planet) -> (width, height)``, the shape the system injects."""
    spaces = spaces or {"terra": FakeSpace()}

    def _bounds(planet):
        space = spaces.get(planet)
        return (space.width, space.height) if space else None

    return _bounds


class FakeFog:
    """Fog stand-in exposing only what the survey reads and writes.

    ``known`` holds the tiles the player already has an enemy-building snapshot
    for — the signal the candidate filter must use (NOT bare tile discovery).
    """

    def __init__(self, known=()):
        self._known = set(known)
        self.marked = []

    def get_discovered_buildings(self, player, x, y):
        return [object()] if (x, y) in self._known else []

    def remember_building(self, player, x, y, building_type, owner_name):
        self.marked.append((x, y, building_type, owner_name))
        return True


class FakePlayer:
    def __init__(self, planet="terra", resources=None, key="Raider"):
        self.key = key
        self.db = types.SimpleNamespace(
            coord_planet=planet, coord_x=0, coord_y=0,
        )
        setattr(self.db, SURVEY_ATTR, None)
        self._resources = dict(resources if resources is not None
                               else {"Energy": 500, "Circuits": 500})

    def get_resource(self, res):
        return int(self._resources.get(res.title(), 0))

    def has_resources(self, costs):
        return all(self.get_resource(r) >= a for r, a in costs.items())

    def deduct_resources(self, costs):
        if not self.has_resources(costs):
            return False
        for r, a in costs.items():
            self._resources[r.title()] = self.get_resource(r) - a
        return True

    def add_resource(self, res, amount):
        self._resources[res.title()] = self.get_resource(res) + amount


class Sink:
    """Captures PLAYER_NOTIFICATION events."""

    def __init__(self, bus):
        self.events = []
        bus.subscribe(PLAYER_NOTIFICATION, self._on)

    def _on(self, event_name="", player=None, kind="", data=None, **kw):
        self.events.append((player, kind, data or {}))

    def kinds(self):
        return [kind for _p, kind, _d in self.events]

    def last(self):
        return self.events[-1] if self.events else (None, None, {})

    def last_data(self):
        return self.last()[2]

    def reasons(self):
        return [d.get("reason") for _p, _k, d in self.events]


def _make(outposts=None, discovered=(), balance=None, survey_capable=True,
          seed=1234, spaces=None, locator_raises=False):
    """Build a system plus its collaborators.

    Returns ``(system, sink, fog)``. The live base list is mutable via
    ``system._bases_list`` so a test can wipe a target mid-search.
    """
    bus = EventBus()
    sink = Sink(bus)
    fog = FakeFog(discovered)
    registry = FakeRegistry(balance=balance, survey_capable=survey_capable)
    bases = list(outposts if outposts is not None else [
        {"key": 1, "name": "Outpost", "x": 50, "y": 50},
    ])

    def _locate(planet):
        if locator_raises:
            raise RuntimeError("spawner unavailable")
        return bases if planet == "terra" else []

    system = OutpostSurveySystem(
        registry, bus,
        outposts_provider=_locate,
        fog_provider=lambda: fog,
        bounds_provider=_bounds_provider(spaces),
        rng=random.Random(seed),
    )
    system._bases_list = bases
    return system, sink, fog


def _contract(player):
    return getattr(player.db, SURVEY_ATTR, None)


# -------------------------------------------------------------- #
#  Bench gates
# -------------------------------------------------------------- #

class TestBenchGates(unittest.TestCase):
    def test_no_building_is_wrong_building(self):
        system, sink, _ = _make()
        player = FakePlayer()
        self.assertFalse(system.scan(player, None))
        self.assertEqual(sink.reasons(), ["wrong_building"])
        self.assertIsNone(_contract(player))

    def test_building_without_capability_is_wrong_building(self):
        system, sink, _ = _make()
        player = FakePlayer()
        self.assertFalse(system.scan(player, FakeBuilding(player, btype="HQ")))
        self.assertEqual(sink.reasons(), ["wrong_building"])

    def test_someone_elses_array_is_refused(self):
        system, sink, _ = _make()
        player, other = FakePlayer(), FakePlayer(key="Rival")
        self.assertFalse(system.scan(player, FakeBuilding(other)))
        self.assertEqual(sink.reasons(), ["not_owner"])

    def test_offline_array_is_refused(self):
        system, sink, _ = _make()
        player = FakePlayer()
        self.assertFalse(system.scan(player, FakeBuilding(player, offline=True)))
        self.assertEqual(sink.reasons(), ["building_offline"])

    def test_upgrading_array_is_refused(self):
        system, sink, _ = _make()
        player = FakePlayer()
        self.assertFalse(
            system.scan(player, FakeBuilding(player, under_construction=True))
        )
        self.assertEqual(sink.reasons(), ["building_upgrading"])

    def test_gates_run_before_any_charge(self):
        system, sink, _ = _make()
        player = FakePlayer()
        before = dict(player._resources)
        system.scan(player, FakeBuilding(player, btype="HQ"))
        self.assertEqual(player._resources, before)


# -------------------------------------------------------------- #
#  Opening a survey
# -------------------------------------------------------------- #

class TestScan(unittest.TestCase):
    def test_scan_opens_a_contract_and_charges(self):
        system, sink, _ = _make()
        player = FakePlayer()
        before = player.get_resource("Energy")

        self.assertTrue(system.scan(player, FakeBuilding(player)))

        self.assertIn("survey_started", sink.kinds())
        contract = _contract(player)
        self.assertIsNotNone(contract)
        self.assertEqual((contract["tx"], contract["ty"]), (50, 50))
        self.assertEqual(contract["planet"], "terra")
        self.assertLess(player.get_resource("Energy"), before)

    def test_search_box_contains_the_target(self):
        system, sink, _ = _make()
        player = FakePlayer()
        system.scan(player, FakeBuilding(player))
        d = sink.last_data()
        self.assertLessEqual(d["x1"], 50)
        self.assertLessEqual(50, d["x2"])
        self.assertLessEqual(d["y1"], 50)
        self.assertLessEqual(50, d["y2"])
        self.assertEqual(
            d["tiles"], (d["x2"] - d["x1"] + 1) * (d["y2"] - d["y1"] + 1)
        )

    def test_box_is_not_always_centred_on_the_target(self):
        """The offset is random, so the centre must not be a free answer."""
        offsets = set()
        for seed in range(30):
            system, sink, _ = _make(seed=seed)
            player = FakePlayer()
            system.scan(player, FakeBuilding(player))
            d = sink.last_data()
            offsets.add(((d["x1"] + d["x2"]) // 2 - 50,
                         (d["y1"] + d["y2"]) // 2 - 50))
        self.assertGreater(len(offsets), 1)
        self.assertTrue(any(off != (0, 0) for off in offsets))

    def test_higher_level_array_opens_a_tighter_box(self):
        system, sink, _ = _make()
        boxes = {}
        for level in (1, 5):
            player = FakePlayer()
            system.scan(player, FakeBuilding(player, level=level))
            boxes[level] = sink.last_data()["tiles"]
        self.assertLess(boxes[5], boxes[1])

    def test_box_is_clamped_to_planet_bounds(self):
        system, sink, _ = _make(
            outposts=[{"key": 1, "name": "Outpost", "x": 1, "y": 1}],
            spaces={"terra": FakeSpace(width=20, height=20)},
        )
        player = FakePlayer()
        system.scan(player, FakeBuilding(player))
        d = sink.last_data()
        self.assertGreaterEqual(d["x1"], 0)
        self.assertGreaterEqual(d["y1"], 0)
        self.assertLessEqual(d["x2"], 19)
        self.assertLessEqual(d["y2"], 19)
        self.assertLessEqual(d["x1"], 1)
        self.assertLessEqual(1, d["x2"])

    def test_no_targets_when_planet_has_none(self):
        system, sink, _ = _make(outposts=[])
        player = FakePlayer()
        before = dict(player._resources)
        self.assertFalse(system.scan(player, FakeBuilding(player)))
        self.assertEqual(sink.reasons(), ["no_targets"])
        self.assertEqual(player._resources, before)

    def test_bases_already_known_are_not_offered(self):
        system, sink, _ = _make(
            outposts=[{"key": 1, "name": "Outpost", "x": 50, "y": 50}],
            discovered={(50, 50)},  # an enemy-building snapshot exists here
        )
        player = FakePlayer()
        self.assertFalse(system.scan(player, FakeBuilding(player)))
        self.assertEqual(sink.reasons(), ["no_targets"])

    def test_merely_walking_past_a_tile_does_not_hide_its_base(self):
        """The filter reads BUILDING memory, not bare tile discovery.

        Tile discovery is additive and never pruned, so filtering on it would
        permanently hide any base that later spawned on explored ground.
        """
        system, sink, fog = _make(
            outposts=[{"key": 1, "name": "Outpost", "x": 50, "y": 50}],
        )
        # Fog knows the tile was crossed, but holds no building snapshot.
        assert fog.get_discovered_buildings(None, 50, 50) == []
        player = FakePlayer()

        self.assertTrue(system.scan(player, FakeBuilding(player)))

        self.assertIn("survey_started", sink.kinds())

    def test_a_locator_failure_is_not_reported_as_a_swept_planet(self):
        system, sink, _ = _make(locator_raises=True)
        player = FakePlayer()
        before = dict(player._resources)

        self.assertFalse(system.scan(player, FakeBuilding(player)))

        self.assertEqual(sink.reasons(), ["lookup_failed"])
        self.assertEqual(player._resources, before)

    def test_fortress_tier_targets_are_in_scope_and_named(self):
        """Any tier may be targeted; the readout must name which one."""
        system, sink, _ = _make(
            outposts=[{"key": 9, "name": "Fortress", "x": 50, "y": 50}],
        )
        player = FakePlayer()
        self.assertTrue(system.scan(player, FakeBuilding(player)))
        self.assertEqual(sink.last_data()["name"], "Fortress")

    def test_only_current_planet_outposts_are_targeted(self):
        system, sink, _ = _make()
        player = FakePlayer(planet="mars")
        self.assertFalse(system.scan(player, FakeBuilding(player)))
        self.assertEqual(sink.reasons(), ["no_targets"])

    def test_second_scan_on_same_planet_is_refused(self):
        system, sink, _ = _make()
        player = FakePlayer()
        building = FakeBuilding(player)
        system.scan(player, building)
        spent = dict(player._resources)

        self.assertFalse(system.scan(player, building))

        self.assertEqual(sink.reasons()[-1], "already_active")
        self.assertEqual(player._resources, spent)

    def test_a_contract_from_another_planet_is_never_overwritten(self):
        """Silently replacing it would discard readings already paid for."""
        system, sink, _ = _make()
        player = FakePlayer()
        building = FakeBuilding(player)
        system.scan(player, building)
        original = dict(_contract(player))
        player.db.coord_planet = "mars"
        spent = dict(player._resources)

        self.assertFalse(system.scan(player, building))

        self.assertEqual(sink.reasons()[-1], "other_planet_active")
        self.assertEqual(_contract(player), original)
        self.assertEqual(player._resources, spent)

    def test_a_holder_with_no_resource_pool_cannot_pay(self):
        """The spend gate must fail CLOSED — never grant the effect free."""
        class _Poor:
            key = "Nomad"

            def __init__(self):
                self.db = types.SimpleNamespace(coord_planet="terra")
                setattr(self.db, SURVEY_ATTR, None)

        system, sink, _ = _make()
        player = _Poor()

        self.assertFalse(system.scan(player, FakeBuilding(player)))

        self.assertEqual(sink.reasons()[-1], "insufficient_resources")
        self.assertIsNone(getattr(player.db, SURVEY_ATTR))

    def test_insufficient_resources_opens_nothing(self):
        system, sink, _ = _make()
        player = FakePlayer(resources={"Energy": 0, "Circuits": 0})
        self.assertFalse(system.scan(player, FakeBuilding(player)))
        self.assertEqual(sink.reasons()[-1], "insufficient_resources")
        self.assertIsNone(_contract(player))

    def test_no_planet_is_reported(self):
        system, sink, _ = _make()
        player = FakePlayer(planet=None)
        self.assertFalse(system.scan(player, FakeBuilding(player)))
        self.assertEqual(sink.reasons(), ["no_position"])


# -------------------------------------------------------------- #
#  Status
# -------------------------------------------------------------- #

class TestStatus(unittest.TestCase):
    def test_status_without_contract_reports_inactive(self):
        system, sink, _ = _make()
        player = FakePlayer()
        self.assertFalse(system.status(player, FakeBuilding(player)))
        self.assertFalse(sink.last_data()["active"])

    def test_status_is_free(self):
        system, _, _ = _make()
        player = FakePlayer()
        building = FakeBuilding(player)
        system.scan(player, building)
        spent = dict(player._resources)

        self.assertTrue(system.status(player, building))

        self.assertEqual(player._resources, spent)

    def test_status_reports_the_tracked_box(self):
        system, sink, _ = _make()
        player = FakePlayer()
        building = FakeBuilding(player)
        system.scan(player, building)
        opened = sink.last_data()
        system.status(player, building)
        shown = sink.last_data()
        self.assertTrue(shown["active"])
        for field in ("x1", "y1", "x2", "y2", "tiles"):
            self.assertEqual(shown[field], opened[field])


# -------------------------------------------------------------- #
#  Narrowing
# -------------------------------------------------------------- #

class TestNarrow(unittest.TestCase):
    def test_narrow_requires_a_contract(self):
        system, sink, _ = _make()
        player = FakePlayer()
        self.assertFalse(system.narrow(player, FakeBuilding(player)))
        self.assertEqual(sink.reasons(), ["no_contract"])

    def test_narrow_shrinks_the_box_and_keeps_the_target(self):
        system, sink, _ = _make()
        player = FakePlayer()
        building = FakeBuilding(player)
        system.scan(player, building)
        before = sink.last_data()["tiles"]

        self.assertTrue(system.narrow(player, building))

        after = sink.last_data()
        self.assertLess(after["tiles"], before)
        self.assertLessEqual(after["x1"], 50)
        self.assertLessEqual(50, after["x2"])
        self.assertLessEqual(after["y1"], 50)
        self.assertLessEqual(50, after["y2"])

    def test_repeated_narrowing_pinpoints_the_outpost(self):
        system, sink, fog = _make()
        player = FakePlayer()
        building = FakeBuilding(player)
        system.scan(player, building)

        for _ in range(12):
            if _contract(player) is None:
                break
            system.narrow(player, building)

        self.assertIn("survey_found", sink.kinds())
        found = sink.last_data()
        self.assertEqual((found["x"], found["y"]), (50, 50))
        self.assertIsNone(_contract(player))
        self.assertEqual(fog.marked[-1][:2], (50, 50))

    def test_narrow_charges_per_sweep(self):
        system, _, _ = _make()
        player = FakePlayer()
        building = FakeBuilding(player)
        system.scan(player, building)
        before = player.get_resource("Energy")
        system.narrow(player, building)
        self.assertLess(player.get_resource("Energy"), before)

    def test_contract_from_another_planet_cannot_be_worked(self):
        system, sink, _ = _make()
        player = FakePlayer()
        building = FakeBuilding(player)
        system.scan(player, building)
        player.db.coord_planet = "mars"

        self.assertFalse(system.narrow(player, building))

        self.assertEqual(sink.reasons()[-1], "other_planet")

    def test_a_wiped_target_closes_the_search_without_charging(self):
        """Another raider clearing the base (or a staleness sweep) must not
        leave the player paying for readings against a phantom."""
        system, sink, _ = _make()
        player = FakePlayer()
        building = FakeBuilding(player)
        system.scan(player, building)
        system._bases_list.clear()  # the base is gone
        spent = dict(player._resources)

        self.assertFalse(system.narrow(player, building))

        self.assertEqual(sink.reasons()[-1], "target_lost")
        self.assertIsNone(_contract(player))
        self.assertEqual(player._resources, spent)

    def test_a_locator_outage_does_not_destroy_a_paid_search(self):
        """Target revalidation must fail OPEN — an intel outage is not proof
        the base is gone."""
        system, sink, _ = _make()
        player = FakePlayer()
        building = FakeBuilding(player)
        system.scan(player, building)
        contract = dict(_contract(player))

        system._outposts_provider = lambda planet: (_ for _ in ()).throw(
            RuntimeError("spawner down")
        )
        self.assertTrue(system.narrow(player, building))

        self.assertIsNotNone(_contract(player))
        self.assertEqual(_contract(player)["key"], contract["key"])


# -------------------------------------------------------------- #
#  Probing
# -------------------------------------------------------------- #

class TestProbe(unittest.TestCase):
    def _open(self, **kw):
        system, sink, fog = _make(**kw)
        player = FakePlayer()
        building = FakeBuilding(player)
        system.scan(player, building)
        return system, sink, fog, player, building

    def test_probe_requires_a_contract(self):
        system, sink, _ = _make()
        player = FakePlayer()
        self.assertFalse(system.probe(player, FakeBuilding(player), 1, 1))
        self.assertEqual(sink.reasons(), ["no_contract"])

    def test_probe_outside_the_box_is_refused_without_charge(self):
        system, sink, _fog, player, building = self._open()
        spent = dict(player._resources)

        self.assertFalse(system.probe(player, building, 999, 999))

        self.assertEqual(sink.reasons()[-1], "outside_box")
        self.assertEqual(player._resources, spent)

    def test_probe_reports_bearing_and_band(self):
        system, sink, _fog, player, building = self._open()
        box = None
        for _p, kind, data in sink.events:
            if kind == "survey_started":
                box = data
        # Probe a corner of the box that is not the target tile.
        px, py = box["x1"], box["y1"]
        if (px, py) == (50, 50):
            px, py = box["x2"], box["y2"]

        self.assertTrue(system.probe(player, building, px, py))

        data = sink.last_data()
        self.assertEqual((data["x"], data["y"]), (px, py))
        self.assertIn("bearing", data)
        self.assertIn("band", data)

    def test_walking_the_reported_bearing_closes_on_the_target(self):
        """The bearing is verified by MOVEMENT, not by restating the table.

        Asserting against a copy of the production sign-to-name map would pass
        even if north and south were inverted. Instead the reported bearing is
        resolved through the game's canonical direction vectors and applied: a
        correct bearing must strictly reduce the distance to the target.
        """
        # The single axis convention (north = +y), as CmdMove defines it.
        steps = {
            "north": (0, 1), "south": (0, -1),
            "east": (1, 0), "west": (-1, 0),
            "northeast": (1, 1), "northwest": (-1, 1),
            "southeast": (1, -1), "southwest": (-1, -1),
        }
        checked = 0
        for seed in range(25):
            system, sink, _fog = _make(seed=seed)
            player = FakePlayer()
            building = FakeBuilding(player)
            system.scan(player, building)
            box = sink.last_data()
            for px, py in ((box["x1"], box["y1"]), (box["x2"], box["y2"])):
                before = max(abs(50 - px), abs(50 - py))
                if before <= system._reveal_radius() + 1:
                    continue  # too close: a step could overshoot or pinpoint
                system.probe(player, building, px, py)
                data = sink.last_data()
                bearing = data.get("bearing")
                if bearing is None:
                    continue
                dx, dy = steps[bearing]
                after = max(abs(50 - (px + dx)), abs(50 - (py + dy)))
                self.assertLess(
                    after, before,
                    f"walking {bearing} from ({px},{py}) must close the gap",
                )
                checked += 1
                break
        self.assertGreater(checked, 0, "no usable probe tile was generated")

    def test_probe_band_sharpens_as_you_close_in(self):
        """Each band boundary reports its own label, in order, and anything
        past the last boundary reports the faint label."""
        system, _sink, _fog = _make()
        labels = [system._band(bound) for bound, _label in DISTANCE_BANDS]
        self.assertEqual(labels, [label for _b, label in DISTANCE_BANDS])

        beyond = DISTANCE_BANDS[-1][0] + 1
        self.assertEqual(system._band(beyond), DISTANCE_BAND_FAINT)

    def test_probe_on_the_target_pinpoints_and_marks_the_map(self):
        system, sink, fog, player, building = self._open()

        self.assertTrue(system.probe(player, building, 50, 50))

        self.assertIn("survey_found", sink.kinds())
        data = sink.last_data()
        self.assertEqual((data["x"], data["y"]), (50, 50))
        self.assertTrue(data["marked"])
        self.assertIsNone(_contract(player))
        self.assertEqual(fog.marked, [(50, 50, "HQ", "Outpost")])

    def test_probe_charges_less_than_a_sweep(self):
        balance = BalanceConfig()
        self.assertLess(
            sum(balance.survey_probe_cost.values()),
            sum(balance.survey_narrow_cost.values()),
        )

    def test_non_numeric_probe_is_rejected(self):
        system, sink, _fog, player, building = self._open()
        self.assertFalse(system.probe(player, building, "north", "east"))
        self.assertEqual(sink.reasons()[-1], "bad_coords")


# -------------------------------------------------------------- #
#  Abandon + persistence
# -------------------------------------------------------------- #

class TestAbandon(unittest.TestCase):
    def test_abandon_clears_the_contract(self):
        system, sink, _ = _make()
        player = FakePlayer()
        building = FakeBuilding(player)
        system.scan(player, building)

        self.assertTrue(system.abandon(player, building))

        self.assertIn("survey_abandoned", sink.kinds())
        self.assertIsNone(_contract(player))

    def test_abandon_without_a_contract_is_reported(self):
        system, sink, _ = _make()
        player = FakePlayer()
        self.assertFalse(system.abandon(player, FakeBuilding(player)))
        self.assertEqual(sink.reasons(), ["no_contract"])

    def test_abandon_then_scan_can_pick_a_fresh_target(self):
        system, sink, _ = _make()
        player = FakePlayer()
        building = FakeBuilding(player)
        system.scan(player, building)
        system.abandon(player, building)

        self.assertTrue(system.scan(player, building))

        self.assertIsNotNone(_contract(player))


class TestContractPersistence(unittest.TestCase):
    def test_true_coordinates_are_never_sent_to_the_player(self):
        """Only the pinpoint notification may carry the exact tile."""
        system, sink, _ = _make()
        player = FakePlayer()
        building = FakeBuilding(player)
        system.scan(player, building)
        system.status(player, building)
        system.narrow(player, building)

        for _p, kind, data in sink.events:
            if kind == "survey_found":
                continue
            self.assertNotIn("tx", data)
            self.assertNotIn("ty", data)

    def test_a_malformed_contract_is_treated_as_absent(self):
        system, sink, _ = _make()
        player = FakePlayer()
        setattr(player.db, SURVEY_ATTR, {"planet": "terra"})  # missing coords
        self.assertFalse(system.narrow(player, FakeBuilding(player)))
        self.assertEqual(sink.reasons(), ["no_contract"])

    def test_contract_is_stored_as_a_plain_dict(self):
        system, _, _ = _make()
        player = FakePlayer()
        system.scan(player, FakeBuilding(player))
        self.assertIsInstance(_contract(player), dict)


if __name__ == "__main__":
    unittest.main()
