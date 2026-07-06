"""
Agent behavior-script lifecycle mixin.

Owns the attach/detach/resolve logic for the Evennia Scripts that drive agent
behavior (role scripts like ``HarvesterScript`` and gated ability scripts like
``DeliveryBehavior``). Split out of ``agent_system`` so that module stays
focused on orchestration; combined into ``AgentSystem`` via inheritance so
every method runs against the same ``self`` (a pure relocation).

"""

from __future__ import annotations

from typing import Any

from world.constants import DeliveryState
from world.systems.agent_constants import ABILITY_SCRIPT_KEYS, logger


class AgentBehaviorMixin:
    """Attach/detach/resolve behavior + gated-ability Scripts on agent NPCs.

    Depends on the host class providing ``self.evaluate_gated_abilities`` (from
    ``AgentProgressionMixin``) — satisfied by ``AgentSystem``.
    """

    def _attach_behavior_script(self, agent: Any, role: str) -> None:
        """Attach the Evennia base role Script(s) for *role*, then gate abilities.

        Uses ``ROLE_SCRIPT_MAP`` from ``agent_scripts`` to look up the correct
        base Script class (or list of classes) and adds each via Evennia's
        ``scripts.add``. When a role maps to a list, every script in the list is
        attached (list-handling path retained for any future multi-script role).

        After the base role script(s) are attached, ``evaluate_gated_abilities``
        runs unconditionally so gated abilities (e.g. ``DeliveryBehavior`` for
        harvesters) attach if and only if the agent's ``Effective_Level`` meets or
        exceeds the gate's required level AND the owning player has enabled that
        ability. For harvesters this means ``HarvesterScript`` always attaches and
        ``DeliveryBehavior`` attaches only via the gate evaluation, so the
        ``assign_agent`` reserve-restore/reassign path attaches delivery iff
        effective ≥ gate AND enabled.

        Silently no-ops in test environments where Evennia isn't available.
        """
        try:
            from typeclasses.agent_scripts import ROLE_SCRIPT_MAP

            value = ROLE_SCRIPT_MAP.get(role)

            if value is not None and hasattr(agent, "scripts"):
                # Normalise to a list so both single classes and lists are handled
                script_classes = value if isinstance(value, list) else [value]
                for script_cls in script_classes:
                    agent.scripts.add(script_cls)
        except Exception:
            pass

        # Regardless of role, converge gated abilities so delivery (and any
        # future gated ability) attaches only when effective level meets the
        # gate AND the player has enabled it. Defensive so a base-script attach
        # is never undone by a gate-evaluation failure.
        try:
            self.evaluate_gated_abilities(agent)
        except Exception:
            logger.exception(
                "evaluate_gated_abilities failed during _attach_behavior_script "
                "for agent %s",
                getattr(agent, "key", "?"),
            )

    @staticmethod
    def _detach_behavior_script(agent: Any) -> None:
        """Remove any agent behavior script(s) from the NPC.

        Removes all scripts whose key matches a known behavior/ability script.
        The key set is derived from the role/ability tables
        (``ALL_BEHAVIOR_SCRIPT_KEYS``) rather than hardcoded here, so it stays
        in sync automatically. Matching by key avoids instantiating Evennia
        Script classes outside the DB context (which silently fails).
        """
        try:
            if not hasattr(agent, "scripts"):
                return

            from typeclasses.agent_scripts import ALL_BEHAVIOR_SCRIPT_KEYS

            for script in list(agent.scripts.all()):
                if getattr(script, "key", "") in ALL_BEHAVIOR_SCRIPT_KEYS:
                    script.delete()
        except Exception:
            pass

    @staticmethod
    def resolve_ability_script(key: str) -> type | None:
        """Resolve a gated ability *key* to its Script class.

        Looks up ``ABILITY_SCRIPT_MAP`` from ``agent_scripts`` lazily so the
        system stays decoupled from Script construction and importable outside
        the Evennia DB context. Returns the Script class, or ``None`` when the
        key is unresolved or the import fails.
        """
        try:
            from typeclasses.agent_scripts import ABILITY_SCRIPT_MAP

            return ABILITY_SCRIPT_MAP.get(key)
        except Exception:
            return None

    @staticmethod
    def _ability_script_key(script_cls: type) -> str | None:
        """Return the Evennia ``key`` for a gated ability Script class.

        Script subclasses set ``key`` inside ``at_script_creation`` rather than
        as a class attribute, so we map by class name via ``ABILITY_SCRIPT_KEYS``
        to avoid instantiating the class outside the DB context. Falls back to a
        class-level ``key`` attribute if one is reliably present.
        """
        name = getattr(script_cls, "__name__", "")
        mapped = ABILITY_SCRIPT_KEYS.get(name)
        if mapped:
            return mapped
        key = getattr(script_cls, "key", "")
        return key or None

    def _attach_single_script(self, agent: Any, script_cls: type) -> None:
        """Idempotently attach a single gated ability *script_cls* to *agent*.

        Checks the agent's existing scripts by ``key`` before adding so a
        duplicate is never attached. When attaching ``DeliveryBehavior``,
        initializes ``delivery_state = DeliveryState.IDLE`` so the delivery FSM
        starts from a clean state.

        Silently no-ops in test environments where Evennia isn't available.
        """
        try:
            if script_cls is None:
                return
            if not hasattr(agent, "scripts"):
                return

            script_key = self._ability_script_key(script_cls)

            # Idempotency: don't add if a script with this key already exists.
            if script_key is not None:
                for script in list(agent.scripts.all()):
                    if getattr(script, "key", "") == script_key:
                        return

            agent.scripts.add(script_cls)

            # Initialize delivery FSM state when attaching DeliveryBehavior.
            if getattr(script_cls, "__name__", "") == "DeliveryBehavior":
                agent.db.delivery_state = DeliveryState.IDLE
        except Exception:
            pass

    @staticmethod
    def _detach_single_script(agent: Any, script_key: str) -> None:
        """Remove only the gated script whose key == *script_key*.

        Unlike ``_detach_behavior_script`` (which removes all behavior scripts on
        reassignment), this removes a single named ability script, leaving all
        other scripts — including ``HarvesterScript`` — attached. Used by gate
        re-lock and player disable.

        Silently no-ops in test environments where Evennia isn't available.
        """
        try:
            if not hasattr(agent, "scripts"):
                return

            for script in list(agent.scripts.all()):
                if getattr(script, "key", "") == script_key:
                    script.delete()
        except Exception:
            pass
