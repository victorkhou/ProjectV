"""
Concrete EntityAdapters for the unified admin CRUD layer.

One module per entity type; each adapter satisfies the
``world.admin.types.EntityAdapter`` Protocol (its three segregated planes:
grammar contract, instance plane, definition plane) and is registered at
server start via ``world.admin.adapter_registry.register_all()``.

The twelve adapters and the admin surface each backs:

- :class:`~world.admin.adapters.item_adapter.ItemAdapter` — ``@item``
- :class:`~world.admin.adapters.building_adapter.BuildingAdapter` — ``@building``
- :class:`~world.admin.adapters.agent_adapter.AgentAdapter` — ``@agent``
- :class:`~world.admin.adapters.tech_adapter.TechnologyAdapter` — ``@tech``
- :class:`~world.admin.adapters.outpost_adapter.OutpostAdapter` — ``@outpost``
- :class:`~world.admin.adapters.alliance_adapter.AllianceAdapter` — ``@alliance``
- :class:`~world.admin.adapters.player_adapter.PlayerAdapter` — ``@player``
- :class:`~world.admin.adapters.stat_adapter.StatAdapter` — ``@stat``
- :class:`~world.admin.adapters.resource_adapter.ResourceAdapter` — ``@resource``
- :class:`~world.admin.adapters.powerup_adapter.PowerupAdapter` — ``@powerup``
- :class:`~world.admin.adapters.terrain_adapter.TerrainAdapter` — ``@terrain``
- :class:`~world.admin.adapters.planet_adapter.PlanetAdapter` — ``@planet``

The last three (powerup/terrain/planet) are definition-only: they inherit the
shared instance-plane opt-out stubs from
:class:`~world.admin.adapters._def_only.DefOnlyAdapter` and expose only the
``def *`` verbs.
"""

from world.admin.adapters.agent_adapter import AgentAdapter
from world.admin.adapters.alliance_adapter import AllianceAdapter
from world.admin.adapters.building_adapter import BuildingAdapter
from world.admin.adapters.item_adapter import ItemAdapter
from world.admin.adapters.outpost_adapter import OutpostAdapter
from world.admin.adapters.planet_adapter import PlanetAdapter
from world.admin.adapters.player_adapter import PlayerAdapter
from world.admin.adapters.powerup_adapter import PowerupAdapter
from world.admin.adapters.resource_adapter import ResourceAdapter
from world.admin.adapters.stat_adapter import StatAdapter
from world.admin.adapters.tech_adapter import TechnologyAdapter
from world.admin.adapters.terrain_adapter import TerrainAdapter

__all__ = [
    "AgentAdapter",
    "AllianceAdapter",
    "BuildingAdapter",
    "ItemAdapter",
    "OutpostAdapter",
    "PlanetAdapter",
    "PlayerAdapter",
    "PowerupAdapter",
    "ResourceAdapter",
    "StatAdapter",
    "TechnologyAdapter",
    "TerrainAdapter",
]
