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
    ) -> None:
        super().__init__(registry, event_bus)
        self._current_tick_func = current_tick_func or (lambda: 0)
        self.pending_actions: list[dict] = []
        # Late-bound resolver for the agent XP-awarder. CombatEngine is built
        # before AgentSystem at the composition root, so a *callable* is
        # injected (via set_agent_xp_awarder) rather than the instance. Defaults
        # to the game_systems-global lookup for un-injected/legacy contexts.
        self._agent_xp_awarder_provider = agent_xp_awarder_provider

    def set_agent_xp_awarder(self, provider: Callable[[], Any]) -> None:
        """Inject the late-bound agent XP-awarder resolver.

        *provider* is a zero-arg callable returning the object exposing
        ``award_agent_xp`` / ``apply_agent_death_loss`` (the AgentSystem), or
        ``None`` when unavailable. Called at the composition root once both
        systems exist, replacing the game_systems-global reach.
        """
        self._agent_xp_awarder_provider = provider

    # ------------------------------------------------------------------ #
    #  Queue attack
    # ------------------------------------------------------------------ #

    def queue_attack(
        self, attacker: Any, target: Any
    ) -> tuple[bool, str]:
        """Queue an attack action for resolution on the next tick.

        Validation:
            1. Attacker has a weapon equipped
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

        Returns:
            (success, message) tuple.
        """
        # 1. Weapon check
        weapon_item = self._get_weapon_item(attacker)
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

        # Publish combat_action event (drives the combat timer subscriber).
        self.event_bus.publish(
            COMBAT_ACTION,
            attacker=attacker,
            target=target,
            item=weapon_item,
            damage=damage,
        )

        # Notify the target (or building owner) of the hit.
        self._notify_target(target, attacker, weapon_item, damage)

        # Defeat / destruction when HP has reached zero.
        if self._get_hp(target) <= 0:
            if self._is_player(target):
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

    def process_turrets(self, active_buildings: list) -> None:
        """Auto-attack nearest hostile player within turret radius.

        For each active turret building (building_type="VV", not offline):
            - Find nearest hostile player within turret_radius
            - Queue an attack with turret_damage

        Args:
            active_buildings: List of Building objects to check.
        """
        turret_radius = self.registry.balance.turret_radius
        turret_damage = self.registry.balance.turret_damage

        for building in active_buildings:
            # Only process turrets
            building_type = self._get_building_type(building)
            if building_type != "VV":
                continue

            # Skip offline turrets
            if getattr(building, "is_offline", False):
                continue

            owner = self._get_building_owner(building)
            building_loc = self._get_building_location(building)
            b_coords = self._get_coords(building_loc)
            if b_coords is None:
                continue

            # Find nearest hostile player within radius
            nearest = None
            nearest_dist = turret_radius + 1

            # Get players from the building's location context
            players = self._get_nearby_players(building_loc, turret_radius)
            for player in players:
                # Skip the turret owner
                if player is owner:
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
                if dist <= turret_radius and dist < nearest_dist:
                    nearest = player
                    nearest_dist = dist

            if nearest is not None:
                # Create a synthetic turret weapon action
                turret_action = {
                    "attacker": building,
                    "target": nearest,
                    "weapon_item": _TurretWeapon(turret_damage, turret_radius),
                }
                self.pending_actions.append(turret_action)

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
        Their XP, however, is routed through the freeze-aware ``AgentSystem``
        rather than the player XP balance:

        - When the attacker is an agent, award ``"combat"`` XP through
          ``AgentSystem.award_agent_xp`` (freeze-aware); when the
          attacker is a (non-agent) player, award the player ``xp_kill`` balance.
        - When the victim is an agent, apply death loss through
          ``AgentSystem.apply_agent_death_loss`` instead of the player
          ``xp_death_loss`` deduction, so death loss is never double-applied.
        - Respawn victim (reset HP).
        - Publish player_eliminated event.

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

        # Award XP to attacker.
        if own_victim:
            pass  # no reward for friendly fire on your own agent
        elif self._is_agent(attacker):
            # Agent combat kill XP via the freeze-aware AgentSystem.
            self._award_agent_combat_xp(attacker)
        elif self._is_player(attacker):
            attacker_xp = self._get_combat_xp(attacker)
            self._set_combat_xp(attacker, attacker_xp + xp_kill)

        # Deduct XP from victim.
        if self._is_agent(victim):
            # Agents use the agent death-loss balance, not the player one.
            # Routed through AgentSystem to avoid double-deducting.
            self._apply_agent_death_loss(victim)
        else:
            victim_xp = self._get_combat_xp(victim)
            new_xp = max(0, victim_xp - xp_death_loss)
            self._set_combat_xp(victim, new_xp)

        # Respawn victim (reset HP)
        hp_max = self._get_hp_max(victim)
        self._set_hp(victim, hp_max)

        # Publish event
        self.event_bus.publish(
            PLAYER_ELIMINATED,
            attacker=attacker,
            victim=victim,
        )

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
        if self._is_player(attacker) and not own_building:
            attacker_xp = self._get_combat_xp(attacker)
            self._set_combat_xp(attacker, attacker_xp + xp_building_destroy)

        tile = self._get_building_location(building)

        # Publish event
        self.event_bus.publish(
            BUILDING_DESTROYED,
            attacker=attacker,
            building=building,
            tile=tile,
        )

        # Remove building
        if hasattr(building, "delete"):
            building.delete()

    # ------------------------------------------------------------------ #
    #  Notification
    # ------------------------------------------------------------------ #

    def _notify_target(
        self, target: Any, attacker: Any, weapon_item: Any, damage: int
    ) -> None:
        """Notify the target of an attack.

        For player targets: msg() directly.
        For building targets: msg() the owner.
        """
        attacker_name = getattr(attacker, "key", "Unknown")
        weapon_name = getattr(weapon_item, "key", str(weapon_item))

        if self._is_player(target):
            self.notify(target, "attacked", attacker_name=attacker_name,
                        weapon_name=weapon_name, damage=damage)
        elif self._is_building(target):
            owner = self._get_building_owner(target)
            if owner is not None:
                building_name = getattr(target, "key", "building")
                self.notify(owner, "building_attacked", building_name=building_name,
                            attacker_name=attacker_name, weapon_name=weapon_name,
                            damage=damage)

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

    def _award_agent_combat_xp(self, agent: Any) -> None:
        """Award combat-kill XP to an agent via the freeze-aware AgentSystem.

        No-op (guarded) when the agent system is unavailable, so combat
        resolution never breaks.
        """
        agent_system = self._get_agent_system()
        if agent_system is None:
            return
        try:
            agent_system.award_agent_xp(agent, "combat")
        except Exception:  # noqa: BLE001 - never let XP award break combat
            pass

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

    @staticmethod
    def _get_nearby_players(
        location: Any, radius: int
    ) -> list:
        """Get players near a location within a radius.

        This is a hook for the game to provide nearby player lookup.
        In tests, the location object can provide a ``get_nearby_players``
        method or a ``_nearby_players`` attribute.
        """
        if hasattr(location, "get_nearby_players"):
            return location.get_nearby_players(radius)
        if hasattr(location, "_nearby_players"):
            return location._nearby_players
        return []


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
