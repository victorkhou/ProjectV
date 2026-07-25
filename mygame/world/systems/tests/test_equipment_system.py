"""
Unit tests for the EquipmentSystem mediated actions (tasks 3.1-3.6, 9.1).

Covers the use-case that mediates the raw EquipmentHandler store:

- equip accept/deny by rank
- re-equip replaces the item in an occupied slot (slot cardinality)
- unequip bad-slot rejection
- use-heal clamp (heal never exceeds hp_max)
- use-buff entry shape + expiry (routed through the injected PowerupSystem;
  PowerupSystem.process_tick expires it)
- throw target selection + armor respected (via an injected fake area-damage
  applier exposing _calculate_damage / _apply_damage)
- reload transfer + already-full / no-ammo / non-ranged paths
- carry partial add (add_supply_drop adds up to the binding limit and returns
  the amount added)

Validates: Requirements 1.2, 1.3, 7.x, 8.x, 9.x, 10.x, 11.x
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

from mygame.world.constants import BASE_CARRY_WEIGHT  # noqa: E402
from mygame.world.systems.equipment_system import EquipmentSystem  # noqa: E402
from mygame.world.systems.equipment_handler import EquipmentHandler  # noqa: E402
from mygame.world.systems.powerup_system import PowerupSystem  # noqa: E402
from mygame.world.systems import building_storage as bs  # noqa: E402
from mygame.world.data_registry import DataRegistry  # noqa: E402
from mygame.world.definitions import (  # noqa: E402
    BalanceConfig,
    BuildingDef,
    ItemDef,
    RankDef,
)
from mygame.world.event_bus import EventBus, PLAYER_NOTIFICATION  # noqa: E402

# -------------------------------------------------------------- #
#  Fakes
# -------------------------------------------------------------- #

class DB:
    """A tiny attribute bag standing in for an Evennia ``.db`` proxy."""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class FakeItem:
    """Lightweight stand-in for an equippable GameItem (Gear)."""

    def __init__(self, key, slot, stat_modifiers=None, required_rank=None):
        self.key = key
        self.name = key
        self.slot = slot
        self.stat_modifiers = stat_modifiers or {}
        self.required_rank = required_rank

    def get_stat(self, stat_name, default=0):
        return float(self.stat_modifiers.get(stat_name, default))


class FakeWeapon:
    """Stand-in for an equipped weapon GameItem tracking db.loaded."""

    def __init__(self, key="rifle", ammo_type=None, magazine_size=None,
                 loaded=0, weapon_type=None, ammo_cost=None):
        self.key = key
        self.name = key
        self.slot = "weapon"
        self.ammo_type = ammo_type
        self.magazine_size = magazine_size
        self.weapon_type = weapon_type
        self.ammo_cost = ammo_cost
        self.stat_modifiers = {}
        self.db = DB(loaded=loaded)

    def get_stat(self, stat_name, default=0):
        return float(self.stat_modifiers.get(stat_name, default))


class FakePlayer:
    """Stand-in for a CombatCharacter with a real EquipmentHandler."""

    def __init__(self, level=1, hp=100, hp_max=100, resources=None,
                 coord_x=0, coord_y=0, admin=False):
        self.key = "TestPlayer"
        self.db = DB(
            level=level,
            hp=hp,
            hp_max=hp_max,
            resources=dict(resources or {}),
            coord_x=coord_x,
            coord_y=coord_y,
            combat_xp=0,
        )
        self.equipment = EquipmentHandler(self)
        self.location = None
        self._admin = admin

    def heal(self, amount):
        before = self.db.hp
        self.db.hp = min(self.db.hp + int(amount), self.db.hp_max)
        return self.db.hp - before

    def check_permstring(self, perm):
        return self._admin

    # Resource pool (Spend_Pool) — used by crafting and agent-run production.
    def get_resource(self, resource):
        return int(self.db.resources.get(str(resource).title(), 0))

    def add_resource(self, resource, amount):
        key = str(resource).title()
        self.db.resources[key] = self.db.resources.get(key, 0) + int(amount)

    def has_resources(self, costs):
        return all(
            self.db.resources.get(str(r).title(), 0) >= amt
            for r, amt in costs.items()
        )

    def deduct_resources(self, costs):
        if not self.has_resources(costs):
            return False
        for r, amt in costs.items():
            key = str(r).title()
            self.db.resources[key] = self.db.resources.get(key, 0) - int(amt)
        return True

    # Salvage currency (item-loot-economy R7, task 5.1) — mirrors the
    # CombatCharacter accessors in typeclasses/characters.py.
    def get_salvage(self):
        return int(getattr(self.db, "salvage", 0) or 0)

    def add_salvage(self, amount):
        self.db.salvage = max(0, self.get_salvage() + int(amount))

    def spend_salvage(self, amount):
        amount = int(amount)
        if amount < 0:
            return False
        balance = self.get_salvage()
        if balance < amount:
            return False
        self.db.salvage = balance - amount
        return True


class FakeTarget:
    """A damageable target: a player-like entity at fixed coords."""

    def __init__(self, key, x, y, hp=100, damage_reduction=0):
        self.key = key
        self.db = DB(coord_x=x, coord_y=y, hp=hp, hp_max=hp, combat_xp=0)
        self.equipment = EquipmentHandler(self)
        if damage_reduction:
            self.equipment.equip(
                FakeItem(f"{key}_armor", "torso",
                         {"damage_reduction": damage_reduction})
            )


class FakeLocation:
    """A planet stand-in exposing get_objects_in_area for throw targeting."""

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


# -------------------------------------------------------------- #
#  Registry / system construction
# -------------------------------------------------------------- #

RANKS = [
    RankDef(name="Recruit", level=1, xp_threshold=0),
    RankDef(name="Sergeant", level=3, xp_threshold=100),
    RankDef(name="Captain", level=6, xp_threshold=500),
]

ITEMS = {
    "medkit": ItemDef(
        key="medkit", name="Medkit", slot="", category="consumable",
        effect={"type": "heal", "amount": 30}, weight=5.0, max_stack=10,
        craft_cost={"Wood": 5},
    ),
    "combat_stim": ItemDef(
        key="combat_stim", name="Combat Stim", slot="", category="consumable",
        effect={"type": "buff", "stat": "damage_bonus", "amount": 10,
                "duration_ticks": 30},
        weight=2.0, max_stack=10,
    ),
    "frag_grenade": ItemDef(
        key="frag_grenade", name="Frag Grenade", slot="", category="throwable",
        effect={"type": "aoe_damage", "amount": 40, "radius": 2, "range": 6},
        weight=3.0, max_stack=10,
    ),
    "rifle_rounds": ItemDef(
        key="rifle_rounds", name="Rifle Rounds", slot="", category="ammo",
        weight=0.1, max_stack=200, craft_cost={"Iron": 2},
    ),
    "heavy_ammo": ItemDef(
        key="heavy_ammo", name="Heavy Ammo", slot="", category="ammo",
        weight=10.0, max_stack=200,
    ),
    "featherlite": ItemDef(
        key="featherlite", name="Featherlite Rounds", slot="", category="ammo",
        weight=0.0, max_stack=200,  # zero weight — exercises the /0 guard
    ),
    # Equippable gear (slot set) — resolvable ItemDefs so the PvP gear
    # drop-on-death path (which looks item_key up in the registry) can spawn a
    # drop. TestDeathLoss equips these by key and only checked the stash before.
    "assault_rifle": ItemDef(
        key="assault_rifle", name="Assault Rifle", slot="weapon",
        category="weapon", stat_modifiers={"damage": 25}, weight=8.0,
    ),
    "kevlar_vest": ItemDef(
        key="kevlar_vest", name="Kevlar Vest", slot="torso", category="armor",
        stat_modifiers={"damage_reduction": 5}, weight=6.0,
    ),
}


def _make_registry():
    registry = DataRegistry()
    registry.items = dict(ITEMS)
    registry.ranks = list(RANKS)
    registry.powerups = {}
    # HQ def so a production building's owner can pass the base-deactivation
    # gate (production stops while the owner has no active HQ).
    registry.buildings = {
        "HQ": BuildingDef(
            name="Headquarters", abbreviation="HQ", cost={"Wood": 10},
            max_health=500, requires_hq=False, required_terrain=None,
            category="headquarters", produces=None,
            capabilities=frozenset({"headquarters"}),
        ),
    }
    # Yield one item per production call (cooldown gate covered separately).
    registry.balance = BalanceConfig(equipment_production_ticks=1)
    return registry


def _hq_building():
    """An HQ-capability building for a production owner's get_buildings()."""
    return type("_HQ", (), {
        "db": DB(building_type="HQ", under_construction=False),
        "location": None,
    })()


def _give_hq(owner):
    """Give a production/base owner a completed HQ (passes owner_has_active_hq).

    Equipment production is gated on the owner having an active HQ (the PvP
    'no HQ = base inert' rule)."""
    owner.get_buildings = lambda: [_hq_building()]
    return owner


def _make_system(registry=None):
    registry = registry or _make_registry()
    event_bus = EventBus()
    system = EquipmentSystem(registry, event_bus)
    sink = NotificationSink()
    event_bus.subscribe(PLAYER_NOTIFICATION, sink)
    return system, event_bus, sink


class NotificationSink:
    """Captures PLAYER_NOTIFICATION events for assertions."""

    def __init__(self):
        self.events = []

    def __call__(self, event_name=None, player=None, kind=None, data=None,
                 **_extra):
        self.events.append((kind, data or {}))

    def kinds(self):
        return [k for k, _ in self.events]

    def last(self):
        return self.events[-1] if self.events else (None, {})


# -------------------------------------------------------------- #
#  equip — rank gate + slot cardinality
# -------------------------------------------------------------- #

class TestEquip(unittest.TestCase):
    def test_equip_accept_when_rank_met(self):
        system, _, sink = _make_system()
        # Sergeant requires rank level 3; player level 11 -> rank 3.
        player = FakePlayer(level=11)
        item = FakeItem("rifle", "weapon", {"damage": 20},
                        required_rank="Sergeant")
        self.assertTrue(system.equip(player, item))
        self.assertIs(player.equipment.get_equipped("weapon"), item)
        self.assertIn("equipped", sink.kinds())

    def test_equip_denied_when_below_rank(self):
        system, _, sink = _make_system()
        player = FakePlayer(level=1)  # rank 1 < Sergeant (3)
        item = FakeItem("rifle", "weapon", {"damage": 20},
                        required_rank="Sergeant")
        self.assertFalse(system.equip(player, item))
        self.assertIsNone(player.equipment.get_equipped("weapon"))
        self.assertIn("equip_denied", sink.kinds())
        _kind, data = sink.last()
        self.assertEqual(data.get("required_rank"), "Sergeant")

    def test_equip_rejects_noncanonical_slot(self):
        system, _, _ = _make_system()
        player = FakePlayer(level=60)
        item = FakeItem("junk", "pocket", {})
        self.assertFalse(system.equip(player, item))

    def test_reequip_replaces_item_in_occupied_slot(self):
        """Slot cardinality: re-equip replaces the occupant (Req 1.2, 1.3)."""
        system, _, _ = _make_system()
        player = FakePlayer(level=60)
        old = FakeItem("knife", "weapon", {"damage": 10})
        new = FakeItem("rifle", "weapon", {"damage": 25})
        self.assertTrue(system.equip(player, old))
        self.assertTrue(system.equip(player, new))
        # Exactly one item occupies the slot, and it is the newest.
        self.assertIs(player.equipment.get_equipped("weapon"), new)
        self.assertEqual(
            [k for k in player.equipment.get_slot_names() if k == "weapon"],
            ["weapon"],
        )

    def test_reequip_emits_unequipped_then_equipped(self):
        """Swapping an occupied slot notifies unequip-old then equip-new."""
        system, _, sink = _make_system()
        player = FakePlayer(level=60)
        old = FakeItem("knife", "weapon", {"damage": 10})
        new = FakeItem("rifle", "weapon", {"damage": 25})
        system.equip(player, old)
        sink.events.clear()

        system.equip(player, new)

        kinds = [k for k, _ in sink.events]
        # unequipped fires first, then equipped.
        self.assertEqual(kinds, ["unequipped", "equipped"])
        # The unequipped notification names the displaced item.
        _, udata = sink.events[0]
        self.assertIn("knife", udata.get("item_name", ""))

    def test_equip_into_empty_slot_no_unequipped_notification(self):
        """Equipping into a free slot fires only 'equipped', never 'unequipped'."""
        system, _, sink = _make_system()
        player = FakePlayer(level=60)
        item = FakeItem("helmet", "head", {"damage_reduction": 3})
        system.equip(player, item)
        self.assertEqual([k for k, _ in sink.events], ["equipped"])


class TestEquipAll(unittest.TestCase):
    """equip_all fills empty slots only (first per slot wins, no swap)."""

    def test_fills_empty_slots_skips_occupied(self):
        system, _, sink = _make_system()
        player = FakePlayer(level=60)
        # Pre-equip a weapon.
        knife = FakeItem("knife", "weapon", {"damage": 10})
        system.equip(player, knife)
        sink.events.clear()

        # Offer two weapons and one helmet.
        rifle = FakeItem("rifle", "weapon", {"damage": 25})
        helmet = FakeItem("helmet", "head", {"damage_reduction": 3})
        count = system.equip_all(player, [rifle, helmet])

        # Only the helmet (empty slot) was equipped; weapon was skipped.
        self.assertEqual(count, 1)
        self.assertIs(player.equipment.get_equipped("weapon"), knife)
        self.assertIs(player.equipment.get_equipped("head"), helmet)
        # Only 'equipped' for helmet — no swap, no unequipped.
        self.assertEqual([k for k, _ in sink.events], ["equipped"])

    def test_first_item_wins_for_a_shared_slot(self):
        system, _, sink = _make_system()
        player = FakePlayer(level=60)

        # Two weapons offered; first in list claims the slot.
        knife = FakeItem("knife", "weapon", {"damage": 10})
        rifle = FakeItem("rifle", "weapon", {"damage": 25})
        count = system.equip_all(player, [knife, rifle])

        self.assertEqual(count, 1)
        self.assertIs(player.equipment.get_equipped("weapon"), knife)
        # Only one equipped notification.
        self.assertEqual([k for k, _ in sink.events], ["equipped"])

    def test_empty_list_equips_nothing(self):
        system, _, sink = _make_system()
        player = FakePlayer(level=60)
        self.assertEqual(system.equip_all(player, []), 0)
        self.assertEqual(sink.events, [])


# -------------------------------------------------------------- #
#  unequip
# -------------------------------------------------------------- #

class TestUnequip(unittest.TestCase):
    def test_unequip_bad_slot_rejected(self):
        system, _, _ = _make_system()
        player = FakePlayer(level=60)
        self.assertFalse(system.unequip(player, "pocket"))

    def test_unequip_empty_slot_returns_false(self):
        system, _, _ = _make_system()
        player = FakePlayer(level=60)
        self.assertFalse(system.unequip(player, "weapon"))

    def test_unequip_success(self):
        system, _, sink = _make_system()
        player = FakePlayer(level=60)
        item = FakeItem("vest", "torso", {"damage_reduction": 5})
        system.equip(player, item)
        self.assertTrue(system.unequip(player, "torso"))
        self.assertIsNone(player.equipment.get_equipped("torso"))
        self.assertIn("unequipped", sink.kinds())


# -------------------------------------------------------------- #
#  use — heal clamp
# -------------------------------------------------------------- #

class TestUseHeal(unittest.TestCase):
    def test_heal_clamps_to_hp_max(self):
        system, _, sink = _make_system()
        player = FakePlayer(level=1, hp=90, hp_max=100)
        player.equipment.add_supply("medkit", 1)
        self.assertTrue(system.use(player, "medkit"))
        # medkit heals 30 but clamps at hp_max=100.
        self.assertEqual(player.db.hp, 100)
        self.assertEqual(player.equipment.get_supply("medkit"), 0)
        kind, data = sink.last()
        self.assertEqual(kind, "healed")
        self.assertEqual(data.get("amount"), 10)  # only 10 restored

    def test_heal_full_amount_when_room(self):
        system, _, _ = _make_system()
        player = FakePlayer(level=1, hp=50, hp_max=100)
        player.equipment.add_supply("medkit", 1)
        self.assertTrue(system.use(player, "medkit"))
        self.assertEqual(player.db.hp, 80)

    def test_use_not_held_rejected(self):
        system, _, sink = _make_system()
        player = FakePlayer(level=1)
        self.assertFalse(system.use(player, "medkit"))
        self.assertIn("use_failed", sink.kinds())


# -------------------------------------------------------------- #
#  use — buff entry shape + expiry (through PowerupSystem)
# -------------------------------------------------------------- #

class TestUseBuff(unittest.TestCase):
    def test_buff_applies_then_expires_via_process_tick(self):
        registry = _make_registry()
        system, _, sink = _make_system(registry)

        clock = {"t": 0}
        powerups = PowerupSystem(
            registry, EventBus(), current_tick_func=lambda: clock["t"]
        )
        system.set_powerup_system(powerups)

        player = FakePlayer(level=1)
        player.equipment.add_supply("combat_stim", 1)

        # Apply the buff.
        self.assertTrue(system.use(player, "combat_stim"))
        self.assertIn("buff_applied", sink.kinds())

        # Entry shape: {expires_tick, effect: {effect_type, effect_value}} and
        # the player is registered for tick-based expiry.
        active = player.db.active_powerups
        self.assertEqual(len(active), 1)
        entry = next(iter(active.values()))
        self.assertEqual(entry["expires_tick"], 30)
        self.assertEqual(entry["effect"]["effect_type"], "damage_bonus")
        self.assertEqual(entry["effect"]["effect_value"], 10)
        self.assertIn(player, powerups._active_players)

        # Buff is live before expiry.
        self.assertEqual(
            powerups.get_stat_modifier(player, "damage_bonus"), 10.0
        )

        # Advance past expires_tick and process — the bonus must be gone.
        clock["t"] = 31
        powerups.process_tick(31)
        self.assertEqual(
            powerups.get_stat_modifier(player, "damage_bonus"), 0.0
        )
        self.assertEqual(player.db.active_powerups, {})

    def test_buff_consumes_one_unit(self):
        registry = _make_registry()
        system, _, _ = _make_system(registry)
        powerups = PowerupSystem(registry, EventBus(),
                                 current_tick_func=lambda: 0)
        system.set_powerup_system(powerups)
        player = FakePlayer(level=1)
        player.equipment.add_supply("combat_stim", 2)
        self.assertTrue(system.use(player, "combat_stim"))
        self.assertEqual(player.equipment.get_supply("combat_stim"), 1)


# -------------------------------------------------------------- #
#  throw — target selection + armor respected
# -------------------------------------------------------------- #

# -------------------------------------------------------------- #
#  reload — transfer / already-full / no-ammo / non-ranged
# -------------------------------------------------------------- #

