"""
Feature flag for the player lobby / spawning lifecycle flow.

The lobby/spawning flow (states 3-4 of the player state machine) is a large
behavioral change — it gates world-action commands behind a PLAYING state,
routes every login through spawning/lobby, and reroutes death and disconnect.
The switch stays a single feature flag so the whole flow can be reverted in one
line if needed.

The flag reads the Evennia setting ``LOBBY_FLOW_ENABLED`` (shipped True in
``settings.py``). It is resolved lazily and defensively: when the setting is
absent or ``django.conf.settings`` can't be read (the Django-free unit-test
suite), it falls back to False so those tests see existing behavior unless they
opt in via ``override_settings``.
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
