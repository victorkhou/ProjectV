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
_OPEN = re.compile(r"\|[wcrRgyYBGm]")
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


def test_shield_generator_topic_exists_and_is_discoverable():
    """The Shield Generator building topic exists under Buildings, covers its
    key mechanics, and is reachable from the buildings overview."""
    assert "shield" in _BY_KEY
    entry = _BY_KEY["shield"]
    assert entry["category"] == "Buildings"
    text = entry["text"].lower()
    for concept in ("shield", "radius", "regenerate", "4 per planet", "level"):
        assert concept in text, f"shield topic missing '{concept}'"
    # Overview + combat cross-link it.
    assert "shield" in _BY_KEY["buildings"]["text"].lower()
    assert "shield" in _BY_KEY["combat"]["text"].lower()
    # Color tags balanced.
    assert len(_OPEN.findall(entry["text"])) <= len(_CLOSE.findall(entry["text"]))


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


# ------------------------------------------------------------------ #
#  Item-loot-economy topics — loot/rarity/affixes/poison/salvage/tech
#  + the Blacksmith/Refinery/Sniper Nest/Watchtower/Field Hospital
# ------------------------------------------------------------------ #

_LOOT_GAME_TOPICS = ("loot", "rarity", "affixes", "poison",
                     "salvage currency", "technologies")
_LOOT_BUILDING_TOPICS = ("blacksmith", "refinery", "sniper nest",
                         "watchtower", "field hospital")


def test_loot_economy_topics_exist_in_expected_categories():
    for key in _LOOT_GAME_TOPICS:
        assert key in _BY_KEY, f"missing help topic '{key}'"
        assert _BY_KEY[key]["category"] == "Game"
    for key in _LOOT_BUILDING_TOPICS:
        assert key in _BY_KEY, f"missing help topic '{key}'"
        assert _BY_KEY[key]["category"] == "Buildings"


def test_loot_topic_covers_key_concepts():
    """The loot topic must teach the tag, the inspect flow, and loseability."""
    text = _BY_KEY["loot"]["text"].lower()
    for concept in ("roll", "quality", "73%", "look", "rarity", "pvp"):
        assert concept in text, f"loot topic missing '{concept}'"


def test_rarity_topic_lists_all_five_tiers():
    text = _BY_KEY["rarity"]["text"]
    for tier in ("Common", "Uncommon", "Rare", "Epic", "Legendary"):
        assert tier in text, f"rarity topic missing tier '{tier}'"
    # Crafted cap + affix budgets are the two rules players trip over.
    lowered = text.lower()
    assert "craft" in lowered
    assert "affix" in lowered


def test_affixes_topic_states_loot_only_rule():
    text = _BY_KEY["affixes"]["text"].lower()
    assert "loot only" in text or "loot-only" in text
    assert "crafted" in text
    assert "of the viper" in text  # the signature proc affix


def test_poison_topic_covers_sources_and_counters():
    text = _BY_KEY["poison"]["text"].lower()
    for concept in ("venom coating", "viper", "resist", "medkit",
                    "toxicology"):
        assert concept in text, f"poison topic missing '{concept}'"


def test_salvage_currency_topic_covers_earn_and_spend():
    text = _BY_KEY["salvage currency"]["text"].lower()
    for concept in ("salvage", "refine", "reroll", "score"):
        assert concept in text, f"salvage currency topic missing '{concept}'"


def test_technologies_topic_lists_new_research():
    text = _BY_KEY["technologies"]["text"]
    for tech in ("Reactive Plating", "Salvage Protocols",
                 "Efficient Construction", "Toxicology",
                 "Ballistics Optimization", "Master Gunsmithing"):
        assert tech in text, f"technologies topic missing '{tech}'"


def test_blacksmith_topic_covers_bench_commands_and_scaling():
    text = _BY_KEY["blacksmith"]["text"].lower()
    for concept in ("insert", "reroll", "salvage", "level", "deed"):
        assert concept in text, f"blacksmith topic missing '{concept}'"


def test_refinery_topic_states_salvage_only_output():
    text = _BY_KEY["refinery"]["text"].lower()
    assert "refine" in text
    assert "salvage only" in text
    assert "never" in text  # never outputs resources (anti-loop)


def test_positional_building_topics_state_owner_on_tile_rule():
    """The aura trio's defining rule: owner-only, on-tile-only."""
    for key in ("sniper nest", "watchtower", "field hospital"):
        text = _BY_KEY[key]["text"].lower()
        assert "owner" in text, f"{key} topic missing the owner-only rule"
        assert "stand" in text, f"{key} topic missing the on-tile rule"


