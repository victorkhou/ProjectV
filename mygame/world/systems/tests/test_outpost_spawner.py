"""
Unit tests for OutpostSpawnerSystem + BaseEliminationHandler (PvE Phase 5).

Covers template-driven base spawning (sentinel + buildings + guards), placement
validity (bounds, passability, occupancy, base separation), respawn scheduling
via the BASE_ELIMINATED event + cooldown, and the elimination fork (NPC base
wiped + XP + loot vs. player HQ untouched).

Requirements: 5.x, 6.x, 7.x, 8.x, 12.6
"""

import random
import sys
import types
import unittest


def _ensure_evennia_stubs():
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

from mygame.world.systems.outpost_spawner import OutpostSpawnerSystem  # noqa: E402
from mygame.world.systems.base_elimination import BaseEliminationHandler  # noqa: E402
from mygame.world.data_registry import DataRegistry  # noqa: E402
from mygame.world.definitions import (  # noqa: E402
    BalanceConfig, BuildingDef, BaseTemplateDef,
    TemplateBuildingDef, TemplateGuardDef,
)
from mygame.world.event_bus import EventBus, BASE_ELIMINATED, BUILDING_DESTROYED  # noqa: E402


# -------------------------------------------------------------- #
#  Fakes
# -------------------------------------------------------------- #

class FakeAttrs:
    def __init__(self, d=None):
        self._d = d or {}

    def get(self, key, default=None):
        return self._d.get(key, default)

    def add(self, key, value):
        self._d[key] = value


class FakeCoordIndex:
    def __init__(self):
        self.added = []

    def add(self, obj, x, y):
        self.added.append((obj, x, y))


class FakeRoom:
    def __init__(self, planet="earth"):
        self._planet = planet
        self.coord_index = FakeCoordIndex()
        self._buildings_at = {}  # (x,y) -> list

    @property
    def planet_name(self):
        return self._planet

    def get_buildings_at(self, x, y):
        return self._buildings_at.get((int(x), int(y)), [])

    def place_building(self, b, x, y):
        self._buildings_at.setdefault((int(x), int(y)), []).append(b)


class FakeBuilding:
    _next = 1000

    def __init__(self, bdef, owner, x, y, room):
        self.db = types.SimpleNamespace(
            building_type=bdef.abbreviation, coord_x=x, coord_y=y,
            hp=bdef.max_health, hp_max=bdef.max_health, under_construction=False,
        )
        self.attributes = FakeAttrs({
            "building_type": bdef.abbreviation, "owner": owner,
            "coord_x": x, "coord_y": y,
        })
        self.owner = owner
        self.location = room
        self.deleted = False
        FakeBuilding._next += 1
        self.id = FakeBuilding._next

    def delete(self):
        self.deleted = True


class FakeSentinel:
    _next = 1
    def __init__(self, name, room, planet):
        self.key = name
        self.location = room
        self.db = types.SimpleNamespace(
            is_sentinel=True, coord_planet=planet,
            base_tier=None, base_planet=None,
        )
        self.attributes = FakeAttrs({"is_sentinel": True})
        self._buildings = []
        self.deleted = False
        FakeSentinel._next += 1
        self.id = FakeSentinel._next
        # Mirror Evennia: a live object has a pk; delete() clears it to None.
        self.pk = self.id

    def get_buildings(self):
        return list(self._buildings)

    def delete(self):
        self.deleted = True
        self.pk = None


class FakeGuard:
    def __init__(self, owner, room, x, y, role, hp):
        self.key = f"{role} guard"
        self.db = types.SimpleNamespace(
            owner=owner, npc_type="enemy", role=role, coord_x=x, coord_y=y,
            hp=hp, hp_max=hp,
        )
        self.location = room
        self.deleted = False

    def delete(self):
        self.deleted = True


class FakeNpcFactory:
    """Records created sentinels/guards; wires buildings onto the sentinel."""
    def __init__(self):
        self.sentinels = []
        self.guards = []

    def create_sentinel(self, name, tile, planet):
        s = FakeSentinel(name, tile, planet)
        self.sentinels.append(s)
        return s

    def create_enemy_guard(self, owner, tile, x, y, role, hp, index=1):
        g = FakeGuard(owner, tile, x, y, role, hp)
        g.index = index
        self.guards.append(g)
        return g


class FakeBuildingFactory:
    def __init__(self):
        self.created = []

    def create_building(self, bdef, tile, owner, x=None, y=None):
        b = FakeBuilding(bdef, owner, x, y, tile)
        self.created.append(b)
        if hasattr(tile, "place_building"):
            tile.place_building(b, x, y)
        # Track on the sentinel so get_buildings() returns it (for wipe tests).
        if hasattr(owner, "_buildings"):
            owner._buildings.append(b)
        return b


class FakeTerrain:
    """Terrain provider: 'grass' everywhere, except ``blocked`` tiles report
    'blocked' (impassable) and ``river`` tiles report 'river' (passable but not
    buildable — a treacherous tile a base must avoid)."""
    def __init__(self, blocked=None, river=None):
        self._blocked = set(blocked or [])
        self._river = set(river or [])

    def get_terrain_and_resource(self, planet, x, y):
        if (x, y) in self._blocked:
            return "blocked", None
        if (x, y) in self._river:
            return "river", None
        return "grass", None


class FakeSpace:
    def __init__(self, width=50, height=50, spawn_x=None, spawn_y=None):
        self.width = width
        self.height = height
        self.spawn_x = spawn_x
        self.spawn_y = spawn_y


class FakePlanetRegistry:
    def __init__(self, width=50, height=50, spawn_x=None, spawn_y=None):
        self._space = FakeSpace(width, height, spawn_x, spawn_y)

    def get_space(self, planet):
        return self._space

    def is_valid_coordinate(self, x, y, planet):
        return 0 <= x < self._space.width and 0 <= y < self._space.height