class TestReload(unittest.TestCase):
    def test_reload_transfers_from_bag_to_magazine(self):
        system, _, sink = _make_system()
        player = FakePlayer(level=1)
        weapon = FakeWeapon("rifle", ammo_type="rifle_rounds",
                            magazine_size=30, loaded=10)
        player.equipment.equip(weapon)
        player.equipment.add_supply("rifle_rounds", 50)

        self.assertTrue(system.reload(player))
        # Transfers min(30-10, 50) = 20 rounds.
        self.assertEqual(weapon.db.loaded, 30)
        self.assertEqual(player.equipment.get_supply("rifle_rounds"), 30)
        kind, data = sink.last()
        self.assertEqual(kind, "reloaded")
        self.assertEqual(data.get("loaded"), 30)
        self.assertEqual(data.get("remaining"), 30)

    def test_reload_limited_by_bag(self):
        system, _, _ = _make_system()
        player = FakePlayer(level=1)
        weapon = FakeWeapon("rifle", ammo_type="rifle_rounds",
                            magazine_size=30, loaded=0)
        player.equipment.equip(weapon)
        player.equipment.add_supply("rifle_rounds", 5)
        self.assertTrue(system.reload(player))
        self.assertEqual(weapon.db.loaded, 5)
        self.assertEqual(player.equipment.get_supply("rifle_rounds"), 0)

    def test_reload_already_full_rejected(self):
        system, _, sink = _make_system()
        player = FakePlayer(level=1)
        weapon = FakeWeapon("rifle", ammo_type="rifle_rounds",
                            magazine_size=30, loaded=30)
        player.equipment.equip(weapon)
        player.equipment.add_supply("rifle_rounds", 50)
        self.assertFalse(system.reload(player))
        # No ammo drawn from the bag.
        self.assertEqual(player.equipment.get_supply("rifle_rounds"), 50)
        kind, data = sink.last()
        self.assertEqual(kind, "reload_failed")
        self.assertEqual(data.get("reason"), "already_loaded")

    def test_reload_no_ammo_in_bag_rejected(self):
        system, _, sink = _make_system()
        player = FakePlayer(level=1)
        weapon = FakeWeapon("rifle", ammo_type="rifle_rounds",
                            magazine_size=30, loaded=5)
        player.equipment.equip(weapon)
        self.assertFalse(system.reload(player))
        kind, data = sink.last()
        self.assertEqual(kind, "reload_failed")
        self.assertEqual(data.get("reason"), "no_ammo")

    def test_reload_non_ranged_weapon_rejected(self):
        system, _, sink = _make_system()
        player = FakePlayer(level=1)
        # A melee weapon declares no ammo_type.
        weapon = FakeWeapon("knife", ammo_type=None, magazine_size=None,
                            loaded=0)
        player.equipment.equip(weapon)
        self.assertFalse(system.reload(player))
        kind, data = sink.last()
        self.assertEqual(kind, "reload_failed")
        self.assertEqual(data.get("reason"), "no_ammo_weapon")

    def test_reload_no_weapon_equipped_rejected(self):
        system, _, sink = _make_system()
        player = FakePlayer(level=1)
        self.assertFalse(system.reload(player))
        kind, data = sink.last()
        self.assertEqual(kind, "reload_failed")
        self.assertEqual(data.get("reason"), "no_ammo_weapon")

    def test_reload_resource_fed_ranged_weapon_reports_no_magazine(self):
        """A ranged weapon that fires from resources (ammo_cost, no ammo_type)
        has no magazine to reload — it reports 'no_magazine', not the
        misleading 'no_ammo_weapon' (the assault-rifle case)."""
        system, _, sink = _make_system()
        player = FakePlayer(level=1)
        rifle = FakeWeapon("assault_rifle", ammo_type=None, magazine_size=None,
                           weapon_type="ranged", ammo_cost={"Iron": 1})
        player.equipment.equip(rifle)
        self.assertFalse(system.reload(player))
        kind, data = sink.last()
        self.assertEqual(kind, "reload_failed")
        self.assertEqual(data.get("reason"), "no_magazine")


# -------------------------------------------------------------- #
#  add_supply_drop — carry partial add (weight/stack bound)
# -------------------------------------------------------------- #

class TestAddSupplyDrop(unittest.TestCase):
    def test_full_pickup_when_within_limits(self):
        system, _, _ = _make_system()
        player = FakePlayer(level=1)  # base carry weight 1000
        added = system.add_supply_drop(player, "rifle_rounds", 100)
        self.assertEqual(added, 100)
        self.assertEqual(player.equipment.get_supply("rifle_rounds"), 100)

    def test_partial_add_capped_by_stack(self):
        system, _, sink = _make_system()
        player = FakePlayer(level=1)
        # medkit max_stack is 10; offer 25 -> only 10 fit.
        added = system.add_supply_drop(player, "medkit", 25)
        self.assertEqual(added, 10)
        self.assertEqual(player.equipment.get_supply("medkit"), 10)
        kind, data = sink.last()
        self.assertEqual(kind, "carry_full")
        self.assertEqual(data.get("carried"), 10)
        self.assertEqual(data.get("dropped"), 15)

    def test_partial_add_capped_by_weight(self):
        system, _, sink = _make_system()
        # heavy_ammo weighs 10 each; base limit 1000 -> only 100 fit by weight.
        player = FakePlayer(level=1)
        added = system.add_supply_drop(player, "heavy_ammo", 150)
        self.assertEqual(added, 100)
        self.assertEqual(player.equipment.get_supply("heavy_ammo"), 100)
        kind, data = sink.last()
        self.assertEqual(kind, "carry_full")
        self.assertEqual(data.get("dropped"), 50)

    def test_weight_room_accounts_for_existing_resources(self):
        system, _, _ = _make_system()
        # Resource weights: default 1.0 for unknown; give 950 units of a
        # resource weighing 1.0 -> only 50 weight room left. heavy_ammo (10)
        # -> floor(50/10)=5 fit.
        player = FakePlayer(level=1, resources={"Scrap": 950})
        added = system.add_supply_drop(player, "heavy_ammo", 20)
        self.assertEqual(added, 5)

    def test_admin_bypasses_weight_cap(self):
        system, _, _ = _make_system()
        player = FakePlayer(level=1, admin=True)
        # heavy_ammo weight 10; admin carry_limit is infinite, but stack cap
        # (200) still binds.
        added = system.add_supply_drop(player, "heavy_ammo", 150)
        self.assertEqual(added, 150)

    def test_over_stack_pickup_conserves_via_spawned_drop(self):
        # Conservation: added + Σ(spawned) == offered, for the stack-capped case.
        system, _, _ = _make_system()
        spawned = []
        system.set_supply_drop_spawner(
            lambda p, key, count: spawned.append((key, count))
        )
        player = FakePlayer(level=1)
        offered = 25
        added = system.add_supply_drop(player, "medkit", offered)  # max_stack 10
        dropped = sum(c for _k, c in spawned)
        self.assertEqual(added, 10)
        self.assertEqual(added + dropped, offered)
        self.assertEqual(spawned, [("medkit", 15)])

    def test_over_weight_pickup_conserves_via_spawned_drop(self):
        # Conservation for the weight-capped case (heavy_ammo weighs 10).
        system, _, _ = _make_system()
        spawned = []
        system.set_supply_drop_spawner(
            lambda p, key, count: spawned.append((key, count))
        )
        player = FakePlayer(level=1)
        offered = 150
        added = system.add_supply_drop(player, "heavy_ammo", offered)  # 100 fit
        dropped = sum(c for _k, c in spawned)
        self.assertEqual(added, 100)
        self.assertEqual(added + dropped, offered)

    def test_zero_weight_item_admits_full_stack_without_error(self):
        # A weight-0 item must not hit ZeroDivisionError in the weight guard;
        # weight is not a binding constraint, so the stack cap alone applies.
        system, _, _ = _make_system()
        player = FakePlayer(level=1)
        # featherlite: weight 0.0, max_stack 200. Offer 250 -> 200 fit by stack.
        added = system.add_supply_drop(player, "featherlite", 250)
        self.assertEqual(added, 200)
        self.assertEqual(player.equipment.get_supply("featherlite"), 200)


# -------------------------------------------------------------- #
#  carry_limit — carry_capacity gear raises the weight limit
# -------------------------------------------------------------- #

class TestCarryLimit(unittest.TestCase):
    """A ``carry_capacity`` gear piece raises the carry cap by its stat amount.

    Validates: Requirements 6.3, 15.5
    """

    def test_no_gear_limit_is_base_carry_weight(self):
        system, _, _ = _make_system()
        player = FakePlayer(level=60)  # non-admin
        self.assertEqual(system.carry_limit(player), float(BASE_CARRY_WEIGHT))

    def test_carry_capacity_gear_raises_limit_by_stat_amount(self):
        system, _, _ = _make_system()
        player = FakePlayer(level=60)  # non-admin
        # A hauler pack (back slot) granting +250 carry_capacity.
        pack = FakeItem("hauler_pack", "back", {"carry_capacity": 250})
        self.assertTrue(system.equip(player, pack))
        self.assertEqual(
            system.carry_limit(player), float(BASE_CARRY_WEIGHT) + 250
        )


# -------------------------------------------------------------- #
#  max_hp gear wiring (task 6.4) — equip raises ceiling, unequip clamps
# -------------------------------------------------------------- #

from mygame.typeclasses.combat_entity import CombatEntity  # noqa: E402


class _HpEntity(CombatEntity):
    """A CombatEntity-backed player double with a real EquipmentHandler.

    The system-level ``FakePlayer`` deliberately lacks ``CombatEntity``
    methods, so these tests use a genuine entity to exercise the
    ``equip`` -> ``refresh_equipment_hp_max`` wiring end to end.
    """

    def __init__(self, level=60):
        self.key = "HpPlayer"
        self.db = DB(level=level, combat_xp=0)
        self.at_combat_entity_init()
        # ``equipment`` is a read-only property on CombatEntity; seed the
        # cached handler it returns.
        self._equipment_handler = EquipmentHandler(self)
        self.location = None
        self._admin = False

    def check_permstring(self, perm):
        return False


class TestMaxHpGear(unittest.TestCase):
    """Equipping ``max_hp`` gear raises the ceiling (no free heal); unequipping
    lowers it and clamps current HP.

    Validates: Requirement 6.4 (deferred D6 follow-up).
    """

    def test_equip_raises_hp_max_without_healing(self):
        system, _, _ = _make_system()
        player = _HpEntity()
        player.take_damage(40)  # hp 60 / 100
        vest = FakeItem("vitality_vest", "torso", {"max_hp": 50})
        self.assertTrue(system.equip(player, vest))
        self.assertEqual(player.db.hp_max, 150)
        self.assertEqual(player.db.hp, 60)  # ceiling raised, HP unchanged

    def test_unequip_lowers_hp_max_and_clamps_current_hp(self):
        system, _, _ = _make_system()
        player = _HpEntity()
        vest = FakeItem("vitality_vest", "torso", {"max_hp": 50})
        system.equip(player, vest)
        player.heal(100)  # hp 150 / 150
        self.assertTrue(system.unequip(player, "torso"))
        self.assertEqual(player.db.hp_max, 100)
        self.assertEqual(player.db.hp, 100)  # clamped to the new ceiling

    def test_swap_to_larger_piece_updates_ceiling(self):
        system, _, _ = _make_system()
        player = _HpEntity()
        system.equip(player, FakeItem("small_vest", "torso", {"max_hp": 30}))
        self.assertEqual(player.db.hp_max, 130)
        # Swapping the same slot auto-unequips the old piece first.
        system.equip(player, FakeItem("big_vest", "torso", {"max_hp": 80}))
        self.assertEqual(player.db.hp_max, 180)
        self.assertEqual(player.db.equipment_hp_bonus, 80)

    def test_non_max_hp_gear_leaves_ceiling_unchanged(self):
        system, _, _ = _make_system()
        player = _HpEntity()
        system.equip(player, FakeItem("helmet", "head", {"damage_reduction": 3}))
        self.assertEqual(player.db.hp_max, 100)
        self.assertEqual(player.db.equipment_hp_bonus, 0)


# -------------------------------------------------------------- #
#  Death loss + respawn-building recovery
# -------------------------------------------------------------- #

class _FakeRespawnBuilding:
    """A Respawn building fake: a db bag that supports recovery_stash + level."""
    def __init__(self, level=1, planet="earth"):
        self.db = DB(building_type="RB", building_level=level,
                     coord_planet=planet, recovery_stash=None)
        self.location = None


def _death_registry():
    """Registry whose RB building def carries the respawn_point capability."""
    reg = _make_registry()
    from world.constants import RESPAWN_POINT
    reg.buildings["RB"] = BuildingDef(
        name="Respawn Beacon", abbreviation="RB", cost={"Wood": 20},
        max_health=200, requires_hq=True, required_terrain=None,
        category="utility", produces=None,
        capabilities=frozenset({RESPAWN_POINT, "upgradable"}),
    )
    return reg


class _DeterministicRNG:
    """rng.random() returns a fixed value, so recovery rolls are predictable."""
    def __init__(self, value):
        self._v = value
    def random(self):
        return self._v


class TestDeathLoss(unittest.TestCase):
    """apply_death_loss: total strip on death, building-scaled recovery."""

    def _player_with_loadout(self, planet="earth", resources=None):
        p = FakePlayer(level=10, resources=resources or {"Iron": 100, "Wood": 40})
        p.db.coord_planet = planet
        p.equipment.equip(FakeItem("assault_rifle", "weapon", {"damage": 25}))
        p.equipment.equip(FakeItem("kevlar_vest", "torso", {"damage_reduction": 5}))
        p.equipment.add_supply("medkit", 4)
        p._buildings = []
        p.get_buildings = lambda: list(p._buildings)
        return p

    def test_total_loss_with_no_respawn_building(self):
        system, _, _ = _make_system(_death_registry())
        p = self._player_with_loadout()
        # rng that would recover everything IF a building existed — but there's none.
        system._rng = _DeterministicRNG(0.0)
        summary = system.apply_death_loss(p)
        self.assertEqual(p.equipment.get_all_equipped(), {})
        self.assertEqual(p.equipment.get_supplies(), {})
        self.assertEqual(p.get_resource("Iron"), 0)
        self.assertEqual(p.get_resource("Wood"), 0)
        self.assertIsNone(summary["building"])
        self.assertEqual(summary["recovered"], {})

    def test_full_recovery_at_high_roll(self):
        system, _, _ = _make_system(_death_registry())
        p = self._player_with_loadout()
        b = _FakeRespawnBuilding(level=5, planet="earth")  # 95%
        p._buildings = [b]
        system._rng = _DeterministicRNG(0.0)  # 0.0 < 0.95 → everything recovers
        summary = system.apply_death_loss(p)
        # Player is stripped bare regardless...
        self.assertEqual(p.equipment.get_all_equipped(), {})
        self.assertEqual(p.get_resource("Iron"), 0)
        # ...but the stash holds the recovered loadout.
        stash = b.db.recovery_stash
        self.assertEqual(stash["items"].get("assault_rifle"), 1)
        self.assertEqual(stash["items"].get("kevlar_vest"), 1)
        self.assertEqual(stash["items"].get("medkit"), 4)
        self.assertEqual(stash["resources"].get("Iron"), 95)  # floor(100*0.95)
        self.assertEqual(stash["resources"].get("Wood"), 38)  # floor(40*0.95)

    def test_no_recovery_at_low_roll_but_resources_floored(self):
        system, _, _ = _make_system(_death_registry())
        p = self._player_with_loadout(resources={"Iron": 100})
        b = _FakeRespawnBuilding(level=1, planet="earth")  # 55%
        p._buildings = [b]
        system._rng = _DeterministicRNG(0.99)  # 0.99 >= 0.55 → no ITEM recovers
        system.apply_death_loss(p)
        stash = b.db.recovery_stash
        # Items all rolled fail → none stashed; resources are deterministic floor.
        self.assertEqual(stash["items"], {})
        self.assertEqual(stash["resources"].get("Iron"), 55)  # floor(100*0.55)

    def test_recovery_scales_with_building_level(self):
        system, _, _ = _make_system(_death_registry())
        # L3 = 75% → floor(100*0.75)=75 Iron recovered.
        p = self._player_with_loadout(resources={"Iron": 100})
        b = _FakeRespawnBuilding(level=3, planet="earth")
        p._buildings = [b]
        system._rng = _DeterministicRNG(0.99)
        system.apply_death_loss(p)
        self.assertEqual(b.db.recovery_stash["resources"].get("Iron"), 75)

    def test_off_planet_building_does_not_recover(self):
        """Recovery is per-planet: a respawn building on ANOTHER planet does not
        save a loadout lost on the death planet — the loss is total."""
        system, _, _ = _make_system(_death_registry())
        p = self._player_with_loadout(planet="earth", resources={"Iron": 100})
        mars_b = _FakeRespawnBuilding(level=5, planet="mars")
        p._buildings = [mars_b]
        system._rng = _DeterministicRNG(0.0)  # would recover all IF it counted
        summary = system.apply_death_loss(p)
        self.assertIsNone(summary["building"], "off-planet building must not count")
        self.assertEqual(p.get_resource("Iron"), 0)  # still stripped
        self.assertIsNone(mars_b.db.recovery_stash)  # nothing stashed there

    def test_same_planet_building_recovers_over_off_planet_one(self):
        """With respawn buildings on multiple planets, the one on the DEATH
        planet is chosen."""
        system, _, _ = _make_system(_death_registry())
        p = self._player_with_loadout(planet="earth", resources={"Iron": 100})
        p._buildings = [_FakeRespawnBuilding(level=1, planet="mars"),
                        _FakeRespawnBuilding(level=5, planet="earth")]
        system._rng = _DeterministicRNG(0.99)
        system.apply_death_loss(p)
        earth_b = p._buildings[1]
        self.assertEqual(earth_b.db.recovery_stash["resources"].get("Iron"), 95)

    def test_collect_round_trip_restores_stash(self):
        """die → recover into beacon → collect back: supplies rejoin the
        Supply_Bag, resources rejoin the pool, and the stash empties."""
        system, _, _ = _make_system(_death_registry())
        p = self._player_with_loadout(resources={"Iron": 100})
        b = _FakeRespawnBuilding(level=5, planet="earth")
        p._buildings = [b]
        system._rng = _DeterministicRNG(0.0)  # recover everything
        system.apply_death_loss(p)
        self.assertEqual(p.equipment.get_supplies(), {})  # stripped

        summary = system.collect_recovery(p, b)
        # medkit (supply) rejoined the Supply_Bag.
        self.assertEqual(p.equipment.get_supply("medkit"), 4)
        self.assertEqual(summary["items"].get("medkit"), 4)
        # Iron came back to the pool (admin/no-weight fake → unbounded room).
        self.assertEqual(p.get_resource("Iron"), 95)
        # Stash emptied of what was collected.
        stash = b.db.recovery_stash
        self.assertEqual(stash["resources"].get("Iron", 0), 0)
        self.assertEqual(stash["items"].get("medkit", 0), 0)

    def test_collect_empty_stash_is_safe(self):
        system, _, sink = _make_system(_death_registry())
        p = self._player_with_loadout()
        b = _FakeRespawnBuilding(level=1, planet="earth")
        summary = system.collect_recovery(p, b)
        self.assertEqual(summary["items"], {})
        self.assertEqual(summary["resources"], {})


