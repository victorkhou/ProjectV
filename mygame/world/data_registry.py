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
    BalanceConfig,
    BuildingDef,
    ItemDef,
    PlanetDef,
    PowerupDef,
    RankDef,
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
}

# Optional config files
_OPTIONAL_FILES = {
    "balance": "config/balance.yaml",
}


class DataRegistryError(Exception):
    """Raised when the Data Registry encounters a fatal loading error."""


class DataRegistry:
    """Centralized registry holding all game definitions loaded from YAML."""

    def __init__(self) -> None:
        self.buildings: dict[str, BuildingDef] = {}
        self.items: dict[str, ItemDef] = {}
        self.item_production_map: dict[str, list[str]] = {}
        self.ranks: list[RankDef] = []
        self.technologies: dict[str, TechnologyDef] = {}
        self.powerups: dict[str, PowerupDef] = {}
        self.terrain: dict[str, TerrainDef] = {}
        self.planets: dict[str, PlanetDef] = {}
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

        # --- Cross-validate references ---
        cross_errors = self._validator.cross_validate(self)
        if cross_errors:
            msg = "Cross-validation failed:\n" + "\n".join(cross_errors)
            logger.error(msg)
            raise DataRegistryError(msg)

        # --- Load optional balance config ---
        self._load_balance(base_path)

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
        self.ranks = temp.ranks
        self.technologies = temp.technologies
        self.powerups = temp.powerups
        self.terrain = temp.terrain
        self.planets = temp.planets
        self.balance = temp.balance

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
            )
            self.buildings[bdef.abbreviation] = bdef

    def _populate_items(self, data: dict) -> None:
        self.items = {}
        self.item_production_map = {}
        items_list = data.get("items", [])
        for entry in items_list:
            idef = ItemDef(
                key=entry["key"],
                name=entry["name"],
                slot=entry["slot"],
                stat_modifiers=entry.get("stat_modifiers", {}),
                ammo_cost=entry.get("ammo_cost"),
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

        # Build BalanceConfig from raw data, falling back to defaults for missing keys
        defaults = BalanceConfig()
        ps_raw = raw.get("production_scaling")
        if ps_raw is not None:
            production_scaling = {int(k): v for k, v in ps_raw.items()}
        else:
            production_scaling = defaults.production_scaling

        self.balance = BalanceConfig(
            production_scaling=production_scaling,
            turret_damage=raw.get("turret_damage", defaults.turret_damage),
            turret_radius=raw.get("turret_radius", defaults.turret_radius),
            xp_kill=raw.get("xp_kill", defaults.xp_kill),
            xp_building_destroy=raw.get("xp_building_destroy", defaults.xp_building_destroy),
            xp_damage=raw.get("xp_damage", defaults.xp_damage),
            xp_death_loss=raw.get("xp_death_loss", defaults.xp_death_loss),
            gather_amount=raw.get("gather_amount", defaults.gather_amount),
            player_default_health=raw.get(
                "player_default_health", defaults.player_default_health
            ),
            resource_respawn_ticks=raw.get(
                "resource_respawn_ticks", defaults.resource_respawn_ticks
            ),
            combat_lockout_ticks=raw.get(
                "combat_lockout_ticks", defaults.combat_lockout_ticks
            ),
            tick_interval=raw.get("tick_interval", defaults.tick_interval),
            chunk_size=raw.get("chunk_size", defaults.chunk_size),
            save_interval=raw.get("save_interval", defaults.save_interval),
            metrics_enabled=raw.get("metrics_enabled", defaults.metrics_enabled),
            metrics_interval=raw.get("metrics_interval", defaults.metrics_interval),
        )

    # ------------------------------------------------------------------ #
    #  Getter methods
    # ------------------------------------------------------------------ #

    def get_building(self, abbr: str) -> BuildingDef:
        """Get a building definition by abbreviation.

        Raises:
            KeyError: If abbreviation not found.
        """
        return self.buildings[abbr]

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
        """Get all item definitions producible by a building."""
        item_keys = self.item_production_map.get(building_abbr, [])
        result = []
        for key in item_keys:
            if key in self.items:
                result.append(self.items[key])
        return result

    def get_rank_for_xp(self, xp: int) -> RankDef:
        """Get the highest rank whose xp_threshold <= the given XP.

        Ranks are sorted by level ascending. Returns the last rank
        whose threshold the XP meets or exceeds.

        Raises:
            ValueError: If no ranks are loaded.
        """
        if not self.ranks:
            raise ValueError("No ranks loaded in registry")
        result = self.ranks[0]
        for rank in self.ranks:
            if rank.xp_threshold <= xp:
                result = rank
            else:
                break
        return result

    def get_rank_by_name(self, name: str) -> RankDef:
        """Get a rank definition by name.

        Raises:
            KeyError: If rank name not found.
        """
        for rank in self.ranks:
            if rank.name == name:
                return rank
        raise KeyError(f"Rank '{name}' not found")

    def get_technologies_for_rank(self, rank_level: int) -> list[TechnologyDef]:
        """Get all technologies available at or below the given rank level."""
        rank_names_at_or_below: set[str] = set()
        for rank in self.ranks:
            if rank.level <= rank_level:
                rank_names_at_or_below.add(rank.name)
        return [
            tdef
            for tdef in self.technologies.values()
            if tdef.required_rank in rank_names_at_or_below
        ]

    def get_powerups_for_rank(self, rank_level: int) -> list[PowerupDef]:
        """Get all powerups available at or below the given rank level."""
        rank_names_at_or_below: set[str] = set()
        for rank in self.ranks:
            if rank.level <= rank_level:
                rank_names_at_or_below.add(rank.name)
        return [
            pdef
            for pdef in self.powerups.values()
            if pdef.required_rank in rank_names_at_or_below
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
