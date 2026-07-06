"""
Property-based tests for World Chunking.

Property 29: World chunk activation
Property 30: Chunk coordinate assignment

Validates: Requirements 31.1, 31.2, 31.3
"""

import sys
import types
import unittest

from hypothesis import given, settings, assume
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

from mygame.world.chunking import WorldChunkManager  # noqa: E402

# -------------------------------------------------------------- #
#  Helpers / Fakes
# -------------------------------------------------------------- #

class FakePlayer:
    """Lightweight stand-in for a player with a position."""

    def __init__(self, x: int, y: int):
        self.position = (x, y)

class FakeBuilding:
    """Lightweight stand-in for a building with a position."""

    def __init__(self, x: int, y: int):
        self.position = (x, y)

# -------------------------------------------------------------- #
#  Hypothesis strategies
# -------------------------------------------------------------- #

@st.composite
def chunk_size_strategy(draw):
    """Generate a valid chunk size (1-50)."""
    return draw(st.integers(min_value=1, max_value=50))

@st.composite
def coordinate_strategy(draw):
    """Generate a tile coordinate."""
    return draw(st.integers(min_value=-200, max_value=200))

@st.composite
def player_positions_strategy(draw):
    """Generate a list of player positions."""
    count = draw(st.integers(min_value=1, max_value=10))
    positions = []
    for _ in range(count):
        x = draw(st.integers(min_value=-200, max_value=200))
        y = draw(st.integers(min_value=-200, max_value=200))
        positions.append((x, y))
    return positions

# -------------------------------------------------------------- #
#  Property 30: Chunk coordinate assignment
#  **Validates: Requirements 31.1**
# -------------------------------------------------------------- #

