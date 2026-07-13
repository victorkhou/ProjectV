"""
Unit tests for BombSystem — fuse config, grenade throw, mine arm, the per-tick
fuse countdown + tile TICK broadcast, and AoE detonation (blast hits everyone
in radius including the placer's own units).
"""

import types
import unittest

from world.data_registry import DataRegistry
from world.definitions import BalanceConfig, ItemDef
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
                 oid=None):
        self.key = "Player"
        self.db = types.SimpleNamespace(
            coord_x=x, coord_y=y, coord_planet=planet,
            bomb_fuses=None, combat_xp=0, hp=100, hp_max=100,
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


def _make(items=None, engine=None, placed=None):
    """Build a BombSystem with a registry stub + notification sink.

    *placed* collects (location, item_def, x, y, owner, bomb_type, fuse, amount,
    radius) for each spawned bomb; the spawner returns a _Bomb tracking those.
    """
    registry = DataRegistry()
    registry.balance = BalanceConfig()
    registry.items = {i.key: i for i in (items or [])}
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
    )
    return system, sink, placed


# -------------------------------------------------------------- #
#  Fuse configuration
# -------------------------------------------------------------- #

class TestSetFuse(unittest.TestCase):
    def test_set_fuse_stores_on_player(self):
        sys, sink, _ = _make(items=[_grenade_def()])
        p = _Player(supplies={"frag_grenade": 2})
        self.assertTrue(sys.set_fuse(p, "frag_grenade", 4))
        self.assertEqual(p.db.bomb_fuses["frag_grenade"], 4)
        self.assertIn("fuse_set", sink.kinds_for(p))

    def test_set_fuse_clamps_to_bounds(self):
        sys, sink, _ = _make(items=[_grenade_def(fmin=1, fmax=10)])
        p = _Player(supplies={"frag_grenade": 1})
        sys.set_fuse(p, "frag_grenade", 99)  # over max
        self.assertEqual(p.db.bomb_fuses["frag_grenade"], 10)

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

    def test_set_all_sets_every_held_bomb_clamped_per_type(self):
        sys, sink, _ = _make(items=[
            _grenade_def(fmax=10), _mine_def(fmax=30),
            ItemDef(key="medkit", name="Medkit", category="consumable"),
        ])
        p = _Player(supplies={"frag_grenade": 1, "land_mine": 2, "medkit": 5})
        count = sys.set_all(p, 20)
        self.assertEqual(count, 2)  # only the two bombs, not the medkit
        self.assertEqual(p.db.bomb_fuses["frag_grenade"], 10)  # clamped to its max
        self.assertEqual(p.db.bomb_fuses["land_mine"], 20)     # within its max
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

    def test_stops_at_first_obstacle(self):
        sys, sink, placed = _make(items=[_grenade_def(rng=8)])
        room = _Room()
        room.place_obj(_Building(0, 3), 0, 3)  # wall 3 north
        p = _Player(x=0, y=0, supplies={"frag_grenade": 1}, location=room)
        sys.set_fuse(p, "frag_grenade", 3)
        sys.throw_grenade(p, "frag_grenade", "n")
        _, _, lx, ly, *_ = placed[0]
        self.assertEqual((lx, ly), (0, 3))  # stopped at the wall's tile

    def test_consumes_grenade_and_fuse(self):
        sys, sink, placed = _make(items=[_grenade_def()])
        room = _Room()
        p = _Player(x=0, y=0, supplies={"frag_grenade": 2}, location=room)
        sys.set_fuse(p, "frag_grenade", 3)
        sys.throw_grenade(p, "frag_grenade", "e")
        self.assertEqual(p.equipment.get_supply("frag_grenade"), 1)  # one used
        self.assertNotIn("frag_grenade", (p.db.bomb_fuses or {}))    # fuse consumed
        self.assertIn("grenade_thrown", sink.kinds_for(p))

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
        p = _Player(x=4, y=7, supplies={"land_mine": 1}, location=room)
        sys.set_fuse(p, "land_mine", 5)
        self.assertTrue(sys.arm_mine(p, "land_mine"))
        _, _, ax, ay, owner, btype, fuse, *_ = placed[0]
        self.assertEqual((ax, ay), (4, 7))
        self.assertEqual(btype, "mine")
        self.assertIn("mine_armed", sink.kinds_for(p))

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

    def test_sheltered_player_excluded_from_blast(self):
        """A player sheltered in a closed building is immune to the blast (a
        blast can't reach inside cover), even though they'd see the tick."""
        engine = _RecordingEngine()
        sys, sink, _ = _make(items=[_grenade_def()], engine=engine)
        # Build a room whose blast query returns a sheltered player.
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
        self.assertNotIn(sheltered, hit_targets)

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


if __name__ == "__main__":
    unittest.main()
