"""
Combat Engine for the RTS Combat Overworld game.

Resolves attack actions and manages combat state. Reads damage from the
attacker's equipped weapon-slot GameItem and damage_reduction from the
target's equipped armor-slot GameItem.

"""

from __future__ import annotations

from typing import Any, Callable

from world.data_registry import DataRegistry
from world.event_bus import (
    BUILDING_DESTROYED,
    COMBAT_ACTION,
    NPC_ELIMINATED,
    PLAYER_ELIMINATED,
    EventBus,
)
from world.systems.base_system import BaseSystem


def _manhattan_distance(x1: int, y1: int, x2: int, y2: int) -> int:
    """Return the Manhattan distance between two coordinate pairs."""
    return abs(x1 - x2) + abs(y1 - y2)


class CombatEngine(BaseSystem):
    """Resolves combat actions each game tick.

    Args:
        registry: The DataRegistry holding balance config and definitions.
        event_bus: The EventBus for publishing game events.
        current_tick_func: Optional callable returning the current game tick.
    """

    def __init__(
        self,
        registry: DataRegistry,
        event_bus: EventBus,
        current_tick_func: Callable[[], int] | None = None,
        agent_xp_awarder_provider: Callable[[], Any] | None = None,
        player_xp_awarder_provider: Callable[[], Any] | None = None,
    ) -> None:
        super().__init__(registry, event_bus)
        self._current_tick_func = current_tick_func or (lambda: 0)
        self.pending_actions: list[dict] = []
        # Optional line-of-sight predicate (location, x1, y1, x2, y2) -> blocked.
        # Injected at the composition root so turrets don't fire through Walls;
        # None means "no LOS restriction" (unit tests, minimal setups).
        self._sight_blocked: Callable[..., bool] | None = None
        # Late-bound resolver for the agent XP-awarder. CombatEngine is built
        # before AgentSystem at the composition root, so a *callable* is
        # injected (via set_agent_xp_awarder) rather than the instance. Defaults
        # to the game_systems-global lookup for un-injected/legacy contexts.
        self._agent_xp_awarder_provider = agent_xp_awarder_provider
        # Late-bound resolver for the PLAYER XP-awarder (the RankSystem). Routing
        # player combat/kill/base XP through it recomputes level/rank and fires
        # LEVEL_CHANGED / RANK_* — a raw ``db.combat_xp`` write does neither, so
        # kills would grant XP that never levels the player up. Injected via
        # set_player_xp_awarder; falls back to the game_systems-global lookup.
        self._player_xp_awarder_provider = player_xp_awarder_provider

    def set_sight_blocked_func(self, func: Callable[..., bool] | None) -> None:
        """Inject the line-of-sight predicate used to gate turret fire.

        *func* is ``(location, x1, y1, x2, y2) -> bool`` (True = blocked by a
        Wall). Wired at the composition root; when unset, turrets ignore LOS.
        """
        self._sight_blocked = func

    def set_agent_xp_awarder(self, provider: Callable[[], Any]) -> None:
        """Inject the late-bound agent XP-awarder resolver.

        *provider* is a zero-arg callable returning the object exposing
        ``award_agent_xp`` / ``apply_agent_death_loss`` (the AgentSystem), or
        ``None`` when unavailable. Called at the composition root once both
        systems exist, replacing the game_systems-global reach.
        """
        self._agent_xp_awarder_provider = provider

    def set_player_xp_awarder(self, provider: Callable[[], Any]) -> None:
        """Inject the late-bound player XP-awarder resolver (the RankSystem).

        *provider* is a zero-arg callable returning the object exposing
        ``award_xp(player, amount, reason)`` (the RankSystem), or ``None`` when
        unavailable. Wired at the composition root; routing player combat XP
        through it (rather than a raw ``db.combat_xp`` write) recomputes the
        player's level/rank and fires ``LEVEL_CHANGED`` / ``RANK_*``.
        """
        self._player_xp_awarder_provider = provider

    # ------------------------------------------------------------------ #
    #  Queue attack
    # ------------------------------------------------------------------ #

    def queue_attack(
        self, attacker: Any, target: Any, weapon: Any = None
    ) -> tuple[bool, str]:
        """Queue an attack action for resolution on the next tick.

        Validation:
            1. Attacker has a weapon (equipped, or supplied via *weapon*)
            2. Target is not self
            3. Target is in range (Manhattan distance <= weapon range;
               a melee weapon's effective range is always 1)
            4. Ammo (melee weapons never consume ammo):
               - A ranged weapon that declares an ammo_type must have
                 ``db.loaded >= ammo_per_shot`` (else the attack is rejected
                 and the attacker is notified to reload).
               - If the weapon declares a resource ammo_cost, the attacker must
                 have sufficient resources; both checks coexist.
            5. On a proceeding shot, deduct ``ammo_per_shot`` from the weapon's
               magazine (never the Supply_Bag) and any resource ammo_cost.

        Args:
            attacker: The attacking entity.
            target: The target entity.
            weapon: Optional weapon to attack with. When given (e.g. a
                :class:`_GuardWeapon` for an NPC guard that wields no Game_Item),
                it overrides the attacker's equipped-weapon lookup, so the same
                validation/ammo pipeline runs against the supplied weapon.

        Returns:
            (success, message) tuple.
        """
        # 1. Weapon check — a supplied weapon (synthetic guard weapon) overrides
        # the attacker's equipped-slot lookup.
        weapon_item = weapon if weapon is not None else self._get_weapon_item(attacker)
        if weapon_item is None:
            return False, "No weapon equipped."

        # 2. Self-attack prevention
        if attacker is target:
            return False, "You cannot attack yourself."

        # Attacking your OWN buildings is allowed (e.g. to demolish one under
        # fire, or clear a misplaced structure). It grants no XP or benefit
        # (see _handle_building_destruction) but still puts you in the combat
        # state like any other attack — so it is intentionally not rejected here.

        # Weapon typing drives range and ammo handling. A weapon with no
        # weapon_type (legacy/synthetic weapons, e.g. turrets and older test
        # fixtures) keeps the previous behavior: range from the stat, no
        # magazine, resource ammo_cost applied as before.
        weapon_type = self._get_weapon_attr(weapon_item, "weapon_type", None)
        is_melee = weapon_type == "melee"
        is_ranged = weapon_type == "ranged"

        # 2b. Closed-cover gate. A ranged attack cannot hit a closed building
        # or a player sheltered inside one — only an adjacent melee attack can.
        # Runs before range/ammo so a blocked shot never consumes ammo or
        # reports a range error.
        if self._ranged_blocked(target, is_melee):
            if self._is_building(target):
                return False, "That building is closed — only melee attacks reach it."
            return False, "They're sheltered inside — only a melee attack reaches them."

        # 3. Range validation. A melee weapon's effective range is always 1,
        # ignoring any `range` stat on the item.
        if is_melee:
            weapon_range = 1
        else:
            weapon_range = self._get_stat(weapon_item, "range", 1)
        if not self._validate_range(attacker, target, weapon_range):
            a_coords = self._get_coords(attacker)
            if a_coords is None:
                attacker_loc = getattr(attacker, "location", attacker)
                a_coords = self._get_coords(attacker_loc)
            t_coords = self._get_coords(target)
            if t_coords is None:
                target_loc = self._get_target_location(target)
                t_coords = self._get_coords(target_loc)
            if a_coords and t_coords:
                dist = _manhattan_distance(a_coords[0], a_coords[1],
                                           t_coords[0], t_coords[1])
            else:
                dist = "?"
            return False, (
                f"Target is out of range ({dist} tiles, max {weapon_range})."
            )

        # 4. Ammo validation and deduction.
        #
        # Melee weapons never consume ammo — skip ALL ammo handling (neither the
        # magazine nor the resource ammo_cost is touched).
        loaded = 0
        ammo_per_shot = 0
        magazine_draw = False
        if not is_melee:
            # 4a. Magazine gating for ranged weapons that declare an ammo_type.
            # A shot draws from the weapon's loaded magazine (db.loaded); the
            # Supply_Bag is NEVER touched on a shot (it is drawn only by reload).
            ammo_type = self._get_weapon_attr(weapon_item, "ammo_type", None)
            if is_ranged and ammo_type:
                ammo_per_shot = int(
                    self._get_weapon_attr(weapon_item, "ammo_per_shot", 1) or 1
                )
                loaded_val = self._get_loaded(weapon_item)
                loaded = int(loaded_val) if loaded_val is not None else 0
                if loaded < ammo_per_shot:
                    # Empty magazine — reject and prompt reload. The player-facing
                    # message is the presenter's ``out_of_ammo`` notification;
                    # return an empty string so the command layer does not msg a
                    # duplicate line (CmdAttack skips empty results).
                    weapon_name = getattr(weapon_item, "key", str(weapon_item))
                    self.notify(attacker, "out_of_ammo",
                                weapon_name=weapon_name, ammo_name=ammo_type)
                    return False, ""
                magazine_draw = True

            # 4b. Resource ammo_cost (energy weapons / legacy) — applied per
            # shot, in addition to the magazine deduction when both are present.
            ammo_cost = self._get_ammo_cost(weapon_item)
            if ammo_cost:
                err = self._validate_ammo(attacker, ammo_cost)
                if err:
                    return False, err
                attacker.deduct_resources(ammo_cost)

            # 5. Deduct the magazine on a proceeding shot (after all checks pass,
            # so a rejected attack never mutates loaded rounds).
            if magazine_draw:
                self._set_loaded(weapon_item, loaded - ammo_per_shot)

        # Queue the action
        action = {
            "attacker": attacker,
            "target": target,
            "weapon_item": weapon_item,
        }
        self.pending_actions.append(action)

        weapon_name = getattr(weapon_item, "key", str(weapon_item))
        return True, f"Attack queued with {weapon_name}."

    # ------------------------------------------------------------------ #
    #  Resolve tick
    # ------------------------------------------------------------------ #

    def resolve_tick(self, active_buildings: list | None = None) -> None:
        """Resolve all pending attack actions in FIFO order.

        For each action:
            1. Calculate damage
            2. Apply damage to target
            3. Set combat lockout on attacker and target
            4. Publish combat_action event
            5. Notify target
            6. Handle defeat/destruction if HP <= 0

        Args:
            active_buildings: Optional list of active buildings (unused
                in resolve but kept for interface consistency).
        """
        actions = list(self.pending_actions)
        self.pending_actions.clear()

        current_tick = self._current_tick_func()

        for action in actions:
            attacker = action["attacker"]
            target = action["target"]
            weapon_item = action["weapon_item"]

            # Calculate damage
            damage = self._calculate_damage(attacker, target, weapon_item)

            # Apply damage
            self._apply_damage(target, damage, attacker)

            # Lockout + event + notify + defeat/destruction. Shared with the
            # throw AoE path so both resolve hits identically.
            self._finalize_hit(attacker, target, weapon_item, damage,
                               current_tick, lockout_attacker=True)

    def _finalize_hit(
        self,
        attacker: Any,
        target: Any,
        weapon_item: Any,
        damage: int,
        current_tick: int,
        lockout_attacker: bool = True,
    ) -> None:
        """Resolve everything that follows applying damage to a target.

        Shared by ``resolve_tick`` (queued attacks/turrets) and the throwable
        AoE path (``EquipmentSystem._apply_aoe_damage``) so both resolve a hit
        identically: combat lockout, the ``COMBAT_ACTION`` event, the target
        notification, and defeat/destruction when HP reaches zero. The caller
        must have already applied the damage via :meth:`_apply_damage`.

        Args:
            attacker: The entity that dealt the damage.
            target: The entity that took it (HP already reduced).
            weapon_item: The weapon/synthetic item used (for names/notifications).
            damage: The damage that was applied (for the event/notification).
            current_tick: The current game tick, for lockout timing.
            lockout_attacker: Whether to place the attacker in combat lockout.
                True for direct/queued attacks and throws; a turret is a
                building and takes no lockout regardless.
        """
        lockout_ticks = self.registry.balance.combat_lockout_ticks
        lockout_until = current_tick + lockout_ticks
        if lockout_attacker:
            self._set_combat_lockout(attacker, lockout_until)
        if self._is_player(target):
            self._set_combat_lockout(target, lockout_until)

        # Resolve the owning player behind each side so a fight involving A's
        # turret/agent pulls A (and the target's owner, if the target is a
        # unit) into combat mode — not just the units that traded blows.
        attacker_owner = self._owning_player(attacker)
        target_owner = self._owning_player(target)
        if lockout_attacker and attacker_owner is not None and attacker_owner is not attacker:
            self._set_combat_lockout(attacker_owner, lockout_until)
        if target_owner is not None and target_owner is not target:
            self._set_combat_lockout(target_owner, lockout_until)

        # Publish combat_action event (drives the combat timer subscriber).
        # Include current_tick so the combat-timer subscriber doesn't have to
        # re-derive it with a per-hit search_script DB query — the engine already
        # holds the tick here (its injected clock, passed down as current_tick).
        # attacker_owner/target_owner let the timer put the OWNING players (A,
        # and the target's owner) into combat, not only the units involved.
        self.event_bus.publish(
            COMBAT_ACTION,
            attacker=attacker,
            target=target,
            attacker_owner=attacker_owner,
            target_owner=target_owner,
            item=weapon_item,
            damage=damage,
            current_tick=current_tick,
        )

        # Notify the target (or building owner) of the hit.
        self._notify_target(target, attacker, weapon_item, damage)

        # Defeat / destruction when HP has reached zero.
        #
        # Enemy NPCs are checked FIRST, above the _is_player branch: an enemy
        # NPC also satisfies _is_player (it carries db.combat_xp like every
        # CombatEntity), so without this ordering it would be routed to
        # _handle_player_defeat and respawned instead of dying permanently.
        if self._get_hp(target) <= 0:
            if self._is_enemy_npc(target):
                self._handle_enemy_death(target, attacker)
            elif self._is_player(target):
                self._handle_player_defeat(target, attacker)
            elif self._is_building(target):
                self._handle_building_destruction(target, attacker)

    def apply_direct_hit(
        self,
        attacker: Any,
        target: Any,
        weapon_item: Any,
        include_attacker_bonus: bool = True,
        current_tick: int | None = None,
    ) -> int:
        """Resolve a single, immediate hit end-to-end and return the damage.

        The public single-hit entry point (alongside ``queue_attack`` /
        ``resolve_tick`` / ``process_turrets``): computes damage, applies it,
        and runs the shared post-damage resolution (lockout, event, target
        notification, defeat/destruction). Used by non-queued attackers such as
        the throwable AoE path, so those callers depend on this contract rather
        than the engine's private helpers.

        Args:
            attacker: The attacking entity.
            target: The target entity.
            weapon_item: The weapon (or synthetic weapon) used.
            include_attacker_bonus: Whether to add the attacker's aggregated
                ``damage_bonus``. Wielded-weapon attacks pass True; a thrown
                explosive passes False so the blast deals a flat
                ``amount − armor`` (spec Property 12).
            current_tick: The tick for lockout timing; defaults to the engine's
                own clock when omitted.

        Returns:
            int: The damage actually applied.
        """
        if current_tick is None:
            current_tick = self._current_tick_func()
        damage = self._calculate_damage(
            attacker, target, weapon_item,
            include_attacker_bonus=include_attacker_bonus,
        )
        self._apply_damage(target, damage, attacker)
        self._finalize_hit(attacker, target, weapon_item, damage, current_tick)
        return damage

    # ------------------------------------------------------------------ #
    #  Process turrets
    # ------------------------------------------------------------------ #

    def process_turrets(
        self, active_buildings: list, active_owner_ids: set | None = None
    ) -> None:
        """Auto-attack nearest hostile player within turret radius.

        For each active Turret building (one whose BuildingDef declares the
        ``turret`` capability, online, and whose owner has an active HQ):
            - Find nearest non-owner player within turret_radius
            - Queue an attack with turret_damage

        Turrets are identified by the ``turret`` capability, NOT a hardcoded
        building_type — the previous ``"VV"`` check never matched the live
        Turret abbreviation (``"TU"``), so no turret ever fired. Targeting uses
        the PlanetRoom's ``get_nearby_players(x, y, radius)`` spatial query.

        Args:
            active_buildings: List of Building objects to check.
            active_owner_ids: Optional precomputed set of owner ids that have a
                live HQ this tick (see ``world.utils.active_hq_owner_ids``). When
                supplied, the deactivation gate is a cheap ``owner.id in`` set
                membership test instead of a per-turret ``get_buildings()`` DB
                query. When ``None`` (isolated tests), it falls back to the
                per-owner :func:`owner_has_active_hq` live query.
        """
        from world.utils import is_owner, owner_has_active_hq

        turret_radius = self.registry.balance.turret_radius
        turret_damage = self.registry.balance.turret_damage

        for building in active_buildings:
            # Only process turret-capable buildings.
            if not self._is_turret(building):
                continue

            # Skip offline turrets.
            if getattr(building, "is_offline", False):
                continue

            owner = self._get_building_owner(building)
            building_loc = self._get_building_location(building)
            # A Building stores its own coord_x/coord_y; its location is the
            # (coordless) PlanetRoom. Read the building's coords first, then
            # fall back to the location (for test doubles that carry coords on
            # the tile). Reading only the location would leave b_coords None for
            # every real turret — the same silent no-fire the "VV" bug caused.
            b_coords = self._get_coords(building)
            if b_coords is None:
                b_coords = self._get_coords(building_loc)
            if b_coords is None:
                continue

            # A turret whose owner's base is deactivated (no active HQ) does not
            # fire — mirrors the PvP "no HQ = base inert" rule. Prefer the
            # precomputed per-tick owner-id set (one in-memory pass over active
            # buildings); fall back to the per-owner live query when it wasn't
            # supplied (isolated tests).
            if active_owner_ids is not None:
                oid = getattr(owner, "id", None)
                if oid is None or oid not in active_owner_ids:
                    continue
            else:
                planet = getattr(building_loc, "planet_name", None)
                if not owner_has_active_hq(owner, planet, provider=self.registry):
                    continue

            # Find nearest non-owner player within radius via the room's
            # spatial query (3-arg: x, y, radius).
            players = self._nearby_players(building_loc, b_coords[0],
                                           b_coords[1], turret_radius)
            nearest = None
            nearest_dist = turret_radius + 1
            for player in players:
                # Skip the turret owner (compare by .id, not identity).
                if is_owner(player, owner):
                    continue

                # A player sheltered inside a closed building is not a valid
                # turret target (ranged fire can't reach them).
                if self._is_sheltered(player):
                    continue

                p_coords = self._get_coords(player)
                if p_coords is None:
                    p_loc = getattr(player, "location", player)
                    p_coords = self._get_coords(p_loc)
                if p_coords is None:
                    continue

                dist = _manhattan_distance(
                    b_coords[0], b_coords[1],
                    p_coords[0], p_coords[1],
                )
                if dist > turret_radius or dist >= nearest_dist:
                    continue
                # A Wall between the turret and the target blocks the shot.
                if self._sight_blocked is not None and self._sight_blocked(
                    building_loc, b_coords[0], b_coords[1],
                    p_coords[0], p_coords[1],
                ):
                    continue
                nearest = player
                nearest_dist = dist

            if nearest is not None:
                # Create a synthetic turret weapon action.
                turret_action = {
                    "attacker": building,
                    "target": nearest,
                    "weapon_item": _TurretWeapon(turret_damage, turret_radius),
                }
                self.pending_actions.append(turret_action)

    @staticmethod
    def _is_sheltered(target: Any) -> bool:
        """Return True if *target* is a player sheltered inside a closed building.

        Delegates to :func:`world.utils.player_is_sheltered` — the single shelter
        authority shared with guard targeting. A sheltered player is immune to
        ranged fire (turrets/ranged weapons/thrown explosives).
        """
        from world.utils import player_is_sheltered
        return player_is_sheltered(target)

    def _ranged_blocked(self, target: Any, is_melee: bool) -> bool:
        """Return True if a ranged attack must be refused against *target*.

        Two cases, both bypassed by a melee (adjacent) attack:
          * the target is a CLOSED building — ranged weapons/turrets can't
            damage a closed structure; and
          * the target is a player SHELTERED inside a closed building — ranged
            fire can't reach an occupant under cover.
        Open buildings and players in the open are never blocked. The single
        gate shared by every ``queue_attack`` caller (players, agents, guards,
        and any future turret-vs-building path).
        """
        if is_melee:
            return False
        if self._is_building(target):
            from world.utils import building_is_open
            return not building_is_open(target)
        return self._is_sheltered(target)

    def _building_has_cap(self, building: Any, capability: str) -> bool:
        """Return True if *building*'s def declares *capability*.

        Resolves the building_type against the INJECTED registry (keeping tests
        hermetic) rather than the global default provider. Safe when the type is
        unknown or the registry lacks it — returns False.
        """
        building_type = self._get_building_type(building)
        if not building_type:
            return False
        try:
            bdef = self.registry.resolve_building(building_type)
        except Exception:
            return False
        return bdef is not None and bdef.has_capability(capability)

    def _is_turret(self, building: Any) -> bool:
        """Return True if *building* declares the turret capability."""
        from world.constants import TURRET
        return self._building_has_cap(building, TURRET)

    def _is_headquarters(self, building: Any) -> bool:
        """Return True if *building* declares the headquarters capability."""
        from world.constants import HEADQUARTERS
        return self._building_has_cap(building, HEADQUARTERS)

    @staticmethod
    def _nearby_players(location: Any, x: int, y: int, radius: int) -> list:
        """Return players near ``(x, y)`` within *radius* via the location.

        Thin delegator to the shared :func:`world.utils.nearby_players` (also
        used by GuardCombatSystem) so turret and guard targeting resolve players
        through one implementation.
        """
        from world.utils import nearby_players
        return nearby_players(location, x, y, radius)

    # ------------------------------------------------------------------ #
    #  Damage calculation
    # ------------------------------------------------------------------ #

    def _calculate_damage(
        self,
        attacker: Any,
        target: Any,
        weapon_item: Any,
        include_attacker_bonus: bool = True,
    ) -> int:
        """Calculate net damage for an attack.

        Damage = weapon_damage + tech/powerup modifiers - armor_reduction
        Minimum 0.

        Args:
            attacker: The attacking entity.
            target: The target entity.
            weapon_item: The weapon GameItem used.
            include_attacker_bonus: Whether to add the attacker's aggregated
                ``damage_bonus`` (equipment + powerups). True for wielded-weapon
                attacks; thrown-explosive AoE passes False so the blast deals
                its flat ``amount − armor`` (spec Property 12), independent of
                what the thrower happens to be wearing.

        Returns:
            Net damage as an integer (minimum 0).
        """
        base_damage = self._get_stat(weapon_item, "damage", 0)

        # Tech/powerup modifiers from attacker (additive bonus)
        bonus = self._get_attacker_bonus(attacker) if include_attacker_bonus else 0

        # Armor damage reduction from target
        armor_reduction = self._get_target_armor_reduction(target)

        net_damage = int(base_damage + bonus - armor_reduction)
        return max(0, net_damage)

    # ------------------------------------------------------------------ #
    #  Handle player defeat
    # ------------------------------------------------------------------ #

    def _handle_player_defeat(self, victim: Any, attacker: Any) -> None:
        """Handle a player or agent being defeated (HP <= 0).

        Agents are ``CombatEntity`` NPCs that also carry ``db.combat_xp``, so
        they reach this handler through the same ``_is_player`` gate as players.
        XP is attributed under the single-owner model:

        - Kill XP goes to the attacker's OWNING PLAYER (``_owning_player``): a
          kill by A's turret or A's agent credits A, a direct player kill
          credits that player, and an enemy/ownerless attacker earns nothing.
          Routed through the progression path so level/rank recompute and fire
          events. Friendly fire (downing your own unit) grants no reward.
        - When the victim is an agent, death loss goes through
          ``AgentSystem.apply_agent_death_loss`` (the agent balance), not the
          player ``xp_death_loss`` deduction, so it is never double-applied.
        - Respawn victim (reset HP).
        - Publish ``player_eliminated`` with owner attribution for the
          announcement ("Player A's Turret has eliminated Player B").

        Args:
            victim: The defeated player or agent.
            attacker: The player/agent/entity that defeated them.
        """
        xp_kill = self.registry.balance.xp_kill
        xp_death_loss = self.registry.balance.xp_death_loss

        # Friendly fire grants no reward: defeating your OWN agent yields no kill
        # XP (mirrors destroying your own building), so it can't be farmed. The
        # victim's death loss still applies below — attacking your own units is
        # allowed but purely costly. Compare owners by .id (via is_owner), not
        # identity: an anti-farm guard must not false-negative if the owner and
        # attacker are distinct instances of the same PK after an idmapper flush.
        from world.utils import is_owner
        victim_owner = getattr(getattr(victim, "db", None), "owner", None)
        own_victim = is_owner(attacker, victim_owner)

        # Award XP to the attacker's OWNING PLAYER — a kill by A's turret or A's
        # agent is credited to A (single-owner model), not banked on the unit.
        # A bare player is its own owner. Friendly fire and ownerless/enemy
        # attackers earn nothing.
        attacker_owner = self._owning_player(attacker)
        if own_victim:
            pass  # no reward for friendly fire on your own unit
        elif attacker_owner is not None:
            # Route through the progression path (recompute level/rank + events),
            # not a raw db.combat_xp write.
            self._award_player_combat_xp(attacker_owner, xp_kill)
            # Cosmetic acknowledgment: tally the kill on the acting unit (a
            # player or agent tracks its own; a turret's tallies on its owner).
            # This does NOT feed XP/level/cap — it's a stat, not progression.
            self._record_kill(attacker)

        # Cosmetic death tally on the victim (player or agent) — the mirror of
        # the kill tally. Counts every defeat, including friendly fire (a death
        # is a death); a stat only, never a progression input.
        self._record_death(victim)

        # Deduct XP from victim.
        if self._is_agent(victim):
            # Agents use the agent death-loss balance, not the player one.
            # Routed through AgentSystem to avoid double-deducting.
            self._apply_agent_death_loss(victim)
        else:
            # Player victim: route the death-loss through the progression path so
            # a level/rank drop recomputes and fires LEVEL_CHANGED / RANK_*.
            self._deduct_player_combat_xp(victim, xp_death_loss)

        # Respawn victim (reset HP)
        hp_max = self._get_hp_max(victim)
        self._set_hp(victim, hp_max)

        # Publish event with owner attribution so the announcement reads
        # "Player A[ 's Turret/Agent] has eliminated Player B" — the kill is
        # credited to the owning player, with the unit kind for phrasing.
        self.event_bus.publish(
            PLAYER_ELIMINATED,
            attacker=attacker,
            victim=victim,
            attacker_owner=attacker_owner,
            attacker_kind=self._unit_kind(attacker),
        )

    # ------------------------------------------------------------------ #
    #  Handle enemy-NPC death (permanent)
    # ------------------------------------------------------------------ #

    def _handle_enemy_death(self, victim: Any, attacker: Any) -> None:
        """Handle an enemy NPC (an NPC-base guard) being killed at 0 HP.

        Enemy NPCs die permanently — the antithesis of player agents, which
        ``_handle_player_defeat`` respawns. This:

        - Awards the attacker ``xp_kill`` (same balance as a player kill),
          under the same ``is_owner`` friendly-fire guard: destroying a unit you
          own grants nothing, so it can't be farmed. Agent attackers route their
          kill XP through the freeze-aware AgentSystem, mirroring
          ``_handle_player_defeat``.
        - Publishes ``NPC_ELIMINATED`` (the base-elimination handler and future
          consumers subscribe) BEFORE deleting, so subscribers can still read
          the victim's coordinates/owner.
        - Deletes the victim (``target.delete()``). ``NPC.at_object_delete``
          already bumps the agent-index generation, so the tick loop's cached
          roster is invalidated — no explicit bump needed here.

        Args:
            victim: The enemy NPC that reached 0 HP.
            attacker: The entity that killed it.
        """
        from world.utils import is_owner

        xp_kill = self.registry.balance.xp_kill

        # Award kill XP to the attacker's OWNING PLAYER (A's turret/agent kill is
        # credited to A), under the same anti-farm guard: no reward for killing
        # your own unit. A bare player is its own owner; enemy/ownerless
        # attackers earn nothing.
        owner = getattr(getattr(victim, "db", None), "owner", None)
        own_victim = is_owner(attacker, owner)
        attacker_owner = self._owning_player(attacker)
        if own_victim:
            pass  # friendly fire — no reward
        elif attacker_owner is not None:
            self._award_player_combat_xp(attacker_owner, xp_kill)
            # Cosmetic kill tally on the acting unit (see _handle_player_defeat).
            self._record_kill(attacker)

        tile = self._get_target_location(victim)
        victim_name = getattr(victim, "key", "enemy")

        # Notify the attacker's owning player of the kill + XP (A hears it
        # whether A, A's turret, or A's agent scored it). Guarded inside notify
        # for a None/NPC player.
        if not own_victim and attacker_owner is not None:
            self.notify(attacker_owner, "npc_killed", name=victim_name, xp=xp_kill)

        # Publish BEFORE delete so subscribers can read victim state/coords.
        self.event_bus.publish(
            NPC_ELIMINATED,
            attacker=attacker,
            victim=victim,
            tile=tile,
            attacker_owner=attacker_owner,
            attacker_kind=self._unit_kind(attacker),
        )

        # Permanent death — delete the NPC (bumps the agent-index generation
        # via NPC.at_object_delete, invalidating the tick roster cache).
        if hasattr(victim, "delete"):
            victim.delete()

    # ------------------------------------------------------------------ #
    #  Handle building destruction
    # ------------------------------------------------------------------ #

    def _handle_building_destruction(
        self, building: Any, attacker: Any
    ) -> None:
        """Handle a building being destroyed (HP <= 0).

        - Award XP to attacker
        - Remove building
        - Publish building_destroyed event

        Args:
            building: The destroyed building.
            attacker: The entity that destroyed it.
        """
        xp_building_destroy = self.registry.balance.xp_building_destroy

        # Award XP to attacker (if it's a player) — but NOT for destroying your
        # own building. Attacking your own structures is permitted, yet grants
        # no XP or benefit, so it can't be farmed for progression. Compare by
        # .id (via is_owner), not identity, so the guard survives an idmapper
        # flush that leaves owner and attacker as distinct same-PK instances.
        from world.utils import is_owner
        owner = self._get_building_owner(building)
        own_building = is_owner(attacker, owner)
        # An enemy NPC satisfies _is_player (carries combat_xp) but has no
        # progression — exclude it so a (future) enemy-destroys-building path
        # can't route XP into the RankSystem. Agents earn no building XP either.
        if (self._is_player(attacker) and not self._is_enemy_npc(attacker)
                and not self._is_agent(attacker) and not own_building):
            self._award_player_combat_xp(attacker, xp_building_destroy)

        tile = self._get_building_location(building)

        # Publish event
        self.event_bus.publish(
            BUILDING_DESTROYED,
            attacker=attacker,
            building=building,
            tile=tile,
        )

        # Losing your HQ deactivates the whole base until you rebuild one (the
        # PvP "no HQ = base inert" rule). Tell the owner. Only for a player-owned
        # HQ — an NPC base's HQ destruction is handled by the base-elimination
        # path (Phase 5), not this deactivation notice.
        if owner is not None and self._is_headquarters(building) \
                and self._is_player(owner):
            self.notify(owner, "base_deactivated")

        # Remove building
        if hasattr(building, "delete"):
            building.delete()

    # ------------------------------------------------------------------ #
    #  Notification
    # ------------------------------------------------------------------ #

    def _notify_target(
        self, target: Any, attacker: Any, weapon_item: Any, damage: int
    ) -> None:
        """Send per-hit combat notices to the players behind both sides.

        Defensive side — the player who was hit (or whose unit was hit):
          - player target → notified directly ("attacked").
          - agent target  → its OWNER notified ("unit_attacked", agent).
          - building target → its OWNER notified ("building_attacked").
          Order matters: an agent satisfies ``_is_player`` (it carries
          ``combat_xp``), so the agent/building cases are checked BEFORE the
          plain-player branch — otherwise an agent hit would msg() the agent
          (no session) and its owner would hear nothing.

        Offensive side — when the attacker is a player's turret/agent, that
        owning player is told its unit struck ("unit_attack"). A bare player
        attacker gets no offensive notice (the target's "attacked" line covers
        it), matching the pre-existing behavior.
        """
        attacker_name = getattr(attacker, "key", "Unknown")
        weapon_name = getattr(weapon_item, "key", str(weapon_item))

        # --- Defensive notice ---
        if self._is_agent(target):
            owner = getattr(getattr(target, "db", None), "owner", None)
            if owner is not None:
                self.notify(owner, "unit_attacked", unit_kind="agent",
                            unit_name=getattr(target, "key", "Agent"),
                            attacker_name=attacker_name, weapon_name=weapon_name,
                            damage=damage)
        elif self._is_building(target):
            owner = self._get_building_owner(target)
            if owner is not None:
                building_name = getattr(target, "key", "building")
                self.notify(owner, "building_attacked", building_name=building_name,
                            attacker_name=attacker_name, weapon_name=weapon_name,
                            damage=damage)
        elif self._is_player(target):
            self.notify(target, "attacked", attacker_name=attacker_name,
                        weapon_name=weapon_name, damage=damage)

        # --- Offensive notice: A's turret/agent struck someone. ---
        attacker_owner = self._owning_player(attacker)
        attacker_kind = self._unit_kind(attacker)
        if (attacker_owner is not None and attacker_owner is not attacker
                and attacker_kind):
            self.notify(attacker_owner, "unit_attack", unit_kind=attacker_kind,
                        unit_name=attacker_name,
                        target_name=getattr(target, "key", "target"),
                        weapon_name=weapon_name, damage=damage)

    # ------------------------------------------------------------------ #
    #  Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _get_weapon_item(attacker: Any) -> Any | None:
        """Get the weapon-slot GameItem from the attacker."""
        equipment = getattr(attacker, "equipment", None)
        if equipment is None:
            return None
        if hasattr(equipment, "get_equipped"):
            return equipment.get_equipped("weapon")
        return None

    def _validate_range(
        self, attacker: Any, target: Any, weapon_range: int
    ) -> bool:
        """Check if target is within weapon range (Manhattan distance)."""
        # Read coords from the entity directly (handles PlanetRoom players)
        a_coords = self._get_coords(attacker)
        t_coords = self._get_coords(target)

        # Fall back to location coords for entities without direct coords
        if a_coords is None:
            attacker_loc = getattr(attacker, "location", attacker)
            a_coords = self._get_coords(attacker_loc)
        if t_coords is None:
            target_loc = self._get_target_location(target)
            t_coords = self._get_coords(target_loc)

        if a_coords is None or t_coords is None:
            # Can't validate without coordinates — allow by default
            return True

        dist = _manhattan_distance(
            a_coords[0], a_coords[1],
            t_coords[0], t_coords[1],
        )
        return dist <= weapon_range

    @staticmethod
    def _validate_ammo(attacker: Any, ammo_cost: dict[str, int]) -> str | None:
        """Check attacker has sufficient ammo resources. Returns error or None."""
        if not hasattr(attacker, "has_resources"):
            return None
        if attacker.has_resources(ammo_cost):
            return None

        missing = []
        for resource, needed in ammo_cost.items():
            current = attacker.get_resource(resource)
            if current < needed:
                missing.append(f"need {needed} {resource}, have {current}")
        return "Insufficient ammo: " + "; ".join(missing) + "."

    @staticmethod
    def _get_stat(item: Any, stat_name: str, default: float = 0) -> float:
        """Read a stat from a GameItem."""
        if hasattr(item, "get_stat"):
            return item.get_stat(stat_name, default)
        if hasattr(item, "stat_modifiers"):
            mods = item.stat_modifiers
            if isinstance(mods, dict):
                return float(mods.get(stat_name, default))
        return default

    @staticmethod
    def _get_ammo_cost(weapon_item: Any) -> dict[str, int] | None:
        """Read ammo_cost from a weapon item."""
        if hasattr(weapon_item, "ammo_cost"):
            cost = weapon_item.ammo_cost
            if isinstance(cost, dict) and cost:
                return cost
        return None

    @staticmethod
    def _get_weapon_attr(weapon_item: Any, name: str, default: Any = None) -> Any:
        """Read a weapon field (weapon_type/ammo_type/ammo_per_shot/…).

        Handles both a live ``GameItem`` (named property accessors) and a
        dict-shaped test weapon. A missing field resolves to *default*, which is
        how legacy/synthetic weapons (no ``weapon_type``) keep their old
        behavior.
        """
        if isinstance(weapon_item, dict):
            return weapon_item.get(name, default)
        return getattr(weapon_item, name, default)

    @staticmethod
    def _get_loaded(weapon_item: Any) -> int:
        """Read a ranged weapon's loaded-round count (0 when unset)."""
        return get_loaded(weapon_item)

    @staticmethod
    def _set_loaded(weapon_item: Any, value: int) -> bool:
        """Write a ranged weapon's loaded-round count; True on success."""
        return set_loaded(weapon_item, value)

    def _get_attacker_bonus(self, attacker: Any) -> float:
        """Get tech/powerup/equipment damage bonus for the attacker."""
        # Flat gear damage_bonus (gloves, accessory) aggregates across all
        # equipped items here; active powerups add a further timed bonus.
        bonus = 0.0

        # Aggregate flat damage_bonus across equipped gear (guard for a
        # missing equipment handler, e.g. synthetic/turret attackers).
        equipment = getattr(attacker, "equipment", None)
        if equipment and hasattr(equipment, "get_stat_total"):
            bonus += equipment.get_stat_total("damage_bonus")

        # Check active powerups for damage_bonus
        active_powerups = None
        if hasattr(attacker, "db"):
            active_powerups = getattr(attacker.db, "active_powerups", None)
        if active_powerups and isinstance(active_powerups, dict):
            for _key, pdata in active_powerups.items():
                if isinstance(pdata, dict):
                    effect = pdata.get("effect", {})
                    if isinstance(effect, dict):
                        if effect.get("effect_type") == "damage_bonus":
                            # Multiplicative bonus stored as multiplier
                            bonus += float(effect.get("effect_value", 0))
        return bonus

    def _get_target_armor_reduction(self, target: Any) -> float:
        """Get armor damage_reduction from the target."""
        if not self._is_player(target):
            return 0.0
        equipment = getattr(target, "equipment", None)
        if equipment and hasattr(equipment, "get_stat_total"):
            return equipment.get_stat_total("damage_reduction")
        return 0.0

    @staticmethod
    def _get_coords(obj: Any) -> tuple[int, int] | None:
        """Extract (x, y) coordinates from an object."""
        from world.utils import get_coords
        return get_coords(obj)

    @staticmethod
    def _get_target_location(target: Any) -> Any:
        """Get the location of a target (player or building)."""
        loc = getattr(target, "location", None)
        if loc is not None:
            return loc
        return target

    @staticmethod
    def _is_player(entity: Any) -> bool:
        """Check if an entity is a player character."""
        from world.utils import is_player
        return is_player(entity)

    @staticmethod
    def _is_building(entity: Any) -> bool:
        """Check if an entity is a building."""
        from world.utils import is_building
        return is_building(entity)

    @staticmethod
    def _is_agent(entity: Any) -> bool:
        """Check if an entity is a player-owned NPC agent.

        An agent is an NPC with ``db.npc_type == "agent"`` (mirrors the
        convention used by AgentSystem and the agent scripts).
        """
        if entity is None or not hasattr(entity, "db"):
            return False
        return getattr(entity.db, "npc_type", None) == "agent"

    @staticmethod
    def _is_enemy_npc(entity: Any) -> bool:
        """Check if an entity is an enemy NPC (an NPC-base guard).

        An enemy NPC has ``db.npc_type == "enemy"``. Unlike a player agent
        (``"agent"``), an enemy is deleted permanently at 0 HP rather than
        respawned — the check that routes it to :meth:`_handle_enemy_death`.
        """
        if entity is None or not hasattr(entity, "db"):
            return False
        return getattr(entity.db, "npc_type", None) == "enemy"

    def _owning_player(self, entity: Any) -> Any | None:
        """Resolve the *responsible player* behind a combat entity.

        The single "who does this act on behalf of" resolver shared by kill
        attribution, combat-mode entry, and owner notifications:

        - A player is its own owner (returned as-is).
        - A player-owned agent or a building returns its ``db.owner`` / ``.owner``
          — so a turret/agent's kill is credited to, and combat/notices routed
          to, the player who owns it.
        - An enemy NPC-base guard (``npc_type == "enemy"``) has no player owner
          for these purposes, so returns None (its ``db.owner`` is the NPC base).

        Returns None when no player can be resolved (ownerless unit, raw double).
        """
        if entity is None:
            return None
        if self._is_enemy_npc(entity):
            return None
        if self._is_agent(entity):
            return getattr(getattr(entity, "db", None), "owner", None)
        if self._is_building(entity):
            return self._get_building_owner(entity)
        if self._is_player(entity):
            return entity
        return None

    def _record_kill(self, attacker: Any) -> None:
        """Increment the cosmetic kill tally for a scored kill.

        A player or agent tracks its OWN kills (shown on its score sheet); a
        turret/building has no sheet, so its kill tallies on the owning player
        instead. Purely a stat — it never feeds XP, level, or the owner cap
        (that is the whole point of the acknowledgment-not-XP design). Guarded
        so a missing/odd ``db`` never breaks combat resolution.
        """
        # The acting unit that gets the tally: player or agent tallies itself;
        # anything else (a turret) tallies its owning player.
        unit = attacker if (self._is_player(attacker) or self._is_agent(attacker)) \
            else self._owning_player(attacker)
        db = getattr(unit, "db", None)
        if db is None:
            return
        try:
            db.kills = int(getattr(db, "kills", 0) or 0) + 1
        except Exception:  # noqa: BLE001 - a tally must never break combat
            pass

    def _record_death(self, victim: Any) -> None:
        """Increment the cosmetic death tally on a defeated player/agent.

        The mirror of :meth:`_record_kill`, tallied on the victim itself (both
        players and agents carry a ``db`` counter and a score sheet). Counts
        every defeat regardless of who dealt it (friendly fire included) —
        purely a stat, never a progression input. Guarded so combat never
        breaks. Not called for enemy-NPC deaths (they have no score sheet).
        """
        db = getattr(victim, "db", None)
        if db is None:
            return
        try:
            db.deaths = int(getattr(db, "deaths", 0) or 0) + 1
        except Exception:  # noqa: BLE001 - a tally must never break combat
            pass

    def _unit_kind(self, entity: Any) -> str:
        """Return an attribution token for *entity*: 'turret', 'agent', or ''.

        Used to phrase owner-attributed lines ("Player A's Turret has
        eliminated ...", "Your Turret attacked ..."). A bare player returns ''
        (no possessive unit suffix); anything unrecognized also returns ''.
        """
        if self._is_building(entity):
            return "turret" if self._is_turret(entity) else "building"
        if self._is_agent(entity):
            return "agent"
        return ""

    def _get_agent_system(self) -> Any | None:
        """Resolve the agent XP-awarder (the AgentSystem).

        Prefers the injected late-bound provider; falls back to the
        game_systems-global lookup for un-injected/legacy contexts. Guarded so
        combat never breaks if the agent system is unavailable (e.g. in tests
        or before initialization).
        """
        provider = self._agent_xp_awarder_provider
        if provider is not None:
            try:
                return provider()
            except Exception:  # noqa: BLE001 - never let resolution break combat
                return None
        try:
            from server.conf.game_init import game_systems

            return game_systems.get("agent_system")
        except Exception:  # noqa: BLE001 - never let a missing system break combat
            return None

    def _apply_agent_death_loss(self, agent: Any) -> None:
        """Apply agent death-loss XP via the AgentSystem.

        Routes an agent victim's death penalty through the agent death-loss
        balance instead of the player ``xp_death_loss`` path, avoiding a double
        deduction. Guarded so combat never breaks if the system is unavailable.
        """
        agent_system = self._get_agent_system()
        if agent_system is None:
            return
        try:
            agent_system.apply_agent_death_loss(agent)
        except Exception:  # noqa: BLE001 - never let death loss break combat
            pass

    def _get_rank_system(self) -> Any | None:
        """Resolve the player XP-awarder (the RankSystem).

        Prefers the injected late-bound provider; falls back to the
        game_systems-global lookup for un-injected/legacy contexts. Guarded so
        combat never breaks if the rank system is unavailable.
        """
        provider = self._player_xp_awarder_provider
        if provider is not None:
            try:
                return provider()
            except Exception:  # noqa: BLE001 - never let resolution break combat
                return None
        try:
            from server.conf.game_init import game_systems

            return game_systems.get("rank_system")
        except Exception:  # noqa: BLE001 - never let a missing system break combat
            return None

    def _award_player_combat_xp(self, player: Any, amount: int) -> None:
        """Award combat/kill/base XP to a *player* through the progression path.

        A raw ``db.combat_xp`` write does NOT recompute level/rank or fire
        ``LEVEL_CHANGED`` / ``RANK_*``, so combat kills would grant XP that never
        levels the player up or unlocks ranks. This routes through, in order of
        preference:

        1. The injected RankSystem (``award_xp`` — recompute + level/rank events),
        2. the entity's own ``CombatEntity.award_xp`` (recompute, no events), or
        3. a raw ``db.combat_xp`` increment (last-resort for minimal test doubles).

        Guarded at each level so combat resolution never breaks.
        """
        if amount <= 0:
            return
        rank_system = self._get_rank_system()
        if rank_system is not None and hasattr(rank_system, "award_xp"):
            try:
                rank_system.award_xp(player, amount, reason="combat")
            except Exception:  # noqa: BLE001
                # RankSystem.award_xp mutates combat_xp BEFORE it can raise (the
                # failure is downstream, in _sync_level / event dispatch), so the
                # XP is already applied — do NOT fall through to award it again
                # (that would double-count). Swallow: combat must never break.
                pass
            return
        # No rank system (isolated tests / early boot): recompute locally if the
        # entity supports it, else fall back to the raw increment.
        if hasattr(player, "award_xp"):
            try:
                player.award_xp(amount)
                return
            except Exception:  # noqa: BLE001 - fall through to raw set
                pass
        self._set_combat_xp(player, self._get_combat_xp(player) + amount)

    def _deduct_player_combat_xp(self, player: Any, amount: int) -> None:
        """Deduct death-loss XP from a *player* through the progression path.

        Mirrors :meth:`_award_player_combat_xp`: prefers the RankSystem
        (``deduct_xp`` — recompute + level/rank-down events), falls back to the
        entity's ``CombatEntity.deduct_xp`` (recompute, no events), then to a raw
        floored ``db.combat_xp`` write. Guarded so combat never breaks.
        """
        if amount <= 0:
            return
        rank_system = self._get_rank_system()
        if rank_system is not None and hasattr(rank_system, "deduct_xp"):
            try:
                rank_system.deduct_xp(player, amount)
            except Exception:  # noqa: BLE001
                # deduct_xp mutates combat_xp before it can raise downstream — do
                # NOT fall through and deduct again (double-deduction). Swallow.
                pass
            return
        if hasattr(player, "deduct_xp"):
            try:
                player.deduct_xp(amount)
                return
            except Exception:  # noqa: BLE001 - fall through to raw set
                pass
        self._set_combat_xp(player, max(0, self._get_combat_xp(player) - amount))

    @staticmethod
    def _get_building_type(building: Any) -> str | None:
        """Read building_type from a building."""
        from world.utils import get_building_type
        return get_building_type(building)

    @staticmethod
    def _get_building_owner(entity: Any) -> Any | None:
        """Get the owner of a building, or None if not a building."""
        if hasattr(entity, "owner"):
            return entity.owner
        if hasattr(entity, "attributes") and hasattr(entity.attributes, "get"):
            return entity.attributes.get("owner", default=None)
        return None

    @staticmethod
    def _get_building_location(building: Any) -> Any:
        """Get the location/tile of a building."""
        return getattr(building, "location", None)

    @staticmethod
    def _get_hp(entity: Any) -> int:
        """Get current HP from an entity."""
        if hasattr(entity, "db") and hasattr(entity.db, "hp"):
            return entity.db.hp or 0
        if hasattr(entity, "attributes") and hasattr(entity.attributes, "get"):
            return entity.attributes.get("hp", default=0) or 0
        return 0

    @staticmethod
    def _get_hp_max(entity: Any) -> int:
        """Get max HP from an entity."""
        if hasattr(entity, "db") and hasattr(entity.db, "hp_max"):
            return entity.db.hp_max or 100
        if hasattr(entity, "attributes") and hasattr(entity.attributes, "get"):
            return entity.attributes.get("hp_max", default=100) or 100
        return 100

    @staticmethod
    def _set_hp(entity: Any, hp: int) -> None:
        """Set current HP on an entity."""
        if hasattr(entity, "db") and hasattr(entity.db, "hp"):
            entity.db.hp = hp
        elif hasattr(entity, "attributes") and hasattr(entity.attributes, "add"):
            entity.attributes.add("hp", hp)

    @staticmethod
    def _get_combat_xp(entity: Any) -> int:
        """Get combat XP from a player."""
        if hasattr(entity, "db"):
            return getattr(entity.db, "combat_xp", 0) or 0
        return 0

    @staticmethod
    def _set_combat_xp(entity: Any, xp: int) -> None:
        """Set combat XP on a player."""
        if hasattr(entity, "db"):
            entity.db.combat_xp = xp

    @staticmethod
    def _set_combat_lockout(entity: Any, tick: int) -> None:
        """Set combat lockout tick on an entity."""
        if hasattr(entity, "db") and hasattr(entity.db, "combat_lockout_tick"):
            entity.db.combat_lockout_tick = tick

    def _apply_damage(self, target: Any, damage: int, attacker: Any) -> None:
        """Apply damage to a target's HP."""
        current_hp = self._get_hp(target)
        new_hp = max(0, current_hp - damage)
        self._set_hp(target, new_hp)


