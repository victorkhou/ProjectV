"""
Property-based tests for definition dataclass round-trip and validation.

**Property 1: Definition YAML Round-Trip**
For any valid game definition object (CoordinateSpaceDef, TerrainDef,
BuildingDef, RankDef), serializing it to a dict (using dataclasses.asdict)
and deserializing back SHALL produce an equivalent object.
**Validates: Requirements 1.7, 15.6**

**Property 2: Definition Validation Rejects Invalid Input**
For any definition dict that is missing a required field or has an invalid
field value, the corresponding validator SHALL reject it. For any definition
dict with all required fields present and valid, the validator SHALL accept it.
**Validates: Requirements 1.2, 2.2, 6.2**
"""

from dataclasses import asdict

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from mygame.world.definitions import (
    BuildingDef,
    CoordinateSpaceDef,
    RankDef,
    TerrainDef,
)
from mygame.world.constants import MAX_BUILDING_LEVEL
from mygame.world.schema_validator import SchemaValidator

validator = SchemaValidator()

# ------------------------------------------------------------------ #
#  Shared strategies
# ------------------------------------------------------------------ #

positive_int = st.integers(min_value=1, max_value=10_000)
non_negative_int = st.integers(min_value=0, max_value=10_000)
short_text = st.text(
    min_size=1, max_size=20,
    alphabet=st.characters(whitelist_categories=("L",)),
)
two_char_str = st.text(
    min_size=2, max_size=2,
    alphabet=st.characters(whitelist_categories=("L",)),
)
resource_cost = st.dictionaries(
    short_text, positive_int, min_size=1, max_size=4,
)


# ------------------------------------------------------------------ #
#  Dataclass strategies for round-trip (Property 1)
# ------------------------------------------------------------------ #

@st.composite
def coordinate_space_defs(draw):
    """Generate a valid CoordinateSpaceDef."""
    # Build terrain_weights that sum to ~1.0
    n_weights = draw(st.integers(min_value=0, max_value=6))
    weights = {}
    if n_weights > 0:
        raw = [draw(st.floats(min_value=0.01, max_value=1.0, allow_nan=False, allow_infinity=False))
               for _ in range(n_weights)]
        total = sum(raw)
        for i, r in enumerate(raw):
            key = draw(short_text)
            weights[key] = round(r / total, 4)

    return CoordinateSpaceDef(
        planet_key=draw(short_text),
        planet_type=draw(short_text),
        width=draw(st.integers(min_value=1, max_value=2000)),
        height=draw(st.integers(min_value=1, max_value=2000)),
        terrain_seed=draw(st.integers(min_value=0, max_value=999999)),
        terrain_noise_cell_size=draw(st.integers(min_value=1, max_value=64)),
        terrain_weights=weights,
        persistence_type=draw(st.sampled_from(["static", "dynamic"])),
        spawn_x=draw(non_negative_int),
        spawn_y=draw(non_negative_int),
        default_planet=draw(st.booleans()),
        z_level=draw(st.integers(min_value=0, max_value=100)),
        seed_rotation_ticks=draw(non_negative_int),
        rank_requirement=draw(positive_int),
    )


@st.composite
def terrain_defs(draw):
    """Generate a valid TerrainDef."""
    return TerrainDef(
        terrain_type=draw(short_text),
        map_symbol=draw(two_char_str),
        resource_type=draw(st.one_of(st.none(), short_text)),
        passable=draw(st.booleans()),
    )


@st.composite
def building_defs(draw):
    """Generate a valid BuildingDef."""
    return BuildingDef(
        name=draw(short_text),
        abbreviation=draw(two_char_str),
        cost=draw(resource_cost),
        max_health=draw(positive_int),
        requires_hq=draw(st.booleans()),
        required_terrain=draw(st.one_of(st.none(), short_text)),
        category=draw(st.sampled_from(["headquarters", "resource", "defense", "research", "equipment"])),
        produces=draw(st.one_of(st.none(), short_text)),
        unlocks=draw(st.lists(short_text, max_size=4)),
        map_symbol=draw(two_char_str),
        build_time_seconds=draw(positive_int),
        max_level=draw(st.integers(min_value=1, max_value=MAX_BUILDING_LEVEL)),
        rank_requirement=draw(positive_int),
        requires_agent=draw(st.booleans()),
        storage_capacity=draw(non_negative_int),
    )


@st.composite
def rank_defs(draw):
    """Generate a valid RankDef."""
    return RankDef(
        name=draw(short_text),
        level=draw(positive_int),
        xp_threshold=draw(non_negative_int),
        unlocks=draw(st.lists(short_text, max_size=4)),
        agent_cap=draw(st.integers(min_value=1, max_value=20)),
        planet_access=draw(st.lists(short_text, max_size=5)),
    )


