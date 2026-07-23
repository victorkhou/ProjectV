"""Unit tests for world.status_prompt — the shared player status line.

Covers the per-channel delivery contract:
  * the PRINTED status line goes to telnet/ssh sessions only (never the
    webclient, which shows the same fields in its map footer);
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


class TestSendStatusChannels:
    def test_printed_line_excludes_webclient_sessions(self):
        telnet = _FakeSession("telnet")
        web = _FakeSession("webclient/websocket")
        p = _FakePlayer(sessions=[telnet, web])
        status_prompt.send_status(p)
        # Exactly one printed line, tagged prompt-line, targeted at the telnet
        # session only (the webclient session is excluded).
        printed = [t for t in p.texts if t[1].get("cls") == "prompt-line"]
        assert len(printed) == 1
        _, _, session_arg = printed[0]
        assert session_arg == [telnet]
        assert web not in session_arg

    def test_unknown_protocol_still_gets_printed_line(self):
        # Fail-open: a session whose protocol can't be positively identified as a
        # webclient STILL gets the printed line (telnet visibility is the whole
        # point — better a stray line than no prompt). This is the regression
        # guard for "prompt vanished on the raw MUD output".
        unknown = _FakeSession("")  # e.g. protocol_key not synced yet
        ssl = _FakeSession("telnet/ssl")
        p = _FakePlayer(sessions=[unknown, ssl])
        status_prompt.send_status(p)
        printed = [t for t in p.texts if t[1].get("cls") == "prompt-line"]
        assert len(printed) == 1
        assert printed[0][2] == [unknown, ssl]

    def test_webclient_only_gets_no_printed_line(self):
        web = _FakeSession("webclient/ajax")
        p = _FakePlayer(sessions=[web])
        status_prompt.send_status(p)
        printed = [t for t in p.texts if t[1].get("cls") == "prompt-line"]
        # Only session is a webclient → excluded → no printed line sent at all.
        assert printed == []
        # But the OOB channels still fire (webclient footer uses prompt_status).
        assert len(p.prompt_status) == 1

    def test_no_session_handler_falls_back_to_plain_print(self):
        # A test double / object with no sessions handler still gets the printed
        # line (plain send) so telnet-style captures keep working.
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
