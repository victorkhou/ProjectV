"""
Unit tests for EquipmentHandler.

Tests equip, unequip, get_equipped, get_all_equipped, get_stat_total,
get_slot_names, auto-unequip on occupied slot, and slot matching.

Requirements: 6.2, 6.17, 6.18
"""

import inspect
import sys
import types
import unittest

from hypothesis import given, settings
from hypothesis import strategies as st

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
    })
    _mod("evennia.commands")
    _mod("evennia.commands.cmdset")
    _mod("evennia.utils")
    _mod("evennia.utils.utils")
    _mod("evennia.utils.logger")

    for name, mod in stubs.items():
        sys.modules.setdefault(name, mod)

_ensure_evennia_stubs()

from mygame.world.systems.equipment_handler import EquipmentHandler  # noqa: E402

# -------------------------------------------------------------- #
#  Helpers
# -------------------------------------------------------------- #

class FakeItem:
    """Lightweight stand-in for a GameItem."""

    def __init__(
        self,
        key: str,
        slot: str,
        stat_modifiers: dict | None = None,
        weapon_type: str | None = None,
        **state,
    ):
        self.key = key
        self.slot = slot
        self.stat_modifiers = stat_modifiers or {}
        if weapon_type is not None:
            self.weapon_type = weapon_type
        for name, value in state.items():
            setattr(self, name, value)

    def get_stat(self, stat_name: str, default: float = 0) -> float:
        return float(self.stat_modifiers.get(stat_name, default))


class FakeCharacter:
    """Lightweight stand-in for a CombatCharacter (no Evennia DB)."""

    def __init__(self):
        self._equipment_slots = {}


# -------------------------------------------------------------- #
#  Tests
# -------------------------------------------------------------- #

class TestEquipBasic(unittest.TestCase):
    def test_equip_item_to_empty_slot(self):
        char = FakeCharacter()
        handler = EquipmentHandler(char)
        item = FakeItem(
            "assault_rifle", "weapon_ranged", {"damage": 25}, "ranged"
        )
        ok, msg = handler.equip(item)
        self.assertTrue(ok)
        self.assertIn("weapon_ranged", msg)
        self.assertIs(handler.get_equipped("weapon_ranged"), item)

    def test_equip_returns_false_for_no_slot(self):
        char = FakeCharacter()
        handler = EquipmentHandler(char)
        item = FakeItem("broken", "", {})
        ok, msg = handler.equip(item)
        self.assertFalse(ok)

    def test_equip_multiple_slots(self):
        char = FakeCharacter()
        handler = EquipmentHandler(char)
        weapon = FakeItem("rifle", "weapon_ranged", {"damage": 20}, "ranged")
        armor = FakeItem("vest", "armor", {"damage_reduction": 5})
        handler.equip(weapon)
        handler.equip(armor)
        self.assertIs(handler.get_equipped("weapon_ranged"), weapon)
        self.assertIs(handler.get_equipped("armor"), armor)


class TestAutoUnequip(unittest.TestCase):
    def test_equip_occupied_slot_replaces_item(self):
        char = FakeCharacter()
        handler = EquipmentHandler(char)
        old = FakeItem("knife", "weapon_melee", {"damage": 10}, "melee")
        new = FakeItem("sword", "weapon_melee", {"damage": 25}, "melee")
        handler.equip(old)
        handler.equip(new)
        self.assertIs(handler.get_equipped("weapon_melee"), new)

    def test_equip_occupied_slot_returns_success(self):
        char = FakeCharacter()
        handler = EquipmentHandler(char)
        old = FakeItem("knife", "weapon_melee", {"damage": 10}, "melee")
        new = FakeItem("sword", "weapon_melee", {"damage": 25}, "melee")
        handler.equip(old)
        ok, msg = handler.equip(new)
        self.assertTrue(ok)


