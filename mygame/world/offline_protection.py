"""
Offline Building Protection for the RTS Combat Overworld game.

Provides helper functions that CombatCharacter hooks call to transition
buildings between online and offline states. Also provides query functions
for the combat engine and movement system to check offline status.

Requirements: 10.1, 10.2, 10.3, 10.4, 10.5
"""

from __future__ import annotations

import logging
from typing import Any

from world.event_bus import PLAYER_LOGIN, PLAYER_LOGOUT, EventBus

logger = logging.getLogger("mygame.offline_protection")


def on_player_logout(player: Any, buildings: list | None = None) -> list:
    """Transition all player buildings to offline state.

    Called from CombatCharacter.at_pre_unpuppet.

    Args:
        player: The player logging out.
        buildings: Optional list of buildings to transition. If None,
            attempts to get buildings from player.get_buildings().

    Returns:
        List of buildings that were set offline.
    """
    if buildings is None:
        buildings = _get_player_buildings(player)

    transitioned = []
    for building in buildings:
        if hasattr(building, "set_offline"):
            building.set_offline(True)
            transitioned.append(building)
        elif hasattr(building, "attributes") and hasattr(building.attributes, "add"):
            building.attributes.add("offline", True)
            transitioned.append(building)

    logger.info(
        "Player %s logged out — set %d buildings offline",
        getattr(player, "key", "?"), len(transitioned),
    )
    return transitioned


def on_player_login(player: Any, buildings: list | None = None) -> list:
    """Transition all player buildings back to online state.

    Called from CombatCharacter.at_post_login.

    Args:
        player: The player logging in.
        buildings: Optional list of buildings to transition. If None,
            attempts to get buildings from player.get_buildings().

    Returns:
        List of buildings that were set online.
    """
    if buildings is None:
        buildings = _get_player_buildings(player)

    transitioned = []
    for building in buildings:
        if hasattr(building, "set_offline"):
            building.set_offline(False)
            transitioned.append(building)
        elif hasattr(building, "attributes") and hasattr(building.attributes, "add"):
            building.attributes.add("offline", False)
            transitioned.append(building)

    logger.info(
        "Player %s logged in — set %d buildings online",
        getattr(player, "key", "?"), len(transitioned),
    )
    return transitioned


def is_building_offline(building: Any) -> bool:
    """Check if a building is in offline protection state.

    Args:
        building: The building to check.

    Returns:
        True if the building is offline.
    """
    if hasattr(building, "is_offline"):
        return bool(building.is_offline)
    if hasattr(building, "attributes") and hasattr(building.attributes, "get"):
        return bool(building.attributes.get("offline", default=False))
    return False


def can_damage_building(building: Any) -> bool:
    """Check if a building can receive damage.

    Offline buildings cannot be damaged.

    Args:
        building: The building to check.

    Returns:
        True if the building can receive damage.
    """
    return not is_building_offline(building)


def can_enter_tile_with_building(building: Any) -> bool:
    """Check if a tile with this building can be entered by other players.

    Tiles with offline buildings block entry.

    Args:
        building: The building on the tile.

    Returns:
        True if the tile can be entered.
    """
    return not is_building_offline(building)


def is_production_suspended(building: Any) -> bool:
    """Check if production is suspended for this building.

    Offline buildings do not produce resources or equipment.

    Args:
        building: The building to check.

    Returns:
        True if production is suspended.
    """
    return is_building_offline(building)


# ------------------------------------------------------------------ #
#  Internal helpers
# ------------------------------------------------------------------ #

def _get_player_buildings(player: Any) -> list:
    """Get all buildings owned by the player."""
    if hasattr(player, "get_buildings"):
        return player.get_buildings()
    return []