class TestPvPGearDropOnDeath(unittest.TestCase):
    """apply_death_loss(player, killer): a slain player's DESTROYED gear can
    drop on their tile for the killer (PvP underdog bounty). Only equipped gear
    drops; supplies/resources never do; PvE/self deaths never drop."""

    def _victim(self, level=10, planet="earth"):
        p = FakePlayer(level=level, resources={"Iron": 100})
        p.db.coord_planet = planet
        p.equipment.equip(FakeItem("assault_rifle", "weapon", {"damage": 25}))
        p.equipment.equip(FakeItem("kevlar_vest", "torso", {"damage_reduction": 5}))
        p.equipment.add_supply("medkit", 4)
        p._buildings = []
        p.get_buildings = lambda: list(p._buildings)
        return p

    def _system_with_drop_recorder(self):
        system, event_bus, _ = _make_system(_death_registry())
        drops = []  # (victim, item_def)

        def _spawner(victim, item_def):
            drops.append((victim, item_def))
            return object()  # non-None → drop "spawned"

        system.set_pvp_gear_drop_spawner(_spawner)
        # Capture notifications WITH their target player (the shared
        # NotificationSink drops the player arg, but we need it to prove the
        # KILLER is the one told about the loot).
        notes = []  # (player, kind, data)

        def _sink(event_name=None, player=None, kind=None, data=None, **_x):
            notes.append((player, kind, data or {}))

        event_bus.subscribe(PLAYER_NOTIFICATION, _sink)
        return system, drops, notes

    def test_pvp_kill_drops_destroyed_gear(self):
        system, drops, notes = self._system_with_drop_recorder()
        victim = self._victim(level=10)
        killer = FakePlayer(level=10)
        # No respawn building → all gear is "destroyed"; base chance 0.15, roll
        # 0.0 always succeeds → both equipped items drop (supplies never do).
        system._rng = _DeterministicRNG(0.0)
        summary = system.apply_death_loss(victim, killer)
        dropped_keys = {d[1].key for d in drops}
        self.assertEqual(dropped_keys, {"assault_rifle", "kevlar_vest"})
        self.assertEqual(summary["dropped"].get("assault_rifle"), 1)
        self.assertEqual(summary["dropped"].get("kevlar_vest"), 1)
        # Supplies are NOT dropped (only equipped gear).
        self.assertNotIn("medkit", summary["dropped"])

    def test_killer_notified_of_drop_with_items_and_coords(self):
        system, _drops, notes = self._system_with_drop_recorder()
        victim = self._victim(level=10)
        victim.key = "Victim"          # distinct keys so the victim_name
        victim.db.coord_x, victim.db.coord_y = 42, 7
        killer = FakePlayer(level=10)
        killer.key = "Killer"          # assertion can't pass on a swap
        system._rng = _DeterministicRNG(0.0)
        system.apply_death_loss(victim, killer)
        # Exactly one pvp_gear_dropped notice, addressed to the KILLER (not the
        # victim), naming the gear (display names) and the pickup coords.
        drop_notes = [n for n in notes if n[1] == "pvp_gear_dropped"]
        self.assertEqual(len(drop_notes), 1)
        player, _kind, data = drop_notes[0]
        self.assertIs(player, killer)
        self.assertEqual((data["x"], data["y"]), (42, 7))
        # Planet is included so a cross-planet turret/agent kill isn't ambiguous.
        self.assertEqual(data["planet"], "earth")
        self.assertIn("Assault Rifle", data["items"])
        self.assertIn("Kevlar Vest", data["items"])
        # The victim is named (NOT the killer) — distinct keys prove no swap.
        self.assertEqual(data["victim_name"], "Victim")

    def test_no_drop_no_notification(self):
        # Gear all recovered (respawn building, low roll) → nothing dropped → the
        # killer gets no loot notice.
        system, _drops, notes = self._system_with_drop_recorder()
        victim = self._victim()
        victim._buildings = [_FakeRespawnBuilding(level=5, planet="earth")]
        killer = FakePlayer(level=10)
        system._rng = _DeterministicRNG(0.0)  # everything recovers, nothing drops
        system.apply_death_loss(victim, killer)
        self.assertNotIn("pvp_gear_dropped", [n[1] for n in notes])

    def test_no_killer_no_drop(self):
        # PvE / self / ally death → killer is None → nothing drops.
        system, drops, _ = self._system_with_drop_recorder()
        victim = self._victim()
        system._rng = _DeterministicRNG(0.0)
        summary = system.apply_death_loss(victim, None)
        self.assertEqual(drops, [])
        self.assertEqual(summary["dropped"], {})
        # Gear was still stripped/destroyed as before.
        self.assertEqual(victim.equipment.get_all_equipped(), {})

    def test_self_kill_no_drop(self):
        system, drops, _ = self._system_with_drop_recorder()
        victim = self._victim()
        system._rng = _DeterministicRNG(0.0)
        system.apply_death_loss(victim, victim)  # killer is victim
        self.assertEqual(drops, [])
        # Positive signal that the strip actually ran (so drops==[] isn't
        # vacuously true from a short-circuit): gear was still stripped.
        self.assertEqual(victim.equipment.get_all_equipped(), {})

    def test_disabled_at_zero_base_chance(self):
        system, drops, _ = self._system_with_drop_recorder()
        system.registry.balance.pvp_gear_drop_base_chance = 0.0
        victim = self._victim()
        killer = FakePlayer(level=10)
        system._rng = _DeterministicRNG(0.0)
        system.apply_death_loss(victim, killer)
        self.assertEqual(drops, [])
        # Positive signal: the strip ran (drops==[] is the disable, not a no-op).
        self.assertEqual(victim.equipment.get_all_equipped(), {})

    def test_recovered_gear_is_not_dropped(self):
        # With a respawn building and a low roll, gear is RECOVERED into the
        # stash — recovered items must never also drop for the killer.
        system, drops, _ = self._system_with_drop_recorder()
        victim = self._victim()
        b = _FakeRespawnBuilding(level=5, planet="earth")  # 95% recovery
        victim._buildings = [b]
        killer = FakePlayer(level=10)
        system._rng = _DeterministicRNG(0.0)  # 0.0 < 0.95 → everything recovers
        summary = system.apply_death_loss(victim, killer)
        self.assertEqual(drops, [])  # nothing destroyed → nothing to drop
        self.assertEqual(summary["dropped"], {})
        self.assertEqual(b.db.recovery_stash["items"].get("assault_rifle"), 1)

    def test_underdog_scaling_increases_chance(self):
        # base 0.15 + 0.02/level over. Victim L30 vs killer L10 → gap 20 →
        # 0.15 + 0.40 = 0.55, clamped to max 0.50.
        system, _drops, _ = self._system_with_drop_recorder()
        victim = self._victim(level=30)
        killer = FakePlayer(level=10)
        chance = system._pvp_gear_drop_chance(victim, killer)
        self.assertAlmostEqual(chance, 0.50)  # clamped to pvp_gear_drop_max_chance
        # Ganking DOWN (victim below killer) → only the base chance.
        low_victim = self._victim(level=5)
        self.assertAlmostEqual(
            system._pvp_gear_drop_chance(low_victim, killer), 0.15
        )

    def test_drop_falls_back_to_destroy_when_spawner_unwired(self):
        # No spawner injected → _drop_gear_on_death returns False → gear is
        # destroyed and counted as lost, not dropped (no crash).
        system, _, _ = _make_system(_death_registry())
        victim = self._victim()
        killer = FakePlayer(level=10)
        system._rng = _DeterministicRNG(0.0)
        summary = system.apply_death_loss(victim, killer)
        self.assertEqual(summary["dropped"], {})
        self.assertIn("assault_rifle", summary["lost"])

    def test_spawner_refusal_counts_as_lost_and_no_notification(self):
        # Spawner WIRED but REFUSES (returns None — e.g. tile full): the item
        # must be counted as LOST, not dropped, and NO killer loot notice fires
        # (guards against telling the killer to grab loot that never spawned).
        system, event_bus, _ = _make_system(_death_registry())
        system.set_pvp_gear_drop_spawner(lambda victim, item_def: None)
        notes = []
        event_bus.subscribe(
            PLAYER_NOTIFICATION,
            lambda event_name=None, player=None, kind=None, data=None, **_x:
            notes.append(kind),
        )
        victim = self._victim()
        killer = FakePlayer(level=10)
        system._rng = _DeterministicRNG(0.0)  # would drop IF the spawner accepted
        summary = system.apply_death_loss(victim, killer)
        self.assertEqual(summary["dropped"], {})
        self.assertIn("assault_rifle", summary["lost"])
        self.assertIn("kevlar_vest", summary["lost"])
        self.assertNotIn("pvp_gear_dropped", notes)

    def test_notify_pvp_drop_aggregates_counts_and_resolves_names(self):
        # Directly exercise _notify_pvp_drop: count>1 renders "Name xN"; a
        # registry-resolved key uses its display name; an unknown key falls back
        # to the raw key (the defensive `or key` branch).
        system, event_bus, _ = _make_system(_death_registry())
        notes = []
        event_bus.subscribe(
            PLAYER_NOTIFICATION,
            lambda event_name=None, player=None, kind=None, data=None, **_x:
            notes.append((player, kind, data or {})),
        )
        killer = FakePlayer(level=10)
        killer.key = "Killer"
        victim = self._victim()
        victim.key = "Victim"
        victim.db.coord_x, victim.db.coord_y = 3, 9
        system._notify_pvp_drop(
            killer, victim,
            {"assault_rifle": 2, "mystery_key": 1},  # known (x2) + unknown key
        )
        self.assertEqual(len(notes), 1)
        player, kind, data = notes[0]
        self.assertIs(player, killer)
        self.assertEqual(kind, "pvp_gear_dropped")
        self.assertEqual(data["victim_name"], "Victim")
        self.assertEqual((data["x"], data["y"]), (3, 9))
        self.assertEqual(data["planet"], "earth")  # via sanctioned coords_of
        self.assertIn("Assault Rifle x2", data["items"])  # count>1 aggregation
        self.assertIn("mystery_key", data["items"])        # unknown key fallback


# -------------------------------------------------------------- #
#  Weight / storage fakes (task 9.7)
# -------------------------------------------------------------- #

class FakeResourcePlayer:
    """A player whose Spend_Pool (``db.resources``) is the single pool that the
    inflow choke point writes and that cost checks read.

    The resource accessors (``get_resource``/``add_resource``/
    ``has_resources``/``deduct_resources``) operate directly on
    ``db.resources`` with the canonical title-case keys, mirroring the real
    ``CombatCharacter`` — so ``carried_weight`` (which iterates
    ``db.resources``) and the inflow paths agree on one pool.
    """

    def __init__(self, level=1, resources=None, admin=False):
        self.key = "ResPlayer"
        self.db = DB(
            level=level,
            hp=100,
            hp_max=100,
            resources=dict(resources or {}),
            coord_x=0,
            coord_y=0,
            combat_xp=0,
        )
        self.equipment = EquipmentHandler(self)
        self.location = None
        self._admin = admin

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
    """Stand-in for a ``storage``-capability Building with a stored pool.

    Exposes ``db.building_type`` (so ``get_building_type`` resolves the
    ``BuildingDef`` via the registry) and ``db.stored_resources`` (the pool
    ``building_storage`` reads/writes). It deliberately carries no
    ``db.combat_xp`` so ``is_player`` never mis-classifies it.
    """

    def __init__(self, building_type="VT", owner=None):
        self.key = building_type
        self.db = DB(
            building_type=building_type,
            coord_x=0,
            coord_y=0,
            stored_resources={},
        )
        self._owner = owner

    @property
    def owner(self):
        return self._owner


#: Storage BuildingDefs the singleton registry resolves for the tests below.
_STORAGE_BUILDINGS = {
    "VT": BuildingDef(
        name="Vault", abbreviation="VT", cost={}, max_health=500,
        requires_hq=True, required_terrain=None, category="storage",
        produces=None, storage_capacity=1000,
        capabilities=frozenset({"storage"}),
    ),
    "HQ": BuildingDef(
        name="Headquarters", abbreviation="HQ", cost={}, max_health=1000,
        requires_hq=False, required_terrain=None, category="command",
        produces=None, storage_capacity=500,
        capabilities=frozenset({"storage"}),
    ),
}


def _make_storage_registry():
    """Registry with storage BuildingDefs + the items/ranks used by the tests."""
    registry = _make_registry()
    registry.buildings = dict(_STORAGE_BUILDINGS)
    return registry


class _StorageSingletonMixin:
    """Registers a storage registry as the process-wide singleton.

    ``building_storage`` resolves ``storage_capacity`` through the *default*
    provider (``DataRegistry.get_instance()``) when no provider is passed —
    which is exactly how ``add_resource_capped``/``deposit``/``withdraw`` call
    it. So the singleton must resolve the building's capacity for these tests.
    """

    def setUp(self):
        super().setUp()
        # building_storage resolves capacity through the *production* module
        # tree (``world.*``), which is a distinct import from ``mygame.world.*``
        # used elsewhere in this test. Register the singleton on that class so
        # ``default_definitions_provider()`` sees it.
        from world.data_registry import DataRegistry as CoreRegistry

        self._core_registry = CoreRegistry
        self._prev_instance = CoreRegistry.get_instance()
        self.registry = _make_storage_registry()
        CoreRegistry.set_instance(self.registry)

    def tearDown(self):
        self._core_registry.set_instance(self._prev_instance)
        super().tearDown()


# -------------------------------------------------------------- #
#  carried_weight — supplies + resources, equipped gear excluded
# -------------------------------------------------------------- #

class TestCarriedWeight(unittest.TestCase):
    """``carried_weight`` = Supply_Bag weight + on-person resource weight; worn
    Gear is excluded (Req 15.4)."""

    def test_carried_weight_sums_supplies_and_resources(self):
        system, _, _ = _make_system()
        # Wood default weight 0.5; 10 wood -> 5.0. medkit weight 5.0; 2 -> 10.0.
        player = FakeResourcePlayer(level=1, resources={"Wood": 10})
        player.equipment.add_supply("medkit", 2)
        self.assertAlmostEqual(system.carried_weight(player), 15.0)

    def test_equipped_gear_excluded_from_carried_weight(self):
        system, _, _ = _make_system()
        player = FakeResourcePlayer(level=1, resources={"Wood": 10})
        player.equipment.add_supply("medkit", 2)
        before = system.carried_weight(player)
        # Equip a heavy piece of Gear — worn, not hauled, so weight unchanged.
        heavy_armor = FakeItem("plate", "torso", {"damage_reduction": 20})
        self.assertTrue(system.equip(player, heavy_armor))
        self.assertAlmostEqual(system.carried_weight(player), before)

    def test_empty_player_has_zero_carried_weight(self):
        system, _, _ = _make_system()
        player = FakeResourcePlayer(level=1)
        self.assertEqual(system.carried_weight(player), 0.0)


# -------------------------------------------------------------- #
#  carry_limit — player capped, admin unlimited
# -------------------------------------------------------------- #

class TestCarryLimitAdmin(unittest.TestCase):
    """Players are capped at ``BASE_CARRY_WEIGHT`` (+ gear); admins unbounded
    (Req 15.5, 15.6)."""

    def test_player_limit_is_base_carry_weight(self):
        system, _, _ = _make_system()
        player = FakeResourcePlayer(level=1, admin=False)
        self.assertEqual(system.carry_limit(player), float(BASE_CARRY_WEIGHT))

    def test_admin_limit_is_infinite(self):
        system, _, _ = _make_system()
        admin = FakeResourcePlayer(level=1, admin=True)
        self.assertEqual(system.carry_limit(admin), float("inf"))


# -------------------------------------------------------------- #
#  add_resource_capped — player: capped inflow, over-cap drop, conservation
# -------------------------------------------------------------- #

class TestAddResourceCappedPlayer(unittest.TestCase):
    """The inflow choke point caps a player's pool by carry weight, spills the
    remainder to a drop, and conserves the offered amount (Req 15.7, 16.7,
    16.8)."""

    def _system_with_drop_recorder(self):
        system, _, sink = _make_system()
        drops = []
        system.set_resource_drop_spawner(
            lambda holder, resource, amount: drops.append((resource, amount))
        )
        return system, sink, drops

    def test_player_capped_and_over_cap_spills_and_conserves(self):
        system, sink, drops = self._system_with_drop_recorder()
        # Iron weight 1.0; base limit 1000 -> at most 1000 units fit by weight.
        player = FakeResourcePlayer(level=1)
        added = system.add_resource_capped(player, "Iron", 1500)

        self.assertEqual(added, 1000)
        self.assertEqual(player.get_resource("Iron"), 1000)
        dropped = sum(a for _, a in drops)
        self.assertEqual(dropped, 500)
        # Conservation: nothing created or destroyed.
        self.assertEqual(added + dropped, 1500)
        # Bound: carried weight never exceeds the limit.
        self.assertLessEqual(
            system.carried_weight(player), system.carry_limit(player)
        )
        self.assertIn("carry_full", sink.kinds())

    def test_player_within_cap_takes_all_no_drop(self):
        system, sink, drops = self._system_with_drop_recorder()
        player = FakeResourcePlayer(level=1)
        added = system.add_resource_capped(player, "Iron", 400)
        self.assertEqual(added, 400)
        self.assertEqual(drops, [])
        self.assertNotIn("carry_full", sink.kinds())

    def test_player_exactly_at_cap_takes_all_no_spurious_drop(self):
        # Boundary: offered == remaining room exactly. All should be taken with
        # no leftover drop and no carry_full — guards the float-floor off-by-one.
        system, sink, drops = self._system_with_drop_recorder()
        player = FakeResourcePlayer(level=1)  # Iron weight 1.0, limit 1000
        added = system.add_resource_capped(player, "Iron", 1000)
        self.assertEqual(added, 1000)
        self.assertEqual(drops, [])
        self.assertNotIn("carry_full", sink.kinds())
        self.assertEqual(system.carried_weight(player), system.carry_limit(player))

    def test_fractional_weight_exact_fill_not_undercounted(self):
        # Energy weight 0.2, limit 1000 -> exactly 5000 units fit (0.2*5000 ==
        # 1000.0). The epsilon guards against float-floor stranding one unit.
        system, sink, drops = self._system_with_drop_recorder()
        player = FakeResourcePlayer(level=1)
        added = system.add_resource_capped(player, "Energy", 5000)
        self.assertEqual(added, 5000)
        self.assertEqual(drops, [])

    def test_admin_bypasses_cap(self):
        system, sink, drops = self._system_with_drop_recorder()
        admin = FakeResourcePlayer(level=1, admin=True)
        added = system.add_resource_capped(admin, "Iron", 5000)
        self.assertEqual(added, 5000)
        self.assertEqual(admin.get_resource("Iron"), 5000)
        self.assertEqual(drops, [])


# -------------------------------------------------------------- #
#  add_resource_capped — building: capacity cap, over-cap drop, conservation
# -------------------------------------------------------------- #

