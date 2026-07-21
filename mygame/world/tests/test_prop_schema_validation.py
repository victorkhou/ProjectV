"""
Property-based tests for SchemaValidator.

**Validates: Requirements 17.2, 17.3, 17.4, 17.5, 18.4, 19.3, 20.3, 21.4, 22.3**

Property 26: Schema validation catches invalid definitions
- The validator NEVER returns an empty error list for structurally invalid input.
- The validator ALWAYS returns an empty error list for valid input.
"""

from types import SimpleNamespace

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from mygame.world.schema_validator import SchemaValidator
from mygame.world.constants import (
    MAX_BUILDING_LEVEL,
    EQUIPMENT_SLOTS,
    GEAR_CATEGORIES,
    ITEM_CATEGORIES,
    WEAPON_TYPES,
    EFFECT_TYPES,
    RESOURCE_TYPES,
)
from mygame.world.definitions import ItemDef

validator = SchemaValidator()

# ------------------------------------------------------------------ #
#  Shared strategies
# ------------------------------------------------------------------ #

positive_int = st.integers(min_value=1, max_value=10_000)
non_negative_int = st.integers(min_value=0, max_value=10_000)
resource_cost = st.dictionaries(
    st.text(min_size=1, max_size=10, alphabet=st.characters(whitelist_categories=("L",))),
    positive_int,
    min_size=1,
    max_size=4,
)
two_char_str = st.text(min_size=2, max_size=2, alphabet=st.characters(whitelist_categories=("L",)))


# ------------------------------------------------------------------ #
#  Building strategies
# ------------------------------------------------------------------ #

def valid_building_dict():
    return st.fixed_dictionaries({
        "name": st.text(min_size=1, max_size=20),
        "abbreviation": two_char_str,
        "cost": resource_cost,
        "max_health": positive_int,
        "requires_hq": st.booleans(),
        "category": st.sampled_from(["headquarters", "resource", "equipment", "defense", "research"]),
        "map_symbol": two_char_str,
        "build_time_seconds": positive_int,
        "max_level": st.integers(min_value=1, max_value=MAX_BUILDING_LEVEL),
        "rank_requirement": positive_int,
        "requires_agent": st.booleans(),
        "storage_capacity": non_negative_int,
    })


# ------------------------------------------------------------------ #
#  Item strategies
# ------------------------------------------------------------------ #

def valid_item_dict():
    # A minimal valid Gear item: category defaults to "armor" (a Gear
    # category), so `slot` is required and must be a canonical EQUIPMENT_SLOT.
    return st.fixed_dictionaries({
        "key": st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("L", "N"))),
        "name": st.text(min_size=1, max_size=20),
        "slot": st.sampled_from(list(EQUIPMENT_SLOTS)),
    })


def valid_items_data():
    return st.fixed_dictionaries({
        "items": st.lists(valid_item_dict(), min_size=1, max_size=5),
        "production_map": st.just({}),
    })


# ------------------------------------------------------------------ #
#  Rank strategies
# ------------------------------------------------------------------ #

def valid_rank_list(min_size=2, max_size=6):
    """Generate a list of rank dicts with strictly increasing xp_thresholds."""
    @st.composite
    def _build(draw):
        n = draw(st.integers(min_value=min_size, max_value=max_size))
        xp = 0
        ranks = []
        for level in range(1, n + 1):
            name = draw(st.text(min_size=1, max_size=15, alphabet=st.characters(whitelist_categories=("L",))))
            agent_cap = draw(st.integers(min_value=1, max_value=20))
            planet_access = draw(st.lists(
                st.text(min_size=1, max_size=10, alphabet=st.characters(whitelist_categories=("L",))),
                min_size=0, max_size=3,
            ))
            ranks.append({
                "name": name,
                "level": level,
                "xp_threshold": xp,
                "agent_cap": agent_cap,
                "planet_access": planet_access,
            })
            xp += draw(st.integers(min_value=1, max_value=500))
        return ranks
    return _build()


