"""
The shared EntityAdapter conformance suite.

One parametrized contract, run against EVERY adapter ``register_all``
installs — replacing the per-adapter copies of the same grammar/registration
assertions that were hand-written once per adapter file (and that a new
adapter silently skipped until someone remembered to copy them again).

What belongs HERE: any invariant true of *all* adapters by contract —
grammar-contract coverage of ``CORE_VERBS``, non-empty opt-out reasons,
alias targets, field-schema shape, the ``def``-plane contract, and the
instance-plane no-op guarantees of def-only adapters.

What stays in the per-adapter files: behavior only that adapter has —
item roll bands and IQS re-stamping, ``@stat``'s revive/top-up side
effects, agent roster scoping, the alliance/outpost write paths. Those are
the tests that actually catch regressions in adapter-specific logic.

Adding an adapter to ``register_all`` therefore adds its conformance
coverage automatically; nothing needs copying.
"""

from __future__ import annotations

import unittest

from parameterized import parameterized

from world.admin.adapter_registry import AdapterRegistry, register_all
from world.admin.types import CORE_VERBS, FieldSpec


def _registered() -> list:
    """Every adapter the real startup path registers."""
    return register_all(AdapterRegistry()).all()


def _cases() -> list[tuple[str, object]]:
    """(entity_key, adapter) per registered adapter — the parametrization.

    The entity_key leads so an failing case is named for its entity in the
    test id (``…_0_item``), making a conformance break self-identifying.
    """
    return [(a.entity_key, a) for a in _registered()]


CASES = _cases()


class TestAdapterRegistrationConformance(unittest.TestCase):
    """Every registered adapter satisfies the registration contract."""

    def test_register_all_binds_each_entity_key_to_its_adapter_class(self):
        """The full adapter roster AND the key→class binding — the one place
        both are asserted (subsumes the per-adapter
        ``test_register_all_includes_<x>_adapter`` copies)."""
        self.assertEqual(
            {key: type(a).__name__ for key, a in CASES},
            {
                "item": "ItemAdapter",
                "building": "BuildingAdapter",
                "agent": "AgentAdapter",
                "tech": "TechnologyAdapter",
                "outpost": "OutpostAdapter",
                "alliance": "AllianceAdapter",
                "player": "PlayerAdapter",
                "stat": "StatAdapter",
                "resource": "ResourceAdapter",
                "powerup": "PowerupAdapter",
                "terrain": "TerrainAdapter",
                "planet": "PlanetAdapter",
            },
        )

    def test_register_all_is_idempotent(self):
        """Re-running startup registration (Evennia in-process reload)
        re-offers each adapter; that must be skipped, not rejected."""
        registry = register_all(AdapterRegistry())
        before = len(registry.all())
        register_all(registry)  # must not raise
        self.assertEqual(len(registry.all()), before)

    @parameterized.expand(CASES)
    def test_registers_cleanly_and_is_retrievable(self, key, adapter):
        registry = AdapterRegistry()
        registry.register(adapter)  # must not raise
        self.assertIs(registry.get(key), adapter)

    @parameterized.expand(CASES)
    def test_entity_key_is_a_non_empty_string(self, key, adapter):
        self.assertIsInstance(key, str)
        self.assertTrue(key.strip())


class TestGrammarContractConformance(unittest.TestCase):
    """The verb-grammar contract the AdapterRegistry enforces (R1.1, R1.5)."""

    @parameterized.expand(CASES)
    def test_supported_plus_opted_out_covers_every_core_verb(self, key, adapter):
        """No core verb may be silently unimplemented."""
        covered = set(adapter.supported_verbs) | set(adapter.opt_outs)
        self.assertEqual(
            covered & CORE_VERBS, CORE_VERBS,
            f"{key} leaves core verbs uncovered: "
            f"{sorted(CORE_VERBS - covered)}",
        )

    @parameterized.expand(CASES)
    def test_no_verb_is_both_supported_and_opted_out(self, key, adapter):
        overlap = set(adapter.supported_verbs) & set(adapter.opt_outs)
        self.assertEqual(overlap, set(), f"{key} double-declares {overlap}")

    @parameterized.expand(CASES)
    def test_every_opt_out_reason_is_non_empty(self, key, adapter):
        """An opt-out must explain itself — the reason is surfaced to the
        operator verbatim, so a blank one is a dead end (R1.5)."""
        for verb, reason in adapter.opt_outs.items():
            self.assertTrue(
                (reason or "").strip(),
                f"{key} opts out of '{verb}' with no reason",
            )

    @parameterized.expand(CASES)
    def test_aliases_point_at_real_verbs(self, key, adapter):
        """A migration alias must resolve to a verb the adapter actually
        supports, else the alias is a broken spelling (R11.1)."""
        for alias, canonical in adapter.aliases.items():
            self.assertIn(
                canonical, set(adapter.supported_verbs),
                f"{key} alias '{alias}' -> unsupported '{canonical}'",
            )

    @parameterized.expand(CASES)
    def test_verb_perm_overrides_name_core_verbs(self, key, adapter):
        overrides = getattr(adapter, "verb_perms", None) or {}
        for verb in overrides:
            self.assertIn(
                verb, CORE_VERBS | set(adapter.extra_verbs),
                f"{key} escalates unknown verb '{verb}'",
            )