class TestLegacyWeaponMigration(unittest.TestCase):
    def test_equipped_ranged_weapon_is_rekeyed_without_losing_state(self):
        char = FakeCharacter()
        item = FakeItem(
            "rifle",
            "weapon",
            {"damage": 25},
            "ranged",
            loaded=17,
            rolled_stats={"damage": 31.5},
            rarity="epic",
            affixes=[{"stat": "damage_bonus", "magnitude": 4}],
            inserts=[{"type": "damage_type", "value": "fire"}],
            damage_type="fire",
        )
        char._equipment_slots = {"weapon": item}
        handler = EquipmentHandler(char)

        equipped = handler.get_all_equipped()

        self.assertEqual(set(equipped), {"weapon_ranged"})
        self.assertIs(equipped["weapon_ranged"], item)
        self.assertEqual(item.slot, "weapon_ranged")
        self.assertEqual(item.loaded, 17)
        self.assertEqual(item.rolled_stats, {"damage": 31.5})
        self.assertEqual(item.rarity, "epic")
        self.assertEqual(
            item.affixes, [{"stat": "damage_bonus", "magnitude": 4}]
        )
        self.assertEqual(
            item.inserts, [{"type": "damage_type", "value": "fire"}]
        )
        self.assertEqual(item.damage_type, "fire")

    def test_equipped_melee_weapon_is_rekeyed(self):
        char = FakeCharacter()
        item = FakeItem("knife", "weapon", {"damage": 10}, "melee")
        char._equipment_slots = {"weapon": item}

        equipped = EquipmentHandler(char).get_all_equipped()

        self.assertEqual(equipped, {"weapon_melee": item})
        self.assertEqual(item.slot, "weapon_melee")

    def test_migration_is_idempotent(self):
        char = FakeCharacter()
        item = FakeItem("rifle", "weapon", weapon_type="ranged", loaded=9)
        char._equipment_slots = {"weapon": item}
        handler = EquipmentHandler(char)

        first = handler.get_all_equipped()
        persisted_after_first = dict(char._equipment_slots)
        second = handler.get_all_equipped()

        self.assertEqual(first, second)
        self.assertEqual(char._equipment_slots, persisted_after_first)
        self.assertIs(second["weapon_ranged"], item)
        self.assertEqual(item.loaded, 9)

    def test_canonical_destination_wins_conflict(self):
        char = FakeCharacter()
        canonical = FakeItem("new_rifle", "weapon_ranged", weapon_type="ranged")
        legacy = FakeItem("old_rifle", "weapon", weapon_type="ranged", loaded=4)
        char._equipment_slots = {
            "weapon_ranged": canonical,
            "weapon": legacy,
        }

        equipped = EquipmentHandler(char).get_all_equipped()

        self.assertEqual(equipped, {"weapon_ranged": canonical})
        self.assertEqual(legacy.slot, "weapon_ranged")
        self.assertEqual(legacy.loaded, 4)
        self.assertNotIn(legacy, equipped.values())

    def test_mismatched_canonical_key_moves_to_inferred_destination(self):
        char = FakeCharacter()
        item = FakeItem("old_rifle", "weapon", weapon_type="ranged", loaded=6)
        char._equipment_slots = {"weapon_melee": item}
        handler = EquipmentHandler(char)

        first = handler.get_all_equipped()
        persisted_after_first = dict(char._equipment_slots)
        second = handler.get_all_equipped()

        self.assertEqual(first, {"weapon_ranged": item})
        self.assertIs(first["weapon_ranged"], item)
        self.assertEqual(item.slot, "weapon_ranged")
        self.assertEqual(item.loaded, 6)
        self.assertEqual(second, first)
        self.assertEqual(char._equipment_slots, persisted_after_first)

    def test_mismatched_canonical_key_loses_destination_collision(self):
        char = FakeCharacter()
        canonical = FakeItem("new_rifle", "weapon_ranged", weapon_type="ranged")
        mismatched = FakeItem(
            "old_rifle", "weapon", weapon_type="ranged", loaded=4
        )
        char._equipment_slots = {
            "weapon_melee": mismatched,
            "weapon_ranged": canonical,
        }

        equipped = EquipmentHandler(char).get_all_equipped()

        self.assertEqual(equipped, {"weapon_ranged": canonical})
        self.assertIs(equipped["weapon_ranged"], canonical)
        self.assertEqual(mismatched.slot, "weapon_ranged")
        self.assertEqual(mismatched.loaded, 4)
        self.assertNotIn(mismatched, equipped.values())

    def test_reciprocal_mismatches_swap_losslessly_in_either_map_order(self):
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                char = FakeCharacter()
                melee = FakeItem(
                    "old_blade", "weapon", weapon_type="melee", rarity="rare"
                )
                ranged = FakeItem(
                    "old_rifle", "weapon", weapon_type="ranged", loaded=8
                )
                entries = [
                    ("weapon_melee", ranged),
                    ("weapon_ranged", melee),
                ]
                if reverse:
                    entries.reverse()
                char._equipment_slots = dict(entries)
                handler = EquipmentHandler(char)

                first = handler.get_all_equipped()
                persisted_after_first = dict(char._equipment_slots)
                second = handler.get_all_equipped()

                self.assertEqual(
                    set(first), {"weapon_melee", "weapon_ranged"}
                )
                self.assertIs(first["weapon_melee"], melee)
                self.assertIs(first["weapon_ranged"], ranged)
                self.assertEqual(melee.slot, "weapon_melee")
                self.assertEqual(melee.rarity, "rare")
                self.assertEqual(ranged.slot, "weapon_ranged")
                self.assertEqual(ranged.loaded, 8)
                self.assertEqual(second, first)
                self.assertEqual(char._equipment_slots, persisted_after_first)

    def test_missing_type_uses_ranged_hints_and_persists_type(self):
        char = FakeCharacter()
        item = FakeItem("old_rifle", "weapon", magazine_size=30, loaded=12)
        char._equipment_slots = {"weapon": item}

        equipped = EquipmentHandler(char).get_all_equipped()

        self.assertIs(equipped["weapon_ranged"], item)
        self.assertEqual(item.slot, "weapon_ranged")
        self.assertEqual(item.weapon_type, "ranged")

    def test_invalid_untyped_legacy_weapon_defaults_to_melee(self):
        char = FakeCharacter()
        item = FakeItem("old_weapon", "weapon", weapon_type="unknown")
        char._equipment_slots = {"weapon": item}

        equipped = EquipmentHandler(char).get_all_equipped()

        self.assertIs(equipped["weapon_melee"], item)
        self.assertEqual(item.slot, "weapon_melee")
        self.assertEqual(item.weapon_type, "melee")

    def test_legacy_map_key_repairs_item_with_missing_slot(self):
        char = FakeCharacter()
        item = FakeItem("old_rifle", "", weapon_type="ranged")
        char._equipment_slots = {"weapon": item}

        equipped = EquipmentHandler(char).get_all_equipped()

        self.assertEqual(equipped, {"weapon_ranged": item})
        self.assertEqual(item.slot, "weapon_ranged")

    def test_untyped_legacy_weapon_with_reach_is_ranged(self):
        """A legacy weapon that struck from a distance must not become melee:
        melee reach is hard-forced to 1, which would silently gut its range."""
        char = FakeCharacter()
        item = FakeItem("old_launcher", "weapon", {"damage": 30, "range": 8})
        char._equipment_slots = {"weapon": item}

        equipped = EquipmentHandler(char).get_all_equipped()

        self.assertIs(equipped["weapon_ranged"], item)
        self.assertEqual(item.slot, "weapon_ranged")
        self.assertEqual(item.weapon_type, "ranged")

    def test_untyped_legacy_weapon_with_melee_reach_stays_melee(self):
        char = FakeCharacter()
        item = FakeItem("old_baton", "weapon", {"damage": 10, "range": 1})
        char._equipment_slots = {"weapon": item}

        equipped = EquipmentHandler(char).get_all_equipped()

        self.assertIs(equipped["weapon_melee"], item)
        self.assertEqual(item.weapon_type, "melee")

    def test_non_weapon_slots_are_left_untouched(self):
        """Only weapon keys can be re-keyed by the split, so an item under an
        armor key is never half-repaired (slot rewritten but key left behind)
        and the read path does not scan it."""
        char = FakeCharacter()
        armor = FakeItem("plate", "torso", {"armor": 5})
        stray = FakeItem("odd", "weapon", {"damage": 3})
        char._equipment_slots = {"torso": armor, "head": stray}

        equipped = EquipmentHandler(char).get_all_equipped()

        self.assertEqual(equipped, {"torso": armor, "head": stray})
        self.assertEqual(armor.slot, "torso")
        self.assertEqual(stray.slot, "weapon")
        self.assertFalse(hasattr(stray, "weapon_type"))

    def test_stale_item_reference_does_not_break_equipment_reads(self):
        """A reference to a deleted object must not raise out of a read that
        runs on every attack and every damage calculation."""
        class _RaisingAttributes:
            def get(self, *args, **kwargs):
                raise RuntimeError("object was deleted")

        class _Ghost:
            key = "ghost"

            def __init__(self, pk):
                self.pk = pk
                self.attributes = _RaisingAttributes()

        for label, pk in (("deleted_row", None), ("raising_handler", 7)):
            with self.subTest(case=label):
                char = FakeCharacter()
                ghost = _Ghost(pk)
                char._equipment_slots = {"weapon_melee": ghost}

                equipped = EquipmentHandler(char).get_all_equipped()

                self.assertIs(equipped["weapon_melee"], ghost)


