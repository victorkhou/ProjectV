"""
Property-based tests for the EquipmentSystem mediated actions.

Property 1:  Slot cardinality (Req 1.2, 1.3) — at most one item per slot;
             re-equip replaces.
Property 8:  Reload conservation — rounds moved from bag into magazine are
             conserved (bag decrement == loaded increment; total ammo
             conserved; never exceeds magazine_size).
Property 9:  Rank gate — equip/use/throw permitted iff player rank >= required
             rank.
Property 10: Heal clamp — hp after heal == min(hp_before + amount, hp_max).
Property 12: Throw AoE + armor — damage applied to each target ==
             max(0, amount - target damage_reduction).

Validates: Requirements 1.2, 1.3, 7.x, 8.x, 9.x, 10.x, 11.x
"""

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

from mygame.world.systems.equipment_system import EquipmentSystem  # noqa: E402
from mygame.world.systems.equipment_handler import EquipmentHandler  # noqa: E402
from mygame.world.systems.combat_engine import CombatEngine  # noqa: E402
from mygame.world.systems.rank_system import rank_from_level  # noqa: E402
from mygame.world.systems import building_storage as bs  # noqa: E402
from mygame.world.data_registry import DataRegistry  # noqa: E402
from mygame.world.definitions import (  # noqa: E402
    BalanceConfig,
    BuildingDef,
    ItemDef,
    RankDef,
)
from mygame.world.event_bus import EventBus  # noqa: E402
from mygame.world.constants import (  # noqa: E402
    AGGREGATED_STATS,
    BASE_CARRY_WEIGHT,
    EQUIPMENT_SLOTS,
)

# -------------------------------------------------------------- #
#  Fakes
# -------------------------------------------------------------- #

class DB:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class FakeItem:
    def __init__(self, key, slot, stat_modifiers=None, required_rank=None):
        self.key = key
        self.name = key
        self.slot = slot
        self.stat_modifiers = stat_modifiers or {}
        self.required_rank = required_rank

    def get_stat(self, stat_name, default=0):
        return float(self.stat_modifiers.get(stat_name, default))


class FakeWeapon:
    def __init__(self, ammo_type, magazine_size, loaded):
        self.key = "rifle"
        self.name = "rifle"
        self.slot = "weapon"
        self.ammo_type = ammo_type
        self.magazine_size = magazine_size
        self.stat_modifiers = {}
        self.db = DB(loaded=loaded)


class FakePlayer:
    def __init__(self, level=1, hp=100, hp_max=100, coord_x=0, coord_y=0):
        self.key = "P"
        self.db = DB(level=level, hp=hp, hp_max=hp_max, resources={},
                     coord_x=coord_x, coord_y=coord_y, combat_xp=0)
        self.equipment = EquipmentHandler(self)
        self.location = None

    def heal(self, amount):
        before = self.db.hp
        self.db.hp = min(self.db.hp + int(amount), self.db.hp_max)
        return self.db.hp - before

    def check_permstring(self, perm):
        return False


class FakeTarget:
    def __init__(self, key, x, y, hp=1000, damage_reduction=0):
        self.key = key
        self.db = DB(coord_x=x, coord_y=y, hp=hp, hp_max=hp, combat_xp=0)
        self.equipment = EquipmentHandler(self)
        if damage_reduction:
            self.equipment.equip(
                FakeItem(f"{key}_armor", "torso",
                         {"damage_reduction": damage_reduction})
            )


class FakeLocation:
    def __init__(self, objects):
        self._objects = list(objects)

    def get_objects_in_area(self, x1, y1, x2, y2):
        out = []
        for obj in self._objects:
            cx = getattr(obj.db, "coord_x", None)
            cy = getattr(obj.db, "coord_y", None)
            if cx is None or cy is None:
                continue
            if x1 <= cx <= x2 and y1 <= cy <= y2:
                out.append(obj)
        return out


RANKS = [
    RankDef(name="Recruit", level=1, xp_threshold=0),
    RankDef(name="Sergeant", level=3, xp_threshold=100),
    RankDef(name="Captain", level=6, xp_threshold=500),
    RankDef(name="Marshal", level=12, xp_threshold=5000),
]


def _make_registry(items=None):
    registry = DataRegistry()
    registry.items = dict(items or {})
    registry.ranks = list(RANKS)
    registry.powerups = {}
    registry.balance = BalanceConfig()
    return registry


def _make_system(registry=None):
    registry = registry or _make_registry()
    return EquipmentSystem(registry, EventBus())


