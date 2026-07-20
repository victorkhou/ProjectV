"""
Unit tests for BombSystem — fuse config, grenade throw, mine arm, the per-tick
fuse countdown + tile TICK broadcast, and AoE detonation (blast hits everyone
in radius including the placer's own units).
"""

import types
import unittest

from world.data_registry import DataRegistry
from world.definitions import BalanceConfig, ItemDef, RankDef
from world.event_bus import EventBus, PLAYER_NOTIFICATION
from world.systems.bomb_system import BombSystem


# -------------------------------------------------------------- #
#  Fakes
# -------------------------------------------------------------- #

class _Handler:
    """Supply-bag stand-in: a {item_key: count} map."""
    def __init__(self, supplies=None):
        self._supplies = dict(supplies or {})

    def get_supply(self, item_key):
        return self._supplies.get(item_key, 0)

    def get_supplies(self):
        return dict(self._supplies)

    def remove_supply(self, item_key, count):
        have = self._supplies.get(item_key, 0)
        if have < count:
            return False
        self._supplies[item_key] = have - count
        return True


class _Player:
    def __init__(self, x=5, y=5, planet="earth", supplies=None, location=None,
                 oid=None, level=60):
        self.key = "Player"
        self.db = types.SimpleNamespace(
            coord_x=x, coord_y=y, coord_planet=planet,
            bomb_fuses=None, combat_xp=0, hp=100, hp_max=100, level=level,
        )
        self.equipment = _Handler(supplies)
        self.location = location
        self._messages = []
        if oid is not None:
            self.id = oid

    def msg(self, text):
        self._messages.append(text)


class _Room:
    """A tile grid: objects_by_coord + players_by_coord for queries."""
    def __init__(self, planet="earth"):
        self.planet_name = planet
        self._objects = {}   # (x,y) -> [objs]  (buildings/units blocking a ray)
        self._players = {}   # (x,y) -> [players]
        self.contents = []

    def place_obj(self, obj, x, y):
        self._objects.setdefault((x, y), []).append(obj)

    def place_player(self, p, x, y):
        self._players.setdefault((x, y), []).append(p)
        self.contents.append(p)

    def get_objects_at(self, x, y, type_tag=None):
        return list(self._objects.get((x, y), []))

    def get_players_at(self, x, y):
        return list(self._players.get((x, y), []))

    def get_objects_in_area(self, x1, y1, x2, y2):
        out = []
        for (ox, oy), objs in self._objects.items():
            if x1 <= ox <= x2 and y1 <= oy <= y2:
                out.extend(objs)
        for (px, py), ps in self._players.items():
            if x1 <= px <= x2 and y1 <= py <= y2:
                out.extend(ps)
        return out


class _Building:
    """A building stand-in for ray-blocking / blast targeting."""
    def __init__(self, x, y, open_=True):
        self.key = "Wall"
        self.db = types.SimpleNamespace(
            coord_x=x, coord_y=y, building_type="WL", open=open_,
        )
        self.attributes = types.SimpleNamespace(
            get=lambda k, default=None: {"open": open_, "building_type": "WL"}.get(k, default)
        )


class _RecordingEngine:
    """Records apply_direct_hit calls (the blast fan-out)."""
    def __init__(self):
        self.hits = []  # (attacker, target)

    def apply_direct_hit(self, attacker, target, weapon, include_attacker_bonus=True):
        self.hits.append((attacker, target))
        return 10


class _Bomb:
    """A live-bomb stand-in the fuse countdown ticks."""
    def __init__(self, room, x, y, owner=None, amount=40, radius=2, fuse=3,
                 item_key="frag_grenade", name="Frag Grenade", bomb_type="grenade"):
        self.key = name
        self.location = room
        self.pk = 1
        self.deleted = False
        self.db = types.SimpleNamespace(
            coord_x=x, coord_y=y, owner=owner, amount=amount, radius=radius,
            fuse_remaining=fuse, item_key=item_key, bomb_type=bomb_type,
        )
        self.tags = types.SimpleNamespace(
            get=lambda k, category=None: k == "bomb" and category == "object_type"
        )

    def delete(self):
        self.deleted = True
        self.pk = None


class _Sink:
    def __init__(self):
        self.events = []  # (player, kind, data)

    def __call__(self, player=None, kind=None, data=None, **_):
        self.events.append((player, kind, data or {}))

    def kinds_for(self, player):
        return [k for p, k, _ in self.events if p is player]

    def all_kinds(self):
        return [k for _, k, _ in self.events]


