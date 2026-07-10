"""
End-to-end integration test for the PvE NPC-bases raid loop (Phase 6, task 6.3).

Wires the four real systems together — OutpostSpawnerSystem (Phase 5),
GuardCombatSystem (Phase 3), CombatEngine (Phases 1/4), and
BaseEliminationHandler (Phase 5) — over a single event bus and drives the whole
loop with lightweight fakes at the Evennia boundary:

    spawn an outpost -> player approaches -> guards attack back ->
    player destroys the HQ -> base wipes (buildings + guards + sentinel) ->
    XP + loot awarded -> respawn queued -> respawn fires after cooldown.

The fakes use a UNIFIED attribute store (db.* and attributes.get/add read the
same dict, like a real Evennia object) so the real CombatEngine's HP reads/
writes and the elimination handler's capability/owner lookups all see
consistent state.

Requirements: 12.6 (and exercises 1, 3, 4, 6, 7 together).
"""

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
from mygame.world.systems.guard_combat_system import GuardCombatSystem  # noqa: E402
from mygame.world.systems.combat_engine import CombatEngine  # noqa: E402
from mygame.world.data_registry import DataRegistry  # noqa: E402
from mygame.world.definitions import (  # noqa: E402
    BalanceConfig, BuildingDef, BaseTemplateDef,
    TemplateBuildingDef, TemplateGuardDef,
)
from mygame.world.event_bus import EventBus, BASE_ELIMINATED  # noqa: E402


# -------------------------------------------------------------- #
#  Unified-store fakes (db.* and attributes.get/add share one dict)
# -------------------------------------------------------------- #

class _Attrs:
    def __init__(self, store):
        self._s = store

    def get(self, key, default=None):
        return self._s.get(key, default)

    def add(self, key, value):
        self._s[key] = value


class _Db:
    def __init__(self, store):
        object.__setattr__(self, "_s", store)

    def __getattr__(self, key):
        # Raise for unset keys (like a real Evennia object) so hasattr(db, k)
        # is meaningful — is_player()/is_building() rely on hasattr(db,
        # "combat_xp") to tell a player/agent from a building. getattr(db, k,
        # default) still works (it catches this AttributeError).
        store = object.__getattribute__(self, "_s")
        if key in store:
            return store[key]
        raise AttributeError(key)

    def __setattr__(self, key, value):
        object.__getattribute__(self, "_s")[key] = value


class _Entity:
    """Base fake with a unified db/attributes store and delete tracking."""
    _next_id = 1

    def __init__(self, **initial):
        self._store = dict(initial)
        self.db = _Db(self._store)
        self.attributes = _Attrs(self._store)
        self.deleted = False
        _Entity._next_id += 1
        self.id = _Entity._next_id
        self.location = None

    def delete(self):
        self.deleted = True


class FakeWeapon:
    def __init__(self, damage, weapon_range, weapon_type="ranged", key="rifle"):
        self.key = key
        self.slot = "weapon"
        self.weapon_type = weapon_type
        self.ammo_type = None
        self.ammo_cost = None
        self.stat_modifiers = {"damage": damage, "range": weapon_range}

    def get_stat(self, name, default=0):
        return float(self.stat_modifiers.get(name, default))


class FakeEquipment:
    def __init__(self, weapon=None):
        self._weapon = weapon

    def get_equipped(self, slot):
        return self._weapon if slot == "weapon" else None

    def get_stat_total(self, stat):
        return 0.0


class FakePlayer(_Entity):
    def __init__(self, name="Raider", x=0, y=0, weapon=None):
        super().__init__(coord_x=x, coord_y=y, combat_xp=0, hp=100, hp_max=100,
                         combat_lockout_tick=0, active_powerups={})
        self.key = name
        self.has_account = True
        self.equipment = FakeEquipment(weapon)
        self._messages = []

    def msg(self, text):
        self._messages.append(text)


class FakeBuilding(_Entity):
    def __init__(self, bdef, owner, x, y, room):
        super().__init__(
            building_type=bdef.abbreviation, owner=owner, coord_x=x, coord_y=y,
            hp=bdef.max_health, hp_max=bdef.max_health, under_construction=False,
            offline=False,
        )
        self.key = bdef.name
        self.location = room

    @property
    def owner(self):
        return self._store.get("owner")

    @property
    def is_offline(self):
        return bool(self._store.get("offline", False))