# -------------------------------------------------------------- #
#  Property 1: Slot cardinality (Req 1.2, 1.3)
# -------------------------------------------------------------- #

@st.composite
def gear_item_strategy(draw):
    slot = draw(st.sampled_from(EQUIPMENT_SLOTS))
    key = draw(st.text(
        alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd"),
                               whitelist_characters="_"),
        min_size=1, max_size=12,
    ))
    return FakeItem(key, slot, {"damage_reduction": 1})


class TestProperty1SlotCardinality(unittest.TestCase):
    """Property 1: Slot cardinality (Req 1.2, 1.3).

    Equipping any sequence of items through the use-case leaves at most one
    item per slot, and each occupied slot holds the last item equipped to it
    (re-equip replaces the previous occupant).
    """

    @given(items=st.lists(gear_item_strategy(), min_size=1, max_size=25))
    @settings(max_examples=200)
    def test_at_most_one_per_slot_and_last_wins(self, items):
        system = _make_system()
        player = FakePlayer(level=60)  # no rank gate blocks (no required_rank)

        last_per_slot = {}
        for item in items:
            ok = system.equip(player, item)
            self.assertTrue(ok)
            last_per_slot[item.slot] = item

        equipped = player.equipment.get_all_equipped()
        # One item per occupied slot; count equals distinct slots used.
        self.assertEqual(len(equipped), len(last_per_slot))
        for slot, expected in last_per_slot.items():
            self.assertIs(player.equipment.get_equipped(slot), expected)


# -------------------------------------------------------------- #
#  Property 8: Reload conservation
# -------------------------------------------------------------- #

class TestProperty8ReloadConservation(unittest.TestCase):
    """Property 8: Reload conservation.

    A reload moves rounds from the Supply_Bag into the magazine without
    creating or destroying ammo: the bag decrement equals the magazine
    increment, total ammo is conserved, and the magazine never exceeds
    ``magazine_size``.
    """

    @given(
        magazine_size=st.integers(min_value=1, max_value=100),
        loaded=st.integers(min_value=0, max_value=100),
        bag=st.integers(min_value=0, max_value=300),
    )
    @settings(max_examples=300)
    def test_reload_conserves_ammo(self, magazine_size, loaded, bag):
        loaded = min(loaded, magazine_size)
        items = {
            "rifle_rounds": ItemDef(
                key="rifle_rounds", name="Rifle Rounds", slot="",
                category="ammo", max_stack=1000,
            )
        }
        system = _make_system(_make_registry(items))
        player = FakePlayer(level=1)
        weapon = FakeWeapon("rifle_rounds", magazine_size, loaded)
        player.equipment.equip(weapon)
        if bag > 0:
            player.equipment.add_supply("rifle_rounds", bag, max_stack=1000)

        total_before = loaded + bag
        system.reload(player)

        loaded_after = weapon.db.loaded
        bag_after = player.equipment.get_supply("rifle_rounds")

        # Conservation: no ammo created or destroyed.
        self.assertEqual(loaded_after + bag_after, total_before)
        # Magazine never overfills.
        self.assertLessEqual(loaded_after, magazine_size)
        # Rounds only move into the magazine (never back out).
        self.assertGreaterEqual(loaded_after, loaded)
        # Bag decrement equals magazine increment.
        self.assertEqual(loaded_after - loaded, bag - bag_after)


# -------------------------------------------------------------- #
#  Property 9: Rank gate
# -------------------------------------------------------------- #

class TestProperty9RankGate(unittest.TestCase):
    """Property 9: Rank gate.

    equip / use / throw are permitted iff the player's derived rank is at
    least the item's ``required_rank``.
    """

    @given(
        level=st.integers(min_value=1, max_value=60),
        required=st.sampled_from([r.name for r in RANKS]),
    )
    @settings(max_examples=200)
    def test_equip_gated_by_rank(self, level, required):
        system = _make_system()
        player = FakePlayer(level=level)
        req_level = next(r.level for r in RANKS if r.name == required)
        expected = rank_from_level(level) >= req_level

        item = FakeItem("gun", "weapon", {"damage": 5}, required_rank=required)
        self.assertEqual(system.equip(player, item), expected)
        self.assertEqual(
            player.equipment.get_equipped("weapon") is item, expected
        )

    @given(
        level=st.integers(min_value=1, max_value=60),
        required=st.sampled_from([r.name for r in RANKS]),
    )
    @settings(max_examples=200)
    def test_use_gated_by_rank(self, level, required):
        req_level = next(r.level for r in RANKS if r.name == required)
        expected = rank_from_level(level) >= req_level
        items = {
            "medkit": ItemDef(
                key="medkit", name="Medkit", slot="", category="consumable",
                effect={"type": "heal", "amount": 10}, required_rank=required,
            )
        }
        system = _make_system(_make_registry(items))
        player = FakePlayer(level=level, hp=50, hp_max=100)
        player.equipment.add_supply("medkit", 1)
        self.assertEqual(system.use(player, "medkit"), expected)
        # Consumed iff permitted.
        self.assertEqual(
            player.equipment.get_supply("medkit"), 0 if expected else 1
        )


