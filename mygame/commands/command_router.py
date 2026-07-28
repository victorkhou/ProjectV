"""
Subcommand router base classes for the RTS Combat Overworld.

Provides a consistent dispatch pattern: parse verb → look up handler →
check permission → invoke handler.  Subclasses declare a ``subcommands``
dict mapping verb strings to ``(handler, help_text, required_perm)`` tuples.

The dispatch logic lives in :class:`SubcommandDispatchMixin` so it can be
shared by both the plain (:class:`SubcommandRouter`) and prefix-matching
(:class:`GameSubcommandRouter`) router bases without copy-paste.  The mixin
also exposes two guard helpers every handler needs — :meth:`require_system`
and :meth:`parse_int` — so individual ``sub_*`` methods don't re-implement the
"look up system / msg on failure" and "parse int arg / msg on failure"
boilerplate.

"""

from __future__ import annotations

import dataclasses
import logging

from evennia.commands.command import Command as BaseCommand

from commands.game_commands import GameCommand
from world.admin.adapter_registry import get_registry
from world.admin.def_write import (
    NOT_FOUND,
    OK,
    OVERLAY_ERROR,
    DefWriteTransaction,
)
from world.admin.outcomes import (
    FIELD_SET,
    PERM_DENIED,
    UNKNOWN_FIELD,
    OutcomeRecorder,
)
from world.admin.overlay_store import OverlayStore, OverlayStoreError
from world.admin.resolution import LIST_CACHE, caller_key, resolve_player_scope
from world.admin.show_renderer import fmt_bound, render_show
from world.admin.types import DEF_ID_FIELDS, SetResult, resolve_bounds
from world.data_registry import OVERLAY_RELOAD_LOCK
from world.utils import get_system, require_system

logger = logging.getLogger("mygame.admin")


class SubcommandDispatchMixin(OutcomeRecorder):
    """Shared verb-dispatch behavior + handler guard helpers.

    A pure mixin (extends ``object`` only) so it can be combined with either
    ``BaseCommand`` or ``GameCommand`` without MRO conflicts.  It relies on the
    command instance providing ``self.args``, ``self.caller`` and ``self.key``
    (both Evennia command bases do).

    Via :class:`~world.admin.outcomes.OutcomeRecorder` every dispatch also
    records its *decisions* (see ``record_outcome`` call sites) alongside the
    prose it sends. The messages are unchanged; the record is what lets a test
    assert "this clamped to the upper bound" without quoting the sentence that
    said so.
    """

    # Subclasses override this:
    # subcommands = {
    #     "spawn": (sub_spawn, "Spawn a building", "Builder"),
    #     "destroy": (sub_destroy, "Destroy a building", "Builder"),
    # }
    subcommands: dict = {}

    def func(self):
        verb, rest = self._get_subcommand_and_args()
        if verb is None:
            self._show_help()
            return
        entry = self.subcommands.get(verb)
        if entry is None:
            self._show_error(verb)
            return
        handler, _help_text, perm = entry
        if perm and not self._check_sub_perm(perm, verb):
            return
        handler(self, rest)

    def _get_subcommand_and_args(self) -> tuple:
        """Parse first token as verb, remainder as args. Case-insensitive."""
        raw = self.args.strip()
        if not raw:
            return None, ""
        parts = raw.split(None, 1)
        verb = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""
        return verb, rest

    def _show_help(self):
        """Display help listing all subcommands."""
        lines = [f"|wUsage: {self.key} <subcommand> [args]|n", ""]
        for verb, (_, help_text, perm) in self.subcommands.items():
            perm_tag = f" ({perm}+)" if perm else ""
            lines.append(f"  |c{verb}|n — {help_text}{perm_tag}")
        self.caller.msg("\n".join(lines))

    def _show_error(self, invalid_verb: str):
        """Display error for unknown subcommand with valid list."""
        valid = ", ".join(self.subcommands.keys())
        self.caller.msg(
            f"Unknown subcommand '{invalid_verb}'. "
            f"Available: {valid}"
        )

    def _check_sub_perm(self, perm: str, verb: str) -> bool:
        """Check caller permission; msg on failure. Returns True if allowed."""
        if self.caller.check_permstring(perm):
            return True
        self.record_outcome(PERM_DENIED, required=perm, scope="verb",
                            target=verb)
        self.caller.msg(
            f"Permission denied. {perm}+ required for '{verb}'."
        )
        return False

    def _log_admin(self, verb: str, detail: str):
        """Log admin action: operator, command+verb, target."""
        logger.info(
            "Admin %s: %s %s — %s",
            self.caller.key, self.key, verb, detail,
        )

    # ------------------------------------------------------------------ #
    #  Handler guard helpers (shared by every sub_* method)
    # ------------------------------------------------------------------ #

    def require_system(self, name: str, label: str | None = None):
        """Return game system ``name``, or msg the caller and return ``None``.

        Collapses the ``system = get_system(...); if system is None: msg;
        return`` boilerplate that every handler needs.  Delegates to the single
        :func:`world.utils.require_system` implementation; the generated message
        is ``"{label} unavailable."`` where ``label`` defaults to the system
        name with underscores spaced out (``"agent_system"`` → ``"Agent
        system unavailable."``).

        Args:
            name: System key to look up via ``world.utils.get_system``.
            label: Optional human-readable name for the failure message.

        Returns:
            The system instance, or ``None`` (after messaging the caller).
        """
        return require_system(self.caller, name, label)

    def parse_int(self, raw, label: str = "Agent ID"):
        """Parse ``raw`` as an int, or msg the caller and return ``None``.

        Collapses the ``try: int(x) except ValueError: msg; return`` guard
        repeated across every id/count-parsing handler.  The failure message
        is ``"{label} must be a number."``.

        Args:
            raw: The raw string (or value) to convert.
            label: Subject of the failure message (e.g. ``"Agent ID"``).

        Returns:
            The parsed ``int``, or ``None`` (after messaging the caller).
        """
        try:
            return int(raw)
        except (TypeError, ValueError):
            self.caller.msg(f"{label} must be a number.")
            return None


class SubcommandRouter(SubcommandDispatchMixin, BaseCommand):
    """
    Base class for commands that dispatch to subcommand handler methods.

    Subclasses define a ``subcommands`` dict mapping verb strings to
    ``(handler_method, help_text, required_perm)`` tuples.  All dispatch
    behavior comes from :class:`SubcommandDispatchMixin`.
    """