def _make_registry():
    r = DataRegistry()
    r.balance = BalanceConfig()
    r.buildings = {
        "HQ": BuildingDef(name="Headquarters", abbreviation="HQ",
                          cost={"Wood": 10}, max_health=500, requires_hq=False,
                          required_terrain=None, category="hq", produces=None,
                          capabilities=frozenset({"headquarters"})),
        "WL": BuildingDef(name="Wall", abbreviation="WL", cost={"Stone": 5},
                          max_health=600, requires_hq=True, required_terrain=None,
                          category="defense", produces=None,
                          capabilities=frozenset()),
        "TU": BuildingDef(name="Turret", abbreviation="TU",
                          cost={"Iron": 15}, max_health=300, requires_hq=True,
                          required_terrain=None, category="defense", produces=None,
                          capabilities=frozenset({"turret"})),
    }
    # Terrain defs for passability + buildability lookups. 'river' mirrors the
    # real treacherous tile: passable (you can walk it) but NOT buildable, so a
    # base must never spawn on it.
    r.terrain = {
        "grass": types.SimpleNamespace(passable=True, buildable=True),
        "blocked": types.SimpleNamespace(passable=False, buildable=False),
        "river": types.SimpleNamespace(passable=True, buildable=False),
    }
    r.get_terrain = lambda t: r.terrain[t]
    r.base_templates = {
        "outpost": BaseTemplateDef(
            tier="outpost", display_name="Outpost",
            difficulty_class="outpost",
            buildings=[
                TemplateBuildingDef("HQ", (0, 0), hp=200),
                TemplateBuildingDef("WL", (0, 1), hp=300),
            ],
            guards=[TemplateGuardDef("guard", "melee", 2)],
            loot={"Iron": 30, "Stone": 20},
        ),
        "fortress": BaseTemplateDef(
            tier="fortress", display_name="Fortress",
            difficulty_class="fortress",
            buildings=[TemplateBuildingDef("HQ", (0, 0), hp=600)],
            guards=[TemplateGuardDef("soldier", "ranged", 3)],
            loot={"Iron": 100},
            xp_reward=800,     # difficulty-scaled XP override
            gear_rolls=3,      # three roll rounds
            gear_drop_chance=1.0,
            gear_pool=["assault_rifle"],
        ),
    }
    return r


def _make_spawner(registry=None, event_bus=None, terrain=None,
                  planet_registry=None, rng_seed=1, tick=0):
    registry = registry or _make_registry()
    event_bus = event_bus or EventBus()
    room = FakeRoom("earth")
    npc_factory = FakeNpcFactory()
    building_factory = FakeBuildingFactory()
    spawner = OutpostSpawnerSystem(
        registry, event_bus,
        npc_base_factory=npc_factory,
        building_factory=building_factory,
        terrain_provider=terrain or FakeTerrain(),
        planet_rooms_provider=lambda: {"earth": room},
        planet_registry=planet_registry or FakePlanetRegistry(),
        rng=random.Random(rng_seed),
        current_tick_func=lambda: tick,
    )
    return spawner, npc_factory, building_factory, room, event_bus


# -------------------------------------------------------------- #
#  Spawn tests
# -------------------------------------------------------------- #

class TestSpawnBase(unittest.TestCase):

    def test_spawn_creates_sentinel_buildings_guards(self):
        spawner, npc, bf, room, _ = _make_spawner()
        base = spawner.spawn_base("earth", "outpost", coords=(10, 10))
        self.assertIsNotNone(base)
        self.assertEqual(len(npc.sentinels), 1)
        # Template: HQ + WL buildings, 2 guards.
        self.assertEqual(len(bf.created), 2)
        self.assertEqual(len(npc.guards), 2)

    def test_spawn_places_buildings_at_offsets(self):
        spawner, npc, bf, room, _ = _make_spawner()
        spawner.spawn_base("earth", "outpost", coords=(10, 10))
        coords = {(b.db.coord_x, b.db.coord_y) for b in bf.created}
        self.assertEqual(coords, {(10, 10), (10, 11)})  # HQ + WL at offset (0,1)

    def test_spawn_applies_template_hp(self):
        spawner, npc, bf, room, _ = _make_spawner()
        spawner.spawn_base("earth", "outpost", coords=(10, 10))
        hq = next(b for b in bf.created if b.db.building_type == "HQ")
        # HP override goes through set_obj_attr → attributes (as the real
        # Building stores hp; db.hp is the same proxy in live Evennia).
        self.assertEqual(hq.attributes.get("hp"), 200)
        self.assertEqual(hq.attributes.get("hp_max"), 200)

    def test_guard_hp_by_tier(self):
        spawner, npc, bf, room, _ = _make_spawner()
        spawner.spawn_base("earth", "outpost", coords=(10, 10))
        self.assertTrue(all(g.db.hp == 80 for g in npc.guards))  # outpost_guard_hp

    def test_guards_get_distinct_incrementing_indices(self):
        """Each guard is created with a unique 1-based index so it can be named
        distinctly (the factory turns the index into a unique key)."""
        spawner, npc, bf, room, _ = _make_spawner()
        spawner.spawn_base("earth", "outpost", coords=(10, 10))
        indices = sorted(g.index for g in npc.guards)
        self.assertEqual(indices, [1, 2])  # two outpost guards → 1, 2
        npc.guards.clear()
        spawner.spawn_base("earth", "fortress", coords=(30, 30))
        self.assertTrue(all(g.db.hp == 150 for g in npc.guards))  # fortress_guard_hp

    def test_sentinel_stamped_with_tier_and_planet(self):
        spawner, npc, bf, room, _ = _make_spawner()
        spawner.spawn_base("earth", "outpost", coords=(10, 10))
        s = npc.sentinels[0]
        self.assertEqual(s.attributes.get("base_tier"), "outpost")
        self.assertEqual(s.attributes.get("base_planet"), "earth")

    def test_unknown_tier_returns_none(self):
        spawner, npc, bf, room, _ = _make_spawner()
        self.assertIsNone(spawner.spawn_base("earth", "citadel", coords=(10, 10)))
        self.assertEqual(len(npc.sentinels), 0)

    def test_spawn_initial_places_configured_counts(self):
        spawner, npc, bf, room, _ = _make_spawner()
        spawned = spawner.spawn_initial("earth")
        # outpost_count=5 + fortress_count=2 = 7 bases.
        self.assertEqual(len(spawned), 7)
        self.assertEqual(len(npc.sentinels), 7)