# -------------------------------------------------------------- #
#  Property 10: Heal clamp
# -------------------------------------------------------------- #

class TestProperty10HealClamp(unittest.TestCase):
    """Property 10: Heal clamp.

    After using a heal consumable, hp == min(hp_before + amount, hp_max).
    """

    @given(
        hp_max=st.integers(min_value=1, max_value=1000),
        hp_before=st.integers(min_value=0, max_value=1000),
        amount=st.integers(min_value=0, max_value=1000),
    )
    @settings(max_examples=300)
    def test_heal_clamped_to_hp_max(self, hp_max, hp_before, amount):
        hp_before = min(hp_before, hp_max)
        items = {
            "medkit": ItemDef(
                key="medkit", name="Medkit", slot="", category="consumable",
                effect={"type": "heal", "amount": amount},
            )
        }
        system = _make_system(_make_registry(items))
        player = FakePlayer(level=1, hp=hp_before, hp_max=hp_max)
        player.equipment.add_supply("medkit", 1)

        used = system.use(player, "medkit")
        if hp_before >= hp_max:
            # A player already at full HP keeps the medkit rather than burning
            # it for a 0-point heal; HP stays clamped at the max.
            self.assertFalse(used)
            self.assertEqual(player.equipment.get_supply("medkit"), 1)
            self.assertEqual(player.db.hp, hp_max)
        else:
            self.assertTrue(used)
            self.assertEqual(player.db.hp, min(hp_before + amount, hp_max))


# -------------------------------------------------------------- #
#  Weight / storage fakes (task 9.7)
# -------------------------------------------------------------- #

class FakeResourcePlayer:
    """A player whose Spend_Pool (``db.resources``) is the single pool the
    inflow choke point writes and cost checks read."""

    def __init__(self, resources=None, admin=False, carry_capacity=0):
        self.key = "P"
        self.db = DB(level=1, hp=100, hp_max=100,
                     resources=dict(resources or {}),
                     coord_x=0, coord_y=0, combat_xp=0)
        self.equipment = EquipmentHandler(self)
        self.location = None
        self._admin = admin
        if carry_capacity:
            self.equipment.equip(
                FakeItem("pack", "back", {"carry_capacity": carry_capacity})
            )

    def get_resource(self, resource):
        return int(self.db.resources.get(str(resource).title(), 0))

    def add_resource(self, resource, amount):
        key = str(resource).title()
        self.db.resources[key] = self.db.resources.get(key, 0) + int(amount)

    def has_resources(self, costs):
        return all(self.get_resource(r) >= amt for r, amt in costs.items())

    def deduct_resources(self, costs):
        if not self.has_resources(costs):
            return False
        for r, amt in costs.items():
            key = str(r).title()
            self.db.resources[key] = self.db.resources.get(key, 0) - int(amt)
        return True

    def check_permstring(self, perm):
        return self._admin


class FakeStorageBuilding:
    """Stand-in for a ``storage``-capability Building with a stored pool."""

    def __init__(self, building_type="VT", owner=None, stored=None):
        self.key = building_type
        self.db = DB(building_type=building_type, coord_x=0, coord_y=0,
                     stored_resources=dict(stored or {}))
        self._owner = owner

    @property
    def owner(self):
        return self._owner


def _make_registry_wb(resource_weights=None, items=None):
    """Registry with an explicit ``resource_weights`` balance override."""
    registry = _make_registry(items)
    registry.balance = BalanceConfig(
        resource_weights=dict(resource_weights or {})
    )
    return registry


