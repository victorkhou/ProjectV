"""
Deterministic terrain generator using hash-based value noise.

Computes terrain type for any (x, y) coordinate given a planet's
CoordinateSpaceDef.  Uses only Python stdlib — no scipy or numpy.

For dynamic planets with seed_rotation_ticks > 0, the effective seed
changes each epoch, causing the terrain to reshuffle periodically.
"""

from __future__ import annotations

import bisect

from world.definitions import CoordinateSpaceDef
from world.services import get_registry

# Large prime used to map Python's hash output into [0, 1).
_LARGE_PRIME = 2_147_483_647  # 2^31 - 1 (Mersenne prime)

#: Noise-equalization tuning. The bilinear blend of four uniform corner hashes
#: is bell-shaped (clustered near 0.5), so bucketing it directly into the
#: cumulative weight bands starves the first/last band and inflates the middle
#: ones. We correct for this by mapping each raw noise value through the
#: empirical CDF of the noise field (its own percentile rank), which restores a
#: ~uniform distribution so realized terrain frequencies track the configured
#: weights. _EQUALIZE_SAMPLE is the side length of the deterministic sample grid
#: used to estimate that CDF; _EQUALIZE_QUANTILES is the compact breakpoint
#: count kept for a cheap per-lookup bisect.
_EQUALIZE_SAMPLE = 64
_EQUALIZE_QUANTILES = 255


