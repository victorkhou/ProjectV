"""
Unit tests for the player-lifecycle commands (commands/lifecycle_commands.py):
the class / spawn selection (state 3) and deploy (state 4) flow.
"""

import sys
import types
import unittest


def _ensure_evennia_stubs():
    if "evennia" in sys.modules and getattr(sys.modules["evennia"], "__file__", None):
        return

    def _mod(name, attrs=None):
        m = types.ModuleType(name)
        for k, v in (attrs or {}).items():
            setattr(m, k, v)
        sys.modules.setdefault(name, m)
        return m

    class Command:
        key = ""
        aliases = []
        locks = ""
        help_category = "General"
        def func(self):
            pass

    _mod("evennia")
    _mod("evennia.commands")
    _mod("evennia.commands.command", {"Command": Command})


_ensure_evennia_stubs()

from world.constants import (  # noqa: E402
    PLAYER_STATE_LOBBY,
    PLAYER_STATE_PLAYING,
    PLAYER_STATE_SPAWNING,
)
from world.definitions import ClassDef  # noqa: E402
from commands.lifecycle_commands import (  # noqa: E402
    CmdClass,
    CmdDeploy,
    CmdSelect,
    CmdSpawn,
    _relocate,
    apply_spawn_choice,
    deploy_from_lobby,
    require_in_game,
)
from world import services  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _services_sandbox():
    """Give every test a private, empty facade state, restored on exit."""
    with services.override({}):
        yield


def _install_systems(systems):
    """Register fake *systems* for the current test through the facade."""
    services.get_systems().update(systems)


# -------------------------------------------------------------- #
#  Fakes
# -------------------------------------------------------------- #

class _Registry:
    def __init__(self, classes):
        self.classes = {c.key: c for c in classes}

    def resolve_class(self, token):
        t = token.strip().lower()
        for c in self.classes.values():
            if c.key.lower() == t or c.name.lower() == t:
                return c
        matches = [c for c in self.classes.values() if c.key.lower().startswith(t)]
        return matches[0] if len(matches) == 1 else None


class _NDB:
    def __init__(self, systems):
        self.systems = systems
        self.spawn_choice = None


class _Room:
    """Minimal PlanetRoom fake with configurable indexed-move outcomes."""

    def __init__(self):
        self.ndb = types.SimpleNamespace(_coord_index=None)
        self.move_entity_result = None
        self.move_entity_error = None
        self.move_entity_calls = []

    def get_buildings_at(self, x, y):
        return []

    def move_entity(self, obj, x, y, notify=True):
        self.move_entity_calls.append((obj, x, y, notify))
        if self.move_entity_error is not None:
            raise self.move_entity_error
        if self.move_entity_result is False:
            return False
        obj.db.coord_x = x
        obj.db.coord_y = y
        return self.move_entity_result


class _Index:
    """Set-backed CoordinateIndex fake with one-shot cleanup failure modes."""

    def __init__(self):
        self._data = {}
        self.fail_next_remove = None

    def add(self, obj, x, y):
        self._data.setdefault((x, y), set()).add(obj)

    def remove(self, obj, x, y):
        bucket = self._data.get((x, y))
        if bucket:
            bucket.discard(obj)
            if not bucket:
                self._data.pop((x, y), None)
        failure = self.fail_next_remove
        self.fail_next_remove = None
        if failure == "false":
            return False
        if failure == "exception":
            raise RuntimeError("index remove failed after mutation")
        return None

    def move(self, obj, old_x, old_y, new_x, new_y):
        if old_x is not None and old_y is not None:
            self.remove(obj, int(old_x), int(old_y))
        self.add(obj, new_x, new_y)

    def get_at(self, x, y):
        return list(self._data.get((x, y), set()))