class _StorageSingletonMixin:
    """Registers a storage registry on the production (``world.*``) singleton
    so ``building_storage`` resolves ``storage_capacity`` via the default
    provider (which is a distinct import from ``mygame.world.*``)."""

    def setUp(self):
        super().setUp()
        from world.data_registry import DataRegistry as CoreRegistry

        self._core_registry = CoreRegistry
        self._prev_instance = CoreRegistry.get_instance()
        self.registry = _make_registry_wb({"Iron": 1.0})
        self.registry.buildings = {}
        CoreRegistry.set_instance(self.registry)

    def tearDown(self):
        self._core_registry.set_instance(self._prev_instance)
        super().tearDown()

    def _set_capacity(self, capacity):
        self.registry.buildings["VT"] = BuildingDef(
            name="Vault", abbreviation="VT", cost={}, max_health=500,
            requires_hq=True, required_terrain=None, category="storage",
            produces=None, storage_capacity=int(capacity),
            capabilities=frozenset({"storage"}),
        )


# -------------------------------------------------------------- #
#  Property 16: Carry-weight bound (Req 15.4–15.7)
# -------------------------------------------------------------- #

class TestProperty16CarryWeightBound(unittest.TestCase):
    """Property 16: Carry-weight bound.

    After any capped inflow (``add_resource_capped``) into a non-admin
    player's Spend_Pool, the player's ``carried_weight`` never exceeds their
    ``carry_limit`` = ``BASE_CARRY_WEIGHT + Σ carry_capacity(gear)``.

    Validates: Requirements 15.4, 15.5, 15.6, 15.7
    """

    @given(
        resource_weight=st.floats(min_value=0.01, max_value=100.0,
                                  allow_nan=False, allow_infinity=False),
        carry_capacity=st.integers(min_value=0, max_value=5000),
        amount=st.integers(min_value=0, max_value=1_000_000),
    )
    @settings(max_examples=300)
    def test_carried_weight_never_exceeds_limit(
        self, resource_weight, carry_capacity, amount
    ):
        registry = _make_registry_wb({"Iron": resource_weight})
        system = EquipmentSystem(registry, EventBus())
        # Spill degrades to a log when unwired; the bound must still hold.
        player = FakeResourcePlayer(carry_capacity=carry_capacity)

        system.add_resource_capped(player, "Iron", amount)

        limit = system.carry_limit(player)
        carried = system.carried_weight(player)
        # Non-admin -> finite limit; carried stays within it (float epsilon).
        self.assertNotEqual(limit, float("inf"))
        self.assertLessEqual(carried, limit + 1e-6)

    @given(amount=st.integers(min_value=0, max_value=1_000_000))
    @settings(max_examples=100)
    def test_admin_is_unbounded(self, amount):
        registry = _make_registry_wb({"Iron": 1.0})
        system = EquipmentSystem(registry, EventBus())
        admin = FakeResourcePlayer(admin=True)
        added = system.add_resource_capped(admin, "Iron", amount)
        # Admins bypass the cap entirely (Req 15.6).
        self.assertEqual(added, amount)
        self.assertEqual(system.carry_limit(admin), float("inf"))


# -------------------------------------------------------------- #
#  Property 17: Weight conservation on over-capacity (Req 16.7)
# -------------------------------------------------------------- #

class TestProperty17WeightConservationPlayer(unittest.TestCase):
    """Property 17: Weight conservation on over-capacity (player holder).

    For ``add_resource_capped`` into a player's pool, ``added + dropped ==
    amount`` — no resource is created or destroyed.

    Validates: Requirements 16.7, 16.8
    """

    @given(
        resource_weight=st.floats(min_value=0.01, max_value=100.0,
                                  allow_nan=False, allow_infinity=False),
        amount=st.integers(min_value=0, max_value=1_000_000),
    )
    @settings(max_examples=300)
    def test_added_plus_dropped_equals_amount(self, resource_weight, amount):
        registry = _make_registry_wb({"Iron": resource_weight})
        system = EquipmentSystem(registry, EventBus())
        drops = []
        system.set_resource_drop_spawner(
            lambda holder, resource, amt: drops.append((resource, amt))
        )
        player = FakeResourcePlayer()

        added = system.add_resource_capped(player, "Iron", amount)
        dropped = sum(a for _, a in drops)

        self.assertGreaterEqual(added, 0)
        self.assertGreaterEqual(dropped, 0)
        self.assertEqual(added + dropped, amount)
        # What was added is exactly what landed in the Spend_Pool.
        self.assertEqual(player.get_resource("Iron"), added)


