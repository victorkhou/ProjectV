"""
PlayerAdapter — the ``@player`` EntityAdapter (unified-admin-crud Phase 3).

Migrates the player admin surface onto the unified grammar
(Requirements 1.5, 11.5, 11.6):

- **NEW ``show``**: players gain the uniform instance readout (level,
  rank, XP, plus the modifiable-fields block).
- **``set`` with ``level`` and ``rank``** (Requirement 11.5): ``level``
  is an int Field_Spec with STATIC bounds 1–``MAX_LEVEL`` (1–100);
  ``rank`` is an enum Field_Spec whose valid values are the numeric rank
  ids ``1``–``NUM_RANKS`` (the same ids the legacy ``@player rank <N>``
  form accepted). Both writes go through the EXISTING single-writer
  progression path the legacy verbs used: XP is re-stamped to the target
  level's threshold via ``RankSystem.xp_for_level``, ``db.level``/
  ``db.rank_level`` are written, and ``RankSystem.check_promotion``
  recomputes rank events (tech unlocks, agent-cap adjustments) before
  the success response — side effects preserved.
- **``level``/``rank`` Migration_Aliases to ``set``** (Requirement
  11.5, design D5): the old verb forms keep working with a deprecation
  note. Their legacy argument order (``level <N> [player]``) differs
  from the canonical ``set <target> <field> <value>``, so the ROUTER
  subclass reshapes the arguments before dispatching through the shared
  alias path (see ``CmdAdminPlayer._dispatch_alias``).
- **``spawn`` opted out** (design per-entity matrix): players register
  through account creation — the reason carries the pointer to the
  supported path (Requirement 1.5).
- **``destroy`` opted out** with the pointer to the existing
  ``@obliterate`` flow (destructive, separate confirmation flow).
- **Definition scope opted out**: players have no YAML definition
  domain; all five ``def`` verbs carry that reason.
- **Verb-tier escalation** (Requirement 8.7): the legacy ``level``/
  ``rank`` verbs were Admin-gated, so ``set`` is escalated to Admin via
  ``verb_perms`` — alias and canonical spellings hit the identical
  permission outcome (Requirement 11.1).

System/registry access is LAZY (services facade, then — for the
registry — the DataRegistry singleton), so constructing and registering
the adapter never needs a booted server; tests may inject
``rank_system``/``registry`` doubles and a ``players_provider``.
"""

from __future__ import annotations

from typing import Any, Callable

from world.admin.adapters._support import live_registry, live_service
from world.admin.resolution import (
    LIST_CACHE,
    Resolution,
    resolve_instance_token,
    resolve_player_scope,
)
from world.admin.types import (
    CreateResult,
    DeleteResult,
    FieldSpec,
    InstanceRow,
    SetResult,
    ShowReport,
)
from world.constants import MAX_LEVEL, NUM_RANKS

#: The spawn opt-out reason (with its pointer to the supported path),
#: surfaced verbatim by the router (Requirement 1.5).
_SPAWN_OPT_OUT = (
    "players register through account creation and are not admin-spawned "
    "— new characters come from the login/registration flow"
)

#: The destroy opt-out reason: pointer to the existing obliterate flow
#: (design per-entity matrix: destructive, separate confirmation flow).
_DESTROY_OPT_OUT = (
    "players are not destroyed here — use the existing '@obliterate' "
    "flow (destructive, with its own separate confirmation)"
)

#: Reason shared by all five opted-out definition verbs.
_NO_DEF_DOMAIN_REASON = (
    "players have no YAML definition domain — progression is per-player "
    "instance state; use '@player set <target> level|rank' instead"
)

#: Valid rank ids for the ``rank`` enum Field_Spec — the numeric rank
#: ids 1–NUM_RANKS (from world.constants), matching what the legacy
#: ``@player rank <N>`` verb accepted.
_RANK_ENUM_VALUES = tuple(str(i) for i in range(1, NUM_RANKS + 1))


def _db_field(player: Any, name: str, default: Any = None) -> Any:
    """Best-effort read of one ``db`` field off a live player."""
    value = getattr(getattr(player, "db", None), name, None)
    return default if value is None else value