class TestPlacement(unittest.TestCase):

    def test_placement_rejects_impassable(self):
        # Block a large region so a valid multi-tile spot is impossible.
        blocked = {(x, y) for x in range(50) for y in range(50)}
        terrain = FakeTerrain(blocked=blocked)
        spawner, npc, bf, room, _ = _make_spawner(terrain=terrain)
        base = spawner.spawn_base("earth", "outpost")  # auto placement
        self.assertIsNone(base)

    def test_placement_rejects_non_buildable_terrain(self):
        # Flood the whole map with river tiles (passable but NOT buildable).
        # A base must never spawn on treacherous terrain the way a player could
        # never build there — so auto-placement finds nowhere and returns None.
        river = {(x, y) for x in range(50) for y in range(50)}
        terrain = FakeTerrain(river=river)
        spawner, npc, bf, room, _ = _make_spawner(terrain=terrain)
        self.assertIsNone(spawner.spawn_base("earth", "outpost"))
        self.assertEqual(len(npc.sentinels), 0)

    def test_placement_avoids_river_tile_for_offsets(self):
        # A single river tile at (0,1) must block a base whose HQ is at (0,0)
        # with a WL offset onto (0,1): _placement_valid rejects the footprint
        # because one occupied tile is non-buildable.
        terrain = FakeTerrain(river={(0, 1)})
        spawner, npc, bf, room, _ = _make_spawner(terrain=terrain)
        offsets = [(0, 0), (0, 1)]  # matches the outpost template footprint
        self.assertFalse(
            spawner._placement_valid("earth", room, 0, 0, offsets)
        )
        # A footprint clear of the river tile is fine.
        self.assertTrue(
            spawner._placement_valid("earth", room, 10, 10, offsets)
        )

    def test_placement_rejects_occupied_tile(self):
        spawner, npc, bf, room, _ = _make_spawner()
        # Occupy every tile so no placement is valid.
        for x in range(50):
            for y in range(50):
                room.place_building(object(), x, y)
        self.assertIsNone(spawner.spawn_base("earth", "outpost"))

    def test_bases_keep_separation(self):
        # Tiny planet so, after one base, a second can't keep separation.
        # 4x4 → max Manhattan distance is 6, below _MIN_BASE_SEPARATION (8), so
        # no second HQ can be placed far enough from the first.
        pr = FakePlanetRegistry(width=4, height=4)
        spawner, npc, bf, room, _ = _make_spawner(planet_registry=pr)
        first = spawner.spawn_base("earth", "outpost")
        self.assertIsNotNone(first)
        second = spawner.spawn_base("earth", "outpost")
        self.assertIsNone(second)

    def test_avoids_player_spawn_point(self):
        """No base may spawn within separation of the player spawn tile, so a
        new player never spawns inside a base. A 4x4 planet whose spawn point is
        the center leaves no valid tile far enough away."""
        pr = FakePlanetRegistry(width=4, height=4, spawn_x=2, spawn_y=2)
        spawner, npc, bf, room, _ = _make_spawner(planet_registry=pr)
        # Every tile on a 4x4 grid is within Manhattan 6 of (2,2) < separation 8.
        self.assertIsNone(spawner.spawn_base("earth", "outpost"))


class TestRespawn(unittest.TestCase):

    def test_base_eliminated_event_schedules_respawn(self):
        spawner, npc, bf, room, bus = _make_spawner(tick=100)
        bus.publish(BASE_ELIMINATED, sentinel=None, tier="outpost",
                    planet="earth", x=5, y=5)
        self.assertEqual(len(spawner._pending_respawns), 1)
        self.assertEqual(spawner._pending_respawns[0]["respawn_at"],
                         100 + spawner.registry.balance.outpost_respawn_ticks)

    def test_process_respawns_before_cooldown_noop(self):
        spawner, npc, bf, room, bus = _make_spawner(tick=100)
        bus.publish(BASE_ELIMINATED, sentinel=None, tier="outpost",
                    planet="earth", x=5, y=5)
        self.assertEqual(spawner.process_respawns(200), 0)  # 100+600=700 not due
        self.assertEqual(len(npc.sentinels), 0)

    def test_process_respawns_after_cooldown_spawns(self):
        spawner, npc, bf, room, bus = _make_spawner(tick=100)
        bus.publish(BASE_ELIMINATED, sentinel=None, tier="outpost",
                    planet="earth", x=5, y=5)
        n = spawner.process_respawns(100 + 600)  # exactly due
        self.assertEqual(n, 1)
        self.assertEqual(len(npc.sentinels), 1)
        self.assertEqual(len(spawner._pending_respawns), 0)

    def test_respawn_disabled_when_ticks_zero(self):
        r = _make_registry()
        r.balance = BalanceConfig(outpost_respawn_ticks=0)
        spawner, npc, bf, room, bus = _make_spawner(registry=r, tick=100)
        bus.publish(BASE_ELIMINATED, sentinel=None, tier="outpost",
                    planet="earth", x=5, y=5)
        self.assertEqual(len(spawner._pending_respawns), 0)

    def test_crowded_respawn_backs_off_not_every_tick(self):
        """A respawn that can't be placed (crowded) re-arms after a fraction of
        the cooldown, not the next tick — bounding placement work."""
        r = _make_registry()
        r.balance = BalanceConfig(outpost_respawn_ticks=600)
        spawner, npc, bf, room, bus = _make_spawner(registry=r, tick=100)
        # Occupy every tile so placement always fails.
        for x in range(50):
            for y in range(50):
                room.place_building(object(), x, y)
        bus.publish(BASE_ELIMINATED, sentinel=None, tier="outpost",
                    planet="earth", x=5, y=5)
        due_at = spawner._pending_respawns[0]["respawn_at"]  # 700
        spawner.process_respawns(due_at)  # fails to place -> re-armed
        self.assertEqual(len(spawner._pending_respawns), 1)
        # Re-armed by 600 // 4 = 150 ticks, not +1.
        self.assertEqual(spawner._pending_respawns[0]["respawn_at"], due_at + 150)