class TestAddResourceCappedBuilding(_StorageSingletonMixin, unittest.TestCase):
    """The inflow choke point caps a building's stored pool by
    ``storage_capacity``, spills the remainder to a drop, and conserves the
    offered amount (Req 16.7, 16.8)."""

    def test_building_capped_and_over_cap_spills_and_conserves(self):
        event_bus = EventBus()
        system = EquipmentSystem(self.registry, event_bus)
        sink = NotificationSink()
        event_bus.subscribe(PLAYER_NOTIFICATION, sink)
        drops = []
        system.set_resource_drop_spawner(
            lambda holder, resource, amount: drops.append((resource, amount))
        )

        owner = FakeResourcePlayer(level=1)
        building = FakeStorageBuilding("VT", owner=owner)  # capacity 1000
        added = system.add_resource_capped(building, "Iron", 1500)

        self.assertEqual(added, 1000)
        self.assertEqual(bs.get_total_stored(building), 1000)
        dropped = sum(a for _, a in drops)
        self.assertEqual(dropped, 500)
        self.assertEqual(added + dropped, 1500)
        self.assertIn("storage_full", sink.kinds())

    def test_building_within_capacity_takes_all(self):
        system = EquipmentSystem(self.registry, EventBus())
        drops = []
        system.set_resource_drop_spawner(
            lambda holder, resource, amount: drops.append((resource, amount))
        )
        building = FakeStorageBuilding("VT")
        added = system.add_resource_capped(building, "Iron", 600)
        self.assertEqual(added, 600)
        self.assertEqual(bs.get_stored(building, "Iron"), 600)
        self.assertEqual(drops, [])


# -------------------------------------------------------------- #
#  deposit / withdraw — conservation, capacity, carry-weight bound, HQ
# -------------------------------------------------------------- #

class TestDepositWithdraw(_StorageSingletonMixin, unittest.TestCase):
    """Deposit/withdraw conserve total resources (player pool + building pool)
    and never push carried weight over the limit (Req 16.2–16.4, 16.8)."""

    def _make(self):
        event_bus = EventBus()
        system = EquipmentSystem(self.registry, event_bus)
        sink = NotificationSink()
        event_bus.subscribe(PLAYER_NOTIFICATION, sink)
        return system, sink

    def test_deposit_moves_and_conserves_total(self):
        system, sink = self._make()
        player = FakeResourcePlayer(level=1, resources={"Wood": 200})
        vault = FakeStorageBuilding("VT")  # capacity 1000

        before = player.get_resource("Wood") + bs.get_stored(vault, "Wood")
        stored = system.deposit(player, vault, "Wood", 150)

        self.assertEqual(stored, 150)
        self.assertEqual(player.get_resource("Wood"), 50)
        self.assertEqual(bs.get_stored(vault, "Wood"), 150)
        after = player.get_resource("Wood") + bs.get_stored(vault, "Wood")
        self.assertEqual(before, after)  # conserved
        self.assertIn("deposited", sink.kinds())

    def test_deposit_capped_by_capacity_does_not_destroy(self):
        system, _ = self._make()
        # HQ capacity 500; player holds 700 -> only 500 stored, 200 stays.
        player = FakeResourcePlayer(level=1, resources={"Wood": 700})
        hq = FakeStorageBuilding("HQ")

        before = player.get_resource("Wood") + bs.get_stored(hq, "Wood")
        stored = system.deposit(player, hq, "Wood", 700)

        self.assertEqual(stored, 500)
        self.assertEqual(bs.get_total_stored(hq), 500)
        self.assertEqual(player.get_resource("Wood"), 200)  # surplus preserved
        after = player.get_resource("Wood") + bs.get_stored(hq, "Wood")
        self.assertEqual(before, after)

    def test_withdraw_never_exceeds_carry_weight_and_conserves(self):
        system, sink = self._make()
        # Vault holds 3000 Iron (weight 1.0). Player carry limit 1000 ->
        # withdraw caps at 1000 units; the remaining 2000 stays in storage.
        player = FakeResourcePlayer(level=1)
        vault = FakeStorageBuilding("VT")
        vault.db.stored_resources = {"Iron": 3000}

        before = player.get_resource("Iron") + bs.get_stored(vault, "Iron")
        withdrawn = system.withdraw(player, vault, "Iron", 3000)

        self.assertEqual(withdrawn, 1000)
        self.assertEqual(player.get_resource("Iron"), 1000)
        self.assertEqual(bs.get_stored(vault, "Iron"), 2000)  # leftover stays
        after = player.get_resource("Iron") + bs.get_stored(vault, "Iron")
        self.assertEqual(before, after)  # conserved
        # Bound: carried weight never exceeds the limit.
        self.assertLessEqual(
            system.carried_weight(player), system.carry_limit(player)
        )
        self.assertIn("withdrew", sink.kinds())

    def test_admin_withdraw_unbounded_by_carry_weight(self):
        system, _ = self._make()
        admin = FakeResourcePlayer(level=1, admin=True)
        vault = FakeStorageBuilding("VT")
        vault.db.stored_resources = {"Iron": 900}
        withdrawn = system.withdraw(admin, vault, "Iron", 900)
        self.assertEqual(withdrawn, 900)
        self.assertEqual(admin.get_resource("Iron"), 900)

    def test_deposit_withdraw_round_trip_conserves(self):
        system, _ = self._make()
        player = FakeResourcePlayer(level=1, resources={"Stone": 300})
        vault = FakeStorageBuilding("VT")
        total_before = player.get_resource("Stone")

        system.deposit(player, vault, "Stone", 300)
        system.withdraw(player, vault, "Stone", 300)

        total_after = player.get_resource("Stone") + bs.get_stored(vault, "Stone")
        self.assertEqual(total_before, total_after)

    def test_hq_usable_from_level_1(self):
        """A non-zero-capacity HQ accepts deposits/withdrawals at level 1
        (Req 16.2, no rank gate on storage)."""
        system, _ = self._make()
        player = FakeResourcePlayer(level=1, resources={"Wood": 100})
        hq = FakeStorageBuilding("HQ")  # capacity 500

        stored = system.deposit(player, hq, "Wood", 100)
        self.assertEqual(stored, 100)
        self.assertEqual(bs.get_stored(hq, "Wood"), 100)

        withdrawn = system.withdraw(player, hq, "Wood", 100)
        self.assertEqual(withdrawn, 100)
        self.assertEqual(player.get_resource("Wood"), 100)

    def test_deposit_all_via_none_amount(self):
        # amount=None means "all held" — exercised against the real system, not
        # just command forwarding. Capacity < held, so surplus stays on player.
        system, _ = self._make()
        player = FakeResourcePlayer(level=1, resources={"Wood": 700})
        hq = FakeStorageBuilding("HQ")  # capacity 500

        stored = system.deposit(player, hq, "Wood", None)
        self.assertEqual(stored, 500)              # capped by capacity
        self.assertEqual(player.get_resource("Wood"), 200)  # surplus preserved

    def test_withdraw_all_via_none_amount(self):
        # amount=None withdraws as much as stored, capped by carry weight.
        system, _ = self._make()
        player = FakeResourcePlayer(level=1)
        vault = FakeStorageBuilding("VT")
        vault.db.stored_resources = {"Iron": 3000}  # Iron weight 1.0, cap 1000

        withdrawn = system.withdraw(player, vault, "Iron", None)
        self.assertEqual(withdrawn, 1000)          # capped by carry weight
        self.assertEqual(bs.get_stored(vault, "Iron"), 2000)

    def test_deposit_nothing_held_notifies(self):
        system, sink = self._make()
        player = FakeResourcePlayer(level=1)  # holds no Wood
        hq = FakeStorageBuilding("HQ")
        stored = system.deposit(player, hq, "Wood", 100)
        self.assertEqual(stored, 0)
        self.assertIn("deposit_failed", sink.kinds())

    def test_withdraw_nothing_stored_notifies(self):
        system, sink = self._make()
        player = FakeResourcePlayer(level=1)
        vault = FakeStorageBuilding("VT")  # empty
        withdrawn = system.withdraw(player, vault, "Iron", 100)
        self.assertEqual(withdrawn, 0)
        self.assertIn("withdraw_failed", sink.kinds())


# -------------------------------------------------------------- #
#  process_production — category routing (task 8.4)
# -------------------------------------------------------------- #

class FakeProductionBuilding:
    """Stand-in for an active production building (AR/MB/LB).

    Passive production is gated on an assigned agent, so ``assigned_agent``
    defaults to a truthy sentinel (an agent is present). Pass
    ``assigned_agent=None`` to model an agentless (inert) building.
    """

    def __init__(self, building_type="AR", owner=None, offline=False,
                 assigned_agent="engineer"):
        self.key = building_type
        self.db = DB(building_type=building_type, offline=offline,
                     assigned_agent=assigned_agent)
        self._owner = owner

    @property
    def owner(self):
        return self._owner

    @property
    def is_offline(self):
        return bool(getattr(self.db, "offline", False))


class TestProductionRouting(unittest.TestCase):
    """Produced items are routed to storage by their category (Req 3.2, 3.3).

    Supply (ammo/consumable/throwable) becomes a counted Supply_Bag stack;
    Gear (armor/weapon/accessory) becomes a unique Game_Item slot object.
    There is no crossover between the two stores.

    Validates: Requirements 3.2, 3.3, 13.4
    """

    def _make(self, production_map):
        registry = _make_registry()
        # Add a gear item alongside the supply items already in ITEMS.
        registry.items["kevlar_vest"] = ItemDef(
            key="kevlar_vest", name="Kevlar Vest", slot="torso",
            category="armor", stat_modifiers={"damage_reduction": 5},
            craft_cost={"Iron": 20, "Stone": 10},
        )
        registry.item_production_map = dict(production_map)
        event_bus = EventBus()
        created = []
        system = EquipmentSystem(
            registry, event_bus,
            create_item_func=lambda idef, owner: created.append(idef.key),
        )
        return system, created

    @staticmethod
    def _rich_player():
        """A player with plenty of every resource, so craft_cost is affordable
        and the tests below exercise routing/rate/cap, not the resource gate.

        Also owns an HQ so passive production isn't blocked by the
        base-deactivation gate (production stops with no active HQ)."""
        return _give_hq(FakePlayer(level=1, resources={
            r: 100000 for r in
            ("Wood", "Stone", "Iron", "Energy", "Circuits", "Nexium")
        }))

    def test_supply_category_lands_in_bag_not_as_object(self):
        system, created = self._make({"MB": ["medkit"]})
        player = self._rich_player()
        building = FakeProductionBuilding("MB", owner=player)

        system.process_production([building])

        # A counted stack in the Supply_Bag; no Game_Item object created.
        self.assertEqual(player.equipment.get_supply("medkit"), 1)
        self.assertEqual(created, [])

    def test_production_stops_when_owner_has_no_hq(self):
        """Phase 2: an equipment building produces nothing while its owner has
        no active HQ (the 'no HQ = base inert' deactivation rule)."""
        system, created = self._make({"MB": ["medkit"]})
        player = self._rich_player()
        player.get_buildings = lambda: []  # HQ destroyed -> base deactivated
        building = FakeProductionBuilding("MB", owner=player)

        for _ in range(5):
            system.process_production([building])

        self.assertEqual(player.equipment.get_supply("medkit"), 0)
        self.assertEqual(created, [])

    def test_gear_category_becomes_object_not_bag_entry(self):
        system, created = self._make({"AR": ["kevlar_vest"]})
        player = self._rich_player()
        building = FakeProductionBuilding("AR", owner=player)

        system.process_production([building])

        # A unique Game_Item object; nothing added to the Supply_Bag.
        self.assertEqual(created, ["kevlar_vest"])
        self.assertEqual(player.equipment.get_supplies(), {})

    def test_passive_gear_production_drops_on_building_tile(self):
        """When a gear-drop spawner is wired (production), passive gear produce
        is spawned on the BUILDING (a ground drop), NOT the owner's inventory."""
        system, created = self._make({"AR": ["kevlar_vest"]})
        # Wire a gear-drop spawner that records (building, item_def).
        dropped = []
        system.set_gear_drop_spawner(
            lambda building, item_def: dropped.append((building, item_def.key))
            or object()  # non-None => routing success
        )
        player = self._rich_player()
        building = FakeProductionBuilding("AR", owner=player)

        system.process_production([building])

        # Gear went to the drop spawner (on the building), not the inventory
        # factory, and not the Supply_Bag.
        self.assertEqual(len(dropped), 1)
        self.assertIs(dropped[0][0], building)
        self.assertEqual(dropped[0][1], "kevlar_vest")
        self.assertEqual(created, [], "gear must NOT go to the inventory factory")
        self.assertEqual(player.equipment.get_supplies(), {})

    def test_passive_gear_drop_failure_refunds(self):
        """If the gear-drop spawner returns None (no resolvable tile), production
        treats it as a routing failure and refunds the craft_cost."""
        system, created = self._make({"AR": ["kevlar_vest"]})
        system.set_gear_drop_spawner(lambda building, item_def: None)
        player = self._rich_player()
        before = player.get_resource("Iron")
        building = FakeProductionBuilding("AR", owner=player)

        system.process_production([building])

        self.assertEqual(created, [])
        # kevlar_vest craft_cost is Iron: 20 — must be refunded after the None.
        self.assertEqual(player.get_resource("Iron"), before)

    def test_no_crossover_over_many_ticks(self):
        # AR list mixes gear (kevlar_vest) and supply (rifle_rounds).
        system, created = self._make(
            {"AR": ["kevlar_vest", "rifle_rounds"]}
        )
        player = self._rich_player()
        building = FakeProductionBuilding("AR", owner=player)

        for _ in range(40):
            system.process_production([building])

        # Every gear produce is an object (never a bag count) and every
        # supply produce is a bag count (never an object).
        self.assertTrue(all(k == "kevlar_vest" for k in created))
        bag = player.equipment.get_supplies()
        self.assertTrue(set(bag).issubset({"rifle_rounds"}))
        # Conservation: gear objects + supply counts == ticks produced.
        self.assertEqual(len(created) + sum(bag.values()), 40)

    def test_supply_without_handler_produces_nothing(self):
        system, created = self._make({"MB": ["medkit"]})

        class NoHandlerOwner:
            """Has resources but no equipment handler — routing fails, refunds."""
            key = "NoHandler"

            def __init__(self):
                self._res = {"Wood": 1000}

            def get_resource(self, r):
                return self._res.get(str(r).title(), 0)

            def has_resources(self, costs):
                return all(self._res.get(str(r).title(), 0) >= a
                           for r, a in costs.items())

            def deduct_resources(self, costs):
                if not self.has_resources(costs):
                    return False
                for r, a in costs.items():
                    self._res[str(r).title()] -= a
                return True

            def add_resource(self, r, a):
                self._res[str(r).title()] = self._res.get(str(r).title(), 0) + a

        owner = NoHandlerOwner()
        building = FakeProductionBuilding("MB", owner=owner)
        # Must not raise; nothing is created and the spend is refunded.
        system.process_production([building])
        self.assertEqual(created, [])
        self.assertEqual(owner.get_resource("Wood"), 1000)  # refunded

    def test_production_at_max_stack_refunds_and_produces_nothing(self):
        """A full Supply_Bag entry must not silently burn the owner's resources.

        Regression: ``add_supply`` adds 0 once the entry is at ``max_stack``.
        ``_route_produced_item`` must report that as a routing failure so the
        deducted ``craft_cost`` is refunded — otherwise the owner pays for an
        item that never lands in the bag.
        """
        system, created = self._make({"MB": ["medkit"]})
        player = self._rich_player()
        # medkit max_stack is 10 (see ITEMS). Fill the bag to the cap.
        player.equipment.add_supply("medkit", 10, max_stack=10)
        before = player.get_resource("Wood")  # medkit craft_cost is Wood: 5

        for _ in range(5):
            system.process_production([building := FakeProductionBuilding(
                "MB", owner=player)])

        # Still capped at 10, and not a single Wood was consumed.
        self.assertEqual(player.equipment.get_supply("medkit"), 10)
        self.assertEqual(player.get_resource("Wood"), before)
        self.assertEqual(created, [])

    def test_production_gear_factory_raise_refunds(self):
        """A raising gear factory during passive production refunds the owner.

        Regression: _route_produced_item now contains the exception and reports
        failure, so the refund fires and the tick loop isn't handed an escaping
        error mid-building.
        """
        registry = _make_registry()
        registry.items["kevlar_vest"] = ItemDef(
            key="kevlar_vest", name="Kevlar Vest", slot="torso",
            category="armor", stat_modifiers={"damage_reduction": 5},
            craft_cost={"Iron": 20, "Stone": 10},
        )
        registry.item_production_map = {"AR": ["kevlar_vest"]}
        event_bus = EventBus()

        def boom(idef, owner):
            raise RuntimeError("create_object failed")

        system = EquipmentSystem(registry, event_bus, create_item_func=boom)
        player = self._rich_player()
        before_iron = player.get_resource("Iron")
        building = FakeProductionBuilding("AR", owner=player)

        # Must not raise; the spend is refunded, nothing produced.
        system.process_production([building])
        self.assertEqual(player.get_resource("Iron"), before_iron)

    def test_production_requires_assigned_agent(self):
        """An equipment building with no assigned agent produces nothing."""
        system, created = self._make({"MB": ["medkit"]})
        player = FakePlayer(level=1)
        building = FakeProductionBuilding("MB", owner=player, assigned_agent=None)

        for _ in range(40):
            system.process_production([building])

        self.assertEqual(player.equipment.get_supply("medkit"), 0)
        self.assertEqual(created, [])

    def test_production_stalls_when_owner_cannot_afford(self):
        """With no resources, an agent-run building idles (no free items)."""
        system, created = self._make({"MB": ["medkit"]})
        system.registry.balance.equipment_production_ticks = 1
        player = FakePlayer(level=1, resources={})  # empty stockpile
        building = FakeProductionBuilding("MB", owner=player)

        for _ in range(10):
            system.process_production([building])

        self.assertEqual(player.equipment.get_supply("medkit"), 0)

    def test_production_is_rate_gated_by_cooldown(self):
        # With the default cooldown, a building yields at most one item per
        # equipment_production_ticks, not one every tick.
        system, _created = self._make({"MB": ["medkit"]})
        system.registry.balance.equipment_production_ticks = 5
        player = self._rich_player()
        building = FakeProductionBuilding("MB", owner=player)

        for _ in range(5):
            system.process_production([building])
        # 5 ticks at cooldown 5 -> exactly one yield (on the 5th tick).
        self.assertEqual(player.equipment.get_supply("medkit"), 1)

        for _ in range(5):
            system.process_production([building])
        self.assertEqual(player.equipment.get_supply("medkit"), 2)

    def test_production_stalls_at_owner_cap(self):
        # Once the owner holds owner_cap un-equipped items, production stalls.
        system, _created = self._make({"MB": ["medkit"]})
        system.registry.balance.equipment_production_ticks = 1
        system.registry.balance.equipment_production_owner_cap = 3
        player = self._rich_player()
        building = FakeProductionBuilding("MB", owner=player)

        for _ in range(20):
            system.process_production([building])
        # Never exceeds the cap despite 20 ticks.
        self.assertEqual(player.equipment.get_supply("medkit"), 3)


