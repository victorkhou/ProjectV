"""
Outpost_Survey — the Survey Array's signal-triangulation search.

The intelligence answer to "where are the enemy bases on this planet?". Every
tier is in scope — outposts and fortresses alike — and the readout always names
the tier it found, so the player knows what they are walking toward.

The array never hands over coordinates: opening a survey picks ONE NPC base the
player does not already know about on their CURRENT planet and returns a search
BOX known to contain it, placed at a random offset so its centre is not a free
answer. From there the player closes in with two tools that cost resources:

* ``narrow`` — a sweep that roughly quarters the search area, and
* ``probe (x, y)`` — a reading from any tile giving a compass BEARING toward the
  target and a coarse DISTANCE BAND, never an exact range.

A probe that lands within ``survey_reveal_radius`` of the target (or a box that
collapses to a single tile) PINPOINTS it: the tile is written into the player's
fog-of-war discovery memory so it shows on the map permanently.

Randomization is per-search and planet-scoped: the target is drawn with the
injected RNG from the bases on the player's own planet, and every box is
re-placed at a fresh random offset, so two players hunting the same base get
different search boxes and the same player cannot replay a memorized sequence.

Contract state lives on ``player.db.survey_contract`` so a search survives a
disconnect — the true coordinates are held there (server-side only) and are
never sent to the player until the pinpoint.
"""

from __future__ import annotations

import logging
import random
from typing import Any, Callable

from world.constants import OUTPOST_SURVEY
from world.systems.base_system import BaseSystem
from world.systems.bench_gate import BenchGateMixin

logger = logging.getLogger("evennia.world.systems.outpost_survey")

#: Player attribute holding the in-progress search. Shape:
#: ``{"planet", "key", "name", "tx", "ty", "x1", "y1", "x2", "y2", "narrows"}``
#: where ``tx``/``ty`` are the hidden true coordinates and ``x1..y2`` the
#: inclusive search box the player has been told about.
SURVEY_ATTR = "survey_contract"

#: Coarse distance bands (max Chebyshev distance, label) a probe reports instead
#: of an exact range. The bands widen with distance so a far reading is vague and
#: a near one is sharp — that gradient is what makes walking toward the target
#: and re-probing worthwhile. The final band is open-ended.
DISTANCE_BANDS: tuple[tuple[int, str], ...] = (
    (2, "almost on top of it"),
    (5, "very close"),
    (10, "close"),
    (20, "some distance off"),
    (40, "far off"),
)
#: Label used beyond the last band boundary.
DISTANCE_BAND_FAINT = "barely registering"

#: Compass labels for a probe bearing, indexed by ``(dy sign, dx sign)``.
#: Mirrors the game's single axis convention — **north = +y**, matching
#: ``CmdMove.DIRECTION_MAP`` (the source of truth), ``bomb_system._DIRECTIONS``
#: and ``game_commands._SHOOT_DIRECTIONS``. This is the INVERSE mapping (sign
#: pair → name) so it cannot be derived from those tables mechanically; if the
#: axis convention ever changes, this must change with it or every survey
#: bearing silently inverts.
_BEARINGS = {
    (1, 0): "north", (-1, 0): "south",
    (0, 1): "east", (0, -1): "west",
    (1, 1): "northeast", (1, -1): "northwest",
    (-1, 1): "southeast", (-1, -1): "southwest",
}


