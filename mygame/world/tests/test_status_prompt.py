"""Unit tests for world.status_prompt — the shared player status line.

Covers the per-channel delivery contract:
  * the PRINTED status line is broadcast to every session (no session=
    targeting); telnet/ssh show it directly, and the webclient's custom_out
    decides per-view whether to render it inline (Text view) or drop it (Map
    view, where the footer shows the same fields);
  * the OOB channels (prompt=, prompt_status=) always go out;
  * push_status sends ONLY the OOB channels (no printed line) for a
    server-driven HP change.
"""

from mygame.world import status_prompt


class _FakeDB:
    def __init__(self, **kw):
        self.hp = kw.get("hp", 80)
        self.hp_max = kw.get("hp_max", 100)
        self.level = kw.get("level", 3)
        self.coord_x = kw.get("coord_x", 5)
        self.coord_y = kw.get("coord_y", 6)
        self.coord_planet = kw.get("coord_planet", "terra")
        self.inside_building = False
        self.combat_timer_expires = kw.get("combat_timer_expires", 0)


class _FakeSession:
    def __init__(self, protocol_key):
        self.protocol_key = protocol_key


class _FakeSessions:
    def __init__(self, sessions):
        self._sessions = sessions

    def all(self):
        return list(self._sessions)


class _FakePlayer:
    """Player whose msg() records text + kwargs and the session it targeted."""

    def __init__(self, sessions=None, **db):
        self.db = _FakeDB(**db)
        self.ndb = type("NDB", (), {})()
        # texts: list of (text, kwargs_dict, session_arg)
        self.texts = []
        self.prompts = []
        self.prompt_status = []
        if sessions is not None:
            self.sessions = _FakeSessions(sessions)

    def msg(self, text=None, session=None, **kwargs):
        if text is not None:
            body = text[0] if isinstance(text, tuple) else text
            meta = text[1] if isinstance(text, tuple) else {}
            self.texts.append((body, meta, session))
        if "prompt" in kwargs:
            self.prompts.append(kwargs["prompt"])
        if "prompt_status" in kwargs:
            self.prompt_status.append(kwargs["prompt_status"])


class TestStatusFields:
    def test_fields_snapshot(self):
        p = _FakePlayer(sessions=[], hp=40, hp_max=200, level=7)
        # No sessions → still produces fields (fields don't depend on sessions).
        f = status_prompt.status_fields(p)
        assert f["hp"] == 40 and f["hp_max"] == 200 and f["level"] == 7
        assert f["x"] == 5 and f["y"] == 6 and f["planet"] == "terra"

    def test_none_without_position(self):
        p = _FakePlayer(sessions=[], coord_planet="")
        assert status_prompt.status_fields(p) is None


class TestFormatStatusLine:
    def test_contains_hp_level_coords(self):
        f = {"hp": 80, "hp_max": 100, "level": 4, "x": 5, "y": 6,
             "planet": "terra", "terrain": "Plains"}
        line = status_prompt.format_status_line(f)
        assert "HP" in line and "80/100" in line
        assert "Lv 4" in line and "(5,6)" in line and "Plains" in line

    def test_low_hp_red(self):
        f = {"hp": 10, "hp_max": 100, "level": 1, "x": 0, "y": 0,
             "planet": "terra", "terrain": ""}
        assert "|r" in status_prompt.format_status_line(f)

    def test_combat_segment_shown_when_in_combat(self):
        f = {"hp": 80, "hp_max": 100, "level": 4, "x": 5, "y": 6,
             "planet": "terra", "terrain": "Plains",
             "in_combat": True, "combat_secs": 42}
        line = status_prompt.format_status_line(f)
        assert "Combat" in line and "42s" in line

    def test_combat_segment_absent_when_not_in_combat(self):
        f = {"hp": 80, "hp_max": 100, "level": 4, "x": 5, "y": 6,
             "planet": "terra", "terrain": "Plains",
             "in_combat": False, "combat_secs": 0}
        line = status_prompt.format_status_line(f)
        assert "Combat" not in line

    def test_combat_segment_absent_when_fields_missing(self):
        # Legacy callers that don't supply the combat keys still format fine.
        f = {"hp": 80, "hp_max": 100, "level": 4, "x": 5, "y": 6,
             "planet": "terra", "terrain": "Plains"}
        line = status_prompt.format_status_line(f)
        assert "Combat" not in line


