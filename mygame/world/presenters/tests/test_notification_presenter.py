"""
Unit tests for NotificationPresenter.

Proves the format-table: each notification kind produces the expected string
from sample data, and the presenter delivers it to the correct player.
"""

from mygame.world.event_bus import EventBus, PLAYER_NOTIFICATION
from mygame.world.presenters.notification_presenter import NotificationPresenter


class _FakeNotifier:
    def __init__(self):
        self.sent = []

    def notify(self, player, message):
        self.sent.append((player, message))


class _Player:
    pass


def _make():
    bus = EventBus()
    notifier = _FakeNotifier()
    presenter = NotificationPresenter(bus, player_notifier=notifier)
    return bus, notifier, presenter


class TestFormatTable:
    def test_rank_level_up(self):
        bus, n, _ = _make()
        p = _Player()
        bus.publish(PLAYER_NOTIFICATION, player=p, kind="rank_level_up",
                    data={"level": 7, "rank_name": "Private", "sub": 2})
        assert n.sent == [(p, "You are now Level 7 (Private 2)")]

    def test_building_progress_upgrade(self):
        bus, n, _ = _make()
        p = _Player()
        bus.publish(PLAYER_NOTIFICATION, player=p, kind="building_progress",
                    data={"btype": "HQ", "target_level": 3, "progress": 10,
                          "total": 50, "remaining": 40})
        assert "Upgrading HQ to L3" in n.sent[0][1]
        assert "10/50s" in n.sent[0][1]

    def test_building_progress_construction(self):
        bus, n, _ = _make()
        p = _Player()
        bus.publish(PLAYER_NOTIFICATION, player=p, kind="building_progress",
                    data={"btype": "EX", "target_level": None, "progress": 5,
                          "total": 20, "remaining": 15})
        assert "Constructing EX" in n.sent[0][1]

    def test_building_complete_upgrade(self):
        bus, n, _ = _make()
        p = _Player()
        bus.publish(PLAYER_NOTIFICATION, player=p, kind="building_complete",
                    data={"building_type": "VT", "target_level": 4})
        assert "VT upgraded to level 4" in n.sent[0][1]

    def test_building_complete_new(self):
        bus, n, _ = _make()
        p = _Player()
        bus.publish(PLAYER_NOTIFICATION, player=p, kind="building_complete",
                    data={"building_type": "EX", "target_level": None})
        assert "construction finished" in n.sent[0][1]

    def test_agent_training_complete(self):
        bus, n, _ = _make()
        p = _Player()
        bus.publish(PLAYER_NOTIFICATION, player=p, kind="agent_training_complete",
                    data={"agent_id": 3})
        assert "Agent #3 training finished" in n.sent[0][1]
        assert "assign 3" in n.sent[0][1]

    def test_agent_training_progress(self):
        bus, n, _ = _make()
        p = _Player()
        bus.publish(PLAYER_NOTIFICATION, player=p, kind="agent_training_progress",
                    data={"agent_id": 5, "remaining": 12})
        assert "Agent #5" in n.sent[0][1]
        assert "12s remaining" in n.sent[0][1]

    def test_harvest_drop(self):
        bus, n, _ = _make()
        p = _Player()
        bus.publish(PLAYER_NOTIFICATION, player=p, kind="harvest_drop",
                    data={"amount": 20, "resource_type": "Iron"})
        assert "+20 Iron dropped" in n.sent[0][1]

    def test_harvester_produced(self):
        """A harvester agent's Extractor output notifies the owner (mirrors the
        equipment buildings' 'produced' line) so autonomous extraction isn't
        silent."""
        bus, n, _ = _make()
        p = _Player()
        bus.publish(PLAYER_NOTIFICATION, player=p, kind="harvester_produced",
                    data={"amount": 9, "resource_type": "Wood"})
        msg = n.sent[0][1]
        assert "Extractor" in msg
        assert "+9 Wood" in msg
        assert "get" in msg

    def test_attacked(self):
        bus, n, _ = _make()
        p = _Player()
        bus.publish(PLAYER_NOTIFICATION, player=p, kind="attacked",
                    data={"attacker_name": "Rex", "weapon_name": "Axe", "damage": 15})
        msg = n.sent[0][1]
        assert "Rex" in msg
        assert "Axe" in msg
        assert "15" in msg
        # Incoming attacks on the receiving player render in bright red. In
        # Evennia ANSI, |r is bright (HILITE+RED); |R is dark (UNHILITE+RED).
        assert "|r" in msg
        assert "|R" not in msg

    def test_building_attacked(self):
        bus, n, _ = _make()
        p = _Player()
        bus.publish(PLAYER_NOTIFICATION, player=p, kind="building_attacked",
                    data={"building_name": "Wall", "attacker_name": "Orc",
                          "weapon_name": "Club", "damage": 8})
        msg = n.sent[0][1]
        assert "Wall" in msg and "Orc" in msg and "8" in msg
        assert "|r" in msg and "|R" not in msg  # bright red for an incoming attack

    def test_unit_attacked_is_bright_red(self):
        bus, n, _ = _make()
        p = _Player()
        bus.publish(PLAYER_NOTIFICATION, player=p, kind="unit_attacked",
                    data={"unit_kind": "agent", "unit_name": "Guard",
                          "attacker_name": "Raider", "weapon_name": "Rifle",
                          "damage": 12})
        msg = n.sent[0][1]
        assert "Guard" in msg and "Raider" in msg
        assert "|r" in msg and "|R" not in msg  # bright red for a hit on your unit

    def test_ability_active(self):
        bus, n, _ = _make()
        p = _Player()
        bus.publish(PLAYER_NOTIFICATION, player=p, kind="ability_active",
                    data={"key": "delivery", "agent_id": 7})
        assert "delivery" in n.sent[0][1]
        assert "now active" in n.sent[0][1]
        assert "#7" in n.sent[0][1]

    def test_ability_relocked(self):
        bus, n, _ = _make()
        p = _Player()
        bus.publish(PLAYER_NOTIFICATION, player=p, kind="ability_relocked",
                    data={"key": "delivery", "agent_id": 7, "required": 21})
        msg = n.sent[0][1]
        assert "re-locked" in msg
        assert "21" in msg

    def test_ability_available(self):
        bus, n, _ = _make()
        p = _Player()
        bus.publish(PLAYER_NOTIFICATION, player=p, kind="ability_available",
                    data={"key": "delivery", "agent_id": 7})
        msg = n.sent[0][1]
        assert "available" in msg
        assert "agent ability 7 delivery on" in msg

    def test_unknown_kind_is_logged_not_delivered(self):
        bus, n, _ = _make()
        bus.publish(PLAYER_NOTIFICATION, player=_Player(), kind="unknown_xyz",
                    data={})
        assert n.sent == []


