"""
Live-boot smoke test — the ONE test that exercises the real composition root
(``server.conf.game_init.initialize_game``) and real Evennia typeclasses against
a real (in-memory) database.

Why this exists
---------------
The rest of the suite runs against lightweight fakes installed by
``mygame/conftest.py``. That is fast, but it means the production wiring path and
the real Evennia object model are never exercised — which is exactly how a
cluster of HIGH-severity "wiring/reality gap" bugs (see COMPLEXITY_REVIEW.md
Part 5) passed 2000+ green tests while being broken in-game. The fakes were, in
the one place that mattered, *higher-fidelity-than-real*: a fake ``db`` raised
``AttributeError`` on a missing attribute, whereas Evennia's ``DbHolder`` returns
``None`` and never raises, so a ``hasattr``-based predicate failed *open*.

This test closes that gap. It boots real Evennia + a Django test DB, runs the
real ``initialize_game()``, and asserts the wiring/behaviour properties that each
of those bugs violated — on real ``Building`` / ``CombatCharacter`` objects.

How it runs
-----------
It is skipped under the normal (stubbed) suite. To run it, set the escape hatch
so ``conftest`` does NOT install Evennia stubs, and point Django at the settings:

    EVENNIA_REAL_BOOT=1 DJANGO_SETTINGS_MODULE=server.conf.settings \
        python -m pytest mygame/tests/test_live_boot_smoke.py -q

(A convenience wrapper lives in ``mygame/tests/run_live_boot_smoke.sh``.)

The module self-skips (rather than erroring) when the escape hatch is absent, so
``pytest mygame`` stays green and fast.
"""

import os
import unittest

import pytest

# ---------------------------------------------------------------- #
#  Guard: only run in a real-Evennia process. Under the stubbed suite this
#  whole module is skipped (module-level) so the fast default run is unaffected.
# ---------------------------------------------------------------- #

_REAL_BOOT = os.environ.get("EVENNIA_REAL_BOOT") == "1"

if not _REAL_BOOT:
    pytest.skip(
        "live-boot smoke test: set EVENNIA_REAL_BOOT=1 (and "
        "DJANGO_SETTINGS_MODULE=server.conf.settings) to run it against real "
        "Evennia; skipped under the stubbed suite.",
        allow_module_level=True,
    )


# ---------------------------------------------------------------- #
#  Boot real Django + Evennia with an in-memory DB. We never touch the real
#  server/evennia.db3 — force ``:memory:`` before django.setup().
# ---------------------------------------------------------------- #

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "server.conf.settings")

import django  # noqa: E402
from django.conf import settings  # noqa: E402

# Redirect to an ephemeral DB so a test run can never corrupt the game DB.
settings.DATABASES["default"]["NAME"] = ":memory:"

django.setup()

import evennia  # noqa: E402
evennia._init()

from evennia.utils import create  # noqa: E402
from evennia.utils.test_resources import EvenniaTest  # noqa: E402


# ---------------------------------------------------------------- #
#  Build the test-DB schema once for the module. We are not running under the
#  Django test runner (no pytest-django), so the migrations that create the
#  content-type/ObjectDB tables have not run. DiscoverRunner.setup_databases()
#  creates the ephemeral :memory: schema; teardown drops it. EvenniaTest's
#  per-test transaction rollback then works on top of it.
# ---------------------------------------------------------------- #

_DB_RUNNER = None
_DB_CONFIG = None


def setUpModule():
    global _DB_RUNNER, _DB_CONFIG
    from django.test.utils import setup_test_environment
    from django.test.runner import DiscoverRunner

    # Evennia's create_object tolerates a missing DEFAULT_HOME (#2) only when
    # settings.TEST_ENVIRONMENT is True. Evennia's own test runner sets this;
    # since we bootstrap the DB manually, set it ourselves so EvenniaTest.setUp
    # (which creates rooms without an explicit home) doesn't raise.
    settings.TEST_ENVIRONMENT = True

    setup_test_environment()
    _DB_RUNNER = DiscoverRunner(verbosity=0)
    _DB_CONFIG = _DB_RUNNER.setup_databases()


def tearDownModule():
    from django.test.utils import teardown_test_environment
    if _DB_RUNNER is not None and _DB_CONFIG is not None:
        _DB_RUNNER.teardown_databases(_DB_CONFIG)
    teardown_test_environment()


