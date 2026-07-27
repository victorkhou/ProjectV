"""
AgentAdapter — the ``@agent`` EntityAdapter (unified-admin-crud Phase 2).

Brings the agent admin surface onto the shared adapter layer the
:class:`~commands.command_router.EntityAdminRouter` verb handlers drive:

- **NEW ``show`` and ``set``** (Requirement 7.3): agents gain the uniform
  instance readout and bounded field writes (``hp`` with dynamic bounds
  from the target's own ``hp_max``, ``hp_max``/``kills``/``deaths`` with
  static floors).
- **All writes via AgentSystem** (Requirement 3.5): creation goes through
  :meth:`AgentSystem.admin_create_agent`, field writes through
  :meth:`AgentSystem.admin_set_agent_field`, deletion through
  :meth:`AgentSystem.admin_destroy_agent` — AgentSystem stays the single
  writer for agent state.
- **Definition scope opted out** (Requirement 7.3): agents have no YAML
  definition domain — they are spawned from player context (Academy
  training, or the admin ``spawn`` verb). All five ``def`` verbs carry
  that reason plus the pointer to the supported path.
- **``create``→``spawn`` migration alias** (Requirement 11.5, design D5):
  the legacy admin spelling keeps working with a deprecation note.
- **Player-scoped resolution** (Requirement 2.4): instances come from a
  player's agent roster; a trailing ``[player]`` token scopes the search
  and defaults to the caller. ``#N`` indexes the caller's agent
  List_Cache; agent IDs are the stable row keys, so ``@agent show 3``
  and ``@agent destroy 2 Bob`` resolve exactly like the legacy grammar.

System access is LAZY (via the services facade) so constructing and
registering the adapter never needs a booted server; tests may inject an
``agent_system`` double.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from world.admin.adapters._support import live_service
from world.admin.resolution import (
    LIST_CACHE,
    Resolution,
    resolve_instance_token,
    resolve_player_scope,
)
from world.admin.types import (
    CreateResult as _BaseCreateResult,
    DeleteResult,
    FieldSpec,
    InstanceRow,
    SetResult,
    ShowReport,
    resolve_bounds,
)

#: The def-scope opt-out reason (with its pointer to the supported path),
#: shared verbatim by all five def verbs (Requirements 1.5, 7.3).
_NO_DEF_DOMAIN_REASON = (
    "agents have no YAML definition domain — they are spawned from player "
    "context; use the instance verbs ('@agent list/show/set/spawn/destroy') "
    "or Academy training instead"
)


@dataclass(frozen=True)
class CreateResult(_BaseCreateResult):
    """Outcome of a ``spawn`` — extends the canonical result with the IDs
    of the agent(s) created (``spawn`` may create several at once)."""

    #: Agent IDs created by this invocation (spawn may create several).
    created_ids: tuple[int, ...] = ()


def _db(agent: Any) -> Any:
    """The agent's attribute bag (``db``), or ``None``."""
    return getattr(agent, "db", None)


def _field(agent: Any, name: str, default: Any = None) -> Any:
    """Best-effort read of one ``db`` field off a live agent."""
    value = getattr(_db(agent), name, None)
    return default if value is None else value


def _status(agent: Any) -> str:
    """One-word roster status (mirrors the legacy list rendering)."""
    if _field(agent, "incapacitated", False):
        return "Incapacitated"
    if _field(agent, "reserve", False):
        return "Reserved"
    return "Active"