# Item defs (grenade + mine) built directly.
def _grenade_def(key="frag_grenade", amount=40, radius=2, rng=5,
                 fmin=1, fmax=10, fdef=3):
    return ItemDef(
        key=key, name="Frag Grenade", category="throwable",
        effect={"type": "aoe_damage", "bomb_type": "grenade", "amount": amount,
                "radius": radius, "range": rng, "fuse_min": fmin,
                "fuse_max": fmax, "fuse_default": fdef},
    )


def _mine_def(key="land_mine", amount=60, radius=1, fmin=1, fmax=30, fdef=5):
    return ItemDef(
        key=key, name="Land Mine", category="mine",
        effect={"type": "aoe_damage", "bomb_type": "mine", "amount": amount,
                "radius": radius, "fuse_min": fmin, "fuse_max": fmax,
                "fuse_default": fdef},
    )


def _make(items=None, engine=None, placed=None, ranks=None, in_bounds=None,
          rng=None, randint=None):
    """Build a BombSystem with a registry stub + notification sink.

    *placed* collects (location, item_def, x, y, owner, bomb_type, fuse, amount,
    radius) for each spawned bomb; the spawner returns a _Bomb tracking those.
    *ranks* is a list of RankDef for the rank gate; *in_bounds* is an optional
    (x,y,planet)->bool bounds check for the throw-ray clamp. *rng* is an
    optional zero-arg float source for the disarm success roll, *randint* an
    optional (a,b)->int source for the disarm duration (force success/failure
    and a deterministic tick count).
    """
    registry = DataRegistry()
    registry.balance = BalanceConfig()
    registry.items = {i.key: i for i in (items or [])}
    registry.ranks = list(ranks or [])
    # resolve_item: exact key or name (space/underscore-insensitive).
    def _resolve(token):
        if not token:
            return None
        norm = str(token).strip().lower().replace("_", " ")
        for idef in registry.items.values():
            if idef.key.lower().replace("_", " ") == norm or \
               idef.name.lower() == norm:
                return idef
        return None
    registry.resolve_item = _resolve

    def _rank_by_name(name):
        for r in registry.ranks:
            if r.name == name:
                return r
        raise KeyError(name)
    registry.get_rank_by_name = _rank_by_name

    bus = EventBus()
    sink = _Sink()
    bus.subscribe(PLAYER_NOTIFICATION, sink)

    placed = placed if placed is not None else []

    def _spawn(location, item_def, x, y, owner, bomb_type, fuse, amount, radius):
        placed.append((location, item_def, x, y, owner, bomb_type, fuse, amount, radius))
        bomb = _Bomb(location, x, y, owner=owner, amount=amount, radius=radius,
                     fuse=fuse, item_key=item_def.key, name=item_def.name,
                     bomb_type=bomb_type)
        if location is not None:
            location.place_obj(bomb, x, y)
        return bomb

    system = BombSystem(
        registry, bus,
        spawn_bomb_func=_spawn,
        area_damage_applier=(lambda: engine) if engine else None,
        in_bounds_func=in_bounds,
        rng_func=rng,
        randint_func=randint,
    )
    return system, sink, placed


# -------------------------------------------------------------- #
#  Fuse configuration
# -------------------------------------------------------------- #