class TestUnequip(unittest.TestCase):
    def test_unequip_returns_item(self):
        char = FakeCharacter()
        handler = EquipmentHandler(char)
        item = FakeItem("vest", "armor", {"damage_reduction": 5})
        handler.equip(item)
        returned = handler.unequip("armor")
        self.assertIs(returned, item)

    def test_unequip_leaves_slot_empty(self):
        char = FakeCharacter()
        handler = EquipmentHandler(char)
        item = FakeItem("vest", "armor", {"damage_reduction": 5})
        handler.equip(item)
        handler.unequip("armor")
        self.assertIsNone(handler.get_equipped("armor"))

    def test_unequip_empty_slot_returns_none(self):
        char = FakeCharacter()
        handler = EquipmentHandler(char)
        self.assertIsNone(handler.unequip("weapon_melee"))


class TestGetAllEquipped(unittest.TestCase):
    def test_returns_all_equipped(self):
        char = FakeCharacter()
        handler = EquipmentHandler(char)
        w = FakeItem("rifle", "weapon_ranged", {"damage": 25}, "ranged")
        a = FakeItem("vest", "armor", {"damage_reduction": 5})
        handler.equip(w)
        handler.equip(a)
        all_eq = handler.get_all_equipped()
        self.assertEqual(len(all_eq), 2)
        self.assertIs(all_eq["weapon_ranged"], w)
        self.assertIs(all_eq["armor"], a)

    def test_empty_when_nothing_equipped(self):
        char = FakeCharacter()
        handler = EquipmentHandler(char)
        self.assertEqual(handler.get_all_equipped(), {})