class TestOwnerProducedCount(unittest.TestCase):
    """_owner_produced_count bounds ACCUMULATION: supplies + un-equipped gear.

    Equipped gear must NOT count — equipment slots are bounded and equipping is
    how a player relieves the production stall.
    """

    class _GearObj:
        """A carried Game_Item object (as _owner_produced_count sees it)."""
        _object_type_tag = "item"

        def __init__(self, key, slot):
            self.key = key
            self.name = key
            self.slot = slot
            self.stat_modifiers = {}

        def get_stat(self, stat_name, default=0):
            return float(self.stat_modifiers.get(stat_name, default))

    class _Owner:
        """Owner with a real EquipmentHandler and a carried-object list."""
        def __init__(self):
            self.key = "Owner"
            self.db = DB()
            self.equipment = EquipmentHandler(self)
            self.contents = []

    def test_equipped_gear_not_counted(self):
        owner = self._Owner()
        vest = self._GearObj("kevlar_vest", "torso")
        helmet = self._GearObj("helmet", "head")
        owner.contents = [vest, helmet]
        # Both carried, un-equipped -> both count.
        self.assertEqual(EquipmentSystem._owner_produced_count(owner), 2)

        # Equip the vest; it stays in contents but must drop out of the count.
        ok, _msg = owner.equipment.equip(vest)
        self.assertTrue(ok)
        self.assertEqual(EquipmentSystem._owner_produced_count(owner), 1)

    def test_supplies_and_unequipped_gear_summed(self):
        owner = self._Owner()
        owner.equipment.add_supply("medkit", 4, max_stack=20)
        owner.contents = [self._GearObj("kevlar_vest", "torso")]
        # 4 supply units + 1 un-equipped gear object = 5.
        self.assertEqual(EquipmentSystem._owner_produced_count(owner), 5)

    def test_equipping_relieves_production_stall(self):
        """A player at the cap resumes production after equipping gear.

        Regression: equipped gear used to count, so a fully-kitted player could
        permanently starve their own equipment building.
        """
        registry = _make_registry()
        registry.items["kevlar_vest"] = ItemDef(
            key="kevlar_vest", name="Kevlar Vest", slot="torso",
            category="armor", craft_cost={"Iron": 1},
        )
        registry.item_production_map = {"AR": ["kevlar_vest"]}
        registry.balance.equipment_production_ticks = 1
        registry.balance.equipment_production_owner_cap = 1
        event_bus = EventBus()

        owner = self._Owner()
        owner.db.resources = {"Iron": 100}
        # Resource-pool shims (production reads has_resources/deduct_resources).
        owner.has_resources = lambda costs: all(
            owner.db.resources.get(str(r).title(), 0) >= a
            for r, a in costs.items())

        def _deduct(costs):
            if not owner.has_resources(costs):
                return False
            for r, a in costs.items():
                owner.db.resources[str(r).title()] -= a
            return True
        owner.deduct_resources = _deduct
        owner.add_resource = lambda r, a: owner.db.resources.__setitem__(
            str(r).title(), owner.db.resources.get(str(r).title(), 0) + a)
        _give_hq(owner)  # owner has an HQ so production isn't deactivation-gated

        # Factory that appends a real carried gear object to contents.
        def factory(idef, o):
            o.contents.append(self._GearObj(idef.key, idef.slot))

        system = EquipmentSystem(registry, event_bus, create_item_func=factory)
        building = FakeProductionBuilding("AR", owner=owner)

        # First tick produces one vest -> count hits the cap (1) -> stalls.
        system.process_production([building])
        self.assertEqual(len(owner.contents), 1)
        system.process_production([building])
        self.assertEqual(len(owner.contents), 1)  # stalled at cap

        # Equip the vest; the cap frees up and production resumes.
        owner.equipment.equip(owner.contents[0])
        system.process_production([building])
        self.assertEqual(len(owner.contents), 2)


class TestHasAssignedAgent(unittest.TestCase):
    """_has_assigned_agent tolerates the db and Attribute-handler shapes."""

    class _AttrHandler:
        def __init__(self, values):
            self._values = dict(values)

        def get(self, key, default=None):
            return self._values.get(key, default)

    def test_db_shape_agent_present(self):
        building = types.SimpleNamespace(db=DB(assigned_agent="engineer"))
        self.assertTrue(EquipmentSystem._has_assigned_agent(building))

    def test_db_shape_agent_absent(self):
        building = types.SimpleNamespace(db=DB(assigned_agent=None))
        self.assertFalse(EquipmentSystem._has_assigned_agent(building))

    def test_attributes_handler_fallback_present(self):
        # No db attribute at all -> falls through to the attributes handler.
        building = types.SimpleNamespace(
            attributes=self._AttrHandler({"assigned_agent": "engineer"})
        )
        self.assertTrue(EquipmentSystem._has_assigned_agent(building))

    def test_attributes_handler_fallback_cleared(self):
        # A cleared assignment (None) via the attributes handler reads as absent.
        building = types.SimpleNamespace(
            attributes=self._AttrHandler({"assigned_agent": None})
        )
        self.assertFalse(EquipmentSystem._has_assigned_agent(building))

    def test_no_db_no_attributes(self):
        self.assertFalse(
            EquipmentSystem._has_assigned_agent(types.SimpleNamespace())
        )


class TestCraft(unittest.TestCase):
    """Manual crafting at an equipment building (craft command backend)."""

    def _make(self, create_item_func=None):
        registry = _make_registry()
        registry.items["kevlar_vest"] = ItemDef(
            key="kevlar_vest", name="Kevlar Vest", slot="torso",
            category="armor", stat_modifiers={"damage_reduction": 5},
            craft_cost={"Iron": 20, "Stone": 10},
        )
        registry.item_production_map = {"AR": ["kevlar_vest", "rifle_rounds"],
                                        "MB": ["medkit"]}
        event_bus = EventBus()
        created = []
        sink = NotificationSink()
        event_bus.subscribe(PLAYER_NOTIFICATION, sink)
        factory = create_item_func or (
            lambda idef, owner: created.append(idef.key)
        )
        system = EquipmentSystem(registry, event_bus, create_item_func=factory)
        return system, created, sink

    def _player(self, **res):
        return FakePlayer(level=1, resources=res or {"Iron": 100, "Stone": 100})

    def test_craft_gear_deducts_and_creates(self):
        system, created, sink = self._make()
        player = self._player(Iron=100, Stone=100)
        ar = FakeProductionBuilding("AR", owner=player)
        self.assertTrue(system.craft(player, "kevlar_vest", ar))
        self.assertEqual(created, ["kevlar_vest"])
        self.assertEqual(player.get_resource("Iron"), 80)
        self.assertEqual(player.get_resource("Stone"), 90)
        self.assertEqual(sink.last()[0], "crafted")

    def test_craft_supply_adds_to_bag(self):
        system, _created, sink = self._make()
        player = self._player(Iron=100, Stone=100)
        ar = FakeProductionBuilding("AR", owner=player)
        self.assertTrue(system.craft(player, "rifle_rounds", ar))
        self.assertEqual(player.equipment.get_supply("rifle_rounds"), 1)

    def test_craft_notification_shows_no_value_for_unrolled_item(self):
        """A def without a roll_spec crafts with NO iqs/rarity in the
        success payload — the value readout only appears where it is
        meaningful (R2.5)."""
        system, _created, sink = self._make()
        player = self._player(Iron=100, Stone=100)
        ar = FakeProductionBuilding("AR", owner=player)
        self.assertTrue(system.craft(player, "kevlar_vest", ar))
        kind, data = sink.last()
        self.assertEqual(kind, "crafted")
        self.assertNotIn("iqs", data)
        self.assertNotIn("rarity", data)

    def test_craft_notification_shows_iqs_and_rarity(self):
        """Crafting rolled gear surfaces the stamped value in the success
        notification: the IQS quality score, plus the rarity when the
        crafting building's level draw assigned one (forced rare here)."""
        system, _created, sink = self._make(
            create_item_func=lambda idef, owner: {"key": idef.key})
        system.registry.items["rolled_rifle"] = _rolled_rifle_def()
        system.registry.item_production_map["AR"].append("rolled_rifle")
        system.registry.balance = BalanceConfig(
            equipment_production_ticks=1,
            craft_rarity_table={5: {"rare": 1}},
        )
        player = self._player(Iron=100, Stone=100)
        ar = FakeProductionBuilding("AR", owner=player)
        ar.db.building_level = 5

        self.assertTrue(system.craft(player, "rolled_rifle", ar))

        kind, data = sink.last()
        self.assertEqual(kind, "crafted")
        self.assertIsInstance(data.get("iqs"), int)
        self.assertEqual(data.get("rarity"), "rare")

    def test_craft_notification_iqs_without_rarity_below_table(self):
        """A rolled craft whose building level reaches no table row still
        shows its IQS — value without a rarity tag."""
        system, _created, sink = self._make(
            create_item_func=lambda idef, owner: {"key": idef.key})
        system.registry.items["rolled_rifle"] = _rolled_rifle_def()
        system.registry.item_production_map["AR"].append("rolled_rifle")
        system.registry.balance = BalanceConfig(
            equipment_production_ticks=1,
            craft_rarity_table={3: {"rare": 1}},
        )
        player = self._player(Iron=100, Stone=100)
        ar = FakeProductionBuilding("AR", owner=player)
        ar.db.building_level = 1

        self.assertTrue(system.craft(player, "rolled_rifle", ar))

        kind, data = sink.last()
        self.assertEqual(kind, "crafted")
        self.assertIsInstance(data.get("iqs"), int)
        self.assertNotIn("rarity", data)

    def test_craft_wrong_building(self):
        system, _c, sink = self._make()
        player = self._player(Iron=100, Stone=100)
        # medkit is made at MB, not AR.
        ar = FakeProductionBuilding("AR", owner=player)
        self.assertFalse(system.craft(player, "medkit", ar))
        kind, data = sink.last()
        self.assertEqual(kind, "craft_failed")
        self.assertEqual(data.get("reason"), "wrong_building")

    def test_craft_no_building(self):
        system, _c, sink = self._make()
        player = self._player(Iron=100, Stone=100)
        self.assertFalse(system.craft(player, "kevlar_vest", None))
        self.assertEqual(sink.last()[1].get("reason"), "wrong_building")

    def test_craft_not_owner(self):
        system, _c, sink = self._make()
        player = self._player(Iron=100, Stone=100)
        other = FakePlayer(level=1)
        ar = FakeProductionBuilding("AR", owner=other)
        self.assertFalse(system.craft(player, "kevlar_vest", ar))
        self.assertEqual(sink.last()[1].get("reason"), "not_owner")

    def test_craft_insufficient_resources(self):
        system, created, sink = self._make()
        player = self._player(Iron=5, Stone=5)  # kevlar needs 20/10
        ar = FakeProductionBuilding("AR", owner=player)
        self.assertFalse(system.craft(player, "kevlar_vest", ar))
        self.assertEqual(created, [])
        self.assertEqual(player.get_resource("Iron"), 5)  # not deducted
        kind, data = sink.last()
        self.assertEqual(kind, "craft_failed")
        self.assertEqual(data.get("reason"), "insufficient_resources")

    def test_craft_offline_building(self):
        system, _c, sink = self._make()
        player = self._player(Iron=100, Stone=100)
        ar = FakeProductionBuilding("AR", owner=player, offline=True)
        self.assertFalse(system.craft(player, "kevlar_vest", ar))
        self.assertEqual(sink.last()[1].get("reason"), "building_offline")

    def test_craft_unknown_item(self):
        system, _c, sink = self._make()
        player = self._player(Iron=100, Stone=100)
        ar = FakeProductionBuilding("AR", owner=player)
        self.assertFalse(system.craft(player, "nonexistent", ar))
        self.assertEqual(sink.last()[1].get("reason"), "unknown_item")

    def test_craft_supply_at_max_stack_refunds(self):
        """Crafting a supply into a full bag refunds and reports bag_full.

        Regression: without honoring add_supply's return, the resources are
        deducted, nothing is added, and a false 'crafted' fires.
        """
        system, _created, sink = self._make()
        player = self._player(Iron=100, Stone=100)
        # rifle_rounds max_stack is 200 (see ITEMS); craft_cost is Iron: 2.
        player.equipment.add_supply("rifle_rounds", 200, max_stack=200)
        ar = FakeProductionBuilding("AR", owner=player)

        self.assertFalse(system.craft(player, "rifle_rounds", ar))
        # Not deducted (refunded), bag unchanged, and told the bag is full.
        self.assertEqual(player.get_resource("Iron"), 100)
        self.assertEqual(player.equipment.get_supply("rifle_rounds"), 200)
        kind, data = sink.last()
        self.assertEqual(kind, "craft_failed")
        self.assertEqual(data.get("reason"), "bag_full")

    def test_craft_gear_factory_raise_is_contained_and_refunds(self):
        """If the gear factory raises, craft() refunds and doesn't propagate.

        Regression: an unguarded factory raise escaped past the refund block,
        leaving resources deducted with no item — and broke the 'never raises
        into the command layer' contract.
        """
        def boom(idef, owner):
            raise RuntimeError("create_object failed")

        system, _created, sink = self._make(create_item_func=boom)
        player = self._player(Iron=100, Stone=100)
        ar = FakeProductionBuilding("AR", owner=player)

        # Must not raise; kevlar_vest is gear (craft_cost Iron 20 / Stone 10).
        self.assertFalse(system.craft(player, "kevlar_vest", ar))
        self.assertEqual(player.get_resource("Iron"), 100)  # refunded
        self.assertEqual(player.get_resource("Stone"), 100)
        kind, data = sink.last()
        self.assertEqual(kind, "craft_failed")
        self.assertEqual(data.get("reason"), "craft_error")


# -------------------------------------------------------------- #
#  sell / junk — carried gear disposal (partial refund / destroy)
# -------------------------------------------------------------- #

class _SellableItem:
    """A carried gear item with an item_key (resolves to a known ItemDef).

    Exposes ``item_key`` directly (like a real GameItem's @property that reads
    from attributes) so ``_item_attr(item, "item_key")`` resolves it.
    """
    def __init__(self, key):
        self.key = key
        self.item_key = key  # _item_attr reads this via getattr(item, name)
        self.db = DB(item_key=key, count=None)
        self.deleted = False
        self.location = None

    def delete(self):
        self.deleted = True


class TestSellAndJunk(unittest.TestCase):
    """sell_item / junk_item — carried-gear-only disposal."""

    def _sys(self):
        registry = _make_registry()
        # Add a gear item with a known craft_cost for the sell refund test.
        registry.items["combat_knife"] = ItemDef(
            key="combat_knife", name="Combat Knife", slot="weapon",
            category="weapon", stat_modifiers={"damage": 8},
            craft_cost={"Iron": 5, "Stone": 3},
        )
        event_bus = EventBus()
        sink = NotificationSink()
        event_bus.subscribe(PLAYER_NOTIFICATION, sink)
        system = EquipmentSystem(registry, event_bus)
        return system, sink

    def _player(self, **resources):
        return FakePlayer(resources=resources)

    def test_sell_refunds_half_craft_cost_and_deletes(self):
        system, sink = self._sys()
        player = self._player(Iron=0, Stone=0)
        item = _SellableItem("combat_knife")

        ok = system.sell_item(player, item)

        self.assertTrue(ok)
        self.assertTrue(item.deleted)
        # 50% of {Iron:5, Stone:3} = Iron:2 + Stone:1 (floored).
        self.assertEqual(player.get_resource("Iron"), 2)
        self.assertEqual(player.get_resource("Stone"), 1)
        kind, data = sink.last()
        self.assertEqual(kind, "sold")
        self.assertIn("Iron", str(data.get("refund")))

    def test_junk_deletes_with_no_refund(self):
        system, sink = self._sys()
        player = self._player(Iron=10)
        item = _SellableItem("combat_knife")

        ok = system.junk_item(player, item)

        self.assertTrue(ok)
        self.assertTrue(item.deleted)
        self.assertEqual(player.get_resource("Iron"), 10)  # unchanged
        kind, _ = sink.last()
        self.assertEqual(kind, "junked")

    def test_sell_rejects_equipped_item(self):
        system, sink = self._sys()
        player = self._player()
        item = FakeItem("combat_knife", "weapon")
        player.equipment.equip(item)

        ok = system.sell_item(player, item)

        self.assertFalse(ok)
        kind, data = sink.last()
        self.assertEqual(kind, "sell_failed")
        self.assertEqual(data.get("reason"), "equipped")

    def test_sell_rejects_supply_stack(self):
        system, sink = self._sys()
        player = self._player()
        item = _SellableItem("combat_knife")
        item.db.count = 5  # has a count → is a supply drop, not gear

        ok = system.sell_item(player, item)

        self.assertFalse(ok)
        kind, data = sink.last()
        self.assertEqual(kind, "sell_failed")
        self.assertEqual(data.get("reason"), "not_gear")


# -------------------------------------------------------------- #
#  Loot-roller spawn wiring (item-loot-economy task 1.5)
# -------------------------------------------------------------- #

#: A roll_spec matching the design §1.1 example: loot bands + tighter,
#: contained craft bands. The craft bands are STRICT sub-bands so a
#: crafted-band assertion can never pass by accident of the loot band.
_ROLL_SPEC = {
    "stats": {
        "damage": {"min": 18, "max": 30, "weight": 3},
        "range": {"min": 4, "max": 7, "weight": 1},
    },
    "craft": {
        "damage": {"min": 20, "max": 25},
        "range": {"min": 4, "max": 5},
    },
}


def _rolled_rifle_def():
    return ItemDef(
        key="rolled_rifle", name="Rolled Rifle", slot="weapon",
        category="weapon", stat_modifiers={"damage": 25, "range": 5},
        craft_cost={"Iron": 5}, roll_spec=_ROLL_SPEC,
    )


class _DropStub:
    """A spawned-drop stand-in carrying a ``db`` bag like a GameItem."""

    def __init__(self):
        self.db = DB()


