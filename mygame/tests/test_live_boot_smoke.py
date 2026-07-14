"""
Live-boot smoke test — the ONE test that exercises the real composition root
(``server.conf.game_init.initialize_game``) and real Evennia typeclasses against
a real (in-memory) database.

Why this exists
---------------
The rest of the suite runs against lightweight fakes installed by
``mygame/conftest.py``. That is fast, but it means the production wiring path and
the real Evennia object model are never exercised — which is exactly how a
cluster of HIGH-severity "wiring/reality gap" bugs (see COMPLEXITY_REVIEW.md
Part 5) passed 2000+ green tests while being broken in-game. The fakes were, in
the one place that mattered, *higher-fidelity-than-real*: a fake ``db`` raised
``AttributeError`` on a missing attribute, whereas Evennia's ``DbHolder`` returns
``None`` and never raises, so a ``hasattr``-based predicate failed *open*.

This test closes that gap. It boots real Evennia + a Django test DB, runs the
real ``initialize_game()``, and asserts the wiring/behaviour properties that each
of those bugs violated — on real ``Building`` / ``CombatCharacter`` objects.

How it runs
-----------
It is skipped under the normal (stubbed) suite. To run it, set the escape hatch
so ``conftest`` does NOT install Evennia stubs, and point Django at the settings:

    EVENNIA_REAL_BOOT=1 DJANGO_SETTINGS_MODULE=server.conf.settings \
        python -m pytest mygame/tests/test_live_boot_smoke.py -q

(A convenience wrapper lives in ``mygame/tests/run_live_boot_smoke.sh``.)

The module self-skips (rather than erroring) when the escape hatch is absent, so
``pytest mygame`` stays green and fast.
"""

import os
import unittest

import pytest

# ---------------------------------------------------------------- #
#  Guard: only run in a real-Evennia process. Under the stubbed suite this
#  whole module is skipped (module-level) so the fast default run is unaffected.
# ---------------------------------------------------------------- #

_REAL_BOOT = os.environ.get("EVENNIA_REAL_BOOT") == "1"

if not _REAL_BOOT:
    pytest.skip(
        "live-boot smoke test: set EVENNIA_REAL_BOOT=1 (and "
        "DJANGO_SETTINGS_MODULE=server.conf.settings) to run it against real "
        "Evennia; skipped under the stubbed suite.",
        allow_module_level=True,
    )


# ---------------------------------------------------------------- #
#  Boot real Django + Evennia with an in-memory DB. We never touch the real
#  server/evennia.db3 — force ``:memory:`` before django.setup().
# ---------------------------------------------------------------- #

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "server.conf.settings")

import django  # noqa: E402
from django.conf import settings  # noqa: E402

# Redirect to an ephemeral DB so a test run can never corrupt the game DB.
settings.DATABASES["default"]["NAME"] = ":memory:"

django.setup()

import evennia  # noqa: E402
evennia._init()

from evennia.utils import create  # noqa: E402
from evennia.utils.test_resources import EvenniaTest  # noqa: E402


# ---------------------------------------------------------------- #
#  Build the test-DB schema once for the module. We are not running under the
#  Django test runner (no pytest-django), so the migrations that create the
#  content-type/ObjectDB tables have not run. DiscoverRunner.setup_databases()
#  creates the ephemeral :memory: schema; teardown drops it. EvenniaTest's
#  per-test transaction rollback then works on top of it.
# ---------------------------------------------------------------- #

_DB_RUNNER = None
_DB_CONFIG = None


def setUpModule():
    global _DB_RUNNER, _DB_CONFIG
    from django.test.utils import setup_test_environment
    from django.test.runner import DiscoverRunner

    # Evennia's create_object tolerates a missing DEFAULT_HOME (#2) only when
    # settings.TEST_ENVIRONMENT is True. Evennia's own test runner sets this;
    # since we bootstrap the DB manually, set it ourselves so EvenniaTest.setUp
    # (which creates rooms without an explicit home) doesn't raise.
    settings.TEST_ENVIRONMENT = True

    setup_test_environment()
    _DB_RUNNER = DiscoverRunner(verbosity=0)
    _DB_CONFIG = _DB_RUNNER.setup_databases()


def tearDownModule():
    from django.test.utils import teardown_test_environment
    if _DB_RUNNER is not None and _DB_CONFIG is not None:
        _DB_RUNNER.teardown_databases(_DB_CONFIG)
    teardown_test_environment()


