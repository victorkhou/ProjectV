"""
Unit tests for the GameItem typeclass field accessors.

Verifies that the new equipment/weapon/supply metadata fields
(category, weapon_type, ammo_type, ammo_per_shot, magazine_size,
effect, max_stack, weight) are exposed on a live GameItem as named
property accessors that read from Evennia attributes with defaults
matching ItemDef, and that the creation factory copies them.

Requirements: 14.4
"""

import sys
import types
import unittest

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

    class _AttrStore:
        def __init__(self):
            self._data = {}
        def get(self, key, default=None, **kw):
            return self._data.get(key, default)
        def add(self, key, value, **kw):
            self._data[key] = value
        def has(self, key):
            return key in self._data

    class _DbProxy:
        def __init__(self, store):
            object.__setattr__(self, "_store", store)
        def __getattr__(self, key):
            return object.__getattribute__(self, "_store").get(key)
        def __setattr__(self, key, value):
            object.__getattribute__(self, "_store").add(key, value)

    class DefaultObject:
        def __init__(self, **kwargs):
            self._attr_store = _AttrStore()
            self.attributes = self._attr_store
            self.db = _DbProxy(self._attr_store)
            self.key = kwargs.get("key", "TestItem")
            self.location = None

    _mod("evennia")
    _mod("evennia.objects")
    _mod("evennia.objects.objects", {
        "DefaultObject": DefaultObject,
        "DefaultCharacter": type("DefaultCharacter", (), {}),
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

from mygame.typeclasses.objects import GameItem  # noqa: E402
from mygame.world.definitions import ItemDef  # noqa: E402
from mygame.world.systems.equipment_system import EquipmentSystem  # noqa: E402


# -------------------------------------------------------------- #
#  Helpers
# -------------------------------------------------------------- #

def _make_item(**attrs) -> GameItem:
    """Create a GameItem with stubbed Evennia internals and attributes set."""
    item = GameItem(key=attrs.pop("key", "TestItem"))
    for name, value in attrs.items():
        item.attributes.add(name, value)
    return item


# -------------------------------------------------------------- #
#  Tests: defaults match ItemDef defaults when attribute unset
# -------------------------------------------------------------- #

class TestGameItemAccessorDefaults(unittest.TestCase):
    """When no attribute is set, accessors return the ItemDef defaults."""

    def setUp(self):
        self.item = _make_item()
        self.defaults = ItemDef(key="x", name="X")

    def test_category_default(self):
        self.assertEqual(self.item.category, self.defaults.category)

    def test_weapon_type_default_none(self):
        self.assertIsNone(self.item.weapon_type)
        self.assertEqual(self.item.weapon_type, self.defaults.weapon_type)

    def test_ammo_type_default_none(self):
        self.assertIsNone(self.item.ammo_type)

    def test_ammo_per_shot_default(self):
        self.assertEqual(self.item.ammo_per_shot, self.defaults.ammo_per_shot)
        self.assertEqual(self.item.ammo_per_shot, 1)

    def test_magazine_size_default_none(self):
        self.assertIsNone(self.item.magazine_size)

    def test_effect_default_none(self):
        self.assertIsNone(self.item.effect)

    def test_max_stack_default(self):
        self.assertEqual(self.item.max_stack, self.defaults.max_stack)
        self.assertEqual(self.item.max_stack, 99)

    def test_weight_default(self):
        self.assertEqual(self.item.weight, self.defaults.weight)
        self.assertEqual(self.item.weight, 1.0)


# -------------------------------------------------------------- #
#  Tests: accessors return set attribute values
# -------------------------------------------------------------- #

class TestGameItemAccessorValues(unittest.TestCase):
    """Accessors reflect the values stored on the object's attributes."""

    def test_ranged_weapon_fields(self):
        item = _make_item(
            key="assault_rifle",
            category="weapon",
            weapon_type="ranged",
            ammo_type="rifle_rounds",
            ammo_per_shot=2,
            magazine_size=30,
            weight=8.0,
        )
        self.assertEqual(item.category, "weapon")
        self.assertEqual(item.weapon_type, "ranged")
        self.assertEqual(item.ammo_type, "rifle_rounds")
        self.assertEqual(item.ammo_per_shot, 2)
        self.assertEqual(item.magazine_size, 30)
        self.assertEqual(item.weight, 8.0)

    def test_melee_weapon_fields(self):
        item = _make_item(category="weapon", weapon_type="melee")
        self.assertEqual(item.weapon_type, "melee")
        self.assertIsNone(item.ammo_type)

    def test_consumable_effect_field(self):
        effect = {"type": "heal", "amount": 30}
        item = _make_item(category="consumable", effect=effect, max_stack=10)
        self.assertEqual(item.category, "consumable")
        self.assertEqual(item.effect, effect)
        self.assertEqual(item.max_stack, 10)

    def test_throwable_effect_field(self):
        effect = {"type": "aoe_damage", "amount": 40, "radius": 2}
        item = _make_item(category="throwable", effect=effect)
        self.assertEqual(item.category, "throwable")
        self.assertEqual(item.effect["radius"], 2)


# -------------------------------------------------------------- #
#  Tests: per-instance roll storage + get_stat preference
#  (item-loot-economy task 1.3 — Requirements 1.2, 12.1)
# -------------------------------------------------------------- #

class TestRolledStatStorage(unittest.TestCase):
    """db.rolled_stats/db.iqs/db.rarity accessors and get_stat preference."""

    def test_roll_state_defaults_when_unrolled(self):
        item = _make_item(stat_modifiers={"damage": 25})
        self.assertEqual(item.rolled_stats, {})
        self.assertEqual(item.affixes, [])
        self.assertIsNone(item.rarity)
        self.assertIsNone(item.iqs)
        self.assertEqual(item.inserts, [])

    def test_roll_state_accessors_read_db(self):
        item = _make_item(
            stat_modifiers={"damage": 25},
            rolled_stats={"damage": 31.5},
            rarity="epic",
            iqs=72.0,
            affixes=[{"key": "of_reach"}],
            inserts=["extended_barrel"],
        )
        self.assertEqual(item.rolled_stats, {"damage": 31.5})
        self.assertEqual(item.rarity, "epic")
        self.assertEqual(item.iqs, 72.0)
        self.assertEqual(item.affixes, [{"key": "of_reach"}])
        self.assertEqual(item.inserts, ["extended_barrel"])

    def test_get_stat_prefers_rolled_value(self):
        item = _make_item(
            stat_modifiers={"damage": 25, "range": 3},
            rolled_stats={"damage": 31.5},
        )
        self.assertEqual(item.get_stat("damage"), 31.5)

    def test_get_stat_falls_back_to_def_base_for_unrolled_stat(self):
        item = _make_item(
            stat_modifiers={"damage": 25, "range": 3},
            rolled_stats={"damage": 31.5},
        )
        # "range" is not in rolled_stats — the def base is used.
        self.assertEqual(item.get_stat("range"), 3.0)

    def test_get_stat_without_roll_data_unchanged(self):
        item = _make_item(stat_modifiers={"damage": 25})
        self.assertEqual(item.get_stat("damage"), 25.0)
        self.assertEqual(item.get_stat("missing", 7), 7.0)

    def test_combat_engine_get_stat_reads_rolled_value(self):
        """Combat's _get_stat sees the rolled value with no engine change."""
        from mygame.world.systems.combat_engine import CombatEngine

        item = _make_item(
            stat_modifiers={"damage": 25, "range": 3},
            rolled_stats={"damage": 31.5, "range": 4},
        )
        self.assertEqual(CombatEngine._get_stat(item, "damage", 0), 31.5)
        self.assertEqual(CombatEngine._get_stat(item, "range", 1), 4.0)

    def test_equipment_handler_get_stat_total_aggregates_rolled(self):
        """get_stat_total sums rolled values across equipped GameItems."""
        from mygame.world.systems.equipment_handler import EquipmentHandler

        weapon = _make_item(
            key="rifle",
            slot="weapon",
            stat_modifiers={"damage": 25},
            rolled_stats={"damage": 31.5},
        )
        armor = _make_item(
            key="vest",
            slot="torso",
            stat_modifiers={"damage_reduction": 4, "damage": 1},
            rolled_stats={"damage_reduction": 5.5},
        )

        class _Char:
            def __init__(self):
                self._equipment_slots = {}

        handler = EquipmentHandler(_Char())
        handler.equip(weapon)
        handler.equip(armor)

        self.assertEqual(handler.get_stat_total("damage"), 31.5 + 1)
        self.assertEqual(handler.get_stat_total("damage_reduction"), 5.5)


# -------------------------------------------------------------- #
#  Tests: affix stat contributions read through get_stat
#  (item-loot-economy task 2.3 — Requirements 3.4, 3.5)
# -------------------------------------------------------------- #

class TestAffixStatContribution(unittest.TestCase):
    """db.affixes magnitudes on a stat axis ADD on top of the base value
    in get_stat, so aggregating-axis affixes flow into combat via
    get_stat_total with no combat-engine change (R3.5)."""

    def test_affix_adds_on_top_of_rolled_base(self):
        item = _make_item(
            stat_modifiers={"damage": 25},
            rolled_stats={"damage": 31.5},
            affixes=[{"key": "keen", "name": "of Power",
                      "stat": "damage_bonus", "magnitude": 4, "value": 5.0}],
        )
        self.assertEqual(item.get_stat("damage"), 31.5)  # untouched axis
        self.assertEqual(item.get_stat("damage_bonus"), 4.0)

    def test_affix_adds_on_top_of_def_base(self):
        # An affix on an axis the def already carries stacks with it.
        item = _make_item(
            stat_modifiers={"damage_reduction": 4},
            affixes=[{"key": "sturdy", "stat": "damage_reduction",
                      "magnitude": 5.5}],
        )
        self.assertEqual(item.get_stat("damage_reduction"), 9.5)

    def test_multiple_affixes_on_same_axis_sum(self):
        item = _make_item(
            affixes=[
                {"key": "warding_f", "stat": "fire_resist", "magnitude": 3},
                {"key": "flameproof", "stat": "fire_resist", "magnitude": 2},
            ],
        )
        self.assertEqual(item.get_stat("fire_resist"), 5.0)

    def test_non_matching_and_malformed_affixes_ignored(self):
        item = _make_item(
            stat_modifiers={"damage": 25},
            affixes=[
                {"key": "warded", "stat": "psychic_resist", "magnitude": 4},
                {"key": "broken", "stat": "fire_resist"},  # no magnitude
                {"key": "boolish", "stat": "fire_resist", "magnitude": True},
                "not_a_dict",
            ],
        )
        self.assertEqual(item.get_stat("damage"), 25.0)
        self.assertEqual(item.get_stat("fire_resist"), 0.0)
        self.assertEqual(item.get_stat("psychic_resist"), 4.0)

    def test_default_still_honored_without_affixes(self):
        item = _make_item(stat_modifiers={"damage": 25})
        self.assertEqual(item.get_stat("missing", 7), 7.0)

    def test_combat_engine_get_stat_reads_affix_contribution(self):
        """Combat's _get_stat sees the affix value with no engine change."""
        from mygame.world.systems.combat_engine import CombatEngine

        item = _make_item(
            stat_modifiers={"damage": 25},
            affixes=[{"key": "keen", "stat": "damage_bonus", "magnitude": 4}],
        )
        self.assertEqual(CombatEngine._get_stat(item, "damage_bonus", 0), 4.0)

    def test_get_stat_total_aggregates_affix_dr_and_resist(self):
        """get_stat_total — the exact read combat uses for DR
        (combat_engine._get_target_damage_reduction) and typed resists
        (_get_target_typed_resist builds f"{type}_resist") — picks up
        affix magnitudes across equipped items with no engine change."""
        from mygame.world.systems.equipment_handler import EquipmentHandler

        weapon = _make_item(
            key="rifle",
            slot="weapon",
            stat_modifiers={"damage": 25},
            affixes=[{"key": "warding_f", "stat": "fire_resist",
                      "magnitude": 4}],
        )
        armor = _make_item(
            key="vest",
            slot="torso",
            stat_modifiers={"damage_reduction": 4},
            rolled_stats={"damage_reduction": 5.5},
            affixes=[{"key": "sturdy", "stat": "damage_reduction",
                      "magnitude": 3},
                     {"key": "flameproof", "stat": "fire_resist",
                      "magnitude": 2}],
        )

        class _Char:
            def __init__(self):
                self._equipment_slots = {}

        handler = EquipmentHandler(_Char())
        handler.equip(weapon)
        handler.equip(armor)

        # Armor DR: rolled 5.5 + affix 3 (the weapon adds none).
        self.assertEqual(handler.get_stat_total("damage_reduction"), 8.5)
        # fire_resist aggregates across BOTH equipped items: 4 + 2.
        self.assertEqual(handler.get_stat_total("fire_resist"), 6.0)


# -------------------------------------------------------------- #
#  Tests: name decoration + inspect roll details
#  (item-loot-economy task 1.6 — Requirements 2.3, 2.5)
# -------------------------------------------------------------- #

def _make_item_with_def(item_def, **attrs):
    """Create a GameItem whose ``item_def`` resolves to *item_def*."""

    class _ItemWithDef(GameItem):
        @property
        def item_def(self):
            return item_def

    item = _ItemWithDef(key=attrs.pop("key", item_def.name))
    for name, value in attrs.items():
        item.attributes.add(name, value)
    return item


class TestDamageTypeAccessor(unittest.TestCase):
    """GameItem.damage_type — instance override then def fallback (task 4.3).

    A Blacksmith damage-type insert writes ``db.damage_type`` on the
    weapon instance; combat's ``CombatEngine._get_damage_type`` reads this
    property, so the conversion (and the shipped typed weapons' def-level
    damage_type) dispatches typed damage in combat.

    Validates: Requirements 5.1, 5.2
    """

    def test_unset_without_def_is_none(self):
        item = _make_item()
        self.assertIsNone(item.damage_type)

    def test_def_damage_type_falls_through(self):
        """A shipped typed weapon (def-level damage_type) reads its type."""
        idef = ItemDef(key="incendiary_rifle", name="Incendiary Rifle",
                       slot="weapon", category="weapon", damage_type="fire")
        item = _make_item_with_def(idef)
        self.assertEqual(item.damage_type, "fire")

    def test_instance_override_beats_def(self):
        """An insert conversion (db.damage_type) overrides the def."""
        idef = ItemDef(key="assault_rifle", name="Assault Rifle",
                       slot="weapon", category="weapon")  # physical def
        item = _make_item_with_def(idef, damage_type="poison")
        self.assertEqual(item.damage_type, "poison")

    def test_combat_reads_the_instance_conversion(self):
        """The exact combat read: _get_damage_type on a converted item."""
        from mygame.world.systems.combat_engine import CombatEngine
        item = _make_item(damage_type="fire")
        self.assertEqual(CombatEngine._get_damage_type(item), "fire")

    def test_combat_default_stays_physical(self):
        """An unconverted, untyped item still dispatches physical."""
        from mygame.world.systems.combat_engine import CombatEngine
        item = _make_item()
        self.assertEqual(CombatEngine._get_damage_type(item), "physical")


class TestRollNameDecoration(unittest.TestCase):
    """get_quality_tag / get_display_name — '[Rarity · IQS%]' (R2.3, R2.5)."""

    def test_unrolled_item_name_is_neutral(self):
        """A fixed item shows NO IQS/rarity readout (R2.5)."""
        item = _make_item(key="Assault Rifle", stat_modifiers={"damage": 25})
        self.assertEqual(item.get_quality_tag(), "")
        self.assertEqual(item.get_display_name(), "Assault Rifle")

    def test_iqs_only_tag(self):
        """Phase 1: no rarity assigned yet — the tag shows just the IQS."""
        item = _make_item(
            key="Assault Rifle", rolled_stats={"damage": 27}, iqs=73,
        )
        self.assertEqual(item.get_quality_tag(), "[73%]")
        self.assertEqual(item.get_display_name(), "Assault Rifle [73%]")

    def test_rarity_and_iqs_tag_colored(self):
        """Once rarity lands (Phase 2) the tag reads '[Rare · 73%]' in the
        rarity color (design §3.1)."""
        item = _make_item(
            key="Assault Rifle",
            rolled_stats={"damage": 27},
            iqs=73,
            rarity="rare",
        )
        self.assertEqual(item.get_quality_tag(), "[Rare · 73%]")
        self.assertEqual(
            item.get_display_name(), "|cAssault Rifle [Rare · 73%]|n"
        )

    def test_rarity_colors_match_design_palette(self):
        palette = {
            "common": "|w", "uncommon": "|g", "rare": "|c",
            "epic": "|m", "legendary": "|y",
        }
        for rarity, color in palette.items():
            item = _make_item(key="X", iqs=50, rarity=rarity)
            self.assertTrue(
                item.get_display_name().startswith(color),
                f"{rarity} should color with {color}",
            )

    def test_unknown_rarity_uncolored(self):
        item = _make_item(key="X", iqs=50, rarity="mythic")
        self.assertEqual(item.get_display_name(), "X [Mythic · 50%]")

    def test_iqs_rounds_to_int(self):
        item = _make_item(key="X", iqs=72.6)
        self.assertEqual(item.get_quality_tag(), "[73%]")

    def test_score_above_100_renders(self):
        """Task 2.4 (design §2.2): the displayed score is base IQS +
        affix values and can exceed 100 — 'Legendary 112' reads top-tier."""
        item = _make_item(key="Assault Rifle", iqs=112, rarity="legendary")
        self.assertEqual(item.get_quality_tag(), "[Legendary · 112%]")
        self.assertEqual(
            item.get_display_name(),
            "|yAssault Rifle [Legendary · 112%]|n",
        )

    def test_display_caps_at_999(self):
        """The display renders min(score, 999); the stored math is never
        clamped (design §2.2)."""
        item = _make_item(key="X", iqs=1500)
        self.assertEqual(item.get_quality_tag(), "[999%]")
        self.assertEqual(item.iqs, 1500)  # stored value untouched


class TestRollInspectDetails(unittest.TestCase):
    """get_roll_details / return_appearance — per-stat 'rolled (min–max)'."""

    ROLL_SPEC = {
        "stats": {
            "damage": {"min": 18, "max": 30, "weight": 3},
            "range": {"min": 4, "max": 7, "weight": 1},
        },
    }

    def _rifle_def(self):
        return ItemDef(
            key="assault_rifle", name="Assault Rifle", slot="weapon",
            stat_modifiers={"damage": 25, "range": 5},
            roll_spec=self.ROLL_SPEC,
        )

    def test_details_show_rolled_value_with_band(self):
        item = _make_item_with_def(
            self._rifle_def(),
            rolled_stats={"damage": 27, "range": 6},
            iqs=73,
        )
        self.assertEqual(
            item.get_roll_details(),
            ["Damage 27 (18–30)", "Range 6 (4–7)"],
        )

    def test_rolled_stat_without_band_shows_value_alone(self):
        """A stat outside the spec (e.g. insert-added) has no band."""
        item = _make_item_with_def(
            self._rifle_def(),
            rolled_stats={"damage": 27, "fire_resist": 3},
        )
        details = item.get_roll_details()
        self.assertIn("Damage 27 (18–30)", details)
        self.assertIn("Fire resist 3", details)

    def test_details_without_item_def_omit_bands(self):
        """No registry/def available → values still show, no bands."""
        item = _make_item(key="X", rolled_stats={"damage": 27.5})
        self.assertEqual(item.get_roll_details(), ["Damage 27.5"])

    def test_affixes_listed(self):
        item = _make_item_with_def(
            self._rifle_def(),
            rolled_stats={"damage": 27},
            affixes=[{"key": "warding_f", "stat": "fire_resist",
                      "magnitude": 4}],
        )
        self.assertIn("+4 fire_resist", item.get_roll_details())

    def test_unrolled_item_has_no_details(self):
        """Fixed item → empty details (neutral, R2.5)."""
        item = _make_item(key="X", stat_modifiers={"damage": 25})
        self.assertEqual(item.get_roll_details(), [])

    def test_return_appearance_appends_detail_block(self):
        item = _make_item_with_def(
            self._rifle_def(),
            rolled_stats={"damage": 27, "range": 6},
            iqs=73,
        )
        text = item.return_appearance(looker=None)
        self.assertIn("Damage 27 (18–30)", text)
        self.assertIn("Range 6 (4–7)", text)

    def test_return_appearance_neutral_for_unrolled(self):
        item = _make_item(key="Assault Rifle", stat_modifiers={"damage": 25})
        text = item.return_appearance(looker=None)
        self.assertNotIn("%", text)
        self.assertNotIn("(", text)

    def test_structured_state_includes_roll_state(self):
        item = _make_item(
            key="X",
            rolled_stats={"damage": 27},
            iqs=73,
            rarity="rare",
            affixes=[{"key": "k"}],
            inserts=["extended_barrel"],
        )
        state = item.get_structured_state()
        self.assertEqual(state["rolled_stats"], {"damage": 27})
        self.assertEqual(state["iqs"], 73)
        self.assertEqual(state["rarity"], "rare")
        self.assertEqual(state["affixes"], [{"key": "k"}])
        self.assertEqual(state["inserts"], ["extended_barrel"])

    def test_structured_state_neutral_when_unrolled(self):
        state = _make_item(key="X").get_structured_state()
        self.assertEqual(state["rolled_stats"], {})
        self.assertIsNone(state["iqs"])
        self.assertIsNone(state["rarity"])
        self.assertEqual(state["affixes"], [])
        self.assertEqual(state["inserts"], [])


# -------------------------------------------------------------- #
#  Tests: creation factory copies the new fields
# -------------------------------------------------------------- #

class TestCreationFactoryFieldCopy(unittest.TestCase):
    """EquipmentSystem._default_create_item copies the new ItemDef fields."""

    def test_factory_copies_all_new_fields(self):
        item_def = ItemDef(
            key="assault_rifle",
            name="Assault Rifle",
            slot="weapon",
            category="weapon",
            stat_modifiers={"damage": 25, "range": 3},
            weapon_type="ranged",
            ammo_type="rifle_rounds",
            ammo_per_shot=1,
            magazine_size=30,
            effect=None,
            max_stack=99,
            weight=8.0,
        )

        class _Owner:
            def __init__(self):
                self._inventory = []

        owner = _Owner()
        item = EquipmentSystem._default_create_item(item_def, owner)

        self.assertEqual(item["category"], "weapon")
        self.assertEqual(item["weapon_type"], "ranged")
        self.assertEqual(item["ammo_type"], "rifle_rounds")
        self.assertEqual(item["ammo_per_shot"], 1)
        self.assertEqual(item["magazine_size"], 30)
        self.assertIsNone(item["effect"])
        self.assertEqual(item["max_stack"], 99)
        self.assertEqual(item["weight"], 8.0)
        self.assertIn(item, owner._inventory)

    def test_factory_copies_effect_dict(self):
        item_def = ItemDef(
            key="medkit",
            name="Medkit",
            category="consumable",
            effect={"type": "heal", "amount": 30},
            max_stack=10,
            weight=1.5,
        )

        class _Owner:
            def __init__(self):
                self._inventory = []

        item = EquipmentSystem._default_create_item(item_def, _Owner())
        self.assertEqual(item["effect"], {"type": "heal", "amount": 30})
        # ensure it's a copy, not the same dict object
        self.assertIsNot(item["effect"], item_def.effect)
        self.assertEqual(item["max_stack"], 10)
        self.assertEqual(item["weight"], 1.5)


if __name__ == "__main__":
    unittest.main()