class TestSetFuse(unittest.TestCase):
    def test_set_fuse_arms_every_held_unit(self):
        sys, sink, _ = _make(items=[_grenade_def()])
        p = _Player(supplies={"frag_grenade": 3})
        self.assertTrue(sys.set_fuse(p, "frag_grenade", 4))
        # One queued fuse per held grenade — all 3 can be thrown.
        self.assertEqual(p.db.bomb_fuses["frag_grenade"], [4, 4, 4])
        self.assertIn("fuse_set", sink.kinds_for(p))

    def test_set_fuse_clamps_to_bounds(self):
        sys, sink, _ = _make(items=[_grenade_def(fmin=1, fmax=10)])
        p = _Player(supplies={"frag_grenade": 1})
        sys.set_fuse(p, "frag_grenade", 99)  # over max
        self.assertEqual(p.db.bomb_fuses["frag_grenade"], [10])

    def test_set_fuse_resets_queue_to_current_held(self):
        """Re-setting a type replaces its queue (doesn't stack stale entries)."""
        sys, _, _ = _make(items=[_grenade_def()])
        p = _Player(supplies={"frag_grenade": 2})
        sys.set_fuse(p, "frag_grenade", 5)
        sys.set_fuse(p, "frag_grenade", 3)  # re-set
        self.assertEqual(p.db.bomb_fuses["frag_grenade"], [3, 3])

    def test_set_fuse_rejects_non_bomb(self):
        sys, sink, _ = _make(items=[
            ItemDef(key="medkit", name="Medkit", category="consumable")])
        p = _Player(supplies={"medkit": 1})
        self.assertFalse(sys.set_fuse(p, "medkit", 5))
        self.assertIn("not_a_bomb", sink.kinds_for(p))

    def test_set_fuse_rejects_not_held(self):
        sys, sink, _ = _make(items=[_grenade_def()])
        p = _Player(supplies={})  # not carrying
        self.assertFalse(sys.set_fuse(p, "frag_grenade", 5))
        self.assertIn("bomb_not_held", sink.kinds_for(p))

    def test_set_all_arms_every_unit_of_every_type_clamped_per_type(self):
        sys, sink, _ = _make(items=[
            _grenade_def(fmax=10), _mine_def(fmax=30),
            ItemDef(key="medkit", name="Medkit", category="consumable"),
        ])
        p = _Player(supplies={"frag_grenade": 1, "land_mine": 2, "medkit": 5})
        count = sys.set_all(p, 20)
        self.assertEqual(count, 3)  # 1 grenade + 2 mines armed (not the medkit)
        self.assertEqual(p.db.bomb_fuses["frag_grenade"], [10])       # clamped to max
        self.assertEqual(p.db.bomb_fuses["land_mine"], [20, 20])      # both units
        self.assertNotIn("medkit", p.db.bomb_fuses)
        self.assertIn("fuse_all_set", sink.kinds_for(p))


# -------------------------------------------------------------- #
#  Grenade throw (directional)
# -------------------------------------------------------------- #