class AdminSubcommandRouter(SubcommandRouter):
    """
    Base class for admin commands that use subcommand routing.

    Sets help_category to "Admin" and locks to Builder+ (the lowest admin
    level).  Individual subcommands enforce stricter permissions via
    ``_check_sub_perm``.

    """

    help_category = "Admin"
    locks = "cmd:perm(Builder);view:perm(Builder)"


class GameSubcommandRouter(SubcommandDispatchMixin, GameCommand):
    """
    Base class for game commands that use subcommand routing with prefix
    matching.

    Inherits ``GameCommand`` for prefix matching and gets its dispatch logic
    from :class:`SubcommandDispatchMixin` (a pure mixin, so combining it with
    ``GameCommand`` raises no MRO conflict).

    Sets ``help_category = "Game"``.

    """

    help_category = "Game"


# ---------------------------------------------------------------------- #
#  EntityAdminRouter — unified admin CRUD grammar (unified-admin-crud)
# ---------------------------------------------------------------------- #

#: Permission-tier ordering for the per-field escalation check
#: (Requirement 8.4). Unknown tiers rank above everything so a typo'd
#: FieldSpec.perm fails closed (the check always runs) instead of open.
_PERM_RANK: dict[str, int] = {
    "player": 0,
    "helper": 1,
    "builder": 2,
    "admin": 3,
    "developer": 4,
}


def _perm_rank(perm: str) -> int:
    """Numeric rank of a permission tier; unknown tiers rank highest."""
    return _PERM_RANK.get((perm or "").lower(), max(_PERM_RANK.values()) + 1)


def coerce_field_value(spec, raw):
    """Interpret *raw* as *spec*'s declared kind.

    Returns ``(value, None)`` on success or ``(None, error)`` where
    *error* states the expected kind (Requirement 3.8) or, for enum
    fields, lists the valid values (Requirement 3.9).
    """
    text = str(raw).strip()
    if spec.kind == "int":
        try:
            return int(text), None
        except ValueError:
            return None, (
                f"'{raw}' cannot be interpreted as field "
                f"'{spec.name}''s expected kind: int."
            )
    if spec.kind == "float":
        try:
            return float(text), None
        except ValueError:
            return None, (
                f"'{raw}' cannot be interpreted as field "
                f"'{spec.name}''s expected kind: float."
            )
    if spec.kind == "enum":
        valid = spec.enum_values or ()
        if text in valid:
            return text, None
        return None, (
            f"'{raw}' is not a valid value for '{spec.name}' — "
            f"valid values: {', '.join(valid)}."
        )
    # "str" (and any unbounded textual kind): the raw text as-is.
    return text, None


def clamp_field_value(spec, entity, requested):
    """The bounded-set invariant: clamp *requested* into *spec*'s bounds.

    Pure function targeted by the bounded-set property tests (tasks
    1.13/1.14). Returns ``(applied, clamped, lo, hi)``:

    - Bounds are static (``min_value``/``max_value``) or dynamic
      (``spec.dynamic_bounds(entity)`` computed from the target entity's
      current state — Requirement 3.4); dynamic bounds take precedence.
    - Out-of-bounds numeric values clamp to the nearest bound
      (Requirement 3.2); in-bounds or unbounded values pass through
      unchanged (Requirement 3.3).
    - ``clamped == (applied != requested)`` (the SetResult contract).
    - Non-numeric kinds never clamp; ``lo``/``hi`` are ``None``.
    """
    if spec.kind not in ("int", "float"):
        return requested, False, None, None
    lo, hi = resolve_bounds(spec, entity)
    applied = requested
    if lo is not None and applied < lo:
        applied = lo
    if hi is not None and applied > hi:
        applied = hi
    return applied, applied != requested, lo, hi


#: Pending multi-target destroy confirmations, keyed by
#: (caller, entity_key). A multi-target ``destroy`` stores its resolved
#: targets here and deletes nothing until the caller runs
#: ``destroy confirm`` (Requirement 4.5); ``destroy cancel`` (or issuing
#: a different destroy) discards the entry with no state change.
_PENDING_DESTROY: dict = {}


