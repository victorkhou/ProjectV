"""
Agent progression mixin — owner-level cap, effective level, gated abilities,
freeze-aware XP/death, and the roster progression view.

Split out of ``agent_system`` to keep that module focused on roster/training/
assignment/tick orchestration. ``AgentProgressionMixin`` is combined into
``AgentSystem`` via inheritance, so every method here runs with the same
``self`` (``self.registry``, ``self.get_agents``, the behavior-script helpers,
etc.) — this is a pure relocation, not a behavior change.

"""

from __future__ import annotations

from typing import Any

from world.systems.agent_constants import AGENT_XP_SOURCE_FIELDS, logger


class AgentProgressionMixin:
    """Owner-cap math, gated-ability convergence, XP/death, roster view.

    Depends on the host class providing: ``self.registry`` (with ``balance``,
    ``ability_gates``, ``ranks``, ``get_ability_gates``, ``get_ability_gate``),
    ``self.get_agents`` / ``self.get_agent_by_id`` (roster queries), and the
    behavior-script helpers (``resolve_ability_script``, ``_attach_single_script``,
    ``_detach_single_script``, ``_ability_script_key``) — all satisfied by
    ``AgentSystem``.
    """

    # ------------------------------------------------------------------ #
    #  Owner-level cap
    # ------------------------------------------------------------------ #

    @classmethod
    def _level_from_player(cls, player: Any) -> int:
        """Resolve a player's Entity_Level using the shared level rule.

        Delegates to ``world.utils.get_player_level`` (the single source of
        truth), which prefers ``db.level``, falls back to ``db.rank_level``
        (1-12 rank number → first level of that rank), defaults to 1 when
        neither is set, and treats a non-numeric stored value as unset.
        """
        from world.utils import get_player_level
        return get_player_level(player, default=1)

    def get_owner_level(self, agent: Any) -> int:
        """Return the owning player's Entity_Level (default 1 when missing).

        Reuses the ``RankSystem._get_level`` legacy rule for the owner. When the
        agent has no ``db.owner`` (orphaned), defaults to 1 — the most
        conservative outcome (cap_ceiling 1, effective_level 1). Never raises.
        """
        owner = getattr(getattr(agent, "db", None), "owner", None)
        if owner is None:
            logger.debug(
                "Agent %s has no owner; defaulting owner_level to 1.",
                getattr(agent, "key", "?"),
            )
            return 1
        return self._level_from_player(owner)

    def get_cap_ceiling(self, agent: Any) -> int:
        """Return the agent's Cap_Ceiling = ``max(1, owner_level)``.

        The maximum Effective_Level the owner cap permits. Equal to the
        owner's level (early-game rebalance R3.1 — previously owner_level - 1,
        which froze a level-1 player's first agent at level 1 with no XP gain).
        Floors at 1 for an orphaned agent.
        """
        return max(1, self.get_owner_level(agent))

    @staticmethod
    def _raw_level(agent: Any) -> int:
        """Return the agent's owner-agnostic Raw_Level from its own Combat_XP.

        Prefers the ``CombatEntity.get_raw_level`` method; falls back to deriving
        it from ``db.combat_xp`` via the shared curve for bare agents (e.g. test
        fakes without the mixin). Single source of truth for raw-level derivation
        so ``compute_effective_level`` and ``get_agent_progression_view`` cannot
        drift apart.
        """
        from world.utils import _coerce_level
        if hasattr(agent, "get_raw_level"):
            raw = _coerce_level(agent.get_raw_level())
            if raw is not None:
                return raw
        from world import progression

        raw_xp = getattr(getattr(agent, "db", None), "combat_xp", 0) or 0
        try:
            return progression.level_for_xp(raw_xp)
        except (TypeError, ValueError):
            logger.debug("Non-numeric combat_xp %r; treating raw level as 1.", raw_xp)
            return 1

    def compute_effective_level(self, agent: Any) -> int:
        """Return the agent's Effective_Level under the owner-level cap.

        ``max(1, min(Raw_Level, Cap_Ceiling))`` where Cap_Ceiling ==
        ``max(1, owner_level)`` (early-game rebalance R3.1 — the cap equals the
        owner's level so a level-1 player's first agent can grow). The
        Raw_Level is derived owner-agnostically from the agent's own Combat_XP
        via ``agent.get_raw_level()``. Handles the owner-demotion edge case
        where a stored raw level can exceed the new ceiling, and re-derives on
        XP/owner changes. Delegates the ceiling to :meth:`get_cap_ceiling` so
        the two can never disagree.
        """
        return max(1, min(self._raw_level(agent), self.get_cap_ceiling(agent)))

    # ------------------------------------------------------------------ #
    #  Ability-status classification  (single source of truth)
    # ------------------------------------------------------------------ #
    #
    # One classifier decides an ability's state; the roster wire encoding
    # (``get_agent_progression_view``), the roster's human rendering
    # (``agent_commands.sub_list``), and the ``agent ability`` status command
    # (``get_ability_status``) all derive from it, so the three renderings can
    # never diverge.

    @staticmethod
    def _classify_ability(
        effective: int, required: int, is_enabled: bool
    ) -> tuple[str, int]:
        """Return ``(state, required_level)`` for one gate.

        ``state`` is one of ``"enabled"`` / ``"available"`` / ``"locked"``.
        ``required_level`` is echoed back so callers can render "locked" with
        the threshold without re-reading the gate.
        """
        if is_enabled:
            return "enabled", required
        if effective >= required:
            return "available", required
        return "locked", required

    @classmethod
    def _encode_ability_status(
        cls, effective: int, required: int, is_enabled: bool
    ) -> str:
        """Encode a gate's status for the roster view's ``ability_status`` map.

        Wire encoding consumed by ``agent_commands.sub_list``:
        ``"enabled"`` / ``"available"`` / ``"locked:N"`` (N = required level).
        Kept stable because the roster decodes it via ``decode_ability_status``.
        """
        state, req = cls._classify_ability(effective, required, is_enabled)
        return f"locked:{req}" if state == "locked" else state

    @staticmethod
    def decode_ability_status(encoded: str) -> tuple[str, str]:
        """Decode an ``ability_status`` value into ``(state, readable)``.

        Inverse of ``_encode_ability_status``, so the roster command never
        hand-parses the wire format. Returns the bare ``state``
        (``"enabled"`` / ``"available"`` / ``"locked"`` / other) and a
        human-readable rendering (``"locked LvN"`` for the locked encoding).
        """
        if isinstance(encoded, str) and encoded.startswith("locked:"):
            return "locked", f"locked Lv{encoded.split(':', 1)[1]}"
        return encoded, encoded

    # ------------------------------------------------------------------ #
    #  Enabled-ability state
    # ------------------------------------------------------------------ #

    def get_enabled_abilities(self, agent: Any) -> set:
        """Return the agent's stored set of enabled gated-ability keys.

        Reads ``agent.db.enabled_abilities`` (a persisted list); absent or
        ``None`` → empty set (legacy default). The set is sticky and
        independent of attach state — it reflects what the player has explicitly
        enabled, not what is currently active.
        """
        keys = getattr(getattr(agent, "db", None), "enabled_abilities", None)
        if not keys:
            return set()
        return set(keys)

    def _set_enabled_abilities(self, agent: Any, keys) -> None:
        """Persist the enabled-ability set back to ``agent.db.enabled_abilities``.

        Stored as a list for Evennia attribute persistence.
        """
        agent.db.enabled_abilities = list(keys)

    # ------------------------------------------------------------------ #
    #  Gate evaluation
    # ------------------------------------------------------------------ #

    def evaluate_gated_abilities(self, agent: Any, notify: bool = True) -> None:
        """Converge an agent's gated behavior scripts to its current state.

        For each ``Ability_Gate`` in the registry, attaches or detaches the
        gate's behavior script so that it is present *if and only if* the agent's
        ``Effective_Level`` meets or exceeds the gate's required level AND the
        owning player has enabled that ability for the agent.

        Per-gate branch logic (mirrors the design pseudocode):

        - ``want and not attached`` → attach + init delivery state + notify the
          owner the ability is now active.
        - ``attached and not want`` → detach the script. Notify re-lock ONLY when
          the loss was caused by a level drop (``not available``); a detach
          caused purely by the player disabling a still-available ability is
          silent here (the disable command confirms it).
        - ``available and not enabled and not attached`` → mark the ability
          available and notify the owner how to enable it, once per
          availability window.
        - otherwise → no-op.

        Unresolved ability keys are skipped with a single warning so a missing
        script never blocks evaluation of the remaining gates. The
        method is idempotent: repeated calls leave at most one instance of each
        script attached.
        """
        effective = self.compute_effective_level(agent)
        enabled = self.get_enabled_abilities(agent)
        notified = self._get_notified_available(agent)
        notified_changed = False

        for gate in self.registry.get_ability_gates():
            key = gate.key
            required = gate.required_level
            available = effective >= required
            is_enabled = key in enabled

            script_cls = self.resolve_ability_script(key)
            if script_cls is None:
                # Unresolved key — skip attachment, log once, keep evaluating.
                logger.warning("Unresolved ability gate key: %s", key)
                continue

            script_key = self._ability_script_key(script_cls)
            attached = self._has_ability_script(agent, script_key)
            want = available and is_enabled

            if want and not attached:
                # Two conditions met and not yet attached → attach + activate.
                self._attach_single_script(agent, script_cls)
                if key in notified:
                    notified.discard(key)
                    notified_changed = True
                self._notify_owner(
                    agent, notify, "ability_active",
                    key=key, agent_id=self._agent_id(agent),
                )
            elif attached and not want:
                # Attached but no longer wanted → detach. Re-lock notification
                # only when the cause is a level drop (ability still wanted by
                # the player, i.e. enabled, but no longer available).
                self._detach_single_script(agent, script_key)
                if not available:
                    # The availability window has closed: clear any stale
                    # "available" flag so a future re-cross into the
                    # available-but-not-enabled state notifies again. Mirrors
                    # the cleanup in the no-op else branch.
                    if key in notified:
                        notified.discard(key)
                        notified_changed = True
                    self._notify_owner(
                        agent, notify, "ability_relocked",
                        key=key, agent_id=self._agent_id(agent), required=required,
                    )
            elif available and not is_enabled and not attached:
                # Unlocked but not enabled → offer it to the player once.
                if key not in notified:
                    notified.add(key)
                    notified_changed = True
                    self._notify_owner(
                        agent, notify, "ability_available",
                        key=key, agent_id=self._agent_id(agent),
                    )
            else:
                # No-op. If the gate is no longer available, clear any stale
                # "available" notification so a future re-cross notifies again.
                if not available and key in notified:
                    notified.discard(key)
                    notified_changed = True

        if notified_changed:
            self._set_notified_available(agent, notified)

    # -- gate-evaluation helpers --------------------------------------- #

    @staticmethod
    def _agent_id(agent: Any) -> Any:
        """Return the agent's display id (``db.agent_id``), or '?' when absent."""
        return getattr(getattr(agent, "db", None), "agent_id", None) or "?"

    @staticmethod
    def _has_ability_script(agent: Any, script_key: str | None) -> bool:
        """Return True if a script with ``script_key`` is attached to *agent*.

        Scans ``agent.scripts`` by key. Guards ``hasattr`` so it is safe in test
        environments and on agents without a script manager (returns False).
        """
        if script_key is None:
            return False
        if not hasattr(agent, "scripts"):
            return False
        try:
            for script in agent.scripts.all():
                if getattr(script, "key", "") == script_key:
                    return True
        except Exception:
            return False
        return False

    @staticmethod
    def _get_notified_available(agent: Any) -> set:
        """Return the per-agent set of ability keys already offered to the owner.

        Read from ``agent.db.notified_available_abilities`` (a persisted list);
        absent or ``None`` → empty set. Used to send the "available, enable with"
        notification at most once per availability window.
        """
        keys = getattr(
            getattr(agent, "db", None), "notified_available_abilities", None
        )
        if not keys:
            return set()
        return set(keys)

    @staticmethod
    def _set_notified_available(agent: Any, keys) -> None:
        """Persist the notified-available set as a list for Evennia attributes."""
        agent.db.notified_available_abilities = list(keys)

    def _notify_owner(self, agent: Any, notify: bool, kind: str, **data: Any) -> None:
        """Emit a *kind* notification to the agent's owning player.

        No-ops when ``notify`` is False or the agent has no ``db.owner``. The
        message text is composed by the NotificationPresenter from *kind* +
        *data*, not here — the domain only supplies structured values.
        """
        if not notify:
            return
        owner = getattr(getattr(agent, "db", None), "owner", None)
        if owner is None:
            return
        self.notify(owner, kind, **data)

    # ------------------------------------------------------------------ #
    #  Ability enable / disable / status command backends
    #
    # ------------------------------------------------------------------ #

    def enable_ability(self, player: Any, agent_id: Any, key: str) -> str:
        """Enable a gated ability *key* for the owner's *agent_id*.

        Validates ownership (unknown agent → reject) and that *key* is
        a known ability gate (unknown key → reject). When the agent's
        ``Effective_Level`` meets or exceeds the gate's required level, records
        the key in the enabled set, attaches the gate's behavior script (which
        initializes its delivery state), and confirms. When
        below the gate, rejects with the required level and neither records the
        key nor attaches the script. Generic across keys.

        Returns a human-readable string for the command layer to ``msg()``.
        """
        agent = self.get_agent_by_id(player, agent_id)
        if agent is None:
            return f"Agent #{agent_id} not found."

        if key not in self.registry.ability_gates:
            return f"Unknown ability '{key}'."

        gate = self.registry.get_ability_gate(key)
        effective = self.compute_effective_level(agent)

        if effective < gate.required_level:
            return (
                f"Agent #{agent_id} cannot enable '{key}' yet — requires "
                f"level {gate.required_level} (currently level {effective})."
            )

        # Record the key in the enabled set (sticky).
        enabled = self.get_enabled_abilities(agent)
        enabled.add(key)
        self._set_enabled_abilities(agent, enabled)

        # Attach the gate's behavior script (inits delivery state).
        self._attach_single_script(agent, self.resolve_ability_script(key))

        # Enabling attaches the script directly (bypassing the
        # available-but-not-enabled branch of evaluate_gated_abilities), so
        # clear any stale "available" notification flag here too. Otherwise a
        # later detach + re-cross would find the flag set and skip the
        # legitimate re-notification.
        notified = self._get_notified_available(agent)
        if key in notified:
            notified.discard(key)
            self._set_notified_available(agent, notified)

        return f"Ability '{key}' enabled for Agent #{agent_id}."

    def disable_ability(self, player: Any, agent_id: Any, key: str) -> str:
        """Disable a gated ability *key* for the owner's *agent_id*.

        Validates ownership (unknown agent → reject) and that *key* is
        a known ability gate (unknown key → reject). Clears the key
        from the enabled set so it does not auto-re-attach and detaches
        only that ability's behavior script via ``_detach_single_script`` —
        ``HarvesterScript`` and any other scripts stay attached.

        Returns a human-readable string for the command layer to ``msg()``.
        """
        agent = self.get_agent_by_id(player, agent_id)
        if agent is None:
            return f"Agent #{agent_id} not found."

        if key not in self.registry.ability_gates:
            return f"Unknown ability '{key}'."

        # Clear the enabled flag.
        enabled = self.get_enabled_abilities(agent)
        enabled.discard(key)
        self._set_enabled_abilities(agent, enabled)

        # Detach only this ability's script, leaving HarvesterScript et al.
        script_cls = self.resolve_ability_script(key)
        if script_cls is not None:
            self._detach_single_script(
                agent, self._ability_script_key(script_cls)
            )

        return f"Ability '{key}' disabled for Agent #{agent_id}."

    def get_ability_status(self, player: Any, agent_id: Any) -> str:
        """Return a per-ability status summary for the owner's *agent_id*.

        Validates ownership (unknown agent → reject). For each gate in
        the registry, reports one of:

        - ``locked (Lv N)`` when the agent's ``Effective_Level`` is below the
          gate's required level N;
        - ``available`` when the effective level meets/exceeds the gate but the
          key is not enabled;
        - ``enabled`` when the key is in the agent's enabled set.

        Generic across all gate keys. Returns a readable multi-line
        string for the command layer to ``msg()``.
        """
        agent = self.get_agent_by_id(player, agent_id)
        if agent is None:
            return f"Agent #{agent_id} not found."

        effective = self.compute_effective_level(agent)
        enabled = self.get_enabled_abilities(agent)

        gates = self.registry.get_ability_gates()
        if not gates:
            return f"Agent #{agent_id} has no gated abilities."

        lines = [f"Agent #{agent_id} abilities (level {effective}):"]
        for gate in gates:
            state, required = self._classify_ability(
                effective, gate.required_level, gate.key in enabled
            )
            label = f"locked (Lv {required})" if state == "locked" else state
            lines.append(f"  {gate.key}: {label}")
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    #  Roster progression view
    # ------------------------------------------------------------------ #

    def _rank_name_for_level(self, level: int) -> str:
        """Return the cosmetic rank name for a (effective) *level*.

        Mirrors how ``RankSystem`` derives a rank name from a level: map the
        level to a rank number via ``rank_from_level`` then find the registry
        ``RankDef`` whose ``.level`` equals that rank number, returning its
        ``.name`` with underscores normalized to spaces. Falls back to a
        generic ``Rank N`` when no matching ``RankDef`` is loaded so the view
        never raises.
        """
        from world.systems.rank_system import rank_from_level

        rank_num = rank_from_level(int(level))
        getter = getattr(self.registry, "get_rank_by_level", None)
        rank = getter(rank_num) if callable(getter) else None
        if rank is not None:
            return str(rank.name).replace("_", " ")
        return f"Rank {rank_num}"

    def get_agent_progression_view(self, agent: Any) -> dict:
        """Return a roster-display view of an agent's capped progression.

        Computes everything on demand from the agent's own Combat_XP and the
        owner-level cap, so the view can never go stale:

        - ``effective_level``: ``compute_effective_level(agent)`` — the
          owner-capped level.
        - ``rank_name``: the cosmetic rank name derived from the *effective*
          level, not the raw level, so a capped agent shows its capped rank.
        - ``ability_status``: a map of each registry gate key to its status —
          ``'enabled'`` when the key is in the agent's enabled set,
          ``'available'`` when ``effective_level >= required_level`` but not
          enabled, else ``'locked:N'`` with ``N`` the gate's required level.
        - ``capped_by_commander``: ``True`` iff the agent's Raw_Level exceeds
          its Effective_Level, i.e. the owner cap is actively suppressing it.

        Generic across all gate keys (no delivery-specific behavior).
        """
        effective = self.compute_effective_level(agent)
        raw_level = self._raw_level(agent)

        enabled = self.get_enabled_abilities(agent)
        ability_status: dict[str, str] = {}
        for gate in self.registry.get_ability_gates():
            ability_status[gate.key] = self._encode_ability_status(
                effective, gate.required_level, gate.key in enabled
            )

        return {
            "effective_level": effective,
            "rank_name": self._rank_name_for_level(effective),
            "ability_status": ability_status,
            "capped_by_commander": raw_level > effective,
        }

    # ------------------------------------------------------------------ #
    #  Freeze-aware XP award / death loss
    # ------------------------------------------------------------------ #

    def _reevaluate_agent(self, agent: Any) -> None:
        """Re-evaluate gated abilities after an XP change, if available.

        Guards the call so an XP award is never lost to a re-evaluation failure.
        """
        evaluate = getattr(self, "evaluate_gated_abilities", None)
        if evaluate is None:
            return
        try:
            evaluate(agent)
        except Exception:
            logger.exception(
                "evaluate_gated_abilities failed for agent %s",
                getattr(agent, "key", "?"),
            )

    def on_owner_level_changed(
        self, player: Any, old_level: Any = None, new_level: Any = None
    ) -> None:
        """Re-evaluate every owned Agent when the owning Player's level changes.

        Subscribed to the ``LEVEL_CHANGED`` event (payload ``player``,
        ``old_level``, ``new_level``); the level arguments are accepted to match
        that payload but are not needed here because each Agent's ``Cap_Ceiling``
        is recomputed from the owner's current level inside
        ``evaluate_gated_abilities``.

        For each Agent owned by *player*, recomputes ``Cap_Ceiling`` /
        ``Effective_Level`` and calls ``evaluate_gated_abilities``, which applies
        the per-gate convergence:

        - a level rise that crosses a gate marks the ability available and
          notifies the owner (no attach) unless the ability is already enabled,
          in which case it attaches the script and notifies it is active;
        - a level drop below a gate detaches the script, retains the Agent's
          enabled flag, and notifies a re-lock.

        Each Agent is evaluated inside its own ``try``/``except`` so one bad
        Agent never halts re-evaluation of the rest of the roster.
        """
        for agent in self.get_agents(player):
            try:
                self.evaluate_gated_abilities(agent)
            except Exception:
                logger.exception(
                    "on_owner_level_changed: evaluate_gated_abilities failed "
                    "for agent %s",
                    getattr(agent, "key", "?"),
                )

    def award_agent_xp(self, agent: Any, source: str) -> bool:
        """FREEZE-AWARE Combat-XP award to *agent* for an earning *source*.

        Computes the effective level and cap ceiling FIRST. WHILE the agent's
        level has reached its ``Cap_Ceiling``, no XP is awarded — gain is frozen
        at the ceiling and no surplus accumulates. Otherwise the
        amount is looked up from ``registry.balance`` by *source* key, awarded
        via ``agent.award_xp`` (a zero/unknown amount is a no-op), and the
        agent's effective level + gated abilities are re-evaluated. When the
        owner later raises the ceiling, awards resume on the next earning event.

        Returns ``True`` iff an award actually happened (and therefore gated
        abilities were re-evaluated), so callers like ``_process_agent_tick``
        can avoid a redundant second ``evaluate_gated_abilities`` pass.
        """
        # FREEZE check first — compute cap ceiling and compare against the
        # agent's raw level. No banking when at/above the ceiling.
        cap_ceiling = self.get_cap_ceiling(agent)
        current_level = getattr(getattr(agent, "db", None), "level", None)
        if current_level is None:
            current_level = self.compute_effective_level(agent)
        if int(current_level) >= cap_ceiling:
            return False

        # Look up the data-driven amount for this source (unknown → no-op).
        field = AGENT_XP_SOURCE_FIELDS.get(source)
        if field is None:
            return False
        amount = getattr(self.registry.balance, field, 0) or 0
        if amount <= 0:
            # Zero amount → no-op. Nothing changed; skip re-eval.
            return False

        agent.award_xp(amount)

        # Re-derive effective level + gated abilities after the change.
        self._reevaluate_agent(agent)
        return True

    def apply_agent_death_loss(self, agent: Any) -> None:
        """Apply the configured death-loss XP penalty to *agent*.

        Deducts ``balance.agent_xp_death_loss`` via ``agent.deduct_xp`` (floored
        at 0 by ``CombatEntity``), then re-derives the effective level and gated
        abilities. Death loss is NEVER frozen — it only
        reduces XP, never adds past the ceiling.
        """
        amount = getattr(self.registry.balance, "agent_xp_death_loss", 0) or 0
        if amount > 0:
            agent.deduct_xp(amount)
        self._reevaluate_agent(agent)

    def handle_demotion(self, player: Any, new_agent_cap: int) -> None:
        """Reserve highest-ID agents that exceed the new cap.

        new_agent_cap includes the commander slot, so agent-only max = cap - 1.
        """
        agents = self.get_agents(player)
        agents.sort(key=lambda a: getattr(a.db, "agent_id", 0), reverse=True)

        max_agents = new_agent_cap - 1
        excess = len(agents) - max_agents
        if excess <= 0:
            return

        from world.utils import resting_activity_status
        for agent in agents:
            if excess <= 0:
                break
            if not getattr(agent.db, "reserve", False):
                agent.db.reserve = True
                # Re-derive the resting status so a benched agent's Activity line
                # reads "Reserve" instead of freezing on its pre-demotion string
                # (a reserved agent's per-tick scripts are skipped, so nothing
                # else refreshes it).
                agent.db.activity_status = resting_activity_status(agent)
                excess -= 1

    def handle_promotion(self, player: Any, new_agent_cap: int) -> None:
        """Restore reserved agents up to the new cap (lowest IDs first).

        new_agent_cap includes the commander slot, so agent-only max = cap - 1.
        """
        agents = self.get_agents(player)
        agents.sort(key=lambda a: getattr(a.db, "agent_id", 0))

        max_agents = new_agent_cap - 1
        reserved = [a for a in agents if getattr(a.db, "reserve", False)]
        active = len(agents) - len(reserved)
        slots_available = max_agents - active

        from world.utils import resting_activity_status
        for agent in reserved:
            if slots_available <= 0:
                break
            agent.db.reserve = False
            # Re-derive now that it's un-benched (a role-at-building agent reads
            # "Working" again; its per-tick script refines transient statuses).
            agent.db.activity_status = resting_activity_status(agent)
            slots_available -= 1