class TestCombatState:
    def test_fields_report_in_combat_with_future_expiry(self):
        # _get_current_tick() returns 0 in the unit-test env (no game_tick
        # script), so any positive expiry reads as a future combat timer.
        p = _FakePlayer(sessions=[], combat_timer_expires=30)
        f = status_prompt.status_fields(p)
        assert f["in_combat"] is True
        assert f["combat_secs"] >= 1

    def test_fields_report_out_of_combat_with_zero_expiry(self):
        p = _FakePlayer(sessions=[], combat_timer_expires=0)
        f = status_prompt.status_fields(p)
        assert f["in_combat"] is False
        assert f["combat_secs"] == 0


class TestSendStatusChannels:
    def test_printed_line_broadcast_to_all_sessions(self):
        # The printed line is broadcast (no session= targeting) so EVERY client
        # receives it; per-client presentation is decided client-side (telnet
        # shows it; the webclient renders it inline in Text view, drops it in Map
        # view). This is the regression guard for "prompt vanished on the client".
        telnet = _FakeSession("telnet")
        web = _FakeSession("webclient/websocket")
        p = _FakePlayer(sessions=[telnet, web])
        status_prompt.send_status(p)
        printed = [t for t in p.texts if t[1].get("cls") == "prompt-line"]
        assert len(printed) == 1
        # Broadcast: no session= targeting (None), not a filtered subset.
        assert printed[0][2] is None
        # Leading newline sets the prompt apart from the command's own output
        # (blank line on telnet, <br> in the webclient's inline Text view).
        assert printed[0][0].startswith("\n")

    def test_webclient_only_still_gets_printed_line(self):
        # Even a webclient-only connection gets the broadcast printed line — the
        # webclient decides per-view whether to render it (Text) or drop it (Map).
        web = _FakeSession("webclient/ajax")
        p = _FakePlayer(sessions=[web])
        status_prompt.send_status(p)
        printed = [t for t in p.texts if t[1].get("cls") == "prompt-line"]
        assert len(printed) == 1
        assert printed[0][2] is None
        # The OOB channels still fire (webclient footer uses prompt_status).
        assert len(p.prompt_status) == 1

    def test_no_session_handler_still_prints(self):
        # A test double / object with no sessions handler still gets the printed
        # line (plain broadcast) so telnet-style captures keep working.
        p = _FakePlayer(sessions=None)
        status_prompt.send_status(p)
        printed = [t for t in p.texts if t[1].get("cls") == "prompt-line"]
        assert len(printed) == 1
        assert printed[0][2] is None  # no session= targeting

    def test_send_status_emits_both_oob(self):
        p = _FakePlayer(sessions=[_FakeSession("telnet")])
        status_prompt.send_status(p)
        assert len(p.prompts) == 1
        assert len(p.prompt_status) == 1
        # The bare prompt= carries no leading newline — prompt-aware clients
        # render it in a dedicated area, not the scrollback.
        assert not p.prompts[0].startswith("\n")


class TestPushStatus:
    def test_push_sends_oob_only_no_printed_line(self):
        p = _FakePlayer(sessions=[_FakeSession("telnet")])
        status_prompt.push_status(p)
        # push_status is the live server-driven refresh — OOB only, so incoming
        # combat doesn't spam the telnet scrollback with a status line per hit.
        assert p.texts == []
        assert len(p.prompts) == 1
        assert len(p.prompt_status) == 1

    def test_push_noop_without_position(self):
        p = _FakePlayer(sessions=[_FakeSession("telnet")], coord_planet="")
        status_prompt.push_status(p)
        assert p.prompts == [] and p.prompt_status == []
