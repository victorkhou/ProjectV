"""
Composition-root wiring guards (L5 review fix).

``server/conf/game_init.py`` is the ONLY place production systems are wired
together (house style: systems never import each other at module top — every
cross-system collaboration is injected there via a setter). That makes it the
single point of failure for a whole bug class: a hook that every unit test
exercises by injecting a fake, but that PRODUCTION never actually wires — the
tests stay green while the live server silently runs with the dead default
(the "production wiring gap": is_player broken on the real db, the tick clock
frozen at 0, death-loss never applied...).

These tests are cheap source-text tripwires in the spirit of
``TestOutpostStaleStep`` (which guards a tick step registered but not
emitted): they read game_init.py's SOURCE — without booting Django/Evennia —
and assert the load-bearing injection calls are still present. They cannot
prove the wiring is *correct*, but they make it impossible to delete or
rename one of these lines without a test telling you exactly which production
hook just went dead.

If one of these fails: either restore the setter call in
``initialize_game()``, or — if the hook was intentionally removed — delete
its assertion here IN THE SAME CHANGE and document where the behavior moved.
"""

import os
import re
import unittest

_GAME_INIT_PATH = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "..", "..", "server", "conf", "game_init.py"
))


def _read_source() -> str:
    with open(_GAME_INIT_PATH, encoding="utf-8") as f:
        return f.read()


def _strip_comments(source: str) -> str:
    """Drop comment-only content so a commented-out injection can't pass."""
    return "\n".join(
        re.sub(r"#.*$", "", line) for line in source.splitlines()
    )