class TestSpawnPathRolling(unittest.TestCase):
    """Task 1.5: production drops and crafted items are rolled; unrolled
    defs stay fixed on every path (R1.1, R1.3, R1.4, R6.1)."""

    def _registry(self):
        registry = _make_registry()
        registry.items["rolled_rifle"] = _rolled_rifle_def()
        return registry

    def test_production_drop_is_rolled(self):
        """The passive/agent production-drop path stamps rolled_stats + iqs
        and (task 2.2) a lowest-bucket rarity — design §3.2: production
        drops pass weight 0, the guard_kill/safe-floor treatment."""
        system, _, _ = _make_system(self._registry())
        stub = _DropStub()
        system.set_gear_drop_spawner(lambda building, item_def: stub)
        building = FakeProductionBuilding("AR", owner=FakePlayer())

        ok = system._route_produced_item(
            system.registry.items["rolled_rifle"], FakePlayer(),
            building=building,
        )

        self.assertTrue(ok)
        rolled = stub.db.rolled_stats
        self.assertEqual(set(rolled), {"damage", "range"})
        self.assertTrue(18 <= rolled["damage"] <= 30)
        self.assertTrue(4 <= rolled["range"] <= 7)
        self.assertIsInstance(stub.db.iqs, int)
        self.assertTrue(0 <= stub.db.iqs <= 100)
        # Task 2.2: weight 0 → lowest bucket; only its rarities are possible.
        from world.systems.loot_roller import DEFAULT_RARITY_TABLE
        lowest = DEFAULT_RARITY_TABLE["guard_kill"]["weights"]
        self.assertIn(stub.db.rarity, set(lowest))
        # Affix draw is task 2.3 — never written here yet.
        self.assertIsNone(getattr(stub.db, "affixes", None))

    def test_crafted_item_rolls_in_craft_band(self):
        """The craft path (building=None) rolls crafted=True: every stat
        lands in the tighter craft band, never merely the loot band."""
        registry = self._registry()
        created = []

        def factory(item_def, owner):
            item = {"key": item_def.key}
            created.append(item)
            return item

        system = EquipmentSystem(registry, EventBus(),
                                 create_item_func=factory)

        ok = system._route_produced_item(
            registry.items["rolled_rifle"], FakePlayer())

        self.assertTrue(ok)
        item = created[0]
        rolled = item["rolled_stats"]
        self.assertTrue(20 <= rolled["damage"] <= 25)  # craft band (R6.1)
        self.assertTrue(4 <= rolled["range"] <= 5)
        self.assertIsInstance(item["iqs"], int)
        # Crafted-rarity change (deviation from R6.1): rarity now comes from
        # the crafting BUILDING's level. With no craft_building supplied
        # (level unknown), the draw is skipped — the original no-rarity
        # behavior stays for this path.
        self.assertNotIn("rarity", item)

    def _craft_via_route(self, craft_rarity_table, level):
        """Route one manual craft of the rolled rifle at a leveled building."""
        registry = self._registry()
        registry.balance = BalanceConfig(
            equipment_production_ticks=1,
            craft_rarity_table=craft_rarity_table,
        )
        created = []
        system = EquipmentSystem(
            registry, EventBus(),
            create_item_func=lambda idef, owner: created.append(
                {"key": idef.key}) or created[-1],
        )
        armory = FakeProductionBuilding("AR", owner=FakePlayer())
        armory.db.building_level = level
        ok = system._route_produced_item(
            registry.items["rolled_rifle"], FakePlayer(),
            craft_building=armory,
        )
        self.assertTrue(ok)
        return created[0]

    def test_craft_building_level_drives_crafted_rarity(self):
        """Crafted-rarity change (deviation from R6.1): the crafting
        building's level selects the craft_rarity_table row — a forced-rare
        L5 row stamps `rarity`, applies the 0.25 roll floor INSIDE the
        craft band, and still never draws affixes."""
        item = self._craft_via_route({5: {"rare": 1}}, level=5)
        self.assertEqual(item["rarity"], "rare")
        self.assertNotIn("affixes", item)
        rolled = item["rolled_stats"]
        # Rare floor guarantee inside the CRAFT band [20, 25] at skew 2:
        # rolled >= 20 + 5 * 0.25**2, and never above the craft max.
        self.assertGreaterEqual(rolled["damage"], 20 + 5 * 0.25 ** 2 - 1e-9)
        self.assertLessEqual(rolled["damage"], 25)

    def test_craft_rarity_hard_capped_at_rare(self):
        """Even a (mis-)authored epic/legendary weight in a craft row can
        never mint an epic craft — the roller filters tiers above rare."""
        item = self._craft_via_route(
            {5: {"legendary": 99, "epic": 99, "rare": 1}}, level=5)
        self.assertEqual(item["rarity"], "rare")

    def test_craft_below_lowest_table_level_keeps_no_rarity(self):
        """A building level below the lowest table row (defensive: odd
        data) keeps the original no-rarity crafted behavior."""
        item = self._craft_via_route({3: {"rare": 1}}, level=1)
        self.assertNotIn("rarity", item)

    def test_rolled_stats_read_back_through_get_stat(self):
        """The rolled value is what combat's read path sees: a GameItem-like
        stub prefers rolled_stats over the def base (R1.1)."""
        system, _, _ = _make_system(self._registry())
        stub = _DropStub()
        system.set_gear_drop_spawner(lambda building, item_def: stub)
        building = FakeProductionBuilding("AR", owner=FakePlayer())
        system._route_produced_item(
            system.registry.items["rolled_rifle"], FakePlayer(),
            building=building,
        )

        # Mirror GameItem.get_stat: rolled_stats wins over stat_modifiers.
        def get_stat(stat, default=0):
            rolled = getattr(stub.db, "rolled_stats", None) or {}
            if stat in rolled:
                return float(rolled[stat])
            return float(_ROLL_SPEC["stats"].get(stat, {}).get("min", default))

        self.assertEqual(get_stat("damage"), stub.db.rolled_stats["damage"])

    def test_unrolled_def_stays_fixed_on_production_drop(self):
        """No roll_spec → no rolled_stats/iqs written, drop still routes (R1.3)."""
        system, _, _ = _make_system(self._registry())
        stub = _DropStub()
        system.set_gear_drop_spawner(lambda building, item_def: stub)
        building = FakeProductionBuilding("AR", owner=FakePlayer())

        ok = system._route_produced_item(
            system.registry.items["kevlar_vest"], FakePlayer(),
            building=building,
        )

        self.assertTrue(ok)
        self.assertIsNone(getattr(stub.db, "rolled_stats", None))
        self.assertIsNone(getattr(stub.db, "iqs", None))

    def test_unrolled_def_stays_fixed_on_craft(self):
        registry = self._registry()
        created = []

        def factory(item_def, owner):
            item = {"key": item_def.key}
            created.append(item)
            return item

        system = EquipmentSystem(registry, EventBus(),
                                 create_item_func=factory)

        ok = system._route_produced_item(
            registry.items["kevlar_vest"], FakePlayer())

        self.assertTrue(ok)
        self.assertNotIn("rolled_stats", created[0])
        self.assertNotIn("iqs", created[0])


class TestPvPDropPreservesInstanceState(unittest.TestCase):
    """R1.6/R5.4: the PvP death drop carries the dropped instance's
    rolled_stats/affixes/rarity/iqs/inserts — never re-rolled."""

    _STATE = {
        "rolled_stats": {"damage": 27.5},
        "affixes": [{"key": "keen", "magnitude": 4}],
        "rarity": "Rare",
        "iqs": 73,
        "inserts": [{"key": "incendiary_core"}],
        # A damage-type insert writes db.damage_type on the instance
        # (task 4.3) — the conversion must carry with the drop (R5.4).
        "damage_type": "fire",
    }

    def _victim_with_rolled_rifle(self):
        victim = FakePlayer(level=10)
        victim.db.coord_planet = "earth"
        victim._buildings = []
        victim.get_buildings = lambda: list(victim._buildings)
        rifle = FakeItem("assault_rifle", "weapon", {"damage": 25})
        for name, value in self._STATE.items():
            setattr(rifle, name, value)
        victim.equipment.equip(rifle)
        return victim, rifle

    def _system_with_stub_spawner(self):
        system, _, _ = _make_system(_death_registry())
        spawned = []  # (item_def, stub)

        def _spawner(victim, item_def):
            stub = _DropStub()
            spawned.append((item_def, stub))
            return stub

        system.set_pvp_gear_drop_spawner(_spawner)
        return system, spawned

    def test_death_drop_carries_exact_instance_state(self):
        system, spawned = self._system_with_stub_spawner()
        victim, rifle = self._victim_with_rolled_rifle()
        killer = FakePlayer(level=10)
        system._rng = _DeterministicRNG(0.0)  # not recovered → drop roll wins

        summary = system.apply_death_loss(victim, killer)

        self.assertEqual(summary["dropped"].get("assault_rifle"), 1)
        self.assertEqual(len(spawned), 1)
        _, drop = spawned[0]
        for name, value in self._STATE.items():
            self.assertEqual(getattr(drop.db, name), value,
                             f"{name} not preserved across the death drop")
        # Deep-copied, not shared with the destroyed original (mutables).
        self.assertIsNot(drop.db.rolled_stats, rifle.rolled_stats)
        self.assertIsNot(drop.db.affixes, rifle.affixes)
        self.assertIsNot(drop.db.inserts, rifle.inserts)

    def test_unrolled_item_drops_unrolled(self):
        """An item with no roll state drops with none — never gains empty
        roll attributes (R12.1)."""
        system, spawned = self._system_with_stub_spawner()
        victim = FakePlayer(level=10)
        victim.db.coord_planet = "earth"
        victim._buildings = []
        victim.get_buildings = lambda: list(victim._buildings)
        victim.equipment.equip(FakeItem("kevlar_vest", "torso",
                                        {"damage_reduction": 5}))
        killer = FakePlayer(level=10)
        system._rng = _DeterministicRNG(0.0)

        system.apply_death_loss(victim, killer)

        self.assertEqual(len(spawned), 1)
        _, drop = spawned[0]
        for name in ("rolled_stats", "affixes", "rarity", "iqs", "inserts",
                     "damage_type"):
            self.assertIsNone(getattr(drop.db, name, None))


# ================================================================== #
#  Item-loot-economy task 4.3 — Blacksmith inserts (apply_insert)
# ================================================================== #

INSERT_DEFS = {
    "venom_coating": ItemDef(
        key="venom_coating", name="Venom Coating", slot="", category="insert",
        insert_effect={"type": "damage_type", "value": "poison"},
        weight=1.0, max_stack=10),
    "incendiary_core": ItemDef(
        key="incendiary_core", name="Incendiary Core", slot="",
        category="insert",
        insert_effect={"type": "damage_type", "value": "fire"},
        weight=1.0, max_stack=10),
    "extended_barrel": ItemDef(
        key="extended_barrel", name="Extended Barrel", slot="",
        category="insert",
        insert_effect={"type": "range", "value": 2},
        weight=2.0, max_stack=10),
    "hollowpoint": ItemDef(
        key="hollowpoint", name="Hollow-Point Kit", slot="", category="insert",
        insert_effect={"type": "stat", "stat": "damage", "value": 4,
                       "tradeoff": {"range": -1}},
        weight=0.5, max_stack=10),
    # A rank-gated insert for the rank-gate test (Captain = level 6).
    "elite_core": ItemDef(
        key="elite_core", name="Elite Core", slot="", category="insert",
        insert_effect={"type": "range", "value": 1},
        weight=1.0, max_stack=10, required_rank="Captain"),
}


class FakeBlacksmith:
    """Stand-in for a built Blacksmith bench (BS building instance)."""

    def __init__(self, owner=None, level=1, offline=False,
                 under_construction=False, building_type="BS"):
        self.key = "Blacksmith"
        self.db = DB(building_type=building_type, offline=offline,
                     under_construction=under_construction,
                     building_level=level)
        self._owner = owner

    @property
    def owner(self):
        return self._owner

    @property
    def is_offline(self):
        return bool(getattr(self.db, "offline", False))


class InsertableWeapon(FakeWeapon):
    """FakeWeapon with GameItem-style rolled-first reads.

    ``get_stat`` prefers ``db.rolled_stats`` and ``damage_type`` reads the
    instance override off ``db`` — mirroring the real ``GameItem``
    accessors (which are themselves covered by the typeclasses tests), so
    these tests can assert the exact reads combat performs
    (``CombatEngine._get_stat`` / ``_get_damage_type``).
    """

    def __init__(self, key="assault_rifle", stat_modifiers=None, **kwargs):
        super().__init__(key=key, weapon_type=kwargs.pop("weapon_type",
                                                         "ranged"), **kwargs)
        self.name = "Assault Rifle"
        self.stat_modifiers = dict(stat_modifiers
                                   if stat_modifiers is not None
                                   else {"damage": 25, "range": 5})

    def get_stat(self, stat_name, default=0):
        rolled = getattr(self.db, "rolled_stats", None) or {}
        if stat_name in rolled:
            return float(rolled[stat_name])
        return float(self.stat_modifiers.get(stat_name, default))

    @property
    def damage_type(self):
        return getattr(self.db, "damage_type", None)


class TestApplyInsert(unittest.TestCase):
    """Blacksmith inserts mutate the equipped weapon (task 4.3, R5).

    Covers the three insert effect types (mutation lands where combat
    reads), the slot limit ``1 + level//3``, the craft-mirroring gate
    order (wrong building / ownership / operational / rank / cost — no
    active-HQ gate), consumption of the insert supply on success, and the
    IQS re-stamp through the single writer.

    Validates: Requirements 4.2, 4.3, 5.1, 5.2, 5.3, 5.4
    """

    ROLL_SPEC = {"stats": {"damage": {"min": 18, "max": 30, "weight": 3},
                           "range": {"min": 4, "max": 7, "weight": 1}}}

    def _make(self):
        registry = _make_registry()
        registry.items.update(INSERT_DEFS)
        registry.items["assault_rifle"] = ItemDef(
            key="assault_rifle", name="Assault Rifle", slot="weapon",
            category="weapon", stat_modifiers={"damage": 25, "range": 5},
            roll_spec=self.ROLL_SPEC, weight=8.0)
        registry.buildings["BS"] = BuildingDef(
            name="Blacksmith", abbreviation="BS", cost={"Iron": 50},
            max_health=300, requires_hq=True, required_terrain=None,
            category="equipment", produces=None,
            capabilities=frozenset({"blacksmith"}),
        )
        event_bus = EventBus()
        sink = NotificationSink()
        event_bus.subscribe(PLAYER_NOTIFICATION, sink)
        system = EquipmentSystem(registry, event_bus)
        return system, registry, sink

    def _player_with_weapon(self, supplies=("extended_barrel",), level=10):
        player = FakePlayer(level=level)
        weapon = InsertableWeapon()
        player.equipment.equip(weapon)
        for key in supplies:
            player.equipment.add_supply(key, 1, max_stack=10)
        return player, weapon

    # ---------------- effect types mutate + combat reads ---------------- #

    def test_damage_type_insert_converts_weapon(self):
        from mygame.world.systems.combat_engine import CombatEngine
        system, _r, sink = self._make()
        player, weapon = self._player_with_weapon(supplies=("venom_coating",))
        bs = FakeBlacksmith(owner=player)

        self.assertTrue(system.apply_insert(player, "venom_coating", bs))
        # Mutated where combat's damage-type dispatch reads it.
        self.assertEqual(weapon.db.damage_type, "poison")
        self.assertEqual(CombatEngine._get_damage_type(weapon), "poison")
        self.assertEqual(sink.last()[0], "insert_applied")

    def test_fire_conversion_read_by_combat(self):
        from mygame.world.systems.combat_engine import CombatEngine
        system, _r, _s = self._make()
        player, weapon = self._player_with_weapon(
            supplies=("incendiary_core",))
        bs = FakeBlacksmith(owner=player)

        self.assertTrue(system.apply_insert(player, "incendiary_core", bs))
        self.assertEqual(CombatEngine._get_damage_type(weapon), "fire")

    def test_range_insert_extends_combat_range_read(self):
        from mygame.world.systems.combat_engine import CombatEngine
        system, _r, _s = self._make()
        player, weapon = self._player_with_weapon(
            supplies=("extended_barrel",))
        bs = FakeBlacksmith(owner=player)

        self.assertTrue(system.apply_insert(player, "extended_barrel", bs))
        # Base 5 seeded into rolled_stats, +2 — the exact read
        # _resolve_weapon_range performs (task 3.1) via _get_stat/get_stat.
        self.assertEqual(weapon.db.rolled_stats["range"], 7)
        self.assertEqual(CombatEngine._get_stat(weapon, "range", 1), 7.0)

    def test_stat_insert_bumps_damage_and_applies_tradeoff(self):
        from mygame.world.systems.combat_engine import CombatEngine
        system, _r, _s = self._make()
        player, weapon = self._player_with_weapon(supplies=("hollowpoint",))
        bs = FakeBlacksmith(owner=player)

        self.assertTrue(system.apply_insert(player, "hollowpoint", bs))
        self.assertEqual(weapon.db.rolled_stats["damage"], 29)  # 25 + 4
        self.assertEqual(weapon.db.rolled_stats["range"], 4)    # 5 - 1
        self.assertEqual(CombatEngine._get_stat(weapon, "damage", 0), 29.0)
        self.assertEqual(CombatEngine._get_stat(weapon, "range", 1), 4.0)

    def test_insert_on_rolled_weapon_adds_to_rolled_value(self):
        """A rolled weapon's per-instance value is the base, not the def."""
        system, _r, _s = self._make()
        player, weapon = self._player_with_weapon(
            supplies=("extended_barrel",))
        weapon.db.rolled_stats = {"damage": 27, "range": 6}
        bs = FakeBlacksmith(owner=player)

        self.assertTrue(system.apply_insert(player, "extended_barrel", bs))
        self.assertEqual(weapon.db.rolled_stats["range"], 8)   # 6 + 2
        self.assertEqual(weapon.db.rolled_stats["damage"], 27)  # untouched

    def test_insert_recorded_and_supply_consumed(self):
        system, _r, _s = self._make()
        player, weapon = self._player_with_weapon(
            supplies=("extended_barrel",))
        bs = FakeBlacksmith(owner=player)

        self.assertTrue(system.apply_insert(player, "extended_barrel", bs))
        inserts = weapon.db.inserts
        self.assertEqual(len(inserts), 1)
        self.assertEqual(inserts[0]["key"], "extended_barrel")
        self.assertEqual(inserts[0]["effect"], {"type": "range", "value": 2})
        # The consumable was the cost — gone from the Supply_Bag.
        self.assertEqual(player.equipment.get_supply("extended_barrel"), 0)

    def test_iqs_restamped_after_insert(self):
        """recompute_iqs (the single writer) re-stamps after the mutation."""
        system, registry, _s = self._make()
        player, weapon = self._player_with_weapon(
            supplies=("extended_barrel",))
        weapon.item_def = registry.items["assault_rifle"]  # supplies roll_spec
        weapon.db.rolled_stats = {"damage": 24, "range": 5}
        weapon.db.iqs = 1  # stale stamp
        bs = FakeBlacksmith(owner=player)

        self.assertTrue(system.apply_insert(player, "extended_barrel", bs))
        # range 5 → 7 (band max): u = ((24-18)/12*3 + 1.0*1) / 4 = 0.625.
        self.assertEqual(weapon.db.iqs, 62)

    # ---------------- slot limit = 1 + level // 3 ---------------- #

    def test_slot_limit_level1_refuses_second_insert(self):
        system, _r, sink = self._make()
        player, weapon = self._player_with_weapon(
            supplies=("extended_barrel", "venom_coating"))
        bs = FakeBlacksmith(owner=player, level=1)  # 1 + 1//3 = 1 slot

        self.assertTrue(system.apply_insert(player, "extended_barrel", bs))
        self.assertFalse(system.apply_insert(player, "venom_coating", bs))
        kind, data = sink.last()
        self.assertEqual(kind, "insert_failed")
        self.assertEqual(data.get("reason"), "no_slots")
        self.assertEqual(data.get("slot_limit"), 1)
        # Refused = weapon unchanged AND the insert NOT consumed (R5.3).
        self.assertIsNone(getattr(weapon.db, "damage_type", None))
        self.assertEqual(len(weapon.db.inserts), 1)
        self.assertEqual(player.equipment.get_supply("venom_coating"), 1)

    def test_slot_limit_level3_allows_two_then_refuses(self):
        system, _r, sink = self._make()
        player, _w = self._player_with_weapon(
            supplies=("extended_barrel", "venom_coating", "hollowpoint"))
        bs = FakeBlacksmith(owner=player, level=3)  # 1 + 3//3 = 2 slots

        self.assertTrue(system.apply_insert(player, "extended_barrel", bs))
        self.assertTrue(system.apply_insert(player, "venom_coating", bs))
        self.assertFalse(system.apply_insert(player, "hollowpoint", bs))
        self.assertEqual(sink.last()[1].get("reason"), "no_slots")

    # ---------------- gate order mirrors craft ---------------- #

    def test_unknown_item(self):
        system, _r, sink = self._make()
        player, _w = self._player_with_weapon()
        bs = FakeBlacksmith(owner=player)
        self.assertFalse(system.apply_insert(player, "nonexistent", bs))
        self.assertEqual(sink.last()[1].get("reason"), "unknown_item")

    def test_not_an_insert(self):
        system, _r, sink = self._make()
        player, _w = self._player_with_weapon()
        bs = FakeBlacksmith(owner=player)
        self.assertFalse(system.apply_insert(player, "medkit", bs))
        self.assertEqual(sink.last()[1].get("reason"), "not_an_insert")

    def test_wrong_building_none(self):
        system, _r, sink = self._make()
        player, _w = self._player_with_weapon()
        self.assertFalse(system.apply_insert(player, "extended_barrel", None))
        self.assertEqual(sink.last()[1].get("reason"), "wrong_building")

    def test_wrong_building_non_blacksmith(self):
        """An equipment building without the capability is not a bench."""
        system, _r, sink = self._make()
        player, _w = self._player_with_weapon()
        ar = FakeProductionBuilding("AR", owner=player)
        self.assertFalse(system.apply_insert(player, "extended_barrel", ar))
        self.assertEqual(sink.last()[1].get("reason"), "wrong_building")

    def test_not_owner(self):
        system, _r, sink = self._make()
        player, _w = self._player_with_weapon()
        other = FakePlayer(level=10)
        bs = FakeBlacksmith(owner=other)
        self.assertFalse(system.apply_insert(player, "extended_barrel", bs))
        self.assertEqual(sink.last()[1].get("reason"), "not_owner")

    def test_offline_bench(self):
        system, _r, sink = self._make()
        player, _w = self._player_with_weapon()
        bs = FakeBlacksmith(owner=player, offline=True)
        self.assertFalse(system.apply_insert(player, "extended_barrel", bs))
        self.assertEqual(sink.last()[1].get("reason"), "building_offline")

    def test_mid_upgrade_bench(self):
        system, _r, sink = self._make()
        player, _w = self._player_with_weapon()
        bs = FakeBlacksmith(owner=player, under_construction=True)
        self.assertFalse(system.apply_insert(player, "extended_barrel", bs))
        self.assertEqual(sink.last()[1].get("reason"), "building_upgrading")

    def test_rank_gate(self):
        """A rank-gated insert is refused below rank (emits equip_denied)."""
        system, _r, sink = self._make()
        player, _w = self._player_with_weapon(supplies=("elite_core",),
                                              level=1)  # below Captain
        bs = FakeBlacksmith(owner=player)
        self.assertFalse(system.apply_insert(player, "elite_core", bs))
        self.assertEqual(sink.last()[0], "equip_denied")

    def test_no_weapon_equipped(self):
        system, _r, sink = self._make()
        player = FakePlayer(level=10)
        player.equipment.add_supply("extended_barrel", 1, max_stack=10)
        bs = FakeBlacksmith(owner=player)
        self.assertFalse(system.apply_insert(player, "extended_barrel", bs))
        self.assertEqual(sink.last()[1].get("reason"), "no_weapon")

    def test_weapon_token_mismatch(self):
        system, _r, sink = self._make()
        player, _w = self._player_with_weapon()
        bs = FakeBlacksmith(owner=player)
        self.assertFalse(system.apply_insert(player, "extended_barrel", bs,
                                             "plasma sword"))
        self.assertEqual(sink.last()[1].get("reason"), "weapon_not_equipped")

    def test_weapon_token_match_tolerates_case_and_underscores(self):
        system, _r, _s = self._make()
        player, weapon = self._player_with_weapon()
        bs = FakeBlacksmith(owner=player)
        self.assertTrue(system.apply_insert(player, "extended_barrel", bs,
                                            "Assault_Rifle"))
        self.assertEqual(weapon.db.rolled_stats["range"], 7)

    def test_insufficient_supply(self):
        """The cost gate: the insert must be carried in the Supply_Bag."""
        system, _r, sink = self._make()
        player, weapon = self._player_with_weapon(supplies=())
        bs = FakeBlacksmith(owner=player)
        self.assertFalse(system.apply_insert(player, "extended_barrel", bs))
        self.assertEqual(sink.last()[1].get("reason"), "insufficient_supply")
        self.assertIsNone(getattr(weapon.db, "inserts", None))  # unchanged

    # ---------------- persistence on the PvP death drop (R5.4) -------- #

    def test_applied_inserts_persist_on_death_drop(self):
        """A modified weapon dropped on death carries its inserts AND their
        effects (rolled_stats mutation + damage_type conversion)."""
        system, _r, _s = self._make()
        player, weapon = self._player_with_weapon(
            supplies=("extended_barrel", "incendiary_core"))
        bs = FakeBlacksmith(owner=player, level=3)  # 2 slots
        self.assertTrue(system.apply_insert(player, "extended_barrel", bs))
        self.assertTrue(system.apply_insert(player, "incendiary_core", bs))

        drop = _DropStub()
        EquipmentSystem._preserve_instance_state(weapon, drop)

        self.assertEqual(drop.db.rolled_stats["range"], 7)
        self.assertEqual(drop.db.damage_type, "fire")
        self.assertEqual([i["key"] for i in drop.db.inserts],
                         ["extended_barrel", "incendiary_core"])


