"""Unit tests for the new-player XP-feedback surface.

Covers the pure XP-progress helpers (:func:`world.ui_formatters.format_xp_bar`,
:func:`world.ui_formatters.xp_progress`), the XP fields the status prompt folds
into its line, and the prompt's column budget. These are the visible-XP and
XP-bar pieces of the first-hour dopamine pass.

Imports deliberately go through ``world.*`` rather than ``mygame.world.*``:
those are DISTINCT module objects with separate threshold caches, and the code
under test (``status_prompt``, ``ui_formatters``) imports through ``world.*``.
Importing the other spelling here would exercise a different copy of
``progression`` than the code reads, so the curve a test builds would not be the
curve the assertion measures.
"""

import re

from world import progression, status_prompt
from world.ui_formatters import format_xp_bar, xp_progress


def _reset_curve():
    """Build the level<->XP curve the code under test actually reads."""
    progression.build_thresholds()


def _plain(text: str) -> str:
    """Strip Evennia colour markup so assertions test text, not markup."""
    return re.sub(r"\|(?:\[)?[a-zA-Z0-9]", "", text)


# ------------------------------------------------------------------ #
#  format_xp_bar — pure renderer
# ------------------------------------------------------------------ #

class TestFormatXpBar:
    def test_empty_bar_is_all_dots(self):
        bar = format_xp_bar(0, width=10)
        assert "0%" in bar
        assert "\u2588" not in bar  # no filled cells
        assert "." * 10 in bar

    def test_full_bar_is_all_blocks(self):
        bar = format_xp_bar(100, width=10)
        assert "100%" in bar
        assert "\u2588" * 10 in bar

    def test_half_bar_splits(self):
        bar = format_xp_bar(50, width=10)
        assert "\u2588" * 5 in bar
        assert "." * 5 in bar
        assert "50%" in bar

    def test_nearly_full_bar_is_not_full(self):
        """A 97% player must NOT see a completely filled bar (floor, not
        round) — otherwise they'd wonder why they hadn't levelled."""
        bar = format_xp_bar(97, width=20)
        assert "\u2588" * 20 not in bar
        assert "." in bar  # at least one empty cell remains

    def test_bare_mode_drops_brackets_and_percent(self):
        bare = format_xp_bar(50, width=6, bare=True)
        assert "%" not in bare
        assert "[" not in _plain(bare)

    def test_clamps_out_of_range(self):
        assert "100%" in format_xp_bar(250, width=8)
        assert "0%" in format_xp_bar(-10, width=8)

    def test_bad_input_degrades_to_zero(self):
        # Never raises on garbage.
        assert "0%" in format_xp_bar(None, width=8)  # type: ignore[arg-type]


# ------------------------------------------------------------------ #
#  xp_progress — total XP -> in-level progress
# ------------------------------------------------------------------ #

class _DB:
    def __init__(self, combat_xp, level):
        self.combat_xp = combat_xp
        self.level = level


class _Player:
    def __init__(self, combat_xp, level):
        self.db = _DB(combat_xp, level)


