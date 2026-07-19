"""
Shared constants and derived lookups for the Agent subsystem.

Extracted into a leaf module so the AgentSystem facade and its mixins
(``agent_progression``, ``agent_behavior``) can all import these without an
import cycle. Role/ability metadata is defined once in
``typeclasses.agent_scripts`` (``AGENT_ROLES`` / ``AGENT_ABILITIES``); the maps
below are DERIVED from those tables so they cannot drift.
"""

from __future__ import annotations

import logging

from typeclasses.agent_scripts import AGENT_ROLES as _AGENT_ROLES
from typeclasses.agent_scripts import AGENT_ABILITIES as _AGENT_ABILITIES

logger = logging.getLogger("mygame.agent_system")

#: Valid role identifiers for PLAYER assignment (order preserved from the
#: table). Hidden roles — placeholder scripts not yet implemented — are
#: excluded (early-game rebalance R6); see ALL_ROLES for the unfiltered set.
VALID_ROLES: tuple[str, ...] = tuple(
    spec.name for spec in _AGENT_ROLES.values() if not spec.hidden
)

#: Every role including hidden ones — for admin/test assignment paths (R6.3).
ALL_ROLES: tuple[str, ...] = tuple(_AGENT_ROLES.keys())

#: Building abbreviation → the agent role that building requires.
BUILDING_ROLE_MAP: dict[str, str] = {
    abbr: spec.name
    for spec in _AGENT_ROLES.values()
    for abbr in spec.buildings
}

#: Roles that belong to the army and do NOT require a target building.
ARMY_ROLES: tuple[str, ...] = tuple(
    spec.name for spec in _AGENT_ROLES.values() if spec.army
)

# Maps an XP source key → the BalanceConfig attribute holding its amount.
# Death loss is handled separately (it uses ``agent_xp_death_loss``).
# An unknown source key resolves to no field → 0 amount → no-op award.
AGENT_XP_SOURCE_FIELDS: dict[str, str] = {
    "harvest": "agent_xp_harvest",
    "delivery": "agent_xp_delivery",
    "construction": "agent_xp_construction",
    "combat": "agent_xp_combat",
    "time_served": "agent_xp_time_served",
}

#: Maps a gated ability Script class name → its Evennia ``key`` (derived from
#: the ability table). Script subclasses set ``key`` in ``at_script_creation``
#: (not as a class attribute), so this lets the attach/detach helpers match
#: scripts by key without instantiating the class (which silently fails outside
#: the Evennia DB context).
ABILITY_SCRIPT_KEYS: dict[str, str] = {
    spec.script.__name__: spec.script_key for spec in _AGENT_ABILITIES.values()
}