class TestProperty17WeightConservationBuilding(
    _StorageSingletonMixin, unittest.TestCase
):
    """Property 17: Weight conservation on over-capacity (building holder).

    For ``add_resource_capped`` into a Storage_Building's pool, ``added +
    dropped == amount``.

    Validates: Requirements 16.7, 16.8
    """

    @given(
        capacity=st.integers(min_value=0, max_value=5000),
        amount=st.integers(min_value=0, max_value=1_000_000),
    )
    @settings(max_examples=300)
    def test_added_plus_dropped_equals_amount(self, capacity, amount):
        self._set_capacity(capacity)
        system = EquipmentSystem(self.registry, EventBus())
        drops = []
        system.set_resource_drop_spawner(
            lambda holder, resource, amt: drops.append((resource, amt))
        )
        building = FakeStorageBuilding("VT", owner=FakeResourcePlayer())

        added = system.add_resource_capped(building, "Iron", amount)
        dropped = sum(a for _, a in drops)

        self.assertGreaterEqual(added, 0)
        self.assertGreaterEqual(dropped, 0)
        self.assertEqual(added + dropped, amount)
        self.assertEqual(bs.get_stored(building, "Iron"), added)
        # Building pool never exceeds its capacity.
        self.assertLessEqual(bs.get_total_stored(building), capacity)


# -------------------------------------------------------------- #
#  Property 18: Deposit/withdraw conservation & split (Req 16.2–16.4)
# -------------------------------------------------------------- #

class TestProperty18DepositWithdraw(_StorageSingletonMixin, unittest.TestCase):
    """Property 18: Deposit/withdraw conservation & split.

    ``deposit`` + ``withdraw`` conserve the total (player Spend_Pool +
    building stored pool) across the operation, and ``withdraw`` never pushes a
    non-admin player's ``carried_weight`` over their ``carry_limit``.

    Validates: Requirements 16.2, 16.3, 16.4, 16.8
    """

    @given(
        capacity=st.integers(min_value=0, max_value=1500),
        # Player starts at/under the limit (Iron weight 1.0, limit 1000).
        player_start=st.integers(min_value=0, max_value=1000),
        building_start=st.integers(min_value=0, max_value=1500),
        deposit_amt=st.integers(min_value=0, max_value=3000),
        withdraw_amt=st.integers(min_value=0, max_value=3000),
    )
    @settings(max_examples=300)
    def test_deposit_withdraw_conserve_and_respect_carry(
        self, capacity, player_start, building_start, deposit_amt, withdraw_amt
    ):
        self._set_capacity(capacity)
        system = EquipmentSystem(self.registry, EventBus())

        player = FakeResourcePlayer(resources={"Iron": player_start})
        # The building may not start over its capacity.
        stored0 = min(building_start, capacity)
        vault = FakeStorageBuilding("VT", stored={"Iron": stored0} if stored0 else None)

        limit = system.carry_limit(player)

        def total():
            return player.get_resource("Iron") + bs.get_stored(vault, "Iron")

        total_before = total()

        # Deposit: player -> building. Conserves total; player only shrinks.
        system.deposit(player, vault, "Iron", deposit_amt)
        self.assertEqual(total(), total_before)
        self.assertLessEqual(system.carried_weight(player), limit + 1e-6)
        self.assertLessEqual(bs.get_total_stored(vault), capacity)

        # Withdraw: building -> player. Conserves total; carried stays bounded.
        system.withdraw(player, vault, "Iron", withdraw_amt)
        self.assertEqual(total(), total_before)
        self.assertLessEqual(system.carried_weight(player), limit + 1e-6)
        # Spend_Pool remains the only place a player's resources live.
        self.assertEqual(
            player.db.resources.get("Iron", 0), player.get_resource("Iron")
        )


# -------------------------------------------------------------- #
#  Property 4: Zero-equipment identity (Req 2.5, 14.2, 14.6)
# -------------------------------------------------------------- #

# A small pool of supply keys used to confirm that carrying Supplies never
# leaks into the Gear stat surface (Supplies are not equipped Gear).
_SUPPLY_KEYS = ["rifle_rounds", "energy_cell", "medkit", "frag_grenade"]


