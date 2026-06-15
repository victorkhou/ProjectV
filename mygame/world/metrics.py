"""
Metrics Collector for the RTS Combat Overworld game.

Lightweight in-memory counters and gauges for server health monitoring.
Logs a summary at a configurable interval when enabled.

Requirements: 30.1, 30.2, 30.3, 30.4
"""

from __future__ import annotations

import logging
import time
from typing import Any


logger = logging.getLogger("evennia")

#: Names of the gauge metrics that ``set_gauge`` is allowed to write.
_GAUGE_METRICS = frozenset({"connected_players"})


class MetricsCollector:
    """Tracks server health metrics.

    Metrics tracked:
    - connected_players (gauge)
    - commands_processed (counter)
    - tick_duration_ms (per-tick timing)
    - combat_actions (counter)
    - buildings_constructed (counter)
    - errors (counter)

    Args:
        enabled: Whether metrics collection is active.
        log_interval: Seconds between summary logs (default 60).
    """

    def __init__(
        self, enabled: bool = False, log_interval: int = 60
    ) -> None:
        self.enabled = enabled
        self.log_interval = log_interval

        # Gauges
        self.connected_players: int = 0

        # Counters
        self.commands_processed: int = 0
        self.combat_actions: int = 0
        self.buildings_constructed: int = 0
        self.errors: int = 0

        # Per-tick
        self._tick_durations: list[float] = []
        self._last_summary_time: float = time.time()

    def record_tick(self, duration_ms: float) -> None:
        """Record the duration of a game tick.

        Args:
            duration_ms: Tick duration in milliseconds.
        """
        if not self.enabled:
            return
        self._tick_durations.append(duration_ms)
        self._maybe_log_summary()

    def increment(self, metric: str, amount: int = 1) -> None:
        """Increment a counter metric.

        Args:
            metric: Name of the metric to increment.
            amount: Amount to add (default 1).
        """
        if not self.enabled:
            return
        current = getattr(self, metric, None)
        if current is not None and isinstance(current, int):
            setattr(self, metric, current + amount)

    def set_gauge(self, metric: str, value: int) -> None:
        """Set a gauge metric to a specific value.

        Args:
            metric: Name of the gauge metric.
            value: The value to set.
        """
        if not self.enabled:
            return
        # Only allow declared gauge metrics, so a stray call can never
        # overwrite control attributes (enabled, log_interval, etc.).
        if metric in _GAUGE_METRICS:
            setattr(self, metric, value)

    def log_summary(self) -> None:
        """Log a summary of all metrics."""
        if not self._tick_durations:
            avg_tick = 0.0
            max_tick = 0.0
        else:
            avg_tick = sum(self._tick_durations) / len(self._tick_durations)
            max_tick = max(self._tick_durations)

        summary = (
            f"[Metrics] players={self.connected_players}, "
            f"commands={self.commands_processed}, "
            f"combat_actions={self.combat_actions}, "
            f"buildings={self.buildings_constructed}, "
            f"errors={self.errors}, "
            f"avg_tick_ms={avg_tick:.2f}, "
            f"max_tick_ms={max_tick:.2f}, "
            f"ticks={len(self._tick_durations)}"
        )
        try:
            logger.info(summary)
        finally:
            # Reset per-interval data regardless of logging outcome so the
            # per-interval buffer cannot grow without bound.
            self._tick_durations.clear()
            self._last_summary_time = time.time()

    def get_summary(self) -> dict:
        """Get a dict summary of all metrics.

        Returns:
            Dict with all metric values.
        """
        if not self._tick_durations:
            avg_tick = 0.0
            max_tick = 0.0
        else:
            avg_tick = sum(self._tick_durations) / len(self._tick_durations)
            max_tick = max(self._tick_durations)

        return {
            "connected_players": self.connected_players,
            "commands_processed": self.commands_processed,
            "combat_actions": self.combat_actions,
            "buildings_constructed": self.buildings_constructed,
            "errors": self.errors,
            "avg_tick_ms": avg_tick,
            "max_tick_ms": max_tick,
            "tick_count": len(self._tick_durations),
        }

    def _maybe_log_summary(self) -> None:
        """Log summary if enough time has elapsed since last summary."""
        now = time.time()
        if now - self._last_summary_time >= self.log_interval:
            self.log_summary()
