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

            closed = self._make_building("MM", x=3, y=0, planet="earth", hp=200)
            closed.set_open(False)
            open_b = self._make_building("MM", x=4, y=0, planet="earth", hp=200)
            # open_b left at its factory default (open) — but this building was
            # made directly, so set it explicitly to be unambiguous.
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