class TestGetStatTotal(unittest.TestCase):
    def test_sums_stat_across_items(self):
        char = FakeCharacter()
        handler = EquipmentHandler(char)
        w = FakeItem(
            "rifle", "weapon_ranged", {"damage": 25, "range": 5}, "ranged"
        )
        g = FakeItem("scope", "gadget", {"sight_range": 3, "damage": 2})
        handler.equip(w)
        handler.equip(g)
        self.assertAlmostEqual(handler.get_stat_total("damage"), 27.0)

    def test_returns_zero_for_missing_stat(self):
        char = FakeCharacter()
        handler = EquipmentHandler(char)
        w = FakeItem("rifle", "weapon_ranged", {"damage": 25}, "ranged")
        handler.equip(w)
        self.assertAlmostEqual(handler.get_stat_total("sight_range"), 0.0)

    def test_returns_zero_when_empty(self):
        char = FakeCharacter()
        handler = EquipmentHandler(char)
        self.assertAlmostEqual(handler.get_stat_total("damage"), 0.0)


class TestGetAttackStatTotal(unittest.TestCase):
    def setUp(self):
        char = FakeCharacter()
        self.handler = EquipmentHandler(char)
        self.melee = FakeItem(
            "blade", "weapon_melee", {"damage_bonus": 2}, "melee"
        )
        self.ranged = FakeItem(
            "rifle", "weapon_ranged", {"damage_bonus": 7}, "ranged"
        )
        self.shared = FakeItem("gloves", "hands", {"damage_bonus": 3})
        for item in (self.melee, self.ranged, self.shared):
            self.handler.equip(item)

    def test_non_weapon_bonus_is_shared(self):
        self.assertAlmostEqual(
            self.handler.get_attack_stat_total("damage_bonus"), 3.0
        )

    def test_active_melee_excludes_ranged_bonus(self):
        self.assertAlmostEqual(
            self.handler.get_attack_stat_total(
                "damage_bonus", active_weapon=self.melee
            ),
            5.0,
        )

    def test_active_ranged_excludes_melee_bonus(self):
        self.assertAlmostEqual(
            self.handler.get_attack_stat_total(
                "damage_bonus", active_weapon=self.ranged
            ),
            10.0,
        )

    def test_none_excludes_both_weapon_slots(self):
        self.assertAlmostEqual(
            self.handler.get_attack_stat_total(
                "damage_bonus", active_weapon=None
            ),
            3.0,
        )


