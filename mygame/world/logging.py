"""
Structured Logger for the RTS Combat Overworld game.

Wraps Python's logging module with structured context fields for
game events. Supports both human-readable and JSON output formats.

Requirements: 29.1, 29.2, 29.3
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any


class GameLogger:
    """Structured logging for game events.

    Each log entry includes: timestamp, log level, logger name,
    event type, and context key-value pairs.

    Args:
        name: Logger name (default "game").
        json_format: If True, output JSON format; otherwise human-readable.
    """

    def __init__(self, name: str = "game", json_format: bool = False) -> None:
        self._logger = logging.getLogger(name)
        self._json_format = json_format

    def log_event(
        self,
        event_type: str,
        level: int = logging.INFO,
        **context: Any,
    ) -> None:
        """Log a structured game event.

        Args:
            event_type: The type of event (e.g., "player_login",
                "combat_action", "building_constructed").
            level: Logging level (default INFO).
            **context: Arbitrary key-value context fields.
        """
        entry = {
            "timestamp": time.time(),
            "event_type": event_type,
            **context,
        }

        if self._json_format:
            message = json.dumps(entry, default=str)
        else:
            ctx_parts = [f"{k}={v}" for k, v in context.items()]
            ctx_str = ", ".join(ctx_parts) if ctx_parts else ""
            message = f"[{event_type}] {ctx_str}" if ctx_str else f"[{event_type}]"

        self._logger.log(level, message)

    def info(self, event_type: str, **context: Any) -> None:
        """Log an INFO-level game event."""
        self.log_event(event_type, level=logging.INFO, **context)

    def warning(self, event_type: str, **context: Any) -> None:
        """Log a WARNING-level game event."""
        self.log_event(event_type, level=logging.WARNING, **context)

    def error(self, event_type: str, **context: Any) -> None:
        """Log an ERROR-level game event."""
        self.log_event(event_type, level=logging.ERROR, **context)

    def debug(self, event_type: str, **context: Any) -> None:
        """Log a DEBUG-level game event."""
        self.log_event(event_type, level=logging.DEBUG, **context)


# Module-level singleton
game_logger = GameLogger()