class _PersistRoom(FakeRoom):
    """A FakeRoom that also supports attribute persistence (db/attributes)."""
    def __init__(self, planet="earth"):
        super().__init__(planet)
        self._store = {}
        self.attributes = FakeAttrs(self._store)


class TestPersistence(unittest.TestCase):
    """Req 7.6: pending respawns + active bases survive a server restart."""

    def _spawner_with_room(self, room, registry=None, tick=0):
        registry = registry or _make_registry()
        bus = EventBus()
        npc = FakeNpcFactory()
        bf = FakeBuildingFactory()
        spawner = OutpostSpawnerSystem(
            registry, bus,
            npc_base_factory=npc, building_factory=bf,
            terrain_provider=FakeTerrain(),
            planet_rooms_provider=lambda: {"earth": room},
            planet_registry=FakePlanetRegistry(),
            rng=random.Random(1),
            current_tick_func=lambda: tick,
        )
        return spawner, npc, bf, bus

    def test_pending_respawn_persisted_to_room(self):
        room = _PersistRoom("earth")
        spawner, npc, bf, bus = self._spawner_with_room(room, tick=100)
        bus.publish(BASE_ELIMINATED, sentinel=None, tier="outpost",
                    planet="earth", x=5, y=5)
        stored = room.attributes.get("npc_base_pending_respawns")
        self.assertTrue(stored)
        self.assertEqual(stored[0]["tier"], "outpost")
        self.assertEqual(stored[0]["respawn_at"], 700)

    def test_rebuild_reloads_pending_and_respawns_after_restart(self):
        # First "boot": schedule a respawn, which persists onto the room.
        room = _PersistRoom("earth")
        s1, npc1, bf1, bus1 = self._spawner_with_room(room, tick=100)
        bus1.publish(BASE_ELIMINATED, sentinel=None, tier="outpost",
                     planet="earth", x=5, y=5)

        # Second "boot": a brand-new spawner (empty in-memory state) rebuilds
        # from the persisted room attribute — the pending respawn survives.
        s2, npc2, bf2, bus2 = self._spawner_with_room(room, tick=700)
        self.assertEqual(len(s2._pending_respawns), 0)  # nothing yet in-memory
        s2.rebuild_from_world([])  # no surviving sentinels, but pending reload
        self.assertEqual(len(s2._pending_respawns), 1)
        # The cooldown is now due -> it respawns (world does not empty out).
        self.assertEqual(s2.process_respawns(700), 1)
        self.assertEqual(len(npc2.sentinels), 1)

    def test_rebuild_repopulates_active_bases_from_sentinels(self):
        room = _PersistRoom("earth")
        s1, npc1, bf1, bus1 = self._spawner_with_room(room)
        s1.spawn_base("earth", "outpost", coords=(10, 10))
        sentinel = npc1.sentinels[0]

        # New spawner after restart: rebuild active bases from the sentinel so
        # the separation check still knows the base is there.
        s2, npc2, bf2, bus2 = self._spawner_with_room(room)
        self.assertEqual(len(s2._active_bases), 0)
        s2.rebuild_from_world([sentinel])
        self.assertEqual(len(s2._active_bases), 1)
        rec = next(iter(s2._active_bases.values()))
        self.assertEqual(rec["tier"], "outpost")
        self.assertEqual((rec["x"], rec["y"]), (10, 10))


# -------------------------------------------------------------- #
#  Base elimination tests
# -------------------------------------------------------------- #

class FakePlayer:
    def __init__(self, oid=1, combat_xp=0):
        self.key = "Raider"
        self.id = oid
        self.db = types.SimpleNamespace(combat_xp=combat_xp)


def _make_handler(registry=None, event_bus=None, owned=None, loot_sink=None):
    registry = registry or _make_registry()
    event_bus = event_bus or EventBus()
    drops = loot_sink if loot_sink is not None else []

    def loot_drop(room, resource, amount, x, y):
        drops.append((resource, amount, x, y))

    handler = BaseEliminationHandler(
        registry, event_bus,
        owned_entities_provider=lambda s: (owned or {}).get(s, []),
        loot_drop_func=loot_drop,
    )
    return handler, event_bus, drops