class TestThrowGrenade(unittest.TestCase):
    def test_needs_fuse_set_first(self):
        sys, sink, placed = _make(items=[_grenade_def()])
        room = _Room()
        p = _Player(x=0, y=0, supplies={"frag_grenade": 1}, location=room)
        self.assertFalse(sys.throw_grenade(p, "frag_grenade", "n"))
        self.assertIn("need_fuse", sink.kinds_for(p))
        self.assertEqual(placed, [])

    def test_clear_line_lands_at_max_range(self):
        sys, sink, placed = _make(items=[_grenade_def(rng=5)])
        room = _Room()
        p = _Player(x=0, y=0, supplies={"frag_grenade": 1}, location=room)
        sys.set_fuse(p, "frag_grenade", 3)
        self.assertTrue(sys.throw_grenade(p, "frag_grenade", "n"))
        # north = +y; clear line -> lands at (0, 5).
        _, _, lx, ly, owner, btype, fuse, amount, radius = placed[0]
        self.assertEqual((lx, ly), (0, 5))
        self.assertEqual(btype, "grenade")
        self.assertEqual(fuse, 3)
        self.assertIs(owner, p)

    def test_lands_before_building(self):
        """A grenade thrown at a building lands on the clear tile BEFORE it (it
        bounces off the structure), so its blast breaches from outside."""
        sys, sink, placed = _make(items=[_grenade_def(rng=8)])
        room = _Room()
        room.place_obj(_Building(0, 3), 0, 3)  # wall 3 north
        p = _Player(x=0, y=0, supplies={"frag_grenade": 1}, location=room)
        sys.set_fuse(p, "frag_grenade", 3)
        sys.throw_grenade(p, "frag_grenade", "n")
        _, _, lx, ly, *_ = placed[0]
        self.assertEqual((lx, ly), (0, 2))  # tile just before the wall

    def test_lands_at_unit_feet(self):
        """A grenade thrown at a unit lands ON their tile (lob it right at them)."""
        sys, sink, placed = _make(items=[_grenade_def(rng=8)])
        room = _Room()
        enemy = _Player(x=0, y=3, oid=42)
        room.place_obj(enemy, 0, 3)  # on the ray (get_objects_at scans objects)
        p = _Player(x=0, y=0, supplies={"frag_grenade": 1}, location=room)
        sys.set_fuse(p, "frag_grenade", 3)
        sys.throw_grenade(p, "frag_grenade", "n")
        _, _, lx, ly, *_ = placed[0]
        self.assertEqual((lx, ly), (0, 3))  # at the enemy's feet

    def test_building_on_first_step_lands_at_feet(self):
        """A building directly in front lands the grenade at the thrower's feet
        (the last clear tile is their own)."""
        sys, sink, placed = _make(items=[_grenade_def(rng=5)])
        room = _Room()
        room.place_obj(_Building(0, 1), 0, 1)  # wall immediately north
        p = _Player(x=0, y=0, supplies={"frag_grenade": 1}, location=room)
        sys.set_fuse(p, "frag_grenade", 3)
        sys.throw_grenade(p, "frag_grenade", "n")
        _, _, lx, ly, *_ = placed[0]
        self.assertEqual((lx, ly), (0, 0))  # thrower's own tile

    def test_consumes_grenade_and_one_queued_fuse(self):
        sys, sink, placed = _make(items=[_grenade_def()])
        room = _Room()
        p = _Player(x=0, y=0, supplies={"frag_grenade": 2}, location=room)
        sys.set_fuse(p, "frag_grenade", 3)
        sys.throw_grenade(p, "frag_grenade", "e")
        self.assertEqual(p.equipment.get_supply("frag_grenade"), 1)  # one used
        # One queued fuse consumed; the second grenade is still armed.
        self.assertEqual(p.db.bomb_fuses.get("frag_grenade"), [3])
        self.assertIn("grenade_thrown", sink.kinds_for(p))

    def test_set_all_then_throw_every_grenade(self):
        """The reported bug: 'set all 3' with 3 grenades must let all 3 throw
        without re-setting between throws."""
        sys, sink, placed = _make(items=[_grenade_def(rng=3)])
        room = _Room()
        p = _Player(x=5, y=5, supplies={"frag_grenade": 3}, location=room)
        sys.set_all(p, 3)
        self.assertTrue(sys.throw_grenade(p, "frag_grenade", "e"))
        self.assertTrue(sys.throw_grenade(p, "frag_grenade", "w"))
        self.assertTrue(sys.throw_grenade(p, "frag_grenade", "n"))
        self.assertEqual(p.equipment.get_supply("frag_grenade"), 0)  # all thrown
        self.assertEqual(len(placed), 3)                              # all placed
        self.assertNotIn("frag_grenade", (p.db.bomb_fuses or {}))     # queue drained

    def test_last_queued_fuse_removes_key(self):
        sys, _, _ = _make(items=[_grenade_def()])
        room = _Room()
        p = _Player(x=0, y=0, supplies={"frag_grenade": 1}, location=room)
        sys.set_fuse(p, "frag_grenade", 3)
        sys.throw_grenade(p, "frag_grenade", "e")
        # A single-unit queue empties → key removed, so 'need fuse' gates again.
        self.assertNotIn("frag_grenade", (p.db.bomb_fuses or {}))

    def test_players_on_landing_tile_are_notified(self):
        sys, sink, placed = _make(items=[_grenade_def(rng=5)])
        room = _Room()
        p = _Player(x=0, y=0, supplies={"frag_grenade": 1}, location=room)
        bystander = _Player(x=0, y=5, oid=99)
        room.place_player(bystander, 0, 5)
        sys.set_fuse(p, "frag_grenade", 3)
        sys.throw_grenade(p, "frag_grenade", "n")  # lands at (0,5)
        self.assertIn("bomb_landed", sink.kinds_for(bystander))

    def test_rejects_non_grenade(self):
        sys, sink, placed = _make(items=[_mine_def()])
        room = _Room()
        p = _Player(supplies={"land_mine": 1}, location=room)
        self.assertFalse(sys.throw_grenade(p, "land_mine", "n"))
        self.assertIn("throw_failed", sink.kinds_for(p))
        self.assertEqual(placed, [])


# -------------------------------------------------------------- #
#  Mine arm
# -------------------------------------------------------------- #

