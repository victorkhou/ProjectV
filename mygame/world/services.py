"""Facade over the installed game systems.

The single access point for game systems. The composition root
(server/conf/game_init.py initialize_game) constructs the systems and calls
install(); everything else reads through the accessors. This module imports
nothing from server.conf — the dependency points from the composition root
into this module, never back.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

_systems: dict[str, Any] | None = None


def install(systems: dict[str, Any]) -> None:
    """Store *systems* as the installed mapping, replacing any previous one.

    Stores the dict by reference (not a copy), matching the previous
    shared-``game_systems``-dict semantics: mutations made by the owner after
    install are visible through the accessors.
    """
    global _systems
    _systems = systems


def get_service(name: str) -> Any | None:
    """Return the installed system named *name*, or None (also pre-install)."""
    if _systems is None:
        return None
    return _systems.get(name)


def get_systems() -> dict[str, Any]:
    """Return the installed systems dict, or an empty dict pre-install."""
    return _systems if _systems is not None else {}


def get_registry() -> Any | None:
    """Return the installed registry system, or None."""
    return get_service("registry")


def get_balance() -> Any | None:
    """Return the balance configuration on the installed registry, or None."""
    registry = get_registry()
    return getattr(registry, "balance", None)


@contextmanager
def override(systems: dict[str, Any]) -> Iterator[None]:
    """Temporarily install *systems*; restore the prior state on exit.

    Test injection helper: snapshots the current installed state (including
    the not-installed None state) and restores it on exit, so no injected
    system leaks into subsequently executed tests.
    """
    global _systems
    previous = _systems
    _systems = systems
    try:
        yield
    finally:
        _systems = previous