# ------------------------------------------------------------------ #
#  Technology strategies
# ------------------------------------------------------------------ #

def valid_tech_dict():
    return st.fixed_dictionaries({
        "name": st.text(min_size=1, max_size=20),
        "key": st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("L", "N"))),
        "required_rank": st.text(min_size=1, max_size=15),
        "resource_cost": resource_cost,
        "research_ticks": positive_int,
    })


# ------------------------------------------------------------------ #
#  Powerup strategies
# ------------------------------------------------------------------ #

def valid_powerup_dict():
    return st.fixed_dictionaries({
        "name": st.text(min_size=1, max_size=20),
        "key": st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("L", "N"))),
        "required_rank": st.text(min_size=1, max_size=15),
        "effect_type": st.text(min_size=1, max_size=15),
        "effect_value": st.floats(min_value=0.1, max_value=10.0, allow_nan=False),
        "duration_ticks": positive_int,
        "cooldown_ticks": positive_int,
    })


# ------------------------------------------------------------------ #
#  Terrain strategies
# ------------------------------------------------------------------ #

def valid_terrain_data():
    @st.composite
    def _build(draw):
        n = draw(st.integers(min_value=1, max_value=4))
        terrain_list = []
        terrain_types = []
        for _ in range(n):
            tt = draw(st.text(min_size=1, max_size=15, alphabet=st.characters(whitelist_categories=("L",))))
            ms = draw(two_char_str)
            terrain_list.append({"terrain_type": tt, "map_symbol": ms})
            terrain_types.append(tt)
        planets = [{
            "name": draw(st.text(min_size=1, max_size=15)),
            "terrain_types": terrain_types,
        }]
        return {"terrain": terrain_list, "planets": planets}
    return _build()


# ------------------------------------------------------------------ #
#  Balance strategies
# ------------------------------------------------------------------ #

def valid_balance_dict():
    return st.fixed_dictionaries({
        "turret_damage": positive_int,
        "turret_radius": positive_int,
        "xp_kill": positive_int,
        "xp_building_destroy": positive_int,
        "xp_death_loss": positive_int,
        "gather_amount": positive_int,
        "player_default_health": positive_int,
        "resource_respawn_ticks": positive_int,
        "combat_lockout_ticks": positive_int,
        "chunk_size": positive_int,
        "save_interval": positive_int,
        "metrics_interval": positive_int,
        "tick_interval": st.floats(min_value=0.1, max_value=10.0, allow_nan=False),
        "metrics_enabled": st.booleans(),
    })


# ================================================================== #
#  PROPERTY TESTS — Valid inputs produce no errors
# ================================================================== #

class TestProperty26ValidInputs:
    """Schema validation returns empty error list for valid definitions."""

    @given(building=valid_building_dict())
    @settings(max_examples=50)
    def test_valid_building_produces_no_errors(self, building):
        """**Validates: Requirements 17.2, 17.3**"""
        errors = validator.validate_buildings([building])
        assert errors == [], f"Valid building produced errors: {errors}"

    @given(data=valid_items_data())
    @settings(max_examples=50)
    def test_valid_items_produce_no_errors(self, data):
        """**Validates: Requirements 18.4**"""
        errors = validator.validate_items(data)
        assert errors == [], f"Valid items produced errors: {errors}"

    @given(ranks=valid_rank_list())
    @settings(max_examples=50)
    def test_valid_ranks_produce_no_errors(self, ranks):
        """**Validates: Requirements 19.3**"""
        errors = validator.validate_ranks(ranks)
        assert errors == [], f"Valid ranks produced errors: {errors}"

    @given(tech=valid_tech_dict())
    @settings(max_examples=50)
    def test_valid_tech_produces_no_errors(self, tech):
        """**Validates: Requirements 20.3**"""
        errors = validator.validate_technologies([tech])
        assert errors == [], f"Valid tech produced errors: {errors}"

    @given(powerup=valid_powerup_dict())
    @settings(max_examples=50)
    def test_valid_powerup_produces_no_errors(self, powerup):
        """**Validates: Requirements 22.3**"""
        errors = validator.validate_powerups([powerup])
        assert errors == [], f"Valid powerup produced errors: {errors}"

    @given(data=valid_terrain_data())
    @settings(max_examples=50)
    def test_valid_terrain_produces_no_errors(self, data):
        """**Validates: Requirements 21.4**"""
        errors = validator.validate_terrain(data)
        assert errors == [], f"Valid terrain produced errors: {errors}"

    @given(data=valid_balance_dict())
    @settings(max_examples=50)
    def test_valid_balance_produces_no_errors(self, data):
        """**Validates: Requirements 17.2**"""
        errors = validator.validate_balance(data)
        assert errors == [], f"Valid balance produced errors: {errors}"