# ================================================================== #
#  Item-loot-economy task 4.4 — Blacksmith reroll (reroll)
# ================================================================== #

class _FixedRNG:
    """Scripted RNG: every ``random()`` returns the same fixed U.

    With ``u=0.0`` a reroll lands on the exact floor of every band:
    ``rolled = lo + (hi - lo) * floor**skew`` — making the level/rarity
    floor math exactly assertable.
    """

    def __init__(self, u=0.0):
        self._u = float(u)

    def random(self):
        return self._u


class TestReroll(unittest.TestCase):
    """Blacksmith reroll re-rolls BASE stats only (task 4.4, R4.4/R4.5).

    Covers: fresh in-band base rolls (affixes/rarity/inserts untouched,
    insert deltas re-applied); the level floor ``0.1 * (level - 1)`` rising
    with Blacksmith level; the rarity floor still applying if higher; the
    Salvage + resource charge (checked-then-deducted, refused when short);
    the IQS re-stamp through the single writer; and the craft-mirroring
    gate order (unknown/not-rerollable/wrong building/ownership/
    operational — no active-HQ gate).

    Validates: Requirements 4.2, 4.3, 4.4, 4.5, 2.4
    """

    ROLL_SPEC = {"stats": {"damage": {"min": 18, "max": 30, "weight": 3},
                           "range": {"min": 4, "max": 7, "weight": 1}}}

    def _make(self):
        registry = _make_registry()
        registry.items.update(INSERT_DEFS)
        registry.items["assault_rifle"] = ItemDef(
            key="assault_rifle", name="Assault Rifle", slot="weapon",
            category="weapon", stat_modifiers={"damage": 25, "range": 5},
            roll_spec=self.ROLL_SPEC, weight=8.0)
        # A fixed (unrolled) gear def — never rerollable (R1.3).
        registry.items["iron_helm"] = ItemDef(
            key="iron_helm", name="Iron Helm", slot="head",
            category="armor", stat_modifiers={"damage_reduction": 2},
            weight=2.0)
        registry.buildings["BS"] = BuildingDef(
            name="Blacksmith", abbreviation="BS", cost={"Iron": 50},
            max_health=300, requires_hq=True, required_terrain=None,
            category="equipment", produces=None,
            capabilities=frozenset({"blacksmith"}),
        )
        event_bus = EventBus()
        sink = NotificationSink()
        event_bus.subscribe(PLAYER_NOTIFICATION, sink)
        system = EquipmentSystem(registry, event_bus)
        return system, registry, sink

    def _player_with_weapon(self, salvage=100, iron=50, level=10):
        player = FakePlayer(level=level, resources={"Iron": iron})
        player.add_salvage(salvage)
        weapon = InsertableWeapon()
        player.equipment.equip(weapon)
        return player, weapon

    # ------------------- the reroll itself ------------------- #

    def test_rerolls_base_stats_within_band(self):
        import random
        system, _r, sink = self._make()
        system._rng = random.Random(42)
        player, weapon = self._player_with_weapon()
        weapon.db.rolled_stats = {"damage": 30, "range": 7}  # god-roll
        bs = FakeBlacksmith(owner=player)

        self.assertTrue(system.reroll(player, "assault_rifle", bs))
        rolled = weapon.db.rolled_stats
        self.assertNotEqual(rolled, {"damage": 30, "range": 7})
        self.assertTrue(18 <= rolled["damage"] <= 30)
        self.assertTrue(4 <= rolled["range"] <= 7)
        self.assertEqual(sink.last()[0], "rerolled")

    def test_affixes_and_rarity_untouched(self):
        import random
        system, _r, _s = self._make()
        system._rng = random.Random(7)
        player, weapon = self._player_with_weapon()
        affixes = [{"key": "keen", "name": "of Power", "stat": "damage_bonus",
                    "magnitude": 4, "value": 6.0}]
        weapon.db.rarity = "uncommon"
        weapon.db.affixes = list(affixes)
        bs = FakeBlacksmith(owner=player)

        self.assertTrue(system.reroll(player, "assault_rifle", bs))
        self.assertEqual(weapon.db.rarity, "uncommon")
        self.assertEqual(weapon.db.affixes, affixes)

    def test_insert_deltas_reapplied_after_reroll(self):
        # An irreversible insert's value is never erased by a reroll: the
        # fresh base roll gets the recorded insert deltas re-applied on top,
        # and the inserts record itself is unchanged.
        system, _r, _s = self._make()
        player, weapon = self._player_with_weapon()
        player.equipment.add_supply("extended_barrel", 1, max_stack=10)
        bs = FakeBlacksmith(owner=player)
        self.assertTrue(system.apply_insert(player, "extended_barrel", bs))

        system._rng = _FixedRNG(0.0)  # floor rolls: damage 18, range 4
        self.assertTrue(system.reroll(player, "assault_rifle", bs))
        self.assertEqual(weapon.db.rolled_stats["range"], 6)   # 4 + 2 insert
        self.assertAlmostEqual(weapon.db.rolled_stats["damage"], 18.0)
        self.assertEqual([i["key"] for i in weapon.db.inserts],
                         ["extended_barrel"])

    # ------------------- floors ------------------- #

    def test_floor_rises_with_blacksmith_level(self):
        # Worst-case roll (U = 0) at L1 vs L5: level floor 0.1*(level-1)
        # → L1 lands on the band min, L5 lands 0.4**2 = 16% up the band.
        system, _r, _s = self._make()
        system._rng = _FixedRNG(0.0)
        player, weapon = self._player_with_weapon()
        self.assertTrue(system.reroll(player, "assault_rifle",
                                      FakeBlacksmith(owner=player, level=1)))
        l1_damage = weapon.db.rolled_stats["damage"]

        player2, weapon2 = self._player_with_weapon()
        self.assertTrue(system.reroll(player2, "assault_rifle",
                                      FakeBlacksmith(owner=player2, level=5)))
        l5_damage = weapon2.db.rolled_stats["damage"]

        self.assertAlmostEqual(l1_damage, 18.0)                # band min
        self.assertAlmostEqual(l5_damage, 18 + 12 * 0.4 ** 2)  # 19.92
        self.assertGreater(l5_damage, l1_damage)

    def test_rarity_floor_still_applies_when_higher(self):
        # An Epic item (rarity floor 0.50) at a L1 bench (level floor 0.0)
        # keeps its rarity guarantee: min roll = lo + (hi-lo) * 0.5**2.
        system, _r, _s = self._make()
        system._rng = _FixedRNG(0.0)
        player, weapon = self._player_with_weapon()
        weapon.db.rarity = "epic"
        bs = FakeBlacksmith(owner=player, level=1)

        self.assertTrue(system.reroll(player, "assault_rifle", bs))
        self.assertAlmostEqual(weapon.db.rolled_stats["damage"],
                               18 + 12 * 0.5 ** 2)  # 21.0

    # ------------------- cost ------------------- #

    def test_charges_salvage_and_resources(self):
        system, _r, _s = self._make()
        player, _w = self._player_with_weapon(salvage=100, iron=50)
        bs = FakeBlacksmith(owner=player)

        self.assertTrue(system.reroll(player, "assault_rifle", bs))
        self.assertEqual(player.get_salvage(), 60)        # -40 (balance)
        self.assertEqual(player.get_resource("Iron"), 40)  # -10 (balance)

    def test_insufficient_salvage_refused(self):
        system, _r, sink = self._make()
        player, weapon = self._player_with_weapon(salvage=39, iron=50)
        weapon.db.rolled_stats = {"damage": 30, "range": 7}
        bs = FakeBlacksmith(owner=player)

        self.assertFalse(system.reroll(player, "assault_rifle", bs))
        self.assertEqual(sink.last()[1].get("reason"), "insufficient_salvage")
        self.assertEqual(player.get_salvage(), 39)          # unchanged
        self.assertEqual(player.get_resource("Iron"), 50)   # unchanged
        self.assertEqual(weapon.db.rolled_stats,
                         {"damage": 30, "range": 7})        # unchanged

    def test_insufficient_resources_refused(self):
        system, _r, sink = self._make()
        player, weapon = self._player_with_weapon(salvage=100, iron=0)
        bs = FakeBlacksmith(owner=player)

        self.assertFalse(system.reroll(player, "assault_rifle", bs))
        self.assertEqual(sink.last()[1].get("reason"),
                         "insufficient_resources")
        self.assertEqual(player.get_salvage(), 100)  # checked before deduct
        self.assertIsNone(getattr(weapon.db, "rolled_stats", None))

    # ------------------- IQS re-stamp ------------------- #

    def test_iqs_restamped_through_single_writer(self):
        # A floor roll at L1 lands every stat on its band min → base IQS 0;
        # the stale god-roll stamp must be overwritten (R2.4).
        system, _r, _s = self._make()
        system._rng = _FixedRNG(0.0)
        player, weapon = self._player_with_weapon()
        weapon.db.rolled_stats = {"damage": 30, "range": 7}
        weapon.db.iqs = 100
        bs = FakeBlacksmith(owner=player)

        self.assertTrue(system.reroll(player, "assault_rifle", bs))
        self.assertEqual(weapon.db.iqs, 0)

    # ------------------- targeting ------------------- #

    def test_carried_item_rerollable(self):
        # R4.2: "a held/equipped rolled item" — a loose carried GameItem
        # (player.contents) is a valid target too.
        import random
        system, _r, _s = self._make()
        system._rng = random.Random(11)
        player = FakePlayer(level=10, resources={"Iron": 50})
        player.add_salvage(100)
        carried = InsertableWeapon()
        player.contents = [carried]
        bs = FakeBlacksmith(owner=player)

        self.assertTrue(system.reroll(player, "assault_rifle", bs))
        rolled = carried.db.rolled_stats
        self.assertTrue(18 <= rolled["damage"] <= 30)

    # ------------------- gates ------------------- #

    def test_unknown_item_refused(self):
        system, _r, sink = self._make()
        player, _w = self._player_with_weapon()
        bs = FakeBlacksmith(owner=player)
        self.assertFalse(system.reroll(player, "plasma_sword", bs))
        self.assertEqual(sink.last()[1].get("reason"), "unknown_item")

    def test_unrolled_item_not_rerollable(self):
        system, _r, sink = self._make()
        player, _w = self._player_with_weapon()
        player.equipment.equip(
            FakeItem("iron_helm", "head", {"damage_reduction": 2}))
        bs = FakeBlacksmith(owner=player)
        self.assertFalse(system.reroll(player, "iron_helm", bs))
        self.assertEqual(sink.last()[1].get("reason"), "not_rerollable")

    def test_requires_blacksmith(self):
        system, _r, sink = self._make()
        player, _w = self._player_with_weapon()
        self.assertFalse(system.reroll(player, "assault_rifle", None))
        self.assertEqual(sink.last()[1].get("reason"), "wrong_building")
        ar = FakeProductionBuilding("AR", owner=player)
        self.assertFalse(system.reroll(player, "assault_rifle", ar))
        self.assertEqual(sink.last()[1].get("reason"), "wrong_building")

    def test_not_owner_refused(self):
        system, _r, sink = self._make()
        player, _w = self._player_with_weapon()
        bs = FakeBlacksmith(owner=FakePlayer(level=10))
        self.assertFalse(system.reroll(player, "assault_rifle", bs))
        self.assertEqual(sink.last()[1].get("reason"), "not_owner")

    def test_operational_gates(self):
        system, _r, sink = self._make()
        player, _w = self._player_with_weapon()
        offline = FakeBlacksmith(owner=player, offline=True)
        self.assertFalse(system.reroll(player, "assault_rifle", offline))
        self.assertEqual(sink.last()[1].get("reason"), "building_offline")
        upgrading = FakeBlacksmith(owner=player, under_construction=True)
        self.assertFalse(system.reroll(player, "assault_rifle", upgrading))
        self.assertEqual(sink.last()[1].get("reason"), "building_upgrading")


# ================================================================== #
#  Item-loot-economy task 5.4 — Salvage Protocols cost consumer
# ================================================================== #

