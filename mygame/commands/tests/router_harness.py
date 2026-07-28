"""
Shared test harness for the admin router command tests.

One caller double and one base class, replacing the copies that had been
hand-written once per test section. Before this module there were 21
``check_permstring`` doubles across 13 files and 18 ``output()`` helpers in
``mygame/commands/tests/``, in three mutually incompatible spellings
(``caller.messages`` in 9 files, ``caller._messages`` in 11, and a
``str(m)``-coercing variant in 4) — so a test's capture attribute depended
on which section of which file it happened to live in.

What belongs HERE: plumbing every router test needs and none of them should
own — caller identity, permission checks, message capture, and the
process-global state that leaks between router invocations.

What stays in the per-file tests: the toy adapter, the router subclass, and
the behavior being asserted. Those are the parts that differ on purpose.

``RouterCaller`` deliberately implements the REAL Evennia permission rule
(``evennia/typeclasses/models.py:710`` — a direct match, or holding any
strictly higher tier in ``PERMISSION_HIERARCHY``) rather than the
set-membership check the old doubles used. The old rule answered False for
every tier BELOW the one held, so a ``Builder`` double failed
``check_permstring("Player")`` where a real Builder passes it. Verb tiers in
this codebase run from ``Player`` up, so that divergence was live.

NOTE: this harness does NOT close the stub gap. Every test using it runs
against the Evennia stubs installed by ``mygame/conftest.py`` (see
``_ensure_evennia_stubs``); the only real-boot admin coverage is
``mygame/tests/test_live_boot_smoke.py``, which asserts the adapters
register and never invokes a verb. A shared double raises the FIDELITY of
the stubbed permission check; it does not prove production wiring.

Not named ``test_*`` so pytest does not collect it as a test module —
matching ``mygame/world/presenters/test_support.py``.
"""

from __future__ import annotations

import itertools
import unittest
from typing import Any

from world.admin.outcomes import FIELD_SET, PERM_DENIED, UNKNOWN_FIELD

# Evennia's default PERMISSION_HIERARCHY (evennia/settings_default.py:784).
# ``mygame`` does not override it, so this IS the production ordering.
PERMISSION_HIERARCHY = (
    "Guest",
    "Player",
    "Helper",
    "Builder",
    "Admin",
    "Developer",
)

#: One process-wide id source. Previously each file seeded its own
#: ``itertools.count`` and three of them collided on 50_000
#: (test_entity_admin_def_mutations.py, test_prop_admin_set.py,
#: test_outpost_adapter.py) — callers in different files shared an identity,
#: and identity is the key for both LIST_CACHE and the pending-destroy map.
#:
#: Seeded high on purpose: the not-yet-migrated files still run their own
#: counters (the highest seeds at 900_000, and one at 1), so the shared
#: range must sit above all of them or the collision would simply move.
_CALLER_IDS = itertools.count(10_000_000)


def next_entity_id() -> int:
    """A fresh object id from the shared sequence.

    Callers get one automatically; this is for the *target* doubles that sit
    in the same identity namespace (``LIST_CACHE`` and the pending-destroy
    map key on ``.id``, and a target can be looked up by ``#N`` just like a
    caller). Sharing one counter is what makes "no two doubles collide" a
    property of the harness rather than of each file's chosen seed.
    """
    return next(_CALLER_IDS)


def _rank(perm: str) -> int:
    """Position of *perm* in the hierarchy, or -1 if it is not a tier."""
    try:
        return PERMISSION_HIERARCHY.index(str(perm).capitalize())
    except ValueError:
        return -1


class RouterCaller:
    """A command caller: identity, permission tier, and a message sink.

    Accepts either spelling used by the doubles it replaces::

        RouterCaller(perms=("Builder", "Admin"))   # explicit perm set
        RouterCaller(tier="Builder")               # single tier

    Both resolve through the real hierarchy rule, so a ``Builder`` passes
    ``check_permstring("Player")`` exactly as a live Builder would.
    """

    def __init__(self, perms=("Builder",), tier=None, key="TestAdmin",
                 contents=None):
        self.id = next(_CALLER_IDS)
        self.key = key
        self.name = key
        self.perms = {tier} if tier else set(perms)
        self.contents = list(contents or [])
        self.messages: list = []
        #: name -> object, for routers that resolve a trailing [player] token
        self.search_results: dict = {}

    # --- message sink -------------------------------------------------- #

    def msg(self, text, **kwargs):
        self.messages.append(text)

    @property
    def _messages(self) -> list:
        """Alias for the sections that spelled the capture attribute with a
        leading underscore. Same list object, so either name may be read or
        appended to without the two views diverging."""
        return self.messages

    # --- permissions --------------------------------------------------- #

    def check_permstring(self, permstring):
        """The real rule: a direct match, or any strictly higher tier held.

        Mirrors ``evennia/typeclasses/models.py:737-747``.
        """
        if not permstring:
            return False
        held = {str(p).capitalize() for p in self.perms}
        wanted = str(permstring).capitalize()
        if wanted in held:
            return True
        pos = _rank(wanted)
        if pos < 0:
            return False
        return any(_rank(p) > pos for p in held)

    @property
    def _search_results(self) -> dict:
        """Alias for the sections that spelled the search map with a leading
        underscore. Same dict object, so ``caller._search_results[k] = v``
        is visible through either name."""
        return self.search_results

    # --- search -------------------------------------------------------- #

    def search(self, name, **kwargs):
        """Case-insensitive on the fallback so the ``@tech``-style callers
        (which lower-cased their keys) and the exact-match callers can share
        one implementation."""
        key = str(name)
        if key in self.search_results:
            return self.search_results[key]
        lowered = {str(k).lower(): v for k, v in self.search_results.items()}
        return lowered.get(key.lower())