class _IndexedRoom(_Room):
    """Room fake whose move can fail only after index/coordinate mutation."""

    def __init__(self):
        super().__init__()
        self.ndb._coord_index = _Index()

    def move_entity(self, obj, x, y, notify=True):
        self.move_entity_calls.append((obj, x, y, notify))
        old_x = obj.db.coord_x
        old_y = obj.db.coord_y
        self.ndb._coord_index.move(obj, old_x, old_y, x, y)
        obj.db.coord_x = x
        obj.db.coord_y = y
        if self.move_entity_error is not None:
            raise self.move_entity_error
        return self.move_entity_result


class _Caller:
    def __init__(self, state=None, classes=None):
        self.db = types.SimpleNamespace(
            player_state=state, player_class=None,
            pending_spawn_choice=None, coord_planet="terra",
            coord_x=1, coord_y=1,
        )
        self.ndb = _NDB({})
        self.location = None
        self.move_to_result = True
        self.move_to_error = None
        self.move_to_calls = []
        self._room = _Room()
        _install_systems({
            "registry": _Registry(classes or []),
            "planet_rooms": {"terra": self._room},
        })
        self._messages = []
        self._executed = []

    def move_to(self, room, **kwargs):
        self.move_to_calls.append((room, kwargs))
        if self.move_to_error is not None:
            raise self.move_to_error
        if self.move_to_result is not False:
            self.location = room
        return self.move_to_result

    def msg(self, text=None, **kw):
        if text is not None:
            self._messages.append(text)

    def execute_cmd(self, cmd, session=None, **kw):
        self._executed.append(cmd)
        self._executed_sessions = getattr(self, "_executed_sessions", [])
        self._executed_sessions.append(session)

    # last message helper
    def last(self):
        return self._messages[-1] if self._messages else ""


_CLASSES = [
    ClassDef(key="vanguard", name="Vanguard", description="Front line."),
    ClassDef(key="engineer", name="Engineer", description="Builder."),
]


def _run(cmd_cls, caller, args="", cmdstring=None, session=None):
    cmd = cmd_cls()
    cmd.caller = caller
    cmd.args = args
    cmd.session = session
    cmd.cmdstring = cmdstring if cmdstring is not None else getattr(cmd_cls, "key", "")
    cmd.func()
    return caller


# -------------------------------------------------------------- #
#  require_in_game guard
# -------------------------------------------------------------- #

class TestRequireInGame(unittest.TestCase):
    def test_playing_allowed(self):
        c = _Caller(state=PLAYER_STATE_PLAYING)
        self.assertTrue(require_in_game(c))

    def test_none_state_allowed(self):
        # No lifecycle state (flow off / legacy char) -> allowed, unchanged.
        c = _Caller(state=None)
        self.assertTrue(require_in_game(c))

    def test_spawning_blocked_with_hint(self):
        c = _Caller(state=PLAYER_STATE_SPAWNING)
        self.assertFalse(require_in_game(c))
        self.assertIn("class", c.last().lower())

    def test_lobby_blocked_with_hint(self):
        c = _Caller(state=PLAYER_STATE_LOBBY)
        self.assertFalse(require_in_game(c))
        self.assertIn("enter", c.last().lower())


# -------------------------------------------------------------- #
#  class selection (3.2)
# -------------------------------------------------------------- #

