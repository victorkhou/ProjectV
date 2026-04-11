"""
Deterministic terrain generator using hash-based value noise.

Computes terrain type for any (x, y) coordinate given a planet's
CoordinateSpaceDef.  Uses only Python stdlib — no scipy or numpy.

For dynamic planets with seed_rotation_ticks > 0, the effective seed
changes each epoch, causing the terrain to reshuffle periodically.
"""

from __future__ import annotations

from world.definitions import CoordinateSpaceDef

# Large prime used to map Python's hash output into [0, 1).
_LARGE_PRIME = 2_147_483_647  # 2^31 - 1 (Mersenne prime)


class TerrainGenerator:
    """Deterministic terrain generator using hash-based value noise.

    For static planets, the seed never changes. For dynamic planets
    with seed_rotation_ticks > 0, call ``advance_tick(tick)`` to
    update the epoch — the terrain reshuffles when the epoch changes.
    """

    def __init__(self, space_def: CoordinateSpaceDef) -> None:
        self._base_seed: int = space_def.terrain_seed
        self._seed: int = space_def.terrain_seed
        self._cell_size: int = max(space_def.terrain_noise_cell_size, 1)
        self._rotation_ticks: int = space_def.seed_rotation_ticks
        self._current_epoch: int = 0

        # Pre-compute cumulative weight thresholds for terrain selection.
        # terrain_weights maps terrain_type -> relative weight.
        weights = space_def.terrain_weights
        total = sum(weights.values()) if weights else 0.0

        self._terrain_thresholds: list[tuple[float, str]] = []
        if total > 0:
            cumulative = 0.0
            for terrain_type, weight in weights.items():
                cumulative += weight / total
                self._terrain_thresholds.append((cumulative, terrain_type))

        # Build a terrain-type -> resource-type mapping from terrain.yaml
        # definitions loaded via the DataRegistry.
        self._resource_map: dict[str, str | None] = {}
        try:
            from world.data_registry import registry

            for _, terrain_type in self._terrain_thresholds:
                try:
                    terrain_def = registry.get_terrain(terrain_type)
                    self._resource_map[terrain_type] = terrain_def.resource_type
                except (KeyError, AttributeError):
                    self._resource_map[terrain_type] = None
        except ImportError:
            # DataRegistry not available (e.g. in unit tests).
            # Caller can inject resource map via _set_resource_map.
            pass

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def get_terrain(self, x: int, y: int) -> str:
        """Return the terrain type string for coordinate (x, y).

        Deterministic: same (x, y) + same seed = same result, always.
        Returns the first terrain type if no thresholds are configured.
        """
        if not self._terrain_thresholds:
            return "unknown"
        noise_val = self._noise(x, y)
        return self._terrain_from_noise(noise_val)

    def get_terrain_and_resource(
        self, x: int, y: int
    ) -> tuple[str, str | None]:
        """Return (terrain_type, resource_type) for coordinate (x, y).

        resource_type is derived from the planet's terrain-to-resource
        mapping.  Returns None for resource_type if the terrain has no
        associated resource.
        """
        terrain = self.get_terrain(x, y)
        resource = self._resource_map.get(terrain)
        return terrain, resource

    def advance_tick(self, current_tick: int) -> bool:
        """Update the epoch based on the current game tick.

        For dynamic planets with seed_rotation_ticks > 0, the epoch
        is ``current_tick // seed_rotation_ticks``. When the epoch
        changes, the effective seed is updated and the terrain
        reshuffles.

        Returns True if the epoch changed (terrain reshuffled).
        """
        if self._rotation_ticks <= 0:
            return False

        new_epoch = current_tick // self._rotation_ticks
        if new_epoch != self._current_epoch:
            self._current_epoch = new_epoch
            self._seed = self._base_seed + new_epoch
            return True
        return False

    @property
    def epoch(self) -> int:
        """Current terrain epoch. Changes trigger a reshuffle."""
        return self._current_epoch

    @property
    def is_dynamic(self) -> bool:
        """True if this generator rotates its seed."""
        return self._rotation_ticks > 0

    # ------------------------------------------------------------------ #
    #  Internal helpers
    # ------------------------------------------------------------------ #

    def _noise(self, x: int, y: int) -> float:
        """Hash-based value noise returning a float in [0, 1).

        Algorithm:
        1. Determine the coarse-grid cell that (x, y) falls into.
        2. Compute hash values at the four corners of that cell.
        3. Bilinearly interpolate between the four corner values using
           the fractional position of (x, y) within the cell.
        """
        cs = self._cell_size

        # Coarse-grid cell coordinates
        gx = x // cs
        gy = y // cs

        # Fractional position within the cell [0, 1)
        fx = (x % cs) / cs
        fy = (y % cs) / cs

        # Hash values at the four corners
        v00 = self._hash_corner(gx, gy)
        v10 = self._hash_corner(gx + 1, gy)
        v01 = self._hash_corner(gx, gy + 1)
        v11 = self._hash_corner(gx + 1, gy + 1)

        # Bilinear interpolation
        top = v00 + (v10 - v00) * fx
        bottom = v01 + (v11 - v01) * fx
        return top + (bottom - top) * fy

    def _hash_corner(self, cx: int, cy: int) -> float:
        """Deterministic hash of a coarse-grid corner -> float in [0, 1)."""
        return (hash((cx, cy, self._seed)) % _LARGE_PRIME) / _LARGE_PRIME

    def _terrain_from_noise(self, noise_value: float) -> str:
        """Map a noise value in [0, 1) to a terrain type using cumulative
        weight thresholds."""
        # Clamp to [0, 1) for safety
        nv = max(0.0, min(noise_value, 0.9999999))
        for threshold, terrain_type in self._terrain_thresholds:
            if nv < threshold:
                return terrain_type
        # Fallback: return the last terrain type (handles floating-point edge)
        return self._terrain_thresholds[-1][1]

    # ------------------------------------------------------------------ #
    #  Test helpers
    # ------------------------------------------------------------------ #

    def _set_resource_map(self, resource_map: dict[str, str | None]) -> None:
        """Inject a terrain-to-resource mapping (for testing without
        DataRegistry)."""
        self._resource_map = dict(resource_map)
