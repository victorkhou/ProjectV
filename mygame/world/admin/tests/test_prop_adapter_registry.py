"""
Property-based tests for AdapterRegistry verb-grammar enforcement
(unified-admin-crud task 1.3).

# Feature: unified-admin-crud, Property 4: Verb-grammar uniformity

For every adapter in ``AdapterRegistry.all()`` (and for generated
synthetic adapters), the adapter supports or explicitly opts out (with a
reason non-empty after trimming) of every core verb; incomplete adapters
are rejected at registration and never added.

**Validates: Requirements 1.1, 1.2, 1.3, 1.5**
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from mygame.world.admin.adapter_registry import (
    AdapterRegistrationError,
    AdapterRegistry,
    register_all,
)
from mygame.world.admin.types import CORE_VERBS

# ------------------------------------------------------------------ #
#  Strategies
# ------------------------------------------------------------------ #

_SORTED_CORE_VERBS = sorted(CORE_VERBS)

#: Verb-ish names that never collide with a core verb.
_SAFE_NAMES = ["open", "grant", "revoke", "kick", "transfer", "rename",
               "stats", "tiers", "give", "inspect", "disband", "reset"]

#: Opt-out reasons: valid (non-empty after trim) and invalid (missing or
#: empty/whitespace-only) — the registry must reject the latter (R1.2).
_valid_reason = st.text(min_size=1, max_size=40).filter(
    lambda s: s.strip() != ""
)
_invalid_reason = st.one_of(
    st.just(""),
    st.sampled_from([" ", "   ", "\t", "\n", " \t\n "]),
    st.none(),
)
_reason = st.one_of(_valid_reason, _invalid_reason)

#: Names for extra verbs / aliases: mostly safe, sometimes a deliberate
#: core-verb collision (R1.7 rejection path).
_maybe_colliding_name = st.one_of(
    st.sampled_from(_SAFE_NAMES),
    st.sampled_from(_SORTED_CORE_VERBS),
)


class _SyntheticAdapter:
    """Minimal object carrying the grammar-contract surface the registry
    enforces (the EntityAdapter Protocol's CRUD methods are irrelevant to
    registration-time verb-coverage checks)."""

    def __init__(self, entity_key, supported_verbs, opt_outs, extra_verbs,
                 aliases):
        self.entity_key = entity_key
        self.supported_verbs = frozenset(supported_verbs)
        self.opt_outs = dict(opt_outs)
        self.extra_verbs = dict(extra_verbs)
        self.aliases = dict(aliases)


@st.composite
def _synthetic_adapter(draw):
    """A synthetic adapter with a random (often incomplete) grammar."""
    supported = draw(
        st.frozensets(st.sampled_from(_SORTED_CORE_VERBS))
    )
    # Opt-outs over a random subset of core verbs — may overlap with
    # supported, may leave gaps, may carry empty/whitespace reasons.
    opt_outs = draw(
        st.dictionaries(st.sampled_from(_SORTED_CORE_VERBS), _reason)
    )
    extra_verbs = draw(
        st.dictionaries(_maybe_colliding_name, st.just("help text"),
                        max_size=3)
    )
    aliases = draw(
        st.dictionaries(_maybe_colliding_name,
                        st.sampled_from(_SORTED_CORE_VERBS), max_size=3)
    )
    return _SyntheticAdapter("synthetic", supported, opt_outs, extra_verbs,
                             aliases)


# ------------------------------------------------------------------ #
#  Property helpers
# ------------------------------------------------------------------ #

def _covers_all_core_verbs(adapter):
    """Property 4's per-adapter invariant: every core verb is supported
    or explicitly opted out with a reason non-empty after trimming."""
    for verb in CORE_VERBS:
        if verb in adapter.supported_verbs:
            continue
        reason = adapter.opt_outs.get(verb)
        if not isinstance(reason, str) or not reason.strip():
            return False
    return True


def _has_core_collision(adapter):
    """True when an extra verb or alias name shadows a core verb (R1.7)."""
    return bool(
        (set(adapter.extra_verbs) | set(adapter.aliases)) & CORE_VERBS
    )


def _all_opt_out_reasons_valid(adapter):
    return all(
        isinstance(reason, str) and reason.strip()
        for reason in adapter.opt_outs.values()
    )


# ------------------------------------------------------------------ #
#  Property 4: Verb-grammar uniformity
#  # Feature: unified-admin-crud, Property 4: Verb-grammar uniformity
#  **Validates: Requirements 1.1, 1.2, 1.3, 1.5**
# ------------------------------------------------------------------ #

class TestProperty4VerbGrammarUniformity:
    """Registration succeeds iff the grammar contract holds; rejected
    adapters are never added; every adapter that IS in ``all()`` covers
    every core verb (support or reasoned opt-out)."""

    @settings(max_examples=25)
    @given(adapter=_synthetic_adapter())
    def test_prop_registration_accepts_iff_contract_holds(self, adapter):
        registry = AdapterRegistry()
        expected_ok = (
            _covers_all_core_verbs(adapter)
            and _all_opt_out_reasons_valid(adapter)
            and not _has_core_collision(adapter)
        )

        if expected_ok:
            registry.register(adapter)  # must not raise (R1.1, R1.2)
            assert registry.get(adapter.entity_key) is adapter
            assert adapter in registry.all()
        else:
            try:
                registry.register(adapter)
            except AdapterRegistrationError as exc:
                # On rejection every unaccounted-for core verb is named
                # in the error (R1.1).
                message = str(exc)
                unaccounted = CORE_VERBS - adapter.supported_verbs - set(
                    adapter.opt_outs)
                for verb in unaccounted:
                    assert repr(verb) in message, (
                        f"unaccounted verb {verb!r} not named in: {message}"
                    )
            else:
                raise AssertionError(
                    "registry accepted an adapter violating the grammar "
                    f"contract: supported={sorted(adapter.supported_verbs)} "
                    f"opt_outs={adapter.opt_outs} "
                    f"extra_verbs={sorted(adapter.extra_verbs)} "
                    f"aliases={sorted(adapter.aliases)}"
                )
            # A rejected adapter is never added (R1.1, R1.3).
            assert registry.get(adapter.entity_key) is None
            assert registry.all() == []

    @settings(max_examples=25)
    @given(adapters=st.lists(_synthetic_adapter(), max_size=6))
    def test_prop_every_registered_adapter_covers_all_core_verbs(
        self, adapters
    ):
        # Whatever mix of good and bad adapters is offered, everything
        # that ends up in all() satisfies Property 4's invariant — the
        # opt-out reason is available verbatim for the router's opted-out
        # response (R1.5).
        registry = AdapterRegistry()
        for i, adapter in enumerate(adapters):
            adapter.entity_key = f"synthetic_{i}"
            try:
                registry.register(adapter)
            except AdapterRegistrationError:
                pass

        for registered in registry.all():
            assert _covers_all_core_verbs(registered)
            assert not _has_core_collision(registered)
            for verb in CORE_VERBS - registered.supported_verbs:
                assert registered.opt_outs[verb].strip() != ""

    def test_real_registry_register_all_satisfies_property(self):
        # The process-wide startup path (game_init -> register_all): every
        # adapter it registers on a fresh registry covers every core verb
        # or carries a reasoned opt-out (R1.3 — enforced before any
        # @<entity> command is invocable).
        registry = register_all(AdapterRegistry())
        for adapter in registry.all():
            assert _covers_all_core_verbs(adapter), (
                f"adapter {adapter.entity_key!r} escaped verb-coverage "
                "enforcement"
            )
            assert not _has_core_collision(adapter)