class PlayerAdapter:
    """EntityAdapter for players (the ``@player`` admin surface).

    Tests may inject a ``rank_system``/``registry`` double and a
    ``players_provider`` callable enumerating the live player
    characters; production resolves everything lazily.
    """

    entity_key = "player"

    # --- grammar contract (design per-entity matrix row for @player) ---
    supported_verbs = frozenset({"list", "show", "set"})
    opt_outs: dict[str, str] = {
        "spawn": _SPAWN_OPT_OUT,
        "destroy": _DESTROY_OPT_OUT,
        "def list": _NO_DEF_DOMAIN_REASON,
        "def show": _NO_DEF_DOMAIN_REASON,
        "def set": _NO_DEF_DOMAIN_REASON,
        "def reset": _NO_DEF_DOMAIN_REASON,
        "def diff": _NO_DEF_DOMAIN_REASON,
    }
    extra_verbs: dict[str, str] = {}
    #: Migration aliases (Requirement 11.5, design D5): the legacy verb
    #: forms. Argument reshaping (legacy ``level <N> [player]`` →
    #: canonical ``set <target> level <N>``) happens on the router
    #: subclass before the shared alias dispatch runs.
    aliases: dict[str, str] = {"level": "set", "rank": "set"}
    #: Verb-tier escalation (Requirement 8.7): the legacy ``level``/
    #: ``rank`` writes were Admin-gated; ``set`` inherits that tier.
    verb_perms: dict[str, str] = {"set": "Admin"}

    def __init__(self, rank_system: Any | None = None,
                 registry: Any | None = None,
                 players_provider: Callable[[], list] | None = None) -> None:
        self._rank_system = rank_system
        self._registry = registry
        self._players_provider = players_provider

    # ------------------------------------------------------------------ #
    #  Lazy system access (no live game required to construct)
    # ------------------------------------------------------------------ #

    def _system(self) -> Any | None:
        """The injected rank_system double, else the live RankSystem."""
        return live_service("rank_system", self._rank_system)

    def _live_registry(self) -> Any | None:
        """Injected double, else services facade, else the singleton."""
        return live_registry(self._registry)

    def _rank_name(self, rank_num: Any) -> str:
        """The rank's display name from ``DataRegistry.ranks``, or a
        ``Rank N`` placeholder (mirrors the legacy lookup)."""
        fallback = f"Rank {rank_num}"
        registry = self._live_registry()
        ranks = getattr(registry, "ranks", None) if registry else None
        if not ranks:
            return fallback
        try:
            for rank_def in ranks:
                if getattr(rank_def, "level", None) == rank_num:
                    return getattr(rank_def, "name", None) or fallback
        except Exception:  # noqa: BLE001 - rendering never breaks a verb
            pass
        return fallback

    # ------------------------------------------------------------------ #
    #  Field schema (instance plane)
    # ------------------------------------------------------------------ #

    def instance_fields(self) -> dict[str, FieldSpec]:
        """Modifiable player fields — both written through the existing
        rank-system progression path inside :meth:`update`.

        ``level``: int with STATIC bounds 1–``MAX_LEVEL`` (task 7.3 /
        design matrix: 1–100). ``rank``: enum over the numeric rank ids
        1–``NUM_RANKS`` (Requirement 3.9 valid-values errors come from
        the enum contract).
        """
        specs = (
            FieldSpec(name="level", kind="int", min_value=1,
                      max_value=MAX_LEVEL, perm="Admin"),
            FieldSpec(name="rank", kind="enum",
                      enum_values=_RANK_ENUM_VALUES, perm="Admin"),
        )
        return {spec.name: spec for spec in specs}

    def definition_fields(self) -> dict[str, FieldSpec]:
        """No definition plane: players have no YAML definition domain."""
        return {}

    # ------------------------------------------------------------------ #
    #  Listing + resolution (instance plane)
    # ------------------------------------------------------------------ #

    def _players(self) -> list:
        """Every live player character, sorted by key (stable order).

        Uses the injected ``players_provider`` when present; production
        queries the DB by the ``combat_xp`` marker attribute — the same
        enumeration the existing admin reset path uses.
        """
        if self._players_provider is not None:
            try:
                players = list(self._players_provider() or [])
            except Exception:  # noqa: BLE001 - reads never break a verb
                return []
        else:
            try:
                from evennia.objects.models import ObjectDB

                players = list(
                    ObjectDB.objects.filter(db_attributes__db_key="combat_xp")
                )
            except Exception:  # noqa: BLE001 - no DB under test stubs
                return []
        return sorted(players, key=lambda p: str(getattr(p, "key", "")))

    def _row(self, index: int, player: Any) -> InstanceRow:
        """One live player as an InstanceRow (list output + #N cache)."""
        key = str(getattr(player, "key", "?"))
        level = _db_field(player, "level", 1)
        rank_num = _db_field(player, "rank_level", "?")
        xp = _db_field(player, "combat_xp", 0)
        summary = (
            f"{key} — level {level}, rank {rank_num} "
            f"({self._rank_name(rank_num)}), XP {xp}"
        )
        return InstanceRow(index=index, key=key, name=key,
                           summary=summary, ref=player)

    def _candidate_rows(self, caller: Any) -> list[InstanceRow]:
        return [
            self._row(i, player)
            for i, player in enumerate(self._players(), start=1)
        ]

    def list_instances(self, caller: Any, filter_str: str
                       ) -> list[InstanceRow]:
        """Live player characters as indexed rows (Requirement 4.1)."""
        filt = (filter_str or "").strip().lower()
        rows: list[InstanceRow] = []
        for player in self._players():
            if filt and filt not in str(getattr(player, "key", "")).lower():
                continue
            rows.append(self._row(len(rows) + 1, player))
        return rows

    def resolve_instance(self, caller: Any, token: str) -> Resolution:
        """Resolve *token* per the uniform grammar over live players.

        ``me``/``self`` resolve to the caller (the legacy verbs' default
        target when ``[player]`` was omitted). ``#N`` indexes the
        caller's player List_Cache; key/name/prefix tiers run over the
        enumerated live players. When those tiers miss, the token falls
        back to the caller-scoped player search — the same resolution
        path every legacy admin command used — so partially-connected
        environments (and test doubles without a DB) still resolve.
        """
        token = (token or "").strip()
        if token.lower() in ("me", "self"):
            return Resolution(ok=True, target=caller)
        rows = LIST_CACHE.get(caller, self.entity_key)
        primary = resolve_instance_token(
            token, rows=rows, candidates=self._candidate_rows(caller)
        )
        if primary.ok or token.startswith("#"):
            return primary
        scope = resolve_player_scope(caller, token)
        if scope.ok and scope.target is not None:
            return Resolution(ok=True, target=scope.target)
        return primary

    # ------------------------------------------------------------------ #
    #  Instance CRUD hooks (writes via the existing progression path)
    # ------------------------------------------------------------------ #

    def create(self, caller: Any, def_token: str, kwargs: dict
               ) -> CreateResult:
        """``spawn`` is opted out — defensive refusal should anything
        bypass the router's opt-out dispatch (no state change)."""
        return CreateResult(ok=False, error=_SPAWN_OPT_OUT)

    def read(self, caller: Any, player: Any) -> ShowReport:
        """``show``: identity header, progression state, modifiable
        fields (Requirement 4.3)."""
        key = str(getattr(player, "key", "?"))
        level = _db_field(player, "level", 1)
        rank_num = _db_field(player, "rank_level", "?")
        rank_name = self._rank_name(rank_num)
        xp = _db_field(player, "combat_xp", 0)

        state_lines = [
            f"Level: {level}    Rank: {rank_num} ({rank_name})    "
            f"XP: {xp}",
        ]
        values = {"level": level, "rank": rank_num}
        fields = [
            (spec, values.get(spec.name, "—"), False)
            for spec in self.instance_fields().values()
        ]
        return ShowReport(
            header=f"{key} — player ({rank_name})",
            state_lines=state_lines,
            fields=fields,
            staleness_note=None,  # no definition domain to drift from
        )

    def _apply_progression(self, player: Any, level: int,
                           rank_num: int) -> None:
        """The EXISTING single-writer progression path both legacy verbs
        used: re-stamp XP to the level's threshold, write ``db.level``/
        ``db.rank_level``, then let ``check_promotion`` recompute the
        derived rank events (tech unlocks, agent-cap adjustments)."""
        system = self._system()
        if system is not None:
            player.db.combat_xp = system.xp_for_level(level)
        player.db.level = level
        player.db.rank_level = rank_num
        if system is not None:
            system.check_promotion(player)

    def update(self, caller: Any, player: Any, field: str, value: Any
               ) -> SetResult:
        """``set``: bounded write through the existing progression path
        (Requirements 3.5, 3.6). The router already coerced and clamped
        *value*; ``level`` re-clamps defensively into 1–``MAX_LEVEL``
        (the SetResult contract must hold whoever calls ``update``)."""
        name = str(getattr(player, "key", None) or "target")
        spec = self.instance_fields().get(field)
        if spec is None:
            valid = ", ".join(sorted(self.instance_fields()))
            return SetResult.fail(
                field, value,
                f"'{field}' is not a modifiable player field; "
                f"settable: {valid}",
            )
        if not hasattr(player, "db") or getattr(player, "db", None) is None:
            return SetResult.fail(
                field, value, f"{name} is not a valid player character"
            )

        from world.systems.rank_system import (
            level_range_for_rank,
            rank_from_level,
        )

        if field == "level":
            try:
                requested = int(value)
            except (TypeError, ValueError):
                return SetResult.fail(
                    field, value, f"level must be a number (got '{value}')"
                )
            applied = max(1, min(requested, MAX_LEVEL))
            self._apply_progression(player, applied,
                                    rank_from_level(applied))
            return SetResult(ok=True, field=field, requested=requested,
                             applied=applied,
                             clamped=(applied != requested))

        # field == "rank": enum-validated upstream; validate defensively.
        text = str(value).strip()
        if text not in _RANK_ENUM_VALUES:
            return SetResult.fail(
                field, value,
                f"'{value}' is not a valid value for 'rank' — "
                f"valid values: {', '.join(_RANK_ENUM_VALUES)}",
            )
        rank_id = int(text)
        level, _ = level_range_for_rank(rank_id)
        self._apply_progression(player, level, rank_id)
        return SetResult(ok=True, field=field, requested=value,
                         applied=rank_id, clamped=False)

    def delete(self, caller: Any, player: Any) -> DeleteResult:
        """``destroy`` is opted out — defensive refusal (no state
        change) should anything bypass the router's opt-out dispatch."""
        return DeleteResult(ok=False, error=_DESTROY_OPT_OUT)

    # ------------------------------------------------------------------ #
    #  Definition scope (opted out — no YAML definition domain)
    # ------------------------------------------------------------------ #

    def def_registry_dict(self) -> None:
        """Players have no definition registry (def scope is opted out)."""
        return None

    def def_resolve(self, token: str) -> None:
        """No definition domain — nothing to resolve."""
        return None
