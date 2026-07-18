"""
Shared Evennia channel mechanics.

The low-level channel operations that ChatSystem, AllianceSystem, and the ``chat``
command each re-implemented: look up the ChannelDB class (importing evennia
lazily so this module is safe to import anywhere, including ``world/systems``),
resolve a channel by its ``db_key``, connect/disconnect an account inside a DB
savepoint (so a failing channel query can't poison the surrounding transaction),
broadcast a system line, and ensure/delete a channel. Each helper is best-effort
and never raises into a caller — a channel problem must never break a mutation or
a command.

Callers keep their own key derivation (``Public`` / ``alliance_<id>``) and any
surrounding work (nick aliases, sender attribution); this module owns only the
ChannelDB mechanics.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("evennia")


def get_channel_db(override: Any = None) -> Any:
    """Return Evennia's ``ChannelDB`` class, or ``None`` outside a full env.

    *override* lets a caller inject a stand-in (the ChatSystem test seam); when
    given it is returned as-is.
    """
    if override is not None:
        return override
    try:
        from evennia.comms.models import ChannelDB
        return ChannelDB
    except Exception:  # noqa: BLE001 - no channel layer outside a full env
        return None


def find_channel(key: str, *, channel_db: Any = None) -> Any:
    """Return the channel whose ``db_key`` is *key*, or ``None`` (best-effort)."""
    channel_db = channel_db or get_channel_db()
    if channel_db is None:
        return None
    try:
        return channel_db.objects.filter(db_key=key).first()
    except Exception:  # noqa: BLE001
        return None


def subscribe_account(account: Any, key: str, *, channel_db: Any = None) -> None:
    """Connect *account* to channel *key* if not already, in a DB savepoint."""
    channel_db = channel_db or get_channel_db()
    if channel_db is None or account is None:
        return
    try:
        from django.db import transaction
        with transaction.atomic():
            channel = channel_db.objects.filter(db_key=key).first()
            if channel is not None and not channel.has_connection(account):
                channel.connect(account)
    except Exception:  # noqa: BLE001
        logger.debug("Channel subscribe failed for %s", key, exc_info=True)


def unsubscribe_account(account: Any, key: str, *, channel_db: Any = None) -> None:
    """Disconnect *account* from channel *key* if connected, in a savepoint."""
    channel_db = channel_db or get_channel_db()
    if channel_db is None or account is None:
        return
    try:
        from django.db import transaction
        with transaction.atomic():
            channel = channel_db.objects.filter(db_key=key).first()
            if channel is not None and channel.has_connection(account):
                channel.disconnect(account)
    except Exception:  # noqa: BLE001
        logger.debug("Channel unsubscribe failed for %s", key, exc_info=True)


def broadcast(key: str, message: str, *, channel_db: Any = None, senders: Any = None) -> None:
    """Send *message* to channel *key* (best-effort).

    *senders* is forwarded to ``channel.msg`` for player-attributed lines; omit
    it for anonymous system announcements.
    """
    channel = find_channel(key, channel_db=channel_db)
    if channel is None:
        return
    try:
        if senders is not None:
            channel.msg(message, senders=senders)
        else:
            channel.msg(message)
    except Exception:  # noqa: BLE001
        logger.debug("Channel broadcast failed for %s", key, exc_info=True)


def ensure_channel(key: str, *, desc: str = "", channel_db: Any = None) -> Any:
    """Return channel *key*, creating it if missing (best-effort), else ``None``."""
    channel_db = channel_db or get_channel_db()
    if channel_db is None:
        return None
    try:
        from django.db import transaction
        with transaction.atomic():
            existing = channel_db.objects.filter(db_key=key).first()
            if existing is not None:
                return existing
            from evennia import create_channel
            return create_channel(key, desc=desc)
    except Exception:  # noqa: BLE001
        logger.debug("Channel ensure failed for %s", key, exc_info=True)
        return None


def destroy_channel(key: str, *, channel_db: Any = None) -> None:
    """Delete channel *key* if it exists (best-effort), in a savepoint."""
    channel_db = channel_db or get_channel_db()
    if channel_db is None:
        return
    try:
        from django.db import transaction
        with transaction.atomic():
            channel = channel_db.objects.filter(db_key=key).first()
            if channel is not None:
                channel.delete()
    except Exception:  # noqa: BLE001
        logger.debug("Channel destroy failed for %s", key, exc_info=True)