# ================================================================== #
#  PROPERTY TESTS — Invalid inputs always produce errors
# ================================================================== #

# Strategy for required building fields to drop
BUILDING_REQUIRED_FIELDS = [
    "name", "abbreviation", "cost", "max_health", "requires_hq", "category",
    "build_time_seconds", "max_level", "rank_requirement", "requires_agent", "storage_capacity",
]
ITEM_REQUIRED_FIELDS = ["key", "name", "slot"]
RANK_REQUIRED_FIELDS = ["name", "level", "xp_threshold", "agent_cap"]
TECH_REQUIRED_FIELDS = ["name", "key", "required_rank", "resource_cost", "research_ticks"]
POWERUP_REQUIRED_FIELDS = ["name", "key", "required_rank", "effect_type", "effect_value", "duration_ticks", "cooldown_ticks"]
TERRAIN_REQUIRED_FIELDS = ["terrain_type", "map_symbol"]


class TestProperty26MissingFields:
    """Schema validation catches definitions with missing required fields."""

    @given(building=valid_building_dict(), field=st.sampled_from(BUILDING_REQUIRED_FIELDS))
    @settings(max_examples=50)
    def test_building_missing_field_produces_error(self, building, field):
        """**Validates: Requirements 17.2, 17.3**"""
        del building[field]
        errors = validator.validate_buildings([building])
        assert len(errors) > 0, f"Missing '{field}' should produce error"

    @given(item=valid_item_dict(), field=st.sampled_from(ITEM_REQUIRED_FIELDS))
    @settings(max_examples=50)
    def test_item_missing_field_produces_error(self, item, field):
        """**Validates: Requirements 18.4**"""
        del item[field]
        data = {"items": [item], "production_map": {}}
        errors = validator.validate_items(data)
        assert len(errors) > 0, f"Missing '{field}' should produce error"

    @given(ranks=valid_rank_list(min_size=2, max_size=4), field=st.sampled_from(RANK_REQUIRED_FIELDS))
    @settings(max_examples=50)
    def test_rank_missing_field_produces_error(self, ranks, field):
        """**Validates: Requirements 19.3**"""
        # Remove field from the last rank entry
        del ranks[-1][field]
        errors = validator.validate_ranks(ranks)
        assert len(errors) > 0, f"Missing '{field}' should produce error"

    @given(tech=valid_tech_dict(), field=st.sampled_from(TECH_REQUIRED_FIELDS))
    @settings(max_examples=50)
    def test_tech_missing_field_produces_error(self, tech, field):
        """**Validates: Requirements 20.3**"""
        del tech[field]
        errors = validator.validate_technologies([tech])
        assert len(errors) > 0, f"Missing '{field}' should produce error"

    @given(powerup=valid_powerup_dict(), field=st.sampled_from(POWERUP_REQUIRED_FIELDS))
    @settings(max_examples=50)
    def test_powerup_missing_field_produces_error(self, powerup, field):
        """**Validates: Requirements 22.3**"""
        del powerup[field]
        errors = validator.validate_powerups([powerup])
        assert len(errors) > 0, f"Missing '{field}' should produce error"

    @given(data=valid_terrain_data(), field=st.sampled_from(TERRAIN_REQUIRED_FIELDS))
    @settings(max_examples=50)
    def test_terrain_missing_field_produces_error(self, data, field):
        """**Validates: Requirements 21.4**"""
        # Remove field from the first terrain entry
        del data["terrain"][0][field]
        errors = validator.validate_terrain(data)
        assert len(errors) > 0, f"Missing '{field}' should produce error"