class OutpostSurveySystem(BenchGateMixin, BaseSystem):
    """Runs the Survey Array's base-triangulation search.

    Args:
        registry: DataRegistry (balance knobs + building defs for the
            capability check).
        event_bus: EventBus — player-facing text is emitted as
            ``PLAYER_NOTIFICATION`` kinds for the presenter.
        outposts_provider: ``(planet) -> list[{key, name, x, y}]`` returning the
            live NPC bases on a planet (the Outpost_Spawner's
            ``bases_on_planet``). Injected so this system never imports the
            spawner.
        fog_provider: zero-arg callable returning the FogOfWarSystem, used to
            skip bases the player already knows about and to write the pinpoint
            into discovery memory. Late-bound because the fog system is not
            available when this system is constructed.
        bounds_provider: ``(planet) -> (width, height)`` giving a planet's tile
            extent, for clamping search boxes to the real map. A narrow callable
            rather than the PlanetRegistry itself, so this system depends only
            on the two numbers it needs, not on ``CoordinateSpaceDef``'s shape.
        rng: Optional ``random.Random`` for deterministic target choice and box
            placement in tests.
    """

    def __init__(
        self,
        registry: Any,
        event_bus: Any,
        outposts_provider: Callable[[Any], list] | None = None,
        fog_provider: Callable[[], Any] | None = None,
        bounds_provider: Callable[[Any], tuple] | None = None,
        rng: "random.Random | None" = None,
    ) -> None:
        super().__init__(registry, event_bus)
        self._outposts_provider = outposts_provider or (lambda planet: [])
        self._fog_provider = fog_provider or (lambda: None)
        self._bounds_provider = bounds_provider or (lambda planet: None)
        self._rng = rng or random.Random()

    # ------------------------------------------------------------------ #
    #  Public API — one method per player action
    # ------------------------------------------------------------------ #

    def status(self, player: Any, building: Any) -> bool:
        """Report the in-progress search, or say none is open.

        The only free action: reading back the box you already paid for costs
        nothing, so a player who forgets their lead is not charged to recover it.
        Still gated on standing in your own operational array, so the readout
        cannot be used as a remote status console.
        """
        if not self._usable(player, building):
            return False
        contract = self._contract(player)
        if contract is None:
            self.notify(player, "survey_status", active=False)
            return False
        self.notify(
            player, "survey_status", active=True,
            name=contract.get("name", "base"),
            planet=contract.get("planet"),
            **self._box_payload(contract),
        )
        return True

    def scan(self, player: Any, building: Any) -> bool:
        """Open a survey on one NPC base the player does not already know about.

        Any tier is eligible (outpost through citadel); the target's tier name
        travels in the readout so the player can judge the fight.

        Only ONE search may be open at a time, on any planet: overwriting a
        contract would silently discard readings the player paid for, so an
        existing search must be finished or explicitly abandoned first.
        """
        if not self._usable(player, building):
            return False
        planet = self._planet(player)
        if not planet:
            self.notify(player, "survey_failed", reason="no_position")
            return False

        # One search at a time, on any planet: silently overwriting a contract
        # would throw away readings the player already paid for.
        contract = self._contract(player)
        if contract is not None:
            if contract.get("planet") == planet:
                self.notify(
                    player, "survey_failed", reason="already_active",
                    name=contract.get("name", "base"),
                    **self._box_payload(contract),
                )
            else:
                self.notify(
                    player, "survey_failed", reason="other_planet_active",
                    name=contract.get("name", "base"),
                    planet=contract.get("planet"),
                )
            return False

        targets = self._candidates(player, planet)
        if targets is None:
            self.notify(player, "survey_failed", reason="lookup_failed")
            return False
        if not targets:
            self.notify(player, "survey_failed", reason="no_targets")
            return False

        if not self._charge(player, "survey_scan_cost", "survey_failed"):
            return False

        target = self._rng.choice(targets)
        radius = self._initial_radius(building)
        box = self._place_box(planet, int(target["x"]), int(target["y"]), radius)
        contract = {
            "planet": planet,
            "key": target.get("key"),
            "name": target.get("name", "Base"),
            "tx": int(target["x"]), "ty": int(target["y"]),
            "narrows": 0,
            **box,
        }
        self._save(player, contract)
        logger.info(
            "%s opened a survey for %s on %s",
            getattr(player, "key", "?"), contract["name"], planet,
        )
        self.notify(
            player, "survey_started", name=contract["name"], planet=planet,
            **self._box_payload(contract),
        )
        return True

    def narrow(self, player: Any, building: Any) -> bool:
        """Pay to shrink the search box, roughly quartering its area.

        The brute-force tool. Each sweep halves the box's half-width and
        re-places it at a fresh random offset that still contains the target, so
        the box never converges on its own centre. A sweep that reduces the box
        to a single tile pinpoints the base.
        """
        contract = self._active_contract(player, building)
        if contract is None:
            return False
        if not self._charge(player, "survey_narrow_cost", "survey_failed"):
            return False

        half = max(0, (self._box_half(contract) - 1) // 2)
        box = self._place_box(
            contract["planet"], contract["tx"], contract["ty"], half
        )
        contract.update(box)
        contract["narrows"] = int(contract.get("narrows", 0)) + 1

        if box["x1"] == box["x2"] and box["y1"] == box["y2"]:
            self._reveal(player, contract, via="narrow")
            return True

        self._save(player, contract)
        self.notify(
            player, "survey_narrowed", name=contract["name"],
            narrows=contract["narrows"], **self._box_payload(contract),
        )
        return True

    def probe(self, player: Any, building: Any, x: Any, y: Any) -> bool:
        """Read a bearing and distance band toward the target from ``(x, y)``.

        The skill move, and the cheapest action: the player picks where to
        measure from. A probe inside ``survey_reveal_radius`` of the target
        pinpoints it; anything else reports only a compass direction and a
        coarse band, so two readings from different tiles are what actually
        locate the base.

        The probe tile must be inside the current search box — the array is
        measuring against a known search volume, and allowing probes anywhere
        would turn the box into decoration.
        """
        contract = self._active_contract(player, building)
        if contract is None:
            return False
        try:
            px, py = int(x), int(y)
        except (TypeError, ValueError):
            self.notify(player, "survey_failed", reason="bad_coords")
            return False
        if not self._in_box(contract, px, py):
            self.notify(
                player, "survey_failed", reason="outside_box",
                **self._box_payload(contract),
            )
            return False
        if not self._charge(player, "survey_probe_cost", "survey_failed"):
            return False

        from world.utils import chebyshev_distance

        dx = contract["tx"] - px
        dy = contract["ty"] - py
        dist = chebyshev_distance(px, py, contract["tx"], contract["ty"])
        if dist <= self._reveal_radius():
            self._reveal(player, contract, via="probe")
            return True

        self.notify(
            player, "survey_probe", name=contract["name"],
            x=px, y=py, bearing=self._bearing(dx, dy), band=self._band(dist),
        )
        return True

    def abandon(self, player: Any, building: Any) -> bool:
        """Drop the open search. Free, but no refund either.

        Abandoning costs nothing because the readings were already delivered —
        the player is discarding value, not buying anything. Re-rolling for a
        conveniently-placed box therefore costs one full ``scan``, which is the
        priciest action, so it is never the cheap path.
        """
        if not self._usable(player, building):
            return False
        contract = self._contract(player)
        if contract is None:
            self.notify(player, "survey_failed", reason="no_contract")
            return False
        self._save(player, None)
        self.notify(
            player, "survey_abandoned", name=contract.get("name", "base")
        )
        return True

    # ------------------------------------------------------------------ #
    #  Gates
    # ------------------------------------------------------------------ #

    def _usable(self, player: Any, building: Any) -> bool:
        """Return True if *player* may operate *building* as a Survey Array.

        Capability gate first (also covers "no building here"), then the shared
        ownership + operational tail every bench uses.
        """
        from world.utils import building_has_capability

        if building is None or not building_has_capability(
            building, OUTPOST_SURVEY, provider=self.registry
        ):
            self.notify(player, "survey_failed", reason="wrong_building")
            return False
        return self.check_owner_operational(player, building, "survey_failed")

    def _active_contract(self, player: Any, building: Any) -> dict | None:
        """Return the open contract for the player's CURRENT planet, or None.

        Messages and returns None when the array is unusable, no search is open,
        or the open search belongs to another planet (the array only reaches its
        own planet, so that contract cannot be worked from here).
        """
        if not self._usable(player, building):
            return None
        contract = self._contract(player)
        if contract is None:
            self.notify(player, "survey_failed", reason="no_contract")
            return None
        planet = self._planet(player)
        if contract.get("planet") != planet:
            self.notify(
                player, "survey_failed", reason="other_planet",
                planet=contract.get("planet"),
            )
            return None
        # The tracked base can be wiped out from under a search — cleared by
        # another raider, or swept for staleness and respawned elsewhere. Drop
        # the contract instead of charging for readings against a phantom.
        if not self._target_alive(contract):
            self._save(player, None)
            self.notify(
                player, "survey_failed", reason="target_lost",
                name=contract.get("name", "base"),
            )
            return None
        return contract

    def _target_alive(self, contract: dict) -> bool:
        """True if the contract's base is still tracked on its planet.

        Matched by the spawner's base key, which is what makes the key stored at
        scan time meaningful. Falls OPEN when the locator is unavailable or the
        contract predates key tracking, so an intel outage never destroys a
        search the player paid for.
        """
        key = contract.get("key")
        if key is None:
            return True
        bases = self._bases(contract.get("planet"))
        if bases is None:
            return True
        return any(b.get("key") == key for b in bases)

    def _charge(self, player: Any, cost_field: str, fail_kind: str) -> bool:
        """Deduct the named balance cost via the shared bench spend.

        Deduct-before-effect: every caller applies its effect only after this
        returns True, so a failed spend can never yield free intel. Nothing
        after the charge can fail, so no refund path is needed.
        """
        return self.charge_resources(
            player, self._cost(cost_field), fail_kind
        )

    # ------------------------------------------------------------------ #
    #  Target selection
    # ------------------------------------------------------------------ #

    def _bases(self, planet: Any) -> list[dict] | None:
        """Live bases on *planet*, or ``None`` when the locator failed.

        ``None`` is distinct from ``[]`` on purpose: an empty planet is a swept
        planet, while a locator failure must not be reported to the player as
        "everything is already on your map".
        """
        try:
            return list(self._outposts_provider(planet) or [])
        except Exception:  # noqa: BLE001 - reported as a lookup failure
            logger.warning("survey: base lookup failed for %r", planet,
                           exc_info=True)
            return None

    def _candidates(self, player: Any, planet: Any) -> list[dict] | None:
        """Bases on *planet* the player does not already KNOW ABOUT.

        Filtered on fog BUILDING memory, not on discovered tiles: discovery of
        ground is additive and never pruned, so a tile filter would permanently
        hide any base that later spawned on ground the player once crossed. The
        building snapshot is the real "I know a base is here" signal, and
        ``update_discovery`` drops it again if the base is gone — so a wiped and
        respawned base correctly becomes surveyable once more.

        Returns ``None`` when the base lookup itself failed (see :meth:`_bases`).
        """
        bases = self._bases(planet)
        if bases is None:
            return None
        fog = self._fog()
        if fog is None or not hasattr(fog, "get_discovered_buildings"):
            return bases
        out = []
        for base in bases:
            try:
                known = fog.get_discovered_buildings(
                    player, int(base.get("x", 0)), int(base.get("y", 0))
                )
            except Exception:  # noqa: BLE001 - unknown means "still a target"
                logger.debug("survey: discovery read failed", exc_info=True)
                known = None
            if not known:
                out.append(base)
        return out

    # ------------------------------------------------------------------ #
    #  Search-box geometry
    # ------------------------------------------------------------------ #

    def _initial_radius(self, building: Any) -> int:
        """Opening box half-width, tightened by the array's LEVEL.

        ``survey_initial_radius - 2 x (level - 1)``, floored at
        ``survey_min_radius`` so a maxed array still leaves a real search.
        """
        from world.utils import get_building_level

        balance = getattr(self.registry, "balance", None)
        initial = int(getattr(balance, "survey_initial_radius", 12) or 12)
        floor = int(getattr(balance, "survey_min_radius", 3) or 3)
        try:
            level = int(get_building_level(building))
        except (TypeError, ValueError):
            level = 1
        return max(floor, initial - 2 * max(0, level - 1))

    def _place_box(
        self, planet: Any, tx: int, ty: int, half: int
    ) -> dict[str, int]:
        """A box of half-width *half* containing ``(tx, ty)`` at a random offset.

        The target sits at a uniformly random position inside the box rather
        than at its centre, so the box itself leaks no more than its bounds.
        Each axis is clamped to the planet's real bounds while still containing
        the target, so a box near an edge stays on the map.
        """
        x1, x2 = self._place_span(planet, tx, half, axis="x")
        y1, y2 = self._place_span(planet, ty, half, axis="y")
        return {"x1": x1, "y1": y1, "x2": x2, "y2": y2}

    def _place_span(
        self, planet: Any, target: int, half: int, axis: str
    ) -> tuple[int, int]:
        """One axis of :meth:`_place_box`: a span of ``2*half+1`` over *target*.

        Containment is the invariant that must survive every clamp — a box that
        excluded its own target would make the tile unprobeable. The target is
        therefore pulled inside the planet bounds FIRST (it can only be outside
        them given stale data or a resized planet), so the closing clamps can
        never push the span off it.
        """
        lo_bound, hi_bound = self._axis_bounds(planet, axis)
        if lo_bound is not None:
            target = max(target, lo_bound)
        if hi_bound is not None:
            target = min(target, hi_bound)

        span = 2 * half
        low = target - self._rng.randint(0, span)
        high = low + span
        if lo_bound is not None and low < lo_bound:
            high += lo_bound - low
            low = lo_bound
        if hi_bound is not None and high > hi_bound:
            low -= high - hi_bound
            high = hi_bound
        # Clamping must never push the span off the target it has to contain.
        low = min(low, target)
        high = max(high, target)
        if lo_bound is not None:
            low = max(low, lo_bound)
        if hi_bound is not None:
            high = min(high, hi_bound)
        return int(low), int(high)

    def _axis_bounds(self, planet: Any, axis: str):
        """``(min, max)`` inclusive tile bounds for *axis*, or ``(None, None)``.

        Falls open when the planet is unknown or no bounds provider is wired
        (tests / unwired), so an unresolvable planet yields an unclamped box
        rather than no box.
        """
        try:
            size = self._bounds_provider(planet)
        except Exception:  # noqa: BLE001 - unknown planet: no clamping
            return None, None
        if not size:
            return None, None
        extent = size[0] if axis == "x" else size[1]
        if not extent:
            return None, None
        return 0, int(extent) - 1

    @staticmethod
    def _box_half(contract: dict) -> int:
        """Current half-width of the contract's box (largest axis)."""
        return max(
            (int(contract["x2"]) - int(contract["x1"])) // 2,
            (int(contract["y2"]) - int(contract["y1"])) // 2,
        )

    @staticmethod
    def _in_box(contract: dict, x: int, y: int) -> bool:
        """True if ``(x, y)`` lies inside the contract's inclusive box."""
        return (
            int(contract["x1"]) <= x <= int(contract["x2"])
            and int(contract["y1"]) <= y <= int(contract["y2"])
        )

    @staticmethod
    def _box_payload(contract: dict) -> dict:
        """Box bounds + tile count, the shape every box notification carries."""
        x1, y1 = int(contract["x1"]), int(contract["y1"])
        x2, y2 = int(contract["x2"]), int(contract["y2"])
        return {
            "x1": x1, "y1": y1, "x2": x2, "y2": y2,
            "tiles": (x2 - x1 + 1) * (y2 - y1 + 1),
        }

    # ------------------------------------------------------------------ #
    #  Readings
    # ------------------------------------------------------------------ #

    @staticmethod
    def _bearing(dx: int, dy: int) -> str:
        """Compass direction of the target from the probe tile (north = +y)."""
        sign_x = (dx > 0) - (dx < 0)
        sign_y = (dy > 0) - (dy < 0)
        return _BEARINGS.get((sign_y, sign_x), "right here")

    @staticmethod
    def _band(dist: int) -> str:
        """Coarse label for a Chebyshev distance — never an exact range."""
        for bound, label in DISTANCE_BANDS:
            if dist <= bound:
                return label
        return DISTANCE_BAND_FAINT

    def _reveal_radius(self) -> int:
        balance = getattr(self.registry, "balance", None)
        return max(0, int(getattr(balance, "survey_reveal_radius", 1) or 0))

    def _reveal(self, player: Any, contract: dict, via: str) -> None:
        """Pinpoint the base: clear the search and mark it on the map.

        The tile is written into fog discovery memory (as a remembered enemy HQ)
        so it renders on the map permanently, exactly as a base seen with line of
        sight would. Discovery is additive, so the mark survives the array being
        destroyed.
        """
        tx, ty = int(contract["tx"]), int(contract["ty"])
        name = contract.get("name", "Base")
        self._save(player, None)

        fog = self._fog()
        marked = False
        if fog is not None:
            try:
                fog.remember_building(player, tx, ty, "HQ", name)
                marked = True
            except Exception:  # noqa: BLE001 - the find still stands
                logger.debug("survey: map mark failed", exc_info=True)
        logger.info(
            "%s pinpointed %s at (%d, %d) via %s",
            getattr(player, "key", "?"), name, tx, ty, via,
        )
        self.notify(
            player, "survey_found", name=name, x=tx, y=ty,
            planet=contract.get("planet"), marked=marked, via=via,
        )

    # ------------------------------------------------------------------ #
    #  Contract persistence
    # ------------------------------------------------------------------ #

    @staticmethod
    def _contract(player: Any) -> dict | None:
        """Read the player's open contract as a plain dict, or None.

        Copied out of the Attribute because a real Evennia save returns a
        ``_SaverDict`` proxy; callers mutate the copy and hand it back to
        :meth:`_save`, so a partially-applied update can never be persisted.
        """
        raw = getattr(getattr(player, "db", None), SURVEY_ATTR, None)
        if not raw:
            return None
        try:
            contract = dict(raw)
        except (TypeError, ValueError):
            return None
        required = ("planet", "tx", "ty", "x1", "y1", "x2", "y2")
        if any(field not in contract for field in required):
            return None
        return contract

    @staticmethod
    def _save(player: Any, contract: dict | None) -> None:
        """Persist (or clear) the contract on the player."""
        db = getattr(player, "db", None)
        if db is None:
            return
        try:
            setattr(db, SURVEY_ATTR, dict(contract) if contract else None)
        except Exception:  # noqa: BLE001 - a save failure must not raise out
            logger.debug("survey: contract save failed", exc_info=True)

    # ------------------------------------------------------------------ #
    #  Small readers
    # ------------------------------------------------------------------ #

    def _fog(self) -> Any:
        try:
            return self._fog_provider()
        except Exception:  # noqa: BLE001
            return None

    def _cost(self, field: str) -> dict:
        balance = getattr(self.registry, "balance", None)
        cost = getattr(balance, field, None)
        return dict(cost) if isinstance(cost, dict) else {}

    @staticmethod
    def _planet(player: Any) -> Any:
        return getattr(getattr(player, "db", None), "coord_planet", None)
