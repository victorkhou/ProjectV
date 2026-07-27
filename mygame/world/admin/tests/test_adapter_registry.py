"""
Unit tests for AdapterRegistry startup verb-coverage enforcement.

Tests:
- Rejection of adapters with unaccounted-for core verbs (each named)
- Rejection of opt-outs with missing / empty-after-trim reasons
- Rejection of extra-verb and alias names colliding with core verbs
- Successful registration + get()/all() lookups
- Duplicate entity_key rejection and register_all() idempotency

Requirements: 1.1, 1.2, 1.3, 1.7
"""

import unittest

from mygame.world.admin.adapter_registry import (
    AdapterRegistrationError,
    AdapterRegistry,
    register_all,
)
from mygame.world.admin.types import CORE_VERBS


class _FakeAdapter:
    """Minimal object satisfying the grammar-contract surface the registry
    checks (the EntityAdapter Protocol's CRUD methods are irrelevant here)."""

    def __init__(
        self,
        entity_key="widget",
        supported_verbs=CORE_VERBS,
        opt_outs=None,
        extra_verbs=None,
        aliases=None,
    ):
        self.entity_key = entity_key
        self.supported_verbs = frozenset(supported_verbs)
        self.opt_outs = dict(opt_outs or {})
        self.extra_verbs = dict(extra_verbs or {})
        self.aliases = dict(aliases or {})


class TestVerbCoverageEnforcement(unittest.TestCase):
    """Requirement 1.1 — reject incomplete coverage, naming each verb."""

    def setUp(self):
        self.registry = AdapterRegistry()

    def test_missing_verbs_rejected_and_each_named(self):
        missing = {"destroy", "def diff", "def reset"}
        adapter = _FakeAdapter(supported_verbs=CORE_VERBS - missing)
        with self.assertRaises(AdapterRegistrationError) as ctx:
            self.registry.register(adapter)
        message = str(ctx.exception)
        for verb in missing:
            self.assertIn(repr(verb), message)
        # Rejected adapter is never added (Requirement 1.1).
        self.assertIsNone(self.registry.get("widget"))
        self.assertEqual(self.registry.all(), [])

    def test_verb_covered_by_opt_out_is_accounted_for(self):
        adapter = _FakeAdapter(
            supported_verbs=CORE_VERBS - {"spawn"},
            opt_outs={"spawn": "widgets are founded by players"},
        )
        self.registry.register(adapter)
        self.assertIs(self.registry.get("widget"), adapter)

    def test_empty_grammar_rejected_names_every_core_verb(self):
        adapter = _FakeAdapter(supported_verbs=frozenset())
        with self.assertRaises(AdapterRegistrationError) as ctx:
            self.registry.register(adapter)
        message = str(ctx.exception)
        for verb in CORE_VERBS:
            self.assertIn(repr(verb), message)


class TestOptOutReasonEnforcement(unittest.TestCase):
    """Requirement 1.2 — opt-out reasons must be non-empty after trimming."""

    def setUp(self):
        self.registry = AdapterRegistry()

    def test_empty_reason_rejected(self):
        adapter = _FakeAdapter(
            supported_verbs=CORE_VERBS - {"destroy"},
            opt_outs={"destroy": ""},
        )
        with self.assertRaises(AdapterRegistrationError) as ctx:
            self.registry.register(adapter)
        self.assertIn("'destroy'", str(ctx.exception))
        self.assertIsNone(self.registry.get("widget"))

    def test_whitespace_only_reason_rejected(self):
        adapter = _FakeAdapter(
            supported_verbs=CORE_VERBS - {"def set"},
            opt_outs={"def set": "   \t\n"},
        )
        with self.assertRaises(AdapterRegistrationError):
            self.registry.register(adapter)
        self.assertEqual(self.registry.all(), [])

    def test_non_string_reason_rejected(self):
        adapter = _FakeAdapter(
            supported_verbs=CORE_VERBS - {"set"},
            opt_outs={"set": None},
        )
        with self.assertRaises(AdapterRegistrationError):
            self.registry.register(adapter)


class TestCoreVerbCollisions(unittest.TestCase):
    """Requirement 1.7 — extra verbs / aliases must not shadow core verbs."""

    def setUp(self):
        self.registry = AdapterRegistry()

    def test_extra_verb_colliding_with_core_verb_rejected(self):
        adapter = _FakeAdapter(extra_verbs={"set": "shadowing help text"})
        with self.assertRaises(AdapterRegistrationError) as ctx:
            self.registry.register(adapter)
        self.assertIn("extra verb 'set'", str(ctx.exception))
        self.assertIsNone(self.registry.get("widget"))

    def test_alias_colliding_with_core_verb_rejected(self):
        adapter = _FakeAdapter(aliases={"list": "def list"})
        with self.assertRaises(AdapterRegistrationError) as ctx:
            self.registry.register(adapter)
        self.assertIn("alias 'list'", str(ctx.exception))

    def test_non_colliding_extras_and_aliases_accepted(self):
        adapter = _FakeAdapter(
            extra_verbs={"open": "Open shop menu"},
            aliases={"stats": "show"},
        )
        self.registry.register(adapter)
        self.assertIs(self.registry.get("widget"), adapter)


class TestLookups(unittest.TestCase):
    """get()/all() behavior and duplicate registration."""

    def setUp(self):
        self.registry = AdapterRegistry()

    def test_successful_registration_get_and_all(self):
        item = _FakeAdapter(entity_key="item")
        building = _FakeAdapter(entity_key="building")
        self.registry.register(item)
        self.registry.register(building)
        self.assertIs(self.registry.get("item"), item)
        self.assertIs(self.registry.get("building"), building)
        self.assertEqual(self.registry.all(), [item, building])

    def test_get_unknown_key_returns_none(self):
        self.assertIsNone(self.registry.get("nope"))

    def test_duplicate_entity_key_rejected(self):
        self.registry.register(_FakeAdapter(entity_key="item"))
        with self.assertRaises(AdapterRegistrationError):
            self.registry.register(_FakeAdapter(entity_key="item"))
        self.assertEqual(len(self.registry.all()), 1)


class TestRegisterAll(unittest.TestCase):
    """The startup entry point wired from game_init (Requirement 1.3)."""

    def test_register_all_registers_the_item_adapter(self):
        # Phase 1: the @item pilot adapter registers at startup — without
        # a live game (registry access is lazy), so this must not raise.
        # Later phases append more adapters (building, agent, ...); the
        # pilot must stay registered and first, so assert that invariant
        # rather than an exact list that every phase would churn.
        registry = AdapterRegistry()
        result = register_all(registry)
        self.assertIs(result, registry)
        self.assertIsNotNone(registry.get("item"))
        self.assertEqual(registry.all()[0].entity_key, "item")

    def test_register_all_is_idempotent(self):
        registry = AdapterRegistry()
        register_all(registry)
        count = len(registry.all())
        register_all(registry)  # re-run (in-process reload) must not raise
        self.assertEqual(len(registry.all()), count)


if __name__ == "__main__":
    unittest.main()
