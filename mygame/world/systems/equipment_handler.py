"""
Equipment Handler for the RTS Combat Overworld game.

Manages equipment slots on a CombatCharacter. One item per slot.
Adapted from EvAdventure's EquipmentHandler pattern.

"""

from __future__ import annotations

from world.utils import coords_of


class EquipmentHandler:
    """Manages equipment slots on a character. One item per slot.

    Stores equipped items as a dict on the character. Uses Evennia's
    Attribute system when available, with a simple dict fallback for
    testing without a running server.

    Args:
        character: The character object this handler is attached to.
    """

    def __init__(self, character) -> None:
        self.character = character

    # ------------------------------------------------------------------ #
    #  Internal storage access
    # ------------------------------------------------------------------ #

    def _get_slots(self) -> dict:
        """Return the current equipment slots dict."""
        # Try Evennia Attribute first
        if hasattr(self.character, "attributes") and hasattr(
            self.character.attributes, "get"
        ):
            slots = self.character.attributes.get("equipment_slots", default=None)
            if slots is not None:
                return dict(slots)
        # Fallback for testing: use a plain dict on the character
        if not hasattr(self.character, "_equipment_slots"):
            self.character._equipment_slots = {}
        return self.character._equipment_slots

    def _set_slots(self, slots: dict) -> None:
        """Persist the equipment slots dict."""
        # Try Evennia Attribute first
        if hasattr(self.character, "attributes") and hasattr(
            self.character.attributes, "add"
        ):
            self.character.attributes.add("equipment_slots", slots)
        # Always keep the fallback in sync
        self.character._equipment_slots = slots

    def _get_supplies(self) -> dict:
        """Return the current Supply_Bag dict (``db.supplies``).

        Mirrors :meth:`_get_slots`: reads the Evennia ``supplies`` Attribute
        when available, falling back to a plain ``_supplies`` dict on the
        character for the stubbed test environment.
        """
        # Try Evennia Attribute first
        if hasattr(self.character, "attributes") and hasattr(
            self.character.attributes, "get"
        ):
            supplies = self.character.attributes.get("supplies", default=None)
            if supplies is not None:
                return dict(supplies)
        # Fallback for testing: use a plain dict on the character
        if not hasattr(self.character, "_supplies"):
            self.character._supplies = {}
        return self.character._supplies

    def _set_supplies(self, supplies: dict) -> None:
        """Persist the Supply_Bag dict. Mirrors :meth:`_set_slots`."""
        # Try Evennia Attribute first
        if hasattr(self.character, "attributes") and hasattr(
            self.character.attributes, "add"
        ):
            self.character.attributes.add("supplies", supplies)
        # Always keep the fallback in sync
        self.character._supplies = supplies

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def equip(self, item) -> tuple[bool, str]:
        """Equip a GameItem to its slot.

        If the slot is already occupied, the existing item is auto-unequipped
        back to inventory first.

        Args:
            item: A GameItem instance with a ``slot`` property.

        Returns:
            Tuple of (success, message).
        """
        slot = getattr(item, "slot", None)
        if not slot:
            # Try reading from attributes for mock items
            if hasattr(item, "attributes") and hasattr(item.attributes, "get"):
                slot = item.attributes.get("slot", default=None)
            if not slot:
                return False, "Item has no equipment slot defined."

        slots = self._get_slots()

        # Auto-unequip existing item in this slot
        existing = slots.get(slot)
        if existing is not None:
            self.unequip(slot)
            # Re-read slots after unequip
            slots = self._get_slots()

        slots[slot] = item
        self._set_slots(slots)

        # Take the item into the wearer's possession: an item can be equipped
        # straight off the ground/tile (equip resolves via caller.search, which
        # sees the room's contents too). Moving it onto the character means that
        # when it is later unequipped it stays in the character's inventory (its
        # location is the character), so it shows in 'inventory' and can be
        # re-equipped — rather than being left on whatever tile it came from.
        self._move_item_to_character(item)

        item_name = getattr(item, "key", str(item))
        return True, f"Equipped {item_name} to {slot} slot."

    def _move_item_to_character(self, item) -> None:
        """Ensure *item* is located on this handler's character.

        Uses Evennia's ``move_to`` (quiet, no hooks) when the item and character
        support it; degrades to a direct ``location`` set, then to a no-op for
        lightweight test doubles. Never raises into the equip/unequip path.

        When the item was on a tile (equipped straight off the ground), it is
        also de-registered from that room's coordinate index — otherwise the
        worn item would linger on the map (visible to look/scan/get) as a ghost,
        because ``move_hooks=False`` skips the ``at_object_leave`` hook that
        normally clears the index.
        """
        character = self.character
        try:
            old_loc = getattr(item, "location", None)
            if old_loc is character:
                return

            # Drop the item out of its old tile's coordinate index (if any).
            self._deindex_from_tile(item, old_loc)

            if hasattr(item, "move_to"):
                item.move_to(character, quiet=True, move_hooks=False)
            elif hasattr(item, "location"):
                item.location = character
        except Exception:
            # A move failure must never break equipping; the slot dict is the
            # source of truth for what's worn.
            pass

    @staticmethod
    def _deindex_from_tile(item, room) -> None:
        """Remove *item* from *room*'s coordinate index at its current tile."""
        if room is None or not hasattr(room, "coord_index"):
            return
        coords = coords_of(item)
        if coords is None:
            return
        cx, cy, _planet = coords
        try:
            room.coord_index.remove(item, int(cx), int(cy))
        except Exception:
            pass
        # The item is no longer on a tile — clear its tile coords so a later
        # drop re-stamps them cleanly and nothing treats it as ground-placed.
        try:
            item.db.coord_x = None
            item.db.coord_y = None
        except Exception:
            pass

    def unequip(self, slot: str):
        """Unequip the item in the given slot.

        The item is returned (to be placed back in inventory by the caller).

        Args:
            slot: The equipment slot name to unequip.

        Returns:
            The unequipped GameItem, or None if the slot was empty.
        """
        slots = self._get_slots()
        item = slots.pop(slot, None)
        self._set_slots(slots)
        return item

    def get_equipped(self, slot: str):
        """Return the item equipped in the given slot, or None."""
        slots = self._get_slots()
        return slots.get(slot)

    def get_all_equipped(self) -> dict:
        """Return a dict of all equipped items: slot -> item."""
        slots = self._get_slots()
        return {k: v for k, v in slots.items() if v is not None}

    def get_stat_total(self, stat_name: str) -> float:
        """Sum a stat across all equipped items.

        Args:
            stat_name: The stat key to sum (e.g. "damage", "damage_reduction").

        Returns:
            The total value as a float.
        """
        total = 0.0
        for item in self.get_all_equipped().values():
            if hasattr(item, "get_stat"):
                total += item.get_stat(stat_name, 0)
            elif hasattr(item, "stat_modifiers"):
                mods = item.stat_modifiers
                if isinstance(mods, dict):
                    total += float(mods.get(stat_name, 0))
        return total

    def get_slot_names(self) -> list[str]:
        """Return a list of all currently occupied slot names."""
        return list(self._get_slots().keys())

    # ------------------------------------------------------------------ #
    #  Supply_Bag API (counted, fungible Supplies: ammo/consumable/throwable)
    # ------------------------------------------------------------------ #

    def get_supplies(self) -> dict[str, int]:
        """Return a copy of the Supply_Bag: ``{item_key: count}``.

        Counts are non-negative integers; depleted entries are never present
        (they are removed by :meth:`remove_supply`).
        """
        return dict(self._get_supplies())

    def get_supply(self, item_key: str) -> int:
        """Return the carried count for *item_key* (0 if not held)."""
        return int(self._get_supplies().get(item_key, 0))

    def add_supply(self, item_key: str, count: int, max_stack: int = 99) -> int:
        """Add *count* units of *item_key* to the Supply_Bag.

        Respects the per-entry ``max_stack`` cap: the entry never grows beyond
        ``max_stack``. The handler holds no definitions provider, so the caller
        (the ``EquipmentSystem``, which resolves ``Item_Def.max_stack`` via its
        provider) passes the resolved cap; it defaults to the ``ItemDef``
        default of 99 for provider-less callers/tests.

        Args:
            item_key: The Supply item key.
            count: The number of units requested to add (non-positive is a
                no-op returning 0).
            max_stack: The per-entry stack cap (positive int).

        Returns:
            The number of units actually added (0..count), after capping.
        """
        if count <= 0:
            return 0
        supplies = self._get_supplies()
        current = int(supplies.get(item_key, 0))
        room = max_stack - current
        if room <= 0:
            return 0
        added = min(count, room)
        supplies[item_key] = current + added
        self._set_supplies(supplies)
        return added

    def remove_supply(self, item_key: str, count: int) -> bool:
        """Remove *count* units of *item_key* from the Supply_Bag.

        Never underflows: if the bag holds fewer than *count* units, nothing is
        removed and ``False`` is returned. A depleted entry (count reaches 0) is
        removed from the bag entirely.

        Args:
            item_key: The Supply item key.
            count: The number of units to remove (non-positive is rejected).

        Returns:
            ``True`` if the removal succeeded, ``False`` if insufficient.
        """
        if count <= 0:
            return False
        supplies = self._get_supplies()
        current = int(supplies.get(item_key, 0))
        if current < count:
            return False
        remaining = current - count
        if remaining > 0:
            supplies[item_key] = remaining
        else:
            supplies.pop(item_key, None)
        self._set_supplies(supplies)
        return True

    def supplies_weight(self, provider) -> float:
        """Return the total carried weight of the Supply_Bag.

        Computed as ``Σ Item_Def.weight × count`` over every entry. The item
        definitions are resolved via *provider* — an explicit
        ``DefinitionsProvider`` / registry-like argument — because the handler
        holds no provider itself (keeping it framework-free).

        Args:
            provider: An object able to resolve an item key to its ``ItemDef``
                (via ``resolve_item``/``get_item``/``items``), each exposing a
                ``weight`` attribute.

        Returns:
            The total weight as a float. An item whose definition cannot be
            resolved contributes 0.
        """
        total = 0.0
        for item_key, count in self._get_supplies().items():
            item_def = self._resolve_item_def(provider, item_key)
            if item_def is None:
                continue
            weight = getattr(item_def, "weight", 0.0)
            total += float(weight) * int(count)
        return total

    @staticmethod
    def _resolve_item_def(provider, item_key: str):
        """Resolve *item_key* to an ``ItemDef`` via *provider*, or ``None``.

        Tolerates the several shapes a provider/registry may expose:
        ``resolve_item(key)``, ``get_item(key)``, or an ``items`` mapping.
        """
        if provider is None:
            return None
        resolve = getattr(provider, "resolve_item", None)
        if callable(resolve):
            return resolve(item_key)
        get_item = getattr(provider, "get_item", None)
        if callable(get_item):
            try:
                return get_item(item_key)
            except KeyError:
                return None
        items = getattr(provider, "items", None)
        if isinstance(items, dict):
            return items.get(item_key)
        return None
