"""Equipment-slot compatibility helpers.

The weapon-slot split changed the persisted vocabulary from ``weapon`` to
``weapon_melee`` / ``weapon_ranged``. Existing ``GameItem`` objects keep a
copy of their definition fields in Evennia Attributes, and characters keep a
persistent ``equipment_slots`` mapping, so changing YAML alone cannot migrate
live data. This module provides framework-free, duck-typed helpers used by
both typeclasses and systems to repair an item lazily while preserving the
same object (ammo, rolls, rarity, affixes, inserts, and damage type included).
"""

from __future__ import annotations

from typing import Any

from world.constants import LEGACY_WEAPON_SLOT, WEAPON_SLOT_BY_TYPE


def _read_item_attr(item: Any, name: str, default: Any = None) -> Any:
    """Read a persisted/item field across live objects and lightweight fakes."""
    if item is None:
        return default
    if isinstance(item, dict):
        return item.get(name, default)

    attributes = getattr(item, "attributes", None)
    if attributes is not None and hasattr(attributes, "get"):
        try:
            value = attributes.get(name, default=default)
        except TypeError:
            value = attributes.get(name, default)
        if value is not None:
            return value

    db = getattr(item, "db", None)
    if db is not None:
        try:
            value = getattr(db, name, None)
        except Exception:
            value = None
        if value is not None:
            return value

    try:
        return getattr(item, name, default)
    except Exception:
        return default


def _write_item_attr(item: Any, name: str, value: Any) -> None:
    """Persist one compatibility field on a live item or test double."""
    if item is None:
        return
    if isinstance(item, dict):
        item[name] = value
        return

    attributes = getattr(item, "attributes", None)
    if attributes is not None and hasattr(attributes, "add"):
        try:
            attributes.add(name, value)
            return
        except Exception:
            pass

    db = getattr(item, "db", None)
    if db is not None:
        try:
            setattr(db, name, value)
            return
        except Exception:
            pass

    try:
        setattr(item, name, value)
    except Exception:
        # A read-only property on a non-Evennia fake has nowhere durable to
        # write. Callers still receive the canonical value for this access.
        pass


def _normalise_weapon_type(value: Any) -> str | None:
    """Return a supported lower-case weapon type, or ``None``."""
    normalised = str(value or "").strip().lower()
    return normalised if normalised in WEAPON_SLOT_BY_TYPE else None


def _safe_item_def(item: Any) -> Any:
    """Return the item's current definition, or ``None`` if unavailable."""
    try:
        return getattr(item, "item_def", None)
    except Exception:
        return None


def _legacy_stat_range(item: Any) -> float:
    """Return the item's (or its definition's) ``range`` stat, else ``0``."""
    for stats in (
        _read_item_attr(item, "stat_modifiers", None),
        getattr(_safe_item_def(item), "stat_modifiers", None),
    ):
        if isinstance(stats, dict) and stats.get("range") is not None:
            try:
                return float(stats["range"])
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def _legacy_weapon_type(item: Any) -> str:
    """Infer the type of an item carrying the legacy singular weapon slot.

    Ordered by how much the source is trusted: the item's own persisted type,
    then its current definition's, then observable traits. A magazine or an
    ammo cost identifies a ranged weapon, and so does a reach beyond one tile —
    a legacy weapon that struck from a distance must not be typed melee, since
    ``CombatEngine._resolve_weapon_range`` hard-forces melee reach to 1 and the
    same-tile melee gate would then silently collapse the weapon's range.
    """
    weapon_type = _normalise_weapon_type(
        _read_item_attr(item, "weapon_type", None)
    )
    if weapon_type is not None:
        return weapon_type

    item_def = _safe_item_def(item)
    weapon_type = _normalise_weapon_type(
        getattr(item_def, "weapon_type", None) if item_def else None
    )
    if weapon_type is not None:
        return weapon_type

    ranged_hint = any(
        _read_item_attr(item, field, None) not in (None, {}, "")
        for field in ("ammo_type", "magazine_size", "ammo_cost")
    ) or _legacy_stat_range(item) > 1
    return "ranged" if ranged_hint else "melee"


def weapon_slot_for_item(item: Any, *, stored_slot: str | None = None) -> str:
    """Return the canonical slot for *item*, migrating legacy ``weapon``.

    ``weapon_type`` is authoritative. If a very old object lacks a valid type,
    the current ``ItemDef`` is consulted; failing that, magazine/resource-ammo
    traits or a reach beyond one tile identify a ranged weapon. A weapon with
    no distance evidence at all defaults to melee rather than being left as an
    unusable ghost item.

    The inferred ``weapon_type`` is persisted deliberately: the runtime slot
    gate (``EquipmentSystem._item_matches_slot``) derives the expected slot
    from it, so a weapon-category item left untyped could no longer be
    re-equipped into the slot it was just migrated into. The trade-off is that
    a later definition fix cannot retroactively retype an already-migrated
    instance; only items with no type anywhere are affected.

    Only the compatibility fields ``slot`` and, when needed, ``weapon_type``
    are written. The item's identity and all per-instance state are untouched,
    and subsequent calls make no further changes.
    """
    slot = stored_slot
    if slot is None:
        slot = _read_item_attr(item, "slot", "")
    slot = str(slot or "")
    if slot != LEGACY_WEAPON_SLOT:
        return slot

    weapon_type = _legacy_weapon_type(item)
    destination = WEAPON_SLOT_BY_TYPE[weapon_type]
    _write_item_attr(item, "weapon_type", weapon_type)
    _write_item_attr(item, "slot", destination)
    return destination
