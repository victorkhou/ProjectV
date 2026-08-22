"""Player-facing readout of what a building still needs.

The onboarding directives name buildings ("Build a Munitions Plant"), but the
gates on those buildings live in buildings.yaml — a level, sometimes a deed,
always a resource cost. Without surfacing them, an objective the player cannot
yet afford reads as a bug: the chain sits on it and says nothing.

:func:`unmet_requirements` answers "why can't I build this yet?" and
:func:`requirement_note` renders the answer as a short parenthetical for a
notification or list row. Both return empty when the player can build the thing,
so a reachable objective stays uncluttered.

Never raises — a missing registry, an odd player double, or malformed definition
data yields "no known requirements" rather than breaking the caller.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("evennia.world.build_requirements")


def _resolve_def(abbr: str, registry: Any = None) -> Any | None:
    """Return the BuildingDef for *abbr*, or None."""
    if not abbr:
        return None
    reg = registry
    if reg is None:
        from world import services
        reg = services.get_service("registry")
    if reg is None:
        return None
    resolver = getattr(reg, "resolve_building", None)
    if callable(resolver):
        bdef = resolver(abbr)
        if bdef is not None:
            return bdef
    buildings = getattr(reg, "buildings", None) or {}
    return buildings.get(abbr) or buildings.get(str(abbr).upper())


def _build_cost(bdef: Any, player: Any) -> dict:
    """The cost to build *bdef* for *player*, research discount included.

    Prefers ``BuildingSystem.get_build_cost`` so a quoted figure matches what
    the build command will actually charge; falls back to the raw definition
    cost when the system is unavailable.
    """
    try:
        from world import services
        system = services.get_service("building_system")
        if system is not None and hasattr(system, "get_build_cost"):
            return dict(system.get_build_cost(bdef, player) or {})
    except Exception:  # noqa: BLE001 - fall back to the definition cost
        pass
    return dict(getattr(bdef, "cost", None) or {})


def unmet_requirements(player: Any, abbr: str, registry: Any = None) -> list[str]:
    """Return the gates on building *abbr* that *player* does not yet meet.

    Each entry is a short player-facing phrase, ordered by how blocking it is:
    the level gate first (time-bound), then the deed gate (action-bound), then
    the resource shortfall (the most transient). An empty list means the player
    can build it now.

    Deliberately mirrors the gates ``BuildingSystem._validate_construction``
    enforces for AVAILABILITY, and skips the per-tile ones (terrain, occupancy,
    build range) — those depend on where the player is standing, not on whether
    the building is open to them.
    """
    missing: list[str] = []
    try:
        bdef = _resolve_def(abbr, registry)
        if bdef is None:
            return []

        db = getattr(player, "db", None)

        # Level gate.
        from world.utils import get_player_level
        level = get_player_level(player, default=1)
        try:
            required_level = int(getattr(bdef, "rank_requirement", 1) or 1)
        except (TypeError, ValueError):
            required_level = 1
        if level < required_level:
            missing.append(f"level {required_level} (you are {level})")

        # Deed gate.
        deed = getattr(bdef, "unlock_deed", None)
        if deed:
            try:
                required = int(getattr(bdef, "unlock_deed_count", 1) or 1)
            except (TypeError, ValueError):
                required = 1
            deeds = getattr(db, "deeds", None) or {}
            have = int(deeds.get(deed, 0) or 0) if isinstance(deeds, dict) else 0
            if have < required:
                from world.constants import DEED_DESCRIPTIONS
                desc = DEED_DESCRIPTIONS.get(deed, deed)
                if required > 1:
                    missing.append(f"{desc} ×{required} ({have}/{required})")
                else:
                    missing.append(str(desc))

        # Resource shortfall — only the resources actually short, so a nearly
        # affordable building reports the one thing to go gather.
        if hasattr(player, "get_resource"):
            short = []
            for resource, amount in _build_cost(bdef, player).items():
                try:
                    need = int(amount or 0)
                except (TypeError, ValueError):
                    continue
                if need <= 0:
                    continue
                have = int(player.get_resource(resource) or 0)
                if have < need:
                    short.append(f"{need} {resource} (have {have})")
            if short:
                missing.append(", ".join(short))
        return missing
    except Exception:  # noqa: BLE001 - an annotation never breaks its caller
        logger.debug("Requirement check failed for %r", abbr, exc_info=True)
        return []


def requirement_note(player: Any, abbr: str, registry: Any = None) -> str:
    """Render :func:`unmet_requirements` as a ``" — needs …"`` suffix.

    A dash lead-in rather than a parenthetical, because the individual gates
    already carry their own parentheses ("level 6 (you are 4)") and nesting them
    inside another pair reads badly.

    Returns ``""`` when the player can build *abbr* now, so callers can append
    it unconditionally.
    """
    missing = unmet_requirements(player, abbr, registry)
    if not missing:
        return ""
    return f" |y— needs {'; '.join(missing)}|n"


def directive_building(directive: dict) -> str | None:
    """The building abbreviation a directive step asks the player to have.

    Read from the step's explicit ``requires_building``, falling back to a
    ``condition.building_type`` (which the build/upgrade steps already carry).
    ``None`` for steps that need no building.
    """
    if not isinstance(directive, dict):
        return None
    explicit = directive.get("requires_building")
    if explicit:
        return str(explicit)
    condition = directive.get("condition") or {}
    btype = condition.get("building_type") if isinstance(condition, dict) else None
    return str(btype) if btype else None