# ================================================================== #
#  Property 1: Definition YAML Round-Trip
# ================================================================== #

class TestProperty1DefinitionRoundTrip:
    """For any valid definition object, asdict → constructor round-trip
    produces an equivalent object.

    **Validates: Requirements 1.7, 15.6**
    """

    @given(cs=coordinate_space_defs())
    @settings(max_examples=100)
    def test_coordinate_space_def_round_trip(self, cs):
        """CoordinateSpaceDef survives dict serialization round-trip."""
        d = asdict(cs)
        restored = CoordinateSpaceDef(**d)
        assert restored == cs

    @given(td=terrain_defs())
    @settings(max_examples=100)
    def test_terrain_def_round_trip(self, td):
        """TerrainDef survives dict serialization round-trip."""
        d = asdict(td)
        restored = TerrainDef(**d)
        assert restored == td

    @given(bd=building_defs())
    @settings(max_examples=100)
    def test_building_def_round_trip(self, bd):
        """BuildingDef survives dict serialization round-trip."""
        d = asdict(bd)
        restored = BuildingDef(**d)
        assert restored == bd

    @given(rd=rank_defs())
    @settings(max_examples=100)
    def test_rank_def_round_trip(self, rd):
        """RankDef survives dict serialization round-trip."""
        d = asdict(rd)
        restored = RankDef(**d)
        assert restored == rd


# ================================================================== #
#  Property 2: Definition Validation Rejects Invalid Input
# ================================================================== #

# ---- Strategies for valid validator dicts ----

def valid_building_validator_dict():
    """Dict matching what SchemaValidator.validate_buildings expects."""
    return st.fixed_dictionaries({
        "name": short_text,
        "abbreviation": two_char_str,
        "cost": resource_cost,
        "max_health": positive_int,
        "requires_hq": st.booleans(),
        "category": st.sampled_from(["headquarters", "resource", "defense", "research", "equipment"]),
        "build_time_seconds": positive_int,
        "max_level": st.integers(min_value=1, max_value=MAX_BUILDING_LEVEL),
        "rank_requirement": positive_int,
        "requires_agent": st.booleans(),
        "storage_capacity": non_negative_int,
    })


@st.composite
def valid_rank_validator_list(draw, min_size=2, max_size=6):
    """List of rank dicts with strictly increasing xp_thresholds."""
    n = draw(st.integers(min_value=min_size, max_value=max_size))
    xp = 0
    ranks = []
    for level in range(1, n + 1):
        ranks.append({
            "name": draw(short_text),
            "level": level,
            "xp_threshold": xp,
            "agent_cap": draw(st.integers(min_value=1, max_value=20)),
            "planet_access": draw(st.lists(short_text, min_size=0, max_size=3)),
        })
        xp += draw(st.integers(min_value=1, max_value=500))
    return ranks


def valid_terrain_validator_dict():
    """Dict matching what SchemaValidator.validate_terrain expects."""
    @st.composite
    def _build(draw):
        n = draw(st.integers(min_value=1, max_value=4))
        terrain_list = []
        terrain_types = []
        for _ in range(n):
            tt = draw(short_text)
            terrain_list.append({
                "terrain_type": tt,
                "map_symbol": draw(two_char_str),
            })
            terrain_types.append(tt)
        return {
            "terrain": terrain_list,
            "planets": [{"name": draw(short_text), "terrain_types": terrain_types}],
        }
    return _build()


# ---- Required field lists for each validator ----

BUILDING_REQUIRED = [
    "name", "abbreviation", "cost", "max_health", "requires_hq", "category",
    "build_time_seconds", "max_level", "rank_requirement", "requires_agent",
    "storage_capacity",
]
RANK_REQUIRED = ["name", "level", "xp_threshold", "agent_cap"]
TERRAIN_REQUIRED = ["terrain_type", "map_symbol"]


class TestProperty2ValidInputAccepted:
    """Valid definition dicts are accepted by the validator.

    **Validates: Requirements 1.2, 2.2, 6.2**
    """

    @given(building=valid_building_validator_dict())
    @settings(max_examples=100)
    def test_valid_building_accepted(self, building):
        errors = validator.validate_buildings([building])
        assert errors == [], f"Valid building rejected: {errors}"

    @given(ranks=valid_rank_validator_list())
    @settings(max_examples=100)
    def test_valid_ranks_accepted(self, ranks):
        errors = validator.validate_ranks(ranks)
        assert errors == [], f"Valid ranks rejected: {errors}"

    @given(data=valid_terrain_validator_dict())
    @settings(max_examples=100)
    def test_valid_terrain_accepted(self, data):
        errors = validator.validate_terrain(data)
        assert errors == [], f"Valid terrain rejected: {errors}"