def get_loaded(weapon: Any) -> int:
    """Read a ranged weapon's loaded-round count, or 0 when unset.

    The single loaded-rounds accessor shared by combat (magazine draw) and
    equipment (reload). Reads ``db.loaded`` on a live ``GameItem`` and falls
    back to a ``"loaded"`` key/attribute for dict-shaped test weapons. Any
    missing or non-integer value yields 0, matching how ``queue_attack``
    treats a not-yet-loaded weapon.
    """
    if isinstance(weapon, dict):
        raw = weapon.get("loaded", 0)
    else:
        db = getattr(weapon, "db", None)
        raw = getattr(db, "loaded", None) if db is not None else \
            getattr(weapon, "loaded", None)
    try:
        return int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        return 0


def set_loaded(weapon: Any, value: int) -> bool:
    """Write a ranged weapon's loaded-round count; return True on success.

    Counterpart to :func:`get_loaded`. Writes ``db.loaded`` on a live
    ``GameItem`` and falls back to a ``"loaded"`` key/attribute for dict-shaped
    test weapons. Returns ``False`` if a persistent write raised, so callers
    (e.g. ``reload``) can avoid a half-mutation.
    """
    value = int(value)
    if isinstance(weapon, dict):
        weapon["loaded"] = value
        return True
    db = getattr(weapon, "db", None)
    if db is not None:
        try:
            db.loaded = value
            return True
        except Exception:  # noqa: BLE001 - fall through to a plain-attr write
            pass
    try:
        weapon.loaded = value
        return True
    except Exception:  # noqa: BLE001 - never let a write break combat/reload
        return False