class TestProperty30ChunkCoordinateAssignment(unittest.TestCase):
    """Property 30: Chunk coordinate assignment.

    For any tile at coordinates (x, y) and chunk size S,
    the tile SHALL belong to chunk (x // S, y // S).

    **Validates: Requirements 31.1**
    """

    @given(
        x=st.integers(min_value=-500, max_value=500),
        y=st.integers(min_value=-500, max_value=500),
        chunk_size=chunk_size_strategy(),
    )
    @settings(max_examples=100)
    def test_chunk_coord_is_floor_division(self, x, y, chunk_size):
        """Chunk coordinate equals floor division of tile coord by chunk size."""
        mgr = WorldChunkManager(chunk_size=chunk_size)
        cx, cy = mgr.get_chunk_coord(x, y)
        self.assertEqual(cx, x // chunk_size)
        self.assertEqual(cy, y // chunk_size)

    @given(
        x=st.integers(min_value=-500, max_value=500),
        y=st.integers(min_value=-500, max_value=500),
        chunk_size=chunk_size_strategy(),
    )
    @settings(max_examples=100)
    def test_tiles_in_same_chunk_share_chunk_coord(self, x, y, chunk_size):
        """All tiles within the same chunk_size block share the same chunk coord."""
        mgr = WorldChunkManager(chunk_size=chunk_size)
        base_cx, base_cy = mgr.get_chunk_coord(x, y)

        # Any tile in the same chunk block should have the same chunk coord
        # The block starts at (base_cx * chunk_size, base_cy * chunk_size)
        block_start_x = base_cx * chunk_size
        block_start_y = base_cy * chunk_size

        # Verify the original tile is within this block
        self.assertGreaterEqual(x, block_start_x)
        self.assertLess(x, block_start_x + chunk_size)
        self.assertGreaterEqual(y, block_start_y)
        self.assertLess(y, block_start_y + chunk_size)

# -------------------------------------------------------------- #
#  Property 29: World chunk activation
#  **Validates: Requirements 31.2, 31.3**
# -------------------------------------------------------------- #

class TestProperty29WorldChunkActivation(unittest.TestCase):
    """Property 29: World chunk activation.

    For any set of online player positions and a given chunk size,
    a chunk SHALL be active if and only if at least one player is
    located within the chunk or within one chunk radius of it.

    **Validates: Requirements 31.2, 31.3**
    """

    @given(
        positions=player_positions_strategy(),
        chunk_size=chunk_size_strategy(),
    )
    @settings(max_examples=100)
    def test_player_chunk_is_active(self, positions, chunk_size):
        """The chunk containing each player is always active."""
        mgr = WorldChunkManager(chunk_size=chunk_size)
        players = [FakePlayer(x, y) for x, y in positions]
        active = mgr.get_active_chunks("test_planet", players)

        for x, y in positions:
            chunk = mgr.get_chunk_coord(x, y)
            self.assertIn(
                chunk, active,
                f"Player at ({x},{y}) -> chunk {chunk} should be active",
            )

    @given(
        positions=player_positions_strategy(),
        chunk_size=chunk_size_strategy(),
    )
    @settings(max_examples=100)
    def test_neighbor_chunks_are_active(self, positions, chunk_size):
        """All chunks within 1 radius of a player's chunk are active."""
        mgr = WorldChunkManager(chunk_size=chunk_size)
        players = [FakePlayer(x, y) for x, y in positions]
        active = mgr.get_active_chunks("test_planet", players)

        for x, y in positions:
            cx, cy = mgr.get_chunk_coord(x, y)
            for dx in range(-1, 2):
                for dy in range(-1, 2):
                    neighbor = (cx + dx, cy + dy)
                    self.assertIn(
                        neighbor, active,
                        f"Neighbor {neighbor} of player chunk ({cx},{cy}) "
                        f"should be active",
                    )

    @given(
        positions=player_positions_strategy(),
        chunk_size=chunk_size_strategy(),
    )
    @settings(max_examples=100)
    def test_active_chunks_only_near_players(self, positions, chunk_size):
        """Every active chunk is within 1 radius of some player's chunk."""
        mgr = WorldChunkManager(chunk_size=chunk_size)
        players = [FakePlayer(x, y) for x, y in positions]
        active = mgr.get_active_chunks("test_planet", players)

        player_chunks = set()
        for x, y in positions:
            player_chunks.add(mgr.get_chunk_coord(x, y))

        for chunk in active:
            # This chunk must be within 1 radius of at least one player chunk
            near_some_player = False
            for pcx, pcy in player_chunks:
                if abs(chunk[0] - pcx) <= 1 and abs(chunk[1] - pcy) <= 1:
                    near_some_player = True
                    break
            self.assertTrue(
                near_some_player,
                f"Active chunk {chunk} is not near any player chunk",
            )

    @given(
        chunk_size=chunk_size_strategy(),
    )
    @settings(max_examples=50)
    def test_no_players_means_no_active_chunks(self, chunk_size):
        """With no online players, no chunks are active."""
        mgr = WorldChunkManager(chunk_size=chunk_size)
        active = mgr.get_active_chunks("test_planet", [])
        self.assertEqual(len(active), 0)

    @given(
        positions=player_positions_strategy(),
        chunk_size=chunk_size_strategy(),
    )
    @settings(max_examples=100)
    def test_buildings_filtered_by_active_chunks(self, positions, chunk_size):
        """get_buildings_in_chunks only returns buildings in active chunks."""
        mgr = WorldChunkManager(chunk_size=chunk_size)
        players = [FakePlayer(x, y) for x, y in positions]
        active = mgr.get_active_chunks("test_planet", players)

        # Create buildings both inside and outside active chunks
        all_buildings = []
        for x, y in positions:
            all_buildings.append(FakeBuilding(x, y))
        far_x = max(p[0] for p in positions) + chunk_size * 5
        far_y = max(p[1] for p in positions) + chunk_size * 5
        all_buildings.append(FakeBuilding(far_x, far_y))

        result = mgr.get_buildings_in_chunks("test_planet", active, all_buildings)

        for b in result:
            chunk = mgr.get_chunk_coord(b.position[0], b.position[1])
            self.assertIn(chunk, active)

if __name__ == "__main__":
    unittest.main()
