"""
Branch System — Technology Branch identity, commitment, and dormancy.

A **Branch** is a technology tree together with the buildings, technologies,
agent role, and signature vector that belong to that tree. This system is the
authority on Branch identity: which Branch a building definition or technology
belongs to, which lab hosts a Branch, which agent role a Branch owns, and the
six-Branch overview a player reads before committing.

Three invariants shape the module, and every later addition must keep them:

* **Derive, do not store.** A Branch_Commitment is a query over the buildings a
  player owns, not a field; a Branch_Estate is a query; Branch_Dormancy is the
  absence of a commitment. Nothing here caches an answer the world already
  holds, so no restart and no missed event can desynchronize it (R14.6).
* **Framework-free (R15.1).** No game-framework module is imported at module
  scope. Every framework-dependent collaborator arrives by injection at the
  composition root, so this module imports and every query answers with
  ``evennia`` absent from ``sys.modules``.
* **No raise into a caller (R15.3).** Every method returns a value for every
  input. A query that cannot resolve returns the documented empty value —
  ``None``, ``0``, ``{}``, or ``[]`` — rather than raising, so a command layer
  reads a result instead of guarding a call.

Definitions are read through the **injected** :class:`~world.data_registry.\
DataRegistry` and never through ``DataRegistry.get_instance()`` (R15.4), so the
system is fully testable without a process-wide singleton.

**No prose (R13.5).** The three construction gates
(:meth:`BranchSystem.construction_validators`) answer a message *key* carrying
structured data — a :class:`BranchRefusal` — and the one gate message that is a
report rather than a refusal goes out as a structured notification on the event
bus. Not one player-facing sentence is composed in this module; the presenter
and the command layer own every word.

**Two decisions genuinely cannot be derived, and this module is the single
writer of both (R15.5).** The second is a *consent*: whether a player accepts an
ally's support, and whether they accept an ally designating targets for them, is
a decision no query over the world can answer, so it is stored on the consenting
player as ``db.vector_consent`` and revoked when the two stop being allies
(R11.11). The first is the Reinstatement bit below.

Requirements 5.5 and 5.9 differ on one point only: a Branch abandoned
*voluntarily* costs Reinstatement research on the way back, and a Branch whose
lab was destroyed by an enemy does not. After the fact the world cannot tell the
two apart — the lab is simply gone either way — so exactly one bit is persisted,
at the one moment the distinction is known: ``db.branch_abandoned``, written on a
voluntary demolition of that Branch's lab and on nothing else. Its consumer is
``db.branch_reinstatement``, seeded from the owner's recorded technologies when
that Branch's lab is completed again. Both attributes are written *here and
nowhere else*, through the read-copy-write discipline every persisted container
in this codebase uses (R14.7): read the attribute, mutate a **copy**, write the
whole container back, because an Evennia attribute may hand back a serialized
copy whose in-place mutation is discarded. The consent store is written the same
way, through the same two helpers.

**Two ledgers are history rather than state, and this module is the single writer
of both as well (R15.5).** A cooldown is *when* a building last ran an
Operation_Kind (``db.vector_cooldowns`` on that building, R8.19) and an
escalation entry is *when* a hostile operation resolved against a target
(``db.vector_escalation`` on the attacker, R10.6). Both are past events the
present world no longer shows, so neither is derivable — but neither is a
decision either, which is why they are described apart from the two above. Both
go through the same read-copy-write pair, and both are pruned on read against the
**injected** tick source, so a ledger cannot grow without bound and a stale entry
never outlives its window. The third limit, the in-flight cap (R8.20), needs no
ledger at all: the non-terminal Operation_Records a Vector_System already tracks
*are* the count.

**Derived state elsewhere is refreshed by trigger.** Everything this module owns
is a query, but ``db.tech_bonuses`` — the dict a commitment decides the contents
of — is derived state living in another system, and an agent's role assignment is
stored state living in a third. So this module subscribes to every event that can
change a commitment and, from each, asks
``TechLabSystem.recompute_tech_bonuses`` to rebuild the dict (R5.2) and — on a
*lapse* specifically — asks ``AgentSystem.unassign_branch_roles`` to release the
agents a dormant Branch may no longer command (R7.8). The rebuild is cheap and
idempotent, so a trigger is nothing more than "call it again"; and every
subscriber swallows its own failures, because a callback must never raise into
the event bus (R15.3).
"""

from __future__ import annotations

import logging
import math
from contextlib import contextmanager
from typing import Any, Callable, Iterator, TYPE_CHECKING

from world.constants import (
    ATTR_BRANCH_ABANDONED,
    ATTR_BRANCH_REINSTATEMENT,
    ATTR_VECTOR_CONSENT,
    ATTR_VECTOR_COOLDOWNS,
    ATTR_VECTOR_ESCALATION,
    BRANCH_DOCTRINE,
    BRANCH_OPERATION_KIND,
    BRANCH_ROLE,
    BRANCHES,
    CONSENT_KINDS,
    CONSENT_SUPPORT,
    RESEARCH_LAB,
)
from world.systems.base_system import BaseSystem

if TYPE_CHECKING:  # imported for typing only — no runtime cost, no cycle
    from world.data_registry import DataRegistry
    from world.definitions import BuildingDef, TechnologyDef
    from world.event_bus import EventBus

logger = logging.getLogger("evennia.world.systems.branch_system")

#: Message KEYS the three construction gates refuse with. A gate never composes
#: prose (R13.5): it returns the key plus the structured data behind it (see
#: :class:`BranchRefusal`), and the presenter or the command layer owns every
#: word a player reads.
MSG_BRANCH_LAB_REQUIRED = "branch_lab_required"
MSG_BRANCH_MISMATCH = "branch_mismatch"
MSG_BRANCH_SWITCH_BLOCKED = "branch_switch_blocked"
MSG_BRANCH_UNLOCK_REQUIRED = "branch_unlock_required"

#: Message KEYS :meth:`BranchSystem.may_target` refuses with — the four
#: protection gates every Vector_Operation passes through, in the same
#: key-plus-structured-data shape the construction gates use (R13.5).
MSG_VECTOR_TARGET_SHIELDED = "vector_target_shielded"
MSG_VECTOR_TARGET_ALLIED = "vector_target_allied"
MSG_VECTOR_CONSENT_REQUIRED = "vector_consent_required"
MSG_VECTOR_ESCALATION_LIMIT = "vector_escalation_limit"

#: The one gate message that is a REPORT rather than a refusal, so it has no
#: return channel through the validation chain and is published as a structured
#: notification instead (R4.8, R13.4, R13.5).
NOTIFY_BRANCH_DORMANCY = "branch_dormancy_warning"

#: The four lifecycle states R8.2 declares TERMINAL, by value. The Operation
#: Contract owns the ``OperationState`` enum and this module deliberately does
#: not import it: the in-flight count reads a record duck-typed (§4.8), and a
#: ``StrEnum`` member, a plain string, and a persisted value all reduce to the
#: same four names — so the vocabulary is shared by value without the dependency.
#: Private on purpose, so nothing reads this instead of the enum that owns it.
_TERMINAL_STATE_NAMES: frozenset[str] = frozenset(
    {"resolved", "expired", "cancelled", "discarded"}
)

#: ``reason`` values on a ``branch_unlock_required`` refusal, naming which half
#: of R6.2 failed: the technology is not researched at all, its Branch is not
#: committed here so its effects are dormant, or it is still awaiting its
#: Reinstatement job.
UNLOCK_NOT_RESEARCHED = "not_researched"
UNLOCK_DORMANT = "dormant"
UNLOCK_REINSTATEMENT_PENDING = "reinstatement_pending"

__all__ = [
    "BranchRefusal",
    "BranchSystem",
    "MSG_BRANCH_LAB_REQUIRED",
    "MSG_BRANCH_MISMATCH",
    "MSG_BRANCH_SWITCH_BLOCKED",
    "MSG_BRANCH_UNLOCK_REQUIRED",
    "MSG_VECTOR_CONSENT_REQUIRED",
    "MSG_VECTOR_ESCALATION_LIMIT",
    "MSG_VECTOR_TARGET_ALLIED",
    "MSG_VECTOR_TARGET_SHIELDED",
    "NOTIFY_BRANCH_DORMANCY",
    "UNLOCK_DORMANT",
    "UNLOCK_NOT_RESEARCHED",
    "UNLOCK_REINSTATEMENT_PENDING",
]


class BranchRefusal(str):
    """A construction-gate refusal: a message KEY carrying structured data.

    The gates splice into ``BuildingSystem._validate_construction``, whose
    existing validators all answer ``str | None`` — a truthy string refuses the
    construction and a ``None`` passes it. The design requires the Branch gates
    to answer *a message key plus structured data and never composed prose*
    (R13.5), which is two things where the chain accepts one.

    Subclassing ``str`` carries both through the one channel the chain has:

    * The **string value is the message key** (``str(refusal) == refusal.key``),
      so the chain's ``if err:`` refuses exactly as it does for every existing
      validator, ``err + suffix`` still concatenates, and the splice in
      ``BuildingSystem`` stays mechanical — it needs no knowledge of this type.
    * The **structured payload rides along** on :attr:`data`, so whoever renders
      the refusal (the presenter, through the notification contract, or the
      command layer) has every value the requirement asks be reported without a
      single word of prose being composed inside a system component.

    A caller that only understands plain strings degrades to showing a key,
    never to a crash or to a wrong decision — which is why this shape was
    preferred over returning a tuple or stashing the payload on the system
    (state a derive-do-not-store system must not hold).

    Attributes:
        key: The message key, equal to the string value.
        data: The structured values the refusal reports. A fresh dict per
            refusal, so no two refusals share a container.
    """

    __slots__ = ("key", "data")

    def __new__(cls, key: str, **data: Any) -> "BranchRefusal":
        refusal = super().__new__(cls, key)
        refusal.key = key
        refusal.data = data
        return refusal

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"BranchRefusal({self.key!r}, {self.data!r})"