class AgentAdapter:
    """EntityAdapter for agents (the ``@agent`` admin surface).

    Tests may inject an ``agent_system`` double; production resolves the
    live system lazily through the services facade.
    """

    entity_key = "agent"

    # --- grammar contract (design per-entity matrix row for @agent) ---
    supported_verbs = frozenset(
        {"list", "spawn", "show", "set", "destroy"}
    )
    opt_outs: dict[str, str] = {
        "def list": _NO_DEF_DOMAIN_REASON,
        "def show": _NO_DEF_DOMAIN_REASON,
        "def set": _NO_DEF_DOMAIN_REASON,
        "def reset": _NO_DEF_DOMAIN_REASON,
        "def diff": _NO_DEF_DOMAIN_REASON,
    }
    extra_verbs: dict[str, str] = {}
    #: Migration alias (design D5): the legacy creation spelling.
    aliases = {"create": "spawn"}
    #: Verb-tier escalations (Requirement 8.7): the legacy ``@agent``
    #: mutations were Admin-gated; ``set`` (new) follows that convention.
    verb_perms = {"spawn": "Admin", "set": "Admin", "destroy": "Admin"}

    def __init__(self, agent_system: Any | None = None) -> None:
        self._agent_system = agent_system

    # ------------------------------------------------------------------ #
    #  System access (lazy — no live game required to construct)
    # ------------------------------------------------------------------ #

    def _system(self) -> Any | None:
        """The injected agent_system double, else the live AgentSystem."""
        return live_service("agent_system", self._agent_system)

    # ------------------------------------------------------------------ #
    #  Field schema (instance plane)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _hp_bounds(agent: Any) -> tuple[float | None, float | None]:
        """Dynamic ``hp`` bounds from the TARGET agent's current state
        (Requirement 3.4): 0 up to its own ``hp_max`` (unbounded high
        when the agent carries no ``hp_max``)."""
        hp_max = _field(agent, "hp_max")
        try:
            return (0, int(hp_max)) if hp_max is not None else (0, None)
        except (TypeError, ValueError):
            return (0, None)

    def instance_fields(self) -> dict[str, FieldSpec]:
        """Modifiable agent fields — all written through the AgentSystem
        single-writer path inside :meth:`update`."""
        specs = (
            FieldSpec(name="hp", kind="int", perm="Builder",
                      dynamic_bounds=self._hp_bounds),
            FieldSpec(name="hp_max", kind="int", min_value=1,
                      perm="Builder"),
            FieldSpec(name="kills", kind="int", min_value=0,
                      perm="Builder"),
            FieldSpec(name="deaths", kind="int", min_value=0,
                      perm="Builder"),
        )
        return {spec.name: spec for spec in specs}

    def definition_fields(self) -> dict[str, FieldSpec]:
        """No definition plane: agents have no YAML definition domain."""
        return {}

    # ------------------------------------------------------------------ #
    #  Listing + resolution (instance plane)
    # ------------------------------------------------------------------ #

    def _agents_of(self, player: Any) -> list:
        """The player's agent roster via the AgentSystem query path."""
        system = self._system()
        if system is None:
            return []
        try:
            agents = system.get_agents(player) or []
        except Exception:  # noqa: BLE001 - reads must never break a verb
            return []
        return sorted(
            agents, key=lambda a: _field(a, "agent_id", 0) or 0
        )

    def _row(self, index: int, agent: Any) -> InstanceRow:
        """One roster entry as an InstanceRow (list output + cache).

        The row ``key`` is the agent's stable numeric id (the design's
        "stable identifier"), so plain-``N`` tokens resolve by exact key
        exactly like the legacy id grammar.
        """
        aid = _field(agent, "agent_id", "?")
        name = str(getattr(agent, "key", None) or f"Agent-{aid}")
        role = str(_field(agent, "role", "") or "unassigned")
        hp = _field(agent, "hp", "?")
        hp_max = _field(agent, "hp_max", "?")
        summary = (
            f"{name} {role} {_status(agent)} HP {hp}/{hp_max}"
        )
        return InstanceRow(index=index, key=str(aid), name=name,
                           summary=summary, ref=agent)

    def _candidate_rows(self, player: Any) -> list[InstanceRow]:
        """The player's roster as resolution candidates."""
        return [
            self._row(i, agent)
            for i, agent in enumerate(self._agents_of(player), start=1)
        ]

    @staticmethod
    def _matches_filter(agent: Any, filt: str) -> bool:
        """Lenient list filter: role, status, or name substring."""
        role = str(_field(agent, "role", "") or "unassigned").lower()
        if filt in (role, _status(agent).lower()):
            return True
        name = str(getattr(agent, "key", "") or "").lower()
        return filt in name

    def _parse_list_scope(self, caller: Any, filter_str: str
                          ) -> tuple[Any, str]:
        """Split ``list``'s args into (scope player, filter).

        A trailing token that resolves to exactly one player scopes the
        listing to that player's roster (Requirement 2.4); otherwise the
        whole string is the filter and the scope defaults to the caller.
        """
        tokens = (filter_str or "").split()
        if tokens:
            scope = resolve_player_scope(caller, tokens[-1])
            if scope.ok and scope.target is not None:
                return scope.target, " ".join(tokens[:-1]).strip().lower()
        return caller, (filter_str or "").strip().lower()

    def list_instances(self, caller: Any, filter_str: str
                       ) -> list[InstanceRow]:
        """Agent instances in the scoped player's roster, indexed rows."""
        scope, filt = self._parse_list_scope(caller, filter_str)
        rows: list[InstanceRow] = []
        for agent in self._agents_of(scope):
            if filt and not self._matches_filter(agent, filt):
                continue
            rows.append(self._row(len(rows) + 1, agent))
        return rows

    def resolve_instance(self, caller: Any, token: str) -> Resolution:
        """Resolve *token* per the uniform grammar over agent rosters.

        ``#N`` indexes the caller's agent List_Cache; key/name/prefix
        tiers run over the caller's own roster first (agent ids are the
        keys, so ``2`` hits agent #2 exactly). When that fails and the
        token's LAST word resolves to a player, the remainder is
        re-resolved against that player's roster (trailing ``[player]``
        scoping, Requirement 2.4) — preserving the legacy
        ``<id> <player>`` addressing.
        """
        token = (token or "").strip()
        rows = LIST_CACHE.get(caller, self.entity_key)
        primary = resolve_instance_token(
            token, rows=rows, candidates=self._candidate_rows(caller)
        )
        if primary.ok:
            return primary
        parts = token.rsplit(None, 1)
        if len(parts) == 2:
            agent_token, player_token = parts
            scope = resolve_player_scope(caller, player_token)
            if scope.ok and scope.target is not None:
                return resolve_instance_token(
                    agent_token,
                    rows=rows,
                    candidates=self._candidate_rows(scope.target),
                )
        return primary

    # ------------------------------------------------------------------ #
    #  Instance CRUD hooks (all writes via the AgentSystem single writer)
    # ------------------------------------------------------------------ #

    def create(self, caller: Any, def_token: str, kwargs: dict
               ) -> CreateResult:
        """``spawn``: create agent(s) through the AgentSystem admin path.

        Agents have no definition domain, so the "definition" of an
        agent spawn is its player context: the ``player`` kwarg
        (resolved by the router) targets the grant, defaulting to the
        caller; ``count=N`` creates several. Each creation goes through
        :meth:`AgentSystem.admin_create_agent` (bypassing cost/timer but
        keeping the never-reuse id rule).
        """
        system = self._system()
        if system is None:
            return CreateResult(ok=False, error="Agent system unavailable")
        target = kwargs.get("player") or caller
        try:
            count = max(1, int(kwargs.get("count", 1)))
        except (TypeError, ValueError):
            return CreateResult(ok=False, error="count must be a number")

        created: list = []
        for _ in range(count):
            try:
                npc = system.admin_create_agent(target)
            except Exception as exc:  # noqa: BLE001 - relay path failures
                if created:
                    break  # partial grant: report what was made
                return CreateResult(ok=False, error=str(exc))
            if npc is None:
                if created:
                    break
                return CreateResult(ok=False, error="creation path failed")
            created.append(npc)

        if not created:
            return CreateResult(ok=False, error="creation path failed")
        ids = tuple(
            int(_field(npc, "agent_id", 0) or 0) for npc in created
        )
        return CreateResult(
            ok=True,
            instance=created[0] if len(created) == 1 else created,
            created_ids=ids,
        )

    def read(self, caller: Any, agent: Any) -> ShowReport:
        """``show``: identity header, live state, modifiable fields."""
        aid = _field(agent, "agent_id", "?")
        name = str(getattr(agent, "key", None) or f"Agent-{aid}")
        owner = _field(agent, "owner")
        owner_name = str(getattr(owner, "key", None) or "?")
        role = str(_field(agent, "role", "") or "unassigned")
        activity = str(_field(agent, "activity_status", "") or "Idle")

        state_lines = [
            f"Role: {role}    Status: {_status(agent)}",
            f"HP: {_field(agent, 'hp', '?')}/{_field(agent, 'hp_max', '?')}"
            f"    Kills: {_field(agent, 'kills', 0) or 0}"
            f"    Deaths: {_field(agent, 'deaths', 0) or 0}",
            f"Activity: {activity}",
        ]

        fields = [
            (spec, _field(agent, spec.name, "—"), False)
            for spec in self.instance_fields().values()
        ]
        return ShowReport(
            header=f"Agent #{aid} ({name}) — owner: {owner_name}",
            state_lines=state_lines,
            fields=fields,
            staleness_note=None,  # no definition domain to drift from
        )

    def update(self, caller: Any, agent: Any, field: str, value: Any
               ) -> SetResult:
        """``set``: bounded write through the AgentSystem single writer.

        The router already coerced and clamped *value*; this re-clamps
        defensively (the SetResult contract — ``applied`` always lands
        in-bounds — must hold whoever calls ``update``) and performs the
        write via :meth:`AgentSystem.admin_set_agent_field`. No state
        changes on any failure (Requirement 3.10).
        """
        name = str(getattr(agent, "key", None) or "agent")
        spec = self.instance_fields().get(field)
        if spec is None:
            valid = ", ".join(sorted(self.instance_fields()))
            return SetResult.fail(
                field, value,
                f"'{field}' is not a modifiable agent field; "
                f"settable: {valid}",
            )
        try:
            requested = int(value)
        except (TypeError, ValueError):
            return SetResult.fail(
                field, value, f"value must be a number (got '{value}')"
            )

        lo, hi = resolve_bounds(spec, agent)
        applied = requested
        if lo is not None and applied < lo:
            applied = int(lo)
        if hi is not None and applied > hi:
            applied = int(hi)

        system = self._system()
        if system is None or not system.admin_set_agent_field(
            agent, field, applied
        ):
            return SetResult.fail(
                field, requested,
                f"could not write {field} onto {name} — unchanged",
            )
        return SetResult(ok=True, field=field, requested=requested,
                         applied=applied, clamped=(applied != requested))

    def delete(self, caller: Any, agent: Any) -> DeleteResult:
        """``destroy``: delete through the AgentSystem deletion path."""
        system = self._system()
        if system is None:
            return DeleteResult(ok=False, error="Agent system unavailable")
        if not system.admin_destroy_agent(agent):
            return DeleteResult(ok=False, error="deletion path failed")
        return DeleteResult(ok=True)

    # ------------------------------------------------------------------ #
    #  Definition scope (opted out — no YAML definition domain)
    # ------------------------------------------------------------------ #

    def def_registry_dict(self) -> None:
        """Agents have no definition registry (def scope is opted out)."""
        return None

    def def_resolve(self, token: str) -> None:
        """No definition domain — nothing to resolve."""
        return None
