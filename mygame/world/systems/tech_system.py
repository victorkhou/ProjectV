"""
Tech Lab System for the RTS Combat Overworld game.

Manages technology research: listing available techs by rank, starting
research with resource deduction, tick-based timer countdown, and
applying completed technology effects.

"""

from __future__ import annotations

import logging
from typing import Any

from world.constants import BRANCH_DOCTRINE, BRANCH_OPERATION_KIND, BRANCHES
from world.data_registry import DataRegistry
from world.definitions import TechnologyDef
from world.event_bus import TECHNOLOGY_RESEARCHED, EventBus
from world.systems.base_system import BaseSystem

logger = logging.getLogger("mygame.tech_system")

#: Fallback for ``balance.branch_reinstatement_cost_fraction`` (R5.6) when the
#: injected registry carries no readable ``BalanceConfig`` — the same value the
#: config declares as its default, so an unconfigured fixture prices a
#: Reinstatement job exactly as a configured deployment does.
DEFAULT_REINSTATEMENT_COST_FRACTION = 0.5


class TechLabSystem(BaseSystem):
    """Manages technology research at Tech Labs.

    Research timers are stored on the Tech_Lab building as
    ``building.db.research_timers`` (dict of tech_key -> {ticks_remaining, player}).

    Args:
        registry: The DataRegistry holding technology/rank definitions.
        event_bus: The EventBus for publishing game events.
    """

    def __init__(self, registry: DataRegistry, event_bus: EventBus) -> None:
        super().__init__(registry, event_bus)
        # Track active research: list of
        # {tech_key, player, ticks_remaining, tech_lab, reinstatement}.
        # ``reinstatement`` marks a reduced-cost Reinstatement job (R5.6); an
        # entry is otherwise identical either way, which is what makes the two
        # share one countdown and one completion path.
        self._active_research: list[dict] = []
        # The Branch_System, injected at the composition root via
        # set_branch_resolver. None until then, and None in every fixture that
        # predates the Branch feature — which is why each consumer keeps its
        # pre-feature fallback rather than assuming a resolver is there.
        self._branch: Any = None

    # ------------------------------------------------------------------ #
    #  Branch resolver injection
    # ------------------------------------------------------------------ #

    def set_branch_resolver(self, resolver: Any) -> None:
        """Inject the Branch_System that owns Branch_Commitment.

        Called once at the composition root. The tech system asks the resolver
        which Branch is live for a player rather than deriving it itself, so
        commitment has exactly one implementation.

        Pass ``None`` to unwire it, which restores the pre-feature behavior:
        the tree gate derives the owned lab's tree locally and no dormancy
        filter applies.
        """
        self._branch = resolver

    # ------------------------------------------------------------------ #
    #  List available technologies
    # ------------------------------------------------------------------ #

    def list_available(self, player: Any) -> list[TechnologyDef]:
        """Return technologies available to research right now.

        A tech is available when the player's rank meets its ``required_rank``,
        it is not already researched, AND it belongs to the tree hosted by the
        research lab the player OWNS on their current planet. With no research
        lab the list is empty — there is nothing they can research until they
        build one and thereby pick a tree.

        Args:
            player: The player to query.

        Returns:
            List of TechnologyDef objects available for research.
        """
        tree = self.owned_research_tree(player)
        if tree is None:
            return []

        rank_level = self._get_player_level(player)
        from world.systems.rank_system import rank_from_level
        rank_num = rank_from_level(rank_level)
        all_techs = self.registry.get_technologies_for_rank(rank_num)

        researched = self._get_researched_techs(player)
        return [
            t for t in all_techs
            if t.key not in researched and t.tree == tree
        ]

    def owned_research_tree(self, player: Any) -> str | None:
        """Return the tech tree of the research lab *player* owns, or ``None``.

        A thin forwarder to the injected Branch resolver's
        ``commitment(player)`` — the same derivation, in its proper owner, so
        the tree gate and the Branch_Commitment can never disagree. The name
        stays because the research commands and the tech view already call it.

        The body below is the **pre-feature derivation**, kept as the fallback
        for when no resolver is wired (a minimal test fixture, or a deployment
        where the Branch system is not installed): it resolves the player's
        ``research_lab`` building on their current planet
        (:func:`world.utils.owner_research_lab`) and reads its ``research_tree``
        from the building's definition. Passing ``self.registry`` as the
        capability/definition provider keeps the lookup hermetic in tests.

        Returns:
            The hosted tree, or ``None`` when the player owns no research lab on
            this planet (so they can research nothing yet) or when the lab's
            type can't be resolved.
        """
        resolver = self._branch
        if resolver is not None:
            derive = getattr(resolver, "commitment", None)
            if callable(derive):
                try:
                    return derive(player)
                except Exception:  # noqa: BLE001 - fall back, never raise out
                    logger.exception(
                        "Branch resolver failed to derive a commitment; "
                        "falling back to the local lab derivation."
                    )

        from world.utils import owner_research_lab, get_building_type

        planet = getattr(getattr(player, "db", None), "coord_planet", None)
        lab = owner_research_lab(player, planet=planet, provider=self.registry)
        if lab is None:
            return None
        btype = get_building_type(lab)
        if not btype:
            return None
        try:
            bdef = self.registry.get_building(btype)
        except (KeyError, AttributeError):
            return None
        return getattr(bdef, "research_tree", None)

    # ------------------------------------------------------------------ #
    #  The technology view (R13.1, R13.2, R13.5)
    # ------------------------------------------------------------------ #

    def report_technology_view(self, player: Any) -> dict:
        """Publish the technology view *player* requested, and return its data.

        The one place the technology view is assembled. Everything a player
        needs to judge their doctrine commitment before and after making it,
        as **structured data only** — every word the player reads is composed by
        the ``technology_view`` formatter in the NotificationPresenter, never
        here (R13.5).

        What the view reports:

        * the Branch_Commitment on the planet the player occupies, as the Branch
          key and its player-facing doctrine name, or ``None`` for a player who
          owns no Branch_Lab there (R13.1);
        * that Branch's Signature_Vector, as its Operation_Kind identifier
          (R13.1);
        * that Branch's researched technologies and the ones still available to
          research — both already scoped to the commitment, because the record
          is filtered here and ``list_available`` gates on the same owned lab
          (R13.1);
        * every Branch in Branch_Dormancy for the player, with the count of
          technologies recorded in it, plus the Reinstatement cost fraction that
          prices bringing any of them back (R13.2).

        The view **reads**: it recomputes nothing, writes nothing, and leaves the
        researched record untouched, so asking for it can never change a bonus.

        Args:
            player: The player who asked. The planet is the one they occupy —
                a commitment is per-planet, and the view describes where the
                player is standing (R13.1).

        Returns:
            The published payload, so a caller can read the same structured data
            the presenter renders without re-deriving it. Every key is always
            present; an absent answer is ``None``, ``0``, or an empty list.
        """
        planet = self._occupied_planet(player)
        branch = self.owned_research_tree(player)
        technologies = getattr(self.registry, "technologies", None) or {}
        recorded = self._get_researched_techs(player)
        # Only the committed Branch's record is the *view's* researched list
        # (R13.1); what sits in the other Branches is reported as dormancy
        # counts below rather than dropped (R13.2).
        researched = sorted(
            key for key in recorded
            if getattr(technologies.get(key), "tree", None) == branch
        ) if branch is not None else []
        dormant = self._dormant_counts(player, planet, branch)
        view = {
            "planet": planet,
            "branch": branch,
            "doctrine": BRANCH_DOCTRINE.get(branch),
            "operation_kind": BRANCH_OPERATION_KIND.get(branch),
            "researched": [
                {"key": key, "name": getattr(technologies.get(key), "name", None)}
                for key in researched
            ],
            "available": [
                {"key": tdef.key, "name": getattr(tdef, "name", None)}
                for tdef in self.list_available(player)
            ],
            # The committed Branch's keys still awaiting their reduced-cost
            # Reinstatement job: recorded, inert, and re-researchable (R5.7).
            "reinstatement_pending": [
                key for key in researched
                if self._awaiting_reinstatement(player, key)
            ],
            "reinstatement_fraction": self._reinstatement_fraction(),
            "dormant": [
                {
                    "branch": dormant_branch,
                    "doctrine": BRANCH_DOCTRINE.get(dormant_branch),
                    "count": count,
                }
                for dormant_branch, count in dormant.items()
            ],
            "dormant_count": sum(dormant.values()),
        }
        self.notify(player, "technology_view", **view)
        return view

    def _dormant_counts(
        self, player: Any, planet: Any, branch: str | None
    ) -> dict[str, int]:
        """Return ``{branch: recorded count}`` for each dormant Branch (R13.2).

        The Branch system owns what dormancy means, so the answer is asked of the
        injected resolver — the same defensive shape every other resolver call
        here uses.

        The fallback, for an unwired resolver (a minimal fixture, a deployment
        without the Branch system) or one that cannot answer, groups the record
        locally by tree and drops the live one. Both paths report the same
        figures for the same record; the resolver is preferred because it owns
        the definition of "not committed here". Falling back rather than
        answering empty is deliberate: an empty answer would silently hide a
        player's research in the trees they are not running, which is the one
        thing this half of the view exists to show.

        Canonical Branch order, and only Branches the player has a record in, so
        the view is deterministic and quotes no zeroes.
        """
        resolver = self._branch
        query = getattr(resolver, "dormant_branches", None) if resolver else None
        if callable(query):
            try:
                return self._ordered_counts(query(player, planet))
            except Exception:  # noqa: BLE001 - fall back, never raise out
                logger.exception(
                    "Branch resolver failed to report dormant Branches; "
                    "grouping the researched record locally instead."
                )
        technologies = getattr(self.registry, "technologies", None) or {}
        counts: dict[str, int] = {}
        for key in self._get_researched_techs(player):
            tree = getattr(technologies.get(key), "tree", None)
            if tree is None or tree == branch:
                continue
            counts[tree] = counts.get(tree, 0) + 1
        return self._ordered_counts(counts)

    @staticmethod
    def _ordered_counts(raw: Any) -> dict[str, int]:
        """Return *raw* as ``{branch: count}`` in canonical Branch order.

        The normalizer both :meth:`_dormant_counts` paths pass through, so a
        resolver's answer and the local grouping project identically: known
        Branches only, in :data:`~world.constants.BRANCHES` order, and a count
        that cannot be read as a whole number dropped rather than rendered.
        """
        if not raw:
            return {}
        ordered: dict[str, int] = {}
        for branch in BRANCHES:
            try:
                count = raw.get(branch)
            except (AttributeError, TypeError):
                return {}
            if not count:
                continue
            try:
                ordered[branch] = int(count)
            except (TypeError, ValueError):
                continue
        return ordered

    @staticmethod
    def _occupied_planet(player: Any) -> Any:
        """Return the planet *player* is standing on, or ``None``.

        The same ``db.coord_planet`` read :meth:`owned_research_tree` uses, so
        the view's commitment and its dormancy counts are scoped to one planet
        (R13.1).
        """
        return getattr(getattr(player, "db", None), "coord_planet", None)

    # ------------------------------------------------------------------ #
    #  Start research
    # ------------------------------------------------------------------ #

    def start_research(
        self, player: Any, tech_key: str, tech_lab: Any = None
    ) -> tuple[bool, str]:
        """Start researching a technology.

        Validation:
            1. Tech key exists in registry
            2. Player rank meets required_rank
            3. Tech not already researched — **unless** it is awaiting its
               Reinstatement job, which is what re-researching it is (R5.7)
            4. Player has sufficient resources

        On success:
            - Deduct resources
            - Add to active research queue

        A **Reinstatement job** is an ordinary entry in that queue carrying a
        ``reinstatement: True`` marker, so it shares the tick countdown, the
        completion publish, and every gate above with a first-time job — the
        rank gate included, unchanged (R5.8). Only two things differ: the
        resource cost per line and the duration are scaled by
        ``balance.branch_reinstatement_cost_fraction`` (R5.6), and completing it
        clears the key from the pending set rather than adding it to a record
        that already holds it.

        Returns:
            (success, message) tuple.
        """
        # 1. Look up technology definition
        tdef = self.registry.technologies.get(tech_key)
        if tdef is None:
            return False, f"Unknown technology: {tech_key}"

        # 1b. Research-lab gate (research-lab-trees). Research is gated on
        # OWNERSHIP: the player must own a research lab on their planet, and its
        # tree must match this tech's tree. Runs before rank/resource so a
        # wrong-lab attempt reads clearly ("your lab researches X, not Y")
        # rather than a misleading rank/cost error.
        owned_tree = self.owned_research_tree(player)
        if owned_tree is None:
            return False, (
                f"You need a research lab to research {tdef.name}. Build the "
                f"lab that hosts the '{tdef.tree}' tree."
            )
        if tdef.tree != owned_tree:
            return False, (
                f"{tdef.name} belongs to the '{tdef.tree}' tree, but your "
                f"research lab hosts the '{owned_tree}' tree. Each planet has "
                f"one lab (one tree) — demolish it to switch."
            )

        # 2. Rank check — compare player's derived rank against required rank
        player_level = self._get_player_level(player)
        from world.systems.rank_system import player_meets_rank
        if not player_meets_rank(player_level, tdef.required_rank, self.registry):
            return False, (
                f"Requires rank {tdef.required_rank} "
                f"(you are level {player_level})."
            )

        # 3. Already researched check — the one branch Reinstatement needs
        # (R5.7). A recorded key that sits in the Branch's pending set is
        # *reinstatable*, not done: the record was retained through dormancy
        # (R5.3), so "already researched" is the wrong refusal for it and
        # re-researching it at the reduced cost is exactly what R5.5 asks for.
        researched = self._get_researched_techs(player)
        reinstatement = False
        if tech_key in researched:
            if not self._awaiting_reinstatement(player, tech_key):
                return False, f"Technology {tech_key} is already researched."
            reinstatement = True

        # 4. Already in progress check
        for entry in self._active_research:
            if entry["tech_key"] == tech_key and entry["player"] is player:
                return False, f"Technology {tech_key} is already being researched."

        # 5. Resource check and deduction, at the scaled price for a
        # Reinstatement job and at the defined one otherwise (R5.6).
        cost = tdef.resource_cost
        ticks = tdef.research_ticks
        if reinstatement:
            fraction = self._reinstatement_fraction()
            cost = self._scaled_cost(tdef.resource_cost, fraction)
            ticks = self._scaled_ticks(tdef.research_ticks, fraction)
        if cost:
            if not player.has_resources(cost):
                # Use the shared have/need breakdown so this reads identically to
                # building construction/upgrade and agent training.
                from world.utils import format_insufficient_resources
                return False, format_insufficient_resources(player, cost)
            player.deduct_resources(cost)

        # Add to active research. The ``reinstatement`` marker is read by the
        # completion path alone — the countdown never looks at it, which is what
        # makes the two kinds of job share one timer.
        self._active_research.append({
            "tech_key": tech_key,
            "player": player,
            "ticks_remaining": ticks,
            "tech_lab": tech_lab,
            "reinstatement": reinstatement,
        })

        logger.info(
            "Started %s %s for %s (%d ticks)",
            "reinstatement of" if reinstatement else "research",
            tech_key, getattr(player, "key", "?"), ticks,
        )

        if reinstatement:
            return True, (
                f"Started reinstating {tdef.name} at the reduced Reinstatement "
                f"cost ({ticks} ticks)."
            )
        return True, (
            f"Started researching {tdef.name} "
            f"({ticks} ticks)."
        )

    # ------------------------------------------------------------------ #
    #  Tick processing
    # ------------------------------------------------------------------ #

    def process_tick(self) -> None:
        """Decrement research timers and apply completed technologies.

        For each active research entry:
            - Decrement ticks_remaining
            - If ticks_remaining <= 0, apply the technology and remove

        A **Reinstatement** entry rides the same countdown and the same
        completion publish; what differs is where its effect comes from. Its key
        is already on the record (dormancy erased no history — R5.3), so nothing
        is added there. Instead the key is cleared from the Branch's pending set
        through the Branch system, which is that attribute's single writer
        (R15.5), and ``db.tech_bonuses`` is rebuilt — so the reinstated effect
        lands at the same moment a first-time research effect would (R5.7).
        """
        completed = []
        remaining = []

        for entry in self._active_research:
            entry["ticks_remaining"] -= 1
            if entry["ticks_remaining"] <= 0:
                completed.append(entry)
            else:
                remaining.append(entry)

        self._active_research = remaining

        for entry in completed:
            tech_key = entry["tech_key"]
            player = entry["player"]

            tdef = self.registry.technologies.get(tech_key)
            if tdef is None:
                continue

            if entry.get("reinstatement"):
                # R5.7: the key leaves the pending set and the whole bonus dict
                # is rebuilt from the record, which is what applies the effect.
                # The rebuild rather than a direct apply, because the dict is
                # derived state and the pending set is one of its inputs.
                self._clear_reinstatement(player, tech_key)
                self.recompute_tech_bonuses(player)
            else:
                # Apply the technology
                self.apply_technology(player, tdef)

                # Add to researched set
                researched = self._get_researched_techs(player)
                researched.add(tech_key)
                self._set_researched_techs(player, researched)

            # Publish event
            self.event_bus.publish(
                TECHNOLOGY_RESEARCHED,
                player=player,
                technology=tdef,
            )

            logger.info(
                "%s completed: %s for %s",
                "Reinstatement" if entry.get("reinstatement") else "Research",
                tech_key, getattr(player, "key", "?"),
            )

    # ------------------------------------------------------------------ #
    #  Apply technology effects
    # ------------------------------------------------------------------ #

    def apply_technology(self, player: Any, tech_def: TechnologyDef) -> None:
        """Apply a completed technology's effect to the player (R13.3).

        Writes the technology's payload into ``player.db.tech_bonuses`` — a
        cumulative bonus dict read by downstream consumers (CombatEngine,
        FogOfWar, building-hp, production). Multiplicative effects
        (``production_multiplier``) compose; all others are additive.

        The five shipped payload keys and their consumers:
        - ``building_hp``            → building hp_max computation
        - ``damage``                 → CombatEngine attacker bonus
        - ``damage_reduction``       → CombatEngine armor path
        - ``sight_range``            → FogOfWar player vision radius
        - ``production_multiplier``  → equipment/extractor production path

        Args:
            player: The player to apply the effect to.
            tech_def: The technology definition.
        """
        if not tech_def.effect_value:
            return
        self._apply_tech_effect(player, tech_def)

    @staticmethod
    def _apply_tech_effect(player: Any, tech_def: TechnologyDef) -> None:
        """Write tech effects into db.tech_bonuses (R13.3, D5)."""
        effect = tech_def.effect_value
        if not isinstance(effect, dict):
            return
        db = getattr(player, "db", None)
        if db is None:
            return
        bonuses = dict(getattr(db, "tech_bonuses", None) or {})
        for key, value in effect.items():
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
            if key == "production_multiplier":
                bonuses[key] = bonuses.get(key, 1.0) * value
            else:
                bonuses[key] = bonuses.get(key, 0) + value
        db.tech_bonuses = bonuses

    def recompute_tech_bonuses(self, player: Any, planet: Any = None) -> None:
        """Rebuild db.tech_bonuses from scratch out of researched_techs (R13.5).

        ``db.tech_bonuses`` is fully derived state, so it can always be
        recomputed: clear it, then re-apply every researched tech's effect.
        This is the grandfathering path — players who received techs from the
        old rank auto-grant (which never wrote bonuses) gain the real effects
        on their next login recompute. Unknown/stale tech keys are skipped.

        With a Branch resolver wired the rebuild is **filtered to the live
        Branch_Commitment**: a technology whose Branch is not committed on the
        occupied planet is dormant and its effect stays out of the dict (R5.1),
        and so does one still awaiting its reduced-cost Reinstatement job
        (R5.7). Because the whole dict is derived from the record on every
        recompute, a commitment changing needs nothing but another call — the
        Branch system triggers one on every event that can change the answer
        (R5.2). The **record itself is never touched** (R5.3): dormancy suspends
        effects, it erases no history. And because a commitment follows
        ownership of a completed lab rather than that lab's Operational state, a
        lab that is offline, mid-upgrade, or suspended keeps its Branch's
        bonuses applied (R5.10).

        This system stays the single writer of ``db.tech_bonuses``: the Branch
        system supplies the filter (:meth:`_applied_technologies`) and writes
        nothing here.

        Args:
            player: The player whose bonus dict to rebuild.
            planet: The planet the commitment is scoped to, defaulting to the
                one *player* occupies. Passed by the recompute triggers that
                already know the planet, so an arrival recompute can name the
                planet being arrived at.
        """
        db = getattr(player, "db", None)
        if db is None:
            return
        applied = self._applied_technologies(player, planet)
        db.tech_bonuses = {}
        for tech_key in self._get_researched_techs(player):
            if applied is not None and tech_key not in applied:
                continue                       # dormant (R5.1) or pending (R5.7)
            tdef = self.registry.technologies.get(tech_key)
            if tdef is not None and tdef.effect_value:
                self._apply_tech_effect(player, tdef)

    def _applied_technologies(
        self, player: Any, planet: Any = None
    ) -> frozenset[str] | None:
        """Return the Branch filter for the bonus rebuild, or ``None`` for none.

        ``None`` means *unfiltered*, and it is the answer whenever this system
        has no Branch resolver to ask: with none wired the rebuild accumulates
        every researched technology exactly as it did before the Branch feature,
        which is what keeps a minimal fixture and a deployment without the
        Branch system on the pre-feature behavior. Distinguishing "no filter"
        from "an empty filter" is the whole point of the ``None`` — an empty
        frozenset is the legitimate answer for a player committed to nothing,
        and returning that for an unwired resolver would silently zero every
        bonus in the game.

        A wired resolver that cannot answer degrades the same way: whether it
        exposes no filter at all, answers ``None``, or raises, the rebuild runs
        unfiltered rather than raising out of a login or a tick — a raise is
        logged, the other two are the documented "no filter" answer.
        """
        resolver = self._branch
        if resolver is None:
            return None
        query = getattr(resolver, "applied_technologies", None)
        if not callable(query):
            return None
        try:
            applied = query(player, planet)
            return None if applied is None else frozenset(applied)
        except Exception:  # noqa: BLE001 - degrade to unfiltered, never raise out
            logger.exception(
                "Branch resolver failed to filter the researched set; "
                "rebuilding the bonus dict unfiltered."
            )
            return None

    # ------------------------------------------------------------------ #
    #  Reinstatement job pricing and bookkeeping (R5.6, R5.7)
    # ------------------------------------------------------------------ #

    def _awaiting_reinstatement(self, player: Any, tech_key: str) -> bool:
        """Return True when *tech_key* is reinstatable rather than done (R5.7).

        Asked by :meth:`start_research` about a key the record already holds.
        The Branch system owns the pending set, so this is a question rather
        than an attribute read — the same defensive shape
        :meth:`_applied_technologies` and :meth:`owned_research_tree` use.

        ``False`` is the answer whenever the resolver cannot be asked: none
        wired (a minimal fixture, a deployment without the Branch system), one
        that predates the query, or one that raises. That degrades to the
        pre-feature refusal — a recorded technology is simply already
        researched — which is the safe direction: it withholds a discount rather
        than granting a free re-research.
        """
        resolver = self._branch
        if resolver is None:
            return False
        query = getattr(resolver, "reinstatement_pending", None)
        if not callable(query):
            return False
        try:
            return bool(query(player, tech_key))
        except Exception:  # noqa: BLE001 - degrade to "already researched"
            logger.exception(
                "Branch resolver failed to answer whether %s awaits "
                "Reinstatement; treating it as already researched.", tech_key,
            )
            return False

    def _clear_reinstatement(self, player: Any, tech_key: str) -> None:
        """Ask the Branch system to clear a completed Reinstatement key (R5.7).

        ``db.branch_reinstatement`` has exactly one writer and it is not this
        system (R15.5), so a completed job *requests* the clear and then rebuilds
        the bonus dict — the state each system owns stays where it lives.

        Guarded like every other resolver call here: an unwired resolver, one
        without the method, and one that raises are all logged no-ops rather than
        an exception out of a tick. The consequence of a failed clear is a key
        that stays pending and a job that can be run again, which is recoverable;
        a raise inside ``process_tick`` would not be.
        """
        resolver = self._branch
        if resolver is None:
            return
        clear = getattr(resolver, "on_reinstatement_completed", None)
        if not callable(clear):
            logger.warning(
                "Reinstatement of %s completed but the Branch resolver exposes "
                "no on_reinstatement_completed; the key stays pending.",
                tech_key,
            )
            return
        try:
            clear(player, tech_key)
        except Exception:  # noqa: BLE001 - a tick never raises out of here
            logger.exception(
                "Branch resolver failed to clear the Reinstatement of %s; the "
                "key stays pending.", tech_key,
            )

    def _reinstatement_fraction(self) -> float:
        """Return ``balance.branch_reinstatement_cost_fraction`` (R5.6).

        Falls back to :data:`DEFAULT_REINSTATEMENT_COST_FRACTION` for a registry
        carrying no readable :class:`~world.definitions.BalanceConfig`, so a
        minimal fixture prices a Reinstatement job exactly as a configured
        deployment does. The 0.0-to-1.0 range is the schema validator's to
        enforce at load; the floor of one applied below is what keeps even a
        fraction of zero from making a job free.
        """
        balance = getattr(self.registry, "balance", None)
        raw = getattr(
            balance, "branch_reinstatement_cost_fraction",
            DEFAULT_REINSTATEMENT_COST_FRACTION,
        )
        try:
            return float(raw)
        except (TypeError, ValueError):
            return DEFAULT_REINSTATEMENT_COST_FRACTION

    @staticmethod
    def _scaled_cost(cost: dict | None, fraction: float) -> dict:
        """Scale a technology's resource cost for a Reinstatement job (R5.6).

        Per resource line: the defined amount times the configured fraction,
        rounded to the nearest whole unit, with a **floor of one** — so a cheap
        technology is discounted but never free, and a line that exists in the
        defined cost exists in the scaled one. A non-numeric amount is carried
        through untouched rather than dropped, so a hand-edited definition
        cannot turn into a cheaper job than it declares.
        """
        scaled: dict = {}
        for resource, amount in (cost or {}).items():
            try:
                scaled[resource] = max(1, int(round(float(amount) * fraction)))
            except (TypeError, ValueError):
                scaled[resource] = amount
        return scaled

    @staticmethod
    def _scaled_ticks(ticks: Any, fraction: float) -> int:
        """Scale a technology's duration for a Reinstatement job (R5.6).

        The defined duration times the configured fraction, rounded to the
        nearest tick, with the same floor of one: a Reinstatement job always
        takes at least one tick, so it can never complete in the tick it starts.
        An unreadable duration falls back to one tick for the same reason.
        """
        try:
            return max(1, int(round(float(ticks) * fraction)))
        except (TypeError, ValueError):
            return 1

    # ------------------------------------------------------------------ #
    #  Admin single-writer paths (unified-admin-crud @tech adapter)
    # ------------------------------------------------------------------ #
    #
    # The TechnologyAdapter (``world/admin/adapters/tech_adapter.py``)
    # routes every admin grant/revoke through these methods so
    # TechLabSystem stays the single writer for researched-tech state
    # (Requirement 3.5). Derived tech bonuses are recomputed BEFORE the
    # methods return, so the admin response never precedes the recompute
    # (Requirements 7.7, 7.8).

    def admin_grant_technology(self, player: Any, tech_key: str
                               ) -> tuple[bool, str]:
        """Grant *tech_key* to *player* through the research path.

        Adds the technology to the player's researched set exactly like
        research completion does (same set, same event publish), then
        rebuilds ``db.tech_bonuses`` from scratch via
        :meth:`recompute_tech_bonuses` before returning — so derived
        bonuses are current when the admin gets the success response
        (Requirement 7.7).

        Returns:
            ``(True, "")`` on success; ``(False, error)`` when the tech
            key is unknown or the player already holds the technology —
            the error states the player's current grant state and no
            state changes (Requirement 7.9).
        """
        tdef = self.registry.technologies.get(tech_key)
        if tdef is None:
            return False, f"Unknown technology: {tech_key}"
        name = getattr(player, "key", "?")
        researched = self._get_researched_techs(player)
        if tech_key in researched:
            return False, (
                f"{name} already holds technology '{tech_key}' — "
                "current grant state: granted. Nothing changed."
            )
        researched.add(tech_key)
        self._set_researched_techs(player, researched)
        # Derived state: rebuild the bonus dict from the researched set
        # (idempotent — never double-applies) BEFORE returning (R7.7).
        self.recompute_tech_bonuses(player)
        # Mirror the research-completion publish; best-effort — a
        # subscriber error must never fail the completed admin grant.
        try:
            self.event_bus.publish(
                TECHNOLOGY_RESEARCHED, player=player, technology=tdef,
            )
        except Exception:  # noqa: BLE001
            logger.exception("admin grant event publish failed")
        logger.info("Admin-granted tech %s to %s", tech_key, name)
        return True, ""

    def admin_revoke_technology(self, player: Any, tech_key: str
                                ) -> tuple[bool, str]:
        """Revoke *tech_key* from *player* and recompute derived bonuses.

        Removes the technology from the player's researched set, then
        rebuilds ``db.tech_bonuses`` from scratch before returning
        (Requirement 7.8).

        Returns:
            ``(True, "")`` on success; ``(False, error)`` when the
            player does not hold the technology — the error states the
            player's current grant state and no state changes
            (Requirement 7.9).
        """
        name = getattr(player, "key", "?")
        researched = self._get_researched_techs(player)
        if tech_key not in researched:
            return False, (
                f"{name} does not hold technology '{tech_key}' — "
                "current grant state: not granted. Nothing changed."
            )
        researched.discard(tech_key)
        self._set_researched_techs(player, researched)
        self.recompute_tech_bonuses(player)
        logger.info("Admin-revoked tech %s from %s", tech_key, name)
        return True, ""

    # ------------------------------------------------------------------ #
    #  Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _get_player_level(player: Any) -> int:
        """Read the player's level (1-100). See ``world.utils.get_player_level``."""
        from world.utils import get_player_level
        return get_player_level(player, default=0)

    @staticmethod
    def _get_researched_techs(player: Any) -> set:
        """Read the player's researched_techs set."""
        if hasattr(player, "db"):
            techs = getattr(player.db, "researched_techs", None)
            if techs is None:
                techs = set()
                player.db.researched_techs = techs
            return set(techs)
        return set()

    @staticmethod
    def _set_researched_techs(player: Any, techs: set) -> None:
        """Write the player's researched_techs set."""
        if hasattr(player, "db"):
            player.db.researched_techs = techs
