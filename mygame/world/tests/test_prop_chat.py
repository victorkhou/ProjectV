"""
Property-based tests for Chat System message delivery scope.

Property 24: Chat message delivery scope

Validates: Requirements 13.3, 13.4, 13.5, 13.6, 13.8
"""

import sys
import types
import unittest

from hypothesis import given, settings
from hypothesis import strategies as st

# -------------------------------------------------------------- #
#  Bootstrap: stub out Evennia modules
# -------------------------------------------------------------- #

def _ensure_evennia_stubs():
    """Insert lightweight stubs for Evennia modules into sys.modules."""
    if "evennia" in sys.modules:
        mod = sys.modules["evennia"]
        if hasattr(mod, "__file__") and mod.__file__:
            return
    stubs = {}

    def _mod(name, attrs=None):
        m = types.ModuleType(name)
        if attrs:
            for k, v in attrs.items():
                setattr(m, k, v)
        stubs[name] = m
        return m

    _mod("evennia")
    _mod("evennia.objects")
    _mod("evennia.objects.objects", {
        "DefaultObject": type("DefaultObject", (), {}),
        "DefaultRoom": type("DefaultRoom", (), {}),
        "DefaultCharacter": type("DefaultCharacter", (), {}),
    })
    _mod("evennia.commands")
    _mod("evennia.commands.cmdset")
    _mod("evennia.utils")
    _mod("evennia.utils.utils")
    _mod("evennia.utils.logger")

    for name, mod in stubs.items():
        sys.modules.setdefault(name, mod)

_ensure_evennia_stubs()

from mygame.world.chat_system import ChatSystem  # noqa: E402

# -------------------------------------------------------------- #
#  Helpers / Fakes
# -------------------------------------------------------------- #

RANK_NAMES = [
    "Recruit", "Private", "Corporal", "Sergeant",
    "Captain", "Major", "Colonel", "General",
]

class FakePlayer:
    """Lightweight stand-in for a player with rank info."""

    def __init__(self, name="TestPlayer", rank_name="Recruit"):
        self.key = name
        self.rank_name = rank_name

# -------------------------------------------------------------- #
#  Hypothesis strategies
# -------------------------------------------------------------- #

@st.composite
def player_strategy(draw):
    """Generate a player with a random name and rank."""
    name = draw(st.text(
        alphabet=st.characters(whitelist_categories=("L",)),
        min_size=1,
        max_size=15,
    ))
    rank = draw(st.sampled_from(RANK_NAMES))
    return FakePlayer(name=name, rank_name=rank)

@st.composite
def message_strategy(draw):
    """Generate a chat message string."""
    return draw(st.text(
        alphabet=st.characters(
            whitelist_categories=("L", "N", "P", "Z"),
        ),
        min_size=1,
        max_size=100,
    ))

# -------------------------------------------------------------- #
#  Property 24: Chat message delivery scope
#  **Validates: Requirements 13.3, 13.4, 13.5, 13.6, 13.8**
# -------------------------------------------------------------- #

class TestProperty24ChatMessageDeliveryScope(unittest.TestCase):
    """Property 24: Chat message delivery scope.

    For any global chat message, the formatted output SHALL include
    the sender's name and rank. For any direct message, the formatted
    output SHALL include the sender's rank. The rank formatting SHALL
    correctly prepend the sender's current rank.

    **Validates: Requirements 13.3, 13.4, 13.5, 13.6, 13.8**
    """

    @given(
        player=player_strategy(),
        message=message_strategy(),
    )
    @settings(max_examples=100)
    def test_channel_message_includes_rank_and_name(self, player, message):
        """Global channel messages include sender rank and name."""
        chat = ChatSystem()
        formatted = chat.format_channel_message(player, message)

        self.assertIn(
            player.rank_name, formatted,
            f"Channel message should include rank '{player.rank_name}'",
        )
        self.assertIn(
            player.key, formatted,
            f"Channel message should include name '{player.key}'",
        )
        self.assertIn(
            message, formatted,
            "Channel message should include the original message",
        )

    @given(
        player=player_strategy(),
        message=message_strategy(),
    )
    @settings(max_examples=100)
    def test_channel_message_format(self, player, message):
        """Global channel message follows '[rank] name: message' format."""
        chat = ChatSystem()
        formatted = chat.format_channel_message(player, message)

        expected = f"[{player.rank_name}] {player.key}: {message}"
        self.assertEqual(
            formatted, expected,
            f"Expected '{expected}', got '{formatted}'",
        )

    @given(
        player=player_strategy(),
        message=message_strategy(),
    )
    @settings(max_examples=100)
    def test_dm_message_includes_rank(self, player, message):
        """Direct messages include sender rank."""
        chat = ChatSystem()
        formatted = chat.format_dm_message(player, message)

        self.assertIn(
            player.rank_name, formatted,
            f"DM should include rank '{player.rank_name}'",
        )
        self.assertIn(
            player.key, formatted,
            f"DM should include name '{player.key}'",
        )
        self.assertIn(
            message, formatted,
            "DM should include the original message",
        )

    @given(
        player=player_strategy(),
        message=message_strategy(),
    )
    @settings(max_examples=100)
    def test_dm_message_format(self, player, message):
        """DM follows '[rank] name (DM): message' format."""
        chat = ChatSystem()
        formatted = chat.format_dm_message(player, message)

        expected = f"[{player.rank_name}] {player.key} (DM): {message}"
        self.assertEqual(
            formatted, expected,
            f"Expected '{expected}', got '{formatted}'",
        )

    @given(
        player1=player_strategy(),
        player2=player_strategy(),
        message=message_strategy(),
    )
    @settings(max_examples=100)
    def test_different_senders_produce_different_formats(self, player1, player2, message):
        """Different senders produce messages with their own rank/name."""
        chat = ChatSystem()
        msg1 = chat.format_channel_message(player1, message)
        msg2 = chat.format_channel_message(player2, message)

        if player1.key != player2.key or player1.rank_name != player2.rank_name:
            self.assertNotEqual(
                msg1, msg2,
                "Different senders should produce different formatted messages",
            )

if __name__ == "__main__":
    unittest.main()