class TestCmdClass(unittest.TestCase):
    def test_lists_numbered_choices_when_no_arg(self):
        c = _Caller(state=PLAYER_STATE_SPAWNING, classes=_CLASSES)
        _run(CmdClass, c, "")
        msg = c.last()
        self.assertIn("Vanguard", msg)
        self.assertIn("Engineer", msg)
        self.assertIn("1", msg)  # numbered
        self.assertIn("2", msg)

    def test_number_selects_class(self):
        c = _Caller(state=PLAYER_STATE_SPAWNING, classes=_CLASSES)
        _run(CmdClass, c, "1")  # classes sorted by key: engineer, vanguard
        self.assertEqual(c.db.player_class, "engineer")

    def test_out_of_range_number_reprompts(self):
        c = _Caller(state=PLAYER_STATE_SPAWNING, classes=_CLASSES)
        _run(CmdClass, c, "5")
        self.assertIsNone(c.db.player_class)
        self.assertTrue(any("between" in m.lower() for m in c._messages))

    def test_sets_class_by_name(self):
        c = _Caller(state=PLAYER_STATE_SPAWNING, classes=_CLASSES)
        _run(CmdClass, c, "Vanguard")
        self.assertEqual(c.db.player_class, "vanguard")

    def test_sets_class_by_prefix(self):
        c = _Caller(state=PLAYER_STATE_SPAWNING, classes=_CLASSES)
        _run(CmdClass, c, "eng")
        self.assertEqual(c.db.player_class, "engineer")

    def test_unknown_class_reports(self):
        c = _Caller(state=PLAYER_STATE_SPAWNING, classes=_CLASSES)
        _run(CmdClass, c, "wizard")
        self.assertIsNone(c.db.player_class)
        self.assertIn("unknown", c.last().lower())

    def test_refused_outside_spawning(self):
        c = _Caller(state=PLAYER_STATE_PLAYING, classes=_CLASSES)
        _run(CmdClass, c, "Vanguard")
        self.assertIsNone(c.db.player_class)


# -------------------------------------------------------------- #
#  spawn selection (3.1)
# -------------------------------------------------------------- #

class TestCmdSpawn(unittest.TestCase):
    def test_lists_numbered_options_when_no_arg(self):
        c = _Caller(state=PLAYER_STATE_SPAWNING)
        _run(CmdSpawn, c, "")
        msg = c.last()
        # Numbered menu with human labels (not raw keys).
        self.assertIn("1", msg)
        self.assertIn("Headquarters", msg)
        self.assertIn("Random location", msg)

    def test_sets_choice(self):
        c = _Caller(state=PLAYER_STATE_SPAWNING)
        _run(CmdSpawn, c, "hq")
        self.assertEqual(c.db.pending_spawn_choice, "hq")

    def test_prefix_choice(self):
        c = _Caller(state=PLAYER_STATE_SPAWNING)
        _run(CmdSpawn, c, "ran")
        self.assertEqual(c.db.pending_spawn_choice, "random")

    def test_number_selects_spawn_option(self):
        c = _Caller(state=PLAYER_STATE_SPAWNING)
        _run(CmdSpawn, c, "1")  # first option = respawn beacon (where your
        # recovered loadout waits after death)
        self.assertEqual(c.db.pending_spawn_choice, "respawn")

    def test_out_of_range_number_reprompts(self):
        c = _Caller(state=PLAYER_STATE_SPAWNING)
        _run(CmdSpawn, c, "9")
        self.assertIsNone(c.db.pending_spawn_choice)
        self.assertTrue(any("listed" in m.lower() for m in c._messages))

    def test_unknown_choice_reports(self):
        c = _Caller(state=PLAYER_STATE_SPAWNING)
        _run(CmdSpawn, c, "nowhere")
        self.assertIsNone(c.db.pending_spawn_choice)


class _FakeResolver:
    """Mutable spawn resolver for menu and deploy timing tests."""

    def __init__(self, available):
        self._available = set(available)

    def option_available(self, player, choice, planet_key):
        return choice in self._available

    def resolve(self, player, choice, planet_key):
        if choice not in self._available:
            return None
        return (planet_key, 7, 8)


