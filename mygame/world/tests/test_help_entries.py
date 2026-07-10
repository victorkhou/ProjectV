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
