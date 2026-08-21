"""
Equipment System for the RTS Combat Overworld game.

The framework-free use-case behind the equipment/weapons/special-items feature.
It owns four separable concerns:

- **Item production** — Armorer (AR), Medbay (MB), and Lab (LB) buildings yield
  items on a cooldown, routed into the owner's Supply_Bag (counted) or as Gear
  Game_Item objects by category.
- **Item actions** — equip / unequip / use / throw / reload, with rank gating,
  routing all player-facing text through the presenter.
- **Carry weight** — the weight-based carry cap (Supplies + on-person
  resources; equipped Gear excluded), with admin exemption.
- **Vault/HQ storage** — deposit / withdraw between the player's Spend_Pool and
  a Storage_Building, and the over-capacity spill funnel (add_resource_capped).

"""

from __future__ import annotations

import logging
import math
import random
from typing import Any, Callable

from world.constants import (
    BASE_CARRY_WEIGHT,
    DEFAULT_RESOURCE_WEIGHT,
    EQUIPMENT_SLOTS,
    GEAR_CATEGORIES,
    SUPPLY_CATEGORIES,
    WEAPON_SLOT_BY_TYPE,
)
from world.data_registry import DataRegistry
from world.definitions import ItemDef
from world.equipment_slots import weapon_slot_for_item
from world.event_bus import EventBus
from world.systems.base_system import BaseSystem
from world.systems.bench_gate import BenchGateMixin
from world.systems.equipment_carry import CarryWeightMixin
from world.systems.equipment_storage import StorageMixin

logger = logging.getLogger("mygame.equipment_system")

# Buildings that run item production each tick: Armorer (weapons/ammo/modern
# gear), Medbay (consumables), Lab (futuristic gear), Munitions Plant (bombs).
# Per-building catalogs live in items.yaml ``production_map``. Note the Armorer
# is ``AR``, not ``AA`` — there is no such building.
EQUIPMENT_BUILDING_TYPES = ("AR", "MB", "LB", "MP")

# Blacksmith reroll floor per level:
#
#     level_floor = REROLL_FLOOR_PER_LEVEL * (blacksmith_level - 1)
#
# L1 0.0 (no floor) through L5 0.4 — a maxed bench is meaningfully kinder
# without guaranteeing god-rolls (a 0.4 U clamp at skew 2 puts the worst
# possible roll at 16% of the band). The effective floor is
# ``max(level_floor, rarity_floor)``, so an Epic (0.50) or Legendary (0.75)
# item never rerolls below its rarity guarantee at any bench level.
REROLL_FLOOR_PER_LEVEL = 0.1

# Floor for the researched ``salvage_cost_mult``, clamped to ``[floor, 1.0]``
# (mirroring ``building_system._build_cost_multiplier``) so stacked
# cost-reduction research can neither trivialize the reroll Salvage sink nor
# raise costs. A module constant rather than a balance field because the
# shipped tech is 0.75 — this is a stacking guard, not a tuning lever.
# Promote to balance.yaml if a second economy-cost tech ships.
SALVAGE_COST_MULT_FLOOR = 0.5

# Master Gunsmithing craft-floor cap (item-loot-economy task 6.4, R11.6):
# the craft-path consumer clamps the researched ``craft_iqs_floor`` tech
# value to ``[0.0, cap]`` before handing it to the loot roller as the
# crafted-roll U-clamp. The tech accumulator ADDS effect values, so a
# second floor tech would SUM floors (0.25 + 0.25 = 0.5) — the cap absorbs
# additive stacking and keeps crafted gear "reliable, never god-roll"
# (design §9: the craft band's top is already only a good loot roll; a 0.5
# U-clamp at skew 2 puts the worst crafted roll at 25% of that band). A
# module constant, not a balance field — the shipped tech is 0.25, well
# under the cap, so this is a stacking guard, not a tuning lever
# (mirroring SALVAGE_COST_MULT_FLOOR above).
CRAFT_IQS_FLOOR_CAP = 0.5