class TestGetSlotNames(unittest.TestCase):
    def test_returns_occupied_slots(self):
        char = FakeCharacter()
        handler = EquipmentHandler(char)
        handler.equip(FakeItem("rifle", "weapon_ranged", weapon_type="ranged"))
        handler.equip(FakeItem("vest", "armor"))
        names = sorted(handler.get_slot_names())
        self.assertEqual(names, ["armor", "weapon_ranged"])

    def test_empty_when_nothing_equipped(self):
        char = FakeCharacter()
        handler = EquipmentHandler(char)
        self.assertEqual(handler.get_slot_names(), [])

# -------------------------------------------------------------- #
#  Supply_Bag helpers: stubs
# -------------------------------------------------------------- #

class FakeItemDef:
    """Lightweight stand-in for an ItemDef exposing a ``weight``."""

    def __init__(self, weight: float):
        self.weight = weight

class FakeProvider:
    """Stub DefinitionsProvider: resolves item keys to FakeItemDefs.

    Exposes ``resolve_item`` (the first shape ``supplies_weight`` tries),
    returning ``None`` for unknown keys.
    """

    def __init__(self, defs: dict[str, FakeItemDef]):
        self._defs = defs

    def resolve_item(self, item_key: str):
        return self._defs.get(item_key)

# -------------------------------------------------------------- #
#  Supply_Bag unit tests
# -------------------------------------------------------------- #

class TestSupplyRoundTrip(unittest.TestCase):
    def test_add_then_remove_round_trip(self):
        handler = EquipmentHandler(FakeCharacter())
        added = handler.add_supply("rifle_rounds", 30)
        self.assertEqual(added, 30)
        self.assertEqual(handler.get_supply("rifle_rounds"), 30)
        ok = handler.remove_supply("rifle_rounds", 30)
        self.assertTrue(ok)
        self.assertEqual(handler.get_supply("rifle_rounds"), 0)

    def test_add_returns_amount_actually_added(self):
        handler = EquipmentHandler(FakeCharacter())
        self.assertEqual(handler.add_supply("medkit", 5, max_stack=10), 5)
        # Only 5 room left before hitting the cap of 10.
        self.assertEqual(handler.add_supply("medkit", 8, max_stack=10), 5)
        self.assertEqual(handler.get_supply("medkit"), 10)

    def test_add_non_positive_is_noop(self):
        handler = EquipmentHandler(FakeCharacter())
        self.assertEqual(handler.add_supply("medkit", 0), 0)
        self.assertEqual(handler.add_supply("medkit", -3), 0)
        self.assertEqual(handler.get_supply("medkit"), 0)

    def test_get_supplies_returns_copy(self):
        handler = EquipmentHandler(FakeCharacter())
        handler.add_supply("energy_cell", 4)
        snapshot = handler.get_supplies()
        snapshot["energy_cell"] = 999
        # Mutating the returned dict must not affect the bag.
        self.assertEqual(handler.get_supply("energy_cell"), 4)

class TestSupplyRemoval(unittest.TestCase):
    def test_remove_insufficient_returns_false_and_unchanged(self):
        handler = EquipmentHandler(FakeCharacter())
        handler.add_supply("frag_grenade", 2)
        ok = handler.remove_supply("frag_grenade", 5)
        self.assertFalse(ok)
        self.assertEqual(handler.get_supply("frag_grenade"), 2)

    def test_remove_missing_key_returns_false(self):
        handler = EquipmentHandler(FakeCharacter())
        self.assertFalse(handler.remove_supply("nonexistent", 1))

    def test_remove_non_positive_rejected(self):
        handler = EquipmentHandler(FakeCharacter())
        handler.add_supply("medkit", 3)
        self.assertFalse(handler.remove_supply("medkit", 0))
        self.assertFalse(handler.remove_supply("medkit", -1))
        self.assertEqual(handler.get_supply("medkit"), 3)

    def test_depleted_key_disappears(self):
        handler = EquipmentHandler(FakeCharacter())
        handler.add_supply("medkit", 3)
        self.assertTrue(handler.remove_supply("medkit", 3))
        # The entry is removed entirely once the count hits 0.
        self.assertNotIn("medkit", handler.get_supplies())
        self.assertEqual(handler.get_supply("medkit"), 0)

    def test_partial_removal_keeps_remainder(self):
        handler = EquipmentHandler(FakeCharacter())
        handler.add_supply("rifle_rounds", 30)
        self.assertTrue(handler.remove_supply("rifle_rounds", 10))
        self.assertEqual(handler.get_supply("rifle_rounds"), 20)