class TerrainGenerator:
    """Deterministic terrain generator using hash-based value noise.

    For static planets, the seed never changes. For dynamic planets
    with seed_rotation_ticks > 0, call ``advance_tick(tick)`` to
    update the epoch — the terrain reshuffles when the epoch changes.
    """

    def __init__(self, space_def: CoordinateSpaceDef, data_registry=None) -> None:
        self._base_seed: int = space_def.terrain_seed
        self._seed: int = space_def.terrain_seed
        self._cell_size: int = max(space_def.terrain_noise_cell_size, 1)
        self._rotation_ticks: int = space_def.seed_rotation_ticks
        self._current_epoch: int = 0
        #: Map height, used to normalize a tile's y into a latitude signal for
        #: latitude-biased terrain. 1 when unknown (bias then inert).
        self._height: int = max(int(getattr(space_def, "height", 0) or 0), 1)

        # Base (latitude-neutral) weight list, kept as parallel arrays so the
        # per-tile latitude adjustment can rescale weights cheaply.
        weights = space_def.terrain_weights
        total = sum(weights.values()) if weights else 0.0
        self._names: list[str] = []
        self._weights: list[float] = []
        if total > 0:
            for terrain_type, weight in weights.items():
                self._names.append(terrain_type)
                self._weights.append(weight / total)

        # Pre-compute the latitude-neutral cumulative thresholds. Used directly
        # when no terrain on this planet declares a latitude_bias (the common
        # case), avoiding any per-tile weight recomputation.
        self._terrain_thresholds: list[tuple[float, str]] = []
        cumulative = 0.0
        for name, w in zip(self._names, self._weights):
            cumulative += w
            self._terrain_thresholds.append((cumulative, name))

        # Equalization breakpoints for the current seed (see module notes).
        self._equalize_quantiles: list[float] = []
        self._rebuild_equalization()

        # Build a terrain-type -> resource-type mapping AND a latitude-bias map
        # from terrain definitions loaded via the DataRegistry.
        self._resource_map: dict[str, str | None] = {}
        self._latitude_bias: list[float] = [0.0] * len(self._names)
        self._latitude_min: list[float] = [0.0] * len(self._names)
        reg = data_registry
        if reg is None:
            # Fall back to the installed registry (available after game init)
            reg = get_registry()
        if reg is not None:
            for i, terrain_type in enumerate(self._names):
                try:
                    terrain_def = reg.get_terrain(terrain_type)
                    self._resource_map[terrain_type] = terrain_def.resource_type
                    self._latitude_bias[i] = float(
                        getattr(terrain_def, "latitude_bias", 0.0) or 0.0
                    )
                    self._latitude_min[i] = float(
                        getattr(terrain_def, "latitude_min", 0.0) or 0.0
                    )
                except (KeyError, AttributeError):
                    self._resource_map[terrain_type] = None
        #: True only when some terrain declares a latitude rule — gates the
        #: per-tile latitude path so unbiased planets keep the fast path.
        self._has_latitude_rule: bool = (
            any(self._latitude_bias) or any(self._latitude_min)
        )

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
        if self._has_latitude_rule:
            return self._terrain_from_noise_at_latitude(noise_val, y)
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
            # The noise field changed with the seed, so its CDF did too —
            # rebuild the equalization breakpoints for the new epoch.
            self._rebuild_equalization()
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

    def _rebuild_equalization(self) -> None:
        """Recompute the noise-CDF quantile breakpoints for the current seed.

        Samples the raw bilinear noise over a deterministic
        ``_EQUALIZE_SAMPLE``-square grid, then keeps ``_EQUALIZE_QUANTILES``
        evenly-spaced quantile boundary values. ``_equalize`` bisects a raw
        value against these to get its percentile rank, flattening the
        bell-shaped noise back toward uniform so realized terrain frequencies
        match the configured weights. Deterministic (seed-derived), so it never
        breaks reproducibility.
        """
        if not self._terrain_thresholds:
            self._equalize_quantiles = []
            return
        n = _EQUALIZE_SAMPLE
        samples = sorted(
            self._noise(x, y) for y in range(n) for x in range(n)
        )
        q = _EQUALIZE_QUANTILES
        m = len(samples)
        self._equalize_quantiles = [
            samples[min(m - 1, (i * m) // (q + 1))] for i in range(1, q + 1)
        ]

    def _equalize(self, noise_value: float) -> float:
        """Map a raw noise value to its percentile rank in [0, 1).

        Returns the value unchanged when no quantile table is built (e.g. a
        planet with no terrain weights).
        """
        q = self._equalize_quantiles
        if not q:
            return noise_value
        return bisect.bisect_right(q, noise_value) / (len(q) + 1)

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
        weight thresholds.

        The raw value is first equalized (mapped through the noise field's own
        CDF) so its distribution is ~uniform; without this the bell-shaped
        noise would make realized terrain frequencies diverge sharply from the
        configured weights.
        """
        # Equalize, then clamp to [0, 1) for safety.
        nv = self._equalize(noise_value)
        nv = max(0.0, min(nv, 0.9999999))
        for threshold, terrain_type in self._terrain_thresholds:
            if nv < threshold:
                return terrain_type
        # Fallback: return the last terrain type (handles floating-point edge)
        return self._terrain_thresholds[-1][1]

    def _terrain_from_noise_at_latitude(self, noise_value: float, y: int) -> str:
        """Bucket an equalized noise value using latitude-adjusted weights.

        The tile's latitude signal is ``lat = |2*y/H - 1|`` — 0 at the equator
        (vertical middle) and 1 at the poles (top/bottom edges). Each terrain's
        base weight is rescaled by:

          * a hard cutoff: weight → 0 when ``lat < latitude_min`` (e.g. Snow
            with latitude_min 0.3 never spawns in the central 30% of rows); and
          * a soft bias: weight *= max(0, 1 + latitude_bias * signal), where
            ``signal = 2*lat - 1`` (+1 pole, -1 equator), so a positive bias
            concentrates the terrain poleward and a negative one equatorward.

        The surviving weights are renormalized for this row, so a terrain zeroed
        by its cutoff has its share redistributed to the others at that
        latitude. The value is equalized first (same as the flat path) so the
        global-histogram correction still holds before the latitude reshaping.
        """
        nv = self._equalize(noise_value)
        nv = max(0.0, min(nv, 0.9999999))

        lat = abs(2.0 * y / self._height - 1.0)  # 0 equator … 1 pole
        signal = 2.0 * lat - 1.0                  # -1 equator … +1 pole

        adjusted = []
        total = 0.0
        for i, base_w in enumerate(self._weights):
            if lat < self._latitude_min[i]:
                w = 0.0
            else:
                w = base_w * max(0.0, 1.0 + self._latitude_bias[i] * signal)
            adjusted.append(w)
            total += w

        if total <= 0.0:
            # Degenerate row (everything cut off) — fall back to flat weights.
            return self._terrain_from_noise(noise_value)

        cumulative = 0.0
        for i, w in enumerate(adjusted):
            cumulative += w / total
            if nv < cumulative:
                return self._names[i]
        return self._names[-1]

    # ------------------------------------------------------------------ #
    #  Test helpers
    # ------------------------------------------------------------------ #

    def _set_resource_map(self, resource_map: dict[str, str | None]) -> None:
        """Inject a terrain-to-resource mapping (for testing without
        DataRegistry)."""
        self._resource_map = dict(resource_map)
