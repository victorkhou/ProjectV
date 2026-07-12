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
    DEFAULT_THROW_RANGE,
    EQUIPMENT_SLOTS,
    GEAR_CATEGORIES,
    SUPPLY_CATEGORIES,
)
from world.data_registry import DataRegistry
from world.definitions import ItemDef
from world.event_bus import EventBus
from world.systems.base_system import BaseSystem
from world.systems.equipment_carry import CarryWeightMixin
from world.systems.equipment_storage import StorageMixin

logger = logging.getLogger("mygame.equipment_system")

# Building abbreviations that run item production each tick. Armorer (AR)
# makes weapons/ammo/modern gear, Medbay (MB) makes consumables, and Lab (LB)
# makes futuristic gear/throwables (see items.yaml ``production_map``). There
# is no Armory ``AA`` building — the abbreviation is Armorer ``AR`` — so it is
# intentionally excluded (task 8.4 / 8.3).
EQUIPMENT_BUILDING_TYPES = ("AR", "MB", "LB")


class EquipmentSystem(CarryWeightMixin, StorageMixin, BaseSystem):
    """Mediates equipment production, item actions, carry weight, and storage.

    The single use-case for the equipment/weapons/special-items feature: it
    routes per-tick item production (Armorer/Medbay/Lab) into the owner's
    stores, mediates equip/unequip/use/throw/reload with rank gating, computes
    the weight-based carry cap, and moves resources between a player's
    Spend_Pool and a Storage_Building (with over-capacity spill). All
    player-facing text is emitted as ``PLAYER_NOTIFICATION`` events for the
    presenter; the system composes no strings.

    Two of those concerns live in mixins combined here (mirroring the
    ``AgentSystem`` split, same MRO/zero behavior change): the carry-weight
    model in :class:`~world.systems.equipment_carry.CarryWeightMixin` and the
    Vault/HQ storage + inflow choke point in
    :class:`~world.systems.equipment_storage.StorageMixin`.

    Args:
        registry: The DataRegistry holding item/building definitions.
        event_bus: The EventBus for publishing game events.
        create_item_func: Optional factory callable for creating item
            objects. Signature: ``(item_def, owner) -> item``.
            If not provided, uses a default that creates a simple
            dict-like item.
    """

    def __init__(
        self,
        registry: DataRegistry,
        event_bus: EventBus,
        create_item_func: Callable[[ItemDef, Any], Any] | None = None,
    ) -> None:
        super().__init__(registry, event_bus)
        self._create_item_func = create_item_func or self._default_create_item
        # Injected collaborator (composition root wires this via
        # ``set_powerup_system``) — used by ``use`` to apply consumable buffs
        # through the real timed-effect machinery rather than reaching into a
        # global service locator. Keeps ``world/systems`` framework-free.
        self._powerup_system: Any = None
        # Injected area-damage applier (composition root wires this via
        # ``set_area_damage_applier`` — see game_init, task 3.7). A zero-arg
        # callable returning the object that exposes the combat damage pipeline
        # (``_calculate_damage`` + ``_apply_damage``), i.e. the CombatEngine.
        # ``throw`` routes each AoE victim through it so target armor and the
        # min-0 clamp apply for free. Kept as an injected callable (not a
        # ``game_systems`` reach) so the layering guard stays green.
        self._area_damage_applier: Callable[[], Any] | None = None
        # Injected supply-drop spawner (composition root wires this via
        # ``set_supply_drop_spawner``). A callable ``(player, item_key, count)``
        # that re-creates a ground pickup for supply units that could not be
        # carried (over stack/weight). ``add_supply_drop`` calls it to spill the
        # leftover so supplies are never destroyed (D9). Kept as an injected
        # callable — rather than a ``game_systems``/``typeclasses`` reach at
        # module scope — so ``world/systems`` stays framework-free and the
        # layering guard stays green; when unwired the spill degrades to a log
        # (mirroring :meth:`_apply_aoe_damage` without an applier).
        self._supply_drop_spawner: Callable[[Any, str, int], Any] | None = None
        # Injected resource-drop spawner (composition root wires this via
        # ``set_resource_drop_spawner`` — see game_init, task 11.1). A callable
        # ``(holder, resource, amount)`` that spawns a ``ResourceDrop`` at the
        # holder's coords for the over-capacity remainder of an inflow into a
        # *holder pool* (a player's Spend_Pool or a Storage_Building's pool).
        # ``add_resource_capped`` calls it to spill the leftover so resources
        # are never destroyed (D9, Req 16.8). Mirrors the area-damage applier /
        # supply-drop spawner pattern: an injected callable rather than a
        # ``game_systems``/``typeclasses`` reach at module scope, so
        # ``world/systems`` stays framework-free and the layering guard stays
        # green; when unwired the spill degrades to a log.
        self._resource_drop_spawner: Callable[[Any, str, int], Any] | None = None
        # Injected gear-drop spawner (composition root wires this via
        # ``set_gear_drop_spawner``). A callable ``(building, item_def)`` that
        # spawns a unique equippable Gear ``GameItem`` as a GROUND DROP on the
        # building's tile (indexed), used by PASSIVE/agent production so produced
        # gear lands on the map for the player to ``get`` rather than teleporting
        # into their inventory. When unwired (isolated tests), the gear branch
        # falls back to the ``_create_item_func`` inventory factory.
        self._gear_drop_spawner: Callable[[Any, Any], Any] | None = None

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

    def set_area_damage_applier(self, func: Callable[[], Any]) -> None:
        """Inject the area-damage applier used by :meth:`throw`.

        *func* is a zero-arg callable returning the object that owns the combat
        damage pipeline (the :class:`~world.systems.combat_engine.CombatEngine`,
        which exposes ``_calculate_damage`` and ``_apply_damage``). Wired once
        at the composition root (``server/conf/game_init.py``, task 3.7) as
        ``set_area_damage_applier(lambda: combat_engine)`` so ``throw`` can route
        each AoE victim through the real damage formula — target
        ``damage_reduction`` armor and the min-0 clamp apply for free — without
        ``world/systems`` reaching into the ``game_systems`` service locator.
        """
        self._area_damage_applier = func

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
            # Skip offline buildings
            if getattr(building, "is_offline", False):
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

            # Rate gate: advance this building's production progress and only
            # yield on the cooldown boundary (mirrors ResourceSystem's harvest
            # cooldown). Without this a building creates an object every tick.
            progress = self._advance_production_progress(building)
            if cooldown > 1 and progress % cooldown != 0:
                continue

            # Get owner
            owner = getattr(building, "owner", None)
            if owner is None:
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

            # Tell the owner what their building made (previously silent, which
            # read as "it never produced anything").
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
        self, item_def: ItemDef, owner: Any, building: Any = None
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
                return drop is not None
            try:
                self._create_item_func(item_def, owner)
            except Exception:
                logger.exception(
                    "Failed to create gear %s for %s",
                    item_def.key, getattr(owner, "key", "?"),
                )
                return False
            return True

        # Unrecognized category — content is load-validated to one of the six,
        # so this is defensive; produce nothing rather than mis-route.
        logger.warning(
            "Cannot produce %s: unrecognized category %r",
            item_def.key, category,
        )
        return False

    # ------------------------------------------------------------------ #
    #  Mediated actions (use-case)
    # ------------------------------------------------------------------ #

    def equip(self, player: Any, item: Any) -> bool:
        """Equip a Game_Item for *player*, enforcing slot and rank gates.

        The use-case mediates the raw :class:`EquipmentHandler` store:

        1. Reject an item whose ``slot`` is not one of the canonical
           :data:`~world.constants.EQUIPMENT_SLOTS` (defensive — content is
           also slot-validated at load).
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

        # 1. Slot gate — reject items whose slot is not canonical.
        slot = self._item_attr(item, "slot", "")
        if slot not in EQUIPMENT_SLOTS:
            logger.info(
                "Rejected equip of %s: slot %r not in EQUIPMENT_SLOTS",
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
            # Unequip message first, then the equip message — the order the
            # player experiences the swap.
            if displaced is not None:
                self.notify(player, "unequipped",
                            item_name=self._item_name(displaced), slot=slot)
            self.notify(player, "equipped", item_name=item_name, slot=slot)
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
            slot = self._item_attr(item, "slot", "")
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

    def throw(self, player: Any, item_key: str, tx: int, ty: int) -> bool:
        """Throw one unit of a ``throwable`` Supply at ``(tx, ty)``.

        The use-case mediates the raw :class:`EquipmentHandler` Supply_Bag and
        the injected combat damage pipeline:

        1. Reject if the player does not hold *item_key* in their Supply_Bag
           (``handler.get_supply(item_key) <= 0``) or the item's category is
           not ``throwable`` — Req 9.6.
        2. Enforce the rank gate: if the item declares a ``required_rank`` the
           player does not meet, reject — Req 7.3 (reuses the equip gate).
        3. Enforce the throw range: the Manhattan distance from the player to
           ``(tx, ty)`` must be within the throwable's ``effect.range`` (or
           :data:`~world.constants.DEFAULT_THROW_RANGE` when the effect declares
           none) — Req 9.3.
        4. Resolve every valid target within the effect's ``radius`` (Manhattan)
           of ``(tx, ty)`` on the player's current planet via the coordinate
           index, and — when the effect type is ``aoe_damage`` — apply the
           effect's ``amount`` to each through the injected area-damage applier,
           so target armor (``damage_reduction``) and the min-0 clamp apply for
           free (Req 9.4, 9.7).
        5. Decrement the Supply_Bag by one (a throw with no valid targets still
           consumes the item and reports ``count=0``) and notify the thrower
           ``bombed`` with the number of targets hit — Req 9.5.

        A player-facing notification is emitted for every outcome; the domain
        composes no strings. Never raises into the command layer.

        Args:
            player: The throwing entity (a ``Combat_Entity``).
            item_key: The Supply item key to throw.
            tx: Target x coordinate.
            ty: Target y coordinate.

        Returns:
            ``True`` if the throwable was thrown, ``False`` otherwise.
        """
        handler = getattr(player, "equipment", None)
        item_def = self.registry.resolve_item(item_key)
        item_name = getattr(item_def, "name", None) or item_key

        # 1a. Held check (Req 9.6).
        if handler is None or handler.get_supply(item_key) <= 0:
            self.notify(
                player, "throw_failed", item_name=item_name, reason="not_held"
            )
            return False

        # 1b. Category check — only throwables are throwable (Req 9.6).
        category = getattr(item_def, "category", None) if item_def else None
        if category != "throwable":
            self.notify(
                player, "throw_failed", item_name=item_name, reason="not_throwable"
            )
            return False

        # 2. Rank gate (Req 7.3) — reuse the equip rank-gate logic.
        required_rank = getattr(item_def, "required_rank", None)
        if not self._rank_allows(player, required_rank, item_name):
            return False

        effect = getattr(item_def, "effect", None) or {}

        # 3. Throw-range gate (Req 9.3).
        try:
            throw_range = int(effect.get("range", DEFAULT_THROW_RANGE))
        except (TypeError, ValueError):
            throw_range = DEFAULT_THROW_RANGE
        from world.utils import get_coords

        p_coords = get_coords(player)
        if p_coords is None:
            self.notify(
                player, "throw_failed", item_name=item_name, reason="no_position"
            )
            return False
        distance = abs(p_coords[0] - int(tx)) + abs(p_coords[1] - int(ty))
        if distance > throw_range:
            self.notify(
                player,
                "throw_failed",
                item_name=item_name,
                reason="out_of_range",
                distance=distance,
                range=throw_range,
            )
            return False

        # 4. Resolve targets and apply AoE damage (Req 9.4, 9.7).
        try:
            radius = int(effect.get("radius", 0))
        except (TypeError, ValueError):
            radius = 0
        targets = self._resolve_throw_targets(player, int(tx), int(ty), radius)

        count = 0
        if effect.get("type") == "aoe_damage":
            try:
                amount = int(effect.get("amount", 0))
            except (TypeError, ValueError):
                amount = 0
            count = self._apply_aoe_damage(
                player, targets, amount, radius, weapon_name=item_name
            )
        else:
            # Non-damage throwables are out of scope for this feature; the
            # item is still consumed (content is load-validated to aoe_damage).
            count = 0

        # 5. Consume one unit and notify (Req 9.5). A throw with no valid
        #    targets still consumes the item and reports count=0.
        if not handler.remove_supply(item_key, 1):
            return False
        self.notify(player, "bombed", count=count, x=int(tx), y=int(ty))
        return True

    def reload(self, player: Any, *_args: Any, **_kwargs: Any) -> bool:
        """Reload the player's equipped ranged weapon from the Supply_Bag.

        Implements the magazine model (D5, Req 11): a ranged ``weapon``-slot
        Game_Item holds its loaded rounds in ``db.loaded`` (0..``magazine_size``)
        and is refilled from the counted Ammo_Type in the player's Supply_Bag.

        The use-case mediates the raw :class:`EquipmentHandler` Supply_Bag and
        the weapon's magazine state:

        1. Read the ``weapon``-slot Game_Item via
           ``player.equipment.get_equipped("weapon")``. Reject if there is no
           equipped weapon, or the weapon declares no ``ammo_type`` (it is not
           a ranged, ammo-using weapon) — ``reload_failed`` reason
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

        weapon = handler.get_equipped("weapon")

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

        # 4. Ownership gate. Read the ``owner`` property (Building exposes one),
        #    falling back to the raw Attribute/db for objects that don't.
        from world.utils import is_owner, get_building_attr
        owner = getattr(building, "owner", None)
        if owner is None:
            owner = get_building_attr(building, "owner")
        if not is_owner(player, owner):
            self.notify(player, "craft_failed", reason="not_owner",
                        item_name=item_name)
            return False

        # 5. Offline gate.
        if getattr(building, "is_offline", False):
            self.notify(player, "craft_failed", reason="building_offline",
                        item_name=item_name)
            return False

        # 6. Rank gate (shared with equip/use; emits its own equip_denied).
        if not self._rank_allows(player, item_def.required_rank, item_name):
            return False

        # 7. Resource gate.
        if not player.has_resources(craft_cost):
            from world.utils import format_insufficient_resources
            self.notify(player, "craft_failed", reason="insufficient_resources",
                        item_name=item_name,
                        breakdown=format_insufficient_resources(player, craft_cost))
            return False

        # Deduct and produce. Deduct FIRST; only route the item if the spend
        # succeeded, so a failed deduction can never mint a free item.
        if not player.deduct_resources(craft_cost):
            from world.utils import format_insufficient_resources
            self.notify(player, "craft_failed", reason="insufficient_resources",
                        item_name=item_name,
                        breakdown=format_insufficient_resources(player, craft_cost))
            return False

        if not self._route_produced_item(item_def, player):
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
        self.notify(player, "crafted", item_name=item_name,
                    category=item_def.category)
        return True

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

    def _resolve_throw_targets(
        self, player: Any, tx: int, ty: int, radius: int
    ) -> list:
        """Return valid AoE targets within *radius* (Manhattan) of ``(tx, ty)``.

        Queries the player's current planet (its coordinate index) for objects
        inside the bounding box around the target tile, then keeps only
        damageable entities (players/agents and buildings) whose Manhattan
        distance to the target is within *radius*.

        Friendly fire is intentional: the blast is indiscriminate and damages
        every player, agent, and building in radius — including the thrower's
        own agents and buildings. Only the thrower entity itself is excluded so
        a player never directly bombs their own character. (Direct weapon
        attacks reject own-building targets; a thrown explosive deliberately
        does not, so positioning matters.)
        """
        from world.utils import (
            get_coords, is_building, is_player, building_is_open,
        )

        location = getattr(player, "location", None)
        if location is None:
            return []

        x1, y1 = tx - radius, ty - radius
        x2, y2 = tx + radius, ty + radius

        candidates: list = []
        getter = getattr(location, "get_objects_in_area", None)
        if callable(getter):
            candidates = list(getter(x1, y1, x2, y2))
        else:
            idx = getattr(location, "coord_index", None)
            if idx is not None and hasattr(idx, "get_in_area"):
                candidates = list(idx.get_in_area(x1, y1, x2, y2))

        targets = []
        for obj in candidates:
            if obj is player:
                continue
            obj_is_building = is_building(obj)
            if not (is_player(obj) or obj_is_building):
                continue
            # A thrown explosive is ranged: a closed building is immune to it
            # (only adjacent melee reaches a closed building).
            if obj_is_building and not building_is_open(obj):
                continue
            coords = get_coords(obj)
            if coords is None:
                continue
            if abs(coords[0] - tx) + abs(coords[1] - ty) <= radius:
                targets.append(obj)
        return targets

    def _apply_aoe_damage(
        self,
        player: Any,
        targets: list,
        amount: int,
        radius: int,
        weapon_name: str = "Throwable",
    ) -> int:
        """Apply *amount* AoE damage to each target via the injected applier.

        Builds a :class:`SyntheticWeapon` (the same shape turrets use, mirroring the turret
        pattern) and, for each target, routes through the combat engine's
        ``_calculate_damage`` (with ``include_attacker_bonus=False`` so the
        blast deals its flat ``amount − armor`` per spec Property 12),
        ``_apply_damage``, and then ``_finalize_hit`` — the same post-damage
        resolution (combat lockout, ``COMBAT_ACTION`` event, target/owner
        notification, and defeat/destruction on HP<=0) that queued attacks use.
        The blast is indiscriminate: it damages every player, agent, and
        building in radius except the thrower, including the thrower's own
        (friendly fire is intentional).

        Returns the number of targets damaged. When no applier is injected
        (e.g. before composition-root wiring or in a lightweight test), returns
        the count of resolved targets without applying damage so the ``bombed``
        notification is still meaningful.
        """
        if not targets:
            return 0
        if self._area_damage_applier is None:
            # No applier wired (e.g. before composition-root wiring or in a
            # lightweight test): report resolved targets without dealing damage.
            return len(targets)

        try:
            engine = self._area_damage_applier()
        except Exception:  # noqa: BLE001 - never let resolution break a throw
            engine = None
        if engine is None:
            # An applier was wired but could not be resolved — in production it
            # is always wired (game_init), so log so a genuine wiring break is
            # visible rather than silently doing no damage.
            logger.warning(
                "throw: area-damage applier resolved to None; %d target(s) "
                "took no damage",
                len(targets),
            )
            return len(targets)

        from world.systems.combat_engine import SyntheticWeapon
        weapon = SyntheticWeapon(amount, radius, name=weapon_name)
        hit = 0
        for target in targets:
            try:
                # One public single-hit call resolves damage + the shared
                # post-damage handling (lockout, event, victim notification,
                # defeat/destruction). include_attacker_bonus=False so the blast
                # deals a flat amount−armor (spec Property 12); without this a
                # lethal bomb would leave a target at 0 HP but un-defeated.
                engine.apply_direct_hit(
                    player, target, weapon, include_attacker_bonus=False
                )
                hit += 1
            except Exception:  # noqa: BLE001 - one bad target must not abort the AoE
                logger.warning(
                    "throw: failed to apply AoE damage to %r",
                    getattr(target, "key", target),
                )
        return hit

    def _rank_allows(
        self, player: Any, required_rank: str | None, item_name: str
    ) -> bool:
        """Return ``True`` if *player*'s rank satisfies *required_rank*.

        Shared by :meth:`equip` and :meth:`use`. When the item declares a
        ``required_rank`` the player does not meet, emits an ``equip_denied``
        notification and returns ``False``. An unknown rank name falls open
        (returns ``True``) — content is load-validated, matching TechSystem.
        """
        if not required_rank:
            return True
        from world.utils import get_player_level

        player_level = get_player_level(player)
        try:
            req_rank_def = self.registry.get_rank_by_name(required_rank)
            from world.systems.rank_system import rank_from_level

            player_rank = rank_from_level(player_level)
            if player_rank < req_rank_def.level:
                self.notify(
                    player,
                    "equip_denied",
                    item_name=item_name,
                    required_rank=required_rank,
                    current_rank=self._current_rank_name(player_level),
                )
                return False
        except (KeyError, ImportError):
            # Unknown rank name: fall open rather than block.
            pass
        return True

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
    def _hp_pair(player: Any) -> tuple[int, int]:
        """Return ``(hp, hp_max)`` off *player*'s ``db``, defaulting to 0."""
        db = getattr(player, "db", None)
        hp = int(getattr(db, "hp", 0) or 0)
        hp_max = int(getattr(db, "hp_max", 0) or 0)
        return hp, hp_max

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
        for rank in self.registry.ranks:
            if rank.level == rank_num:
                return rank.name
        return f"Rank {rank_num}"

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