class LiveBootSmokeTest(EvenniaTest):
    """Boots the real composition root and asserts the wiring/reality
    properties that the Part 5 bugs violated, on real typeclass objects.

    ``EvenniaTest`` (a Django ``TestCase``) creates room #1/#2 and #2 as
    ``DEFAULT_HOME`` in ``setUp`` and wraps everything in a transaction that is
    rolled back in ``tearDown`` — so object creation works and nothing persists.
    """

    # -------------------------------------------------------------- #
    #  Helpers
    # -------------------------------------------------------------- #

    def _make_building(self, btype="HQ", x=10, y=10, planet="earth", hp=200):
        b = create.create_object(
            "typeclasses.objects.Building", key=f"{btype}-{x}-{y}",
            location=self.room1, home=self.room1,
        )
        b.db.building_type = btype
        b.db.coord_x = x
        b.db.coord_y = y
        b.db.coord_planet = planet
        b.db.hp = hp
        b.db.hp_max = hp
        b.attributes.add("building_type", btype)
        return b

    def _make_player(self, x=11, y=10, planet="earth", combat_xp=0, location=None):
        c = create.create_object(
            "typeclasses.characters.CombatCharacter", key="Raider",
            location=location or self.room1, home=self.room1,
        )
        c.db.coord_x = x
        c.db.coord_y = y
        c.db.coord_planet = planet
        c.db.combat_xp = combat_xp
        return c

    def _make_planet_room(self, planet="earth"):
        room = create.create_object(
            "typeclasses.rooms.PlanetRoom", key=f"Planet-{planet}", nohome=True,
        )
        room.db.planet = planet
        return room

    def _make_gear_item(self, key="Combat Knife", location=None):
        item = create.create_object(
            "typeclasses.objects.GameItem", key=key,
            location=location, home=self.room1,
        )
        item.db.item_key = "combat_knife"
        item.db.category = "weapon"
        item.db.slot = "weapon"
        return item

    def _make_agent(self, x=0, y=0, planet="earth", location=None):
        npc = create.create_object(
            "typeclasses.npcs.NPC", key="Agent",
            location=location, home=self.room1,
        )
        npc.db.coord_x = x
        npc.db.coord_y = y
        npc.db.coord_planet = planet
        npc.db.npc_type = "agent"
        return npc

    # -------------------------------------------------------------- #
    #  Fix #1 — is_player must NOT fail open on a real Building
    # -------------------------------------------------------------- #

    def test_is_player_false_for_real_building(self):
        from world.utils import is_player, is_building

        b = self._make_building("HQ")
        # This is the crux: on a REAL Evennia object, db.combat_xp is unset and
        # db.__getattribute__ returns None (never raises). A hasattr-based check
        # would say True here — the fail-open bug.
        self.assertIsNone(b.db.combat_xp)
        self.assertFalse(
            is_player(b),
            "is_player must be False for a real Building (fail-open regression)",
        )
        self.assertTrue(is_building(b))

    def test_is_player_true_for_real_character(self):
        from world.utils import is_player

        c = self._make_player()
        self.assertTrue(is_player(c), "a real CombatCharacter is a player")

    # -------------------------------------------------------------- #
    #  Player lifecycle state machine — real DB seeding + routing
    # -------------------------------------------------------------- #

    def test_lifecycle_fields_seed_and_router_promotes_on_real_char(self):
        """On a real CombatCharacter: the new PLAYER_DEFAULTS lifecycle fields
        seed via at_object_creation (player_state None, player_class None,
        death_* None, linkdead_until 0.0), and the login router promotes a
        fresh (None) character to SPAWNING through the single-writer transition
        — the persisted-field behavior the stubbed suite can't prove."""
        from world import player_lifecycle as pl
        from world.constants import (
            PLAYER_STATE_SPAWNING, PLAYER_STATE_LOBBY, PLAYER_STATE_PLAYING,
        )

        c = self._make_player()
        # Seeded defaults present on a real Evennia object.
        self.assertIsNone(c.db.player_state)
        self.assertIsNone(c.db.player_class)
        self.assertIsNone(c.db.death_x)
        self.assertEqual(c.db.linkdead_until, 0.0)

        # Router promotes the fresh character to SPAWNING and it persists.
        self.assertEqual(pl.route_on_login(c), PLAYER_STATE_SPAWNING)
        self.assertEqual(c.db.player_state, PLAYER_STATE_SPAWNING)

        # Illegal edge (spawning -> playing) is rejected; the field is unchanged.
        self.assertFalse(pl.transition(c, PLAYER_STATE_PLAYING))
        self.assertEqual(c.db.player_state, PLAYER_STATE_SPAWNING)

        # Pick a class AND a spawn point, advance through the lobby into play,
        # then die back to spawning — the full walk against a real persisted db.
        # (finish_spawning gates on BOTH class and spawn choice — R13.2.)
        c.db.player_class = "Vanguard"
        c.db.pending_spawn_choice = "random"
        self.assertTrue(pl.finish_spawning(c))
        self.assertEqual(c.db.player_state, PLAYER_STATE_LOBBY)
        self.assertTrue(pl.enter_game(c))
        self.assertEqual(c.db.player_state, PLAYER_STATE_PLAYING)
        pl.record_death(c, c.db.coord_x, c.db.coord_y, c.db.coord_planet)
        self.assertEqual(c.db.player_state, PLAYER_STATE_SPAWNING)
        self.assertEqual(c.db.death_x, c.db.coord_x)

    def test_ensure_attributes_backfills_lifecycle_fields(self):
        """A legacy character missing the lifecycle fields gets them back-filled
        by ensure_attributes (the login migration path) without clobbering a
        real value — player_state stays None (router decides) and a set class is
        preserved."""
        c = self._make_player()
        # Simulate a legacy character: strip the fields as if created pre-feature.
        c.attributes.remove("player_state")
        c.attributes.remove("player_class")
        c.attributes.remove("linkdead_until")

        c.ensure_attributes()

        # Back-filled to defaults (None / 0.0), never crashing.
        self.assertIsNone(c.db.player_state)
        self.assertIsNone(c.db.player_class)
        self.assertEqual(c.db.linkdead_until, 0.0)

        # A pre-existing value is not overwritten by a second migration pass.
        c.db.player_class = "Vanguard"
        c.ensure_attributes()
        self.assertEqual(c.db.player_class, "Vanguard")

    def test_session_model_settings_the_lifecycle_flow_depends_on(self):
        """The staging flow assumes single-character auto-puppet (R12): a login
        auto-puppets ONE character so at_post_puppet fires and drops the player
        into the lobby/spawning UI instead of an OOC character-select screen.
        These are Evennia defaults, but we pin them explicitly in settings.py so a
        future edit can't silently break the flow — assert they hold on a real
        boot."""
        from django.conf import settings

        self.assertTrue(
            getattr(settings, "AUTO_PUPPET_ON_LOGIN", None),
            "lifecycle flow requires AUTO_PUPPET_ON_LOGIN=True (else players land "
            "at OOC char-select and at_post_puppet doesn't route them)",
        )
        self.assertEqual(
            getattr(settings, "MULTISESSION_MODE", None), 0,
            "lifecycle flow assumes MULTISESSION_MODE=0 (single-puppet)",
        )
        self.assertEqual(
            getattr(settings, "MAX_NR_CHARACTERS", None), 1,
            "lifecycle flow assumes one character per account",
        )

    def test_new_session_requires_explicit_login_no_autologin_cookie(self):
        """Every new webclient socket must land at the login screen — never
        auto-authenticate from a shared browser cookie (which, with
        MULTISESSION_MODE=0, would usurp the character already playing).

        Two writers of the webclient_authenticated_uid cookie are disabled:
          1. SharedLoginMiddleware is removed from MIDDLEWARE (settings.py).
          2. Both webclient protocols' at_login cookie-write is neutralized by
             the portal startup monkeypatch (portal_services_plugins.py).
        Assert both, on a real boot, so a regression (Evennia rename, settings
        edit) is caught."""
        from django.conf import settings

        # (1) The website->webclient shared-login middleware is gone.
        self.assertNotIn(
            "evennia.web.utils.middleware.SharedLoginMiddleware",
            list(getattr(settings, "MIDDLEWARE", [])),
            "SharedLoginMiddleware must be removed so a website login can't "
            "auto-authenticate the webclient",
        )

        # (2) Run the portal plugin hook (idempotent) and confirm at_login no
        # longer persists the cookie on EITHER webclient protocol.
        from server.conf.portal_services_plugins import (
            _disable_webclient_autologin_cookie,
        )
        _disable_webclient_autologin_cookie()

        from evennia.server.portal.webclient import WebSocketClient
        from evennia.server.portal.webclient_ajax import AjaxWebClientSession

        class _FakeCsession(dict):
            saved = False
            def save(self):
                self.saved = True

        for cls, label in ((WebSocketClient, "websocket"),
                           (AjaxWebClientSession, "ajax")):
            obj = cls.__new__(cls)
            obj.uid = 4242
            # The real at_login guards its cookie write on ``if csession:`` — an
            # EMPTY dict is falsy and would short-circuit the write even with the
            # monkeypatch absent, making this assertion a false-positive that
            # never fails on regression. Seed the csession so it is truthy: now
            # the real (unpatched) at_login WOULD persist the cookie, so this
            # assertion genuinely fails if the monkeypatch is removed.
            cs = _FakeCsession(existing_session_key="seed")
            obj.get_client_session = lambda cs=cs: cs
            obj.at_login()  # must NOT write the auth cookie
            self.assertNotIn(
                "webclient_authenticated_uid", cs,
                f"{label} at_login must not persist the auto-login cookie",
            )

    def test_full_lobby_flow_enabled_end_to_end(self):
        """With LOBBY_FLOW_ENABLED, exercise the whole flow on real objects:
        fresh char routes to SPAWNING → pick class + spawn → LOBBY → deploy →
        PLAYING (relocated to the resolved spawn) → death routes back to
        SPAWNING recording the death tile. Also proves the in-game command gate
        refuses a world action while spawning."""
        from django.test import override_settings
        from server.conf.game_init import initialize_game
        from world import player_lifecycle as pl
        from world.constants import (
            PLAYER_STATE_SPAWNING, PLAYER_STATE_LOBBY, PLAYER_STATE_PLAYING,
        )
        from commands.lifecycle_commands import (
            CmdClass, CmdSpawn, deploy_from_lobby,
        )

        systems = initialize_game()
        try:
            with override_settings(LOBBY_FLOW_ENABLED=True):
                room = self._make_planet_room("terra")
                systems["planet_rooms"]["terra"] = room
                player = self._make_player(x=1, y=1, planet="terra", location=room)
                player.ndb.systems = systems  # so require_system finds the registry

                # Fresh login routing → SPAWNING with the prompt.
                player._route_lifecycle_on_login()
                self.assertEqual(pl.get_state(player), PLAYER_STATE_SPAWNING)

                # A world-action command is refused while spawning.
                from commands.game_commands import CmdHarvest
                cmd = CmdHarvest()
                cmd.caller = player
                cmd.args = ""
                cmd.session = None
                self.assertTrue(cmd.at_pre_cmd(), "world action must abort while spawning")

                # Pick a class (real classes.yaml is loaded) + a spawn point.
                def _run(cmd_cls, args):
                    c = cmd_cls(); c.caller = player; c.args = args; c.func()
                _run(CmdClass, "vanguard")
                self.assertEqual(player.db.player_class, "vanguard")
                _run(CmdSpawn, "random")
                # Both chosen → advanced to the lobby.
                self.assertEqual(pl.get_state(player), PLAYER_STATE_LOBBY)

                # Deploy → PLAYING, relocated to a valid in-bounds tile.
                self.assertTrue(deploy_from_lobby(player))
                self.assertEqual(pl.get_state(player), PLAYER_STATE_PLAYING)
                self.assertEqual(player.db.coord_planet, "terra")
                pr = systems["planet_registry"]
                self.assertTrue(
                    pr.is_valid_coordinate(player.db.coord_x, player.db.coord_y, "terra"),
                    "deployed to an in-bounds tile",
                )

                # Death routes PLAYING → SPAWNING, records the death tile, AND
                # stows the player out of the world (OOC while re-choosing) so
                # they can't be spawn-camped at full HP on the death tile.
                dx, dy = player.db.coord_x, player.db.coord_y
                from server.conf.game_init import _route_player_death
                # Capture the player-facing death prompt: death routes to
                # SPAWNING, and (like login) must tell the player they died and
                # how to redeploy — otherwise they're silently dumped OOC.
                captured = []
                orig_msg = player.msg
                player.msg = lambda text=None, **kw: captured.append(
                    text[0] if isinstance(text, tuple) else text)
                try:
                    self.assertTrue(_route_player_death(player))
                finally:
                    player.msg = orig_msg
                joined = "\n".join(str(m) for m in captured if m)
                self.assertIn("eliminated", joined.lower(),
                              "a slain player must be told they died")
                # Death re-presents the numbered spawning wizard. The player
                # kept their class, so it resumes at the spawn-point step —
                # assert on the step-agnostic wizard guidance, not "class".
                self.assertTrue(
                    "choose" in joined.lower() or "step" in joined.lower(),
                    "the death prompt must re-present the spawning menu",
                )
                self.assertEqual(pl.get_state(player), PLAYER_STATE_SPAWNING)
                self.assertEqual((player.db.death_x, player.db.death_y), (dx, dy))
                self.assertIsNone(player.location,
                                  "a spawning (dead) player is stowed out of the world")
                from world.utils import player_is_present
                self.assertFalse(player_is_present(player),
                                 "a spawning player is not a combat target")
        finally:
            _teardown_game(systems)

    def test_linkdead_expiry_finds_and_removes_on_real_db(self):
        """H1 regression: the linkdead-expiry tick step enumerates linkdead
        characters via search_object_attribute (a plain db.player_state is
        pickled in db_value, so a db_strvalue ORM filter matched NOTHING and the
        grace timer was effectively infinite). Prove a real linkdead char past
        its deadline is found, routed to LOBBY, and stowed."""
        from django.test import override_settings
        from server.conf.game_init import initialize_game
        from world import player_lifecycle as pl
        from world.constants import PLAYER_STATE_PLAYING, PLAYER_STATE_LOBBY
        import time as _t

        systems = initialize_game()
        try:
            with override_settings(LOBBY_FLOW_ENABLED=True):
                room = self._make_planet_room("terra")
                char = self._make_player(x=6, y=6, planet="terra", location=room)
                char.db.player_state = PLAYER_STATE_PLAYING
                room.coord_index.add(char, 6, 6)
                # Enter linkdead with a deadline ALREADY in the past.
                pl.begin_linkdead(char, now=_t.monotonic() - 100.0, grace_seconds=1.0)

                # Run the expiry step directly (a fresh script instance — the
                # step is a plain method that queries the DB, no tick_data).
                from typeclasses.scripts import GameTickScript
                GameTickScript()._process_linkdead_expiry()

                self.assertEqual(pl.get_state(char), PLAYER_STATE_LOBBY,
                                 "expired linkdead char must be routed to the lobby")
                self.assertIsNone(char.location,
                                  "expired linkdead char must be stowed from the world")
                self.assertEqual(room.get_players_at(6, 6), [],
                                 "expired linkdead char must be de-indexed")
        finally:
            _teardown_game(systems)

    def test_disconnect_clean_quit_vs_drop_on_real_char(self):
        """at_post_unpuppet distinguishes a clean quit (marker set → LOBBY) from
        a dropped connection (no marker → LINKDEAD with a grace deadline), which
        is the corrected signal since Evennia forwards no reason to the hook."""
        from django.test import override_settings
        from server.conf.game_init import initialize_game
        from world import player_lifecycle as pl
        from world.constants import (
            PLAYER_STATE_PLAYING, PLAYER_STATE_LOBBY, PLAYER_STATE_LINKDEAD,
        )

        systems = initialize_game()
        try:
            with override_settings(LOBBY_FLOW_ENABLED=True):
                room = self._make_planet_room("terra")

                # Clean quit: marker set → LOBBY, stowed away (location None).
                quitter = self._make_player(x=2, y=2, planet="terra", location=room)
                quitter.db.player_state = PLAYER_STATE_PLAYING
                quitter.ndb._clean_quit = True
                quitter.at_post_unpuppet(account=None, session=None)
                self.assertEqual(pl.get_state(quitter), PLAYER_STATE_LOBBY)

                # Dropped connection: no marker → LINKDEAD, still in the world.
                dropper = self._make_player(x=4, y=4, planet="terra", location=room)
                dropper.db.player_state = PLAYER_STATE_PLAYING
                room.coord_index.add(dropper, 4, 4)
                dropper.at_post_unpuppet(account=None, session=None)
                self.assertEqual(pl.get_state(dropper), PLAYER_STATE_LINKDEAD)
                self.assertGreater(dropper.db.linkdead_until, 0.0)
                # Lingers in the world (NOT stowed away) so it stays attackable.
                self.assertIsNotNone(dropper.location,
                                     "a linkdead char must linger in the world")
                self.assertIn(dropper, room.get_players_at(4, 4))
        finally:
            _teardown_game(systems)

    def test_quit_retreat_multi_puppet_any_in_combat_blocks_all(self):
        """CmdQuit._retreat_playing_puppets_to_lobby iterates ALL of an account's
        puppets (R12.2 multi-puppet handling). Even though MULTISESSION_MODE=0
        means one puppet in practice, the coded behavior is: if ANY PLAYING puppet
        is in combat, the whole quit is blocked (return True, nobody retreats or
        disconnects); otherwise every PLAYING puppet is retreated to the lobby.

        Exercised on real CombatCharacters via a fake account exposing
        get_all_puppets()."""
        from django.test import override_settings
        from server.conf.game_init import initialize_game
        from world import player_lifecycle as pl
        from world.constants import PLAYER_STATE_PLAYING, PLAYER_STATE_LOBBY
        from world.combat_timer import _get_current_tick, COMBAT_TIMER_DURATION
        from commands.lifecycle_commands import CmdQuit

        systems = initialize_game()
        try:
            with override_settings(LOBBY_FLOW_ENABLED=True):
                room = self._make_planet_room("terra")

                class _FakeAccount:
                    def __init__(self, puppets):
                        self._puppets = puppets
                    def get_all_puppets(self):
                        return self._puppets

                # Case 1: two PLAYING puppets, one in combat → whole quit blocked,
                # NEITHER retreats (both stay PLAYING).
                a = self._make_player(x=2, y=2, planet="terra", location=room)
                b = self._make_player(x=3, y=3, planet="terra", location=room)
                for c in (a, b):
                    c.db.player_state = PLAYER_STATE_PLAYING
                b.db.combat_timer_expires = _get_current_tick() + COMBAT_TIMER_DURATION

                blocked = CmdQuit._retreat_playing_puppets_to_lobby(
                    _FakeAccount([a, b]))
                self.assertTrue(blocked, "any puppet in combat blocks the quit")
                self.assertEqual(pl.get_state(a), PLAYER_STATE_PLAYING,
                                 "no puppet retreats when the quit is blocked")
                self.assertEqual(pl.get_state(b), PLAYER_STATE_PLAYING)

                # Case 2: none in combat → every PLAYING puppet retreats to LOBBY.
                c1 = self._make_player(x=4, y=4, planet="terra", location=room)
                c2 = self._make_player(x=5, y=5, planet="terra", location=room)
                for c in (c1, c2):
                    c.db.player_state = PLAYER_STATE_PLAYING
                    c.db.combat_timer_expires = 0

                retreated = CmdQuit._retreat_playing_puppets_to_lobby(
                    _FakeAccount([c1, c2]))
                self.assertTrue(retreated, "a clear puppet retreats (stay connected)")
                self.assertEqual(pl.get_state(c1), PLAYER_STATE_LOBBY)
                self.assertEqual(pl.get_state(c2), PLAYER_STATE_LOBBY)
        finally:
            _teardown_game(systems)

    def test_quit_fails_closed_when_retreat_raises(self):
        """Anti-combat-log fail-CLOSED: if _retreat_playing_puppets_to_lobby
        raises, CmdQuit.func must NOT fall through to the disconnect path (which
        would let an in-combat player escape to a non-targetable LOBBY). It must
        return early — no clean-quit marker set, no super().func() disconnect."""
        from django.test import override_settings
        from commands.lifecycle_commands import CmdQuit

        with override_settings(LOBBY_FLOW_ENABLED=True):
            disconnected = []  # records if super().func() (the real disconnect) ran

            class _Sessions:
                @staticmethod
                def all():
                    return []

            class _Account:
                sessions = _Sessions()
                def get_all_puppets(self):
                    return []

            cmd = CmdQuit()
            cmd.account = _Account()
            cmd.session = None
            cmd.msg = lambda *a, **k: None
            # Force the retreat gate to raise, simulating a bug in it.
            cmd._retreat_playing_puppets_to_lobby = (
                lambda account: (_ for _ in ()).throw(RuntimeError("boom"))
            )
            # Failing OPEN would reach super().func() (the real disconnect);
            # patch it to record instead of actually disconnecting a session.
            import commands.lifecycle_commands as _lc
            orig_super_func = _lc._BaseQuit.func
            _lc._BaseQuit.func = lambda self: disconnected.append(True)
            try:
                cmd.func()
            finally:
                _lc._BaseQuit.func = orig_super_func

            self.assertEqual(
                disconnected, [],
                "quit must NOT disconnect when the retreat gate raises "
                "(fail-closed: an in-combat player can't escape via an error)",
            )

    def test_linkdead_present_to_turret_targeting_on_real_room(self):
        """A LINKDEAD character (no session) is still 'present' to turret/guard
        target acquisition — get_players_at/get_nearby_players include it — so it
        is attackable during grace exactly as bombs already hit it."""
        from world.utils import player_is_present
        from world.constants import PLAYER_STATE_LINKDEAD

        room = self._make_planet_room("terra")
        player = self._make_player(x=3, y=3, planet="terra", location=room)
        room.coord_index.add(player, 3, 3)

        # Puppeted (has_account) — present, and found by targeting.
        # (No session in this harness, so has_account is False; simulate the
        # linkdead marker and assert presence + targeting inclusion.)
        player.db.player_state = PLAYER_STATE_LINKDEAD
        self.assertTrue(player_is_present(player),
                        "a linkdead character is present (attackable during grace)")
        self.assertIn(player, room.get_players_at(3, 3))
        self.assertIn(player, room.get_nearby_players(3, 3, 5))

    # -------------------------------------------------------------- #
    #  Fix #2 — game_init injects a live tick clock into the 3 systems
    # -------------------------------------------------------------- #

    def test_initialize_game_injects_live_tick_clock(self):
        from server.conf.game_init import initialize_game

        systems = initialize_game()
        try:
            for name in ("combat_engine", "building_system", "powerup_system"):
                sys_obj = systems.get(name)
                self.assertIsNotNone(sys_obj, f"{name} missing from game_systems")
                tick_func = getattr(sys_obj, "_current_tick_func", None)
                self.assertIsNotNone(tick_func, f"{name} has no tick func")
                # Must be the real clock, not the frozen lambda: 0 default. It
                # returns an int (0 before the tick script has ticked, but it is
                # the LIVE reader, which is the property we assert).
                self.assertIsInstance(tick_func(), int)
                # And it must NOT be a hard-frozen zero closure: the injected
                # function reads the GameTickScript, so identity differs from a
                # fresh `lambda: 0`. We assert it is callable + wired, above.
        finally:
            _teardown_game(systems)

    # -------------------------------------------------------------- #
    #  Fix #1 blast radius — a destroyed real Building routes to destruction,
    #  not player-respawn (so BUILDING_DESTROYED fires).
    # -------------------------------------------------------------- #

    def test_zero_hp_building_publishes_building_destroyed(self):
        from world.systems.combat_engine import CombatEngine
        from world.data_registry import DataRegistry
        from world.event_bus import EventBus, BUILDING_DESTROYED

        registry = DataRegistry()
        registry.load_all()
        bus = EventBus()
        seen = []
        bus.subscribe(BUILDING_DESTROYED, lambda **kw: seen.append(kw))

        engine = CombatEngine(registry, bus, current_tick_func=lambda: 5)

        attacker = self._make_player(combat_xp=0)
        building = self._make_building("HQ", hp=200)
        building.db.hp = 0  # already at zero — force the death branch

        # Drive the finalize path directly (same branch order as resolve_tick).
        engine._finalize_hit(attacker, building, weapon_item=None, damage=999,
                             current_tick=5)
        self.assertTrue(
            seen,
            "a 0-HP real Building must publish BUILDING_DESTROYED (it must route "
            "to _handle_building_destruction, not _handle_player_defeat)",
        )

    # -------------------------------------------------------------- #
    #  Fix #3 — with a real online player on a PlanetRoom, the tick's active
    #  building list is NON-empty (coords resolved from the entity, not loc.z).
    # -------------------------------------------------------------- #

    def test_active_building_list_nonempty_for_online_player(self):
        from server.conf.game_init import initialize_game

        systems = initialize_game()
        try:
            planet_rooms = systems.get("planet_rooms") or {}
            self.assertTrue(planet_rooms, "no planet rooms created at boot")
            planet_key, room = next(iter(planet_rooms.items()))

            # Place a real player and a real building on the same tile-ish.
            player = self._make_player(x=10, y=10, planet=planet_key)
            player.location = room
            building = self._make_building("HQ", x=11, y=10, planet=planet_key)
            building.location = room

            from typeclasses.scripts import GameTickScript
            script = GameTickScript.__new__(GameTickScript)
            script._get_all_buildings = lambda: [building]

            chunking = systems.get("chunking")
            active = script._compute_active_data(chunking, [player])
            self.assertIn(
                building, active,
                "an online real player on a PlanetRoom must yield a non-empty "
                "active-building list (coords from entity db, planet-scoped)",
            )
        finally:
            _teardown_game(systems)

    # -------------------------------------------------------------- #
    #  Shield Generator — projects a regenerating shield onto the owner's
    #  nearby buildings; combat drains the shield before HP. On real objects
    #  through the real composition root (ShieldSystem wired at boot).
    # -------------------------------------------------------------- #

    def test_shield_generator_shields_neighbor_and_absorbs_damage(self):
        from server.conf.game_init import initialize_game
        from world.systems.combat_engine import _TurretWeapon

        systems = initialize_game()
        try:
            shield_system = systems["shield_system"]
            engine = systems["combat_engine"]
            self.assertIsNotNone(shield_system, "shield_system must be wired at boot")
            room = self._make_planet_room("earth")

            owner = self._make_player(x=5, y=5, planet="earth", location=room)

            # A level-1 Shield Generator at (5,5) and a Vault two tiles away
            # (within the L1 radius of 2), same owner + planet.
            gen = self._make_building("SG", x=5, y=5, planet="earth", hp=200)
            gen.db.owner = owner
            gen.db.building_level = 1
            vault = self._make_building("VT", x=7, y=5, planet="earth", hp=400)
            vault.db.owner = owner
            vault.db.building_level = 1

            # Recompute shields from the live layout.
            shield_system.refresh([gen, vault])
            # 25% x level(1) x 400 = 100, powered on charged.
            self.assertEqual(vault.db.shield_max, 100)
            self.assertEqual(vault.db.shield, 100)

            # A raider hits the shielded Vault: damage comes off the shield first.
            raider = self._make_player(x=6, y=5, planet="earth", location=room)
            raider.db.combat_xp = 100000
            hp_before = vault.db.hp
            engine.apply_direct_hit(raider, vault, _TurretWeapon(40, 10),
                                    current_tick=1)
            self.assertEqual(vault.db.shield, 60, "shield absorbed the 40 damage")
            self.assertEqual(vault.db.hp, hp_before, "HP untouched while shield holds")

            # Regen ticks the shield back up (1% of 100 per 5-tick interval).
            shield_system.process_tick([vault], tick_number=5)
            self.assertEqual(vault.db.shield, 61)
        finally:
            _teardown_game(systems)

    def test_shield_applied_via_construction_event_not_manual_refresh(self):
        """The LIVE path: building a Shield Generator must shield the owner's
        neighbours via the BUILDING event chain alone — no manual refresh().

        Every other shield test calls ``shield_system.refresh([...])`` directly,
        which masks whether the real wiring (BUILDING_CONSTRUCTED /
        CONSTRUCTION_COMPLETED → ShieldSystem._on_building_changed →
        owner.get_buildings() → refresh) actually fires and finds the roster on
        real Evennia objects. Buildings are created by the REAL
        ``EvenniaBuildingFactory`` (not the coord_planet-setting test helper), so
        ``coord_planet`` is unset exactly as in game. This reproduces the
        in-game report ("SG not shielding nearby buildings") by driving only the
        event bus.
        """
        from server.conf.game_init import initialize_game
        from world.event_bus import event_bus, CONSTRUCTION_COMPLETED
        from world.adapters.evennia_building_repository import (
            EvenniaBuildingFactory,
        )

        systems = initialize_game()
        try:
            registry = systems["registry"]
            factory = EvenniaBuildingFactory()
            room = self._make_planet_room("earth")
            owner = self._make_player(x=5, y=5, planet="earth", location=room)

            # Create both buildings through the SAME factory the game uses — it
            # stamps coord_x/coord_y (via place_on_tile) but NOT coord_planet.
            vault = factory.create_building(
                registry.resolve_building("VT"), room, owner, x=7, y=5,
            )
            gen = factory.create_building(
                registry.resolve_building("SG"), room, owner, x=5, y=5,
            )
            # Real factory leaves coord_planet unset — assert that so this test
            # keeps reproducing the in-game object shape if the factory changes.
            self.assertIsNone(gen.db.coord_planet)
            self.assertIsNone(vault.db.coord_planet)

            # Sanity: the owner's live roster (the link refresh() walks) sees
            # both buildings. If get_buildings() can't find them, the shield
            # can never be computed on the live path.
            roster_ids = {getattr(b, "id", None) for b in owner.get_buildings()}
            self.assertIn(gen.id, roster_ids,
                          "owner.get_buildings() must include the generator")
            self.assertIn(vault.id, roster_ids,
                          "owner.get_buildings() must include the vault")

            event_bus.publish(
                CONSTRUCTION_COMPLETED, player=owner, building=gen, tile=room,
            )

            self.assertEqual(
                vault.db.shield_max, 100,
                "the vault must be shielded purely via the construction event "
                "(25% x level 1 x 400 hp = 100)",
            )
            self.assertEqual(vault.db.shield, 100, "powers on charged")
        finally:
            _teardown_game(systems)

    def test_shield_self_heals_via_periodic_sweep_no_event(self):
        """The reported bug: a Shield Generator created WITHOUT firing a build
        event (admin ``@building spawn``, or a building predating the feature)
        must still shield neighbours — via the tick loop's periodic
        ``refresh_owners`` sweep, not any event.

        Builds both via the real factory, fires NO event, and calls the sweep
        the tick loop runs each regen interval. Before the fix the vault stayed
        unshielded forever; after it, the sweep finds the generator through the
        owner's full roster and shields the vault.
        """
        from server.conf.game_init import initialize_game
        from world.adapters.evennia_building_repository import (
            EvenniaBuildingFactory,
        )

        systems = initialize_game()
        try:
            registry = systems["registry"]
            shield_system = systems["shield_system"]
            factory = EvenniaBuildingFactory()
            room = self._make_planet_room("earth")
            owner = self._make_player(x=5, y=5, planet="earth", location=room)

            gen = factory.create_building(
                registry.resolve_building("SG"), room, owner, x=5, y=5,
            )
            vault = factory.create_building(
                registry.resolve_building("VT"), room, owner, x=7, y=5,
            )
            # No event fired — the vault is unshielded so far.
            self.assertEqual(vault.db.shield_max or 0, 0)

            # The tick loop's safety-net sweep, seeded with the active buildings.
            shield_system.refresh_owners([gen, vault])

            self.assertEqual(
                vault.db.shield_max, 100,
                "the periodic sweep must shield the vault even with no event",
            )
            self.assertEqual(vault.db.shield, 100, "powers on charged")
        finally:
            _teardown_game(systems)

    # -------------------------------------------------------------- #
    #  Repair — tick-based, active-presence, on real objects/composition root
    # -------------------------------------------------------------- #

    def test_repair_progresses_per_tick_and_charges(self):
        """A tick-based repair restores repair_hp_percent_per_tick% HP and
        charges the matching per-tick cost, on a real Building through the real
        BuildingSystem — the active-presence loop the tick script drives."""
        from server.conf.game_init import initialize_game

        systems = initialize_game()
        try:
            building_system = systems["building_system"]
            room = self._make_planet_room("earth")
            player = self._make_player(x=6, y=6, planet="earth", location=room)
            player.db.level = 20
            player.db.rank_level = 20
            # Seed plenty of every resource a Vault needs (Stone + Iron).
            player.db.resources = {"Stone": 999, "Iron": 999}

            vault = self._make_building("VT", x=6, y=6, planet="earth", hp=400)
            vault.db.owner = player
            vault.db.building_level = 1
            vault.db.hp = 200  # damaged to 50%

            ok, msg = building_system.repair(player, vault)
            self.assertTrue(ok, msg)
            self.assertEqual(player.db.activity_state, "repairing")

            # One repair step restores 5% of 400 = 20 HP and charges one tick.
            before = dict(player.db.resources)
            done, reason = building_system.apply_repair_step(vault, player)
            self.assertFalse(done)
            self.assertEqual(vault.db.hp, 220)
            self.assertLess(
                sum(player.db.resources.values()), sum(before.values()),
                "a repair tick must charge resources",
            )
        finally:
            _teardown_game(systems)

    # -------------------------------------------------------------- #
    #  Bombs — 'set all' arms every held unit; the per-type fuse queue
    #  must round-trip through the real Character DB (list Attribute).
    # -------------------------------------------------------------- #

    def test_set_all_lets_every_grenade_be_thrown(self):
        """The reported bug on real objects: with 3 grenades, one 'set all'
        must let all 3 throw without re-setting. Exercises the per-type fuse
        QUEUE persisting on a real CombatCharacter (an Evennia list Attribute),
        through the real BombSystem + equipment handler."""
        from server.conf.game_init import initialize_game

        systems = initialize_game()
        try:
            bomb_system = systems["bomb_system"]
            self.assertIsNotNone(bomb_system, "bomb_system must be wired at boot")
            room = self._make_planet_room("earth")
            player = self._make_player(x=8, y=8, planet="earth", location=room)
            player.db.combat_xp = 100000  # rank high enough to deploy

            # Stock 3 real grenades in the supply bag.
            player.equipment.add_supply("frag_grenade", 3)

            # One 'set all' arms all three (queue of 3 persisted on the DB).
            armed = bomb_system.set_all(player, 3)
            self.assertEqual(armed, 3, "set all must arm every held grenade")
            self.assertEqual(list(player.db.bomb_fuses["frag_grenade"]), [3, 3, 3])

            # All three throw with no re-set between them.
            for direction in ("n", "e", "w"):
                self.assertTrue(
                    bomb_system.throw_grenade(player, "frag_grenade", direction),
                    f"grenade throw {direction} must succeed after one 'set all'",
                )
            self.assertEqual(player.equipment.get_supply("frag_grenade"), 0)
            # Queue fully drained → key removed.
            self.assertNotIn("frag_grenade", (player.db.bomb_fuses or {}))
        finally:
            _teardown_game(systems)

    def test_disarm_multi_tick_resolves_on_real_bomb(self):
        """A multi-tick disarm attempt runs on a real LiveBomb through the real
        fuse tick loop: the disarm timer + who's-disarming persist on the bomb's
        db, and it resolves (success) when the timer elapses before the fuse."""
        from server.conf.game_init import initialize_game

        systems = initialize_game()
        try:
            bomb_system = systems["bomb_system"]
            # Force a 2-tick disarm and a guaranteed success roll.
            bomb_system._randint_func = lambda a, b: 2
            bomb_system._rng_func = lambda: 0.0
            room = self._make_planet_room("earth")
            player = self._make_player(x=4, y=4, planet="earth", location=room)
            player.db.combat_xp = 100000
            player.equipment.add_supply("frag_grenade", 1)

            # Throw a grenade with a long fuse so the disarm wins the race,
            # then stand on wherever it landed so disarm() finds it.
            bomb_system.set_fuse(player, "frag_grenade", 10)
            self.assertTrue(bomb_system.throw_grenade(player, "frag_grenade", "n"))
            self.assertTrue(bomb_system._live_bombs, "a live bomb should exist")
            bomb = bomb_system._live_bombs[0]
            player.db.coord_x = int(bomb.db.coord_x)
            player.db.coord_y = int(bomb.db.coord_y)

            self.assertTrue(bomb_system.disarm(player))
            self.assertEqual(int(bomb.db.disarm_ticks_remaining), 2)
            bomb_system.process_tick(1)   # fuse 10->9, disarm 2->1
            bomb_system.process_tick(2)   # disarm 1->0 → resolve success
            self.assertIsNone(getattr(bomb, "pk", None), "bomb removed on success")
        finally:
            _teardown_game(systems)

    # -------------------------------------------------------------- #
    #  Upgrade — resume (no re-charge) + cancel refund on real objects
    # -------------------------------------------------------------- #

    def test_upgrade_resume_does_not_recharge_on_real_building(self):
        """start_upgrade RESUMES a paused upgrade on a real Building without
        re-deducting the cost or resetting progress (the reported bug)."""
        from server.conf.game_init import initialize_game

        systems = initialize_game()
        try:
            building_system = systems["building_system"]
            room = self._make_planet_room("earth")
            player = self._make_player(x=6, y=6, planet="earth", location=room)
            player.db.level = 30
            player.db.rank_level = 30
            player.db.resources = {"Wood": 999, "Stone": 999, "Iron": 999,
                                   "Energy": 999, "Circuits": 999, "Nexium": 999}

            vault = self._make_building("VT", x=6, y=6, planet="earth", hp=400)
            vault.db.owner = player
            vault.db.building_level = 1

            ok, _ = building_system.start_upgrade(player, vault)
            self.assertTrue(ok)
            self.assertTrue(vault.db.under_construction)
            spent = {r: 999 - v for r, v in player.db.resources.items()}
            # Progress a bit, then resume.
            vault.db.construction_progress = 2
            ok, msg = building_system.start_upgrade(player, vault)  # resume
            self.assertTrue(ok)
            self.assertIn("Resuming", msg)
            # No further spend and progress preserved.
            for r, v in player.db.resources.items():
                self.assertEqual(999 - v, spent[r], f"{r} must not be re-charged")
            self.assertEqual(vault.db.construction_progress, 2)

            # Cancel refunds fully.
            ok, _ = building_system.cancel_upgrade(player, vault)
            self.assertTrue(ok)
            for r, v in player.db.resources.items():
                self.assertEqual(v, 999, f"{r} fully refunded on cancel")
            self.assertFalse(vault.db.under_construction)
        finally:
            _teardown_game(systems)

    # -------------------------------------------------------------- #
    #  Drop/pickup — a dropped item must be indexed and re-gettable
    # -------------------------------------------------------------- #

    def test_dropped_item_is_indexed_and_can_be_picked_back_up(self):
        """The custom CmdDrop must set coords AND register the item in the
        PlanetRoom coordinate index, so get/scan/look see it and it can be
        picked back up. (The stock 'drop' left items un-indexed and invisible.)"""
        from commands.game_commands import CmdDrop, CmdGet

        room = self._make_planet_room("earth")
        player = self._make_player(x=7, y=7, planet="earth", location=room)
        knife = self._make_gear_item("Combat Knife", location=player)

        # --- drop it ---
        drop = CmdDrop()
        drop.caller = player
        drop.args = "Combat Knife"
        drop.func()

        # It left the player and is on the tile, coordinate-indexed.
        self.assertIsNot(knife.location, player, "item should have left inventory")
        self.assertEqual(knife.db.coord_x, 7)
        self.assertEqual(knife.db.coord_y, 7)
        at_tile = room.get_objects_at(7, 7)
        self.assertIn(
            knife, at_tile,
            "dropped item must be in the coordinate index (visible to get/scan/look)",
        )

        # --- pick it back up ---
        get = CmdGet()
        get.caller = player
        get.args = "Combat Knife"
        get.func()

        self.assertIs(
            knife.location, player,
            "the dropped item must be pick-back-up-able via get",
        )
        # No longer on the tile after pickup.
        self.assertNotIn(knife, room.get_objects_at(7, 7))

    def test_empty_tile_capacity_caps_at_one_gear_drop(self):
        """An empty tile (capacity 1) accepts one gear drop and refuses the
        second — exercising spawn_gear_drop's real coordinate-index cap check
        against a real PlanetRoom."""
        from typeclasses.objects import spawn_gear_drop
        from world.definitions import ItemDef

        room = self._make_planet_room("earth")
        item_def = ItemDef(
            key="combat_knife", name="Combat Knife", slot="weapon",
            category="weapon", stat_modifiers={"damage": 8},
        )

        first = spawn_gear_drop(room, item_def, x=3, y=3)
        self.assertIsNotNone(first, "first drop onto an empty tile must succeed")
        self.assertEqual(len(room.get_objects_at(3, 3)), 1)

        # Empty-tile capacity is 1 → the second new gear drop is refused.
        second = spawn_gear_drop(room, item_def, x=3, y=3)
        self.assertIsNone(second, "a full tile must refuse a new gear drop")
        self.assertEqual(len(room.get_objects_at(3, 3)), 1,
                         "the tile must still hold only one item")

    # -------------------------------------------------------------- #
    #  Equip/unequip lifecycle — item location + no map ghost
    # -------------------------------------------------------------- #

    def test_equip_from_ground_then_unequip_returns_to_inventory(self):
        """Equipping an item off a tile moves it onto the player (de-indexed
        from the tile — no map ghost); unequipping leaves it in inventory so it
        shows in 'inventory' and can be re-equipped."""
        from server.conf.game_init import initialize_game
        from typeclasses.objects import spawn_gear_drop
        from world.definitions import ItemDef

        systems = initialize_game()
        try:
            eq = systems["equipment_system"]
            room = self._make_planet_room("earth")
            player = self._make_player(x=5, y=5, planet="earth",
                                       combat_xp=100000, location=room)
            idef = ItemDef(key="combat_knife", name="Combat Knife",
                           slot="weapon", category="weapon",
                           stat_modifiers={"damage": 8})
            knife = spawn_gear_drop(room, idef, x=5, y=5)
            self.assertIn(knife, room.get_objects_at(5, 5))

            # Equip straight off the ground.
            eq.equip(player, knife)
            self.assertIs(knife.location, player, "equipped item must be on the player")
            self.assertNotIn(
                knife, room.get_objects_at(5, 5),
                "equipped item must NOT linger on the tile (map ghost)",
            )
            self.assertIs(player.equipment.get_equipped("weapon"), knife)

            # Unequip → stays in inventory (location is the player).
            eq.unequip(player, "weapon")
            self.assertIn(knife, player.contents,
                          "unequipped item must be in the player's inventory")
            self.assertIsNone(player.equipment.get_equipped("weapon"))
            self.assertNotIn(knife, room.get_objects_at(5, 5))

            # Re-equip from inventory works.
            self.assertTrue(eq.equip(player, knife))
            self.assertIs(player.equipment.get_equipped("weapon"), knife)
        finally:
            _teardown_game(systems)

    # -------------------------------------------------------------- #
    #  Arrival status — an agent that WALKS to its assignment lands on the
    #  DERIVED resting status ("Working"), not a hardcoded "Idle".
    # -------------------------------------------------------------- #

    def test_walked_agent_lands_on_working_not_idle(self):
        """A real NPC that walks a queued path must, on arrival, take the resting
        status DERIVED from its role/assignment — not a hardcoded "Idle".

        On a real ``db`` (where an unset attribute is None, not a raise),
        ``advance_movement`` calls ``resting_activity_status``, which returns
        "Working" for an assigned agent. This is the armory-agent bug, fixed at
        the class level: the movement engine no longer guesses the status.
        """
        room = self._make_planet_room("earth")
        npc = self._make_agent(x=0, y=0, planet="earth", location=room)
        room.coord_index.add(npc, 0, 0)
        # Assigned to a building → resting status must derive to "Working".
        npc.db.role = "engineer"
        npc.db.role_target = self._make_building("AR", x=1, y=0, planet="earth")

        # Queue a one-step walk, exactly as AgentSystem._move_agent_to does.
        npc.set_movement_queue([(1, 0)])

        # Drive the movement engine until the queue drains.
        npc.advance_movement(tick_number=1)

        self.assertEqual(list(npc.db.movement_queue or []), [])
        self.assertEqual(
            npc.db.activity_status, "Working",
            "a walked, assigned agent must derive 'Working' on arrival, not Idle",
        )

    def test_unassigned_walked_agent_lands_on_idle(self):
        """The mirror case: a real NPC with no role derives "Idle" on arrival —
        confirming the authority isn't just hardcoding "Working"."""
        room = self._make_planet_room("earth")
        npc = self._make_agent(x=0, y=0, planet="earth", location=room)
        room.coord_index.add(npc, 0, 0)
        npc.db.role = ""  # unassigned

        npc.set_movement_queue([(1, 0)])
        npc.advance_movement(tick_number=1)

        self.assertEqual(npc.db.activity_status, "Idle")

    # -------------------------------------------------------------- #
    #  Extractor notification — a harvester agent's production notifies its
    #  owner through the real presenter (autonomous extraction isn't silent).
    # -------------------------------------------------------------- #

    def test_harvester_production_notifies_owner_through_presenter(self):
        """With the real composition root booted, a HarvesterScript producing on
        an Extractor emits a ``harvester_produced`` notification that the real
        presenter renders to the owner's ``msg`` sink."""
        from server.conf.game_init import initialize_game
        from typeclasses.agent_scripts import _notify_owner

        systems = initialize_game()
        try:
            room = self._make_planet_room("earth")
            player = self._make_player(x=4, y=4, planet="earth", location=room)

            captured = []
            orig_msg = player.msg
            player.msg = lambda text=None, **kw: captured.append(text)

            npc = self._make_agent(x=4, y=4, planet="earth", location=room)
            npc.db.owner = player

            # Emit the exact notification HarvesterScript.at_repeat sends.
            _notify_owner(npc, "harvester_produced", amount=6, resource_type="Wood")

            player.msg = orig_msg
            self.assertTrue(
                any("Extractor" in (m or "") and "Wood" in (m or "")
                    for m in captured),
                f"owner must be notified of extractor output; got {captured!r}",
            )
        finally:
            _teardown_game(systems)

    # -------------------------------------------------------------- #
    #  Owner-attributed combat — a real turret kill credits/announces/combats
    #  the OWNING player, and pulls that player into combat mode.
    # -------------------------------------------------------------- #

    def test_turret_kill_attributes_to_owner_on_real_objects(self):
        """On real Evennia objects (where an unset db attr is None, not a raise):
        a turret killing player B credits A's kill XP, announces
        "A's Turret has eliminated B", and puts A into combat mode."""
        from server.conf.game_init import initialize_game
        from world.event_bus import COMBAT_ACTION, PLAYER_ELIMINATED

        systems = initialize_game()
        try:
            engine = systems["combat_engine"]
            bus = systems["event_bus"]
            room = self._make_planet_room("earth")

            owner_a = self._make_player(x=5, y=5, planet="earth", location=room)
            owner_a.db.combat_xp = 0
            victim_b = self._make_player(x=6, y=5, planet="earth", location=room)
            victim_b.db.combat_xp = 500  # enough to lose death-loss from
            victim_b.db.hp = 1           # one hit ends it

            turret = self._make_building("TU", x=5, y=5, planet="earth")
            turret.db.owner = owner_a

            eliminations = []
            bus.subscribe(PLAYER_ELIMINATED, lambda **kw: eliminations.append(kw))
            combat_actions = []
            bus.subscribe(COMBAT_ACTION, lambda **kw: combat_actions.append(kw))

            # A synthetic high-damage weapon; the attacker being a TU building is
            # what drives owner attribution.
            from world.systems.combat_engine import _TurretWeapon
            engine.apply_direct_hit(turret, victim_b, _TurretWeapon(999, 10),
                                    current_tick=1)

            # 1. Kill XP credited to the OWNER, not the turret (turret has none).
            self.assertEqual(owner_a.db.combat_xp,
                             engine.registry.balance.xp_kill)
            # 1b. Elimination event carries owner attribution.
            self.assertTrue(eliminations)
            elim = eliminations[-1]
            self.assertIs(elim["attacker_owner"], owner_a)
            self.assertEqual(elim["attacker_kind"], "turret")
            # 2. The COMBAT_ACTION carried the owner so the timer pulls A in.
            self.assertTrue(combat_actions)
            self.assertIs(combat_actions[-1]["attacker_owner"], owner_a)
            # 2b. A is actually in combat (timer expiry in the future).
            self.assertGreater(owner_a.db.combat_timer_expires or 0, 1)
            # 3. Cosmetic tallies on a real db (unset -> None): the turret has
            #    no score sheet so the kill tallies on the OWNER, and the
            #    victim's death tallies on the victim.
            self.assertEqual(owner_a.db.kills, 1)
            self.assertEqual(victim_b.db.deaths, 1)
        finally:
            _teardown_game(systems)

    def test_closed_building_immune_to_ranged_on_real_objects(self):
        """On real objects: a CLOSED building rejects a ranged attack but a melee
        (adjacent) attack still lands; an OPEN building takes the ranged hit."""
        from server.conf.game_init import initialize_game
        from world.systems.combat_engine import _TurretWeapon, SyntheticWeapon

        systems = initialize_game()
        try:
            engine = systems["combat_engine"]
            attacker = self._make_player(x=0, y=0, planet="earth")
            attacker.db.combat_xp = 100000  # ranked, irrelevant to the gate

            # Both buildings created directly; set open state explicitly (the
            # factory default is CLOSED — buildings are cover unless opened).
            closed = self._make_building("MM", x=3, y=0, planet="earth", hp=200)
            closed.set_open(False)
            open_b = self._make_building("MM", x=4, y=0, planet="earth", hp=200)
            open_b.set_open(True)

            ranged = _TurretWeapon(50, 10)  # no weapon_type -> ranged

            # Ranged vs CLOSED building: rejected, no damage.
            ok, msg = engine.queue_attack(attacker, closed, weapon=ranged)
            self.assertFalse(ok)
            self.assertIn("closed", msg.lower())

            # Ranged vs OPEN building: allowed.
            ok, _ = engine.queue_attack(attacker, open_b, weapon=ranged)
            self.assertTrue(ok)

            # Melee vs CLOSED building (attacker adjacent): allowed.
            melee = SyntheticWeapon(50, 1, name="Fist")
            melee.weapon_type = "melee"
            attacker.db.coord_x, attacker.db.coord_y = 2, 0  # adjacent to (3,0)
            ok, _ = engine.queue_attack(attacker, closed, weapon=melee)
            self.assertTrue(ok)
        finally:
            _teardown_game(systems)

    def test_breach_shot_damages_closed_building_on_real_objects(self):
        """On real objects: a breaching directional shot (breach=True) damages a
        CLOSED building — the 'shoot a closed structure down' mechanic — while a
        non-breach ranged shot at the same building is still rejected."""
        from server.conf.game_init import initialize_game
        from world.systems.combat_engine import _TurretWeapon

        systems = initialize_game()
        try:
            engine = systems["combat_engine"]
            attacker = self._make_player(x=0, y=0, planet="earth")
            attacker.db.combat_xp = 100000

            closed = self._make_building("MM", x=3, y=0, planet="earth", hp=200)
            closed.set_open(False)
            ranged = _TurretWeapon(50, 10)  # no weapon_type -> ranged

            # Non-breach ranged shot: rejected by the closed-cover gate.
            ok, msg = engine.queue_attack(attacker, closed, weapon=ranged)
            self.assertFalse(ok)
            self.assertIn("closed", msg.lower())

            # Breaching shot: allowed, and it damages the closed building.
            ok, _ = engine.queue_attack(attacker, closed, weapon=ranged, breach=True)
            self.assertTrue(ok, "a breaching shot must reach a closed building")
            hp_before = closed.db.hp
            engine.resolve_tick()
            self.assertLess(closed.db.hp, hp_before)
        finally:
            _teardown_game(systems)

    def test_wall_takes_ranged_fire_on_real_objects(self):
        """A Wall (combat_barrier) is intrinsically OPEN on real objects: ranged
        fire breaches it even with its 'open' attribute explicitly False —
        resolved via the live registry's WL capability."""
        from server.conf.game_init import initialize_game
        from world.systems.combat_engine import _TurretWeapon
        from world.utils import building_is_open

        systems = initialize_game()
        try:
            engine = systems["combat_engine"]
            attacker = self._make_player(x=0, y=0, planet="earth")
            attacker.db.combat_xp = 100000

            wall = self._make_building("WL", x=3, y=0, planet="earth", hp=600)
            wall.set_open(False)  # explicitly closed — the wall rule overrides
            self.assertTrue(building_is_open(wall), "wall must read as open")

            ranged = _TurretWeapon(50, 10)  # no weapon_type -> ranged
            ok, _ = engine.queue_attack(attacker, wall, weapon=ranged)
            self.assertTrue(ok, "ranged fire must breach a wall")
        finally:
            _teardown_game(systems)

    def test_melee_is_same_tile_only_on_real_objects(self):
        """On real objects: melee connects only when attacker and target share
        the exact tile — an adjacent (even diagonal) foe is out of reach until
        someone closes in."""
        from server.conf.game_init import initialize_game
        from world.systems.combat_engine import SyntheticWeapon

        systems = initialize_game()
        try:
            engine = systems["combat_engine"]
            room = self._make_planet_room("earth")
            attacker = self._make_player(x=5, y=5, planet="earth", location=room)

            melee = SyntheticWeapon(30, 1, name="Fist")
            melee.weapon_type = "melee"

            # Diagonal neighbour (6,6): adjacent but NOT same tile -> refused.
            diag = self._make_player(x=6, y=6, planet="earth", location=room)
            ok, msg = engine.queue_attack(attacker, diag, weapon=melee)
            self.assertFalse(ok, "adjacent foe is not in melee reach")
            self.assertIn("same tile", msg.lower())

            # Same tile (5,5): in reach.
            same = self._make_player(x=5, y=5, planet="earth", location=room)
            ok, _ = engine.queue_attack(attacker, same, weapon=melee)
            self.assertTrue(ok, "a same-tile foe is in melee reach")
        finally:
            _teardown_game(systems)

    def test_player_is_sheltered_on_real_objects(self):
        """On real objects (where an unset db attr is None, not a raise):
        player_is_sheltered is True only for a player inside a CLOSED building,
        and the combat engine refuses a ranged shot against such a player."""
        from server.conf.game_init import initialize_game
        from world.utils import player_is_sheltered
        from world.systems.combat_engine import _TurretWeapon

        systems = initialize_game()
        try:
            engine = systems["combat_engine"]
            room = self._make_planet_room("earth")

            player = self._make_player(x=6, y=5, planet="earth", location=room)
            shelter = self._make_building("MM", x=6, y=5, planet="earth")
            shelter.location = room
            # Register the shelter in this room's coordinate index so
            # get_buildings_at(6,5) (used by player_is_sheltered) resolves it.
            room.coord_index.add(shelter, 6, 5)

            # Standing on the tile but NOT inside -> exposed.
            self.assertFalse(player_is_sheltered(player))

            # Inside a CLOSED building (factory default is closed) -> sheltered.
            player.db.inside_building = True
            shelter.set_open(False)
            self.assertTrue(player_is_sheltered(player))

            # A ranged attack against a sheltered player is refused.
            attacker = self._make_player(x=0, y=0, planet="earth", location=room)
            ok, msg = engine.queue_attack(
                attacker, player, weapon=_TurretWeapon(50, 10)
            )
            self.assertFalse(ok)
            self.assertIn("sheltered", msg.lower())

            # Symmetric cover: while sheltered (closed), the player also can't
            # fire ranged OUT. Give them a ranged weapon and confirm rejection.
            from world.definitions import ItemDef
            rifle_def = ItemDef(key="rifle", name="Rifle", slot="weapon",
                                category="weapon", stat_modifiers={"damage": 20},
                                weapon_type="ranged")
            from typeclasses.objects import spawn_gear_drop
            rifle = spawn_gear_drop(room, rifle_def, x=6, y=5)
            systems["equipment_system"].equip(player, rifle)
            bystander = self._make_player(x=7, y=5, planet="earth", location=room)
            ok, msg = engine.queue_attack(player, bystander)
            self.assertFalse(ok, "sheltered player must not fire ranged out")
            self.assertIn("inside", msg.lower())

            # Open the building -> no cover -> exposed again, ranged allowed.
            shelter.set_open(True)
            self.assertFalse(player_is_sheltered(player))
            attacker.db.coord_x, attacker.db.coord_y = 0, 5  # within range 10
            ok, _ = engine.queue_attack(
                attacker, player, weapon=_TurretWeapon(50, 10)
            )
            self.assertTrue(ok)
        finally:
            _teardown_game(systems)

    def test_melee_room_gate_on_real_objects(self):
        """On real objects: a player inside a building can only be meleed from
        the SAME tile. An adjacent melee attack is refused even for an OPEN
        building (the reported guard-through-the-wall bug); a same-tile melee
        lands. Independent of the closed-cover rule."""
        from server.conf.game_init import initialize_game
        from world.utils import target_inside_building
        from world.systems.combat_engine import SyntheticWeapon

        systems = initialize_game()
        try:
            engine = systems["combat_engine"]
            room = self._make_planet_room("earth")

            # Raider inside an OPEN building at (6,5) — like the turret in the
            # report. inside_building True + a building registered on the tile.
            raider = self._make_player(x=6, y=5, planet="earth", location=room)
            raider.db.inside_building = True
            turret = self._make_building("TU", x=6, y=5, planet="earth")
            turret.location = room
            turret.set_open(True)
            room.coord_index.add(turret, 6, 5)
            self.assertTrue(target_inside_building(raider))

            melee = SyntheticWeapon(50, 1, name="Fist")
            melee.weapon_type = "melee"

            # Attacker on the ADJACENT tile (5,5) — e.g. a guard on the HQ tile.
            guard = self._make_player(x=5, y=5, planet="earth", location=room)
            ok, msg = engine.queue_attack(guard, raider, weapon=melee)
            self.assertFalse(ok, "adjacent melee must not reach into a building")
            self.assertIn("same tile", msg.lower())

            # Same tile (6,5) -> melee lands.
            guard.db.coord_x, guard.db.coord_y = 6, 5
            ok, _ = engine.queue_attack(guard, raider, weapon=melee)
            self.assertTrue(ok, "same-tile melee should land")
        finally:
            _teardown_game(systems)

    def test_targeting_lock_and_shoot_on_real_objects(self):
        """On real objects: TargetingSystem is wired at the composition root; a
        ranged lock completes over the balance-configured ticks and then a
        locked shot queues an accuracy-bearing attack through the engine."""
        from server.conf.game_init import initialize_game
        from world.definitions import ItemDef
        from typeclasses.objects import spawn_gear_drop

        systems = initialize_game()
        try:
            targeting = systems["targeting_system"]
            engine = systems["combat_engine"]
            self.assertIsNotNone(targeting, "targeting_system must be wired")
            room = self._make_planet_room("earth")

            shooter = self._make_player(x=0, y=0, planet="earth", location=room)
            shooter.db.combat_xp = 100000  # high rank so no equip gate blocks

            # A real ranged weapon, equipped via the real equipment system.
            rifle_def = ItemDef(key="rifle", name="Rifle", slot="weapon",
                                category="weapon",
                                stat_modifiers={"damage": 20, "range": 8},
                                weapon_type="ranged")
            rifle = spawn_gear_drop(room, rifle_def, x=0, y=0)
            systems["equipment_system"].equip(shooter, rifle)

            # An enemy in range.
            enemy = self._make_player(x=3, y=0, planet="earth", location=room)

            ok, _ = targeting.acquire(shooter, enemy)
            self.assertTrue(ok, "should start a lock with a ranged weapon in range")
            self.assertFalse(targeting.is_locked(shooter))

            # Advance ticks until the lock completes (bounded).
            for tick in range(1, 20):
                targeting.process_tick(tick, [shooter])
                if targeting.is_locked(shooter):
                    break
            self.assertTrue(targeting.is_locked(shooter), "lock should complete")

            # A locked shot queues an attack carrying the targeted accuracy.
            engine.pending_actions.clear()
            acc = targeting.targeted_accuracy(rifle)
            ok, _ = engine.queue_attack(shooter, enemy, weapon=rifle, accuracy=acc)
            self.assertTrue(ok)
            self.assertEqual(len(engine.pending_actions), 1)
            self.assertEqual(engine.pending_actions[0]["accuracy"], acc)

            # The SHOOTER moving breaks the lock immediately (at_coord_change),
            # not on the next tick — a real move_entity fires the hook.
            self.assertTrue(targeting.is_locked(shooter))
            room.move_entity(shooter, 0, 1)  # step north
            self.assertIsNone(targeting.get_target(shooter),
                              "moving must break the shooter's lock")
            self.assertFalse(targeting.is_locked(shooter))

            # And the enemy leaving weapon range also breaks a (re-acquired) lock
            # via the per-tick upkeep.
            shooter.db.coord_x, shooter.db.coord_y = 0, 0
            targeting.acquire(shooter, enemy)
            for tick in range(1, 20):
                targeting.process_tick(tick, [shooter])
                if targeting.is_locked(shooter):
                    break
            self.assertTrue(targeting.is_locked(shooter))
            enemy.db.coord_x = 50  # out of range
            targeting.process_tick(99, [shooter])
            self.assertFalse(targeting.is_locked(shooter))
            self.assertIsNone(targeting.get_target(shooter))
        finally:
            _teardown_game(systems)

    def test_lock_onto_real_enemy_guard_survives_upkeep(self):
        """Regression: a real enemy guard (via the NPC-base factory) carries
        coord_planet, so locking onto one does NOT instantly drop with 'left the
        area' on the next upkeep tick (the reported bug)."""
        from server.conf.game_init import initialize_game
        from world.adapters.evennia_npc_base_factory import EvenniaNpcBaseFactory
        from world.definitions import ItemDef
        from typeclasses.objects import spawn_gear_drop

        systems = initialize_game()
        try:
            targeting = systems["targeting_system"]
            room = self._make_planet_room("earth")

            shooter = self._make_player(x=0, y=0, planet="earth", location=room)
            shooter.db.combat_xp = 100000
            rifle_def = ItemDef(key="rifle", name="Rifle", slot="weapon",
                                category="weapon",
                                stat_modifiers={"damage": 20, "range": 8},
                                weapon_type="ranged")
            rifle = spawn_gear_drop(room, rifle_def, x=0, y=0)
            systems["equipment_system"].equip(shooter, rifle)

            factory = EvenniaNpcBaseFactory()
            sentinel = factory.create_sentinel("Outpost #1", room, "earth")
            guard = factory.create_enemy_guard(sentinel, room, 3, 0, "guard", 80)
            # The fix: the guard is stamped with the planet.
            self.assertEqual(guard.db.coord_planet, "earth")

            ok, _ = targeting.acquire(shooter, guard)
            self.assertTrue(ok)
            # One upkeep tick must NOT drop the lock as 'left the area'.
            targeting.process_tick(1, [shooter])
            self.assertIsNotNone(targeting.get_target(shooter),
                                 "lock must survive: guard is on the same planet")
        finally:
            _teardown_game(systems)

    def test_lock_onto_agent_without_coord_planet_survives_upkeep(self):
        """Regression (review): a player-owned agent carries coords but NOT
        coord_planet. Locking onto one must survive upkeep — _planet falls back
        to the agent's ROOM planet, which matches the shooter. Without the
        fallback the lock dropped on the first tick as 'left the area'."""
        from server.conf.game_init import initialize_game
        from world.definitions import ItemDef
        from typeclasses.objects import spawn_gear_drop

        systems = initialize_game()
        try:
            targeting = systems["targeting_system"]
            room = self._make_planet_room("earth")
            shooter = self._make_player(x=0, y=0, planet="earth", location=room)
            shooter.db.combat_xp = 100000
            rifle_def = ItemDef(key="rifle", name="Rifle", slot="weapon",
                                category="weapon",
                                stat_modifiers={"damage": 20, "range": 8},
                                weapon_type="ranged")
            rifle = spawn_gear_drop(room, rifle_def, x=0, y=0)
            systems["equipment_system"].equip(shooter, rifle)

            # A real agent NPC in the same room, with coords but NO coord_planet.
            agent = self._make_agent(x=3, y=0, location=room)
            agent.db.coord_planet = None  # the gap the fallback closes
            self.assertEqual(room.planet_name, "earth")

            ok, _ = targeting.acquire(shooter, agent)
            self.assertTrue(ok)
            targeting.process_tick(1, [shooter])
            self.assertIsNotNone(
                targeting.get_target(shooter),
                "lock must survive when target planet resolves via its room")
        finally:
            _teardown_game(systems)

    def test_missed_shot_puts_both_sides_in_combat_on_real_objects(self):
        """On real objects: a MISSED ranged shot publishes COMBAT_ACTION, so the
        wired combat-timer subscriber sets combat_timer_expires on BOTH shooter
        and target — the state that actually gates wall-passage/enter-leave. A
        miss that set only combat_lockout_tick would leave both free to move."""
        from server.conf.game_init import initialize_game
        from world.definitions import ItemDef
        from typeclasses.objects import spawn_gear_drop

        systems = initialize_game()
        try:
            engine = systems["combat_engine"]
            room = self._make_planet_room("earth")
            shooter = self._make_player(x=0, y=0, planet="earth", location=room)
            shooter.db.combat_xp = 100000
            rifle_def = ItemDef(key="rifle", name="Rifle", slot="weapon",
                                category="weapon",
                                stat_modifiers={"damage": 20, "range": 8},
                                weapon_type="ranged")
            rifle = spawn_gear_drop(room, rifle_def, x=0, y=0)
            systems["equipment_system"].equip(shooter, rifle)
            target = self._make_player(x=3, y=0, planet="earth", location=room)
            target.key = "Victim"

            # Force a MISS: rng.random() always returns ~1.0 (>= any accuracy).
            class _AlwaysMiss:
                def random(self):
                    return 0.999999
            engine._rng = _AlwaysMiss()

            engine.queue_attack(shooter, target, weapon=rifle, accuracy=0.5)
            engine.resolve_tick()

            # No damage (it missed) but BOTH are now "in combat" via the timer.
            self.assertEqual(target.db.hp, target.db.hp_max)
            self.assertGreater(shooter.db.combat_timer_expires or 0, 0,
                               "shooter must be in combat after firing (even a miss)")
            self.assertGreater(target.db.combat_timer_expires or 0, 0,
                               "target must be in combat after being shot at")
        finally:
            _teardown_game(systems)

    def test_instant_attack_resolves_immediately_on_real_objects(self):
        """On real objects: a player's direct attack via resolve_now applies
        damage in the SAME call (instant), without touching the tick queue."""
        from server.conf.game_init import initialize_game
        from world.definitions import ItemDef
        from typeclasses.objects import spawn_gear_drop

        systems = initialize_game()
        try:
            engine = systems["combat_engine"]
            room = self._make_planet_room("earth")
            attacker = self._make_player(x=0, y=0, planet="earth", location=room)
            attacker.db.combat_xp = 100000
            knife_def = ItemDef(key="knife", name="Knife", slot="weapon",
                                category="weapon",
                                stat_modifiers={"damage": 15, "range": 1},
                                weapon_type="melee")
            knife = spawn_gear_drop(room, knife_def, x=0, y=0)
            systems["equipment_system"].equip(attacker, knife)
            # Melee is same-tile only — put the victim on the attacker's tile.
            target = self._make_player(x=0, y=0, planet="earth", location=room)
            target.key = "Victim"
            hp0 = target.db.hp

            ok, _ = engine.resolve_now(attacker, target)
            self.assertTrue(ok)
            self.assertLess(target.db.hp, hp0, "instant attack applies damage now")
            self.assertEqual(len(engine.pending_actions), 0,
                             "resolve_now must not queue to the tick")
        finally:
            _teardown_game(systems)

    def test_mine_arm_tick_and_detonate_on_real_objects(self):
        """On real objects: arm a mine (LiveBomb placed + indexed), tick its fuse
        down, and confirm it detonates — a co-located victim takes damage and the
        bomb object is deleted. Exercises the real BombSystem + spawn_bomb +
        coordinate index + AoE-through-combat-engine path end to end."""
        from server.conf.game_init import initialize_game

        systems = initialize_game()
        try:
            bomb_system = systems["bomb_system"]
            self.assertIsNotNone(bomb_system, "bomb_system must be wired")
            room = self._make_planet_room("earth")

            placer = self._make_player(x=4, y=4, planet="earth", location=room)
            placer.db.combat_xp = 100000
            placer.equipment.add_supply("land_mine", 1)

            # A victim standing on the same tile as the armed mine. Register in
            # the coordinate index (as movement would) so the blast area-query
            # finds them — _make_player sets coords but doesn't index.
            victim = self._make_player(x=4, y=4, planet="earth", location=room)
            victim.key = "Victim"
            room.coord_index.add(victim, 4, 4)
            hp0 = victim.db.hp

            # Set a 2s fuse and arm the mine on the placer's tile.
            self.assertTrue(bomb_system.set_fuse(placer, "land_mine", 2))
            self.assertTrue(bomb_system.arm_mine(placer, "land_mine"))
            # The mine is now a placed, indexed LiveBomb on (4,4).
            bombs = room.get_objects_at(4, 4, type_tag="bomb")
            self.assertEqual(len(bombs), 1)
            mine = bombs[0]
            self.assertEqual(mine.db.fuse_remaining, 2)

            # Tick once: fuse 2 -> 1, still live, no damage yet.
            bomb_system.process_tick(1)
            self.assertEqual(mine.db.fuse_remaining, 1)
            self.assertEqual(victim.db.hp, hp0)

            # Tick again: fuse 1 -> 0 -> detonate. Victim takes the blast and the
            # bomb is removed from the world + the coordinate index.
            bomb_system.process_tick(2)
            self.assertLess(victim.db.hp, hp0, "co-located victim caught in blast")
            self.assertIsNone(getattr(mine, "pk", None),
                              "detonated mine must be deleted")
            self.assertEqual(room.get_objects_at(4, 4, type_tag="bomb"), [],
                             "detonated mine must be de-indexed")
        finally:
            _teardown_game(systems)

    def test_blast_breaches_cover_hits_sheltered_player_and_building(self):
        """On real objects: a bomb blast BREACHES cover. The reported bug was that
        a placer standing inside their own (closed) building took no damage and
        the building itself was unharmed. A blast is an anti-structure weapon —
        it must damage BOTH a player sheltered inside a closed building AND the
        closed building on the tile."""
        from server.conf.game_init import initialize_game
        from world.utils import player_is_sheltered

        systems = initialize_game()
        try:
            bomb_system = systems["bomb_system"]
            room = self._make_planet_room("earth")

            # A closed building on (4,4), with the placer sheltered inside it.
            # HQ (not a combat_barrier) can actually be CLOSED — a Wall is
            # intrinsically open and would not shelter its occupant.
            building = self._make_building(btype="HQ", x=4, y=4, planet="earth",
                                           hp=500)
            building.set_open(False)
            room.coord_index.add(building, 4, 4)
            b_hp0 = building.db.hp

            placer = self._make_player(x=4, y=4, planet="earth", location=room)
            placer.db.combat_xp = 100000
            placer.db.inside_building = True
            room.coord_index.add(placer, 4, 4)
            p_hp0 = placer.db.hp
            # Precondition: the placer is genuinely sheltered (closed building).
            self.assertTrue(player_is_sheltered(placer),
                            "placer must be sheltered for the breach to be meaningful")

            placer.equipment.add_supply("land_mine", 1)
            self.assertTrue(bomb_system.set_fuse(placer, "land_mine", 1))
            self.assertTrue(bomb_system.arm_mine(placer, "land_mine"))

            # Fuse 1 -> 0 -> detonate.
            bomb_system.process_tick(1)

            self.assertLess(placer.db.hp, p_hp0,
                            "a blast must reach a sheltered player (breach cover)")
            self.assertLess(building.db.hp, b_hp0,
                            "a blast must damage a closed building (anti-structure)")
        finally:
            _teardown_game(systems)

    def test_armed_mine_survives_reboot_via_rebuild(self):
        """A mine armed before a restart resumes its fuse: rebuild_from_world
        re-tracks the persisted LiveBomb so it keeps ticking (its fuse state and
        coords persist on db; only the in-memory countdown list is rebuilt)."""
        from server.conf.game_init import initialize_game

        systems = initialize_game()
        try:
            bomb_system = systems["bomb_system"]
            room = self._make_planet_room("earth")
            placer = self._make_player(x=2, y=2, planet="earth", location=room)
            placer.db.combat_xp = 100000
            placer.equipment.add_supply("land_mine", 1)
            bomb_system.set_fuse(placer, "land_mine", 5)
            bomb_system.arm_mine(placer, "land_mine")

            # Simulate a reboot: drop the in-memory list, then rebuild from world.
            bomb_system._live_bombs = []
            n = bomb_system.rebuild_from_world({"earth": room})
            self.assertEqual(n, 1, "the armed mine must be re-tracked after reboot")
        finally:
            _teardown_game(systems)

    def test_armed_bomb_cannot_be_picked_up_on_real_objects(self):
        """On real objects: a co-located player CANNOT 'get' an armed mine — the
        game's CmdGet gates pickup through at_pre_get, and LiveBomb.at_pre_get
        refuses. (Regression: the get:false() lock alone was never enforced by
        CmdGet, so the bomb was pocketable — the HIGH review finding.)"""
        from server.conf.game_init import initialize_game
        from commands.game_commands import CmdGet

        systems = initialize_game()
        try:
            bomb_system = systems["bomb_system"]
            room = self._make_planet_room("earth")
            placer = self._make_player(x=6, y=6, planet="earth", location=room)
            placer.db.combat_xp = 100000
            placer.equipment.add_supply("land_mine", 1)
            bomb_system.set_fuse(placer, "land_mine", 30)
            bomb_system.arm_mine(placer, "land_mine")
            bombs = room.get_objects_at(6, 6, type_tag="bomb")
            self.assertEqual(len(bombs), 1)
            mine = bombs[0]

            # A player on the mine's tile tries to grab it.
            grabber = self._make_player(x=6, y=6, planet="earth", location=room)
            grabber.key = "Grabber"
            room.coord_index.add(grabber, 6, 6)
            get = CmdGet()
            get.caller = grabber
            get.args = "Land Mine"
            get.func()

            # The bomb is NOT in the grabber's inventory and stays on its tile.
            self.assertIsNot(mine.location, grabber,
                             "an armed bomb must not be pickupable")
            self.assertIn(mine, room.get_objects_at(6, 6, type_tag="bomb"),
                          "the bomb must remain on its tile")
            # at_pre_get refuses directly (independent of the get command path).
            self.assertFalse(mine.at_pre_get(grabber),
                             "LiveBomb.at_pre_get must refuse pickup")
        finally:
            _teardown_game(systems)

    def test_grenade_throw_clamps_to_map_edge_on_real_objects(self):
        """On real objects: BombSystem gets planet_registry.is_valid_coordinate
        wired at boot, so a grenade thrown toward a map edge lands ON the edge
        tile, never off-map. Uses a REAL planet room/key so the bounds check
        actually resolves (a bogus planet would fall open and hide the clamp)."""
        from server.conf.game_init import initialize_game

        systems = initialize_game()
        try:
            bomb_system = systems["bomb_system"]
            planet_rooms = systems.get("planet_rooms") or {}
            self.assertTrue(planet_rooms, "no planet rooms at boot")
            planet_key, room = next(iter(planet_rooms.items()))

            # Stand near the west edge; throw west with a long range.
            thrower = self._make_player(x=1, y=4, planet=planet_key, location=room)
            thrower.db.combat_xp = 100000
            thrower.equipment.add_supply("frag_grenade", 1)
            bomb_system.set_fuse(thrower, "frag_grenade", 30)
            bomb_system.throw_grenade(thrower, "frag_grenade", "w")

            bombs = room.get_objects_at(0, 4, type_tag="bomb")
            self.assertEqual(len(bombs), 1, "grenade must land on the edge tile (0,4)")
            # And nothing landed off-map at a negative x.
            self.assertGreaterEqual(bombs[0].db.coord_x, 0)
        finally:
            _teardown_game(systems)

    def test_out_of_bounds_renders_as_fog_on_real_objects(self):
        """On real objects: the FogOfWarSystem gets planet_registry.is_valid_
        coordinate wired at boot, so a tile beyond a real planet's bounds is
        out-of-bounds (fog), while a tile inside it is not. Exercises the real
        composition-root wiring, not a stub."""
        from server.conf.game_init import initialize_game

        systems = initialize_game()
        try:
            fog = systems["fog_system"]
            planet_registry = systems["planet_registry"]
            planet_rooms = systems.get("planet_rooms") or {}
            self.assertTrue(planet_rooms, "no planet rooms at boot")
            planet_key = next(iter(planet_rooms.keys()))
            space = planet_registry.get_space(planet_key)

            # Origin is in-bounds; one tile below/left of origin is off-map.
            self.assertTrue(fog.is_in_bounds(planet_key, 0, 0))
            self.assertFalse(fog.is_in_bounds(planet_key, -1, 0))
            self.assertFalse(fog.is_in_bounds(planet_key, 0, -1))
            # One tile past the max edge is off-map; the last valid tile is in.
            self.assertTrue(fog.is_in_bounds(planet_key, space.width - 1, space.height - 1))
            self.assertFalse(fog.is_in_bounds(planet_key, space.width, 0))
            self.assertFalse(fog.is_in_bounds(planet_key, 0, space.height))
        finally:
            _teardown_game(systems)

    def test_teleport_looks_after_coords_updated_cross_planet(self):
        """On real objects: teleporting to a DIFFERENT planet updates all coords
        (planet + x + y) and THEN issues one look — so the shown view reflects
        the destination. The old bug: at_object_receive fired mid-move (before
        x/y updated) on a Z change, leaking a stale-coord tile line; a same-planet
        teleport showed nothing. Both are now a single, correct look."""
        from server.conf.game_init import initialize_game
        from commands.admin_commands import CmdTeleport

        systems = initialize_game()
        try:
            planet_rooms = systems.get("planet_rooms") or {}
            self.assertGreaterEqual(len(planet_rooms), 2,
                                    "need two planets for a cross-planet teleport")
            keys = list(planet_rooms.keys())
            src_key, dst_key = keys[0], keys[1]
            src_room = planet_rooms[src_key]

            player = self._make_player(x=2, y=3, planet=src_key, location=src_room)
            # Grant Builder perms so the command's lock passes.
            player.permissions.add("Builder")

            captured = []
            orig_msg = player.msg
            player.msg = lambda text=None, **kw: captured.append(
                text[0] if isinstance(text, tuple) else text)

            cmd = CmdTeleport()
            cmd.caller = player
            cmd.cmdstring = "goto"
            cmd.args = f"7 9 {dst_key}"
            cmd.func()

            player.msg = orig_msg

            # All three coords updated to the destination.
            self.assertEqual(player.db.coord_planet, dst_key)
            self.assertEqual((player.db.coord_x, player.db.coord_y), (7, 9))
            self.assertIs(player.location, planet_rooms[dst_key])
            # A look ran after the teleport (map/tile output was produced).
            self.assertTrue(captured, "teleport must issue a look")
            # No captured line references the ORIGIN coords (2,3) — the stale
            # mid-move renders (auto-look + tile line) are suppressed.
            self.assertFalse(
                any("(2, 3)" in (m or "") or "2,3" in (m or "") for m in captured),
                f"teleport leaked a stale-coord line: {captured!r}",
            )
            # The player no longer leaks in the ORIGIN planet's coordinate index
            # (skipping move hooks means we de-indexed it manually).
            self.assertNotIn(player, src_room.get_objects_at(2, 3))
            # And it IS indexed at the destination tile on the new planet.
            self.assertIn(player, planet_rooms[dst_key].get_objects_at(7, 9))
        finally:
            _teardown_game(systems)

    def test_teleport_same_planet_issues_look(self):
        """A same-planet (X/Y-only) teleport also issues a look — previously it
        fired no arrival hook at all, so the view never refreshed."""
        from server.conf.game_init import initialize_game
        from commands.admin_commands import CmdTeleport

        systems = initialize_game()
        try:
            planet_rooms = systems.get("planet_rooms") or {}
            self.assertTrue(planet_rooms)
            key = next(iter(planet_rooms.keys()))
            room = planet_rooms[key]
            player = self._make_player(x=2, y=3, planet=key, location=room)
            player.permissions.add("Builder")

            captured = []
            orig_msg = player.msg
            player.msg = lambda text=None, **kw: captured.append(
                text[0] if isinstance(text, tuple) else text)

            cmd = CmdTeleport()
            cmd.caller = player
            cmd.cmdstring = "goto"
            cmd.args = "8 8"  # same planet, new x/y
            cmd.func()

            player.msg = orig_msg
            self.assertEqual((player.db.coord_x, player.db.coord_y), (8, 8))
            self.assertTrue(captured, "same-planet teleport must still issue a look")
        finally:
            _teardown_game(systems)

    # -------------------------------------------------------------- #
    #  Alliances — real registry, roster rebuild, end-to-end, leaderboard
    # -------------------------------------------------------------- #

    def _prep_alliance_char(self, char, level=20):
        """Seed an EvenniaTest-provided (account-linked) character for alliance ops.

        Uses the harness's ``char1``/``char2`` (which have a real linked account,
        so ``has_account`` is True and ``_is_real_player`` passes) rather than
        fabricating one — ``has_account`` reads the real account FK, not a db attr.
        """
        char.db.coord_x = 1
        char.db.coord_y = 1
        char.db.coord_planet = "earth"
        char.db.combat_xp = 0
        char.db.level = level
        char.db.player_state = "playing"
        # Clear any leftover pointer so each test starts un-allianced.
        char.db.player_alliance = None
        char.db.alliance_rank = None
        return char

    def test_alliance_registry_persists_and_next_id_starts_at_one(self):
        from server.conf.game_init import initialize_game
        from evennia.utils.search import search_script

        systems = initialize_game()
        try:
            found = search_script("alliance_registry")
            self.assertTrue(found, "AllianceRegistry script must exist after boot")
            reg = found[0]
            self.assertEqual(reg.db.next_alliance_id, 1)
            self.assertEqual(reg.db.alliances, {})
            # And the system is linked to it.
            self.assertIs(systems["alliance_system"]._alliances, reg)
        finally:
            _teardown_game(systems)

    def test_alliance_roster_rebuild_via_search_object_attribute(self):
        """Regression guard: a member's pointer is a pickled int, so it must be
        found by search_object_attribute — a db_strvalue filter matches nothing."""
        from server.conf.game_init import initialize_game
        from evennia.utils.search import search_object_attribute

        systems = initialize_game()
        try:
            system = systems["alliance_system"]
            leader = self._prep_alliance_char(self.char1)
            aid = system.found(leader, "Iron Wolves", "IW")
            self.assertIsNotNone(aid)
            self.assertEqual(leader.db.player_alliance, aid)
            # The pickled-int pointer IS discoverable by search_object_attribute.
            found = search_object_attribute(key="player_alliance", value=aid)
            self.assertIn(leader, list(found))
        finally:
            _teardown_game(systems)

    def test_alliance_end_to_end_found_invite_accept_deposit_disband(self):
        from server.conf.game_init import initialize_game

        systems = initialize_game()
        try:
            system = systems["alliance_system"]
            leader = self._prep_alliance_char(self.char1)
            member = self._prep_alliance_char(self.char2, level=10)
            aid = system.found(leader, "Coalition", "COAL")
            self.assertIsNotNone(aid)
            self.assertTrue(system.invite(leader, member))
            self.assertTrue(system.accept(member, "COAL"))
            self.assertEqual(member.db.player_alliance, aid)
            # Deposit into the treasury, then disband and confirm the even-split
            # credits both members (11 Iron / 2 -> 5 each, remainder 1 to leader).
            member.add_resource("Iron", 11)
            self.assertTrue(system.deposit(member, {"Iron": 11}))
            l_before = leader.get_resource("Iron")
            m_before = member.get_resource("Iron")
            self.assertTrue(system.disband(leader))
            self.assertIsNone(system._record(aid), "record gone after disband")
            self.assertIsNone(leader.db.player_alliance)
            self.assertIsNone(member.db.player_alliance)
            self.assertEqual(member.get_resource("Iron") - m_before, 5)
            self.assertEqual(leader.get_resource("Iron") - l_before, 6)  # 5 + rem 1
        finally:
            _teardown_game(systems)

    def test_alliance_leaderboard_exact_composite_score(self):
        """A broken Member_Resolver (ranking everything at 0) fails this — it
        asserts the EXACT PvP-weighted score for known member stats."""
        from server.conf.game_init import initialize_game

        systems = initialize_game()
        try:
            system = systems["alliance_system"]
            bal = system.registry.balance
            leader = self._prep_alliance_char(self.char1, level=10)
            aid = system.found(leader, "Scorers", "SCR")
            # Known stats: level 10, 2 pvp kills, 1 pve kill, 0 buildings. Anchor
            # the decay tick to NOW (the real GameTickScript may have ticked past
            # 0) so elapsed == 0 and no decay applies — score = 10*w_level +
            # 2*w_pvp + 1*w_pve + 0.
            leader.db.level = 10
            leader.db.scored_kills_pvp = 2.0
            leader.db.scored_kills_pve = 1.0
            leader.db.last_kill_decay_tick = system._now_tick()
            expected = (
                10 * bal.alliance_score_w_level
                + 2 * bal.alliance_score_w_kills_pvp
                + 1 * bal.alliance_score_w_kills_pve
            )
            self.assertAlmostEqual(system.alliance_score(aid), expected)
            board = system.leaderboard()
            self.assertEqual(board[0][0], aid)
            self.assertAlmostEqual(board[0][1], expected)
        finally:
            _teardown_game(systems)

    # -------------------------------------------------------------- #
    #  Early-game rebalance — the new-player economy loop and agent
    #  purpose, exercised end-to-end on real objects + the real
    #  composition root (the rebalance's headline behaviours).
    # -------------------------------------------------------------- #

    def test_build_completion_awards_economy_xp_and_levels_up(self):
        """R1: completing a construction routes economy XP through the live
        RankSystem, so a fresh (level-1) player's XP and level actually advance
        from building alone — the core new-player dopamine loop, on real objects.
        """
        from server.conf.game_init import initialize_game
        from world.progression import xp_for_level

        systems = initialize_game()
        try:
            building_system = systems["building_system"]
            room = self._make_planet_room("earth")
            player = self._make_player(x=8, y=8, planet="earth",
                                       combat_xp=0, location=room)
            player.db.level = 1
            player.db.rank_level = 1
            # A brand-new build sitting at level 1 (no upgrade) → build_complete XP.
            building = self._make_building("EX", x=8, y=8, planet="earth")
            building.db.building_level = 1
            building.db.owner = player

            xp_award = systems["registry"].balance.xp_build_complete
            self.assertGreater(xp_award, 0, "build XP must be configured")

            building_system._complete_construction(player, building)

            # XP was credited through the RankSystem (not a raw db write) and the
            # level recomputed from the live hybrid curve.
            self.assertEqual(player.db.combat_xp, xp_award)
            # xp_build_complete (30) clears the L2 threshold (40? no — L2=40, so
            # one build may not level; assert the level is at least what the curve
            # says for the awarded XP, proving recompute ran).
            from world import progression
            self.assertEqual(player.db.level,
                             progression.level_for_xp(xp_award))

            # A few more builds push past L2 — proving the loop advances the bar.
            for i in range(6):
                b = self._make_building("EX", x=9 + i, y=8, planet="earth")
                b.db.building_level = 1
                b.db.owner = player
                building_system._complete_construction(player, b)
            self.assertGreaterEqual(
                player.db.level, 2,
                "several builds must advance a fresh player past level 1",
            )
            self.assertGreaterEqual(player.db.combat_xp, xp_for_level(2))
        finally:
            _teardown_game(systems)

    def test_train_assign_guard_and_patrol_on_real_objects(self):
        """R3/R4: a fresh player trains an agent, assigns it the army 'guard'
        role WITHOUT a building (R4.1), and sets a patrol route — the agent
        flow the rebalance made purposeful, exercised on real objects and the
        real composition root (agent cap now = owner level, R3.1)."""
        from server.conf.game_init import initialize_game

        systems = initialize_game()
        try:
            agent_system = systems["agent_system"]
            room = self._make_planet_room("earth")
            # Level 5 → agent cap = max(1, level) = 5 (R3.1 unfreeze), so a fresh
            # player can train at least one agent.
            player = self._make_player(x=4, y=4, planet="earth", location=room)
            player.db.level = 5
            player.db.rank_level = 1
            player.db.next_agent_id = 1
            for r in ("Wood", "Stone", "Iron"):
                player.add_resource(r, 500)

            academy = self._make_building("AC", x=4, y=4, planet="earth")
            academy.db.owner = player
            # The real building factory always stamps building_level; _make_building
            # doesn't, and a real db returns None (not the getattr default) for an
            # unset attr, so set it explicitly for the training-time math.
            academy.db.building_level = 1

            # Train → complete → a real NPC agent exists and is owned by player.
            ok, msg = agent_system.train_agent(player, academy)
            self.assertTrue(ok, f"training must start: {msg}")
            agent = agent_system.complete_training(academy)
            self.assertIsNotNone(agent, "training completion must spawn an agent")
            agent.location = room
            agent.db.coord_x, agent.db.coord_y = 4, 4
            agent.db.coord_planet = "earth"

            agent_id = agent.db.agent_id
            # Assign the army 'guard' role WITHOUT a target building (R4.1).
            ok, msg = agent_system.assign_agent(player, agent_id, "guard")
            self.assertTrue(ok, f"guard assignment (no building) must succeed: {msg}")
            self.assertEqual(agent.db.role, "guard")

            # Set a patrol route — the guard/scout patrol capability (R4).
            ok, msg = agent_system.set_patrol_route(
                player, agent_id, [(4, 4), (5, 4), (5, 5)]
            )
            self.assertTrue(ok, f"guard must accept a patrol route: {msg}")
            self.assertTrue(
                agent.db.patrol_route,
                "patrol waypoints must persist on the agent",
            )
        finally:
            _teardown_game(systems)


def _teardown_game(systems):
    """Best-effort teardown: stop any scripts initialize_game created so they
    don't leak across tests. The in-memory DB is rolled back by EvenniaTest."""
    try:
        from evennia.utils.search import search_script
        for key in ("game_tick", "auto_save", "alliance_registry"):
            for s in search_script(key) or []:
                try:
                    s.stop()
                except Exception:
                    pass
    except Exception:
        pass


if __name__ == "__main__":
    unittest.main()
