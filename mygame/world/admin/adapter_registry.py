"""
AdapterRegistry — registration point and lookup for all EntityAdapters.

The registry is the enforcement point of the verb-grammar contract
(design Component 2): ``register`` raises at registration time — i.e. at
server start, before any ``@<entity>`` command is invocable — when an
adapter neither supports nor explicitly opts out (with a reason that is
non-empty after trimming whitespace) of every core verb in
``world.admin.types.CORE_VERBS``, naming each unaccounted-for verb
(Requirements 1.1, 1.2, 1.3). Extra-verb and alias names colliding with a
core verb are likewise rejected (Requirement 1.7).

Registration is wired from ``server.conf.game_init.initialize_game`` via
``register_all()`` — the single startup entry point that later phases
extend with concrete adapters (ItemAdapter, BuildingAdapter, ...).
"""

from __future__ import annotations

from world.admin.types import CORE_VERBS, EntityAdapter


class AdapterRegistrationError(ValueError):
    """An EntityAdapter violated the grammar contract at registration time."""


class AdapterRegistry:
    """Holds every registered EntityAdapter, keyed by ``entity_key``.

    ``register`` fails fast on an incomplete or colliding grammar contract,
    making the uniform verb grammar a checked invariant rather than a
    convention. A rejected adapter is never added (Requirement 1.1).
    """

    def __init__(self) -> None:
        self._adapters: dict[str, EntityAdapter] = {}

    def register(self, adapter: EntityAdapter) -> None:
        """Register *adapter*, enforcing the verb-grammar contract.

        Raises:
            AdapterRegistrationError: when the adapter neither supports nor
                opts out of every core verb (each unaccounted-for verb is
                named in the message), when any opt-out reason is missing or
                empty after trimming, when an extra-verb or alias name
                collides with a core verb, or when an adapter is already
                registered under the same ``entity_key``.
        """
        entity_key = adapter.entity_key
        if entity_key in self._adapters:
            raise AdapterRegistrationError(
                f"cannot register adapter for entity '{entity_key}': "
                f"an adapter is already registered under that key"
            )

        supported = frozenset(adapter.supported_verbs)
        opt_outs = dict(adapter.opt_outs)
        problems: list[str] = []

        # Requirement 1.1 — every core verb must be supported or opted out;
        # name each unaccounted-for verb in the error.
        unaccounted = sorted(CORE_VERBS - supported - set(opt_outs))
        if unaccounted:
            problems.append(
                "unaccounted-for core verb(s): "
                + ", ".join(repr(verb) for verb in unaccounted)
            )

        # Requirement 1.2 — every declared opt-out must carry a reason that
        # is non-empty after trimming whitespace.
        for verb in sorted(opt_outs):
            reason = opt_outs[verb]
            if not isinstance(reason, str) or not reason.strip():
                problems.append(
                    f"opt-out for verb {verb!r} has a missing or empty reason"
                )

        # Requirement 1.7 — extra verbs and aliases must not collide with
        # core verbs.
        for label, names in (
            ("extra verb", adapter.extra_verbs),
            ("alias", adapter.aliases),
        ):
            for name in sorted(set(names) & CORE_VERBS):
                problems.append(f"{label} {name!r} collides with a core verb")

        if problems:
            raise AdapterRegistrationError(
                f"cannot register adapter for entity '{entity_key}': "
                + "; ".join(problems)
            )

        self._adapters[entity_key] = adapter

    def get(self, entity_key: str) -> EntityAdapter | None:
        """Return the adapter registered under *entity_key*, or None."""
        return self._adapters.get(entity_key)

    def all(self) -> list[EntityAdapter]:
        """Return every registered adapter, in registration order."""
        return list(self._adapters.values())


#: Process-wide registry the routers resolve adapters through. Populated by
#: ``register_all()`` at server start (game_init); tests build their own
#: ``AdapterRegistry`` instances instead of touching this one.
_REGISTRY = AdapterRegistry()


def get_registry() -> AdapterRegistry:
    """Return the process-wide AdapterRegistry."""
    return _REGISTRY


def register_all(registry: AdapterRegistry | None = None) -> AdapterRegistry:
    """Register every concrete EntityAdapter (startup entry point).

    Called from ``server.conf.game_init.initialize_game`` so that grammar-
    contract violations raise at server start, before any ``@<entity>``
    command becomes invocable (Requirement 1.3). Later phases append their
    adapters to the list below (Phase 1: ItemAdapter; Phase 2:
    BuildingAdapter, AgentAdapter, TechnologyAdapter; ...).

    Idempotent per entity_key: an Evennia in-process reload re-runs the
    startup hook, and re-offering an already-registered adapter is skipped
    rather than rejected as a duplicate.
    """
    reg = registry if registry is not None else _REGISTRY

    # Imported here (not at module top) so adapter modules — which pull in
    # game-system code — only load when registration actually runs.
    from world.admin.adapters.agent_adapter import AgentAdapter
    from world.admin.adapters.alliance_adapter import AllianceAdapter
    from world.admin.adapters.building_adapter import BuildingAdapter
    from world.admin.adapters.item_adapter import ItemAdapter
    from world.admin.adapters.outpost_adapter import OutpostAdapter
    from world.admin.adapters.planet_adapter import PlanetAdapter
    from world.admin.adapters.player_adapter import PlayerAdapter
    from world.admin.adapters.powerup_adapter import PowerupAdapter
    from world.admin.adapters.resource_adapter import ResourceAdapter
    from world.admin.adapters.stat_adapter import StatAdapter
    from world.admin.adapters.tech_adapter import TechnologyAdapter
    from world.admin.adapters.terrain_adapter import TerrainAdapter

    # Phase 1: @item pilot; Phase 2: @building, @agent, @tech;
    # Phase 3: @outpost, @alliance, @player, @stat, @resource;
    # Phase 4: @powerup, @terrain (def-only), @planet (def-read-only).
    adapters: list[EntityAdapter] = [
        ItemAdapter(),
        BuildingAdapter(),
        AgentAdapter(),
        TechnologyAdapter(),
        OutpostAdapter(),
        AllianceAdapter(),
        PlayerAdapter(),
        StatAdapter(),
        ResourceAdapter(),
        PowerupAdapter(),
        TerrainAdapter(),
        PlanetAdapter(),
    ]

    for adapter in adapters:
        if reg.get(adapter.entity_key) is None:
            reg.register(adapter)
    return reg