class EquipmentSystem(BenchGateMixin, CarryWeightMixin, StorageMixin, BaseSystem):
    """Mediates equipment production, item actions, carry weight, and storage.

    Routes per-tick item production (Armory/Medbay/Lab) into the owner's
    stores, mediates equip/unequip/use/throw/reload with rank gating, computes
    the weight-based carry cap, and moves resources between a player's
    Spend_Pool and a Storage_Building (with over-capacity spill). All
    player-facing text is emitted as ``PLAYER_NOTIFICATION`` events for the
    presenter; the system composes no strings.

    Carry weight lives in
    :class:`~world.systems.equipment_carry.CarryWeightMixin` and the Vault/HQ
    storage + inflow choke point in
    :class:`~world.systems.equipment_storage.StorageMixin`.

    Args:
        registry: The DataRegistry holding item/building definitions.
        event_bus: The EventBus for publishing game events.
        create_item_func: Optional ``(item_def, owner) -> item`` factory;
            defaults to a dict-like item.
    """

    def __init__(
        self,
        registry: DataRegistry,
        event_bus: EventBus,
        create_item_func: Callable[[ItemDef, Any], Any] | None = None,
    ) -> None:
        super().__init__(registry, event_bus)
        self._create_item_func = create_item_func or self._default_create_item
        # Collaborators injected by the composition root via the set_* methods
        # below, rather than reached through a global service locator, so
        # ``world/systems`` stays framework-free. Each degrades safely when
        # unwired (isolated tests) — see the individual setters.

        #: Applies consumable buffs through the real timed-effect machinery.
        self._powerup_system: Any = None
        #: ``(player, item_key, count)`` — respawns a ground pickup for supply
        #: units that exceeded stack/weight, so supplies are never destroyed.
        self._supply_drop_spawner: Callable[[Any, str, int], Any] | None = None
        #: ``(holder, resource, amount)`` — spills the over-capacity remainder
        #: of an inflow into a holder pool as a ResourceDrop at their coords.
        self._resource_drop_spawner: Callable[[Any, str, int], Any] | None = None
        #: ``(building, item_def)`` — passive/agent production drops gear on the
        #: building's tile to be picked up, not into the owner's inventory.
        #: Unwired, the gear branch falls back to ``_create_item_func``.
        self._gear_drop_spawner: Callable[[Any, Any], Any] | None = None
        #: ``(victim, item_def)`` — the PvP underdog-bounty drop on the victim's
        #: death tile. Unwired (PvE), the gear is destroyed instead.
        self._pvp_gear_drop_spawner: Callable[[Any, Any], Any] | None = None

    # ------------------------------------------------------------------ #
    #  Collaborator injection (composition root)
    # ------------------------------------------------------------------ #

    def set_powerup_system(self, powerup_system: Any) -> None:
        """Inject the :class:`PowerupSystem` collaborator.

        Wired once at the composition root (``server/conf/game_init.py``).
        ``use`` routes consumable ``buff`` effects through
        ``powerup_system.apply_timed_effect`` so a stim writes the real
        ``db.active_powerups`` shape and is registered for tick-based expiry.
        """
        self._powerup_system = powerup_system

    def set_supply_drop_spawner(
        self, func: Callable[[Any, str, int], Any]
    ) -> None:
        """Inject the supply-drop spawner used by :meth:`add_supply_drop`.

        *func* is a callable ``(player, item_key, count)`` that re-creates a
        ground pickup for supply units the player could not carry (over the
        item's ``max_stack`` or the player's carry weight). Wired once at the
        composition root (``server/conf/game_init.py``) so the over-capacity
        spill re-uses the world's drop-spawn mechanism without
        ``world/systems`` importing ``typeclasses`` at module scope. When it is
        not wired, the spill degrades to a log and the leftover is reported via
        the ``carry_full`` notification but not respawned.
        """
        self._supply_drop_spawner = func

    def set_resource_drop_spawner(
        self, func: Callable[[Any, str, int], Any]
    ) -> None:
        """Inject the resource-drop spawner used by :meth:`add_resource_capped`.

        *func* is a callable ``(holder, resource, amount)`` that spawns a
        ``ResourceDrop`` at the holder's coordinates for the over-capacity
        remainder of an inflow into a *holder pool* (a player's Spend_Pool or a
        Storage_Building's pool). Wired once at the composition root
        (``server/conf/game_init.py``, task 11.1) over the existing
        ``ResourceSystem._spawn_resource_drop`` mechanism so the spill re-uses
        the world's drop machinery without ``world/systems`` importing
        ``typeclasses`` at module scope. When it is not wired, the spill
        degrades to a log — the leftover is still reported to the owning player
        via the ``carry_full``/``storage_full`` notification but not respawned.
        """
        self._resource_drop_spawner = func

    def set_gear_drop_spawner(
        self, func: Callable[[Any, Any], Any]
    ) -> None:
        """Inject the gear-drop spawner used by PASSIVE gear production.

        *func* is a callable ``(building, item_def)`` that spawns a unique
        equippable Gear ``GameItem`` as a ground drop on *building*'s tile
        (coordinate-indexed) and returns it. Wired once at the composition root
        (``server/conf/game_init.py``) over ``typeclasses.objects.spawn_gear_drop``
        so passive/agent production drops gear on the map — the player collects
        it with ``get`` — without ``world/systems`` importing ``typeclasses`` at
        module scope. When unwired (isolated tests), :meth:`_route_produced_item`
        falls back to the inventory ``_create_item_func`` factory.
        """
        self._gear_drop_spawner = func

    def set_pvp_gear_drop_spawner(
        self, func: Callable[[Any, Any], Any]
    ) -> None:
        """Inject the PvP gear drop-on-death spawner used by death loss.

        *func* is a callable ``(victim, item_def) -> obj`` that spawns a
        pickup-able Gear ``GameItem`` on the *victim*'s death tile (coordinate-
        indexed). Wired once at the composition root
        (``server/conf/game_init.py``) over ``typeclasses.objects.spawn_gear_drop``
        so a slain player's destroyed gear can drop for the killer without
        ``world/systems`` importing ``typeclasses`` at module scope. When unwired
        (PvE deaths, isolated tests) no drop occurs and the gear is destroyed as
        before.
        """
        self._pvp_gear_drop_spawner = func

    # ------------------------------------------------------------------ #
    #  Production
    # ------------------------------------------------------------------ #

    def process_production(self, active_buildings: list) -> None:
        """Process item production for active equipment buildings.

        For each active production building (AR/MB/LB, not offline):
            - Look up producible items via the registry
            - Select one item from the list
            - Route it into storage by its ``Item_Def.category`` (Req 3.2, 3.3,
              13.4):
                * **Supply** (``ammo``/``consumable``/``throwable``) → add a
                  counted unit to the owner's Supply_Bag via
                  ``owner.equipment.add_supply(item_key, 1, max_stack=...)``
                  (a count, never a Game_Item object).
                * **Gear** (``armor``/``weapon``/``accessory``) → create a
                  unique Game_Item slot object via ``_create_item_func``.
            There is no crossover: gear never lands in the bag and supplies
            never become slot objects.

        Production is rate-gated: each building accumulates one tick of
        progress per call and yields at most one item every
        ``balance.equipment_production_ticks`` ticks (mirroring the harvest
        cooldown), and stalls once its owner already holds
        ``balance.equipment_production_owner_cap`` un-equipped produced items.
        Together these bound the number of persistent objects a single idle
        building can create.

        Args:
            active_buildings: List of Building objects to process.
        """
        balance = getattr(self.registry, "balance", None)
        cooldown = int(getattr(balance, "equipment_production_ticks", 30) or 1)
        owner_cap = int(getattr(balance, "equipment_production_owner_cap", 0) or 0)

        for building in active_buildings:
            # Skip buildings that aren't operational (offline, or mid-upgrade —
            # an upgrading Armory/Medbay/Lab doesn't produce).
            from world.utils import building_is_operational
            if not building_is_operational(building):
                continue

            # Get building type
            building_type = self._get_building_type(building)
            if building_type not in EQUIPMENT_BUILDING_TYPES:
                continue

            # Agent gate: an equipment building only produces passively while it
            # has an assigned agent (Engineer). This is what "agents help do it
            # asynchronously" means — the building automates crafting for you.
            # Without an agent the building is inert; craft by hand instead.
            if not self._has_assigned_agent(building):
                continue

            # Get owner (needed before the rate gate: the owner's researched
            # production_multiplier tech shortens the per-item cooldown).
            owner = getattr(building, "owner", None)
            if owner is None:
                continue

            # Rate gate: advance this building's production progress and only
            # yield on the cooldown boundary (mirrors ResourceSystem's harvest
            # cooldown). Without this a building creates an object every tick.
            # The owner's production_multiplier tech (R13.3) divides the
            # cooldown — ×1.5 research yields items 1.5× as often.
            from world.utils import get_tech_bonus
            multiplier = get_tech_bonus(owner, "production_multiplier", default=1.0)
            effective_cooldown = cooldown
            if multiplier > 1.0:
                effective_cooldown = max(1, int(cooldown / multiplier))
            progress = self._advance_production_progress(building)
            if effective_cooldown > 1 and progress % effective_cooldown != 0:
                continue

            # Deactivation gate: an equipment building stops producing while its
            # owner has no active HQ (the PvP "no HQ = base inert" rule). Resolve
            # the HQ capability against the injected registry (hermetic in tests).
            from world.utils import owner_has_active_hq
            planet = getattr(getattr(building, "location", None), "planet_name", None)
            if not owner_has_active_hq(owner, planet, provider=self.registry):
                continue

            # Owner accumulation cap: stall production once the owner is holding
            # too many un-equipped produced items, so an idle player's building
            # cannot grow the object table without bound.
            if owner_cap and self._owner_produced_count(owner) >= owner_cap:
                continue

            # Look up producible items
            item_defs = self.registry.get_items_for_building(building_type)
            if not item_defs:
                continue

            # Passive production crafts the same items a player would by hand,
            # paying the same craft_cost from the owner's resources. Pick a
            # random item the owner can currently afford; if none is affordable,
            # the building idles this cycle (no free items).
            affordable = [
                idef for idef in item_defs
                if getattr(idef, "craft_cost", None)
                and owner.has_resources(idef.craft_cost)
            ]
            if not affordable:
                continue
            item_def = random.choice(affordable)

            # Charge the owner, then route the produce into storage by category
            # (Req 3.2, 3.3, 13.4). Deduct first so a routing failure can't mint
            # a free item; refund if routing fails.
            if not owner.deduct_resources(item_def.craft_cost):
                continue
            # Passive production: pass the building so gear drops on ITS tile
            # (the player collects it with ``get``), not into the owner's
            # inventory. Supplies still route into the owner's Supply_Bag.
            if not self._route_produced_item(item_def, owner, building=building):
                for res, amt in item_def.craft_cost.items():
                    owner.add_resource(res, amt)
                continue

            # Tell the owner what their building made — without this message,
            # production reads as "it never produced anything".
            self.notify(
                owner, "produced",
                item_name=item_def.name,
                building_type=building_type,
            )
            logger.info(
                "Equipment building %s produced %s for %s",
                building_type,
                item_def.name,
                getattr(owner, "key", "?"),
            )

    @staticmethod
    def _advance_production_progress(building: Any) -> int:
        """Increment and return a building's per-tick production progress.

        Stored on the building's ``db.production_progress`` attribute (falling
        back to a plain instance attribute in the stubbed test environment).
        """
        db = getattr(building, "db", None)
        current = int(getattr(db, "production_progress", 0) or 0) if db is not None \
            else int(getattr(building, "_production_progress", 0) or 0)
        current += 1
        if db is not None:
            try:
                db.production_progress = current
            except Exception:  # noqa: BLE001 - stub db without settable attrs
                building._production_progress = current
        else:
            building._production_progress = current
        return current

    @staticmethod
    def _owner_produced_count(owner: Any) -> int:
        """Count *owner*'s un-equipped produced items (Supply units + Gear objs).

        Sums the owner's Supply_Bag counts and the number of carried, NOT-yet-
        equipped Game_Item objects, giving the accumulation the owner cap bounds.
        Equipped gear is excluded: equipment slots are inherently bounded, and
        equipping is how a player relieves the stall — counting equipped items
        would let a fully-kitted player permanently starve their own production.
        Returns 0 when the owner exposes no equipment handler.
        """
        handler = getattr(owner, "equipment", None)
        if handler is None:
            return 0
        total = 0
        try:
            total += sum(handler.get_supplies().values())
        except Exception:  # noqa: BLE001 - handler without supplies in a stub
            pass
        # Carried, un-equipped Game_Item objects in the owner's inventory.
        # Exclude items currently equipped in a slot (matched by identity, the
        # same way the inventory's carried-gear section filters them).
        equipped_ids = set()
        try:
            equipped_ids = {id(it) for it in handler.get_all_equipped().values()}
        except Exception:  # noqa: BLE001 - handler without equipped accessor in a stub
            equipped_ids = set()
        contents = getattr(owner, "contents", None)
        if contents:
            total += sum(
                1 for obj in contents
                if getattr(obj, "_object_type_tag", None) == "item"
                and id(obj) not in equipped_ids
            )
        return total

    def _route_produced_item(
        self, item_def: ItemDef, owner: Any, building: Any = None,
        craft_building: Any = None,
    ) -> bool:
        """Route a produced *item_def* into storage by category.

        Supply-category produce (``ammo``/``consumable``/``throwable``) is added
        as a counted stack to the owner's Supply_Bag via ``add_supply`` — never
        a Game_Item object.

        Gear-category produce (``armor``/``weapon``/``accessory``) becomes a
        unique Game_Item. WHERE it lands depends on *building*:

        - **Passive/agent production** passes the producing *building*: the gear
          is spawned as a GROUND DROP on the building's tile (via the injected
          gear-drop spawner), so the player collects it with ``get``.
        - **Manual craft** passes ``building=None``: the gear goes into the
          crafter's inventory via ``_create_item_func`` (you hold what you made).

        There is no supply/gear crossover (Req 3.2, 3.3, 13.4).

        Args:
            item_def: The definition of the produced item.
            owner: The building owner receiving supply produce / crafted gear.
            building: The producing building (passive path) → gear drops on its
                tile; ``None`` (craft path) → gear goes to *owner*'s inventory.
            craft_building: The building a MANUAL craft happens at (the craft
                path passes it alongside ``building=None`` — ``building``
                keeps its "passive production drop target" meaning). Its
                level drives the crafted-rarity draw (≤ Rare, capped 5% at
                L5 — the deviation-from-R6.1 decision documented on
                :meth:`_roll_spawned_gear`), whichever equipment building
                it is (Armory/Lab/Medbay — the mechanism is generic).

        Returns:
            ``True`` if the item was routed, ``False`` otherwise (e.g. a Supply
            for an owner with no handler, a Supply_Bag entry at ``max_stack``, a
            gear drop with no resolvable tile, or an unrecognized category).
            Callers deduct the cost before routing and refund on ``False``.
        """
        category = getattr(item_def, "category", None)

        # Supply -> counted stack in the Supply_Bag (never a slot object).
        if category in SUPPLY_CATEGORIES:
            handler = getattr(owner, "equipment", None)
            if handler is None or not hasattr(handler, "add_supply"):
                logger.warning(
                    "Cannot produce supply %s: %s has no equipment handler",
                    item_def.key, getattr(owner, "key", "?"),
                )
                return False
            try:
                max_stack = int(getattr(item_def, "max_stack", 99) or 99)
            except (TypeError, ValueError):
                max_stack = 99
            # add_supply returns the number actually added (0 when the entry is
            # already at max_stack). Treat "added nothing" as a routing failure
            # so the caller refunds — reporting success here would burn the
            # cost for an item that never landed in the bag.
            added = handler.add_supply(item_def.key, 1, max_stack=max_stack)
            return bool(added)

        # Gear -> a unique Game_Item. Passive production (a *building* was given
        # AND a gear-drop spawner is wired) drops it on the building's tile so
        # the player collects it with ``get``; manual craft (no building) puts it
        # in the owner's inventory via the factory. Both paths call
        # evennia.create_object, which can raise (DB error, etc.); contain it and
        # report failure so the caller refunds — an escaping exception would
        # leave the cost deducted with no item and no refund. (A falsy return is
        # NOT treated as failure — the default dict/test factories return None on
        # success. The drop spawner returns None only on a missing tile, which IS
        # a failure — handled explicitly below.)
        if category in GEAR_CATEGORIES:
            if building is not None and self._gear_drop_spawner is not None:
                try:
                    drop = self._gear_drop_spawner(building, item_def)
                except Exception:
                    logger.exception(
                        "Failed to drop gear %s at %s's tile",
                        item_def.key, getattr(building, "key", "?"),
                    )
                    return False
                # None => no resolvable tile (building off-map): a real failure,
                # so the caller refunds rather than minting a lost item.
                if drop is None:
                    return False
                # Passive/agent production drop: roll the spawned instance in
                # the LOWEST rarity bucket (source weight 0) with no affixes —
                # passive income gets the safe-floor treatment (item-loot-
                # economy task 1.5, design §3.2). Defs without a roll_spec
                # stay fixed (R1.3); a roll failure degrades to a fixed item,
                # never a lost drop (R1.5).
                self._roll_spawned_gear(drop, item_def, crafted=False)
                return True
            try:
                item = self._create_item_func(item_def, owner)
            except Exception:
                logger.exception(
                    "Failed to create gear %s for %s",
                    item_def.key, getattr(owner, "key", "?"),
                )
                return False
            # Manual craft (building=None) rolls in the tighter per-stat
            # craft band (crafted=True — R1.4/R6.1, task 1.5) and applies
            # the crafter's Master Gunsmithing floor (task 6.4, R11.6) plus
            # the crafting building's level-scaled rarity draw (via
            # craft_building — see _roll_spawned_gear). The unwired-spawner
            # fallback (building given, isolated tests) is still passive
            # production, so it keeps the production-drop treatment.
            crafted = building is None
            craft_level = 0
            if crafted and craft_building is not None:
                from world.utils import get_building_level
                try:
                    craft_level = int(get_building_level(craft_building))
                except (TypeError, ValueError):
                    craft_level = 0
            roll = self._roll_spawned_gear(item, item_def,
                                           crafted=crafted,
                                           owner=owner,
                                           craft_level=craft_level)
            if crafted:
                # Stash the craft roll so `craft` can surface the stamped
                # IQS/rarity in its success notification (unrolled defs
                # leave this None → no value readout, R2.5).
                self._last_craft_roll = roll
            return True

        # Unrecognized category — content is load-validated to one of the six,
        # so this is defensive; produce nothing rather than mis-route.
        logger.warning(
            "Cannot produce %s: unrecognized category %r",
            item_def.key, category,
        )
        return False

    def _craft_iqs_floor(self, player: Any) -> float:
        """Resolve *player*'s crafted-roll floor tech (R11.6, task 6.4).

        Reads the ``craft_iqs_floor`` tech key via ``get_tech_bonus`` with
        ``default=0.0`` (no research → no floor, exactly today's crafted
        roll). The Master Gunsmithing tech ships ``effect_value:
        {craft_iqs_floor: 0.25}`` — a U-clamp fraction the loot roller
        applies INSIDE the craft band (the same mechanism as the rarity
        roll floors), so the researched floor raises the low end of a
        crafted roll but can never push it past the band (R6.1: crafted
        stays craft-band-bounded).

        Clamped to ``[0.0, CRAFT_IQS_FLOOR_CAP]``: the tech accumulator
        ADDS values, so stacked floor techs would sum — the cap absorbs
        that, and negative/garbage values degrade to 0 (never a *worse*
        roll than unresearched).
        """
        from world.utils import get_tech_bonus

        try:
            floor = float(get_tech_bonus(player, "craft_iqs_floor",
                                         default=0.0))
        except (TypeError, ValueError):
            return 0.0
        return min(max(floor, 0.0), CRAFT_IQS_FLOOR_CAP)

    def _roll_spawned_gear(self, item: Any, item_def: ItemDef,
                           *, crafted: bool, owner: Any = None,
                           craft_level: int = 0) -> Any:
        """Roll a freshly spawned gear *item* (item-loot-economy task 1.5).

        Delegates to :func:`world.systems.loot_roller.roll_and_stamp`, which
        writes per-instance ``rolled_stats`` + ``iqs`` onto the item (design
        §1.2). Both production paths pass rarity weight 0 — production drops
        roll in the lowest rarity bucket (design §3.2) and crafted items never
        get affixes (R6.1); base-elimination drops carry their own weight in
        ``base_elimination``. Crafted rolls additionally apply the *owner*'s
        Master Gunsmithing ``craft_iqs_floor`` tech (task 6.4, R11.6) as the
        in-band roll floor — see :meth:`_craft_iqs_floor` — and, when a
        *craft_level* (the crafting building's level) is supplied, draw a
        crafted rarity (≤ Rare) from the balance ``craft_rarity_table``
        (deliberate deviation from R6.1's "no crafted rarity" — per user
        request, a higher-level building crafts better gear, capping at
        Rare 5% at L5; see the loot_roller module docstring for the full
        decision record). When both floors apply (a Rare craft under Master
        Gunsmithing) the roller takes ``max`` of the two, like the reroll
        path. Uses the injected ``self._rng`` when a test set one (mirroring
        ``apply_death_loss``), else the module :mod:`random`. Never raises;
        a def without a ``roll_spec`` no-ops (R1.3/R1.5).
        """
        from world.systems.loot_roller import (
            DEFAULT_LOOT_ROLL_SKEW, roll_and_stamp,
        )
        balance = getattr(self.registry, "balance", None)
        craft_floor = 0.0
        if crafted and owner is not None:
            craft_floor = self._craft_iqs_floor(owner)
        return roll_and_stamp(
            item, item_def,
            source_rarity_weight=0.0,
            crafted=crafted,
            rng=getattr(self, "_rng", random),
            default_skew=getattr(
                balance, "loot_roll_skew", DEFAULT_LOOT_ROLL_SKEW),
            rarity_table=getattr(balance, "rarity_table", None),
            craft_floor=craft_floor,
            craft_level=craft_level,
            craft_rarity_table=getattr(balance, "craft_rarity_table", None),
        )

    # ------------------------------------------------------------------ #
    #  Mediated actions (use-case)
    # ------------------------------------------------------------------ #

    def equip(self, player: Any, item: Any) -> bool:
        """Equip a Game_Item for *player*, enforcing slot and rank gates.

        The use-case mediates the raw :class:`EquipmentHandler` store:

        1. Lazily migrate the legacy singular ``weapon`` slot, then reject an
           item whose canonical slot is not in
           :data:`~world.constants.EQUIPMENT_SLOTS` or does not match its
           weapon category/type.
        2. If the item declares a ``required_rank``, permit the equip only
           when the player's current rank is at least that rank. The rank name
           is resolved to a rank level via the registry rank table (the same
           lookup ``RankSystem``/``TechSystem`` use) and compared against the
           rank derived from ``world.utils.get_player_level``.
        3. On pass, delegate to ``player.equipment.equip(item)`` (the handler,
           which returns ``(ok, msg)``).

        A player-facing notification is emitted for every outcome
        (``equipped`` on success, ``equip_denied`` on a rank rejection); the
        domain composes no strings. Never raises into the command layer.

        Args:
            player: The equipping entity (a ``Combat_Entity``).
            item: The Game_Item to equip.

        Returns:
            ``True`` if the item was equipped, ``False`` otherwise.
        """
        item_name = self._item_name(item)

        # 1. Compatibility + slot gate. Migrate persisted singular weapon
        # slots before enforcing the same category/type pairing as the schema.
        slot = weapon_slot_for_item(item)
        if slot not in EQUIPMENT_SLOTS:
            logger.info(
                "Rejected equip of %s: slot %r not in EQUIPMENT_SLOTS",
                item_name, slot,
            )
            return False
        if not self._item_matches_slot(item, slot):
            logger.info(
                "Rejected equip of %s: category/weapon_type does not match "
                "slot %r",
                item_name, slot,
            )
            return False

        # 2. Rank gate — resolve required_rank -> rank level and compare.
        required_rank = self._item_attr(item, "required_rank", None)
        if not self._rank_allows(player, required_rank, item_name):
            return False

        # 3. Delegate to the per-entity storage handler.
        handler = getattr(player, "equipment", None)
        if handler is None:
            logger.warning("Cannot equip %s: player has no equipment handler", item_name)
            return False

        # Detect a swap: if the slot already holds a DIFFERENT item, the handler
        # auto-unequips it back to inventory. Capture it so we can tell the
        # player they took the old one off BEFORE announcing the new one.
        displaced = None
        if hasattr(handler, "get_equipped"):
            current = handler.get_equipped(slot)
            if current is not None and current is not item:
                displaced = current

        ok, _msg = handler.equip(item)
        if ok:
            # Re-fold any ``max_hp`` gear bonus into the entity's ceiling now
            # that the equipped set is final (covers swaps too). Raising the
            # ceiling grants headroom, not free HP.
            self._refresh_hp_max(player)
            # Unequip message first, then the equip message — the order the
            # player experiences the swap.
            if displaced is not None:
                self.notify(player, "unequipped",
                            item_name=self._item_name(displaced), slot=slot)
            self.notify(player, "equipped", item_name=item_name, slot=slot)
            # Directive trigger (D8)
            try:
                from world.event_bus import ITEM_EQUIPPED
                self.event_bus.publish(
                    ITEM_EQUIPPED, player=player,
                    item_key=self._item_attr(item, "item_key", item_name),
                    slot=slot,
                )
            except Exception:  # noqa: BLE001
                pass
        return bool(ok)

    def equip_all(self, player: Any, loose_items: list) -> int:
        """Equip loose gear into empty slots — one item per slot, deterministic.

        Iterates *loose_items* (already carried, unequipped gear in a stable
        order) and for each item whose target slot is still *empty*, equips it
        via :meth:`equip`. Items whose slot is already occupied (either from the
        start or claimed earlier in this pass) are **skipped** — no swapping.
        This gives a predictable "fill what's empty" behavior for ``equip all``.

        Args:
            player: The equipping entity.
            loose_items: Carried, unequipped gear (from ``_carried_gear_items``),
                in a deterministic order (caller must sort if desired).

        Returns:
            The number of items successfully equipped.
        """
        handler = getattr(player, "equipment", None)
        if handler is None or not hasattr(handler, "get_all_equipped"):
            return 0
        # Snapshot of slots already occupied at the start. Items equip into this
        # set — one per slot, first in sequence wins — so later same-slot items
        # are naturally skipped.
        filled: set[str] = set(handler.get_all_equipped().keys())
        count = 0
        for item in loose_items:
            slot = weapon_slot_for_item(item)
            if not slot or slot in filled:
                continue
            if self.equip(player, item):
                filled.add(slot)
                count += 1
        return count

    def unequip(self, player: Any, slot: str) -> bool:
        """Unequip whatever occupies *slot* for *player*.

        The use-case mediates the raw :class:`EquipmentHandler` store:

        1. Reject a ``slot`` that is not one of the canonical
           :data:`~world.constants.EQUIPMENT_SLOTS` (defensive — commands also
           resolve slots against this set).
        2. Delegate to ``player.equipment.unequip(slot)`` (the handler, which
           returns the removed Game_Item, or ``None`` when the slot was empty).

        A player-facing notification is emitted on success (``unequipped``);
        the domain composes no strings. Never raises into the command layer.

        Args:
            player: The unequipping entity (a ``Combat_Entity``).
            slot: The equipment slot name to clear.

        Returns:
            ``True`` if an item was unequipped, ``False`` otherwise (bad slot,
            no handler, or an empty slot).
        """
        # 1. Slot gate — reject slots that are not canonical.
        if slot not in EQUIPMENT_SLOTS:
            logger.info(
                "Rejected unequip: slot %r not in EQUIPMENT_SLOTS", slot
            )
            self.notify(player, "unequip_failed", slot=slot, reason="bad_slot")
            return False

        # 2. Delegate to the per-entity storage handler.
        handler = getattr(player, "equipment", None)
        if handler is None:
            logger.warning("Cannot unequip %s: player has no equipment handler", slot)
            return False

        item = handler.unequip(slot)
        if item is None:
            # Slot was already empty — tell the player rather than go silent.
            self.notify(player, "unequip_failed", slot=slot, reason="empty")
            return False

        # Re-fold the (now smaller) ``max_hp`` gear bonus; if the ceiling
        # dropped below current HP, ``refresh_equipment_hp_max`` clamps it down.
        self._refresh_hp_max(player)
        self.notify(
            player, "unequipped", item_name=self._item_name(item), slot=slot
        )
        return True

    def use(self, player: Any, item_key: str) -> bool:
        """Use one unit of a ``consumable`` Supply from *player*'s Supply_Bag.

        The use-case mediates the raw :class:`EquipmentHandler` Supply_Bag:

        1. Reject if the player does not hold *item_key* in their Supply_Bag
           (``handler.get_supply(item_key) <= 0``) — Req 8.4.
        2. Reject if the item's category is not ``consumable`` — Req 8.6.
        3. Enforce the rank gate: if the item declares a ``required_rank`` the
           player does not meet, reject — Req 7.3 (reuses the equip gate).
        4. Apply the ``effect``:
           - ``heal`` → :meth:`CombatEntity.heal` (already clamps to
             ``hp_max``); notify ``healed`` — Req 8.2.
           - ``buff`` → route through the injected
             :meth:`PowerupSystem.apply_timed_effect` so the entry uses the
             real ``{expires_tick, effect:{...}}`` shape and the player is
             registered for tick-based expiry; notify ``buff_applied`` —
             Req 8.3.
        5. On a successful effect, decrement the Supply_Bag by one and return
           ``True`` — Req 8.5.

        A player-facing notification is emitted for every outcome; the domain
        composes no strings. Never raises into the command layer.

        Args:
            player: The using entity (a ``Combat_Entity``).
            item_key: The Supply item key to use.

        Returns:
            ``True`` if the consumable was used, ``False`` otherwise.
        """
        handler = getattr(player, "equipment", None)
        item_def = self.registry.resolve_item(item_key)
        item_name = getattr(item_def, "name", None) or item_key

        # 1. Held check (Req 8.4).
        if handler is None or handler.get_supply(item_key) <= 0:
            self.notify(
                player, "use_failed", item_name=item_name, reason="not_held"
            )
            return False

        # 2. Category check — only consumables are usable (Req 8.6).
        category = getattr(item_def, "category", None) if item_def else None
        if category != "consumable":
            self.notify(
                player, "use_failed", item_name=item_name, reason="not_consumable"
            )
            return False

        # 3. Rank gate (Req 7.3) — reuse the equip rank-gate logic.
        required_rank = getattr(item_def, "required_rank", None)
        if not self._rank_allows(player, required_rank, item_name):
            return False

        # 4. Apply the effect.
        effect = getattr(item_def, "effect", None) or {}
        effect_type = effect.get("type")

        if effect_type == "heal":
            # Don't burn a medkit for nothing: a player already at full HP
            # keeps the item and is told they're at full health, rather than
            # consuming it for a 0-point heal.
            hp, hp_max = self._hp_pair(player)
            if hp >= hp_max:
                self.notify(
                    player, "use_failed", item_name=item_name, reason="already_full"
                )
                return False
            amount = int(effect.get("amount", 0))
            healed = self._apply_heal(player, amount)
            hp, hp_max = self._hp_pair(player)
            if not handler.remove_supply(item_key, 1):
                return False
            self.notify(
                player, "healed", amount=healed, hp=hp, hp_max=hp_max
            )
            return True

        if effect_type == "buff":
            if self._powerup_system is None:
                logger.warning(
                    "Cannot apply buff %s: no PowerupSystem injected", item_key
                )
                self.notify(
                    player, "use_failed", item_name=item_name, reason="unavailable"
                )
                return False
            stat = effect.get("stat")
            amount = effect.get("amount", 0)
            duration_ticks = int(effect.get("duration_ticks", 0))
            self._powerup_system.apply_timed_effect(
                player, stat, amount, duration_ticks
            )
            if not handler.remove_supply(item_key, 1):
                return False
            self.notify(
                player,
                "buff_applied",
                stat=stat,
                amount=amount,
                duration_ticks=duration_ticks,
            )
            return True

        # Unknown/unsupported effect for a consumable (defensive; content is
        # load-validated). Do not consume the item.
        logger.info(
            "Rejected use of %s: unsupported consumable effect %r",
            item_key, effect_type,
        )
        self.notify(
            player, "use_failed", item_name=item_name, reason="no_effect"
        )
        return False

    def reload(self, player: Any, *_args: Any, **_kwargs: Any) -> bool:
        """Reload the player's equipped ranged weapon from the Supply_Bag.

        Implements the magazine model (D5, Req 11): the ``weapon_ranged``-slot
        Game_Item holds its loaded rounds in ``db.loaded`` (0..``magazine_size``)
        and is refilled from the counted Ammo_Type in the player's Supply_Bag.

        The use-case mediates the raw :class:`EquipmentHandler` Supply_Bag and
        the weapon's magazine state:

        1. Read the ``weapon_ranged``-slot Game_Item via
           ``player.equipment.get_equipped("weapon_ranged")``. Reject if there
           is no equipped weapon, or the weapon declares no ``ammo_type`` (it
           is not a ranged, ammo-using weapon) — ``reload_failed`` reason
           ``no_ammo_weapon`` (Req 11.5).
        2. Reject if the magazine is already full (``db.loaded ==
           magazine_size``) — ``reload_failed`` reason ``already_loaded``; no
           ammo is drawn from the bag (Req 11.3).
        3. Reject if the Supply_Bag holds no matching ``ammo_type`` —
           ``reload_failed`` reason ``no_ammo`` (Req 11.4).
        4. Otherwise transfer ``min(magazine_size − db.loaded,
           bag[ammo_type])`` rounds from the Supply_Bag into ``db.loaded``,
           decrementing the bag by exactly that amount (Req 11.1, 11.2), and
           notify ``reloaded`` with the weapon's new ``loaded``/``magazine_size``
           and the remaining Ammo_Type in the bag (Req 11.6).

        A player-facing notification is emitted for every outcome; the domain
        composes no strings. Never raises into the command layer.

        Args:
            player: The reloading entity (a ``Combat_Entity``).

        Returns:
            ``True`` if the weapon was reloaded, ``False`` otherwise.
        """
        handler = getattr(player, "equipment", None)
        if handler is None:
            logger.warning("Cannot reload: player has no equipment handler")
            self.notify(player, "reload_failed", reason="no_ammo_weapon")
            return False

        weapon = handler.get_equipped("weapon_ranged")

        # 1. Ranged-weapon gate — must be an equipped weapon with an ammo_type.
        ammo_type = (
            self._item_attr(weapon, "ammo_type", None) if weapon is not None else None
        )
        if weapon is None or not ammo_type:
            # Distinguish the two "can't reload" cases so the message isn't
            # misleading. A ranged weapon that fires from the resource
            # stockpile (declares ``ammo_cost`` but no magazine ``ammo_type``)
            # simply has nothing to reload — say so, rather than claiming it
            # isn't an "ammo-using weapon" (it is; it just draws resources per
            # shot). Only a truly non-ammo weapon (none equipped, or a melee /
            # magazine-less item) gets ``no_ammo_weapon``.
            fires_from_resources = (
                weapon is not None
                and self._item_attr(weapon, "weapon_type", None) == "ranged"
                and self._item_attr(weapon, "ammo_cost", None)
            )
            reason = "no_magazine" if fires_from_resources else "no_ammo_weapon"
            self.notify(player, "reload_failed", reason=reason)
            return False

        weapon_name = self._item_name(weapon)

        # 2. Already-full gate — take no ammo from the bag (Req 11.3).
        try:
            magazine_size = int(self._item_attr(weapon, "magazine_size", 0) or 0)
        except (TypeError, ValueError):
            magazine_size = 0
        loaded = self._get_loaded(weapon)
        if loaded >= magazine_size:
            self.notify(player, "reload_failed", reason="already_loaded")
            return False

        # 3. Ammo-availability gate — bag must hold matching Ammo_Type (Req 11.4).
        available = handler.get_supply(ammo_type)
        transfer = min(magazine_size - loaded, available)
        if transfer <= 0:
            self.notify(player, "reload_failed", reason="no_ammo")
            return False

        # 4. Transfer exactly `transfer` rounds bag -> magazine (Req 11.1, 11.2).
        # Write the magazine FIRST; only decrement the bag if the write
        # succeeded, so a failed persistent write can never destroy ammo (the
        # bag would otherwise lose rounds the magazine never received).
        if not self._set_loaded(weapon, loaded + transfer):
            self.notify(player, "reload_failed", reason="no_ammo_weapon")
            return False
        if not handler.remove_supply(ammo_type, transfer):
            # Insufficient (should not happen given the check above) — defensive.
            # Roll the magazine back so loaded and bag stay consistent.
            self._set_loaded(weapon, loaded)
            self.notify(player, "reload_failed", reason="no_ammo")
            return False

        remaining = handler.get_supply(ammo_type)
        self.notify(
            player,
            "reloaded",
            weapon_name=weapon_name,
            loaded=loaded + transfer,
            magazine_size=magazine_size,
            ammo_name=self._ammo_name(ammo_type),
            remaining=remaining,
        )
        return True

    def craft(self, player: Any, item_token: str, building: Any) -> bool:
        """Craft one unit of an item at the player's current equipment building.

        The manual counterpart to the passive per-tick production an assigned
        agent drives (:meth:`process_production`): a player standing in their
        own Armory/Lab/Medbay spends the item's ``craft_cost`` to make one unit
        immediately. Agents just do this asynchronously while the player is
        elsewhere; both draw from the same resource pool and the same
        ``production_map`` catalog.

        Gates (each emits a ``craft_failed`` notification with a reason):

        1. ``unknown_item`` — the token resolves to no Item_Def.
        2. ``not_craftable`` — the item declares no ``craft_cost``.
        3. ``wrong_building`` — the player is not in an equipment building
           whose ``production_map`` catalog includes this item (also covers
           "no building here").
        4. ``not_owner`` — the building is not the player's.
        5. ``building_offline`` — the building is in offline protection.
        6. rank gate — reuses :meth:`_rank_allows` (emits ``equip_denied``).
        7. ``insufficient_resources`` — the player can't afford ``craft_cost``.

        On success the resources are deducted, the item is routed into the
        player's stores by category (reusing :meth:`_route_produced_item`), and
        a ``crafted`` notification fires. Never raises into the command layer.

        Args:
            player: The crafting player.
            item_token: Item key or display name (typo-tolerant resolve).
            building: The building the player is standing in (or ``None``).

        Returns:
            ``True`` if an item was crafted, ``False`` otherwise.
        """
        # 1. Resolve the item.
        item_def = self.registry.resolve_item(item_token)
        if item_def is None:
            self.notify(player, "craft_failed", reason="unknown_item",
                        item_name=item_token)
            return False

        item_name = item_def.name

        # 2. Craftable gate.
        craft_cost = getattr(item_def, "craft_cost", None)
        if not craft_cost:
            self.notify(player, "craft_failed", reason="not_craftable",
                        item_name=item_name)
            return False

        # 3. Right-building gate — the current building must be an equipment
        #    building whose catalog includes this item.
        btype = self._get_building_type(building) if building is not None else None
        catalog_keys = {
            idef.key for idef in self.registry.get_items_for_building(btype or "")
        }
        if (
            building is None
            or btype not in EQUIPMENT_BUILDING_TYPES
            or item_def.key not in catalog_keys
        ):
            self.notify(player, "craft_failed", reason="wrong_building",
                        item_name=item_name)
            return False

        # 4-5. Ownership + operational gate (shared bench tail).
        if not self._check_owner_operational(
            player, building, "craft_failed", item_name=item_name
        ):
            return False

        # 6. Rank gate (shared with equip/use; emits its own equip_denied).
        if not self._rank_allows(player, item_def.required_rank, item_name):
            return False

        # 7. Resource gate — the shared bench spend (deduct BEFORE producing,
        #    so a failed deduction can never mint a free item).
        if not self.charge_resources(
            player, craft_cost, "craft_failed", item_name=item_name
        ):
            return False

        # Clear the stashed craft roll before routing so a supply craft (or
        # an unrolled def) can never inherit a previous craft's IQS readout.
        self._last_craft_roll = None
        if not self._route_produced_item(item_def, player,
                                         craft_building=building):
            # Routing failed — refund so the spend isn't lost. The reachable
            # cause depends on category: a full Supply_Bag (max_stack) for
            # supplies, or a gear-factory error for gear. Report each accurately
            # rather than the misleading "wrong building" (gate 3 already
            # confirmed the building was right).
            for res, amt in craft_cost.items():
                player.add_resource(res, amt)
            reason = ("bag_full" if item_def.category in SUPPLY_CATEGORIES
                      else "craft_error")
            self.notify(player, "craft_failed", reason=reason,
                        item_name=item_name)
            return False

        logger.info(
            "%s crafted %s at %s",
            getattr(player, "key", "?"), item_def.key, btype,
        )
        # Surface the crafted item's value in the success line: the stamped
        # IQS (the `[73%]` quality tag) and — when the building-level rarity
        # draw assigned one — its rarity (`[Rare · 73%]`). Unrolled items
        # (no roll_spec) carry no roll and show no value, matching the
        # neutral display treatment elsewhere (R2.5).
        extra: dict[str, Any] = {}
        roll = getattr(self, "_last_craft_roll", None)
        if roll is not None and getattr(roll, "iqs", None) is not None:
            extra["iqs"] = roll.iqs
            if getattr(roll, "rarity", None):
                extra["rarity"] = roll.rarity
        self.notify(player, "crafted", item_name=item_name,
                    category=item_def.category, **extra)
        return True

    def apply_insert(self, player: Any, insert_token: str, building: Any,
                     weapon_token: str | None = None) -> bool:
        """Apply a Blacksmith insert to the player's equipped weapon (R5).

        The `insert` command backend (item-loot-economy §4.3): standing in
        their own operational Blacksmith, a player consumes one insert item
        from their Supply_Bag to permanently mutate the equipped weapon
        ``GameItem`` — irreversible by design (decided §12).

        Gate order mirrors :meth:`craft` (each failure emits an
        ``insert_failed`` notification with a reason; no active-HQ gate —
        design §4.1):

        1. ``unknown_item`` — the token resolves to no Item_Def.
        2. ``not_an_insert`` — the item is not a ``category: insert``
           consumable carrying a well-formed ``insert_effect``.
        3. ``wrong_building`` — the player is not standing in a building
           with the ``blacksmith`` capability (also covers "nothing here").
        4. ``not_owner`` — the Blacksmith is not the player's.
        5. ``building_offline`` / ``building_upgrading`` — operational gate.
        6. rank gate — reuses :meth:`_rank_allows` (emits ``equip_denied``).
        7. ``no_weapon`` / ``weapon_not_equipped`` / ``ambiguous_weapon`` —
           an equipped weapon is required. With two weapon slots
           (``weapon_melee`` + ``weapon_ranged``), *weapon_token* picks which
           one when both are equipped. Exact names/keys beat prefix matches;
           a prefix must identify exactly one equipped weapon. If both slots
           are filled and no token is given, or the token is ambiguous, the
           insert is refused before anything is consumed.
        8. ``no_slots`` — slot limit ``1 + blacksmith_level // 3`` (L1–2 →
           1 slot, L3+ → 2); over-limit is REFUSED with the weapon
           unchanged and the insert NOT consumed (design §4.3, R5.3).
        9. ``insufficient_supply`` — the player doesn't carry the insert
           item (the "cost": one unit is consumed from the Supply_Bag).

        On success the weapon instance is mutated per the effect type
        (``damage_type`` → ``db.damage_type``; ``range``/``stat`` →
        ``db.rolled_stats`` so combat's ``get_stat`` read path picks it
        up), the applied insert is recorded in ``db.inserts`` (display +
        slot-limit enforcement, and the PvP death drop carries it — R5.4),
        and the IQS is re-stamped through the single writer
        ``recompute_iqs`` (R2.4). Never raises into the command layer.

        Args:
            player: The player applying the insert.
            insert_token: Insert item key or display name (typo-tolerant).
            building: The building the player is standing in (or ``None``).
            weapon_token: Optional weapon name to sanity-check against the
                equipped weapon.

        Returns:
            ``True`` if the insert was applied, ``False`` otherwise.
        """
        from world.constants import BLACKSMITH
        from world.utils import building_has_capability, get_building_level
        from world.systems.loot_roller import (recompute_iqs,
                                               write_instance_field)

        # 1. Resolve the insert item.
        item_def = self.registry.resolve_item(insert_token)
        if item_def is None:
            self.notify(player, "insert_failed", reason="unknown_item",
                        item_name=insert_token)
            return False

        item_name = item_def.name

        # 2. Insert gate — must be a category:"insert" consumable with a
        #    well-formed payload (shape is load-validated; the type check
        #    here keeps the consume-then-mutate step below unreachable for
        #    anything the mutation switch doesn't handle).
        effect = getattr(item_def, "insert_effect", None)
        if (
            getattr(item_def, "category", None) != "insert"
            or not isinstance(effect, dict)
            or effect.get("type") not in ("damage_type", "range", "stat")
        ):
            self.notify(player, "insert_failed", reason="not_an_insert",
                        item_name=item_name)
            return False

        # 3. Right-building gate — the bench is any building whose def
        #    declares the `blacksmith` capability (design §4.1: the
        #    Blacksmith is a pure bench, not a production building, so this
        #    is a capability check rather than a production-catalog check).
        if building is None or not building_has_capability(
            building, BLACKSMITH, provider=self.registry
        ):
            self.notify(player, "insert_failed", reason="wrong_building",
                        item_name=item_name)
            return False

        # 4-5. Ownership + operational gate (shared bench tail).
        if not self._check_owner_operational(
            player, building, "insert_failed", item_name=item_name
        ):
            return False

        # 6. Rank gate (shared with equip/use/craft; emits equip_denied).
        if not self._rank_allows(player, item_def.required_rank, item_name):
            return False

        # 7. Weapon gate — inserts mutate an EQUIPPED weapon only (R5.1).
        #    Two weapon slots means *weapon_token* disambiguates when both
        #    are filled; see docstring point 7.
        handler = getattr(player, "equipment", None)
        can_get = handler is not None and hasattr(handler, "get_equipped")
        melee = handler.get_equipped("weapon_melee") if can_get else None
        ranged = handler.get_equipped("weapon_ranged") if can_get else None
        equipped = []
        for candidate in (melee, ranged):
            if candidate is not None and not any(
                candidate is existing for existing in equipped
            ):
                equipped.append(candidate)

        if not equipped:
            self.notify(player, "insert_failed", reason="no_weapon",
                        item_name=item_name)
            return False

        if weapon_token:
            ranked = [
                (self._weapon_match_rank(candidate, weapon_token), candidate)
                for candidate in equipped
            ]
            best_rank = max((rank for rank, _candidate in ranked), default=0)
            if best_rank == 0:
                self.notify(player, "insert_failed",
                            reason="weapon_not_equipped",
                            item_name=item_name, weapon_name=weapon_token)
                return False
            matches = [
                candidate for rank, candidate in ranked if rank == best_rank
            ]
            if len(matches) != 1:
                self.notify(
                    player, "insert_failed", reason="ambiguous_weapon",
                    item_name=item_name,
                    melee_name=self._item_name(melee),
                    ranged_name=self._item_name(ranged),
                )
                return False
            weapon = matches[0]
        elif len(equipped) == 1:
            weapon = equipped[0]
        else:
            # Both slots filled and nothing to disambiguate with — refuse
            # rather than silently guessing which weapon gets mutated.
            self.notify(
                player, "insert_failed", reason="ambiguous_weapon",
                item_name=item_name,
                melee_name=self._item_name(melee),
                ranged_name=self._item_name(ranged),
            )
            return False
        weapon_name = self._item_name(weapon)

        # 8. Slot-limit gate — refuse BEFORE consuming, weapon unchanged
        #    (R5.3). Limit = 1 + level//3: L1–2 → 1 slot, L3+ → 2.
        applied = list(self._read_instance_field(weapon, "inserts") or [])
        slot_limit = 1 + (int(get_building_level(building)) // 3)
        if len(applied) >= slot_limit:
            self.notify(player, "insert_failed", reason="no_slots",
                        item_name=item_name, weapon_name=weapon_name,
                        slot_limit=slot_limit)
            return False

        # 9. Cost gate — consuming the insert item from the Supply_Bag IS
        #    the cost. Deduct-first (mirrors craft); the mutation below is
        #    plain attribute writes and cannot fail after this point.
        if (
            not hasattr(handler, "remove_supply")
            or not handler.remove_supply(item_def.key, 1)
        ):
            self.notify(player, "insert_failed",
                        reason="insufficient_supply", item_name=item_name)
            return False

        # Apply — mutate the equipped weapon instance (design §4.3).
        effect = dict(effect)
        etype = effect.get("type")
        if etype == "damage_type":
            # Combat's _get_damage_type reads the instance: fire/psychic/
            # blast dispatch today, poison via the task-3.2 DoT branch.
            write_instance_field(weapon, "damage_type",
                                 str(effect.get("value")).lower())
        elif etype == "range":
            # Lands in rolled_stats["range"], read by the task-3.1
            # _resolve_weapon_range hook via get_stat.
            self._bump_rolled_stat(weapon, "range", effect.get("value"))
        elif etype == "stat":
            self._bump_rolled_stat(weapon, effect.get("stat"),
                                   effect.get("value"))
            for trade_stat, trade_val in (effect.get("tradeoff") or {}).items():
                self._bump_rolled_stat(weapon, trade_stat, trade_val)

        # Record the applied insert (display + slot-limit enforcement + the
        # PvP death-drop carry, R5.4) and re-stamp IQS through the single
        # writer (R2.4).
        applied.append({"key": item_def.key, "name": item_name,
                        "effect": effect})
        write_instance_field(weapon, "inserts", applied)
        recompute_iqs(weapon)

        logger.info("%s applied insert %s to %s",
                    getattr(player, "key", "?"), item_def.key, weapon_name)
        self.notify(player, "insert_applied", item_name=item_name,
                    weapon_name=weapon_name, slots_used=len(applied),
                    slot_limit=slot_limit)
        return True

    @classmethod
    def _bump_rolled_stat(cls, weapon: Any, stat: Any, delta: Any) -> None:
        """Add *delta* to *stat* in the weapon's ``rolled_stats`` (§4.3).

        ``get_stat`` prefers ``rolled_stats`` over the def base, so an
        unrolled weapon is seeded from its ``stat_modifiers`` base first —
        a +2 range insert on a base-5 weapon reads 7 afterwards, never 2.
        Results floor at 0 (a tradeoff can't push a stat negative).
        Integral values are stored as ints to match the loot roller.
        """
        from world.systems.loot_roller import write_instance_field

        if not stat:
            return
        try:
            delta = float(delta)
        except (TypeError, ValueError):
            return
        rolled = dict(cls._read_instance_field(weapon, "rolled_stats") or {})
        if stat in rolled:
            try:
                base = float(rolled[stat] or 0)
            except (TypeError, ValueError):
                base = 0.0
        else:
            mods = cls._item_attr(weapon, "stat_modifiers", None) or {}
            try:
                base = float(mods.get(stat, 0) or 0) if hasattr(mods, "get") \
                    else 0.0
            except (TypeError, ValueError):
                base = 0.0
        new = max(base + delta, 0.0)
        rolled[stat] = int(new) if new.is_integer() else new
        write_instance_field(weapon, "rolled_stats", rolled)

    @classmethod
    def _weapon_match_rank(cls, weapon: Any, token: str) -> int:
        """Rank a weapon-name match: exact ``2``, prefix ``1``, none ``0``.

        Ranking one weapon at a time lets callers compare the complete equipped
        set before choosing. In particular, an exact ranged match must beat a
        melee prefix encountered first, and a prefix matching both slots must
        remain ambiguous.
        """
        token_norm = " ".join(str(token).lower().replace("_", " ").split())
        if not token_norm:
            return 0
        candidates = (
            cls._item_attr(weapon, "item_key", None),
            getattr(weapon, "key", None),
            cls._item_name(weapon),
        )
        normalised = {
            " ".join(str(candidate).lower().replace("_", " ").split())
            for candidate in candidates if candidate
        }
        if token_norm in normalised:
            return 2
        if any(candidate.startswith(token_norm) for candidate in normalised):
            return 1
        return 0

    @classmethod
    def _weapon_matches(cls, weapon: Any, token: str) -> bool:
        """Return whether *token* exactly or prefix-matches *weapon*.

        A single-item predicate cannot establish that a prefix is unique among
        multiple candidates. It remains for reroll/salvage compatibility;
        set-wide operations such as inserts compare :meth:`_weapon_match_rank`
        across all candidates so exact-match priority and ambiguity are safe.
        """
        return cls._weapon_match_rank(weapon, token) > 0

    def _salvage_cost_multiplier(self, player: Any) -> float:
        """Resolve *player*'s economy cost-mult tech (R11.2, task 5.4).

        Reads the NEW ``salvage_cost_mult`` tech key via ``get_tech_bonus``
        with ``default=1.0`` (no research → costs unchanged — a copied
        ``default=0.0`` would make every reroll free, the wiring landmine
        flagged in the combat-rebalance review). The Salvage Protocols tech
        ships ``effect_value: {salvage_cost_mult: 0.75}`` meaning "reroll
        charge ×0.75" (−25%).

        Applies to the Blacksmith **reroll** charge (Salvage + resources)
        only — an insert's "cost" is the consumed insert item itself, which
        a multiplier cannot meaningfully discount (documented decision,
        task 5.4).

        Accumulator semantics (mirrors ``building_system.
        _build_cost_multiplier``): ``TechSystem._apply_tech_effect`` ADDS
        effect values for every key except ``production_multiplier``, so a
        single ``salvage_cost_mult`` tech stores its multiplier verbatim
        (0 + 0.75) and this consumer reads it directly. Two such techs
        would SUM (nonsense as a multiplier), so the value is clamped to
        ``[SALVAGE_COST_MULT_FLOOR, 1.0]``: research can never RAISE the
        charge (upper clamp absorbs additive stacking) and stacking can
        never trivialize the Salvage sink (the floor, 0.5). Only one
        salvage_cost_mult tech is the supported data shape.
        """
        from world.utils import get_tech_bonus

        mult = get_tech_bonus(player, "salvage_cost_mult", default=1.0)
        return min(1.0, max(SALVAGE_COST_MULT_FLOOR, float(mult)))

    def reroll(self, player: Any, item_token: str, building: Any) -> bool:
        """Re-roll a held/equipped item's BASE stats at the Blacksmith (R4.5).

        The `reroll` command backend (item-loot-economy §4.2/§4.4, task
        4.4): standing in their own operational Blacksmith, a player pays
        Salvage + resources to draw fresh base rolls for a rolled item they
        carry or have equipped. Base stats ONLY — rarity, affixes, and
        applied inserts are untouched (R4.5 default; reforge is deferred),
        and insert stat deltas are re-applied on top of the fresh base so
        an irreversible insert's value is never erased by a reroll.

        The reroll floor (design §4.4): fresh rolls use the loot band with
        a U-clamp floor of ``max(level_floor, rarity_floor)`` where
        ``level_floor = REROLL_FLOOR_PER_LEVEL * (blacksmith_level - 1)``
        (L1 0.0 → L5 0.4) — a higher bench raises the worst case, while an
        Epic/Legendary item keeps its rarity-guaranteed floor if higher.

        Gate order mirrors :meth:`apply_insert` (each failure emits a
        ``reroll_failed`` notification with a reason; no active-HQ gate —
        design §4.1):

        1. ``unknown_item`` — no carried or equipped item matches the token.
        2. ``not_rerollable`` — the item's def declares no ``roll_spec``
           (fixed items — ammo, consumables, legacy gear — never roll).
        3. ``wrong_building`` — not standing in a ``blacksmith``-capability
           building (also covers "nothing here").
        4. ``not_owner`` — the Blacksmith is not the player's.
        5. ``building_offline`` / ``building_upgrading`` — operational gate.
        6. rank gate — reuses :meth:`_rank_allows` (emits ``equip_denied``).
        7. ``insufficient_salvage`` / ``insufficient_resources`` — the cost
           (``balance.reroll_salvage_cost`` + ``balance.reroll_resource_cost``,
           both × the clamped ``salvage_cost_mult`` tech multiplier — see
           :meth:`_salvage_cost_multiplier`, R11.2 Salvage Protocols),
           checked then deducted FIRST (Salvage refunded if the resource
           spend fails mid-way), mirroring craft's deduct-first discipline.

        On success the fresh rolls land in ``db.rolled_stats`` (combat's
        ``get_stat`` read path), insert deltas are re-applied, and the IQS
        is re-stamped through the single writer ``recompute_iqs`` (R2.4).
        Never raises into the command layer.

        Args:
            player: The player rerolling.
            item_token: Name/key of a carried or equipped item (lenient
                matching, same as the insert weapon check).
            building: The building the player is standing in (or ``None``).

        Returns:
            ``True`` if the item was rerolled, ``False`` otherwise.
        """
        from world.constants import BLACKSMITH
        from world.utils import building_has_capability, get_building_level
        from world.systems.loot_roller import (rarity_roll_floor,
                                               recompute_iqs,
                                               reroll_base_stats,
                                               write_instance_field,
                                               DEFAULT_LOOT_ROLL_SKEW)

        # 1. Resolve the target — an item the player carries or wears
        #    (R4.2: "a held/equipped rolled item").
        item = self._find_carried_or_equipped(player, item_token)
        if item is None:
            self.notify(player, "reroll_failed", reason="unknown_item",
                        item_name=item_token)
            return False
        item_name = self._item_name(item)

        # 2. Rerollable gate — only items whose def declares a roll_spec
        #    roll at all (R1.3); everything else is fixed, permanently.
        #    ``item_key`` names the def on a live GameItem; the object key
        #    is the fallback for stubs/legacy objects without one.
        item_key = (self._item_attr(item, "item_key", None)
                    or getattr(item, "key", None) or "")
        item_def = self.registry.resolve_item(str(item_key))
        roll_spec = getattr(item_def, "roll_spec", None) if item_def else None
        if not isinstance(roll_spec, dict) or not roll_spec.get("stats"):
            self.notify(player, "reroll_failed", reason="not_rerollable",
                        item_name=item_name)
            return False

        # 3. Right-building gate — the bench is any building with the
        #    `blacksmith` capability (mirrors apply_insert).
        if building is None or not building_has_capability(
            building, BLACKSMITH, provider=self.registry
        ):
            self.notify(player, "reroll_failed", reason="wrong_building",
                        item_name=item_name)
            return False

        # 4-5. Ownership + operational gate (shared bench tail).
        if not self._check_owner_operational(
            player, building, "reroll_failed", item_name=item_name
        ):
            return False

        # 6. Rank gate (shared with equip/use/craft; emits equip_denied).
        if not self._rank_allows(player, item_def.required_rank, item_name):
            return False

        # 7. Cost gate — Salvage + resources (design §9), both checked
        #    before either is deducted, then deducted Salvage-first with a
        #    refund if the resource spend fails (craft's deduct-first
        #    discipline; the mutation below is plain writes and cannot
        #    fail after this point). Salvage Protocols research (R11.2,
        #    task 5.4) discounts BOTH components via the clamped
        #    ``salvage_cost_mult`` tech multiplier — no research reads 1.0
        #    and leaves the charge exactly at the balance numbers.
        balance = getattr(self.registry, "balance", None)
        cost_mult = self._salvage_cost_multiplier(player)
        salvage_cost = max(0, int(round(
            (int(getattr(balance, "reroll_salvage_cost", 40) or 0))
            * cost_mult)))
        resource_cost = {
            res: int(round(amt * cost_mult))
            for res, amt in dict(getattr(balance, "reroll_resource_cost",
                                         None) or {}).items()
        }
        resource_cost = {res: amt for res, amt in resource_cost.items()
                         if amt > 0}

        get_salvage = getattr(player, "get_salvage", None)
        have_salvage = int(get_salvage()) if callable(get_salvage) else 0
        if salvage_cost and have_salvage < salvage_cost:
            self.notify(player, "reroll_failed",
                        reason="insufficient_salvage", item_name=item_name,
                        salvage_cost=salvage_cost, salvage_have=have_salvage)
            return False
        if resource_cost and not player.has_resources(resource_cost):
            from world.utils import format_insufficient_resources
            self.notify(player, "reroll_failed",
                        reason="insufficient_resources", item_name=item_name,
                        breakdown=format_insufficient_resources(
                            player, resource_cost))
            return False

        if salvage_cost:
            spend_salvage = getattr(player, "spend_salvage", None)
            if not callable(spend_salvage) or not spend_salvage(salvage_cost):
                self.notify(player, "reroll_failed",
                            reason="insufficient_salvage",
                            item_name=item_name, salvage_cost=salvage_cost,
                            salvage_have=have_salvage)
                return False
        if resource_cost and not player.deduct_resources(resource_cost):
            # Refund the Salvage already spent — the charge is atomic.
            if salvage_cost and callable(getattr(player, "add_salvage", None)):
                player.add_salvage(salvage_cost)
            from world.utils import format_insufficient_resources
            self.notify(player, "reroll_failed",
                        reason="insufficient_resources", item_name=item_name,
                        breakdown=format_insufficient_resources(
                            player, resource_cost))
            return False

        # Effective floor: bench level lever + rarity guarantee (§4.4).
        level = int(get_building_level(building))
        level_floor = REROLL_FLOOR_PER_LEVEL * max(level - 1, 0)
        rarity = self._read_instance_field(item, "rarity")
        floor = min(max(level_floor, rarity_roll_floor(rarity)), 0.95)

        rolled = reroll_base_stats(
            roll_spec, floor=floor, rng=getattr(self, "_rng", random),
            default_skew=getattr(balance, "loot_roll_skew",
                                 DEFAULT_LOOT_ROLL_SKEW),
        )
        if not rolled:
            # Unreachable after gate 2 on well-formed data; refund on the
            # never-raise principle rather than eat the charge (R1.5 spirit).
            if salvage_cost and callable(getattr(player, "add_salvage", None)):
                player.add_salvage(salvage_cost)
            for res, amt in resource_cost.items():
                player.add_resource(res, amt)
            self.notify(player, "reroll_failed", reason="reroll_error",
                        item_name=item_name)
            return False

        # Fresh base rolls in, then re-apply insert deltas on top so the
        # permanent (irreversible) inserts keep their value (R5.4 spirit —
        # a reroll re-rolls BASE stats, it never strips a modification).
        write_instance_field(item, "rolled_stats", rolled)
        for applied in list(self._read_instance_field(item, "inserts") or []):
            effect = applied.get("effect") if hasattr(applied, "get") else None
            if not isinstance(effect, dict):
                continue
            etype = effect.get("type")
            if etype == "range":
                self._bump_rolled_stat(item, "range", effect.get("value"))
            elif etype == "stat":
                self._bump_rolled_stat(item, effect.get("stat"),
                                       effect.get("value"))
                for t_stat, t_val in (effect.get("tradeoff") or {}).items():
                    self._bump_rolled_stat(item, t_stat, t_val)
            # damage_type inserts live in db.damage_type — untouched here.

        # Re-stamp IQS through the single writer (R2.4).
        new_iqs = recompute_iqs(item, roll_spec)

        logger.info("%s rerolled %s at Blacksmith L%d (floor %.2f)",
                    getattr(player, "key", "?"), item_def.key, level, floor)
        self.notify(player, "rerolled", item_name=item_name, iqs=new_iqs,
                    salvage_cost=salvage_cost)
        return True

    def salvage(self, player: Any, item_token: str, building: Any) -> bool:
        """Break a carried item down into Salvage at the Blacksmith (R7).

        The ``salvage <item>`` command backend (item-loot-economy §5/§4.4,
        task 5.2): standing in their own operational Blacksmith, a player
        destroys a loose carried item and is credited Salvage per the
        design §5 yield formula::

            round((base_salvage + iqs * salvage_per_iqs)
                  * (1 + salvage_level_bonus * (blacksmith_level - 1)))

        The yield scales with BOTH the item's IQS and the bench level
        (R7.1) and is monotonic non-decreasing in each (R7.2): at the
        defaults (5 / 0.5 / 0.125) a 70-IQS item is ≈ 40 Salvage at L1 and
        ≈ 60 at L5 (L1 1.0× → L5 1.5×).

        **Eligibility (decided, task 5.2):** any loose CARRIED gear item —
        equipped gear is refused (unequip first; mirrors ``sell``, so a
        player never silently strips their loadout), counted Supply-bag
        stacks are not gear, and an UNROLLED item (no ``iqs``) salvages at
        the ``base_salvage`` floor with ``iqs = 0`` — R7's "a use for the
        loot I don't want" keeps even junk/legacy gear salvageable.

        Gate order mirrors :meth:`reroll` (each failure emits a
        ``salvage_failed`` notification with a reason; no active-HQ or
        rank gate — destroying your own item needs no rank):

        1. ``unknown_item`` / ``equipped`` / ``not_gear`` — target
           resolution (possession, R7.4): the token must match a loose
           carried gear item with a known def.
        2. ``wrong_building`` — not standing in a ``blacksmith``-capability
           building (also covers "nothing here").
        3. ``not_owner`` — the Blacksmith is not the player's (R7.4).
        4. ``building_offline`` / ``building_upgrading`` — operational gate.

        On success the source item is destroyed (R7.4) and the yield is
        credited to the player's ``db.salvage`` via ``add_salvage``
        (R7.3). Never raises into the command layer.

        Args:
            player: The salvaging player.
            item_token: Name/key of a carried item (lenient matching, same
                as the reroll target check).
            building: The building the player is standing in (or ``None``).

        Returns:
            ``True`` if the item was salvaged, ``False`` otherwise.
        """
        from world.constants import BLACKSMITH
        from world.utils import building_has_capability, get_building_level

        # 1. Resolve the target — a loose CARRIED item only (equipped gear
        #    is refused with its own reason; counted stacks aren't gear).
        item, reason = self._find_salvage_target(player, item_token)
        if item is None:
            self.notify(player, "salvage_failed", reason=reason,
                        item_name=item_token)
            return False
        item_name = self._item_name(item)

        # Only registry-known items are salvageable (mirrors
        # _resolve_sellable — arbitrary world objects aren't gear).
        item_key = (self._item_attr(item, "item_key", None)
                    or getattr(item, "key", None) or "")
        item_def = self.registry.resolve_item(str(item_key))
        if item_def is None:
            self.notify(player, "salvage_failed", reason="unknown_item",
                        item_name=item_name)
            return False

        # 2. Right-building gate — the bench is any building with the
        #    `blacksmith` capability (mirrors reroll/apply_insert).
        if building is None or not building_has_capability(
            building, BLACKSMITH, provider=self.registry
        ):
            self.notify(player, "salvage_failed", reason="wrong_building",
                        item_name=item_name)
            return False

        # 3-4. Ownership + operational gate (shared bench tail, R7.4).
        if not self._check_owner_operational(
            player, building, "salvage_failed", item_name=item_name
        ):
            return False

        # Yield (design §5): unrolled/legacy items read iqs 0 → the
        # base_salvage floor still applies (decided above).
        balance = getattr(self.registry, "balance", None)
        base = float(getattr(balance, "base_salvage", 5) or 0)
        per_iqs = float(getattr(balance, "salvage_per_iqs", 0.5) or 0)
        level_bonus = float(getattr(balance, "salvage_level_bonus", 0.125)
                            or 0)
        try:
            iqs = int(self._read_instance_field(item, "iqs") or 0)
        except (TypeError, ValueError):
            iqs = 0
        level = int(get_building_level(building))
        level_mult = 1.0 + level_bonus * max(level - 1, 0)
        amount = max(0, int(round((base + iqs * per_iqs) * level_mult)))

        # Destroy the source item (R7.4), then credit the yield (R7.3).
        if hasattr(item, "delete"):
            item.delete()
        add_salvage = getattr(player, "add_salvage", None)
        if callable(add_salvage):
            add_salvage(amount)
        get_salvage = getattr(player, "get_salvage", None)
        total = int(get_salvage()) if callable(get_salvage) else amount

        logger.info("%s salvaged %s (iqs %d) at Blacksmith L%d for %d",
                    getattr(player, "key", "?"), item_def.key, iqs, level,
                    amount)
        self.notify(player, "salvaged", item_name=item_name,
                    salvage=amount, salvage_total=total)
        return True

    def _find_salvage_target(self, player: Any, token: str):
        """Resolve a salvage target: a loose CARRIED item matching *token*.

        Returns ``(item, "")`` on success or ``(None, reason)`` where
        *reason* is one of:

        - ``unknown_item`` — nothing carried or equipped matches;
        - ``equipped`` — the only match is currently worn (unequip first —
          salvage never silently strips gear, mirroring ``sell``);
        - ``not_gear`` — the match is a counted supply drop/stack, not a
          loose Gear object.

        Carried objects (``player.contents``) are searched FIRST so a
        player holding a spare copy of an equipped item can salvage the
        spare. Matching is the same lenient item_key/key/name prefix match
        the reroll target check uses. Never raises.
        """
        if not token or not str(token).strip():
            return None, "unknown_item"
        try:
            carried = list(getattr(player, "contents", None) or [])
        except Exception:  # noqa: BLE001 - exotic contents proxy
            carried = []
        for item in carried:
            if item is None or not self._weapon_matches(item, token):
                continue
            if getattr(getattr(item, "db", None), "count", None) is not None:
                return None, "not_gear"
            return item, ""
        # An EQUIPPED match gets a dedicated message: unequip it first.
        handler = getattr(player, "equipment", None)
        if handler is not None and hasattr(handler, "get_all_equipped"):
            try:
                equipped = list(handler.get_all_equipped().values())
            except Exception:  # noqa: BLE001 - handler stub without the view
                equipped = []
            for item in equipped:
                if item is not None and self._weapon_matches(item, token):
                    return None, "equipped"
        return None, "unknown_item"

    def refine(self, player: Any, resource_token: str,
               amount: int | None, building: Any) -> bool:
        """Convert a carried resource into Salvage at the Refinery (R10.4).

        The ``refine <resource> [<amount>|all]`` command backend
        (item-loot-economy §7, task 5.3): standing in their own
        operational Refinery, a player burns *amount* units of a resource
        and is credited Salvage at a building-level-scaled rate (R10.5)::

            round(amount * refine_salvage_per_unit
                  * (1 + refine_level_bonus * (refinery_level - 1)))

        At the defaults (0.5 / 0.125) that is L1 1.0× → L5 1.5× —
        the same curve as the Blacksmith salvage yield, monotonic
        non-decreasing in level.

        **The Nexium sink (R10.4, anti-loop):** every ``RESOURCE_TYPES``
        entry is a valid INPUT — Nexium included, that is the point of
        the sink — but the conversion outputs Salvage ONLY. There is no
        code path here that credits Nexium (or any resource): the single
        credit call is ``player.add_salvage``.

        Gate order mirrors :meth:`salvage` (each failure emits a
        ``refine_failed`` notification with a reason; no active-HQ or
        rank gate — burning your own resources needs no rank):

        1. ``unknown_resource`` — the token is not a known resource type.
        2. ``wrong_building`` — not standing in a ``resource_converter``-
           capability building (also covers "nothing here").
        3. ``not_owner`` — the Refinery is not the player's.
        4. ``building_offline`` / ``building_upgrading`` — operational gate.
        5. ``insufficient_resources`` — the player carries less than
           *amount* of the resource (or none at all for ``all``).
        6. ``too_little`` — the conversion would round to 0 Salvage; the
           batch is refused with NOTHING deducted (never burn resources
           for no yield).

        Args:
            player: The refining player.
            resource_token: Resource name (case-insensitive; canonicalized
                to Title Case against ``RESOURCE_TYPES``).
            amount: Units to convert; ``None`` means "all carried".
            building: The building the player is standing in (or ``None``).

        Returns:
            ``True`` if the batch was converted, ``False`` otherwise.
        """
        from world.constants import RESOURCE_CONVERTER, RESOURCE_TYPES
        from world.utils import building_has_capability, get_building_level

        # 1. Resolve the resource — any known type is a valid input
        #    (Nexium included: the sink accepts it, R10.4).
        resource = str(resource_token or "").strip().title()
        if resource not in RESOURCE_TYPES:
            self.notify(player, "refine_failed", reason="unknown_resource",
                        resource=resource_token)
            return False

        # 2. Right-building gate — any building with the
        #    `resource_converter` capability (mirrors the Blacksmith
        #    bench's capability check).
        if building is None or not building_has_capability(
            building, RESOURCE_CONVERTER, provider=self.registry
        ):
            self.notify(player, "refine_failed", reason="wrong_building",
                        resource=resource)
            return False

        # 3-4. Ownership + operational gate (shared bench tail).
        if not self._check_owner_operational(
            player, building, "refine_failed", resource=resource
        ):
            return False

        # 5. Stock check — `all` (amount None) resolves to the full stock.
        get_resource = getattr(player, "get_resource", None)
        have = int(get_resource(resource)) if callable(get_resource) else 0
        if amount is None:
            amount = have
        amount = int(amount)
        if amount <= 0 or have < amount:
            self.notify(player, "refine_failed",
                        reason="insufficient_resources",
                        resource=resource, have=have, need=max(amount, 1))
            return False

        # Conversion rate (R10.5): per-unit rate × the per-level
        # multiplier (L1 1.0× → L5 1.5× at the defaults).
        balance = getattr(self.registry, "balance", None)
        per_unit = float(getattr(balance, "refine_salvage_per_unit", 0.5)
                         or 0)
        level_bonus = float(getattr(balance, "refine_level_bonus", 0.125)
                            or 0)
        level = int(get_building_level(building))
        level_mult = 1.0 + level_bonus * max(level - 1, 0)
        yielded = max(0, int(round(amount * per_unit * level_mult)))

        # 6. A batch that rounds to nothing is refused BEFORE any deduction
        #    — never burn resources for zero yield.
        if yielded < 1:
            self.notify(player, "refine_failed", reason="too_little",
                        resource=resource, amount=amount)
            return False

        # Deduct the input, then credit the yield. Salvage is the ONLY
        # output (R10.4 anti-loop) — no resource is ever credited here.
        if not player.deduct_resources({resource: amount}):
            # Unreachable after the stock check; refuse rather than raise.
            self.notify(player, "refine_failed",
                        reason="insufficient_resources",
                        resource=resource, have=have, need=amount)
            return False
        add_salvage = getattr(player, "add_salvage", None)
        if callable(add_salvage):
            add_salvage(yielded)
        get_salvage = getattr(player, "get_salvage", None)
        total = int(get_salvage()) if callable(get_salvage) else yielded

        logger.info("%s refined %d %s at Refinery L%d for %d Salvage",
                    getattr(player, "key", "?"), amount, resource, level,
                    yielded)
        self.notify(player, "refined", resource=resource, amount=amount,
                    salvage=yielded, salvage_total=total)
        return True

    def _find_carried_or_equipped(self, player: Any, token: str) -> Any:
        """The first equipped or carried item matching *token*, or ``None``.

        Reroll targets "a held/equipped rolled item" (R4.2): equipped gear
        is searched first (the common case — reroll the rifle you're
        holding), then loose carried objects (``player.contents``). Matching
        is the same lenient item_key/key/name prefix match the insert
        weapon check uses. Counted Supply-bag stacks aren't objects and are
        naturally excluded. Never raises.
        """
        if not token or not str(token).strip():
            return None
        candidates: list[Any] = []
        handler = getattr(player, "equipment", None)
        if handler is not None and hasattr(handler, "get_all_equipped"):
            try:
                candidates.extend(handler.get_all_equipped().values())
            except Exception:  # noqa: BLE001 - handler stub without the view
                pass
        try:
            candidates.extend(getattr(player, "contents", None) or [])
        except Exception:  # noqa: BLE001 - exotic contents proxy
            pass
        for item in candidates:
            if item is not None and self._weapon_matches(item, token):
                return item
        return None

    def sell_item(self, player: Any, item: Any) -> bool:
        """Sell a carried Gear *item* for a partial (50%) craft_cost refund.

        The item must be a loose (carried, not equipped) Gear ``GameItem`` the
        player is holding — the command resolves it. Refund = ``floor(cost/2)``
        per resource in the item's ``craft_cost``. The refund is routed through
        :meth:`add_resource_capped` (the carry-weight-bounded inflow choke
        point), so any amount over the player's carry limit spills to a ground
        drop rather than being lost. The item object is then deleted.

        Emits ``sell_failed`` (with a reason) on rejection and ``sold`` on
        success. Never raises into the command layer.

        Args:
            player: The selling player.
            item: The carried Gear ``GameItem`` to sell.

        Returns:
            ``True`` if the item was sold, ``False`` otherwise.
        """
        ok, item_def, reason = self._resolve_sellable(player, item)
        if not ok:
            self.notify(player, "sell_failed", reason=reason,
                        item_name=self._item_name(item))
            return False

        item_name = item_def.name
        craft_cost = getattr(item_def, "craft_cost", None) or {}

        # 50% refund, floored per resource. Route each through the capped inflow
        # so an over-carry-limit refund spills to the ground (never destroyed).
        refunded: dict[str, int] = {}
        for res, amt in craft_cost.items():
            give = int(amt) // 2
            if give <= 0:
                continue
            self.add_resource_capped(player, res, give)
            refunded[res] = give

        # Remove the sold item from the world.
        if hasattr(item, "delete"):
            item.delete()

        logger.info("%s sold %s (refund %r)",
                    getattr(player, "key", "?"), item_def.key, refunded)
        self.notify(player, "sold", item_name=item_name, refund=refunded)
        return True

    def junk_item(self, player: Any, item: Any) -> bool:
        """Destroy a carried Gear *item* with no refund.

        Same eligibility as :meth:`sell_item` (a loose, carried, non-equipped
        Gear ``GameItem``), but simply deletes the item — no resources returned.
        Emits ``sell_failed`` on rejection (shared reasons) and ``junked`` on
        success.

        Args:
            player: The player junking the item.
            item: The carried Gear ``GameItem`` to destroy.

        Returns:
            ``True`` if the item was destroyed, ``False`` otherwise.
        """
        ok, item_def, reason = self._resolve_sellable(player, item)
        if not ok:
            self.notify(player, "sell_failed", reason=reason,
                        item_name=self._item_name(item))
            return False

        item_name = item_def.name
        if hasattr(item, "delete"):
            item.delete()

        logger.info("%s junked %s", getattr(player, "key", "?"), item_def.key)
        self.notify(player, "junked", item_name=item_name)
        return True

    def _resolve_sellable(self, player: Any, item: Any):
        """Validate that *item* is a loose, carried Gear item the player owns.

        Shared eligibility for :meth:`sell_item` / :meth:`junk_item`. Returns
        ``(ok, item_def, reason)``: on success ``(True, ItemDef, "")``; on
        failure ``(False, None, reason)`` where *reason* is one of
        ``no_item`` / ``equipped`` / ``not_gear`` / ``unknown_item``.

        - ``no_item`` — nothing to act on.
        - ``equipped`` — the item is currently worn (unequip it first); we do
          not silently strip gear.
        - ``not_gear`` — a counted Supply-bag stack, not a loose Gear object
          (supplies aren't sellable in this pass).
        - ``unknown_item`` — the object carries no resolvable ``item_key``.
        """
        if item is None:
            return False, None, "no_item"

        # Reject equipped gear — must be unequipped first.
        handler = getattr(player, "equipment", None)
        if handler is not None and hasattr(handler, "get_all_equipped"):
            try:
                if any(it is item for it in handler.get_all_equipped().values()):
                    return False, None, "equipped"
            except Exception:  # noqa: BLE001 - handler stub without equipped view
                pass

        # Reject counted Supply drops/stacks (scope: carried gear only).
        if getattr(getattr(item, "db", None), "count", None) is not None:
            return False, None, "not_gear"

        item_key = self._item_attr(item, "item_key", None)
        if not item_key:
            return False, None, "unknown_item"
        item_def = self.registry.resolve_item(item_key)
        if item_def is None:
            return False, None, "unknown_item"

        return True, item_def, ""

    def add_supply_drop(self, player: Any, item_key: str, count: int) -> int:
        """Add up to *count* units of *item_key* to *player*'s Supply_Bag.

        Weight- and stack-aware pickup (D7, Req 10.2, 10.3). The number of
        units actually taken is::

            addable = min(count, max_stack_room, floor(weight_room / weight))

        where

        - ``max_stack_room = item.max_stack − current_count_in_bag`` — the room
          left in this bag entry before hitting the per-entry stack cap; and
        - ``weight_room = carry_limit(player) − carried_weight(player)`` — the
          remaining carry-weight budget (∞ for admins, whose ``carry_limit`` is
          unbounded).

        The per-unit ``weight`` guards against a non-positive weight: when
        ``weight <= 0`` the item imposes no weight cost, so weight is not a
        binding constraint and the pickup is limited only by stack room. When
        ``weight_room`` is ∞ (admin) the weight bound is also ∞.

        The units that fit are added via
        ``player.equipment.add_supply(item_key, addable, max_stack=item.max_stack)``.
        Any remainder (``count − added``) is spilled to a ground drop at the
        player's location via the injected supply-drop spawner so supplies are
        never destroyed (D9), and the player is notified ``carry_full`` with the
        carried/dropped split. Never raises into the command layer.

        Args:
            player: The picking-up entity (a ``Combat_Entity``).
            item_key: The Supply item key being picked up.
            count: The number of units offered by the drop.

        Returns:
            The number of units actually added to the Supply_Bag (0..count).
        """
        item_def = self.registry.resolve_item(item_key)
        item_name = getattr(item_def, "name", None) or item_key

        try:
            count = int(count)
        except (TypeError, ValueError):
            return 0
        if count <= 0:
            return 0

        handler = getattr(player, "equipment", None)
        if handler is None:
            logger.warning(
                "Cannot add supply drop %s: player has no equipment handler",
                item_key,
            )
            return 0

        # Resolve the per-entry stack cap and per-unit weight from the def.
        try:
            max_stack = int(getattr(item_def, "max_stack", 99) or 99)
        except (TypeError, ValueError):
            max_stack = 99
        try:
            weight = float(getattr(item_def, "weight", 1.0))
        except (TypeError, ValueError):
            weight = 1.0

        # Stack room left in this bag entry.
        current = int(handler.get_supply(item_key))
        max_stack_room = max_stack - current

        # Units that fit by weight (∞ for admins / weightless items), via the
        # shared count-by-weight conversion used for resource inflow too.
        weight_bound = self._units_that_fit(player, weight)

        addable = int(max(0, min(count, max_stack_room, weight_bound)))
        added = int(handler.add_supply(item_key, addable, max_stack=max_stack))

        dropped = count - added
        if dropped > 0:
            self._spawn_supply_drop(player, item_key, dropped)
            self.notify(
                player,
                "carry_full",
                item_name=item_name,
                carried=added,
                dropped=dropped,
            )
        return added

    # ------------------------------------------------------------------ #
    #  Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _get_loaded(weapon: Any) -> int:
        """Return a weapon's loaded rounds, or 0. Delegates to the shared
        accessor so combat and reload share one null/coercion contract."""
        from world.systems.combat_engine import get_loaded
        return get_loaded(weapon)

    @staticmethod
    def _set_loaded(weapon: Any, value: int) -> bool:
        """Write a weapon's loaded rounds; True on success. Delegates to the
        shared accessor. ``reload`` checks the result before decrementing the
        bag so a failed magazine write never destroys ammo."""
        from world.systems.combat_engine import set_loaded
        return set_loaded(weapon, value)

    def _spawn_supply_drop(self, player: Any, item_key: str, count: int) -> None:
        """Spill *count* units of *item_key* to a ground drop at *player*.

        Re-creates a pickup for supply units that could not be carried (over the
        stack cap or carry weight), so over-capacity inflow is never destroyed
        (D9). Routes through the injected supply-drop spawner
        (:meth:`set_supply_drop_spawner`) rather than importing ``typeclasses``
        at module scope, keeping ``world/systems`` framework-free. When no
        spawner is wired (e.g. before composition-root wiring or in a
        lightweight test), the spill degrades to a log — the leftover is still
        reported to the player via the ``carry_full`` notification.
        """
        if count <= 0:
            return
        spawner = self._supply_drop_spawner
        if spawner is None:
            logger.info(
                "add_supply_drop: no supply-drop spawner wired; %d %s left "
                "behind (not respawned)",
                count, item_key,
            )
            return
        try:
            spawner(player, item_key, count)
        except Exception:  # noqa: BLE001 - a spawn failure must not break pickup
            logger.warning(
                "add_supply_drop: supply-drop spawner failed for %d %s",
                count, item_key,
            )

    def _ammo_name(self, ammo_type: str) -> str:
        """Resolve an Ammo_Type item key to a display name for notifications."""
        try:
            item_def = self.registry.resolve_item(ammo_type)
        except Exception:  # noqa: BLE001 - resolution must never break reload
            item_def = None
        return getattr(item_def, "name", None) or ammo_type

    def _check_owner_operational(
        self, player: Any, building: Any, fail_kind: str, **payload: Any
    ) -> bool:
        """Shared bench gate: ownership + operational (offline/upgrading).

        Alias of :meth:`BenchGateMixin.check_owner_operational`, kept under a
        private name because the craft/insert/reroll/salvage/refine call sites
        read better with it.
        """
        return self.check_owner_operational(
            player, building, fail_kind, **payload
        )

    def _rank_allows(
        self, player: Any, required_rank: str | None, item_name: str
    ) -> bool:
        """Return ``True`` if *player*'s rank satisfies *required_rank*.

        Emits ``equip_denied`` and returns ``False`` when it does not. An
        unknown rank name falls open — content is load-validated.
        """
        if not required_rank:
            return True
        from world.utils import get_player_level
        from world.systems.rank_system import player_meets_rank

        player_level = get_player_level(player)
        if not player_meets_rank(player_level, required_rank, self.registry):
            self.notify(
                player,
                "equip_denied",
                item_name=item_name,
                required_rank=required_rank,
                current_rank=self._current_rank_name(player_level),
            )
            return False
        return True

    # ------------------------------------------------------------------ #
    #  Death loss + respawn-building recovery
    # ------------------------------------------------------------------ #

    def apply_death_loss(self, player: Any, killer: Any = None) -> dict:
        """Strip everything the *player* was carrying on death, recovering a
        building-level-scaled fraction into their Respawn building's stash.

        Death strips the character, not the base: all equipped gear, the
        Supply_Bag, and CARRIED resources are lost, while HQ/Vault storage is
        untouched. An owned ``RESPAWN_POINT`` building on the death planet
        recovers a fraction into its ``db.recovery_stash``; with no respawn
        building the loss is total — the building IS the safety net.

        The fraction is ``RESPAWN_RECOVERY_BY_LEVEL[building_level]`` (55% at L1
        → 95% at L5), applied per-item probabilistically and as
        ``floor(pct × amount)`` of each carried resource stack.

        PvP underdog bounty: when *killer* is a real player, each equipped item
        NOT recovered rolls to drop as a ground pickup on the victim's tile,
        at ``pvp_gear_drop_base_chance`` plus a per-level bonus when the victim
        outranks the killer, clamped to ``pvp_gear_drop_max_chance``. Only
        equipped gear drops. *killer* is None for PvE/agent/self/ally deaths.

        Returns ``{recovered, lost, dropped, building}``; never raises, since a
        recovery failure must not break combat.
        """
        from world.constants import RESPAWN_POINT, RESPAWN_RECOVERY_BY_LEVEL
        from world.utils import (
            building_has_capability, get_building_level, get_obj_attr,
        )

        summary = {"recovered": {}, "lost": {}, "dropped": {},
                   "building": None, "pct": 0.0}
        equipment = getattr(player, "equipment", None)

        # Resolve the recovery building: an owned RESPAWN_POINT building on the
        # planet the player died on. None → total loss.
        building = self._find_respawn_building(player)
        pct = 0.0
        if building is not None:
            level = max(1, min(int(get_building_level(building) or 1),
                               max(RESPAWN_RECOVERY_BY_LEVEL)))
            pct = float(RESPAWN_RECOVERY_BY_LEVEL.get(level, 0.0))
            summary["building"] = building
            summary["pct"] = pct

        stash = self._get_recovery_stash(building) if building is not None else None
        rng = getattr(self, "_rng", random)
        # PvP drop chance for a NOT-recovered gear item (0.0 = no drop / PvE).
        drop_chance = self._pvp_gear_drop_chance(player, killer)

        # --- Equipped gear + Supply_Bag: per-item probabilistic recovery ---
        if equipment is not None:
            # Equipped Gear (one object per slot).
            for slot in list(equipment.get_all_equipped().keys()):
                item = equipment.unequip(slot)
                if item is None:
                    continue
                key = self._item_attr(item, "item_key", None) or getattr(
                    item, "key", None)
                if stash is not None and key and rng.random() < pct:
                    self._stash_add(stash, "items", key, 1)
                    summary["recovered"][key] = summary["recovered"].get(key, 0) + 1
                    self._destroy_item(item)  # object destroyed; stash holds the key
                    continue
                # Not recovered → normally destroyed. In PvP, give it a second
                # roll to DROP as a ground pickup on the victim's tile (for the
                # killer) instead of being destroyed — the underdog bounty.
                if (drop_chance > 0 and key
                        and rng.random() < drop_chance
                        and self._drop_gear_on_death(player, key, item=item)):
                    summary["dropped"][key] = summary["dropped"].get(key, 0) + 1
                    self._destroy_item(item)  # original stripped; a fresh drop spawned
                else:
                    summary["lost"][key or "?"] = summary["lost"].get(key or "?", 0) + 1
                    self._destroy_item(item)
            # Supply_Bag (counted stacks) — roll each unit independently.
            for key, count in list(equipment.get_supplies().items()):
                count = int(count or 0)
                kept = sum(1 for _ in range(count) if rng.random() < pct) if (
                    stash is not None and pct > 0) else 0
                equipment.remove_supply(key, count)
                if kept > 0:
                    self._stash_add(stash, "items", key, kept)
                    summary["recovered"][key] = summary["recovered"].get(key, 0) + kept
                if count - kept > 0:
                    summary["lost"][key] = summary["lost"].get(key, 0) + (count - kept)

        # --- Carried resources: floor(pct x amount) recovered ---
        resources = self._read_carried_resources(player)
        for rtype, amount in resources.items():
            amount = int(amount or 0)
            if amount <= 0:
                continue
            kept = int(math.floor(amount * pct)) if stash is not None else 0
            if kept > 0:
                self._stash_add(stash, "resources", rtype, kept)
                summary["recovered"][rtype] = summary["recovered"].get(rtype, 0) + kept
            if amount - kept > 0:
                summary["lost"][rtype] = summary["lost"].get(rtype, 0) + (amount - kept)
        self._clear_carried_resources(player)

        if stash is not None:
            self._set_recovery_stash(building, stash)

        # PvP loot notice: tell the KILLER what the victim dropped and WHERE
        # (tile + planet), so the bounty is discoverable. The drop is a normal
        # ground pickup on the victim's tile with no owner lock — for a melee
        # kill the killer is standing on it, but for a turret/agent/ranged kill
        # the killer may be elsewhere (even on another planet), so the notice
        # names the planet and is purely informational: it does NOT imply the
        # killer is the only one who can grab it. No-op unless a genuine PvP kill
        # actually dropped something.
        if killer is not None and summary["dropped"]:
            self._notify_pvp_drop(killer, player, summary["dropped"])

        return summary

    def _notify_pvp_drop(self, killer: Any, victim: Any, dropped: dict) -> None:
        """Notify *killer* of the gear *victim* dropped on death (tile + planet).

        ``dropped`` is ``{item_key: count}``. Item keys are resolved to display
        names via the registry (falling back to the key). Location is read via
        the sanctioned :func:`world.utils.coords_of` (``(x, y, planet)``) so the
        drop is unambiguous even for a cross-planet turret/agent kill. Best-effort
        — a notification hiccup must never break death resolution.
        """
        try:
            from world.utils import coords_of

            items = getattr(self.registry, "items", None) or {}
            names = []
            for key, count in dropped.items():
                name = getattr(items.get(key), "name", None) or key
                names.append(f"{name} x{count}" if count > 1 else name)
            coords = coords_of(victim)
            x, y, planet = coords if coords is not None else ("?", "?", None)
            self.notify(
                killer, "pvp_gear_dropped",
                victim_name=getattr(victim, "key", "your foe"),
                items=", ".join(names),
                x=x, y=y, planet=planet,
            )
        except Exception:  # noqa: BLE001 - a loot notice must not break combat
            logger.exception("PvP drop notification failed")

    def _pvp_gear_drop_chance(self, victim: Any, killer: Any) -> float:
        """Per-item chance a NOT-recovered gear item drops for the killer.

        ``0.0`` (no drop) unless *killer* is a real player distinct from the
        victim and the feature is enabled. Otherwise
        ``base + per_level * max(0, victim_level - killer_level)`` clamped to
        ``max_chance`` — an UNDERDOG (victim outranks killer) drops MORE gear as
        a catch-up bounty; ganking down grants only the base. Never raises.
        """
        if killer is None or killer is victim:
            return 0.0
        bal = getattr(self.registry, "balance", None)
        base = float(getattr(bal, "pvp_gear_drop_base_chance", 0.0) or 0.0)
        if base <= 0:
            return 0.0  # feature disabled
        per_level = float(
            getattr(bal, "pvp_gear_drop_underdog_bonus_per_level", 0.0) or 0.0)
        max_chance = float(getattr(bal, "pvp_gear_drop_max_chance", base) or base)
        try:
            from world.utils import get_player_level
            gap = get_player_level(victim) - get_player_level(killer)
        except Exception:  # noqa: BLE001 - level read must not break death loss
            gap = 0
        chance = base + per_level * max(0, gap)
        return max(0.0, min(chance, max_chance))

    def _drop_gear_on_death(self, victim: Any, item_key: str,
                            item: Any = None) -> bool:
        """Spawn *item_key* as a ground-pickup Gear drop on *victim*'s tile.

        Delegates to the injected PvP gear-drop spawner (composition root; over
        ``spawn_gear_drop``). Returns True if a drop was spawned, False if the
        spawner is unwired, the key has no ItemDef, or the spawn was refused
        (e.g. tile full) — in which case the caller destroys the item as normal.
        Never raises.

        **R1.6 preservation contract (item-loot-economy, design §1.2):** the
        spawner creates a FRESH ``GameItem`` from the ItemDef, so when the
        stripped instance *item* is given, its per-item state — ``rolled_stats``,
        ``affixes``, ``rarity``, ``iqs``, ``inserts`` — is copied onto the drop.
        The PvP death drop NEVER re-rolls: dropped gear keeps its rolls, always.
        """
        spawner = self._pvp_gear_drop_spawner
        if spawner is None:
            return False
        item_def = (getattr(self.registry, "items", None) or {}).get(item_key)
        if item_def is None:
            return False
        try:
            drop = spawner(victim, item_def)
        except Exception:  # noqa: BLE001 - a drop must not break death loss
            logger.exception("PvP gear drop failed for %s", item_key)
            return False
        if drop is None:
            return False
        if item is not None:
            self._preserve_instance_state(item, drop)
        return True

    #: Per-instance state a PvP death drop carries with it (R1.6, R5.4):
    #: rolls, affixes, rarity, IQS, applied Blacksmith inserts, and the
    #: instance damage_type a damage-type insert wrote (range/stat inserts
    #: live in rolled_stats, but a conversion lives in its own field — it
    #: must carry too, or a dropped fire-converted weapon reverts, task 4.3).
    _INSTANCE_STATE_FIELDS = ("rolled_stats", "affixes", "rarity",
                              "iqs", "inserts", "damage_type")

    @classmethod
    def _preserve_instance_state(cls, source: Any, drop: Any) -> None:
        """Copy *source*'s per-instance item state onto the fresh *drop* (R1.6).

        Only fields actually SET on the source are written — an unrolled item
        drops as an unrolled item, never gaining empty roll attributes (R12.1).
        Values are deep-copied so the drop owns its state after the original
        is destroyed. Best-effort per field; never raises into death loss.
        """
        import copy as _copy
        from world.systems.loot_roller import write_instance_field
        for name in cls._INSTANCE_STATE_FIELDS:
            try:
                value = cls._read_instance_field(source, name)
                if value is None or value == {} or value == []:
                    continue  # unset on the source → stays unset on the drop
                write_instance_field(drop, name, _copy.deepcopy(value))
            except Exception:  # noqa: BLE001 - a copy must not break death loss
                logger.exception(
                    "PvP drop: failed to carry %s across", name)

    @staticmethod
    def _read_instance_field(item: Any, name: str) -> Any:
        """Read a per-instance roll-state field off *item* robustly.

        Works for a live ``GameItem`` (named properties over ``db``), a stub
        exposing plain attributes or a ``db`` bag, an Evennia ``attributes``
        handler, or the dict-shaped test factory item. Returns ``None`` when
        the field is unset anywhere.
        """
        value = getattr(item, name, None)
        if value not in (None, "", {}, []) and not callable(value):
            return value
        db = getattr(item, "db", None)
        if db is not None:
            value = getattr(db, name, None)
            if value is not None:
                return value
        attrs = getattr(item, "attributes", None)
        if attrs is not None and hasattr(attrs, "get"):
            try:
                value = attrs.get(name, default=None)
            except TypeError:
                value = attrs.get(name)
            if value is not None:
                return value
        if isinstance(item, dict):
            return item.get(name)
        return None

    def _find_respawn_building(self, player: Any):
        """Return the player's owned RESPAWN_POINT building on their death planet.

        Respawn recovery is per-planet: only a respawn building on the SAME
        planet the player died on recovers their loadout (one per planet is the
        model). Returns None if the player owns no respawn building on that
        planet — the loss is then total. If the player's planet can't be resolved
        (a locationless test double), any owned respawn building qualifies.
        Best-effort — a lookup failure yields None, never raises.
        """
        from world.constants import RESPAWN_POINT
        from world.utils import building_has_capability, get_obj_attr
        try:
            buildings = list(player.get_buildings() or []) if hasattr(
                player, "get_buildings") else []
        except Exception:  # noqa: BLE001
            return None
        candidates = [
            b for b in buildings
            if building_has_capability(b, RESPAWN_POINT, provider=self.registry)
        ]
        if not candidates:
            return None
        planet = get_obj_attr(player, "coord_planet")
        if not planet:
            return candidates[0]  # can't scope by planet (test double) → any
        same = [b for b in candidates
                if get_obj_attr(b, "coord_planet") == planet]
        return same[0] if same else None

    @staticmethod
    def _get_recovery_stash(building: Any) -> dict:
        """Return a mutable copy of the building's recovery stash."""
        from world.utils import get_obj_attr
        stash = get_obj_attr(building, "recovery_stash") or {}
        return {
            "items": dict(stash.get("items", {})),
            "resources": dict(stash.get("resources", {})),
        }

    @staticmethod
    def _set_recovery_stash(building: Any, stash: dict) -> None:
        """Persist the recovery stash back onto the building (db/attributes)."""
        db = getattr(building, "db", None)
        if db is not None and hasattr(db, "recovery_stash"):
            db.recovery_stash = stash
            return
        attrs = getattr(building, "attributes", None)
        if attrs is not None and hasattr(attrs, "add"):
            attrs.add("recovery_stash", stash)
        elif db is not None:
            db.recovery_stash = stash

    @staticmethod
    def _stash_add(stash: dict, bucket: str, key: str, amount: int) -> None:
        b = stash.setdefault(bucket, {})
        b[key] = int(b.get(key, 0)) + int(amount)

    def _read_carried_resources(self, player: Any) -> dict:
        """Return a snapshot of the player's CARRIED resources ({type: amount})."""
        db = getattr(player, "db", None)
        res = getattr(db, "resources", None) if db is not None else None
        if isinstance(res, dict):
            return dict(res)
        # Fallback for fakes exposing get_resource over RESOURCE_TYPES.
        from world.constants import RESOURCE_TYPES
        if hasattr(player, "get_resource"):
            return {r: int(player.get_resource(r) or 0) for r in RESOURCE_TYPES}
        return {}

    @staticmethod
    def _clear_carried_resources(player: Any) -> None:
        """Zero the player's carried resources (base storage is untouched)."""
        from world.constants import RESOURCE_TYPES
        db = getattr(player, "db", None)
        if db is not None and isinstance(getattr(db, "resources", None), dict):
            db.resources = {r: 0 for r in RESOURCE_TYPES}
            return
        # Fake fallback: deduct everything currently held.
        if hasattr(player, "get_resource") and hasattr(player, "deduct_resources"):
            held = {r: int(player.get_resource(r) or 0) for r in RESOURCE_TYPES}
            player.deduct_resources({r: a for r, a in held.items() if a > 0})

    @staticmethod
    def _destroy_item(item: Any) -> None:
        """Destroy a Gear GameItem object (best-effort; dicts/fakes are no-ops)."""
        delete = getattr(item, "delete", None)
        if callable(delete):
            try:
                delete()
            except Exception:  # noqa: BLE001 - never break death handling
                pass

    def collect_recovery(self, player: Any, building: Any) -> dict:
        """Move a Respawn building's recovery stash back to the *player*.

        The retrieval half of the death-loss loop: when a player stands on their
        Respawn building after dying, this returns the items and resources that
        were recovered into ``db.recovery_stash``. Supplies rejoin the Supply_Bag
        and Gear is created into inventory (the player re-equips manually);
        resources are added up to the player's remaining carry weight, with the
        leftover STAYING in the stash (never dropped). Emits a ``recovery_collected``
        notification. Returns a summary ``{items, resources, left_behind}``.
        Best-effort — never raises into the command layer.
        """
        summary = {"items": {}, "resources": {}, "left_behind": {}}
        if building is None:
            return summary
        stash = self._get_recovery_stash(building)
        if not stash.get("items") and not stash.get("resources"):
            self.notify(player, "recovery_empty")
            return summary

        # Items: supplies → Supply_Bag; gear → inventory via the factory.
        remaining_items: dict[str, int] = {}
        for key, count in list(stash.get("items", {}).items()):
            count = int(count or 0)
            if count <= 0:
                continue
            item_def = None
            try:
                item_def = self.registry.resolve_item(key)
            except Exception:  # noqa: BLE001
                item_def = None
            if item_def is None:
                remaining_items[key] = count  # unknown key stays stashed
                continue
            category = getattr(item_def, "category", None)
            handler = getattr(player, "equipment", None)
            if category in SUPPLY_CATEGORIES and handler is not None \
                    and hasattr(handler, "add_supply"):
                max_stack = int(getattr(item_def, "max_stack", 99) or 99)
                added = handler.add_supply(key, count, max_stack=max_stack)
                if added:
                    summary["items"][key] = summary["items"].get(key, 0) + added
                if count - added > 0:
                    remaining_items[key] = count - added  # over stack → keep rest
            else:
                # Gear (or a supply with no handler): create one object per unit.
                made = 0
                for _ in range(count):
                    try:
                        self._create_item_func(item_def, player)
                        made += 1
                    except Exception:  # noqa: BLE001
                        break
                if made:
                    summary["items"][key] = summary["items"].get(key, 0) + made
                if count - made > 0:
                    remaining_items[key] = count - made

        # Resources: add up to carry room; leftover stays stashed.
        remaining_res: dict[str, int] = {}
        for rtype, amount in list(stash.get("resources", {}).items()):
            amount = int(amount or 0)
            if amount <= 0:
                continue
            room = self._resource_room(player, rtype)
            take = amount if room is None else min(amount, room)
            take = max(0, int(take))
            if take > 0 and hasattr(player, "add_resource"):
                player.add_resource(rtype, take)
                summary["resources"][rtype] = take
            if amount - take > 0:
                remaining_res[rtype] = amount - take
                summary["left_behind"][rtype] = amount - take

        # Persist what did not fit; clear the rest.
        self._set_recovery_stash(
            building, {"items": remaining_items, "resources": remaining_res}
        )
        self.notify(
            player, "recovery_collected",
            items=dict(summary["items"]), resources=dict(summary["resources"]),
            left_behind=dict(summary["left_behind"]),
        )
        return summary

    def _resource_room(self, player: Any, resource: str):
        """Units of *resource* the player can still carry, or None if unbounded.

        Reuses the carry-weight model (``_resource_weight_room`` — admins and
        non-positive weights are unbounded → ``inf`` → None here). Returns None
        also when weight can't be evaluated (test double), so a collect never
        silently drops everything."""
        try:
            room = self._resource_weight_room(player, resource)
        except Exception:  # noqa: BLE001
            return None
        if room == float("inf"):
            return None
        return int(room)

    @staticmethod
    def _apply_heal(player: Any, amount: int) -> int:
        """Heal *player* by *amount* via ``CombatEntity.heal`` (clamped).

        Returns the actual HP restored (0 if the entity cannot heal).
        """
        heal = getattr(player, "heal", None)
        if callable(heal):
            return int(heal(amount))
        return 0

    @staticmethod
    def _refresh_hp_max(player: Any) -> None:
        """Re-fold *player*'s equipped ``max_hp`` bonus into ``db.hp_max``.

        Delegates to :meth:`CombatEntity.refresh_equipment_hp_max`, which
        recomputes the ceiling from the current equipped set and clamps current
        HP down on a drop. Defensive: entities (or test doubles) without the
        method are simply skipped, and the call never raises into the equip path.
        """
        refresh = getattr(player, "refresh_equipment_hp_max", None)
        if not callable(refresh):
            return
        try:
            refresh()
        except Exception:  # pragma: no cover - defensive
            logger.debug(
                "refresh_equipment_hp_max failed for %s",
                getattr(player, "key", "?"), exc_info=True,
            )

    @staticmethod
    def _hp_pair(player: Any) -> tuple[int, int]:
        """Return ``(hp, hp_max)`` off *player*'s ``db``, defaulting to 0."""
        db = getattr(player, "db", None)
        hp = int(getattr(db, "hp", 0) or 0)
        hp_max = int(getattr(db, "hp_max", 0) or 0)
        return hp, hp_max

    @classmethod
    def _item_matches_slot(cls, item: Any, slot: str) -> bool:
        """Return whether runtime item metadata is valid for *slot*.

        Live content mirrors ``SchemaValidator``: weapon-category items need a
        supported ``weapon_type`` and its matching split slot. Explicitly
        non-weapon items may neither declare ``weapon_type`` nor occupy either
        canonical weapon slot. Lightweight test doubles that omit ``category``
        remain supported, but a type they do declare must still agree with the
        slot.
        """
        category = cls._item_attr(item, "category", None)
        raw_weapon_type = cls._item_attr(item, "weapon_type", None)
        weapon_type = str(raw_weapon_type or "").strip().lower()
        expected_slot = WEAPON_SLOT_BY_TYPE.get(weapon_type)
        weapon_slots = frozenset(WEAPON_SLOT_BY_TYPE.values())

        if slot in weapon_slots and category is not None and category != "weapon":
            return False
        if category == "weapon":
            return expected_slot == slot
        if category is not None and raw_weapon_type is not None:
            return False
        if expected_slot is not None:
            return expected_slot == slot
        return True

    @staticmethod
    def _item_attr(item: Any, name: str, default: Any = None) -> Any:
        """Read *name* off an item robustly (property, Attribute, or dict).

        Works for a live ``GameItem`` (named properties), an Evennia object
        with an ``attributes`` handler, or a plain ``dict`` (the test/default
        item factory shape).
        """
        val = getattr(item, name, None)
        if val is not None and val != "":
            return val
        attrs = getattr(item, "attributes", None)
        if attrs is not None and hasattr(attrs, "get"):
            got = attrs.get(name, default=None)
            if got is not None:
                return got
        if isinstance(item, dict):
            return item.get(name, default)
        return val if val is not None else default

    @classmethod
    def _item_name(cls, item: Any) -> str:
        """Return a display name for *item* for notifications."""
        return (
            cls._item_attr(item, "name", None)
            or getattr(item, "key", None)
            or "item"
        )

    def _current_rank_name(self, player_level: int) -> str:
        """Resolve the player's current rank name from their level."""
        from world.systems.rank_system import rank_from_level

        rank_num = rank_from_level(player_level)
        rank = self.registry.get_rank_by_level(rank_num)
        return rank.name if rank else f"Rank {rank_num}"

    @staticmethod
    def _get_building_type(building: Any) -> str | None:
        """Read the building_type string from a building."""
        from world.utils import get_building_type
        return get_building_type(building)

    @staticmethod
    def _has_assigned_agent(building: Any) -> bool:
        """Return True if *building* has an agent assigned to it.

        Reads ``db.assigned_agent`` (an Engineer, for equipment buildings),
        tolerating the Attribute-handler and plain-attribute shapes. Passive
        production is gated on this — an agentless building is inert.
        """
        db = getattr(building, "db", None)
        if db is not None:
            agent = getattr(db, "assigned_agent", None)
            if agent is not None:
                return True
        attrs = getattr(building, "attributes", None)
        if attrs is not None and hasattr(attrs, "get"):
            return attrs.get("assigned_agent", default=None) is not None
        return False

    @staticmethod
    def _default_create_item(item_def: ItemDef, owner: Any) -> dict:
        """Default item factory — creates a simple dict representation.

        In a real Evennia environment this would use create_object to
        make a GameItem typeclass instance. For testing and lightweight
        use, returns a dict with the item's properties.
        """
        item = {
            "key": item_def.key,
            "name": item_def.name,
            "slot": item_def.slot,
            "category": item_def.category,
            "stat_modifiers": dict(item_def.stat_modifiers),
            "weapon_type": item_def.weapon_type,
            "ammo_type": item_def.ammo_type,
            "ammo_per_shot": item_def.ammo_per_shot,
            "magazine_size": item_def.magazine_size,
            "ammo_cost": dict(item_def.ammo_cost) if item_def.ammo_cost else None,
            "effect": dict(item_def.effect) if item_def.effect else None,
            "max_stack": item_def.max_stack,
            "weight": item_def.weight,
            "classification": item_def.classification,
            "required_rank": item_def.required_rank,
        }
        # A freshly produced/picked-up ranged weapon arrives with a full
        # magazine so it is usable before the first reload (Req 5.2, 11.7).
        # Defensive: only ranged weapons that declare a magazine size get a
        # loaded count; melee weapons and non-weapons never track ``loaded``.
        if item_def.weapon_type == "ranged" and item_def.magazine_size is not None:
            item["loaded"] = item_def.magazine_size
        # Add to owner's inventory if possible
        if hasattr(owner, "db") and hasattr(owner.db, "inventory"):
            inv = owner.db.inventory
            if inv is None:
                inv = []
                owner.db.inventory = inv
            inv.append(item)
        elif hasattr(owner, "_inventory"):
            owner._inventory.append(item)
        return item