class TestBaseElimination(unittest.TestCase):

    def _sentinel_hq(self, room, tier="outpost"):
        sentinel = FakeSentinel("Outpost #1", room, "earth")
        sentinel.db.base_tier = tier
        sentinel.db.base_planet = "earth"
        sentinel.attributes.add("base_tier", tier)
        sentinel.attributes.add("base_planet", "earth")
        hqdef = BuildingDef(name="HQ", abbreviation="HQ", cost={}, max_health=200,
                            requires_hq=False, required_terrain=None, category="hq",
                            produces=None, capabilities=frozenset({"headquarters"}))
        hq = FakeBuilding(hqdef, sentinel, 5, 5, room)
        return sentinel, hq

    def test_npc_hq_destruction_wipes_base(self):
        room = FakeRoom()
        sentinel, hq = self._sentinel_hq(room)
        wall = FakeBuilding(
            BuildingDef(name="Wall", abbreviation="WL", cost={}, max_health=300,
                        requires_hq=True, required_terrain=None, category="defense",
                        produces=None, capabilities=frozenset()),
            sentinel, 5, 6, room)
        guard = FakeGuard(sentinel, room, 5, 5, "guard", 80)
        handler, bus, drops = _make_handler(owned={sentinel: [hq, wall, guard]})

        bus.publish(BUILDING_DESTROYED, building=hq,
                    attacker=FakePlayer(oid=2), tile=room)

        self.assertTrue(wall.deleted)
        self.assertTrue(guard.deleted)
        self.assertTrue(sentinel.deleted)
        self.assertFalse(hq.deleted)  # HQ deleted by combat engine, not here

    def test_destroyer_awarded_xp_hq_destroy(self):
        room = FakeRoom()
        sentinel, hq = self._sentinel_hq(room)
        handler, bus, drops = _make_handler(owned={sentinel: [hq]})
        raider = FakePlayer(oid=2, combat_xp=100)
        bus.publish(BUILDING_DESTROYED, building=hq, attacker=raider, tile=room)
        self.assertEqual(raider.db.combat_xp, 100 + 300)  # xp_hq_destroy=300

    def test_loot_dropped_at_hq_tile(self):
        room = FakeRoom()
        sentinel, hq = self._sentinel_hq(room)
        handler, bus, drops = _make_handler(owned={sentinel: [hq]})
        bus.publish(BUILDING_DESTROYED, building=hq, attacker=FakePlayer(oid=2),
                    tile=room)
        # Outpost loot: Iron 30, Stone 20 at (5,5).
        self.assertIn(("Iron", 30, 5, 5), drops)
        self.assertIn(("Stone", 20, 5, 5), drops)

    def test_base_eliminated_event_published(self):
        room = FakeRoom()
        sentinel, hq = self._sentinel_hq(room)
        handler, bus, drops = _make_handler(owned={sentinel: [hq]})
        events = []
        bus.subscribe(BASE_ELIMINATED, lambda **kw: events.append(kw))
        bus.publish(BUILDING_DESTROYED, building=hq, attacker=FakePlayer(oid=2),
                    tile=room)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["tier"], "outpost")
        self.assertEqual(events[0]["planet"], "earth")

    def test_player_hq_not_wiped(self):
        """A player-owned HQ (no is_sentinel) follows PvP deactivation, not wipe."""
        room = FakeRoom()
        player = FakePlayer(oid=9)  # not a sentinel
        hqdef = BuildingDef(name="HQ", abbreviation="HQ", cost={}, max_health=200,
                            requires_hq=False, required_terrain=None, category="hq",
                            produces=None, capabilities=frozenset({"headquarters"}))
        hq = FakeBuilding(hqdef, player, 5, 5, room)
        other = FakeBuilding(hqdef, player, 5, 6, room)
        handler, bus, drops = _make_handler(owned={player: [hq, other]})
        bus.publish(BUILDING_DESTROYED, building=hq, attacker=FakePlayer(oid=2),
                    tile=room)
        self.assertFalse(other.deleted)  # PvP: base left intact
        self.assertEqual(drops, [])       # no loot on a player base

    def test_hq_skipped_by_id_not_identity(self):
        """owned_entities re-queries the DB; a DISTINCT instance with the same
        .id as the HQ must still be skipped (not re-deleted) — the combat engine
        owns the HQ deletion."""
        room = FakeRoom()
        sentinel, hq = self._sentinel_hq(room)
        # A distinct object sharing the HQ's id (idmapper re-fetch).
        hq_proxy = FakeBuilding(
            BuildingDef(name="HQ", abbreviation="HQ", cost={}, max_health=200,
                        requires_hq=False, required_terrain=None, category="hq",
                        produces=None, capabilities=frozenset({"headquarters"})),
            sentinel, 5, 5, room)
        hq_proxy.id = hq.id  # same PK, different instance
        handler, bus, drops = _make_handler(owned={sentinel: [hq_proxy]})
        bus.publish(BUILDING_DESTROYED, building=hq, attacker=FakePlayer(oid=2),
                    tile=room)
        # The proxy is recognized as the HQ and skipped (combat engine deletes
        # the real HQ) — so it must NOT be deleted here.
        self.assertFalse(hq_proxy.deleted)
        self.assertTrue(sentinel.deleted)  # sentinel still wiped

    def test_non_hq_building_ignored(self):
        """Destroying a non-HQ sentinel building does NOT wipe the base."""
        room = FakeRoom()
        sentinel, hq = self._sentinel_hq(room)
        walldef = BuildingDef(name="Wall", abbreviation="WL", cost={},
                              max_health=300, requires_hq=True, required_terrain=None,
                              category="defense", produces=None,
                              capabilities=frozenset())
        wall = FakeBuilding(walldef, sentinel, 5, 6, room)
        handler, bus, drops = _make_handler(owned={sentinel: [hq, wall]})
        bus.publish(BUILDING_DESTROYED, building=wall, attacker=FakePlayer(oid=2),
                    tile=room)
        self.assertFalse(sentinel.deleted)  # base survives
        self.assertFalse(hq.deleted)


if __name__ == "__main__":
    unittest.main()


# -------------------------------------------------------------- #
#  Gear-drop resolution (R8.3/R8.4 + R11.5 anti-silent-no-op)
# -------------------------------------------------------------- #

