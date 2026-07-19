"""
Unit tests for ShieldSystem — Shield Generator building shields.

Covers: coverage radius by level, shield_max = fraction x level x hp_max,
overlap-takes-max (no stacking), per-owner and per-planet scope, new-capacity-
comes-online-charged, clamp-down on lost capacity, and interval-gated regen.
"""

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
    _mod("evennia.utils")
    _mod("evennia.utils.utils")
    _mod("evennia.utils.logger")
    for name, mod in stubs.items():
        sys.modules.setdefault(name, mod)


_ensure_evennia_stubs()

from mygame.world.systems.shield_system import ShieldSystem  # noqa: E402
from mygame.world.definitions import BalanceConfig, BuildingDef  # noqa: E402
from mygame.world.data_registry import DataRegistry  # noqa: E402
from mygame.world.event_bus import EventBus  # noqa: E402


class _DB:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def __getattr__(self, name):
        return None


class _Owner:
    _next = 1

    def __init__(self):
        self.id = _Owner._next
        _Owner._next += 1


class _Building:
    """Minimal building stand-in: a typed db bag with coords/owner/planet."""
    def __init__(self, btype, x, y, owner, planet="earth", level=1,
                 hp_max=400, shield=0, shield_max=0):
        self.key = f"{btype}-{x}-{y}"
        self.id = id(self)
        self.db = _DB(
            building_type=btype, coord_x=x, coord_y=y, coord_planet=planet,
            owner=owner, building_level=level, hp_max=hp_max, hp=hp_max,
            shield=shield, shield_max=shield_max,
        )


def _registry():
    """Real DataRegistry with just the two building types the tests use, so
    building_has_capability(SG) resolves through the registry provider."""
    reg = DataRegistry()
    reg.balance = BalanceConfig()
    reg.buildings = {
        "SG": BuildingDef(
            name="Shield Generator", abbreviation="SG", cost={},
            max_health=200, requires_hq=True, required_terrain=None,
            category="defense", produces=None,
            capabilities=frozenset({"shield_generator", "upgradable"}),
        ),
        "VT": BuildingDef(
            name="Vault", abbreviation="VT", cost={},
            max_health=400, requires_hq=True, required_terrain=None,
            category="utility", produces=None,
            capabilities=frozenset({"storage"}),
        ),
    }
    return reg


def _system():
    return ShieldSystem(_registry(), EventBus())


class TestShieldCoverage(unittest.TestCase):
    def test_l1_covers_radius_2_and_shields_25pct(self):
        sys_ = _system()
        owner = _Owner()
        gen = _Building("SG", 5, 5, owner, level=1, hp_max=200)
        vault = _Building("VT", 7, 5, owner, hp_max=400)  # Chebyshev dist 2 → covered
        sys_.refresh([gen, vault])
        # 25% x level(1) x 400 = 100.
        self.assertEqual(vault.db.shield_max, 100)
        # New capacity powers on charged.
        self.assertEqual(vault.db.shield, 100)

    def test_building_outside_radius_gets_no_shield(self):
        sys_ = _system()
        owner = _Owner()
        gen = _Building("SG", 5, 5, owner, level=1)
        far = _Building("VT", 8, 5, owner, hp_max=400)  # dist 3 > radius 2
        sys_.refresh([gen, far])
        self.assertEqual(far.db.shield_max, 0)
        self.assertEqual(far.db.shield, 0)

    def test_radius_grows_with_level(self):
        sys_ = _system()
        owner = _Owner()
        gen = _Building("SG", 5, 5, owner, level=2)  # radius 3
        edge = _Building("VT", 8, 5, owner, hp_max=400)  # dist 3 → now covered
        sys_.refresh([gen, edge])
        # 25% x level(2) x 400 = 200.
        self.assertEqual(edge.db.shield_max, 200)

    def test_shield_scales_with_level(self):
        sys_ = _system()
        owner = _Owner()
        gen = _Building("SG", 5, 5, owner, level=4, hp_max=200)
        vault = _Building("VT", 5, 5, owner, hp_max=400)
        sys_.refresh([gen, vault])
        # L4 → 100% of hp_max.
        self.assertEqual(vault.db.shield_max, 400)