class OutcomeAssertions:
    """Assertions over the outcomes a router recorded.

    Split out of :class:`RouterTestCase` so the files that still carry their
    own ``TestCase`` base (``test_admin_commands.py``,
    ``test_admin_legacy_routers.py``) can mix these in without also
    inheriting the global-state teardown they don't have set up yet. Mix into
    any ``TestCase``::

        class TestThing(OutcomeAssertions, unittest.TestCase):
            ...

    Pytest-style tests (no ``self``) can skip this entirely and read
    ``cmd.outcomes_of(KIND)`` directly — the recorder is on the command.
    """

    # --- asserting on the DECISION, not the sentence -------------------- #
    #
    # Each of these replaces a cluster of assertIn(...) over the response
    # prose. What they assert is strictly MORE than the prose was: the prose
    # said "Admin" appeared somewhere in the message, these say the gate
    # demanded Admin for that specific field. Rewording a message no longer
    # touches a test; changing the decision still does.

    def assertPermDenied(self, cmd, required=None, scope=None, target=None):
        """A permission gate refused. Optionally pin the tier/scope/target."""
        denials = cmd.outcomes_of(PERM_DENIED)
        self.assertTrue(
            denials,
            f"expected a permission denial, got outcomes={cmd.outcomes!r} "
            f"and output {self.output(cmd)!r}",
        )
        got = denials[-1]
        if required is not None:
            self.assertEqual(got["required"], required)
        if scope is not None:
            self.assertEqual(got["scope"], scope)
        if target is not None:
            self.assertEqual(got["target"], target)
        return got

    def assertNoPermDenied(self, cmd):
        """No permission gate refused."""
        self.assertEqual(
            cmd.outcomes_of(PERM_DENIED), [],
            f"unexpected permission denial: {self.output(cmd)!r}")

    def assertUnknownField(self, cmd, field=None, valid=None, plane=None):
        """A field name missed the schema.

        *valid* asserts the offered names CONTAIN the given ones (the full
        list is an adapter detail that grows); pass a set to pin it exactly.
        """
        misses = cmd.outcomes_of(UNKNOWN_FIELD)
        self.assertTrue(
            misses,
            f"expected an unknown-field rejection, got {cmd.outcomes!r}")
        got = misses[-1]
        if field is not None:
            self.assertEqual(got["field"], field)
        if plane is not None:
            self.assertEqual(got["plane"], plane)
        if valid is not None:
            offered = set(got["valid"])
            if isinstance(valid, set):
                self.assertEqual(offered, valid)
            else:
                missing = set(valid) - offered
                self.assertFalse(
                    missing,
                    f"valid-field list {sorted(offered)} omits {sorted(missing)}")
        return got

    def assertClamped(self, cmd, field=None, applied=None, lo=None, hi=None,
                      requested=None):
        """A set landed and the bounds MOVED the value.

        Asserting ``applied`` here is what the prose could not do safely: a
        substring check for "50" matches a message reporting the *requested*
        500, and "clamped to 5" is a prefix of "clamped to 50".
        """
        got = self._assertFieldSet(cmd, field)
        self.assertTrue(
            got["clamped"],
            f"expected a clamp, but {got['field']} applied "
            f"{got['applied']!r} unclamped (bounds {got['lo']}–{got['hi']})")
        for name, want in (("applied", applied), ("lo", lo), ("hi", hi),
                           ("requested", requested)):
            if want is not None:
                self.assertEqual(got[name], want, f"{name} mismatch")
        return got

    def assertNotClamped(self, cmd, field=None, applied=None, target=None):
        """A set landed and the bounds did NOT move the value.

        The positive form of the old ``assertNotIn("clamped", out)``, which
        also passed when the set never happened at all.
        """
        got = self._assertFieldSet(cmd, field)
        self.assertFalse(
            got["clamped"],
            f"expected no clamp, but {got['field']} was clamped from "
            f"{got['requested']!r} to {got['applied']!r}")
        if applied is not None:
            self.assertEqual(got["applied"], applied)
        if target is not None:
            self.assertIn(target, got["target"])
        return got

    def assertFieldSet(self, cmd, field=None, applied=None, target=None):
        """A set landed, without a claim either way about clamping.

        *target* is substring-matched against the recorded identity: the
        identity is an adapter's rendering of the object (a name for most, a
        dict repr for the rolled-loot items), so pinning it exactly would
        re-couple the test to a different piece of formatting.
        """
        got = self._assertFieldSet(cmd, field)
        if applied is not None:
            self.assertEqual(got["applied"], applied)
        if target is not None:
            self.assertIn(target, got["target"])
        return got

    def assertNoFieldSet(self, cmd):
        """Nothing was written — the positive form of "state unchanged"."""
        self.assertEqual(
            cmd.outcomes_of(FIELD_SET), [],
            f"expected no write, got {cmd.outcomes_of(FIELD_SET)!r}")

    def _assertFieldSet(self, cmd, field=None):
        """The recorded field-write, asserting one happened at all."""
        sets = cmd.outcomes_of(FIELD_SET)
        self.assertTrue(
            sets,
            f"no field was set — outcomes={cmd.outcomes!r}, "
            f"output {self.output(cmd)!r}")
        if field is not None:
            matching = [o for o in sets if o["field"] == field]
            self.assertTrue(
                matching,
                f"no write to {field!r}; wrote "
                f"{[o['field'] for o in sets]!r}")
            return matching[-1]
        return sets[-1]

    # --- reading what the caller was told ------------------------------ #

    @staticmethod
    def output(source) -> str:
        """Everything the command said, newline-joined.

        Accepts a command OR a caller: the per-file runners disagree about
        which one they return (``test_alliance_commands`` hands back the
        caller, ``test_agent_router`` the command), and the failure messages
        above need the prose either way.

        ``str()`` per message because some routers send non-str payloads; the
        coercing and non-coercing copies of this helper had drifted apart
        across files. ``_messages`` is the spelling the not-yet-migrated
        doubles use.
        """
        caller = getattr(source, "caller", source)
        messages = getattr(caller, "messages", None)
        if messages is None:
            messages = getattr(caller, "_messages", ())
        return "\n".join(str(m) for m in messages)


