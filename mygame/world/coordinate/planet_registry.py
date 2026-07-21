"""
Planet Registry for the Procedural Coordinate World system.

Stores Coordinate_Space definitions loaded from YAML configuration.
Follows the same YAML loading pattern as the existing Data_Registry.
"""

from __future__ import annotations

import logging
import os

import yaml

from world.definitions import CoordinateSpaceDef

logger = logging.getLogger("mygame.planet_registry")


class PlanetRegistryError(Exception):
    """Raised when the Planet Registry encounters a loading or validation error."""


class PlanetRegistry:
    """Configuration store for all Coordinate_Space definitions."""

    def __init__(self) -> None:
        self._spaces: dict[str, CoordinateSpaceDef] = {}

    def load_from_yaml(self, path: str) -> None:
        """Load planet definitions from a YAML file.

        Args:
            path: Path to the planets.yaml file.

        Raises:
            PlanetRegistryError: If the file is missing, malformed, or
                contains invalid planet definitions.
        """
        if not os.path.isfile(path):
            raise PlanetRegistryError(f"Planet definition file not found: {path}")

        try:
            with open(path, "r") as f:
                raw = yaml.safe_load(f)
        except Exception as exc:
            raise PlanetRegistryError(f"Failed to read {path}: {exc}")

        if raw is None or "planets" not in raw:
            raise PlanetRegistryError(
                f"Planet definition file must contain a 'planets' key: {path}"
            )

        planets_list = raw["planets"]
        if not isinstance(planets_list, list):
            raise PlanetRegistryError("'planets' must be a list of planet definitions")

        errors: list[str] = []
        seen_keys: set[str] = set()
        spaces: dict[str, CoordinateSpaceDef] = {}

        for i, entry in enumerate(planets_list):
            if not isinstance(entry, dict):
                errors.append(f"Planet entry {i} is not a mapping")
                continue

            planet_key = entry.get("planet_key")
            if not planet_key:
                errors.append(f"Planet entry {i} missing 'planet_key'")
                continue

            # Unique key check
            if planet_key in seen_keys:
                errors.append(f"Duplicate planet_key: '{planet_key}'")
                continue
            seen_keys.add(planet_key)

            # Validate dimensions are positive
            width = entry.get("width", 0)
            height = entry.get("height", 0)
            if not isinstance(width, int) or width <= 0:
                errors.append(
                    f"Planet '{planet_key}': width must be a positive integer, got {width}"
                )
            if not isinstance(height, int) or height <= 0:
                errors.append(
                    f"Planet '{planet_key}': height must be a positive integer, got {height}"
                )

            # Validate terrain_weights sum > 0 (empty dict allowed for space-type planets)
            terrain_weights = entry.get("terrain_weights", {})
            if not isinstance(terrain_weights, dict):
                terrain_weights = {}
            if terrain_weights and sum(terrain_weights.values()) <= 0:
                errors.append(
                    f"Planet '{planet_key}': terrain_weights sum must be > 0 when weights are provided"
                )

            if errors:
                continue

            space = CoordinateSpaceDef(
                planet_key=planet_key,
                planet_type=entry.get("planet_type", ""),
                width=width,
                height=height,
                terrain_seed=entry.get("terrain_seed", 0),
                terrain_noise_cell_size=entry.get("terrain_noise_cell_size", 8),
                terrain_weights=terrain_weights,
                persistence_type=entry.get("persistence_type", "static"),
                spawn_x=entry.get("spawn_x", 0),
                spawn_y=entry.get("spawn_y", 0),
                default_planet=entry.get("default_planet", False),
                z_level=entry.get("z_level", i),
                seed_rotation_ticks=entry.get("seed_rotation_ticks", 0),
                rank_requirement=entry.get("rank_requirement", 1),
                yield_scale=float(entry.get("yield_scale", 1.0) or 1.0),
                npc_scale=float(entry.get("npc_scale", 1.0) or 1.0),
            )
            spaces[planet_key] = space

        if errors:
            msg = "Planet definition validation failed:\n" + "\n".join(errors)
            logger.error(msg)
            raise PlanetRegistryError(msg)

        self._spaces = spaces
        logger.info(
            "Planet Registry loaded %d planet(s) from '%s'", len(self._spaces), path
        )

    def get_space(self, planet_key: str) -> CoordinateSpaceDef:
        """Get a Coordinate_Space definition by planet key.

        Args:
            planet_key: The unique planet identifier.

        Returns:
            The CoordinateSpaceDef for the given planet.

        Raises:
            KeyError: If the planet key is not found.
        """
        return self._spaces[planet_key]

    def list_planets(self) -> list[str]:
        """Return a list of all registered planet keys."""
        return list(self._spaces.keys())

    def is_valid_coordinate(self, x: int, y: int, planet_key: str) -> bool:
        """Check whether (x, y) is within the bounds of the given planet.

        Args:
            x: X coordinate.
            y: Y coordinate.
            planet_key: The planet to check against.

        Returns:
            True if 0 <= x < width and 0 <= y < height for the planet.

        Raises:
            KeyError: If the planet key is not found.
        """
        space = self._spaces[planet_key]
        return 0 <= x < space.width and 0 <= y < space.height

    def resolve_planet(self, identifier: str) -> str | None:
        """Resolve a planet identifier to its canonical key.

        Accepts:
        - Exact key: "earth_planet"
        - Case-insensitive key: "Earth_Planet", "EARTH_PLANET"
        - Z-level number: "0", "1", "2"
        - Prefix match: "earth", "ind"

        Returns the canonical planet_key or None if not found.
        """
        # Try exact match first
        if identifier in self._spaces:
            return identifier

        # Try z-level (numeric)
        try:
            z = int(identifier)
            for key, space in self._spaces.items():
                if space.z_level == z:
                    return key
        except ValueError:
            pass

        # Case-insensitive match
        lower = identifier.lower()
        for key in self._spaces:
            if key.lower() == lower:
                return key

        # Prefix match (case-insensitive)
        for key in self._spaces:
            if key.lower().startswith(lower):
                return key

        return None