class TestArmMine(unittest.TestCase):
    def test_arm_places_bomb_on_own_tile(self):
        sys, sink, placed = _make(items=[_mine_def()])
        room = _Room()
        p = _Player(x=4, y=7, supplies={"land_mine": 2}, location=room)
        sys.set_fuse(p, "land_mine", 5)
        self.assertTrue(sys.arm_mine(p, "land_mine"))
        _, _, ax, ay, owner, btype, fuse, *_ = placed[0]
        self.assertEqual((ax, ay), (4, 7))
        self.assertEqual(btype, "mine")
        self.assertIn("mine_armed", sink.kinds_for(p))
        # The mine + ONE queued fuse are consumed; the second mine stays armed.
        self.assertEqual(p.equipment.get_supply("land_mine"), 1)
        self.assertEqual(p.db.bomb_fuses.get("land_mine"), [5])

    def test_arm_needs_fuse(self):
        sys, sink, placed = _make(items=[_mine_def()])
        room = _Room()
        p = _Player(supplies={"land_mine": 1}, location=room)
        self.assertFalse(sys.arm_mine(p, "land_mine"))
        self.assertIn("need_fuse", sink.kinds_for(p))

    def test_arm_rejects_grenade(self):
        sys, sink, placed = _make(items=[_grenade_def()])
        room = _Room()
        p = _Player(supplies={"frag_grenade": 1}, location=room)
        self.assertFalse(sys.arm_mine(p, "frag_grenade"))
        self.assertIn("not_a_mine", sink.kinds_for(p))

    def test_others_on_tile_see_arm(self):
        sys, sink, placed = _make(items=[_mine_def()])
        room = _Room()
        p = _Player(x=2, y=2, supplies={"land_mine": 1}, location=room)
        bystander = _Player(x=2, y=2, oid=99)
        room.place_player(bystander, 2, 2)
        sys.set_fuse(p, "land_mine", 5)
        sys.arm_mine(p, "land_mine")
        self.assertIn("bomb_armed", sink.kinds_for(bystander))
        # The placer gets the distinct 'mine_armed', not the bystander 'bomb_armed'.
        self.assertNotIn("bomb_armed", sink.kinds_for(p))


# -------------------------------------------------------------- #
#  Rank gate on deploy (review fix)
# -------------------------------------------------------------- #

class TestBombRankGate(unittest.TestCase):
    """A rank-gated bomb can't be thrown/armed below its required rank — the
    deploy path enforces it (production/pickup don't)."""

    _RANKS = [RankDef(name="Private", level=1, xp_threshold=0),
              RankDef(name="Sergeant", level=3, xp_threshold=100)]

    def _sgt_grenade(self):
        d = _grenade_def()
        d.required_rank = "Sergeant"
        return d

    def _sgt_mine(self):
        d = _mine_def()
        d.required_rank = "Sergeant"
        return d

    def test_underranked_cannot_throw(self):
        sys, sink, placed = _make(items=[self._sgt_grenade()], ranks=self._RANKS)
        room = _Room()
        # level 1 -> rank 1 (Private) < Sergeant (rank 3): denied.
        p = _Player(x=0, y=0, supplies={"frag_grenade": 1}, location=room, level=1)
        sys.set_fuse(p, "frag_grenade", 3)
        self.assertFalse(sys.throw_grenade(p, "frag_grenade", "n"))
        self.assertIn("equip_denied", sink.kinds_for(p))
        self.assertEqual(placed, [])
        self.assertEqual(p.equipment.get_supply("frag_grenade"), 1)  # not consumed

    def test_ranked_can_throw(self):
        sys, sink, placed = _make(items=[self._sgt_grenade()], ranks=self._RANKS)
        room = _Room()
        p = _Player(x=0, y=0, supplies={"frag_grenade": 1}, location=room, level=15)
        sys.set_fuse(p, "frag_grenade", 3)
        self.assertTrue(sys.throw_grenade(p, "frag_grenade", "n"))
        self.assertEqual(len(placed), 1)

    def test_underranked_cannot_arm(self):
        sys, sink, placed = _make(items=[self._sgt_mine()], ranks=self._RANKS)
        room = _Room()
        p = _Player(x=0, y=0, supplies={"land_mine": 1}, location=room, level=1)
        sys.set_fuse(p, "land_mine", 5)
        self.assertFalse(sys.arm_mine(p, "land_mine"))
        self.assertIn("equip_denied", sink.kinds_for(p))
        self.assertEqual(placed, [])


# -------------------------------------------------------------- #
#  Off-map throw clamp (review fix)
# -------------------------------------------------------------- #