class EntityAdminRouter(AdminSubcommandRouter):
    """Admin router base driven by an EntityAdapter's grammar contract.

    Subclasses set ``adapter_key`` (e.g. ``"item"``); the ``subcommands``
    dict is auto-built from the adapter registered under that key in the
    :class:`~world.admin.adapter_registry.AdapterRegistry`:

    - Core verbs the adapter supports get the shared handlers below at
      their canonical tiers (read verbs at Builder, ``def set``/``def
      reset`` at Admin — Requirements 8.1–8.3).
    - Opted-out core verbs dispatch to a handler that surfaces the
      adapter's declared reason (which carries the pointer to the
      supported path) and changes no state (Requirement 1.5).
    - ``adapter.extra_verbs`` register alongside the core verbs with
      their declared help text (Requirement 1.6); their handlers live on
      the subclass as ``sub_<verb>`` methods.
    - ``adapter.aliases`` dispatch to the canonical handler — identical
      state changes, permission outcomes, output, and audit entries —
      plus a one-line deprecation note naming both spellings
      (Requirements 11.1, 11.2).
    - Unknown verbs error with the list of available verbs and change no
      state (Requirement 1.8).

    The Builder floor is unchanged (``locks`` inherited from
    :class:`AdminSubcommandRouter`). The ``def`` keyword pivots into the
    Definition_Scope sub-dispatch, whose read verbs (``def list``,
    ``def show``, ``def diff``) are functional here; the mutating
    instance verbs (``spawn``/``set``/``destroy``) and the ``def set``/
    ``def reset`` flow are registered at their tiers but delegate to
    ``_do_*`` methods that later phases implement.
    """

    #: Subclasses set this to the adapter's entity_key ("item", ...).
    adapter_key: str = ""

    #: When set (e.g. ``"me"``), an omitted ``show`` target defaults to
    #: this token instead of erroring with usage — the legacy "defaults to
    #: you" behavior ``@stat``/``@resource`` keep. ``None`` (the default)
    #: preserves the explicit-target contract every other entity uses.
    default_show_target: str | None = None

    #: Canonical permission tier per core verb (design Verb Grammar
    #: Table). Adapters may override a verb's tier via an optional
    #: ``verb_perms`` mapping (Requirement 8.7) — except the def-write
    #: verbs, which are Admin on every entity (Requirement 8.3).
    CORE_VERB_PERMS: dict = {
        "list": "Builder",
        "spawn": "Builder",
        "show": "Builder",
        "set": "Builder",
        "destroy": "Builder",
        "def list": "Builder",
        "def show": "Builder",
        "def set": "Admin",
        "def reset": "Admin",
        "def diff": "Builder",
    }

    _INSTANCE_VERBS = ("list", "spawn", "show", "set", "destroy")
    _DEF_SUBVERBS = ("list", "show", "set", "reset", "diff")

    # ------------------------------------------------------------------ #
    #  Adapter access + lazy subcommand construction
    # ------------------------------------------------------------------ #

    def _adapter_registry(self):
        """The AdapterRegistry to resolve adapters through (test hook)."""
        return get_registry()

    @property
    def adapter(self):
        """The EntityAdapter for ``adapter_key`` (cached per invocation)."""
        cached = getattr(self, "_adapter_cache", None)
        if cached is None:
            cached = self._adapter_registry().get(self.adapter_key)
            self._adapter_cache = cached
        return cached

    @property
    def subcommands(self) -> dict:
        """The dispatch dict, auto-built from the adapter's contract."""
        cached = getattr(self, "_subcommands_cache", None)
        if cached is None:
            cached = self._build_subcommands()
            self._subcommands_cache = cached
        return cached

    def func(self):
        if self.adapter is None:
            self.caller.msg(
                f"No entity adapter is registered for "
                f"'{self.adapter_key or self.key}'."
            )
            return
        super().func()

    def _verb_perm(self, verb: str) -> str:
        """The tier for *verb*: canonical, unless the adapter escalates it.

        ``def set``/``def reset`` are pinned to Admin on every entity
        (Requirement 8.3) and cannot be lowered by an adapter override.
        """
        default = self.CORE_VERB_PERMS.get(verb, "Builder")
        if verb in ("def set", "def reset"):
            return default
        overrides = getattr(self.adapter, "verb_perms", None) or {}
        return overrides.get(verb, default)

    def _build_subcommands(self) -> dict:
        adapter = self.adapter
        if adapter is None:
            return {}
        cls = type(self)
        subs: dict = {}

        core_help = {
            "list": "List live instances (optional filter)",
            "spawn": "Create an instance from a definition",
            "show": "Full readout of one instance",
            "set": "Set a modifiable field (bounded write)",
            "destroy": "Delete an instance",
        }
        core_handlers = {
            "list": cls._sub_list,
            "spawn": cls._sub_spawn,
            "show": cls._sub_show,
            "set": cls._sub_set,
            "destroy": cls._sub_destroy,
        }

        def _optout_handler(verb):
            def handler(cmd, rest):
                cmd._msg_opt_out(verb)
            return handler

        for verb in self._INSTANCE_VERBS:
            if verb in adapter.supported_verbs:
                subs[verb] = (
                    core_handlers[verb], core_help[verb],
                    self._verb_perm(verb),
                )
            elif verb in adapter.opt_outs:
                subs[verb] = (
                    _optout_handler(verb),
                    f"Not available — {adapter.opt_outs[verb]}",
                    "",
                )

        # The `def` keyword pivots into the Definition_Scope sub-dispatch;
        # each def verb's perm and opt-out is checked inside _sub_def.
        subs["def"] = (
            cls._sub_def,
            "Definition scope: def list | show <key> | set <key> <field> "
            "<value> | reset <key> [field] | diff",
            "",
        )

        # Adapter-declared extra verbs — handlers live on the subclass as
        # sub_<verb> methods (Requirement 1.6).
        def _missing_handler(verb):
            def handler(cmd, rest):
                cmd.caller.msg(
                    f"'{verb}' is declared by the adapter but "
                    f"{cmd.__class__.__name__} defines no sub_{verb} handler."
                )
            return handler

        for verb, help_text in adapter.extra_verbs.items():
            method = getattr(cls, f"sub_{verb}", None)
            subs[verb] = (
                method if method is not None else _missing_handler(verb),
                help_text,
                self._verb_perm(verb),
            )

        # Migration aliases: old spelling -> canonical verb, dispatched
        # through the canonical handler with a deprecation note
        # (Requirements 11.1, 11.2). Perm is checked against the CANONICAL
        # verb inside _dispatch_alias so outcomes are identical.
        def _alias_handler(alias, canonical):
            def handler(cmd, rest):
                cmd._dispatch_alias(alias, canonical, rest)
            return handler

        for alias, canonical in adapter.aliases.items():
            subs[alias] = (
                _alias_handler(alias, canonical),
                f"Alias of '{canonical}' (deprecated)",
                "",
            )

        return subs

    # ------------------------------------------------------------------ #
    #  Dispatch pieces: opt-outs, aliases, unknown verbs, def scope
    # ------------------------------------------------------------------ #

    def _msg_opt_out(self, verb: str):
        """Surface the adapter's opt-out reason; no state change (R1.5).

        The reason string is declared with its pointer to the supported
        alternative path (enforced non-empty at registration).
        """
        reason = self.adapter.opt_outs.get(verb, "not supported")
        self.caller.msg(f"{self.key} {verb} is not available: {reason}")

    def _available_verbs(self) -> list:
        """Every invocable spelling, for unknown-verb errors (R1.8)."""
        adapter = self.adapter
        verbs = [v for v in self._INSTANCE_VERBS
                 if v in adapter.supported_verbs]
        verbs += [f"def {sub}" for sub in self._DEF_SUBVERBS
                  if f"def {sub}" in adapter.supported_verbs]
        verbs += list(adapter.extra_verbs)
        verbs += list(adapter.aliases)
        return verbs

    def _show_error(self, invalid_verb: str):
        """Unknown verb: list the available verbs, change nothing (R1.8)."""
        valid = ", ".join(self._available_verbs())
        self.caller.msg(
            f"Unknown subcommand '{invalid_verb}'. Available: {valid}"
        )

    def _dispatch_alias(self, alias: str, canonical: str, rest: str):
        """Dispatch a Migration_Alias to its canonical handler.

        Emits the one-line deprecation note naming both spellings
        (Requirement 11.2), then runs the canonical verb's permission
        check and handler so state changes, perm outcomes, output, and
        audit entries are identical to the canonical spelling
        (Requirement 11.1).
        """
        self.caller.msg(
            f"Note: '{alias}' is deprecated — use "
            f"'{self.key} {canonical}' instead."
        )
        if canonical.startswith("def "):
            sub_args = canonical[len("def "):]
            self._sub_def(f"{sub_args} {rest}".strip())
            return
        entry = self.subcommands.get(canonical)
        if entry is None:
            self.caller.msg(
                f"Alias '{alias}' points at unknown verb '{canonical}'."
            )
            return
        handler, _help_text, perm = entry
        if perm and not self._check_sub_perm(perm, canonical):
            return
        handler(self, rest)

    def _sub_def(self, rest: str):
        """The Definition_Scope sub-dispatch (`def <verb> ...`)."""
        raw = (rest or "").strip()
        available = [f"def {sub}" for sub in self._DEF_SUBVERBS
                     if f"def {sub}" in self.adapter.supported_verbs]
        if not raw:
            listing = ", ".join(available) if available else "(none)"
            self.caller.msg(
                f"Usage: {self.key} def <verb> [args]. Available: {listing}"
            )
            return
        parts = raw.split(None, 1)
        sub = parts[0].lower()
        subrest = parts[1] if len(parts) > 1 else ""
        if sub not in self._DEF_SUBVERBS:
            listing = ", ".join(available) if available else "(none)"
            self.caller.msg(
                f"Unknown def subcommand 'def {sub}'. Available: {listing}"
            )
            return
        verb = f"def {sub}"
        if verb in self.adapter.opt_outs:
            self._msg_opt_out(verb)
            return
        if verb not in self.adapter.supported_verbs:
            # Unreachable for registry-validated adapters; guard anyway.
            listing = ", ".join(available) if available else "(none)"
            self.caller.msg(
                f"'{verb}' is not available here. Available: {listing}"
            )
            return
        perm = self._verb_perm(verb)
        if perm and not self._check_sub_perm(perm, verb):
            return
        handlers = {
            "list": type(self)._def_list,
            "show": type(self)._def_show,
            "set": type(self)._def_set,
            "reset": type(self)._def_reset,
            "diff": type(self)._def_diff,
        }
        handlers[sub](self, subrest)

    # ------------------------------------------------------------------ #
    #  Shared read handlers — instance plane
    # ------------------------------------------------------------------ #

    def _sub_list(self, rest: str):
        """``list [filter]``: indexed instance rows; replaces the caller's
        List_Cache with exactly the displayed rows (Requirements 4.1, 4.6).
        """
        adapter = self.adapter
        filter_str = (rest or "").strip()
        rows = list(adapter.list_instances(self.caller, filter_str))
        LIST_CACHE.store(self.caller, adapter.entity_key, rows)
        if not rows:
            suffix = f" matching '{filter_str}'" if filter_str else ""
            self.caller.msg(
                f"No {adapter.entity_key} instances found{suffix}."
            )
            return
        lines = [f"|w{adapter.entity_key.capitalize()} instances "
                 f"({len(rows)}):|n"]
        for row in rows:
            lines.append(f"  |c#{row.index}|n {row.summary}")
        self.caller.msg("\n".join(lines))

    def _sub_show(self, rest: str):
        """``show <target>``: resolve via the Resolution_Engine, render the
        adapter's ShowReport (Requirement 4.3).

        An entity may set ``default_show_target`` (e.g. ``"me"``) to keep
        the legacy "defaults to you" behavior when the target is omitted;
        otherwise an empty token is a usage message (explicit-target
        contract)."""
        token = (rest or "").strip() or (self.default_show_target or "")
        if not token:
            self.caller.msg(f"Usage: {self.key} show <target>")
            return
        resolution = self.adapter.resolve_instance(self.caller, token)
        if not resolution.ok:
            self.caller.msg(resolution.error)
            return
        report = self.adapter.read(self.caller, resolution.target)
        self.caller.msg(render_show(report, resolution.target))

    # ------------------------------------------------------------------ #
    #  Shared read handlers — definition plane
    # ------------------------------------------------------------------ #

    def _overlay_store(self) -> OverlayStore:
        """The OverlayStore serving def-scope reads (test hook)."""
        return OverlayStore()

    def _def_domain(self) -> str | None:
        """The overlay/definition domain for this entity, or None.

        The single source is the adapter's ``def_domain`` attribute; an
        adapter that declares none (or an empty one) has no overlay domain,
        so ``def show`` does no overlay lookup and the def-write context is
        unreachable (e.g. ``@planet``, which is read-only by design).
        """
        return getattr(self.adapter, "def_domain", None) or None

    def _def_overrides(self, def_key: str) -> dict:
        """Current overlay overrides for *def_key* (empty on any failure)."""
        domain = self._def_domain()
        if not domain:
            return {}
        try:
            return self._overlay_store().get(domain, def_key)
        except OverlayStoreError:
            return {}

    @staticmethod
    def _definition_key(definition) -> str:
        """The identifying key of a definition object or mapping."""
        for id_field in DEF_ID_FIELDS:
            if isinstance(definition, dict):
                value = definition.get(id_field)
            else:
                value = getattr(definition, id_field, None)
            if value:
                return str(value)
        return str(definition)

    @staticmethod
    def _definition_items(definition) -> list:
        """(name, value) pairs of a definition's fields, for rendering."""
        if dataclasses.is_dataclass(definition):
            return [
                (f.name, getattr(definition, f.name))
                for f in dataclasses.fields(definition)
            ]
        if isinstance(definition, dict):
            return sorted(definition.items())
        return sorted(vars(definition).items())

    def _def_list(self, rest: str):
        """``def list``: definitions in this domain from the merged
        registry (Requirement 5.7)."""
        registry_dict = self.adapter.def_registry_dict()
        if registry_dict is None:
            self.caller.msg(
                f"{self.key} has no definition registry."
            )
            return
        if not registry_dict:
            self.caller.msg(
                f"No {self.adapter.entity_key} definitions loaded."
            )
            return
        lines = [f"|w{self.adapter.entity_key.capitalize()} definitions "
                 f"({len(registry_dict)}):|n"]
        for key in sorted(registry_dict):
            definition = registry_dict[key]
            if isinstance(definition, dict):
                name = definition.get("name", "")
            else:
                name = getattr(definition, "name", "")
            suffix = f" — {name}" if name and name != key else ""
            lines.append(f"  {key}{suffix}")
        self.caller.msg("\n".join(lines))

    def _def_show(self, rest: str):
        """``def show <key>``: merged definition values, overridden fields
        flagged ``*override*`` (Requirement 5.4 rendering)."""
        token = (rest or "").strip()
        if not token:
            self.caller.msg(f"Usage: {self.key} def show <key>")
            return
        definition = self.adapter.def_resolve(token)
        if definition is None:
            self.caller.msg(f"No definition found for '{token}'.")
            return
        def_key = self._definition_key(definition)
        overrides = self._def_overrides(def_key)
        lines = [f"|w{self.adapter.entity_key} definition: {def_key}|n"]
        for name, value in self._definition_items(definition):
            flag = " *override*" if name in overrides else ""
            lines.append(f"  {name}: {value}{flag}")
        note = self._live_instances_note(def_key)
        if note:
            lines.append(note)
        self.caller.msg("\n".join(lines))

    def _live_instances_note(self, def_key: str) -> str:
        """The ``def show`` live-instances note (Requirement 10.4).

        When the adapter can report live-instance existence for a
        definition key — by declaring an optional
        ``has_live_instances(def_key) -> bool`` hook — and at least one
        live instance exists, return the note stating that existing
        instances retain previously stamped values. Adapters without the
        hook (or whose hook fails) produce no note.
        """
        checker = getattr(self.adapter, "has_live_instances", None)
        if checker is None:
            return ""
        try:
            if not checker(def_key):
                return ""
        except Exception:  # noqa: BLE001 - a broken hook must not break show
            return ""
        return (
            "Note: existing instances retain previously stamped values "
            "(definition changes apply on the next lazy read)."
        )

    def _def_diff(self, rest: str):
        """``def diff``: current deviations from base YAML in this domain;
        an empty overlay produces an empty diff (Requirement 5.6)."""
        domain = self._def_domain()
        if not domain:
            self.caller.msg(
                f"{self.key} has no definition domain to diff."
            )
            return
        try:
            domain_diff = self._overlay_store().diff().get(domain) or {}
        except OverlayStoreError as exc:
            self.caller.msg(str(exc))
            return
        if not domain_diff:
            self.caller.msg(
                f"No definition overrides in the '{domain}' domain."
            )
            return
        lines = [f"|wDefinition overrides in '{domain}':|n"]
        for key in sorted(domain_diff):
            fields = domain_diff[key] or {}
            for field in sorted(fields):
                lines.append(f"  {key}.{field} = {fields[field]!r}")
        self.caller.msg("\n".join(lines))

    # ------------------------------------------------------------------ #
    #  Mutating verbs — spawn / set / destroy (task 1.12);
    #  def set / def reset land in task 1.15.
    # ------------------------------------------------------------------ #

    def _sub_spawn(self, rest: str):
        self._do_spawn(rest)

    def _sub_set(self, rest: str):
        self._do_set(rest)

    def _sub_destroy(self, rest: str):
        self._do_destroy(rest)

    def _def_set(self, rest: str):
        self._do_def_set(rest)

    def _def_reset(self, rest: str):
        self._do_def_reset(rest)

    # --- shared helpers -------------------------------------------------- #

    def _audit(self, verb: str, detail: str) -> str:
        """Record one Audit_Log entry; return the response note on failure.

        Exactly one call per successful mutation (Requirement 9.1). An
        audit-write failure leaves the completed mutation applied and
        returns the note the response must carry (Requirement 9.4);
        success returns an empty string.
        """
        try:
            self._log_admin(verb, detail)
            return ""
        except Exception:  # noqa: BLE001 - audit must never undo a mutation
            return " |y(note: audit logging failed)|n"

    @staticmethod
    def _describe_instance(entity) -> str:
        """Identity string for a live instance: ``name (key)`` or best-of."""
        name = getattr(entity, "name", None)
        key = getattr(entity, "key", None)
        if name and key and str(name) != str(key):
            return f"{name} ({key})"
        if name:
            return str(name)
        if key:
            return str(key)
        return str(entity)

    # --- spawn ------------------------------------------------------------ #

    def _do_spawn(self, rest: str):
        """``spawn <def> [k=v ...] [player]`` (Requirements 4.2, 4.7, 4.8)."""
        adapter = self.adapter
        parts = (rest or "").split()
        if not parts:
            self.caller.msg(
                f"Usage: {self.key} spawn <definition> [key=value ...] "
                "[player]"
            )
            return
        def_token = parts[0]
        kwargs: dict = {}
        player_token = None
        for tok in parts[1:]:
            if "=" in tok:
                k, v = tok.split("=", 1)
                kwargs[k] = v
            elif player_token is None:
                player_token = tok
            else:
                self.caller.msg(
                    f"Unexpected argument '{tok}' — usage: {self.key} "
                    "spawn <definition> [key=value ...] [player]"
                )
                return

        # Def-token resolution via the adapter's existing resolver: an
        # unresolved token errors naming the token; nothing is created
        # (Requirement 4.7).
        definition = adapter.def_resolve(def_token)
        if definition is None:
            self.caller.msg(
                f"No definition found for '{def_token}' — nothing created."
            )
            return

        scope = resolve_player_scope(self.caller, player_token)
        if not scope.ok:
            self.caller.msg(scope.error)
            return
        if player_token:
            kwargs["player"] = scope.target

        # Create through the adapter's existing creation path; a path
        # failure (exception, None, or ok=False result) is reported with
        # no further state change (Requirement 4.8).
        try:
            result = adapter.create(self.caller, def_token, kwargs)
        except Exception as exc:  # noqa: BLE001 - relay creation-path errors
            self.caller.msg(f"Spawn failed: {exc}")
            return
        if result is None or getattr(result, "ok", True) is False:
            error = getattr(result, "error", None) or "creation path failed"
            self.caller.msg(f"Spawn failed: {error}")
            return

        created = getattr(result, "instance", result)
        identity = self._describe_instance(created)
        note = self._audit(
            "spawn", f"{adapter.entity_key} '{def_token}' -> {identity}"
        )
        self.caller.msg(f"Spawned {adapter.entity_key}: {identity}.{note}")

    # --- set ---------------------------------------------------------------- #

    def _do_set(self, rest: str):
        """``set <target> <field> <value>`` — the bounded write
        (Requirements 3.2–3.5, 3.7–3.10, 8.4, 8.5, 9.1, 9.3)."""
        adapter = self.adapter
        parts = (rest or "").split(None, 2)
        if len(parts) < 3:
            self.caller.msg(
                f"Usage: {self.key} set <target> <field> <value>"
            )
            return
        token, field_name, raw_value = parts

        resolution = adapter.resolve_instance(self.caller, token)
        if not resolution.ok:
            self.caller.msg(resolution.error)
            return
        target = resolution.target

        # Unknown field: error naming the valid fields, no state change
        # (Requirement 3.7).
        fields = adapter.instance_fields()
        spec = fields.get(field_name)
        if spec is None:
            valid = ", ".join(sorted(fields)) or "(none)"
            self.record_outcome(UNKNOWN_FIELD, field=field_name,
                                valid=sorted(fields), plane="instance")
            self.caller.msg(
                f"Unknown field '{field_name}' — valid fields: {valid}."
            )
            return

        # Per-field permission escalation: checked after the verb-level
        # check (already passed in dispatch) and before bounds handling;
        # a FieldSpec tier at or below the verb tier adds no extra check
        # (Requirement 8.4). Insufficient tier rejects in full, naming
        # the required tier (Requirement 8.5).
        if _perm_rank(spec.perm) > _perm_rank(self._verb_perm("set")):
            if not self.caller.check_permstring(spec.perm):
                self.record_outcome(PERM_DENIED, required=spec.perm,
                                    scope="field", target=spec.name)
                self.caller.msg(
                    f"Permission denied. {spec.perm}+ required for "
                    f"field '{spec.name}'."
                )
                return

        # Kind coercion (3.8) and enum validation (3.9).
        requested, error = coerce_field_value(spec, raw_value)
        if error is not None:
            self.caller.msg(error)
            return

        # Bounds: static from the FieldSpec, dynamic computed from the
        # target's current state (3.4); out-of-bounds clamps to the
        # nearest bound (3.2, D2), in-bounds/unbounded applies unchanged
        # (3.3).
        applied, clamped, lo, hi = clamp_field_value(spec, target, requested)

        # Write through the entity's existing single-writer path; a
        # failed write errors with the pre-command state retained
        # (Requirements 3.5, 3.10).
        try:
            result = adapter.update(self.caller, target, spec.name, applied)
        except Exception as exc:  # noqa: BLE001 - relay write-path errors
            self.caller.msg(
                f"Write failed: {exc} — {self._describe_instance(target)} "
                "is unchanged."
            )
            return
        if isinstance(result, SetResult) and not result.ok:
            error = result.error or "write path failed"
            self.caller.msg(
                f"Write failed: {error} — {self._describe_instance(target)} "
                "is unchanged."
            )
            return

        identity = self._describe_instance(target)
        # Recorded on every successful set, clamped or not, so a test can
        # assert "this did not clamp" as a fact instead of as the absence of
        # the word "clamped" from a sentence. ``target`` is the same identity
        # string the message opens with, so a test can assert "the write
        # landed on the caller, not on Bob" without matching that prefix.
        self.record_outcome(FIELD_SET, field=spec.name, requested=requested,
                            applied=applied, clamped=clamped, lo=lo, hi=hi,
                            target=identity)
        clamp_note = ""
        if clamped:
            clamp_note = (
                f" (clamped to {fmt_bound(applied)}; bounds "
                f"{fmt_bound(lo)}–{fmt_bound(hi)})"
            )
        # Audit: requested and applied values recorded, distinguishable
        # on clamp (Requirements 9.1, 9.3).
        note = self._audit(
            "set",
            f"{adapter.entity_key} {identity} {spec.name}: "
            f"requested={requested!r} applied={applied!r}",
        )
        self.caller.msg(
            f"{identity}: {spec.name} set to {applied}{clamp_note}.{note}"
        )

    # --- destroy ------------------------------------------------------------ #

    def _do_destroy(self, rest: str):
        """``destroy <target>[, <target> ...]`` | ``destroy confirm`` |
        ``destroy cancel`` (Requirements 4.4, 4.5, 4.8)."""
        raw = (rest or "").strip()
        if not raw:
            self.caller.msg(
                f"Usage: {self.key} destroy <target>[, <target> ...] "
                f"| {self.key} destroy confirm | {self.key} destroy cancel"
            )
            return

        pending_key = (caller_key(self.caller), self.adapter.entity_key)
        word = raw.split(None, 1)[0].lower()
        if word == "confirm":
            self._destroy_confirm(pending_key)
            return
        if word == "cancel":
            self._destroy_cancel(pending_key)
            return

        # A new destroy always supersedes (cancels) any pending one.
        _PENDING_DESTROY.pop(pending_key, None)

        # Resolve every target up front; any failure deletes nothing.
        tokens = [t.strip() for t in raw.split(",") if t.strip()]
        targets = []
        for token in tokens:
            resolution = self.adapter.resolve_instance(self.caller, token)
            if not resolution.ok:
                self.caller.msg(
                    f"{resolution.error} Nothing was destroyed."
                )
                return
            targets.append(resolution.target)

        if len(targets) == 1:
            self._destroy_now(targets)
            return

        # Multi-target: show count + identities, delete nothing before
        # explicit confirmation (Requirement 4.5).
        _PENDING_DESTROY[pending_key] = tuple(targets)
        lines = [
            f"|yThis will destroy {len(targets)} "
            f"{self.adapter.entity_key} instances:|n"
        ]
        for target in targets:
            lines.append(f"  {self._describe_instance(target)}")
        lines.append(
            f"Run '{self.key} destroy confirm' to proceed or "
            f"'{self.key} destroy cancel' to abort."
        )
        self.caller.msg("\n".join(lines))

    def _destroy_confirm(self, pending_key):
        """Execute a pending multi-target destroy."""
        targets = _PENDING_DESTROY.pop(pending_key, None)
        if targets is None:
            self.caller.msg(
                "No destroy is pending confirmation — run "
                f"'{self.key} destroy <target>' first."
            )
            return
        self._destroy_now(list(targets))

    def _destroy_cancel(self, pending_key):
        """Decline a pending destroy: cancel, no state change (R4.5)."""
        if _PENDING_DESTROY.pop(pending_key, None) is None:
            self.caller.msg("No destroy is pending confirmation.")
            return
        self.caller.msg("Destroy cancelled — nothing was destroyed.")

    def _destroy_now(self, targets):
        """Delete *targets* through the adapter's existing deletion path.

        Deletion-path failure reports the error and makes no further
        state change (Requirement 4.8); already-completed deletions in a
        confirmed batch stay deleted and are audited.
        """
        adapter = self.adapter
        destroyed = []
        failure = None
        for target in targets:
            identity = self._describe_instance(target)
            try:
                result = adapter.delete(self.caller, target)
            except Exception as exc:  # noqa: BLE001 - relay deletion errors
                failure = f"Destroy failed for {identity}: {exc}"
                break
            if result is False or getattr(result, "ok", True) is False:
                error = getattr(result, "error", None) or "deletion path failed"
                failure = f"Destroy failed for {identity}: {error}"
                break
            destroyed.append(identity)

        note = ""
        if destroyed:
            # Exactly one audit entry per completed destroy invocation,
            # listing every destroyed identity (Requirement 9.1).
            note = self._audit(
                "destroy",
                f"{adapter.entity_key}: {', '.join(destroyed)}",
            )
        if failure is not None:
            lines = [failure]
            if destroyed:
                lines.append(
                    f"Destroyed before the failure: {', '.join(destroyed)}."
                )
            self.caller.msg("\n".join(lines) + note)
            return
        if len(destroyed) == 1:
            self.caller.msg(f"Destroyed {destroyed[0]}.{note}")
        else:
            self.caller.msg(
                f"Destroyed {len(destroyed)} {adapter.entity_key} "
                f"instances: {', '.join(destroyed)}.{note}"
            )

    # --- def set / def reset (task 1.15) ---------------------------------- #

    def _data_registry(self):
        """The live DataRegistry the def-write flow reloads (test hook).

        Matches how the existing admin reload path (``@reboot`` in
        ``commands/admin_commands.py``) reaches the live registry.
        """
        return get_system(self.caller, "registry")

    def _reload_lock(self):
        """The lock serializing overlay-write + reload sequences (R6.6).

        The real :data:`world.data_registry.OVERLAY_RELOAD_LOCK` (an
        RLock — ``reload_all`` re-enters it cleanly under our outer
        hold); a test hook so tests can substitute a spy.
        """
        return OVERLAY_RELOAD_LOCK

    @staticmethod
    def _definition_value(definition, name):
        """The current merged value of one definition field."""
        if definition is None:
            return None
        if isinstance(definition, dict):
            return definition.get(name)
        return getattr(definition, name, None)

    def _def_write_context(self):
        """Shared preconditions for ``def set``/``def reset``.

        Returns ``(domain, registry)`` or ``(None, None)`` after
        messaging the caller. Checked before anything is written so a
        missing domain/registry leaves the overlay untouched.
        """
        domain = self._def_domain()
        if not domain:
            self.caller.msg(
                f"{self.key} has no definition domain — "
                "the overlay was not modified."
            )
            return None, None
        registry = self._data_registry()
        if registry is None:
            self.caller.msg(
                "Data Registry unavailable — the overlay was not modified."
            )
            return None, None
        return domain, registry

    def _def_transaction(self, domain, registry):
        """The :class:`DefWriteTransaction` wired to this router's resolved
        collaborators — the shared resolve → write → reload → rollback
        control flow ``def set`` and ``def reset`` both drive."""
        return DefWriteTransaction(
            adapter=self.adapter,
            store=self._overlay_store(),
            registry=registry,
            lock=self._reload_lock(),
            domain=domain,
            definition_key=self._definition_key,
            definition_value=self._definition_value,
        )

    def _do_def_set(self, rest: str):
        """``def set <key> <field> <value>`` — overlay write + serialized
        validated reload (Requirements 5.2, 5.8, 6.3–6.8, 8.4, 9.2)."""
        adapter = self.adapter
        parts = (rest or "").split(None, 2)
        if len(parts) < 3:
            self.caller.msg(
                f"Usage: {self.key} def set <key> <field> <value>"
            )
            return
        token, field_name, raw_value = parts

        # Field must be in the adapter's definition Field_Spec schema —
        # else error naming the valid fields, overlay untouched (R5.8).
        fields = adapter.definition_fields()
        spec = fields.get(field_name)
        if spec is None:
            valid = ", ".join(sorted(fields)) or "(none)"
            self.record_outcome(UNKNOWN_FIELD, field=field_name,
                                valid=sorted(fields), plane="definition")
            self.caller.msg(
                f"Unknown definition field '{field_name}' — valid fields: "
                f"{valid}. The overlay was not modified."
            )
            return

        # Per-field perm escalation above the def-set tier, checked after
        # the verb-level check and before the write (R8.4, R8.5).
        if _perm_rank(spec.perm) > _perm_rank(self._verb_perm("def set")):
            if not self.caller.check_permstring(spec.perm):
                self.record_outcome(PERM_DENIED, required=spec.perm,
                                    scope="field", target=spec.name)
                self.caller.msg(
                    f"Permission denied. {spec.perm}+ required for "
                    f"field '{spec.name}'."
                )
                return

        # Kind coercion (reuses the instance-plane helper; R3.8/3.9
        # messaging). Deeper validation belongs to the merged reload.
        value, error = coerce_field_value(spec, raw_value)
        if error is not None:
            self.caller.msg(error)
            return

        domain, registry = self._def_write_context()
        if domain is None:
            return

        # The resolve → write → reload sequence runs inside the shared
        # transaction under the serialization lock (R6.6); the write is a
        # single-field overlay set, and only this field is reported.
        result = self._def_transaction(domain, registry).run(
            token,
            mutate=lambda store, dom, key: store.set(
                dom, key, spec.name, value
            ),
            snapshot_fields=lambda definition, key: [spec.name],
        )

        if result.status == NOT_FOUND:
            self.caller.msg(
                f"No definition found for '{token}' — "
                "the overlay was not modified."
            )
            return
        if result.status == OVERLAY_ERROR:
            # Overlay-write failure: no reload, overlay unchanged (R6.8).
            self.caller.msg(
                f"Override write failed: {result.store_error} "
                "No reload was triggered; the overlay is unchanged."
            )
            return

        def_key = result.def_key
        if result.status == OK:
            before = result.before.get(spec.name)
            after = result.after.get(spec.name)
            note = self._audit(
                "def set",
                f"{adapter.entity_key} def {def_key}.{spec.name}: "
                f"requested={value!r} applied={after!r} — "
                "reload applied",
            )
            self.caller.msg(
                f"{def_key}.{spec.name}: {before} → {after} "
                f"(override). Reloaded OK.{note}"
            )
            return

        # Reload failed (validation/parse/IO): live registry is unchanged;
        # the overlay was rolled back inside the transaction (R6.5).
        note = self._audit(
            "def set",
            f"{adapter.entity_key} def {def_key}.{spec.name}: "
            f"requested={value!r} — reload failed, overlay "
            "rolled back to pre-command snapshot",
        )
        error_text = "\n".join(result.errors) if result.errors \
            else "unknown error"
        self.caller.msg(
            f"Override rejected:\n{error_text}\n{result.rollback_note}{note}"
        )

    def _do_def_reset(self, rest: str):
        """``def reset <key> [field]`` — remove override(s) + serialized
        validated reload (Requirements 5.5, 5.9, 6.3–6.8, 9.2)."""
        adapter = self.adapter
        parts = (rest or "").split()
        if not parts or len(parts) > 2:
            self.caller.msg(f"Usage: {self.key} def reset <key> [field]")
            return
        token = parts[0]
        field_name = parts[1] if len(parts) > 1 else None

        domain, registry = self._def_write_context()
        if domain is None:
            return

        # Fields whose base values this reset restores (for the before→after
        # report): the named field, or every currently overridden field on a
        # whole-key reset (resolved against the def_key inside the txn).
        def _snapshot_fields(definition, def_key):
            if field_name is not None:
                return [field_name]
            return sorted(self._def_overrides(def_key))

        result = self._def_transaction(domain, registry).run(
            token,
            mutate=lambda store, dom, key: store.reset(dom, key, field_name),
            snapshot_fields=_snapshot_fields,
        )

        if result.status == NOT_FOUND:
            self.caller.msg(
                f"No definition found for '{token}' — "
                "the overlay was not modified."
            )
            return
        if result.status == OVERLAY_ERROR:
            # Covers no-existing-override (R5.9) and unparseable-file
            # rejection (R5.11): no reload, overlay untouched.
            self.caller.msg(
                f"{result.store_error} No reload was triggered; "
                "the overlay is unchanged."
            )
            return

        def_key = result.def_key
        target = f"{def_key}.{field_name}" if field_name else def_key
        report_fields = list(result.before)
        if result.status == OK:
            restored_lines = [
                f"  {name}: {result.before.get(name)} → "
                f"{result.after.get(name)} (base)"
                for name in report_fields
            ]
            note = self._audit(
                "def reset",
                f"{adapter.entity_key} def {target}: override removed "
                f"({', '.join(report_fields) or 'none'}) — "
                "reload applied",
            )
            body = "\n".join(restored_lines)
            self.caller.msg(
                f"Reset {target} — restored base values:\n"
                f"{body}\nReloaded OK.{note}"
            )
            return

        note = self._audit(
            "def reset",
            f"{adapter.entity_key} def {target}: reload failed, "
            "overlay rolled back to pre-command snapshot",
        )
        error_text = "\n".join(result.errors) if result.errors \
            else "unknown error"
        self.caller.msg(
            f"Reset rejected:\n{error_text}\n{result.rollback_note}{note}"
        )