class TestGearDropResolution(unittest.TestCase):
    """A won gear roll must produce a real ItemDef-backed drop, never a
    silent no-op. Regression for the bug where `_spawn_gear_item` passed a
    raw key string to `spawn_gear_drop` (which expects an ItemDef) and
    swallowed the resulting AttributeError at debug level — every gear drop
    vanished silently."""

    def _handler_with_items(self):
        registry = _make_registry()
        # Registry item definitions the gear pools resolve against.
        registry.items = {
            "combat_knife": types.SimpleNamespace(
                key="combat_knife", name="Combat Knife"),
        }
        handler, bus, drops = _make_handler(registry=registry)
        return handler, registry

    def test_won_roll_resolves_itemdef_and_spawns(self):
        """_spawn_gear_item resolves the key to its ItemDef and passes the
        DEF (not the string) to spawn_gear_drop."""
        import typeclasses.objects as objects_mod
        handler, registry = self._handler_with_items()
        room = FakeRoom()
        calls = []

        original = getattr(objects_mod, "spawn_gear_drop", None)
        objects_mod.spawn_gear_drop = (
            lambda location, item_def, x=None, y=None:
            calls.append((location, item_def, x, y)) or object()
        )
        try:
            handler._spawn_gear_item(room, "combat_knife", 5, 5)
        finally:
            if original is not None:
                objects_mod.spawn_gear_drop = original
            else:
                del objects_mod.spawn_gear_drop

        self.assertEqual(len(calls), 1, "a won roll must spawn exactly once")
        _, item_def, x, y = calls[0]
        self.assertIs(item_def, registry.items["combat_knife"],
                      "spawn_gear_drop must receive the resolved ItemDef, "
                      "not the key string")
        self.assertEqual((x, y), (5, 5))

    def test_unknown_key_logs_error_and_never_spawns(self):
        """An unknown pool key (should be impossible post-R11.5 validation)
        is an ERROR, not a silent debug line — and nothing spawns."""
        import typeclasses.objects as objects_mod
        handler, registry = self._handler_with_items()
        room = FakeRoom()
        calls = []

        original = getattr(objects_mod, "spawn_gear_drop", None)
        objects_mod.spawn_gear_drop = (
            lambda location, item_def, x=None, y=None:
            calls.append(item_def) or object()
        )
        try:
            with self.assertLogs(
                "evennia.world.systems.base_elimination", level="ERROR"
            ):
                handler._spawn_gear_item(room, "no_such_item", 5, 5)
        finally:
            if original is not None:
                objects_mod.spawn_gear_drop = original
            else:
                del objects_mod.spawn_gear_drop

        self.assertEqual(calls, [], "an unresolved key must never spawn")

    def test_gear_roll_uses_template_chance_and_pool(self):
        """_try_gear_drops with chance=1.0 always spawns from the pool."""
        import typeclasses.objects as objects_mod
        handler, registry = self._handler_with_items()
        room = FakeRoom()
        template = types.SimpleNamespace(
            gear_drop_chance=1.0, gear_pool=["combat_knife"],
            rare_gear_chance=0.0, rare_pool=[],
        )
        calls = []
        original = getattr(objects_mod, "spawn_gear_drop", None)
        objects_mod.spawn_gear_drop = (
            lambda location, item_def, x=None, y=None:
            calls.append(item_def) or object()
        )
        try:
            handler._try_gear_drops(room, template, 3, 4)
        finally:
            if original is not None:
                objects_mod.spawn_gear_drop = original
            else:
                del objects_mod.spawn_gear_drop
        self.assertEqual(len(calls), 1)
        self.assertIs(calls[0], registry.items["combat_knife"])

    def test_gear_rolls_multiplies_drops(self):
        """gear_rolls=N runs N independent (gear+rare) rounds — a difficult base
        rains several upgrades. With chance 1.0 and gear_rolls=3, three drops."""
        import typeclasses.objects as objects_mod
        handler, registry = self._handler_with_items()
        room = FakeRoom()
        template = types.SimpleNamespace(
            gear_drop_chance=1.0, gear_pool=["combat_knife"],
            rare_gear_chance=0.0, rare_pool=[], gear_rolls=3,
        )
        calls = []
        original = getattr(objects_mod, "spawn_gear_drop", None)
        objects_mod.spawn_gear_drop = (
            lambda location, item_def, x=None, y=None:
            calls.append(item_def) or object()
        )
        try:
            handler._try_gear_drops(room, template, 3, 4)
        finally:
            if original is not None:
                objects_mod.spawn_gear_drop = original
            else:
                del objects_mod.spawn_gear_drop
        self.assertEqual(len(calls), 3, "gear_rolls=3 → three gear drops")


# -------------------------------------------------------------- #
#  Difficulty-scaled rewards (XP + spawn count by tier)
# -------------------------------------------------------------- #

