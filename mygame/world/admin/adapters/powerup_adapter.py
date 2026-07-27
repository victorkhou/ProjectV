"""
PowerupAdapter — the NEW ``@powerup`` def-only EntityAdapter
(unified-admin-crud Phase 4, task 8.1).

Powerups had no admin surface at all; this adapter gives them a
definition-only one under the unified grammar (Requirement 7.4):

- **Def scope only**: powerups are pure definitions (a powerup is applied
  to a player through the powerup system, not spawned as a standalone
  admin object), so every INSTANCE verb — ``list``/``spawn``/``show``/
  ``set``/``destroy`` — is opted out with a reason pointing at the ``def``
  scope, and the full definition scope (``def list``/``def show``/
  ``def set``/``def reset``/``def diff``) is supported.
- **Overlay-backed writes**: ``def set``/``def reset`` land in the same
  ``definitions_overrides.yaml`` overlay as every other def domain and
  trigger a validated ``DataRegistry.reload_all`` (``powerups`` is a
  required file, so the overlay merges and the change goes live on the
  next lazy read). A failed reload rolls the overlay back unchanged.
- **Live registry / resolver**: ``def_registry_dict`` serves the live
  ``DataRegistry.powerups`` and ``def_resolve`` delegates to the existing
  ``resolve_powerup`` key/name/prefix matcher (Requirement 2.6).

Registry access is LAZY (services facade, then singleton) so constructing
and registering the adapter never needs a booted server; tests inject a
``registry`` double.
"""

from __future__ import annotations

from typing import Any

from world.admin.adapters._def_only import DefOnlyAdapter
from world.admin.adapters._support import live_registry
from world.admin.types import FieldSpec

#: The def-only opt-out reasons for the instance verbs (surfaced verbatim
#: by the router, each pointing at the supported def-scope path —
#: Requirements 1.5, 7.4).
_LIST_REASON = (
    "powerups are definition-only — there are no powerup instances to "
    "list; use 'def list' to see the powerup definitions"
)
_SPAWN_REASON = (
    "powerups are definition-only and are applied through the powerup "
    "system, not spawned as standalone objects; use 'def set' to edit a "
    "powerup definition"
)
_SHOW_REASON = (
    "powerups have no per-instance admin surface; use 'def show <key>' "
    "to inspect a powerup definition"
)
_SET_REASON = (
    "powerups have no modifiable per-instance fields; use 'def set "
    "<key> <field> <value>' to change a powerup definition"
)
_DESTROY_REASON = (
    "powerups are definition-only and cannot be destroyed as instances; "
    "edit 'powerups.yaml' (or use 'def reset') to change a definition"
)


class PowerupAdapter(DefOnlyAdapter):
    """EntityAdapter for powerups (the ``@powerup`` def-only admin surface).

    The instance plane (``list``/``spawn``/``show``/``set``/``destroy``) is
    opted out and inherited from :class:`DefOnlyAdapter` as unreachable
    stubs; this class declares only the grammar contract and the
    definition scope.

    Tests may inject a registry double via ``registry``; production
    resolves the live ``DataRegistry`` lazily per call.
    """

    entity_key = "powerup"
    #: Overlay/definition domain (matches DataRegistry._REQUIRED_FILES).
    def_domain = "powerups"

    # --- grammar contract (design per-entity matrix row for @powerup) ---
    supported_verbs = frozenset({
        "def list", "def show", "def set", "def reset", "def diff",
    })
    opt_outs: dict[str, str] = {
        "list": _LIST_REASON,
        "spawn": _SPAWN_REASON,
        "show": _SHOW_REASON,
        "set": _SET_REASON,
        "destroy": _DESTROY_REASON,
    }
    extra_verbs: dict[str, str] = {}
    aliases: dict[str, str] = {}

    def __init__(self, registry: Any | None = None) -> None:
        self._registry = registry

    # ------------------------------------------------------------------ #
    #  Registry access (lazy — no live game required)
    # ------------------------------------------------------------------ #

    def _live_registry(self) -> Any | None:
        """Injected double, else services facade, else the singleton."""
        return live_registry(self._registry)

    # ------------------------------------------------------------------ #
    #  Field schemas
    # ------------------------------------------------------------------ #

    def definition_fields(self) -> dict[str, FieldSpec]:
        """Overridable ``def set`` fields, against real ``PowerupDef``
        fields (``key`` is the identity and is not settable). Merged data
        still runs the full SchemaValidator + cross_validate on reload
        (e.g. required_rank must name a loaded rank), so anything subtler
        than these kind checks fails the reload, not the game."""
        specs = (
            FieldSpec(name="name", kind="str", perm="Admin"),
            FieldSpec(name="required_rank", kind="str", perm="Admin"),
            FieldSpec(name="effect_type", kind="str", perm="Admin"),
            FieldSpec(name="effect_value", kind="float", perm="Admin"),
            FieldSpec(name="duration_ticks", kind="int", min_value=0,
                      perm="Admin"),
            FieldSpec(name="cooldown_ticks", kind="int", min_value=0,
                      perm="Admin"),
        )
        return {spec.name: spec for spec in specs}

    # ------------------------------------------------------------------ #
    #  Definition scope
    # ------------------------------------------------------------------ #

    def def_registry_dict(self) -> dict | None:
        """The live ``DataRegistry.powerups`` dict (merged registry)."""
        registry = self._live_registry()
        powerups = (getattr(registry, "powerups", None)
                    if registry else None)
        return powerups if isinstance(powerups, dict) else None

    def def_resolve(self, token: str) -> Any | None:
        """Resolve a definition token via the existing ``resolve_powerup``
        key/name/prefix matcher (Requirement 2.6), with an exact dict
        lookup fallback for doubles exposing only the ``powerups`` dict."""
        registry = self._live_registry()
        if registry is None:
            return None
        token = str(token or "").strip()
        if not token:
            return None
        resolver = getattr(registry, "resolve_powerup", None)
        pdef = resolver(token) if callable(resolver) else None
        if pdef is None:
            powerups = getattr(registry, "powerups", None)
            if isinstance(powerups, dict):
                pdef = powerups.get(token)
        return pdef
