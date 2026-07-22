"""
Property-based tests for coords_of totality.

**Validates: Requirements 3.2, 3.3**

# Feature: refactor-foundations, Property 2: coords_of correctness and None-safety
# across arbitrary entity shapes

For any entity shape — no ``db`` attribute, ``db=None``, or a plain-namespace
``db`` carrying any subset of ``coord_x``/``coord_y``/``coord_planet`` with
values drawn from ints and None — ``coords_of`` never raises. It returns
``(x, y, planet)`` exactly when both coordinates are present and non-None
(``planet`` is the stored ``coord_planet`` value, or None when unset), and
returns None otherwise.
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from mygame.world.utils import coords_of, get_coords

# ------------------------------------------------------------------ #
#  Entity-shape test doubles
# ------------------------------------------------------------------ #


class NoDbEntity:
    """Entity shape with no ``db`` attribute at all."""


class DbEntity:
    """Entity whose ``db`` is whatever it was constructed with (may be None)."""

    def __init__(self, db):
        self.db = db


class Namespace:
    """Plain attribute namespace standing in for Evennia's db handler."""

    def __init__(self, attrs):
        for key, value in attrs.items():
            setattr(self, key, value)


# ------------------------------------------------------------------ #
#  Strategies (shared: Property 3 reuses entity_st for get_coords)
# ------------------------------------------------------------------ #

# Values a stored coordinate attribute may hold: ints or an explicit None.
coord_value_st = st.one_of(st.none(), st.integers())

# Any subset of the three coordinate attributes, each mapped to a value.
db_attrs_st = st.dictionaries(
    keys=st.sampled_from(["coord_x", "coord_y", "coord_planet"]),
    values=coord_value_st,
    max_size=3,
)

# Yields (entity, attrs): ``attrs`` is None for the no-db and db=None shapes,
# and the dict of explicitly-set db attributes otherwise. Absent dict keys
# model attributes that were never set on the namespace.
entity_st = st.one_of(
    st.builds(lambda: (NoDbEntity(), None)),
    st.builds(lambda: (DbEntity(None), None)),
    db_attrs_st.map(lambda attrs: (DbEntity(Namespace(attrs)), attrs)),
)


# ------------------------------------------------------------------ #
#  Property 2: coords_of correctness and None-safety across arbitrary
#  entity shapes
# ------------------------------------------------------------------ #


class TestProperty2CoordsOfTotality:
    """coords_of: no exception, triple iff both coordinates present/non-None."""

    @given(entity_and_attrs=entity_st)
    @settings(max_examples=200)
    def test_coords_of_never_raises_and_matches_contract(self, entity_and_attrs):
        entity, attrs = entity_and_attrs

        # Totality: any entity shape must be handled without raising.
        result = coords_of(entity)

        if attrs is None:
            # No db handler (missing attribute or db=None) -> None.
            assert result is None
            return

        cx = attrs.get("coord_x")
        cy = attrs.get("coord_y")
        if cx is None or cy is None:
            # Either coordinate absent or explicitly None -> None.
            assert result is None
        else:
            # Both coordinates present and non-None -> exact stored triple,
            # with planet None when coord_planet is unset.
            assert result == (cx, cy, attrs.get("coord_planet"))


# ------------------------------------------------------------------ #
#  Property 3: get_coords is the (x, y) projection of coords_of
# ------------------------------------------------------------------ #

# Feature: refactor-foundations, Property 3: get_coords is the (x, y) projection
# of coords_of


class TestProperty3GetCoordsProjection:
    """get_coords: (int(x), int(y)) when coords_of yields a triple, None otherwise.

    **Validates: Requirements 3.9**
    """

    @given(entity_and_attrs=entity_st)
    @settings(max_examples=200)
    def test_get_coords_projects_coords_of(self, entity_and_attrs):
        entity, _attrs = entity_and_attrs

        triple = coords_of(entity)
        result = get_coords(entity)

        if triple is None:
            # get_coords is None exactly when coords_of is None.
            assert result is None
        else:
            x, y, _planet = triple
            # get_coords keeps its int-coercing (x, y) projection contract.
            assert result == (int(x), int(y))