# =========================================================================== #
#  Task 5.2 — End-to-end presenter tests
#
#  Property 13: Presenter ownership.
#    (a) Structural: no player-facing string is composed inside
#        ``world/systems/`` — the systems route every message through
#        ``self.notify(player, kind, **data)`` and never build the
#        human-readable notification text (that lives here in the presenter).
#    (b) Behavioral: every notification kind this feature added renders through
#        an *attached* NotificationPresenter to the player's ``msg`` sink via
#        the injected player notifier.
#
#  Validates: Requirements 12.12
# =========================================================================== #

import ast
import os

from mygame.world.adapters.evennia_player_notifier import EvenniaPlayerNotifier


# Sample payloads for every notification kind this feature added. The 11 "new"
# kinds called out by the task, plus the four the presenter also formats
# (equipped / unequipped / use_failed / throw_failed).
_FEATURE_KIND_SAMPLES = {
    "equipped": {"item_name": "Kevlar Vest", "slot": "torso"},
    "unequipped": {"item_name": "Kevlar Vest", "slot": "torso"},
    "equip_denied": {"item_name": "Power Armor", "required_rank": "Sergeant",
                     "current_rank": "Private"},
    "use_failed": {"item_name": "Medkit", "reason": "not_held"},
    "healed": {"amount": 20, "hp": 80, "hp_max": 100},
    "buff_applied": {"stat": "damage_bonus", "amount": 5, "duration_ticks": 30},
    "throw_failed": {"item_name": "Frag Grenade", "reason": "out_of_range",
                     "distance": 9, "range": 4},
    "bombed": {"count": 3, "x": 12, "y": 7},
    "out_of_ammo": {"weapon_name": "Rifle", "ammo_name": "rifle_rounds"},
    "reloaded": {"weapon_name": "Rifle", "loaded": 30, "magazine_size": 30,
                 "remaining": 60, "ammo_name": "rifle_rounds"},
    "reload_failed": {"reason": "no_ammo"},
    "carry_full": {"item_name": "Rifle Rounds", "carried": 10, "dropped": 5},
    "storage_full": {"building": "HQ", "stored": 100, "resource": "Iron",
                     "dropped": 20},
    "deposited": {"amount": 50, "resource": "Iron", "building": "HQ",
                  "stored": 150, "capacity": 500},
    "withdrew": {"amount": 25, "resource": "Iron", "carried": 25, "limit": 1000},
}

