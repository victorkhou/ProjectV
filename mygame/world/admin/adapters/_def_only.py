"""
DefOnlyAdapter — shared base for definition-only EntityAdapters
(unified-admin-crud).

Three adapters — terrain, powerup, planet — expose ONLY a definition
scope: each is a pure YAML/registry definition with no admin-owned
"instances" (a tile's terrain, an applied powerup, and a coordinate-space
planet are all derived or config-defined, never spawned as admin objects).
Every INSTANCE verb (``list``/``spawn``/``show``/``set``/``destroy``) is
opted out with a reason pointing at the ``def`` scope, so the router
surfaces that reason and NEVER routes to the instance plane — the
instance-plane methods are purely DEFENSIVE, unreachable stubs.

Those seven stub bodies were copy-pasted identically across the three
adapters save for which opt-out reason each cited — and every cited reason
is exactly ``self.opt_outs[verb]`` (the same string the router surfaces).
This base supplies the seven once, deriving each message from
``self.opt_outs`` so the stub and the router can't drift. A subclass need
only declare its grammar contract (``entity_key``, ``supported_verbs``,
``opt_outs`` covering every instance verb, optionally ``def_domain``) plus
the definition-scope methods (``definition_fields``/``def_registry_dict``/
``def_resolve``).

Subclasses remain plain adapters — they satisfy the EntityAdapter Protocol
structurally; this base only removes the duplicated instance-plane
boilerplate, adding no new required attributes.
"""

from __future__ import annotations

from typing import Any

from world.admin.resolution import Resolution
from world.admin.types import FieldSpec


class DefOnlyAdapter:
    """Instance-plane opt-out stubs for a definition-only adapter.

    The seven instance-plane methods below are inherited by every
    def-only adapter: each is unreachable through the router (guarded by
    ``opt_outs``) and derives its message from the adapter's own declared
    reason, so the stub and the surfaced opt-out reason never diverge.
    """

    #: Subclasses MUST override with the instance opt-out reasons keyed by
    #: verb (``list``/``spawn``/``show``/``set``/``destroy``). Declared here
    #: only so the stubs below can read it on the pre-override base.
    opt_outs: dict[str, str] = {}

    def _opt_out_reason(self, verb: str) -> str:
        """The declared opt-out reason for *verb*.

        Falls back to the ``show`` reason and then a generic message —
        both unreachable in practice, since every def-only adapter
        declares all five instance opt-outs.
        """
        reason = self.opt_outs.get(verb) or self.opt_outs.get("show")
        return reason or f"{verb} is not available (definition-only entity)."

    # ------------------------------------------------------------------ #
    #  Instance plane — all opted out (unreachable through the router)
    # ------------------------------------------------------------------ #

    def instance_fields(self) -> dict[str, FieldSpec]:
        """No per-instance fields — ``set`` is opted out."""
        return {}

    def list_instances(self, caller: Any, filter_str: str) -> list:
        """``list`` is opted out — no instances exist."""
        return []

    def resolve_instance(self, caller: Any, token: str) -> Resolution:
        """``show``/``set``/``destroy`` are opted out; resolution is the
        opt-out reason if ever reached."""
        return Resolution(ok=False, error=self._opt_out_reason("show"))

    def create(self, caller: Any, def_token: str, kwargs: dict) -> Any:
        """``spawn`` is opted out — unreachable through the router."""
        raise NotImplementedError(self._opt_out_reason("spawn"))

    def read(self, caller: Any, instance: Any) -> Any:
        """``show`` is opted out — unreachable through the router."""
        raise NotImplementedError(self._opt_out_reason("show"))

    def update(self, caller: Any, instance: Any, field: str,
               value: Any) -> Any:
        """``set`` is opted out — unreachable through the router."""
        raise NotImplementedError(self._opt_out_reason("set"))

    def delete(self, caller: Any, instance: Any) -> Any:
        """``destroy`` is opted out — unreachable through the router."""
        raise NotImplementedError(self._opt_out_reason("destroy"))
