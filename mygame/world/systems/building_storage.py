"""
Storage_Building resource pool (D8).

Gives ``storage``-capability buildings (Vault ``VT``, HQ) a real, persistent
stored-resource pool — ``db.stored_resources: dict[str, int]`` — bounded by the
building's ``storage_capacity`` (a ``BuildingDef`` field enforced here as the
upper bound).

This module is deliberately framework-free: it reads and writes the building's
persistent attribute through :func:`world.utils.get_building_attr` /
:func:`world.utils.set_building_attr` (the same safe accessors the systems use)
and resolves ``storage_capacity`` through a
:class:`~world.core.ports.definitions_provider.DefinitionsProvider`, mirroring
:func:`world.utils.building_has_capability`. Pass *provider* to inject a fake in
tests; when omitted it defaults to a provider over the live ``DataRegistry``.

The pool is distinct from any player's Spend_Pool (``db.resources``): cost checks
never read this pool. Higher layers (the ``deposit``/``withdraw`` use-case in
task 9.5 and the inflow choke point in task 9.2) build on these helpers.
"""

from __future__ import annotations

from typing import Any

from world.utils import (
    get_building_attr,
    get_building_type,
    set_building_attr,
)

#: The persistent building attribute holding the stored-resource pool.
_POOL_ATTR = "stored_resources"


# ------------------------------------------------------------------ #
#  Capacity resolution
# ------------------------------------------------------------------ #

def get_storage_capacity(building: Any, provider: Any = None) -> int:
    """Resolve *building*'s ``storage_capacity`` via its ``BuildingDef``.

    Resolves the building's ``building_type`` through a
    :class:`DefinitionsProvider` (defaulting to one over the live
    ``DataRegistry``) and reads ``storage_capacity`` off the resolved
    ``BuildingDef``. Returns ``0`` when the type is unknown, no provider is
    available, or the value is missing/invalid. A capacity of ``0`` means
    "no storage" — never "unlimited" — so enforcement is never silently
    disabled (Req 16.2).
    """
    btype = get_building_type(building)
    if not btype:
        return 0
    try:
        if provider is None:
            from world.adapters.registry_definitions_provider import (
                default_definitions_provider,
            )
            provider = default_definitions_provider()
        if provider is None:
            return 0
        bdef = provider.resolve_building(btype)
    except Exception:
        return 0
    if bdef is None:
        return 0
    try:
        return max(0, int(getattr(bdef, "storage_capacity", 0) or 0))
    except (TypeError, ValueError):
        return 0


# ------------------------------------------------------------------ #
#  Pool access
# ------------------------------------------------------------------ #

def _get_pool(building: Any) -> dict[str, int]:
    """Return a plain-dict copy of the building's stored-resource pool.

    Copying avoids mutating an Evennia saver-dict in place; callers mutate the
    copy and persist it via :func:`_set_pool`. Mirrors the read-copy-write idiom
    used by ``EquipmentHandler``.
    """
    pool = get_building_attr(building, _POOL_ATTR, None)
    if not pool:
        return {}
    return {str(k): int(v) for k, v in dict(pool).items()}


def _set_pool(building: Any, pool: dict[str, int]) -> None:
    """Persist the stored-resource pool back onto the building."""
    set_building_attr(building, _POOL_ATTR, pool)


def get_stored_pool(building: Any) -> dict[str, int]:
    """Return a copy of the building's stored-resource pool (``{}`` if empty)."""
    return _get_pool(building)


def get_stored(building: Any, resource: str) -> int:
    """Return the stored amount of *resource* (``0`` if none)."""
    return _get_pool(building).get(resource, 0)


def get_total_stored(building: Any) -> int:
    """Return the total number of units stored across all resource types."""
    return sum(_get_pool(building).values())


def get_remaining_capacity(building: Any, provider: Any = None) -> int:
    """Return the free storage room = ``storage_capacity - total stored`` (≥ 0)."""
    capacity = get_storage_capacity(building, provider)
    return max(0, capacity - get_total_stored(building))


# ------------------------------------------------------------------ #
#  Mutators
# ------------------------------------------------------------------ #

def deposit_to_building(
    building: Any, resource: str, amount: int, provider: Any = None
) -> int:
    """Add up to remaining capacity of *resource* to the pool.

    Adds ``min(amount, remaining_capacity)`` and returns the amount actually
    stored. A non-positive *amount* (or a full building) stores nothing and
    returns ``0``. The pool total can never exceed ``storage_capacity``.
    """
    if amount is None or amount <= 0:
        return 0
    room = get_remaining_capacity(building, provider)
    if room <= 0:
        return 0
    to_store = min(int(amount), room)
    pool = _get_pool(building)
    pool[resource] = pool.get(resource, 0) + to_store
    _set_pool(building, pool)
    return to_store


def withdraw_from_building(building: Any, resource: str, amount: int) -> int:
    """Remove up to the available amount of *resource* from the pool.

    Removes ``min(amount, stored[resource])`` and returns the amount actually
    withdrawn. A non-positive *amount* (or an empty entry) withdraws nothing and
    returns ``0``. An entry drained to zero is dropped from the pool.
    """
    if amount is None or amount <= 0:
        return 0
    pool = _get_pool(building)
    available = pool.get(resource, 0)
    if available <= 0:
        return 0
    to_take = min(int(amount), available)
    remaining = available - to_take
    if remaining > 0:
        pool[resource] = remaining
    else:
        pool.pop(resource, None)
    _set_pool(building, pool)
    return to_take
