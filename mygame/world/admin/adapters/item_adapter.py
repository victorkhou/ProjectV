"""
ItemAdapter — the `@item` EntityAdapter (unified-admin-crud Phase 1 pilot).

Generalizes what the existing ``@item`` router already does into the
adapter layer the shared :class:`~commands.command_router.EntityAdminRouter`
verb handlers drive:

- **Instance fields with dynamic bounds** (Requirements 3.1, 3.4): every
  stat any loaded ``ItemDef.roll_spec`` declares becomes a ``FieldSpec``
  whose ``dynamic_bounds`` callable computes ``(lo, hi)`` from the TARGET
  instance's own item def bands at clamp time — bounds vary per item, so
  they can never be static. A stat the target's def does not band resolves
  to unbounded ``(None, None)`` and the write is then rejected inside
  :meth:`update` (the def's bands are the only place a [min, max] comes
  from — mirroring the legacy ``@item set`` rejection).
- **IQS re-stamp** (Requirement 7.6): every roll-field write re-stamps the
  item's quality score through the existing single writer
  (``world.systems.loot_roller.recompute_iqs``) INSIDE :meth:`update`,
  before the success response.
- **Player-scoped instance resolution** (Requirement 2.4): instances come
  from a player's holdings (equipped gear + carried item objects); a
  trailing ``[player]`` token scopes the search and defaults to the
  caller. Resolution runs through the shared Resolution_Engine helpers
  (``resolve_instance_token`` / ``resolve_player_scope`` / List_Cache).
- **Definition plane** (Requirement 3.5): ``def_registry_dict`` serves the
  LIVE ``DataRegistry.items`` and ``def_resolve`` delegates to the
  existing ``resolve_item`` matcher. Registry access is lazy
  (``DataRegistry.get_instance()``) so constructing/registering the
  adapter never needs a running game.
- **Grammar contract**: full core-verb support (no opt-outs), the
  ``stats``→``show`` migration alias (installed when task 3.2 migrates
  the router), and the optional ``has_live_instances`` hook feeding the
  ``def show`` live-instances note.
"""

from __future__ import annotations

from typing import Any

from world.admin.adapters._support import live_registry
from world.admin.resolution import (
    LIST_CACHE,
    Resolution,
    resolve_instance_token,
    resolve_player_scope,
)
from world.admin.types import (
    CORE_VERBS,
    CreateResult,
    FieldSpec,
    InstanceRow,
    SetResult,
    ShowReport,
)
from world.constants import GEAR_CATEGORIES, WEAPON_TYPES
from world.systems.loot_roller import (
    DEFAULT_LOOT_ROLL_SKEW,
    RARITY_ORDER,
    recompute_iqs,
    roll_and_stamp,
    stats_at_quality,
    write_instance_field,
)

#: Typed damages an item definition may declare (``ItemDef.damage_type``).
_DAMAGE_TYPES = ("physical", "fire", "psychic", "blast")

#: Scalar attributes ``_apply_item_def`` stamps onto a GameItem at
#: creation. ``show`` compares each stamped value against the CURRENT
#: merged definition and appends a staleness note per differing attribute
#: (Requirement 10.3 / design Error Handling Scenario 5) — a later
#: ``def set`` never retro-updates live instances, so the drift must be
#: surfaced, not solved.
_STAMPED_SCALAR_ATTRS = (
    "slot",
    "category",
    "weapon_type",
    "ammo_type",
    "ammo_per_shot",
    "magazine_size",
    "max_stack",
    "weight",
    "classification",
    "required_rank",
)


class _GrantSummary:
    """Identity handle for a multi-item / supply grant (`name (key)`)."""

    def __init__(self, name: str, key: str) -> None:
        self.name = name
        self.key = key