class TestCmdSpawnFiltering(unittest.TestCase):
    """Only options the player has a target for are offered (state 3.1)."""

    def test_first_time_player_only_sees_random_at_canonical_number(self):
        # No HQ, no beacon, never died -> random keeps canonical option 4.
        c = _Caller(state=PLAYER_STATE_SPAWNING)
        _install_systems({"spawn_resolver": _FakeResolver({"random"})})
        _run(CmdSpawn, c, "")
        msg = c.last()
        self.assertIn("|w4|n. |cRandom location", msg)
        self.assertNotIn("Headquarters", msg)
        self.assertNotIn("Respawn Beacon", msg)
        self.assertNotIn("Place of death", msg)

    def test_hq_hidden_without_hq(self):
        c = _Caller(state=PLAYER_STATE_SPAWNING)
        _install_systems({"spawn_resolver": _FakeResolver({"random", "respawn"})})
        _run(CmdSpawn, c, "hq")
        self.assertIsNone(c.db.pending_spawn_choice)
        self.assertIn("not currently available", c.last().lower())

    def test_respawn_hidden_without_beacon(self):
        c = _Caller(state=PLAYER_STATE_SPAWNING)
        _install_systems({"spawn_resolver": _FakeResolver({"random", "hq"})})
        _run(CmdSpawn, c, "respawn")
        self.assertIsNone(c.db.pending_spawn_choice)
        self.assertIn("not currently available", c.last().lower())

    def test_available_option_still_selectable(self):
        c = _Caller(state=PLAYER_STATE_SPAWNING)
        _install_systems({"spawn_resolver": _FakeResolver({"random", "hq"})})
        _run(CmdSpawn, c, "hq")
        self.assertEqual(c.db.pending_spawn_choice, "hq")

    def test_hidden_options_leave_numbering_gaps(self):
        c = _Caller(state=PLAYER_STATE_SPAWNING)
        _install_systems({"spawn_resolver": _FakeResolver({"random"})})

        _run(CmdSpawn, c, "1")
        self.assertIsNone(c.db.pending_spawn_choice)
        self.assertTrue(any("not currently available" in m for m in c._messages))

        _run(CmdSpawn, c, "4")
        self.assertEqual(c.db.pending_spawn_choice, "random")

    def test_option_disappearing_between_render_and_input_is_not_rebound(self):
        c = _Caller(state=PLAYER_STATE_SPAWNING)
        resolver = _FakeResolver({"hq", "random"})
        _install_systems({"spawn_resolver": resolver})

        _run(CmdSpawn, c, "")
        self.assertIn("|w2|n. |cHeadquarters", c.last())
        resolver._available.remove("hq")
        _run(CmdSpawn, c, "2")

        self.assertIsNone(c.db.pending_spawn_choice)
        self.assertTrue(any("not currently available" in m for m in c._messages))

    def test_hidden_option_named_reports_availability_not_unknown(self):
        # 'spawn hq' with no HQ built is a real option that is merely hidden;
        # the numbered path already says so, and the named path must agree.
        c = _Caller(state=PLAYER_STATE_SPAWNING)
        _install_systems({"spawn_resolver": _FakeResolver({"random"})})

        _run(CmdSpawn, c, "hq")

        self.assertIsNone(c.db.pending_spawn_choice)
        joined = " ".join(c._messages).lower()
        self.assertIn("not currently available", joined)
        self.assertNotIn("unknown spawn option", joined)

    def test_unrecognized_spawn_word_still_reports_unknown(self):
        c = _Caller(state=PLAYER_STATE_SPAWNING)
        _install_systems({"spawn_resolver": _FakeResolver({"random"})})

        _run(CmdSpawn, c, "zzz")

        self.assertIsNone(c.db.pending_spawn_choice)
        self.assertIn("unknown spawn option", " ".join(c._messages).lower())

    def test_no_resolver_installed_fails_open_shows_all(self):
        # No spawn_resolver system wired -> unchanged legacy behavior (all
        # four options shown) rather than hiding everything.
        c = _Caller(state=PLAYER_STATE_SPAWNING)
        _run(CmdSpawn, c, "")
        msg = c.last()
        self.assertIn("Headquarters", msg)
        self.assertIn("Respawn Beacon", msg)
        self.assertIn("Place of death", msg)
        self.assertIn("Random location", msg)


# -------------------------------------------------------------- #
#  Advance SPAWNING -> LOBBY once both chosen
# -------------------------------------------------------------- #