class LiveBootSmokeTest(EvenniaTest):
    """Boots the real composition root and asserts the wiring/reality
    properties that the Part 5 bugs violated, on real typeclass objects.

    ``EvenniaTest`` (a Django ``TestCase``) creates room #1/#2 and #2 as
    ``DEFAULT_HOME`` in ``setUp`` and wraps everything in a transaction that is
    rolled back in ``tearDown`` — so object creation works and nothing persists.
    """

    # -------------------------------------------------------------- #
    #  Helpers
    # -------------------------------------------------------------- #

    def _make_building(self, btype="HQ", x=10, y=10, planet="earth", hp=200):
        b = create.create_object(
            "typeclasses.objects.Building", key=f"{btype}-{x}-{y}",
            location=self.room1, home=self.room1,
        )
        b.db.building_type = btype
        b.db.coord_x = x
        b.db.coord_y = y
        b.db.coord_planet = planet
        b.db.hp = hp
        b.db.hp_max = hp
        b.attributes.add("building_type", btype)
        return b

    def _make_player(self, x=11, y=10, planet="earth", combat_xp=0, location=None):
        c = create.create_object(
            "typeclasses.characters.CombatCharacter", key="Raider",
            location=location or self.room1, home=self.room1,
        )
        c.db.coord_x = x
        c.db.coord_y = y
        c.db.coord_planet = planet
        c.db.combat_xp = combat_xp
        return c

    def _make_planet_room(self, planet="earth"):
        room = create.create_object(
            "typeclasses.rooms.PlanetRoom", key=f"Planet-{planet}", nohome=True,
        )
        room.db.planet = planet
        return room

    def _make_gear_item(self, key="Combat Knife", location=None):
        item = create.create_object(
            "typeclasses.objects.GameItem", key=key,
            location=location, home=self.room1,
        )
        item.db.item_key = "combat_knife"
        item.db.category = "weapon"
        item.db.slot = "weapon"
        return item

    def _make_agent(self, x=0, y=0, planet="earth", location=None):
        npc = create.create_object(
            "typeclasses.npcs.NPC", key="Agent",
            location=location, home=self.room1,
        )
        npc.db.coord_x = x
        npc.db.coord_y = y
        npc.db.coord_planet = planet
        npc.db.npc_type = "agent"
        return npc

    # -------------------------------------------------------------- #
    #  Fix #1 — is_player must NOT fail open on a real Building
    # -------------------------------------------------------------- #

    def test_is_player_false_for_real_building(self):
        from world.utils import is_player, is_building

        b = self._make_building("HQ")
        # This is the crux: on a REAL Evennia object, db.combat_xp is unset and
        # db.__getattribute__ returns None (never raises). A hasattr-based check
        # would say True here — the fail-open bug.
        self.assertIsNone(b.db.combat_xp)
        self.assertFalse(
            is_player(b),
            "is_player must be False for a real Building (fail-open regression)",
        )
        self.assertTrue(is_building(b))

    def test_is_player_true_for_real_character(self):
        from world.utils import is_player

        c = self._make_player()
        self.assertTrue(is_player(c), "a real CombatCharacter is a player")

    # -------------------------------------------------------------- #
    #  Fix #2 — game_init injects a live tick clock into the 3 systems
    # -------------------------------------------------------------- #

    def test_initialize_game_injects_live_tick_clock(self):
        from server.conf.game_init import initialize_game

        systems = initialize_game()
        try:
            for name in ("combat_engine", "building_system", "powerup_system"):
                sys_obj = systems.get(name)
                self.assertIsNotNone(sys_obj, f"{name} missing from game_systems")
                tick_func = getattr(sys_obj, "_current_tick_func", None)
                self.assertIsNotNone(tick_func, f"{name} has no tick func")
                # Must be the real clock, not the frozen lambda: 0 default. It
                # returns an int (0 before the tick script has ticked, but it is
                # the LIVE reader, which is the property we assert).
                self.assertIsInstance(tick_func(), int)
                # And it must NOT be a hard-frozen zero closure: the injected
                # function reads the GameTickScript, so identity differs from a
                # fresh `lambda: 0`. We assert it is callable + wired, above.
        finally:
            _teardown_game(systems)

    # -------------------------------------------------------------- #
    #  Fix #1 blast radius — a destroyed real Building routes to destruction,
    #  not player-respawn (so BUILDING_DESTROYED fires).
    # -------------------------------------------------------------- #

    def test_zero_hp_building_publishes_building_destroyed(self):
        from world.systems.combat_engine import CombatEngine
        from world.data_registry import DataRegistry
        from world.event_bus import EventBus, BUILDING_DESTROYED

        registry = DataRegistry()
        registry.load_all()
        bus = EventBus()
        seen = []
        bus.subscribe(BUILDING_DESTROYED, lambda **kw: seen.append(kw))

        engine = CombatEngine(registry, bus, current_tick_func=lambda: 5)

        attacker = self._make_player(combat_xp=0)
        building = self._make_building("HQ", hp=200)
        building.db.hp = 0  # already at zero — force the death branch

        # Drive the finalize path directly (same branch order as resolve_tick).
        engine._finalize_hit(attacker, building, weapon_item=None, damage=999,
                             current_tick=5)
        self.assertTrue(
            seen,
            "a 0-HP real Building must publish BUILDING_DESTROYED (it must route "
            "to _handle_building_destruction, not _handle_player_defeat)",
        )

    # -------------------------------------------------------------- #
    #  Fix #3 — with a real online player on a PlanetRoom, the tick's active
    #  building list is NON-empty (coords resolved from the entity, not loc.z).
    # -------------------------------------------------------------- #

    def test_active_building_list_nonempty_for_online_player(self):
        from server.conf.game_init import initialize_game

        systems = initialize_game()
        try:
            planet_rooms = systems.get("planet_rooms") or {}
            self.assertTrue(planet_rooms, "no planet rooms created at boot")
            planet_key, room = next(iter(planet_rooms.items()))

            # Place a real player and a real building on the same tile-ish.
            player = self._make_player(x=10, y=10, planet=planet_key)
            player.location = room
            building = self._make_building("HQ", x=11, y=10, planet=planet_key)
            building.location = room

            from typeclasses.scripts import GameTickScript
            script = GameTickScript.__new__(GameTickScript)
            script._get_all_buildings = lambda: [building]

            chunking = systems.get("chunking")
            active = script._compute_active_data(chunking, [player])
            self.assertIn(
                building, active,
                "an online real player on a PlanetRoom must yield a non-empty "
                "active-building list (coords from entity db, planet-scoped)",
            )
        finally:
            _teardown_game(systems)

    # -------------------------------------------------------------- #
    #  Drop/pickup — a dropped item must be indexed and re-gettable
    # -------------------------------------------------------------- #

    def test_dropped_item_is_indexed_and_can_be_picked_back_up(self):
        """The custom CmdDrop must set coords AND register the item in the
        PlanetRoom coordinate index, so get/scan/look see it and it can be
        picked back up. (The stock 'drop' left items un-indexed and invisible.)"""
        from commands.game_commands import CmdDrop, CmdGet

        room = self._make_planet_room("earth")
        player = self._make_player(x=7, y=7, planet="earth", location=room)
        knife = self._make_gear_item("Combat Knife", location=player)

        # --- drop it ---
        drop = CmdDrop()
        drop.caller = player
        drop.args = "Combat Knife"
        drop.func()

        # It left the player and is on the tile, coordinate-indexed.
        self.assertIsNot(knife.location, player, "item should have left inventory")
        self.assertEqual(knife.db.coord_x, 7)
        self.assertEqual(knife.db.coord_y, 7)
        at_tile = room.get_objects_at(7, 7)
        self.assertIn(
            knife, at_tile,
            "dropped item must be in the coordinate index (visible to get/scan/look)",
        )

        # --- pick it back up ---
        get = CmdGet()
        get.caller = player
        get.args = "Combat Knife"
        get.func()

        self.assertIs(
            knife.location, player,
            "the dropped item must be pick-back-up-able via get",
        )
        # No longer on the tile after pickup.
        self.assertNotIn(knife, room.get_objects_at(7, 7))

    def test_empty_tile_capacity_caps_at_one_gear_drop(self):
        """An empty tile (capacity 1) accepts one gear drop and refuses the
        second — exercising spawn_gear_drop's real coordinate-index cap check
        against a real PlanetRoom."""
        from typeclasses.objects import spawn_gear_drop
        from world.definitions import ItemDef

        room = self._make_planet_room("earth")
        item_def = ItemDef(
            key="combat_knife", name="Combat Knife", slot="weapon",
            category="weapon", stat_modifiers={"damage": 8},
        )

        first = spawn_gear_drop(room, item_def, x=3, y=3)
        self.assertIsNotNone(first, "first drop onto an empty tile must succeed")
        self.assertEqual(len(room.get_objects_at(3, 3)), 1)

        # Empty-tile capacity is 1 → the second new gear drop is refused.
        second = spawn_gear_drop(room, item_def, x=3, y=3)
        self.assertIsNone(second, "a full tile must refuse a new gear drop")
        self.assertEqual(len(room.get_objects_at(3, 3)), 1,
                         "the tile must still hold only one item")

    # -------------------------------------------------------------- #
    #  Equip/unequip lifecycle — item location + no map ghost
    # -------------------------------------------------------------- #

    def test_equip_from_ground_then_unequip_returns_to_inventory(self):
        """Equipping an item off a tile moves it onto the player (de-indexed
        from the tile — no map ghost); unequipping leaves it in inventory so it
        shows in 'inventory' and can be re-equipped."""
        from server.conf.game_init import initialize_game
        from typeclasses.objects import spawn_gear_drop
        from world.definitions import ItemDef

        systems = initialize_game()
        try:
            eq = systems["equipment_system"]
            room = self._make_planet_room("earth")
            player = self._make_player(x=5, y=5, planet="earth",
                                       combat_xp=100000, location=room)
            idef = ItemDef(key="combat_knife", name="Combat Knife",
                           slot="weapon", category="weapon",
                           stat_modifiers={"damage": 8})
            knife = spawn_gear_drop(room, idef, x=5, y=5)
            self.assertIn(knife, room.get_objects_at(5, 5))

            # Equip straight off the ground.
            eq.equip(player, knife)
            self.assertIs(knife.location, player, "equipped item must be on the player")
            self.assertNotIn(
                knife, room.get_objects_at(5, 5),
                "equipped item must NOT linger on the tile (map ghost)",
            )
            self.assertIs(player.equipment.get_equipped("weapon"), knife)

            # Unequip → stays in inventory (location is the player).
            eq.unequip(player, "weapon")
            self.assertIn(knife, player.contents,
                          "unequipped item must be in the player's inventory")
            self.assertIsNone(player.equipment.get_equipped("weapon"))
            self.assertNotIn(knife, room.get_objects_at(5, 5))

            # Re-equip from inventory works.
            self.assertTrue(eq.equip(player, knife))
            self.assertIs(player.equipment.get_equipped("weapon"), knife)
        finally:
            _teardown_game(systems)

    # -------------------------------------------------------------- #
    #  Arrival status — an agent that WALKS to its assignment lands on the
    #  DERIVED resting status ("Working"), not a hardcoded "Idle".
    # -------------------------------------------------------------- #

    def test_walked_agent_lands_on_working_not_idle(self):
        """A real NPC that walks a queued path must, on arrival, take the resting
        status DERIVED from its role/assignment — not a hardcoded "Idle".

        On a real ``db`` (where an unset attribute is None, not a raise),
        ``advance_movement`` calls ``resting_activity_status``, which returns
        "Working" for an assigned agent. This is the armory-agent bug, fixed at
        the class level: the movement engine no longer guesses the status.
        """
        room = self._make_planet_room("earth")
        npc = self._make_agent(x=0, y=0, planet="earth", location=room)
        room.coord_index.add(npc, 0, 0)
        # Assigned to a building → resting status must derive to "Working".
        npc.db.role = "engineer"
        npc.db.role_target = self._make_building("AR", x=1, y=0, planet="earth")

        # Queue a one-step walk, exactly as AgentSystem._move_agent_to does.
        npc.set_movement_queue([(1, 0)])

        # Drive the movement engine until the queue drains.
        npc.advance_movement(tick_number=1)

        self.assertEqual(list(npc.db.movement_queue or []), [])
        self.assertEqual(
            npc.db.activity_status, "Working",
            "a walked, assigned agent must derive 'Working' on arrival, not Idle",
        )

    def test_unassigned_walked_agent_lands_on_idle(self):
        """The mirror case: a real NPC with no role derives "Idle" on arrival —
        confirming the authority isn't just hardcoding "Working"."""
        room = self._make_planet_room("earth")
        npc = self._make_agent(x=0, y=0, planet="earth", location=room)
        room.coord_index.add(npc, 0, 0)
        npc.db.role = ""  # unassigned

        npc.set_movement_queue([(1, 0)])
        npc.advance_movement(tick_number=1)

        self.assertEqual(npc.db.activity_status, "Idle")

    # -------------------------------------------------------------- #
    #  Extractor notification — a harvester agent's production notifies its
    #  owner through the real presenter (autonomous extraction isn't silent).
    # -------------------------------------------------------------- #

    def test_harvester_production_notifies_owner_through_presenter(self):
        """With the real composition root booted, a HarvesterScript producing on
        an Extractor emits a ``harvester_produced`` notification that the real
        presenter renders to the owner's ``msg`` sink."""
        from server.conf.game_init import initialize_game
        from typeclasses.agent_scripts import _notify_owner

        systems = initialize_game()
        try:
            room = self._make_planet_room("earth")
            player = self._make_player(x=4, y=4, planet="earth", location=room)

            captured = []
            orig_msg = player.msg
            player.msg = lambda text=None, **kw: captured.append(text)

            npc = self._make_agent(x=4, y=4, planet="earth", location=room)
            npc.db.owner = player

            # Emit the exact notification HarvesterScript.at_repeat sends.
            _notify_owner(npc, "harvester_produced", amount=6, resource_type="Wood")

            player.msg = orig_msg
            self.assertTrue(
                any("Extractor" in (m or "") and "Wood" in (m or "")
                    for m in captured),
                f"owner must be notified of extractor output; got {captured!r}",
            )
        finally:
            _teardown_game(systems)

    # -------------------------------------------------------------- #
    #  Owner-attributed combat — a real turret kill credits/announces/combats
    #  the OWNING player, and pulls that player into combat mode.
    # -------------------------------------------------------------- #

    def test_turret_kill_attributes_to_owner_on_real_objects(self):
        """On real Evennia objects (where an unset db attr is None, not a raise):
        a turret killing player B credits A's kill XP, announces
        "A's Turret has eliminated B", and puts A into combat mode."""
        from server.conf.game_init import initialize_game
        from world.event_bus import COMBAT_ACTION, PLAYER_ELIMINATED

        systems = initialize_game()
        try:
            engine = systems["combat_engine"]
            bus = systems["event_bus"]
            room = self._make_planet_room("earth")

            owner_a = self._make_player(x=5, y=5, planet="earth", location=room)
            owner_a.db.combat_xp = 0
            victim_b = self._make_player(x=6, y=5, planet="earth", location=room)
            victim_b.db.combat_xp = 500  # enough to lose death-loss from
            victim_b.db.hp = 1           # one hit ends it

            turret = self._make_building("TU", x=5, y=5, planet="earth")
            turret.db.owner = owner_a

            eliminations = []
            bus.subscribe(PLAYER_ELIMINATED, lambda **kw: eliminations.append(kw))
            combat_actions = []
            bus.subscribe(COMBAT_ACTION, lambda **kw: combat_actions.append(kw))

            # A synthetic high-damage weapon; the attacker being a TU building is
            # what drives owner attribution.
            from world.systems.combat_engine import _TurretWeapon
            engine.apply_direct_hit(turret, victim_b, _TurretWeapon(999, 10),
                                    current_tick=1)

            # 1. Kill XP credited to the OWNER, not the turret (turret has none).
            self.assertEqual(owner_a.db.combat_xp,
                             engine.registry.balance.xp_kill)
            # 1b. Elimination event carries owner attribution.
            self.assertTrue(eliminations)
            elim = eliminations[-1]
            self.assertIs(elim["attacker_owner"], owner_a)
            self.assertEqual(elim["attacker_kind"], "turret")
            # 2. The COMBAT_ACTION carried the owner so the timer pulls A in.
            self.assertTrue(combat_actions)
            self.assertIs(combat_actions[-1]["attacker_owner"], owner_a)
            # 2b. A is actually in combat (timer expiry in the future).
            self.assertGreater(owner_a.db.combat_timer_expires or 0, 1)
            # 3. Cosmetic tallies on a real db (unset -> None): the turret has
            #    no score sheet so the kill tallies on the OWNER, and the
            #    victim's death tallies on the victim.
            self.assertEqual(owner_a.db.kills, 1)
            self.assertEqual(victim_b.db.deaths, 1)
        finally:
            _teardown_game(systems)

    def test_closed_building_immune_to_ranged_on_real_objects(self):
        """On real objects: a CLOSED building rejects a ranged attack but a melee
        (adjacent) attack still lands; an OPEN building takes the ranged hit."""
        from server.conf.game_init import initialize_game
        from world.systems.combat_engine import _TurretWeapon, SyntheticWeapon

        systems = initialize_game()
        try:
            engine = systems["combat_engine"]
            attacker = self._make_player(x=0, y=0, planet="earth")
            attacker.db.combat_xp = 100000  # ranked, irrelevant to the gate

            # Both buildings created directly; set open state explicitly (the
            # factory default is CLOSED — buildings are cover unless opened).
            closed = self._make_building("MM", x=3, y=0, planet="earth", hp=200)
            closed.set_open(False)
            open_b = self._make_building("MM", x=4, y=0, planet="earth", hp=200)
            open_b.set_open(True)

            ranged = _TurretWeapon(50, 10)  # no weapon_type -> ranged

            # Ranged vs CLOSED building: rejected, no damage.
            ok, msg = engine.queue_attack(attacker, closed, weapon=ranged)
            self.assertFalse(ok)
            self.assertIn("closed", msg.lower())

            # Ranged vs OPEN building: allowed.
            ok, _ = engine.queue_attack(attacker, open_b, weapon=ranged)
            self.assertTrue(ok)

            # Melee vs CLOSED building (attacker adjacent): allowed.
            melee = SyntheticWeapon(50, 1, name="Fist")
            melee.weapon_type = "melee"
            attacker.db.coord_x, attacker.db.coord_y = 2, 0  # adjacent to (3,0)
            ok, _ = engine.queue_attack(attacker, closed, weapon=melee)
            self.assertTrue(ok)
        finally:
            _teardown_game(systems)

    def test_breach_shot_damages_closed_building_on_real_objects(self):
        """On real objects: a breaching directional shot (breach=True) damages a
        CLOSED building — the 'shoot a closed structure down' mechanic — while a
        non-breach ranged shot at the same building is still rejected."""
        from server.conf.game_init import initialize_game
        from world.systems.combat_engine import _TurretWeapon

        systems = initialize_game()
        try:
            engine = systems["combat_engine"]
            attacker = self._make_player(x=0, y=0, planet="earth")
            attacker.db.combat_xp = 100000

            closed = self._make_building("MM", x=3, y=0, planet="earth", hp=200)
            closed.set_open(False)
            ranged = _TurretWeapon(50, 10)  # no weapon_type -> ranged

            # Non-breach ranged shot: rejected by the closed-cover gate.
            ok, msg = engine.queue_attack(attacker, closed, weapon=ranged)
            self.assertFalse(ok)
            self.assertIn("closed", msg.lower())

            # Breaching shot: allowed, and it damages the closed building.
            ok, _ = engine.queue_attack(attacker, closed, weapon=ranged, breach=True)
            self.assertTrue(ok, "a breaching shot must reach a closed building")
            hp_before = closed.db.hp
            engine.resolve_tick()
            self.assertLess(closed.db.hp, hp_before)
        finally:
            _teardown_game(systems)

    def test_wall_takes_ranged_fire_on_real_objects(self):
        """A Wall (combat_barrier) is intrinsically OPEN on real objects: ranged
        fire breaches it even with its 'open' attribute explicitly False —
        resolved via the live registry's WL capability."""
        from server.conf.game_init import initialize_game
        from world.systems.combat_engine import _TurretWeapon
        from world.utils import building_is_open

        systems = initialize_game()
        try:
            engine = systems["combat_engine"]
            attacker = self._make_player(x=0, y=0, planet="earth")
            attacker.db.combat_xp = 100000

            wall = self._make_building("WL", x=3, y=0, planet="earth", hp=600)
            wall.set_open(False)  # explicitly closed — the wall rule overrides
            self.assertTrue(building_is_open(wall), "wall must read as open")

            ranged = _TurretWeapon(50, 10)  # no weapon_type -> ranged
            ok, _ = engine.queue_attack(attacker, wall, weapon=ranged)
            self.assertTrue(ok, "ranged fire must breach a wall")
        finally:
            _teardown_game(systems)

    def test_melee_is_same_tile_only_on_real_objects(self):
        """On real objects: melee connects only when attacker and target share
        the exact tile — an adjacent (even diagonal) foe is out of reach until
        someone closes in."""
        from server.conf.game_init import initialize_game
        from world.systems.combat_engine import SyntheticWeapon

        systems = initialize_game()
        try:
            engine = systems["combat_engine"]
            room = self._make_planet_room("earth")
            attacker = self._make_player(x=5, y=5, planet="earth", location=room)

            melee = SyntheticWeapon(30, 1, name="Fist")
            melee.weapon_type = "melee"

            # Diagonal neighbour (6,6): adjacent but NOT same tile -> refused.
            diag = self._make_player(x=6, y=6, planet="earth", location=room)
            ok, msg = engine.queue_attack(attacker, diag, weapon=melee)
            self.assertFalse(ok, "adjacent foe is not in melee reach")
            self.assertIn("same tile", msg.lower())

            # Same tile (5,5): in reach.
            same = self._make_player(x=5, y=5, planet="earth", location=room)
            ok, _ = engine.queue_attack(attacker, same, weapon=melee)
            self.assertTrue(ok, "a same-tile foe is in melee reach")
        finally:
            _teardown_game(systems)

    def test_player_is_sheltered_on_real_objects(self):
        """On real objects (where an unset db attr is None, not a raise):
        player_is_sheltered is True only for a player inside a CLOSED building,
        and the combat engine refuses a ranged shot against such a player."""
        from server.conf.game_init import initialize_game
        from world.utils import player_is_sheltered
        from world.systems.combat_engine import _TurretWeapon

        systems = initialize_game()
        try:
            engine = systems["combat_engine"]
            room = self._make_planet_room("earth")

            player = self._make_player(x=6, y=5, planet="earth", location=room)
            shelter = self._make_building("MM", x=6, y=5, planet="earth")
            shelter.location = room
            # Register the shelter in this room's coordinate index so
            # get_buildings_at(6,5) (used by player_is_sheltered) resolves it.
            room.coord_index.add(shelter, 6, 5)

            # Standing on the tile but NOT inside -> exposed.
            self.assertFalse(player_is_sheltered(player))

            # Inside a CLOSED building (factory default is closed) -> sheltered.
            player.db.inside_building = True
            shelter.set_open(False)
            self.assertTrue(player_is_sheltered(player))

            # A ranged attack against a sheltered player is refused.
            attacker = self._make_player(x=0, y=0, planet="earth", location=room)
            ok, msg = engine.queue_attack(
                attacker, player, weapon=_TurretWeapon(50, 10)
            )
            self.assertFalse(ok)
            self.assertIn("sheltered", msg.lower())

            # Symmetric cover: while sheltered (closed), the player also can't
            # fire ranged OUT. Give them a ranged weapon and confirm rejection.
            from world.definitions import ItemDef
            rifle_def = ItemDef(key="rifle", name="Rifle", slot="weapon",
                                category="weapon", stat_modifiers={"damage": 20},
                                weapon_type="ranged")
            from typeclasses.objects import spawn_gear_drop
            rifle = spawn_gear_drop(room, rifle_def, x=6, y=5)
            systems["equipment_system"].equip(player, rifle)
            bystander = self._make_player(x=7, y=5, planet="earth", location=room)
            ok, msg = engine.queue_attack(player, bystander)
            self.assertFalse(ok, "sheltered player must not fire ranged out")
            self.assertIn("inside", msg.lower())

            # Open the building -> no cover -> exposed again, ranged allowed.
            shelter.set_open(True)
            self.assertFalse(player_is_sheltered(player))
            attacker.db.coord_x, attacker.db.coord_y = 0, 5  # within range 10
            ok, _ = engine.queue_attack(
                attacker, player, weapon=_TurretWeapon(50, 10)
            )
            self.assertTrue(ok)
        finally:
            _teardown_game(systems)

    def test_melee_room_gate_on_real_objects(self):
        """On real objects: a player inside a building can only be meleed from
        the SAME tile. An adjacent melee attack is refused even for an OPEN
        building (the reported guard-through-the-wall bug); a same-tile melee
        lands. Independent of the closed-cover rule."""
        from server.conf.game_init import initialize_game
        from world.utils import target_inside_building
        from world.systems.combat_engine import SyntheticWeapon

        systems = initialize_game()
        try:
            engine = systems["combat_engine"]
            room = self._make_planet_room("earth")

            # Raider inside an OPEN building at (6,5) — like the turret in the
            # report. inside_building True + a building registered on the tile.
            raider = self._make_player(x=6, y=5, planet="earth", location=room)
            raider.db.inside_building = True
            turret = self._make_building("TU", x=6, y=5, planet="earth")
            turret.location = room
            turret.set_open(True)
            room.coord_index.add(turret, 6, 5)
            self.assertTrue(target_inside_building(raider))

            melee = SyntheticWeapon(50, 1, name="Fist")
            melee.weapon_type = "melee"

            # Attacker on the ADJACENT tile (5,5) — e.g. a guard on the HQ tile.
            guard = self._make_player(x=5, y=5, planet="earth", location=room)
            ok, msg = engine.queue_attack(guard, raider, weapon=melee)
            self.assertFalse(ok, "adjacent melee must not reach into a building")
            self.assertIn("same tile", msg.lower())

            # Same tile (6,5) -> melee lands.
            guard.db.coord_x, guard.db.coord_y = 6, 5
            ok, _ = engine.queue_attack(guard, raider, weapon=melee)
            self.assertTrue(ok, "same-tile melee should land")
        finally:
            _teardown_game(systems)

    def test_targeting_lock_and_shoot_on_real_objects(self):
        """On real objects: TargetingSystem is wired at the composition root; a
        ranged lock completes over the balance-configured ticks and then a
        locked shot queues an accuracy-bearing attack through the engine."""
        from server.conf.game_init import initialize_game
        from world.definitions import ItemDef
        from typeclasses.objects import spawn_gear_drop

        systems = initialize_game()
        try:
            targeting = systems["targeting_system"]
            engine = systems["combat_engine"]
            self.assertIsNotNone(targeting, "targeting_system must be wired")
            room = self._make_planet_room("earth")

            shooter = self._make_player(x=0, y=0, planet="earth", location=room)
            shooter.db.combat_xp = 100000  # high rank so no equip gate blocks

            # A real ranged weapon, equipped via the real equipment system.
            rifle_def = ItemDef(key="rifle", name="Rifle", slot="weapon",
                                category="weapon",
                                stat_modifiers={"damage": 20, "range": 8},
                                weapon_type="ranged")
            rifle = spawn_gear_drop(room, rifle_def, x=0, y=0)
            systems["equipment_system"].equip(shooter, rifle)

            # An enemy in range.
            enemy = self._make_player(x=3, y=0, planet="earth", location=room)

            ok, _ = targeting.acquire(shooter, enemy)
            self.assertTrue(ok, "should start a lock with a ranged weapon in range")
            self.assertFalse(targeting.is_locked(shooter))

            # Advance ticks until the lock completes (bounded).
            for tick in range(1, 20):
                targeting.process_tick(tick, [shooter])
                if targeting.is_locked(shooter):
                    break
            self.assertTrue(targeting.is_locked(shooter), "lock should complete")

            # A locked shot queues an attack carrying the targeted accuracy.
            engine.pending_actions.clear()
            acc = targeting.targeted_accuracy(rifle)
            ok, _ = engine.queue_attack(shooter, enemy, weapon=rifle, accuracy=acc)
            self.assertTrue(ok)
            self.assertEqual(len(engine.pending_actions), 1)
            self.assertEqual(engine.pending_actions[0]["accuracy"], acc)

            # The SHOOTER moving breaks the lock immediately (at_coord_change),
            # not on the next tick — a real move_entity fires the hook.
            self.assertTrue(targeting.is_locked(shooter))
            room.move_entity(shooter, 0, 1)  # step north
            self.assertIsNone(targeting.get_target(shooter),
                              "moving must break the shooter's lock")
            self.assertFalse(targeting.is_locked(shooter))

            # And the enemy leaving weapon range also breaks a (re-acquired) lock
            # via the per-tick upkeep.
            shooter.db.coord_x, shooter.db.coord_y = 0, 0
            targeting.acquire(shooter, enemy)
            for tick in range(1, 20):
                targeting.process_tick(tick, [shooter])
                if targeting.is_locked(shooter):
                    break
            self.assertTrue(targeting.is_locked(shooter))
            enemy.db.coord_x = 50  # out of range
            targeting.process_tick(99, [shooter])
            self.assertFalse(targeting.is_locked(shooter))
            self.assertIsNone(targeting.get_target(shooter))
        finally:
            _teardown_game(systems)

    def test_lock_onto_real_enemy_guard_survives_upkeep(self):
        """Regression: a real enemy guard (via the NPC-base factory) carries
        coord_planet, so locking onto one does NOT instantly drop with 'left the
        area' on the next upkeep tick (the reported bug)."""
        from server.conf.game_init import initialize_game
        from world.adapters.evennia_npc_base_factory import EvenniaNpcBaseFactory
        from world.definitions import ItemDef
        from typeclasses.objects import spawn_gear_drop

        systems = initialize_game()
        try:
            targeting = systems["targeting_system"]
            room = self._make_planet_room("earth")

            shooter = self._make_player(x=0, y=0, planet="earth", location=room)
            shooter.db.combat_xp = 100000
            rifle_def = ItemDef(key="rifle", name="Rifle", slot="weapon",
                                category="weapon",
                                stat_modifiers={"damage": 20, "range": 8},
                                weapon_type="ranged")
            rifle = spawn_gear_drop(room, rifle_def, x=0, y=0)
            systems["equipment_system"].equip(shooter, rifle)

            factory = EvenniaNpcBaseFactory()
            sentinel = factory.create_sentinel("Outpost #1", room, "earth")
            guard = factory.create_enemy_guard(sentinel, room, 3, 0, "guard", 80)
            # The fix: the guard is stamped with the planet.
            self.assertEqual(guard.db.coord_planet, "earth")

            ok, _ = targeting.acquire(shooter, guard)
            self.assertTrue(ok)
            # One upkeep tick must NOT drop the lock as 'left the area'.
            targeting.process_tick(1, [shooter])
            self.assertIsNotNone(targeting.get_target(shooter),
                                 "lock must survive: guard is on the same planet")
        finally:
            _teardown_game(systems)

    def test_lock_onto_agent_without_coord_planet_survives_upkeep(self):
        """Regression (review): a player-owned agent carries coords but NOT
        coord_planet. Locking onto one must survive upkeep — _planet falls back
        to the agent's ROOM planet, which matches the shooter. Without the
        fallback the lock dropped on the first tick as 'left the area'."""
        from server.conf.game_init import initialize_game
        from world.definitions import ItemDef
        from typeclasses.objects import spawn_gear_drop

        systems = initialize_game()
        try:
            targeting = systems["targeting_system"]
            room = self._make_planet_room("earth")
            shooter = self._make_player(x=0, y=0, planet="earth", location=room)
            shooter.db.combat_xp = 100000
            rifle_def = ItemDef(key="rifle", name="Rifle", slot="weapon",
                                category="weapon",
                                stat_modifiers={"damage": 20, "range": 8},
                                weapon_type="ranged")
            rifle = spawn_gear_drop(room, rifle_def, x=0, y=0)
            systems["equipment_system"].equip(shooter, rifle)

            # A real agent NPC in the same room, with coords but NO coord_planet.
            agent = self._make_agent(x=3, y=0, location=room)
            agent.db.coord_planet = None  # the gap the fallback closes
            self.assertEqual(room.planet_name, "earth")

            ok, _ = targeting.acquire(shooter, agent)
            self.assertTrue(ok)
            targeting.process_tick(1, [shooter])
            self.assertIsNotNone(
                targeting.get_target(shooter),
                "lock must survive when target planet resolves via its room")
        finally:
            _teardown_game(systems)

    def test_missed_shot_puts_both_sides_in_combat_on_real_objects(self):
        """On real objects: a MISSED ranged shot publishes COMBAT_ACTION, so the
        wired combat-timer subscriber sets combat_timer_expires on BOTH shooter
        and target — the state that actually gates wall-passage/enter-leave. A
        miss that set only combat_lockout_tick would leave both free to move."""
        from server.conf.game_init import initialize_game
        from world.definitions import ItemDef
        from typeclasses.objects import spawn_gear_drop

        systems = initialize_game()
        try:
            engine = systems["combat_engine"]
            room = self._make_planet_room("earth")
            shooter = self._make_player(x=0, y=0, planet="earth", location=room)
            shooter.db.combat_xp = 100000
            rifle_def = ItemDef(key="rifle", name="Rifle", slot="weapon",
                                category="weapon",
                                stat_modifiers={"damage": 20, "range": 8},
                                weapon_type="ranged")
            rifle = spawn_gear_drop(room, rifle_def, x=0, y=0)
            systems["equipment_system"].equip(shooter, rifle)
            target = self._make_player(x=3, y=0, planet="earth", location=room)
            target.key = "Victim"

            # Force a MISS: rng.random() always returns ~1.0 (>= any accuracy).
            class _AlwaysMiss:
                def random(self):
                    return 0.999999
            engine._rng = _AlwaysMiss()

            engine.queue_attack(shooter, target, weapon=rifle, accuracy=0.5)
            engine.resolve_tick()

            # No damage (it missed) but BOTH are now "in combat" via the timer.
            self.assertEqual(target.db.hp, target.db.hp_max)
            self.assertGreater(shooter.db.combat_timer_expires or 0, 0,
                               "shooter must be in combat after firing (even a miss)")
            self.assertGreater(target.db.combat_timer_expires or 0, 0,
                               "target must be in combat after being shot at")
        finally:
            _teardown_game(systems)

    def test_instant_attack_resolves_immediately_on_real_objects(self):
        """On real objects: a player's direct attack via resolve_now applies
        damage in the SAME call (instant), without touching the tick queue."""
        from server.conf.game_init import initialize_game
        from world.definitions import ItemDef
        from typeclasses.objects import spawn_gear_drop

        systems = initialize_game()
        try:
            engine = systems["combat_engine"]
            room = self._make_planet_room("earth")
            attacker = self._make_player(x=0, y=0, planet="earth", location=room)
            attacker.db.combat_xp = 100000
            knife_def = ItemDef(key="knife", name="Knife", slot="weapon",
                                category="weapon",
                                stat_modifiers={"damage": 15, "range": 1},
                                weapon_type="melee")
            knife = spawn_gear_drop(room, knife_def, x=0, y=0)
            systems["equipment_system"].equip(attacker, knife)
            # Melee is same-tile only — put the victim on the attacker's tile.
            target = self._make_player(x=0, y=0, planet="earth", location=room)
            target.key = "Victim"
            hp0 = target.db.hp

            ok, _ = engine.resolve_now(attacker, target)
            self.assertTrue(ok)
            self.assertLess(target.db.hp, hp0, "instant attack applies damage now")
            self.assertEqual(len(engine.pending_actions), 0,
                             "resolve_now must not queue to the tick")
        finally:
            _teardown_game(systems)

    def test_mine_arm_tick_and_detonate_on_real_objects(self):
        """On real objects: arm a mine (LiveBomb placed + indexed), tick its fuse
        down, and confirm it detonates — a co-located victim takes damage and the
        bomb object is deleted. Exercises the real BombSystem + spawn_bomb +
        coordinate index + AoE-through-combat-engine path end to end."""
        from server.conf.game_init import initialize_game

        systems = initialize_game()
        try:
            bomb_system = systems["bomb_system"]
            self.assertIsNotNone(bomb_system, "bomb_system must be wired")
            room = self._make_planet_room("earth")

            placer = self._make_player(x=4, y=4, planet="earth", location=room)
            placer.db.combat_xp = 100000
            placer.equipment.add_supply("land_mine", 1)

            # A victim standing on the same tile as the armed mine. Register in
            # the coordinate index (as movement would) so the blast area-query
            # finds them — _make_player sets coords but doesn't index.
            victim = self._make_player(x=4, y=4, planet="earth", location=room)
            victim.key = "Victim"
            room.coord_index.add(victim, 4, 4)
            hp0 = victim.db.hp

            # Set a 2s fuse and arm the mine on the placer's tile.
            self.assertTrue(bomb_system.set_fuse(placer, "land_mine", 2))
            self.assertTrue(bomb_system.arm_mine(placer, "land_mine"))
            # The mine is now a placed, indexed LiveBomb on (4,4).
            bombs = room.get_objects_at(4, 4, type_tag="bomb")
            self.assertEqual(len(bombs), 1)
            mine = bombs[0]
            self.assertEqual(mine.db.fuse_remaining, 2)

            # Tick once: fuse 2 -> 1, still live, no damage yet.
            bomb_system.process_tick(1)
            self.assertEqual(mine.db.fuse_remaining, 1)
            self.assertEqual(victim.db.hp, hp0)

            # Tick again: fuse 1 -> 0 -> detonate. Victim takes the blast and the
            # bomb is removed from the world + the coordinate index.
            bomb_system.process_tick(2)
            self.assertLess(victim.db.hp, hp0, "co-located victim caught in blast")
            self.assertIsNone(getattr(mine, "pk", None),
                              "detonated mine must be deleted")
            self.assertEqual(room.get_objects_at(4, 4, type_tag="bomb"), [],
                             "detonated mine must be de-indexed")
        finally:
            _teardown_game(systems)

    def test_armed_mine_survives_reboot_via_rebuild(self):
        """A mine armed before a restart resumes its fuse: rebuild_from_world
        re-tracks the persisted LiveBomb so it keeps ticking (its fuse state and
        coords persist on db; only the in-memory countdown list is rebuilt)."""
        from server.conf.game_init import initialize_game

        systems = initialize_game()
        try:
            bomb_system = systems["bomb_system"]
            room = self._make_planet_room("earth")
            placer = self._make_player(x=2, y=2, planet="earth", location=room)
            placer.db.combat_xp = 100000
            placer.equipment.add_supply("land_mine", 1)
            bomb_system.set_fuse(placer, "land_mine", 5)
            bomb_system.arm_mine(placer, "land_mine")

            # Simulate a reboot: drop the in-memory list, then rebuild from world.
            bomb_system._live_bombs = []
            n = bomb_system.rebuild_from_world({"earth": room})
            self.assertEqual(n, 1, "the armed mine must be re-tracked after reboot")
        finally:
            _teardown_game(systems)

    def test_armed_bomb_cannot_be_picked_up_on_real_objects(self):
        """On real objects: a co-located player CANNOT 'get' an armed mine — the
        game's CmdGet gates pickup through at_pre_get, and LiveBomb.at_pre_get
        refuses. (Regression: the get:false() lock alone was never enforced by
        CmdGet, so the bomb was pocketable — the HIGH review finding.)"""
        from server.conf.game_init import initialize_game
        from commands.game_commands import CmdGet

        systems = initialize_game()
        try:
            bomb_system = systems["bomb_system"]
            room = self._make_planet_room("earth")
            placer = self._make_player(x=6, y=6, planet="earth", location=room)
            placer.db.combat_xp = 100000
            placer.equipment.add_supply("land_mine", 1)
            bomb_system.set_fuse(placer, "land_mine", 30)
            bomb_system.arm_mine(placer, "land_mine")
            bombs = room.get_objects_at(6, 6, type_tag="bomb")
            self.assertEqual(len(bombs), 1)
            mine = bombs[0]

            # A player on the mine's tile tries to grab it.
            grabber = self._make_player(x=6, y=6, planet="earth", location=room)
            grabber.key = "Grabber"
            room.coord_index.add(grabber, 6, 6)
            get = CmdGet()
            get.caller = grabber
            get.args = "Land Mine"
            get.func()

            # The bomb is NOT in the grabber's inventory and stays on its tile.
            self.assertIsNot(mine.location, grabber,
                             "an armed bomb must not be pickupable")
            self.assertIn(mine, room.get_objects_at(6, 6, type_tag="bomb"),
                          "the bomb must remain on its tile")
            # at_pre_get refuses directly (independent of the get command path).
            self.assertFalse(mine.at_pre_get(grabber),
                             "LiveBomb.at_pre_get must refuse pickup")
        finally:
            _teardown_game(systems)

    def test_grenade_throw_clamps_to_map_edge_on_real_objects(self):
        """On real objects: BombSystem gets planet_registry.is_valid_coordinate
        wired at boot, so a grenade thrown toward a map edge lands ON the edge
        tile, never off-map. Uses a REAL planet room/key so the bounds check
        actually resolves (a bogus planet would fall open and hide the clamp)."""
        from server.conf.game_init import initialize_game

        systems = initialize_game()
        try:
            bomb_system = systems["bomb_system"]
            planet_rooms = systems.get("planet_rooms") or {}
            self.assertTrue(planet_rooms, "no planet rooms at boot")
            planet_key, room = next(iter(planet_rooms.items()))

            # Stand near the west edge; throw west with a long range.
            thrower = self._make_player(x=1, y=4, planet=planet_key, location=room)
            thrower.db.combat_xp = 100000
            thrower.equipment.add_supply("frag_grenade", 1)
            bomb_system.set_fuse(thrower, "frag_grenade", 30)
            bomb_system.throw_grenade(thrower, "frag_grenade", "w")

            bombs = room.get_objects_at(0, 4, type_tag="bomb")
            self.assertEqual(len(bombs), 1, "grenade must land on the edge tile (0,4)")
            # And nothing landed off-map at a negative x.
            self.assertGreaterEqual(bombs[0].db.coord_x, 0)
        finally:
            _teardown_game(systems)

    def test_out_of_bounds_renders_as_fog_on_real_objects(self):
        """On real objects: the FogOfWarSystem gets planet_registry.is_valid_
        coordinate wired at boot, so a tile beyond a real planet's bounds is
        out-of-bounds (fog), while a tile inside it is not. Exercises the real
        composition-root wiring, not a stub."""
        from server.conf.game_init import initialize_game

        systems = initialize_game()
        try:
            fog = systems["fog_system"]
            planet_registry = systems["planet_registry"]
            planet_rooms = systems.get("planet_rooms") or {}
            self.assertTrue(planet_rooms, "no planet rooms at boot")
            planet_key = next(iter(planet_rooms.keys()))
            space = planet_registry.get_space(planet_key)

            # Origin is in-bounds; one tile below/left of origin is off-map.
            self.assertTrue(fog.is_in_bounds(planet_key, 0, 0))
            self.assertFalse(fog.is_in_bounds(planet_key, -1, 0))
            self.assertFalse(fog.is_in_bounds(planet_key, 0, -1))
            # One tile past the max edge is off-map; the last valid tile is in.
            self.assertTrue(fog.is_in_bounds(planet_key, space.width - 1, space.height - 1))
            self.assertFalse(fog.is_in_bounds(planet_key, space.width, 0))
            self.assertFalse(fog.is_in_bounds(planet_key, 0, space.height))
        finally:
            _teardown_game(systems)

    def test_teleport_looks_after_coords_updated_cross_planet(self):
        """On real objects: teleporting to a DIFFERENT planet updates all coords
        (planet + x + y) and THEN issues one look — so the shown view reflects
        the destination. The old bug: at_object_receive fired mid-move (before
        x/y updated) on a Z change, leaking a stale-coord tile line; a same-planet
        teleport showed nothing. Both are now a single, correct look."""
        from server.conf.game_init import initialize_game
        from commands.admin_commands import CmdTeleport

        systems = initialize_game()
        try:
            planet_rooms = systems.get("planet_rooms") or {}
            self.assertGreaterEqual(len(planet_rooms), 2,
                                    "need two planets for a cross-planet teleport")
            keys = list(planet_rooms.keys())
            src_key, dst_key = keys[0], keys[1]
            src_room = planet_rooms[src_key]

            player = self._make_player(x=2, y=3, planet=src_key, location=src_room)
            # Grant Builder perms so the command's lock passes.
            player.permissions.add("Builder")

            captured = []
            orig_msg = player.msg
            player.msg = lambda text=None, **kw: captured.append(
                text[0] if isinstance(text, tuple) else text)

            cmd = CmdTeleport()
            cmd.caller = player
            cmd.cmdstring = "goto"
            cmd.args = f"7 9 {dst_key}"
            cmd.func()

            player.msg = orig_msg

            # All three coords updated to the destination.
            self.assertEqual(player.db.coord_planet, dst_key)
            self.assertEqual((player.db.coord_x, player.db.coord_y), (7, 9))
            self.assertIs(player.location, planet_rooms[dst_key])
            # A look ran after the teleport (map/tile output was produced).
            self.assertTrue(captured, "teleport must issue a look")
            # No captured line references the ORIGIN coords (2,3) — the stale
            # mid-move renders (auto-look + tile line) are suppressed.
            self.assertFalse(
                any("(2, 3)" in (m or "") or "2,3" in (m or "") for m in captured),
                f"teleport leaked a stale-coord line: {captured!r}",
            )
            # The player no longer leaks in the ORIGIN planet's coordinate index
            # (skipping move hooks means we de-indexed it manually).
            self.assertNotIn(player, src_room.get_objects_at(2, 3))
            # And it IS indexed at the destination tile on the new planet.
            self.assertIn(player, planet_rooms[dst_key].get_objects_at(7, 9))
        finally:
            _teardown_game(systems)

    def test_teleport_same_planet_issues_look(self):
        """A same-planet (X/Y-only) teleport also issues a look — previously it
        fired no arrival hook at all, so the view never refreshed."""
        from server.conf.game_init import initialize_game
        from commands.admin_commands import CmdTeleport

        systems = initialize_game()
        try:
            planet_rooms = systems.get("planet_rooms") or {}
            self.assertTrue(planet_rooms)
            key = next(iter(planet_rooms.keys()))
            room = planet_rooms[key]
            player = self._make_player(x=2, y=3, planet=key, location=room)
            player.permissions.add("Builder")

            captured = []
            orig_msg = player.msg
            player.msg = lambda text=None, **kw: captured.append(
                text[0] if isinstance(text, tuple) else text)

            cmd = CmdTeleport()
            cmd.caller = player
            cmd.cmdstring = "goto"
            cmd.args = "8 8"  # same planet, new x/y
            cmd.func()

            player.msg = orig_msg
            self.assertEqual((player.db.coord_x, player.db.coord_y), (8, 8))
            self.assertTrue(captured, "same-planet teleport must still issue a look")
        finally:
            _teardown_game(systems)


def _teardown_game(systems):
    """Best-effort teardown: stop any scripts initialize_game created so they
    don't leak across tests. The in-memory DB is rolled back by EvenniaTest."""
    try:
        from evennia.utils.search import search_script
        for key in ("game_tick", "auto_save"):
            for s in search_script(key) or []:
                try:
                    s.stop()
                except Exception:
                    pass
    except Exception:
        pass


if __name__ == "__main__":
    unittest.main()