def _num(value: Any) -> bool:
    """True for real numbers (bool excluded) — the band-value check."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _read_field(obj: Any, name: str) -> Any:
    """Best-effort per-instance field read (mirrors the loot roller's
    duck-typing): live ``GameItem`` via ``db``, attribute-bag stub via
    ``attributes``, dict-shaped test item by plain key. Never raises."""
    try:
        db = getattr(obj, "db", None)
        if db is not None:
            value = getattr(db, name, None)
            if value is not None:
                return value
        attrs = getattr(obj, "attributes", None)
        if attrs is not None and hasattr(attrs, "get"):
            value = attrs.get(name)
            if value is not None:
                return value
        if isinstance(obj, dict):
            return obj.get(name)
    except Exception:  # noqa: BLE001 - reads must never break a verb
        pass
    return None


class ItemAdapter:
    """EntityAdapter for items (the ``@item`` admin surface).

    Registry access is LAZY: nothing in construction or registration
    touches the live game, so ``register_all()`` (and the fast stubbed
    test suite) never needs a booted server. Tests may inject a
    registry double via ``registry``.
    """

    entity_key = "item"
    #: Overlay/definition domain (matches DataRegistry._REQUIRED_FILES).
    def_domain = "items"

    # --- grammar contract (design per-entity matrix row for @item) ---
    supported_verbs = frozenset(CORE_VERBS)
    opt_outs: dict[str, str] = {}
    extra_verbs: dict[str, str] = {}
    #: Migration alias (design D5): the legacy inspect spelling.
    aliases = {"stats": "show"}

    def __init__(self, registry: Any | None = None) -> None:
        self._registry = registry

    # ------------------------------------------------------------------ #
    #  Registry access (lazy — no live game required to construct)
    # ------------------------------------------------------------------ #

    def _live_registry(self) -> Any | None:
        """The injected registry double, else the live DataRegistry
        (services facade, then the process singleton)."""
        return live_registry(self._registry)

    def _item_def_for(self, instance: Any) -> Any | None:
        """The ItemDef governing a live *instance*, or ``None``.

        Looks the instance's stamped ``item_key`` (object key as
        fallback) up through the registry's resolver — the def is where
        the roll bands live (mirrors the legacy router's
        ``_resolve_instance_def``).
        """
        registry = self._live_registry()
        if registry is None:
            return None
        token = _read_field(instance, "item_key") or getattr(
            instance, "key", None
        )
        if not token:
            return None
        return self._resolve_def(registry, str(token))

    @staticmethod
    def _resolve_def(registry: Any, token: str) -> Any | None:
        """Resolve a def token via the registry's existing matchers."""
        resolver = getattr(registry, "resolve_item", None)
        item_def = resolver(token) if callable(resolver) else None
        if item_def is None and hasattr(registry, "get_item"):
            try:
                item_def = registry.get_item(token)
            except KeyError:
                item_def = None
        if item_def is None:
            items = getattr(registry, "items", None)
            if isinstance(items, dict):
                item_def = items.get(token)
        return item_def

    @staticmethod
    def _bands_of(item_def: Any) -> dict:
        """The def's ``roll_spec.stats`` band dict, or ``{}`` when fixed."""
        spec = getattr(item_def, "roll_spec", None) if item_def else None
        stats = spec.get("stats") if isinstance(spec, dict) else None
        return stats if isinstance(stats, dict) else {}

    def _instance_bands(self, instance: Any) -> dict:
        """The roll bands governing a live *instance* (via its def)."""
        return self._bands_of(self._item_def_for(instance))

    # ------------------------------------------------------------------ #
    #  Field schemas
    # ------------------------------------------------------------------ #

    def _bounds_fn(self, stat: str):
        """A ``dynamic_bounds`` callable: ``(lo, hi)`` from the TARGET
        instance's current item def roll band for *stat* (Requirement
        3.4). A stat the target's def does not band is unbounded
        ``(None, None)`` — :meth:`update` then rejects the write."""

        def bounds(entity: Any) -> tuple[float | None, float | None]:
            band = self._instance_bands(entity).get(stat)
            if isinstance(band, dict):
                lo, hi = band.get("min"), band.get("max")
                if _num(lo) and _num(hi) and lo <= hi:
                    return float(lo), float(hi)
            return (None, None)

        return bounds

    def instance_fields(self) -> dict[str, FieldSpec]:
        """Modifiable instance fields: every stat any loaded def's
        ``roll_spec`` bands (dynamic bounds per instance) plus ``rarity``
        (enum). Computed from the live registry per call so a hot-reload
        is picked up immediately; with no registry (unbooted test
        process) only ``rarity`` is offered."""
        fields: dict[str, FieldSpec] = {
            "rarity": FieldSpec(
                name="rarity",
                kind="enum",
                perm="Builder",
                enum_values=tuple(RARITY_ORDER),
            ),
        }
        registry = self._live_registry()
        items = getattr(registry, "items", None) if registry else None
        stat_names: set[str] = set()
        for item_def in (items or {}).values():
            stat_names.update(str(s) for s in self._bands_of(item_def))
        for stat in sorted(stat_names):
            fields[stat] = FieldSpec(
                name=stat,
                kind="float",
                perm="Builder",
                dynamic_bounds=self._bounds_fn(stat),
            )
        return fields

    def definition_fields(self) -> dict[str, FieldSpec]:
        """Overridable ``def set`` fields, against real ``ItemDef``
        fields (kinds/bounds aligned with the SchemaValidator's item
        rules — merged data still runs the full validator, so anything
        subtler than these checks fails the reload, not the game)."""
        specs = (
            FieldSpec(name="name", kind="str", perm="Admin"),
            FieldSpec(name="weight", kind="float", min_value=0.0,
                      perm="Admin"),
            FieldSpec(name="max_stack", kind="int", min_value=1,
                      perm="Admin"),
            FieldSpec(name="ammo_per_shot", kind="int", min_value=1,
                      perm="Admin"),
            FieldSpec(name="magazine_size", kind="int", min_value=1,
                      perm="Admin"),
            FieldSpec(name="damage_type", kind="enum", perm="Admin",
                      enum_values=_DAMAGE_TYPES),
            FieldSpec(name="weapon_type", kind="enum", perm="Admin",
                      enum_values=tuple(WEAPON_TYPES)),
        )
        return {spec.name: spec for spec in specs}

    # ------------------------------------------------------------------ #
    #  Player-scoped holdings + list/resolve (instance plane)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _holdings(player: Any) -> list:
        """The player's item holdings: equipped gear first, then carried
        item objects (anything stamping an ``item_key``). Deduplicated —
        equipped items may also appear in ``contents``."""
        seen: set[int] = set()
        held: list = []

        def _add(obj: Any) -> None:
            if obj is None or id(obj) in seen:
                return
            seen.add(id(obj))
            held.append(obj)

        handler = getattr(player, "equipment", None)
        if handler is not None and hasattr(handler, "get_all_equipped"):
            try:
                for obj in handler.get_all_equipped().values():
                    _add(obj)
            except Exception:  # noqa: BLE001 - handler stub w/o the view
                pass
        try:
            contents = getattr(player, "contents", None) or []
        except Exception:  # noqa: BLE001 - exotic contents proxy
            contents = []
        for obj in contents:
            if _read_field(obj, "item_key") is not None:
                _add(obj)
        return held

    @staticmethod
    def _instance_name(instance: Any) -> str:
        """Display name of a live instance (object key, else item_key)."""
        name = getattr(instance, "key", None)
        if name:
            return str(name)
        item_key = _read_field(instance, "item_key")
        return str(item_key) if item_key else str(instance)

    def _row(self, index: int, instance: Any) -> InstanceRow:
        """One holdings entry as an InstanceRow (list output + cache)."""
        item_key = _read_field(instance, "item_key")
        key = str(item_key) if item_key else self._instance_name(instance)
        name = self._instance_name(instance)
        iqs = _read_field(instance, "iqs")
        rarity = _read_field(instance, "rarity")
        category = _read_field(instance, "category") or ""
        bits = [name]
        if key != name:
            bits.append(f"({key})")
        if category:
            bits.append(str(category))
        bits.append(f"IQS {iqs if iqs is not None else '—'}")
        if rarity:
            bits.append(str(rarity))
        return InstanceRow(index=index, key=key, name=name,
                           summary=" ".join(bits), ref=instance)

    def _candidate_rows(self, player: Any) -> list[InstanceRow]:
        """The player's holdings as resolution candidates."""
        return [
            self._row(i, obj)
            for i, obj in enumerate(self._holdings(player), start=1)
        ]

    @staticmethod
    def _matches_filter(instance: Any, filt: str) -> bool:
        """Lenient list filter: category, slot, or name/key substring."""
        category = str(_read_field(instance, "category") or "").lower()
        slot = str(_read_field(instance, "slot") or "").lower()
        if filt in (category, slot):
            return True
        name = str(getattr(instance, "key", "") or "").lower()
        item_key = str(_read_field(instance, "item_key") or "").lower()
        return filt in name or filt in item_key

    def _parse_list_scope(self, caller: Any, filter_str: str
                          ) -> tuple[Any, str]:
        """Split ``list``'s args into (scope player, filter).

        A trailing token that resolves to exactly one player scopes the
        listing to that player's holdings (Requirement 2.4); otherwise
        the whole string is the filter and the scope defaults to the
        caller."""
        tokens = (filter_str or "").split()
        if tokens:
            scope = resolve_player_scope(caller, tokens[-1])
            if scope.ok and scope.target is not None:
                return scope.target, " ".join(tokens[:-1]).strip().lower()
        return caller, (filter_str or "").strip().lower()

    def list_instances(self, caller: Any, filter_str: str
                       ) -> list[InstanceRow]:
        """Item instances in the scoped player's holdings, indexed rows."""
        scope, filt = self._parse_list_scope(caller, filter_str)
        rows: list[InstanceRow] = []
        for obj in self._holdings(scope):
            if filt and not self._matches_filter(obj, filt):
                continue
            rows.append(self._row(len(rows) + 1, obj))
        return rows

    def resolve_instance(self, caller: Any, token: str) -> Resolution:
        """Resolve *token* per the uniform grammar over player holdings.

        ``#N`` indexes the caller's item List_Cache; key/name/prefix
        tiers run over the caller's own holdings first. When that fails
        and the token's LAST word resolves to a player, the remainder is
        re-resolved against that player's holdings (trailing ``[player]``
        scoping, Requirement 2.4)."""
        token = (token or "").strip()
        rows = LIST_CACHE.get(caller, self.entity_key)
        primary = resolve_instance_token(
            token, rows=rows, candidates=self._candidate_rows(caller)
        )
        if primary.ok:
            return primary
        parts = token.rsplit(None, 1)
        if len(parts) == 2:
            item_token, player_token = parts
            scope = resolve_player_scope(caller, player_token)
            if scope.ok and scope.target is not None:
                return resolve_instance_token(
                    item_token,
                    rows=rows,
                    candidates=self._candidate_rows(scope.target),
                )
        return primary

    # ------------------------------------------------------------------ #
    #  Instance CRUD hooks (delegating to the REAL system paths)
    # ------------------------------------------------------------------ #

    def create(self, caller: Any, def_token: str, kwargs: dict
               ) -> CreateResult:
        """``spawn``: create through the existing item creation paths.

        Gear (armor/weapon/accessory) is created as equippable
        ``GameItem`` object(s) via ``create_game_item`` and — for
        rollable defs — rolled exactly like a loot drop through
        ``roll_and_stamp`` (``iqs=<0-100>`` pins a deterministic quality
        stamp; ``rarity=<tier>`` forces the tier). Supplies are added to
        the target's Supply_Bag through the equipment handler. The
        ``player`` kwarg (resolved by the router) targets the grant;
        default the caller. ``count=N`` grants several.
        """
        registry = self._live_registry()
        item_def = (self._resolve_def(registry, str(def_token).strip())
                    if registry else None)
        if item_def is None:
            return CreateResult(
                ok=False, error=f"no item definition matches '{def_token}'"
            )
        target = kwargs.get("player") or caller

        try:
            count = max(1, int(kwargs.get("count", 1)))
        except (TypeError, ValueError):
            return CreateResult(ok=False, error="count must be a number")

        rarity = kwargs.get("rarity")
        if rarity is not None:
            rarity = str(rarity).strip().lower()
            if rarity not in RARITY_ORDER:
                return CreateResult(
                    ok=False,
                    error=(f"unknown rarity '{kwargs['rarity']}' — valid: "
                           f"{', '.join(RARITY_ORDER)}"),
                )
        iqs = kwargs.get("iqs")
        if iqs is not None:
            try:
                iqs = min(max(float(iqs), 0.0), 100.0)
            except (TypeError, ValueError):
                return CreateResult(ok=False, error="iqs must be a number")

        if getattr(item_def, "category", None) in GEAR_CATEGORIES:
            return self._create_gear(
                target, item_def, count, iqs=iqs, rarity=rarity,
                registry=registry,
            )
        return self._create_supply(target, item_def, count)

    def _create_gear(self, target: Any, item_def: Any, count: int, *,
                     iqs: float | None, rarity: str | None,
                     registry: Any) -> CreateResult:
        """Gear creation: ``create_game_item`` + the loot-drop roll path
        (mirrors the legacy ``@item spawn`` gear treatment)."""
        import random as _rng

        from typeclasses.objects import create_game_item

        spec = getattr(item_def, "roll_spec", None)
        rollable = (isinstance(spec, dict)
                    and isinstance(spec.get("stats"), dict)
                    and bool(spec.get("stats")))
        balance = getattr(registry, "balance", None)

        created: list = []
        for _ in range(count):
            try:
                item = create_game_item(target, item_def)
            except Exception as exc:  # noqa: BLE001 - relay path failures
                if created:
                    break  # partial grant: report what was made
                return CreateResult(ok=False, error=str(exc))
            if item is None:
                if created:
                    break
                return CreateResult(ok=False, error="creation path failed")
            created.append(item)
            if not rollable:
                continue  # fixed def stays fixed, exactly as always
            try:
                if iqs is not None:
                    rolled = stats_at_quality(spec, iqs / 100.0)
                    if rolled:
                        write_instance_field(item, "rolled_stats", rolled)
                        if rarity:
                            write_instance_field(item, "rarity", rarity)
                        recompute_iqs(item, spec)
                else:
                    if rarity:
                        table = {"admin": {"min_weight": 0.0,
                                           "weights": {rarity: 1}}}
                    else:
                        table = getattr(balance, "rarity_table", None)
                    roll_and_stamp(
                        item, item_def,
                        source_rarity_weight=0.0,
                        crafted=False,
                        rng=_rng,
                        default_skew=getattr(balance, "loot_roll_skew",
                                             DEFAULT_LOOT_ROLL_SKEW),
                        rarity_table=table,
                        affix_pools=getattr(registry, "affixes", None),
                    )
            except Exception:  # noqa: BLE001 - a failed roll = fixed item
                pass

        if not created:
            return CreateResult(ok=False, error="creation path failed")
        if len(created) == 1:
            return CreateResult(ok=True, instance=created[0])
        return CreateResult(
            ok=True,
            instance=_GrantSummary(
                name=f"{len(created)}x {getattr(item_def, 'name', '?')}",
                key=getattr(item_def, "key", "?"),
            ),
        )

    @staticmethod
    def _create_supply(target: Any, item_def: Any, count: int
                       ) -> CreateResult:
        """Supply creation: units into the target's Supply_Bag through
        the equipment handler (the existing single path)."""
        equipment = getattr(target, "equipment", None)
        if equipment is None or not hasattr(equipment, "add_supply"):
            return CreateResult(
                ok=False,
                error=(f"{getattr(target, 'key', 'target')} has no "
                       "equipment handler to receive supplies"),
            )
        added = int(equipment.add_supply(
            item_def.key, count, max_stack=item_def.max_stack
        ) or 0)
        if added <= 0:
            return CreateResult(ok=False, error="supply grant added nothing")
        return CreateResult(
            ok=True,
            instance=_GrantSummary(
                name=f"{added}x {getattr(item_def, 'name', item_def.key)}",
                key=str(item_def.key),
            ),
        )

    def read(self, caller: Any, instance: Any) -> ShowReport:
        """``show``: identity header, live state, modifiable fields."""
        name = self._instance_name(instance)
        item_key = _read_field(instance, "item_key") or name
        item_def = self._item_def_for(instance)
        bands = self._bands_of(item_def)

        iqs = _read_field(instance, "iqs")
        rarity = _read_field(instance, "rarity")
        category = _read_field(instance, "category") or getattr(
            item_def, "category", "") or ""
        slot = _read_field(instance, "slot") or getattr(
            item_def, "slot", "") or ""
        affixes = _read_field(instance, "affixes") or []

        state_lines = [
            f"IQS: {iqs if iqs is not None else '—'}    "
            f"Rarity: {rarity or '—'}",
        ]
        detail = f"Category: {category or '—'}"
        if slot:
            detail += f"    Slot: {slot}"
        state_lines.append(detail)
        if affixes:
            state_lines.append(f"Affixes: {len(affixes)}")

        rolled = _read_field(instance, "rolled_stats") or {}
        base = dict(getattr(item_def, "stat_modifiers", None) or {})

        fields: list[tuple[FieldSpec, Any, bool]] = []
        for stat in sorted(bands):
            spec = FieldSpec(name=stat, kind="float", perm="Builder",
                             dynamic_bounds=self._bounds_fn(stat))
            value = rolled.get(stat, base.get(stat))
            fields.append((spec, value, False))
        fields.append((
            FieldSpec(name="rarity", kind="enum", perm="Builder",
                      enum_values=tuple(RARITY_ORDER)),
            rarity or "—",
            False,
        ))

        return ShowReport(
            header=f"{name} ({item_key}) — item instance",
            state_lines=state_lines,
            fields=fields,
            staleness_note=self._staleness_note(instance, item_def),
        )

    @staticmethod
    def _staleness_note(instance: Any, item_def: Any) -> str | None:
        """Staleness notes for stamped attributes that drifted from the
        CURRENT merged definition (Requirement 10.3).

        ``_apply_item_def`` stamps the def's metadata onto the instance
        at creation; a later ``def set`` + reload changes the merged def
        but never retro-updates live instances (design Error Handling
        Scenario 5). One note line per differing attribute, stating the
        attribute name, the stamped value, and the current merged def
        value. Attributes the instance does not carry (unset / ``None``
        — e.g. dict-shaped test items or partial stubs) are skipped so
        the note never guesses. Returns ``None`` when nothing drifted
        (or without a resolvable def)."""
        if item_def is None:
            return None
        notes: list[str] = []
        for attr in _STAMPED_SCALAR_ATTRS:
            stamped = _read_field(instance, attr)
            if stamped is None:
                continue
            current = getattr(item_def, attr, None)
            if current is None or stamped == current:
                continue
            notes.append(
                f"note: {attr} stamped {stamped!r}, current def says "
                f"{current!r} (def changed after spawn)"
            )
        # The stamped BASE stat dict (not the per-instance rolls) drifts
        # the same way when a def's stat_modifiers are overridden.
        stamped_stats = _read_field(instance, "stat_modifiers")
        def_stats = getattr(item_def, "stat_modifiers", None) or {}
        if isinstance(stamped_stats, dict):
            for stat in sorted(stamped_stats):
                current = def_stats.get(stat)
                if current is None or stamped_stats[stat] == current:
                    continue
                notes.append(
                    f"note: {stat} (base) stamped {stamped_stats[stat]!r}, "
                    f"current def says {current!r} (def changed after spawn)"
                )
        return "\n".join(notes) if notes else None

    def update(self, caller: Any, instance: Any, field: str, value: Any
               ) -> SetResult:
        """``set``: bounded write through the item's single-writer path.

        Roll fields write into ``rolled_stats`` (the per-instance
        override ``get_stat`` prefers) clamped into the def's band, then
        RE-STAMP the quality score through ``recompute_iqs`` — the
        single writer — BEFORE returning the success response
        (Requirement 7.6). ``rarity`` writes the named tier. A stat the
        target's def does not band is rejected with the settable list;
        no state changes on any failure."""
        name = self._instance_name(instance)

        if field == "rarity":
            tier = str(value).strip().lower()
            if tier not in RARITY_ORDER:
                return SetResult.fail(
                    field, value,
                    f"unknown rarity '{value}' — valid: "
                    f"{', '.join(RARITY_ORDER)}",
                )
            if not write_instance_field(instance, "rarity", tier):
                return SetResult.fail(
                    field, value, f"could not write rarity onto {name}"
                )
            return SetResult(ok=True, field=field, requested=value,
                             applied=tier, clamped=False)

        item_def = self._item_def_for(instance)
        bands = self._bands_of(item_def)
        band = bands.get(field)
        lo = band.get("min") if isinstance(band, dict) else None
        hi = band.get("max") if isinstance(band, dict) else None
        if not _num(lo) or not _num(hi) or lo > hi:
            settable = ", ".join(sorted(bands)) or "none — fixed item"
            return SetResult.fail(
                field, value,
                f"'{field}' is not a modifiable stat on {name}; "
                f"settable: {settable}",
            )

        try:
            requested = float(value)
        except (TypeError, ValueError):
            return SetResult.fail(
                field, value, f"value must be a number (got '{value}')"
            )

        # Defensive re-clamp into the band: the router already clamped
        # via dynamic_bounds, but the SetResult contract (applied always
        # in-band) must hold whoever calls update.
        applied = min(max(requested, float(lo)), float(hi))

        rolled = dict(_read_field(instance, "rolled_stats") or {})
        rolled[field] = applied
        if not write_instance_field(instance, "rolled_stats", rolled):
            return SetResult.fail(
                field, requested,
                f"could not write {field} onto {name} — unchanged",
            )
        # Re-stamp IQS through the existing single writer BEFORE the
        # success response (Requirement 7.6).
        recompute_iqs(instance, getattr(item_def, "roll_spec", None))

        return SetResult(ok=True, field=field, requested=requested,
                         applied=applied, clamped=(applied != requested))

    def delete(self, caller: Any, instance: Any) -> Any:
        """``destroy``: delete through the existing object deletion path
        (``GameItem.delete()``); a falsy result reports failure."""
        deleter = getattr(instance, "delete", None)
        if not callable(deleter):
            return CreateResult(
                ok=False,
                error=(f"{self._instance_name(instance)} has no deletion "
                       "path"),
            )
        return deleter()

    # ------------------------------------------------------------------ #
    #  Definition scope
    # ------------------------------------------------------------------ #

    def def_registry_dict(self) -> dict | None:
        """The live ``DataRegistry.items`` dict (merged registry)."""
        registry = self._live_registry()
        items = getattr(registry, "items", None) if registry else None
        return items if isinstance(items, dict) else None

    def def_resolve(self, token: str) -> Any | None:
        """Resolve a definition token via the existing ``resolve_item``
        key/name/prefix matcher (Requirement 2.6)."""
        registry = self._live_registry()
        if registry is None:
            return None
        token = str(token or "").strip()
        if not token:
            return None
        return self._resolve_def(registry, token)

    def has_live_instances(self, def_key: str) -> bool:
        """Optional hook feeding the ``def show`` live-instances note:
        True when at least one live object stamps ``item_key ==
        def_key``. Degrades to False without a live game/DB."""
        try:
            from evennia.utils import search

            matches = search.search_object_attribute(
                key="item_key", value=def_key
            )
            return bool(matches)
        except Exception:  # noqa: BLE001 - no DB in the stubbed suite
            return False
