"""Unit tests for the build-requirement readout.

Covers ``world.build_requirements``: which gates it reports, the order it
reports them in, and its never-raise contract. The point of the module is that a
directive naming a building the player cannot yet raise says WHY, so these tests
pin the "reachable objective gets no suffix" rule as hard as the annotations
themselves.
"""

from __future__ import annotations

import re

from world.build_requirements import (
    directive_building,
    requirement_note,
    unmet_requirements,
)


def _plain(text: str) -> str:
    """Strip Evennia colour markup so assertions test text, not markup."""
    return re.sub(r"\|(?:\[)?[a-zA-Z0-9]", "", str(text))


class _Def:
    """Stand-in for a BuildingDef (duck-typed by the resolver)."""

    def __init__(self, name="Survey Array", rank_requirement=6, cost=None,
                 unlock_deed=None, unlock_deed_count=1):
        self.name = name
        self.rank_requirement = rank_requirement
        self.cost = cost if cost is not None else {}
        self.unlock_deed = unlock_deed
        self.unlock_deed_count = unlock_deed_count


class _Registry:
    def __init__(self, defs):
        self.buildings = dict(defs)

    def resolve_building(self, token):
        return self.buildings.get(str(token).upper())


class _DB:
    def __init__(self, level=1, deeds=None):
        self.level = level
        self.deeds = deeds if deeds is not None else {}


class _Player:
    def __init__(self, level=1, deeds=None, resources=None):
        self.db = _DB(level=level, deeds=deeds)
        self._resources = resources or {}

    def get_resource(self, name):
        return self._resources.get(name, 0)


def _registry(**kw):
    return _Registry({"SA": _Def(**kw)})


class TestLevelGate:
    def test_reports_the_level_and_the_players_own_level(self):
        missing = unmet_requirements(_Player(level=4), "SA", _registry())
        assert missing == ["level 6 (you are 4)"]

    def test_silent_once_the_level_is_met(self):
        assert unmet_requirements(_Player(level=6), "SA", _registry()) == []

    def test_silent_above_the_level(self):
        assert unmet_requirements(_Player(level=40), "SA", _registry()) == []


class TestDeedGate:
    def test_boolean_deed_reports_its_description(self):
        reg = _registry(unlock_deed="outpost_cleared")
        missing = unmet_requirements(_Player(level=6), "SA", reg)
        assert missing == ["destroyed an NPC outpost"]

    def test_counted_deed_reports_progress(self):
        reg = _registry(unlock_deed="outpost_cleared", unlock_deed_count=3)
        player = _Player(level=6, deeds={"outpost_cleared": 1})
        missing = unmet_requirements(player, "SA", reg)
        assert missing == ["destroyed an NPC outpost ×3 (1/3)"]

    def test_silent_once_the_count_is_met(self):
        reg = _registry(unlock_deed="outpost_cleared", unlock_deed_count=3)
        player = _Player(level=6, deeds={"outpost_cleared": 3})
        assert unmet_requirements(player, "SA", reg) == []

    def test_corrupt_deeds_shape_is_treated_as_none_held(self):
        reg = _registry(unlock_deed="outpost_cleared")
        player = _Player(level=6, deeds=["outpost_cleared"])  # legacy/corrupt
        assert unmet_requirements(player, "SA", reg) == ["destroyed an NPC outpost"]


class TestResourceShortfall:
    def test_reports_only_the_resources_actually_short(self):
        reg = _registry(cost={"Wood": 15, "Stone": 25, "Iron": 20})
        player = _Player(level=6, resources={"Wood": 99, "Stone": 12, "Iron": 20})
        missing = unmet_requirements(player, "SA", reg)
        assert missing == ["25 Stone (have 12)"]

    def test_reports_several_shortfalls_together(self):
        reg = _registry(cost={"Stone": 25, "Iron": 20})
        player = _Player(level=6, resources={"Stone": 0, "Iron": 5})
        missing = unmet_requirements(player, "SA", reg)
        assert missing == ["25 Stone (have 0), 20 Iron (have 5)"]

    def test_silent_when_affordable(self):
        reg = _registry(cost={"Wood": 15})
        player = _Player(level=6, resources={"Wood": 15})
        assert unmet_requirements(player, "SA", reg) == []

    def test_a_player_without_a_resource_pool_reports_no_shortfall(self):
        class _NoPool:
            db = _DB(level=6)

        reg = _registry(cost={"Wood": 15})
        assert unmet_requirements(_NoPool(), "SA", reg) == []


class TestOrdering:
    def test_level_then_deed_then_resources(self):
        reg = _registry(
            rank_requirement=8, cost={"Stone": 25},
            unlock_deed="outpost_cleared",
        )
        player = _Player(level=4, resources={"Stone": 0})
        assert unmet_requirements(player, "SA", reg) == [
            "level 8 (you are 4)",
            "destroyed an NPC outpost",
            "25 Stone (have 0)",
        ]


class TestRequirementNote:
    def test_empty_for_a_buildable_target(self):
        assert requirement_note(_Player(level=6), "SA", _registry()) == ""

    def test_renders_a_needs_suffix(self):
        note = requirement_note(_Player(level=4), "SA", _registry())
        assert _plain(note) == " — needs level 6 (you are 4)"

    def test_does_not_nest_parentheses(self):
        # Each gate carries its own "(you are N)" / "(have N)", so the suffix
        # must not wrap them in another pair.
        reg = _registry(rank_requirement=8, cost={"Stone": 25})
        note = _plain(requirement_note(_Player(level=4, resources={}), "SA", reg))
        assert not note.startswith(" (needs")
        assert note.count("((") == 0

    def test_joins_several_gates_with_semicolons(self):
        reg = _registry(rank_requirement=8, cost={"Stone": 25})
        note = requirement_note(_Player(level=4, resources={}), "SA", reg)
        assert "; " in _plain(note)

    def test_empty_for_a_none_target(self):
        # Steps that need no building pass None; the caller appends
        # unconditionally, so this must be safe.
        assert requirement_note(_Player(), None, _registry()) == ""

    def test_empty_for_an_unknown_building(self):
        assert requirement_note(_Player(), "ZZ", _registry()) == ""


class TestNeverRaises:
    def test_an_exploding_registry_yields_no_requirements(self):
        class _Boom:
            def resolve_building(self, token):
                raise RuntimeError("registry on fire")

        assert unmet_requirements(_Player(), "SA", _Boom()) == []
        assert requirement_note(_Player(), "SA", _Boom()) == ""

    def test_malformed_gate_values_do_not_raise(self):
        reg = _registry(rank_requirement="not-a-level", cost={"Stone": "lots"})
        # Degrades to level 1 (met) and skips the uncoercible cost entry.
        assert unmet_requirements(_Player(level=6), "SA", reg) == []

    def test_a_player_without_db_does_not_raise(self):
        class _Bare:
            pass

        assert unmet_requirements(_Bare(), "SA", _registry()) == [
            "level 6 (you are 1)"
        ]


class TestDirectiveBuilding:
    def test_prefers_the_explicit_field(self):
        step = {"requires_building": "SA", "condition": {"building_type": "HQ"}}
        assert directive_building(step) == "SA"

    def test_falls_back_to_the_condition(self):
        assert directive_building({"condition": {"building_type": "MP"}}) == "MP"

    def test_none_for_a_buildingless_step(self):
        assert directive_building({"condition": {"role": "scout"}}) is None
        assert directive_building({}) is None

    def test_none_for_a_non_dict(self):
        assert directive_building(None) is None