class TestDifficultyScaledRewards(unittest.TestCase):

    def _sentinel_hq(self, room, tier):
        sentinel = FakeSentinel(f"{tier} #1", room, "earth")
        sentinel.db.base_tier = tier
        sentinel.db.base_planet = "earth"
        sentinel.attributes.add("base_tier", tier)
        sentinel.attributes.add("base_planet", "earth")
        hqdef = BuildingDef(name="HQ", abbreviation="HQ", cost={}, max_health=200,
                            requires_hq=False, required_terrain=None, category="hq",
                            produces=None, capabilities=frozenset({"headquarters"}))
        hq = FakeBuilding(hqdef, sentinel, 5, 5, room)
        return sentinel, hq

    def test_template_xp_reward_overrides_balance_default(self):
        """A tier's xp_reward is paid instead of the balance xp_hq_destroy — a
        fortress (xp_reward=800) pays far more than the 300 default."""
        room = FakeRoom()
        sentinel, hq = self._sentinel_hq(room, "fortress")
        handler, bus, drops = _make_handler(owned={sentinel: [hq]})
        raider = FakePlayer(oid=2, combat_xp=0)
        bus.publish(BUILDING_DESTROYED, building=hq, attacker=raider, tile=room)
        self.assertEqual(raider.db.combat_xp, 800)  # template override, not 300

    def test_outpost_uses_balance_xp_default(self):
        """A tier WITHOUT xp_reward falls back to balance xp_hq_destroy (300)."""
        room = FakeRoom()
        sentinel, hq = self._sentinel_hq(room, "outpost")  # no xp_reward
        handler, bus, drops = _make_handler(owned={sentinel: [hq]})
        raider = FakePlayer(oid=2, combat_xp=0)
        bus.publish(BUILDING_DESTROYED, building=hq, attacker=raider, tile=room)
        self.assertEqual(raider.db.combat_xp, 300)

    def test_spawn_count_from_template_and_class_fallback(self):
        """spawn_initial places each tier's spawn_count; a template without one
        falls back to the balance count for its difficulty_class."""
        spawner, npc, bf, room, _ = _make_spawner()
        # Give the outpost an explicit spawn_count; leave fortress to fall back
        # to fortress_count (=2, the balance default).
        spawner.registry.base_templates["outpost"].spawn_count = 3
        spawner.registry.base_templates["fortress"].spawn_count = None
        spawned = spawner.spawn_initial("earth")
        tiers = [s["tier"] for s in spawned]
        self.assertEqual(tiers.count("outpost"), 3)   # explicit spawn_count
        self.assertEqual(tiers.count("fortress"), 2)  # fortress_count fallback


# -------------------------------------------------------------- #
#  Staleness decay — disturbed bases refresh after outpost_stale_ticks
# -------------------------------------------------------------- #

def _make_stale_spawner(stale_ticks=100):
    """Spawner with a mutable tick + an owned-entities provider for wipe tests."""
    from world.event_bus import COMBAT_ACTION
    registry = _make_registry()
    registry.balance.outpost_stale_ticks = stale_ticks
    event_bus = EventBus()
    room = FakeRoom("earth")
    npc_factory = FakeNpcFactory()
    building_factory = FakeBuildingFactory()
    clock = {"t": 0}
    spawner = OutpostSpawnerSystem(
        registry, event_bus,
        npc_base_factory=npc_factory,
        building_factory=building_factory,
        terrain_provider=FakeTerrain(),
        planet_rooms_provider=lambda: {"earth": room},
        planet_registry=FakePlanetRegistry(),
        rng=random.Random(1),
        current_tick_func=lambda: clock["t"],
        owned_entities_provider=lambda s: list(s.get_buildings()),
    )
    return spawner, npc_factory, room, event_bus, clock, COMBAT_ACTION


class TestStalenessDecay(unittest.TestCase):

    def test_pristine_base_has_no_timer(self):
        spawner, npc, room, bus, clock, _ = _make_stale_spawner()
        rec = spawner.spawn_base("earth", "outpost", coords=(10, 10))
        self.assertEqual(rec["disturbed_at"], 0)
        self.assertIsNone(spawner.ticks_remaining(rec, clock["t"]))

    def test_combat_action_starts_timer_once(self):
        spawner, npc, room, bus, clock, COMBAT_ACTION = _make_stale_spawner()
        rec = spawner.spawn_base("earth", "outpost", coords=(10, 10))
        guard = npc.guards[0]  # a base guard, owned by the sentinel
        clock["t"] = 50
        bus.publish(COMBAT_ACTION, target=guard, damage=5)
        self.assertEqual(rec["disturbed_at"], 50)
        # A later hit does NOT reset the clock — it runs from first disturbance.
        clock["t"] = 90
        bus.publish(COMBAT_ACTION, target=guard, damage=5)
        self.assertEqual(rec["disturbed_at"], 50)

    def test_zero_damage_does_not_start_timer(self):
        spawner, npc, room, bus, clock, COMBAT_ACTION = _make_stale_spawner()
        rec = spawner.spawn_base("earth", "outpost", coords=(10, 10))
        bus.publish(COMBAT_ACTION, target=npc.guards[0], damage=0)
        self.assertEqual(rec["disturbed_at"], 0)

    def test_stale_base_wiped_and_regenerated(self):
        spawner, npc, room, bus, clock, COMBAT_ACTION = _make_stale_spawner(
            stale_ticks=100)
        rec = spawner.spawn_base("earth", "outpost", coords=(10, 10))
        old_sentinel = rec["sentinel"]
        clock["t"] = 10
        bus.publish(COMBAT_ACTION, target=npc.guards[0], damage=5)
        # Before the deadline: nothing happens.
        clock["t"] = 100
        self.assertEqual(spawner.process_stale(clock["t"]), 0)
        self.assertFalse(old_sentinel.deleted)
        # After the deadline (10 + 100): wiped + a fresh base spawned.
        clock["t"] = 111
        self.assertEqual(spawner.process_stale(clock["t"]), 1)
        self.assertTrue(old_sentinel.deleted)
        self.assertEqual(len(spawner._active_bases), 1)  # the regenerated one
        new_rec = next(iter(spawner._active_bases.values()))
        self.assertEqual(new_rec["disturbed_at"], 0)  # fresh base is pristine

    def test_undisturbed_base_never_wiped(self):
        spawner, npc, room, bus, clock, _ = _make_stale_spawner(stale_ticks=100)
        rec = spawner.spawn_base("earth", "outpost", coords=(10, 10))
        clock["t"] = 100000  # far past any deadline
        self.assertEqual(spawner.process_stale(clock["t"]), 0)
        self.assertFalse(rec["sentinel"].deleted)

    def test_stale_disabled_when_ticks_zero(self):
        spawner, npc, room, bus, clock, COMBAT_ACTION = _make_stale_spawner(
            stale_ticks=0)
        rec = spawner.spawn_base("earth", "outpost", coords=(10, 10))
        bus.publish(COMBAT_ACTION, target=npc.guards[0], damage=5)
        clock["t"] = 999999
        self.assertEqual(spawner.process_stale(clock["t"]), 0)

    def test_bases_near_reports_type_and_countdown(self):
        spawner, npc, room, bus, clock, COMBAT_ACTION = _make_stale_spawner(
            stale_ticks=100)
        spawner.spawn_base("earth", "outpost", coords=(10, 10))
        # Pristine → within range, no countdown.
        near = spawner.bases_near("earth", 12, 12, 20, clock["t"])
        self.assertEqual(len(near), 1)
        self.assertEqual(near[0]["name"], "Outpost")
        self.assertIsNone(near[0]["ticks_remaining"])
        # Out of range → not reported.
        self.assertEqual(spawner.bases_near("earth", 40, 40, 20, clock["t"]), [])
        # Disturbed → countdown surfaces.
        clock["t"] = 10
        bus.publish(COMBAT_ACTION, target=npc.guards[0], damage=5)
        near = spawner.bases_near("earth", 12, 12, 20, clock["t"])
        self.assertEqual(near[0]["ticks_remaining"], 100)  # 10 + 100 - 10

    def test_proximity_refresh_wipes_expired(self):
        spawner, npc, room, bus, clock, COMBAT_ACTION = _make_stale_spawner(
            stale_ticks=100)
        rec = spawner.spawn_base("earth", "outpost", coords=(10, 10))
        key = rec["sentinel"].id
        clock["t"] = 10
        bus.publish(COMBAT_ACTION, target=npc.guards[0], damage=5)
        self.assertTrue(spawner.is_active(key))
        self.assertTrue(spawner.refresh_base_by_key(key))
        self.assertTrue(rec["sentinel"].deleted)
        self.assertFalse(spawner.is_active(key))  # old key gone