def test_loot_topics_reachable_from_front_door_topics():
    """Discoverability: the new systems are cross-linked from the topics a
    player already reads (equipment, craft, combat, buildings, commands)."""
    assert "help loot" in _BY_KEY["equipment"]["text"]
    assert "help loot" in _BY_KEY["craft"]["text"]
    assert "help poison" in _BY_KEY["combat"]["text"]
    buildings_text = _BY_KEY["buildings"]["text"].lower()
    for name in ("blacksmith", "refinery", "sniper nest", "watchtower",
                 "field hospital"):
        assert name in buildings_text, f"buildings overview missing '{name}'"
    commands_text = _BY_KEY["commands"]["text"]
    for cmd in ("insert", "reroll", "salvage", "refine"):
        assert f"|w{cmd}" in commands_text, f"commands topic missing '{cmd}'"


def test_loot_economy_topic_color_tags_balanced():
    for key in (*_LOOT_GAME_TOPICS, *_LOOT_BUILDING_TOPICS):
        text = _BY_KEY[key]["text"]
        assert len(_OPEN.findall(text)) <= len(_CLOSE.findall(text)), (
            f"{key}: unbalanced color tags"
        )


def test_loot_economy_topic_names_do_not_collide():
    """New keys/aliases must not clash with any OTHER topic's key or aliases
    (a clash would let one shadow the other)."""
    for this_key in (*_LOOT_GAME_TOPICS, *_LOOT_BUILDING_TOPICS):
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


# ------------------------------------------------------------------ #
#  Admin — the unified @<entity> CRUD grammar umbrella topic
#  (unified-admin-crud task 9.1 / Requirement 11.3)
# ------------------------------------------------------------------ #

def test_admin_topic_exists_and_is_staff_gated():
    """The umbrella 'admin' topic exists, sits in the Admin category, and is
    lock-gated so ordinary players never see it in their help index."""
    assert "admin" in _BY_KEY
    entry = _BY_KEY["admin"]
    assert entry["category"] == "Admin"
    # Staff-only: readable at Builder+ (the admin-command floor), like the
    # dev 'evennia' topic is Developer-gated.
    assert "perm(Builder)" in entry.get("locks", "")


def test_admin_topic_names_the_full_core_verb_grammar():
    """The topic must teach all ten core verbs and the def scope — the
    single grammar every @<entity> command shares (Requirement 11.3)."""
    text = _BY_KEY["admin"]["text"]
    lowered = text.lower()
    for verb in ("list", "spawn", "show", "set", "destroy",
                 "def list", "def show", "def set", "def reset", "def diff"):
        assert verb in lowered, f"admin topic missing core verb '{verb}'"
    # The #N / key / name / prefix target grammar and player scoping.
    assert "#N" in text
    for concept in ("key", "name", "prefix", "player"):
        assert concept in lowered, f"admin topic missing target concept '{concept}'"


def test_admin_topic_documents_the_full_legacy_alias_matrix():
    """Every installed migration alias is paired with its canonical verb so
    the legacy-spellings section can't drift from the adapters (R11.3/11.5)."""
    text = _BY_KEY["admin"]["text"].lower()
    for alias in ("stats", "create", "inspect", "disband", "tiers", "give"):
        assert alias in text, f"admin topic missing legacy alias '{alias}'"
    # The def-only / read-only surfaces and their opt-out rationale.
    for concept in ("powerup", "terrain", "planet",
                    "not hot-reloadable", "opt"):
        assert concept in text, f"admin topic missing '{concept}'"


def test_admin_topic_lists_every_entity_command():
    """The topic (and its See Also) must name all twelve @<entity> commands
    so each is discoverable from the umbrella (Requirement 11.3)."""
    text = _BY_KEY["admin"]["text"]
    for cmd in ("@item", "@building", "@agent", "@tech", "@outpost",
                "@alliance", "@player", "@stat", "@resource",
                "@powerup", "@terrain", "@planet"):
        assert cmd in text, f"admin topic missing command '{cmd}'"
    assert "# See Also" in text


def test_admin_topic_color_tags_balanced():
    """Every color code opened is closed by a |n (HELP_STYLE §6)."""
    text = _BY_KEY["admin"]["text"]
    assert len(_OPEN.findall(text)) <= len(_CLOSE.findall(text)), (
        "admin: unbalanced color tags"
    )


def test_admin_topic_names_do_not_collide():
    """The 'admin' topic's key + aliases must not clash with any OTHER
    topic's key or aliases (a clash would let one shadow the other)."""
    entry = _BY_KEY["admin"]
    my_names = {entry["key"], *entry.get("aliases", [])}
    other_names = set()
    for other in HELP_ENTRY_DICTS:
        if other["key"] == "admin":
            continue
        other_names.add(other["key"])
        other_names.update(other.get("aliases", []))
    clashes = my_names & other_names
    assert not clashes, f"admin topic names collide with other topics: {clashes}"