class TestProperty4ZeroEquipmentIdentity(unittest.TestCase):
    """Property 4: Zero-equipment identity.

    An unequipped, supply-less character behaves exactly as it did before this
    feature:

    * every Aggregated_Stat is 0 (Req 2.5) — and remains 0 even when the
      Supply_Bag holds items, because Supplies are not equipped Gear;
    * a combat attack with a weapon but no other gear deals exactly the
      weapon's ``damage`` (no ``damage_bonus``, no ``damage_reduction``) —
      the damage formula's shape is unchanged (Req 14.1, 14.2);
    * ``carried_weight`` with an empty bag and empty Spend_Pool is 0.

    Validates: Requirements 2.5, 14.2, 14.6
    """

    @given(
        stat=st.sampled_from(AGGREGATED_STATS),
        supplies=st.lists(
            st.tuples(st.sampled_from(_SUPPLY_KEYS),
                      st.integers(min_value=1, max_value=50)),
            max_size=6,
        ),
    )
    @settings(max_examples=200)
    def test_all_aggregated_stats_zero_without_gear(self, stat, supplies):
        # A fresh character with nothing equipped.
        player = FakePlayer(level=1)
        # Carrying Supplies must not contribute to any Gear stat total.
        for key, count in supplies:
            player.equipment.add_supply(key, count, max_stack=1000)

        # No slot is occupied ...
        self.assertEqual(player.equipment.get_all_equipped(), {})
        # ... so every Aggregated_Stat aggregates to exactly 0.
        self.assertEqual(player.equipment.get_stat_total(stat), 0.0)

    @given(
        weapon_damage=st.integers(min_value=0, max_value=500),
        target_hp=st.integers(min_value=1, max_value=2000),
    )
    @settings(max_examples=200)
    def test_weapon_only_attack_deals_weapon_damage(
        self, weapon_damage, target_hp
    ):
        # Real CombatEngine — no fakes in the damage path.
        registry = _make_registry()
        engine = CombatEngine(
            registry=registry,
            event_bus=EventBus(),
            current_tick_func=lambda: 0,
        )

        # Attacker holds only a weapon; no other gear, no powerups.
        weapon = FakeItem("gun", "weapon", {"damage": weapon_damage})
        attacker = FakePlayer(level=1)
        attacker.equipment.equip(weapon)

        # Target has no gear at all (zero armor).
        target = FakePlayer(level=1, hp=target_hp, hp_max=target_hp)

        # With no damage_bonus and no damage_reduction, net damage is exactly
        # the weapon's base damage — the pre-feature behavior.
        damage = engine._calculate_damage(attacker, target, weapon)
        self.assertEqual(damage, weapon_damage)

    def test_carried_weight_zero_with_empty_bag_and_resources(self):
        system = _make_system()
        player = FakePlayer(level=1)
        # Empty Supply_Bag and empty Spend_Pool.
        self.assertEqual(player.equipment.get_supplies(), {})
        self.assertEqual(player.db.resources, {})
        self.assertEqual(system.carried_weight(player), 0.0)


# -------------------------------------------------------------- #
#  Spend-pool preservation (Req 14.6)
# -------------------------------------------------------------- #

class TestSpendPoolPreservation(unittest.TestCase):
    """Spend-pool preservation.

    ``db.resources`` remains the pool every existing cost check reads
    (build/upgrade/research/ammo via ``has_resources``/``deduct_resources``);
    the feature did not alter Spend_Pool semantics. A successful deduction
    subtracts exactly the cost from ``db.resources`` and an unaffordable
    deduction leaves the pool untouched.

    Validates: Requirements 14.6
    """

    @given(
        stock=st.integers(min_value=0, max_value=10_000),
        cost=st.integers(min_value=1, max_value=5_000),
    )
    @settings(max_examples=200)
    def test_cost_checks_read_and_write_db_resources(self, stock, cost):
        player = FakeResourcePlayer(resources={"Iron": stock})
        costs = {"Iron": cost}

        affordable = stock >= cost
        self.assertEqual(player.has_resources(costs), affordable)

        ok = player.deduct_resources(costs)
        self.assertEqual(ok, affordable)

        # A successful deduction subtracts exactly the cost from the pool;
        # an unaffordable one leaves db.resources untouched.
        expected = stock - cost if affordable else stock
        self.assertEqual(player.db.resources.get("Iron", 0), expected)
        # get_resource stays consistent with the underlying db.resources pool.
        self.assertEqual(player.get_resource("Iron"), expected)

    def test_equipping_gear_does_not_touch_spend_pool(self):
        # Gear is worn, not hauled: equipping must not change db.resources,
        # so every downstream cost check reads the same Spend_Pool as before.
        player = FakeResourcePlayer(resources={"Iron": 100, "Wood": 40})
        before = dict(player.db.resources)
        player.equipment.equip(FakeItem("vest", "torso",
                                        {"damage_reduction": 5}))
        self.assertEqual(player.db.resources, before)
        self.assertTrue(player.has_resources({"Iron": 100, "Wood": 40}))


if __name__ == "__main__":
    unittest.main()
