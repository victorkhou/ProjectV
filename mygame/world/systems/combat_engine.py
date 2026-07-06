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
    ) -> None:
        super().__init__(registry, event_bus)
        self._current_tick_func = current_tick_func or (lambda: 0)
        self.pending_actions: list[dict] = []

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
            3. Target is in range (Manhattan distance <= weapon range)
            4. If weapon has ammo_cost, attacker has sufficient resources
            5. Deduct ammo on queue (not on resolve)

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

        # 2b. Prevent attacking own buildings
        target_owner = self._get_building_owner(target)
        if target_owner is not None and target_owner is attacker:
            return False, "You cannot attack your own buildings."

        # 3. Range validation
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

        # 4. Ammo validation and deduction
        ammo_cost = self._get_ammo_cost(weapon_item)
        if ammo_cost:
            err = self._validate_ammo(attacker, ammo_cost)
            if err:
                return False, err
            # 5. Deduct ammo on queue
            attacker.deduct_resources(ammo_cost)

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
        lockout_ticks = self.registry.balance.combat_lockout_ticks

        for action in actions:
            attacker = action["attacker"]
            target = action["target"]
            weapon_item = action["weapon_item"]

            # Calculate damage
            damage = self._calculate_damage(attacker, target, weapon_item)

            # Apply damage
            self._apply_damage(target, damage, attacker)

            # Set combat lockout on attacker and target (if player)
            lockout_until = current_tick + lockout_ticks
            self._set_combat_lockout(attacker, lockout_until)
            if self._is_player(target):
                self._set_combat_lockout(target, lockout_until)

            # Publish combat_action event
            self.event_bus.publish(
                COMBAT_ACTION,
                attacker=attacker,
                target=target,
                item=weapon_item,
                damage=damage,
            )

            # Notify target
            self._notify_target(target, attacker, weapon_item, damage)

            # Check for defeat / destruction
            target_hp = self._get_hp(target)
            if target_hp <= 0:
                if self._is_player(target):
                    self._handle_player_defeat(target, attacker)
                elif self._is_building(target):
                    self._handle_building_destruction(target, attacker)

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
        self, attacker: Any, target: Any, weapon_item: Any
    ) -> int:
        """Calculate net damage for an attack.

        Damage = weapon_damage + tech/powerup modifiers - armor_reduction
        Minimum 0.

        Args:
            attacker: The attacking entity.
            target: The target entity.
            weapon_item: The weapon GameItem used.

        Returns:
            Net damage as an integer (minimum 0).
        """
        base_damage = self._get_stat(weapon_item, "damage", 0)

        # Tech/powerup modifiers from attacker (additive bonus)
        bonus = self._get_attacker_bonus(attacker)

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

        # Award XP to attacker.
        if self._is_agent(attacker):
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

        # Award XP to attacker (if it's a player)
        if self._is_player(attacker):
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
        message = (
            f"You were attacked by {attacker_name} with {weapon_name} "
            f"for {damage} damage."
        )

        if self._is_player(target):
            if hasattr(target, "msg"):
                target.msg(message)
        elif self._is_building(target):
            owner = self._get_building_owner(target)
            if owner is not None and hasattr(owner, "msg"):
                building_name = getattr(target, "key", "building")
                owner.msg(
                    f"Your {building_name} was attacked by {attacker_name} "
                    f"with {weapon_name} for {damage} damage."
                )

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

    def _get_attacker_bonus(self, attacker: Any) -> float:
        """Get tech/powerup damage bonus for the attacker."""
        # Tech/equipment bonuses are already folded into weapon damage; only
        # active powerups contribute an extra multiplier here.
        bonus = 0.0

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
    def _get_agent_system() -> Any | None:
        """Lazily resolve the AgentSystem from the global game_systems dict.

        Mirrors the lookup pattern used by ``agent_scripts._award_agent_xp`` so
        the CombatEngine stays decoupled from system construction/ordering. The
        lookup is guarded so combat never breaks if the agent system is
        unavailable (e.g. in tests or before initialization).
        """
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


class _TurretWeapon:
    """Synthetic weapon item for turret auto-attacks."""

    def __init__(self, damage: int, weapon_range: int) -> None:
        self.key = "Turret"
        self.stat_modifiers = {"damage": damage, "range": weapon_range}
        self.ammo_cost = None

    def get_stat(self, stat_name: str, default: float = 0) -> float:
        return float(self.stat_modifiers.get(stat_name, default))
