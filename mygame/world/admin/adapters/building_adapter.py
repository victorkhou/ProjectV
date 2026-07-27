"""
BuildingAdapter — the `@building` EntityAdapter (unified-admin-crud Phase 2).

Closes the coverage gap Requirement 7.2 names: buildings gain NEW ``show``
and ``set`` verbs under the unified grammar, while ``spawn``/``destroy``
keep delegating to the same real paths the legacy router used:

- **Instance fields** (Requirements 3.1, 7.2): an integer ``level``
  FieldSpec with STATIC bounds 1–``MAX_BUILDING_LEVEL`` (1–5), ``hp`` with
  dynamic bounds computed from the TARGET building's own ``hp_max`` at
  clamp time, and ``hp_max`` (>= 1). Every write goes through the shared
  building-attribute writer (``world.utils.set_building_attr``) — the same
  safe single accessor ``BuildingSystem``/``AgentSystem``/``ResourceSystem``
  use for all building state.
- **Instance resolution**: buildings on the caller's current planet room
  (``PlanetRoom.get_all_buildings``), addressed per the uniform grammar —
  ``#N`` from the caller's building List_Cache, then key/name/prefix over
  the room's buildings via the shared Resolution_Engine.
- **spawn**: creates a ``typeclasses.objects.Building`` at the caller's
  current tile exactly like the legacy ``@building spawn`` — same attribute
  stamps, same ``place_on_tile`` placement, same best-effort
  ``BUILDING_CONSTRUCTED`` publish. ``owner=<name>`` (or a trailing
  ``[player]`` token) and ``level=<N>`` kwargs are honored.
- **destroy**: deletes through the object deletion path; deliberately does
  NOT publish ``BUILDING_DESTROYED`` (that event triggers base-elimination
  — a full NPC-base wipe on a Sentinel HQ — which a surgical admin delete
  never intends; legacy behavior preserved).
- **Definition plane** (Requirements 11.4, 11.6): ``def_registry_dict``
  serves the live ``DataRegistry.buildings`` and ``def_resolve`` delegates
  to the existing ``resolve_building`` matcher — the old def-meaning of
  ``list`` lives on as ``def list`` (the router appends the moved-to
  pointer).
- **Registry access is lazy**: injected double first (tests), then the
  services facade (``registry`` — how the legacy router reached it via
  ``_get_system``), then the ``DataRegistry`` process singleton. Nothing in
  construction/registration needs a running game.
"""

from __future__ import annotations

from typing import Any

from world.admin.adapters._support import live_registry
from world.admin.resolution import (
    LIST_CACHE,
    Resolution,
    resolve_instance_token,
)
from world.admin.types import (
    CORE_VERBS,
    CreateResult,
    FieldSpec,
    InstanceRow,
    SetResult,
    ShowReport,
    resolve_bounds,
)
from world.constants import MAX_BUILDING_LEVEL
from world.utils import (
    coords_of,
    get_building_attr,
    set_building_attr,
)


#: Owner tokens the legacy ``owner=`` kwarg treats as "no owner".
_NO_OWNER_TOKENS = ("none", "nobody", "null", "")


