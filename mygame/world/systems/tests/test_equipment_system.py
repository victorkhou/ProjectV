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


class FakeCombatEngine:
    """Fake area-damage applier mirroring CombatEngine's public single-hit API.

    ``EquipmentSystem._apply_aoe_damage`` routes each AoE victim through
    ``apply_direct_hit`` (net = max(0, weapon damage - target damage_reduction);
    throwables pass ``include_attacker_bonus=False`` — the fake ignores it since
    it never adds one) which applies damage and records the hit so tests can
    assert both the damage dealt and that the post-damage hook fired.
    """

    def __init__(self):
        self.applied = []
        self.finalized = []

    def apply_direct_hit(self, attacker, target, weapon,
                         include_attacker_bonus=True, current_tick=None):
        base = weapon.get_stat("damage", 0)
        reduction = 0.0
        eq = getattr(target, "equipment", None)
        if eq is not None:
            reduction = eq.get_stat_total("damage_reduction")
        damage = max(0, int(base - reduction))
        self.applied.append((target, damage))
        self.finalized.append((attacker, target, damage))
        target.db.hp = target.db.hp - damage
        return damage


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

class TestThrow(unittest.TestCase):
    def _setup(self, targets):
        system, _, sink = _make_system()
        engine = FakeCombatEngine()
        system.set_area_damage_applier(lambda: engine)
        player = FakePlayer(level=1, coord_x=0, coord_y=0)
        player.location = FakeLocation(targets + [player])
        player.equipment.add_supply("frag_grenade", 1)
        return system, sink, engine, player

    def test_throw_hits_targets_in_radius_and_respects_armor(self):
        # radius 2 around (0,0); amount 40.
        near = FakeTarget("near", 1, 0, hp=100, damage_reduction=0)
        armored = FakeTarget("armored", 0, 2, hp=100, damage_reduction=15)
        far = FakeTarget("far", 5, 5, hp=100, damage_reduction=0)
        system, sink, engine, player = self._setup([near, armored, far])

        self.assertTrue(system.throw(player, "frag_grenade", 0, 0))

        hit_targets = {t for t, _ in engine.applied}
        self.assertIn(near, hit_targets)
        self.assertIn(armored, hit_targets)
        self.assertNotIn(far, hit_targets)  # outside radius

        # Damage respects armor: max(0, 40 - reduction).
        dmg = dict(engine.applied)
        self.assertEqual(dmg[near], 40)
        self.assertEqual(dmg[armored], 25)
        self.assertEqual(near.db.hp, 60)
        self.assertEqual(armored.db.hp, 75)

        # bombed notification reports 2 targets hit; item consumed.
        kind, data = sink.last()
        self.assertEqual(kind, "bombed")
        self.assertEqual(data.get("count"), 2)
        self.assertEqual(player.equipment.get_supply("frag_grenade"), 0)

    def test_throw_finalizes_each_hit_for_defeat_and_notification(self):
        # Regression: the throw path must call _finalize_hit per target so a
        # lethal bomb actually defeats/destroys and notifies — not just set HP.
        near = FakeTarget("near", 1, 0, hp=100, damage_reduction=0)
        armored = FakeTarget("armored", 0, 2, hp=100, damage_reduction=15)
        system, sink, engine, player = self._setup([near, armored])

        self.assertTrue(system.throw(player, "frag_grenade", 0, 0))

        # _finalize_hit ran once per damaged target (the shared defeat/notify
        # hook), with the thrower as attacker.
        finalized_targets = {t for (_a, t, _d) in engine.finalized}
        self.assertEqual(finalized_targets, {near, armored})
        for attacker, _t, _d in engine.finalized:
            self.assertIs(attacker, player)

    def test_throw_out_of_range_rejected(self):
        system, sink, engine, player = self._setup([])
        # effect.range is 6; target manhattan distance 20 -> rejected.
        self.assertFalse(system.throw(player, "frag_grenade", 10, 10))
        self.assertIn("throw_failed", sink.kinds())
        # Item not consumed on a rejected throw.
        self.assertEqual(player.equipment.get_supply("frag_grenade"), 1)

    def test_throw_no_targets_still_consumes_and_reports_zero(self):
        system, sink, engine, player = self._setup([])
        self.assertTrue(system.throw(player, "frag_grenade", 0, 0))
        kind, data = sink.last()
        self.assertEqual(kind, "bombed")
        self.assertEqual(data.get("count"), 0)
        self.assertEqual(player.equipment.get_supply("frag_grenade"), 0)

    def test_throw_wrong_category_rejected(self):
        system, sink, engine, player = self._setup([])
        player.equipment.add_supply("medkit", 1)
        self.assertFalse(system.throw(player, "medkit", 0, 0))
        self.assertIn("throw_failed", sink.kinds())

    def test_throw_with_no_applier_consumes_but_deals_no_damage(self):
        # No area-damage applier wired (e.g. before composition-root wiring):
        # the item is still consumed and bombed reports the in-radius count,
        # but targets take no damage.
        system, _, sink = _make_system()  # note: NO set_area_damage_applier
        target = FakeTarget("t", 1, 0, hp=100, damage_reduction=0)
        player = FakePlayer(level=1, coord_x=0, coord_y=0)
        player.location = FakeLocation([target, player])
        player.equipment.add_supply("frag_grenade", 1)

        self.assertTrue(system.throw(player, "frag_grenade", 0, 0))
        # Consumed, count reported, but no damage applied.
        self.assertEqual(player.equipment.get_supply("frag_grenade"), 0)
        self.assertEqual(target.db.hp, 100)
        kind, data = sink.last()
        self.assertEqual(kind, "bombed")
        self.assertEqual(data.get("count"), 1)


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


if __name__ == "__main__":
    unittest.main()
