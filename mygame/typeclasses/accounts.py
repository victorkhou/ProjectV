"""
Account and Guest typeclasses.

An Account is the per-login OOC entity: it chats on channels and puppets
Characters, but has no presence in the game world itself. Guests are
throwaway accounts, disabled unless ``GUEST_ENABLED = True`` is set.
"""

from evennia.accounts.accounts import DefaultAccount, DefaultGuest


class Account(DefaultAccount):
    """The OOC player entity that puppets characters.

    Customizes three things over ``DefaultAccount``; see the Evennia autodocs
    for the inherited API:

    * :meth:`at_post_login` — auto-subscribes to game channels. Channel
      membership is account-level, so it belongs here rather than on the
      character puppet hook.
    * :meth:`at_pre_channel_msg` — prefixes channel lines with the speaker's
      rank (``[Rank] Name: message``).
    * :meth:`channel_msg` — tags outgoing channel text with the ``game-chat``
      CSS class for webclient routing.
    """

    def at_post_login(self, session=None, **kwargs):
        """Auto-subscribe the account to game channels on login.

        Channel membership is an account-level concern, so it lives here rather
        than on the character puppet hook. (Doing account/channel writes from the
        character's ``at_post_puppet`` corrupted EvenniaTest's per-test DB
        rollback.) Guarded so a subscribe hiccup never blocks login.
        """
        super().at_post_login(session=session, **kwargs)
        try:
            from world.utils import get_system
            chat_system = get_system(self, "chat_system")
            if chat_system:
                chat_system.auto_subscribe(self)
        except Exception:
            pass

    def at_pre_channel_msg(self, message, channel, senders=None, **kwargs):
        """Format channel messages with player rank.

        Replaces Evennia's default "SenderName: message" with
        "[Rank] SenderName: message".
        """
        from world.utils import _get_rank_name

        if senders:
            sender = senders[0]
            # Get the puppet (character) for rank info
            puppet = sender.get_puppet(self.sessions.get()[0]) if self.sessions.get() else None
            rank = _get_rank_name(puppet) if puppet else _get_rank_name(sender)
            sender_name = sender.get_display_name(self)

            message_lstrip = message.lstrip()
            if message_lstrip.startswith((":", ";")):
                spacing = "" if message_lstrip[1:].startswith((":", "'", ",")) else " "
                message = f"[{rank}] {sender_name}{spacing}{message_lstrip[1:]}"
            else:
                message = f"[{rank}] {sender_name}: {message}"

        if not kwargs.get("no_prefix") and not kwargs.get("emit"):
            message = channel.channel_prefix() + message

        return message

    def channel_msg(self, message, channel, senders=None, **kwargs):
        """Override to tag channel messages with 'game-chat' for the webclient."""
        self.msg(
            text=(message, {"from_channel": channel.id, "cls": "game-chat"}),
            from_obj=senders,
            options={"from_channel": channel.id},
        )


class Guest(DefaultGuest):
    """
    This class is used for guest logins. Unlike Accounts, Guests and their
    characters are deleted after disconnection.
    """

    pass
