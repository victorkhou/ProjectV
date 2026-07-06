"""
Equipment Handler for the RTS Combat Overworld game.

Manages equipment slots on a CombatCharacter. One item per slot.
Adapted from EvAdventure's EquipmentHandler pattern.

"""

from __future__ import annotations


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
        item_name = getattr(item, "key", str(item))
        return True, f"Equipped {item_name} to {slot} slot."

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
