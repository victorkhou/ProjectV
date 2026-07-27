"""
TechnologyAdapter — the NEW ``@tech`` EntityAdapter (unified-admin-crud
Phase 2, task 5.3).

Technologies had no admin surface at all; this adapter gives them one
under the unified grammar (Requirement 7.1):

- **Instances are grants**: the ``@tech`` "instance" is one technology
  granted to one player (:class:`TechGrant`). ``list`` shows the
  technologies granted to the trailing ``[player]`` (defaulting to the
  caller); ``show`` renders one granted tech with its def-backed info
  read live from the merged registry.
- **grant → spawn / revoke → destroy** (design per-entity matrix): the
  friendly spellings are extra verbs on the router that dispatch through
  the canonical ``spawn``/``destroy`` handlers. Both writes go through
  the :class:`~world.systems.tech_system.TechLabSystem` admin
  single-writer paths (``admin_grant_technology`` /
  ``admin_revoke_technology``), which add/remove through the existing
  research path and recompute the player's derived tech bonuses BEFORE
  the success response (Requirements 7.7, 7.8).
- **Grant-state errors** (Requirement 7.9): granting an already-held
  tech, or revoking a non-held one, errors stating the player's current
  grant state for that technology with no state change. The not-held
  case surfaces at resolution time (resolution runs over the player's
  granted set; a token naming a real technology the player does not
  hold produces the grant-state error instead of a bare not-found).
- **Instance ``set`` opted out** (Requirement 7.1): technologies have no
  modifiable per-instance fields — a grant is boolean.
- **Full definition scope**: ``def_registry_dict`` serves the live
  ``DataRegistry.technologies`` and ``def_resolve`` delegates to the
  existing ``resolve_technology`` key/name/prefix matcher
  (Requirement 2.6).

System/registry access is LAZY (services facade, then singletons) so
constructing and registering the adapter never needs a booted server;
tests may inject ``registry`` and ``tech_system`` doubles.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from world.admin.adapters._support import live_registry, live_service
from world.admin.resolution import (
    LIST_CACHE,
    Resolution,
    resolve_instance_token,
    resolve_player_scope,
)
from world.admin.types import (
    CORE_VERBS,
    CreateResult,
    DeleteResult,
    FieldSpec,
    InstanceRow,
    ShowReport,
)

#: The instance-``set`` opt-out reason (with its pointer to the supported
#: paths), surfaced verbatim by the router (Requirements 1.5, 7.1).
_NO_INSTANCE_FIELDS_REASON = (
    "technologies have no modifiable per-instance fields — a grant is "
    "boolean; use 'grant'/'revoke' to change a player's technologies, or "
    "'def set' to change the technology definition"
)


@dataclass(frozen=True)
class TechGrant:
    """One technology granted to one player — the ``@tech`` instance.

    Carries ``name``/``key`` so the router's ``_describe_instance``
    renders it as ``name (key)`` in spawn/destroy confirmations.
    """

    player: Any
    key: str
    name: str


class TechnologyAdapter:
    """EntityAdapter for technologies (the ``@tech`` admin surface).

    Tests may inject a registry double via ``registry`` and a
    TechLabSystem double via ``tech_system``; production resolves both
    lazily per call.
    """

    entity_key = "tech"
    #: Overlay/definition domain (matches DataRegistry._REQUIRED_FILES).
    def_domain = "technologies"

    # --- grammar contract (design per-entity matrix row for @tech) ---
    supported_verbs = frozenset(CORE_VERBS - {"set"})
    opt_outs: dict[str, str] = {"set": _NO_INSTANCE_FIELDS_REASON}
    #: grant/revoke are the write model: NEW spellings (not deprecated
    #: aliases) whose router handlers dispatch through the canonical
    #: spawn/destroy handlers (design: "grant <tech> [player] maps to
    #: spawn", "revoke maps to destroy").
    extra_verbs = {
        "grant": "Grant a technology to a player (maps to spawn)",
        "revoke": "Revoke a granted technology (maps to destroy)",
    }
    aliases: dict[str, str] = {}

    def __init__(self, registry: Any | None = None,
                 tech_system: Any | None = None) -> None:
        self._registry = registry
        self._tech_system = tech_system

    # ------------------------------------------------------------------ #
    #  Registry / system access (lazy — no live game required)
    # ------------------------------------------------------------------ #

    def _live_registry(self) -> Any | None:
        """Injected double, else services facade, else the singleton."""
        return live_registry(self._registry)

    def _system(self) -> Any | None:
        """The injected tech_system double, else the live TechLabSystem."""
        return live_service("tech_system", self._tech_system)

    @staticmethod
    def _resolve_def(registry: Any, token: str) -> Any | None:
        """Resolve a def token via the registry's existing matchers.

        ``resolve_technology`` (key/name/prefix — Requirement 2.6)
        first, then an exact dict lookup as a fallback for doubles that
        only expose the ``technologies`` dict.
        """
        resolver = getattr(registry, "resolve_technology", None)
        tdef = resolver(token) if callable(resolver) else None
        if tdef is None:
            technologies = getattr(registry, "technologies", None)
            if isinstance(technologies, dict):
                tdef = technologies.get(token)
        return tdef

    # ------------------------------------------------------------------ #
    #  Field schemas
    # ------------------------------------------------------------------ #

    def instance_fields(self) -> dict[str, FieldSpec]:
        """No modifiable per-instance fields — ``set`` is opted out."""
        return {}

    def definition_fields(self) -> dict[str, FieldSpec]:
        """Overridable ``def set`` fields, against real ``TechnologyDef``
        fields. Merged data still runs the full SchemaValidator +
        cross_validate on reload (e.g. required_rank must name a loaded
        rank), so anything subtler than these checks fails the reload,
        not the game."""
        specs = (
            FieldSpec(name="name", kind="str", perm="Admin"),
            FieldSpec(name="required_rank", kind="str", perm="Admin"),
            FieldSpec(name="research_ticks", kind="int", min_value=1,
                      perm="Admin"),
            FieldSpec(name="effect_type", kind="str", perm="Admin"),
        )
        return {spec.name: spec for spec in specs}

    # ------------------------------------------------------------------ #
    #  Listing + resolution (instance plane = grants on a player)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _granted_keys(player: Any) -> list[str]:
        """The player's researched tech keys, sorted (best-effort read;
        writes go through the TechLabSystem single writer only)."""
        try:
            db = getattr(player, "db", None)
            techs = getattr(db, "researched_techs", None) if db else None
            return sorted(str(k) for k in (techs or ()))
        except Exception:  # noqa: BLE001 - reads must never break a verb
            return []

    def _row(self, index: int, player: Any, tech_key: str) -> InstanceRow:
        """One granted tech as an InstanceRow (list output + #N cache)."""
        registry = self._live_registry()
        tdef = self._resolve_def(registry, tech_key) if registry else None
        name = str(getattr(tdef, "name", None) or tech_key)
        bits = [name]
        if name != tech_key:
            bits.append(f"({tech_key})")
        rank = getattr(tdef, "required_rank", None)
        if rank:
            bits.append(f"rank {rank}")
        effect = getattr(tdef, "effect_type", None)
        if effect:
            bits.append(f"effect {effect}")
        return InstanceRow(
            index=index, key=tech_key, name=name, summary=" ".join(bits),
            ref=TechGrant(player=player, key=tech_key, name=name),
        )

    def _granted_rows(self, player: Any) -> list[InstanceRow]:
        """The player's granted techs as resolution candidates."""
        return [
            self._row(i, player, key)
            for i, key in enumerate(self._granted_keys(player), start=1)
        ]

    def _parse_list_scope(self, caller: Any, filter_str: str
                          ) -> tuple[Any, str]:
        """Split ``list``'s args into (scope player, filter).

        A trailing token that resolves to exactly one player scopes the
        listing to that player's granted techs (Requirements 2.4, 7.1);
        otherwise the whole string is the filter and the scope defaults
        to the caller.
        """
        tokens = (filter_str or "").split()
        if tokens:
            scope = resolve_player_scope(caller, tokens[-1])
            if scope.ok and scope.target is not None:
                return scope.target, " ".join(tokens[:-1]).strip().lower()
        return caller, (filter_str or "").strip().lower()

    @staticmethod
    def _matches_filter(row: InstanceRow, filt: str) -> bool:
        """Lenient list filter: key/name/summary substring."""
        return (filt in row.key.lower() or filt in row.name.lower()
                or filt in row.summary.lower())

    def list_instances(self, caller: Any, filter_str: str
                       ) -> list[InstanceRow]:
        """Technologies granted to the scoped player, indexed rows."""
        scope, filt = self._parse_list_scope(caller, filter_str)
        rows: list[InstanceRow] = []
        for key in self._granted_keys(scope):
            row = self._row(len(rows) + 1, scope, key)
            if filt and not self._matches_filter(row, filt):
                continue
            rows.append(row)
        return rows

    def resolve_instance(self, caller: Any, token: str) -> Resolution:
        """Resolve *token* per the uniform grammar over granted techs.

        ``#N`` indexes the caller's tech List_Cache; key/name/prefix
        tiers run over the caller's own granted techs first. When that
        fails and the token's LAST word resolves to a player, the
        remainder is re-resolved against that player's grants (trailing
        ``[player]`` scoping, Requirement 2.4). A token naming a real
        technology the scoped player does not hold errors stating the
        player's current grant state (Requirement 7.9).
        """
        token = (token or "").strip()
        rows = LIST_CACHE.get(caller, self.entity_key)
        primary = resolve_instance_token(
            token, rows=rows, candidates=self._granted_rows(caller)
        )
        if primary.ok:
            return primary
        parts = token.rsplit(None, 1)
        if len(parts) == 2:
            tech_token, player_token = parts
            scope = resolve_player_scope(caller, player_token)
            if scope.ok and scope.target is not None:
                scoped = resolve_instance_token(
                    tech_token,
                    rows=rows,
                    candidates=self._granted_rows(scope.target),
                )
                if scoped.ok:
                    return scoped
                return self._grant_state_failure(
                    scope.target, tech_token, scoped
                )
        return self._grant_state_failure(caller, token, primary)

    def _grant_state_failure(self, player: Any, token: str,
                             fallback: Resolution) -> Resolution:
        """The not-held grant-state error (Requirement 7.9), when *token*
        names a real technology the scoped *player* does not hold;
        otherwise the original resolution failure passes through."""
        tdef = self.def_resolve(token)
        if tdef is None:
            return fallback
        key = getattr(tdef, "key", token)
        name = str(getattr(player, "key", "?"))
        return Resolution(
            ok=False,
            error=(
                f"{name} does not hold technology '{key}' — current "
                "grant state: not granted. Nothing changed."
            ),
        )

    # ------------------------------------------------------------------ #
    #  Instance CRUD hooks (writes via the TechLabSystem single writer)
    # ------------------------------------------------------------------ #

    def create(self, caller: Any, def_token: str, kwargs: dict
               ) -> CreateResult:
        """``spawn`` (grant): add through the existing research path.

        The ``player`` kwarg (resolved by the router from the trailing
        token) targets the grant, defaulting to the caller. The write —
        researched-set add + derived-bonus recompute BEFORE the response
        (Requirement 7.7) — goes through
        :meth:`TechLabSystem.admin_grant_technology`; granting an
        already-held technology errors stating the current grant state
        with no state change (Requirement 7.9).
        """
        registry = self._live_registry()
        tdef = (self._resolve_def(registry, str(def_token).strip())
                if registry else None)
        if tdef is None:
            return CreateResult(
                ok=False,
                error=f"no technology definition matches '{def_token}'",
            )
        target = kwargs.get("player") or caller
        system = self._system()
        if system is None:
            return CreateResult(ok=False, error="Tech system unavailable")
        tech_key = str(getattr(tdef, "key", def_token))
        ok, error = system.admin_grant_technology(target, tech_key)
        if not ok:
            return CreateResult(ok=False, error=error)
        return CreateResult(
            ok=True,
            instance=TechGrant(
                player=target, key=tech_key,
                name=str(getattr(tdef, "name", tech_key)),
            ),
        )

    def read(self, caller: Any, grant: TechGrant) -> ShowReport:
        """``show``: one granted tech, def-backed info read LIVE from the
        merged registry (a ``def set`` shows up on the next show)."""
        registry = self._live_registry()
        tdef = (self._resolve_def(registry, grant.key)
                if registry else None)
        holder = str(getattr(grant.player, "key", "?"))

        state_lines = [f"Granted to: {holder}"]
        if tdef is not None:
            state_lines.append(
                f"Required rank: {getattr(tdef, 'required_rank', '—')}    "
                f"Research ticks: {getattr(tdef, 'research_ticks', '—')}"
            )
            cost = getattr(tdef, "resource_cost", None) or {}
            if cost:
                cost_str = ", ".join(
                    f"{res} {amount}" for res, amount in sorted(cost.items())
                )
                state_lines.append(f"Resource cost: {cost_str}")
            effect_type = getattr(tdef, "effect_type", "") or "—"
            state_lines.append(
                f"Effect: {effect_type} -> "
                f"{getattr(tdef, 'effect_value', None)!r}"
            )
        else:
            state_lines.append(
                "note: no definition found for this grant (stale key?)"
            )

        return ShowReport(
            header=f"{grant.name} ({grant.key}) — technology granted "
                   f"to {holder}",
            state_lines=state_lines,
            fields=[],  # no modifiable per-instance fields (R7.1)
            staleness_note=None,
        )

    def update(self, caller: Any, instance: Any, field: str, value: Any
               ) -> Any:
        """``set`` is opted out — unreachable through the router; the
        opt-out reason is the answer here too."""
        raise NotImplementedError(_NO_INSTANCE_FIELDS_REASON)

    def delete(self, caller: Any, grant: TechGrant) -> DeleteResult:
        """``destroy`` (revoke): remove + recompute derived bonuses
        BEFORE the response, via
        :meth:`TechLabSystem.admin_revoke_technology` (Requirement 7.8).
        Revoking a non-held technology errors stating the current grant
        state with no state change (Requirement 7.9)."""
        system = self._system()
        if system is None:
            return DeleteResult(ok=False, error="Tech system unavailable")
        ok, error = system.admin_revoke_technology(grant.player, grant.key)
        if not ok:
            return DeleteResult(ok=False, error=error)
        return DeleteResult(ok=True)

    # ------------------------------------------------------------------ #
    #  Definition scope
    # ------------------------------------------------------------------ #

    def def_registry_dict(self) -> dict | None:
        """The live ``DataRegistry.technologies`` dict (merged registry)."""
        registry = self._live_registry()
        technologies = (getattr(registry, "technologies", None)
                        if registry else None)
        return technologies if isinstance(technologies, dict) else None

    def def_resolve(self, token: str) -> Any | None:
        """Resolve a definition token via the existing
        ``resolve_technology`` key/name/prefix matcher (Requirement 2.6)."""
        registry = self._live_registry()
        if registry is None:
            return None
        token = str(token or "").strip()
        if not token:
            return None
        return self._resolve_def(registry, token)