class TestSpecVersionPurge(unittest.TestCase):

    def test_purge_wipes_only_outdated(self):
        spawner, npc, room, bus, clock, _ = _make_stale_spawner()
        cur = spawner.spawn_base("earth", "outpost", coords=(10, 10))
        # An old-spec sentinel (version 1) not tracked in _active_bases.
        from world.utils import set_obj_attr
        old = FakeSentinel("Old #1", room, "earth")
        set_obj_attr(old, "base_spec_version", 1)
        purged = spawner.purge_outdated_bases([cur["sentinel"], old])
        self.assertEqual(purged, 1)
        self.assertTrue(old.deleted)
        self.assertFalse(cur["sentinel"].deleted)  # current-spec base survives


class TestForgetDeadBases(unittest.TestCase):
    """forget_dead_bases drops records whose sentinel was deleted externally
    (e.g. by an admin obliterate) so no phantom base lingers in tracking."""

    def test_forgets_base_whose_sentinel_was_deleted(self):
        spawner, npc, room, bus, clock, _ = _make_stale_spawner()
        base = spawner.spawn_base("earth", "outpost", coords=(10, 10))
        self.assertEqual(len(spawner._active_bases), 1)
        # Simulate an external delete of the HQ (obliterate): pk → None.
        base["sentinel"].delete()
        forgotten = spawner.forget_dead_bases()
        self.assertEqual(forgotten, 1)
        self.assertEqual(len(spawner._active_bases), 0)

    def test_keeps_live_bases(self):
        spawner, npc, room, bus, clock, _ = _make_stale_spawner()
        spawner.spawn_base("earth", "outpost", coords=(10, 10))
        forgotten = spawner.forget_dead_bases()
        self.assertEqual(forgotten, 0)
        self.assertEqual(len(spawner._active_bases), 1)


class TestWipeBasesInArea(unittest.TestCase):
    """wipe_bases_in_area removes bases whose HQ is in a box, as whole units —
    the fix for 'obliterate left the base in @outpost list' (the Sentinel owner
    isn't a tile actor, so a tile-only sweep never deleted it)."""

    def test_wipes_base_in_box_and_deletes_sentinel(self):
        spawner, npc, room, bus, clock, _ = _make_stale_spawner()
        base = spawner.spawn_base("earth", "outpost", coords=(10, 10))
        sentinel = base["sentinel"]
        wiped = spawner.wipe_bases_in_area("earth", 8, 8, 12, 12)
        self.assertEqual(wiped, 1)
        self.assertEqual(len(spawner._active_bases), 0)  # untracked
        self.assertTrue(sentinel.deleted)                # sentinel gone as a unit

    def test_leaves_bases_outside_box(self):
        spawner, npc, room, bus, clock, _ = _make_stale_spawner()
        spawner.spawn_base("earth", "outpost", coords=(10, 10))
        wiped = spawner.wipe_bases_in_area("earth", 100, 100, 110, 110)
        self.assertEqual(wiped, 0)
        self.assertEqual(len(spawner._active_bases), 1)

    def test_does_not_respawn(self):
        # Unlike the staleness refresh, an area clear must NOT re-seed.
        spawner, npc, room, bus, clock, _ = _make_stale_spawner()
        spawner.spawn_base("earth", "outpost", coords=(10, 10))
        spawner.wipe_bases_in_area("earth", 8, 8, 12, 12)
        self.assertEqual(len(spawner._active_bases), 0)
        self.assertEqual(len(spawner._pending_respawns), 0)

    def test_scoped_to_planet(self):
        spawner, npc, room, bus, clock, _ = _make_stale_spawner()
        spawner.spawn_base("earth", "outpost", coords=(10, 10))
        # Same box, different planet → no match.
        wiped = spawner.wipe_bases_in_area("mars", 8, 8, 12, 12)
        self.assertEqual(wiped, 0)
        self.assertEqual(len(spawner._active_bases), 1)