class TestProperty26WrongTypes:
    """Schema validation catches definitions with wrong field types."""

    @given(building=valid_building_dict())
    @settings(max_examples=50)
    def test_building_max_health_wrong_type(self, building):
        """**Validates: Requirements 17.2, 17.3**"""
        building["max_health"] = "not_an_int"
        errors = validator.validate_buildings([building])
        assert len(errors) > 0, "Non-int max_health should produce error"

    @given(building=valid_building_dict())
    @settings(max_examples=50)
    def test_building_cost_negative_value(self, building):
        """**Validates: Requirements 17.2, 17.3**"""
        building["cost"] = {"wood": -5}
        errors = validator.validate_buildings([building])
        assert len(errors) > 0, "Negative cost should produce error"

    @given(item=valid_item_dict())
    @settings(max_examples=50)
    def test_item_stat_modifiers_wrong_type(self, item):
        """**Validates: Requirements 18.4**"""
        item["stat_modifiers"] = {"damage": "high"}
        data = {"items": [item], "production_map": {}}
        errors = validator.validate_items(data)
        assert len(errors) > 0, "Non-numeric stat_modifier should produce error"

    @given(item=valid_item_dict())
    @settings(max_examples=50)
    def test_item_ammo_cost_wrong_type(self, item):
        """**Validates: Requirements 18.4**"""
        item["ammo_cost"] = "not_a_dict"
        data = {"items": [item], "production_map": {}}
        errors = validator.validate_items(data)
        assert len(errors) > 0, "Non-dict ammo_cost should produce error"

    @given(tech=valid_tech_dict())
    @settings(max_examples=50)
    def test_tech_research_ticks_wrong_type(self, tech):
        """**Validates: Requirements 20.3**"""
        tech["research_ticks"] = "slow"
        errors = validator.validate_technologies([tech])
        assert len(errors) > 0, "Non-int research_ticks should produce error"

    @given(data=valid_balance_dict(), field=st.sampled_from([
        "turret_damage", "turret_radius", "xp_kill", "chunk_size",
    ]))
    @settings(max_examples=50)
    def test_balance_int_field_wrong_type(self, data, field):
        """**Validates: Requirements 17.2**"""
        data[field] = "not_an_int"
        errors = validator.validate_balance(data)
        assert len(errors) > 0, f"Non-int {field} should produce error"

    @given(data=valid_balance_dict())
    @settings(max_examples=50)
    def test_balance_float_field_wrong_type(self, data):
        """**Validates: Requirements 17.2**"""
        data["tick_interval"] = "fast"
        errors = validator.validate_balance(data)
        assert len(errors) > 0, "Non-float tick_interval should produce error"

    @given(data=valid_balance_dict())
    @settings(max_examples=50)
    def test_balance_bool_field_wrong_type(self, data):
        """**Validates: Requirements 17.2**"""
        data["metrics_enabled"] = "yes"
        errors = validator.validate_balance(data)
        assert len(errors) > 0, "Non-bool metrics_enabled should produce error"