class ValueFirstSetAliasMixin:
    """Reshape legacy VALUE-first ``set`` aliases into the canonical grammar.

    A handful of entities (``@player``, ``@stat``) carry legacy migration
    aliases whose argument order is VALUE-first — ``@player level <N>
    [player]``, ``@stat hp <N> [target]`` — while the unified canonical
    verb is TARGET-first: ``set <target> <field> <value>`` (Requirement
    11.5). Both routers previously reimplemented the same ``_dispatch_alias``
    override + ``_reshape_legacy_set_args`` pair, differing only in two
    knobs, so the reshape lives here once:

    - ``_LEGACY_SET_ALIASES`` — the value-first alias spellings this router
      reshapes (others fall straight through to the shared alias path).
    - ``_ALIAS_TARGET_NOUN`` — the trailing-arg noun shown in the usage hint
      (``"player"`` for @player, ``"target"`` for @stat).
    - The canonical FIELD an alias writes comes from the adapter's optional
      ``ALIAS_FIELDS`` map (``maxhp``→``hp_max``, ``xp``→``combat_xp`` for
      @stat); an alias absent from it — or an adapter without the map, like
      @player — writes the field of the same name as the alias.

    Mixed in BEFORE :class:`EntityAdminRouter` so its ``_dispatch_alias``
    wins, reshapes, then delegates to the shared alias path (deprecation
    note → canonical perm check → canonical handler), keeping state, perms,
    output, and audit identical to the canonical spelling (R11.1, R11.2).
    """

    #: Subclasses list the value-first alias spellings to reshape.
    _LEGACY_SET_ALIASES: tuple[str, ...] = ()
    #: The trailing-target noun for the usage hint ("player" / "target").
    _ALIAS_TARGET_NOUN: str = "target"

    def _dispatch_alias(self, alias: str, canonical: str, rest: str):
        """Reshape a value-first alias, then run the shared alias path.

        Non-value-first aliases (none, for the current routers) fall
        straight through unchanged. A value-first alias missing its value
        bails to a usage message with NO state change (``rest`` is
        ``None``); otherwise its ``<N> [target]`` args become the canonical
        ``<target|me> <field> <N>`` before the shared dispatch.
        """
        if alias in self._LEGACY_SET_ALIASES:
            rest = self._reshape_legacy_set_args(alias, rest)
            if rest is None:
                return
        super()._dispatch_alias(alias, canonical, rest)

    def _reshape_legacy_set_args(self, alias: str, rest: str) -> str | None:
        """``<N> [target]`` → ``<target|me> <field> <N>``, or ``None``
        (usage messaged) when the value is missing.

        The canonical field name is looked up from the adapter's optional
        ``ALIAS_FIELDS`` map (``@stat``'s ``maxhp``→``hp_max`` etc.);
        aliases absent from it — and adapters without it, like ``@player`` —
        use the alias spelling as the field. An omitted ``[target]``
        defaults to the caller (``me``), as the legacy forms did.
        """
        parts = (rest or "").split()
        if not parts:
            self.caller.msg(
                f"Usage: {self.key} {alias} <N> [{self._ALIAS_TARGET_NOUN}]"
            )
            return None
        value = parts[0]
        target_token = parts[1] if len(parts) > 1 else "me"
        alias_fields = getattr(self.adapter, "ALIAS_FIELDS", {}) or {}
        field = alias_fields.get(alias, alias)
        return f"{target_token} {field} {value}"
