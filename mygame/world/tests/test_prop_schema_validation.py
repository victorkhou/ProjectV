"""
Property-based tests for SchemaValidator.

**Validates: Requirements 17.2, 17.3, 17.4, 17.5, 18.4, 19.3, 20.3, 21.4, 22.3**

Property 26: Schema validation catches invalid definitions
- The validator NEVER returns an empty error list for structurally invalid input.
- The validator ALWAYS returns an empty error list for valid input.
"""

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from mygame.world.schema_validator import SchemaValidator
from mygame.world.constants import MAX_BUILDING_LEVEL

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
    return st.fixed_dictionaries({
        "key": st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("L", "N"))),
        "name": st.text(min_size=1, max_size=20),
        "slot": st.sampled_from(["weapon", "armor", "gadget", "consumable"]),
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
        "xp_damage": st.floats(min_value=0.01, max_value=10.0, allow_nan=False),
        "tick_interval": st.floats(min_value=0.1, max_value=10.0, allow_nan=False),
        "metrics_enabled": st.booleans(),
        "production_scaling": st.just({1: 10, 2: 50, 3: 150, 4: 400, 5: 1000}),
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
RANK_REQUIRED_FIELDS = ["name", "level", "xp_threshold", "agent_cap", "planet_access"]
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