class BranchSystem(BaseSystem):
    """Branch identity, commitment, dormancy, and the shared vector services.

    Constructed at the composition root with every collaborator injected
    (R15.1). Each one is optional so the system is constructible from a bare
    registry and event bus in tests, and so an unwired deployment degrades to a
    refusal rather than an error (R15.2) — a service that needs a collaborator
    checks for it and declines instead of assuming it is there.

    Args:
        registry: The :class:`~world.data_registry.DataRegistry` holding the
            building, technology, and Branch catalog definitions plus the
            hot-tunable :class:`~world.definitions.BalanceConfig`. Every
            definition lookup in this system goes through *this* object, never
            through the process-wide singleton (R15.4).
        event_bus: The :class:`~world.event_bus.EventBus` used to publish
            structured player notifications and to subscribe to the building
            and research events that change a commitment.
        current_tick_func: Callable returning the current game tick, used by the
            cooldown and escalation ledgers. Defaults to a clock that always
            reads 0, which makes every cooldown read as elapsed.
        building_system: The :class:`~world.systems.building_system.\
BuildingSystem`, source of the construction-validation chain the Branch gates
            splice into and of the cumulative-investment arithmetic.
        tech_system: The :class:`~world.systems.tech_system.TechLabSystem`,
            the single writer of ``db.tech_bonuses`` and the owner of the
            researched-technology record a dormancy filter reads.
        agent_system: The :class:`~world.systems.agent_system.AgentSystem`,
            consulted for Carrier_Agent eligibility and asked to unassign
            Branch roles when a commitment lapses.
        alliance_system: The :class:`~world.systems.alliance_system.\
AllianceSystem`, consulted for the allied-target and support-consent checks.
        combat_engine: The :class:`~world.systems.combat_engine.CombatEngine`,
            the only path a Vector_Operation may damage through.
    """

    def __init__(
        self,
        registry: "DataRegistry",
        event_bus: "EventBus",
        current_tick_func: Callable[[], int] | None = None,
        building_system: Any = None,
        tech_system: Any = None,
        agent_system: Any = None,
        alliance_system: Any = None,
        combat_engine: Any = None,
    ) -> None:
        super().__init__(registry, event_bus)
        self._current_tick_func = current_tick_func or (lambda: 0)
        self._building_system = building_system
        self._tech_system = tech_system
        self._agent_system = agent_system
        self._alliance_system = alliance_system
        self._combat_engine = combat_engine
        # A building the owner is about to lose, held only for the duration of
        # one destruction subscriber (see :meth:`_ignoring`). Not cached state:
        # it is always ``None`` between events.
        self._ignored_building: Any = None
        # The registered Vector_Systems, keyed by Operation_Kind in registration
        # order (:meth:`register_vector`). Not cached state either — a live
        # collaborator registry, not an answer — and it has exactly three
        # readers: the in-flight cap, whose count is that vector's own tracked
        # records (R8.20), the tick fan-out (:meth:`process_tick`, R15.9), and
        # the by-value view (:meth:`registered_vectors`) the composition root's
        # restart rebuild walks.
        self._vectors: dict[str, Any] = {}
        self._subscribe_recompute_triggers(event_bus)
        self._subscribe_consent_revocation(event_bus)

    # ------------------------------------------------------------------ #
    #  Identity (R1.6, R2.6, R15.4)
    # ------------------------------------------------------------------ #

    def branch_of_building(self, abbr_or_def: Any) -> str | None:
        """Return the Branch *abbr_or_def* belongs to, or ``None``.

        Accepts either a building abbreviation (``"BX"``) or an already-resolved
        :class:`~world.definitions.BuildingDef`, so callers holding one or the
        other need no conversion step.

        The answer is the definition's Branch_Affiliation (``bdef.branch``)
        falling back to the Branch it *hosts* (``bdef.research_tree``) — so a
        Branch_Lab belongs to its own Branch even when it omits the optional
        affiliation field, and the two fields can never disagree about which
        Branch a lab is part of (R2.4 makes them equal when both are set).

        Returns:
            The Branch name, or ``None`` for a Neutral_Building, an unknown
            abbreviation, or anything this system cannot resolve to a
            definition (R2.2, R15.3).
        """
        return self._branch_of_def(self._building_def(abbr_or_def))

    def branch_of_technology(self, tech_key: str) -> str | None:
        """Return the Branch technology *tech_key* belongs to, or ``None``.

        Reads the technology's ``tree`` from the injected registry. ``None``
        when the key is unknown, empty, or the definition declares no tree —
        never a raise, so a caller can pass a player-supplied key straight in
        (R15.3).
        """
        tdef = self._technology_def(tech_key)
        if tdef is None:
            return None
        return self._clean(getattr(tdef, "tree", None))

    def lab_for_branch(self, branch: str) -> str | None:
        """Return the abbreviation of the Branch_Lab hosting *branch*, or ``None``.

        The Branch-to-lab bijection is enforced at load, so at most one lab
        hosts a Branch; this resolves that one through the injected registry.

        Returns:
            The hosting lab's abbreviation, or ``None`` when *branch* is not one
            of the six, no loaded lab hosts it, or the definition carries no
            abbreviation.
        """
        bdef = self._lab_def_for_branch(branch)
        if bdef is None:
            return None
        return self._clean(getattr(bdef, "abbreviation", None))

    def branch_buildings(self, branch: str) -> list[str]:
        """Return the abbreviations of the Branch_Buildings affiliated with *branch*.

        A Branch_Building is a **non-lab** building whose definition declares
        this Branch, so the hosting lab is deliberately absent from the list —
        :meth:`lab_for_branch` reports that one, and the two together are the
        Branch's full building catalog. (The Branch_Estate query, which counts
        the lab as a member, is a different question about *owned* buildings.)

        The order is the registry's own definition order, so the answer matches
        a linear scan of the loaded definitions.

        Returns:
            A list of abbreviations, empty when *branch* is not one of the six
            or when no loaded definition declares it (R15.3).
        """
        wanted = self._clean(branch)
        if wanted is None:
            return []
        out: list[str] = []
        for bdef in self._iter_building_defs():
            if self._is_lab(bdef):
                continue
            if self._branch_of_def(bdef) != wanted:
                continue
            abbr = self._clean(getattr(bdef, "abbreviation", None))
            if abbr is not None:
                out.append(abbr)
        return out

    def role_for_branch(self, branch: str) -> str | None:
        """Return the one Carrier_Agent role *branch* owns, or ``None``.

        The role-to-Branch mapping is a bijection the schema validator
        cross-checks against the agent role table, so exactly one role belongs
        to each of the six Branches. ``None`` for anything outside them.
        """
        wanted = self._clean(branch)
        if wanted is None:
            return None
        return BRANCH_ROLE.get(wanted)

    def branch_overview(self) -> list[dict]:
        """Return the six-Branch overview a player reads before committing (R13.3).

        One entry per Branch, in the canonical Branch order, so the projection
        is stable between calls and identical for every player — the overview
        describes the *catalog*, not a player's state.

        Every value is structured data for the presenter to render; no message
        text is composed here (R13.5).

        Returns:
            A list of dicts, each holding:

            - ``branch`` — the Branch name, the data word.
            - ``doctrine`` — the player-facing doctrine name.
            - ``lab`` — the hosting Branch_Lab's abbreviation, or ``None``.
            - ``lab_name`` — that lab's display name, or ``None``.
            - ``role`` — the Carrier_Agent role the Branch owns.
            - ``operation_kind`` — the Branch's Signature_Vector identifier.
            - ``buildings`` — the affiliated non-lab Branch_Buildings.
            - ``technologies`` — the technology keys belonging to the Branch.
            - ``advantage_over`` — the Branches this Branch counters.
            - ``countered_by`` — the Branches that counter this Branch.

            The list is never empty and never raises: a Branch with nothing
            loaded for it reports ``None`` and empty lists rather than being
            omitted, so the overview always describes all six.
        """
        web = self._counter_web()
        return [
            {
                "branch": branch,
                "doctrine": BRANCH_DOCTRINE.get(branch),
                "lab": self.lab_for_branch(branch),
                "lab_name": self._clean(
                    getattr(self._lab_def_for_branch(branch), "name", None)
                ),
                "role": BRANCH_ROLE.get(branch),
                "operation_kind": BRANCH_OPERATION_KIND.get(branch),
                "buildings": self.branch_buildings(branch),
                "technologies": self._technologies_for_branch(branch),
                "advantage_over": list(web.get(branch, ())),
                "countered_by": sorted(
                    source
                    for source, targets in web.items()
                    if branch in targets
                ),
            }
            for branch in BRANCHES
        ]

    # ------------------------------------------------------------------ #
    #  Commitment (R3)
    # ------------------------------------------------------------------ #

    def commitment(self, player: Any, planet: Any = None) -> str | None:
        """Return the Branch *player* is committed to on *planet*, or ``None``.

        The Branch_Commitment is **derived**, never stored: it is the Branch
        hosted by the Branch_Lab that *player* owns on *planet* (R3.1). This
        method writes nothing, so no restart and no missed event can
        desynchronize a commitment from the buildings that define it (R14.6) —
        the buildings *are* the record.

        The rule is **ownership of a completed lab, and nothing else**:

        * No Branch_Lab owned on the planet — no commitment (R3.2). A lab that
          was destroyed is gone from the owner's buildings, so its commitment
          vanishes with it and stays gone until a lab is completed there again
          (R3.8).
        * A lab still ``under_construction`` confers no commitment: a half-built
          lab hosts nothing. (:func:`~world.utils.owner_research_lab` owns that
          filter, and it is deliberately left untouched.)
        * A completed lab that is ``offline``, mid-upgrade, or suspended by a
          hostile Signals intrusion **still** confers its owner's commitment
          (R3.9). Commitment follows ownership, not the Operational state, so
          suspending a lab withholds the lab's *function* rather than the
          Branch's researched bonuses (R5.10).

        Args:
            player: The player whose commitment to resolve.
            planet: The planet to scope the answer to. Defaults to the planet
                *player* currently occupies (``db.coord_planet``), because a
                commitment is per-player **and** per-planet — the same player
                may hold a different one on each planet (R3.7).

        Returns:
            The Branch name, or ``None`` when *player* owns no completed
            Branch_Lab there, when the lab's type resolves to no definition, or
            for any input this system cannot read at all (R15.3).
        """
        # No owned lab, or one whose type resolves to no definition, answers
        # ``None`` — which is R3.2 and, once the lab is gone, R3.8.
        return self._hosted_branch_of(self._owned_lab(player, planet))

    def has_commitment(self, player: Any, branch: str, planet: Any = None) -> bool:
        """Return True when *player*'s commitment on *planet* is *branch*.

        The one predicate the construction gates, the role gate, and the
        dormancy filter phrase their question as, so "is this Branch live for
        this player here" has a single implementation.

        A player holding **no** commitment matches no Branch, and so does a
        *branch* outside the six or a blank one — the answer is False rather
        than a raise (R15.3). To ask the opposite question ("is this player
        committed to nothing here"), read :meth:`commitment` for ``None``.
        """
        wanted = self._clean(branch)
        if wanted is None:
            return False
        return self.commitment(player, planet) == wanted

    # ------------------------------------------------------------------ #
    #  Estate (R4)
    # ------------------------------------------------------------------ #

    def estate(self, player: Any, branch: str, planet: Any = None) -> list:
        """Return the buildings *player* owns on *planet* affiliated with *branch*.

        A Branch_Estate is **derived**, never stored (R14.6): it is a scan over
        the buildings the player owns right now, so this method writes nothing
        and no restart can desynchronize it. That is also why nothing counts
        destructions — a razed building has left ``get_buildings()``, so the
        next call simply returns a shorter list, and a hostile destruction
        advances the owner toward a switch on exactly the same terms as a
        demolition (R4.6). An emptied estate stops blocking a lab of another
        Branch for the same reason (R4.3).

        Two membership rules distinguish an estate from the *catalog* queries:

        * **The Branch's own lab is a member.** Each building's Branch is
          resolved through :meth:`_branch_of_live_building`, which reads
          ``branch`` falling back to ``research_tree``, so a Branch_Lab counts
          toward the Branch it hosts even when it omits the optional
          affiliation field. (:meth:`branch_buildings`, the catalog query,
          deliberately excludes it — that one answers "what may be built".)
        * **A building under construction is a member** (R4.7): a half-built
          Branch_Building blocks a switch. No filter excludes it — this is the
          deliberate difference from :meth:`commitment`, which needs a
          *completed* lab, so a half-built lab appears in its Branch's estate
          while conferring no commitment.

        Args:
            player: The owner whose estate to scan.
            branch: The Branch to scope the answer to.
            planet: The planet to scope the answer to. Defaults to the planet
                *player* currently occupies, because an estate is per-player
                **and** per-planet, exactly like a commitment (R3.7). A
                building whose planet cannot be determined is counted on every
                planet, matching the ownership queries in ``world.utils``.

        Returns:
            A list of building objects, in the owner's own building order.
            Empty for a player who owns nothing there, for a Neutral-only
            estate, for a *branch* outside the six, and for any input this
            system cannot read at all (R15.3).
        """
        wanted = self._clean(branch)
        if wanted is None:
            return []
        return self._estate_by_branch(player, planet).get(wanted, [])

    def estate_count(self, player: Any, branch: str, planet: Any = None) -> int:
        """Return how many buildings *player* owns on *planet* in *branch*.

        The count :meth:`estate` reports the members of, and the one number the
        demolish path (R4.5) and the switch refusal (R4.1) quote so a player can
        measure progress toward emptying an estate. ``0`` for anything
        unresolvable, never a raise (R15.3).
        """
        return len(self.estate(player, branch, planet))

    def conflicting_estates(
        self, player: Any, planet: Any, incoming_branch: str
    ) -> dict[str, list]:
        """Return the non-empty estates that block committing to *incoming_branch*.

        The question the Branch-switch gate asks (R4.1, R4.2): the player wants
        a Branch_Lab hosting *incoming_branch* on *planet*, so every **other**
        Branch's estate there stands in the way. The gate refuses while this
        answer is non-empty and reports the count plus each blocking building;
        when it is empty the switch is permitted (R4.3).

        Args:
            player: The owner whose estates to scan.
            planet: The planet the lab would be built on. ``None`` means the
                planet *player* currently occupies, as in :meth:`estate`.
            incoming_branch: The Branch the requested lab hosts, excluded from
                the answer — a player's *own* incoming estate never blocks the
                player.

        Returns:
            ``{branch: [building, ...]}`` in canonical Branch order, holding
            only the Branches with at least one building, so the mapping is
            falsy exactly when nothing blocks the switch. Empty when
            *incoming_branch* is not one of the six or cannot be read, because
            no Branch_Lab is being requested in that case (R15.3).
        """
        incoming = self._clean(incoming_branch)
        if incoming is None or incoming not in BRANCHES:
            return {}
        grouped = self._estate_by_branch(player, planet)
        conflicts: dict[str, list] = {}
        for branch in BRANCHES:
            if branch == incoming:
                continue
            members = grouped.get(branch)
            if members:
                conflicts[branch] = members
        return conflicts

    # ------------------------------------------------------------------ #
    #  Dormancy — the Operational overlay (R5.4, R11.3)
    # ------------------------------------------------------------------ #

    def is_operational(self, building: Any) -> bool:
        """Return True when *building* is Operational **and** its Branch is live.

        The Branch overlay on the existing Operational gate: a Branch_Building's
        own capability behaviour, and the Vector_Operation driver's
        originating-building check, ask *this* rather than
        ``world.utils.building_is_operational`` directly. Three conjuncts, in
        the order that is cheapest to falsify:

        1. **The base gate.** :func:`world.utils.building_is_operational` — the
           existing "is this building doing its job right now" answer: not
           ``offline``, not ``under_construction`` (which covers a mid-upgrade
           building). That function is deliberately **left unmodified**: it has
           many callers, no registry access, and no business reaching into
           commitment state, so the Branch half lives here instead and every
           pre-feature consumer keeps the pre-feature answer (R2.5, R10.8).
        2. **The Active_HQ_Rule** (R11.3). The owner must hold a *completed*
           headquarters on the building's planet, so a player whose HQ is gone
           operates no Branch_Building there — the same rule, read through the
           same :func:`world.utils.owner_has_active_hq` predicate, that already
           gates turret auto-fire and guard AI. An NPC base is covered by the
           identical read: its Sentinel owner enumerates buildings like any
           player, so its template's HQ keeps its Branch_Buildings live.
        3. **The Branch being live** (R5.4). A Branch_Building whose owner holds
           no matching Branch_Commitment on that planet reports non-Operational,
           so a dormant Branch's buildings perform no capability behaviour. A
           **Neutral_Building** has no affiliation and so no Branch to be
           dormant in — this conjunct passes it untouched, which is what bounds
           the overlay's blast radius to the buildings this feature introduces.
           An NPC base's commitment derives from the Branch_Lab its template
           fields (``BaseTemplateDef.branch`` is what says which Branch a
           template fields), exactly as a player's does.

        The planet every conjunct is scoped to is the building's own
        (:meth:`_building_planet`), falling back to the planet the owner
        occupies — one resolution shared by both the HQ read and the commitment
        read, so the two can never answer about different planets. A planet that
        stays unresolvable is the "any planet" wildcard both underlying queries
        already document.

        Args:
            building: The building to judge. A live building object; anything
                else is unresolvable and answers ``False``.

        Returns:
            ``True`` only when all three conjuncts hold. ``False`` for a
            building this system cannot read, one carrying no resolvable owner,
            and any other unresolvable input — never a raise (R15.3), because
            the callers are capability behaviours running inside a tick.
        """
        from world.utils import building_is_operational

        try:
            if not building_is_operational(building):
                return False                              # the base gate
        except Exception:  # noqa: BLE001 - an unreadable building is inert
            logger.debug("operational read failed for %r", building, exc_info=True)
            return False
        owner = self._owner_of(building)
        if owner is None:
            return False
        planet = self._building_planet(building) or self._player_planet(owner)
        if not self._owner_has_active_hq(owner, planet):
            return False                                  # R11.3
        branch = self._branch_of_live_building(building)
        if branch is None:
            return True                                   # Neutral_Building
        return self.commitment(owner, planet) == branch   # R5.4

    def _owner_of(self, building: Any) -> Any:
        """Return the player or NPC Sentinel owning *building*, or ``None``.

        Reads the ``owner`` reference by value through the same guarded
        attribute read every other lookup here uses, so a real Building, a
        Sentinel-owned NPC building, and a test fake all resolve alike, and an
        ownerless or unreadable building answers ``None``.
        """
        return self._player_attr(building, "owner")

    def _owner_has_active_hq(self, owner: Any, planet: Any = None) -> bool:
        """Return True when *owner* holds a completed HQ on *planet* (R11.3).

        Delegates to :func:`world.utils.owner_has_active_hq` — the existing
        Active_HQ_Rule predicate, reused rather than reimplemented, so a base
        that is inert for turrets is inert for Branch_Buildings by the same
        answer. A half-built HQ does not count, and ``planet=None`` is that
        function's "any planet" wildcard.

        The import is function-local so this module stays importable with the
        game framework absent (R15.1), and ``self.registry`` is passed as the
        capability provider so the HQ lookup resolves through the **injected**
        registry rather than a process-wide singleton (R15.4).
        """
        if owner is None:
            return False
        from world.utils import owner_has_active_hq

        try:
            return bool(
                owner_has_active_hq(owner, planet=planet, provider=self.registry)
            )
        except Exception:  # noqa: BLE001 - an unreadable roster is "no HQ"
            logger.debug("active-HQ lookup failed for %r", owner, exc_info=True)
            return False

    # ------------------------------------------------------------------ #
    #  Dormancy — the applied-technology filter (R5.1, R5.3, R5.7)
    # ------------------------------------------------------------------ #

    def applied_technologies(
        self, player: Any, planet: Any = None
    ) -> frozenset[str]:
        """Return the recorded technology keys whose effects are live on *planet*.

        This is the filter ``TechLabSystem.recompute_tech_bonuses`` rebuilds
        ``db.tech_bonuses`` through — the subset of *player*'s researched record
        that the live Branch_Commitment applies. A key is live when
        :meth:`_unapplied_reason` — the single implementation of R6.2's
        "researched AND applied", which the unlock gate reads too — finds no
        reason to withhold it, so the gate that unlocks a building and the
        bonuses in the dict can never disagree about what an applied technology
        is. Two reasons withhold a key here:

        * its Branch is not the commitment on *planet*, so the whole Branch is
          dormant and its effects are inert (R5.1);
        * it is still awaiting its reduced-cost Reinstatement job (R5.7).

        The commitment and the Reinstatement pending set are resolved **once**
        and reused across the record, so filtering a whole record costs one
        owned-buildings scan rather than one per key.

        This method **writes nothing**: the researched record is untouched,
        because dormancy suspends effects and erases no history (R5.3). And it
        is a query over the buildings the player owns right now, so the answer
        follows a commitment lapsing or returning with no event needed (R14.6).

        Args:
            player: The player whose record to filter.
            planet: The planet to scope the commitment to, defaulting to the
                planet *player* occupies — bonuses follow the commitment on the
                planet the player is on (R5.1).

        Returns:
            A frozenset of technology keys. Empty for a player with no record
            and for a player holding no commitment on *planet* (every recorded
            key is then dormant), and empty rather than a raise for any input
            this system cannot read at all (R15.3).
        """
        recorded = self._recorded_technologies(player)
        if not recorded:
            return frozenset()
        live = self.commitment(player, planet)
        pending = self._pending_reinstatement(player, live)
        return frozenset(
            key
            for key in recorded
            if self._withheld_reason(recorded, live, pending, key) is None
        )

    def dormant_branches(self, player: Any, planet: Any = None) -> dict[str, int]:
        """Return how much research *player* holds in each dormant Branch.

        A Branch is in Branch_Dormancy for a player when that player has
        technologies recorded in it but holds no Branch_Commitment for it on the
        planet in question — so its bonuses and abilities are inert there. The
        record itself is untouched by dormancy (R5.3), which is exactly why this
        count exists: it is the size of the history a player keeps in a doctrine
        they are not currently running, and the figure the technology view quotes
        beside the Reinstatement cost fraction (R13.2).

        Derived, like every other Branch query: the commitment is the owned lab
        and the counts are a grouping of the record, so nothing here is stored
        and no missed event can stale the answer (R14.6).

        Args:
            player: The player whose record to group.
            planet: The planet the commitment is scoped to, defaulting to the
                planet *player* occupies — a Branch dormant on one planet may be
                live on another (R3.7).

        Returns:
            ``{branch: count}`` in canonical Branch order, holding only the
            Branches the player has a record in. Empty for a player with no
            record and for one whose whole record sits in the live commitment;
            empty rather than a raise for any unreadable input (R15.3). A player
            holding no commitment at all has **every** recorded Branch here.
        """
        dormant = self._dormant_technologies(
            player, self.commitment(player, planet)
        )
        return {branch: len(keys) for branch, keys in dormant.items()}

    # ------------------------------------------------------------------ #
    #  Recompute triggers (R5.2, R3.8, R7.8)
    # ------------------------------------------------------------------ #

    def _subscribe_recompute_triggers(self, event_bus: Any) -> None:
        """Subscribe the events that can change which Branch bonuses apply.

        Called once from ``__init__``, following the convention every other
        event-driven system here uses (``ShieldSystem``, ``BaseElimination``,
        ``OutpostSpawner``): a system wires its own subscriptions, so the
        composition root constructs it and is done. Three events, and the reason
        each one can change the answer:

        * ``CONSTRUCTION_COMPLETED`` — a Branch_Lab finishing *establishes* a
          commitment, so that Branch's recorded technologies stop being dormant.
        * ``BUILDING_DESTROYED`` — a Branch_Lab lost *removes* one (R3.8), which
          is the lapse path: bonuses go inert and the Branch's agents are
          released (R7.8).
        * ``PLAYER_MOVED`` — a commitment is per-planet, so arriving on a planet
          the player holds a different commitment on (or none) changes which
          bonuses apply without any building changing at all.

        A **voluntary demolition** publishes no event of its own — it refunds,
        deletes, and reports — so it is the one trigger that arrives as a direct
        call, :meth:`on_building_demolished`. That is also the distinction R5.5
        and R5.9 hang on, so keeping it a separate entry point is a feature
        rather than a workaround.

        The import is function-local so this module stays importable with the
        game framework absent (R15.1), and the whole thing is a no-op for an
        event bus that cannot subscribe, so a minimal test double stays usable
        (R15.3).
        """
        subscribe = getattr(event_bus, "subscribe", None)
        if not callable(subscribe):
            return
        from world.event_bus import (
            BUILDING_DESTROYED,
            CONSTRUCTION_COMPLETED,
            PLAYER_MOVED,
        )

        try:
            subscribe(CONSTRUCTION_COMPLETED, self.on_construction_completed)
            subscribe(BUILDING_DESTROYED, self.on_building_destroyed)
            subscribe(PLAYER_MOVED, self.on_player_moved)
        except Exception:  # noqa: BLE001 - an unwired trigger is not fatal
            logger.debug("recompute-trigger subscription failed", exc_info=True)

    def on_construction_completed(
        self,
        event_name: str = "",
        player: Any = None,
        building: Any = None,
        tile: Any = None,
        **_kwargs: Any,
    ) -> None:
        """Rebuild the owner's bonuses when a Branch_Lab completes (R5.2, R5.5).

        A completed lab is what *creates* a Branch_Commitment, so every
        technology the owner has on record in that Branch stops being dormant at
        this moment. Nothing else here changes an answer: a Branch_Building
        completing belongs to an estate, and an estate decides no bonus — so the
        subscriber filters to labs and returns for everything else rather than
        recomputing on every build in the game.

        This is also the **return** half of the Reinstatement bookkeeping. If the
        owner abandoned this Branch voluntarily, the abandoned bit is set, and
        :meth:`_seed_reinstatement` converts it into a pending set: every recorded
        technology of the Branch must be re-researched at the reduced cost before
        its effect applies again (R5.5). If the bit is **absent** — the lab was
        lost to an attack, or this is a first commitment, or an upgrade of a lab
        already standing — nothing is written and the Branch's effects simply
        return on the recompute below with no research at all (R5.9).

        The seed runs **before** the recompute on purpose: the rebuild reads the
        pending set through :meth:`applied_technologies`, so seeding first is what
        makes the reinstated keys inert from the very first rebuild rather than
        applying for one tick and then being withdrawn (R5.7).

        This is **not** a lapse. A commitment is being gained, so no agent loses
        a role here; the release path is :meth:`on_building_destroyed` and
        :meth:`on_building_demolished`.

        Args:
            event_name: The published event name, accepted and unused.
            player: The owner, as ``BuildingSystem`` publishes it. Falls back to
                the building's own ``owner`` for a completion published without
                one (the engineer path).
            building: The completed building.
            tile: The tile it stands on, read only as a planet fallback.
            **_kwargs: Any future payload key, ignored.

        Returns:
            ``None`` always. Every failure is swallowed and logged: a subscriber
            must never raise into the event bus (R15.3).
        """
        bdef = self._building_def_of(building)
        if bdef is None or not self._is_lab(bdef):
            return
        owner = player if player is not None else self._owner_of(building)
        if owner is None:
            return
        self._seed_reinstatement(owner, self._hosted_branch_of_def(bdef))  # R5.5
        # R5.1: the dict reflects the commitment on the planet the owner
        # OCCUPIES, so the rebuild is deliberately not scoped to the planet the
        # lab completed on — the owner may be standing somewhere else entirely,
        # and re-scoping the dict to this event's planet would swap their live
        # bonuses for that planet's until the next trigger corrected it.
        self._recompute_bonuses(owner)

    def on_building_destroyed(
        self,
        event_name: str = "",
        building: Any = None,
        attacker: Any = None,
        tile: Any = None,
        **_kwargs: Any,
    ) -> None:
        """Handle a Branch_Lab lost to hostile action: the lapse path (R3.8).

        Losing the lab removes the commitment it conferred, so two things follow
        and both happen here:

        * the owner's bonuses are rebuilt, dropping that Branch's effects (R5.2);
        * every agent of that owner holding one of that Branch's roles on that
          planet is unassigned, so a dormant Branch commands no agents (R7.8).

        **The event fires before the building is deleted**, so the owner's roster
        still contains the dying lab and a naive commitment read would still
        return its Branch — the same wrinkle ``ShieldSystem`` handles by
        excluding the doomed building from the roster it refreshes. Here the
        whole handler runs inside an :meth:`_ignoring` scope, so the commitment
        read *and* the recompute that calls back into it both see the world as it
        will be one line later.

        Two shapes are deliberately let through untouched:

        * a **non-lab** destruction — it shrinks an estate, and an estate decides
          no bonus and no role;
        * a lab still **under construction** — it hosted nothing, so nothing
          lapses. Recomputing would be harmless, but firing a *lapse* for a
          commitment that never existed would not be.

        This path writes no "abandoned" marker: R5.9 requires a lab lost to an
        attack to restore its Branch's effects on the rebuild with no
        Reinstatement research, and the way to get that is to record nothing
        here.

        Args:
            event_name: The published event name, accepted and unused.
            building: The building about to be deleted.
            attacker: The destroying player, accepted and unused — the lapse is
                the *owner's*.
            tile: The tile it stands on, read only as a planet fallback.
            **_kwargs: Any future payload key, ignored.

        Returns:
            ``None`` always; every failure is swallowed and logged (R15.3).
        """
        bdef = self._building_def_of(building)
        if bdef is None or not self._is_lab(bdef):
            return
        if self._player_attr(building, "under_construction"):
            return                                        # hosted nothing
        owner = self._owner_of(building)
        if owner is None:
            return
        planet = self._event_planet(building, tile, owner)
        lost = self._hosted_branch_of_def(bdef)
        with self._ignoring(building):
            if lost is not None and self.commitment(owner, planet) != lost:
                self._lapse(owner, planet, lost)          # R7.8
            # R5.1: the lapse is scoped to the planet the lab stood on, but the
            # bonus dict reflects the planet the owner OCCUPIES — a lab lost on
            # another planet must not re-scope the dict to that planet and drop
            # the live commitment's bonuses out from under the owner.
            self._recompute_bonuses(owner)                # R5.2

    def on_building_demolished(
        self, player: Any, building_def: Any, planet: Any = None
    ) -> None:
        """Handle a voluntary demolition of a Branch_Lab: the lapse path (R3.8).

        The demolish path publishes no ``BUILDING_DESTROYED`` — it refunds,
        deletes, and reports — so it is the one trigger that arrives as a direct
        call rather than an event. The command layer makes it right after the
        estate-progress report, which is *after* the delete, so unlike
        :meth:`on_building_destroyed` this reads the world as it already is and
        needs no exclusion scope. That is also why the lab is identified by its
        **definition** (or abbreviation) rather than by an object: the object is
        gone.

        The consequences are the destruction path's — bonuses rebuilt (R5.2) and
        the Branch's agents released (R7.8) — and a non-lab demolition is let
        through untouched, because emptying an estate changes no commitment.

        What separates the two paths is what R5.5 and R5.9 hang on: a
        *voluntary* abandonment costs Reinstatement research on the way back and
        a hostile loss does not. **This is the one moment that distinction is
        knowable**, so this is the one place it is recorded: the abandoned bit
        goes on here (R5.5) and :meth:`on_building_destroyed` deliberately writes
        nothing (R5.9). Afterwards the world cannot tell the two apart — the lab
        is gone either way — which is exactly why the bit exists.

        The bit is set on the same condition as the lapse, not on the demolition
        alone: what it marks is a Branch *abandoned*, so a lab of that Branch
        still standing on the planet (the commitment survives the demolition)
        records nothing, because nothing was abandoned.

        Args:
            player: The owner who demolished the lab.
            building_def: The demolished lab's :class:`BuildingDef` or its
                abbreviation — whichever the caller still holds.
            planet: The planet it stood on. ``None`` reads the planet *player*
                occupies, which is the right answer for a demolition: a player
                must stand on a building's tile to demolish it.

        Returns:
            ``None`` always; every failure is swallowed and logged (R15.3).
        """
        bdef = self._building_def(building_def)
        if bdef is None or not self._is_lab(bdef):
            return
        if player is None:
            return
        if planet is None:
            planet = self._player_planet(player)
        lost = self._hosted_branch_of_def(bdef)
        if lost is not None and self.commitment(player, planet) != lost:
            self._mark_abandoned(player, lost)            # R5.5
            self._lapse(player, planet, lost)             # R7.8
        # R5.1: rebuilt for the planet the player OCCUPIES (the default), which
        # for a demolition is the event's planet anyway — a player stands on a
        # building's tile to demolish it — but an admin path naming another
        # planet must not re-scope the dict away from where the player is.
        self._recompute_bonuses(player)                   # R5.2

    def on_player_moved(
        self,
        event_name: str = "",
        player: Any = None,
        planet: Any = None,
        old_planet: Any = None,
        **_kwargs: Any,
    ) -> None:
        """Rebuild the mover's bonuses on a cross-planet arrival (R5.2).

        A Branch_Commitment is per-planet (R3.7) and the bonuses that apply are
        the ones of the planet the player *occupies* (R5.1), so travelling from a
        planet the player holds a lab on to one they do not makes the dict stale
        without a single building changing. The trigger is the arrival.

        A **same-planet** move is skipped: no building changed hands and the
        commitment is by definition the same one, so there is nothing to rebuild.
        A mover with **no recorded technologies** is skipped for the same reason
        — an empty record accumulates to an empty dict either way — which also
        means the agents and other entities that ride the same cross-planet
        primitive are left alone rather than having a bonus dict written onto
        them.

        No lapse fires here. Moving between planets changes no *planet's*
        commitment, so no planet's Branch stops being able to command its agents;
        R7.8 is about a Branch going dormant, not about its owner travelling.

        Args:
            event_name: The published event name, accepted and unused.
            player: The entity that moved.
            planet: The planet arrived at. ``None`` falls back to the planet
                *player* occupies, which the mover has already been moved to.
            old_planet: The planet departed from, used only to skip a
                same-planet move. ``None`` means "unknown", and an unknown
                origin recomputes rather than guessing.
            **_kwargs: Any future payload key, ignored.

        Returns:
            ``None`` always; every failure is swallowed and logged (R15.3).
        """
        if player is None:
            return
        if planet is not None and old_planet is not None and planet == old_planet:
            return
        if not self._recorded_technologies(player):
            return
        self._recompute_bonuses(player, planet)

    def _lapse(self, owner: Any, planet: Any, branch: str) -> None:
        """React to *branch* ceasing to be *owner*'s commitment on *planet*.

        The one place the consequences of a lapse are listed, so the destruction
        trigger and the demolition trigger cannot drift apart. Today that is
        exactly R7.8 — release the agents the Branch may no longer command — and
        the bonus rebuild is deliberately *not* here: it runs on every trigger,
        lapse or not, and both callers make it themselves.
        """
        self._unassign_branch_roles(owner, planet, branch)

    def _unassign_branch_roles(
        self, owner: Any, planet: Any, branch: str
    ) -> None:
        """Ask the AgentSystem to release *branch*'s roles on *planet* (R7.8).

        A dormant Branch commands no agents, and the agent roster is the
        AgentSystem's state, so this is a request rather than a write — this
        module reaches into no other system's attributes.

        Every step is guarded because the collaborator may legitimately be
        absent: no AgentSystem injected at all degrades to a logged no-op
        (R15.2), and so does one that predates ``unassign_branch_roles``, which
        is what lets this trigger be correct now and tighten by itself the moment
        that method lands.
        """
        if owner is None or branch is None:
            return
        agents = self._agent_system
        if agents is None:
            logger.debug(
                "Branch role release skipped for %r: no AgentSystem injected "
                "(R15.2)", branch,
            )
            return
        release = getattr(agents, "unassign_branch_roles", None)
        if not callable(release):
            logger.debug(
                "Branch role release skipped for %r: the injected AgentSystem "
                "exposes no unassign_branch_roles", branch,
            )
            return
        try:
            release(owner, planet, branch)
        except Exception:  # noqa: BLE001 - a release never breaks the event bus
            logger.debug(
                "Branch role release failed for %r on %r", branch, planet,
                exc_info=True,
            )

    def _recompute_bonuses(self, player: Any, planet: Any = None) -> None:
        """Ask the TechLabSystem to rebuild *player*'s applied bonuses (R5.2).

        ``db.tech_bonuses`` is fully derived from the researched record filtered
        to the live commitment, and the rebuild is cheap and idempotent — so a
        trigger is nothing more than "call it again", and calling it when the
        answer has not changed costs a small loop and writes the same dict.
        ``TechLabSystem`` stays the dict's single writer; this module only asks.

        *planet* is the planet the player OCCUPIES, never the planet an event
        happened on: ``db.tech_bonuses`` is one dict per player and R5.1 says
        the bonuses that apply are the ones of the planet the player is
        standing on. Only the arrival trigger names it — the planet being
        arrived at *is* the occupied planet, already known — and every building
        trigger leaves it defaulted, because a lab completing or falling on a
        planet the owner is not standing on must not re-scope the dict to that
        planet.

        Guarded exactly like :meth:`_unassign_branch_roles`: no TechLabSystem
        injected is a logged no-op (R15.2), and a failure inside the rebuild is
        logged rather than raised into the publisher (R15.3).
        """
        if player is None:
            return
        tech = self._tech_system
        if tech is None:
            logger.debug(
                "Branch bonus recompute skipped: no TechLabSystem injected "
                "(R15.2)"
            )
            return
        recompute = getattr(tech, "recompute_tech_bonuses", None)
        if not callable(recompute):
            logger.debug(
                "Branch bonus recompute skipped: the injected TechLabSystem "
                "exposes no recompute_tech_bonuses"
            )
            return
        try:
            recompute(player, planet)
        except Exception:  # noqa: BLE001 - a rebuild never breaks the event bus
            logger.debug(
                "Branch bonus recompute failed for %r on %r", player, planet,
                exc_info=True,
            )

    # ------------------------------------------------------------------ #
    #  Reinstatement bookkeeping — the ONLY persisted player state (R5.5,
    #  R5.9, R15.5). This module is the single writer of both attributes.
    # ------------------------------------------------------------------ #

    def _mark_abandoned(self, player: Any, branch: str) -> None:
        """Record that *player* abandoned *branch* voluntarily (R5.5).

        The one write on the way *out*, made from
        :meth:`on_building_demolished` and from nowhere else — a hostile
        destruction must reach this method by no path at all, because recording
        nothing is precisely what makes a rebuilt-after-an-attack lab restore its
        Branch with no research (R5.9).

        Read-copy-write (R14.7): the stored mapping is read by value, a **copy**
        is mutated, and the whole container is written back. Mutating what the
        read handed over would be discarded by a real Evennia attribute holding a
        serialized container, so the copy is not defensive politeness — it is the
        only thing that makes the write persist.

        Idempotent: marking a Branch already marked leaves the same mapping, so a
        double-fire of the demolish trigger costs nothing. Other Branches' bits
        are carried through untouched, so a player who walked away from two
        Branches owes Reinstatement on both.

        The mapping is keyed by Branch alone and is **not** planet-scoped, unlike
        a commitment (R3.7). That is deliberate and follows the thing it gates:
        the researched record it seeds an exclusion set from is per-player, so a
        per-planet bit would have nothing per-planet to exclude.
        """
        if player is None:
            return
        wanted = self._clean(branch)
        if wanted is None:
            return
        abandoned = self._attr_mapping(player, ATTR_BRANCH_ABANDONED)
        abandoned[wanted] = True
        self._write_player_attr(player, ATTR_BRANCH_ABANDONED, abandoned)

    def _seed_reinstatement(self, player: Any, branch: str) -> None:
        """Turn *branch*'s abandoned bit into a pending Reinstatement set (R5.5).

        The one write on the way *back*, made from
        :meth:`on_construction_completed` when a Branch_Lab finishes. The bit is
        the whole decision:

        * **Bit set** — the owner walked away from this Branch, so every recorded
          technology in it needs its reduced-cost Reinstatement job before its
          effect applies again (R5.5). The pending set is seeded from the record,
          and the bit is cleared: one abandonment costs one round of Reinstatement,
          not one per lab ever built afterwards.
        * **Bit absent** — the lab was lost to an attack, or this is a first
          commitment, or a completed *upgrade* of a lab already standing. Nothing
          is written at all, so the Branch's effects return on the next recompute
          with no research (R5.9).

        The pending list is seeded from the owner's **recorded** technologies
        filtered to this Branch, and the record itself is never touched: dormancy
        suspends effects and erases no history (R5.3), which is also what lets the
        technology view report the count of recorded technologies in a dormant
        Branch. A previous pending list for the same Branch is *replaced* rather
        than merged, because R5.5 asks for a job per recorded technology and the
        record is what it asks about.

        Write order is deliberate: the pending set lands **before** the bit is
        cleared, so a failure between the two leaves the bit set and the next
        completion seeds again. The reverse order could drop the Reinstatement
        cost entirely, which is the one outcome worth guarding against.

        Both writes go through read-copy-write (R14.7); see
        :meth:`_mark_abandoned`.
        """
        if player is None:
            return
        wanted = self._clean(branch)
        if wanted is None:
            return
        abandoned = self._attr_mapping(player, ATTR_BRANCH_ABANDONED)
        if not abandoned.pop(wanted, False):
            return                                        # R5.9 — nothing to do
        pending = self._attr_mapping(player, ATTR_BRANCH_REINSTATEMENT)
        pending[wanted] = sorted(
            key
            for key in self._recorded_technologies(player)
            if self.branch_of_technology(key) == wanted
        )
        self._write_player_attr(player, ATTR_BRANCH_REINSTATEMENT, pending)
        self._write_player_attr(player, ATTR_BRANCH_ABANDONED, abandoned)

    def reinstatement_pending(self, player: Any, tech_key: str) -> bool:
        """Return True when *tech_key* is still awaiting its Reinstatement job.

        The question ``TechLabSystem.start_research`` asks before refusing a
        recorded technology as "already researched" (R5.7): a key
        :meth:`_seed_reinstatement` put in the pending set is **reinstatable**
        rather than done, and re-researching it at the reduced cost is the whole
        point of the job. Its answer is the same one
        :meth:`applied_technologies` and the unlock gate read, so the research
        job, the bonus dict, and the gate cannot disagree about which keys are
        still owed.

        Scoped by the **technology's own Branch** rather than by a commitment, so
        this answers about the *key* and not about where the player stands. The
        research job's tree gate is what requires that Branch's lab to be owned,
        and asking the same question twice is how two answers start to differ.

        Returns:
            ``False`` for a blank or unknown key, for a player with nothing
            pending, and for any input this system cannot read (R15.3) — an
            unresolvable answer leaves the pre-feature "already researched"
            refusal standing rather than opening a discounted job.
        """
        key = self._clean(tech_key)
        if key is None:
            return False
        return key in self._pending_reinstatement(
            player, self.branch_of_technology(key)
        )

    def on_reinstatement_completed(self, player: Any, tech_key: str) -> bool:
        """Clear *tech_key* from the pending set: its job finished (R5.7).

        The third and last write of the Reinstatement bookkeeping, and the reason
        it lives here rather than in the system that runs the research: this
        module is the single writer of ``db.branch_reinstatement`` (R15.5), so
        ``TechLabSystem`` **asks** on completion instead of assigning the
        attribute itself. The two systems keep their own state — the researched
        record and ``db.tech_bonuses`` stay TechLabSystem's, and it rebuilds the
        latter right after this returns, so a reinstated effect lands at the same
        moment a first-time research effect would.

        The Branch is derived from the technology rather than passed in, so a
        caller cannot clear a key out of another Branch's list. A Branch left
        with nothing pending drops out of the mapping entirely, which keeps
        "everything reinstated" and "never abandoned" the same stored shape —
        both read as nothing pending (R14.8).

        Idempotent: clearing a key already gone changes nothing and reports
        ``False``, so a double-fire of a completion costs one read.

        Read-copy-write (R14.7); see :meth:`_mark_abandoned`.

        Args:
            player: The owner whose pending set to clear.
            tech_key: The technology whose Reinstatement job completed.

        Returns:
            ``True`` when a key was actually cleared; ``False`` when there was
            nothing to clear — an unknown key, a technology in no resolvable
            Branch, a player with nothing pending — and for any input this
            system cannot read at all (R15.3).
        """
        if player is None:
            return False
        key = self._clean(tech_key)
        if key is None:
            return False
        branch = self.branch_of_technology(key)
        if branch is None:
            return False
        pending = self._attr_mapping(player, ATTR_BRANCH_REINSTATEMENT)
        remaining = self._clean_keys(pending.get(branch))
        if key not in remaining:
            return False
        remaining.discard(key)
        if remaining:
            pending[branch] = sorted(remaining)
        else:
            pending.pop(branch, None)
        self._write_player_attr(player, ATTR_BRANCH_REINSTATEMENT, pending)
        return True

    def _attr_mapping(self, player: Any, key: str) -> dict:
        """Return a MUTABLE copy of the mapping stored at *key*, or ``{}``.

        The read half of the read-copy-write discipline (R14.7): the caller gets
        a container it owns, so mutating it can never reach — or fail to reach —
        the stored value by accident. The copy is unconditional and the caller
        always writes the whole thing back.

        Only a mapping is honored. An absent attribute, an empty one, and a
        hand-edited value of the wrong shape entirely all collapse to the
        documented default ``{}`` (R14.8) rather than raising into a trigger
        (R15.3) — and because the caller writes back what this returned, a
        garbage value is *replaced* by a well-formed mapping rather than being
        propagated.
        """
        raw = self._player_attr(player, key)
        if not raw:
            return {}
        items = getattr(raw, "items", None)
        if not callable(items):
            return {}
        try:
            return dict(items())
        except (AttributeError, TypeError, ValueError):
            return {}

    @staticmethod
    def _write_player_attr(player: Any, key: str, value: Any) -> None:
        """Write one persisted player attribute, swallowing any failure.

        The write half of the read-copy-write discipline (R14.7), and the single
        choke point for both attributes this feature introduces (R15.5) — so
        "who writes Branch player state" has exactly one answer, and a guard test
        can assert no other module assigns them.

        Delegates to :func:`world.utils.set_obj_attr`, which prefers the
        ``attributes`` handler and falls back to the ``db`` proxy, so a real
        Character, an NPC sentinel, and a test fake are all written alike. The
        import is function-local so this module stays importable with the game
        framework absent (R15.1), and a failed write is logged rather than raised
        into the event bus (R15.3).
        """
        from world.utils import set_obj_attr

        try:
            set_obj_attr(player, key, value)
        except Exception:  # noqa: BLE001 - a failed write never breaks a trigger
            logger.debug(
                "Branch state write of %r failed for %r", key, player, exc_info=True
            )

    @contextmanager
    def _ignoring(self, building: Any) -> Iterator[None]:
        """Treat *building* as already gone for the duration of the block.

        ``BUILDING_DESTROYED`` fires before the delete (so the payload is still
        readable — that is the point of the ordering), which leaves a subscriber
        looking at a roster that still contains the building it is reacting to
        the loss of. Rather than reimplementing the owned-lab scan with an
        exclusion argument, the one lookup that matters
        (:meth:`_owned_lab`) consults this scope.

        The previous value is saved and restored, so nesting is safe, and the
        scope is always closed — including on an exception — so nothing leaks
        into the next event. Between events the field is ``None``, which keeps
        the "derive, do not store" invariant intact: this is a transient view of
        one in-flight deletion, not a cached answer.
        """
        previous = self._ignored_building
        self._ignored_building = building
        try:
            yield
        finally:
            self._ignored_building = previous

    @staticmethod
    def _is_same_building(left: Any, right: Any) -> bool:
        """Return True when *left* and *right* are the same building.

        Identity first, then a matching non-``None`` primary key, because an
        owner's roster may hand back a *different Python object* for the same
        database row than the one an event payload carries — the same comparison
        ``ShieldSystem`` and ``BaseElimination`` make for the same reason. Two
        keyless objects are the same only by identity, and ``None`` matches
        nothing.
        """
        if left is None or right is None:
            return False
        if left is right:
            return True
        left_id = getattr(left, "id", None)
        right_id = getattr(right, "id", None)
        return left_id is not None and left_id == right_id

    def _event_planet(self, building: Any, tile: Any, owner: Any) -> Any:
        """Resolve the planet a building event happened on, or ``None``.

        The building's own planet, then the payload tile's, then the planet the
        owner occupies — three reads because a building event may carry any one
        of them and a planet-scoped commitment needs the answer. ``None`` remains
        the "any planet" wildcard the estate and commitment queries document, so
        an unresolvable planet degrades to the widest scope rather than to a
        skipped trigger.
        """
        planet = self._building_planet(building)
        if planet is not None:
            return planet
        planet = self._tile_planet(tile)
        if planet is not None:
            return planet
        return self._player_planet(owner)

    # ------------------------------------------------------------------ #
    #  Construction gates (R3.3-3.5, R4.1-4.2, R4.8, R6.2-6.3, R13.4)
    # ------------------------------------------------------------------ #

    def construction_validators(self) -> list:
        """Return the three Branch construction gates, in chain order.

        ``BuildingSystem`` appends these to its existing ordered
        ``_validate_construction`` chain in one call, so it never imports this
        module and this module never reaches into it. Each callable has the
        signature the chain's own validators have —
        ``(player, building_def, tile, x=None, y=None) -> str | None`` — so the
        splice is mechanical:

        .. code-block:: python

            lambda: gate(player, building_def, tile, x=x, y=y)

        Order matters and is the design's: affiliation, then switch, then
        unlock. All three sit above ``_validate_resources`` in the chain, which
        is what makes "the report precedes the charge" structural rather than a
        convention (R4.8, R13.4).

        Every gate answers a **message key** carrying structured data
        (:class:`BranchRefusal`) or ``None``, and composes no prose (R13.5).

        Returns:
            A fresh list of three bound callables, so a caller may reorder or
            filter its own copy without touching this system.
        """
        return [
            self._validate_branch_affiliation,
            self._validate_branch_switch,
            self._validate_unlock_technology,
        ]

    def _validate_branch_affiliation(
        self,
        player: Any,
        building_def: Any,
        tile: Any = None,
        x: int | None = None,
        y: int | None = None,
    ) -> "BranchRefusal | None":
        """Gate a Branch_Building on the owner's matching Branch_Commitment.

        R3.3: a Branch_Building may be constructed only while the owner's
        Branch_Commitment on the target planet equals that building's
        Branch_Affiliation. The two refusals report what the player needs:

        * **No commitment at all** — ``branch_lab_required``, naming the
          Branch_Lab that unlocks the building (R3.4).
        * **A different commitment** — ``branch_mismatch``, naming both the
          Branch the player holds and the Branch the building requires (R3.5).

        Two classes of building pass untouched:

        * A **Neutral_Building** (no ``branch``) — every building shipped before
          this feature, so their construction is unaffected (R2.5).
        * A **Branch_Lab** — a lab is what *creates* a commitment, so gating it
          on already holding one would make the first commitment impossible.
          The lab's own question ("what must I tear down to switch") belongs to
          :meth:`_validate_branch_switch`.

        Args:
            player: The requesting owner.
            building_def: The requested building's definition, or its
                abbreviation — both resolve (:meth:`_building_def`).
            tile: The target tile, read only for the planet it stands on.
            x: Target coordinate, accepted for chain-signature compatibility.
                The planet does not depend on it.
            y: As *x*.

        Returns:
            A :class:`BranchRefusal` (a truthy message key), or ``None`` when
            the construction passes this gate — including for every input this
            system cannot resolve, so an unreadable definition never blocks a
            build and never raises (R15.3).
        """
        bdef = self._building_def(building_def)
        if bdef is None or self._is_lab(bdef):
            return None
        required = self._clean(getattr(bdef, "branch", None))
        if required is None:
            return None                                   # Neutral_Building
        planet = self._target_planet(player, tile, x=x, y=y)
        held = self.commitment(player, planet)
        if held == required:
            return None
        data = {
            "building": self._clean(getattr(bdef, "abbreviation", None)),
            "building_name": self._clean(getattr(bdef, "name", None)),
            "planet": planet,
            "required_branch": required,
            "required_doctrine": BRANCH_DOCTRINE.get(required),
            "required_lab": self.lab_for_branch(required),
            "required_lab_name": self._clean(
                getattr(self._lab_def_for_branch(required), "name", None)
            ),
            "current_branch": held,
            "current_doctrine": BRANCH_DOCTRINE.get(held) if held else None,
        }
        if held is None:
            return BranchRefusal(MSG_BRANCH_LAB_REQUIRED, **data)   # R3.4
        return BranchRefusal(MSG_BRANCH_MISMATCH, **data)           # R3.5

    def _validate_branch_switch(
        self,
        player: Any,
        building_def: Any,
        tile: Any = None,
        x: int | None = None,
        y: int | None = None,
    ) -> "BranchRefusal | None":
        """Gate a Branch_Lab on the conflicting Branch_Estates being empty.

        Fires only for a Branch_Lab — every other building is somebody else's
        question. Two outcomes, and both land before ``_validate_resources``
        runs, so the player is told what a switch costs before a single resource
        is charged (R4.8, R13.4):

        * **A conflicting estate still stands** — ``branch_switch_blocked``,
          reporting the count of buildings that remain (R4.1) and, for each one,
          its abbreviation and coordinates (R4.2). The payload also carries the
          dormancy figures below, so the one report covers both halves of R13.4.
        * **Nothing stands in the way** — the gate passes, and when the player
          holds recorded technologies outside the incoming Branch it *reports*
          how many would go dormant. A report is not a refusal, so it cannot
          travel up the chain's single ``str | None`` channel; it is published as
          a structured notification instead (R13.5), which is also the only
          channel that needs no cooperation from ``BuildingSystem``.

        On the dormancy figures: R4.8 phrases the trigger as the player's
        commitment changing, but a commitment *is* the owned lab, and that lab is
        itself a member of its Branch's estate — so while a player still holds an
        outgoing commitment the conflicting estate is never empty (and the
        pre-existing one-lab-per-planet gate has already refused). The reachable
        and useful reading, which is the one implemented, is the record the
        player keeps: every recorded technology outside the incoming Branch,
        grouped by Branch, is inert under the incoming commitment. When an
        outgoing commitment *is* still readable it is named as
        ``outgoing_branch`` as well.

        Args:
            player: The requesting owner.
            building_def: The requested lab's definition or abbreviation.
            tile: The target tile, read only for the planet it stands on.
            x: Accepted for chain-signature compatibility; unused.
            y: As *x*.

        Returns:
            A :class:`BranchRefusal`, or ``None`` when the switch is permitted
            (R4.3) and for every unresolvable input (R15.3).
        """
        bdef = self._building_def(building_def)
        if bdef is None or not self._is_lab(bdef):
            return None
        # The Branch a lab HOSTS is ``research_tree``; ``branch`` is the fallback
        # for a lab declaring only the affiliation, exactly as in ``commitment``.
        incoming = self._hosted_branch_of_def(bdef)
        if incoming is None or incoming not in BRANCHES:
            return None
        planet = self._target_planet(player, tile, x=x, y=y)
        outgoing = self.commitment(player, planet)
        dormant = self._dormant_technologies(player, incoming)
        dormant_count = sum(len(keys) for keys in dormant.values())
        report = {
            "lab": self._clean(getattr(bdef, "abbreviation", None)),
            "lab_name": self._clean(getattr(bdef, "name", None)),
            "planet": planet,
            "incoming_branch": incoming,
            "incoming_doctrine": BRANCH_DOCTRINE.get(incoming),
            "outgoing_branch": outgoing if outgoing != incoming else None,
            "outgoing_doctrine": (
                BRANCH_DOCTRINE.get(outgoing) if outgoing and outgoing != incoming
                else None
            ),
            "dormant_count": dormant_count,
            "dormant_counts": {
                branch: len(keys) for branch, keys in dormant.items()
            },
            "dormant_technologies": {
                branch: list(keys) for branch, keys in dormant.items()
            },
        }
        conflicts = self.conflicting_estates(player, planet, incoming)
        if conflicts:
            blocking = [
                self._blocking_entry(building, branch)
                for branch, members in conflicts.items()
                for building in members
            ]
            return BranchRefusal(
                MSG_BRANCH_SWITCH_BLOCKED,
                count=len(blocking),                      # R4.1
                branches=list(conflicts),
                counts={
                    branch: len(members) for branch, members in conflicts.items()
                },
                blocking=blocking,                        # R4.2
                **report,
            )
        if dormant_count:
            # R4.8 / R13.4: reported from inside the validation chain, which runs
            # above ``_validate_resources``, so it precedes any charge.
            self._publish(player, NOTIFY_BRANCH_DORMANCY, report)
        return None

    def _validate_unlock_technology(
        self,
        player: Any,
        building_def: Any,
        tile: Any = None,
        x: int | None = None,
        y: int | None = None,
    ) -> "BranchRefusal | None":
        """Gate a building on its unlocking technology being researched AND live.

        R6.2 is two conditions, not one: the owner's record must contain the
        named technology *and* that technology's effects must currently be
        applied — its Branch committed on the target planet, and no Reinstatement
        job still pending for it. A building unlocked by a technology whose
        bonuses are inert would be a doctrine capability earned in a doctrine the
        player has left.

        The refusal is one key, ``branch_unlock_required``, reporting the
        technology's key and name and the Branch that hosts it (R6.3), plus a
        ``reason`` naming which condition failed
        (:data:`UNLOCK_NOT_RESEARCHED`, :data:`UNLOCK_DORMANT`,
        :data:`UNLOCK_REINSTATEMENT_PENDING`) so a renderer can be specific
        without the gate composing a sentence.

        A building declaring no ``unlock_technology`` — every building shipped
        before this feature — passes untouched (R6.1).

        Args:
            player: The requesting owner.
            building_def: The requested building's definition or abbreviation.
            tile: The target tile, read only for the planet it stands on. The
                planet matters because a technology's effects are applied per
                planet, exactly as a commitment is.
            x: Accepted for chain-signature compatibility; unused.
            y: As *x*.

        Returns:
            A :class:`BranchRefusal`, or ``None`` when the gate passes and for
            every unresolvable input (R15.3).
        """
        bdef = self._building_def(building_def)
        if bdef is None:
            return None
        required = self._clean(getattr(bdef, "unlock_technology", None))
        if required is None:
            return None                                   # ungated by research
        planet = self._target_planet(player, tile, x=x, y=y)
        reason = self._unapplied_reason(player, required, planet)
        if reason is None:
            return None
        hosting = self.branch_of_technology(required)
        tdef = self._technology_def(required)
        return BranchRefusal(
            MSG_BRANCH_UNLOCK_REQUIRED,
            reason=reason,
            building=self._clean(getattr(bdef, "abbreviation", None)),
            building_name=self._clean(getattr(bdef, "name", None)),
            planet=planet,
            technology=required,
            technology_name=self._clean(getattr(tdef, "name", None)),
            branch=hosting,                               # R6.3
            doctrine=BRANCH_DOCTRINE.get(hosting) if hosting else None,
            lab=self.lab_for_branch(hosting) if hosting else None,
            lab_name=self._clean(
                getattr(self._lab_def_for_branch(hosting), "name", None)
            ) if hosting else None,
        )

    # ------------------------------------------------------------------ #
    #  Shared vector services — carrier eligibility (R7.1, R7.5)
    # ------------------------------------------------------------------ #

    def eligible_carrier(
        self, player: Any, role: str, planet: Any = None
    ) -> Any:
        """Return one of *player*'s agents eligible to carry an operation in *role*.

        Every Vector_Operation needs a Carrier_Agent, so no Vector_Operation
        resolves without one (R7.1) — this is the query that decides whether the
        player has a body to send, and a ``None`` here is what the driver's
        carrier check refuses on.

        Eligibility is the **conjunction of exactly four conditions** (R7.5), and
        the four are read independently so no two collapse into one:

        1. **alive** — ``is_alive()`` when the agent exposes it, otherwise
           ``hp > 0``. An agent that reports neither is taken to be alive, since
           nothing says it is dead;
        2. **assigned to the role the Operation_Kind requires** — compared
           case-insensitively against the stored ``db.role``, so the role table's
           vocabulary and a hand-set attribute agree;
        3. **active outside reserve** — a benched agent does no work, exactly as
           the per-tick sweep already treats it;
        4. **free of incapacitation**.

        The scan is the owner's own roster order, so the answer is deterministic:
        the first eligible agent, not an arbitrary one.

        Args:
            player: The owner whose roster to search.
            role: The role the Operation_Kind requires, from
                :meth:`role_for_branch`.
            planet: The planet the operation happens on. Defaults to the planet
                *player* occupies, because an operation is per-planet like
                everything else here. An agent whose own planet cannot be read
                counts on every planet, matching the estate scan; a *requested*
                planet that cannot be read is the "any planet" wildcard.

        Returns:
            The first eligible agent, or ``None`` — for a blank role, an owner
            with no matching agent, no injected AgentSystem at all (R15.2), and
            every input this system cannot read (R15.3). ``None`` is a refusal,
            never a raise, because the caller is a request path.
        """
        wanted = self._clean(role)
        if player is None or wanted is None:
            return None
        if planet is None:
            planet = self._player_planet(player)
        for agent in self._owned_agents(player):
            db = getattr(agent, "db", None)
            if db is None:
                continue
            assigned = self._clean(self._safe_attr(db, "role"))
            if assigned is None or assigned.lower() != wanted.lower():
                continue
            agent_planet = self._safe_attr(db, "coord_planet")
            if planet is not None and agent_planet not in (None, planet):
                continue
            if self._safe_attr(db, "reserve"):
                continue
            if self._safe_attr(db, "incapacitated"):
                continue
            if not self._agent_is_alive(agent, db):
                continue
            return agent
        return None

    def _owned_agents(self, player: Any) -> list:
        """Return *player*'s agent roster, or ``[]``.

        Asks the **injected** AgentSystem, the roster's owner, rather than
        walking the object database — so eligibility answers about the same
        roster every other agent path does. No AgentSystem injected, or one that
        predates ``get_agents``, degrades to an empty roster with a log (R15.2)
        rather than an error, which reads downstream as "no eligible carrier".
        """
        agents = self._agent_system
        if agents is None:
            logger.debug(
                "Carrier lookup skipped: no AgentSystem injected (R15.2)"
            )
            return []
        roster = getattr(agents, "get_agents", None)
        if not callable(roster):
            logger.debug(
                "Carrier lookup skipped: the injected AgentSystem exposes no "
                "get_agents"
            )
            return []
        try:
            return list(roster(player) or ())
        except Exception:  # noqa: BLE001 - an unreadable roster owns no carrier
            logger.debug("agent roster lookup failed for %r", player, exc_info=True)
            return []

    @classmethod
    def _agent_is_alive(cls, agent: Any, db: Any) -> bool:
        """Return True unless *agent* is readably dead.

        ``is_alive()`` is preferred — the existing CombatEntity predicate, so a
        real agent is judged by the same rule combat judges it by — with
        ``hp > 0`` as the fallback for an object that exposes only the
        attribute. An agent that exposes neither is taken to be alive: this
        conjunct exists to exclude a corpse, not to require proof of life from a
        minimal object.
        """
        checker = getattr(agent, "is_alive", None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception:  # noqa: BLE001 - fall through to the hp read
                logger.debug("is_alive failed for %r", agent, exc_info=True)
        hp = cls._safe_attr(db, "hp")
        if hp is None:
            return True
        try:
            return float(hp) > 0
        except (TypeError, ValueError):
            return True

    # ------------------------------------------------------------------ #
    #  Shared vector services — charge and refund (R12.2, R12.3, R8.6)
    # ------------------------------------------------------------------ #

    def charge(self, player: Any, cost: Any) -> bool:
        """Charge *cost* to *player*, whole or not at all (R12.2).

        Delegates to the character's existing ``has_resources`` /
        ``deduct_resources`` pair — which already checks sufficiency first and
        returns ``False`` without mutating anything — so the whole-or-none
        guarantee is the one the rest of the game already relies on rather than
        a second implementation of resource math that could drift from it. The
        explicit ``has_resources`` call ahead of the deduction is what lets a
        refusal be reported *before* anything is touched (R12.3, through
        :meth:`resource_shortfall`).

        An **empty or absent cost is a no-op that succeeds**, which is what
        makes an NPC-originated operation free rather than special-cased
        (R12.6): the caller hands over ``{}`` and this returns ``True`` having
        written nothing.

        Args:
            player: The payer.
            cost: ``{resource: amount}``. Non-positive and unreadable lines are
                dropped (:meth:`_clean_cost`), so a hand-edited zero cannot
                become a refund line later.

        Returns:
            ``True`` when the whole cost was charged (or there was nothing to
            charge); ``False`` when the player cannot pay, exposes no resource
            methods, or cannot be read at all — never a raise (R15.3). On
            ``False`` the player's resources are exactly as they were.
        """
        lines = self._clean_cost(cost)
        if not lines:
            return True
        if player is None:
            return False
        has = getattr(player, "has_resources", None)
        deduct = getattr(player, "deduct_resources", None)
        if not callable(has) or not callable(deduct):
            logger.debug("charge skipped: %r exposes no resource methods", player)
            return False
        try:
            if not has(lines):
                return False
            return bool(deduct(lines))
        except Exception:  # noqa: BLE001 - a failed charge is a refusal
            logger.debug("charge of %r failed for %r", lines, player, exc_info=True)
            return False

    def refund(self, player: Any, cost: Any) -> None:
        """Add every line of *cost* back to *player* (R8.6).

        The other half of charge-then-enter-Pending: an operation that charged
        and then failed to become Pending returns the **full** charged amount,
        so no operation both charges and fails.

        Each line is added through the character's own ``add_resource``, and each
        is guarded independently — one unreadable line never swallows the rest of
        the refund, which is the direction of error that matters when the player
        is owed the resources.

        Returns:
            ``None`` always. An absent ``add_resource``, an empty cost, and an
            unreadable player are all logged no-ops rather than raises (R15.3).
        """
        lines = self._clean_cost(cost)
        if not lines or player is None:
            return
        add = getattr(player, "add_resource", None)
        if not callable(add):
            # Warning, not debug: the player is OWED these resources, and a
            # refund that silently went nowhere is the one direction of error
            # an operator needs to see.
            logger.warning(
                "refund of %r skipped: %r exposes no add_resource", lines, player
            )
            return
        for resource, amount in lines.items():
            try:
                add(resource, amount)
            except Exception:  # noqa: BLE001 - one line never strands the rest
                logger.warning(
                    "refund of %s %s failed for %r", amount, resource, player,
                    exc_info=True,
                )

    def resource_shortfall(self, player: Any, cost: Any) -> dict[str, dict[str, int]]:
        """Return the have-and-need breakdown of *cost* for *player* (R12.3).

        The structured form of the shared "insufficient resources" report every
        cost gate in the game shows: **every** required resource, not only the
        short ones, so a player reads one complete list of what the operation
        needs and what they hold. Structured data only — the presenter and the
        command layer compose the words (R13.5).

        Returns:
            ``{resource: {"have": int, "need": int}}`` in the cost's own order,
            empty for an empty cost and for a player whose resources cannot be
            read at all (R15.3).
        """
        lines = self._clean_cost(cost)
        if not lines or player is None:
            return {}
        reader = getattr(player, "get_resource", None)
        if not callable(reader):
            return {}
        breakdown: dict[str, dict[str, int]] = {}
        for resource, amount in lines.items():
            try:
                have = int(reader(resource) or 0)
            except Exception:  # noqa: BLE001 - an unreadable stock reads as none
                have = 0
            breakdown[resource] = {"have": have, "need": amount}
        return breakdown

    @staticmethod
    def _clean_cost(cost: Any) -> dict[str, int]:
        """Return *cost* as ``{resource: positive int}``, or ``{}``.

        The one normalizer both halves of the charge path share, so a cost that
        is charged is exactly the cost that would be refunded. A ``None``, a
        non-mapping, a blank resource name, and a non-positive or non-numeric
        amount all drop out rather than reaching the resource methods — which is
        what stops a zero line from becoming a phantom refund.
        """
        if not cost:
            return {}
        items = getattr(cost, "items", None)
        if not callable(items):
            return {}
        try:
            pairs = list(items())
        except (AttributeError, TypeError, ValueError):
            return {}
        lines: dict[str, int] = {}
        for resource, amount in pairs:
            if not isinstance(resource, str) or not resource.strip():
                continue
            try:
                value = int(amount)
            except (TypeError, ValueError):
                continue
            if value > 0:
                lines[resource.strip()] = value
        return lines

    # ------------------------------------------------------------------ #
    #  Shared vector services — targeting (R10.4, R10.7, R11.8, R11.9)
    # ------------------------------------------------------------------ #

    def may_target(
        self, actor: Any, target: Any, hostile: bool = True
    ) -> "BranchRefusal | None":
        """Return why *actor* may not act on *target*, or ``None`` when they may.

        The three protection gates every Vector_Operation passes through, folded
        into one answer so all six vectors share one implementation of them and
        the driver's target check has a single call to make:

        * **The new-player shield** (R10.4) — a hostile operation against a
          player below ``new_player_vector_shield_level`` is refused, reporting
          the level at which that target becomes valid.
        * **The allied-target refusal** (R11.9) — a hostile operation naming an
          allied entity is refused, reporting the alliance that protects it.
        * **The support-consent check** (R11.8) — a *supporting* operation on an
          ally is performed only while that ally has consented to receive it,
          and the refusal reports the missing consent.
        * **The escalation cap** (R10.6) — a hostile operation is refused once
          this actor has already resolved ``escalation_cap`` operations against
          this target inside ``escalation_window_ticks``, reporting the ticks
          until a slot frees. See :meth:`escalation_remaining`.

        All four are applied to **alliance members, allies, and unaffiliated
        players on identical terms** (R10.7): the gates key on the relationship
        and the target's own level, never on whether the two are friendly, so
        alliance membership grants no exemption. (In this game an alliance member
        *is* an ally — one pointer, one predicate — so "members and allies on the
        same terms" is structural rather than a rule applied twice.)

        The shield is evaluated **before** the allied refusal so that a shielded
        target answers with the qualifying level whatever the two players'
        relationship is — the reading R10.7 asks for, since an alliance may not
        change what a gate reports any more than whether it fires. An allied
        target is still refused either way; only which of the two reasons the
        refusal carries depends on the order.

        The escalation cap is evaluated **last** for the reason the driver's whole
        check order follows: a refusal a player can act on now (train an agent,
        pick another target) is more useful than one that only says "wait", so the
        timing refusal goes behind the structural ones.

        Two shapes pass untouched, both deliberately:

        * **The actor's own entities.** A player may always act on what they
          own, so no shield, no alliance, and no consent stands between a player
          and their own base — including a brand-new player placing their first
          Trap on their own tile.
        * **A non-player target.** An NPC base's Sentinel and an unowned world
          object are no "target player", so the level shield does not reach
          them; they are the practice targets a new player is meant to have
          (R11.6).

        Args:
            actor: The requesting player.
            target: The operation's target — a player, or an entity whose
                ``owner`` is the player the gates protect. Both resolve.
            hostile: ``True`` for an operation aimed *at* the target (the
                stricter reading, so a caller that forgets gets the safe one),
                ``False`` for one performed *in support of* them.

        Returns:
            A :class:`BranchRefusal` — the message key plus every value the
            requirement asks be reported — or ``None`` when the operation may
            proceed, which is also the answer for every input this system cannot
            resolve: a lookup failure must never suppress legitimate targeting
            (R15.3).
        """
        protected = self._target_player(target)
        if protected is None or self._is_same_player(actor, protected):
            return None
        allied = self._are_allied(actor, protected)
        if not hostile:
            if not allied:
                return None                               # not a supported ally
            if self.has_consent(protected, CONSENT_SUPPORT, actor):
                return None
            return BranchRefusal(                         # R11.8
                MSG_VECTOR_CONSENT_REQUIRED,
                consent=CONSENT_SUPPORT,
                ally_name=self._name_of(protected),
                target_name=self._name_of(protected),
            )
        shield = self._balance_int("new_player_vector_shield_level")
        level = self._entity_level(protected)
        if shield > 0 and level is not None and level < shield:
            return BranchRefusal(                         # R10.4
                MSG_VECTOR_TARGET_SHIELDED,
                target_name=self._name_of(protected),
                target_level=level,
                required_level=shield,
            )
        if allied:
            alliance_id = self._alliance_id_of(protected)
            summary = self._alliance_summary(alliance_id) or {}
            return BranchRefusal(                         # R11.9
                MSG_VECTOR_TARGET_ALLIED,
                target_name=self._name_of(protected),
                alliance=alliance_id,
                alliance_name=summary.get("name"),
                alliance_tag=summary.get("tag"),
            )
        escalation = self._escalation_status(actor, protected)
        if escalation["remaining"] > 0:
            return BranchRefusal(                         # R10.6
                MSG_VECTOR_ESCALATION_LIMIT,
                target_name=self._name_of(protected),
                remaining_ticks=escalation["remaining"],
                count=escalation["count"],
                cap=escalation["cap"],
                window=escalation["window"],
            )
        return None

    # ------------------------------------------------------------------ #
    #  Consent — the second (and last) persisted player state (R11.8, R11.11,
    #  R15.5). This module is the single writer of ``db.vector_consent``.
    # ------------------------------------------------------------------ #

    def has_consent(self, player: Any, kind: str, other: Any) -> bool:
        """Return True when *player* has granted *other* a consent of *kind*.

        Read on the **consenting** player, because the consent is theirs to give
        (R11.8), and only honored **while the two are still allied**: consent is
        a permission inside an alliance, so leaving one ends it whether or not
        the stored entry has been cleared yet. That makes
        :meth:`revoke_alliance_consents` a housekeeping write rather than the
        only thing standing between an ex-ally and a free pass (R11.11).

        Returns:
            ``False`` for an unknown consent kind, an unidentifiable player, a
            player who granted nothing, and every unreadable input (R15.3) — the
            absence of a consent is the default, so an operation needing one is
            refused rather than waved through.
        """
        wanted = self._clean(kind)
        if wanted not in CONSENT_KINDS:
            return False
        other_id = self._identity_of(other)
        if other_id is None or not self._are_allied(player, other):
            return False
        return bool(self._consent_map(player).get(wanted, {}).get(other_id))

    def grant_consent(self, player: Any, kind: str, other: Any) -> bool:
        """Record that *player* consents to *other* acting for them (R11.8).

        Read-copy-write (R14.7), like every persisted container here: the stored
        mapping is read by value, a **copy** is mutated, and the whole container
        is written back, because an Evennia attribute may hand back a serialized
        copy whose in-place mutation is discarded.

        Idempotent — granting a consent already granted writes the same mapping.
        The grant is deliberately **not** gated on the two being allied: a
        player may set the permission up before or after joining, and
        :meth:`has_consent` is what requires the alliance at read time.

        Returns:
            ``True`` when the consent is stored, ``False`` for an unknown kind,
            an unidentifiable *other*, and an unreadable player (R15.3).
        """
        return self._write_consent(player, kind, other, granted=True)

    def revoke_consent(self, player: Any, kind: str, other: Any) -> bool:
        """Withdraw a consent *player* granted *other*, returning success.

        The player-facing counterpart of :meth:`grant_consent`, and idempotent
        the same way: revoking a consent that was never granted changes nothing
        and reports ``False``. A kind left with no entries drops out of the
        mapping entirely, so "revoked everything" and "never consented" are the
        same stored shape — both read as no consent (R14.8).
        """
        return self._write_consent(player, kind, other, granted=False)

    def revoke_alliance_consents(self, player: Any, alliance_id: Any = None) -> int:
        """Revoke every consent between *player* and an alliance's members (R11.11).

        Called when a player leaves an alliance — the moment the permissions
        granted inside it stop meaning anything. Both directions are cleared,
        because a consent lives on whichever side granted it:

        * **The leaver's own store is emptied.** They now belong to no alliance,
          so every consent they had granted is with a former ally by definition.
        * **The remaining members' stores lose the leaver.** The roster is read
          from the injected AllianceSystem when it can answer; when it cannot,
          those entries stay behind but are already inert, because
          :meth:`has_consent` honors a consent only while the two are allied.

        Args:
            player: The departing member.
            alliance_id: The alliance being left, used to resolve the remaining
                roster. ``None`` skips that half and clears only the leaver's
                own store.

        Returns:
            The number of players whose stored consents changed, so a caller or
            a test reads the effect instead of inferring it. ``0`` when there was
            nothing to revoke and for every unreadable input (R15.3).
        """
        if player is None:
            return 0
        changed = 1 if self._clear_consents(player) else 0
        leaver_id = self._identity_of(player)
        if leaver_id is None:
            return changed
        for member in self._alliance_members(alliance_id):
            if self._is_same_player(member, player):
                continue
            if self._clear_consents(member, only=leaver_id):
                changed += 1
        return changed

    def on_alliance_member_left(
        self,
        event_name: str = "",
        player: Any = None,
        alliance_id: Any = None,
        **_kwargs: Any,
    ) -> None:
        """Revoke a departing member's consents (R11.11).

        The subscriber behind :meth:`revoke_alliance_consents`, wired in
        :meth:`_subscribe_consent_revocation`. ``AllianceSystem`` publishes the
        same event for a leave and for a kick, and both end the alliance
        relationship, so both revoke — the requirement is about the relationship
        ending, not about who ended it.

        Returns:
            ``None`` always; every failure is swallowed and logged, because a
            subscriber must never raise into the event bus (R15.3).
        """
        if player is None:
            return
        try:
            self.revoke_alliance_consents(player, alliance_id)
        except Exception:  # noqa: BLE001 - a revocation never breaks the bus
            logger.debug(
                "consent revocation failed for %r leaving %r", player, alliance_id,
                exc_info=True,
            )

    def _subscribe_consent_revocation(self, event_bus: Any) -> None:
        """Subscribe the one event that ends a consent (R11.11).

        Separate from :meth:`_subscribe_recompute_triggers` because it is a
        different concern: nothing about a bonus dict changes when a player
        leaves an alliance, and nothing about an alliance changes when a lab
        completes. Wired here rather than at the composition root, following the
        convention every event-driven system in this codebase uses.

        The import is function-local so this module stays importable with the
        game framework absent (R15.1), and the whole thing is a no-op for an
        event bus that cannot subscribe, so a minimal test double stays usable
        (R15.3).
        """
        subscribe = getattr(event_bus, "subscribe", None)
        if not callable(subscribe):
            return
        from world.event_bus import ALLIANCE_MEMBER_LEFT

        try:
            subscribe(ALLIANCE_MEMBER_LEFT, self.on_alliance_member_left)
        except Exception:  # noqa: BLE001 - an unwired trigger is not fatal
            logger.debug("consent-revocation subscription failed", exc_info=True)

    def _consent_map(self, player: Any) -> dict:
        """Return a MUTABLE copy of *player*'s consent store, normalized.

        The read half of the consent store's read-copy-write discipline
        (R14.7), and the one place its shape is enforced:
        ``{kind: {player_id: True}}``, holding only the known kinds and only
        truthy entries. A hand-edited value of any other shape collapses to the
        documented default ``{}`` (R14.8) — and because the caller writes back
        what this returned, a garbage value is *replaced* by a well-formed
        mapping rather than propagated.
        """
        raw = self._attr_mapping(player, ATTR_VECTOR_CONSENT)
        store: dict[str, dict] = {}
        for kind in CONSENT_KINDS:
            granted = raw.get(kind)
            items = getattr(granted, "items", None)
            if not callable(items):
                continue
            try:
                pairs = list(items())
            except (AttributeError, TypeError, ValueError):
                continue
            entries = {key: True for key, value in pairs if value}
            if entries:
                store[kind] = entries
        return store

    def _write_consent(
        self, player: Any, kind: str, other: Any, granted: bool
    ) -> bool:
        """Add or remove one consent entry, returning whether anything changed.

        The single write path for ``db.vector_consent`` (R15.5), so
        :meth:`grant_consent` and :meth:`revoke_consent` cannot drift apart on
        the stored shape. Read-copy-write (R14.7).
        """
        if player is None:
            return False
        wanted = self._clean(kind)
        if wanted not in CONSENT_KINDS:
            return False
        other_id = self._identity_of(other)
        if other_id is None:
            return False
        store = self._consent_map(player)
        entries = dict(store.get(wanted, {}))
        if granted:
            if entries.get(other_id):
                return True                               # already consented
            entries[other_id] = True
        elif not entries.pop(other_id, False):
            return False                                  # nothing to revoke
        if entries:
            store[wanted] = entries
        else:
            store.pop(wanted, None)
        self._write_player_attr(player, ATTR_VECTOR_CONSENT, store)
        return True

    def _clear_consents(self, player: Any, only: Any = None) -> bool:
        """Drop *player*'s consents — all of them, or only those naming *only*.

        The revocation write (R11.11). Returns ``True`` when the store actually
        changed, so :meth:`revoke_alliance_consents` can count the players it
        affected instead of guessing. Read-copy-write (R14.7), and a player with
        nothing stored is left untouched rather than being given an empty
        mapping, which keeps "revoked" and "never consented" one stored shape
        (R14.8).
        """
        store = self._consent_map(player)
        if not store:
            return False
        if only is None:
            self._write_player_attr(player, ATTR_VECTOR_CONSENT, {})
            return True
        changed = False
        for kind in list(store):
            entries = dict(store[kind])
            if entries.pop(only, None) is None:
                continue
            changed = True
            if entries:
                store[kind] = entries
            else:
                store.pop(kind, None)
        if changed:
            self._write_player_attr(player, ATTR_VECTOR_CONSENT, store)
        return changed

    # ------------------------------------------------------------------ #
    #  Shared vector services — the three limit ledgers (R8.19, R8.20,
    #  R10.6, R10.7). This module is the single writer of both persisted
    #  ones (R15.5); the third has nothing to persist.
    # ------------------------------------------------------------------ #

    def cooldown_remaining(self, building: Any, kind: str) -> int:
        """Return how many ticks *building* must wait to run *kind* again (R8.19).

        The cooldown is **per originating building per Operation_Kind**, stored on
        the building itself as ``db.vector_cooldowns = {kind: ready_at_tick}``, so
        two Branch_Buildings of one Branch cool down independently and a razed
        building takes its cooldown with it. The clock is the **injected** tick
        source, never a module-level call, so a test drives it and the system
        stays framework-free (R15.1).

        The answer is ``max(0, ready_at - now)``, which is exactly the figure the
        driver's cooldown refusal reports, and it is **clamped to the currently
        configured length**. That clamp matters for one real failure: the shipped
        tick source answers ``0`` when it cannot read the tick script, so a stored
        ``ready_at`` far in the future would otherwise read as a lockout of
        hundreds of ticks rather than of one cooldown. Clamping makes the ledger
        self-healing and never changes the normal reading, where
        ``ready_at - now`` is ``length - elapsed`` and so is already at most the
        length.

        Args:
            building: The originating Branch_Building. Anything unreadable, and a
                building that has never run this kind, answers ``0``.
            kind: The Operation_Kind, from
                :data:`~world.constants.OPERATION_KINDS`.

        Returns:
            The ticks remaining, ``0`` once the cooldown has elapsed — and ``0``
            for a blank kind, an unreadable building, a hand-edited ledger of the
            wrong shape, and a tick source that cannot answer (R15.3). A knob or a
            clock that cannot be read must not become an accidental refusal.
        """
        wanted = self._clean(kind)
        now = self._now()
        if wanted is None or now is None:
            return 0
        ready_at = self._cooldown_map(building).get(wanted)
        if ready_at is None:
            return 0
        length = self._kind_balance_int(wanted, "cooldown_field", "_cooldown_ticks")
        return max(0, min(ready_at - now, max(0, length)))

    def note_cooldown(self, building: Any, kind: str) -> None:
        """Start *kind*'s cooldown on *building*, from now (R8.19).

        Called by the driver the moment an operation is accepted, so the cooldown
        measures from the request rather than from the effect — a long-fused
        operation and an instant one throttle their originating building the same
        way.

        The length is read from the Balance_Config field the Operation_Kind
        registry **binds** to this kind (``cooldown_field``, falling back to the
        conventional ``<kind>_cooldown_ticks`` name), read on **every call** so an
        ``@reload`` retunes the next operation (R15.7).

        Read-copy-write (R14.7), through the same pair every persisted container
        here uses: the ledger is read by value, a **copy** is mutated, and the
        whole mapping is written back. Idempotent in shape — noting a kind twice
        simply moves that kind's ``ready_at`` forward — and the other kinds' entries
        are carried through untouched.

        A non-positive configured length still writes an entry, at ``now``, which
        reads back as elapsed: refreshing the marker is what lets a retune *down*
        to zero free a building immediately instead of leaving a stale clock
        behind.

        Returns:
            ``None`` always. An unreadable building, a blank kind, and a tick
            source that cannot answer are logged no-ops rather than raises
            (R15.3) — a ledger write must never break an accepted operation.
        """
        wanted = self._clean(kind)
        now = self._now()
        if building is None or wanted is None or now is None:
            return
        length = self._kind_balance_int(wanted, "cooldown_field", "_cooldown_ticks")
        ledger = self._cooldown_map(building)
        ledger[wanted] = now + max(0, length)
        self._write_player_attr(building, ATTR_VECTOR_COOLDOWNS, ledger)

    def in_flight_count(self, player: Any, kind: str, planet: Any = None) -> int:
        """Return how many *kind* operations *player* has in flight on *planet* (R8.20).

        The in-flight cap is the one limit of the three with **nothing to
        persist**: the non-terminal Operation_Records the Vector_System already
        tracks *are* the count, so this counts them rather than keeping a second
        tally that could drift from the records it is supposed to describe.

        The registered vector for *kind* is asked for its tracked records and each
        is read **duck-typed** — ``kind``, ``owner_ref``, ``planet``, ``state`` by
        name, through guarded reads. That keeps this module independent of the
        record type (which the Operation Contract owns, not the Branch_System) and
        keeps the vector free to hold records in whatever container it likes.

        A record counts when all four hold:

        1. its kind is *kind* — a record that cannot say is taken at its
           vector's word, since it is tracked *by* that vector;
        2. its state is **not** one of the four terminal states (R8.2). A record
           that cannot say counts: it is tracked, and a tracked record is in
           flight until it says otherwise;
        3. its owner is *player* — by object identity or by stored id, because a
           persisted ``owner_ref`` is a reference rather than the object;
        4. its planet is *planet*. A record whose planet cannot be read counts on
           every planet, matching the estate scan's own convention.

        Args:
            player: The owner whose operations to count.
            kind: The Operation_Kind, which also selects the vector.
            planet: The planet to scope the count to. Defaults to the planet
                *player* occupies, like every other per-planet answer here; a
                planet that stays unresolvable is the "any planet" wildcard.

        Returns:
            The count, and ``0`` for a kind no vector is registered for, a vector
            that cannot be read, a blank kind, and an unreadable player (R15.3) —
            an unanswerable count must not become an accidental refusal.
        """
        wanted = self._clean(kind)
        if player is None or wanted is None:
            return 0
        vector = self._vectors.get(wanted)
        if vector is None:
            return 0
        if planet is None:
            planet = self._player_planet(player)
        count = 0
        for record in self._tracked_records(vector):
            record_kind = self._clean(self._record_field(record, "kind"))
            if record_kind is not None and record_kind != wanted:
                continue
            if self._is_terminal_record(record):
                continue
            if not self._owner_matches(self._record_field(record, "owner_ref"), player):
                continue
            record_planet = self._record_field(record, "planet")
            if planet is not None and record_planet not in (None, planet):
                continue
            count += 1
        return count

    def in_flight_cap(self, kind: str) -> int:
        """Return the simultaneous-operation cap for *kind* (R8.20).

        Read from the Balance_Config field the Operation_Kind registry binds to
        this kind (``cap_field``, falling back to ``<kind>_max_in_flight``), on
        every call so an ``@reload`` retunes the next request (R15.7). The driver
        pairs it with :meth:`in_flight_count` and reports both figures in the
        refusal.

        Returns:
            The cap, or ``0`` when no cap is configured for *kind* — an absent
            field, a non-numeric one, a negative one, or a blank kind. **``0``
            means unbounded, not "refuse everything"**: an unreadable knob must
            not lock a player out of their own doctrine, so the in-flight check
            enforces nothing at all below ``1``.
        """
        return max(0, self._kind_balance_int(kind, "cap_field", "_max_in_flight"))

    def escalation_remaining(self, actor: Any, target: Any) -> int:
        """Return the ticks until *actor* may resolve on *target* again (R10.6).

        The escalation cap bounds how many hostile Vector_Operations one player
        may **resolve** against one target player inside a rolling window, so the
        ledger is a list of resolution ticks per target, stored on the attacker as
        ``db.vector_escalation = {target_id: [tick, ...]}`` and pruned to
        ``escalation_window_ticks`` on every read (see :meth:`_escalation_map`).
        Both knobs are read per call, so an ``@reload`` retunes the next request
        (R15.7).

        Keyed on **target identity and nothing else** (R10.7): the ledger knows
        nothing about alliances, so an alliance member, an ally, and an
        unaffiliated player are all throttled on identical terms, and joining an
        alliance neither clears an entry nor adds one. A target *entity* resolves
        to the player it belongs to, exactly as in :meth:`may_target`, so a
        player cannot spread the cap over one target's buildings.

        The figure is the ticks until the entry a slot is waiting on ages out —
        the oldest entry in the ordinary case, and after a cap retuned *downward*
        the one whose expiry actually brings the count back below the cap.

        Returns:
            ``0`` while the actor is under the cap — the answer that lets the
            operation proceed — and the remaining ticks once the cap is reached,
            always at least ``1`` in that case, because an entry inside the window
            has by definition not aged out. ``0`` too for a non-positive cap or
            window (no limit configured), an unidentifiable target, a tick source
            that cannot answer, and every unreadable input (R15.3).
        """
        return self._escalation_status(actor, target)["remaining"]

    def note_escalation(self, actor: Any, target: Any) -> None:
        """Record that a hostile operation of *actor*'s resolved on *target* (R10.6).

        Called when an operation **resolves**, not when it is requested, because
        the requirement bounds resolutions: an operation that is cancelled or
        expires costs its owner nothing against the cap.

        The entry is the current tick, appended to this target's list on the
        actor's own ledger through read-copy-write (R14.7). Every other target's
        list is carried through untouched, and this target's is pruned to the
        window as it is read, so the stored ledger cannot grow past the entries
        the window can hold.

        **Nothing is recorded while no limit is configured** (a cap or window at
        or below zero): an entry the cap can never read would enforce nothing,
        and — because the read-side prune needs a window — it would accumulate
        without bound for as long as the limit stayed off. Turning the limit on
        starts a clean count from that moment, which is also the fairest
        reading: resolutions made while no rule was in force are not held
        against anyone.

        Returns:
            ``None`` always. An unreadable actor, a target with no identity, and a
            tick source that cannot answer are logged no-ops rather than raises
            (R15.3) — this is called from a resolution path, which must not fail
            because a ledger could not be written.
        """
        now = self._now()
        target_id = self._identity_of(self._target_player(target))
        if actor is None or target_id is None or now is None:
            return
        cap = self._balance_int("escalation_cap")
        window = self._balance_int("escalation_window_ticks")
        if cap <= 0 or window <= 0:
            # No limit is configured, so there is nothing this entry could ever
            # enforce — and with no window the read-side prune never runs, so
            # recording here would grow the ledger without bound for as long as
            # the limit stays off. Entries already stored are left untouched
            # (a misconfigured knob must delete nobody's history); they are
            # pruned by the window the moment a limit is configured again.
            return
        ledger = self._escalation_map(actor, now, window)
        ledger[target_id] = sorted([*ledger.get(target_id, ()), now])
        self._write_player_attr(actor, ATTR_VECTOR_ESCALATION, ledger)

    # ------------------------------------------------------------------ #
    #  Shared vector services — the Counter_Web advantage and the
    #  Response_Window floor (R8.8, R9.4, R9.5). Both are arithmetic over a
    #  hot knob: neither reads nor writes any state.
    # ------------------------------------------------------------------ #

    def counter_multiplier(self, actor_branch: str, target_branch: str) -> float:
        """Return the Counter_Web advantage *actor_branch* holds over *target_branch*.

        **One lookup, one clamp, no accumulation (R9.4, R9.5).** The web names
        which Branches a Branch holds an advantage over; this asks it once for the
        one pair the operation is between and clamps the answer into
        ``[1.0, counter_advantage_cap]``. There is deliberately no loop over paths
        and no running product, so "advantage multipliers do not compound" (R9.5)
        is *structural* rather than enforced: a chain ``A -> B -> C`` cannot
        multiply, because the only question ever asked is whether the web names an
        edge from the actor's Branch straight to the target's, and the only answer
        is a single capped value. Out-degree is irrelevant for the same reason —
        a Branch holding two advantages still contributes exactly one of them to
        any one operation, because only one target Branch is ever involved.

        The cap is read from Balance_Config on **every call**, so an ``@reload``
        retunes the next operation (R15.7). The clamp's lower bound of ``1.0``
        means a mis-authored edge, an unreadable cap, and a cap retuned below
        ``1.0`` can only ever be neutral — never a penalty — and its upper bound
        is what keeps an advantage a change of magnitude rather than immunity
        (R9.4).

        Args:
            actor_branch: The Branch of the player running the operation.
            target_branch: The Branch of the player it is aimed at.

        Returns:
            Exactly ``1.0`` when the web names no edge from *actor_branch* to
            *target_branch* — which covers an empty or absent web, a Branch with
            no advantages, either Branch being blank or unreadable, and a
            registry that cannot answer (R15.3) — and otherwise the single edge
            magnitude clamped into ``[1.0, counter_advantage_cap]``.
        """
        actor = self._clean(actor_branch)
        target = self._clean(target_branch)
        if actor is None or target is None:
            return 1.0
        edges = self._counter_edges(actor)
        if target not in edges:
            return 1.0                                    # the web names no edge
        cap = self._balance_float("counter_advantage_cap", 1.0)
        return max(1.0, min(cap, self._edge_magnitude(edges, target, cap)))

    def response_window(self, base_ticks: Any, reduction: Any = 0) -> int:
        """Return the ticks a hostile operation gives its target to respond (R8.8).

        The Response_Window is measured from the target's notification to the
        effect, and R8.8 puts a floor under it: whatever a vector asks for and
        whatever a Counter_Web Response_Window reduction (R9.4's second permitted
        form) takes off, the target gets at least
        ``minimum_response_window_ticks`` of warning. The answer is
        ``max(floor, base - reduction)``, and the floor is a ``max`` rather than a
        subtraction, which is why it holds for **every** reduction value including
        absurd ones — a reduction larger than the base leaves the floor exactly,
        not a negative window.

        The floor is read from Balance_Config on every call, so an ``@reload``
        retunes the next operation (R15.7), and a floor configured at or below
        zero is treated as no floor at all rather than as a negative one, so the
        window is never negative.

        Args:
            base_ticks: The window the vector asks for, in ticks.
            reduction: The Counter_Web reduction to apply. A negative reduction
                lengthens the window, which the floor claim survives unchanged.

        Returns:
            The window in ticks, at least the configured floor and never
            negative. An unreadable *base_ticks* falls back to the floor and an
            unreadable *reduction* to no reduction — each the direction that
            leaves the target more warning rather than less — so neither raises
            into an operation being placed (R15.3).
        """
        floor = max(0, self._balance_int("minimum_response_window_ticks"))
        base = self._as_ticks(base_ticks)
        return max(floor, base - self._as_ticks(reduction))

    # ------------------------------------------------------------------ #
    #  The tick fan-out (R8.10, R15.9). One registration wires a
    #  Vector_System in, and one tick step drives every one of them.
    # ------------------------------------------------------------------ #

    def register_vector(self, vector: Any) -> None:
        """Register *vector* as the Vector_System owning its Operation_Kind (R15.9).

        The composition root calls this once per vector, immediately after
        constructing it (``branch_system.register_vector(ordnance_system)``), and
        that one call is the entire wiring: it is what makes the vector's
        operations countable against the in-flight cap (R8.20) and what puts the
        vector on the shared tick step (R15.9), so no Vector_System drives its own
        timer and none has to be named anywhere else.

        The vector is read **duck-typed**: its ``operation_kind`` is the key, and
        nothing else about it is inspected here. This module deliberately imports
        nothing from the Operation Contract that defines ``OperationDriver`` —
        the dependency runs the other way, a vector consuming these services — so
        "what a vector is" means *an object naming its kind that can advance its
        own records*, and each half of that is checked where it is used rather
        than asserted at registration time.

        Registering a kind twice **replaces** that kind's entry rather than
        adding a second one, keeping the position the first registration took: a
        kind has exactly one owning Vector_System, so a re-registration is a
        rewire, and the fan-out must never advance one vector twice on one tick.

        Returns:
            ``None`` always. A vector naming no Operation_Kind — absent, blank, or
            an attribute that cannot be read — is a logged no-op rather than a
            raise (R15.3), because a composition root that mis-wires one vector
            must still finish wiring the others.
        """
        kind = self._clean(self._safe_attr(vector, "operation_kind"))
        if kind is None:
            logger.debug(
                "Vector registration skipped for %r: it names no operation_kind",
                vector,
            )
            return
        self._vectors[kind] = vector

    def registered_vectors(self) -> dict[str, Any]:
        """Return the registered Vector_Systems, keyed by Operation_Kind.

        A fresh dict, in registration order — the same registry the tick
        fan-out drives, read by value so the composition root's restart rebuild
        (and any diagnostic) can walk it without reaching into a private field.
        Mutating the copy re-wires nothing: :meth:`register_vector` stays the
        one way in.
        """
        return dict(self._vectors)

    def process_tick(self, tick_number: int) -> None:
        """Advance every registered Vector_System by one tick (R8.10, R15.9).

        The body of the one ``vector_operations`` step
        ``GameTickScript._build_tick_steps`` registers, and the only thing this
        module does with a tick. It fans out and owns nothing about what advancing
        means: the records, the lifecycle, and the effects all belong to the
        vector, so this asks each registered one for ``advance_all(tick)`` and
        looks at nothing it does.

        **Each vector is isolated in its own try/except** — the same shape
        ``at_repeat`` already uses for its named steps and
        ``BombSystem.process_tick`` uses for its bombs — so a vector that raises
        leaves every *other* vector advanced (R8.10) and the failure reads as a
        log line naming the Operation_Kind rather than as a dead tick step. The
        per-*operation* isolation inside one vector is the driver's own; this is
        the outer ring of the same discipline, and the two together mean neither
        one bad operation nor one bad vector can stop the rest.

        **An empty registry is a no-op**, which is precisely the shipped state of
        this feature: the framework lands with no Vector_System registered, so the
        step iterates nothing and the whole operation half stays inert until a
        vector spec registers one.

        Args:
            tick_number: The current game tick, passed straight through to each
                vector. A value that cannot be read as a number is passed as
                ``0`` rather than stopping the fan-out, because a tick advances
                every operation by exactly one whatever the tick is *numbered*.

        Returns:
            ``None`` always. A vector exposing no ``advance_all`` and a vector
            that raises inside it are both logged and stepped over (R15.2,
            R15.3) — a tick step must never raise into the tick script.
        """
        if not self._vectors:
            return
        tick = self._as_ticks(tick_number)
        # A snapshot: a vector that registers another one mid-tick must not
        # mutate the mapping being walked.
        for kind, vector in tuple(self._vectors.items()):
            advance = self._safe_attr(vector, "advance_all")
            if not callable(advance):
                logger.warning(
                    "Vector %r advanced nothing: it exposes no advance_all "
                    "(R15.2)", kind,
                )
                continue
            try:
                advance(tick)
            except Exception:  # noqa: BLE001 - one bad vector must not halt the step
                # Exception level, not debug: a vector failing wholesale every
                # tick is an outage of one sixth of the operation feature, and
                # the per-operation isolation inside advance_all already keeps
                # a single bad record at a quieter level.
                logger.exception(
                    "Vector %r failed to advance on tick %s", kind, tick,
                )

    # ------------------------------------------------------------------ #
    #  Internal helpers — the ledgers' reads
    # ------------------------------------------------------------------ #

    def _now(self) -> int | None:
        """Return the current game tick, or ``None`` when it cannot be read.

        The one call site of the **injected** tick source (R15.1), guarded once
        here so no ledger has to. ``None`` is distinct from tick ``0`` on purpose:
        it means *there is no clock*, and every ledger treats that as "nothing to
        enforce" rather than as the epoch, because a cooldown measured against a
        clock nobody can read would refuse for its whole length.
        """
        try:
            return int(self._current_tick_func())
        except Exception:  # noqa: BLE001 - an unreadable clock is no clock
            logger.debug("tick source unreadable; ledgers read as inert")
            return None

    def _kind_def(self, kind: Any) -> Any:
        """Return the :class:`OperationKindDef` for *kind*, or ``None``.

        Read through the **injected** registry (R15.4). ``None`` when
        ``branches.yaml`` is absent (which leaves every kind unbound and every
        balance field resolved by naming convention), when the kind is not in the
        registry, or when the registry cannot answer.
        """
        kinds = getattr(self.registry, "operation_kinds", None) or {}
        try:
            return kinds.get(kind)
        except (AttributeError, TypeError):
            return None

    def _kind_balance_int(
        self, kind: Any, binding: str, suffix: str, default: int = 0
    ) -> int:
        """Return the per-kind Balance_Config integer *binding* names.

        Two layers, in the order the design intends: the **binding is data** — the
        Operation_Kind registry entry names the field, so a kind can be retargeted
        in ``branches.yaml`` without code — and the naming convention
        ``<kind><suffix>`` is the fallback that keeps every read working with
        ``branches.yaml`` absent. The **value** is read per call either way, never
        cached, so the knob stays hot (R15.7).
        """
        wanted = self._clean(kind)
        if wanted is None:
            return default
        field = self._clean(getattr(self._kind_def(wanted), binding, None))
        return self._balance_int(field or f"{wanted}{suffix}", default)

    def _cooldown_map(self, building: Any) -> dict[str, int]:
        """Return a MUTABLE copy of *building*'s cooldown ledger, normalized.

        The read half of the cooldown ledger's read-copy-write discipline
        (R14.7), and the one place its shape is enforced: ``{kind: ready_at}``
        with a non-blank string key and an integer tick. An absent attribute and a
        hand-edited value of any other shape collapse to the documented default
        ``{}`` (R14.8) — and because the caller writes back what this returned, a
        garbage entry is *dropped* rather than propagated.

        The keys are deliberately not restricted to the six known Operation_Kinds:
        the vocabulary belongs to the registry, and a ledger that silently
        discarded an unrecognized kind would hide a data problem instead of
        letting the kind's own gate report it.
        """
        ledger: dict[str, int] = {}
        for kind, ready_at in self._attr_mapping(building, ATTR_VECTOR_COOLDOWNS).items():
            wanted = self._clean(kind)
            if wanted is None:
                continue
            try:
                ledger[wanted] = int(ready_at)
            except (TypeError, ValueError):
                continue
        return ledger

    def _escalation_map(
        self, actor: Any, now: int | None = None, window: int = 0
    ) -> dict:
        """Return a MUTABLE copy of *actor*'s escalation ledger, pruned and sorted.

        The read half of the escalation ledger's read-copy-write discipline
        (R14.7). Each target's ticks are normalized to integers and sorted
        ascending, and a target left with no entries drops out entirely, so
        "aged out" and "never attacked" are one stored shape (R14.8).

        Pruning keeps exactly the entries inside the window, ``0 <= now - t <
        window``, and so does two jobs:

        * **the window itself** — an entry older than the window is spent, and
          dropping it on read is what bounds the stored list;
        * **a clock that went backwards** — an entry in the *future* cannot
          describe a past resolution, so it is dropped too. The shipped tick
          source answers ``0`` when it cannot read the tick script, and without
          this a single such reading would leave a player throttled against ticks
          that will not come round again for the length of the whole window.

        Pruning is skipped entirely when there is no clock or no configured window
        (``window <= 0``), because a misconfigured knob must delete nobody's
        history — it leaves the limit inert, which is what
        :meth:`_escalation_status` reports.
        """
        prune = now is not None and window > 0
        ledger: dict[Any, list[int]] = {}
        for target_id, ticks in self._attr_mapping(
            actor, ATTR_VECTOR_ESCALATION
        ).items():
            entries = sorted(self._clean_ticks(ticks))
            if prune:
                entries = [tick for tick in entries if 0 <= now - tick < window]
            if entries:
                ledger[target_id] = entries
        return ledger

    def _escalation_status(self, actor: Any, target: Any) -> dict[str, int]:
        """Return the escalation figures for one actor-target pair.

        One read of the ledger answering both consumers, so
        :meth:`escalation_remaining` and the :meth:`may_target` refusal cannot
        disagree about a count they would otherwise each go and fetch.

        Returns:
            ``{"count": int, "cap": int, "window": int, "remaining": int}``.
            ``remaining`` is non-zero **exactly** when the cap is reached, so a
            caller may gate on it alone; every field is ``0`` when no limit is
            configured or nothing can be read (R15.3).
        """
        cap = self._balance_int("escalation_cap")
        window = self._balance_int("escalation_window_ticks")
        now = self._now()
        target_id = self._identity_of(self._target_player(target))
        inert = {"count": 0, "cap": max(0, cap), "window": max(0, window),
                 "remaining": 0}
        if actor is None or target_id is None or now is None:
            return inert
        if cap <= 0 or window <= 0:
            return inert                                  # no limit configured
        entries = self._escalation_map(actor, now, window).get(target_id, [])
        status = dict(inert, count=len(entries))
        if len(entries) < cap:
            return status                                 # under the cap
        # The entry a freed slot waits on: the oldest while the count sits at the
        # cap, and the right one after a cap retuned downward left the ledger
        # holding more entries than the cap now allows.
        waiting_on = entries[len(entries) - cap]
        status["remaining"] = max(0, waiting_on + window - now)
        return status

    @staticmethod
    def _clean_ticks(raw: Any) -> list[int]:
        """Return *raw* as a list of integer ticks, dropping what is not one.

        The normalizer every escalation read passes through: a ``None``, a string
        (deliberately not iterated character by character), a non-iterable, and a
        garbage entry inside an otherwise good list all collapse to the documented
        empty list or drop out, instead of reaching an arithmetic comparison.
        """
        if raw is None or isinstance(raw, str) or not hasattr(raw, "__iter__"):
            return []
        try:
            values = list(raw)
        except (AttributeError, TypeError):
            return []
        ticks: list[int] = []
        for value in values:
            try:
                ticks.append(int(value))
            except (TypeError, ValueError):
                continue
        return ticks

    @staticmethod
    def _tracked_records(vector: Any) -> list:
        """Return the Operation_Records *vector* is tracking, or ``[]``.

        The one duck-typed reach into a Vector_System, kept deliberately narrow:
        a public accessor is preferred and the driver's own tracked list is the
        fallback, so this module needs no import of the Operation Contract and no
        knowledge of the record type to count what is in flight.

        A vector that exposes neither, or one whose accessor raises, yields no
        records with a log (R15.2) — which reads downstream as "nothing in
        flight" rather than as an error in a request path.
        """
        for name in ("tracked_records", "tracked_operations"):
            accessor = getattr(vector, name, None)
            if not callable(accessor):
                continue
            try:
                return list(accessor() or ())
            except Exception:  # noqa: BLE001 - an unreadable vector tracks nothing
                logger.debug("%r.%s failed", vector, name, exc_info=True)
                return []
        try:
            return list(getattr(vector, "_tracked", None) or ())
        except (AttributeError, TypeError):
            return []

    @staticmethod
    def _record_field(record: Any, name: str) -> Any:
        """Read one field off an Operation_Record by value, or ``None``.

        Guarded, and mapping-aware: a record may reach this module as the
        dataclass the Operation Contract defines or as the plain dict that
        dataclass persists as, and the in-flight count must read both alike
        without either shape raising into a request path (R15.3).
        """
        if record is None:
            return None
        try:
            if hasattr(record, name):
                return getattr(record, name, None)
            getter = getattr(record, "get", None)
            return getter(name) if callable(getter) else None
        except Exception:  # noqa: BLE001 - an unreadable field is absent
            return None

    @classmethod
    def _is_terminal_record(cls, record: Any) -> bool:
        """Return True when *record*'s lifecycle state is a terminal one (R8.2).

        Judged on the state's **value**, not on an imported enum: the Operation
        Contract owns :class:`OperationState` and this module must not depend on
        it, so the four names R8.2 declares terminal are compared as strings.
        That is exactly what a ``StrEnum`` member, a plain string, and a persisted
        record all reduce to, which is why the vocabulary can be shared by value
        without being shared by import.

        A state that cannot be read is **not** terminal: the record is tracked,
        and a tracked record is in flight until it says otherwise. Counting it is
        also the conservative direction for a cap.
        """
        state = cls._record_field(record, "state")
        state = getattr(state, "value", state)
        if not isinstance(state, str):
            return False
        return state.strip().lower() in _TERMINAL_STATE_NAMES

    def _owner_matches(self, owner_ref: Any, player: Any) -> bool:
        """Return True when *owner_ref* refers to *player*.

        A record's ``owner_ref`` is a *reference* — the design has it resolved
        lazily — so it may be the player object itself, that player's database
        id, or a ``#dbref`` string spelling the same id. All three are the same
        owner, and the in-flight count would silently under-count if only the
        first were recognized.

        Returns:
            ``False`` for a missing reference and for a player with no readable
            identity, so an unattributable record is never counted against
            someone (R15.3).
        """
        if owner_ref is None or player is None:
            return False
        if self._is_same_player(owner_ref, player):
            return True
        player_id = self._identity_of(player)
        if player_id is None:
            return False
        ref_id = self._identity_of(owner_ref)
        if ref_id is not None:
            return ref_id == player_id
        if isinstance(owner_ref, int):
            return owner_ref == player_id
        if isinstance(owner_ref, str):
            return owner_ref.strip().lstrip("#") == str(player_id)
        return False

    # ------------------------------------------------------------------ #
    #  Internal helpers — the Counter_Web and Response_Window reads
    # ------------------------------------------------------------------ #

    def _counter_edges(self, branch: str) -> Any:
        """Return the advantage edges leaving *branch*, as the web declares them.

        Read through the **injected** registry (R15.4). The container is handed
        back as loaded rather than rebuilt, because the only question asked of it
        is a single ``in`` test: normalizing it would mean walking edges to answer
        a membership question the container already answers, and the six Branch
        names are a closed vocabulary the SchemaValidator has already checked
        (R9.12).

        A ``str`` is rejected on purpose: it would answer ``in`` by *substring*,
        so a hand-written ``"defense"`` in place of ``["defense"]`` would name an
        edge to every Branch whose name it contains a piece of.

        Returns:
            The declared edge container — a tuple of target Branch names as the
            loader produces, or a mapping when one carries per-edge magnitudes —
            and ``()`` for a Branch with no advantages, an absent or empty web, a
            garbage entry, and a registry that cannot answer (R15.3).
        """
        web = getattr(self.registry, "counter_web", None) or {}
        try:
            edges = web.get(branch)
        except (AttributeError, TypeError):
            return ()
        if edges is None or isinstance(edges, str):
            return ()
        return edges if hasattr(edges, "__contains__") else ()

    @staticmethod
    def _edge_magnitude(edges: Any, target: str, default: float) -> float:
        """Return the magnitude the web declares for one edge, else *default*.

        *default* is the Counter_Advantage_Cap, so an edge declared as a bare
        Branch name — every edge the shipped ``branches.yaml`` and its loader can
        express — is worth the cap. That is a deliberate simplification for the
        framework: a per-pair magnitude is a balance question the six vector
        specs are better placed to answer, and this is the seam a future per-edge
        value arrives through, flowing into :meth:`counter_multiplier`'s clamp
        without touching one caller.

        A non-numeric and a non-finite declared value both answer *default*: an
        infinite magnitude would survive the clamp's ceiling only if the ceiling
        were infinite too, and neither may become the immunity R9.4 forbids.
        """
        getter = getattr(edges, "get", None)
        if not callable(getter):
            return default
        try:
            value = float(getter(target))
        except (OverflowError, TypeError, ValueError):
            return default
        return value if math.isfinite(value) else default

    @staticmethod
    def _as_ticks(value: Any, default: int = 0) -> int:
        """Return *value* as an integer tick count, or *default*.

        The scalar counterpart of :meth:`_clean_ticks`: a ``None``, a string that
        spells no number, an infinity, and a garbage value all collapse to
        *default* instead of reaching an arithmetic comparison (R15.3). Callers
        of :meth:`response_window` are vectors rather than validated config, so
        this guards a value the module does not own.
        """
        try:
            return int(value)
        except (OverflowError, TypeError, ValueError):
            return default

    # ------------------------------------------------------------------ #
    #  Internal helpers — the targeting reads
    # ------------------------------------------------------------------ #

    def _target_player(self, target: Any) -> Any:
        """Return the player the protection gates protect for *target*, or ``None``.

        A Vector_Operation may name a player, or an entity that *belongs* to one
        — a building, an agent, a convoy. Both resolve to the same answer here:
        the entity's ``owner`` when it has one, otherwise the target itself. So
        the gates are written once and every vector's target shape passes through
        them.
        """
        if target is None:
            return None
        owner = self._player_attr(target, "owner")
        return owner if owner is not None else target

    @staticmethod
    def _is_same_player(left: Any, right: Any) -> bool:
        """Return True when *left* and *right* are the same player.

        Delegates to :func:`world.utils.is_owner`, the existing ``.id``-based
        identity comparison every ownership check in the game makes, so a player
        object handed back twice by different lookups still reads as one player.
        """
        if left is None or right is None:
            return False
        from world.utils import is_owner

        try:
            return bool(is_owner(left, right))
        except Exception:  # noqa: BLE001 - an unreadable identity is "not me"
            return False

    def _are_allied(self, first: Any, second: Any) -> bool:
        """Return True when *first* and *second* are two distinct allied players.

        Asks the **injected** AllianceSystem — the alliance state's owner —
        preferring whichever predicate it exposes, and falls back to
        :func:`world.utils.are_allied`, the shared friend-or-foe rule turret
        targeting and the fog renderer already use. Either way the answer is the
        existing one rather than a parallel notion of "allied" invented here.

        Returns:
            ``False`` on any missing data or unavailable system, matching the
            shared predicate's own posture: a lookup failure must never suppress
            legitimate hostile targeting.
        """
        if first is None or second is None:
            return False
        alliance = self._alliance_system
        for name in ("are_allied", "is_allied_building_owner"):
            check = getattr(alliance, name, None)
            if not callable(check):
                continue
            try:
                return bool(check(first, second))
            except Exception:  # noqa: BLE001 - an unreadable alliance is "not allied"
                logger.debug("alliance check %r failed", name, exc_info=True)
                return False
        from world.utils import are_allied

        try:
            return bool(are_allied(first, second))
        except Exception:  # noqa: BLE001 - an unreadable alliance is "not allied"
            return False

    def _alliance_id_of(self, player: Any) -> Any:
        """Return the id of the alliance *player* belongs to, or ``None``.

        Read from the player's own ``player_alliance`` pointer — the single
        stored answer ``AllianceSystem`` keeps and ``are_allied`` compares — so
        the refusal reports the same alliance the gate decided on.
        """
        return self._player_attr(player, "player_alliance")

    def _alliance_summary(self, alliance_id: Any) -> dict | None:
        """Return the injected AllianceSystem's summary of *alliance_id*, or ``None``.

        Read-only, and used purely so an allied-target refusal can name the
        alliance protecting the target (R11.9). A system that is absent, cannot
        answer, or no longer holds the record leaves the refusal reporting the
        id alone rather than failing.
        """
        if alliance_id is None:
            return None
        lookup = getattr(self._alliance_system, "alliance_summary", None)
        if not callable(lookup):
            return None
        try:
            summary = lookup(alliance_id)
        except Exception:  # noqa: BLE001 - a report never blocks a decision
            logger.debug("alliance summary failed for %r", alliance_id, exc_info=True)
            return None
        return summary if isinstance(summary, dict) else None

    def _alliance_members(self, alliance_id: Any) -> list:
        """Return the live members of *alliance_id*, or ``[]``.

        The roster the consent revocation needs so a departing member is dropped
        from the *other* side's stores too (R11.11). Every accessor is consulted
        by name and guarded, so an AllianceSystem that is absent or cannot
        enumerate leaves the other side's entries in place — inert, because
        :meth:`has_consent` requires a live alliance anyway.
        """
        if alliance_id is None:
            return []
        alliance = self._alliance_system
        for name in ("alliance_members", "live_members", "_live_members"):
            lookup = getattr(alliance, name, None)
            if not callable(lookup):
                continue
            try:
                return list(lookup(alliance_id) or ())
            except Exception:  # noqa: BLE001 - an unreadable roster is empty
                logger.debug(
                    "alliance roster %r failed for %r", name, alliance_id,
                    exc_info=True,
                )
                return []
        return []

    @staticmethod
    def _identity_of(entity: Any) -> Any:
        """Return the stable identity a consent is keyed by, or ``None``.

        The database ``.id`` every ownership comparison in this codebase uses,
        so a consent survives the same object being handed back as a different
        Python instance. An entity with no id cannot be consented to — a
        consent that could not be matched back later would be worse than none.
        """
        if entity is None:
            return None
        try:
            return getattr(entity, "id", None)
        except Exception:  # noqa: BLE001 - an unreadable identity is no identity
            return None

    def _entity_level(self, entity: Any) -> int | None:
        """Return *entity*'s Entity_Level, or ``None`` when it has none.

        Delegates to :func:`world.utils.get_player_level`, the single source of
        truth for "which level is this" (including the legacy ``rank_level``
        fallback), so the new-player shield reads the same number the rank gates
        do.

        ``None`` means *no level to compare* rather than level zero, and that
        distinction is the point: an NPC base's Sentinel, an unowned world
        object, and anything whose level cannot be read are not "a target
        player" (R10.4), so the shield must not reach them.
        """
        if entity is None:
            return None
        db = getattr(entity, "db", None)
        if db is None:
            return None
        if self._safe_attr(db, "is_sentinel") or self._safe_attr(db, "npc_type"):
            return None                                   # an NPC is no new player
        from world.utils import get_player_level

        try:
            level = int(get_player_level(entity, default=0))
        except Exception:  # noqa: BLE001 - an unreadable level is no level
            return None
        return level if level > 0 else None

    def _balance_int(self, field: str, default: int = 0) -> int:
        """Return the integer Balance_Config value of *field*, or *default*.

        Read through the **injected** registry on every call rather than cached,
        so an ``@reload`` retunes the next request (R15.7). An absent or
        non-numeric field answers *default*, which for every gate here is the
        inert value — a knob that cannot be read must not become an accidental
        refusal.
        """
        balance = getattr(self.registry, "balance", None)
        if balance is None:
            return default
        try:
            return int(getattr(balance, field, default))
        except (TypeError, ValueError):
            return default

    def _balance_float(self, field: str, default: float = 0.0) -> float:
        """Return the float Balance_Config value of *field*, or *default*.

        The fractional counterpart of :meth:`_balance_int` — the Counter_Web cap
        is a multiplier, not a tick count — read through the **injected** registry
        on every call so an ``@reload`` retunes the next operation (R15.7).

        An absent field, a non-numeric one, and a **non-finite** one all answer
        *default*. The last matters: ``nan`` and ``inf`` both parse as floats out
        of YAML, and an infinite ceiling would let a multiplier grow without
        bound — the one thing R9.4's cap exists to prevent — so they are refused
        here rather than at every call site.
        """
        balance = getattr(self.registry, "balance", None)
        if balance is None:
            return default
        try:
            value = float(getattr(balance, field, default))
        except (TypeError, ValueError):
            return default
        return value if math.isfinite(value) else default

    @staticmethod
    def _name_of(entity: Any) -> str | None:
        """Return *entity*'s display key, or ``None`` — the name a refusal quotes."""
        try:
            key = getattr(entity, "key", None)
        except Exception:  # noqa: BLE001 - an unreadable key is no name
            return None
        return key if isinstance(key, str) and key.strip() else None

    @staticmethod
    def _safe_attr(db: Any, key: str) -> Any:
        """Read one attribute off a ``db`` proxy by value, or ``None``.

        A single guarded read, because a fake, a Sentinel, and a real
        ``DbHolder`` all answer differently for an unset key and none of them
        may raise into an eligibility or targeting decision (R15.3).
        """
        if db is None:
            return None
        try:
            return getattr(db, key, None)
        except Exception:  # noqa: BLE001 - an unreadable attribute is absent
            return None

    # ------------------------------------------------------------------ #
    #  Internal helpers — the gates' reads
    # ------------------------------------------------------------------ #

    def _unapplied_reason(
        self, player: Any, tech_key: str, planet: Any = None
    ) -> str | None:
        """Return why *tech_key*'s effects are not applied for *player*, or None.

        The single implementation of R6.2's "researched AND applied", so the
        unlock gate and any later consumer cannot disagree about what an applied
        technology is:

        1. absent from the owner's record — :data:`UNLOCK_NOT_RESEARCHED`;
        2. recorded, but its Branch is not the commitment on *planet*, so the
           whole Branch is dormant — :data:`UNLOCK_DORMANT`;
        3. recorded and committed, but still awaiting its reduced-cost
           Reinstatement job — :data:`UNLOCK_REINSTATEMENT_PENDING` (R5.7).

        A technology this system cannot place in a Branch cannot be dormant in
        one, so a recorded key with no resolvable tree counts as applied.

        Resolves the three reads this answer needs — the record, the commitment,
        and the pending set — then delegates the verdict to
        :meth:`_withheld_reason`, which :meth:`applied_technologies` shares.

        Returns:
            The reason string, or ``None`` when the effects are applied.
        """
        live = self.commitment(player, planet)
        return self._withheld_reason(
            self._recorded_technologies(player),
            live,
            self._pending_reinstatement(player, live),
            tech_key,
        )

    def _withheld_reason(
        self,
        recorded: set[str],
        live: str | None,
        pending: frozenset[str],
        tech_key: str,
    ) -> str | None:
        """Judge one key against already-resolved reads; see :meth:`_unapplied_reason`.

        Split out so the one-key question (the unlock gate) and the whole-record
        question (:meth:`applied_technologies`) share a single definition of
        "applied" while each pays for its reads exactly once.

        Args:
            recorded: *player*'s researched keys, already normalized.
            live: The Branch_Commitment on the planet in question, or ``None``.
            pending: The Reinstatement set of *live* — the only Branch whose
                pending keys can matter, because a key of any **other** Branch
                is already withheld as dormant before the pending test runs.
            tech_key: The key to judge.

        Returns:
            The reason string, or ``None`` when the effects are applied.
        """
        key = self._clean(tech_key)
        if key is None or key not in recorded:
            return UNLOCK_NOT_RESEARCHED
        branch = self.branch_of_technology(key)
        if branch is None:
            return None
        if live != branch:
            return UNLOCK_DORMANT
        if key in pending:
            return UNLOCK_REINSTATEMENT_PENDING
        return None

    def _dormant_technologies(
        self, player: Any, incoming_branch: str
    ) -> dict[str, tuple[str, ...]]:
        """Return *player*'s recorded technologies outside *incoming_branch*.

        The figures the pre-charge switch report quotes (R4.8, R13.4): grouped
        by Branch, in canonical Branch order, each list sorted so the report is
        deterministic. Under a commitment to *incoming_branch* every key here is
        inert, and the record itself is untouched — dormancy suspends effects
        and erases no history (R5.3).

        Returns:
            ``{branch: (tech_key, ...)}`` holding only the Branches the player
            has a record in, so the mapping is falsy exactly when committing
            costs the player no bonuses.
        """
        incoming = self._clean(incoming_branch)
        recorded = self._recorded_technologies(player)
        if not recorded:
            return {}
        grouped: dict[str, list[str]] = {}
        for key in recorded:
            branch = self.branch_of_technology(key)
            if branch is None or branch == incoming:
                continue
            grouped.setdefault(branch, []).append(key)
        return {
            branch: tuple(sorted(grouped[branch]))
            for branch in BRANCHES
            if branch in grouped
        }

    def _recorded_technologies(self, player: Any) -> set[str]:
        """Return the technology keys *player* has on record, or an empty set.

        Prefers a public accessor on the injected TechLabSystem — the record's
        owner — and falls back to reading the player's own ``researched_techs``
        attribute by value, which is where that system keeps it. Reading is all
        this system does: ``TechLabSystem`` stays the record's only writer.

        Every key is normalized through :meth:`_clean`, so a blank or non-string
        entry in a hand-edited record cannot leak into a comparison.
        """
        raw: Any = None
        accessor = getattr(self._tech_system, "researched_techs", None)
        if callable(accessor):
            try:
                raw = accessor(player)
            except Exception:  # noqa: BLE001 - fall back, never raise out
                logger.debug(
                    "tech-system record lookup failed for %r", player, exc_info=True
                )
                raw = None
        if raw is None:
            raw = self._player_attr(player, "researched_techs")
        return self._clean_keys(raw)

    def _pending_reinstatement(
        self, player: Any, branch: str | None
    ) -> frozenset[str]:
        """Return the keys of *branch* awaiting Reinstatement for *player*.

        Reads the ``branch_reinstatement`` player attribute
        (:data:`~world.constants.ATTR_BRANCH_REINSTATEMENT`), whose documented
        shape is ``{branch: [tech_key, ...]}`` and whose documented default is
        absent — a player who never abandoned a Branch has nothing pending, and an
        absent attribute reads as that (R14.8). :meth:`_seed_reinstatement` is
        what fills it, and the Reinstatement research job is what empties it key
        by key; while a key sits here its effect is withheld from the applied
        bonuses and from the unlock gate alike (R5.7).
        """
        wanted = self._clean(branch)
        if wanted is None:
            return frozenset()
        raw = self._player_attr(player, ATTR_BRANCH_REINSTATEMENT)
        if not raw:
            return frozenset()
        try:
            keys = raw.get(wanted)
        except (AttributeError, TypeError):
            return frozenset()
        return frozenset(self._clean_keys(keys))

    def _blocking_entry(self, building: Any, branch: str) -> dict:
        """Describe one building that blocks a switch (R4.2).

        Structured data only: the Branch it belongs to, its abbreviation and
        display name, and its coordinates — every value the player needs to walk
        over and demolish it, with no sentence built here. An unreadable field
        reports ``None`` rather than omitting the building, so the entry count
        always equals the estate count the same refusal reports.
        """
        bdef = self._building_def_of(building)
        x, y = self._coords_of(building)
        return {
            "branch": branch,
            "building": self._abbr_of_live_building(building),
            "building_name": self._clean(getattr(bdef, "name", None)),
            "x": x,
            "y": y,
        }

    def _abbr_of_live_building(self, building: Any) -> str | None:
        """Return *building*'s definition abbreviation, or its raw type.

        The definition's own ``abbreviation`` is preferred so the report quotes
        the same code the build command takes; a building whose type resolves to
        no definition falls back to the stored type string, which is still more
        useful to a player than ``None``.
        """
        bdef = self._building_def_of(building)
        abbr = self._clean(getattr(bdef, "abbreviation", None))
        if abbr is not None:
            return abbr
        from world.utils import get_building_type

        try:
            return self._clean(get_building_type(building))
        except Exception:  # noqa: BLE001 - an unreadable type is "unknown"
            return None

    def _publish(self, player: Any, kind: str, data: dict) -> None:
        """Publish a structured player notification, swallowing any failure.

        The gates run inside another system's validation chain, so a missing or
        broken event bus must not become an exception in a construction attempt
        (R15.3). :meth:`BaseSystem.notify` is the only message channel used —
        this system composes no text (R13.5).
        """
        try:
            self.notify(player, kind, **data)
        except Exception:  # noqa: BLE001 - a report never breaks a build
            logger.debug("notification %r failed for %r", kind, player, exc_info=True)

    def _target_planet(
        self, player: Any, tile: Any = None, x: int | None = None, y: int | None = None
    ) -> Any:
        """Return the planet a construction request targets, or ``None``.

        The target *tile*'s planet, falling back to the planet *player* occupies
        — which is the pre-feature behavior for a tile whose planet cannot be
        read, and keeps the gates' planet scope identical to
        ``BuildingSystem``'s own per-planet caps. ``None`` remains the "any
        planet" wildcard the estate and commitment queries document.

        *x* / *y* are accepted so the gates share the chain's validator
        signature; a tile's planet does not depend on the coordinate within it.
        """
        planet = self._tile_planet(tile)
        if planet is not None:
            return planet
        return self._player_planet(player)

    @staticmethod
    def _tile_planet(tile: Any) -> Any:
        """Return the planet key of a target *tile*, or ``None``.

        Mirrors ``BuildingSystem._tile_planet`` — the ``coord_planet`` tag, then
        ``db.planet`` / ``db.coord_planet``, then ``planet_name`` — so a gate and
        the caps it sits beside agree on what "this planet" means. Every read is
        guarded: an exotic tile resolves to ``None`` rather than raising.
        """
        if tile is None:
            return None
        tags = getattr(tile, "tags", None)
        if tags is not None and hasattr(tags, "get"):
            try:
                planet = tags.get(category="coord_planet", return_list=False)
            except Exception:  # noqa: BLE001 - no tag handler, no tag
                planet = None
            if planet:
                return planet
        db = getattr(tile, "db", None)
        if db is not None:
            for key in ("planet", "coord_planet"):
                try:
                    planet = getattr(db, key, None)
                except Exception:  # noqa: BLE001 - an unreadable db is no planet
                    planet = None
                if planet:
                    return planet
        try:
            return getattr(tile, "planet_name", None) or None
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _coords_of(entity: Any) -> tuple:
        """Return *entity*'s ``(x, y)``, or ``(None, None)``.

        Delegates to :func:`world.utils.get_coords` so a reported coordinate is
        the same pair every other system reads. The import is function-local so
        this module stays importable with the game framework absent (R15.1).
        """
        from world.utils import get_coords

        try:
            coords = get_coords(entity)
        except Exception:  # noqa: BLE001 - unreadable coordinates are unknown
            return (None, None)
        if not coords:
            return (None, None)
        return (coords[0], coords[1])

    @staticmethod
    def _player_attr(player: Any, key: str) -> Any:
        """Read one persisted player attribute by value, or ``None``.

        Delegates to :func:`world.utils.get_obj_attr`, which checks the
        ``attributes`` handler then the ``db`` proxy, so a real Character, an
        NPC sentinel, and a test fake all read alike. An absent attribute is
        ``None`` — the caller applies that attribute's documented default
        (R14.8).
        """
        from world.utils import get_obj_attr

        try:
            return get_obj_attr(player, key)
        except Exception:  # noqa: BLE001 - an unreadable attribute is absent
            return None

    @classmethod
    def _clean_keys(cls, raw: Any) -> set[str]:
        """Return *raw* as a set of non-empty stripped strings.

        The normalizer every record read passes through: a ``None``, a string
        (deliberately not iterated character by character), a mapping, or a
        garbage value all collapse to the documented empty set instead of
        leaking into a membership test.
        """
        if raw is None or isinstance(raw, str) or not hasattr(raw, "__iter__"):
            return set()
        try:
            items = list(raw)
        except (AttributeError, TypeError):
            return set()
        return {key for key in (cls._clean(item) for item in items) if key}

    def _technology_def(self, tech_key: str) -> "TechnologyDef | None":
        """Resolve *tech_key* to a technology definition, or ``None``.

        Reads the injected registry (R15.4); ``None`` for a blank key, an
        unknown one, or a registry that cannot answer.
        """
        key = self._clean(tech_key)
        if key is None:
            return None
        technologies = getattr(self.registry, "technologies", None) or {}
        try:
            return technologies.get(key)
        except (AttributeError, TypeError):
            return None

    # ------------------------------------------------------------------ #
    #  Internal helpers — definition resolution through the INJECTED registry
    # ------------------------------------------------------------------ #

    def _estate_by_branch(self, player: Any, planet: Any = None) -> dict[str, list]:
        """Group the buildings *player* owns on *planet* by their Branch.

        The one scan behind every estate answer, so :meth:`estate`,
        :meth:`estate_count`, and :meth:`conflicting_estates` cannot disagree
        about membership and the six-Branch question costs one pass rather than
        six. Neutral_Buildings are absent from the result: they belong to no
        estate and so block no switch.

        Returns:
            ``{branch: [building, ...]}`` holding only the Branches with at
            least one building, empty for an owner this system cannot read.
        """
        if planet is None:
            planet = self._player_planet(player)
        grouped: dict[str, list] = {}
        for building in self._owned_buildings(player):
            # A building whose planet cannot be determined counts everywhere,
            # matching ``owner_research_lab`` and ``_owner_hq_buildings``.
            if planet is not None and self._building_planet(building) not in (
                None,
                planet,
            ):
                continue
            branch = self._branch_of_live_building(building)
            if branch is None:
                continue                                  # Neutral_Building
            grouped.setdefault(branch, []).append(building)
        return grouped

    def _branch_of_live_building(self, building: Any) -> str | None:
        """Return the Branch a live *building* object belongs to, or ``None``.

        The object-level counterpart of :meth:`branch_of_building`: it resolves
        the object's ``building_type`` to a definition through the **injected**
        registry (R15.4) and reads that definition's Branch, so the affiliation
        of a placed building and of its abbreviation are the same answer by
        construction. ``branch`` wins and ``research_tree`` is the fallback, so
        a Branch_Lab belongs to the Branch it hosts.

        Returns:
            The Branch name, or ``None`` for a Neutral_Building and for any
            building whose type resolves to no loaded definition (R15.3).
        """
        return self._branch_of_def(self._building_def_of(building))

    @staticmethod
    def _owned_buildings(player: Any) -> list:
        """Return the buildings *player* owns, across every planet, or ``[]``.

        Thin, defensive wrapper over the existing ``get_buildings()``
        enumeration — the same one ``world.utils`` walks — so an owner that
        exposes no such method (an NPC sentinel, a garbage argument) yields an
        empty estate instead of raising into the caller (R15.3).
        """
        if player is None or not hasattr(player, "get_buildings"):
            return []
        try:
            return list(player.get_buildings() or ())
        except Exception:  # noqa: BLE001 - an unreadable roster is "owns nothing"
            logger.debug("owned-buildings lookup failed for %r", player, exc_info=True)
            return []

    @staticmethod
    def _building_planet(building: Any) -> Any:
        """Return the planet *building* stands on, or ``None`` for "any planet".

        Delegates to :func:`world.utils._building_planet` — a building derives
        its planet from its room and falls back to ``coord_planet`` — so a
        planet-scoped estate and a planet-scoped commitment agree on what "on
        this planet" means. The import is function-local so this module stays
        importable with the game framework absent (R15.1).
        """
        from world.utils import _building_planet as resolve_planet

        try:
            return resolve_planet(building)
        except Exception:  # noqa: BLE001 - an unreadable planet is the wildcard
            return None

    def _owned_lab(self, player: Any, planet: Any = None) -> Any:
        """Return the completed Branch_Lab *player* owns on *planet*, or ``None``.

        Delegates to :func:`world.utils.owner_research_lab` — the existing
        ownership query, reused rather than reimplemented, and deliberately
        **not** modified: it filters on ``under_construction`` alone and never
        consults ``offline`` or ``building_is_operational``, which is exactly the
        R3.9 rule this system needs.

        The import is function-local so this module stays importable with the
        game framework absent (R15.1), and ``self.registry`` is passed as the
        capability provider so the lookup resolves through the **injected**
        registry rather than a process-wide singleton (R15.4).

        The one exception to "read the world as it is": while a destruction
        subscriber holds an :meth:`_ignoring` scope open, the lab about to be
        deleted is treated as already gone. ``BUILDING_DESTROYED`` fires
        *before* the delete, so without that the trigger would recompute the
        bonuses of the very commitment it is reacting to the loss of. The
        one-lab-per-planet limit is what makes dropping the match sufficient:
        there is no second lab on that planet to fall through to.
        """
        if player is None:
            return None
        from world.utils import owner_research_lab

        if planet is None:
            planet = self._player_planet(player)
        try:
            lab = owner_research_lab(player, planet=planet, provider=self.registry)
        except Exception:  # noqa: BLE001 - a query never raises into a caller
            logger.debug("owned-lab lookup failed for %r", player, exc_info=True)
            return None
        if lab is not None and self._is_same_building(lab, self._ignored_building):
            return None
        return lab

    def _building_def_of(self, building: Any) -> "BuildingDef | None":
        """Return the definition of a live *building* object, or ``None``.

        Resolves the object's ``building_type`` through the injected registry
        (R15.4). ``None`` for a building carrying no type, a type absent from
        the loaded definitions, or a registry that cannot answer.
        """
        from world.utils import get_building_type

        try:
            btype = get_building_type(building)
        except Exception:  # noqa: BLE001 - a corrupt read is "no definition"
            return None
        if not btype:
            return None
        return self._building_def(btype)

    @staticmethod
    def _player_planet(player: Any) -> Any:
        """Return the planet *player* currently occupies, or ``None``.

        ``None`` is the documented "any planet" wildcard downstream, which is
        the pre-feature behavior for a player whose planet cannot be read.
        """
        try:
            return getattr(getattr(player, "db", None), "coord_planet", None)
        except Exception:  # noqa: BLE001 - an unreadable db is "no planet"
            return None

    @staticmethod
    def _clean(value: Any) -> str | None:
        """Return *value* as a non-empty stripped string, or ``None``.

        The one normalizer every identity answer passes through, so a missing
        field, a null field, an empty string, and a non-string all collapse to
        the same documented empty value instead of leaking into a comparison.
        """
        if not isinstance(value, str):
            return None
        stripped = value.strip()
        return stripped or None

    def _iter_building_defs(self) -> list:
        """Return every loaded :class:`BuildingDef`, or an empty list.

        Tolerates a registry whose ``buildings`` mapping is absent or of an
        unexpected shape so a catalog scan degrades to "nothing matches"
        instead of raising (R15.3).
        """
        buildings = getattr(self.registry, "buildings", None)
        if not buildings:
            return []
        try:
            return list(buildings.values())
        except (AttributeError, TypeError):
            return []

    def _building_def(self, abbr_or_def: Any) -> "BuildingDef | None":
        """Resolve *abbr_or_def* to a building definition, or ``None``.

        Accepts a definition object (returned as-is, recognized by carrying an
        ``abbreviation``) or an abbreviation string, which is looked up in the
        injected registry — exact first, then upper-cased, because
        abbreviations are stored upper-case while players type either case.
        """
        if abbr_or_def is None:
            return None
        if not isinstance(abbr_or_def, str):
            # A definition object, or something duck-typed like one. Anything
            # without the fields the caller wants simply resolves to no Branch.
            return abbr_or_def if hasattr(abbr_or_def, "abbreviation") else None
        abbr = self._clean(abbr_or_def)
        if abbr is None:
            return None
        buildings = getattr(self.registry, "buildings", None) or {}
        try:
            return buildings.get(abbr) or buildings.get(abbr.upper())
        except (AttributeError, TypeError):
            return None

    def _branch_of_def(self, bdef: Any) -> str | None:
        """Return the Branch a building definition belongs to, or ``None``.

        ``branch`` (the Branch_Affiliation) wins; ``research_tree`` (the Branch
        a lab *hosts*) is the fallback so a lab omitting the optional
        affiliation still belongs to its own Branch. A definition declaring
        neither is a Neutral_Building (R2.2).
        """
        if bdef is None:
            return None
        affiliation = self._clean(getattr(bdef, "branch", None))
        if affiliation is not None:
            return affiliation
        return self._clean(getattr(bdef, "research_tree", None))

    def _hosted_branch_of_def(self, bdef: Any) -> str | None:
        """Return the Branch a *lab* definition HOSTS, or ``None``.

        The mirror image of :meth:`_branch_of_def`'s precedence, and the rule
        every commitment answer uses: ``research_tree`` is what says which
        Branch a lab hosts, and ``branch`` is the fallback for a lab declaring
        only the optional affiliation. R2.4 requires the two to agree when both
        are set, so the precedence is only ever visible for a lab that omits
        one — and picking the *hosted* field first keeps a lab's commitment and
        its Branch_Estate membership the same answer.

        Single-sourced here so :meth:`commitment`, the Branch-switch gate, and
        the destruction trigger cannot drift apart on what a lab hosts.
        """
        if bdef is None:
            return None
        hosted = self._clean(getattr(bdef, "research_tree", None))
        if hosted is not None:
            return hosted
        return self._clean(getattr(bdef, "branch", None))

    def _hosted_branch_of(self, lab: Any) -> str | None:
        """Return the Branch a live *lab* object hosts, or ``None``.

        The object-level form of :meth:`_hosted_branch_of_def`, resolving the
        object's ``building_type`` through the **injected** registry (R15.4).
        ``None`` for a missing lab and for one whose type resolves to no loaded
        definition, which is what makes a commitment answer ``None`` rather than
        raise for an unreadable lab (R15.3).
        """
        return self._hosted_branch_of_def(self._building_def_of(lab))

    @staticmethod
    def _is_lab(bdef: Any) -> bool:
        """Return True when *bdef* declares the ``research_lab`` capability.

        Keys on the capability rather than on an abbreviation, so the two labs
        this feature adds — and any future one — are covered by data alone.
        Falls back to reading ``capabilities`` directly for a definition-like
        object that carries no ``has_capability``.
        """
        if bdef is None:
            return False
        checker = getattr(bdef, "has_capability", None)
        if callable(checker):
            try:
                return bool(checker(RESEARCH_LAB))
            except Exception:  # noqa: BLE001 - a duck-typed def must not raise here
                return False
        try:
            return RESEARCH_LAB in (getattr(bdef, "capabilities", None) or ())
        except TypeError:
            return False

    def _lab_def_for_branch(self, branch: str) -> "BuildingDef | None":
        """Return the definition of the Branch_Lab hosting *branch*, or ``None``.

        Prefers the registry's own bijection lookup so the hosting-lab answer
        has a single implementation; falls back to no lab when the injected
        registry predates that accessor or the lookup fails.
        """
        wanted = self._clean(branch)
        if wanted is None:
            return None
        lookup = getattr(self.registry, "research_lab_for_tree", None)
        if not callable(lookup):
            return None
        try:
            return lookup(wanted)
        except Exception:  # noqa: BLE001 - a query never raises into a caller
            logger.debug("lab lookup failed for branch %r", branch, exc_info=True)
            return None

    def _technologies_for_branch(self, branch: str) -> list[str]:
        """Return the technology keys belonging to *branch*, in definition order."""
        wanted = self._clean(branch)
        if wanted is None:
            return []
        technologies = getattr(self.registry, "technologies", None)
        if not technologies:
            return []
        try:
            tdefs = list(technologies.values())
        except (AttributeError, TypeError):
            return []
        out: list[str] = []
        for tdef in tdefs:
            if self._clean(getattr(tdef, "tree", None)) != wanted:
                continue
            key = self._clean(getattr(tdef, "key", None))
            if key is not None:
                out.append(key)
        return out

    def _counter_web(self) -> dict[str, tuple[str, ...]]:
        """Return the loaded Counter_Web, normalized to the six Branches.

        The registry keeps ``branches.yaml`` a faithful round-trip and leaves
        every content question to the schema validator, so this read drops
        anything the validator would have rejected — a name outside the six, a
        self-edge, a duplicate — and sorts each target list. That keeps a
        player-facing overview free of garbage even against a hand-edited
        dataset, and makes the projection deterministic.

        Returns:
            ``{branch: (branch, ...)}``, empty when no Counter_Web is loaded —
            which is the documented inert state where no Branch counters any
            other.
        """
        raw = getattr(self.registry, "counter_web", None)
        if not raw:
            return {}
        try:
            items = list(raw.items())
        except (AttributeError, TypeError):
            return {}
        web: dict[str, tuple[str, ...]] = {}
        for source, targets in items:
            key = self._clean(source)
            if key is None or key not in BRANCHES:
                continue
            if isinstance(targets, str) or not hasattr(targets, "__iter__"):
                continue
            clean = {
                name
                for name in (self._clean(t) for t in targets)
                if name is not None and name in BRANCHES and name != key
            }
            web[key] = tuple(sorted(clean))
        return web