def _num(value: Any) -> bool:
    """True for real numbers (bool excluded)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class BuildingAdapter:
    """EntityAdapter for buildings (the ``@building`` admin surface).

    Tests may inject a registry double via ``registry``; otherwise the
    live registry is resolved lazily per call (services facade, then the
    DataRegistry singleton), so a hot-reload is picked up immediately and
    construction never needs a booted server.
    """

    entity_key = "building"
    #: Overlay/definition domain (matches DataRegistry._REQUIRED_FILES).
    def_domain = "buildings"

    # --- grammar contract (design per-entity matrix row for @building) ---
    supported_verbs = frozenset(CORE_VERBS)
    opt_outs: dict[str, str] = {}
    #: The legacy tile toggle survives as an extra verb; its handler stays
    #: on the router subclass as ``sub_open`` (Requirement 1.6).
    extra_verbs = {
        "open": "Open/close the building at your tile to ranged fire",
    }
    #: No renamed spellings: the one migration change is `list`'s meaning
    #: (defs -> instances), surfaced by the router's moved-to pointer
    #: (Requirement 11.4), not by an alias name.
    aliases: dict[str, str] = {}

    def __init__(self, registry: Any | None = None) -> None:
        self._registry = registry

    # ------------------------------------------------------------------ #
    #  Registry access (lazy — no live game required to construct)
    # ------------------------------------------------------------------ #

    def _live_registry(self) -> Any | None:
        """Injected double, else services facade, else the singleton.

        The services facade is checked before the DataRegistry singleton
        because that is how the legacy router reached the registry
        (``_get_system(caller, "registry")``) — existing tests and wiring
        that install the registry there keep working unchanged.
        """
        return live_registry(self._registry)

    @staticmethod
    def _resolve_def(registry: Any, token: str) -> Any | None:
        """Resolve a def token via the registry's existing matchers.

        ``resolve_building`` (abbr/name/prefix — Requirement 2.6) first,
        then an exact ``get_building`` / dict lookup on the upper-cased
        abbreviation, mirroring the legacy spawn resolution order.
        """
        resolver = getattr(registry, "resolve_building", None)
        bdef = resolver(token) if callable(resolver) else None
        if bdef is None and hasattr(registry, "get_building"):
            try:
                bdef = registry.get_building(token.upper())
            except KeyError:
                bdef = None
        if bdef is None:
            buildings = getattr(registry, "buildings", None)
            if isinstance(buildings, dict):
                bdef = buildings.get(token.upper())
        return bdef

    # ------------------------------------------------------------------ #
    #  Field schemas
    # ------------------------------------------------------------------ #

    @staticmethod
    def _hp_bounds(entity: Any) -> tuple[float | None, float | None]:
        """Dynamic ``hp`` bounds: 0 up to the TARGET's current ``hp_max``
        (Requirement 3.4). Without a numeric hp_max the top is unbounded."""
        hp_max = get_building_attr(entity, "hp_max")
        if _num(hp_max):
            return (0.0, float(hp_max))
        return (0.0, None)

    def instance_fields(self) -> dict[str, FieldSpec]:
        """Modifiable live-building fields (Requirement 7.2).

        ``level`` carries the STATIC 1–MAX_BUILDING_LEVEL bounds the
        requirement names; ``hp`` clamps into the target's own hp_max;
        ``hp_max`` only needs a floor of 1.
        """
        specs = (
            FieldSpec(name="level", kind="int", min_value=1,
                      max_value=float(MAX_BUILDING_LEVEL), perm="Builder"),
            FieldSpec(name="hp", kind="int", perm="Builder",
                      dynamic_bounds=self._hp_bounds),
            FieldSpec(name="hp_max", kind="int", min_value=1,
                      perm="Builder"),
        )
        return {spec.name: spec for spec in specs}

    def definition_fields(self) -> dict[str, FieldSpec]:
        """Overridable ``def set`` fields, against real ``BuildingDef``
        fields. Merged data still runs the full SchemaValidator +
        cross_validate on reload, so anything subtler than these checks
        fails the reload, not the game."""
        specs = (
            FieldSpec(name="name", kind="str", perm="Admin"),
            FieldSpec(name="max_health", kind="int", min_value=1,
                      perm="Admin"),
            FieldSpec(name="build_time_seconds", kind="int", min_value=1,
                      perm="Admin"),
            FieldSpec(name="max_level", kind="int", min_value=1,
                      max_value=float(MAX_BUILDING_LEVEL), perm="Admin"),
            FieldSpec(name="rank_requirement", kind="int", min_value=1,
                      perm="Admin"),
            FieldSpec(name="storage_capacity", kind="int", min_value=0,
                      perm="Admin"),
        )
        return {spec.name: spec for spec in specs}

    # ------------------------------------------------------------------ #
    #  Listing + resolution (instance plane)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _location_buildings(caller: Any) -> list:
        """Live buildings on the caller's current planet room."""
        room = getattr(caller, "location", None)
        if room is None:
            return []
        getter = getattr(room, "get_all_buildings", None)
        if callable(getter):
            try:
                return list(getter() or [])
            except Exception:  # noqa: BLE001 - room double without the view
                pass
        try:
            contents = getattr(room, "contents", None) or []
        except Exception:  # noqa: BLE001 - exotic contents proxy
            contents = []
        return [
            obj for obj in contents
            if get_building_attr(obj, "building_type") is not None
        ]

    @staticmethod
    def _owner_name(instance: Any) -> str:
        owner = get_building_attr(instance, "owner")
        if owner is None:
            return "nobody"
        return str(getattr(owner, "key", owner))

    def _row(self, index: int, instance: Any) -> InstanceRow:
        """One building as an InstanceRow (list output + #N cache)."""
        name = str(getattr(instance, "key", None) or "?")
        btype = str(get_building_attr(instance, "building_type") or "??")
        level = get_building_attr(instance, "building_level", 1) or 1
        hp = get_building_attr(instance, "hp")
        hp_max = get_building_attr(instance, "hp_max")
        bits = [name, f"({btype})", f"lvl {level}"]
        if hp is not None or hp_max is not None:
            bits.append(f"HP {hp if hp is not None else '—'}/"
                        f"{hp_max if hp_max is not None else '—'}")
        coords = coords_of(instance)
        if coords is not None:
            bits.append(f"at ({coords[0]}, {coords[1]})")
        bits.append(f"owner {self._owner_name(instance)}")
        return InstanceRow(index=index, key=name, name=name,
                           summary=" ".join(bits), ref=instance)

    def _candidate_rows(self, caller: Any) -> list[InstanceRow]:
        return [
            self._row(i, obj)
            for i, obj in enumerate(self._location_buildings(caller),
                                    start=1)
        ]

    def _matches_filter(self, instance: Any, filt: str) -> bool:
        """Lenient list filter: type, name, or owner substring."""
        btype = str(get_building_attr(instance, "building_type") or "").lower()
        name = str(getattr(instance, "key", "") or "").lower()
        owner = self._owner_name(instance).lower()
        return filt in btype or filt in name or filt in owner

    def list_instances(self, caller: Any, filter_str: str
                       ) -> list[InstanceRow]:
        """Building instances on the caller's planet room, indexed rows."""
        filt = (filter_str or "").strip().lower()
        rows: list[InstanceRow] = []
        for obj in self._location_buildings(caller):
            if filt and not self._matches_filter(obj, filt):
                continue
            rows.append(self._row(len(rows) + 1, obj))
        return rows

    def resolve_instance(self, caller: Any, token: str) -> Resolution:
        """Resolve *token* per the uniform grammar: ``#N`` indexes the
        caller's building List_Cache; key/name/prefix tiers run over the
        buildings on the caller's current planet room."""
        rows = LIST_CACHE.get(caller, self.entity_key)
        return resolve_instance_token(
            (token or "").strip(), rows=rows,
            candidates=self._candidate_rows(caller),
        )

    # ------------------------------------------------------------------ #
    #  Instance CRUD hooks (delegating to the REAL system paths)
    # ------------------------------------------------------------------ #

    def create(self, caller: Any, def_token: str, kwargs: dict
               ) -> CreateResult:
        """``spawn``: create at the caller's tile, legacy path preserved.

        Same attribute stamps, ``place_on_tile`` placement, and
        best-effort ``BUILDING_CONSTRUCTED`` publish as the legacy
        ``@building spawn``. Kwargs: ``owner=<name|none>`` (legacy
        spelling) or a router-resolved ``player`` (trailing token) set the
        owner (default the caller); ``level=<N>`` clamps into
        1–MAX_BUILDING_LEVEL.
        """
        registry = self._live_registry()
        bdef = (self._resolve_def(registry, str(def_token).strip())
                if registry else None)
        if bdef is None:
            return CreateResult(
                ok=False,
                error=f"no building definition matches '{def_token}'",
            )

        try:
            level = int(kwargs.get("level", 1))
        except (TypeError, ValueError):
            return CreateResult(
                ok=False,
                error=f"level must be a number 1-{MAX_BUILDING_LEVEL}",
            )
        level = max(1, min(level, MAX_BUILDING_LEVEL))

        owner, error = self._resolve_owner(caller, kwargs)
        if error is not None:
            return CreateResult(ok=False, error=error)

        planet_room = getattr(caller, "location", None)
        if planet_room is None:
            return CreateResult(ok=False, error="you have no location")
        coords = coords_of(caller)
        if coords is None:
            return CreateResult(ok=False, error="you have no coordinates set")
        cx, cy, _planet = coords

        try:
            from evennia.utils.create import create_object

            from world.utils import place_on_tile

            hp = getattr(bdef, "max_health", 500)
            building = create_object(
                typeclass="typeclasses.objects.Building",
                key=getattr(bdef, "name", str(def_token)),
                location=planet_room,
            )
            set_building_attr(building, "building_type", bdef.abbreviation)
            set_building_attr(building, "owner", owner)
            set_building_attr(building, "building_level", level)
            set_building_attr(building, "hp", hp)
            set_building_attr(building, "hp_max", hp)
            set_building_attr(building, "offline", False)
            # at_object_receive ran during create_object before the coords
            # were set — stamp + register them now (legacy behavior).
            place_on_tile(building, planet_room, cx, cy)
        except Exception as exc:  # noqa: BLE001 - relay path failures
            return CreateResult(ok=False, error=str(exc))

        # Announce on the event bus exactly as the player build path does
        # so subscribers (e.g. ShieldSystem) react immediately.
        # Best-effort — a missing bus or subscriber error must never fail
        # the admin spawn (legacy behavior).
        try:
            from world import services
            from world.event_bus import BUILDING_CONSTRUCTED

            event_bus = services.get_systems().get("event_bus")
            if event_bus is not None:
                event_bus.publish(
                    BUILDING_CONSTRUCTED,
                    player=owner, building=building, tile=planet_room,
                )
        except Exception:  # noqa: BLE001
            pass

        return CreateResult(ok=True, instance=building)

    @staticmethod
    def _resolve_owner(caller: Any, kwargs: dict) -> tuple[Any, str | None]:
        """The spawn owner: router-resolved ``player``, legacy ``owner=``
        name (``none`` -> unowned), default the caller. Returns
        ``(owner, error)``."""
        if kwargs.get("player") is not None:
            return kwargs["player"], None
        if "owner" in kwargs:
            owner_name = str(kwargs["owner"]).strip()
            if owner_name.lower() in _NO_OWNER_TOKENS:
                return None, None
            search = getattr(caller, "search", None)
            found = search(owner_name, quiet=True) if callable(search) else None
            if not found:
                return None, f"could not find player '{owner_name}'"
            return found[0] if isinstance(found, (list, tuple)) else found, None
        return caller, None

    def read(self, caller: Any, instance: Any) -> ShowReport:
        """``show``: identity header, live state, modifiable fields
        (Requirements 4.3, 7.2)."""
        name = str(getattr(instance, "key", None) or "?")
        btype = str(get_building_attr(instance, "building_type") or "??")
        level = get_building_attr(instance, "building_level", 1) or 1
        hp = get_building_attr(instance, "hp")
        hp_max = get_building_attr(instance, "hp_max")

        state_lines = [
            f"Level: {level}    "
            f"HP: {hp if hp is not None else '—'}/"
            f"{hp_max if hp_max is not None else '—'}",
            f"Owner: {self._owner_name(instance)}",
        ]
        coords = coords_of(instance)
        if coords is not None:
            state_lines.append(f"Position: ({coords[0]}, {coords[1]})")
        is_open = bool(get_building_attr(instance, "open", False))
        offline = bool(get_building_attr(instance, "offline", False))
        state_lines.append(
            f"Open to ranged fire: {'yes' if is_open else 'no'}    "
            f"Offline: {'yes' if offline else 'no'}"
        )

        fields_by_name = self.instance_fields()
        values = {"level": level, "hp": hp, "hp_max": hp_max}
        fields = [
            (fields_by_name[fname], values[fname], False)
            for fname in ("level", "hp", "hp_max")
        ]

        return ShowReport(
            header=f"{name} ({btype}) — building instance",
            state_lines=state_lines,
            fields=fields,
        )

    def update(self, caller: Any, instance: Any, field: str, value: Any
               ) -> SetResult:
        """``set``: bounded write through the shared building-attribute
        writer (``set_building_attr`` — the single safe accessor every
        building system uses). Defensively re-clamps into the field's
        bounds so the SetResult contract (applied always in-bounds) holds
        whoever calls update (Requirements 3.2, 3.3, 7.2)."""
        name = str(getattr(instance, "key", None) or "?")
        spec = self.instance_fields().get(field)
        if spec is None:
            valid = ", ".join(sorted(self.instance_fields()))
            return SetResult.fail(
                field, value,
                f"'{field}' is not a modifiable building field; "
                f"settable: {valid}",
            )

        try:
            requested = int(value)
        except (TypeError, ValueError):
            return SetResult.fail(
                field, value,
                f"value must be a whole number (got '{value}')",
            )

        lo, hi = resolve_bounds(spec, instance)
        applied = requested
        if lo is not None:
            applied = max(applied, int(lo))
        if hi is not None:
            applied = min(applied, int(hi))

        attr = "building_level" if field == "level" else field
        try:
            set_building_attr(instance, attr, applied)
            if field == "hp_max":
                # Preserve the hp <= hp_max invariant: lowering the cap
                # below the current hp caps hp immediately (matches how
                # shield capacity drops behave).
                hp = get_building_attr(instance, "hp")
                if _num(hp) and hp > applied:
                    set_building_attr(instance, "hp", applied)
        except Exception as exc:  # noqa: BLE001 - relay write-path failures
            return SetResult.fail(
                field, requested,
                f"could not write {field} onto {name}: {exc}",
            )

        return SetResult(ok=True, field=field, requested=requested,
                         applied=applied, clamped=(applied != requested))

    def delete(self, caller: Any, instance: Any) -> Any:
        """``destroy``: delete through the object deletion path.

        NOTE: deliberately does NOT publish ``BUILDING_DESTROYED`` — that
        event triggers base-elimination (a full NPC-base wipe on a
        Sentinel HQ), which a surgical admin delete never intends. Shield
        capacity on the survivors self-corrects on the ShieldSystem's
        next periodic sweep (legacy behavior preserved).
        """
        deleter = getattr(instance, "delete", None)
        if not callable(deleter):
            name = str(getattr(instance, "key", None) or "?")
            return CreateResult(
                ok=False, error=f"{name} has no deletion path",
            )
        return deleter()

    # ------------------------------------------------------------------ #
    #  Definition scope
    # ------------------------------------------------------------------ #

    def def_registry_dict(self) -> dict | None:
        """The live ``DataRegistry.buildings`` dict (merged registry)."""
        registry = self._live_registry()
        buildings = getattr(registry, "buildings", None) if registry else None
        return buildings if isinstance(buildings, dict) else None

    def def_resolve(self, token: str) -> Any | None:
        """Resolve a definition token via the existing
        ``resolve_building`` abbr/name/prefix matcher (Requirement 2.6)."""
        registry = self._live_registry()
        if registry is None:
            return None
        token = str(token or "").strip()
        if not token:
            return None
        return self._resolve_def(registry, token)

    def has_live_instances(self, def_key: str) -> bool:
        """Optional hook feeding the ``def show`` live-instances note:
        True when at least one live object stamps ``building_type ==
        def_key``. Degrades to False without a live game/DB."""
        try:
            from evennia.utils import search

            matches = search.search_object_attribute(
                key="building_type", value=def_key
            )
            return bool(matches)
        except Exception:  # noqa: BLE001 - no DB in the stubbed suite
            return False