class TestFieldSchemaConformance(unittest.TestCase):
    """Both field schemas are well-formed FieldSpec maps (R3.1)."""

    @parameterized.expand(CASES)
    def test_instance_fields_are_well_formed(self, key, adapter):
        self._assert_schema(key, adapter.instance_fields(), "instance")

    @parameterized.expand(CASES)
    def test_definition_fields_are_well_formed(self, key, adapter):
        self._assert_schema(key, adapter.definition_fields(), "definition")

    def _assert_schema(self, key, fields, plane):
        self.assertIsInstance(fields, dict)
        for name, spec in fields.items():
            self.assertIsInstance(
                spec, FieldSpec, f"{key} {plane} '{name}' is not a FieldSpec"
            )
            # The dict key must match the spec — the router looks fields up
            # by key then reports spec.name, so a mismatch misnames errors.
            self.assertEqual(
                name, spec.name, f"{key} {plane} key/name mismatch: {name}"
            )
            self.assertIn(
                spec.kind, ("int", "float", "str", "enum"),
                f"{key} {plane} '{name}' has unknown kind '{spec.kind}'",
            )
            if spec.kind == "enum":
                self.assertTrue(
                    spec.enum_values,
                    f"{key} {plane} enum '{name}' declares no values",
                )
            if spec.min_value is not None and spec.max_value is not None:
                self.assertLessEqual(
                    spec.min_value, spec.max_value,
                    f"{key} {plane} '{name}' has inverted bounds",
                )

    @parameterized.expand(CASES)
    def test_settable_verb_implies_a_field_schema(self, key, adapter):
        """An adapter supporting ``set`` must expose something to set —
        otherwise the verb is registered but unusable."""
        if "set" in adapter.supported_verbs:
            self.assertTrue(
                adapter.instance_fields(),
                f"{key} supports 'set' but declares no instance fields",
            )
        if "def set" in adapter.supported_verbs:
            self.assertTrue(
                adapter.definition_fields(),
                f"{key} supports 'def set' but declares no def fields",
            )


class TestDefinitionPlaneConformance(unittest.TestCase):
    """The def plane is consistent with the def-verb opt-outs."""

    @parameterized.expand(CASES)
    def test_def_registry_dict_is_a_mapping_or_none(self, key, adapter):
        """``def_registry_dict`` returns the domain's registry mapping, or
        ``None``.

        NOTE: ``None`` is NOT assertable as "has no definition surface"
        here — every registry-backed adapter reads the LIVE DataRegistry,
        which is unbooted under the test stubs, so they all return ``None``
        in this suite. The declaration-level equivalent (a def-write verb
        implies a def field schema) is covered by
        ``test_settable_verb_implies_a_field_schema``; the live-registry
        behavior is covered by the def-scope integration tests.
        """
        result = adapter.def_registry_dict()
        if result is not None:
            self.assertTrue(
                hasattr(result, "keys"),
                f"{key} def_registry_dict returned a non-mapping",
            )

    @parameterized.expand(CASES)
    def test_def_verbs_are_supported_or_opted_out_as_a_set(self, key, adapter):
        """The def plane is declared coherently: an adapter that opts out of
        reading definitions (``def show``) cannot claim to write them."""
        if "def show" in adapter.opt_outs:
            for verb in ("def set", "def reset"):
                self.assertIn(
                    verb, adapter.opt_outs,
                    f"{key} opts out of 'def show' but supports '{verb}'",
                )

    @parameterized.expand(CASES)
    def test_def_resolve_handles_junk_tokens(self, key, adapter):
        """Resolution never raises on unmatchable input; it returns None so
        the router can report 'no definition found' (R2.3)."""
        for token in ("", "   ", "no-such-definition-xyz"):
            self.assertIsNone(
                adapter.def_resolve(token),
                f"{key} resolved junk token {token!r}",
            )


class TestDefOnlyInstancePlaneConformance(unittest.TestCase):
    """Def-only adapters (terrain/powerup/planet) have an inert instance
    plane: the router never reaches it (it checks ``opt_outs`` first), and
    the inherited stubs must stay side-effect-free if anything does."""

    @staticmethod
    def _def_only():
        return [
            (key, a) for key, a in CASES
            if not ({"list", "show", "set", "spawn", "destroy"}
                    & set(a.supported_verbs))
        ]

    def test_the_def_only_set_is_exactly_the_expected_three(self):
        self.assertEqual(
            {key for key, _ in self._def_only()},
            {"terrain", "powerup", "planet"},
        )

    def test_instance_plane_reads_are_empty_not_raising(self):
        for key, adapter in self._def_only():
            with self.subTest(entity=key):
                self.assertEqual(adapter.instance_fields(), {})
                self.assertEqual(adapter.list_instances(None, ""), [])

    def test_resolve_instance_fails_with_the_opt_out_reason(self):
        """The stub's error must carry the adapter's own declared reason,
        not a generic message — asserted by identity against opt_outs, so
        rewording a reason cannot break this test."""
        for key, adapter in self._def_only():
            with self.subTest(entity=key):
                result = adapter.resolve_instance(None, "anything")
                self.assertFalse(result.ok)
                expected = (adapter.opt_outs.get("show")
                            or adapter.opt_outs.get("list"))
                self.assertEqual(result.error, expected)


if __name__ == "__main__":
    unittest.main()