class TestXpProgress:
    def setup_method(self):
        _reset_curve()

    def test_progress_at_level_start_is_zero(self):
        # A player sitting exactly on their current level's threshold.
        start = progression.xp_for_level(3)
        prog = xp_progress(_Player(combat_xp=start, level=3))
        assert prog is not None
        assert prog["level"] == 3
        assert prog["into_level"] == 0
        assert prog["percent"] == 0.0
        assert prog["level_start_xp"] == start
        assert prog["next_level_xp"] == progression.xp_for_level(4)

    def test_progress_midway(self):
        start = progression.xp_for_level(3)
        nxt = progression.xp_for_level(4)
        span = nxt - start
        mid = start + span // 2
        prog = xp_progress(_Player(combat_xp=mid, level=3))
        assert prog is not None
        assert prog["into_level"] == span // 2
        assert prog["level_span"] == span
        # Exact, not a loose band: percent is fully determined by the curve.
        assert prog["percent"] == (span // 2) / span * 100.0

    def test_into_level_is_clamped_when_level_lags_xp(self):
        """db.level can lag combat_xp between an award and its sync; the
        in-level numerator must not exceed the span (no '340/297')."""
        start = progression.xp_for_level(3)
        nxt = progression.xp_for_level(4)
        # XP well past the next threshold while level still reads 3.
        prog = xp_progress(_Player(combat_xp=nxt + 500, level=3))
        assert prog is not None
        assert prog["into_level"] <= prog["level_span"]
        assert prog["percent"] == 100.0

    def test_maxed_player_returns_none(self):
        from mygame.world.constants import MAX_LEVEL
        top = progression.xp_for_level(MAX_LEVEL)
        assert xp_progress(_Player(combat_xp=top, level=MAX_LEVEL)) is None

    def test_no_db_returns_none(self):
        assert xp_progress(object()) is None


# ------------------------------------------------------------------ #
#  status_prompt — XP fields + bar segment
# ------------------------------------------------------------------ #

class _PromptDB:
    def __init__(self, combat_xp, level):
        self.hp = 80
        self.hp_max = 100
        self.level = level
        self.combat_xp = combat_xp
        self.coord_x = 5
        self.coord_y = 6
        self.coord_planet = "terra"
        self.inside_building = False
        self.combat_timer_expires = 0


class _PromptPlayer:
    def __init__(self, combat_xp, level):
        self.db = _PromptDB(combat_xp, level)
        self.location = None


class TestStatusPromptXp:
    def setup_method(self):
        _reset_curve()

    def test_status_fields_carry_xp_progress(self):
        start = progression.xp_for_level(3)
        nxt = progression.xp_for_level(4)
        mid = start + (nxt - start) // 2
        fields = status_prompt.status_fields(_PromptPlayer(combat_xp=mid, level=3))
        assert fields is not None
        assert "xp_percent" in fields
        assert "xp_into_level" in fields
        assert "xp_level_span" in fields
        assert fields["xp_level_span"] == nxt - start

    def test_status_line_includes_xp_bar_when_known(self):
        fields = {
            "hp": 80, "hp_max": 100, "level": 3,
            "x": 5, "y": 6, "planet": "terra", "terrain": "",
            "in_combat": False, "combat_secs": 0,
            "xp_percent": 50.0, "xp_into_level": 10, "xp_level_span": 20,
        }
        line = status_prompt.format_status_line(fields)
        assert "Lv 3" in line
        # Half of a width-6 bar: 3 filled cells and 3 empty, glyphs only.
        assert "\u2588" * 3 in line
        assert "..." in line
        assert "%" not in line  # bare in the prompt — percent is score-only

    def test_status_line_omits_bar_in_combat(self):
        """The combat segment takes the remaining column budget, so the bar
        stands down while fighting."""
        fields = {
            "hp": 80, "hp_max": 100, "level": 3,
            "x": 5, "y": 6, "planet": "terra", "terrain": "Plains",
            "in_combat": True, "combat_secs": 30,
            "xp_percent": 50.0, "xp_into_level": 10, "xp_level_span": 20,
        }
        line = status_prompt.format_status_line(fields)
        assert "\u2588" not in line
        assert "!Combat" in line

    def test_status_line_without_xp_shows_plain_level(self):
        # A maxed player omits xp_percent; the level segment is plain.
        fields = {
            "hp": 80, "hp_max": 100, "level": 100,
            "x": 5, "y": 6, "planet": "terra", "terrain": "",
            "in_combat": False, "combat_secs": 0,
        }
        line = status_prompt.format_status_line(fields)
        assert "Lv 100" in line
        assert "\u2588" not in line

    def test_status_line_stays_within_80_columns(self):
        """Regression guard: the XP bar must not push the prompt past 80
        columns on realistic worst-case values (long terrain name, 4-digit
        coords, 4-digit HP, 3-digit level)."""
        worst_cases = [
            # Out of combat, everything long, bar shown.
            {"hp": 1000, "hp_max": 1000, "level": 99,
             "x": 999, "y": 999, "planet": "elysium",
             "terrain": "Radiation Zone", "in_combat": False,
             "combat_secs": 0, "xp_percent": 100.0,
             "xp_into_level": 1, "xp_level_span": 1},
            # In combat (bar suppressed) with the combat segment present.
            {"hp": 1000, "hp_max": 1000, "level": 100,
             "x": 999, "y": 999, "planet": "elysium",
             "terrain": "Radiation Zone", "in_combat": True,
             "combat_secs": 120, "xp_percent": 50.0,
             "xp_into_level": 1, "xp_level_span": 2},
        ]
        for fields in worst_cases:
            width = len(_plain(status_prompt.format_status_line(fields)))
            assert width <= 80, (width, fields)
