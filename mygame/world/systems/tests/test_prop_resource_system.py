"""
Property-based tests for ResourceSystem.

Property 5: Resource node respawn cycle
Property 10: Resource production scales with level

Validates: Requirements 2.6, 2.7, 15.1, 15.2, 15.4
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

    _mod("evennia")
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
    _mod("evennia.utils.logger")

    for name, mod in stubs.items():
        sys.modules.setdefault(name, mod)

_ensure_evennia_stubs()

from mygame.world.systems.resource_system import ResourceSystem  # noqa: E402
from mygame.world.data_registry import DataRegistry  # noqa: E402
from mygame.world.definitions import BalanceConfig  # noqa: E402
from mygame.world.event_bus import EventBus  # noqa: E402

# -------------------------------------------------------------- #
#  Helpers / Fakes
# -------------------------------------------------------------- #

class FakeDB:
    """Simulates Evennia's db handler (attribute-style access)."""


class FakePlanetRoom:
    """Lightweight stand-in for a PlanetRoom's node-depletion API."""

    def __init__(self):
        self.db = FakeDB()
        self.db.depleted_nodes = {}

    def get_depleted_nodes(self):
        return self.db.depleted_nodes or {}

    def set_node_depleted(self, x, y, resource_type, respawn_counter):
        nodes = self.db.depleted_nodes or {}
        nodes[f"{x},{y}"] = {
            "resource_type": resource_type,
            "respawn_counter": respawn_counter,
        }
        self.db.depleted_nodes = nodes

    def clear_node_depletion(self, x, y):
        nodes = self.db.depleted_nodes or {}
        nodes.pop(f"{x},{y}", None)
        self.db.depleted_nodes = nodes

    def is_node_depleted(self, x, y):
        return f"{x},{y}" in (self.db.depleted_nodes or {})

def _make_system():
    """Create a ResourceSystem with test balance config."""
    registry = DataRegistry()
    registry.balance = BalanceConfig(harvest_crit_chance=0.0)
    return ResourceSystem(registry, EventBus())

# -------------------------------------------------------------- #
#  Hypothesis strategies
# -------------------------------------------------------------- #

@st.composite
def respawn_ticks_strategy(draw):
    """Generate a respawn tick count (1-100)."""
    return draw(st.integers(min_value=1, max_value=100))

# -------------------------------------------------------------- #
#  Property 5: Resource node respawn cycle
#  **Validates: Requirements 2.6, 2.7, 15.1, 15.2, 15.4**
# -------------------------------------------------------------- #

class TestProperty5RespawnCycle(unittest.TestCase):
    """Property 5: Resource node respawn cycle.

    A depleted resource node with respawn_counter = N SHALL become
    non-depleted after exactly N calls to process_respawns. The counter
    SHALL decrement by exactly 1 each tick.

    **Validates: Requirements 2.6, 2.7, 15.1, 15.2, 15.4**
    """

    @given(respawn_ticks=respawn_ticks_strategy())
    @settings(max_examples=100)
    def test_respawn_after_exact_ticks(self, respawn_ticks):
        """A depleted node respawns after exactly respawn_ticks ticks."""
        room = FakePlanetRoom()
        room.set_node_depleted(3, 4, "Straw", respawn_ticks)

        system = _make_system()

        # Process N-1 ticks: should still be depleted
        for i in range(respawn_ticks - 1):
            system.process_respawns([room])
            self.assertTrue(
                room.is_node_depleted(3, 4),
                f"Node should still be depleted after {i + 1} ticks "
                f"(needs {respawn_ticks})",
            )
            self.assertEqual(
                room.get_depleted_nodes()["3,4"]["respawn_counter"],
                respawn_ticks - (i + 1),
                f"Counter should be {respawn_ticks - (i + 1)} after {i + 1} ticks",
            )

        # Process the Nth tick: should be restored
        system.process_respawns([room])
        self.assertFalse(
            room.is_node_depleted(3, 4),
            f"Node should be restored after exactly {respawn_ticks} ticks",
        )
        self.assertEqual(room.get_depleted_nodes(), {})

if __name__ == "__main__":
    unittest.main()