class FakeGuard(_Entity):
    def __init__(self, owner, room, x, y, role, hp):
        super().__init__(
            owner=owner, npc_type="enemy", role=role, coord_x=x, coord_y=y,
            hp=hp, hp_max=hp, combat_xp=0, reserve=False, incapacitated=False,
            combat_lockout_tick=0, active_powerups={},
        )
        self.key = f"{role.title()} guard"
        self.location = room

    def msg(self, text):
        pass


class FakeSentinel(_Entity):
    def __init__(self, name, room, planet):
        super().__init__(is_sentinel=True, coord_planet=planet, combat_xp=0)
        self.key = name
        self.location = room
        self._buildings = []

    def get_buildings(self):
        return [b for b in self._buildings if not b.deleted]

    def msg(self, text):
        pass


class FakeCoordIndex:
    def add(self, obj, x, y):
        pass


class FakeRoom:
    def __init__(self, planet="earth"):
        self._planet = planet
        self.coord_index = FakeCoordIndex()
        self._buildings_at = {}
        self._players = []  # live players for get_nearby_players

    @property
    def planet_name(self):
        return self._planet

    def get_buildings_at(self, x, y):
        return self._buildings_at.get((int(x), int(y)), [])

    def place_building(self, b, x, y):
        self._buildings_at.setdefault((int(x), int(y)), []).append(b)

    def add_player(self, p):
        self._players.append(p)

    def get_nearby_players(self, x, y, radius):
        near = []
        for p in self._players:
            px, py = p.db.coord_x, p.db.coord_y
            if abs(int(px) - x) + abs(int(py) - y) <= radius:
                near.append(p)
        return near


class FakeNpcFactory:
    def __init__(self):
        self.sentinels = []
        self.guards = []

    def create_sentinel(self, name, tile, planet):
        s = FakeSentinel(name, tile, planet)
        self.sentinels.append(s)
        return s

    def create_enemy_guard(self, owner, tile, x, y, role, hp):
        g = FakeGuard(owner, tile, x, y, role, hp)
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
        if hasattr(owner, "_buildings"):
            owner._buildings.append(b)
        return b


class FakeSpace:
    def __init__(self, width=50, height=50, spawn_x=0, spawn_y=0):
        self.width = width
        self.height = height
        self.spawn_x = spawn_x
        self.spawn_y = spawn_y


class FakePlanetRegistry:
    def __init__(self):
        self._space = FakeSpace()

    def get_space(self, planet):
        return self._space

    def is_valid_coordinate(self, x, y, planet):
        return 0 <= x < self._space.width and 0 <= y < self._space.height


def _make_registry():
    r = DataRegistry()
    r.balance = BalanceConfig()
    r.buildings = {
        "HQ": BuildingDef(name="Headquarters", abbreviation="HQ",
                          cost={"Wood": 10}, max_health=200, requires_hq=False,
                          required_terrain=None, category="hq", produces=None,
                          capabilities=frozenset({"headquarters"})),
        "WL": BuildingDef(name="Wall", abbreviation="WL", cost={"Stone": 5},
                          max_health=300, requires_hq=True, required_terrain=None,
                          category="defense", produces=None,
                          capabilities=frozenset()),
    }
    r.terrain = {"grass": types.SimpleNamespace(passable=True)}
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
    }
    return r