class TestProperty2MissingFieldRejected:
    """Dicts missing a required field are rejected by the validator.

    **Validates: Requirements 1.2, 2.2, 6.2**
    """

    @given(
        building=valid_building_validator_dict(),
        field=st.sampled_from(BUILDING_REQUIRED),
    )
    @settings(max_examples=100)
    def test_building_missing_field_rejected(self, building, field):
        del building[field]
        errors = validator.validate_buildings([building])
        assert len(errors) > 0, f"Missing '{field}' should be rejected"

    @given(
        ranks=valid_rank_validator_list(min_size=2, max_size=4),
        field=st.sampled_from(RANK_REQUIRED),
    )
    @settings(max_examples=100)
    def test_rank_missing_field_rejected(self, ranks, field):
        del ranks[-1][field]
        errors = validator.validate_ranks(ranks)
        assert len(errors) > 0, f"Missing '{field}' should be rejected"

    @given(
        data=valid_terrain_validator_dict(),
        field=st.sampled_from(TERRAIN_REQUIRED),
    )
    @settings(max_examples=100)
    def test_terrain_missing_field_rejected(self, data, field):
        del data["terrain"][0][field]
        errors = validator.validate_terrain(data)
        assert len(errors) > 0, f"Missing '{field}' should be rejected"


class TestProperty2InvalidValueRejected:
    """Dicts with invalid field values are rejected by the validator.

    **Validates: Requirements 1.2, 2.2, 6.2**
    """

    @given(building=valid_building_validator_dict())
    @settings(max_examples=100)
    def test_building_negative_max_health_rejected(self, building):
        building["max_health"] = -1
        errors = validator.validate_buildings([building])
        assert len(errors) > 0, "Negative max_health should be rejected"

    @given(building=valid_building_validator_dict())
    @settings(max_examples=100)
    def test_building_wrong_abbreviation_length_rejected(self, building):
        building["abbreviation"] = "ABC"
        errors = validator.validate_buildings([building])
        assert len(errors) > 0, "3-char abbreviation should be rejected"

    @given(building=valid_building_validator_dict())
    @settings(max_examples=100)
    def test_building_negative_cost_rejected(self, building):
        building["cost"] = {"Wood": -5}
        errors = validator.validate_buildings([building])
        assert len(errors) > 0, "Negative cost should be rejected"

    @given(building=valid_building_validator_dict())
    @settings(max_examples=100)
    def test_building_zero_build_time_rejected(self, building):
        building["build_time_seconds"] = 0
        errors = validator.validate_buildings([building])
        assert len(errors) > 0, "Zero build_time_seconds should be rejected"

    @given(building=valid_building_validator_dict())
    @settings(max_examples=100)
    def test_building_non_bool_requires_agent_rejected(self, building):
        building["requires_agent"] = "yes"
        errors = validator.validate_buildings([building])
        assert len(errors) > 0, "Non-bool requires_agent should be rejected"

    @given(building=valid_building_validator_dict())
    @settings(max_examples=100)
    def test_building_negative_storage_capacity_rejected(self, building):
        building["storage_capacity"] = -1
        errors = validator.validate_buildings([building])
        assert len(errors) > 0, "Negative storage_capacity should be rejected"

    @given(ranks=valid_rank_validator_list(min_size=2, max_size=4))
    @settings(max_examples=100)
    def test_rank_negative_agent_cap_rejected(self, ranks):
        ranks[-1]["agent_cap"] = -1
        errors = validator.validate_ranks(ranks)
        assert len(errors) > 0, "Negative agent_cap should be rejected"

    @given(ranks=valid_rank_validator_list(min_size=2, max_size=4))
    @settings(max_examples=100)
    def test_rank_non_list_planet_access_rejected(self, ranks):
        ranks[-1]["planet_access"] = "terra"
        errors = validator.validate_ranks(ranks)
        assert len(errors) > 0, "Non-list planet_access should be rejected"

    @given(data=valid_terrain_validator_dict())
    @settings(max_examples=100)
    def test_terrain_wrong_symbol_length_rejected(self, data):
        data["terrain"][0]["map_symbol"] = "ABC"
        errors = validator.validate_terrain(data)
        assert len(errors) > 0, "3-char map_symbol should be rejected"