class TestCompositionRootWiring(unittest.TestCase):
    """The load-bearing injection lines exist in game_init.py's source."""

    #: setter-call -> what goes silently dead in production without it.
    REQUIRED_INJECTIONS = {
        "set_range_resolver": (
            "TargetingSystem falls back to the RAW weapon stat: lock "
            "acquire/upkeep/fire-time range checks lose the tech/Sniper-Nest "
            "bonuses and the max_weapon_range cap, diverging from the "
            "engine's queue/resolve checks (R8)."
        ),
        "set_definitions_provider": (
            "FogOfWarSystem's Watchtower vision-aura capability check falls "
            "back to the live-registry default instead of the injected "
            "provider (R10.2 wiring)."
        ),
        "set_death_loss_func": (
            "Death loss goes dead: a slain player keeps ALL carried "
            "gear/supplies/resources and the Respawn Beacon recovery stash "
            "never fills (death-loss + Respawn Beacon design)."
        ),
        "set_gear_drop_spawner": (
            "Passive (agent-driven) gear production can no longer drop the "
            "produced item on the building's tile — production refunds "
            "forever instead of yielding gear."
        ),
        "set_pvp_gear_drop_spawner": (
            "The PvP underdog-bounty gear drop goes dead: a slain player's "
            "gear is silently destroyed instead of (sometimes) dropping for "
            "the killer."
        ),
        "set_branch_validators": (
            "BuildingSystem's chain loses the three Branch construction "
            "gates: a Branch_Building can be raised with no matching "
            "Branch_Commitment and a Branch switch costs nothing (R3.3-3.5, "
            "R4.1-4.2)."
        ),
        "set_branch_estate_provider": (
            "The demolish path stops reporting the Branch_Estate it is about "
            "to shrink, so the last building of a live Branch goes down with "
            "no warning (R5.3)."
        ),
        "set_branch_resolver": (
            "TechLabSystem and AgentSystem fall back to deriving/ignoring "
            "commitment locally: db.tech_bonuses rebuilds from a dormant "
            "tree and the Branch agent roles are ungated (R6, R7)."
        ),
        # Not a setter, but the same bug class: a call whose absence is
        # invisible to every unit test because the tests call rebuild()
        # themselves. ``rebuild_from_world`` (bombs, outposts) does not match
        # this pattern — the paren must follow ``rebuild`` directly.
        "rebuild": (
            "Every persisted Vector_Operation stays inert after a restart: "
            "the Operation_Records survive on their durable owners but no "
            "vector re-tracks them, so a launched operation never resolves, "
            "never expires, and keeps counting against its owner's in-flight "
            "cap forever (R8.22, R14.3-14.5)."
        ),
    }

    @classmethod
    def setUpClass(cls):
        cls.source = _strip_comments(_read_source())

    def test_game_init_source_is_readable(self):
        self.assertTrue(
            os.path.isfile(_GAME_INIT_PATH),
            f"game_init.py not found at {_GAME_INIT_PATH} — if the "
            "composition root moved, update this guard to follow it.",
        )
        self.assertIn("def initialize_game", self.source)

    def test_load_bearing_injections_present(self):
        for setter, consequence in self.REQUIRED_INJECTIONS.items():
            with self.subTest(setter=setter):
                # A real CALL (``.set_x(``), not a bare-name mention.
                pattern = re.compile(r"\.\s*" + re.escape(setter) + r"\s*\(")
                self.assertRegex(
                    self.source, pattern,
                    f"game_init.py no longer calls {setter}(...) — this is "
                    "the production-dead-hook bug class: unit tests inject "
                    "fakes and stay green while the live server runs the "
                    f"dead default. Without this wiring: {consequence}",
                )

    def test_range_resolver_wired_to_public_engine_method(self):
        """The targeting resolver is the engine's PUBLIC resolve_weapon_range.

        L4 review fix: game_init previously injected the private
        ``_resolve_weapon_range``; the public alias is the supported
        cross-system surface. Guards against regressing to the private name
        (or wiring some other callable entirely).
        """
        self.assertRegex(
            self.source,
            re.compile(
                r"set_range_resolver\s*\(\s*combat_engine\s*\.\s*"
                r"resolve_weapon_range\s*\)"
            ),
            "targeting_system.set_range_resolver must be wired to the "
            "PUBLIC combat_engine.resolve_weapon_range (not the private "
            "_resolve_weapon_range, and not something else).",
        )

    def test_branch_resolver_wired_into_both_consumers(self):
        """BOTH TechLabSystem and AgentSystem receive the Branch resolver.

        The presence check above is satisfied by a single
        ``set_branch_resolver`` call, but these are two independent hooks:
        wiring one and forgetting the other leaves half the feature running on
        its pre-feature default with every test still green.
        """
        for consumer in ("tech_system", "agent_system"):
            with self.subTest(consumer=consumer):
                self.assertRegex(
                    self.source,
                    re.compile(
                        re.escape(consumer)
                        + r"\s*\.\s*set_branch_resolver\s*\(\s*branch_system\s*\)"
                    ),
                    f"{consumer}.set_branch_resolver(branch_system) is "
                    "missing from the composition root.",
                )

    def test_branch_system_constructed_with_every_collaborator(self):
        """BranchSystem takes every collaborator by injection (R15.1, R15.4).

        Each constructor argument defaults to ``None`` so the system stays
        constructible from a bare registry in tests — which means a forgotten
        argument here degrades production to a silent refusal instead of an
        error. This asserts the live construction passes all of them.
        """
        match = re.search(
            r"branch_system\s*=\s*BranchSystem\((.*?)\n    \)",
            self.source,
            re.DOTALL,
        )
        self.assertIsNotNone(
            match, "game_init.py no longer constructs BranchSystem(...)."
        )
        call = match.group(1)
        for collaborator in (
            "current_tick_func",
            "building_system",
            "tech_system",
            "agent_system",
            "alliance_system",
            "combat_engine",
        ):
            with self.subTest(collaborator=collaborator):
                self.assertIn(
                    collaborator + "=", call,
                    f"BranchSystem is constructed without {collaborator} — "
                    "it defaults to None and the services needing it refuse.",
                )

    def test_branch_system_registered_under_its_documented_key(self):
        """``game_systems["branch_system"]`` is the documented lookup key.

        The ``vector_operations`` tick step is registered only when this key
        resolves, so a renamed key silently stops every Vector_Operation from
        advancing.
        """
        self.assertRegex(
            self.source,
            re.compile(r"[\"']branch_system[\"']\s*:\s*branch_system"),
            "branch_system is not registered in the game_systems dict under "
            'the key "branch_system".',
        )


if __name__ == "__main__":
    unittest.main()