class TestAdvanceToLobby(unittest.TestCase):
    def test_class_then_spawn_advances_to_lobby(self):
        c = _Caller(state=PLAYER_STATE_SPAWNING, classes=_CLASSES)
        _run(CmdClass, c, "Vanguard")
        self.assertEqual(c.db.player_state, PLAYER_STATE_SPAWNING)  # need spawn too
        _run(CmdSpawn, c, "hq")
        self.assertEqual(c.db.player_state, PLAYER_STATE_LOBBY)

    def test_spawn_then_class_advances_to_lobby(self):
        c = _Caller(state=PLAYER_STATE_SPAWNING, classes=_CLASSES)
        _run(CmdSpawn, c, "random")
        self.assertEqual(c.db.player_state, PLAYER_STATE_SPAWNING)
        _run(CmdClass, c, "Engineer")
        self.assertEqual(c.db.player_state, PLAYER_STATE_LOBBY)


# -------------------------------------------------------------- #
#  Bare-number selection (CmdSelect) — the numbered wizard
# -------------------------------------------------------------- #

class TestCmdSelect(unittest.TestCase):
    def test_bare_number_picks_class_then_spawn_in_order(self):
        # A player types '1' then '1' — first pick is a class, second a spawn.
        c = _Caller(state=PLAYER_STATE_SPAWNING, classes=_CLASSES)
        _run(CmdSelect, c, args="", cmdstring="1")  # bare '1' -> class[0]
        self.assertEqual(c.db.player_class, "engineer")
        self.assertEqual(c.db.player_state, PLAYER_STATE_SPAWNING)  # spawn still needed
        _run(CmdSelect, c, args="", cmdstring="1")  # bare '1' -> spawn[0] = respawn
        self.assertEqual(c.db.pending_spawn_choice, "respawn")
        self.assertEqual(c.db.player_state, PLAYER_STATE_LOBBY)

    def test_select_with_arg_form(self):
        c = _Caller(state=PLAYER_STATE_SPAWNING, classes=_CLASSES)
        _run(CmdSelect, c, args="2", cmdstring="select")  # class[1] = vanguard
        self.assertEqual(c.db.player_class, "vanguard")

    def test_select_noop_outside_spawning(self):
        c = _Caller(state=PLAYER_STATE_PLAYING, classes=_CLASSES)
        _run(CmdSelect, c, args="", cmdstring="1")
        self.assertIsNone(c.db.player_class)
        self.assertIn("nothing to select", c.last().lower())

    def test_select_non_number_reprompts_current_step(self):
        c = _Caller(state=PLAYER_STATE_SPAWNING, classes=_CLASSES)
        _run(CmdSelect, c, args="", cmdstring="select")  # no number given
        # Reprompts the class menu (still on step 1).
        self.assertIn("class", c.last().lower())
        self.assertIsNone(c.db.player_class)

    def test_lobby_select_1_enters_game(self):
        c = _Caller(state=PLAYER_STATE_LOBBY)
        c.db.player_class = "vanguard"
        c.db.pending_spawn_choice = "hq"
        _run(CmdSelect, c, args="", cmdstring="1")
        self.assertEqual(c.db.player_state, PLAYER_STATE_PLAYING)

    def test_lobby_select_0_quits(self):
        c = _Caller(state=PLAYER_STATE_LOBBY)
        _run(CmdSelect, c, args="", cmdstring="0")
        self.assertIn("quit", c._executed)  # routed to the quit command
        self.assertEqual(c.db.player_state, PLAYER_STATE_LOBBY)  # still lobby

    def test_lobby_select_0_forwards_session_to_quit(self):
        # The quit must carry the invoking session, or CmdQuit (account_caller)
        # crashes on a None session (the reported lobby-quit traceback).
        c = _Caller(state=PLAYER_STATE_LOBBY)
        sentinel_session = object()
        _run(CmdSelect, c, args="", cmdstring="0", session=sentinel_session)
        self.assertEqual(c._executed, ["quit"])
        self.assertEqual(c._executed_sessions, [sentinel_session])

    def test_lobby_select_2_shows_password_hint(self):
        c = _Caller(state=PLAYER_STATE_LOBBY)
        _run(CmdSelect, c, args="", cmdstring="2")
        self.assertIn("password", c.last().lower())
        self.assertEqual(c.db.player_state, PLAYER_STATE_LOBBY)

    def test_lobby_select_3_shows_chardelete_hint(self):
        c = _Caller(state=PLAYER_STATE_LOBBY)
        _run(CmdSelect, c, args="", cmdstring="3")
        self.assertIn("chardelete", c.last().lower())
        self.assertEqual(c.db.player_state, PLAYER_STATE_LOBBY)

    def test_lobby_select_other_reprompts_menu(self):
        c = _Caller(state=PLAYER_STATE_LOBBY)
        _run(CmdSelect, c, args="", cmdstring="7")
        self.assertIn("enter the game", c.last().lower())
        self.assertEqual(c.db.player_state, PLAYER_STATE_LOBBY)


