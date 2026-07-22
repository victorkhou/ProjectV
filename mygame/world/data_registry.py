"""
Data Registry for the RTS Combat Overworld game.

Centralized runtime store for all loaded definitions and configuration.
Loads YAML definition files, validates them through SchemaValidator,
and provides getter methods for all game systems.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import yaml

from world.definitions import (
    AbilityGateDef,
    BalanceConfig,
    BaseTemplateDef,
    BuildingDef,
    ClassDef,
    ItemDef,
    PlanetDef,
    PowerupDef,
    RankDef,
    TemplateBuildingDef,
    TemplateGuardDef,
    TerrainDef,
    TechnologyDef,
)
from world.schema_validator import SchemaValidator

logger = logging.getLogger("mygame.data_registry")

# Required definition files (relative to base_path)
_REQUIRED_FILES = {
    "buildings": "definitions/buildings.yaml",
    "items": "definitions/items.yaml",
    "ranks": "definitions/ranks.yaml",
    "technologies": "definitions/technologies.yaml",
    "powerups": "definitions/powerups.yaml",
    "terrain": "definitions/terrain.yaml",
    "ability_gates": "definitions/ability_gates.yaml",
}

# Optional config files
_OPTIONAL_FILES = {
    "balance": "config/balance.yaml",
    # NPC base templates (PvE NPC bases feature). Optional so the game loads
    # fine without it — an absent/empty file just means no NPC bases spawn.
    "outposts": "definitions/outposts.yaml",
    # Player class definitions (state 3.2 selection). Optional so the game
    # loads fine without it — an absent/empty file just means the spawning
    # flow offers a single default class.
    "classes": "definitions/classes.yaml",
    # Alliance perk catalog (alliance feature). Optional so the game loads fine
    # without it — an absent/empty file just means the alliance feature offers
    # no perks (membership/treasury/leaderboard still work).
    "alliance_perks": "definitions/alliance_perks.yaml",
    # Onboarding directive chain (early-game rebalance R10). Optional — an
    # absent/empty file just means no directive checklist runs.
    "directives": "definitions/directives.yaml",
}


class DataRegistryError(Exception):
    """Raised when the Data Registry encounters a fatal loading error."""


class DataRegistry:
    """Centralized registry holding all game definitions loaded from YAML."""

    #: Process-wide singleton, registered once at server start via
    #: ``set_instance``. Lets owner-agnostic helpers (e.g. ``world.progression``,
    #: ``chat_system``, ``agent_scripts``) resolve the live registry without a
    #: reference being threaded through. ``None`` until registered (e.g. in the
    #: fast unit-test suite), which every caller treats as "unavailable".
    _instance: "DataRegistry | None" = None

    @classmethod
    def get_instance(cls) -> "DataRegistry | None":
        """Return the process-wide DataRegistry singleton, or ``None`` if unset.

        Set by ``set_instance`` at server start. Callers must tolerate ``None``
        (uninitialized state / test suites) and apply their own fallback.
        """
        return cls._instance

    @classmethod
    def set_instance(cls, instance: "DataRegistry | None") -> None:
        """Register the process-wide singleton (called once at server start).

        Intentionally NOT called from ``__init__`` so the throwaway temp
        registry built inside ``reload_all`` never usurps the live singleton.
        """
        cls._instance = instance

    def __init__(self) -> None:
        self.buildings: dict[str, BuildingDef] = {}
        self.items: dict[str, ItemDef] = {}
        self.item_production_map: dict[str, list[str]] = {}
        #: Memoized resolved ItemDef lists per building abbreviation, built
        #: lazily by get_items_for_building and cleared on (re)load. The
        #: production map and items are static between loads, so this avoids
        #: rebuilding the list every tick per equipment building.
        self._items_for_building_cache: dict[str, list[ItemDef]] = {}
        self.ranks: list[RankDef] = []
        self.technologies: dict[str, TechnologyDef] = {}
        self.powerups: dict[str, PowerupDef] = {}
        self.terrain: dict[str, TerrainDef] = {}
        self.ability_gates: dict[str, AbilityGateDef] = {}
        self.planets: dict[str, PlanetDef] = {}
        #: NPC-base templates keyed by tier ("outpost", "fortress", ...); loaded
        #: from the optional data/definitions/outposts.yaml. Empty when absent.
        self.base_templates: dict[str, BaseTemplateDef] = {}
        #: Selectable player classes keyed by class key ("vanguard", ...); loaded
        #: from the optional data/definitions/classes.yaml. Empty when absent
        #: (the spawning flow then offers a single default class).
        self.classes: dict[str, ClassDef] = {}
        #: Alliance perk catalog keyed by perk key ("shared_vision", ...); loaded
        #: from the optional data/definitions/alliance_perks.yaml. Each value is a
        #: dict {category, effect_type, levels: {int_level: {tier, cost, ...}}}.
        #: Empty when absent (the alliance feature then offers no perks). Kept as
        #: raw nested dicts (not a dataclass) since AllianceSystem interprets the
        #: level/tier/cost/effect payloads directly.
        self.alliance_perks: dict[str, dict] = {}
        #: Onboarding directive chain (early-game rebalance R10): an ORDERED
        #: list of directive dicts loaded from the optional
        #: data/definitions/directives.yaml. Empty when absent (no checklist).
        self.directives: list[dict] = []
        self.balance: BalanceConfig = BalanceConfig()
        self._base_path: str = "data"
        self._validator = SchemaValidator()

    # ------------------------------------------------------------------ #
    #  Loading
    # ------------------------------------------------------------------ #

    def load_all(self, base_path: str = "data") -> None:
        """Load and validate all definition files.

        Args:
            base_path: Root directory containing definitions/ and config/ subdirs.

        Raises:
            DataRegistryError: If any required file is missing or fails validation.
        """
        self._base_path = base_path
        errors: list[str] = []

        # --- Check required files exist ---
        for key, rel_path in _REQUIRED_FILES.items():
            full_path = os.path.join(base_path, rel_path)
            if not os.path.isfile(full_path):
                errors.append(f"Required definition file missing: {full_path}")

        if errors:
            msg = "Cannot start: missing required definition files:\n" + "\n".join(errors)
            logger.error(msg)
            raise DataRegistryError(msg)

        # --- Load each file ---
        raw: dict[str, Any] = {}
        for key, rel_path in _REQUIRED_FILES.items():
            full_path = os.path.join(base_path, rel_path)
            try:
                with open(full_path, "r") as f:
                    raw[key] = yaml.safe_load(f)
            except Exception as exc:
                errors.append(f"Failed to read {full_path}: {exc}")

        if errors:
            msg = "Cannot start: errors reading definition files:\n" + "\n".join(errors)
            logger.error(msg)
            raise DataRegistryError(msg)

        # --- Validate schemas ---
        errors.extend(self._validator.validate_buildings(raw["buildings"]))
        errors.extend(self._validator.validate_items(raw["items"]))
        errors.extend(self._validator.validate_ranks(raw["ranks"]))
        errors.extend(self._validator.validate_technologies(raw["technologies"]))
        errors.extend(self._validator.validate_powerups(raw["powerups"]))
        errors.extend(self._validator.validate_terrain(raw["terrain"]))
        errors.extend(self._validator.validate_ability_gates(raw["ability_gates"]))

        if errors:
            msg = "Definition validation failed:\n" + "\n".join(errors)
            logger.error(msg)
            raise DataRegistryError(msg)

        # --- Populate registry dicts ---
        self._populate_buildings(raw["buildings"])
        self._populate_items(raw["items"])
        self._populate_ranks(raw["ranks"])
        self._populate_technologies(raw["technologies"])
        self._populate_powerups(raw["powerups"])
        self._populate_terrain(raw["terrain"])
        self._populate_ability_gates(raw["ability_gates"])

        # --- Cross-validate references ---
        cross_errors = self._validator.cross_validate(self)
        if cross_errors:
            msg = "Cross-validation failed:\n" + "\n".join(cross_errors)
            logger.error(msg)
            raise DataRegistryError(msg)

        # --- Load optional balance config ---
        self._load_balance(base_path)

        # --- Load optional NPC-base templates ---
        self._load_base_templates(base_path)

        # --- Load optional player classes ---
        self._load_classes(base_path)

        # --- Load optional alliance perk catalog ---
        self._load_alliance_perks(base_path)

        # --- Load optional onboarding directives ---
        self._load_directives(base_path)

        logger.info("Data Registry loaded successfully from '%s'", base_path)

    def reload_all(self) -> tuple[bool, list[str]]:
        """Hot-reload all definition files with atomic swap.

        Re-reads and re-validates all files into a temporary registry.
        On success, atomically swaps contents. On failure, preserves
        current data and returns errors.

        Returns:
            Tuple of (success: bool, errors: list[str]).
        """
        temp = DataRegistry()
        try:
            temp.load_all(self._base_path)
        except DataRegistryError as exc:
            error_lines = str(exc).split("\n")
            logger.warning("Hot-reload failed, keeping current data: %s", error_lines[0])
            return False, error_lines

        # Atomic swap — replace all data attributes
        self.buildings = temp.buildings
        self.items = temp.items
        self.item_production_map = temp.item_production_map
        self._items_for_building_cache = {}  # invalidate memo on hot-reload
        self.ranks = temp.ranks
        self.technologies = temp.technologies
        self.powerups = temp.powerups
        self.terrain = temp.terrain
        self.ability_gates = temp.ability_gates
        self.planets = temp.planets
        self.base_templates = temp.base_templates
        self.classes = temp.classes
        self.alliance_perks = temp.alliance_perks
        self.directives = temp.directives
        self.balance = temp.balance

        # Rebuild the shared level<->XP threshold curve. The curve is the
        # R14 hybrid formula parameterized by balance.yaml tunables
        # (xp_curve_*) — the *ranks* argument only trips the rebuild; ranks.yaml
        # xp_threshold values are legacy display data and do NOT feed the curve.
        # Rebuilding here lets a balance.yaml retune of the curve take effect on
        # hot-reload. Guarded so a rebuild hiccup never invalidates the
        # successful data swap above.
        try:
            from world import progression

            progression.build_thresholds(self.ranks)
        except Exception:
            logger.exception(
                "Hot-reload: failed to rebuild progression thresholds; "
                "level<->XP curve may be stale until restart."
            )

        logger.info("Hot-reload completed successfully")
        return True, []

    # ------------------------------------------------------------------ #
    #  Private population helpers
    # ------------------------------------------------------------------ #

    def _populate_buildings(self, data: list[dict]) -> None:
        self.buildings = {}
        for entry in data:
            bdef = BuildingDef(
                name=entry["name"],
                abbreviation=entry["abbreviation"],
                cost=entry.get("cost", {}),
                max_health=entry["max_health"],
                requires_hq=entry.get("requires_hq", True),
                required_terrain=entry.get("required_terrain"),
                category=entry.get("category", ""),
                produces=entry.get("produces"),
                unlocks=entry.get("unlocks", []),
                map_symbol=entry.get("map_symbol", entry["abbreviation"]),
                build_time_seconds=entry.get("build_time_seconds", 120),
                max_level=entry.get("max_level", 5),
                rank_requirement=entry.get("rank_requirement", 1),
                requires_agent=entry.get("requires_agent", False),
                storage_capacity=entry.get("storage_capacity", 0),
                capabilities=frozenset(entry.get("capabilities", []) or []),
                unlock_deed=entry.get("unlock_deed"),
                unlock_deed_count=entry.get("unlock_deed_count", 1),
            )
            self.buildings[bdef.abbreviation] = bdef

    def _populate_items(self, data: dict) -> None:
        self.items = {}
        self.item_production_map = {}
        self._items_for_building_cache = {}
        items_list = data.get("items", [])
        for entry in items_list:
            idef = ItemDef(
                key=entry["key"],
                name=entry["name"],
                slot=entry.get("slot", ""),
                category=entry.get("category", "armor"),
                stat_modifiers=entry.get("stat_modifiers", {}),
                weapon_type=entry.get("weapon_type"),
                damage_type=entry.get("damage_type", "physical"),
                ammo_type=entry.get("ammo_type"),
                ammo_per_shot=entry.get("ammo_per_shot", 1),
                magazine_size=entry.get("magazine_size"),
                ammo_cost=entry.get("ammo_cost"),
                craft_cost=entry.get("craft_cost"),
                effect=entry.get("effect"),
                max_stack=entry.get("max_stack", 99),
                weight=entry.get("weight", 1.0),
                classification=entry.get("classification", "modern"),
                required_rank=entry.get("required_rank"),
            )
            self.items[idef.key] = idef
        self.item_production_map = data.get("production_map", {})

    def _populate_ranks(self, data: list[dict]) -> None:
        ranks = []
        for entry in data:
            rdef = RankDef(
                name=entry["name"],
                level=entry["level"],
                xp_threshold=entry["xp_threshold"],
                unlocks=entry.get("unlocks", []),
                agent_cap=entry.get("agent_cap", 2),
                planet_access=entry.get("planet_access", []),
            )
            ranks.append(rdef)
        self.ranks = sorted(ranks, key=lambda r: r.level)

    def _populate_technologies(self, data: list[dict]) -> None:
        self.technologies = {}
        for entry in data:
            tdef = TechnologyDef(
                name=entry["name"],
                key=entry["key"],
                required_rank=entry["required_rank"],
                resource_cost=entry.get("resource_cost", {}),
                research_ticks=entry.get("research_ticks", 10),
                effect_type=entry.get("effect_type", ""),
                effect_value=entry.get("effect_value"),
            )
            self.technologies[tdef.key] = tdef

    def _populate_powerups(self, data: list[dict]) -> None:
        self.powerups = {}
        for entry in data:
            pdef = PowerupDef(
                name=entry["name"],
                key=entry["key"],
                required_rank=entry["required_rank"],
                effect_type=entry["effect_type"],
                effect_value=entry["effect_value"],
                duration_ticks=entry["duration_ticks"],
                cooldown_ticks=entry["cooldown_ticks"],
            )
            self.powerups[pdef.key] = pdef

    def _populate_terrain(self, data: dict) -> None:
        self.terrain = {}
        self.planets = {}
        for entry in data.get("terrain", []):
            tdef = TerrainDef(
                terrain_type=entry["terrain_type"],
                map_symbol=entry["map_symbol"],
                resource_type=entry.get("resource_type"),
                passable=entry.get("passable", True),
            )
            self.terrain[tdef.terrain_type] = tdef
        for entry in data.get("planets", []):
            pdef = PlanetDef(
                name=entry["name"],
                planet_type=entry.get("planet_type", ""),
                terrain_types=entry.get("terrain_types", []),
            )
            self.planets[pdef.name] = pdef

    def _populate_ability_gates(self, data: list[dict]) -> None:
        self.ability_gates = {}
        for entry in data:
            adef = AbilityGateDef(
                key=entry["key"],
                required_level=entry["required_level"],
            )
            self.ability_gates[adef.key] = adef

    def _load_balance(self, base_path: str) -> None:
        """Load balance config. Uses hardcoded defaults if file is missing."""
        balance_path = os.path.join(base_path, _OPTIONAL_FILES["balance"])
        if not os.path.isfile(balance_path):
            logger.warning(
                "Balance config file not found at '%s', using hardcoded defaults.",
                balance_path,
            )
            self.balance = BalanceConfig()
            return

        try:
            with open(balance_path, "r") as f:
                raw = yaml.safe_load(f)
        except Exception as exc:
            raise DataRegistryError(f"Failed to read balance config: {exc}")

        if raw is None:
            logger.warning("Balance config file is empty, using hardcoded defaults.")
            self.balance = BalanceConfig()
            return

        errors = self._validator.validate_balance(raw)
        if errors:
            msg = "Balance config validation failed:\n" + "\n".join(errors)
            logger.error(msg)
            raise DataRegistryError(msg)

        self.balance = self._build_balance(raw)

    def _build_balance(self, raw: dict) -> BalanceConfig:
        """Construct a BalanceConfig from raw YAML, defaulting missing keys.

        Scalar fields are pulled generically from the dataclass field list so
        a newly-added scalar tunable only needs a field on ``BalanceConfig``
        (plus a validator entry) — no change here.  The nested-dict fields
        (balance maps with int keys) need light key coercion and are handled
        explicitly.
        """
        from dataclasses import fields

        defaults = BalanceConfig()

        # Fields needing custom key/type handling — excluded from the generic
        # scalar copy below and rebuilt explicitly.
        special = {
            "demolish_refund_rates", "base_training_cost",
            "resource_weights", "alliance_level_thresholds",
        }

        kwargs: dict[str, Any] = {}
        for f in fields(BalanceConfig):
            if f.name in special:
                continue
            kwargs[f.name] = raw.get(f.name, getattr(defaults, f.name))

        # demolish_refund_rates: level keys may be strings → coerce to int.
        dr_raw = raw.get("demolish_refund_rates")
        kwargs["demolish_refund_rates"] = (
            {int(k): v for k, v in dr_raw.items()}
            if dr_raw is not None
            else defaults.demolish_refund_rates
        )

        # base_training_cost: resource-name keys stay as strings.
        btc_raw = raw.get("base_training_cost")
        kwargs["base_training_cost"] = (
            dict(btc_raw) if btc_raw is not None else defaults.base_training_cost
        )

        # resource_weights: resource-name keys stay as strings; float values.
        rw_raw = raw.get("resource_weights")
        kwargs["resource_weights"] = (
            dict(rw_raw) if rw_raw is not None else defaults.resource_weights
        )

        # alliance_level_thresholds: summed-level keys may be strings → coerce to
        # int (YAML reads "40" as a string key; int-level lookups would miss).
        alt_raw = raw.get("alliance_level_thresholds")
        kwargs["alliance_level_thresholds"] = (
            {int(k): int(v) for k, v in alt_raw.items()}
            if alt_raw is not None
            else defaults.alliance_level_thresholds
        )

        return BalanceConfig(**kwargs)

    def _read_optional_yaml(self, base_path: str, file_key: str, *,
                            missing_msg: str) -> dict | None:
        """Read an optional YAML file into a dict, or return ``None``.

        The shared read-guard prologue for the optional dict-shaped catalogs
        (NPC-base templates, player classes, alliance perks): resolve
        ``_OPTIONAL_FILES[file_key]`` under *base_path*; if the file is absent
        log ``missing_msg`` (an info — the feature is simply disabled) and return
        ``None``; on a read error log the exception and return ``None``; and
        return ``None`` for a non-dict payload. Returns the parsed dict on
        success. Loaders keep only their own per-entry population loop.

        NOTE: not used by ``_load_balance``, which has stricter semantics (raises
        on read error, warns, installs a default, and empty-checks ``raw is
        None``).
        """
        path = os.path.join(base_path, _OPTIONAL_FILES[file_key])
        if not os.path.isfile(path):
            logger.info(missing_msg, path)
            return None
        try:
            with open(path, "r") as f:
                raw = yaml.safe_load(f)
        except Exception:
            logger.exception("Failed to read %s at '%s'.", file_key, path)
            return None
        if not isinstance(raw, dict):
            return None
        return raw

    def _load_base_templates(self, base_path: str) -> None:
        """Load optional NPC-base templates from outposts.yaml.

        Absent, empty, or malformed → an empty template set (the feature simply
        spawns no bases), so a template problem never blocks server start. Each
        top-level key is a tier ("outpost", "fortress"); its value describes the
        buildings, guards, and loot.
        """
        self.base_templates = {}
        raw = self._read_optional_yaml(
            base_path, "outposts",
            missing_msg="No NPC-base templates at '%s' — NPC bases disabled.",
        )
        if raw is None:
            return

        for tier, spec in raw.items():
            if not isinstance(spec, dict):
                logger.warning("Skipping malformed NPC-base template %r.", tier)
                continue
            try:
                buildings = [
                    TemplateBuildingDef(
                        building_type=b["type"],
                        offset=tuple(b.get("offset", (0, 0))),
                        hp=b.get("hp"),
                        level=b.get("level", 1),
                    )
                    for b in spec.get("buildings", [])
                ]
                guards = [
                    TemplateGuardDef(
                        role=g.get("role", "guard"),
                        weapon_type=g.get("weapon_type", "melee"),
                        count=int(g.get("count", 1)),
                        hp=g.get("hp"),
                    )
                    for g in spec.get("guards", [])
                ]
                # Loot: keep raw value — int or [min, max] list (R8.1).
                loot_raw = spec.get("loot") or {}
                loot = {}
                for k, v in loot_raw.items():
                    if isinstance(v, list):
                        loot[k] = [int(v[0]), int(v[1])]
                    else:
                        loot[k] = int(v)
            except (KeyError, TypeError, ValueError):
                logger.exception("Skipping invalid NPC-base template %r.", tier)
                continue
            # Gear-pool key validation (R11.5): every pool entry must be a
            # known item key so a drop roll can never silently no-op. Items
            # are a required file loaded before this optional one, so
            # self.items is authoritative here. Loud (ERROR log) + filtered
            # rather than a raise — outposts.yaml is an optional file whose
            # problems must never block server start.
            gear_pool = self._validate_gear_pool(
                tier, "gear_pool", spec.get("gear_pool") or []
            )
            rare_pool = self._validate_gear_pool(
                tier, "rare_pool", spec.get("rare_pool") or []
            )
            self.base_templates[tier] = BaseTemplateDef(
                tier=tier,
                display_name=spec.get("display_name", tier.title()),
                buildings=buildings,
                guards=guards,
                loot=loot,
                guard_loot_chance=spec.get("guard_loot_chance"),
                guard_loot_amount=spec.get("guard_loot_amount"),
                gear_drop_chance=spec.get("gear_drop_chance"),
                rare_gear_chance=spec.get("rare_gear_chance"),
                gear_pool=gear_pool,
                rare_pool=rare_pool,
            )
        logger.info("Loaded %d NPC-base template(s).", len(self.base_templates))

    def _validate_gear_pool(self, tier: str, field: str, pool: list) -> list:
        """Return *pool* with unknown item keys removed, logging each (R11.5)."""
        valid = []
        for key in pool:
            if key in self.items:
                valid.append(key)
            else:
                logger.error(
                    "NPC-base template %r: %s entry %r is not a known item "
                    "key (items.yaml) — removed from the drop pool.",
                    tier, field, key,
                )
        return valid

    def get_base_template(self, tier: str) -> "BaseTemplateDef | None":
        """Return the NPC-base template for *tier*, or ``None`` if undefined."""
        return self.base_templates.get(tier)

    def _load_classes(self, base_path: str) -> None:
        """Load optional player classes from classes.yaml.

        Absent, empty, or malformed → an empty class set (the spawning flow
        offers a single default), so a class-content problem never blocks server
        start. Each entry has a ``key`` (persisted on the character), a display
        ``name``, and an optional ``description``.
        """
        self.classes = {}
        raw = self._read_optional_yaml(
            base_path, "classes",
            missing_msg="No player classes at '%s' — using a default.",
        )
        if raw is None:
            return
        for entry in raw.get("classes", []) or []:
            try:
                key = entry["key"]
            except (KeyError, TypeError):
                logger.warning("Skipping malformed player class entry %r.", entry)
                continue
            # key drives dict insertion (must be hashable), .title() for the
            # default name, and persistence on db.player_class — so a non-string
            # key would crash load, not degrade. Enforce str here, matching the
            # "malformed → skip, never block start" contract in the docstring.
            if not isinstance(key, str) or not key:
                logger.warning("Skipping player class with non-string key %r.", key)
                continue
            name = entry.get("name")
            description = entry.get("description")
            self.classes[key] = ClassDef(
                key=key,
                name=name if isinstance(name, str) and name else key.title(),
                description=(description if isinstance(description, str) else "").strip(),
                stat_modifiers=entry.get("stat_modifiers") or {},
            )
        logger.info("Loaded %d player class(es).", len(self.classes))

    def _load_alliance_perks(self, base_path: str) -> None:
        """Load the optional alliance perk catalog from alliance_perks.yaml.

        Absent, empty, or malformed → an empty catalog (the alliance feature
        offers no perks), so a perk-content problem never blocks server start.
        Each top-level perk key maps to ``{category, effect_type, levels}`` where
        ``levels`` is ``{int_level: {tier, cost, <effect payload>}}``. YAML level
        keys may arrive as strings, so they are coerced to int (int-level lookups
        would otherwise miss — YAML reads numeric keys as strings).
        Malformed individual perks/levels are skipped, not fatal.
        """
        self.alliance_perks = {}
        raw = self._read_optional_yaml(
            base_path, "alliance_perks",
            missing_msg="No alliance perks at '%s' — alliances offer no perks.",
        )
        if raw is None:
            return
        for key, spec in (raw.get("perks", {}) or {}).items():
            if not isinstance(key, str) or not isinstance(spec, dict):
                logger.warning("Skipping malformed alliance perk %r.", key)
                continue
            levels_raw = spec.get("levels")
            if not isinstance(levels_raw, dict) or not levels_raw:
                logger.warning("Alliance perk '%s' has no levels — skipping.", key)
                continue
            levels: dict[int, dict] = {}
            for lvl, payload in levels_raw.items():
                try:
                    lvl_int = int(lvl)
                except (TypeError, ValueError):
                    logger.warning("Perk '%s' has non-int level %r — skipping.", key, lvl)
                    continue
                if not isinstance(payload, dict):
                    continue
                levels[lvl_int] = dict(payload)
            if not levels:
                continue
            self.alliance_perks[key] = {
                "category": spec.get("category", key),
                "effect_type": spec.get("effect_type"),
                "levels": levels,
            }
        logger.info("Loaded %d alliance perk(s).", len(self.alliance_perks))

    def get_alliance_perk(self, key: str) -> "dict | None":
        """Return the alliance perk spec for *key*, or ``None`` if undefined."""
        return self.alliance_perks.get(key)

    def _load_directives(self, base_path: str) -> None:
        """Load the optional onboarding directive chain from directives.yaml.

        Absent, empty, or malformed → an empty chain (no checklist runs), so
        a directive-content problem never blocks server start. The file is an
        ORDERED list of directive dicts; entries missing a ``key`` or
        ``trigger_event`` are skipped (order of the survivors is preserved).
        NOTE: list-shaped, so it can't share ``_read_optional_yaml`` (dict-only).
        """
        self.directives = []
        path = os.path.join(base_path, _OPTIONAL_FILES["directives"])
        if not os.path.isfile(path):
            logger.info("No directives at '%s' — onboarding chain disabled.", path)
            return
        try:
            with open(path, "r") as f:
                raw = yaml.safe_load(f)
        except Exception:
            logger.exception("Failed to read directives at '%s'.", path)
            return
        if not isinstance(raw, list):
            return
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            if not entry.get("key") or not entry.get("trigger_event"):
                logger.warning("Skipping malformed directive %r.", entry)
                continue
            self.directives.append(dict(entry))
        logger.info("Loaded %d directive step(s).", len(self.directives))

    def get_class(self, key: str) -> "ClassDef | None":
        """Return the player class for *key*, or ``None`` if undefined."""
        return self.classes.get(key)

    def resolve_class(self, token: str) -> "ClassDef | None":
        """Resolve a player class by key OR name, typo-/prefix-tolerantly."""
        return self._resolve(token, self.classes, key_upper=False)

    # ------------------------------------------------------------------ #
    #  Getter methods
    # ------------------------------------------------------------------ #

    def get_building(self, abbr: str) -> BuildingDef:
        """Get a building definition by abbreviation.

        Raises:
            KeyError: If abbreviation not found.
        """
        return self.buildings[abbr]

    @staticmethod
    def _resolve(token: str, by_key: dict, name_attr: str = "name",
                 key_upper: bool = True):
        """Resolve a definition by registry key OR full name, typo-tolerantly.

        Shared implementation behind the ``resolve_*`` family. Players type
        either the short key (``EX``, an item key), the human name
        (``extractor``), or an unambiguous PREFIX of either (``plasma_gr`` →
        ``plasma_grenade``, ``head`` → ``Headquarters``); this accepts all three.
        Matching is case-insensitive and tolerates spaces vs. underscores in
        names and keys (``power armor`` == ``Power_Armor``). Returns ``None`` if
        nothing matches, so callers surface a clean "unknown" message rather than
        raising.

        Resolution order (first hit wins), so an exact match always beats a
        prefix and a prefix never shadows a full name someone typed exactly:
          1. exact registry key,
          2. exact full name,
          3. unambiguous prefix of a key or name (exactly ONE def matches;
             an ambiguous prefix returns ``None`` so the caller can prompt).

        Args:
            token: The user-supplied string.
            by_key: The registry dict (key -> def).
            name_attr: Attribute on each def holding its display name.
            key_upper: Upper-case the token before the exact-key lookup
                (building abbreviations are stored upper-case; item/tech/powerup
                keys are stored verbatim, so those pass ``False``).
        """
        if not token:
            return None
        key = token.strip()

        # 1) Exact registry key.
        lookup = key.upper() if key_upper else key
        if lookup in by_key:
            return by_key[lookup]

        # 2) Full name, case-insensitive and space/underscore-insensitive.
        norm = key.lower().replace("_", " ").strip()
        for d in by_key.values():
            dname = getattr(d, name_attr, "") or ""
            if dname.lower().replace("_", " ").strip() == norm:
                return d

        # 3) Unambiguous prefix of a key OR display name. Collect every def whose
        # normalised key or name STARTS WITH the token; resolve only when exactly
        # one distinct def matches (an ambiguous prefix is treated as no match).
        if not norm:
            return None
        matches = []
        for d in by_key.values():
            dkey = str(getattr(d, "key", "") or getattr(d, "abbreviation", "") or "")
            dkey_norm = dkey.lower().replace("_", " ").strip()
            dname_norm = (getattr(d, name_attr, "") or "").lower().replace("_", " ").strip()
            if dkey_norm.startswith(norm) or dname_norm.startswith(norm):
                if d not in matches:
                    matches.append(d)
        if len(matches) == 1:
            return matches[0]
        return None

    def resolve_building(self, token: str) -> BuildingDef | None:
        """Resolve a building by abbreviation OR full name, case-insensitively.

        Players type either the 2-letter abbreviation (``EX``) or the human
        name (``extractor``); this accepts both. Returns ``None`` if nothing
        matches (callers surface a clean "unknown building" message).
        """
        return self._resolve(token, self.buildings, key_upper=True)

    def resolve_item(self, token: str) -> ItemDef | None:
        """Resolve an item by key OR full name, typo-tolerantly (or ``None``)."""
        return self._resolve(token, self.items, key_upper=False)

    def resolve_technology(self, token: str) -> TechnologyDef | None:
        """Resolve a technology by key OR full name (or ``None``)."""
        return self._resolve(token, self.technologies, key_upper=False)

    def resolve_powerup(self, token: str) -> PowerupDef | None:
        """Resolve a powerup by key OR full name (or ``None``)."""
        return self._resolve(token, self.powerups, key_upper=False)

    def get_item(self, key: str) -> ItemDef:
        """Get an item definition by key.

        Raises:
            KeyError: If item key not found.
        """
        return self.items[key]

    def get_items_for_slot(self, slot: str) -> list[ItemDef]:
        """Get all item definitions for a given equipment slot."""
        return [idef for idef in self.items.values() if idef.slot == slot]

    def get_items_for_building(self, building_abbr: str) -> list[ItemDef]:
        """Get all item definitions producible by a building.

        Memoized per abbreviation: the production map and items are static
        between loads, so the resolved list is cached and reused (the tick loop
        calls this every tick per active equipment building). The cache is
        cleared whenever definitions are (re)loaded.
        """
        cached = self._items_for_building_cache.get(building_abbr)
        if cached is not None:
            return cached
        item_keys = self.item_production_map.get(building_abbr, [])
        result = [self.items[key] for key in item_keys if key in self.items]
        self._items_for_building_cache[building_abbr] = result
        return result

    # NOTE: no rank-from-XP lookup is provided here. ranks.yaml xp_threshold
    # values are legacy display data under the R14 formula-derived curve — a
    # ranking based on them could disagree with the authoritative RANK_BANDS
    # lookup (rank_system.rank_from_level). Zero production callers remained.

    def get_rank_by_name(self, name: str) -> RankDef:
        """Get a rank definition by name.

        Raises:
            KeyError: If rank name not found.
        """
        for rank in self.ranks:
            if rank.name == name:
                return rank
        raise KeyError(f"Rank '{name}' not found")

    def get_rank_by_level(self, level: int) -> RankDef | None:
        """Get the rank definition whose ``level`` matches, or ``None``.

        The single exact-match ``level -> RankDef`` lookup that several callers
        need to turn a rank number into its display name. Returns ``None`` (not
        a raise) so display paths can fall back to a ``"Rank N"`` label.
        """
        for rank in self.ranks:
            if rank.level == level:
                return rank
        return None

    def _rank_names_at_or_below(self, rank_level: int) -> set[str]:
        """Return the set of rank names whose level is <= ``rank_level``.

        Shared by the ``get_*_for_rank`` filters so the "which ranks has the
        player reached" logic lives in one place.
        """
        return {rank.name for rank in self.ranks if rank.level <= rank_level}

    def get_technologies_for_rank(self, rank_level: int) -> list[TechnologyDef]:
        """Get all technologies available at or below the given rank level."""
        available = self._rank_names_at_or_below(rank_level)
        return [
            tdef
            for tdef in self.technologies.values()
            if tdef.required_rank in available
        ]

    def get_powerups_for_rank(self, rank_level: int) -> list[PowerupDef]:
        """Get all powerups available at or below the given rank level."""
        available = self._rank_names_at_or_below(rank_level)
        return [
            pdef
            for pdef in self.powerups.values()
            if pdef.required_rank in available
        ]

    def get_terrain(self, terrain_type: str) -> TerrainDef:
        """Get a terrain definition by type string.

        Raises:
            KeyError: If terrain type not found.
        """
        return self.terrain[terrain_type]

    def get_planet(self, name: str) -> PlanetDef:
        """Get a planet definition by name.

        Raises:
            KeyError: If planet name not found.
        """
        return self.planets[name]

    def get_ability_gate(self, key: str) -> AbilityGateDef:
        """Get an ability-gate definition by key.

        Raises:
            KeyError: If ability-gate key not found.
        """
        return self.ability_gates[key]

    def get_ability_gates(self) -> list[AbilityGateDef]:
        """Get all ability-gate definitions."""
        return list(self.ability_gates.values())
