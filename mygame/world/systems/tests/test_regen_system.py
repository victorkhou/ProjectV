"""
Unit tests for RegenSystem — passive HP regen for players and agents.

Covers: interval gating, percent-of-max healing, hp_max cap, skipping
dead/incapacitated/full entities, fractional accumulation for sub-1-HP rates,
the per-entity regen_multiplier hook, injected modifier providers, and the
disabled (0%) config.
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

from mygame.world.systems.regen_system import RegenSystem  # noqa: E402
from mygame.world.definitions import BalanceConfig  # noqa: E402
from mygame.world.event_bus import EventBus  # noqa: E402


class _DB:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Entity:
    """Minimal CombatEntity stand-in with a db bag."""
    def __init__(self, hp=50, hp_max=100, incapacitated=False,
                 regen_multiplier=None):
        self.key = "Ent"
        self.db = _DB(hp=hp, hp_max=hp_max, incapacitated=incapacitated)
        if regen_multiplier is not None:
            self.db.regen_multiplier = regen_multiplier


class _Registry:
    def __init__(self, balance=None):
        self.balance = balance or BalanceConfig()


def _make(percent=1.0, interval=2):
    balance = BalanceConfig()
    balance.hp_regen_percent = percent
    balance.hp_regen_interval_ticks = interval
    return RegenSystem(_Registry(balance), EventBus())


class TestRegenBasics(unittest.TestCase):
    def test_heals_percent_of_max_on_interval_tick(self):
        # 1% of 100 = 1 HP, interval 2 -> applies on tick % 2 == 0.
        system = _make(percent=1.0, interval=2)
        ent = _Entity(hp=50, hp_max=100)
        system.process_tick([ent], tick_number=2)
        self.assertEqual(ent.db.hp, 51)

    def test_no_heal_off_interval(self):
        system = _make(percent=1.0, interval=2)
        ent = _Entity(hp=50, hp_max=100)
        system.process_tick([ent], tick_number=3)  # 3 % 2 != 0
        self.assertEqual(ent.db.hp, 50)

    def test_caps_at_hp_max(self):
        system = _make(percent=50.0, interval=1)  # 50 HP/tick
        ent = _Entity(hp=90, hp_max=100)
        system.process_tick([ent], tick_number=1)
        self.assertEqual(ent.db.hp, 100)  # not 140

    def test_full_hp_entity_skipped(self):
        system = _make(percent=10.0, interval=1)
        ent = _Entity(hp=100, hp_max=100)
        system.process_tick([ent], tick_number=1)
        self.assertEqual(ent.db.hp, 100)

    def test_dead_entity_not_regenerated(self):
        system = _make(percent=10.0, interval=1)
        ent = _Entity(hp=0, hp_max=100)
        system.process_tick([ent], tick_number=1)
        self.assertEqual(ent.db.hp, 0)  # revives via respawn, not regen

    def test_incapacitated_entity_not_regenerated(self):
        system = _make(percent=10.0, interval=1)
        ent = _Entity(hp=20, hp_max=100, incapacitated=True)
        system.process_tick([ent], tick_number=1)
        self.assertEqual(ent.db.hp, 20)

    def test_accumulator_reset_at_cap_no_burst_after_damage(self):
        """Surplus is dropped when capping, so a later hit can't burst-heal.

        Regression for the reviewer-flagged gap: if the accumulator retained
        the surplus at cap instead of resetting to 0.0, a subsequent damage
        event would let a big banked remainder heal back instantly.
        """
        system = _make(percent=50.0, interval=1)  # 50 HP/interval
        ent = _Entity(hp=90, hp_max=100)
        system.process_tick([ent], tick_number=1)  # 90 -> cap 100, surplus 40
        self.assertEqual(ent.db.hp, 100)
        # The surplus must NOT be banked (else it would be ~40).
        self.assertEqual(ent.db.hp_regen_accumulator, 0.0)
        # Take damage, then regen one interval: heals only one interval's worth
        # (50), not 90 (= 50 + a retained 40 surplus).
        ent.db.hp = 20
        system.process_tick([ent], tick_number=2)
        self.assertEqual(ent.db.hp, 70)


class TestShouldRegenThisTick(unittest.TestCase):
    """The interval/enabled gate the tick loop consults before enumerating."""

    def test_true_on_interval_boundary(self):
        system = _make(percent=1.0, interval=2)
        self.assertTrue(system.should_regen_this_tick(4))

    def test_false_off_interval(self):
        system = _make(percent=1.0, interval=2)
        self.assertFalse(system.should_regen_this_tick(3))

    def test_false_when_percent_disabled(self):
        system = _make(percent=0.0, interval=2)
        self.assertFalse(system.should_regen_this_tick(4))

    def test_false_when_interval_non_positive(self):
        system = _make(percent=1.0, interval=0)
        self.assertFalse(system.should_regen_this_tick(4))


class TestFractionalAccumulation(unittest.TestCase):
    def test_sub_one_hp_rate_accumulates_then_heals(self):
        # 0.5% of 100 = 0.5 HP/interval; interval 1 for simplicity.
        system = _make(percent=0.5, interval=1)
        ent = _Entity(hp=50, hp_max=100)
        system.process_tick([ent], tick_number=1)
        self.assertEqual(ent.db.hp, 50)  # 0.5 banked, no whole HP yet
        system.process_tick([ent], tick_number=2)
        self.assertEqual(ent.db.hp, 51)  # 0.5 + 0.5 = 1.0 applied

    def test_remainder_carried_across_applications(self):
        # 1.5 HP/interval -> +1 now with 0.5 banked, +2 next (0.5+1.5=2.0).
        system = _make(percent=1.5, interval=1)
        ent = _Entity(hp=50, hp_max=100)
        system.process_tick([ent], tick_number=1)
        self.assertEqual(ent.db.hp, 51)
        system.process_tick([ent], tick_number=2)
        self.assertEqual(ent.db.hp, 53)


class TestModifiers(unittest.TestCase):
    def test_per_entity_multiplier_scales_rate(self):
        system = _make(percent=1.0, interval=1)  # base 1 HP
        ent = _Entity(hp=50, hp_max=100, regen_multiplier=3.0)
        system.process_tick([ent], tick_number=1)
        self.assertEqual(ent.db.hp, 53)  # 1 * 3

    def test_zero_multiplier_disables_for_entity(self):
        system = _make(percent=10.0, interval=1)
        ent = _Entity(hp=50, hp_max=100, regen_multiplier=0.0)
        system.process_tick([ent], tick_number=1)
        self.assertEqual(ent.db.hp, 50)

    def test_injected_modifier_provider_applies(self):
        system = _make(percent=1.0, interval=1)
        # A "heal-rate tech" style provider doubling regen for everyone.
        system.add_modifier_provider(lambda e: 2.0)
        ent = _Entity(hp=50, hp_max=100)
        system.process_tick([ent], tick_number=1)
        self.assertEqual(ent.db.hp, 52)

    def test_provider_and_entity_multiplier_compound(self):
        system = _make(percent=1.0, interval=1)
        system.add_modifier_provider(lambda e: 2.0)
        ent = _Entity(hp=50, hp_max=100, regen_multiplier=2.0)
        system.process_tick([ent], tick_number=1)
        self.assertEqual(ent.db.hp, 54)  # 1 * 2 * 2


# -------------------------------------------------------------- #
#  Field Hospital HEAL_AURA tile bonus (item-loot-economy task 6.3)
# -------------------------------------------------------------- #

class _FakeBuildingDef:
    """BuildingDef stand-in exposing has_capability."""

    def __init__(self, capabilities=()):
        self._caps = frozenset(capabilities)

    def has_capability(self, cap):
        return cap in self._caps


class _AuraRegistry(_Registry):
    """Registry fake that also resolves building defs (FH = heal_aura)."""

    _DEFS = {
        "FH": _FakeBuildingDef({"heal_aura", "upgradable"}),
        "MB": _FakeBuildingDef(()),
    }

    def resolve_building(self, btype):
        return self._DEFS.get(btype)


class _AuraRoom:
    """PlanetRoom-shaped fake answering ``get_buildings_at`` — the tile
    read ``_tile_heal_bonus`` mirrors from the Sniper Nest / Watchtower."""

    def __init__(self):
        self._at = {}

    def place(self, x, y, building):
        self._at.setdefault((x, y), []).append(building)

    def get_buildings_at(self, x, y):
        return list(self._at.get((x, y), []))


class _FakeHospital:
    """Field Hospital building fake; attributes read through the db bag."""

    def __init__(self, btype="FH", owner=None, level=1, offline=False,
                 under_construction=False):
        self.db = _DB(
            building_type=btype,
            owner=owner,
            offline=offline,
            under_construction=under_construction,
        )
        self.building_level = level


def _aura_make(percent=1.0, interval=1):
    """A RegenSystem whose registry resolves the FH (heal_aura) def."""
    balance = BalanceConfig()
    balance.hp_regen_percent = percent
    balance.hp_regen_interval_ticks = interval
    return RegenSystem(_AuraRegistry(balance), EventBus())


def _aura_entity(room, x, y, oid=1, hp=50, hp_max=100, owner=None,
                 regen_multiplier=None):
    """A player/agent standing on tile (x, y) of *room*, with a stable id."""
    ent = _Entity(hp=hp, hp_max=hp_max, regen_multiplier=regen_multiplier)
    ent.id = oid
    ent.location = room
    ent.db.coord_x = x
    ent.db.coord_y = y
    if owner is not None:
        ent.db.owner = owner  # agent shape: attributed to its owning player
    return ent


class TestFieldHospitalHealAura(unittest.TestCase):
    """R10.3 (item-loot-economy task 6.3): the Field Hospital HEAL_AURA.

    ``_tile_heal_bonus`` grants ``1 + (level-1)//2`` extra HP per regen
    interval only while the entity's owning player stands on their OWN,
    OPERATIONAL heal-aura building's tile — on-tile only and owner-only,
    mirroring the Sniper Nest / Watchtower auras. It rides the regen
    machinery, so the hp_max clamp and interval gating apply unchanged.
    """

    def test_owner_on_tile_heals_extra_each_interval(self):
        # Base regen 1% of 100 = 1 HP; FH L1 adds +1 → 2 HP per interval.
        system = _aura_make(percent=1.0, interval=1)
        room = _AuraRoom()
        owner = _aura_entity(room, 5, 5, hp=50)
        room.place(5, 5, _FakeHospital(owner=owner, level=1))
        system.process_tick([owner], tick_number=1)
        self.assertEqual(owner.db.hp, 52)
        system.process_tick([owner], tick_number=2)
        self.assertEqual(owner.db.hp, 54)

    def test_no_heal_bonus_off_tile(self):
        """One tile off the hospital → base regen only (no adjacency)."""
        system = _aura_make(percent=1.0, interval=1)
        room = _AuraRoom()
        owner = _aura_entity(room, 6, 5, hp=50)
        room.place(5, 5, _FakeHospital(owner=owner, level=5))
        system.process_tick([owner], tick_number=1)
        self.assertEqual(owner.db.hp, 51)  # base 1 HP, no +3

    def test_someone_elses_hospital_grants_nothing(self):
        """Standing on ANOTHER player's hospital: base regen only."""
        system = _aura_make(percent=1.0, interval=1)
        room = _AuraRoom()
        builder = _aura_entity(room, 0, 0, oid=2)
        intruder = _aura_entity(room, 5, 5, oid=1, hp=50)
        room.place(5, 5, _FakeHospital(owner=builder, level=5))
        system.process_tick([intruder], tick_number=1)
        self.assertEqual(intruder.db.hp, 51)

    def test_level_scaling_plus_one_to_plus_three(self):
        system = _aura_make(percent=1.0, interval=1)
        for level, bonus in ((1, 1), (2, 1), (3, 2), (4, 2), (5, 3)):
            room = _AuraRoom()
            owner = _aura_entity(room, 0, 0, hp=50)
            room.place(0, 0, _FakeHospital(owner=owner, level=level))
            system.process_tick([owner], tick_number=1)
            self.assertEqual(
                owner.db.hp, 50 + 1 + bonus,
                f"level {level} should grant +{bonus} on top of base regen",
            )

    def test_non_operational_hospital_inert(self):
        """Offline or mid-upgrade/construction hospitals heal nothing extra."""
        system = _aura_make(percent=1.0, interval=1)
        room = _AuraRoom()
        owner = _aura_entity(room, 0, 0, hp=50)
        room.place(0, 0, _FakeHospital(owner=owner, level=3, offline=True))
        system.process_tick([owner], tick_number=1)
        self.assertEqual(owner.db.hp, 51)

        room2 = _AuraRoom()
        owner2 = _aura_entity(room2, 0, 0, hp=50)
        room2.place(0, 0, _FakeHospital(owner=owner2, level=3,
                                        under_construction=True))
        system.process_tick([owner2], tick_number=1)
        self.assertEqual(owner2.db.hp, 51)

    def test_never_exceeds_hp_max(self):
        system = _aura_make(percent=1.0, interval=1)
        room = _AuraRoom()
        owner = _aura_entity(room, 0, 0, hp=99)
        room.place(0, 0, _FakeHospital(owner=owner, level=5))
        system.process_tick([owner], tick_number=1)
        self.assertEqual(owner.db.hp, 100)  # not 103
        self.assertEqual(owner.db.hp_regen_accumulator, 0.0)  # no banked burst

    def test_full_hp_owner_not_touched(self):
        system = _aura_make(percent=1.0, interval=1)
        room = _AuraRoom()
        owner = _aura_entity(room, 0, 0, hp=100)
        room.place(0, 0, _FakeHospital(owner=owner, level=5))
        system.process_tick([owner], tick_number=1)
        self.assertEqual(owner.db.hp, 100)

    def test_dead_and_incapacitated_not_healed(self):
        """The aura rides regen, so its dead/incapacitated skip applies."""
        system = _aura_make(percent=1.0, interval=1)
        room = _AuraRoom()
        dead = _aura_entity(room, 0, 0, hp=0)
        room.place(0, 0, _FakeHospital(owner=dead, level=5))
        system.process_tick([dead], tick_number=1)
        self.assertEqual(dead.db.hp, 0)

        room2 = _AuraRoom()
        downed = _aura_entity(room2, 0, 0, hp=20)
        downed.db.incapacitated = True
        room2.place(0, 0, _FakeHospital(owner=downed, level=5))
        system.process_tick([downed], tick_number=1)
        self.assertEqual(downed.db.hp, 20)

    def test_off_interval_tick_heals_nothing(self):
        """The aura obeys the regen interval cadence (HP per interval)."""
        system = _aura_make(percent=1.0, interval=2)
        room = _AuraRoom()
        owner = _aura_entity(room, 0, 0, hp=50)
        room.place(0, 0, _FakeHospital(owner=owner, level=1))
        system.process_tick([owner], tick_number=3)  # 3 % 2 != 0
        self.assertEqual(owner.db.hp, 50)
        system.process_tick([owner], tick_number=4)
        self.assertEqual(owner.db.hp, 52)

    def test_owners_agent_on_tile_benefits(self):
        """An owner's AGENT (db.owner) on the tile heals too — the Sniper
        Nest attribution shape."""
        system = _aura_make(percent=1.0, interval=1)
        room = _AuraRoom()
        player = _aura_entity(room, 9, 9, oid=1)
        agent = _aura_entity(room, 0, 0, oid=7, hp=50, owner=player)
        room.place(0, 0, _FakeHospital(owner=player, level=1))
        system.process_tick([agent], tick_number=1)
        self.assertEqual(agent.db.hp, 52)

    def test_zero_regen_multiplier_does_not_switch_hospital_off(self):
        """The flat aura is additive, NOT scaled by regen_multiplier — a
        zeroed metabolic regen still gets the facility's heal."""
        system = _aura_make(percent=1.0, interval=1)
        room = _AuraRoom()
        owner = _aura_entity(room, 0, 0, hp=50, regen_multiplier=0.0)
        room.place(0, 0, _FakeHospital(owner=owner, level=1))
        system.process_tick([owner], tick_number=1)
        self.assertEqual(owner.db.hp, 51)  # +1 aura, no base regen

    def test_non_aura_building_grants_nothing(self):
        """Standing on an owned building WITHOUT heal_aura (a Medbay) → base."""
        system = _aura_make(percent=1.0, interval=1)
        room = _AuraRoom()
        owner = _aura_entity(room, 0, 0, hp=50)
        room.place(0, 0, _FakeHospital(btype="MB", owner=owner, level=5))
        system.process_tick([owner], tick_number=1)
        self.assertEqual(owner.db.hp, 51)

    def test_empty_tile_and_missing_location_never_raise(self):
        """No building, no location, unknown types — always base regen."""
        system = _aura_make(percent=1.0, interval=1)
        room = _AuraRoom()
        on_empty = _aura_entity(room, 0, 0, hp=50)
        system.process_tick([on_empty], tick_number=1)
        self.assertEqual(on_empty.db.hp, 51)
        # Plain entity with no location/coords at all (regression: plain
        # regen unchanged by the aura wiring).
        plain = _Entity(hp=50, hp_max=100)
        system.process_tick([plain], tick_number=1)
        self.assertEqual(plain.db.hp, 51)

    def test_corrupted_building_level_none_degrades_to_zero(self):
        """A hospital whose level reads None (corrupted data) grants no aura
        (base regen only) rather than raising out of the regen tick — the
        shared tile_aura_level helper's full-body guard absorbs int(None)."""
        system = _aura_make(percent=1.0, interval=1)
        room = _AuraRoom()
        owner = _aura_entity(room, 0, 0, hp=50)
        room.place(0, 0, _FakeHospital(owner=owner, level=None))
        system.process_tick([owner], tick_number=1)
        self.assertEqual(owner.db.hp, 51)  # base 1 HP, no aura, no exception


class TestDisabled(unittest.TestCase):
    def test_zero_percent_disables_regen(self):
        system = _make(percent=0.0, interval=2)
        ent = _Entity(hp=50, hp_max=100)
        system.process_tick([ent], tick_number=2)
        self.assertEqual(ent.db.hp, 50)

    def test_zero_interval_disables_regen(self):
        system = _make(percent=1.0, interval=0)
        ent = _Entity(hp=50, hp_max=100)
        system.process_tick([ent], tick_number=0)
        self.assertEqual(ent.db.hp, 50)

    def test_empty_entities_is_noop(self):
        system = _make()
        system.process_tick([], tick_number=2)  # must not raise
        system.process_tick(None, tick_number=2)  # tolerate None


if __name__ == "__main__":
    unittest.main()