# -------------------------------------------------------------- #
#  relocation transaction
# -------------------------------------------------------------- #

class TestRelocateTransaction(unittest.TestCase):
    @staticmethod
    def _cross_room_case():
        c = _Caller(state=PLAYER_STATE_LOBBY)
        origin = _IndexedRoom()
        destination = _IndexedRoom()
        c.location = origin
        c.db.coord_x = 1
        c.db.coord_y = 2
        c.db.coord_planet = "mars"
        origin.ndb._coord_index.add(c, 1, 2)
        _install_systems({"planet_rooms": {"terra": destination}})
        return c, origin, destination

    def _assert_rolled_back(self, c, origin, destination):
        self.assertIs(c.location, origin)
        self.assertEqual(
            (c.db.coord_x, c.db.coord_y, c.db.coord_planet),
            (1, 2, "mars"),
        )
        self.assertIn(c, origin.ndb._coord_index.get_at(1, 2))
        self.assertNotIn(c, destination.ndb._coord_index.get_at(1, 2))
        self.assertNotIn(c, destination.ndb._coord_index.get_at(7, 9))
        self.assertEqual(len(c.move_to_calls), 2)
        self.assertIs(c.move_to_calls[0][0], destination)
        self.assertIs(c.move_to_calls[1][0], origin)
        for _room, kwargs in c.move_to_calls:
            self.assertTrue(kwargs["quiet"])
            self.assertFalse(kwargs["move_hooks"])

    def test_destination_failure_after_mutation_rolls_back_everything(self):
        for outcome in ("false", "exception"):
            with self.subTest(outcome=outcome):
                c, origin, destination = self._cross_room_case()
                if outcome == "false":
                    destination.move_entity_result = False
                else:
                    destination.move_entity_error = RuntimeError(
                        "failed after destination index move"
                    )

                self.assertFalse(_relocate(c, "terra", 7, 9))

                self._assert_rolled_back(c, origin, destination)

    def test_origin_cleanup_failure_rolls_back_destination(self):
        for outcome in ("false", "exception"):
            with self.subTest(outcome=outcome):
                c, origin, destination = self._cross_room_case()
                origin.ndb._coord_index.fail_next_remove = outcome

                self.assertFalse(_relocate(c, "terra", 7, 9))

                self._assert_rolled_back(c, origin, destination)

    def test_stowed_origin_is_restored_after_partial_failure(self):
        c = _Caller(state=PLAYER_STATE_LOBBY)
        destination = _IndexedRoom()
        destination.move_entity_error = RuntimeError("partial destination move")
        c.db.coord_x = 3
        c.db.coord_y = 4
        c.db.coord_planet = "mars"
        _install_systems({"planet_rooms": {"terra": destination}})

        self.assertFalse(_relocate(c, "terra", 7, 9))

        self.assertIsNone(c.location)
        self.assertEqual(
            (c.db.coord_x, c.db.coord_y, c.db.coord_planet),
            (3, 4, "mars"),
        )
        self.assertNotIn(c, destination.ndb._coord_index.get_at(3, 4))
        self.assertNotIn(c, destination.ndb._coord_index.get_at(7, 9))
        self.assertIsNone(c.move_to_calls[-1][0])
        self.assertTrue(c.move_to_calls[-1][1]["to_none"])

    def test_none_returning_mutators_commit_successfully(self):
        c, origin, destination = self._cross_room_case()
        c.move_to_result = None

        self.assertTrue(_relocate(c, "terra", 7, 9))

        self.assertIs(c.location, destination)
        self.assertEqual(
            (c.db.coord_x, c.db.coord_y, c.db.coord_planet),
            (7, 9, "terra"),
        )
        self.assertNotIn(c, origin.ndb._coord_index.get_at(1, 2))
        self.assertIn(c, destination.ndb._coord_index.get_at(7, 9))


