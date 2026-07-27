"""
StatAdapter — the ``@stat`` EntityAdapter (unified-admin-crud Phase 3).

Migrates the combat-stat admin surface onto the unified grammar
(Requirements 1.5, 11.5, 11.6). ``@stat`` is the odd one out among the
adapters: it does not manage a spawnable *collection* of instances — it
edits the combat/progression fields of ONE named unit (a player, an
agent, or an enemy NPC), defaulting to the caller. So:

- **``show`` + ``set`` are the only core verbs** (design per-entity
  matrix row for ``@stat``). ``set`` exposes exactly the legacy
  allowlist — the combat/progression fields ``@stat set`` always
  guarded (hp, hp_max, combat_xp, level, rank_level, kills, deaths) —
  so a typo can't clobber structural attrs (coords, owner, alliance
  pointers).
- **No backing system**: unlike agents/buildings/items, combat stats
  are raw ``db`` attributes the combat/regen/rank systems *read*; there
  is no single-writer service to route through. :meth:`update` writes
  the ``db`` field directly and reproduces the three side effects the
  legacy named subcommands carried: setting positive ``hp`` revives a
  downed unit; ``hp_max`` tops a full unit up to the new ceiling (and
  clamps an over-max unit down); ``combat_xp`` re-derives level/rank
  from the XP curve (``recompute_progression``).
- **``list``/``spawn``/``destroy`` and the whole ``def`` scope are
  OPTED OUT** with reasons pointing at the supported path — stats are
  neither a listable roster nor spawnable nor deletable (you delete the
  *unit*, not its stats), and they have no YAML definition domain.
- **Caller-default resolution**: an empty token (or ``me``/``self``)
  resolves to the caller — the legacy "defaults to you" behavior — so
  ``@stat show`` and the reshaped ``@stat hp <N>`` still act on the
  operator. A name/prefix token searches live combat units (player OR
  NPC) the same way the legacy ``@stat`` resolver did.

The migration aliases (``hp``/``maxhp``/``xp`` — Requirement 11.5) are
VALUE-first (``hp <N> [target]``) while the unified ``set`` is
TARGET-first (``set <target> <field> <value>``); the reshaping happens
in :class:`~commands.admin_commands.CmdAdminStat`'s ``_dispatch_alias``
override, which builds a canonical ``set`` string and dispatches it
through the shared write path so state/perm/output/audit stay identical
(Requirement 11.1). This adapter just declares the aliases and owns the
field schema + write semantics.

No live game is required to construct or register the adapter — target
resolution reaches the world only lazily (via the caller's own
``search`` and, as a fallback, ``evennia.search_object``).
"""

from __future__ import annotations

from typing import Any

from world.admin.resolution import Resolution
from world.admin.types import (
    FieldSpec,
    InstanceRow,
    SetResult,
    ShowReport,
    resolve_bounds,
)
from world.constants import MAX_LEVEL, NUM_RANKS

#: The def-scope opt-out reason (with its pointer to the supported path),
#: shared verbatim by all five def verbs (Requirements 1.5, 11.6).
_NO_DEF_DOMAIN_REASON = (
    "combat stats have no YAML definition domain — they are live per-unit "
    "fields; use '@stat show <target>' / '@stat set <target> <field> "
    "<value>' (target defaults to you)"
)

#: Instance-plane opt-out reasons (each names the supported alternative).
_NO_LIST_REASON = (
    "stats are fields on a named unit, not a listable instance collection — "
    "use '@stat show <target>' (target defaults to you), or '@agent list' / "
    "'@building list' for instance rosters"
)
_NO_SPAWN_REASON = (
    "combat stats are attributes of existing units, not spawnable — create "
    "units via '@agent spawn' or '@outpost spawn', then edit their stats "
    "with '@stat set'"
)
_NO_DESTROY_REASON = (
    "'@stat' edits a unit's fields; it cannot delete the unit — use "
    "'@agent destroy', '@outpost destroy', or 'obliterate' instead"
)


def _db(target: Any) -> Any:
    """The unit's attribute bag (``db``), or ``None``."""
    return getattr(target, "db", None)