class TestPvERaidLoop(unittest.TestCase):
    """The whole raid loop across all four systems on one event bus."""

    def setUp(self):
        self.registry = _make_registry()
        self.bus = EventBus()
        self.room = FakeRoom("earth")
        self.tick = 100

        self.npc_factory = FakeNpcFactory()
        self.building_factory = FakeBuildingFactory()

        self.engine = CombatEngine(
            self.registry, self.bus, current_tick_func=lambda: self.tick,
        )
        self.guard_ai = GuardCombatSystem(
            self.registry, self.bus, combat_engine=self.engine,
        )
        self.spawner = OutpostSpawnerSystem(
            self.registry, self.bus,
            npc_base_factory=self.npc_factory,
            building_factory=self.building_factory,
            terrain_provider=None,
            planet_rooms_provider=lambda: {"earth": self.room},
            planet_registry=FakePlanetRegistry(),
            current_tick_func=lambda: self.tick,
        )
        self.loot_drops = []

        def _owned(sentinel):
            return (sentinel.get_buildings()
                    + [g for g in self.npc_factory.guards
                       if g.db.owner is sentinel and not g.deleted])

        def _drop(room, resource, amount, x, y):
            self.loot_drops.append((resource, amount, x, y))

        self.handler = BaseEliminationHandler(
            self.registry, self.bus,
            owned_entities_provider=_owned,
            loot_drop_func=_drop,
        )

    def test_full_raid_loop(self):
        # --- 1. Spawn an outpost at (10, 10). ---
        base = self.spawner.spawn_base("earth", "outpost", coords=(10, 10))
        self.assertIsNotNone(base)
        sentinel = base["sentinel"]
        hq = next(b for b in self.building_factory.created
                  if b.db.building_type == "HQ")
        guards = list(self.npc_factory.guards)
        self.assertEqual(len(guards), 2)
        self.assertEqual(len(self.spawner._active_bases), 1)

        # --- 2. Player approaches: stand adjacent to the base (11, 10). ---
        weapon = FakeWeapon(damage=250, weapon_range=5)  # enough to one-shot HQ
        player = FakePlayer(name="Raider", x=11, y=10, weapon=weapon)
        self.room.add_player(player)

        # --- 3. Guards fight back: guard AI queues, combat resolves same tick. ---
        self.guard_ai.process_tick(self.tick, guards)
        self.assertTrue(self.engine.pending_actions,
                        "a guard should have queued an attack on the player")
        hp_before = player.db.hp
        self.engine.resolve_tick()
        self.assertLess(player.db.hp, hp_before,
                        "player should have taken guard damage")

        # --- 4. Player destroys the HQ -> whole base wipes. ---
        eliminated = []
        self.bus.subscribe(BASE_ELIMINATED, lambda **kw: eliminated.append(kw))
        xp_before = player.db.combat_xp

        ok, msg = self.engine.queue_attack(player, hq)
        self.assertTrue(ok, msg)
        self.engine.resolve_tick()

        # HQ destroyed by the combat engine; elimination handler wiped the rest.
        self.assertTrue(hq.deleted)
        self.assertTrue(sentinel.deleted, "sentinel should be deleted")
        wall = next(b for b in self.building_factory.created
                    if b.db.building_type == "WL")
        self.assertTrue(wall.deleted, "other buildings should be wiped")
        self.assertTrue(all(g.deleted for g in guards),
                        "all guards should be wiped")

        # --- XP: xp_building_destroy (HQ building) + xp_hq_destroy (base). ---
        gained = player.db.combat_xp - xp_before
        self.assertEqual(
            gained,
            self.registry.balance.xp_building_destroy
            + self.registry.balance.xp_hq_destroy,
        )

        # --- Loot dropped at the HQ tile. ---
        self.assertIn(("Iron", 30, 10, 10), self.loot_drops)
        self.assertIn(("Stone", 20, 10, 10), self.loot_drops)

        # --- BASE_ELIMINATED published; active base removed. ---
        self.assertEqual(len(eliminated), 1)
        self.assertEqual(eliminated[0]["tier"], "outpost")
        self.assertEqual(len(self.spawner._active_bases), 0)

        # --- 5. Respawn: queued for current_tick + cooldown; fires after it. ---
        self.assertEqual(len(self.spawner._pending_respawns), 1)
        respawn_at = self.spawner._pending_respawns[0]["respawn_at"]
        self.assertEqual(
            respawn_at, self.tick + self.registry.balance.outpost_respawn_ticks,
        )
        # Before the cooldown: nothing respawns.
        self.assertEqual(self.spawner.process_respawns(respawn_at - 1), 0)
        self.assertEqual(len(self.npc_factory.sentinels), 1)
        # At/after the cooldown: a fresh base spawns.
        self.tick = respawn_at
        spawned = self.spawner.process_respawns(respawn_at)
        self.assertEqual(spawned, 1)
        self.assertEqual(len(self.npc_factory.sentinels), 2)
        self.assertEqual(len(self.spawner._active_bases), 1)


if __name__ == "__main__":
    unittest.main()