class TestProperty26TopLevelWrongTypes:
    """Schema validation catches wrong top-level types for each definition."""

    @given(bad_input=st.one_of(st.text(), st.integers(), st.none(), st.dictionaries(st.text(), st.text())))
    @settings(max_examples=30)
    def test_buildings_rejects_non_list(self, bad_input):
        """**Validates: Requirements 17.3**"""
        assume(not isinstance(bad_input, list))
        errors = validator.validate_buildings(bad_input)
        assert len(errors) > 0, "Non-list input should produce error"

    @given(bad_input=st.one_of(st.text(), st.integers(), st.none(), st.lists(st.text())))
    @settings(max_examples=30)
    def test_items_rejects_non_dict(self, bad_input):
        """**Validates: Requirements 18.4**"""
        assume(not isinstance(bad_input, dict))
        errors = validator.validate_items(bad_input)
        assert len(errors) > 0, "Non-dict input should produce error"

    @given(bad_input=st.one_of(st.text(), st.integers(), st.none(), st.dictionaries(st.text(), st.text())))
    @settings(max_examples=30)
    def test_ranks_rejects_non_list(self, bad_input):
        """**Validates: Requirements 19.3**"""
        assume(not isinstance(bad_input, list))
        errors = validator.validate_ranks(bad_input)
        assert len(errors) > 0, "Non-list input should produce error"

    @given(bad_input=st.one_of(st.text(), st.integers(), st.none(), st.dictionaries(st.text(), st.text())))
    @settings(max_examples=30)
    def test_technologies_rejects_non_list(self, bad_input):
        """**Validates: Requirements 20.3**"""
        assume(not isinstance(bad_input, list))
        errors = validator.validate_technologies(bad_input)
        assert len(errors) > 0, "Non-list input should produce error"

    @given(bad_input=st.one_of(st.text(), st.integers(), st.none(), st.dictionaries(st.text(), st.text())))
    @settings(max_examples=30)
    def test_powerups_rejects_non_list(self, bad_input):
        """**Validates: Requirements 22.3**"""
        assume(not isinstance(bad_input, list))
        errors = validator.validate_powerups(bad_input)
        assert len(errors) > 0, "Non-list input should produce error"

    @given(bad_input=st.one_of(st.text(), st.integers(), st.none(), st.lists(st.text())))
    @settings(max_examples=30)
    def test_terrain_rejects_non_dict(self, bad_input):
        """**Validates: Requirements 21.4**"""
        assume(not isinstance(bad_input, dict))
        errors = validator.validate_terrain(bad_input)
        assert len(errors) > 0, "Non-dict input should produce error"

    @given(bad_input=st.one_of(st.text(), st.integers(), st.none(), st.lists(st.text())))
    @settings(max_examples=30)
    def test_balance_rejects_non_dict(self, bad_input):
        """**Validates: Requirements 17.2**"""
        assume(not isinstance(bad_input, dict))
        errors = validator.validate_balance(bad_input)
        assert len(errors) > 0, "Non-dict input should produce error"


class TestProperty26RankXPOrdering:
    """Schema validation catches non-strictly-increasing xp_thresholds in ranks.

    **Validates: Requirements 19.3**
    """

    @given(ranks=valid_rank_list(min_size=3, max_size=6))
    @settings(max_examples=50)
    def test_swapped_xp_thresholds_produce_error(self, ranks):
        """Swap two adjacent xp_thresholds to break strict ordering."""
        # Pick two adjacent ranks and swap their xp values
        i = len(ranks) // 2
        assume(ranks[i]["xp_threshold"] != ranks[i - 1]["xp_threshold"])
        ranks[i]["xp_threshold"], ranks[i - 1]["xp_threshold"] = (
            ranks[i - 1]["xp_threshold"],
            ranks[i]["xp_threshold"],
        )
        errors = validator.validate_ranks(ranks)
        assert len(errors) > 0, "Non-strictly-increasing xp_thresholds should produce error"

    @given(ranks=valid_rank_list(min_size=3, max_size=6))
    @settings(max_examples=50)
    def test_duplicate_xp_thresholds_produce_error(self, ranks):
        """Set two ranks to the same xp_threshold to break strict ordering."""
        # Make the last rank have the same xp as the second-to-last
        ranks[-1]["xp_threshold"] = ranks[-2]["xp_threshold"]
        errors = validator.validate_ranks(ranks)
        assert len(errors) > 0, "Duplicate xp_thresholds should produce error"