# The 11 kinds the task explicitly enumerates as "new".
_ELEVEN_NEW_KINDS = [
    "equip_denied", "out_of_ammo", "reloaded", "reload_failed", "healed",
    "buff_applied", "bombed", "carry_full", "storage_full", "deposited",
    "withdrew",
]


class _MsgPlayer:
    """Fake player exposing the Evennia ``msg`` sink the real notifier uses."""

    def __init__(self):
        self.messages = []

    def msg(self, text):
        self.messages.append(text)


def _systems_dir():
    """Absolute path to ``mygame/world/systems``."""
    here = os.path.dirname(os.path.abspath(__file__))          # .../presenters/tests
    world = os.path.dirname(os.path.dirname(here))              # .../world
    return os.path.join(world, "systems")


def _system_source_files():
    """Every non-test ``*.py`` module in the systems layer."""
    root = _systems_dir()
    files = []
    for name in sorted(os.listdir(root)):
        if not name.endswith(".py"):
            continue
        files.append(os.path.join(root, name))
    return files


def _iter_notify_calls(tree):
    """Yield every ``self.notify(...)`` Call node in an AST."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "notify"
            and isinstance(func.value, ast.Name)
            and func.value.id == "self"
        ):
            yield node


def _kind_node(call):
    """Return the AST node for a notify call's ``kind`` argument, if present."""
    if len(call.args) >= 2:
        return call.args[1]
    for kw in call.keywords:
        if kw.arg == "kind":
            return kw.value
    return None


def _prose_violations(path):
    """Structural scan: report any player-facing prose composed at a notify call.

    A violation is either an f-string argument (runtime string composition) or a
    multi-word string literal argument — both signal that a human-readable
    sentence is being built inside the system instead of in the presenter. Short
    single-token literals (``"no_ammo"``, ``"torso"``, the ``kind`` itself) are
    structured data and are fine. Command-layer ``return False, "..."`` strings
    are not notify arguments, so they are never inspected here.
    """
    with open(path, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)

    violations = []
    for call in _iter_notify_calls(tree):
        nodes = []
        kind = _kind_node(call)
        if kind is not None:
            nodes.append(kind)
        # Any positional args beyond (player, kind) and every keyword value.
        nodes.extend(call.args[2:])
        nodes.extend(kw.value for kw in call.keywords if kw.arg != "kind")

        for node in nodes:
            if isinstance(node, ast.JoinedStr):
                violations.append((path, call.lineno, "f-string composed at notify()"))
            elif (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and " " in node.value.strip()
            ):
                violations.append(
                    (path, call.lineno, f"prose literal {node.value!r} at notify()")
                )
    return violations