class TestOverlapAndScope(unittest.TestCase):
    def test_overlap_takes_max_not_sum(self):
        sys_ = _system()
        owner = _Owner()
        g1 = _Building("SG", 5, 5, owner, level=1)   # would give 100
        g2 = _Building("SG", 6, 5, owner, level=3)   # would give 300
        vault = _Building("VT", 5, 5, owner, hp_max=400)
        sys_.refresh([g1, g2, vault])
        # Max, not 100+300.
        self.assertEqual(vault.db.shield_max, 300)

    def test_generator_does_not_shield_other_players(self):
        sys_ = _system()
        a, b = _Owner(), _Owner()
        gen = _Building("SG", 5, 5, a, level=2)
        theirs = _Building("VT", 5, 5, b, hp_max=400)  # same tile, different owner
        sys_.refresh([gen, theirs])
        self.assertEqual(theirs.db.shield_max, 0)

    def test_generator_does_not_shield_other_planet(self):
        sys_ = _system()
        owner = _Owner()
        gen = _Building("SG", 5, 5, owner, planet="earth", level=2)
        elsewhere = _Building("VT", 5, 5, owner, planet="mars", hp_max=400)
        sys_.refresh([gen, elsewhere])
        self.assertEqual(elsewhere.db.shield_max, 0)


class TestCapacityChanges(unittest.TestCase):
    def test_lost_generator_clamps_shield_down(self):
        sys_ = _system()
        owner = _Owner()
        gen = _Building("SG", 5, 5, owner, level=1)
        vault = _Building("VT", 5, 5, owner, hp_max=400)
        sys_.refresh([gen, vault])
        self.assertEqual(vault.db.shield, 100)
        # Generator gone: refresh without it → shield_max 0, shield clamped.
        sys_.refresh([vault])
        self.assertEqual(vault.db.shield_max, 0)
        self.assertEqual(vault.db.shield, 0)

    def test_static_refresh_does_not_refill_drained_shield(self):
        sys_ = _system()
        owner = _Owner()
        gen = _Building("SG", 5, 5, owner, level=1)
        vault = _Building("VT", 5, 5, owner, hp_max=400)
        sys_.refresh([gen, vault])
        vault.db.shield = 20  # drained by combat
        # A periodic refresh with the SAME layout must NOT top it back up.
        sys_.refresh([gen, vault])
        self.assertEqual(vault.db.shield, 20)
        self.assertEqual(vault.db.shield_max, 100)


class TestRegen(unittest.TestCase):
    def test_regen_on_interval_only(self):
        sys_ = _system()
        b = _Building("VT", 5, 5, _Owner(), hp_max=400, shield=50, shield_max=100)
        # interval default 5 → tick 4 no-op, tick 5 heals.
        sys_.process_tick([b], tick_number=4)
        self.assertEqual(b.db.shield, 50)
        sys_.process_tick([b], tick_number=5)
        # 1% of 100 = 1.
        self.assertEqual(b.db.shield, 51)

    def test_regen_caps_at_shield_max(self):
        sys_ = _system()
        reg = _registry()
        reg.balance.shield_regen_percent = 50.0
        reg.balance.shield_regen_interval_ticks = 1
        s = ShieldSystem(reg, EventBus())
        b = _Building("VT", 5, 5, _Owner(), hp_max=400, shield=90, shield_max=100)
        s.process_tick([b], tick_number=1)  # +50 capped
        self.assertEqual(b.db.shield, 100)

    def test_unshielded_building_never_regens(self):
        sys_ = _system()
        b = _Building("VT", 5, 5, _Owner(), hp_max=400, shield=0, shield_max=0)
        sys_.process_tick([b], tick_number=5)
        self.assertEqual(b.db.shield, 0)


if __name__ == "__main__":
    unittest.main()
