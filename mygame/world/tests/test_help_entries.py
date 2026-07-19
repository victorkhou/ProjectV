"""
Lightweight guard tests for player help content (PvE NPC bases, Phase 6).

Help topics are prose data, validated by HELP_STYLE.md — but a couple of
structural invariants are worth locking in so the 'outposts' topic (and its
combat cross-link) can't silently regress: it must exist under the 'Game'
category, be reachable from 'combat', and have balanced color tags.
"""

import re

from mygame.world.help_entries import HELP_ENTRY_DICTS


_BY_KEY = {e["key"]: e for e in HELP_ENTRY_DICTS}
# Opening color codes in this codebase's help; |n is the reset/close.
_OPEN = re.compile(r"\|[wcrRgyYBG]")
_CLOSE = re.compile(r"\|n")


def test_outposts_topic_exists_in_game_category():
    assert "outposts" in _BY_KEY
    assert _BY_KEY["outposts"]["category"] == "Game"


def test_outposts_topic_covers_key_concepts():
    text = _BY_KEY["outposts"]["text"].lower()
    for concept in ("fortress", "guard", "loot", "headquarters", "scan", "respawn"):
        assert concept in text, f"outposts topic missing '{concept}'"


def test_combat_topic_cross_links_outposts():
    assert "outposts" in _BY_KEY["combat"]["text"]


def test_combat_topic_covers_base_elimination_and_guards():
    text = _BY_KEY["combat"]["text"].lower()
    assert "guard" in text
    assert "eliminated" in text  # base-elimination reward line
    assert "inert" in text       # PvP deactivation


def test_outposts_reachable_from_front_door_topics():
    """Discoverability: the topic is cross-linked from tutorial + buildings."""
    assert "outposts" in _BY_KEY["tutorial"]["text"]
    assert "outposts" in _BY_KEY["buildings"]["text"]


def test_new_topic_color_tags_balanced():
    """Every color code opened is closed by a |n (HELP_STYLE §6)."""
    for key in ("outposts", "combat"):
        text = _BY_KEY[key]["text"]
        assert len(_OPEN.findall(text)) <= len(_CLOSE.findall(text)), (
            f"{key}: unbalanced color tags"
        )


def test_outposts_names_do_not_collide_with_other_topics():
    """The new 'outposts' topic's key + aliases must not clash with any OTHER
    topic's key or aliases (a clash would let one shadow the other)."""
    outposts = _BY_KEY["outposts"]
    new_names = {outposts["key"], *outposts.get("aliases", [])}
    other_names = set()
    for entry in HELP_ENTRY_DICTS:
        if entry["key"] == "outposts":
            continue
        other_names.add(entry["key"])
        other_names.update(entry.get("aliases", []))
    clashes = new_names & other_names
    assert not clashes, f"outposts topic names collide with other topics: {clashes}"


# ------------------------------------------------------------------ #
#  Progression topics (early-game rebalance) — level/rank + directives
# ------------------------------------------------------------------ #

def test_level_and_directives_topics_exist_in_game_category():
    for key in ("level", "directives"):
        assert key in _BY_KEY, f"missing help topic '{key}'"
        assert _BY_KEY[key]["category"] == "Game"


def test_level_topic_covers_progression_concepts():
    """The level/rank topic must explain the one-bar model a new player needs:
    XP from both economy and combat, the 1-100 range, and rank bands."""
    text = _BY_KEY["level"]["text"].lower()
    for concept in ("level", "rank", "xp", "recruit", "marshal", "100"):
        assert concept in text, f"level topic missing '{concept}'"
    # Rank is a high-water mark (never demotes) — the key surprising rule.
    assert "never fall" in text or "high-water" in text or "sticks" in text


def test_level_topic_rank_bands_match_constants():
    """Every rank display name must appear in the level topic so the ladder
    can't silently drift from the rank set."""
    text = _BY_KEY["level"]["text"]
    # The 12 rank display names (underscores rendered as spaces in prose).
    for name in ("Recruit", "Private", "Corporal", "Sergeant",
                 "Staff Sergeant", "Lieutenant", "Captain", "Major",
                 "Colonel", "Brigadier", "General", "Marshal"):
        assert name in text, f"level topic missing rank '{name}'"


def test_directives_topic_covers_on_off():
    text = _BY_KEY["directives"]["text"].lower()
    for concept in ("directives", "objective", "off", "on", "reward"):
        assert concept in text, f"directives topic missing '{concept}'"


def test_progression_topics_reachable_from_front_door():
    """Discoverability: level + directives are cross-linked from tutorial."""
    assert "level" in _BY_KEY["tutorial"]["text"]
    assert "directives" in _BY_KEY["tutorial"]["text"]


def test_progression_topic_color_tags_balanced():
    for key in ("level", "directives"):
        text = _BY_KEY[key]["text"]
        assert len(_OPEN.findall(text)) <= len(_CLOSE.findall(text)), (
            f"{key}: unbalanced color tags"
        )


def test_progression_topic_names_do_not_collide():
    """level + directives keys/aliases must not clash with any OTHER topic."""
    for this_key in ("level", "directives"):
        entry = _BY_KEY[this_key]
        my_names = {entry["key"], *entry.get("aliases", [])}
        other_names = set()
        for other in HELP_ENTRY_DICTS:
            if other["key"] == this_key:
                continue
            other_names.add(other["key"])
            other_names.update(other.get("aliases", []))
        clashes = my_names & other_names
        assert not clashes, f"{this_key} topic names collide: {clashes}"