# -------------------------------------------------------------- #
#  apply/deploy relocation safety
# -------------------------------------------------------------- #

class TestApplySpawnChoice(unittest.TestCase):
    def test_every_concrete_relocation_failure_is_propagated(self):
        cases = (
            ("resolved choice", "hq", _FakeResolver({"hq"})),
            ("choice fallback", "random", None),
            ("clean re-entry", None, None),
        )
        for label, choice, resolver in cases:
            with self.subTest(case=label):
                c = _Caller(state=PLAYER_STATE_LOBBY)
                c.db.pending_spawn_choice = choice
                systems = {"planet_rooms": {}}
                if resolver is not None:
                    systems["spawn_resolver"] = resolver
                _install_systems(systems)

                self.assertFalse(apply_spawn_choice(c))
                self.assertEqual(c.db.pending_spawn_choice, choice)
                self.assertIsNone(c.location)


# -------------------------------------------------------------- #
#  deploy (4.1)
# -------------------------------------------------------------- #

class TestCmdDeploy(unittest.TestCase):
    def test_deploy_from_lobby_enters_game(self):
        c = _Caller(state=PLAYER_STATE_LOBBY)
        c.db.player_class = "vanguard"
        c.db.pending_spawn_choice = "hq"
        _run(CmdDeploy, c, "")
        self.assertEqual(c.db.player_state, PLAYER_STATE_PLAYING)
        self.assertIn("look", c._executed)  # world shown on deploy
        self.assertIsNone(c.db.pending_spawn_choice)  # choice consumed

    def test_deploy_clears_lingering_combat_state(self):
        # A player who died/quit mid-fight must re-enter NOT in combat.
        c = _Caller(state=PLAYER_STATE_LOBBY)
        c.db.player_class = "vanguard"
        c.db.pending_spawn_choice = "hq"
        c.db.combat_timer_expires = 9999
        c.db.combat_lockout_tick = 9999
        _run(CmdDeploy, c, "")
        self.assertEqual(c.db.combat_timer_expires, 0)
        self.assertEqual(c.db.combat_lockout_tick, 0)

    def test_missing_planet_room_cancels_deploy(self):
        c = _Caller(state=PLAYER_STATE_LOBBY, classes=_CLASSES)
        c.db.player_class = "vanguard"
        c.db.pending_spawn_choice = "hq"
        c.db.combat_timer_expires = 55
        c.db.combat_lockout_tick = 66
        c.ndb._clean_quit = True
        _install_systems({
            "spawn_resolver": _FakeResolver({"hq"}),
            "planet_rooms": {},
        })

        self.assertFalse(deploy_from_lobby(c))

        self.assertEqual(c.db.player_state, PLAYER_STATE_SPAWNING)
        self.assertIsNone(c.db.pending_spawn_choice)
        self.assertEqual(c.db.combat_timer_expires, 55)
        self.assertEqual(c.db.combat_lockout_tick, 66)
        self.assertTrue(c.ndb._clean_quit)
        self.assertNotIn("look", c._executed)
        self.assertIsNone(c.location)
        # The HQ was still there; placing the player failed. Saying the HQ is
        # gone would be a false diagnosis of a server-side failure.
        joined = " ".join(c._messages).lower()
        self.assertIn("could not be placed", joined)
        self.assertNotIn("no longer available", joined)

    def test_move_to_rejection_cancels_deploy_before_indexing(self):
        c = _Caller(state=PLAYER_STATE_LOBBY, classes=_CLASSES)
        c.db.player_class = "vanguard"
        c.db.pending_spawn_choice = "hq"
        origin = object()
        c.location = origin
        c.move_to_result = False
        _install_systems({"spawn_resolver": _FakeResolver({"hq"})})

        self.assertFalse(deploy_from_lobby(c))

        self.assertEqual(c.db.player_state, PLAYER_STATE_SPAWNING)
        self.assertIs(c.location, origin)
        self.assertEqual(c._room.move_entity_calls, [])
        self.assertNotIn("look", c._executed)

    def test_index_rejection_or_exception_cancels_deploy(self):
        for outcome in ("false", "exception"):
            with self.subTest(outcome=outcome):
                c = _Caller(state=PLAYER_STATE_LOBBY, classes=_CLASSES)
                c.db.player_class = "vanguard"
                c.db.pending_spawn_choice = "hq"
                c.location = c._room
                if outcome == "false":
                    c._room.move_entity_result = False
                else:
                    c._room.move_entity_error = RuntimeError("index failed")
                _install_systems({"spawn_resolver": _FakeResolver({"hq"})})

                self.assertFalse(deploy_from_lobby(c))

                self.assertEqual(c.db.player_state, PLAYER_STATE_SPAWNING)
                self.assertEqual((c.db.coord_x, c.db.coord_y), (1, 1))
                self.assertIs(c.location, c._room)
                self.assertNotIn("look", c._executed)

    def test_named_choice_disappearing_before_deploy_returns_to_spawning(self):
        for choice in ("respawn", "hq", "death"):
            with self.subTest(choice=choice):
                c = _Caller(state=PLAYER_STATE_SPAWNING, classes=_CLASSES)
                c.db.player_class = "vanguard"
                resolver = _FakeResolver({choice, "random"})
                _install_systems({"spawn_resolver": resolver})

                _run(CmdSpawn, c, choice)
                self.assertEqual(c.db.player_state, PLAYER_STATE_LOBBY)
                resolver._available.remove(choice)
                _run(CmdDeploy, c, "")

                self.assertEqual(c.db.player_state, PLAYER_STATE_SPAWNING)
                self.assertIsNone(c.db.pending_spawn_choice)
                self.assertNotIn("look", c._executed)
                self.assertTrue(
                    any("no longer available" in m.lower() for m in c._messages)
                )
                self.assertIn("choose your spawn point", c.last().lower())

    def test_target_lost_between_revalidation_and_resolution_is_reprompted(self):
        class _VanishingResolver(_FakeResolver):
            def resolve(self, player, choice, planet_key):
                return None

        c = _Caller(state=PLAYER_STATE_LOBBY, classes=_CLASSES)
        c.db.player_class = "vanguard"
        c.db.pending_spawn_choice = "hq"
        _install_systems({"spawn_resolver": _VanishingResolver({"hq"})})

        _run(CmdDeploy, c, "")

        self.assertEqual(c.db.player_state, PLAYER_STATE_SPAWNING)
        self.assertIsNone(c.db.pending_spawn_choice)
        self.assertNotIn("look", c._executed)

    def test_deploy_blocked_while_spawning(self):
        c = _Caller(state=PLAYER_STATE_SPAWNING)
        _run(CmdDeploy, c, "")
        self.assertEqual(c.db.player_state, PLAYER_STATE_SPAWNING)
        self.assertIn("class", c.last().lower())

    def test_deploy_when_already_playing(self):
        c = _Caller(state=PLAYER_STATE_PLAYING)
        _run(CmdDeploy, c, "")
        self.assertIn("already in the game", c.last().lower())


if __name__ == "__main__":
    unittest.main()
