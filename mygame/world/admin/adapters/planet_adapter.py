"""
PlanetAdapter — the NEW ``@planet`` def-READ-only EntityAdapter
(unified-admin-crud Phase 4, task 8.2).

Planets had no admin surface at all; this adapter gives them a
read-only definition surface under the unified grammar (Requirement 7.5,
design Component 6):

- **Read-only def scope**: ``planets.yaml`` loads into a separate
  :class:`~world.coordinate.planet_registry.PlanetRegistry` and is NOT
  part of ``DataRegistry.reload_all`` — so planets are not hot-reloadable.
  Only ``def list`` and ``def show`` are supported, served straight from
  the PlanetRegistry. Every WRITE verb — ``def set``/``def reset`` — and
  ``def diff`` (there is no overlay for planets) are opted out with the
  reason "planets are not hot-reloadable; edit planets.yaml and restart".
- **No instances**: a planet is a coordinate-space definition, not an
  admin-owned object, so every instance verb (``list``/``spawn``/
  ``show``/``set``/``destroy``) is opted out too, pointing at the def
  scope.
- **No overlay / no def_domain**: this adapter deliberately declares no
  ``def_domain``; the router's ``_def_domain()`` returns None, so
  ``def show`` performs no overlay lookup (planets have no overrides) and
  the def-write context is unreachable (guarded by the opt-outs anyway).

Registry access is LAZY (services facade) so constructing and registering
the adapter never needs a booted server; tests inject a ``registry``
double exposing ``list_planets``/``get_space``/``resolve_planet``.
"""

from __future__ import annotations

from typing import Any

from world.admin.adapters._def_only import DefOnlyAdapter
from world.admin.adapters._support import live_service
from world.admin.types import FieldSpec

#: The single "not hot-reloadable" reason, surfaced verbatim by the router
#: for every def-write verb (Requirements 1.5, 7.5). Also stated in help.
_NOT_HOT_RELOADABLE = (
    "planets are not hot-reloadable; edit planets.yaml and restart"
)

#: The instance-verb opt-out reasons (Requirement 7.5), each pointing at
#: the supported read-only def scope.
_LIST_REASON = (
    "planets are definition-only — there are no planet instances to "
    "list; use 'def list' to see the planet definitions"
)
_SPAWN_REASON = (
    "planets are defined in planets.yaml, not spawned; " + _NOT_HOT_RELOADABLE
)
_SHOW_REASON = (
    "planets have no per-instance admin surface; use 'def show <key>' "
    "to inspect a planet definition"
)
_SET_REASON = (
    "planets have no modifiable per-instance fields; " + _NOT_HOT_RELOADABLE
)
_DESTROY_REASON = (
    "planets are definition-only and cannot be destroyed; "
    + _NOT_HOT_RELOADABLE
)


class PlanetAdapter(DefOnlyAdapter):
    """EntityAdapter for planets (the ``@planet`` def-read-only surface).

    The instance plane (``list``/``spawn``/``show``/``set``/``destroy``) is
    opted out and inherited from :class:`DefOnlyAdapter` as unreachable
    stubs; this class declares only the grammar contract and the
    read-only definition scope (``def set``/``def reset``/``def diff`` are
    opted out too — planets are not hot-reloadable).

    Tests may inject a PlanetRegistry double via ``registry``; production
    resolves it lazily from the services facade under ``planet_registry``.
    """

    entity_key = "planet"
    # NOTE: no ``def_domain`` — planets are not in the overlay/reload
    # pipeline (design Component 6), so the router does no overlay work.

    # --- grammar contract (design per-entity matrix row for @planet) ---
    supported_verbs = frozenset({"def list", "def show"})
    opt_outs: dict[str, str] = {
        # Instance plane — no planet instances.
        "list": _LIST_REASON,
        "spawn": _SPAWN_REASON,
        "show": _SHOW_REASON,
        "set": _SET_REASON,
        "destroy": _DESTROY_REASON,
        # Definition writes + diff — not hot-reloadable, no overlay.
        "def set": _NOT_HOT_RELOADABLE,
        "def reset": _NOT_HOT_RELOADABLE,
        "def diff": _NOT_HOT_RELOADABLE,
    }
    extra_verbs: dict[str, str] = {}
    aliases: dict[str, str] = {}

    def __init__(self, registry: Any | None = None) -> None:
        self._registry = registry

    # ------------------------------------------------------------------ #
    #  Registry access (lazy — no live game required)
    # ------------------------------------------------------------------ #

    def _planet_registry(self) -> Any | None:
        """Injected double, else the services-facade PlanetRegistry."""
        return live_service("planet_registry", self._registry)

    # ------------------------------------------------------------------ #
    #  Field schemas
    # ------------------------------------------------------------------ #

    def definition_fields(self) -> dict[str, FieldSpec]:
        """No settable definition fields — ``def set`` is opted out
        (planets are not hot-reloadable)."""
        return {}

    # ------------------------------------------------------------------ #
    #  Definition scope (read-only)
    # ------------------------------------------------------------------ #

    def def_registry_dict(self) -> dict | None:
        """A ``{planet_key: CoordinateSpaceDef}`` view built from the
        PlanetRegistry's public API (``list_planets`` + ``get_space``), so
        ``def list`` renders straight from the live registry without
        touching its private ``_spaces`` store."""
        registry = self._planet_registry()
        if registry is None:
            return None
        list_planets = getattr(registry, "list_planets", None)
        get_space = getattr(registry, "get_space", None)
        if not callable(list_planets) or not callable(get_space):
            return None
        result: dict = {}
        for key in list_planets():
            try:
                result[key] = get_space(key)
            except Exception:  # noqa: BLE001 - skip an unreadable planet
                continue
        return result

    def def_resolve(self, token: str) -> Any | None:
        """Resolve a planet token to its :class:`CoordinateSpaceDef` via
        the PlanetRegistry's existing ``resolve_planet`` matcher (exact
        key / z-level / case-insensitive / unambiguous prefix →
        canonical key), then ``get_space``. Falls back to an exact
        ``get_space`` for doubles without ``resolve_planet``."""
        registry = self._planet_registry()
        if registry is None:
            return None
        token = str(token or "").strip()
        if not token:
            return None
        get_space = getattr(registry, "get_space", None)
        if not callable(get_space):
            return None
        resolver = getattr(registry, "resolve_planet", None)
        key = resolver(token) if callable(resolver) else token
        if key is None:
            return None
        try:
            return get_space(key)
        except Exception:  # noqa: BLE001 - unknown key → clean not-found
            return None
