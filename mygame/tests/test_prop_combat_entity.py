"""
Property-based tests for CombatEntity mixin.

**Property 10: CombatEntity Damage/Heal Round-Trip**
For any CombatEntity with hp and hp_max, and any positive integer N
where N <= hp, calling take_damage(N) then heal(N) SHALL return hp
to its pre-damage value, capped at hp_max.
**Validates: Requirements 7.11**
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from mygame.typeclasses.combat_entity import CombatEntity


# ------------------------------------------------------------------ #
#  Lightweight host class (mirrors test_combat_entity.py pattern)
# ------------------------------------------------------------------ #

class _AttrStore:
    """Minimal Evennia-style attribute store."""
    def __init__(self):
        self._data = {}
    def get(self, key, default=None, **kw):
        return self._data.get(key, default)
    def add(self, key, value, **kw):
        self._data[key] = value
    def has(self, key):
        return key in self._data


class _DbProxy:
    """Minimal proxy mimicking Evennia's db handler."""
    def __init__(self, store):
        object.__setattr__(self, "_store", store)
    def __getattr__(self, key):
        return object.__getattribute__(self, "_store").get(key)
    def __setattr__(self, key, value):
        object.__getattribute__(self, "_store").add(key, value)


class _Host(CombatEntity):
    """Fake host class providing self.db like Evennia typeclasses."""
    def __init__(self):
        self._attr_store = _AttrStore()
        self.db = _DbProxy(self._attr_store)
        self.at_combat_entity_init()


# ------------------------------------------------------------------ #
#  Property 10: CombatEntity Damage/Heal Round-Trip
# ------------------------------------------------------------------ #

class TestProperty10DamageHealRoundTrip:
    """
    **Validates: Requirements 7.11**

    For any CombatEntity with hp and hp_max, and any positive integer N
    where N <= hp, calling take_damage(N) then heal(N) SHALL return hp
    to its pre-damage value, capped at hp_max.
    """

    @given(
        hp_max=st.integers(min_value=1, max_value=10_000),
        data=st.data(),
    )
    @settings(max_examples=200)
    def test_damage_heal_round_trip(self, hp_max, data):
        entity = _Host()
        entity.db.hp_max = hp_max
        entity.db.hp = data.draw(
            st.integers(min_value=1, max_value=hp_max), label="hp"
        )
        pre_hp = entity.db.hp

        n = data.draw(
            st.integers(min_value=1, max_value=pre_hp), label="damage_amount"
        )

        entity.take_damage(n)
        # If take_damage drove hp to 0, the entity is incapacitated;
        # reset incapacitation so heal can work on the same entity.
        if entity.db.incapacitated:
            entity.db.incapacitated = False

        entity.heal(n)

        expected = min(pre_hp, hp_max)
        assert entity.db.hp == expected, (
            f"hp={entity.db.hp}, expected={expected} "
            f"(hp_max={hp_max}, pre_hp={pre_hp}, N={n})"
        )
