"""
Shared adapter support — lazy registry/service access (unified-admin-crud).

Every EntityAdapter needs the same two things from the running game, and
needs them *lazily* so the adapter can be constructed and registered without
a booted server (tests inject doubles; production resolves on first use):

- the live :class:`~world.data_registry.DataRegistry` (for def-scope reads
  and def resolution), and
- a named game *system* off the services facade (AgentSystem, RankSystem,
  AllianceSystem, TechLabSystem, the OutpostSpawnerSystem, …).

These were copy-pasted into eight adapters with three subtly different
bodies. They live here once, as free functions taking the injected double
explicitly, so each adapter keeps its thin ``_live_registry`` / ``_system``
method (the surface some tests call) while delegating the body here.

All access is defensive: the services facade may be unavailable mid-import,
and reads must never raise out of a verb — an unavailable dependency
resolves to ``None`` and the caller reports it, rather than crashing.
"""

from __future__ import annotations

from typing import Any


def live_registry(injected: Any | None = None) -> Any | None:
    """The live DataRegistry: the *injected* double, else the services
    facade's ``registry``, else the process singleton.

    Returns ``None`` only if every path fails (facade absent AND the
    singleton import/construction raises) — callers guard ``None``.
    """
    if injected is not None:
        return injected
    try:
        from world import services

        registry = services.get_systems().get("registry")
        if registry is not None:
            return registry
    except Exception:  # noqa: BLE001 - facade unavailable mid-import
        pass
    try:
        from world.data_registry import DataRegistry

        return DataRegistry.get_instance()
    except Exception:  # noqa: BLE001 - reads must never break a verb
        return None


def live_service(name: str, injected: Any | None = None) -> Any | None:
    """A named game system: the *injected* double, else the services
    facade's system of that *name*, else ``None`` (facade unavailable or
    the system not installed). Callers guard ``None``.
    """
    if injected is not None:
        return injected
    try:
        from world import services

        return services.get_service(name)
    except Exception:  # noqa: BLE001 - facade unavailable mid-import
        return None