class TestThrowBounds(unittest.TestCase):
    """A thrown grenade stops at the map edge instead of landing off-map."""

    @staticmethod
    def _bounds_0_to_9(x, y, planet):
        return 0 <= x <= 9 and 0 <= y <= 9

    def test_throw_clamps_at_map_edge(self):
        sys, sink, placed = _make(items=[_grenade_def(rng=5)],
                                  in_bounds=self._bounds_0_to_9)
        room = _Room()
        # At (1,0) throwing west (range 5) would reach (-4,0); must stop at (0,0).
        p = _Player(x=1, y=0, supplies={"frag_grenade": 1}, location=room)
        sys.set_fuse(p, "frag_grenade", 3)
        sys.throw_grenade(p, "frag_grenade", "w")
        _, _, lx, ly, *_ = placed[0]
        self.assertEqual((lx, ly), (0, 0), "grenade must land on the edge tile, not off-map")

    def test_throw_unbounded_when_no_bounds_func(self):
        # No in_bounds injected -> falls open (unchanged max-range behavior).
        sys, sink, placed = _make(items=[_grenade_def(rng=5)])
        room = _Room()
        p = _Player(x=1, y=0, supplies={"frag_grenade": 1}, location=room)
        sys.set_fuse(p, "frag_grenade", 3)
        sys.throw_grenade(p, "frag_grenade", "w")
        _, _, lx, ly, *_ = placed[0]
        self.assertEqual((lx, ly), (-4, 0))  # unclamped max range


# -------------------------------------------------------------- #
#  Fuse countdown + detonation
# -------------------------------------------------------------- #

class TestFuseCountdown(unittest.TestCase):
    def test_tick_decrements_and_broadcasts(self):
        engine = _RecordingEngine()
        sys, sink, _ = _make(items=[_grenade_def()], engine=engine)
        room = _Room()
        watcher = _Player(x=1, y=1, oid=5)
        room.place_player(watcher, 1, 1)
        bomb = _Bomb(room, 1, 1, fuse=3)
        room.place_obj(bomb, 1, 1)
        sys._live_bombs = [bomb]
        sys.process_tick(1)
        self.assertEqual(bomb.db.fuse_remaining, 2)
        self.assertIn("bomb_tick", sink.kinds_for(watcher))
        self.assertEqual(engine.hits, [])  # not yet exploded

    def test_detonates_at_zero_and_deletes(self):
        engine = _RecordingEngine()
        sys, sink, _ = _make(items=[_grenade_def()], engine=engine)
        room = _Room()
        victim = _Player(x=1, y=1, oid=5)
        room.place_player(victim, 1, 1)
        owner = _Player(x=9, y=9, oid=1)
        bomb = _Bomb(room, 1, 1, owner=owner, amount=40, radius=2, fuse=1)
        room.place_obj(bomb, 1, 1)
        sys._live_bombs = [bomb]
        sys.process_tick(1)  # 1 -> 0 -> detonate
        self.assertTrue(bomb.deleted)
        self.assertEqual(sys._live_bombs, [])
        # The victim on the tile took the blast, attributed to the owner.
        self.assertIn((owner, victim), engine.hits)

    def test_blast_hits_placers_own_units_and_placer(self):
        """Blast is indiscriminate: it hits the placer's own units AND the
        placer if they're in radius (friendly fire, per the chosen rule)."""
        engine = _RecordingEngine()
        sys, sink, _ = _make(items=[_mine_def()], engine=engine)
        room = _Room()
        owner = _Player(x=0, y=0, oid=1)
        room.place_player(owner, 0, 0)          # placer standing on the mine
        own_agent = _Player(x=1, y=0, oid=2)    # owner's own unit, adjacent
        room.place_player(own_agent, 1, 0)
        bomb = _Bomb(room, 0, 0, owner=owner, amount=60, radius=2, fuse=1)
        room.place_obj(bomb, 0, 0)
        sys._live_bombs = [bomb]
        sys.process_tick(1)
        hit_targets = [t for _, t in engine.hits]
        self.assertIn(owner, hit_targets, "the placer is caught in their own blast")
        self.assertIn(own_agent, hit_targets, "friendly units are hit too")

    def test_sheltered_player_still_hit_by_blast(self):
        """A blast BREACHES cover: a player sheltered inside a closed building on
        the blast tile is still caught (an explosion reaches through walls). This
        is why the placer standing inside their own structure takes the hit."""
        engine = _RecordingEngine()
        sys, sink, _ = _make(items=[_grenade_def()], engine=engine)
        room = _Room()
        # Sheltered player: inside a closed building on their tile.
        sheltered = _Player(x=1, y=1, oid=7)
        sheltered.db.inside_building = True

        class _ShelterRoom(_Room):
            def get_buildings_at(self, x, y):
                return [_Building(x, y, open_=False)]
        shelter_room = _ShelterRoom()
        sheltered.location = shelter_room
        room.place_player(sheltered, 1, 1)
        bomb = _Bomb(room, 1, 1, amount=40, radius=2, fuse=1)
        room.place_obj(bomb, 1, 1)
        sys._live_bombs = [bomb]
        sys.process_tick(1)
        hit_targets = [t for _, t in engine.hits]
        self.assertIn(sheltered, hit_targets)

    def test_closed_building_hit_by_blast(self):
        """A blast damages a building whether OPEN or CLOSED — an explosion is an
        anti-structure weapon that levels closed walls/buildings too."""
        engine = _RecordingEngine()
        sys, sink, _ = _make(items=[_mine_def()], engine=engine)
        room = _Room()
        closed_bldg = _Building(1, 0, open_=False)
        room.place_obj(closed_bldg, 1, 0)
        bomb = _Bomb(room, 0, 0, amount=60, radius=2, fuse=1)
        room.place_obj(bomb, 0, 0)
        sys._live_bombs = [bomb]
        sys.process_tick(1)
        hit_targets = [t for _, t in engine.hits]
        self.assertIn(closed_bldg, hit_targets, "a blast breaches a closed building")

    def test_deleted_bomb_pruned_without_error(self):
        sys, sink, _ = _make(items=[_grenade_def()])
        room = _Room()
        bomb = _Bomb(room, 1, 1, fuse=3)
        bomb.pk = None  # deleted out from under us
        sys._live_bombs = [bomb]
        sys.process_tick(1)
        self.assertEqual(sys._live_bombs, [])

    def test_bad_bomb_isolated_from_others(self):
        engine = _RecordingEngine()
        sys, sink, _ = _make(items=[_grenade_def()], engine=engine)
        room = _Room()
        good = _Bomb(room, 1, 1, fuse=3)
        room.place_obj(good, 1, 1)

        class _Boom(_Bomb):
            @property
            def db(self):
                raise RuntimeError("boom")
        boom = _Boom.__new__(_Boom)  # bypass __init__ to make db raise
        boom.pk = 1
        sys._live_bombs = [boom, good]
        sys.process_tick(1)
        # The good bomb still advanced despite the bad one raising.
        self.assertEqual(good.db.fuse_remaining, 2)