class TestSuppliesWeight(unittest.TestCase):
    def test_weight_is_sum_of_weight_times_count(self):
        handler = EquipmentHandler(FakeCharacter())
        handler.add_supply("rifle_rounds", 30)
        handler.add_supply("medkit", 2)
        provider = FakeProvider({
            "rifle_rounds": FakeItemDef(0.1),
            "medkit": FakeItemDef(5.0),
        })
        # 30 * 0.1 + 2 * 5.0 = 3.0 + 10.0 = 13.0
        self.assertAlmostEqual(handler.supplies_weight(provider), 13.0)

    def test_empty_bag_weighs_zero(self):
        handler = EquipmentHandler(FakeCharacter())
        provider = FakeProvider({})
        self.assertAlmostEqual(handler.supplies_weight(provider), 0.0)

    def test_unresolvable_def_contributes_zero(self):
        handler = EquipmentHandler(FakeCharacter())
        handler.add_supply("known", 4)
        handler.add_supply("mystery", 10)
        provider = FakeProvider({"known": FakeItemDef(2.0)})
        # mystery has no def -> contributes 0; only 4 * 2.0 counts.
        self.assertAlmostEqual(handler.supplies_weight(provider), 8.0)

# -------------------------------------------------------------- #
#  Property 11: Supply non-negativity & stack cap
#  **Validates: Requirements 10.1, 10.4**
# -------------------------------------------------------------- #

# A small pool of supply keys so operations collide on the same entries.
SUPPLY_KEYS = ["rifle_rounds", "energy_cell", "medkit", "frag_grenade"]

@st.composite
def supply_op_strategy(draw):
    """Generate a single ('add'|'remove', key, count) operation."""
    kind = draw(st.sampled_from(["add", "remove"]))
    key = draw(st.sampled_from(SUPPLY_KEYS))
    # Include non-positive counts to exercise the no-op / reject paths.
    count = draw(st.integers(min_value=-5, max_value=150))
    return (kind, key, count)

class TestProperty11SupplyBounds(unittest.TestCase):
    """Property 11: Supply non-negativity & stack cap.

    Bag counts stay within ``[0, max_stack]`` across any sequence of
    add/remove operations: ``remove_supply`` never underflows (returns
    False and leaves the bag unchanged when insufficient), and
    ``add_supply`` never grows an entry beyond ``max_stack``.

    **Validates: Requirements 10.1, 10.4**
    """

    @given(
        ops=st.lists(supply_op_strategy(), min_size=1, max_size=40),
        max_stack=st.integers(min_value=1, max_value=99),
    )
    @settings(max_examples=200)
    def test_counts_stay_within_bounds(self, ops, max_stack):
        handler = EquipmentHandler(FakeCharacter())
        for kind, key, count in ops:
            before = handler.get_supply(key)
            if kind == "add":
                added = handler.add_supply(key, count, max_stack=max_stack)
                # Never adds more than requested, never negative.
                self.assertGreaterEqual(added, 0)
                if count > 0:
                    self.assertLessEqual(added, count)
                else:
                    self.assertEqual(added, 0)
                # add never exceeds max_stack.
                self.assertEqual(handler.get_supply(key), before + added)
                self.assertLessEqual(handler.get_supply(key), max_stack)
            else:
                ok = handler.remove_supply(key, count)
                after = handler.get_supply(key)
                if count <= 0 or before < count:
                    # Insufficient / invalid: rejected and unchanged.
                    self.assertFalse(ok)
                    self.assertEqual(after, before)
                else:
                    self.assertTrue(ok)
                    self.assertEqual(after, before - count)

            # Global invariant: every entry in the bag stays in [0, max_stack].
            for v in handler.get_supplies().values():
                self.assertGreaterEqual(v, 0)
                self.assertLessEqual(v, max_stack)

    @given(
        count=st.integers(min_value=1, max_value=500),
        max_stack=st.integers(min_value=1, max_value=99),
    )
    @settings(max_examples=100)
    def test_single_add_never_exceeds_cap(self, count, max_stack):
        handler = EquipmentHandler(FakeCharacter())
        handler.add_supply("medkit", count, max_stack=max_stack)
        self.assertLessEqual(handler.get_supply("medkit"), max_stack)

    @given(
        stored=st.integers(min_value=0, max_value=99),
        remove=st.integers(min_value=1, max_value=200),
    )
    @settings(max_examples=100)
    def test_remove_never_underflows(self, stored, remove):
        handler = EquipmentHandler(FakeCharacter())
        if stored > 0:
            handler.add_supply("rifle_rounds", stored)
        ok = handler.remove_supply("rifle_rounds", remove)
        if remove > stored:
            self.assertFalse(ok)
            self.assertEqual(handler.get_supply("rifle_rounds"), stored)
        else:
            self.assertTrue(ok)
        # Count is never negative regardless of outcome.
        self.assertGreaterEqual(handler.get_supply("rifle_rounds"), 0)

