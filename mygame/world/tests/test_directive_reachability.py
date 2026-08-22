"""Reachability guards for the onboarding directive chain.

The chain is strictly sequential and does no achievability check: if a step asks
for something the player cannot do at the XP they hold when it becomes current,
the chain parks there permanently. These tests recompute the whole walkthrough
from the LIVE data files, so a retune of a building gate, a directive reward, or
the XP curve fails here rather than silently stranding new players.

The bug that motivated them: the Survey Array was level 8 and cost Circuits — a
resource that does not occur on the starting planet at any level — while the
directive asking for one became current at level 6.
"""

from __future__ import annotations

import pathlib

import yaml

from world import progression

_DATA = pathlib.Path(__file__).resolve().parents[2] / "data" / "definitions"

#: Resources the STARTING planet's terrain yields. Terra's terrain_weights name
#: Plains/Dirt/Rock/Mountain/Sand/Snow/Forest/River; of those only these four
#: carry a resource_type.
TERRA_RESOURCES = {"Wood", "Stone", "Iron", "Biomass"}

#: Economy XP the guided path necessarily earns, from balance.yaml.
XP_BUILD, XP_UPGRADE, XP_TRAIN = 30, 30, 40

#: Action XP banked BEFORE each step becomes current — the buildings the player
#: must raise (and the agent they must train) to get that far. Keyed by step.
_ACTION_XP_BEFORE = {
    "build_hq": 0,                          # nothing built yet
    "build_extractor": XP_BUILD,            # the HQ landed
    "train_agent": XP_BUILD,                # the Extractor landed
    "assign_harvester": XP_BUILD + XP_TRAIN,  # Academy built + agent trained
    "build_wall": 0,
    "equip_weapon": XP_BUILD,               # the Wall landed
    "guard_patrol": XP_BUILD,               # the Armory landed
    "build_munitions_plant": 0,
    "survey_outpost": XP_BUILD,             # the Munitions Plant landed
    "upgrade_hq": XP_BUILD,                 # the Survey Array landed
    "scout_patrol": XP_UPGRADE,             # the HQ upgrade completed
}


def _load(name):
    return yaml.safe_load((_DATA / name).read_text(encoding="utf-8"))


def _directives():
    return _load("directives.yaml")


def _buildings():
    return {b["abbreviation"]: b for b in _load("buildings.yaml")}


def _required_building(step) -> str | None:
    """The building a step needs — explicit field, else its condition."""
    explicit = step.get("requires_building")
    if explicit:
        return str(explicit)
    btype = (step.get("condition") or {}).get("building_type")
    return str(btype) if btype else None


def _walkthrough():
    """Yield ``(step_key, xp_when_current, required_abbr)`` down the chain."""
    progression.build_thresholds()
    total = 0
    for step in _directives():
        key = step["key"]
        total += _ACTION_XP_BEFORE.get(key, 0)
        yield key, total, _required_building(step)
        total += int((step.get("reward") or {}).get("xp", 0) or 0)


class TestEveryStepIsReachable:
    def test_no_step_is_gated_above_the_xp_held_when_it_becomes_current(self):
        progression.build_thresholds()
        buildings = _buildings()
        for key, xp, abbr in _walkthrough():
            if abbr is None:
                continue
            required_level = int(buildings[abbr].get("rank_requirement", 1) or 1)
            needed_xp = progression.xp_for_level(required_level)
            assert xp >= needed_xp, (
                f"Directive {key!r} asks for {abbr} (level {required_level} = "
                f"{needed_xp} XP) but the player holds only {xp} XP when that "
                f"step becomes current. The chain would park here forever."
            )

    def test_no_step_needs_a_resource_the_starting_planet_lacks(self):
        buildings = _buildings()
        for key, _xp, abbr in _walkthrough():
            if abbr is None:
                continue
            cost = buildings[abbr].get("cost") or {}
            offworld = set(cost) - TERRA_RESOURCES
            assert not offworld, (
                f"Directive {key!r} asks for {abbr}, which costs {sorted(offworld)} "
                f"— not obtainable on the starting planet at any level."
            )

    def test_no_step_needs_a_deed_the_chain_never_awards(self):
        # The chain deliberately excludes an outpost kill, so no step may ask
        # for a building behind a deed gate — the player would have no way to
        # satisfy it from the guided path.
        buildings = _buildings()
        for key, _xp, abbr in _walkthrough():
            if abbr is None:
                continue
            deed = buildings[abbr].get("unlock_deed")
            assert not deed, (
                f"Directive {key!r} asks for {abbr}, gated behind the {deed!r} "
                f"deed the onboarding chain never awards."
            )


class TestChainOutcome:
    def test_completer_lands_in_the_intended_band(self):
        progression.build_thresholds()
        steps = _directives()
        total = sum(_ACTION_XP_BEFORE.get(s["key"], 0) for s in steps)
        total += sum(int((s.get("reward") or {}).get("xp", 0) or 0) for s in steps)
        level = progression.level_for_xp(total)
        assert 6 <= level <= 7, (
            f"Chain completer reaches {total} XP = level {level}; the documented "
            f"target is level 6-7."
        )

    def test_documented_directive_xp_total_matches_the_data(self):
        # The header comment quotes a total; keep it honest.
        total = sum(
            int((s.get("reward") or {}).get("xp", 0) or 0) for s in _directives()
        )
        assert total == 185, (
            f"Directive XP sums to {total}; the header comment says 185. "
            f"Update whichever is wrong."
        )

    def test_every_building_step_names_its_building(self):
        # A step whose description mentions building/upgrading something must
        # expose it, or the requirement annotation silently shows nothing.
        for step in _directives():
            desc = step.get("description", "").lower()
            if desc.startswith("build ") or "upgrade your" in desc:
                assert _required_building(step) is not None, (
                    f"Step {step['key']!r} ({desc!r}) names a building in its "
                    f"description but exposes no requires_building/condition, "
                    f"so its gates cannot be annotated."
                )