# ================================================================== #
#  PROPERTY 14 — Schema fail-fast (equipment-items feature)
#
#  **Validates: Requirements 3.4, 3.5, 3.6, 4.5, 5.7, 5.8, 13.5,
#  15.1, 15.2**
#
#  The validator reports an error IF AND ONLY IF an item/balance config
#  violates one of the item/balance schema rules:
#    - a bad `category`                                    (Req 3.4)
#    - a Gear item with a slot not in EQUIPMENT_SLOTS      (Req 3.5, 3.6)
#    - a weapon with weapon_type not in {melee, ranged}    (Req 4.5)
#    - an `ammo_type` not referencing an ammo item         (Req 5.7)
#    - `ammo_type`/`magazine_size` on a melee weapon       (Req 5.8)
#    - a negative `weight`                                 (Req 15.1)
#    - an `effect.type` not in EFFECT_TYPES                (Req 13.5)
#    - a `resource_weights` key not in RESOURCE_TYPES /
#      a negative value                                    (Req 15.2)
#
#  Rules are validated by three collaborating methods:
#    - validate_items       (per-item category/slot/weapon_type/weight/effect)
#    - cross_validate       (ammo_type FK + melee ammo-field rejection)
#    - validate_balance     (resource_weights)
# ================================================================== #

_ITEM_KEY = st.text(
    min_size=1, max_size=12,
    alphabet=st.characters(whitelist_categories=("L", "N")),
)
_ITEM_NAME = st.text(min_size=1, max_size=20)

# Category strings guaranteed to be OUTSIDE the controlled vocabulary.
_BAD_CATEGORIES = ["gadget", "junk", "tool", "misc", "food", "material"]
# A slot string guaranteed to be outside EQUIPMENT_SLOTS.
_BAD_SLOT = "not_a_real_slot"


@st.composite
def clean_item_dict(draw):
    """A single item dict the validator MUST accept (no validate_items error)."""
    category = draw(st.sampled_from(list(ITEM_CATEGORIES)))
    item = {
        "key": draw(_ITEM_KEY),
        "name": draw(_ITEM_NAME),
        "category": category,
    }
    if category in GEAR_CATEGORIES:
        # A weapon must occupy the `weapon` slot (combat resolves it there);
        # armor/accessory gear may occupy any body slot.
        if category == "weapon":
            item["slot"] = "weapon"
        else:
            item["slot"] = draw(st.sampled_from(list(EQUIPMENT_SLOTS)))
    if category == "weapon":
        wt = draw(st.sampled_from(list(WEAPON_TYPES)))
        item["weapon_type"] = wt
        if wt == "ranged":
            if draw(st.booleans()):
                item["magazine_size"] = draw(st.integers(min_value=1, max_value=100))
            if draw(st.booleans()):
                item["ammo_per_shot"] = draw(st.integers(min_value=1, max_value=10))
    if category in ("consumable", "throwable") and draw(st.booleans()):
        item["effect"] = {"type": draw(st.sampled_from(list(EFFECT_TYPES)))}
    if draw(st.booleans()):
        item["weight"] = draw(
            st.floats(min_value=0, max_value=100, allow_nan=False, allow_infinity=False)
        )
    return item