def _emitted_kind_literals(path):
    """Set of string-literal ``kind`` values a system emits via ``self.notify``."""
    with open(path, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    kinds = set()
    for call in _iter_notify_calls(tree):
        kind = _kind_node(call)
        if isinstance(kind, ast.Constant) and isinstance(kind.value, str):
            kinds.add(kind.value)
    return kinds


def _notify_owner_kind_literals(path):
    """Set of string-literal ``kind`` values emitted via a ``_notify_owner``
    helper, in EITHER of its two shapes:

    * the free-function form in ``agent_scripts`` — ``_notify_owner(npc, "kind",
      ...)`` (an ``ast.Name`` call, kind at args[1]); and
    * the method form in ``agent_progression`` — ``self._notify_owner(agent,
      notify, "kind", ...)`` (an ``ast.Attribute`` call, kind at args[2]).

    Both route through ``self.notify(owner, kind, ...)`` with a VARIABLE kind, so
    the plain ``_emitted_kind_literals`` scan can't see the literal — without
    this, a kind emitted only via ``_notify_owner`` would silently drop (no
    formatter) while the contract test stayed green. To be shape-agnostic we
    accept the literal at ANY positional index of a ``_notify_owner`` call.
    """
    with open(path, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    kinds = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (func.id if isinstance(func, ast.Name)
                else func.attr if isinstance(func, ast.Attribute) else None)
        if name != "_notify_owner":
            continue
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                kinds.add(arg.value)
    return kinds


class TestPresenterOwnershipStructural:
    """Property 13(a): systems own no player-facing strings."""

    def test_no_prose_composed_in_systems_notify_calls(self):
        all_violations = []
        for path in _system_source_files():
            all_violations.extend(_prose_violations(path))
        assert all_violations == [], (
            "Player-facing text must be composed in the presenter, not in "
            f"world/systems/. Offending notify() calls: {all_violations}"
        )

    def test_feature_systems_route_kinds_through_notify(self):
        """The equipment/combat systems emit their feature kinds via notify()."""
        systems = _systems_dir()
        emitted = _emitted_kind_literals(
            os.path.join(systems, "equipment_system.py")
        ) | _emitted_kind_literals(os.path.join(systems, "combat_engine.py"))

        # Kinds this feature's systems actually emit today (deposit/withdraw/
        # storage_full are wired by a later task and are covered behaviorally).
        expected_routed = {
            "equipped", "unequipped", "equip_denied", "use_failed", "healed",
            "buff_applied", "throw_failed", "bombed", "carry_full",
            "out_of_ammo", "reloaded", "reload_failed",
        }
        missing = expected_routed - emitted
        assert not missing, f"Feature kinds not routed through notify(): {missing}"

    def test_every_emitted_kind_has_a_formatter(self):
        """Contract: EVERY ``kind`` any system emits via ``self.notify`` must have
        a matching entry in the presenter's ``_FORMATTERS`` table.

        ``on_notification`` silently drops a kind with no formatter (logs, but the
        player sees nothing) — the exact "a typo/new kind is dropped and CI stays
        green" risk flagged in the architecture docs. This scans every system
        source file (plus the other notify-emitters) for string-literal kinds and
        asserts each is formattable, turning that latent risk into a hard failure.
        """
        paths = list(_system_source_files())
        # Other modules that emit PLAYER_NOTIFICATION kinds via self.notify.
        world_dir = os.path.dirname(_systems_dir())
        for extra in ("combat_timer.py", "notification_system.py"):
            p = os.path.join(world_dir, extra)
            if os.path.exists(p):
                paths.append(p)

        emitted = set()
        for path in paths:
            emitted |= _emitted_kind_literals(path)

        # Some emitters route owner notifications through a ``_notify_owner``
        # helper with a VARIABLE kind, so the plain self.notify scan misses the
        # literal. Scan those explicitly so such a kind can't silently drop:
        #  * typeclasses/agent_scripts.py — free-function _notify_owner(npc, "k")
        #  * world/systems/agent_progression.py — method self._notify_owner(
        #    agent, notify, "k") (already in paths for the self.notify scan, but
        #    that scan can't see the indirected literal).
        mygame_dir = os.path.dirname(world_dir)
        scripts_path = os.path.join(mygame_dir, "typeclasses", "agent_scripts.py")
        if os.path.exists(scripts_path):
            emitted |= _notify_owner_kind_literals(scripts_path)
        progression_path = os.path.join(_systems_dir(), "agent_progression.py")
        if os.path.exists(progression_path):
            emitted |= _notify_owner_kind_literals(progression_path)

        formatter_keys = set(NotificationPresenter._FORMATTERS.keys())
        missing = emitted - formatter_keys
        assert not missing, (
            "These notification kinds are emitted by a system but have NO "
            "formatter in NotificationPresenter._FORMATTERS, so they are "
            f"silently dropped instead of shown to the player: {sorted(missing)}"
        )


class TestPresenterOwnershipBehavioral:
    """Property 13(b): every feature kind renders to player.msg via the presenter."""

    def test_all_feature_kinds_render_to_player_msg(self):
        for kind, data in _FEATURE_KIND_SAMPLES.items():
            bus = EventBus()
            player = _MsgPlayer()
            # Attach a real presenter backed by the real player notifier so the
            # message is delivered through the player's msg() sink.
            NotificationPresenter(bus, player_notifier=EvenniaPlayerNotifier())
            bus.publish(PLAYER_NOTIFICATION, player=player, kind=kind, data=data)

            assert len(player.messages) == 1, (
                f"kind {kind!r} did not render exactly one message: "
                f"{player.messages}"
            )
            msg = player.messages[0]
            assert isinstance(msg, str) and msg.strip(), (
                f"kind {kind!r} rendered an empty message"
            )

    def test_eleven_new_kinds_all_present_in_format_table(self):
        table = NotificationPresenter._FORMATTERS
        missing = [k for k in _ELEVEN_NEW_KINDS if k not in table]
        assert not missing, f"Missing formatters for new kinds: {missing}"

    def test_new_kinds_render_expected_content(self):
        """Spot-check that key data surfaces in the rendered line."""
        checks = {
            "equip_denied": ["Power Armor", "Sergeant", "Private"],
            "out_of_ammo": ["Rifle", "empty"],
            "reloaded": ["Rifle", "30/30", "rifle_rounds"],
            "reload_failed": ["No ammo"],
            "healed": ["20", "80/100"],
            "buff_applied": ["damage_bonus", "30"],
            "bombed": ["3", "12", "7"],
            "carry_full": ["Rifle Rounds", "10", "5"],
            "storage_full": ["HQ", "Iron"],
            "deposited": ["Iron", "HQ", "150/500"],
            "withdrew": ["Iron", "25/1000"],
        }
        for kind, fragments in checks.items():
            bus = EventBus()
            player = _MsgPlayer()
            NotificationPresenter(bus, player_notifier=EvenniaPlayerNotifier())
            bus.publish(PLAYER_NOTIFICATION, player=player, kind=kind,
                        data=_FEATURE_KIND_SAMPLES[kind])
            msg = player.messages[0]
            for frag in fragments:
                assert frag in msg, (
                    f"kind {kind!r} rendered {msg!r}, missing {frag!r}"
                )

    def test_reload_no_magazine_reason_renders_distinct_message(self):
        """A resource-fed ranged weapon's 'no_magazine' reason reads as 'no
        magazine to reload', not the misleading 'No ammo-using weapon'."""
        bus = EventBus()
        player = _MsgPlayer()
        NotificationPresenter(bus, player_notifier=EvenniaPlayerNotifier())
        bus.publish(PLAYER_NOTIFICATION, player=player, kind="reload_failed",
                    data={"reason": "no_magazine"})
        msg = player.messages[0]
        assert "no magazine" in msg.lower()
        assert "No ammo-using weapon" not in msg

    def test_craft_failed_bag_full_reason_renders_distinct_message(self):
        """A craft rejected because the supply bag is at max_stack reads as
        'supply bag is full', not the misleading 'wrong building'."""
        bus = EventBus()
        player = _MsgPlayer()
        NotificationPresenter(bus, player_notifier=EvenniaPlayerNotifier())
        bus.publish(PLAYER_NOTIFICATION, player=player, kind="craft_failed",
                    data={"reason": "bag_full", "item_name": "Rifle Rounds"})
        msg = player.messages[0]
        assert "full" in msg.lower()
        assert "Rifle Rounds" in msg

    def test_base_deactivated_renders_alert(self):
        """Losing the HQ tells the player their base is deactivated."""
        bus = EventBus()
        player = _MsgPlayer()
        NotificationPresenter(bus, player_notifier=EvenniaPlayerNotifier())
        bus.publish(PLAYER_NOTIFICATION, player=player,
                    kind="base_deactivated", data={})
        msg = player.messages[0]
        assert "deactivated" in msg.lower()
        assert "hq" in msg.lower()

    def test_base_reactivated_renders_alert(self):
        """Rebuilding the HQ tells the player the base is back online."""
        bus = EventBus()
        player = _MsgPlayer()
        NotificationPresenter(bus, player_notifier=EvenniaPlayerNotifier())
        bus.publish(PLAYER_NOTIFICATION, player=player,
                    kind="base_reactivated", data={})
        msg = player.messages[0]
        assert "online" in msg.lower() or "rebuilt" in msg.lower()

    def test_npc_killed_renders_kill_and_xp(self):
        """Killing an enemy NPC reports the name and XP awarded."""
        bus = EventBus()
        player = _MsgPlayer()
        NotificationPresenter(bus, player_notifier=EvenniaPlayerNotifier())
        bus.publish(PLAYER_NOTIFICATION, player=player,
                    kind="npc_killed", data={"name": "Guard #2", "xp": 100})
        msg = player.messages[0]
        assert "Guard #2" in msg
        assert "100" in msg

    def test_base_eliminated_renders_reward(self):
        """Destroying an NPC base reports the tier, XP, and loot coords."""
        bus = EventBus()
        player = _MsgPlayer()
        NotificationPresenter(bus, player_notifier=EvenniaPlayerNotifier())
        bus.publish(PLAYER_NOTIFICATION, player=player, kind="base_eliminated",
                    data={"tier": "Outpost", "xp": 500,
                          "loot": {"Iron": 30}, "x": 34, "y": 67})
        msg = player.messages[0]
        assert "Outpost" in msg
        assert "500" in msg
        assert "34" in msg and "67" in msg

    def test_base_eliminated_without_loot_omits_coords(self):
        bus = EventBus()
        player = _MsgPlayer()
        NotificationPresenter(bus, player_notifier=EvenniaPlayerNotifier())
        bus.publish(PLAYER_NOTIFICATION, player=player, kind="base_eliminated",
                    data={"tier": "Outpost", "xp": 500, "loot": {}})
        msg = player.messages[0]
        assert "eliminated" in msg.lower()
        assert "Loot" not in msg
