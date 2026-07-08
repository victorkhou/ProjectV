"""
Carry-weight model for the equipment/items feature (D7).

``CarryWeightMixin`` holds the weight-based carry-capacity logic — the total
weight a holder carries, their limit, and the count-by-weight conversion used
by resource inflow and supply pickup. It is combined into ``EquipmentSystem``
via inheritance (mirroring the ``AgentSystem`` mixin split), so the public API
and every ``self.`` call-site are unchanged; the concern simply lives in a file
you can hold in your head. Depends on ``self.registry``/``self.notify`` from
``BaseSystem`` and is combined with ``StorageMixin`` (which calls these methods).

"""

from __future__ import annotations

import logging
import math
from typing import Any

from world.constants import BASE_CARRY_WEIGHT, DEFAULT_RESOURCE_WEIGHT

logger = logging.getLogger("mygame.equipment_system")


class CarryWeightMixin:
    """Weight-based carry capacity (D7): carried weight, limit, and fit math."""

    def carried_weight(self, player: Any) -> float:
        """Return *player*'s total carried weight (Supplies + on-person resources).

        Implements the weight-based carry model (D7, Req 15.4): the carried
        weight spans two stores the handler does not jointly own, so it is
        computed here in the use-case rather than on the handler —

            ``carried_weight = handler.supplies_weight(provider)
              + Σ(resource_weights[type] × player.db.resources[type])``

        Equipped **Gear is excluded** by design (worn, not hauled); only the
        Supply_Bag and the on-person Spend_Pool (``player.db.resources``)
        contribute. Supply weights are resolved by the handler through the
        registry (the ``DefinitionsProvider`` passed as ``provider``). Resource
        weights come from :attr:`BalanceConfig.resource_weights`; a resource
        absent from that map uses :data:`~world.constants.DEFAULT_RESOURCE_WEIGHT`.
        Lookups are keyed by the canonical title-case ``RESOURCE_TYPES`` (the
        case invariant — ``add_resource``/``get_resource`` already title-case).

        Args:
            player: The holder whose carried weight is measured.

        Returns:
            The total carried weight as a float.
        """
        total = 0.0

        # Supply_Bag weight — resolved via the registry as the provider.
        handler = getattr(player, "equipment", None)
        if handler is not None:
            supplies_weight = getattr(handler, "supplies_weight", None)
            if callable(supplies_weight):
                total += float(supplies_weight(self.registry))

        # On-person resource (Spend_Pool) weight.
        weights = getattr(self.registry.balance, "resource_weights", None) or {}
        for rtype, amount in self._player_resources(player).items():
            key = str(rtype).title()
            weight = weights.get(key, DEFAULT_RESOURCE_WEIGHT)
            try:
                total += float(weight) * int(amount)
            except (TypeError, ValueError):
                continue
        return total

    def carry_limit(self, player: Any) -> float:
        """Return *player*'s carry-weight limit (Req 15.5, 15.6).

        Admins (Builder+) are exempt and carry an unbounded amount (``∞``).
        Every other holder's limit is
        ``BASE_CARRY_WEIGHT + get_stat_total("carry_capacity")`` — the base
        capacity plus any bonus from equipped ``carry_capacity`` Gear (e.g. a
        hauler pack).

        Args:
            player: The holder whose limit is computed.

        Returns:
            ``float('inf')`` for admins, else the finite limit as a float.
        """
        from world.utils import is_admin

        if is_admin(player):
            return float("inf")

        capacity = 0.0
        handler = getattr(player, "equipment", None)
        if handler is not None:
            get_stat_total = getattr(handler, "get_stat_total", None)
            if callable(get_stat_total):
                try:
                    capacity = float(get_stat_total("carry_capacity"))
                except (TypeError, ValueError):
                    capacity = 0.0
        return float(BASE_CARRY_WEIGHT) + capacity

    def _resource_weight_room(self, player: Any, resource: str) -> float:
        """Return the room (in resource count) for *resource* by carry weight.

        Converts *player*'s remaining carry-weight budget into a number of units
        of *resource* that still fit, mirroring the count-by-weight conversion
        used by ``_add_resource_capped_player``:

            ``weight_room = carry_limit(player) − carried_weight(player)``
            ``room = floor(weight_room / resource_weight)``

        Returns ``float('inf')`` when weight is not a binding constraint — an
        admin's unbounded ``carry_limit`` or a non-positive per-unit resource
        weight — and ``0`` when the player is already at/over the limit. The
        per-unit weight is resolved title-case keyed from
        :attr:`BalanceConfig.resource_weights`, defaulting to
        :data:`~world.constants.DEFAULT_RESOURCE_WEIGHT`.
        """
        weights = getattr(self.registry.balance, "resource_weights", None) or {}
        try:
            resource_weight = float(
                weights.get(str(resource).title(), DEFAULT_RESOURCE_WEIGHT)
            )
        except (TypeError, ValueError):
            resource_weight = float(DEFAULT_RESOURCE_WEIGHT)

        return self._units_that_fit(player, resource_weight)

    def _units_that_fit(self, player: Any, unit_weight: float) -> float:
        """How many units of the given *unit_weight* fit in *player*'s carry room.

        The single count-by-weight conversion shared by resource inflow
        (``_resource_weight_room``) and supply pickup (``add_supply_drop``)::

            room = floor((carry_limit − carried_weight) / unit_weight)

        Returns ``float('inf')`` when weight is not a binding constraint (an
        admin's unbounded ``carry_limit`` or a non-positive *unit_weight*) and
        ``0`` when the player is already at/over the limit. A small epsilon
        absorbs float-representation error so an exact fit is not under-counted.
        """
        weight_room = self.carry_limit(player) - self.carried_weight(player)
        if weight_room == float("inf") or unit_weight <= 0:
            return float("inf")
        if weight_room <= 0:
            return 0
        return math.floor(weight_room / unit_weight + 1e-9)

    @staticmethod
    def _get_player_resource(player: Any, resource: str) -> int:
        """Return *player*'s held amount of *resource* from their Spend_Pool.

        Prefers the ``get_resource`` accessor (which title-cases the key, like
        ``add_resource``/``deduct_resources``) and falls back to reading
        ``db.resources`` directly for the dict-shaped test fake. Returns ``0``
        when the resource is absent or unreadable.
        """
        getter = getattr(player, "get_resource", None)
        if callable(getter):
            try:
                return int(getter(resource))
            except (TypeError, ValueError):
                return 0
        db = getattr(player, "db", None)
        resources = getattr(db, "resources", None) if db is not None else None
        if not resources:
            return 0
        try:
            return int(dict(resources).get(str(resource).title(), 0))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _player_resources(player: Any) -> dict:
        """Return *player*'s on-person resource pool (``db.resources``) as a dict.

        Reads the Spend_Pool dict off ``player.db.resources`` robustly,
        returning an empty dict when the player has no resources attribute or
        it is unset. The returned mapping is only iterated (never mutated).
        """
        db = getattr(player, "db", None)
        if db is None:
            return {}
        resources = getattr(db, "resources", None)
        if not resources:
            return {}
        try:
            return dict(resources)
        except (TypeError, ValueError):
            return {}
