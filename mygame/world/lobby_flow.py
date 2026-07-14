"""
Feature flag for the player lobby / spawning lifecycle flow.

The lobby/spawning flow (states 3-4 of the player state machine) is a large
behavioral change — it gates world-action commands behind a PLAYING state,
routes every login through spawning/lobby, and reroutes death and disconnect.
All the machinery ships built and tested, but the behavioral switch is gated
here so it can be enabled deliberately (after manual UX testing) rather than
flipping on for every player the moment the code lands.

The flag reads the Evennia setting ``LOBBY_FLOW_ENABLED`` (default False). It is
resolved lazily and defensively so the (Django-free) unit-test suite — where
``django.conf.settings`` may be unconfigured — always sees it as disabled and
existing behavior is unchanged.
"""

from __future__ import annotations


def lobby_flow_enabled() -> bool:
    """Return True if the lobby/spawning lifecycle flow is enabled.

    Defaults to False (flow off, current behavior) whenever the setting is
    unset or settings can't be read (test env). Never raises.
    """
    try:
        from django.conf import settings
        return bool(getattr(settings, "LOBBY_FLOW_ENABLED", False))
    except Exception:  # noqa: BLE001 - settings unavailable -> treat as disabled
        return False