class SyntheticWeapon:
    """A weapon-shaped object for attackers that wield no real Game_Item.

    Used by non-equipped attackers — turret auto-attacks and thrown-explosive
    AoE — to route through the same damage pipeline as a wielded weapon. It
    exposes just the surface the pipeline reads: ``key`` (for notifications),
    ``stat_modifiers`` (``damage``/``range``), no ``ammo_cost``, and a
    ``get_stat`` accessor.
    """

    def __init__(self, damage: int, weapon_range: int, name: str = "Attack") -> None:
        self.key = name
        self.stat_modifiers = {"damage": damage, "range": weapon_range}
        self.ammo_cost = None

    def get_stat(self, stat_name: str, default: float = 0) -> float:
        return float(self.stat_modifiers.get(stat_name, default))


class _TurretWeapon(SyntheticWeapon):
    """Synthetic weapon item for turret auto-attacks."""

    def __init__(self, damage: int, weapon_range: int) -> None:
        super().__init__(damage, weapon_range, name="Turret")


class _GuardWeapon(SyntheticWeapon):
    """Synthetic weapon for NPC-guard auto-attacks (same pattern as _TurretWeapon).

    Carries a ``weapon_type`` (``"melee"`` or ``"ranged"``) so it flows through
    the standard ``queue_attack`` validation exactly like a wielded weapon: a
    melee guard's effective range is forced to 1, a ranged guard uses its
    ``range`` stat. It is ammo-free — ``ammo_type``/``ammo_cost`` are ``None`` so
    no magazine gating or resource cost applies — the guard just attacks.
    """

    def __init__(
        self, damage: int, weapon_range: int, weapon_type: str = "melee"
    ) -> None:
        super().__init__(damage, weapon_range, name="Guard")
        self.weapon_type = weapon_type
        self.ammo_type = None
        self.ammo_per_shot = 0
        self.magazine_size = None