@st.composite
def item_and_validity(draw):
    """Yield ``(item_dict, expect_error)`` for the validate_items iff test.

    With ~50% probability a single guaranteed-invalid mutation is injected;
    otherwise the item is left clean. ``expect_error`` records which.
    """
    item = draw(clean_item_dict())
    if not draw(st.booleans()):
        return item, False

    category = item["category"]
    defects = ["bad_category", "negative_weight"]
    if category in GEAR_CATEGORIES:
        defects.append("bad_slot")
    if category == "weapon":
        defects.append("bad_weapon_type")
    else:
        defects.append("weapon_type_on_nonweapon")
    if category in ("consumable", "throwable"):
        defects.append("bad_effect_type")

    defect = draw(st.sampled_from(defects))
    if defect == "bad_category":
        item["category"] = draw(st.sampled_from(_BAD_CATEGORIES))
    elif defect == "negative_weight":
        item["weight"] = draw(
            st.floats(min_value=-100, max_value=-0.01, allow_nan=False, allow_infinity=False)
        )
    elif defect == "bad_slot":
        item["slot"] = _BAD_SLOT
    elif defect == "bad_weapon_type":
        item["weapon_type"] = draw(st.sampled_from(["gun", "sword", "laser", ""]))
    elif defect == "weapon_type_on_nonweapon":
        item["weapon_type"] = draw(st.sampled_from(list(WEAPON_TYPES)))
    elif defect == "bad_effect_type":
        item["effect"] = {"type": draw(st.sampled_from(["explode", "freeze", "", "damage"]))}
    return item, True


def _make_registry(items_dict):
    """A minimal DataRegistry-shaped object for cross_validate.

    Every collection except ``items`` is empty, so only the ammo_type FK and
    melee ammo-field rules can produce errors (all ItemDefs use the default
    ``required_rank=None``/``ammo_cost=None``).
    """
    return SimpleNamespace(
        terrain={},
        ranks=[],
        buildings={},
        items=items_dict,
        technologies={},
        powerups={},
        item_production_map={},
        planets={},
    )


@st.composite
def ammo_fk_case(draw):
    """Yield ``(items_dict, expect_error)`` exercising the ammo_type FK (Req 5.7)."""
    ammo_key = draw(_ITEM_KEY)
    weapon_key = draw(_ITEM_KEY)
    assume(ammo_key != weapon_key)

    if not draw(st.booleans()):
        # Valid: ranged weapon references an existing ammo-category item.
        items = {
            ammo_key: ItemDef(key=ammo_key, name="ammo", category="ammo", slot=""),
            weapon_key: ItemDef(
                key=weapon_key, name="rifle", category="weapon",
                weapon_type="ranged", slot="weapon",
                ammo_type=ammo_key, magazine_size=10,
            ),
        }
        return items, False

    # Invalid: either a dangling reference or a reference to a non-ammo item.
    mode = draw(st.sampled_from(["dangling", "wrong_category"]))
    items = {
        weapon_key: ItemDef(
            key=weapon_key, name="rifle", category="weapon",
            weapon_type="ranged", slot="weapon",
            ammo_type=ammo_key, magazine_size=10,
        ),
    }
    if mode == "wrong_category":
        other = draw(st.sampled_from(["armor", "consumable", "throwable", "accessory", "weapon"]))
        items[ammo_key] = ItemDef(key=ammo_key, name="x", category=other, slot="")
    # (dangling: ammo_key intentionally absent from items)
    return items, True


@st.composite
def melee_ammo_case(draw):
    """Yield ``(items_dict, expect_error)`` exercising melee ammo-field rejection (Req 5.8)."""
    weapon_key = draw(_ITEM_KEY)

    if not draw(st.booleans()):
        # Valid melee weapon: no ammo_type, no magazine_size, default ammo_per_shot.
        items = {
            weapon_key: ItemDef(
                key=weapon_key, name="knife", category="weapon",
                weapon_type="melee", slot="weapon",
            ),
        }
        return items, False

    # Invalid: a melee weapon that declares one of the forbidden ammo fields.
    defect = draw(st.sampled_from(["ammo_type", "magazine_size", "ammo_per_shot"]))
    weapon = ItemDef(
        key=weapon_key, name="knife", category="weapon",
        weapon_type="melee", slot="weapon",
    )
    items = {weapon_key: weapon}
    if defect == "ammo_type":
        ammo_key = draw(_ITEM_KEY)
        assume(ammo_key != weapon_key)
        # A real ammo item exists so the FK passes; only the melee rule fires.
        items[ammo_key] = ItemDef(key=ammo_key, name="ammo", category="ammo", slot="")
        weapon.ammo_type = ammo_key
    elif defect == "magazine_size":
        weapon.magazine_size = draw(st.integers(min_value=1, max_value=50))
    else:  # ammo_per_shot != 1
        weapon.ammo_per_shot = draw(st.integers(min_value=2, max_value=10))
    return items, True


