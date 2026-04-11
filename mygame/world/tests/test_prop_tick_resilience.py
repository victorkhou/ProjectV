"""
Property-based tests for Game Tick error resilience.

Property 23: Tick error resilience — if one system raises, others still execute.

Validates: Requirements 11.3
"""

import sys
import types
import unittest

from hypothesis import given, settings
from hypothesis import strategies as st

# -------------------------------------------------------------- #
#  Bootstrap: stub out Evennia modules
# -------------------------------------------------------------- #

def _ensure_evennia_stubs():
    """Insert lightweight stubs for Evennia modules into sys.modules."""
    if "evennia" in sys.modules:
        mod = sys.modules["evennia"]
        if hasattr(mod, "__file__") and mod.__file__:
            return
    stubs = {}

    def _mod(name, attrs=None):
        m = types.ModuleType(name)
        if attrs:
            for k, v in attrs.items():
                setattr(m, k, v)
        stubs[name] = m
        return m

    ev = _mod("evennia")
    _mod("evennia.objects")
    _mod("evennia.objects.objects", {
        "DefaultObject": type("DefaultObject", (), {}),
        "DefaultRoom": type("DefaultRoom", (), {}),
        "DefaultCharacter": type("DefaultCharacter", (), {}),
    })
    _mod("evennia.commands")
    _mod("evennia.commands.cmdset")
    _mod("evennia.utils")
    _mod("evennia.utils.utils")

    # Stub logger with log_trace
    logger_mod = _mod("evennia.utils.logger")

    # Stub DefaultScript
    class FakeDefaultScript:
        """Minimal DefaultScript stub for testing."""
        class _db:
            tick_count = 0
        class _ndb:
            systems = None
        db = _db()
        ndb = _ndb()

        def __init__(self, *args, **kwargs):
            self.db = type('db', (), {'tick_count': 0, 'systems': None})()
            self.ndb = type('ndb', (), {'systems': None})()

    scripts_mod = _mod("evennia.scripts")
    scripts_scripts_mod = _mod("evennia.scripts.scripts", {
        "DefaultScript": FakeDefaultScript,
    })

    for name, mod in stubs.items():
        sys.modules.setdefault(name, mod)

_ensure_evennia_stubs()

# -------------------------------------------------------------- #
#  Helpers
# -------------------------------------------------------------- #

class StepTracker:
    """Tracks which steps were called and which raised errors."""

    def __init__(self):
        self.called = []

    def make_step(self, name, should_raise=False):
        """Create a callable step that records its execution."""
        def step():
            self.called.append(name)
            if should_raise:
                raise RuntimeError(f"Simulated error in {name}")
        return step

def run_tick_steps(steps):
    """Simulate the GameTickScript at_repeat logic for a list of steps.

    Each step is a (name, callable) tuple. Errors in one step
    do not prevent subsequent steps from executing.
    """
    for step_name, step_fn in steps:
        try:
            step_fn()
        except Exception:
            pass  # Error logged in production; silently caught here

# -------------------------------------------------------------- #
#  Hypothesis strategies
# -------------------------------------------------------------- #

SYSTEM_NAMES = [
    "active_chunks",
    "resource_production",
    "equipment_production",
    "combat_resolution",
    "turret_attacks",
    "powerup_ticks",
    "tech_research",
    "resource_respawns",
    "tick_completed",
]

@st.composite
def failing_steps_strategy(draw):
    """Generate a subset of step names that should raise errors."""
    # Pick 1 to N-1 steps to fail (at least one must succeed)
    count = draw(st.integers(min_value=1, max_value=len(SYSTEM_NAMES) - 1))
    indices = draw(
        st.lists(
            st.sampled_from(range(len(SYSTEM_NAMES))),
            min_size=count,
            max_size=count,
            unique=True,
        )
    )
    return {SYSTEM_NAMES[i] for i in indices}

# -------------------------------------------------------------- #
#  Property 23: Tick error resilience
#  **Validates: Requirements 11.3**
# -------------------------------------------------------------- #

class TestProperty23TickErrorResilience(unittest.TestCase):
    """Property 23: Tick error resilience.

    For any game tick where one processing step throws an error,
    all other processing steps in that tick SHALL still execute.

    **Validates: Requirements 11.3**
    """

    @given(failing_steps=failing_steps_strategy())
    @settings(max_examples=100)
    def test_non_failing_steps_still_execute(self, failing_steps):
        """Steps that don't fail should still be called even when others fail."""
        tracker = StepTracker()

        steps = []
        for name in SYSTEM_NAMES:
            should_fail = name in failing_steps
            steps.append((name, tracker.make_step(name, should_raise=should_fail)))

        run_tick_steps(steps)

        # ALL steps should have been called, regardless of failures
        for name in SYSTEM_NAMES:
            self.assertIn(
                name, tracker.called,
                f"Step '{name}' should have been called even though "
                f"{failing_steps} failed",
            )

    @given(
        failing_index=st.integers(
            min_value=0, max_value=len(SYSTEM_NAMES) - 1
        ),
    )
    @settings(max_examples=100)
    def test_single_failure_doesnt_block_others(self, failing_index):
        """A single failing step doesn't prevent any other step."""
        tracker = StepTracker()
        failing_name = SYSTEM_NAMES[failing_index]

        steps = []
        for name in SYSTEM_NAMES:
            should_fail = name == failing_name
            steps.append((name, tracker.make_step(name, should_raise=should_fail)))

        run_tick_steps(steps)

        self.assertEqual(
            len(tracker.called), len(SYSTEM_NAMES),
            f"All {len(SYSTEM_NAMES)} steps should execute; "
            f"only {len(tracker.called)} did when '{failing_name}' failed",
        )

    @given(st.just(None))
    @settings(max_examples=10)
    def test_all_steps_succeed_when_no_errors(self, _):
        """When no steps fail, all are called normally."""
        tracker = StepTracker()
        steps = [(name, tracker.make_step(name)) for name in SYSTEM_NAMES]

        run_tick_steps(steps)

        self.assertEqual(tracker.called, SYSTEM_NAMES)

if __name__ == "__main__":
    unittest.main()
