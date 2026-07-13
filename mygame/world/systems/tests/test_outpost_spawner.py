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

    def get_buildings(self):
        return list(self._buildings)

    def delete(self):
        self.deleted = True


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
    """Terrain provider: everything passable unless a tile is blocked."""
    def __init__(self, blocked=None):
        self._blocked = set(blocked or [])

    def get_terrain_and_resource(self, planet, x, y):
        return ("blocked" if (x, y) in self._blocked else "grass"), None


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
    # Terrain defs for passability lookups.
    r.terrain = {
        "grass": types.SimpleNamespace(passable=True),
        "blocked": types.SimpleNamespace(passable=False),
    }
    r.get_terrain = lambda t: r.terrain[t]
    r.base_templates = {
        "outpost": BaseTemplateDef(
            tier="outpost", display_name="Outpost",
            buildings=[
                TemplateBuildingDef("HQ", (0, 0), hp=200),
                TemplateBuildingDef("WL", (0, 1), hp=300),
            ],
            guards=[TemplateGuardDef("guard", "melee", 2)],
            loot={"Iron": 30, "Stone": 20},
        ),
        "fortress": BaseTemplateDef(
            tier="fortress", display_name="Fortress",
            buildings=[TemplateBuildingDef("HQ", (0, 0), hp=600)],
            guards=[TemplateGuardDef("soldier", "ranged", 3)],
            loot={"Iron": 100},
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
        self.assertEqual(raider.db.combat_xp, 100 + 500)  # xp_hq_destroy=500

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