@st.composite
def resource_weights_case(draw):
    """Yield ``(balance_dict, expect_error)`` exercising resource_weights (Req 15.2)."""
    if not draw(st.booleans()):
        # Valid: keys subset of RESOURCE_TYPES, values >= 0.
        keys = draw(st.lists(
            st.sampled_from(list(RESOURCE_TYPES)),
            unique=True, max_size=len(RESOURCE_TYPES),
        ))
        rw = {
            k: draw(st.floats(min_value=0, max_value=100, allow_nan=False, allow_infinity=False))
            for k in keys
        }
        return {"resource_weights": rw}, False

    mode = draw(st.sampled_from(["bad_key", "negative_value"]))
    if mode == "bad_key":
        bad = draw(
            st.text(min_size=1, max_size=10).filter(lambda s: s not in RESOURCE_TYPES)
        )
        rw = {bad: draw(st.floats(min_value=0, max_value=10, allow_nan=False, allow_infinity=False))}
    else:
        k = draw(st.sampled_from(list(RESOURCE_TYPES)))
        rw = {k: draw(
            st.floats(min_value=-100, max_value=-0.01, allow_nan=False, allow_infinity=False)
        )}
    return {"resource_weights": rw}, True


class TestProperty14SchemaFailFast:
    """Property 14: the validator flags an item/balance config iff it violates a rule."""

    @given(case=item_and_validity())
    @settings(max_examples=300)
    def test_validate_items_iff(self, case):
        """validate_items errors iff a per-item rule is violated.

        Covers bad category (Req 3.4), Gear slot (Req 3.5/3.6), weapon_type
        (Req 4.5), negative weight (Req 15.1), and effect.type (Req 13.5).
        """
        item, expect_error = case
        data = {"items": [item], "production_map": {}}
        errors = validator.validate_items(data)
        assert bool(errors) == expect_error, (
            f"expected error={expect_error}, got errors={errors} for item={item}"
        )

    @given(case=ammo_fk_case())
    @settings(max_examples=200)
    def test_cross_validate_ammo_type_fk_iff(self, case):
        """cross_validate errors iff a weapon's ammo_type does not reference an ammo item.

        **Validates: Requirements 5.7**
        """
        items, expect_error = case
        errors = validator.cross_validate(_make_registry(items))
        assert bool(errors) == expect_error, (
            f"expected error={expect_error}, got errors={errors} for items={items}"
        )

    @given(case=melee_ammo_case())
    @settings(max_examples=200)
    def test_cross_validate_melee_ammo_fields_iff(self, case):
        """cross_validate errors iff a melee weapon declares ammo fields.

        **Validates: Requirements 5.8**
        """
        items, expect_error = case
        errors = validator.cross_validate(_make_registry(items))
        assert bool(errors) == expect_error, (
            f"expected error={expect_error}, got errors={errors} for items={items}"
        )

    @given(case=resource_weights_case())
    @settings(max_examples=200)
    def test_validate_balance_resource_weights_iff(self, case):
        """validate_balance errors iff resource_weights has a bad key or negative value.

        **Validates: Requirements 15.2**
        """
        data, expect_error = case
        errors = validator.validate_balance(data)
        assert bool(errors) == expect_error, (
            f"expected error={expect_error}, got errors={errors} for data={data}"
        )