# -------------------------------------------------------------- #
#  Property 15: API preservation
#  **Validates: Requirements 10.1, 10.4** (Req 14.3)
# -------------------------------------------------------------- #

class TestProperty15ApiPreservation(unittest.TestCase):
    """Property 15: API preservation.

    The pre-existing ``EquipmentHandler`` methods (``equip``, ``unequip``,
    ``get_equipped``, ``get_all_equipped``, ``get_stat_total``,
    ``get_slot_names``) are unchanged in signature and behavior after the
    Supply_Bag additions (Req 14.3).

    **Validates: Requirements 10.1, 10.4**
    """

    #: The frozen public surface: method name -> ordered param names
    #: (excluding ``self``).
    EXPECTED_SIGNATURES = {
        "equip": ["item"],
        "unequip": ["slot"],
        "get_equipped": ["slot"],
        "get_all_equipped": [],
        "get_stat_total": ["stat_name"],
        "get_slot_names": [],
    }

    def test_signatures_unchanged(self):
        for name, expected_params in self.EXPECTED_SIGNATURES.items():
            method = getattr(EquipmentHandler, name, None)
            self.assertIsNotNone(method, f"Missing method {name!r}")
            params = [
                p for p in inspect.signature(method).parameters
                if p != "self"
            ]
            self.assertEqual(
                params, expected_params,
                f"Signature of {name!r} changed: {params} != {expected_params}",
            )

    def test_equip_unequip_behavior_unchanged(self):
        handler = EquipmentHandler(FakeCharacter())
        item = FakeItem(
            "rifle", "weapon_ranged", {"damage": 25}, weapon_type="ranged"
        )
        ok, msg = handler.equip(item)
        self.assertTrue(ok)
        self.assertIn("weapon_ranged", msg)
        self.assertIs(handler.get_equipped("weapon_ranged"), item)
        self.assertEqual(handler.get_slot_names(), ["weapon_ranged"])
        self.assertEqual(handler.get_all_equipped(), {"weapon_ranged": item})
        self.assertIs(handler.unequip("weapon_ranged"), item)
        self.assertIsNone(handler.get_equipped("weapon_ranged"))

    def test_get_stat_total_behavior_unchanged(self):
        handler = EquipmentHandler(FakeCharacter())
        handler.equip(
            FakeItem(
                "rifle", "weapon_ranged", {"damage": 25}, weapon_type="ranged"
            )
        )
        handler.equip(FakeItem("scope", "gadget", {"damage": 2}))
        self.assertAlmostEqual(handler.get_stat_total("damage"), 27.0)

    def test_supply_bag_does_not_leak_into_equipment_api(self):
        """Supply operations must not disturb the equipment slot surface."""
        handler = EquipmentHandler(FakeCharacter())
        handler.equip(FakeItem("vest", "torso", {"damage_reduction": 5}))
        handler.add_supply("rifle_rounds", 30)
        handler.add_supply("medkit", 2)
        # Equipment views are untouched by Supply_Bag contents.
        self.assertEqual(handler.get_slot_names(), ["torso"])
        self.assertEqual(len(handler.get_all_equipped()), 1)

if __name__ == "__main__":
    unittest.main()