class TestRebuildFromWorld(unittest.TestCase):
    def test_rebuild_retracks_live_bombs(self):
        sys, sink, _ = _make(items=[_grenade_def()])
        room = _Room()
        live = _Bomb(room, 1, 1, fuse=4)
        spent = _Bomb(room, 2, 2, fuse=0)  # already at 0 — not re-tracked
        room.contents = [live, spent]
        n = sys.rebuild_from_world({"earth": room})
        self.assertEqual(n, 1)
        self.assertIn(live, sys._live_bombs)
        self.assertNotIn(spent, sys._live_bombs)


# -------------------------------------------------------------- #
#  Disarm — multi-tick attempt; fuse keeps racing; end-roll on resolve;
#  failure detonates immediately.
# -------------------------------------------------------------- #

class TestDisarm(unittest.TestCase):
    def _setup(self, rng=None, randint=None, x=5, y=5, fuse=8):
        sys, sink, _ = _make(items=[_grenade_def()], rng=rng, randint=randint)
        room = _Room()
        bomb = _Bomb(room, x, y, fuse=fuse)
        room.place_obj(bomb, x, y)
        sys._live_bombs.append(bomb)
        return sys, sink, room, bomb

    def test_disarm_starts_a_timed_attempt(self):
        # randint forces a 3-tick disarm; disarm() only STARTS it (no instant kill).
        sys, sink, room, bomb = self._setup(randint=lambda a, b: 3)
        p = _Player(x=5, y=5, location=room)
        self.assertTrue(sys.disarm(p))
        self.assertEqual(bomb.db.disarm_ticks_remaining, 3)
        self.assertIs(bomb.db.disarm_by, p)
        self.assertFalse(bomb.deleted)  # not resolved yet
        self.assertIn("disarm_start", sink.kinds_for(p))

    def test_disarm_succeeds_after_timer_elapses(self):
        # 2-tick disarm, fuse long enough; success roll (0.0 < 0.7).
        sys, sink, room, bomb = self._setup(rng=lambda: 0.0,
                                            randint=lambda a, b: 2, fuse=8)
        p = _Player(x=5, y=5, location=room)
        sys.disarm(p)
        sys.process_tick(1)   # fuse 8->7, disarm 2->1
        self.assertFalse(bomb.deleted)
        sys.process_tick(2)   # fuse 7->6, disarm 1->0 -> resolve success
        self.assertTrue(bomb.deleted)
        self.assertNotIn(bomb, sys._live_bombs)
        self.assertIn("disarm_success", sink.kinds_for(p))

    def test_disarm_failure_detonates_immediately(self):
        # 2-tick disarm; failure roll (0.99 >= 0.7) → detonate on resolve.
        detonated = {"n": 0}
        sys, sink, room, bomb = self._setup(rng=lambda: 0.99,
                                            randint=lambda a, b: 2, fuse=8)
        orig = sys._detonate
        sys._detonate = lambda b: (detonated.__setitem__("n", detonated["n"] + 1), orig(b))[1]
        p = _Player(x=5, y=5, location=room)
        sys.disarm(p)
        sys.process_tick(1)
        sys.process_tick(2)   # disarm resolves → failure → detonate
        self.assertEqual(detonated["n"], 1)
        self.assertTrue(bomb.deleted)
        self.assertIn("disarm_failed", sink.kinds_for(p))

    def test_fuse_wins_the_race_if_it_expires_first(self):
        # Short fuse (2) vs a long 5-tick disarm: the bomb explodes on the fuse,
        # never reaching a disarm resolution.
        detonated = {"n": 0}
        sys, sink, room, bomb = self._setup(rng=lambda: 0.0,
                                            randint=lambda a, b: 5, fuse=2)
        orig = sys._detonate
        sys._detonate = lambda b: (detonated.__setitem__("n", detonated["n"] + 1), orig(b))[1]
        p = _Player(x=5, y=5, location=room)
        sys.disarm(p)
        sys.process_tick(1)   # fuse 2->1, disarm 5->4
        sys.process_tick(2)   # fuse 1->0 -> detonate (disarm never resolves)
        self.assertEqual(detonated["n"], 1)
        self.assertTrue(bomb.deleted)
        # No success — it blew up mid-attempt.
        self.assertNotIn("disarm_success", sink.kinds_for(p))

    def test_cannot_restart_an_in_progress_disarm(self):
        sys, sink, room, bomb = self._setup(randint=lambda a, b: 4)
        p = _Player(x=5, y=5, location=room)
        self.assertTrue(sys.disarm(p))
        # A second attempt while one is running is rejected (timer not reset).
        p2 = _Player(x=5, y=5, location=room, oid=88)
        self.assertFalse(sys.disarm(p2))
        self.assertEqual(bomb.db.disarm_ticks_remaining, 4)
        self.assertIn("disarm_in_progress", sink.kinds_for(p2))

    def test_no_bomb_on_tile(self):
        sys, sink, room, bomb = self._setup(x=1, y=1)
        p = _Player(x=9, y=9, location=room)  # far from the bomb
        self.assertFalse(sys.disarm(p))
        self.assertFalse(bomb.deleted)
        self.assertIn("disarm_none", sink.kinds_for(p))

    def test_success_notifies_others_on_tile(self):
        sys, sink, room, bomb = self._setup(rng=lambda: 0.0,
                                            randint=lambda a, b: 1, fuse=8)
        p = _Player(x=5, y=5, location=room)
        bystander = _Player(x=5, y=5, oid=77)
        room.place_player(bystander, 5, 5)
        sys.disarm(p)
        sys.process_tick(1)   # disarm resolves (1 tick) → success
        self.assertIn("disarm_success_tile", sink.kinds_for(bystander))

    def test_disarm_duration_clamped_to_at_least_one(self):
        sys, _, _ = _make(items=[_grenade_def()])
        sys.registry.balance.bomb_disarm_ticks_min = 0
        sys.registry.balance.bomb_disarm_ticks_max = 0
        # randint would be called with (1, 1) after clamping; force it to echo lo.
        sys._randint_func = lambda a, b: a
        self.assertGreaterEqual(sys._roll_disarm_ticks(), 1)

    def test_chance_uses_balance_base(self):
        sys, _, _ = _make(items=[_grenade_def()])
        sys.registry.balance.bomb_disarm_base_success = 0.7
        p = _Player(x=0, y=0)
        self.assertAlmostEqual(sys._disarm_success_chance(p), 0.7)

    def test_chance_adds_player_bonus_clamped(self):
        sys, _, _ = _make(items=[_grenade_def()])
        sys.registry.balance.bomb_disarm_base_success = 0.5
        p = _Player(x=0, y=0)
        p.db.disarm_bonus = 0.8  # 0.5 + 0.8 = 1.3 → clamped to 1.0
        self.assertAlmostEqual(sys._disarm_success_chance(p), 1.0)


if __name__ == "__main__":
    unittest.main()