def _stat(target: Any, name: str, default: Any = None) -> Any:
    """Best-effort read of one ``db`` field off a live combat unit."""
    value = getattr(_db(target), name, None)
    return default if value is None else value


class StatAdapter:
    """EntityAdapter for combat stats (the ``@stat`` admin surface).

    Constructing and registering the adapter needs no live game; target
    resolution reaches the world only when a verb actually runs. Tests
    may inject a ``resolver`` callable ``(caller, token) -> list`` to
    supply candidate units without a booted server.
    """

    entity_key = "stat"

    # --- grammar contract (design per-entity matrix row for @stat) ---
    supported_verbs = frozenset({"show", "set"})
    opt_outs: dict[str, str] = {
        "list": _NO_LIST_REASON,
        "spawn": _NO_SPAWN_REASON,
        "destroy": _NO_DESTROY_REASON,
        "def list": _NO_DEF_DOMAIN_REASON,
        "def show": _NO_DEF_DOMAIN_REASON,
        "def set": _NO_DEF_DOMAIN_REASON,
        "def reset": _NO_DEF_DOMAIN_REASON,
        "def diff": _NO_DEF_DOMAIN_REASON,
    }
    extra_verbs: dict[str, str] = {}
    #: Migration aliases (Requirement 11.5): the legacy VALUE-first stat
    #: verbs. The value→target reshaping lives in the router's
    #: ``_dispatch_alias`` override; here they simply point at ``set``.
    aliases: dict[str, str] = {"hp": "set", "maxhp": "set", "xp": "set"}
    #: Verb-tier escalation (Requirement 8.7): the legacy stat mutations
    #: were Admin-gated; ``set`` (and thus the hp/maxhp/xp aliases that
    #: dispatch through it) follows that convention. ``show`` stays at
    #: the Builder floor.
    verb_perms = {"set": "Admin"}

    #: Field name the legacy alias verbs map onto (used by the router's
    #: reshaping override, exposed here so the mapping lives with the
    #: schema it targets).
    ALIAS_FIELDS = {"hp": "hp", "maxhp": "hp_max", "xp": "combat_xp"}

    def __init__(self, resolver: Any | None = None) -> None:
        self._resolver = resolver

    # ------------------------------------------------------------------ #
    #  Field schema (instance plane)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _hp_bounds(target: Any) -> tuple[float | None, float | None]:
        """Dynamic ``hp`` bounds from the TARGET's current state
        (Requirement 3.4): 0 up to its own ``hp_max`` (unbounded high
        when the unit carries no ``hp_max``)."""
        hp_max = _stat(target, "hp_max")
        try:
            return (0, int(hp_max)) if hp_max is not None else (0, None)
        except (TypeError, ValueError):
            return (0, None)

    def instance_fields(self) -> dict[str, FieldSpec]:
        """The legacy ``@stat set`` allowlist as bounded FieldSpecs.

        Restricted to combat/progression fields (mirrors the legacy
        ``_SETTABLE`` set) so a typo can't clobber structural attrs.
        ``hp`` clamps to the target's own ``hp_max``; ``level`` and
        ``rank_level`` clamp to their curve maxima.
        """
        specs = (
            FieldSpec(name="hp", kind="int", perm="Admin",
                      dynamic_bounds=self._hp_bounds),
            FieldSpec(name="hp_max", kind="int", min_value=1, perm="Admin"),
            FieldSpec(name="combat_xp", kind="int", min_value=0,
                      perm="Admin"),
            FieldSpec(name="level", kind="int", min_value=1,
                      max_value=MAX_LEVEL, perm="Admin"),
            FieldSpec(name="rank_level", kind="int", min_value=1,
                      max_value=NUM_RANKS, perm="Admin"),
            FieldSpec(name="kills", kind="int", min_value=0, perm="Admin"),
            FieldSpec(name="deaths", kind="int", min_value=0, perm="Admin"),
        )
        return {spec.name: spec for spec in specs}

    def definition_fields(self) -> dict[str, FieldSpec]:
        """No definition plane: combat stats have no YAML domain."""
        return {}

    # ------------------------------------------------------------------ #
    #  Listing + resolution (instance plane)
    # ------------------------------------------------------------------ #

    def list_instances(self, caller: Any, filter_str: str
                       ) -> list[InstanceRow]:
        """``list`` is opted out — there is no stat roster to enumerate."""
        return []

    def _search_units(self, caller: Any, name: str) -> list:
        """Live combat units matching *name* (player OR NPC), minus the
        caller.

        Mirrors the legacy ``@stat`` resolver: the caller's own
        ``search`` (planet-local name/prefix/alias match) first, then a
        global ``evennia.search_object`` exact-key fallback for units on
        other planets. Injected ``resolver`` short-circuits both for
        tests. Never raises — a stubbed/absent search yields no matches.
        """
        if self._resolver is not None:
            return [m for m in (self._resolver(caller, name) or [])
                    if m is not caller]

        matches: list = []
        search = getattr(caller, "search", None)
        if callable(search):
            try:
                res = search(name, quiet=True)
            except TypeError:  # a search without the quiet kwarg
                res = search(name)
            except Exception:  # noqa: BLE001 - reads must never break a verb
                res = None
            if res:
                matches = list(res) if isinstance(res, (list, tuple)) \
                    else [res]

        if not matches:
            try:
                from evennia import search_object

                matches = list(search_object(name) or [])
            except Exception:  # noqa: BLE001 - no global search in stubs
                matches = []

        return [m for m in matches if m is not caller]

    def resolve_instance(self, caller: Any, token: str) -> Resolution:
        """Resolve *token* to a live combat unit (Requirement 11.6).

        An empty token — or ``me``/``self`` — resolves to the caller
        (the legacy "defaults to you" behavior, so ``@stat show`` and the
        reshaped ``@stat hp <N>`` still act on the operator). Any other
        token searches live units by name/prefix; zero matches is a
        not-found error, several is an ambiguity error listing the
        co-named candidates — resolution never guesses (Requirement 2.3).
        """
        token = (token or "").strip()
        if not token or token.lower() in ("me", "self"):
            return Resolution(ok=True, target=caller)

        matches = self._search_units(caller, token)
        if not matches:
            return Resolution(
                ok=False,
                error=(f"No unit named '{token}' found — use a name or "
                       "unambiguous prefix (or 'me' for yourself)."),
            )
        if len(matches) == 1:
            return Resolution(ok=True, target=matches[0])

        names = tuple(str(getattr(m, "key", "?")) for m in matches)
        return Resolution(
            ok=False,
            error=(f"Multiple units match '{token}': {', '.join(names)}. "
                   "Be more specific."),
            candidates=names,
        )

    # ------------------------------------------------------------------ #
    #  Instance CRUD hooks
    # ------------------------------------------------------------------ #

    def create(self, caller: Any, def_token: str, kwargs: dict) -> Any:
        """``spawn`` is opted out — stats are not spawnable."""
        return None

    def read(self, caller: Any, target: Any) -> ShowReport:
        """``show``: identity header, core combat stats, modifiable
        fields (mirrors the legacy ``@stat show`` readout)."""
        name = str(getattr(target, "key", None) or "unit")
        hp = _stat(target, "hp", "?")
        hp_max = _stat(target, "hp_max", "?")
        level = _stat(target, "level", "?")
        rank_level = _stat(target, "rank_level", "?")

        state_lines = [
            f"HP: {hp}/{hp_max}    XP: {_stat(target, 'combat_xp', 0)}",
            f"Level: {level}  (rank {rank_level})",
            f"Kills: {_stat(target, 'kills', 0)}"
            f"    Deaths: {_stat(target, 'deaths', 0)}",
        ]
        if _stat(target, "incapacitated", False):
            state_lines.append(
                f"|rIncapacitated|n (respawn in "
                f"{_stat(target, 'respawn_timer', 0)})"
            )

        fields = [
            (spec, _stat(target, spec.name, "—"), False)
            for spec in self.instance_fields().values()
        ]
        return ShowReport(
            header=f"{name} — combat stats",
            state_lines=state_lines,
            fields=fields,
            staleness_note=None,  # no definition domain to drift from
        )

    def update(self, caller: Any, target: Any, field: str, value: Any
               ) -> SetResult:
        """``set``: bounded write of one allowlisted stat, reproducing
        the legacy side effects.

        The router already coerced and clamped *value*; this re-clamps
        defensively (the SetResult contract — ``applied`` always lands
        in-bounds — must hold whoever calls ``update``) and writes the
        ``db`` field directly (stats have no single-writer system). The
        three legacy side effects are preserved:

        - ``hp`` > 0 on a downed unit revives it (clears ``incapacitated``
          + ``respawn_timer``).
        - ``hp_max`` tops a full unit up to the new ceiling, and clamps an
          over-max unit down to it.
        - ``combat_xp`` re-derives level/rank from the curve via
          ``recompute_progression`` (owner-agnostic; skipped for units
          that lack it). No state changes on any failure.
        """
        name = str(getattr(target, "key", None) or "unit")
        spec = self.instance_fields().get(field)
        if spec is None:
            valid = ", ".join(sorted(self.instance_fields()))
            return SetResult.fail(
                field, value,
                f"'{field}' is not a settable stat; settable: {valid}",
            )

        db = _db(target)
        if db is None:
            return SetResult.fail(
                field, value, f"{name} has no attributes to set."
            )

        try:
            requested = int(value)
        except (TypeError, ValueError):
            return SetResult.fail(
                field, value,
                f"value must be a whole number (got '{value}')",
            )

        # Defensive re-clamp (dynamic bounds for hp; static otherwise) —
        # the shared bound-selection helper, same source the router clamps
        # and renders with.
        lo, hi = resolve_bounds(spec, target)
        applied = requested
        if lo is not None and applied < lo:
            applied = int(lo)
        if hi is not None and applied > hi:
            applied = int(hi)

        try:
            self._apply_field(target, db, field, applied)
        except Exception as exc:  # noqa: BLE001 - relay write-path failures
            return SetResult.fail(
                field, requested,
                f"could not write {field} onto {name}: {exc}",
            )

        return SetResult(ok=True, field=field, requested=requested,
                         applied=applied, clamped=(applied != requested))

    @staticmethod
    def _apply_field(target: Any, db: Any, field: str, applied: int) -> None:
        """Write one clamped stat onto *db*, with the legacy side effects."""
        if field == "hp":
            db.hp = applied
            # Setting positive HP on a downed unit revives it.
            if applied > 0 and getattr(db, "incapacitated", False):
                db.incapacitated = False
                db.respawn_timer = 0
            return
        if field == "hp_max":
            old_max = getattr(db, "hp_max", 0) or 0
            cur_hp = getattr(db, "hp", 0) or 0
            db.hp_max = applied
            # Top a full unit up to the new ceiling (so "maxhp 1000" on a
            # full-HP unit reads 1000/1000); clamp an over-max unit down.
            if cur_hp >= old_max or cur_hp > applied:
                db.hp = applied
            return
        if field == "combat_xp":
            db.combat_xp = applied
            # Re-derive level/rank so they stay consistent with XP. The
            # owner-agnostic path (players AND NPCs); skipped for units
            # without it (e.g. bare test doubles).
            recompute = getattr(target, "recompute_progression", None)
            if callable(recompute):
                recompute()
            return
        # level / rank_level / kills / deaths — direct writes (setting
        # level/rank is an explicit operator override; no recompute).
        setattr(db, field, applied)

    def delete(self, caller: Any, target: Any) -> Any:
        """``destroy`` is opted out — stats are not deletable."""
        return None

    # ------------------------------------------------------------------ #
    #  Definition scope (opted out — no YAML definition domain)
    # ------------------------------------------------------------------ #

    def def_registry_dict(self) -> None:
        """Combat stats have no definition registry (def scope opted out)."""
        return None

    def def_resolve(self, token: str) -> None:
        """No definition domain — nothing to resolve."""
        return None