class TestSalvageProtocolsCostConsumer(unittest.TestCase):
    """The reroll charge × the clamped ``salvage_cost_mult`` tech (task 5.4).

    The consumer reads ``get_tech_bonus(player, "salvage_cost_mult", 1.0)``
    and clamps it to ``[SALVAGE_COST_MULT_FLOOR, 1.0]`` before applying it
    to BOTH reroll cost components (``reroll_salvage_cost`` 40 +
    ``reroll_resource_cost`` {Iron: 10} at the balance defaults). No
    research → exactly the balance numbers; research can never raise the
    charge (upper clamp) nor trivialize the Salvage sink (floor). R11.7:
    the ``salvage_cost_mult`` key shipped by Salvage Protocols has a live
    consumer.

    Validates: Requirements 11.2, 11.7
    """

    ROLL_SPEC = TestReroll.ROLL_SPEC

    def _make(self):
        registry = _make_registry()
        registry.items["assault_rifle"] = ItemDef(
            key="assault_rifle", name="Assault Rifle", slot="weapon",
            category="weapon", stat_modifiers={"damage": 25, "range": 5},
            roll_spec=self.ROLL_SPEC, weight=8.0)
        registry.buildings["BS"] = BuildingDef(
            name="Blacksmith", abbreviation="BS", cost={"Iron": 50},
            max_health=300, requires_hq=True, required_terrain=None,
            category="equipment", produces=None,
            capabilities=frozenset({"blacksmith"}),
        )
        event_bus = EventBus()
        sink = NotificationSink()
        event_bus.subscribe(PLAYER_NOTIFICATION, sink)
        return EquipmentSystem(registry, event_bus), sink

    def _player_with_weapon(self, salvage=100, iron=50, mult=None):
        player = FakePlayer(level=10, resources={"Iron": iron})
        player.add_salvage(salvage)
        if mult is not None:
            player.db.tech_bonuses = {"salvage_cost_mult": mult}
        weapon = InsertableWeapon()
        player.equipment.equip(weapon)
        return player

    def test_no_research_costs_unchanged(self):
        # default=1.0 (NOT 0.0 — the "free gear" landmine): an unresearched
        # player pays exactly the balance numbers (40 Salvage + 10 Iron).
        system, _s = self._make()
        player = self._player_with_weapon()
        self.assertTrue(system.reroll(player, "assault_rifle",
                                      FakeBlacksmith(owner=player)))
        self.assertEqual(player.get_salvage(), 60)         # -40
        self.assertEqual(player.get_resource("Iron"), 40)  # -10

    def test_research_reduces_reroll_cost(self):
        # Salvage Protocols (0.75) discounts BOTH components:
        # Salvage 40 → 30, Iron round(10 × 0.75) = 8.
        system, _s = self._make()
        player = self._player_with_weapon(mult=0.75)
        self.assertTrue(system.reroll(player, "assault_rifle",
                                      FakeBlacksmith(owner=player)))
        self.assertEqual(player.get_salvage(), 70)         # -30
        self.assertEqual(player.get_resource("Iron"), 42)  # -8

    def test_floor_clamps_stacked_reduction(self):
        # A hypothetical 0.1 accumulation clamps at the 0.5 floor — the
        # Salvage sink can't be trivialized: 40 → 20, 10 → 5.
        system, _s = self._make()
        player = self._player_with_weapon(mult=0.1)
        self.assertTrue(system.reroll(player, "assault_rifle",
                                      FakeBlacksmith(owner=player)))
        self.assertEqual(player.get_salvage(), 80)         # -20
        self.assertEqual(player.get_resource("Iron"), 45)  # -5

    def test_upper_clamp_never_raises_cost(self):
        # Two stacked 0.85-style techs would SUM to 1.7 (the additive
        # accumulator) — the upper clamp holds the charge at ×1.0.
        system, _s = self._make()
        player = self._player_with_weapon(mult=1.7)
        self.assertTrue(system.reroll(player, "assault_rifle",
                                      FakeBlacksmith(owner=player)))
        self.assertEqual(player.get_salvage(), 60)         # -40, not -68
        self.assertEqual(player.get_resource("Iron"), 40)  # -10

    def test_insufficiency_gate_uses_discounted_cost(self):
        # 30 Salvage is short of the base 40 but covers the researched 30 —
        # the gate checks the DISCOUNTED charge (the consumer is live, not
        # display-only).
        system, sink = self._make()
        player = self._player_with_weapon(salvage=30, mult=0.75)
        self.assertTrue(system.reroll(player, "assault_rifle",
                                      FakeBlacksmith(owner=player)))
        self.assertEqual(player.get_salvage(), 0)

        # …and an unresearched twin at 30 Salvage is refused.
        player2 = self._player_with_weapon(salvage=30)
        self.assertFalse(system.reroll(player2, "assault_rifle",
                                       FakeBlacksmith(owner=player2)))
        self.assertEqual(sink.last()[1].get("reason"), "insufficient_salvage")


# ================================================================== #
#  Item-loot-economy task 5.2 — Blacksmith salvage (salvage)
# ================================================================== #

class _SalvageItem:
    """A loose carried GameItem stand-in with per-instance iqs + delete."""

    def __init__(self, key="assault_rifle", name="Assault Rifle", iqs=None):
        self.key = key
        self.name = name
        self.db = DB()
        if iqs is not None:
            self.db.iqs = iqs
        self.deleted = False

    def delete(self):
        self.deleted = True


class TestSalvage(unittest.TestCase):
    """Blacksmith salvage destroys a carried item for Salvage (task 5.2, R7).

    Covers: the design §5 yield formula
    ``round((base_salvage + iqs*salvage_per_iqs)
    * (1 + salvage_level_bonus*(level-1)))`` scaling with IQS (R7.1) and
    monotonic non-decreasing in Blacksmith level (R7.2, L1 1.0× → L5 1.5×);
    the credit landing on ``db.salvage`` (R7.3); item destruction +
    possession/ownership (R7.4, equipped gear refused — unequip first);
    the unrolled-item floor (iqs 0 → base_salvage); and the reroll-mirroring
    gate order (unknown/wrong building/ownership/operational).

    Validates: Requirements 7.1, 7.2, 7.3, 7.4
    """

    def _make(self):
        registry = _make_registry()
        registry.items["assault_rifle"] = ItemDef(
            key="assault_rifle", name="Assault Rifle", slot="weapon",
            category="weapon", stat_modifiers={"damage": 25, "range": 5},
            weight=8.0)
        # A fixed (unrolled) gear def — still salvageable at the floor.
        registry.items["iron_helm"] = ItemDef(
            key="iron_helm", name="Iron Helm", slot="head",
            category="armor", stat_modifiers={"damage_reduction": 2},
            weight=2.0)
        registry.buildings["BS"] = BuildingDef(
            name="Blacksmith", abbreviation="BS", cost={"Iron": 50},
            max_health=300, requires_hq=True, required_terrain=None,
            category="equipment", produces=None,
            capabilities=frozenset({"blacksmith"}),
        )
        event_bus = EventBus()
        sink = NotificationSink()
        event_bus.subscribe(PLAYER_NOTIFICATION, sink)
        system = EquipmentSystem(registry, event_bus)
        return system, registry, sink

    def _player_with_carried(self, iqs=70):
        player = FakePlayer(level=10)
        item = _SalvageItem(iqs=iqs)
        player.contents = [item]
        return player, item

    # ------------------- the yield formula ------------------- #

    def test_yield_credits_db_salvage(self):
        # iqs 70 at L1 (design §9 anchor): round((5 + 70*0.5) * 1.0) = 40.
        system, _r, sink = self._make()
        player, item = self._player_with_carried(iqs=70)
        bs = FakeBlacksmith(owner=player, level=1)

        self.assertTrue(system.salvage(player, "assault_rifle", bs))
        self.assertEqual(player.db.salvage, 40)
        kind, data = sink.last()
        self.assertEqual(kind, "salvaged")
        self.assertEqual(data.get("salvage"), 40)

    def test_yield_scales_with_iqs(self):
        # R7.1: a higher-IQS item salvages for more at the same bench.
        system, _r, _s = self._make()
        yields = []
        for iqs in (0, 20, 70, 100):
            player, _item = self._player_with_carried(iqs=iqs)
            bs = FakeBlacksmith(owner=player, level=1)
            self.assertTrue(system.salvage(player, "assault_rifle", bs))
            yields.append(player.get_salvage())
        # round((5 + iqs*0.5) * 1.0) at iqs 0/20/70/100.
        self.assertEqual(yields, [5, 15, 40, 55])

    def test_yield_monotonic_non_decreasing_in_level(self):
        # R7.2: L1 ≤ L2 ≤ ... ≤ L5 for the same item, exact formula check:
        # round(40 * (1 + 0.125*(level-1))) → 40, 45, 50, 55, 60.
        system, _r, _s = self._make()
        yields = []
        for level in (1, 2, 3, 4, 5):
            player, _item = self._player_with_carried(iqs=70)
            bs = FakeBlacksmith(owner=player, level=level)
            self.assertTrue(system.salvage(player, "assault_rifle", bs))
            yields.append(player.get_salvage())
        self.assertEqual(yields, [40, 45, 50, 55, 60])
        self.assertEqual(yields, sorted(yields))

    def test_unrolled_item_salvages_at_floor(self):
        # Decided (task 5.2): no iqs reads as 0 — the base_salvage floor
        # keeps junk/legacy gear salvageable (R7 "a use for the loot I
        # don't want").
        system, _r, _s = self._make()
        player = FakePlayer(level=10)
        item = _SalvageItem(key="iron_helm", name="Iron Helm", iqs=None)
        player.contents = [item]
        bs = FakeBlacksmith(owner=player, level=1)

        self.assertTrue(system.salvage(player, "iron_helm", bs))
        self.assertEqual(player.get_salvage(), 5)
        self.assertTrue(item.deleted)

    # ------------------- destruction + possession ------------------- #

    def test_destroys_the_item(self):
        system, _r, _s = self._make()
        player, item = self._player_with_carried(iqs=70)
        bs = FakeBlacksmith(owner=player)

        self.assertTrue(system.salvage(player, "assault_rifle", bs))
        self.assertTrue(item.deleted)

    def test_equipped_item_refused(self):
        # R7.4 possession discipline: salvage never silently strips gear —
        # an equipped match is refused with its own reason (mirrors sell).
        system, _r, sink = self._make()
        player = FakePlayer(level=10)
        player.equipment.equip(InsertableWeapon())
        bs = FakeBlacksmith(owner=player)

        self.assertFalse(system.salvage(player, "assault_rifle", bs))
        self.assertEqual(sink.last()[1].get("reason"), "equipped")

    def test_carried_copy_salvageable_while_another_is_equipped(self):
        # Carried objects are searched FIRST: a spare copy of an equipped
        # item salvages the spare, not the loadout.
        system, _r, _s = self._make()
        player = FakePlayer(level=10)
        equipped = InsertableWeapon()
        player.equipment.equip(equipped)
        spare = _SalvageItem(iqs=10)
        player.contents = [spare]
        bs = FakeBlacksmith(owner=player)

        self.assertTrue(system.salvage(player, "assault_rifle", bs))
        self.assertTrue(spare.deleted)
        self.assertIn(equipped,
                      player.equipment.get_all_equipped().values())

    def test_counted_stack_not_gear(self):
        system, _r, sink = self._make()
        player = FakePlayer(level=10)
        stack = _SalvageItem(key="medkit", name="Medkit")
        stack.db.count = 5  # a counted supply drop, not loose gear
        player.contents = [stack]
        bs = FakeBlacksmith(owner=player)

        self.assertFalse(system.salvage(player, "medkit", bs))
        self.assertEqual(sink.last()[1].get("reason"), "not_gear")
        self.assertFalse(stack.deleted)

    # ------------------- gates ------------------- #

    def test_unknown_item_refused(self):
        system, _r, sink = self._make()
        player, _item = self._player_with_carried()
        bs = FakeBlacksmith(owner=player)
        self.assertFalse(system.salvage(player, "plasma_sword", bs))
        self.assertEqual(sink.last()[1].get("reason"), "unknown_item")

    def test_requires_blacksmith(self):
        system, _r, sink = self._make()
        player, item = self._player_with_carried()
        self.assertFalse(system.salvage(player, "assault_rifle", None))
        self.assertEqual(sink.last()[1].get("reason"), "wrong_building")
        ar = FakeProductionBuilding("AR", owner=player)
        self.assertFalse(system.salvage(player, "assault_rifle", ar))
        self.assertEqual(sink.last()[1].get("reason"), "wrong_building")
        self.assertFalse(item.deleted)

    def test_not_owner_refused(self):
        system, _r, sink = self._make()
        player, item = self._player_with_carried()
        bs = FakeBlacksmith(owner=FakePlayer(level=10))
        self.assertFalse(system.salvage(player, "assault_rifle", bs))
        self.assertEqual(sink.last()[1].get("reason"), "not_owner")
        self.assertFalse(item.deleted)

    def test_operational_gates(self):
        system, _r, sink = self._make()
        player, item = self._player_with_carried()
        offline = FakeBlacksmith(owner=player, offline=True)
        self.assertFalse(system.salvage(player, "assault_rifle", offline))
        self.assertEqual(sink.last()[1].get("reason"), "building_offline")
        upgrading = FakeBlacksmith(owner=player, under_construction=True)
        self.assertFalse(system.salvage(player, "assault_rifle", upgrading))
        self.assertEqual(sink.last()[1].get("reason"), "building_upgrading")
        self.assertFalse(item.deleted)
        self.assertEqual(player.get_salvage(), 0)


class FakeRefinery:
    """Stand-in for a built Refinery (RF building instance)."""

    def __init__(self, owner=None, level=1, offline=False,
                 under_construction=False, building_type="RF"):
        self.key = "Refinery"
        self.db = DB(building_type=building_type, offline=offline,
                     under_construction=under_construction,
                     building_level=level)
        self._owner = owner

    @property
    def owner(self):
        return self._owner

    @property
    def is_offline(self):
        return bool(getattr(self.db, "offline", False))


class TestRefine(unittest.TestCase):
    """Refinery converts resources into Salvage — the Nexium sink (task 5.3).

    Covers: the conversion formula ``round(amount * refine_salvage_per_unit
    * (1 + refine_level_bonus*(level-1)))`` scaling with building level
    (R10.5, monotonic L1 1.0× → L5 1.5×); Nexium accepted as INPUT and the
    anti-loop invariant that the conversion outputs Salvage ONLY — never
    Nexium or any other resource (R10.4); the input deduction + Salvage
    credit; the `all` batch; the zero-yield refusal (nothing deducted); and
    the salvage-mirroring gate order (unknown resource / wrong building /
    ownership / operational / stock).

    Validates: Requirements 10.4, 10.5
    """

    def _make(self):
        registry = _make_registry()
        registry.buildings["RF"] = BuildingDef(
            name="Refinery", abbreviation="RF", cost={"Iron": 30},
            max_health=300, requires_hq=True, required_terrain=None,
            category="economy", produces=None,
            capabilities=frozenset({"resource_converter"}),
        )
        registry.buildings["BS"] = BuildingDef(
            name="Blacksmith", abbreviation="BS", cost={"Iron": 50},
            max_health=300, requires_hq=True, required_terrain=None,
            category="equipment", produces=None,
            capabilities=frozenset({"blacksmith"}),
        )
        event_bus = EventBus()
        sink = NotificationSink()
        event_bus.subscribe(PLAYER_NOTIFICATION, sink)
        system = EquipmentSystem(registry, event_bus)
        return system, registry, sink

    # ------------------- the conversion formula ------------------- #

    def test_nexium_input_credits_salvage(self):
        # The sink accepts Nexium (R10.4): 80 Nexium at L1 →
        # round(80 * 0.5 * 1.0) = 40 Salvage.
        system, _r, sink = self._make()
        player = FakePlayer(resources={"Nexium": 100})
        rf = FakeRefinery(owner=player, level=1)

        self.assertTrue(system.refine(player, "nexium", 80, rf))
        self.assertEqual(player.get_salvage(), 40)
        self.assertEqual(player.get_resource("Nexium"), 20)
        kind, data = sink.last()
        self.assertEqual(kind, "refined")
        self.assertEqual(data.get("salvage"), 40)
        self.assertEqual(data.get("resource"), "Nexium")

    def test_rate_monotonic_non_decreasing_in_level(self):
        # R10.5 level scaling: round(40 * (1 + 0.125*(level-1))) for 80
        # units → 40, 45, 50, 55, 60 across L1..L5.
        system, _r, _s = self._make()
        yields = []
        for level in (1, 2, 3, 4, 5):
            player = FakePlayer(resources={"Nexium": 80})
            rf = FakeRefinery(owner=player, level=level)
            self.assertTrue(system.refine(player, "nexium", 80, rf))
            yields.append(player.get_salvage())
        self.assertEqual(yields, [40, 45, 50, 55, 60])
        self.assertEqual(yields, sorted(yields))

    def test_never_outputs_nexium_or_any_resource(self):
        # The anti-loop invariant (R10.4): after any conversion, NO
        # resource has increased — the input decreased by exactly the
        # batch and the only credit is Salvage.
        system, _r, _s = self._make()
        for resource in ("Nexium", "Iron", "Wood"):
            player = FakePlayer(resources={
                "Nexium": 50, "Iron": 50, "Wood": 50})
            before = dict(player.db.resources)
            rf = FakeRefinery(owner=player, level=3)
            self.assertTrue(system.refine(player, resource, 30, rf))
            after = player.db.resources
            for res in before:
                if res == resource:
                    self.assertEqual(after[res], before[res] - 30)
                else:
                    self.assertEqual(after[res], before[res])
            self.assertGreater(player.get_salvage(), 0)

    def test_all_converts_full_stock(self):
        # amount None (`refine nexium` / `refine nexium all`) burns the
        # whole stock.
        system, _r, _s = self._make()
        player = FakePlayer(resources={"Nexium": 60})
        rf = FakeRefinery(owner=player, level=1)

        self.assertTrue(system.refine(player, "nexium", None, rf))
        self.assertEqual(player.get_resource("Nexium"), 0)
        self.assertEqual(player.get_salvage(), 30)

    def test_zero_yield_batch_refused_nothing_deducted(self):
        # 1 unit at 0.5/unit rounds to 0 Salvage → refused BEFORE any
        # deduction (never burn resources for nothing).
        system, _r, sink = self._make()
        player = FakePlayer(resources={"Nexium": 10})
        rf = FakeRefinery(owner=player, level=1)

        self.assertFalse(system.refine(player, "nexium", 1, rf))
        self.assertEqual(sink.last()[1].get("reason"), "too_little")
        self.assertEqual(player.get_resource("Nexium"), 10)
        self.assertEqual(player.get_salvage(), 0)

    # ------------------- gates ------------------- #

    def test_unknown_resource_refused(self):
        system, _r, sink = self._make()
        player = FakePlayer(resources={"Nexium": 50})
        rf = FakeRefinery(owner=player)
        self.assertFalse(system.refine(player, "plasma", 10, rf))
        self.assertEqual(sink.last()[1].get("reason"), "unknown_resource")

    def test_requires_resource_converter_building(self):
        # No building, and a NON-converter building (the Blacksmith bench),
        # both refuse with wrong_building — the capability is the gate.
        system, _r, sink = self._make()
        player = FakePlayer(resources={"Nexium": 50})
        self.assertFalse(system.refine(player, "nexium", 10, None))
        self.assertEqual(sink.last()[1].get("reason"), "wrong_building")
        bs = FakeBlacksmith(owner=player)
        self.assertFalse(system.refine(player, "nexium", 10, bs))
        self.assertEqual(sink.last()[1].get("reason"), "wrong_building")
        self.assertEqual(player.get_resource("Nexium"), 50)

    def test_not_owner_refused(self):
        system, _r, sink = self._make()
        player = FakePlayer(resources={"Nexium": 50})
        rf = FakeRefinery(owner=FakePlayer())
        self.assertFalse(system.refine(player, "nexium", 10, rf))
        self.assertEqual(sink.last()[1].get("reason"), "not_owner")
        self.assertEqual(player.get_resource("Nexium"), 50)

    def test_operational_gates(self):
        system, _r, sink = self._make()
        player = FakePlayer(resources={"Nexium": 50})
        offline = FakeRefinery(owner=player, offline=True)
        self.assertFalse(system.refine(player, "nexium", 10, offline))
        self.assertEqual(sink.last()[1].get("reason"), "building_offline")
        upgrading = FakeRefinery(owner=player, under_construction=True)
        self.assertFalse(system.refine(player, "nexium", 10, upgrading))
        self.assertEqual(sink.last()[1].get("reason"), "building_upgrading")
        self.assertEqual(player.get_resource("Nexium"), 50)
        self.assertEqual(player.get_salvage(), 0)

    def test_insufficient_stock_refused(self):
        system, _r, sink = self._make()
        player = FakePlayer(resources={"Nexium": 5})
        rf = FakeRefinery(owner=player)
        self.assertFalse(system.refine(player, "nexium", 10, rf))
        self.assertEqual(sink.last()[1].get("reason"),
                         "insufficient_resources")
        self.assertEqual(player.get_resource("Nexium"), 5)
        # An empty stock refuses `all` too (amount None resolves to 0).
        broke = FakePlayer()
        rf2 = FakeRefinery(owner=broke)
        self.assertFalse(system.refine(broke, "nexium", None, rf2))
        self.assertEqual(sink.last()[1].get("reason"),
                         "insufficient_resources")


if __name__ == "__main__":
    unittest.main()