class RouterTestCase(OutcomeAssertions, unittest.TestCase):
    """Base for router tests: a fresh caller and no leaked global state.

    Subclasses build their own adapter/registry in ``setUp`` and call
    ``super().setUp()``. Set ``router_class`` to get ``run_cmd`` for free.
    """

    #: EntityAdminRouter subclass under test; set by the subclass.
    router_class: Any = None

    #: Extra class attributes to stamp onto each command instance, e.g.
    #: ``{"registry": ...}``. Usually set in the subclass's ``setUp``.
    cmd_attrs: dict = {}

    def setUp(self):
        super().setUp()
        self.caller = RouterCaller()
        self.audit_log: list = []
        self._reset_router_globals()
        self.addCleanup(self._reset_router_globals)

    @staticmethod
    def _reset_router_globals():
        """Clear the module-level state router invocations accumulate.

        ``LIST_CACHE`` and ``_PENDING_DESTROY`` are process globals keyed by
        caller identity. ``_PENDING_DESTROY`` had NO test ever clearing it,
        so a test that staged a bulk destroy and never confirmed left the
        entry live for the rest of the session.
        """
        from world.admin.resolution import LIST_CACHE
        LIST_CACHE.clear()

        from mygame.commands.command_router import _PENDING_DESTROY
        _PENDING_DESTROY.clear()

    # --- driving the router -------------------------------------------- #

    def run_cmd(self, args, caller=None, **overrides):
        """Invoke ``router_class`` with *args* and return the command.

        ``overrides`` are stamped onto the instance after ``cmd_attrs``, so a
        single test can flip one switch (``audit_fail=True``) without a
        bespoke runner.
        """
        if self.router_class is None:  # pragma: no cover - misuse guard
            raise NotImplementedError(
                f"{type(self).__name__} must set router_class or override "
                "run_cmd()"
            )
        cmd = self.router_class()
        for name, value in {**self.cmd_attrs, **overrides}.items():
            setattr(cmd, name, value)
        cmd.caller = caller or self.caller
        cmd.args = args
        cmd.func()
        return cmd

